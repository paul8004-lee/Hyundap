"""세션의 해시·만료·폐기 저장과 현재 사용자 상태 조회."""

import hashlib
from uuid import uuid4

from sqlalchemy import text

from hyundap.auth.errors import AccessError
from .runtime import require_id, transaction


ROLE_MAP = {
    'platform_admin': 'platform_admin', 'SUPER_ADMIN': 'platform_admin',
    'company_admin': 'company_admin', 'COMPANY_ADMIN': 'company_admin',
    'employee': 'employee', 'EMPLOYEE': 'employee',
}


def token_hash(token):
    """32바이트 이상 토큰의 문자열 형태를 검사하고 DB 비교용 SHA-256을 반환한다."""
    if not isinstance(token, str) or not 43 <= len(token) <= 256:
        raise AccessError('SESSION_EXPIRED')
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_session(user, token, expires_at, *, connection):
    """이미 검증·잠금한 사용자에 대해 세션을 삽입하고 DB 세션 ID를 반환한다.

    호출자 TX에 참여하며 원본 토큰을 저장하지 않는다. 만료 시각은 UTC datetime이다.
    """
    session_id = str(uuid4())
    connection.execute(text('''
        INSERT INTO sessions (id,user_id,token_hash,auth_version,expires_at)
        VALUES (:id,:user_id,:token_hash,:auth_version,:expires_at)
    '''), {'id': session_id, 'user_id': user['id'], 'token_hash': token_hash(token),
          'auth_version': user['auth_version'], 'expires_at': expires_at})
    return session_id


def current_actor(actor, *, connection, lock=False):
    """토큰 소유 증명을 검사하고 DB의 현재 사용자·역할·회사로 문맥을 다시 만든다.

    actor는 서버 내부 dict이며 _session_token을 포함해야 한다. 공개 필드의 역할·회사·
    auth_version을 신뢰하지 않는다. 쓰기 경로의 lock=True는 계정·세션 행을 잠근다.
    토큰 불일치·만료·폐기·계정/회사 비활성·권한 버전 불일치는 인증 실패다.
    """
    if not isinstance(actor, dict):
        raise AccessError('SESSION_EXPIRED')
    digest = token_hash(actor.get('_session_token'))
    session_id = actor.get('session_id')
    if not isinstance(session_id, str) or not session_id or len(session_id) > 36:
        raise AccessError('SESSION_EXPIRED')
    suffix = ' FOR UPDATE' if lock else ''
    # 기존 세션·사용자 키의 collation이 달라도 동작하도록 컬럼 간 JOIN을 피한다.
    session = connection.execute(text('''
        SELECT id,user_id,auth_version FROM sessions
        WHERE id=:id AND token_hash=:token_hash
          AND revoked_at IS NULL AND expires_at>UTC_TIMESTAMP(6)
    ''' + suffix), {'id': session_id, 'token_hash': digest}).mappings().one_or_none()
    if not session:
        raise AccessError('SESSION_EXPIRED')
    row = connection.execute(text('''
        SELECT id,company_id,role,auth_version,is_active FROM users WHERE id=:id
    ''' + suffix), {'id': session['user_id']}).mappings().one_or_none()
    if not row or not row['is_active'] or session['auth_version'] != row['auth_version']:
        raise AccessError('SESSION_EXPIRED')
    role = ROLE_MAP.get(row['role'])
    if role is None or (role != 'platform_admin' and not row['company_id']):
        raise AccessError('ACCESS_DENIED')
    if row['company_id']:
        active = connection.execute(text('SELECT is_active FROM companies WHERE id=:id' + suffix),
                                    {'id': row['company_id']}).scalar_one_or_none()
        if not active:
            raise AccessError('ACCESS_DENIED')
    return {'user_id': row['id'], 'company_id': row['company_id'], 'role': role,
            'session_id': session['id'], 'auth_version': row['auth_version'],
            '_session_token': actor['_session_token']}


def authenticate_token(token, *, connection=None):
    """서버에 보관한 토큰으로 새 요청의 사용자 문맥을 만든다. 비밀값은 출력하지 않는다."""
    digest = token_hash(token)
    with transaction(connection) as database:
        session_id = database.execute(text('SELECT id FROM sessions WHERE token_hash=:hash'),
                                      {'hash': digest}).scalar_one_or_none()
        if session_id is None:
            raise AccessError('SESSION_EXPIRED')
        return current_actor({'session_id': session_id, '_session_token': token}, connection=database)


def revoke_session(actor, *, connection=None):
    """토큰을 소유한 현재 세션만 폐기한다. 다른 사용자 세션을 ID만으로 폐기하지 않는다."""
    with transaction(connection) as database:
        current = current_actor(actor, connection=database, lock=True)
        database.execute(text('UPDATE sessions SET revoked_at=UTC_TIMESTAMP(6) WHERE id=:id'),
                         {'id': require_id(current['session_id'])})
