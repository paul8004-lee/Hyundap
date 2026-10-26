"""03·04 회사 접근·저장 기능 검증. 합성 데이터와 빈 로컬 MySQL만 사용한다.

DB_RUNTIME_TEST_URL은 host=127.0.0.1, database=hyundap_runtime_test여야 한다.
실제 공유 DB에 이 테스트를 실행하지 않는다. 외부 API와 Chroma는 호출하지 않는다.
"""

import hashlib
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from hyundap.auth.errors import AccessError
from hyundap.auth.permissions import get_allowed_company_ids, verify_documents_access
from hyundap.db import chunks_repo, documents_repo, jobs_repo, runtime, schema, versions_repo
from hyundap.db.errors import DbError
from .test_schema_migration import BASELINE, execute_script


def session_fixture(engine, user_id, company_id):
    """인증 담당자가 제공할 세션을 합성 SQL로 준비한다. 로그인 구현을 호출하지 않는다."""
    session_id = str(uuid4())
    token = 'synthetic-session-' + str(uuid4())
    with engine.begin() as database:
        database.execute(text('''
            INSERT INTO sessions (id,user_id,token_hash,auth_version,expires_at)
            SELECT :id,id,:digest,auth_version,DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 1 HOUR)
            FROM users WHERE id=:user_id
        '''), {'id': session_id, 'user_id': user_id,
               'digest': hashlib.sha256(token.encode('utf-8')).hexdigest()})
    return {'user_id': user_id, 'company_id': company_id, 'role': 'company_admin',
            'session_id': session_id, 'auth_version': 1, '_session_token': token}


class OfflineRuntimeTests(unittest.TestCase):
    """DB 없이 입력 경계와 비밀정보 비노출을 확인한다."""

    def test_empty_document_ids_do_not_connect(self):
        """빈 검색 ID는 DB 접속 없이 빈 결과를 반환한다."""
        with patch.object(runtime, 'get_engine') as engine:
            self.assertEqual(documents_repo.get_documents_with_active_version([]), [])
            engine.assert_not_called()

    def test_database_failure_is_not_an_empty_result(self):
        """조회 장애를 빈 결과로 숨기거나 원시 오류의 합성 비밀값을 노출하지 않는다."""
        error = OperationalError('query', {}, Exception('synthetic-private-value'))
        with patch.object(runtime, 'get_engine', side_effect=error):
            with self.assertRaises(DbError) as caught:
                documents_repo.get_documents_with_active_version(['a-document'])
        self.assertEqual(caught.exception.code, 'DB_UNAVAILABLE')
        self.assertNotIn('synthetic-private-value', str(caught.exception))

    def test_bad_manifest_fails_before_connection(self):
        """순번 누락·중복·잘못된 해시는 SQL 전에 거부한다."""
        for chunks in ([], [{'id': 'v:00001', 'chunk_index': 1, 'content_hash': 'a' * 64}],
                       [{'id': 'v:00000', 'chunk_index': 0, 'content_hash': 'bad'}]):
            with self.subTest(chunks=chunks), self.assertRaises(DbError):
                chunks_repo.save_manifest('v', chunks)

    def test_report_discards_secrets_and_raw_messages(self):
        """허용 필드만 남겨 토큰·본문·원시 오류 문자열의 저장을 막는다."""
        report = jobs_repo.normalize_report({'status': 'failed', 'error_code': 'EMPTY_DOCUMENT',
            'password': 'synthetic-private-value', 'error_message': 'synthetic-private-value',
            'api_key': 'synthetic-private-value', 'text': 'synthetic-private-value'})
        self.assertNotIn('synthetic-private-value', json.dumps(report))
        self.assertEqual(report['error_code'], 'EMPTY_DOCUMENT')


@unittest.skipUnless(os.getenv('DB_RUNTIME_TEST_URL'), '격리 MySQL URL 필요')
class DatabaseRuntimeTests(unittest.TestCase):
    """v4 fixture에서 실제 세션·권한·트랜잭션·경쟁 요청을 검증한다."""

    @classmethod
    def setUpClass(cls):
        """비어 있는 로컬 테스트 DB에 대표 v3와 v4 마이그레이션을 준비한다."""
        url = make_url(os.environ['DB_RUNTIME_TEST_URL'])
        if url.host != '127.0.0.1' or url.database != 'hyundap_runtime_test':
            raise RuntimeError('로컬 전용 테스트 DB 이름을 확인하세요.')
        cls.engine = create_engine(url, isolation_level='READ COMMITTED', hide_parameters=True,
                                   connect_args={'init_command': "SET time_zone='+00:00'"})
        cls.addClassCleanup(cls.engine.dispose)
        with cls.engine.begin() as database:
            if inspect(database).get_table_names():
                raise RuntimeError('기존 테이블이 있는 DB에는 실행하지 않습니다.')
            execute_script(database, BASELINE)
            migration = schema.prepare_sql(schema.snapshot(database))
            execute_script(database, migration)
            database.execute(text('UPDATE schema_migrations SET version=4 WHERE version=3'))

    def setUp(self):
        """테스트마다 서로 다른 회사·관리자·세션을 만들고 연결 풀을 격리한다."""
        self.patch_engine = patch.object(runtime, 'get_engine', return_value=self.engine)
        self.patch_engine.start()
        self.addCleanup(self.patch_engine.stop)
        self.company, self.other_company, self.user_id = str(uuid4()), str(uuid4()), str(uuid4())
        self.email = f'{self.user_id}@test.invalid'
        with self.engine.begin() as database:
            database.execute(text('INSERT INTO companies (id,name) VALUES (:id,:name)'),
                             [{'id': self.company, 'name': '합성 회사 A'},
                              {'id': self.other_company, 'name': '합성 회사 B'}])
            database.execute(text('''
                INSERT INTO users (id,company_id,email,role)
                VALUES (:id,:company,:email,'COMPANY_ADMIN')
            '''), {'id': self.user_id, 'company': self.company, 'email': self.email})
        self.actor = session_fixture(self.engine, self.user_id, self.company)
        self.metadata = {'company_id': self.company, 'doc_type': 'manual', 'file_name': '합성.pdf',
                         'storage_path': 'data/uploads/synthetic.pdf', 'sha256': 'a' * 64,
                         'file_size': 10, 'data_category': 'operational'}

    def candidate(self, **changes):
        """기본 합성 업로드 정보를 복사해 관리자의 새 후보를 생성한다."""
        return documents_repo.create_document_version(self.actor, dict(self.metadata, **changes))

    def publish_fixture(self, candidate):
        """접근 검사만을 위해 fixture를 ready/published로 설정한다. Chroma 적재 검증은 아니다."""
        version_id = candidate['version_id']
        chunks_repo.save_manifest(version_id, [{'id': version_id + ':00000', 'chunk_index': 0,
                                               'content_hash': 'b' * 64}])
        with self.engine.begin() as database:
            database.execute(text("UPDATE document_versions SET version_status='ready' WHERE id=:id"),
                             {'id': version_id})
            database.execute(text("UPDATE documents SET publication_status='published',active_version_id=:version WHERE id=:id"),
                             {'id': candidate['document_id'], 'version': version_id})

    def test_forged_role_and_company_cannot_expand_scope(self):
        """전달 문맥의 역할·회사·권한 버전 위조를 무시하고 DB의 현재 값을 사용한다."""
        forged = dict(self.actor, role='platform_admin', company_id=self.other_company, auth_version=999)
        self.assertEqual(get_allowed_company_ids(forged), {self.company})
        with self.assertRaises(AccessError):
            documents_repo.create_document_version(forged, dict(self.metadata, company_id=self.other_company))

    def test_session_id_without_token_is_not_authentication(self):
        """세션 ID만 복사하거나 잘못된 토큰을 넣은 문맥을 거부한다."""
        for token in (None, 'x' * 43):
            actor = dict(self.actor, _session_token=token)
            with self.assertRaises(AccessError):
                get_allowed_company_ids(actor)

    def test_expiry_revocation_and_auth_version_invalidation(self):
        """저장된 폐기·명시 만료·권한 버전 변경이 기존 문맥에도 즉시 적용된다."""
        with self.engine.begin() as database:
            database.execute(text('UPDATE sessions SET revoked_at=UTC_TIMESTAMP(6) WHERE id=:id'),
                             {'id': self.actor['session_id']})
        with self.assertRaises(AccessError):
            get_allowed_company_ids(self.actor)
        fresh = session_fixture(self.engine, self.user_id, self.company)
        with self.engine.begin() as database:
            database.execute(text('UPDATE sessions SET expires_at=UTC_TIMESTAMP(6) WHERE id=:id'),
                             {'id': fresh['session_id']})
        with self.assertRaises(AccessError):
            get_allowed_company_ids(fresh)
        fresh = session_fixture(self.engine, self.user_id, self.company)
        with self.engine.begin() as database:
            database.execute(text('UPDATE users SET auth_version=auth_version+1 WHERE id=:id'), {'id': self.user_id})
        with self.assertRaises(AccessError):
            get_allowed_company_ids(fresh)

    def test_inactive_account_company_and_current_role(self):
        """계정·회사 비활성과 DB에서 변경한 현재 역할을 다시 확인한다."""
        with self.engine.begin() as database:
            database.execute(text("UPDATE users SET role='EMPLOYEE' WHERE id=:id"), {'id': self.user_id})
        with self.assertRaises(AccessError):
            self.candidate()
        with self.engine.begin() as database:
            database.execute(text('UPDATE companies SET is_active=0 WHERE id=:id'), {'id': self.company})
        with self.assertRaises(AccessError):
            get_allowed_company_ids(self.actor)
        with self.engine.begin() as database:
            database.execute(text('UPDATE companies SET is_active=1 WHERE id=:id'), {'id': self.company})
            database.execute(text('UPDATE users SET is_active=0 WHERE id=:id'), {'id': self.user_id})
        with self.assertRaises(AccessError):
            get_allowed_company_ids(self.actor)

    def test_platform_admin_sees_only_active_companies(self):
        """전체 관리자는 소속 회사 없이 유효한 회사들을 조회하고 비활성 회사는 제외한다."""
        with self.engine.begin() as database:
            database.execute(text("UPDATE users SET role='SUPER_ADMIN',company_id=NULL WHERE id=:id"),
                             {'id': self.user_id})
            database.execute(text('UPDATE companies SET is_active=0 WHERE id=:id'), {'id': self.other_company})
        allowed = get_allowed_company_ids(self.actor)
        self.assertIn(self.company, allowed)
        self.assertNotIn(self.other_company, allowed)

    def test_batch_metadata_and_access_recheck(self):
        """정확한 11필드·없는 ID 누락·초기 NULL과 게시 후 접근 재검증을 확인한다."""
        candidate = self.candidate()
        rows = documents_repo.get_documents_with_active_version([candidate['document_id'], str(uuid4())])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 11)
        self.assertIsNone(rows[0]['active_version_id'])
        refs = [(candidate['document_id'], candidate['version_id'])]
        with self.assertRaises(AccessError) as caught:
            verify_documents_access(self.actor, refs)
        self.assertEqual(caught.exception.code, 'STATE_CHANGED')
        self.publish_fixture(candidate)
        self.assertIsNone(verify_documents_access(self.actor, refs))
        with self.engine.begin() as database:
            database.execute(text("UPDATE documents SET publication_status='blocked' WHERE id=:id"),
                             {'id': candidate['document_id']})
        with self.assertRaises(AccessError) as caught:
            verify_documents_access(self.actor, refs)
        self.assertEqual(caught.exception.code, 'STATE_CHANGED')

    def test_wrong_company_and_changed_active_version_are_rejected(self):
        """타 회사 인용과 답변 작성 후 활성 버전 변경을 모두 차단한다."""
        candidate = self.candidate()
        self.publish_fixture(candidate)
        refs = [(candidate['document_id'], candidate['version_id'])]
        with self.engine.begin() as database:
            database.execute(text('UPDATE users SET company_id=:company WHERE id=:id'),
                             {'company': self.other_company, 'id': self.user_id})
        with self.assertRaises(AccessError) as caught:
            verify_documents_access(self.actor, refs)
        self.assertEqual(caught.exception.code, 'ACCESS_DENIED')
        with self.engine.begin() as database:
            database.execute(text('UPDATE users SET company_id=:company WHERE id=:id'),
                             {'company': self.company, 'id': self.user_id})
        newer = self.candidate(document_id=candidate['document_id'], sha256='c' * 64)
        self.publish_fixture(newer)
        with self.assertRaises(AccessError) as caught:
            verify_documents_access(self.actor, refs)
        self.assertEqual(caught.exception.code, 'STATE_CHANGED')

    def test_recheck_rejects_deleted_failed_evaluation_and_missing_references(self):
        """회사 권한이 있어도 삭제·실패·평가 자료나 없는 인용은 최종 반환에서 차단한다."""
        candidate = self.candidate()
        self.publish_fixture(candidate)
        refs = [(candidate['document_id'], candidate['version_id'])]
        cases = [
            ("UPDATE documents SET deleted_at=UTC_TIMESTAMP(6) WHERE id=:id",
             "UPDATE documents SET deleted_at=NULL WHERE id=:id", candidate['document_id']),
            ("UPDATE document_versions SET version_status='failed' WHERE id=:id",
             "UPDATE document_versions SET version_status='ready' WHERE id=:id", candidate['version_id']),
            ("UPDATE document_versions SET data_category='evaluation' WHERE id=:id",
             "UPDATE document_versions SET data_category='operational' WHERE id=:id", candidate['version_id']),
        ]
        for change, restore, identifier in cases:
            with self.subTest(change=change):
                with self.engine.begin() as database:
                    database.execute(text(change), {'id': identifier})
                with self.assertRaises(AccessError) as caught:
                    verify_documents_access(self.actor, refs)
                self.assertEqual(caught.exception.code, 'STATE_CHANGED')
                with self.engine.begin() as database:
                    database.execute(text(restore), {'id': identifier})
        with self.assertRaises(AccessError) as caught:
            verify_documents_access(self.actor, refs + [(str(uuid4()), str(uuid4()))])
        self.assertEqual(caught.exception.code, 'STATE_CHANGED')
        with self.assertRaises(DbError) as caught:
            verify_documents_access(self.actor, [])
        self.assertEqual(caught.exception.code, 'INVALID_INPUT')

    def test_duplicate_failed_deleted_and_cross_company_files(self):
        """실패·삭제된 같은 회사 파일도 중복이며 다른 회사는 허용한다."""
        candidate = self.candidate()
        versions_repo.mark_failed(candidate['version_id'])
        with self.engine.begin() as database:
            database.execute(text('UPDATE documents SET deleted_at=UTC_TIMESTAMP(6) WHERE id=:id'),
                             {'id': candidate['document_id']})
        match = documents_repo.find_duplicate_document(self.company, 'a' * 64)
        self.assertEqual(match['version_status'], 'failed')
        with self.assertRaises(DbError) as caught:
            self.candidate()
        self.assertEqual(caught.exception.code, 'DUPLICATE_FILE')
        with self.engine.begin() as database:
            database.execute(text("UPDATE users SET role='SUPER_ADMIN',company_id=NULL WHERE id=:id"),
                             {'id': self.user_id})
        other = documents_repo.create_document_version(self.actor, dict(self.metadata, company_id=self.other_company))
        self.assertNotEqual(other['version_id'], candidate['version_id'])

    def test_duplicate_exclusion_validates_scope_and_preserves_final_guard(self):
        """현재 후보만 제외하고 최종 신규 생성의 중복 보호는 유지한다."""
        candidate = self.candidate()
        with self.engine.begin() as database:
            self.assertIsNone(documents_repo.find_duplicate_document(
                self.company, 'a' * 64, exclude_version_id=candidate['version_id'], connection=database))
            for company, digest, excluded in (
                    (self.other_company, 'a' * 64, candidate['version_id']),
                    (self.company, 'b' * 64, candidate['version_id']),
                    (self.company, 'a' * 64, str(uuid4()))):
                with self.subTest(company=company, digest=digest), self.assertRaises(DbError) as caught:
                    documents_repo.find_duplicate_document(company, digest,
                        exclude_version_id=excluded, connection=database)
                self.assertEqual(caught.exception.code, 'INVALID_INPUT')
            self.assertTrue(database.in_transaction())
        with self.assertRaises(DbError) as caught:
            self.candidate()
        self.assertEqual(caught.exception.code, 'DUPLICATE_FILE')

    def test_manifest_idempotency_conflict_and_active_version_protection(self):
        """동일 목록 재시도는 허용하고 다른 내용·ready 목록 변경·활성 버전 실패 처리는 거부한다."""
        candidate = self.candidate()
        version = candidate['version_id']
        chunks = [{'id': version + ':00000', 'chunk_index': 0, 'content_hash': 'b' * 64}]
        chunks_repo.save_manifest(version, chunks)
        chunks_repo.save_manifest(version, chunks)
        changed = deepcopy(chunks)
        changed[0]['content_hash'] = 'c' * 64
        with self.assertRaises(DbError) as caught:
            chunks_repo.save_manifest(version, changed)
        self.assertEqual(caught.exception.code, 'CONFLICT')
        self.assertEqual(chunks_repo.get_expected_chunk_ids(version), [version + ':00000'])
        self.publish_fixture(candidate)
        with self.assertRaises(DbError):
            versions_repo.mark_failed(version)
        with self.assertRaises(DbError):
            chunks_repo.save_manifest(version, chunks)

    def test_version_history_does_not_expose_storage_path(self):
        """관리자 이력은 버전 순서로 반환하고 서버 파일 경로는 노출하지 않는다."""
        candidate = self.candidate()
        self.candidate(document_id=candidate['document_id'], sha256='b' * 64)
        versions = versions_repo.list_document_versions(self.actor, candidate['document_id'])
        self.assertEqual([row['version_no'] for row in versions], [1, 2])
        self.assertNotIn('storage_path', versions[0])
        with self.engine.begin() as database:
            database.execute(text("UPDATE users SET role='EMPLOYEE' WHERE id=:id"), {'id': self.user_id})
        with self.assertRaises(AccessError):
            versions_repo.list_document_versions(self.actor, candidate['document_id'])

    def test_reports_preserve_stages_and_reject_late_results(self):
        """앞 단계 기록 보존·지연 결과 차단·보고서 소속 검사·비밀 필드 제거를 확인한다."""
        candidate = self.candidate()
        first = {'stage': 'ingest', 'status': 'success', 'api_key': 'synthetic-secret'}
        jobs_repo.record_stage_result(candidate['job_id'], first)
        jobs_repo.record_stage_result(candidate['job_id'], first)
        jobs_repo.record_stage_result(candidate['job_id'], {'stage': 'embedding', 'status': 'partial',
            'total': 2, 'stored': 1, 'model_name': 'text-embedding-3-small',
            'failures': [{'code': 'API_TIMEOUT', 'message': 'synthetic-secret'}]})
        with self.assertRaises(DbError):
            jobs_repo.record_stage_result(candidate['job_id'], first)
        with self.assertRaises(DbError):
            jobs_repo.record_stage_result(candidate['job_id'], {'status': 'success', 'stage': 'embedding',
                                                               'company_id': self.other_company})
        with self.engine.connect() as database:
            raw = database.execute(text('SELECT report FROM index_jobs WHERE id=:id'), {'id': candidate['job_id']}).scalar_one()
        reports = json.loads(raw)
        self.assertEqual(set(reports), {'ingest', 'embedding'})
        self.assertEqual(reports['embedding']['failures'][0]['code'], 'API_TIMEOUT')
        self.assertNotIn('synthetic-secret', raw)

    def test_failed_job_insert_rolls_back_document_and_version(self):
        """작업 생성 SQL 실패 시 같은 TX의 문서·후보도 롤백된다."""
        def fail_job_insert(conn, cursor, statement, parameters, context, executemany):
            """테스트에서 작업 INSERT에만 합성 DB 장애를 주입한다."""
            if statement.lstrip().startswith('INSERT INTO index_jobs'):
                raise OperationalError('test', {}, Exception('synthetic-private-value'))
        event.listen(self.engine, 'before_cursor_execute', fail_job_insert)
        try:
            with self.assertRaises(DbError):
                self.candidate()
        finally:
            event.remove(self.engine, 'before_cursor_execute', fail_job_insert)
        with self.engine.connect() as database:
            count = database.execute(text('SELECT COUNT(*) FROM documents WHERE company_id=:id'),
                                     {'id': self.company}).scalar_one()
        self.assertEqual(count, 0)

    def test_concurrent_duplicate_upload_creates_only_one_candidate(self):
        """동시에 같은 파일을 올려도 한 건만 생성되고 다른 요청은 중복 오류가 된다."""
        barrier = Barrier(2)
        def upload(_):
            """동시 시작한 업로드의 성공 또는 안전한 오류 코드만 반환한다."""
            barrier.wait(timeout=10)
            try:
                return self.candidate()
            except DbError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(upload, range(2)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn('DUPLICATE_FILE', results)
        with self.engine.connect() as database:
            count = database.execute(text('SELECT COUNT(*) FROM document_versions WHERE company_id=:id'),
                                     {'id': self.company}).scalar_one()
        self.assertEqual(count, 1)

    def test_concurrent_versions_receive_distinct_numbers(self):
        """동일 문서의 동시 새 버전에 서로 다른 증가 번호를 할당한다."""
        first = self.candidate()
        barrier = Barrier(2)
        def upload(digest):
            """문서 ID를 유지하고 다른 파일 해시의 후보를 동시에 생성한다."""
            barrier.wait(timeout=10)
            return self.candidate(document_id=first['document_id'], sha256=digest)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(upload, ['b' * 64, 'c' * 64]))
        versions = versions_repo.list_document_versions(self.actor, first['document_id'])
        self.assertEqual([row['version_no'] for row in versions], [1, 2, 3])

    def test_unmigrated_legacy_document_blocks_new_upload(self):
        """아직 버전으로 가져오지 않은 레거시가 있으면 중복 검사 누락을 막기 위해 업로드를 거부한다."""
        with self.engine.begin() as database:
            database.execute(text('INSERT INTO documents (id,company_id) VALUES (:id,:company)'),
                             {'id': str(uuid4()), 'company': self.company})
        with self.assertRaises(DbError) as caught:
            self.candidate()
        self.assertEqual(caught.exception.code, 'INVALID_STATE')


if __name__ == '__main__':
    unittest.main()
