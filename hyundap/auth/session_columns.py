"""auth/session_columns.py — 로그인 세션 INSERT 문과 Railway v3 호환 값 생성 (SPEC-AUTH-001 D11).

담당: 이성민. sessions 테이블에 기존 v3 컬럼(csrf_token_hash, created_at, last_seen_at,
idle_expires_at, absolute_expires_at)이 남아 있으면 그 값도 채운다. 없으면 v4 INSERT를
그대로 쓴다. v3 시간은 UTC Unix 초(DB_LEGACY_TIME_UNIT=milliseconds면 밀리초)다.
SQLAlchemy 없이 import되도록 표준 라이브러리와 DB 오류 형식만 사용한다.
"""

import hashlib
import os
import secrets
import time

from hyundap.db.errors import DbError

LEGACY_COLUMNS = ('csrf_token_hash', 'created_at', 'last_seen_at', 'idle_expires_at',
                  'absolute_expires_at')
_UNITS = {'seconds': (1_000_000_000, 1), 'milliseconds': (1_000_000, 1000)}
_V4_COLUMNS = 'id,user_id,token_hash,auth_version,expires_at'
_V4_VALUES = ':id,:user_id,:token_hash,:auth_version,DATE_ADD(UTC_TIMESTAMP(6), INTERVAL :ttl MINUTE)'
_COLUMNS_SQL = ("SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name='sessions'")


def present_legacy_columns(connection):
    """호출자의 트랜잭션 연결로 sessions에 남아 있는 v3 컬럼 이름 집합을 반환한다."""
    names = {str(row[0]).lower() for row in connection.exec_driver_sql(_COLUMNS_SQL)}
    return names & set(LEGACY_COLUMNS)


def _legacy_values(present, ttl_minutes):
    """존재하는 v3 컬럼의 값을 만든다. 알 수 없는 시간 단위는 기본값 없이 거부한다."""
    unit = os.environ.get('DB_LEGACY_TIME_UNIT', 'seconds')
    if unit not in _UNITS:
        raise DbError('INVALID_STATE')
    divisor, per_second = _UNITS[unit]
    now = time.time_ns() // divisor
    expires = now + ttl_minutes * 60 * per_second
    # sessions_ck_3: created_at <= last_seen_at <= idle_expires_at <= absolute_expires_at
    values = {'created_at': now, 'last_seen_at': now,
              'idle_expires_at': expires, 'absolute_expires_at': expires}
    if 'csrf_token_hash' in present:
        # 세션 토큰과 별개인 CSRF 토큰의 해시만 저장한다. 원문은 보관·반환·기록하지 않는다.
        values['csrf_token_hash'] = hashlib.sha256(
            secrets.token_urlsafe(32).encode('utf-8')).hexdigest()
    return {name: value for name, value in values.items() if name in present}


def build_session_insert(present, base_params, ttl_minutes):
    """(INSERT SQL 문자열, 인자 dict)를 반환한다. v3 컬럼이 없으면 기존 v4 INSERT와 같다.

    base_params는 id, user_id, token_hash, auth_version을 담는다. 컬럼 이름은
    LEGACY_COLUMNS 안의 고정 값만 SQL에 넣으므로 호출자 입력이 SQL이 되지 않는다.
    """
    params = dict(base_params, ttl=ttl_minutes)
    columns, values = _V4_COLUMNS, _V4_VALUES
    if present:
        extra = _legacy_values(present, ttl_minutes)
        names = [name for name in LEGACY_COLUMNS if name in extra]
        params.update(extra)
        columns += ''.join(',' + name for name in names)
        values += ''.join(',:' + name for name in names)
    return 'INSERT INTO sessions ({}) VALUES ({})'.format(columns, values), params
