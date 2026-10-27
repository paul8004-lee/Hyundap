"""별도 인증 담당자가 발급한 세션을 조회해 DB 접근 권한을 재검증한다.

세션 발급·갱신·폐기는 구현하지 않는다. 토큰 해시는 비밀번호 처리가 아니라
전달받은 서버 세션의 소유 증명을 DB에 저장된 값과 비교하기 위한 용도다.
"""

import hashlib

from sqlalchemy import text

from hyundap.auth.errors import AccessError
from .clock import epoch_now


ROLE_MAP = {
    'platform_admin': 'platform_admin',
    'company_admin': 'company_admin',
    'employee': 'employee',
}


def token_hash(token):
    """32바이트 이상 토큰의 문자열 형태를 검사하고 DB 비교용 SHA-256을 반환한다."""
    if not isinstance(token, str) or not 43 <= len(token) <= 256:
        raise AccessError('SESSION_EXPIRED')
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def current_actor(actor, *, connection, lock=False):
    """서버 actor dict와 DB 연결을 받아 현재 권한을 담은 새 actor dict를 반환한다.

    세션 확인 → 사용자 확인 → 회사·역할 확인 순서다. 전달받은 역할·회사는
    믿지 않고 DB 값을 사용한다. lock=True이면 호출자의 트랜잭션 안에서
    세션 → 사용자 → 회사 순서로 행을 잠가 검사 중 권한 변경을 막는다.
    """
    session_id, digest = _session_credentials(actor)
    session = _read_valid_session(connection, session_id, digest, lock)
    user = _read_active_user(connection, session, lock)
    role = _check_company_role(connection, user, lock)
    return {
        'user_id': user['id'],
        'company_id': user['company_id'],
        'role': role,
        'session_id': session['id'],
        'auth_version': user['auth_version'],
        '_session_token': actor['_session_token'],
    }


def _session_credentials(actor):
    """서버 actor dict의 입력을 검사하고 (세션 ID, 비교용 토큰 해시)를 반환한다."""
    if not isinstance(actor, dict):
        raise AccessError('SESSION_EXPIRED')
    digest = token_hash(actor.get('_session_token'))
    session_id = actor.get('session_id')
    if not isinstance(session_id, str) or not session_id or len(session_id) > 36:
        raise AccessError('SESSION_EXPIRED')
    return session_id, digest


def _read_valid_session(connection, session_id, digest, lock):
    """ID·토큰 해시가 일치하고 만료·폐기되지 않은 세션 DB 행을 반환한다."""
    suffix = ''
    if lock:
        suffix = ' FOR UPDATE'
    parameters = {'id': session_id, 'token_hash': digest, 'now': epoch_now()}
    # 세션 확인 후 사용자 행을 읽어 같은 잠금 순서를 유지한다.
    session = connection.execute(text('''
        SELECT id,user_id,auth_version FROM sessions
        WHERE id=:id AND token_hash=:token_hash
          AND revoked_at IS NULL AND expires_at>UTC_TIMESTAMP(6)
          AND idle_expires_at>:now AND absolute_expires_at>:now
    ''' + suffix), parameters).mappings().one_or_none()
    if not session:
        raise AccessError('SESSION_EXPIRED')
    return session


def _read_active_user(connection, session, lock):
    """세션 DB 행에 연결된 활성 사용자를 반환하고 권한 버전 변경을 거부한다."""
    suffix = ''
    if lock:
        suffix = ' FOR UPDATE'
    user = connection.execute(text('''
        SELECT id,company_id,role,auth_version,is_active FROM users WHERE id=:id
    ''' + suffix), {'id': session['user_id']}).mappings().one_or_none()
    if not user or not user['is_active']:
        raise AccessError('SESSION_EXPIRED')
    if session['auth_version'] != user['auth_version']:
        raise AccessError('SESSION_EXPIRED')
    return user


def _check_company_role(connection, user, lock):
    """사용자 DB 행의 역할·소속 회사 유효성을 검사하고 표준 역할명을 반환한다."""
    role = ROLE_MAP.get(user['role'])
    if role is None:
        raise AccessError('ACCESS_DENIED')
    if role != 'platform_admin' and not user['company_id']:
        raise AccessError('ACCESS_DENIED')

    suffix = ''
    if lock:
        suffix = ' FOR UPDATE'
    if user['company_id']:
        active = connection.execute(text('SELECT is_active FROM companies WHERE id=:id' + suffix),
                                    {'id': user['company_id']}).scalar_one_or_none()
        if not active:
            raise AccessError('ACCESS_DENIED')
    return role
