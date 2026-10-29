"""검색창 아래 답변(최대 두 줄)의 표시·인용 숨김·HTML 안전성 회귀 검사.

실행: python -B -m unittest hyundap.tests.test_ui_chat_page -v
백엔드 답변 생성이나 출처 유효성 판정 대신 UI 표시 결과를 검사한다.
"""
from copy import deepcopy
from html.parser import HTMLParser
import unittest

from hyundap.ui.chat_page import render_compact_answer
from hyundap.ui.search_page import SEARCH_CSS


class _AnswerParser(HTMLParser):
    """렌더링한 HTML에서 사용자가 읽는 답변 글자와 태그를 수집한다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_parts = []
        self.tags = []
        self.in_answer = False

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        classes = dict(attrs).get("class", "").split()
        if tag == "span" and "hd-inline-answer-text" in classes:
            self.in_answer = True

    def handle_endtag(self, tag):
        if tag == "span":
            self.in_answer = False

    def handle_data(self, data):
        if self.in_answer:
            self.text_parts.append(data)


class CompactAnswerTests(unittest.TestCase):
    def rendered_text(self, response):
        parser = _AnswerParser()
        parser.feed(render_compact_answer(response))
        parser.close()
        return "".join(parser.text_parts)

    def test_single_citation_is_hidden(self):
        self.assertEqual(
            self.rendered_text({"answer": "정기 점검을 실시합니다.[1]"}),
            "정기 점검을 실시합니다.",
        )

    def test_adjacent_citations_are_all_hidden(self):
        self.assertEqual(
            self.rendered_text({"answer": "정기 점검을 실시합니다.[1][2][12]"}),
            "정기 점검을 실시합니다.",
        )

    def test_grouped_citations_are_hidden(self):
        for citation in ("[1,2]", "[1, 2, 12]", "[1，2]"):
            with self.subTest(citation=citation):
                self.assertEqual(
                    self.rendered_text({"answer": f"점검{citation} 결과를 기록합니다."}),
                    "점검 결과를 기록합니다.",
                )

    def test_numeric_citation_links_are_hidden_with_their_urls(self):
        for citation in (
            "[1](https://example.invalid/source)",
            "[1, 2](#source-1)",
            "[1](#source-1)[2](#source-2)",
        ):
            with self.subTest(citation=citation):
                self.assertEqual(
                    self.rendered_text({"answer": f"점검 결과를 기록합니다.{citation}"}),
                    "점검 결과를 기록합니다.",
                )

    def test_named_links_keep_their_readable_label(self):
        self.assertEqual(
            self.rendered_text({"answer": "[점검 지침](https://example.invalid/guide)을 확인하세요.[1]"}),
            "점검 지침을 확인하세요.",
        )

    def test_all_sentences_are_kept(self):
        """두 줄 표시 정책: 첫 문장에서 자르지 않고 답변 전체를 넘긴다(넘치는 부분은 CSS가 숨긴다)."""
        for punctuation in (".", "!", "?", "。"):
            with self.subTest(punctuation=punctuation):
                answer = f"첫 번째 안내{punctuation} 다음 문장도 표시합니다."
                self.assertEqual(self.rendered_text({"answer": answer}), answer)

    def test_newlines_are_joined_with_a_space(self):
        self.assertEqual(
            self.rendered_text({"answer": "첫 번째 안내입니다.[1]\n두 번째 안내입니다.[2]"}),
            "첫 번째 안내입니다. 두 번째 안내입니다.",
        )

    def test_long_answer_is_not_truncated_or_ellipsized(self):
        answer = "가" * 300
        self.assertEqual(self.rendered_text({"answer": answer}), answer)

    def test_answer_text_is_clamped_to_two_lines(self):
        self.assertIn("-webkit-line-clamp:2", SEARCH_CSS)
        self.assertNotIn("-webkit-line-clamp:1", SEARCH_CSS)

    def test_normal_numbers_dates_money_and_decimal_are_preserved(self):
        answer = "점검은 2026-10-28부터 30일마다 3회 진행하며 비용은 1,000원, 기준은 1.5배입니다."
        self.assertEqual(self.rendered_text({"answer": answer + "[1]"}), answer)

    def test_no_evidence_uses_fixed_short_notice(self):
        for status in ("NO_EVIDENCE", "no_evidence"):
            with self.subTest(status=status):
                self.assertEqual(
                    self.rendered_text({
                        "status": status,
                        "answer": "표시하면 안 되는 응답",
                        "message": "길거나 내부 정보를 포함할 수 있는 설명",
                    }),
                    "답변할 근거를 찾지 못했어요.",
                )

    def test_untrusted_html_is_readable_text_not_active_elements(self):
        answer = '<script>alert("x")</script> & <img src=x onerror=evil()>'
        rendered = render_compact_answer({"answer": answer})
        parser = _AnswerParser()
        parser.feed(rendered)
        parser.close()
        self.assertEqual("".join(parser.text_parts), answer)
        self.assertNotIn("script", parser.tags)
        self.assertNotIn("img", parser.tags)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&amp;", rendered)

    def test_original_answer_and_citations_are_not_mutated(self):
        response = {
            "status": "ANSWERED",
            "answer": "점검을 실시합니다.[1] 자세한 설명입니다.",
            "citations": [{"number": 1, "document_id": "doc-1", "version_id": "v-1", "excerpt": "원문"}],
        }
        original = deepcopy(response)
        self.assertEqual(self.rendered_text(response), "점검을 실시합니다. 자세한 설명입니다.")
        self.assertEqual(response, original)

    def test_empty_or_citation_only_answer_uses_fallback_notice(self):
        for response in (None, "", {"answer": "   "}, {"answer": "[1][2]"}):
            with self.subTest(response=response):
                self.assertEqual(self.rendered_text(response), "답변을 제공받지 못했어요.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
