"""최종 v4 세션 SQL의 필수 값·시간 일치·CSRF·입력 불변 검증."""

import hashlib
import os
import unittest
from unittest.mock import patch

from hyundap.auth import session_columns

BASE = {'id': 'session-id', 'user_id': 'user-id', 'token_hash': 'a' * 64, 'auth_version': 1}


class BuildSessionInsertTests(unittest.TestCase):
    """DB 없이 세션 필수 값의 생성 계약을 검증한다."""
    def test_fixed_columns_and_single_time_value_preserve_input(self):
        """동일 시각을 재사용하고 입력 dict를 변경하지 않는다."""
        original = dict(BASE)
        with patch.object(session_columns, 'epoch_now', return_value=1000) as now:
            sql, params = session_columns.build_session_insert(BASE, 5)
        now.assert_called_once_with()
        self.assertEqual(BASE, original)
        self.assertEqual((params['created_at'], params['last_seen_at']), (1000, 1000))
        self.assertEqual((params['idle_expires_at'], params['absolute_expires_at']), (1300, 1300))
        self.assertIn('FROM_UNIXTIME(:absolute_expires_at)', sql)
        self.assertNotIn('information_schema', sql)

    def test_removed_time_unit_setting_cannot_change_seconds(self):
        """폐기한 단위 설정이 있어도 Unix 초를 유지한다."""
        for unit in ('milliseconds', '', 'unknown'):
            with self.subTest(unit=unit), patch.dict(os.environ, {'DB_LEGACY_TIME_UNIT': unit}), \
                    patch.object(session_columns, 'epoch_now', return_value=1000):
                _, params = session_columns.build_session_insert(BASE, 5)
                self.assertEqual(params['absolute_expires_at'], 1300)

    def test_csrf_uses_separate_token_and_never_returns_raw_value(self):
        """세션 토큰과 별개인 CSRF의 해시만 반환한다."""
        raw = 'synthetic-csrf-' + 'x' * 30
        with patch.object(session_columns.secrets, 'token_urlsafe', return_value=raw) as token:
            _, params = session_columns.build_session_insert(BASE, 5)
        token.assert_called_once_with(32)
        self.assertEqual(params['csrf_token_hash'], hashlib.sha256(raw.encode()).hexdigest())
        self.assertNotEqual(params['csrf_token_hash'], params['token_hash'])
        self.assertNotIn(raw, repr(params))
