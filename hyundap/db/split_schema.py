"""빈 DB 전용 공통/회사 분리 스키마. 기존 v4 SQL을 분리해 제약을 재사용한다.

    기존 DB를 변환하거나 삭제하지 않는다. 같은 MySQL 서버의 InnoDB DB 간 FK를 사용한다.
"""

import json
import re
import hashlib

from sqlalchemy import text

from .schema import DESIGN_DIR, _read_layout, _structure, _expression
from .tenancy import quoted_database


BUSINESS_TABLES = {'documents', 'document_versions', 'document_chunks', 'index_jobs'}
CONTROL_TABLES = {'companies', 'users', 'sessions', 'oauth_states', 'audit_logs',
                  'login_attempts', 'schema_migrations'}
REGISTRY_SQL = '''
CREATE TABLE company_databases (
    company_id VARCHAR(36) NOT NULL PRIMARY KEY,
    database_name VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
    status VARCHAR(16) NOT NULL,
    schema_version INT NOT NULL DEFAULT 4,
    error_code VARCHAR(64) NULL,
    CONSTRAINT fk_registry_company FOREIGN KEY (company_id) REFERENCES companies(id),
    CONSTRAINT ck_registry_status CHECK (status IN ('creating','ready','failed')),
    CONSTRAINT ck_registry_version CHECK (schema_version=4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin
'''
ROUTES_SQL = '''
CREATE TABLE document_routes (
    version_id VARCHAR(36) NOT NULL PRIMARY KEY,
    document_id VARCHAR(36) NOT NULL,
    job_id VARCHAR(36) NOT NULL UNIQUE,
    company_id VARCHAR(36) NOT NULL,
    INDEX ix_route_document (document_id),
    CONSTRAINT fk_route_company FOREIGN KEY (company_id) REFERENCES companies(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin
'''
IDENTITY_SQL = '''
CREATE TABLE tenant_identity (
    company_id VARCHAR(36) NOT NULL PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin
'''


def _extra_contract(name):
    """신규 등록부와 소속 표의 고정 계약이다. 실측 결과로 기대값을 만들지 않는다."""
    identifier = ['varchar(36)', 'NO', 'utf8mb4', 'utf8mb4_0900_bin', None, '', '']
    result = {'columns': {}, 'engine': 'InnoDB', 'primary': ['company_id'],
              'unique': {}, 'indexes': {}, 'foreign': {}, 'checks': {}}
    if name == 'tenant_identity':
        result['columns'] = {'company_id': identifier}
        return result
    if name == 'company_databases':
        result['foreign'] = {'fk_registry_company': [['company_id'], 'companies', ['id'], {}]}
        result['columns'] = {
            'company_id': identifier,
            'database_name': ['varchar(64)', 'NO', 'ascii', 'ascii_bin', None, '', ''],
            'status': ['varchar(16)', 'NO', 'utf8mb4', 'utf8mb4_0900_bin', None, '', ''],
            'schema_version': ['int', 'NO', None, None, '4', '', ''],
            'error_code': ['varchar(64)', 'YES', 'utf8mb4', 'utf8mb4_0900_bin', None, '', ''],
        }
        result['unique'] = {'database_name': ['database_name']}
        result['indexes'] = {'database_name': [['database_name'], True]}
        result['checks'] = {'ck_registry_status': ["(statusin('creating','ready','failed'))", 'YES'],
                            'ck_registry_version': ['(schema_version=4)', 'YES']}
    else:
        result['foreign'] = {'fk_route_company': [['company_id'], 'companies', ['id'], {}]}
        # 등록부의 각 ID 컬럼은 같은 문자열 규격을 사용한다. 한 번에 한 컬럼씩 등록한다.
        for field in ('version_id', 'document_id', 'job_id', 'company_id'):
            result['columns'][field] = identifier
        result['primary'] = ['version_id']
        result['unique'] = {'job_id': ['job_id']}
        result['indexes'] = {'job_id': [['job_id'], True], 'ix_route_document': [['document_id'], False],
                             'fk_route_company': [['company_id'], False]}
    return result


def _statements(kind, control_name=None):
    """고정 v4 SQL에서 해당 역할의 테이블만 선택한다. 관리 DB 참조만 명시적으로 바꾼다."""
    source = (DESIGN_DIR / 'schema_v4.sql').read_text(encoding='utf-8')
    # SQL 설명 줄을 먼저 제거한다. 아래 SQL 문장 반복과 중첩되는 반복이 아니다.
    sql_lines = []
    for line in source.splitlines():
        if not line.lstrip().startswith('--'):
            sql_lines.append(line)
    source = '\n'.join(sql_lines)
    if kind == 'control':
        allowed = CONTROL_TABLES
    else:
        allowed = BUSINESS_TABLES | {'schema_migrations'}
    result = []
    for statement in source.split(';'):
        statement = statement.strip()
        if not statement:
            continue
        # 고정 SQL에서 테이블 이름만 읽는다. 사용자 SQL을 해석하는 범용 파서가 아니다.
        table = re.match(r'(?:CREATE|ALTER) TABLE (\w+)', statement)
        if table is None:
            continue
        table_name = table.group(1)
        if table_name not in allowed:
            continue
        if kind == 'control':
            # 선택 문서/감사 로그의 문서 ID는 회사 DB 식별자이므로 공통 DB의 로컬 FK를 제거한다.
            statement = re.sub(r'    CONSTRAINT fk_(?:session|audit)_document[^\n]*\n', '', statement)
        else:
            prefix = quoted_database(control_name)
            statement = statement.replace('REFERENCES companies(', f'REFERENCES {prefix}.companies(')
            statement = statement.replace('REFERENCES users(', f'REFERENCES {prefix}.users(')
        result.append(statement)
    return result


def _base_errors(connection, role, company_id=None, control_name=None, *, layout=None):
    """기본 계약을 비교하고 (오류, 실측 layout, 비교용 구조)를 반환한다.

    layout은 같은 점검에서 이미 읽은 경우만 전달한다. 생략하면 새로 실측한다.
    비교용 구조는 회사 CHECK를 분리해 비교하므로 호출자는 추가 테이블 검사에 재사용한다.
    원본 layout은 변경하지 않는다.
    """
    if layout is None:
        layout = _read_layout(connection)
    actual = _structure(layout)
    base = json.loads((DESIGN_DIR / 'schema_v4_contract.json').read_text(encoding='utf-8'))
    if role == 'control':
        names = CONTROL_TABLES
    else:
        names = BUSINESS_TABLES | {'schema_migrations'}
    errors = []
    for name in names:
        expected = base[name]
        if role == 'control' and name in ('sessions', 'audit_logs'):
            if name == 'sessions':
                document_key = 'fk_session_document'
            else:
                document_key = 'fk_audit_document'
            expected['foreign'].pop(document_key, None)
            # 두 FK의 자동 인덱스는 테이블 생성 때 함께 사라진다.
            expected['indexes'].pop(document_key, None)
        found = actual.get(name)
        if role == 'company' and found and name in ('documents', 'document_versions', 'index_jobs'):
            guard = found['checks'].pop('ck_tenant_' + name, None)
            expression = _expression("(company_id = '" + company_id + "')")
            if guard != [expression, "YES"]:
                errors.append(name + ': company boundary mismatch')
        if found != expected:
            errors.append(name + ': incompatible structure')
    if role == 'company':
        # 바깥 반복은 업무 테이블 하나를 고른다.
        for table in BUSINESS_TABLES:
            table_keys = layout['keys'].get(table, {})
            foreign_keys = table_keys.get('foreign', [])
            # 안쪽 반복은 현재 테이블의 FK만 검사한다. 다음 테이블은 이 반복이 끝난 뒤 처리한다.
            for fk in foreign_keys:
                if fk['referred_table'] in ('companies', 'users') and fk.get('referred_schema') != control_name:
                    errors.append(table + ': wrong control database')
    if 'schema_migrations' in actual:
        if connection.execute(text('SELECT version FROM schema_migrations')).scalars().all() != [4]:
            errors.append('schema version mismatch')
    return errors, layout, actual


def validate_control(connection, *, layout=None):
    """공통 DB의 인증/등록 구조를 검사한다. 같은 점검의 실측 layout만 재사용한다."""
    errors, layout, actual = _base_errors(connection, 'control', layout=layout)
    if set(layout['tables']) != CONTROL_TABLES | {'company_databases', 'document_routes'}:
        errors.append('control table set mismatch')
    # 기본 계약과 추가 등록 테이블은 한 번 정규화한 같은 구조로 검사한다.
    for name in ('company_databases', 'document_routes'):
        if actual.get(name) != _extra_contract(name):
            errors.append(name + ': incompatible structure')
        for fk in layout['keys'].get(name, {}).get('foreign', []):
            if fk.get('referred_schema') not in (None, connection.engine.url.database):
                errors.append(name + ': wrong control database')
    return errors


def validate_company(connection, control_name, company_id):
    """회사 DB의 인증 테이블 부재·소속·공통 FK·원장 제약을 검사한다."""
    errors, layout, actual = _base_errors(connection, 'company', company_id, control_name)
    if set(layout['tables']) != BUSINESS_TABLES | {'schema_migrations', 'tenant_identity'}:
        errors.append('company table set mismatch')
    if actual.get('tenant_identity') != _extra_contract('tenant_identity'):
        errors.append('tenant identity structure mismatch')
    elif connection.execute(text('SELECT company_id FROM tenant_identity')).scalars().all() != [company_id]:
        errors.append('tenant identity mismatch')
    return errors


def create_split_schema(engine, *, control_name=None, company_id=None):
    """명시한 빈 DB만 생성한다. 실패 후 자동 DROP/재실행은 하지 않는다."""
    from .runtime import require_id
    if company_id is None:
        role = 'control'
    else:
        role = 'company'
    if role == 'company':
        # CHECK의 문자열 값도 서버 ID 규칙으로 제한한다.
        require_id(company_id)
        if re.fullmatch(r'[a-zA-Z0-9_-]{1,36}', company_id) is None:
            raise ValueError('Invalid company identifier')
        quoted_database(control_name)
    with engine.connect() as connection:
        server = str(connection.scalar(text('SELECT VERSION()')))
        if 'mariadb' in server.lower():
            raise ValueError('MySQL 8.4 or later required')
        # 버전 문자열에서 주 버전과 부 버전을 정수로 바꿔 순서대로 비교한다.
        version_parts = server.split('-')[0].split('.')[:2]
        version_numbers = []
        for part in version_parts:
            version_numbers.append(int(part))
        if tuple(version_numbers) < (8, 4):
            raise ValueError('MySQL 8.4 or later required')
        database_name = connection.scalar(text('SELECT DATABASE()'))
        quoted_database(database_name)
        lock = 'hyundap.split.' + hashlib.sha256(database_name.encode()).hexdigest()[:40]
        if connection.scalar(text('SELECT GET_LOCK(:name,0)'), {'name': lock}) != 1:
            raise ValueError('Schema creation is already running')
        try:
            if connection.scalar(text('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()')):
                raise ValueError('Refuse a nonempty database')
            for statement in _statements(role, control_name):
                connection.execute(text(statement))
            if role == 'control':
                connection.execute(text(REGISTRY_SQL))
                connection.execute(text(ROUTES_SQL))
            else:
                connection.execute(text(IDENTITY_SQL))
                connection.execute(text('INSERT INTO tenant_identity VALUES (:company)'), {'company': company_id})
                # CHECK는 다른 회사의 문서를 잘못 라우팅해도 저장되지 않게 하는 마지막 경계다.
                for table in ('documents', 'document_versions', 'index_jobs'):
                    connection.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT ck_tenant_{table} CHECK (company_id='{company_id}')"))
            connection.execute(text('INSERT INTO schema_migrations VALUES (4)'))
            if role == 'control':
                errors = validate_control(connection)
            else:
                errors = validate_company(connection, control_name, company_id)
            if errors:
                raise ValueError('Split schema validation failed: ' + ', '.join(errors))
            connection.commit()
        finally:
            connection.execute(text('SELECT RELEASE_LOCK(:name)'), {'name': lock})
