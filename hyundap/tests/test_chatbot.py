"""tests/test_chatbot.py — chatbot 인용 검사와 필터 처리 검증.

실행: 저장소 맨 위에서 python -m unittest hyundap.tests.test_chatbot -v
LLM·search·auth는 부르지 않는다. search_chunks는 빈 결과를 돌려주는 가짜로 바꾼다."""
import sys
import types
import unittest
from unittest.mock import patch

from hyundap.chatbot.citations import extract_fact_aliases, render_answer, validate_citations
from hyundap.chatbot.models import ChatbotError, ChatFilters

ask_module = sys.modules['hyundap.chatbot.ask']  # 패키지가 ask 함수를 다시 내보내므로 모듈은 여기서 꺼낸다

ALIASES = {'E1': object(), 'E2': object()}


class ValidateCitationsTests(unittest.TestCase):
    """답변의 모든 문장에 근거 이름표가 붙었는지 검사하는 규칙을 확인한다."""
    def assert_used(self, answer, expected):
        self.assertEqual(validate_citations(answer, ALIASES), expected)

    def assert_rejected(self, answer, code):
        with self.assertRaises(ChatbotError) as caught:
            validate_citations(answer, ALIASES)
        self.assertEqual(caught.exception.code, code)

    def test_sentence_with_alias(self):
        self.assert_used('교육은 11월 3일에 시작합니다 [E1].', ['E1'])

    def test_bullet_list(self):
        self.assert_used('- 신청서를 작성합니다 [E1].\n- 팀장 승인을 받습니다 [E2].', ['E1', 'E2'])

    def test_numbered_list(self):
        """'1.' 번호에서 문장을 자르지 않는다."""
        self.assert_used('1. 신청서를 작성합니다 [E1].\n2. 팀장 승인을 받습니다 [E2].', ['E1', 'E2'])

    def test_alias_after_period(self):
        """마침표 뒤에 붙은 이름표는 앞 문장의 근거로 인정한다."""
        self.assert_used('교육은 11월 3일에 시작합니다. [E1]', ['E1'])

    def test_date_inside_sentence(self):
        """'2026. 11. 3.' 같은 날짜에서 문장을 자르지 않는다."""
        self.assert_used('교육은 2026. 11. 3. 에 시작합니다 [E1].', ['E1'])

    def test_heading_line(self):
        self.assert_used('A사:\n교육은 11월에 시작합니다 [E1].', ['E1'])

    def test_rejects_uncited_sentence(self):
        self.assert_rejected('교육은 11월에 시작합니다 [E1]. 비용은 무료입니다.', 'INVALID_CITATION')

    def test_rejects_alias_on_next_line(self):
        """다음 줄의 이름표로 앞 줄 문장을 인정하지 않는다."""
        self.assert_rejected('비용은 무료입니다.\n[E1]', 'INVALID_CITATION')

    def test_rejects_unknown_alias(self):
        self.assert_rejected('교육은 11월에 시작합니다 [E9].', 'INVALID_CITATION')

    def test_rejects_answer_without_alias(self):
        self.assert_rejected('교육은 11월에 시작합니다.', 'INVALID_CITATION')

    def test_no_evidence_message(self):
        self.assert_rejected('제공된 문서에서 확인할 수 없습니다.', 'NO_EVIDENCE')

    def test_no_evidence_message_with_alias(self):
        """유보 문장에만 이름표를 붙여도 근거 없음이다 (10/30 실측에서 gpt-5가 낸 형태)."""
        self.assert_rejected('제공된 문서에서 확인할 수 없습니다 [E1][E2].', 'NO_EVIDENCE')
        self.assert_rejected('- 제공된 문서에서 확인할 수 없습니다 [E1].\n- 관련 내용이 없습니다 [E2].', 'NO_EVIDENCE')
        self.assertIsNone(ask_module.ask_check_answer('제공된 문서에서 확인할 수 없습니다 [E1].', ALIASES))

    def test_fact_with_partial_no_evidence_is_answer(self):
        """사실과 '그 밖은 확인할 수 없음'이 섞이면 정상 답변이다."""
        self.assert_used('기한은 6 업무시간입니다 [E1]. 비용은 확인할 수 없습니다.', ['E1'])


class FactAliasTests(unittest.TestCase):
    """제목·유보 문장에만 붙은 이름표를 출처에서 빼고 화면에서도 지우는지 확인한다 (10/30 실제 문서 실측에서 나온 형태)."""
    ANSWER = ('현대엘리베이터: [E3]\n'
              '점검 전 표지판을 설치합니다 [E1].\n'
              '브레이크 라이닝이 3mm 미만이면 교체합니다. [E2]\n'
              '현대경제연구원: [E5]\n'
              '제공된 문서에서 확인할 수 없습니다. [E5]')

    def test_fact_aliases_skip_heading_and_no_evidence(self):
        self.assertEqual(extract_fact_aliases(self.ANSWER), ['E1', 'E2'])

    def test_render_drops_unused_aliases(self):
        display, numbers = render_answer(self.ANSWER, ['E1', 'E2'])
        self.assertEqual(numbers, {'E1': 1, 'E2': 2})
        self.assertNotIn('E', display)
        self.assertIn('현대엘리베이터:\n', display)
        self.assertIn('설치합니다 [1].', display)
        self.assertTrue(display.endswith('확인할 수 없습니다.'))

    def test_plain_answer_unchanged(self):
        answer = '교육은 11월 3일에 시작합니다 [E2]. 장소는 본관입니다 [E1][E2].'
        self.assertEqual(extract_fact_aliases(answer), ['E2', 'E1'])
        self.assertEqual(render_answer(answer, ['E2', 'E1'])[0], '교육은 11월 3일에 시작합니다 [1]. 장소는 본관입니다 [2][1].')


class SelectEvidenceTests(unittest.TestCase):
    """문서당 상한 뒤에 남은 자리를 잘린 근거로 채우는지 확인한다."""
    @staticmethod
    def hits(spec):
        return [types.SimpleNamespace(document_id=doc, chunk_id=f'{doc}{i}', score=None) for i, doc in enumerate(spec)]

    def test_backfills_main_document_when_slots_remain(self):
        # 주 문서 5개 + 점수 낮은 다른 문서 1개: 예전에는 주 문서 4개만 남아 5번째(점검 절차 4단계)가 빠졌다
        picked = ask_module.select_evidence_limit(self.hits('AAAABA'))
        self.assertEqual([h.chunk_id for h in picked], ['A0', 'A1', 'A2', 'A3', 'B4', 'A5'])

    def test_cap_still_keeps_other_documents(self):
        # 근거가 8개를 넘으면 상한이 다른 문서 자리를 지킨다: A 10개·B 2개 → A 6개·B 2개
        picked = ask_module.select_evidence_limit(self.hits('AAAAAAAAAABB'))
        self.assertEqual(len(picked), 8)
        self.assertEqual(sum(h.document_id == 'B' for h in picked), 2)


class AskFiltersTests(unittest.TestCase):
    """ask()가 필터를 None·ChatFilters·dict 어느 형태로 받아도 처리하는지 확인한다."""
    def setUp(self):
        fake_search = types.ModuleType('hyundap.search.search')
        fake_search.search_chunks = lambda **kwargs: []
        patcher = patch.dict(sys.modules, {'hyundap.search.search': fake_search})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_filter_forms(self):
        dict_filters = {'company_ids': None, 'doc_types': None, 'document_ids': None}
        for filters in (None, ChatFilters(), dict_filters):
            with self.subTest(filters=type(filters).__name__):
                self.assertEqual(ask_module.ask('질문', filters, user=None).status, 'NO_EVIDENCE')


if __name__ == '__main__':
    unittest.main()
