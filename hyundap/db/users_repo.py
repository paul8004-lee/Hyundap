"""인증용 사용자 조회. 비밀번호 해시는 이 내부 계층에서만 읽는다."""

from sqlalchemy import text

from .runtime import transaction


def find_login_user(identifier, *, connection=None):
    """정규화 이메일로 사용자 한 건을 내부 dict로 반환한다. 없으면 None이다.

    호출자인 auth.login만 해시를 사용하며 UI에 이 행을 직접 반환하지 않는다.
    """
    with transaction(connection) as database:
        row = database.execute(text('''
            SELECT id, company_id, role, is_active, password_hash, auth_version
            FROM users WHERE login_identifier=:identifier
        '''), {'identifier': identifier}).mappings().one_or_none()
        return dict(row) if row else None


def lock_login_user(user_id, *, connection):
    """비밀번호 검증 이후 계정 변경을 확인할 사용자 행을 같은 TX에서 잠근다."""
    return connection.execute(text('''
        SELECT id, company_id, role, is_active, password_hash, auth_version
        FROM users WHERE id=:id FOR UPDATE
    '''), {'id': user_id}).mappings().one_or_none()
