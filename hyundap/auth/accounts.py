"""auth/accounts.py — 초기 계정 등록 (SPEC-AUTH-001 M3).

담당: 이성민. 운영자 CLI(hyundap.auth.create_account)가 호출한다. 웹 가입은 제공하지 않는다.
비밀번호는 scrypt 해시만 users.password_hash에 저장하고 출력·로그에 남기지 않는다.
"""

from uuid import uuid4

from sqlalchemy import text

from hyundap.db.clock import epoch_now
from hyundap.db.errors import DbError
from hyundap.db.runtime import require_id, transaction
from .passwords import hash_password

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024
EMAIL_MAX_LENGTH = 254
APP_ROLES = ('platform_admin', 'company_admin', 'employee')


def create_account(email, role, company_id, password):
    """users 행 1건을 추가하고 새 사용자 ID를 반환한다.

    platform_admin은 회사 없이, 나머지 역할은 활성 회사 ID와 함께 만든다.
    잘못된 입력은 쓰기 전에 DbError('INVALID_INPUT'), 같은 login_identifier는
    DbError('CONFLICT'), DB 장애는 DbError('DB_UNAVAILABLE')다. 이메일은 앞뒤 공백을 지우고
    소문자로 저장한다. 역할도 앱과 같은 소문자 코드를 사용하며 생성·갱신 시간은
    UTC Unix 초로 채운다. 호출자 TX 안에서 고정 INSERT를 실행한다.
    """
    email = _check_email(email)
    if role not in APP_ROLES:
        raise DbError('INVALID_INPUT')
    if role == 'platform_admin':
        if company_id is not None:
            raise DbError('INVALID_INPUT')
    else:
        require_id(company_id)
    if (not isinstance(password, str)
            or not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH):
        raise DbError('INVALID_INPUT')
    # scrypt는 DB 연결을 잡기 전에 계산한다.
    stored = hash_password(password)
    user_id = str(uuid4())
    with transaction(control=True) as database:
        if company_id is not None:
            active = database.execute(text('SELECT is_active FROM companies WHERE id=:id'),
                                      {'id': company_id}).scalar_one_or_none()
            if not active:
                raise DbError('INVALID_INPUT')
        params = {'id': user_id, 'company_id': company_id, 'email': email,
                  'role': role, 'password_hash': stored, 'now': epoch_now()}
        # login_identifier는 DB가 LOWER(TRIM(email))로 만들고 고유 키 충돌은 CONFLICT가 된다.
        database.execute(text('''
            INSERT INTO users (id,company_id,email,role,is_active,auth_version,password_hash,created_at,updated_at)
            VALUES (:id,:company_id,:email,:role,1,1,:password_hash,:now,:now)
        '''), params)
    return user_id


def _check_email(email):
    """공백을 뺀 이메일이 @ 하나와 앞뒤 부분을 갖고 254자 이하인지 확인해 소문자로 반환한다."""
    if not isinstance(email, str):
        raise DbError('INVALID_INPUT')
    email = email.strip()
    local, at, domain = email.partition('@')
    if not at or not local or not domain or '@' in domain or len(email) > EMAIL_MAX_LENGTH:
        raise DbError('INVALID_INPUT')
    return email.lower()
