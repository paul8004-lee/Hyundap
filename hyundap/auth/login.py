"""정규화 이메일·Argon2id 검증과 8시간 서버 세션 발급."""

import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import text

from .errors import AccessError
from hyundap.db import sessions_repo, users_repo
from hyundap.db.runtime import transaction


PASSWORD_HASHER = PasswordHasher()
DUMMY_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def login(identifier, password):
    """이메일·비밀번호로 로그인하고 actor/session_token/expires_at을 반환한다.

    비밀번호는 공백을 제거하지 않는다. 해시 없는 계정·잘못된 입력·불일치의 안내는
    동일하다. 비싼 해시 검증 후 짧은 TX에서 계정을 다시 잠가 변경을 확인한다.
    actor의 _session_token은 서버 내부 인증용이므로 UI 표시·로그·모델 입력에서 제외한다.
    """
    if (not isinstance(identifier, str) or not 1 <= len(identifier.strip()) <= 254
            or not isinstance(password, str) or not password or len(password) > 4096):
        raise AccessError('INVALID_CREDENTIALS')
    user = users_repo.find_login_user(identifier.strip().lower())
    encoded = user['password_hash'] if user else None
    usable_hash = isinstance(encoded, str) and encoded.startswith('$argon2id$')
    try:
        PASSWORD_HASHER.verify(encoded if usable_hash else DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        raise AccessError('INVALID_CREDENTIALS') from None
    if not usable_hash:
        raise AccessError('INVALID_CREDENTIALS')
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=8)
    with transaction() as database:
        locked = users_repo.lock_login_user(user['id'], connection=database)
        if (not locked or not locked['is_active'] or locked['password_hash'] != encoded
                or locked['auth_version'] != user['auth_version']):
            raise AccessError('INVALID_CREDENTIALS')
        role = sessions_repo.ROLE_MAP.get(locked['role'])
        if role is None or (role != 'platform_admin' and not locked['company_id']):
            raise AccessError('INVALID_CREDENTIALS')
        if locked['company_id']:
            active = database.execute(text('SELECT is_active FROM companies WHERE id=:id FOR UPDATE'),
                                      {'id': locked['company_id']}).scalar_one_or_none()
            if not active:
                raise AccessError('INVALID_CREDENTIALS')
        session_id = sessions_repo.create_session(locked, token, expires_at.replace(tzinfo=None),
                                                  connection=database)
        actor = {'user_id': locked['id'], 'company_id': locked['company_id'], 'role': role,
                 'session_id': session_id, 'auth_version': locked['auth_version'], '_session_token': token}
    return {'actor': actor, 'session_token': token, 'expires_at': expires_at}


def authenticate(session_token):
    """서버에 보관한 토큰으로 요청마다 현재 사용자 문맥을 다시 만든다.

    브라우저가 보낸 dict를 신뢰하지 말고 이 함수의 결과를 서버 함수에 넘긴다.
    """
    return sessions_repo.authenticate_token(session_token)


def logout(actor):
    """토큰을 소유한 현재 로그인 세션을 폐기한다. 성공 시 None이다."""
    sessions_repo.revoke_session(actor)
