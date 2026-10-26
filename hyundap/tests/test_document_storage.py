"""DB P1 문서 저장 입력과 중복 조회 계약. SQL 조회는 메모리 SQLite로 검증한다.

MySQL 잠금·경쟁·제약 검증은 test_db_runtime의 격리 MySQL 테스트가 담당한다.
"""

import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, text

from hyundap.common.schemas import DOC_TYPE_CODES
from hyundap.db import documents_repo, runtime
from hyundap.db.errors import DbError


class DocumentStorageTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {'company_id': 'company-a', 'file_name': 'sample.pdf',
                         'storage_path': 'data/sample.pdf', 'sha256': 'a' * 64,
                         'file_size': 10, 'data_category': 'operational'}

    def test_metadata_uses_common_types_and_does_not_mutate_input(self):
        original = dict(self.metadata)
        self.assertEqual(documents_repo.validate_metadata(self.metadata)['doc_type'], 'other')
        self.assertEqual(self.metadata, original)
        for code in DOC_TYPE_CODES:
            self.assertEqual(documents_repo.validate_metadata(
                dict(self.metadata, doc_type=code))['doc_type'], code)

    def test_invalid_metadata_is_rejected_before_database_access(self):
        cases = [{'doc_type': value} for value in (None, '', 'unknown', [], True)]
        cases += [{'file_size': value} for value in (True, 0, -1, 104857601)]
        cases += [{'sha256': 'A' * 64}, {'file_name': 'x' * 256},
                  {'file_name': 'bad\x00.pdf'}, {'storage_path': ' '},
                  {'data_category': 'unknown'}]
        with patch.object(runtime, 'get_engine') as engine:
            for changes in cases:
                with self.subTest(changes=changes), self.assertRaises(DbError) as caught:
                    documents_repo.create_document_version({}, dict(self.metadata, **changes))
                self.assertEqual(caught.exception.code, 'INVALID_INPUT')
            engine.assert_not_called()

    def test_metadata_boundaries_and_extra_fields(self):
        values = dict(self.metadata, file_size=104857600, file_name='x' * 255,
                      storage_path='x' * 1024, document_id='document-a', secret='discard')
        clean = documents_repo.validate_metadata(values)
        self.assertNotIn('secret', clean)
        self.assertEqual(clean['document_id'], 'document-a')
        self.assertEqual(clean['file_size'], 104857600)

    def test_duplicate_lookup_excludes_only_a_matching_server_candidate(self):
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with engine.begin() as database:
            database.execute(text('CREATE TABLE documents (id TEXT, company_id TEXT, deleted_at TEXT)'))
            database.execute(text('CREATE TABLE document_versions (id TEXT, document_id TEXT, company_id TEXT, sha256 TEXT, version_status TEXT, UNIQUE(company_id,sha256))'))
            for company, version in (('company-a', 'version-a'), ('company-b', 'version-b')):
                database.execute(text('INSERT INTO documents VALUES (:company,:company,:deleted)'),
                                 {'company': company, 'deleted': '2026-10-26'})
                database.execute(text('INSERT INTO document_versions VALUES (:version,:company,:company,:hash,:status)'),
                                 {'version': version, 'company': company, 'hash': 'a' * 64, 'status': 'failed'})
            match = documents_repo.find_duplicate_document('company-a', 'a' * 64, connection=database)
            self.assertEqual(match['version_id'], 'version-a')
            self.assertEqual(match['version_status'], 'failed')
            self.assertIsNotNone(match['deleted_at'])
            self.assertIsNone(documents_repo.find_duplicate_document(
                'company-a', 'a' * 64, exclude_version_id='version-a', connection=database))
            for company, digest, excluded in (
                    ('company-a', 'a' * 64, 'version-b'),
                    ('company-a', 'b' * 64, 'version-a'),
                    ('company-a', 'a' * 64, 'missing')):
                with self.subTest(excluded=excluded, company=company, digest=digest), self.assertRaises(DbError) as caught:
                    documents_repo.find_duplicate_document(company, digest,
                        exclude_version_id=excluded, connection=database)
                self.assertEqual(caught.exception.code, 'INVALID_INPUT')
            self.assertEqual(documents_repo.find_duplicate_document(
                'company-b', 'a' * 64, connection=database)['version_id'], 'version-b')
            self.assertIsNone(documents_repo.find_duplicate_document('company-a', 'b' * 64, connection=database))
            self.assertTrue(database.in_transaction())

    def test_candidate_creation_never_excludes_a_version_from_final_check(self):
        with patch.object(documents_repo, 'current_actor', return_value={
                'role': 'company_admin', 'company_id': 'company-a'}), \
                patch.object(documents_repo, 'find_duplicate_document', return_value={'version_id': 'v'}) as lookup:
            database = MagicMock()
            database.execute.return_value.scalar_one_or_none.return_value = 1
            database.execute.return_value.first.return_value = None
            with self.assertRaises(DbError) as caught:
                documents_repo._check_candidate_creation(database, {}, self.metadata)
            self.assertEqual(caught.exception.code, 'DUPLICATE_FILE')
            lookup.assert_called_once_with('company-a', 'a' * 64, connection=database)


if __name__ == '__main__':
    unittest.main()
