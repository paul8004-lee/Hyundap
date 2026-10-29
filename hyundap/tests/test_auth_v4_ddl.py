"""직접 생성한 최종 v4에서 계정 생성·모든 역할·세션 만료와 폐기를 검증한다.

DB_AUTH_TEST_URL은 127.0.0.1의 빈 hyundap_auth_test만 허용한다.
공유 fixture를 한 번 직접 생성하며 기존 DB를 삭제하거나 운영 DB에 접속하지 않는다.
"""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text

from hyundap.auth import accounts, session_service
from hyundap.auth.errors import AccessError
from hyundap.db import runtime
from hyundap.tests.test_auth_session import auth_test_engine

PASSWORD = 'synthetic-Passw0rd!'
KEYS = {'user_id', 'display_name', 'role', 'company_id', 'company_name', 'allowed_company_ids',
        'token', 'session_id'}


@unittest.skipUnless(os.getenv('DB_AUTH_TEST_URL'), '격리 MySQL URL 필요 (DB_AUTH_TEST_URL)')
class V4DdlAccountFlowTests(unittest.TestCase):
    """최종 v4 users·sessions에서 계정 등록과 세션 수명 주기를 확인한다."""

    @classmethod
    def setUpClass(cls):
        cls.engine = auth_test_engine()

    def setUp(self):
        engine = patch.object(runtime, 'get_engine', return_value=self.engine)
        engine.start()
        self.addCleanup(engine.stop)
        environ = patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        self.company = str(uuid4())
        with self.engine.begin() as database:
            database.execute(text('''
                INSERT INTO companies (id,name,created_at,updated_at)
                VALUES (:id,:name,UNIX_TIMESTAMP(),UNIX_TIMESTAMP())
            '''), {'id': self.company, 'name': '합성 회사 ' + uuid4().hex[:8]})

    def database_now(self):
        """DB 기준 현재 UTC Unix 초를 반환한다."""
        with self.engine.connect() as database:
            return int(database.execute(text('SELECT UNIX_TIMESTAMP()')).scalar_one())

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
                         ('company_admin', 'mixed.admin@test.invalid', 'mixed.admin@test.invalid'))
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
        """세 역할이 최종 v4 역할·회사 제약을 통과하고 같은 역할로 로그인된다."""
        cases = (('platform_admin', None, 'platform_admin'), ('company_admin', self.company, 'company_admin'),
                 ('employee', self.company, 'employee'))
        for role, company_id, stored in cases:
            with self.subTest(role=role):
                email = f'{role}.{uuid4().hex[:8]}@test.invalid'
                user_id = accounts.create_account(email, role, company_id, PASSWORD)
                self.assertEqual(self.user_row(user_id)['role'], stored)
                self.assertEqual(session_service.login(username=email, password=PASSWORD)['role'], role)

    def test_expired_sessions_are_rejected(self):
        """expires_at과 유휴·절대 만료가 모두 SESSION_EXPIRED를 반환한다."""
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
