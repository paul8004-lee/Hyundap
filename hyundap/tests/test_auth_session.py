"""SPEC-AUTH-001 M2 — 로그인·세션 재검증·로그아웃 검증.

[offline] 테스트는 DB 없이 입력 경계·DB 장애·TTL 설정·실패 변환을 확인한다.
[DB] 테스트는 DB_AUTH_TEST_URL이 host=127.0.0.1, database=hyundap_auth_test인
빈 로컬 MySQL일 때만 실행한다. 실제 공유 DB에서는 실행하지 않으며 합성 데이터만 쓴다.
실행: 저장소 맨 위에서 python -m unittest hyundap.tests.test_auth_session
"""

import atexit
import hashlib
import importlib
import io
import logging
import os
import sys
import traceback
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from hyundap.auth import session_columns, session_service
from hyundap.auth.errors import AccessError
from hyundap.auth.passwords import hash_password
from hyundap.common import config
from hyundap.db import runtime, schema
from hyundap.db.errors import DbError
from hyundap.tests.test_schema_migration import BASELINE, execute_script

PASSWORD = 'synthetic-Passw0rd!'
MESSAGE = '아이디 또는 비밀번호가 올바르지 않습니다.'
KEYS = {'user_id', 'display_name', 'role', 'company_id', 'company_name', 'allowed_company_ids',
        'token', 'session_id'}
V3_COLUMNS = ('csrf_token_hash', 'created_at', 'last_seen_at', 'idle_expires_at', 'absolute_expires_at')
REAL_SCRYPT = hashlib.scrypt
_ENGINE = None


def auth_test_engine():
    """격리 테스트 DB에 기준 v3 + v4 마이그레이션을 한 번만 준비하고 Engine을 반환한다.

    두 테스트 모듈(session, accounts)이 같은 프로세스에서 공유한다. 기존 테이블이 있는
    DB에는 실행하지 않으므로 다시 실행하려면 빈 hyundap_auth_test DB를 새로 만들어야 한다.
    """
    global _ENGINE
    if _ENGINE is None:
        url = make_url(os.environ['DB_AUTH_TEST_URL'])
        if url.host != '127.0.0.1' or url.database != 'hyundap_auth_test':
            raise RuntimeError('로컬 전용 테스트 DB 이름을 확인하세요.')
        engine = create_engine(url, isolation_level='READ COMMITTED', hide_parameters=True,
                               connect_args={'init_command': "SET time_zone='+00:00'"})
        atexit.register(engine.dispose)
        with engine.begin() as database:
            if inspect(database).get_table_names():
                raise RuntimeError('기존 테이블이 있는 DB에는 실행하지 않습니다.')
            execute_script(database, BASELINE)
            execute_script(database, schema.prepare_sql(schema.snapshot(database)))
            database.execute(text('UPDATE schema_migrations SET version=4 WHERE version=3'))
        _ENGINE = engine
    return _ENGINE


def make_company(engine, *, active=True):
    """합성 회사 1건을 만들고 (id, name)을 반환한다."""
    company_id, name = str(uuid4()), '합성 회사 ' + uuid4().hex[:8]
    with engine.begin() as database:
        database.execute(text('INSERT INTO companies (id,name,is_active) VALUES (:id,:name,:active)'),
                         {'id': company_id, 'name': name, 'active': int(active)})
    return company_id, name


def make_user(engine, *, role='employee', company_id=None, password=PASSWORD, active=True):
    """합성 사용자 1건을 만들고 (id, email)을 반환한다. password=None이면 해시가 NULL이다."""
    user_id = str(uuid4())
    email = f'User.{user_id[:8]}@Test.invalid'
    stored = hash_password(password) if password is not None else None
    with engine.begin() as database:
        database.execute(text('''
            INSERT INTO users (id,company_id,email,role,is_active,password_hash)
            VALUES (:id,:company,:email,:role,:active,:hash)
        '''), {'id': user_id, 'company': company_id, 'email': email, 'role': role,
               'active': int(active), 'hash': stored})
    return user_id, email


def session_count(engine):
    """sessions 행 수를 반환한다."""
    with engine.connect() as database:
        return database.execute(text('SELECT COUNT(*) FROM sessions')).scalar_one()


def session_row(engine, session_id):
    """세션 행 전체를 dict로 반환한다."""
    with engine.connect() as database:
        return dict(database.execute(text('SELECT * FROM sessions WHERE id=:id'),
                                     {'id': session_id}).mappings().one())


def active_company_ids(engine):
    """DB 기준 활성 회사 ID 정렬 목록(N5: 기대값을 DB에서 계산한다)."""
    with engine.connect() as database:
        return sorted(database.execute(text('SELECT id FROM companies WHERE is_active=1')).scalars())


class OfflineInputTests(unittest.TestCase):
    """AC-004 — 잘못된 입력은 DB 연결 없이 거부한다."""

    def test_malformed_inputs_never_touch_the_engine(self):
        """login은 INVALID_CREDENTIALS, validate는 SESSION_EXPIRED, logout은 None이다."""
        with patch.object(runtime, 'get_engine') as engine:
            for username, password in (('', PASSWORD), ('   ', PASSWORD), (None, PASSWORD),
                                       ('a' * 255, PASSWORD), ('user@test.invalid', ''),
                                       ('user@test.invalid', None), ('user@test.invalid', 'x' * 1025)):
                with self.subTest(username=username, password=password), \
                        self.assertRaises(AccessError) as caught:
                    session_service.login(username=username, password=password)
                self.assertEqual(caught.exception.code, 'INVALID_CREDENTIALS')
            for session in (None, [], {}, {'token': 'short'}):
                with self.subTest(session=session), self.assertRaises(AccessError) as caught:
                    session_service.validate_session(session=session)
                self.assertEqual(caught.exception.code, 'SESSION_EXPIRED')
            for session in (None, {}, {'session_id': 'x', 'token': 'short'}):
                with self.subTest(session=session):
                    self.assertIsNone(session_service.logout(session=session))
            engine.assert_not_called()


class OfflineDatabaseOutageTests(unittest.TestCase):
    """AC-006 — DB 장애는 DB_UNAVAILABLE로 전달하고 원시 오류를 노출하지 않는다."""

    def test_outage_is_db_unavailable_for_all_three(self):
        """login·validate_session·logout 모두 DbError(DB_UNAVAILABLE)다."""
        error = OperationalError('query', {}, Exception('synthetic-private-value'))
        session = {'session_id': str(uuid4()), 'token': 'x' * 43}
        calls = (lambda: session_service.login(username='user@test.invalid', password=PASSWORD),
                 lambda: session_service.validate_session(session=session),
                 lambda: session_service.logout(session=session))
        with patch.object(runtime, 'get_engine', side_effect=error):
            for call in calls:
                with self.subTest(call=call), self.assertRaises(DbError) as caught:
                    call()
                self.assertEqual(caught.exception.code, 'DB_UNAVAILABLE')
                self.assertNotIn('synthetic-private-value', str(caught.exception))


class OfflineTtlSettingTests(unittest.TestCase):
    """AC-015 — SESSION_TTL_MINUTES 기본값·범위·대체값."""

    def test_ttl_default_and_bounds(self):
        """누락·범위 밖·숫자 아님은 480, 5와 10080은 그대로다."""
        cases = ((None, 480), ('480', 480), ('5', 5), ('10080', 10080), ('4', 480),
                 ('10081', 480), ('abc', 480), ('', 480))
        self.addCleanup(config.get_settings.cache_clear)
        with patch.object(config, 'load_dotenv'):
            for value, expected in cases:
                environ = {} if value is None else {'SESSION_TTL_MINUTES': value}
                with self.subTest(value=value), patch.dict(os.environ, environ):
                    if value is None:
                        os.environ.pop('SESSION_TTL_MINUTES', None)
                    config.get_settings.cache_clear()
                    self.assertEqual(config.get_settings().session_ttl_minutes, expected)
        self.assertEqual(config.DEFAULT_SESSION_TTL_MINUTES, 480)


@contextmanager
def fake_transaction(database):
    """session_service.transaction을 대신해 mock 연결을 넘기는 테스트용 컨텍스트."""
    @contextmanager
    def transaction(connection=None):
        yield database
    with patch.object(session_service, 'transaction', transaction):
        yield


class OfflineLoginFailureTests(unittest.TestCase):
    """N8 · REQ-AUTH-004 — 실패 변환은 연결 고리를 남기지 않고 scrypt는 1회다(mock 연결)."""

    def assert_generic_failure(self, error):
        """고정 코드·메시지이며 원래 예외를 __cause__·__context__로 드러내지 않는다."""
        self.assertEqual(error.code, 'INVALID_CREDENTIALS')
        self.assertEqual(str(error), MESSAGE)
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__suppress_context__)
        rendered = ''.join(traceback.format_exception(error))
        self.assertNotIn('ACCESS_DENIED', rendered)
        self.assertNotIn(AccessError.MESSAGES['ACCESS_DENIED'], rendered)

    def test_unknown_user_runs_one_dummy_scrypt(self):
        """사용자 행이 없으면 더미 검증 1회 후 INVALID_CREDENTIALS이며 INSERT하지 않는다."""
        database = MagicMock()
        database.execute.return_value.mappings.return_value.one_or_none.return_value = None
        with fake_transaction(database), patch.object(hashlib, 'scrypt', side_effect=REAL_SCRYPT) as spy, \
                self.assertRaises(AccessError) as caught:
            session_service.login(username='nobody@test.invalid', password=PASSWORD)
        self.assertEqual(spy.call_count, 1)
        self.assert_generic_failure(caught.exception)
        self.assertEqual(database.execute.call_count, 1)

    def test_access_error_after_insert_becomes_invalid_credentials(self):
        """세션 추가 뒤 권한 검사가 ACCESS_DENIED를 내도 INVALID_CREDENTIALS로 바꾼다."""
        database = MagicMock()
        database.execute.return_value.mappings.return_value.one_or_none.return_value = {
            'id': 'user-id', 'password_hash': hash_password(PASSWORD), 'auth_version': 1}
        denied = patch.object(session_service, 'current_actor', side_effect=AccessError('ACCESS_DENIED'))
        v4_only = patch.object(session_columns, 'get_table_columns', return_value=[])
        with fake_transaction(database), denied, v4_only, \
                patch.object(hashlib, 'scrypt', side_effect=REAL_SCRYPT) as spy, \
                self.assertRaises(AccessError) as caught:
            session_service.login(username='user@test.invalid', password=PASSWORD)
        self.assertEqual(spy.call_count, 1)
        self.assert_generic_failure(caught.exception)


@unittest.skipUnless(os.getenv('DB_AUTH_TEST_URL'), '격리 MySQL URL 필요 (DB_AUTH_TEST_URL)')
class DatabaseSessionTests(unittest.TestCase):
    """AC-002·003·005·007·008·009·010 및 D11 — 실제 v4 세션 행으로 검증한다."""

    @classmethod
    def setUpClass(cls):
        cls.engine = auth_test_engine()

    def setUp(self):
        patcher = patch.object(runtime, 'get_engine', return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        unit = patch.dict(os.environ)
        unit.start()
        self.addCleanup(unit.stop)
        os.environ.pop('DB_LEGACY_TIME_UNIT', None)
        self.ttl = config.get_settings().session_ttl_minutes
        self.company, self.company_name = make_company(self.engine)

    def login(self, email, password=PASSWORD):
        """앞뒤 공백·대문자를 섞은 아이디로 로그인한다."""
        return session_service.login(username=f'  {email.upper()} ', password=password)

    def assert_invalid(self, email, password=PASSWORD):
        """INVALID_CREDENTIALS 고정 오류이며 세션 행이 늘지 않는지 확인하고 예외를 반환한다."""
        before = session_count(self.engine)
        with self.assertRaises(AccessError) as caught:
            self.login(email, password)
        self.assertEqual(caught.exception.code, 'INVALID_CREDENTIALS')
        self.assertEqual(str(caught.exception), MESSAGE)
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(session_count(self.engine), before)
        return caught.exception

    def deactivate_all_companies(self):
        """N5: 모든 활성 회사를 비활성화하고 정리 시 원래대로 되돌린다."""
        ids = active_company_ids(self.engine)

        def restore():
            with self.engine.begin() as database:
                for company_id in ids:
                    database.execute(text('UPDATE companies SET is_active=1 WHERE id=:id'),
                                     {'id': company_id})
        self.addCleanup(restore)
        with self.engine.begin() as database:
            database.execute(text('UPDATE companies SET is_active=0 WHERE is_active=1'))

    def failure_cases(self):
        """AC-003 실패 분기마다 (이름, 이메일, 비밀번호, 준비 함수)를 만든다."""
        inactive_company = make_company(self.engine, active=False)[0]
        cases = [('unknown', 'nobody.' + uuid4().hex[:8] + '@test.invalid', PASSWORD, None),
                 ('wrong-password', make_user(self.engine, company_id=self.company)[1], PASSWORD + 'x', None),
                 ('inactive-user', make_user(self.engine, company_id=self.company, active=False)[1], PASSWORD, None),
                 ('inactive-company', make_user(self.engine, company_id=inactive_company)[1], PASSWORD, None),
                 ('super-admin-inactive-company',
                  make_user(self.engine, role='SUPER_ADMIN', company_id=inactive_company)[1], PASSWORD, None),
                 ('unknown-role', make_user(self.engine, role='UNKNOWN', company_id=self.company)[1], PASSWORD, None),
                 ('null-hash', make_user(self.engine, company_id=self.company, password=None)[1], PASSWORD, None),
                 ('employee-without-company', make_user(self.engine, company_id=None)[1], PASSWORD, None),
                 ('platform-admin-no-active-company',
                  make_user(self.engine, role='platform_admin', company_id=None)[1], PASSWORD,
                  self.deactivate_all_companies)]
        return cases

    def test_ac002_login_success_payload_and_row(self):
        """직원·회사 없는 SUPER_ADMIN·회사 있는 SUPER_ADMIN 로그인 결과와 세션 행."""
        user_id, email = make_user(self.engine, company_id=self.company)
        before = session_count(self.engine)
        result = self.login(email)
        self.assertEqual(set(result), KEYS)
        self.assertEqual((result['user_id'], result['role'], result['company_id'], result['company_name']),
                         (user_id, 'employee', self.company, self.company_name))
        self.assertEqual(result['allowed_company_ids'], [self.company])
        self.assertEqual(result['display_name'], email.split('@')[0])
        self.assertEqual(len(result['token']), 43)
        self.assertEqual(session_count(self.engine), before + 1)
        row = session_row(self.engine, result['session_id'])
        self.assertEqual(row['token_hash'], hashlib.sha256(result['token'].encode('utf-8')).hexdigest())
        self.assertEqual(row['user_id'], user_id)
        self.assertEqual(row['auth_version'], 1)
        self.assertIsNone(row['revoked_at'])
        with self.engine.connect() as database:
            seconds = database.execute(text(
                'SELECT TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(6), expires_at) FROM sessions WHERE id=:id'),
                {'id': result['session_id']}).scalar_one()
        self.assertLessEqual(abs(seconds - self.ttl * 60), 60)

        _, admin_email = make_user(self.engine, role='SUPER_ADMIN', company_id=None)
        admin = self.login(admin_email)
        self.assertEqual((admin['role'], admin['company_id'], admin['company_name']),
                         ('platform_admin', None, None))
        self.assertEqual(admin['allowed_company_ids'], active_company_ids(self.engine))

        _, scoped_email = make_user(self.engine, role='SUPER_ADMIN', company_id=self.company)
        scoped = self.login(scoped_email)
        self.assertEqual((scoped['role'], scoped['company_id'], scoped['company_name']),
                         ('platform_admin', self.company, self.company_name))

    def test_ac003_every_failure_branch_is_generic(self):
        """모든 실패 분기는 같은 INVALID_CREDENTIALS이며 세션 행을 추가하지 않는다."""
        for name, email, password, prepare in self.failure_cases():
            with self.subTest(name=name):
                if prepare:
                    prepare()
                self.assert_invalid(email, password)

    def test_ac005_one_scrypt_per_attempt_after_fresh_import(self):
        """새로 import한 직후 첫 시도부터 모든 실패·성공 분기에서 scrypt는 정확히 1회다."""
        cases = self.failure_cases()
        _, good_email = make_user(self.engine, company_id=self.company)
        for name in ('hyundap.auth.session_service', 'hyundap.auth.passwords'):
            sys.modules.pop(name, None)
        with patch.object(hashlib, 'scrypt', side_effect=REAL_SCRYPT) as spy:
            service = importlib.import_module('hyundap.auth.session_service')
            self.assertEqual(spy.call_count, 0)
            errors = []
            # 마지막 분기는 모든 회사를 비활성화하므로 성공 사례를 그 앞에서 실행한다.
            for name, email, password, prepare in cases[:-1] + [('success', good_email, PASSWORD, None)] + cases[-1:]:
                with self.subTest(name=name):
                    if prepare:
                        prepare()
                    spy.reset_mock()
                    if name == 'success':
                        self.assertEqual(set(service.login(username=email, password=password)), KEYS)
                    else:
                        with self.assertRaises(AccessError) as caught:
                            service.login(username=email, password=password)
                        errors.append(caught.exception)
                    self.assertEqual(spy.call_count, 1)
        self.assertEqual(len(errors), len(cases))
        shapes = {(type(error), error.code, str(error), error.args) for error in errors}
        self.assertEqual(len(shapes), 1)

    def test_ac007_no_secret_in_db_logs_or_exceptions(self):
        """토큰·비밀번호·저장 해시·토큰 해시가 DB 컬럼·로그·예외 문자열에 없다."""
        user_id, email = make_user(self.engine, company_id=self.company)
        with self.engine.connect() as database:
            stored = database.execute(text('SELECT password_hash FROM users WHERE id=:id'),
                                      {'id': user_id}).scalar_one()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, old_level)
        self.addCleanup(root.removeHandler, handler)
        result = self.login(email)
        failure = self.assert_invalid(email, PASSWORD + 'x')
        token = result['token']
        digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
        row = session_row(self.engine, result['session_id'])
        for value in row.values():
            self.assertNotIn(token, str(value))
        logs = stream.getvalue()
        texts = str(failure) + repr(failure) + repr(failure.args) + ''.join(
            traceback.format_exception(failure))
        for secret in (token, PASSWORD, stored, digest):
            self.assertNotIn(secret, logs)
            self.assertNotIn(secret, texts)

    def test_ac008_forged_fields_are_ignored_and_expiry_not_extended(self):
        """위조한 역할·회사·사용자 ID를 무시하고 두 번 재검증해도 만료가 그대로다."""
        user_id, email = make_user(self.engine, company_id=self.company)
        other, _ = make_company(self.engine)
        session = self.login(email)
        expires = session_row(self.engine, session['session_id'])['expires_at']
        forged = dict(session, role='platform_admin', company_id=other,
                      allowed_company_ids=[other], user_id='forged-user')
        for _ in range(2):
            current = session_service.validate_session(session=forged)
            self.assertEqual(current, session)
            self.assertEqual(set(current), set(session))
        self.assertEqual(current['user_id'], user_id)
        self.assertEqual(session_row(self.engine, session['session_id'])['expires_at'], expires)

    def test_ac009_invalid_sessions_and_company_change(self):
        """만료·폐기·권한 버전 변경·토큰 변조·ID 누락은 SESSION_EXPIRED, 회사 비활성은 ACCESS_DENIED."""
        changes = {
            'expired': 'UPDATE sessions SET expires_at=DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 1 MINUTE) WHERE id=:id',
            'revoked': 'UPDATE sessions SET revoked_at=UTC_TIMESTAMP(6) WHERE id=:id',
            'auth-version': 'UPDATE users SET auth_version=auth_version+1 WHERE id=:user',
        }
        for name, sql in changes.items():
            with self.subTest(name=name):
                user_id, email = make_user(self.engine, company_id=self.company)
                session = self.login(email)
                with self.engine.begin() as database:
                    database.execute(text(sql), {'id': session['session_id'], 'user': user_id})
                with self.assertRaises(AccessError) as caught:
                    session_service.validate_session(session=session)
                self.assertEqual(caught.exception.code, 'SESSION_EXPIRED')
        _, email = make_user(self.engine, company_id=self.company)
        session = self.login(email)
        altered = dict(session, token=('A' if session['token'][0] != 'A' else 'B') + session['token'][1:])
        missing = {key: value for key, value in session.items() if key != 'session_id'}
        for name, broken in (('token', altered), ('session-id', missing)):
            with self.subTest(name=name), self.assertRaises(AccessError) as caught:
                session_service.validate_session(session=broken)
            self.assertEqual(caught.exception.code, 'SESSION_EXPIRED')
        company, _ = make_company(self.engine)
        _, email = make_user(self.engine, company_id=company)
        session = self.login(email)
        with self.engine.begin() as database:
            database.execute(text('UPDATE companies SET is_active=0 WHERE id=:id'), {'id': company})
        with self.assertRaises(AccessError) as caught:
            session_service.validate_session(session=session)
        self.assertEqual(caught.exception.code, 'ACCESS_DENIED')

    def test_ac010_logout_revokes_and_is_idempotent(self):
        """로그아웃은 폐기 후 None이며 이미 폐기·만료·없는 세션·틀린 토큰도 None이다."""
        _, email = make_user(self.engine, company_id=self.company)
        session = self.login(email)
        self.assertIsNone(session_service.logout(session=session))
        first = session_row(self.engine, session['session_id'])['revoked_at']
        self.assertIsNotNone(first)
        with self.assertRaises(AccessError) as caught:
            session_service.validate_session(session=session)
        self.assertEqual(caught.exception.code, 'SESSION_EXPIRED')
        self.assertIsNone(session_service.logout(session=session))
        self.assertEqual(session_row(self.engine, session['session_id'])['revoked_at'], first)

        expired = self.login(email)
        with self.engine.begin() as database:
            database.execute(text(
                'UPDATE sessions SET expires_at=DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 1 MINUTE) WHERE id=:id'),
                {'id': expired['session_id']})
        self.assertIsNone(session_service.logout(session=expired))
        self.assertIsNone(session_service.logout(session={'session_id': str(uuid4()), 'token': 'u' * 43}))
        live = self.login(email)
        self.assertIsNone(session_service.logout(session=dict(live, token='w' * 43)))
        self.assertIsNone(session_row(self.engine, live['session_id'])['revoked_at'])

    def test_d11_v4_only_structure_uses_v4_insert(self):
        """v3 세션 컬럼이 없는 v4 구조에서 로그인 INSERT가 성공한다."""
        with self.engine.connect() as database:
            columns = {column['name'] for column in inspect(database).get_columns('sessions')}
        self.assertFalse(columns & set(V3_COLUMNS))
        _, email = make_user(self.engine, company_id=self.company)
        result = self.login(email)
        self.assertEqual(set(result), KEYS)
        self.assertEqual(session_service.validate_session(session=result), result)

    def test_d11_v3_columns_present_are_filled(self):
        """v3 컬럼과 sessions_ck_3이 있으면 같은 INSERT가 그 값을 채우고 순서 제약을 지킨다."""
        with self.engine.begin() as database:
            database.execute(text('''
                ALTER TABLE sessions ADD COLUMN csrf_token_hash varchar(64) NOT NULL,
                  ADD COLUMN created_at bigint NOT NULL, ADD COLUMN last_seen_at bigint NOT NULL,
                  ADD COLUMN idle_expires_at bigint NOT NULL, ADD COLUMN absolute_expires_at bigint NOT NULL,
                  ADD CONSTRAINT sessions_ck_3 CHECK (created_at <= last_seen_at
                    AND last_seen_at <= idle_expires_at AND idle_expires_at <= absolute_expires_at)
            '''))
        # 구조 캐시는 DDL을 모르므로 ALTER 전후로 비운다.
        runtime.clear_schema_cache()

        def drop_v3_columns():
            with self.engine.begin() as database:
                database.execute(text('ALTER TABLE sessions DROP CHECK sessions_ck_3'))
                database.execute(text('''
                    ALTER TABLE sessions DROP COLUMN csrf_token_hash, DROP COLUMN created_at,
                      DROP COLUMN last_seen_at, DROP COLUMN idle_expires_at, DROP COLUMN absolute_expires_at
                '''))
            runtime.clear_schema_cache()
        self.addCleanup(drop_v3_columns)
        _, email = make_user(self.engine, company_id=self.company)
        result = self.login(email)
        self.assertEqual(set(result), KEYS)
        row = session_row(self.engine, result['session_id'])
        digest = hashlib.sha256(result['token'].encode('utf-8')).hexdigest()
        self.assertRegex(row['csrf_token_hash'], r'^[0-9a-f]{64}$')
        self.assertNotEqual(row['csrf_token_hash'], digest)
        self.assertEqual(row['created_at'], row['last_seen_at'])
        self.assertEqual(row['idle_expires_at'], row['absolute_expires_at'])
        self.assertEqual(row['idle_expires_at'] - row['created_at'], self.ttl * 60)
        with self.engine.connect() as database:
            now = database.execute(text('SELECT UNIX_TIMESTAMP()')).scalar_one()
        self.assertLessEqual(abs(int(now) - row['created_at']), 60)
        self.assertEqual(session_service.validate_session(session=result), result)
        self.assertIsNone(session_service.logout(session=result))


if __name__ == '__main__':
    unittest.main()
