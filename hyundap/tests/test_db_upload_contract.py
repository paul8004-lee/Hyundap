"""수집 결과가 DB 보고서와 UI 반환 사이에서 같은 계약을 유지하는지 확인한다."""
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from hyundap.db import jobs_repo, upload_service, version_service
from hyundap.db.classification import classification_fields
from hyundap.db.errors import DbError
from hyundap.ui.backend_documents import _classification_fields


class UploadClassificationContractTests(unittest.TestCase):
    def test_sparse_success_report_can_be_recorded_again_but_not_changed(self):
        """정규화 전 성공 보고서도 동일 내용 재저장은 허용하고 실제 변경은 거부한다."""
        old = jobs_repo.normalize_report(dict(stage='ingest', status='success', classify_status='failed'))
        for key in ('auto_doc_type', 'doc_type_confidence', 'company_match'):
            old.pop(key)
        clean = jobs_repo.normalize_report(old)
        stored = {'ingest': old}
        self.assertEqual(jobs_repo._merge_stage_reports(stored, clean)['ingest'], clean)
        self.assertIs(stored['ingest'], old)
        changed = dict(clean, chunk_count=99)
        with self.assertRaises(DbError) as caught:
            jobs_repo._merge_stage_reports(stored, changed)
        self.assertEqual(caught.exception.code, 'CONFLICT')

    def test_normalization_is_complete_idempotent_and_drops_untrusted_details(self):
        """실패 결과는 추정값·원시 오류를 전파하지 않고 정규화해도 동일하다."""
        report = dict(classify_status='failed', auto_doc_type='policy', error_message='private-value')
        normalized = classification_fields(report)
        self.assertEqual(normalized, dict(auto_doc_type='', doc_type_confidence=0.0,
                                          company_match='', classify_status='failed'))
        self.assertEqual(classification_fields(normalized), normalized)
        self.assertEqual(classification_fields({}), {})
        self.assertEqual(report['error_message'], 'private-value')

    def test_failed_classification_survives_storage_and_ui_validation(self):
        """분류 실패로 업로드가 성공해도 화면 필수 필드가 누락되지 않는다."""
        raw = dict(stage='ingest', status='success', classify_status='failed',
                   doc_type='manual', doc_type_fallback=True,
                   doc_type_fallback_reason='CLASSIFY_FAILED')
        original = dict(raw)
        report = jobs_repo.normalize_report(raw)
        ids = dict(document_id='d', version_id='v', job_id='j')
        with patch.object(upload_service, 'transaction', return_value=nullcontext(object())), \
                patch.object(version_service, 'read_job', return_value=({}, {'ingest': report, '_auto_classify': True})), \
                patch.object(version_service, 'read_candidate', return_value=({'doc_type': 'manual'}, {})):
            result = upload_service.upload_result(ids, status='success')
        fields = _classification_fields(result, required=True)
        self.assertEqual(fields['classify_status'], 'failed')
        self.assertEqual(fields['auto_doc_type'], '')
        self.assertEqual(fields['doc_type_confidence'], 0.0)
        self.assertEqual(fields['company_match'], '')
        self.assertEqual(raw, original)

    def test_historical_sparse_report_is_completed_on_return(self):
        """기존 저장 보고서가 일부 필드만 가져도 새 UI 반환은 완전한 형식이다."""
        report = dict(status='success', classify_status='failed')
        with patch.object(upload_service, 'transaction', return_value=nullcontext(object())), \
                patch.object(version_service, 'read_job', return_value=({}, {'ingest': report})), \
                patch.object(version_service, 'read_candidate', return_value=({'doc_type': 'manual'}, {})):
            result = upload_service.upload_result(dict(document_id='d', version_id='v'), status='success')
        self.assertEqual(_classification_fields(result)['classify_status'], 'failed')


if __name__ == '__main__':
    unittest.main()
