"""실제 v3 DDL과 합성 자료만 사용하는 로컬 MySQL 호환성 회귀 검증."""

import hashlib
import json
from functools import partial
import os
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from hyundap.auth.errors import AccessError
from hyundap.db import documents_repo, jobs_repo, legacy, runtime, schema, sessions_repo, upload_service, version_service
from hyundap.db.errors import DbError
from .test_schema_migration import execute_script
from . import test_upload_workflow as workflow_fixture


class LegacyConfigurationTests(unittest.TestCase):
    def test_project_default_is_unix_seconds(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(legacy.time, 'time_ns', return_value=123456789000):
            self.assertEqual(legacy.epoch_now(), 123)

    def test_unknown_time_unit_is_blocked(self):
        for unit in ('', 'unknown'):
            with patch.dict(os.environ, {'DB_LEGACY_TIME_UNIT': unit}):
                with self.assertRaises(DbError):
                    legacy.epoch_now()
                with self.assertRaisesRegex(ValueError, 'Confirm legacy time unit'):
                    schema.prepare_legacy_sql(None, unit)

    def test_both_confirmed_time_units(self):
        for unit, expected in (('seconds', 123), ('milliseconds', 123456)):
            with patch.dict(os.environ, {'DB_LEGACY_TIME_UNIT': unit}), \
                    patch.object(legacy.time, 'time_ns', return_value=123456789000):
                self.assertEqual(legacy.epoch_now(), expected)


@unittest.skipUnless(os.getenv('DB_LEGACY_TEST_URL'), 'isolated MySQL URL required')
class LegacyDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ['DB_LEGACY_TEST_URL'])
        if url.host != '127.0.0.1' or url.database != 'hyundap_legacy_test':
            raise RuntimeError('Only the dedicated local legacy test database is allowed')
        cls.engine = create_engine(url, isolation_level='READ COMMITTED', hide_parameters=True,
                                   connect_args={'init_command': "SET time_zone='+00:00'"})
        cls.addClassCleanup(cls.engine.dispose)
        with cls.engine.begin() as database:
            if inspect(database).get_table_names():
                raise RuntimeError('Refuse an existing database')
            fixture = (Path(__file__).parent / 'fixtures' / 'railway_v3_schema.sql').read_text(encoding='utf-8')
            database.execute(text('SET FOREIGN_KEY_CHECKS=0'))
            try:
                execute_script(database, fixture)
            finally:
                database.execute(text('SET FOREIGN_KEY_CHECKS=1'))
            database.execute(text('INSERT INTO schema_migrations VALUES (3)'))
            cls.original = schema.snapshot(database)
            cls.migration = schema.prepare_legacy_sql(database, 'seconds')
            execute_script(database, cls.migration)
            # 실제 CLI는 버전을 바꾸지 않는다. 테스트에서만 검증 완료 후 확정한다.
            if schema.validate_v4(schema.snapshot(database)):
                raise AssertionError('Local v4 structure is incomplete')
            database.execute(text('UPDATE schema_migrations SET version=4 WHERE version=3'))

    def setUp(self):
        self.unit = patch.dict(os.environ, {'DB_LEGACY_TIME_UNIT': 'seconds'})
        self.unit.start()
        self.addCleanup(self.unit.stop)
        self.company = str(uuid4())
        self.user = str(uuid4())
        self.session = str(uuid4())
        self.token = 'synthetic-session-token-' + uuid4().hex
        now = legacy.epoch_now()
        with self.engine.begin() as database:
            database.execute(text('INSERT INTO companies (id,name,created_at,updated_at) VALUES (:id,\'Synthetic\',:now,:now)'),
                             {'id': self.company, 'now': now})
            database.execute(text('''INSERT INTO users
                (id,company_id,email,role,created_at,updated_at)
                VALUES (:id,:company,:email,'COMPANY_ADMIN',:now,:now)'''),
                {'id': self.user, 'company': self.company, 'email': self.user + '@test.invalid', 'now': now})
            database.execute(text('''INSERT INTO sessions
                (id,user_id,token_hash,csrf_token_hash,created_at,last_seen_at,
                 idle_expires_at,absolute_expires_at,auth_version,expires_at)
                VALUES (:id,:user,:hash,:hash,:now,:now,:idle,:absolute,1,'2099-01-01')'''),
                {'id': self.session, 'user': self.user, 'hash': hashlib.sha256(self.token.encode()).hexdigest(),
                 'now': now, 'idle': now + 1800, 'absolute': now + 3600})
        self.actor = {'session_id': self.session, '_session_token': self.token}
        self.metadata = {'company_id': self.company, 'file_name': 'sample.pdf',
                         'storage_path': 'synthetic/' + uuid4().hex, 'sha256': uuid4().hex * 2,
                         'file_size': 10, 'data_category': 'operational'}

    def test_original_mismatch_and_normalized_structure(self):
        self.assertEqual(len(schema.validate_v4(self.original)), 36)  # 기존 35 + 누락됐던 collation 진단
        with self.assertRaisesRegex(ValueError, 'documents.deleted_at'):
            schema.prepare_sql(self.original)
        with self.engine.connect() as database:
            self.assertEqual(schema.validate_v4(schema.snapshot(database)), [])
            self.assertTrue(all(row['violations'] == 0 for row in schema.audit_counts(database)))
        self.assertNotIn('UPDATE schema_migrations', self.migration)

    def test_old_insert_fails_and_compatible_insert_supports_next_version(self):
        with self.assertRaises(OperationalError):
            with self.engine.begin() as database:
                database.execute(text('INSERT INTO documents (id,company_id,doc_type) VALUES (:id,:company,\'other\')'),
                                 {'id': str(uuid4()), 'company': self.company})
        with self.engine.begin() as database:
            ids = documents_repo.create_document_version(self.actor, self.metadata, connection=database)
            row = database.execute(text('SELECT filename,uploaded_by,generation FROM documents WHERE id=:id'),
                                   {'id': ids['document_id']}).mappings().one()
            self.assertEqual((row['filename'], row['uploaded_by'], row['generation']), ('sample.pdf', self.user, 1))
            jobs_repo.record_stage_result(ids['job_id'], {'stage': 'complete', 'status': 'success'}, connection=database)
            self.assertEqual(database.execute(text('SELECT status FROM index_jobs WHERE id=:id'),
                                             {'id': ids['job_id']}).scalar_one(), 'SUCCEEDED')
            next_ids = documents_repo.create_document_version(self.actor, dict(self.metadata,
                document_id=ids['document_id'], sha256=uuid4().hex * 2), connection=database)
            self.assertEqual(database.execute(text('SELECT generation FROM index_jobs WHERE id=:id'),
                                             {'id': next_ids['job_id']}).scalar_one(), 2)

    def test_unknown_unit_blocks_storage_without_partial_rows(self):
        with patch.dict(os.environ, {'DB_LEGACY_TIME_UNIT': ''}), self.assertRaises(DbError):
            with self.engine.begin() as database:
                documents_repo.create_document_version(self.actor, self.metadata, connection=database)
        with self.engine.connect() as database:
            self.assertEqual(database.execute(text('SELECT COUNT(*) FROM documents WHERE company_id=:id'),
                                             {'id': self.company}).scalar_one(), 0)

    def test_upload_and_second_publication_on_actual_legacy_constraints(self):
        backend = workflow_fixture.FakeEmbedding()
        with TemporaryDirectory() as directory, patch.object(runtime, 'get_engine', return_value=self.engine):
            source = Path(directory) / 'sample.txt'
            for index in range(2):
                source.write_text('synthetic source ' + str(index), encoding='utf-8')
                metadata = {'company_id': self.company, 'doc_type': 'manual'}
                if index:
                    metadata['document_id'] = result['document_id']
                result = upload_service.upload_document(self.actor, source, metadata,
                    ingest_document=partial(workflow_fixture.UploadWorkflowTests.ingest, self),
                    embedding=backend, upload_dir=Path(directory) / 'uploads')
                self.assertEqual(result['status'], 'success')
                with self.engine.connect() as database:
                    row = database.execute(text('SELECT status,generation FROM index_jobs WHERE id=:id'),
                                           {'id': result['job_id']}).mappings().one()
                    self.assertEqual((row['status'], row['generation']), ('SUCCEEDED', index + 1))

    def test_both_legacy_deadlines_are_enforced(self):
        for unit in ('seconds', 'milliseconds'):
            with patch.dict(os.environ, {'DB_LEGACY_TIME_UNIT': unit}):
                now = legacy.epoch_now()
                with self.engine.begin() as database:
                    database.execute(text('''UPDATE sessions SET created_at=:start,last_seen_at=:start,
                        idle_expires_at=:idle,absolute_expires_at=:absolute WHERE id=:id'''),
                        {'id': self.session, 'start': now - 100, 'idle': now + 100, 'absolute': now + 200})
                    sessions_repo.current_actor(self.actor, connection=database)
                    database.execute(text('UPDATE sessions SET idle_expires_at=:expiry WHERE id=:id'),
                                     {'id': self.session, 'expiry': now - 1})
                    with self.assertRaises(AccessError):
                        sessions_repo.current_actor(self.actor, connection=database)

    def test_separate_documents_keep_distinct_final_storage_keys(self):
        backend = workflow_fixture.FakeEmbedding()
        with TemporaryDirectory() as directory, patch.object(runtime, 'get_engine', return_value=self.engine):
            for index in range(2):
                source = Path(directory) / f'document-{index}.txt'
                source.write_text('distinct source ' + uuid4().hex, encoding='utf-8')
                result = upload_service.upload_document(self.actor, source,
                    {'company_id': self.company, 'doc_type': 'manual'},
                    ingest_document=partial(workflow_fixture.UploadWorkflowTests.ingest, self),
                    embedding=backend, upload_dir=Path(directory) / 'uploads')
                self.assertEqual(result['status'], 'success', result)
                with self.engine.connect() as database:
                    row = database.execute(text('''SELECT d.storage_key,v.storage_path FROM documents d
                        JOIN document_versions v ON v.document_id=d.id WHERE v.id=:id'''),
                        {'id': result['version_id']}).mappings().one()
                    self.assertEqual(row['storage_key'], row['storage_path'])

    def test_prepare_refuses_data_bearing_tables(self):
        with self.engine.connect() as database:
            with self.assertRaisesRegex(ValueError, 'requires empty table'):
                schema.prepare_legacy_sql(database, 'seconds')

    def test_recovery_finishes_legacy_job_only_after_cleanup_and_allows_next_candidate(self):
        backend = workflow_fixture.FakeEmbedding()
        backend.fail_delete = True
        def fail_activation():
            raise RuntimeError('synthetic activation failure')
        backend.on_activate = fail_activation
        with TemporaryDirectory() as directory, patch.object(runtime, 'get_engine', return_value=self.engine):
            source = Path(directory) / 'recovery.txt'
            source.write_text('recovery fixture ' + uuid4().hex, encoding='utf-8')
            ingest = partial(workflow_fixture.UploadWorkflowTests.ingest, self)
            result = upload_service.upload_document(self.actor, source,
                {'company_id': self.company, 'doc_type': 'manual'}, ingest_document=ingest,
                embedding=backend, upload_dir=Path(directory) / 'uploads')
            self.assertTrue(result['recovery_required'])
            with self.engine.connect() as database:
                pending = database.execute(text('SELECT status,finished_at FROM index_jobs WHERE id=:id'),
                                           {'id': result['job_id']}).mappings().one()
                self.assertEqual(pending['status'], 'QUEUED')
                self.assertIsNone(pending['finished_at'])
            backend.fail_delete = False
            backend.on_activate = None
            recovered = version_service.recover_publication(result['document_id'], result['version_id'], embedding=backend)
            self.assertEqual(recovered['phase'], 'restored')
            with self.engine.connect() as database:
                finished = database.execute(text('SELECT status,finished_at FROM index_jobs WHERE id=:id'),
                                            {'id': result['job_id']}).mappings().one()
                self.assertEqual(finished['status'], 'FAILED')
                self.assertIsNotNone(finished['finished_at'])
            self.assertEqual(version_service.recover_publication(
                result['document_id'], result['version_id'], embedding=backend)['phase'], 'restored')
            source.write_text('next candidate ' + uuid4().hex, encoding='utf-8')
            next_result = upload_service.upload_document(self.actor, source,
                {'company_id': self.company, 'doc_type': 'manual', 'document_id': result['document_id']},
                ingest_document=ingest, embedding=backend, upload_dir=Path(directory) / 'uploads')
            self.assertEqual(next_result['status'], 'success')

    def test_retry_resets_legacy_state_before_external_ingest_and_preserves_history(self):
        backend = workflow_fixture.FakeEmbedding()
        def failed_ingest(path, info, **kwargs):
            return {'status': 'failed', 'error_code': 'EMPTY_DOCUMENT', 'chunks': [],
                    'report': {'status': 'failed', 'error_code': 'EMPTY_DOCUMENT'}}
        with TemporaryDirectory() as directory, patch.object(runtime, 'get_engine', return_value=self.engine):
            source = Path(directory) / 'retry.txt'
            source.write_text('retry fixture ' + uuid4().hex, encoding='utf-8')
            failed = upload_service.upload_document(self.actor, source,
                {'company_id': self.company, 'doc_type': 'manual'}, ingest_document=failed_ingest,
                embedding=backend, upload_dir=Path(directory) / 'uploads')
            self.assertEqual(failed['status'], 'failed')
            with self.engine.begin() as database:
                database.execute(text('''UPDATE index_jobs SET lease_token='synthetic-old-lease',
                    lease_expires_at=1,heartbeat_at=1,started_at=1 WHERE id=:id'''), {'id': failed['job_id']})
            observed = []
            def checked_ingest(path, info, **kwargs):
                with self.engine.connect() as database:
                    row = database.execute(text('SELECT * FROM index_jobs WHERE id=:id'),
                                           {'id': failed['job_id']}).mappings().one()
                    self.assertEqual((row['status'], row['stage'], row['attempt_no'], row['attempts']),
                                     ('QUEUED', 'received', 2, 2))
                    for name in ('finished_at', 'lease_token', 'lease_expires_at', 'heartbeat_at', 'started_at'):
                        self.assertIsNone(row[name])
                    observed.append(True)
                return workflow_fixture.UploadWorkflowTests.ingest(self, path, info, **kwargs)
            retried = upload_service.retry_upload(self.actor, failed['version_id'],
                ingest_document=checked_ingest, embedding=backend)
            self.assertEqual(retried['status'], 'success')
            self.assertEqual(retried['job_id'], failed['job_id'])
            self.assertEqual(observed, [True])
            with self.engine.connect() as database:
                row = database.execute(text('SELECT status,attempt_no,attempts,report FROM index_jobs WHERE id=:id'),
                                       {'id': failed['job_id']}).mappings().one()
                self.assertEqual((row['status'], row['attempt_no'], row['attempts']), ('SUCCEEDED', 2, 2))
                report = json.loads(row['report']) if isinstance(row['report'], str) else row['report']
                self.assertEqual(len(report['_attempts']), 1)

    def test_retry_active_job_conflict_rolls_back_both_state_systems(self):
        with patch.object(runtime, 'get_engine', return_value=self.engine):
            with self.engine.begin() as database:
                ids = documents_repo.create_document_version(self.actor, self.metadata, connection=database)
                database.execute(text("UPDATE document_versions SET version_status='failed' WHERE id=:id"),
                                 {'id': ids['version_id']})
                jobs_repo.record_stage_result(ids['job_id'], {'stage': 'received', 'status': 'failed'}, connection=database)
                database.execute(text('UPDATE index_jobs SET attempts=1,report=:report WHERE id=:id'),
                                 {'id': ids['job_id'], 'report': '{"_upload_root":"synthetic"}'})
                documents_repo.create_document_version(self.actor, dict(self.metadata,
                    document_id=ids['document_id'], sha256=uuid4().hex * 2), connection=database)
            with self.assertRaises(DbError) as conflict:
                upload_service.prepare_retry(self.actor, ids['version_id'])
            self.assertEqual(conflict.exception.code, 'CONFLICT')
            with self.engine.connect() as database:
                row = database.execute(text('''SELECT j.status,j.stage,j.attempts,j.attempt_no,v.version_status
                    FROM index_jobs j JOIN document_versions v ON v.id=j.version_id WHERE j.id=:id'''),
                    {'id': ids['job_id']}).mappings().one()
                self.assertEqual((row['status'], row['stage'], row['attempts'], row['attempt_no'], row['version_status']),
                                 ('FAILED', 'received', 1, 1, 'failed'))


if __name__ == '__main__':
    unittest.main()
