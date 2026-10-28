"""새 로컬 DB에서만 공통 인증·회사 원장 분리를 검증한다. Railway에는 실행하지 않는다."""

import hashlib
import io
import os
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import create_engine, inspect, text, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from hyundap.auth import session_service, permissions, accounts
from hyundap.auth.errors import AccessError
from hyundap.db import runtime, documents_repo, versions_repo, upload_service, version_service
from hyundap.db import tenancy, split_schema, company_admin
from hyundap.db import connection, schema
from hyundap.db.errors import DbError
from .test_upload_workflow import FakeEmbedding
from . import test_upload_workflow as workflow_tests

REAL_GET_ENGINE = runtime.get_engine

class SplitOfflineTests(unittest.TestCase):
    def test_identifier_and_mode_are_not_arbitrary_sql(self):
        for name in ('x;DROP DATABASE y', '../x', '', None, 'a' * 65):
            with self.subTest(name=name), self.assertRaises(DbError):
                tenancy.quoted_database(name)
        with patch.dict(os.environ, {'HYUNDAP_DB_LAYOUT': 'unknown'}), self.assertRaises(DbError):
            runtime.split_layout()


@unittest.skipUnless(os.getenv('DB_SPLIT_TEST_URL'), '격리 분리 MySQL URL 필요')
class SplitDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ['DB_SPLIT_TEST_URL'])
        if url.host != '127.0.0.1' or url.database != 'hyundap_split_test':
            raise RuntimeError('Local empty split test database required')
        cls.engine = create_engine(url, isolation_level='READ COMMITTED', hide_parameters=True,
                                  connect_args={'init_command': "SET time_zone='+00:00'"})
        cls.addClassCleanup(cls.engine.dispose)
        split_schema.create_split_schema(cls.engine)

    def setUp(self):
        self.mode = patch.dict(os.environ, {'HYUNDAP_DB_LAYOUT': 'split'})
        self.mode.start()
        self.addCleanup(self.mode.stop)
        engine_patch = patch.object(runtime, 'get_engine', return_value=self.engine)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)
        self.addCleanup(tenancy.close_company_engines)
        self.a = company_admin.provision_company('시험 회사 A', admin_engine=self.engine)
        self.b = company_admin.provision_company('시험 회사 B', admin_engine=self.engine)
        self.password = 'synthetic-split-password'
        self.email = uuid4().hex + '@test.invalid'
        accounts.create_account(self.email, 'platform_admin', None, self.password)
        self.session = session_service.login(self.email, self.password)
        self.actor = {'session_id': self.session['session_id'], '_session_token': self.session['token']}
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source.txt'
        self.source.write_text('시험용 원본 ' + uuid4().hex, encoding='utf-8')
        self.embedding = FakeEmbedding()

    def upload(self, company, backend=None, ingest=None, **extra):
        # 기존 수집 대역은 Chunk 소속을 실제 업로드 정보로 만든다. 외부 API는 부르지 않는다.
        return upload_service.upload_document(
            self.actor, self.source, dict(company_id=company['company_id'], doc_type='manual', **extra),
            upload_dir=self.root / 'uploads', embedding=backend or self.embedding,
            ingest_document=ingest or (lambda path, info, **kwargs: workflow_tests.UploadWorkflowTests.ingest(self, path, info, **kwargs)))

    def test_shared_login_and_physical_company_storage(self):
        a, b = self.upload(self.a), self.upload(self.b)
        self.assertEqual((a['status'], b['status']), ('success', 'success'))
        with self.engine.connect() as database:
            self.assertFalse(split_schema.validate_control(database))
        self.assertNotIn('documents', inspect(self.engine).get_table_names())
        for company, result in ((self.a, a), (self.b, b)):
            engine = tenancy.get_company_engine(company['company_id'])
            tables = set(inspect(engine).get_table_names())
            self.assertNotIn('users', tables)
            self.assertNotIn('sessions', tables)
            with engine.connect() as database:
                self.assertEqual(database.scalar(text('SELECT COUNT(*) FROM documents')), 1)
                self.assertFalse(split_schema.validate_company(database, self.engine.url.database, company['company_id']))
            detail = documents_repo.get_document_detail(self.actor, result['document_id'])
            self.assertEqual(detail['company_id'], company['company_id'])
            self.assertTrue(versions_repo.list_document_versions(self.actor, result['document_id'])[0]['is_active'])
        rows = documents_repo.get_documents_with_active_version([a['document_id'], b['document_id']])
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]), 11)
        permissions.verify_documents_access(self.actor, [(a['document_id'], a['version_id']), (b['document_id'], b['version_id'])])
        session_service.logout(self.session)
        with self.assertRaises(AccessError):
            documents_repo.get_document_detail(self.actor, a['document_id'])

    def test_wrong_company_and_wrong_connection_are_denied(self):
        result = self.upload(self.a)
        other_email = uuid4().hex + '@test.invalid'
        accounts.create_account(other_email, 'employee', self.b['company_id'], self.password)
        session = session_service.login(other_email, self.password)
        actor = {'session_id': session['session_id'], '_session_token': session['token']}
        with self.assertRaises(AccessError) as caught:
            documents_repo.get_document_detail(actor, result['document_id'])
        self.assertEqual(caught.exception.code, 'ACCESS_DENIED')
        with tenancy.get_company_engine(self.b['company_id']).connect() as database:
            with self.assertRaises(DbError):
                documents_repo.get_document_detail(self.actor, result['document_id'], connection=database)
        with self.assertRaises(AccessError):
            upload_service.upload_document(actor, self.source, {'company_id': self.a['company_id']},
                embedding=self.embedding, ingest_document=lambda *args: None, upload_dir=self.root)

    def test_retry_and_publication_recovery_preserve_old_original(self):
        first = self.upload(self.a)
        self.source.write_bytes(b'next source version')
        self.embedding.partial = True
        failed = self.upload(self.a, document_id=first['document_id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(documents_repo.get_document_detail(self.actor, first['document_id'])['version_id'], first['version_id'])
        self.embedding.partial = False
        retried = upload_service.retry_upload(self.actor, failed['version_id'], embedding=self.embedding,
            ingest_document=lambda path, info, **kwargs: workflow_tests.UploadWorkflowTests.ingest(self, path, info, **kwargs))
        self.assertEqual(retried['status'], 'success')
        self.assertEqual(version_service.recover_publication(first['document_id'], retried['version_id'], embedding=self.embedding)['phase'], 'activated')

    def test_different_companies_process_in_parallel(self):
        barrier = Barrier(2, timeout=10)
        def ingest(path, info, **kwargs):
            barrier.wait()
            return workflow_tests.UploadWorkflowTests.ingest(self, path, info, **kwargs)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(self.upload, company, FakeEmbedding(), ingest) for company in (self.a, self.b)]
            self.assertEqual([job.result(timeout=25)['status'] for job in jobs], ['success', 'success'])

    def test_provision_failure_is_not_exposed_and_ready_request_is_idempotent(self):
        repeated = company_admin.provision_company('시험 회사 A', admin_engine=self.engine, company_id=self.a['company_id'])
        self.assertEqual(repeated['database_name'], self.a['database_name'])
        company_id = str(uuid4())
        with patch.object(company_admin, 'create_split_schema', side_effect=ValueError('synthetic-secret')):
            with self.assertRaises(DbError) as caught:
                company_admin.provision_company('실패 회사', company_id=company_id, admin_engine=self.engine)
        self.assertEqual(caught.exception.code, 'PROVISION_FAILED')
        self.assertNotIn('synthetic-secret', str(caught.exception))
        with self.engine.connect() as database:
            self.assertEqual(database.scalar(text('SELECT status FROM company_databases WHERE company_id=:id'), {'id': company_id}), 'failed')
        with self.assertRaises(DbError):
            tenancy.get_company_engine(company_id)

    def test_tenant_identity_tampering_and_context_escape_fail_closed(self):
        engine = tenancy.get_company_engine(self.a['company_id'])
        with tenancy.company_scope(self.a['company_id']):
            with self.assertRaises(DbError):
                with tenancy.company_scope(self.b['company_id']):
                    pass
        with engine.begin() as database:
            database.execute(text('UPDATE tenant_identity SET company_id=:id'), {'id': self.b['company_id']})
        tenancy.close_company_engines()
        with self.assertRaises(DbError):
            tenancy.get_company_engine(self.a['company_id'])

    def test_route_and_company_ledger_rollback_together(self):
        engine = tenancy.get_company_engine(self.a['company_id'])
        def fail_after_route(connection, cursor, statement, parameters, context, executemany):
            if 'INSERT INTO' in statement and 'document_routes' in statement:
                raise RuntimeError('synthetic transaction failure')
        event.listen(engine, 'after_cursor_execute', fail_after_route)
        try:
            with self.assertRaises(RuntimeError):
                self.upload(self.a)
        finally:
            event.remove(engine, 'after_cursor_execute', fail_after_route)
        with engine.connect() as database:
            for table in ('documents', 'document_versions', 'index_jobs'):
                self.assertEqual(database.scalar(text('SELECT COUNT(*) FROM ' + table)), 0)
        with self.engine.connect() as database:
            self.assertEqual(database.scalar(text('SELECT COUNT(*) FROM document_routes WHERE company_id=:id'),
                                            {'id': self.a['company_id']}), 0)

    def test_cached_company_is_blocked_when_disabled(self):
        tenancy.get_company_engine(self.a['company_id'])
        with self.engine.begin() as database:
            database.execute(text('UPDATE companies SET is_active=0 WHERE id=:id'), {'id': self.a['company_id']})
        with self.assertRaises(DbError):
            tenancy.get_company_engine(self.a['company_id'])

    def test_real_control_engine_and_registry_validation(self):
        url = self.engine.url.render_as_string(hide_password=False)
        REAL_GET_ENGINE.cache_clear()
        try:
            with patch.dict(os.environ, {'DATABASE_URL': url}), patch.object(runtime, 'get_engine', REAL_GET_ENGINE):
                control = runtime.get_engine()
                self.assertEqual(control.url.database, self.engine.url.database)
                control.dispose()
            with self.engine.begin() as database:
                database.execute(text('ALTER TABLE company_databases DROP CHECK ck_registry_version'))
                self.assertTrue(split_schema.validate_control(database))
                database.execute(text('ALTER TABLE company_databases ADD CONSTRAINT ck_registry_version CHECK (schema_version=4)'))
                self.assertFalse(split_schema.validate_control(database))
        finally:
            REAL_GET_ENGINE.cache_clear()

    def test_provision_and_upload_with_separate_dml_account(self):
        # 루트 권한으로만 성공하는 시험이 되지 않도록 앱 계정을 DML로 제한한다.
        from hyundap.db.connection import create_db_engine
        username = 'app_' + uuid4().hex[:16]
        password = uuid4().hex
        with self.engine.connect() as database:
            database.execute(text('CREATE USER :user@:host IDENTIFIED BY :password'),
                             {'user': username, 'host': '%', 'password': password})
            database.execute(text('GRANT SELECT,INSERT,UPDATE,DELETE ON ' +
                                  tenancy.quoted_database(self.engine.url.database) + '.* TO :user@:host'),
                             {'user': username, 'host': '%'})
        app_engine = create_db_engine(self.engine.url.set(username=username, password=password).render_as_string(hide_password=False))
        try:
            with patch.object(runtime, 'get_engine', return_value=app_engine):
                company = company_admin.provision_company('별도 업무 계정 회사', admin_engine=self.engine)
                result = self.upload(company)
                self.assertEqual(result['status'], 'success')
                self.assertEqual(documents_repo.get_document_detail(self.actor, result['document_id'])['company_id'], company['company_id'])
        finally:
            tenancy.close_company_engines()
            app_engine.dispose()

    def test_layout_is_reused_within_one_check_without_mutating_snapshot(self):
        output = io.StringIO()
        with patch.object(connection, 'create_db_engine', return_value=self.engine), \
                patch.object(schema, '_read_layout', wraps=schema._read_layout) as read_snapshot, \
                patch.object(split_schema, '_read_layout', wraps=split_schema._read_layout) as read_again, \
                patch.object(split_schema, '_structure', wraps=split_schema._structure) as normalize, \
                redirect_stdout(output):
            self.assertEqual(connection.main(), 0)
            read_snapshot.assert_called_once()
            read_again.assert_not_called()
            normalize.assert_called_once()
        self.assertIn('DB_CONNECTION_OK: schema_version=4', output.getvalue())
        # 다른 호출에서는 다시 실측한다. 이 최적화는 요청 사이의 캐시가 아니다.
        with self.engine.connect() as database, \
                patch.object(split_schema, '_read_layout', wraps=split_schema._read_layout) as read:
            self.assertFalse(split_schema.validate_control(database))
            read.assert_called_once()
        company = tenancy.get_company_engine(self.a['company_id'])
        with company.connect() as database:
            layout = schema._read_layout(database)
            before = deepcopy(layout)
            with patch.object(split_schema, '_read_layout', return_value=layout), \
                    patch.object(split_schema, '_structure', wraps=split_schema._structure) as normalize:
                self.assertFalse(split_schema.validate_company(database, self.engine.url.database, self.a['company_id']))
                normalize.assert_called_once()
            self.assertEqual(layout, before)

    def test_batch_routes_use_one_select_and_keep_current_versions(self):
        first = self.upload(self.a)
        other = self.upload(self.b)
        self.source.write_bytes(b'a new version for batch routing')
        latest = self.upload(self.a, document_id=first['document_id'])
        queries = []
        def capture(connection, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith('SELECT') and 'document_routes' in statement:
                queries.append(statement)
        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            rows = documents_repo.get_documents_with_active_version(
                [first['document_id'], other['document_id'], first['document_id'], 'missing-document'])
            self.assertEqual(len(queries), 1)
            self.assertEqual(len(rows), 2)
            by_id = {row['document_id']: row for row in rows}
            self.assertEqual(by_id[first['document_id']]['active_version_id'], latest['version_id'])
            self.assertEqual(by_id[other['document_id']]['active_version_id'], other['version_id'])
            self.assertTrue(all(len(row) == 11 for row in rows))
            queries.clear()
            with tenancy.get_company_engine(self.a['company_id']).connect() as database:
                self.assertEqual(len(documents_repo.get_documents_with_active_version(
                    [first['document_id']], connection=database)), 1)
            self.assertEqual(queries, [])
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)

    def test_batch_routes_skip_ambiguous_owners_and_propagate_database_failure(self):
        first, other = self.upload(self.a), self.upload(self.b)
        conflicting_version = str(uuid4())
        with self.engine.begin() as database:
            database.execute(text('''INSERT INTO document_routes (version_id,document_id,job_id,company_id)
                VALUES (:version,:document,:job,:company)'''),
                {'version': conflicting_version, 'document': first['document_id'],
                 'job': str(uuid4()), 'company': self.b['company_id']})
        try:
            with self.assertRaises(DbError) as caught:
                tenancy.company_for_reference(document_id=first['document_id'])
            self.assertEqual(caught.exception.code, 'INVALID_STATE')
            rows = documents_repo.get_documents_with_active_version(
                [first['document_id'], other['document_id'], 'missing-document'])
            self.assertEqual([row['document_id'] for row in rows], [other['document_id']])
        finally:
            with self.engine.begin() as database:
                database.execute(text('DELETE FROM document_routes WHERE version_id=:id'), {'id': conflicting_version})
        def fail_route(connection, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith('SELECT') and 'document_routes' in statement:
                raise OperationalError(statement, {}, Exception('synthetic-private-value'))
        event.listen(self.engine, 'before_cursor_execute', fail_route)
        try:
            with self.assertRaises(DbError) as caught:
                documents_repo.get_documents_with_active_version([first['document_id']])
            self.assertEqual(caught.exception.code, 'DB_UNAVAILABLE')
            self.assertNotIn('synthetic-private-value', str(caught.exception))
        finally:
            event.remove(self.engine, 'before_cursor_execute', fail_route)
