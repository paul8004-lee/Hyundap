"""업로드 호출 경계의 중복 조회·경고 개수 전달 계약."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from hyundap.db import upload_service
from hyundap.db.errors import DbError


class IngestBridgeTests(unittest.TestCase):
    def test_missing_and_unreadable_source_preserve_safe_code(self):
        with TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing.pdf'
            with self.assertRaises(DbError) as caught:
                upload_service.prepare_upload_source(missing, {}, directory)
            self.assertEqual(caught.exception.code, 'FILE_UNAVAILABLE')
            source = Path(directory) / 'source.pdf'
            source.write_bytes(b'sample')
            with patch.object(upload_service, 'file_digest', side_effect=PermissionError('secret')):
                with self.assertRaises(DbError) as caught:
                    upload_service.prepare_upload_source(source, {}, directory)
            self.assertEqual(caught.exception.code, 'FILE_UNAVAILABLE')
            self.assertNotIn('secret', str(caught.exception))

    def test_verified_hash_is_passed_only_to_compatible_ingest(self):
        seen = []
        def ingest(path, metadata, *, find_by_hash, file_hash=None):
            seen.append(file_hash)
            return {'status': 'failed', 'error_code': 'CORRUPTED_FILE'}
        result = upload_service.ingest_and_save_chunks('file', {'company_id': 'c'},
                                                       {'version_id': 'v'}, ingest, 'a' * 64)
        self.assertEqual(result, (None, 'CORRUPTED_FILE'))
        self.assertEqual(seen, ['a' * 64])

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
