"""SPEC-AUTH-001 범위 추가 D11 — Railway v3 세션 컬럼 INSERT 값 생성 검증 (DB 불필요).

실제 INSERT 성공 여부(v4 전용·v3 컬럼 포함 두 구조)는 DB 테스트 범위이며 여기서는
컬럼 감지 결과 처리, 시간 단위, 순서 제약(sessions_ck_3), csrf 해시 생성만 확인한다.
"""

import hashlib
import os
import re
import unittest
from unittest.mock import MagicMock, patch

from hyundap.auth import session_columns
from hyundap.db import legacy
from hyundap.db.errors import DbError

V3 = ('csrf_token_hash', 'created_at', 'last_seen_at', 'idle_expires_at', 'absolute_expires_at')
BASE = {'id': 'session-id', 'user_id': 'user-id', 'token_hash': 'a' * 64, 'auth_version': 1}
V4_SQL = ('INSERT INTO sessions (id,user_id,token_hash,auth_version,expires_at) '
          'VALUES (:id,:user_id,:token_hash,:auth_version,'
          'DATE_ADD(UTC_TIMESTAMP(6), INTERVAL :ttl MINUTE))')
NOW_NS = 1_700_000_000_123_456_789


def build(present, unit=None, ttl=480):
    """환경 단위와 현재 시각을 고정하고 INSERT SQL·인자를 만든다."""
    environ = {} if unit is None else {'DB_LEGACY_TIME_UNIT': unit}
    with patch.dict(os.environ, environ), patch.object(legacy.time, 'time_ns', return_value=NOW_NS):
        if unit is None:
            os.environ.pop('DB_LEGACY_TIME_UNIT', None)
        return session_columns.build_session_insert(present, dict(BASE), ttl)


class BuildSessionInsertTests(unittest.TestCase):
    """v4 전용 구조는 기존 INSERT 그대로, v3 컬럼이 있으면 값을 함께 채운다."""

    def test_v4_only_insert_is_unchanged(self):
        """v3 컬럼이 없으면 기존 v4 INSERT와 인자만 사용한다(단위 설정도 읽지 않는다)."""
        sql, params = build(set(), unit='invalid-unit')
        self.assertEqual(sql, V4_SQL)
        self.assertEqual(params, dict(BASE, ttl=480))

    def test_v3_columns_seconds_default(self):
        """단위 미설정은 초다. created=last_seen=now, idle=absolute=now+TTL."""
        sql, params = build(set(V3))
        now = NOW_NS // 1_000_000_000
        for column in V3:
            self.assertIn(column, sql)
            self.assertIn(':' + column, sql)
        self.assertEqual(params['created_at'], now)
        self.assertEqual(params['last_seen_at'], now)
        self.assertEqual(params['idle_expires_at'], now + 480 * 60)
        self.assertEqual(params['absolute_expires_at'], now + 480 * 60)
        self.assertLessEqual(params['created_at'], params['last_seen_at'])
        self.assertLessEqual(params['last_seen_at'], params['idle_expires_at'])
        self.assertLessEqual(params['idle_expires_at'], params['absolute_expires_at'])
        self.assertTrue(sql.startswith('INSERT INTO sessions (id,user_id,token_hash,auth_version,expires_at,'))

    def test_v3_columns_explicit_seconds_and_milliseconds(self):
        """seconds·milliseconds는 각 단위의 UTC Unix 시각과 TTL을 쓴다."""
        for unit, divisor, scale in (('seconds', 1_000_000_000, 1), ('milliseconds', 1_000_000, 1000)):
            with self.subTest(unit=unit):
                _, params = build(set(V3), unit=unit, ttl=5)
                now = NOW_NS // divisor
                self.assertEqual(params['created_at'], now)
                self.assertEqual(params['idle_expires_at'], now + 5 * 60 * scale)
                self.assertEqual(params['absolute_expires_at'], params['idle_expires_at'])

    def test_invalid_unit_fails_closed(self):
        """빈 값·알 수 없는 단위는 기본값으로 돌아가지 않고 INVALID_STATE로 거부한다."""
        for unit in ('', 'ms', 'Seconds', 'minutes'):
            with self.subTest(unit=unit), self.assertRaises(DbError) as caught:
                build(set(V3), unit=unit)
            self.assertEqual(caught.exception.code, 'INVALID_STATE')

    def test_csrf_hash_is_separate_token_and_raw_value_is_not_kept(self):
        """csrf_token_hash는 별도 token_urlsafe(32)의 SHA-256 hex이며 원문은 인자에 없다."""
        raw = 'synthetic-csrf-' + 'x' * 30
        with patch.object(session_columns.secrets, 'token_urlsafe', return_value=raw) as token:
            _, params = build(set(V3))
        token.assert_called_once_with(32)
        self.assertEqual(params['csrf_token_hash'], hashlib.sha256(raw.encode('utf-8')).hexdigest())
        self.assertRegex(params['csrf_token_hash'], re.compile(r'^[0-9a-f]{64}$'))
        self.assertNotEqual(params['csrf_token_hash'], params['token_hash'])
        self.assertNotIn(raw, repr(params))

    def test_only_present_v3_columns_are_added(self):
        """존재하는 v3 컬럼만 추가하며 다른 컬럼을 만들어 넣지 않는다."""
        sql, params = build({'created_at', 'last_seen_at'})
        self.assertIn('created_at', params)
        self.assertNotIn('csrf_token_hash', params)
        self.assertNotIn('idle_expires_at', sql)
        self.assertEqual(set(params), set(BASE) | {'ttl', 'created_at', 'last_seen_at'})


class ReuseTests(unittest.TestCase):
    """시간 값은 legacy.epoch_now()를 재사용한다(자체 시간 계산 없음)."""

    def test_time_comes_from_legacy_epoch_now(self):
        """legacy.epoch_now() 값을 기준으로 TTL을 더한다."""
        with patch.object(session_columns.legacy, 'epoch_now', return_value=1000) as now:
            _, params = session_columns.build_session_insert(set(V3), dict(BASE), 5)
        now.assert_called_once_with()
        self.assertEqual((params['created_at'], params['idle_expires_at']), (1000, 1000 + 5 * 60))

    def test_milliseconds_ttl_scale_follows_unit(self):
        """밀리초 단위면 TTL도 밀리초로 더한다."""
        with patch.dict(os.environ, {'DB_LEGACY_TIME_UNIT': 'milliseconds'}), \
                patch.object(session_columns.legacy, 'epoch_now', return_value=1000):
            _, params = session_columns.build_session_insert(set(V3), dict(BASE), 5)
        self.assertEqual(params['idle_expires_at'], 1000 + 5 * 60 * 1000)


class PresentLegacyColumnsTests(unittest.TestCase):
    """runtime.get_table_columns() 결과에서 v3 세션 컬럼만 골라낸다."""

    def test_detects_only_known_v3_columns(self):
        """호출자 연결로 구조 캐시를 조회하고 대소문자와 무관하게 알려진 컬럼만 반환한다."""
        connection = MagicMock()
        columns = [{'name': name} for name in ('id', 'TOKEN_HASH', 'csrf_token_hash', 'Created_At',
                                                'revoke_reason')]
        with patch.object(session_columns, 'get_table_columns', return_value=columns) as lookup:
            present = session_columns.present_legacy_columns(connection)
        self.assertEqual(present, {'csrf_token_hash', 'created_at'})
        lookup.assert_called_once_with(connection, 'sessions')

    def test_v4_only_table_has_no_legacy_columns(self):
        """v4 컬럼만 있으면 빈 집합이다."""
        columns = [{'name': name} for name in ('id', 'user_id', 'token_hash', 'auth_version',
                                                'expires_at', 'revoked_at')]
        with patch.object(session_columns, 'get_table_columns', return_value=columns):
            self.assertEqual(session_columns.present_legacy_columns(MagicMock()), set())


if __name__ == '__main__':
    unittest.main()
