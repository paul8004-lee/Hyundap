"""SPEC-AUTH-001 리뷰 R1 — 실제 Railway v3 DDL 기반 계정 생성→로그인→로그아웃 회귀 검증.

tests/fixtures/railway_v3_schema.sql(created_at·updated_at 기본값 없음, 대문자 역할 CHECK,
이메일 소문자 CHECK)에 v4 이행을 적용한 전용 DB에서 실행한다. DB_AUTH_TEST_URL이 없으면 건너뛴다.
URL은 host=127.0.0.1, database=hyundap_auth_test여야 하며, 이 모듈은 같은 서버의
hyundap_auth_railway_test DB를 직접 만들고 끝나면 지운다(CREATE/DROP 권한 필요). 합성 데이터만 쓴다.
실행: 저장소 맨 위에서 python -m unittest hyundap.tests.test_auth_railway_ddl
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from hyundap.auth import accounts, session_service
from hyundap.auth.errors import AccessError
from hyundap.db import runtime, schema
from hyundap.tests.test_schema_migration import execute_script

PASSWORD = 'synthetic-Passw0rd!'
RAILWAY_DB = 'hyundap_auth_railway_test'
KEYS = {'user_id', 'display_name', 'role', 'company_id', 'company_name', 'allowed_company_ids',
        'token', 'session_id'}
FIXTURE = Path(__file__).parent / 'fixtures' / 'railway_v3_schema.sql'


@unittest.skipUnless(os.getenv('DB_AUTH_TEST_URL'), '격리 MySQL URL 필요 (DB_AUTH_TEST_URL)')
class RailwayDdlAccountFlowTests(unittest.TestCase):
    """실제 Railway users·sessions 구조에서 계정 등록과 세션 수명 주기를 확인한다."""

    @classmethod
    def setUpClass(cls):
        base = make_url(os.environ['DB_AUTH_TEST_URL'])
        if base.host != '127.0.0.1' or base.database != 'hyundap_auth_test':
            raise RuntimeError('로컬 전용 테스트 DB 이름을 확인하세요.')
        admin = create_engine(base.set(database=None))
        with admin.begin() as server:
            server.execute(text(f'DROP DATABASE IF EXISTS {RAILWAY_DB}'))
            server.execute(text(f'CREATE DATABASE {RAILWAY_DB} CHARACTER SET utf8mb4'))
        cls.engine = create_engine(base.set(database=RAILWAY_DB), isolation_level='READ COMMITTED',
                                   hide_parameters=True,
                                   connect_args={'init_command': "SET time_zone='+00:00'"})

        def teardown():
            cls.engine.dispose()
            with admin.begin() as server:
                server.execute(text(f'DROP DATABASE IF EXISTS {RAILWAY_DB}'))
            admin.dispose()
        cls.addClassCleanup(teardown)
        with cls.engine.begin() as database:
            database.execute(text('SET FOREIGN_KEY_CHECKS=0'))
            try:
                execute_script(database, FIXTURE.read_text(encoding='utf-8'))
            finally:
                database.execute(text('SET FOREIGN_KEY_CHECKS=1'))
            database.execute(text('INSERT INTO schema_migrations VALUES (3)'))
            execute_script(database, schema.prepare_legacy_sql(database, 'seconds'))
            if schema.validate_v4(schema.snapshot(database)):
                raise AssertionError('v4 구조 이행이 끝나지 않았습니다.')
            database.execute(text('UPDATE schema_migrations SET version=4 WHERE version=3'))

    def setUp(self):
        engine = patch.object(runtime, 'get_engine', return_value=self.engine)
        engine.start()
        self.addCleanup(engine.stop)
        environ = patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop('DB_LEGACY_TIME_UNIT', None)
        runtime.clear_schema_cache()
        self.company = str(uuid4())
        with self.engine.begin() as database:
            database.execute(text('''
                INSERT INTO companies (id,name,created_at,updated_at)
                VALUES (:id,:name,UNIX_TIMESTAMP(UTC_TIMESTAMP()),UNIX_TIMESTAMP(UTC_TIMESTAMP()))
            '''), {'id': self.company, 'name': '합성 회사 ' + uuid4().hex[:8]})

    def database_now(self):
        """DB 기준 현재 UTC Unix 초를 반환한다."""
        with self.engine.connect() as database:
            return int(database.execute(text('SELECT UNIX_TIMESTAMP(UTC_TIMESTAMP())')).scalar_one())

    def user_row(self, user_id):
        """users 행 1건을 dict로 반환한다."""
        with self.engine.connect() as database:
            return dict(database.execute(text('SELECT * FROM users WHERE id=:id'),
                                         {'id': user_id}).mappings().one())

    def session_row(self, session_id):
        """sessions 행 1건을 dict로 반환한다."""
        with self.engine.connect() as database:
            return dict(database.execute(text('SELECT * FROM sessions WHERE id=:id'),
                                         {'id': session_id}).mappings().one())

    def test_create_account_login_validate_logout(self):
        """공백·대문자 이메일로 만든 계정이 CHECK를 통과하고 로그인부터 로그아웃까지 동작한다."""
        user_id = accounts.create_account('  Mixed.Admin@Test.INVALID ', 'company_admin',
                                          self.company, PASSWORD)
        row = self.user_row(user_id)
        now = self.database_now()
        self.assertEqual((row['role'], row['email'], row['login_identifier']),
                         ('COMPANY_ADMIN', 'mixed.admin@test.invalid', 'mixed.admin@test.invalid'))
        self.assertEqual(row['created_at'], row['updated_at'])
        self.assertLessEqual(abs(now - row['created_at']), 60)

        session = session_service.login(username='  MIXED.ADMIN@TEST.INVALID ', password=PASSWORD)
        self.assertEqual(set(session), KEYS)
        self.assertEqual((session['user_id'], session['role'], session['company_id']),
                         (user_id, 'company_admin', self.company))
        stored = self.session_row(session['session_id'])
        self.assertRegex(stored['csrf_token_hash'], r'^[0-9a-f]{64}$')
        self.assertLessEqual(stored['created_at'], stored['last_seen_at'])
        self.assertLessEqual(stored['idle_expires_at'], stored['absolute_expires_at'])
        self.assertIsNone(stored['revoked_at'])
        self.assertEqual(session_service.validate_session(session=session), session)

        self.assertIsNone(session_service.logout(session=session))
        self.assertIsNotNone(self.session_row(session['session_id'])['revoked_at'])
        with self.assertRaises(AccessError) as caught:
            session_service.validate_session(session=session)
        self.assertEqual(caught.exception.code, 'SESSION_EXPIRED')

    def test_every_app_role_is_stored_with_the_allowed_code(self):
        """세 역할 모두 users_ck_3·users_ck_5를 통과하고 로그인 결과는 앱 역할 이름이다."""
        cases = (('platform_admin', None, 'SUPER_ADMIN'), ('company_admin', self.company, 'COMPANY_ADMIN'),
                 ('employee', self.company, 'EMPLOYEE'))
        for role, company_id, stored in cases:
            with self.subTest(role=role):
                email = f'{role}.{uuid4().hex[:8]}@test.invalid'
                user_id = accounts.create_account(email, role, company_id, PASSWORD)
                self.assertEqual(self.user_row(user_id)['role'], stored)
                self.assertEqual(session_service.login(username=email, password=PASSWORD)['role'], role)

    def test_expired_sessions_are_rejected(self):
        """expires_at 만료와 Railway v3 시각 만료는 둘 다 SESSION_EXPIRED다."""
        email = f'expired.{uuid4().hex[:8]}@test.invalid'
        accounts.create_account(email, 'employee', self.company, PASSWORD)
        for name, sql in (
                ('expires_at', 'UPDATE sessions SET expires_at=DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 1 MINUTE)'
                               ' WHERE id=:id'),
                ('v3-deadlines', 'UPDATE sessions SET created_at=:past,last_seen_at=:past,'
                                 'idle_expires_at=:past,absolute_expires_at=:past WHERE id=:id')):
            with self.subTest(name=name):
                session = session_service.login(username=email, password=PASSWORD)
                self.assertEqual(session_service.validate_session(session=session), session)
                with self.engine.begin() as database:
                    database.execute(text(sql), {'id': session['session_id'],
                                                 'past': self.database_now() - 10})
                with self.assertRaises(AccessError) as caught:
                    session_service.validate_session(session=session)
                self.assertEqual(caught.exception.code, 'SESSION_EXPIRED')


if __name__ == '__main__':
    unittest.main()
