"""최종 v4 직접 생성 DB의 상태·재시도·복구 계약을 합성 자료로 검증한다."""

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
from hyundap.db import documents_repo, jobs_repo, clock, runtime, schema, sessions_repo, upload_service, version_service
from hyundap.db.errors import DbError
from . import test_upload_workflow as workflow_fixture


@unittest.skipUnless(os.getenv('DB_CONTRACT_TEST_URL'), 'isolated MySQL URL required')
class FinalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ['DB_CONTRACT_TEST_URL'])
        if url.host != '127.0.0.1' or url.database != 'hyundap_contract_test':
            raise RuntimeError('Only the dedicated local final contract test database is allowed')
        cls.engine = create_engine(url, isolation_level='READ COMMITTED', hide_parameters=True,
                                   connect_args={'init_command': "SET time_zone='+00:00'"})
        cls.addClassCleanup(cls.engine.dispose)
        schema.create_v4_schema(cls.engine)

    def setUp(self):
        self.company = str(uuid4())
        self.user = str(uuid4())
        self.session = str(uuid4())
        self.token = 'synthetic-session-token-' + uuid4().hex
        now = clock.epoch_now()
        with self.engine.begin() as database:
            database.execute(text('INSERT INTO companies (id,name,created_at,updated_at) VALUES (:id,\'Synthetic\',:now,:now)'),
                             {'id': self.company, 'now': now})
            database.execute(text('''INSERT INTO users
                (id,company_id,email,role,created_at,updated_at)
                VALUES (:id,:company,:email,'company_admin',:now,:now)'''),
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

    def test_upload_and_second_publication_on_final_constraints(self):
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
                    row = database.execute(text('SELECT status,attempts FROM index_jobs WHERE id=:id'),
                                           {'id': result['job_id']}).mappings().one()
                    self.assertEqual((row['status'], row['attempts']), ('SUCCEEDED', 1))

    def test_both_deadlines_are_enforced(self):
        now = clock.epoch_now()
        for deadline in ('idle_expires_at', 'absolute_expires_at'):
            with self.engine.begin() as database:
                database.execute(text('UPDATE sessions SET created_at=:past,last_seen_at=:past, '
                    'idle_expires_at=:idle,absolute_expires_at=:absolute WHERE id=:id'),
                    {'id': self.session, 'past': now-100, 'idle': now+100, 'absolute': now+200})
                sessions_repo.current_actor(self.actor, connection=database)
                if deadline == 'absolute_expires_at':
                    database.execute(text('UPDATE sessions SET idle_expires_at=:past,absolute_expires_at=:past WHERE id=:id'),
                                     {'past': now-1, 'id': self.session})
                else:
                    database.execute(text('UPDATE sessions SET idle_expires_at=:past WHERE id=:id'),
                                     {'past': now-1, 'id': self.session})
                with self.assertRaises(AccessError):
                    sessions_repo.current_actor(self.actor, connection=database)

    def test_separate_documents_keep_distinct_version_storage_paths(self):
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
                    row = database.execute(text('''SELECT v.storage_path FROM documents d
                        JOIN document_versions v ON v.document_id=d.id WHERE v.id=:id'''),
                        {'id': result['version_id']}).mappings().one()
                    self.assertTrue(Path(row['storage_path']).is_file())
                    self.assertIn(result['version_id'], row['storage_path'])

    def test_recovery_finishes_job_only_after_cleanup_and_allows_next_candidate(self):
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

    def test_retry_resets_state_before_external_ingest_and_preserves_history(self):
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
                database.execute(text('''UPDATE index_jobs SET started_at=1 WHERE id=:id'''), {'id': failed['job_id']})
            observed = []
            def checked_ingest(path, info, **kwargs):
                with self.engine.connect() as database:
                    row = database.execute(text('SELECT * FROM index_jobs WHERE id=:id'),
                                           {'id': failed['job_id']}).mappings().one()
                    self.assertEqual((row['status'], row['stage'], row['attempts']),
                                     ('QUEUED', 'received', 2))
                    for name in ('finished_at', 'started_at'):
                        self.assertIsNone(row[name])
                    observed.append(True)
                return workflow_fixture.UploadWorkflowTests.ingest(self, path, info, **kwargs)
            retried = upload_service.retry_upload(self.actor, failed['version_id'],
                ingest_document=checked_ingest, embedding=backend)
            self.assertEqual(retried['status'], 'success')
            self.assertEqual(retried['job_id'], failed['job_id'])
            self.assertEqual(observed, [True])
            with self.engine.connect() as database:
                row = database.execute(text('SELECT status,attempts,report FROM index_jobs WHERE id=:id'),
                                       {'id': failed['job_id']}).mappings().one()
                self.assertEqual((row['status'], row['attempts']), ('SUCCEEDED', 2))
                report = json.loads(row['report']) if isinstance(row['report'], str) else row['report']
                self.assertEqual(len(report['_attempts']), 1)

    def test_retry_active_job_conflict_rolls_back_version_and_job(self):
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
                row = database.execute(text('''SELECT j.status,j.stage,j.attempts,v.version_status
                    FROM index_jobs j JOIN document_versions v ON v.id=j.version_id WHERE j.id=:id'''),
                    {'id': ids['job_id']}).mappings().one()
                self.assertEqual((row['status'], row['stage'], row['attempts'], row['version_status']),
                                 ('FAILED', 'received', 1, 'failed'))


if __name__ == '__main__':
    unittest.main()
