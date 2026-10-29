"""ingest 자동 분류 반영(apply_classification) 테스트. OpenAI를 부르지 않고 가짜 값으로 실행한다.

최종 유형을 정하는 규칙(decide_doc_type)은 챗봇 쪽 함수라서, 여기서는 가짜 값으로 바꿔 끼워 수집이 그 값을 쓰는지만 확인한다.
(이미 챗봇에 decide_doc_type이 있으면 create=True는 필요 없다.)

실행(저장소 맨 위 폴더에서):  python -m unittest hyundap.tests.test_ingest_doc_type -v
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

from hyundap.chatbot import doc_classifier
from hyundap.chatbot.models import DocClassification
from hyundap.ingest.pipeline import ingest_document

# 분류기가 돌려주는 값(정책 문서, 확신도 0.93)
GOOD = DocClassification('d1', 'v1', 'other', auto_doc_type='policy', confidence=0.93,
                         company_match='yes', status='success')
# classify_normalized가 이 값을 보고서용 딕셔너리로 바꾼다
GOOD_DICT = {'auto_doc_type': 'policy', 'doc_type_confidence': 0.93,
             'company_match': 'yes', 'classify_status': 'success'}


class ApplyDocTypeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = self.make_docx(Path(folder.name) / '규정.docx')

    def make_docx(self, path):
        """청크가 여러 개 나오도록 긴 본문의 DOCX를 만든다."""
        document = Document()
        for number in range(30):
            document.add_paragraph(f'A사 사내 규정 제{number}조 이 규정은 연구윤리 기준과 보안 지침을 정한다. ' * 2)
        document.save(path)
        return str(path)

    def ingest(self, doc_type='other', **kwargs):
        info = {'document_id': 'd1', 'version_id': 'v1', 'company_id': 'co', 'company_name': 'A사',
                'doc_type': doc_type, 'file_name': '규정.docx'}
        return ingest_document(self.path, info, **kwargs)

    def test_final_type_is_applied_to_every_chunk(self):
        """분류로 정한 최종 유형이 모든 청크의 doc_type이 되고, 보고서에 7개 값이 담기는지 확인한다."""
        with patch.object(doc_classifier, 'classify_common_document', return_value=GOOD) as classify, \
                patch.object(doc_classifier, 'decide_doc_type', create=True,
                             return_value=('policy', False, None)) as decide:
            result = self.ingest(apply_classification=True)
        self.assertEqual(result.status, 'success')
        self.assertGreater(len(result.chunks), 1)
        self.assertEqual({chunk.doc_type for chunk in result.chunks}, {'policy'})
        self.assertEqual(classify.call_count, 1)      # classify를 켜지 않아도 분류한다
        self.assertEqual(decide.call_args.args, (GOOD_DICT, 'other'))   # 분류 값과 현재(임시) 유형을 넘긴다
        expected = dict(GOOD_DICT, doc_type='policy', doc_type_fallback=False, doc_type_fallback_reason=None)
        self.assertEqual({key: result.report[key] for key in expected}, expected)

    def test_fallback_keeps_the_current_type(self):
        """대체 저장이면 규칙이 정한 현재 유형이 청크에 남고, 보고서에 대체 여부와 사유가 담기는지 확인한다."""
        with patch.object(doc_classifier, 'classify_common_document', return_value=GOOD), \
                patch.object(doc_classifier, 'decide_doc_type', create=True,
                             return_value=('manual', True, 'LOW_CONFIDENCE')):
            result = self.ingest(doc_type='manual', apply_classification=True)
        self.assertEqual({chunk.doc_type for chunk in result.chunks}, {'manual'})
        self.assertEqual((result.report['doc_type'], result.report['doc_type_fallback'],
                          result.report['doc_type_fallback_reason']), ('manual', True, 'LOW_CONFIDENCE'))

    def test_classifier_failure_does_not_stop_ingest(self):
        """분류기가 예외를 내도 수집은 성공하고, 규칙에는 실패 값이 넘어가는지 확인한다."""
        with patch.object(doc_classifier, 'classify_common_document', side_effect=RuntimeError('boom')), \
                patch.object(doc_classifier, 'decide_doc_type', create=True,
                             return_value=('other', True, 'CLASSIFY_FAILED')) as decide, \
                self.assertLogs('hyundap.ingest.pipeline', 'WARNING'):
            result = self.ingest(apply_classification=True)
        self.assertEqual(result.status, 'success')
        self.assertEqual(decide.call_args.args, ({'classify_status': 'failed'}, 'other'))
        self.assertEqual(result.report['classify_status'], 'failed')
        self.assertNotIn('auto_doc_type', result.report)
        self.assertEqual((result.report['doc_type_fallback'], result.report['doc_type_fallback_reason']),
                         (True, 'CLASSIFY_FAILED'))

    def test_reused_classification_does_not_call_the_classifier(self):
        """재시도 때 넘긴 분류 결과는 분류기를 다시 부르지 않고 쓰며, 분류 값 4개만 보고서에 옮기는지 확인한다."""
        reused = dict(GOOD_DICT, stage='ingest', status='success')   # 작업 기록의 다른 키가 섞여 있어도
        with patch.object(doc_classifier, 'classify_common_document') as classify, \
                patch.object(doc_classifier, 'decide_doc_type', create=True,
                             return_value=('policy', False, None)) as decide:
            result = self.ingest(apply_classification=True, classification=reused)
        classify.assert_not_called()
        self.assertEqual(decide.call_args.args[0], reused)
        self.assertEqual({chunk.doc_type for chunk in result.chunks}, {'policy'})
        self.assertEqual({key: result.report[key] for key in GOOD_DICT}, GOOD_DICT)
        self.assertNotIn('stage', result.report)

    def test_calls_without_the_flag_stay_the_same(self):
        """apply_classification을 쓰지 않으면 규칙을 부르지 않고 청크 유형·보고서가 지금과 같은지 확인한다."""
        with patch.object(doc_classifier, 'classify_common_document', return_value=GOOD), \
                patch.object(doc_classifier, 'decide_doc_type', create=True) as decide:
            plain = self.ingest(doc_type='manual')
            classified = self.ingest(doc_type='manual', classify=True)
        decide.assert_not_called()
        for result in (plain, classified):
            self.assertEqual({chunk.doc_type for chunk in result.chunks}, {'manual'})
            self.assertNotIn('doc_type', result.report)
            self.assertNotIn('doc_type_fallback', result.report)
        self.assertNotIn('auto_doc_type', plain.report)
        self.assertEqual({key: classified.report[key] for key in GOOD_DICT}, GOOD_DICT)

    def test_upload_type_check_is_unchanged(self):
        """자동 분류 모드에서도 4종이 아닌 업로드 유형은 지금처럼 거부되는지 확인한다."""
        with patch.object(doc_classifier, 'decide_doc_type', create=True) as decide:
            result = self.ingest(doc_type='auto', apply_classification=True)
        self.assertEqual((result.status, result.error_code), ('failed', 'INVALID_DOC_TYPE'))
        decide.assert_not_called()


if __name__ == '__main__':
    unittest.main()
