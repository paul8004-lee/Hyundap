"""공통 인증 서비스 계정의 명시적 권한표. 계정 생성·비밀 교체는 관리자 책임이다."""

import re
from sqlalchemy import text
from .errors import DbError
from .tenancy import quoted_database


# 일상 로그인·세션 폐기·회사 라우팅 조회만 허용한다. 사용자/회사 생성은 관리자 작업이다.
CONTROL_SERVICE_PRIVILEGES = {
    'companies': 'SELECT',
    'users': 'SELECT',
    'sessions': 'SELECT,INSERT,UPDATE',
    'company_databases': 'SELECT',
    'document_routes': 'SELECT',
    'schema_migrations': 'SELECT',
    # 데이터 조회는 주지 않는다. 구조 검증기의 information_schema 가시성만 제공한다.
    # REFERENCES만으로 테이블 생성/변경이나 행 읽기를 할 수 없다.
    'oauth_states': 'REFERENCES',
    'audit_logs': 'REFERENCES',
    'login_attempts': 'REFERENCES',
}


def grant_control_service_access(admin_engine, username, host='localhost'):
    """이미 생성한 전용 계정에 공통 DB 권한표만 적용한다. 기존 권한 제거는 하지 않는다.

    운영자가 비어 있는 신규 계정에 실행하는 함수다. 기존 root 계정을 최소 권한 계정으로
    바꾸는 함수가 아니다. 업무 요청에서는 호출하지 않으며 회사 DB 권한을 추가하지 않는다.
    """
    if admin_engine.dialect.name != 'mysql' or not re.fullmatch(r'[a-zA-Z0-9_]{1,32}', username or ''):
        raise DbError('INVALID_INPUT')
    if not isinstance(host, str) or not re.fullmatch(r'[a-zA-Z0-9.%:_-]{1,255}', host):
        raise DbError('INVALID_INPUT')
    database_name = quoted_database(admin_engine.url.database)
    with admin_engine.connect() as database:
        for table, privileges in CONTROL_SERVICE_PRIVILEGES.items():
            # 표 이름·권한은 위 고정 권한표에서만 가져온다. 계정 값은 바인딩한다.
            statement = f'GRANT {privileges} ON {database_name}.{quoted_database(table)} TO :user@:host'
            database.execute(text(statement), {'user': username, 'host': host})
