"""시험 UI가 문장 전체를 실제 검색 모듈에 전달하는지 확인한다. API 호출은 대역이다."""
import unittest
import subprocess
import sys
from unittest.mock import patch

from hyundap.search.errors import SearchError
from hyundap.search.models import SearchResponse
from hyundap.tests.smoke.services import DatabaseSmokeServices
from hyundap.ui.services import UIServiceError


class SemanticUIBridgeTests(unittest.IsolatedAsyncioTestCase):
    def test_adapter_import_does_not_load_app_or_gradio(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; import hyundap.tests.smoke.services; "
            "assert 'hyundap.app' not in sys.modules; assert 'gradio' not in sys.modules"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_sentence_is_forwarded_to_semantic_search_without_substring_filter(self):
        service = object.__new__(DatabaseSmokeServices)
        service.actor = {'session_id': 'synthetic'}
        question = '장비 유지보수는 얼마나 자주 해야 하나요?'
        response = SearchResponse(query=question, results=[], total_hits=0,
                                  company_ids=['hri'], doc_types=None, elapsed_sec=0.1)
        with patch.object(service, '_checked'), \
                patch('hyundap.tests.smoke.services.search_documents', return_value=response) as search:
            result = await service.search({}, question, company_ids=['hri'])
        search.assert_called_once_with(service.actor, question, company_ids=['hri'], doc_types=None, limit=10)
        self.assertEqual(result['query'], question)
        self.assertEqual(result['results'], [])

    async def test_missing_embedding_configuration_is_not_hidden_by_keyword_fallback(self):
        service = object.__new__(DatabaseSmokeServices)
        service.actor = {}
        with patch.object(service, '_checked'), \
                patch('hyundap.tests.smoke.services.search_documents', side_effect=SearchError('EMBEDDING_FAILED')):
            with self.assertRaises(UIServiceError) as caught:
                await service.search({}, '정비 절차를 알려주세요')
        self.assertEqual(caught.exception.code, 'EMBEDDING_FAILED')


if __name__ == '__main__':
    unittest.main()
