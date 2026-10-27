"""auth/session_service.py — 로그인·세션 재검증·로그아웃 (SPEC-AUTH-001 M2).

담당: 이성민. UI 연결 이름은 login / logout / validate_session이다.
토큰 원문은 반환값의 token에만 담고 DB에는 SHA-256만 저장한다. 비밀번호·해시·토큰·
토큰 해시는 로그·예외 메시지에 넣지 않는다. DB 장애는 DbError(DB_UNAVAILABLE) 그대로다.
"""

import secrets
from uuid import uuid4

from sqlalchemy import text

from hyundap.common.config import get_settings
from hyundap.db.runtime import transaction
from hyundap.db.sessions_repo import current_actor, token_hash
from .errors import AccessError
from .passwords import verify_password
from .permissions import allowed_companies
from .session_columns import build_session_insert

USERNAME_MAX_LENGTH = 254
PASSWORD_MAX_LENGTH = 1024


def login(username, password):
    """아이디·비밀번호를 확인하고 세션 1건을 발급해 사용자·세션 dict를 반환한다.

    모든 자격 증명 실패는 AccessError('INVALID_CREDENTIALS') 하나로 응답하며
    시도마다 scrypt를 정확히 1회 수행한다. 잘못된 입력은 DB에 접속하지 않는다.
    """
    if not isinstance(username, str) or not isinstance(password, str):
        raise AccessError('INVALID_CREDENTIALS')
    identifier = username.strip().lower()
    if not identifier or len(identifier) > USERNAME_MAX_LENGTH:
        raise AccessError('INVALID_CREDENTIALS')
    if not password or len(password) > PASSWORD_MAX_LENGTH:
        raise AccessError('INVALID_CREDENTIALS')
    ttl = get_settings().session_ttl_minutes
    try:
        with transaction() as database:
            user = database.execute(text('''
                SELECT id,password_hash,auth_version FROM users WHERE login_identifier=:identifier
            '''), {'identifier': identifier}).mappings().one_or_none()
            # 사용자가 없거나 해시가 NULL이어도 더미 검증으로 scrypt 1회를 맞춘다.
            stored = user['password_hash'] if user else None
            if not verify_password(password, stored):
                raise AccessError('INVALID_CREDENTIALS')
            token = secrets.token_urlsafe(32)
            session_id = str(uuid4())
            sql, params = build_session_insert(
                {'id': session_id, 'user_id': user['id'], 'token_hash': token_hash(token),
                 'auth_version': user['auth_version']}, ttl)
            database.execute(text(sql), params)
            # 비활성 사용자·알 수 없는 역할·회사 누락·비활성 회사·허용 회사 없음은
            # current_actor·allowed_companies가 같은 트랜잭션에서 거부하고 INSERT는 롤백된다.
            return _session_payload({'session_id': session_id, '_session_token': token}, database)
    except AccessError:
        # 어느 분기에서 실패했는지 예외 연결 고리로도 드러내지 않는다.
        raise AccessError('INVALID_CREDENTIALS') from None


def validate_session(session):
    """login이 반환한 dict를 DB 기준으로 재검증하고 login과 같은 형태의 dict를 반환한다.

    session의 role·company_id·allowed_company_ids·user_id는 읽지 않는다. 만료를 연장하지 않는다.
    """
    actor = _actor(session)
    with transaction() as database:
        return _session_payload(actor, database)


def logout(session):
    """ID·토큰 해시가 모두 맞는 미폐기 세션을 폐기한다. 어떤 경우든 None을 반환한다."""
    try:
        actor = _actor(session)
    except AccessError:
        return None
    with transaction() as database:
        database.execute(text('''
            UPDATE sessions SET revoked_at=UTC_TIMESTAMP(6)
            WHERE id=:id AND token_hash=:token_hash AND revoked_at IS NULL
        '''), {'id': actor['session_id'], 'token_hash': token_hash(actor['_session_token'])})
    return None


def _actor(session):
    """UI 세션 dict의 session_id·token 형식을 DB 접속 전에 검사하고 서버 actor dict를 만든다."""
    if not isinstance(session, dict):
        raise AccessError('SESSION_EXPIRED')
    token = session.get('token')
    token_hash(token)
    session_id = session.get('session_id')
    if not isinstance(session_id, str) or not session_id or len(session_id) > 36:
        raise AccessError('SESSION_EXPIRED')
    return {'session_id': session_id, '_session_token': token}


def _session_payload(actor, database):
    """현재 DB 값으로 login·validate_session 공통 반환 dict를 만든다."""
    current = current_actor(actor, connection=database)
    allowed = sorted(allowed_companies(current, connection=database))
    email = database.execute(text('SELECT email FROM users WHERE id=:id'),
                             {'id': current['user_id']}).scalar_one_or_none()
    company_name = None
    if current['company_id'] is not None:
        company_name = database.execute(text('SELECT name FROM companies WHERE id=:id'),
                                        {'id': current['company_id']}).scalar_one_or_none()
    # display_name 컬럼이 없으므로 email의 @ 앞부분을 쓴다. email이 비면 사용자 ID다.
    display_name = (email or '').split('@', 1)[0] or current['user_id']
    return {
        'user_id': current['user_id'],
        'display_name': display_name,
        'role': current['role'],
        'company_id': current['company_id'],
        'company_name': company_name,
        'allowed_company_ids': allowed,
        'token': actor['_session_token'],
        'session_id': current['session_id'],
    }
