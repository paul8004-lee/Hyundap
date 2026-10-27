"""tests/test_chatbot.py — chatbot 인용 검사와 필터 처리 검증.

실행: 저장소 맨 위에서 python -m unittest hyundap.tests.test_chatbot -v
LLM·search·auth는 부르지 않는다. search_chunks는 빈 결과를 돌려주는 가짜로 바꾼다."""
import sys
import types
import unittest
from unittest.mock import patch

from hyundap.chatbot.citations import validate_citations
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

    def test_single_document_retains_lower_ranked_evidence(self):
        hits = [types.SimpleNamespace(chunk_id=str(i), document_id='doc', score=1 - i / 100)
                for i in range(8)]
        self.assertEqual(len(ask_module.select_evidence(hits)), 4)
        self.assertEqual(len(ask_module.select_evidence(hits, ['doc'])), 8)
        self.assertEqual(len(ask_module.select_evidence(hits, ['doc', 'other'])), 4)


if __name__ == '__main__':
    unittest.main()
