"""업로드 호출 경계의 중복 조회·경고 개수 전달 계약."""
import unittest
from unittest.mock import patch

from hyundap.db import upload_service
from hyundap.db.errors import DbError


class IngestBridgeTests(unittest.TestCase):
    def test_duplicate_callback_is_bound_to_current_server_candidate(self):
        info = {'company_id': 'c'}
        ids = {'version_id': 'v'}
        def ingest(path, metadata, *, find_by_hash):
            self.assertIsNone(find_by_hash('c', 'a' * 64))
            with self.assertRaises(DbError):
                find_by_hash('other', 'a' * 64)
            return {'status': 'success'}
        with patch.object(upload_service.documents_repo, 'find_duplicate_document', return_value=None) as lookup, \
                patch.object(upload_service, 'save_ingest_chunks', return_value=['chunk']):
            self.assertEqual(upload_service.ingest_and_save_chunks('file', info, ids, ingest), (['chunk'], None))
        lookup.assert_called_once_with('c', 'a' * 64, exclude_version_id='v')

    def test_warning_count_replaces_raw_warnings_without_mutating_result(self):
        ids = {'document_id': 'd', 'version_id': 'v', 'job_id': 'j'}
        chunk = {'document_id': 'd', 'version_id': 'v', 'company_id': 'c',
                 'chunk_id': 'v:00000', 'chunk_index': 0, 'text': 'synthetic'}
        report = {'warnings': ['synthetic-secret', 'second'], 'chunk_count': 1}
        result = {'chunks': [chunk], 'report': report}
        with patch.object(upload_service.chunks_repo, 'save_manifest'), \
                patch.object(upload_service.jobs_repo, 'record_stage_result') as record:
            self.assertIs(upload_service.save_ingest_chunks(ids, 'c', result), result['chunks'])
        saved = record.call_args.args[1]
        self.assertEqual(saved['warning_count'], 2)
        self.assertNotIn('warnings', saved)
        self.assertEqual(report['warnings'], ['synthetic-secret', 'second'])


if __name__ == '__main__':
    unittest.main()
