"""새 로컬 DB에서만 공통 인증·회사 원장 분리를 검증한다. Railway에는 실행하지 않는다."""

import hashlib
import io
import os
import multiprocessing
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


def process_upload(url, actor, company, source, root, start, results, barrier=None,
                   document_id=None, crash=False, retry_version=None):
    """새 Python 프로세스로 실제 SQL 업로드를 수행한다. AI·벡터는 대역이다."""
    engine = create_engine(url, isolation_level='READ COMMITTED', hide_parameters=True,
                           connect_args={'init_command': "SET time_zone='+00:00'"})
    def ingest(path, info, **kwargs):
        if barrier is not None:
            barrier.wait(15)
        return workflow_tests.UploadWorkflowTests.ingest(None, path, info, **kwargs)
    try:
        with patch.dict(os.environ, {'HYUNDAP_DB_LAYOUT': 'split', 'HYUNDAP_DB_COMPANY_ACCOUNTS': '0'}), \
                patch.object(runtime, 'get_engine', return_value=engine):
            start.wait(15)
            try:
                embedding = FakeEmbedding()
                if crash:
                    embedding.on_embed = lambda: os._exit(23)
                if retry_version:
                    result = upload_service.retry_upload(actor, retry_version, embedding=embedding,
                                                          ingest_document=ingest)
                else:
                    metadata = dict(company_id=company, doc_type='manual')
                    if document_id:
                        metadata['document_id'] = document_id
                    result = upload_service.upload_document(actor, source, metadata,
                        upload_dir=Path(root) / 'uploads', embedding=embedding, ingest_document=ingest)
                results.put(result['status'])
            except DbError as error:
                results.put(error.code)
    finally:
        tenancy.close_company_engines()
        engine.dispose()

class SplitOfflineTests(unittest.TestCase):
    def test_identifier_and_mode_are_not_arbitrary_sql(self):
        for name in ('x;DROP DATABASE y', '../x', '', None, 'a' * 65):
            with self.subTest(name=name), self.assertRaises(DbError):
                tenancy.quoted_database(name)
        with patch.dict(os.environ, {'HYUNDAP_DB_LAYOUT': 'unknown'}), self.assertRaises(DbError):
            runtime.split_layout()

    def test_company_account_mode_does_not_silently_fall_back(self):
        with patch.dict(os.environ, {'HYUNDAP_DB_LAYOUT': 'split', 'HYUNDAP_DB_COMPANY_ACCOUNTS': 'typo'}):
            with self.assertRaises(DbError):
                runtime.company_accounts_enabled()
        with patch.dict(os.environ, {'HYUNDAP_DB_LAYOUT': 'single', 'HYUNDAP_DB_COMPANY_ACCOUNTS': '1'}):
            with self.assertRaises(DbError):
                runtime.company_accounts_enabled()


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

    def test_process_uploads_serialize_same_company_and_allow_other_company(self):
        context = multiprocessing.get_context('spawn')
        url = self.engine.url.render_as_string(hide_password=False)
        # 같은 회사·같은 파일은 하나만 게시되고 두 번째는 중복으로 거부된다.
        for companies, expected, parallel in (
                ([self.a, self.a], ['success', 'DUPLICATE_FILE'], False),
                ([self.a, self.b], ['success', 'success'], True)):
            self.source.write_text(uuid4().hex, encoding='utf-8')
            start, results = context.Event(), context.Queue()
            barrier = context.Barrier(2) if parallel else None
            processes = []
            for company in companies:
                process = context.Process(target=process_upload, args=(url, self.actor,
                    company['company_id'], str(self.source), str(self.root), start, results, barrier))
                process.start()
                processes.append(process)
            start.set()
            for process in processes:
                process.join(45)
                if process.is_alive():
                    process.kill()
                    process.join()
                self.assertEqual(process.exitcode, 0)
            self.assertCountEqual([results.get(timeout=2) for _ in processes], expected)
            results.close()

    def test_killed_process_releases_lock_and_same_candidate_retry_is_not_duplicated(self):
        first = self.upload(self.a)
        self.source.write_text('new version after process exit', encoding='utf-8')
        context = multiprocessing.get_context('spawn')
        start, results = context.Event(), context.Queue()
        args = (self.engine.url.render_as_string(hide_password=False), self.actor,
                self.a['company_id'], str(self.source), str(self.root), start, results)
        child = context.Process(target=process_upload, args=args,
                                kwargs={'document_id': first['document_id'], 'crash': True})
        child.start()
        start.set()
        child.join(40)
        if child.is_alive():
            child.kill()
            child.join()
        self.assertEqual(child.exitcode, 23)
        self.assertEqual(documents_repo.get_document_detail(self.actor, first['document_id'])['version_id'], first['version_id'])
        with tenancy.get_company_engine(self.a['company_id']).connect() as database:
            row = database.execute(text('SELECT id,storage_path FROM document_versions WHERE document_id=:id AND version_no=2'),
                                   {'id': first['document_id']}).mappings().one()
        self.assertEqual(Path(row['storage_path']).read_bytes(), self.source.read_bytes())
        # 서로 다른 두 프로세스가 같은 후보를 재시도한다. 한 번만 게시할 수 있다.
        processes = [context.Process(target=process_upload, args=args,
                                     kwargs={'retry_version': row['id']}) for _ in range(2)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(40)
            if process.is_alive():
                process.kill()
                process.join()
            self.assertEqual(process.exitcode, 0)
        outcomes = [results.get(timeout=2) for _ in processes]
        self.assertEqual(outcomes.count('success'), 1)
        self.assertTrue(all(value in ('success', 'INVALID_STATE', 'CONFLICT') for value in outcomes), outcomes)
        self.assertEqual(documents_repo.get_document_detail(self.actor, first['document_id'])['version_id'], row['id'])
        with tenancy.get_company_engine(self.a['company_id']).connect() as database:
            self.assertEqual(database.scalar(text('SELECT COUNT(*) FROM document_versions WHERE document_id=:id'),
                                             {'id': first['document_id']}), 2)
        results.close()

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

    def test_company_pool_invalidation_preserves_other_company_and_work_locks(self):
        first = tenancy.get_company_engine(self.a['company_id'])
        other = tenancy.get_company_engine(self.b['company_id'])
        with tenancy.company_scope(self.a['company_id']):
            with tenancy.processing_lock(upload_service.WORKFLOW_LOCK):
                locks = dict(tenancy._workflow_locks)
                with first.connect() as running:
                    self.assertEqual(tenancy.invalidate_company_engine(self.a['company_id']), 1)
                    self.assertEqual(running.scalar(text('SELECT 1')), 1)
                self.assertEqual(tenancy._workflow_locks, locks)
        replacement = tenancy.get_company_engine(self.a['company_id'])
        self.assertIsNot(first, replacement)
        self.assertIs(other, tenancy.get_company_engine(self.b['company_id']))
        self.assertEqual(replacement.pool.checkedout(), 0)
        self.assertEqual(other.pool.checkedout(), 0)
        # 새 풀 생성 전 최대 32개 경계가 적용되는지 확인한다. 실제 32회사 부하 시험은 아니다.
        saved = dict(tenancy._engines)
        try:
            tenancy._engines.clear()
            tenancy._engines.update({('synthetic', index): other for index in range(32)})
            with self.assertRaises(DbError) as caught:
                tenancy.get_company_engine(self.a['company_id'])
            self.assertEqual(caught.exception.code, 'DB_UNAVAILABLE')
        finally:
            tenancy._engines.clear()
            tenancy._engines.update(saved)

    def test_company_pool_reads_rotated_secret_after_explicit_invalidation(self):
        import json
        credential_path = self.root / '.env.company-databases.json'
        account = company_admin.create_company_db_user(self.a['company_id'], admin_engine=self.engine,
                                                       credential_path=credential_path)
        settings = {'HYUNDAP_DB_COMPANY_ACCOUNTS': '1', 'HYUNDAP_COMPANY_CREDENTIALS_FILE': str(credential_path)}
        with patch.dict(os.environ, settings):
            previous = tenancy.get_company_engine(self.a['company_id'])
            replacement_password = uuid4().hex
            with self.engine.connect() as database:
                database.execute(text("ALTER USER :user@'%' IDENTIFIED BY :password"),
                                 {'user': account['username'], 'password': replacement_password})
            # 관리자 교체 절차를 격리 파일에서 재현한다. save_credential의 충돌 계약은 바꾸지 않는다.
            values = json.loads(credential_path.read_text(encoding='utf-8'))
            values[self.a['company_id']]['password'] = replacement_password
            credential_path.write_text(json.dumps(values), encoding='utf-8')
            tenancy.invalidate_company_engine(self.a['company_id'])
            current = tenancy.get_company_engine(self.a['company_id'])
            self.assertIsNot(current, previous)
            with current.connect() as database:
                self.assertEqual(database.scalar(text('SELECT CURRENT_USER()')), account['username'] + '@%')
            values.pop(self.a['company_id'])
            credential_path.write_text(json.dumps(values), encoding='utf-8')
            tenancy.invalidate_company_engine(self.a['company_id'])
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

    def test_control_service_minimum_privileges_authenticate_and_route_company_work(self):
        from hyundap.db.access_policy import grant_control_service_access
        from hyundap.db.download_service import prepare_original_download
        from sqlalchemy.exc import SQLAlchemyError
        credential_path = self.root / '.env.company-databases.json'
        company_admin.create_company_db_user(self.a['company_id'], admin_engine=self.engine,
                                             credential_path=credential_path)
        username, password = 'auth_' + uuid4().hex[:16], uuid4().hex
        with self.engine.connect() as database:
            database.execute(text('CREATE USER :user@\'%\' IDENTIFIED BY :password'),
                             {'user': username, 'password': password})
        grant_control_service_access(self.engine, username, '%')
        limited = connection.create_db_engine(self.engine.url.set(username=username, password=password).render_as_string(hide_password=False))
        self.addCleanup(limited.dispose)
        with limited.connect() as database:
            self.assertFalse(split_schema.validate_control(database))
            for forbidden in ('SELECT * FROM audit_logs', 'SELECT * FROM oauth_states',
                              'SELECT * FROM login_attempts', 'DELETE FROM sessions WHERE 1=0',
                              'UPDATE company_databases SET status=status WHERE 1=0',
                              'CREATE TABLE forbidden_table (id INT)',
                              'SELECT * FROM ' + tenancy.quoted_database(self.a['database_name']) + '.documents'):
                with self.subTest(sql=forbidden), self.assertRaises(SQLAlchemyError):
                    database.execute(text(forbidden))
        settings = {'HYUNDAP_DB_COMPANY_ACCOUNTS': '1',
                    'HYUNDAP_COMPANY_CREDENTIALS_FILE': str(credential_path)}
        with patch.dict(os.environ, settings), patch.object(runtime, 'get_engine', return_value=limited):
            # 일반 서비스가 등록부 쓰기를 못 해도 준비 관리자만으로 신규 회사를 만들 수 있다.
            created = company_admin.provision_company('최소 권한 서비스의 신규 회사', admin_engine=self.engine)
            self.assertEqual(created['status'], 'ready')
            session = session_service.login(self.email, self.password)
            actor = {'session_id': session['session_id'], '_session_token': session['token']}
            result = upload_service.upload_document(actor, self.source,
                dict(company_id=self.a['company_id'], doc_type='manual'), upload_dir=self.root / 'uploads',
                embedding=self.embedding,
                ingest_document=lambda path, info, **kwargs: workflow_tests.UploadWorkflowTests.ingest(self, path, info, **kwargs))
            self.assertEqual(result['status'], 'success')
            prepared = prepare_original_download(actor, result['document_id'], upload_dir=self.root / 'uploads')
            prepared['temporary'].cleanup()
            self.assertEqual(tenancy.company_for_reference(document_id=result['document_id']), self.a['company_id'])
            company_engine = tenancy.get_company_engine(created['company_id'])
            def fail_after_route(connection, cursor, statement, parameters, context, executemany):
                if 'CALL company_register_route' in statement:
                    raise RuntimeError('synthetic route failure')
            event.listen(company_engine, 'after_cursor_execute', fail_after_route)
            try:
                with self.assertRaises(RuntimeError):
                    self.upload(created)
            finally:
                event.remove(company_engine, 'after_cursor_execute', fail_after_route)
            with company_engine.connect() as database:
                self.assertEqual(database.scalar(text('SELECT COUNT(*) FROM documents')), 0)
                self.assertEqual(database.scalar(text('SELECT COUNT(*) FROM document_versions')), 0)
            with limited.connect() as database:
                self.assertEqual(database.scalar(text('SELECT COUNT(*) FROM document_routes WHERE company_id=:id'),
                                                 {'id': created['company_id']}), 0)
            session_service.logout(session)
            with self.assertRaises(AccessError):
                session_service.validate_session(session)
        tenancy.close_company_engines()

    def test_company_accounts_isolate_documents_and_allow_platform_manager(self):
        """회사별 계정은 타사 DB와 공통 인증 테이블을 직접 열지 못한다."""
        from sqlalchemy.exc import SQLAlchemyError
        credential_path = self.root / '.env.company-databases.json'
        for company in (self.a, self.b):
            company_admin.create_company_db_user(company['company_id'], admin_engine=self.engine,
                                                 credential_path=credential_path)
        settings = {'HYUNDAP_DB_COMPANY_ACCOUNTS': '1',
                    'HYUNDAP_COMPANY_CREDENTIALS_FILE': str(credential_path)}
        with patch.dict(os.environ, settings):
            a, b = self.upload(self.a), self.upload(self.b)
            self.assertEqual((a['status'], b['status']), ('success', 'success'))
            for result, company in ((a, self.a), (b, self.b)):
                detail = documents_repo.get_document_detail(self.actor, result['document_id'])
                self.assertEqual(detail['company_id'], company['company_id'])
                self.assertTrue(versions_repo.list_document_versions(self.actor, result['document_id'])[0]['is_active'])
                permissions.verify_documents_access(self.actor, [(result['document_id'], result['version_id'])])
            email = uuid4().hex + '@test.invalid'
            accounts.create_account(email, 'company_admin', self.b['company_id'], self.password)
            other_session = session_service.login(email, self.password)
            other_actor = {'session_id': other_session['session_id'], '_session_token': other_session['token']}
            try:
                with self.assertRaises(AccessError) as caught:
                    documents_repo.get_document_detail(other_actor, a['document_id'])
                self.assertEqual(caught.exception.code, 'ACCESS_DENIED')
                engine = tenancy.get_company_engine(self.a['company_id'])
                other_name = tenancy.get_company_engine(self.b['company_id']).url.database
                with engine.connect() as database:
                    with self.assertRaises(SQLAlchemyError):
                        database.execute(text('SELECT id FROM ' + tenancy.quoted_database(other_name) + '.documents'))
                    with self.assertRaises(SQLAlchemyError):
                        database.execute(text('SELECT id FROM ' + tenancy.quoted_database(self.engine.url.database) + '.users'))
                    with self.assertRaises(SQLAlchemyError):
                        database.execute(text('UPDATE company_information SET is_active=is_active'))
                    with self.assertRaises(SQLAlchemyError):
                        database.execute(text('UPDATE tenant_identity SET company_id=company_id'))
            finally:
                session_service.logout(other_session)

    def test_switching_account_mode_does_not_reuse_shared_engine(self):
        """공통 계정으로 연 연결이 회사 계정 모드에서 재사용되지 않는다."""
        from sqlalchemy.exc import SQLAlchemyError
        credential_path = self.root / '.env.company-databases.json'
        with patch.dict(os.environ, {'HYUNDAP_DB_COMPANY_ACCOUNTS': '0'}):
            shared = tenancy.get_company_engine(self.a['company_id'])
        company_admin.create_company_db_user(self.a['company_id'], admin_engine=self.engine,
                                             credential_path=credential_path)
        with patch.dict(os.environ, {'HYUNDAP_DB_COMPANY_ACCOUNTS': '1',
                                     'HYUNDAP_COMPANY_CREDENTIALS_FILE': str(credential_path)}):
            isolated = tenancy.get_company_engine(self.a['company_id'])
            self.assertIsNot(shared, isolated)
            self.assertTrue(isolated.url.username.startswith('tenant_'))
            with isolated.connect() as database:
                with self.assertRaises(SQLAlchemyError):
                    database.execute(text('SELECT id FROM ' + tenancy.quoted_database(self.engine.url.database) + '.users'))
        with patch.dict(os.environ, {'HYUNDAP_DB_COMPANY_ACCOUNTS': '1',
                                     'HYUNDAP_COMPANY_CREDENTIALS_SOURCE': 'environment',
                                     'HYUNDAP_COMPANY_DATABASE_CREDENTIALS': credential_path.read_text(encoding='utf-8')}):
            hosted = tenancy.get_company_engine(self.a['company_id'])
            self.assertIsNot(isolated, hosted)
            with hosted.connect() as database:
                self.assertEqual(database.scalar(text('SELECT company_id FROM tenant_identity')),
                                 self.a['company_id'])
            with self.assertRaises(DbError) as caught:
                company_admin.provision_company('호스팅 신규 회사', admin_engine=self.engine)
            self.assertEqual(caught.exception.code, 'CONFIG_MISSING')

    def test_new_company_creates_isolated_account_before_ready(self):
        """회사 등록이 전용 계정을 준비한 뒤 문서 업로드를 허용한다."""
        credential_path = self.root / '.env.company-databases.json'
        with patch.dict(os.environ, {'HYUNDAP_DB_COMPANY_ACCOUNTS': '1',
                                     'HYUNDAP_COMPANY_CREDENTIALS_FILE': str(credential_path)}):
            company = company_admin.provision_company('전용 계정 회사', admin_engine=self.engine)
            self.assertEqual(company['status'], 'ready')
            result = self.upload(company)
            self.assertEqual(result['status'], 'success')
            engine = tenancy.get_company_engine(company['company_id'])
            self.assertTrue(engine.url.username.startswith('tenant_'))
            self.assertEqual(documents_repo.get_document_detail(self.actor, result['document_id'])['company_id'],
                             company['company_id'])
            # 새 회사의 최소 권한 계정으로 다운로드 원본 조회도 가능해야 한다.
            from hyundap.db.download_service import prepare_original_download
            prepared = prepare_original_download(self.actor, result['document_id'], upload_dir=self.root / 'uploads')
            try:
                self.assertEqual(prepared['path'].read_bytes(), self.source.read_bytes())
            finally:
                prepared['temporary'].cleanup()

    def test_isolated_account_setup_failure_leaves_company_inactive_and_account_locked(self):
        """설정 저장 실패 시 회사는 비활성이고 미완료 MySQL 계정은 잠긴다."""
        company_id, generated = str(uuid4()), uuid4()
        settings = {'HYUNDAP_DB_COMPANY_ACCOUNTS': '1',
                    'HYUNDAP_COMPANY_CREDENTIALS_FILE': str(self.root / '.env.company-databases.json')}
        with patch.dict(os.environ, settings), patch.object(company_admin, 'uuid4', return_value=generated), \
                patch.object(company_admin, 'save_credential', side_effect=OSError('synthetic failure')):
            with self.assertRaises(DbError) as caught:
                company_admin.provision_company('실패 회사', admin_engine=self.engine, company_id=company_id)
        self.assertEqual(caught.exception.code, 'PROVISION_FAILED')
        with self.engine.connect() as database:
            company = database.execute(text('''SELECT c.is_active,r.status FROM companies c
                JOIN company_databases r ON r.company_id=c.id WHERE c.id=:id'''), {'id': company_id}).mappings().one()
            self.assertEqual((company['is_active'], company['status']), (0, 'failed'))
            locked = database.execute(text('SELECT account_locked FROM mysql.user WHERE user=:name'),
                                      {'name': 'tenant_' + generated.hex[:20]}).scalar_one()
            self.assertEqual(locked, 'Y')

    def test_company_account_authentication_respects_lock_flag(self):
        """조회는 행을 잠그지 않고 쓰기 권한 확인은 세션 행을 잠근다."""
        from sqlalchemy.exc import SQLAlchemyError
        from hyundap.db.sessions_repo import current_actor
        credential_path = self.root / '.env.company-databases.json'
        company_admin.create_company_db_user(self.a['company_id'], admin_engine=self.engine,
                                             credential_path=credential_path)
        settings = {'HYUNDAP_DB_COMPANY_ACCOUNTS': '1',
                    'HYUNDAP_COMPANY_CREDENTIALS_FILE': str(credential_path)}
        with patch.dict(os.environ, settings):
            company = tenancy.get_company_engine(self.a['company_id'])
            for locked in (False, True):
                with self.subTest(lock=locked), company.connect() as database:
                    tx = database.begin()
                    try:
                        current_actor(self.actor, connection=database, lock=locked)
                        with self.engine.connect() as other:
                            other.execute(text('SET SESSION innodb_lock_wait_timeout=1'))
                            try:
                                if locked:
                                    with self.assertRaises(SQLAlchemyError) as caught:
                                        other.execute(text('UPDATE sessions SET auth_version=auth_version WHERE id=:id'),
                                                      {'id': self.actor['session_id']})
                                    self.assertEqual(caught.exception.orig.args[0], 1205)
                                else:
                                    other.execute(text('UPDATE sessions SET auth_version=auth_version WHERE id=:id'),
                                                  {'id': self.actor['session_id']})
                            finally:
                                other.rollback()
                    finally:
                        tx.rollback()

    def test_session_expiry_cannot_be_bypassed_by_caller_time(self):
        """호출자가 과거 시각을 전달해도 DB 현재 시각으로 세션 만료를 확인한다."""
        from hyundap.db.sessions_repo import _session_credentials
        credential_path = self.root / '.env.company-databases.json'
        company_admin.create_company_db_user(self.a['company_id'], admin_engine=self.engine,
                                             credential_path=credential_path)
        with self.engine.begin() as database:
            database.execute(text('UPDATE sessions SET created_at=UNIX_TIMESTAMP()-10, '
                                  'last_seen_at=UNIX_TIMESTAMP()-10, idle_expires_at=UNIX_TIMESTAMP()-1 WHERE id=:id'),
                             {'id': self.actor['session_id']})
        session_id, digest = _session_credentials(self.actor)
        with patch.dict(os.environ, {'HYUNDAP_DB_COMPANY_ACCOUNTS': '1',
                                     'HYUNDAP_COMPANY_CREDENTIALS_FILE': str(credential_path)}):
            company = tenancy.get_company_engine(self.a['company_id'])
            with company.connect() as database, self.assertRaises(OperationalError) as caught:
                database.execute(text('CALL company_authenticate(:session,:digest,0,0)'),
                                 {'session': session_id, 'digest': digest})
            self.assertEqual(caught.exception.orig.args[0], 1644)
            self.assertEqual(caught.exception.orig.args[1], 'SESSION_EXPIRED')

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
