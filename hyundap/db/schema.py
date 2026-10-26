"""db/schema.py — DB 구조 조회·검증과 검토용 마이그레이션 SQL 생성.

마이그레이션을 자동 적용하지 않는 읽기 전용 준비 도구다.
입력은 현재 DB 구조이고 결과는 구조 JSON·오류 목록·검토용 SQL이다.
실제 DDL 적용과 버전 번호 확정은 관리자가 별도 절차로 수행한다."""

import argparse
import json
import re
from pathlib import Path

from sqlalchemy import inspect, text

from db.connection import create_db_engine


SUPPORTED_SCHEMA_VERSIONS = (3, 4)
DESIGN_DIR = Path(__file__).resolve().parents[2] / 'docs' / 'db-design'

# 기존 컬럼을 덮어쓰지 않는다. 이름은 같지만 타입이 다르면 사람이 먼저 검토한다.
ADDITIONS = {
    'documents': {
        'doc_type': "VARCHAR(50) NOT NULL DEFAULT 'other'",
        'publication_status': "VARCHAR(16) NOT NULL DEFAULT 'draft'",
        'deleted_at': 'DATETIME(6) NULL',
        'active_version_id': '{document_key} NULL',
    },
    'users': {
        'password_hash': 'VARCHAR(255) NULL',
        'auth_version': 'INT NOT NULL DEFAULT 1',
        'login_identifier': 'VARCHAR(254) GENERATED ALWAYS AS (LOWER(TRIM(email))) STORED',
    },
    'sessions': {
        'token_hash': 'CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL',
        'auth_version': 'INT NOT NULL DEFAULT 0',
        'expires_at': 'DATETIME(6) NULL',
        'revoked_at': 'DATETIME(6) NULL',
    },
    'index_jobs': {
        'version_id': '{document_key} NULL',
        'stage': "VARCHAR(32) NOT NULL DEFAULT 'received'",
        'report': 'JSON NULL',
        'error_code': 'VARCHAR(64) NULL',
        'error_message': 'VARCHAR(1000) NULL',
        'attempts': 'INT NOT NULL DEFAULT 0',
    },
}

INDEXES = {
    'documents': {
        'uq_v4_document_company': ('id', 'company_id'),
    },
    'users': {'uq_v4_login_identifier': ('login_identifier',)},
    'sessions': {'uq_v4_session_token': ('token_hash',)},
    'document_versions': {
        'uq_v4_version_number': ('document_id', 'version_no'),
        'uq_v4_version_identity': ('id', 'document_id', 'company_id'),
        'uq_v4_company_hash': ('company_id', 'sha256'),
    },
    'document_chunks': {'uq_v4_chunk_order': ('version_id', 'chunk_index')},
}

FOREIGN_KEYS = {
    'documents': {
        'fk_v4_document_company': (('company_id',), 'companies', ('id',)),
        'fk_v4_active_version': (
            ('active_version_id', 'id', 'company_id'),
            'document_versions', ('id', 'document_id', 'company_id'),
        ),
    },
    'document_versions': {
        'fk_v4_version_document': (
            ('document_id', 'company_id'), 'documents', ('id', 'company_id'),
        ),
    },
    'document_chunks': {
        'fk_v4_chunk_version': (('version_id',), 'document_versions', ('id',)),
    },
    'index_jobs': {
        'fk_v4_job_version': (('version_id',), 'document_versions', ('id',)),
    },
}

CHECKS = {
    'documents': {
        'ck_v4_publication': "publication_status IN ('draft','published','blocked')",
        'ck_v4_published_pointer': "publication_status <> 'published' OR active_version_id IS NOT NULL",
        'ck_v4_doc_type': "doc_type IN ('manual','policy','guide','other')",
    },
    'document_versions': {
        'ck_v4_version_number': 'version_no >= 1',
        'ck_v4_version_status': "version_status IN ('processing','ready','failed')",
        'ck_v4_data_category': "data_category IN ('operational','evaluation')",
        'ck_v4_file_size': 'file_size BETWEEN 1 AND 104857600',
        'ck_v4_expected_count': 'expected_chunk_count BETWEEN 0 AND 100000',
        'ck_v4_ready_count': "version_status <> 'ready' OR expected_chunk_count > 0",
        'ck_v4_sha256': "sha256 REGEXP '^[0-9a-f]{64}$'",
    },
    'document_chunks': {
        'ck_v4_chunk_index': 'chunk_index BETWEEN 0 AND 99999',
        'ck_v4_chunk_id': "id = CONCAT(version_id, ':', LPAD(chunk_index, 5, '0'))",
        'ck_v4_content_hash': "content_hash REGEXP '^[0-9a-f]{64}$'",
    },
}


def snapshot(connection):
    """연결된 DB의 버전·컬럼·키·제약 구조를 딕셔너리로 읽는다.

    입력 connection은 SQLAlchemy Connection이다. 반환 딕셔너리는
    version, tables, keys를 가지며 사용자 행·문서 본문·접속 URL은 포함하지 않는다.
    schema_migrations가 단일 현재 버전 행이 아니면 ValueError를 발생시킨다.
    메타데이터 조회 오류는 호출자에게 전달하며 DDL·DML은 실행하지 않는다."""
    columns = connection.execute(text('''
        SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE,
               CHARACTER_SET_NAME, COLLATION_NAME, GENERATION_EXPRESSION
        FROM information_schema.columns WHERE table_schema = DATABASE()
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    ''')).mappings()
    tables = {}
    for row in columns:
        tables.setdefault(row['TABLE_NAME'], {})[row['COLUMN_NAME']] = dict(row)
    reader = inspect(connection)
    keys = {}
    for table in tables:
        keys[table] = {
            'primary': reader.get_pk_constraint(table)['constrained_columns'],
            'unique': reader.get_unique_constraints(table),
            'foreign': reader.get_foreign_keys(table),
            'checks': reader.get_check_constraints(table),
            'engine': reader.get_table_options(table).get('mysql_engine'),
        }
    versions = connection.execute(text('SELECT version FROM schema_migrations')).scalars().all()
    if len(versions) != 1:
        raise ValueError('schema_migrations must have exactly one current-version row')
    return {'version': versions[0], 'tables': tables, 'keys': keys}


def key_definition(column):
    """기존 ID 컬럼과 호환되는 새 FK 컬럼의 SQL 정의를 만든다.

    입력 column은 snapshot의 컬럼 메타데이터 딕셔너리다.
    VARCHAR(36), 문자셋, 정렬 규칙(collation)을 확인해 SQL 문자열을 반환한다.
    다른 ID 타입이나 허용하지 않은 식별자는 ValueError로 거부한다. SQL 실행은 하지 않는다."""
    if column['COLUMN_TYPE'].lower() != 'varchar(36)':
        raise ValueError('Existing IDs must be VARCHAR(36); no automatic key conversion')
    charset = column['CHARACTER_SET_NAME']
    collation = column['COLLATION_NAME']
    for value in (charset, collation):
        if not value or not re.fullmatch(r'[a-zA-Z0-9_]+', value):
            raise ValueError('Unsupported key charset/collation')
    return f'VARCHAR(36) CHARACTER SET {charset} COLLATE {collation}'


def prepare_sql(layout):
    """점검한 v3 구조를 기준으로 추가형 v4 마이그레이션 SQL을 만든다.

    입력 layout은 snapshot 결과이며 반환값은 관리자가 검토할 SQL 문자열이다.
    기존 호환 컬럼·제약은 보존하고 키의 문자셋·정렬 규칙을 복사한다.
    지원하지 않는 구조·타입·버전·부분 적용 상태는 ValueError로 중단한다.
    로컬 SQL 템플릿만 읽으며 DB 쓰기와 버전 번호 변경은 수행하지 않는다."""
    tables, keys = layout['tables'], layout['keys']
    if layout['version'] != 3:
        raise ValueError('Preparation requires schema version 3')
    required = {
        'companies': ('id', 'name', 'is_active'),
        'users': ('id', 'company_id', 'email', 'role', 'is_active'),
        'documents': ('id', 'company_id'),
        'sessions': ('id', 'user_id'),
        'index_jobs': ('id',),
        'oauth_states': (), 'audit_logs': (), 'login_attempts': (),
    }
    for table, columns in required.items():
        if table not in tables or not set(columns) <= tables[table].keys():
            raise ValueError(f'Missing base table/columns: {table}')
        if keys[table]['engine'] != 'InnoDB':
            raise ValueError(f'InnoDB required: {table}')
        if columns and keys[table]['primary'] != ['id']:
            raise ValueError(f'Single id primary key required: {table}')
    if 'document_versions' in tables or 'document_chunks' in tables:
        raise ValueError('Existing v4 tables: inspect partial migration before retrying')
    document_key = key_definition(tables['documents']['id'])
    company_key = key_definition(tables['companies']['id'])
    if key_definition(tables['documents']['company_id']) != company_key:
        raise ValueError('documents.company_id differs from companies.id')
    sql = ['-- GENERATED FROM INSPECTED METADATA. Review before administrator execution.',
           '-- Additive DDL only; version marker is NOT advanced here.',
           '-- Stop application writers. Never use mysql --force.',
           "SET time_zone = '+00:00';"]
    # 기존 호환 컬럼은 보존한다. 다른 타입·생성식은 조용히 덮어쓰지 않는다.
    for table, additions in ADDITIONS.items():
        for name, definition in additions.items():
            ddl = definition.format(document_key=document_key)
            if name in tables[table]:
                column = tables[table][name]
                expected_type = ddl.split(' ')[0].lower()
                actual_type = column['COLUMN_TYPE'].lower()
                if actual_type != expected_type:
                    raise ValueError(f'Incompatible existing type: {table}.{name}')
                if 'NOT NULL' in ddl and column['IS_NULLABLE'] != 'NO':
                    raise ValueError(f'Existing nullable column requires review: {table}.{name}')
                if name == 'login_identifier':
                    expression = re.sub(r'[\s`()]', '', column['GENERATION_EXPRESSION']).lower()
                    if expression != 'lowertrimemail':
                        raise ValueError('Incompatible login_identifier generation')
                if name == 'token_hash' and column['COLLATION_NAME'] != 'ascii_bin':
                    raise ValueError('Session token hash requires ascii_bin collation')
                if name == 'active_version_id' and key_definition(column) != document_key:
                    raise ValueError('Active version key charset/collation mismatch')
                continue
            sql.append(f'ALTER TABLE `{table}` ADD COLUMN `{name}` {ddl};')
    for table, indexes in INDEXES.items():
        if table in tables:
            existing = {tuple(item['column_names']) for item in keys[table]['unique']}
            for name, columns in indexes.items():
                if columns not in existing:
                    names = ', '.join(f'`{column}`' for column in columns)
                    sql.append(f'ALTER TABLE `{table}` ADD CONSTRAINT `{name}` UNIQUE ({names});')
    template = (DESIGN_DIR / 'migrations' / '004_tables.sql').read_text(encoding='utf-8')
    sql.append(template.replace('__DOCUMENT_KEY__', document_key).replace('__COMPANY_KEY__', company_key))
    for table, constraints in FOREIGN_KEYS.items():
        existing = keys.get(table, {}).get('foreign', [])
        for name, (columns, target, referred) in constraints.items():
            if any(tuple(item['constrained_columns']) == columns
                   and item['referred_table'] == target
                   and tuple(item['referred_columns']) == referred for item in existing):
                continue
            left = ', '.join(f'`{column}`' for column in columns)
            right = ', '.join(f'`{column}`' for column in referred)
            sql.append(f'ALTER TABLE `{table}` ADD CONSTRAINT `{name}` FOREIGN KEY ({left}) '
                       f'REFERENCES `{target}` ({right}) ON DELETE RESTRICT ON UPDATE RESTRICT;')
    for table, constraints in CHECKS.items():
        for name, expression in constraints.items():
            sql.append(f'ALTER TABLE `{table}` ADD CONSTRAINT `{name}` CHECK ({expression});')
    return '\n\n'.join(sql) + '\n'


def validate_v4(layout):
    """v4에서 요구하는 컬럼·타입·기본 키·유일성·FK·CHECK 존재를 검사한다.

    입력 layout은 snapshot 결과, 반환값은 발견한 구조 오류 문자열의 목록이다.
    빈 목록이면 이 함수가 확인하는 구조 조건을 통과한 것이다.
    문서 내용·Chroma 적재·모든 CHECK 식의 의미나 앱 전체 동작까지 검증하지는 않는다."""
    errors = []
    required = {table: set(columns) for table, columns in ADDITIONS.items()}
    required.update({
        'document_versions': {'id', 'document_id', 'company_id', 'version_no', 'file_name',
                              'storage_path', 'sha256', 'file_size', 'version_status',
                              'data_category', 'expected_chunk_count'},
        'document_chunks': {'id', 'version_id', 'chunk_index', 'content_hash'},
    })
    for table, columns in required.items():
        missing = columns - layout['tables'].get(table, {}).keys()
        if missing:
            errors.append(f'{table}: missing columns {sorted(missing)}')
    for table, additions in ADDITIONS.items():
        for name, definition in additions.items():
            column = layout['tables'].get(table, {}).get(name)
            if column is None:
                continue
            expected_type = 'varchar(36)' if name in ('active_version_id', 'version_id') else definition.split(' ')[0].lower()
            if column['COLUMN_TYPE'].lower() != expected_type:
                errors.append(f'{table}.{name}: incompatible type')
            if 'NOT NULL' in definition and column['IS_NULLABLE'] != 'NO':
                errors.append(f'{table}.{name}: NOT NULL required')
    for table in ('document_versions', 'document_chunks'):
        if layout['keys'].get(table, {}).get('primary') != ['id']:
            errors.append(f'{table}: id primary key required')
    for table, indexes in INDEXES.items():
        actual = {tuple(item['column_names']) for item in layout['keys'].get(table, {}).get('unique', [])}
        for columns in indexes.values():
            if columns not in actual:
                errors.append(f'{table}: missing unique {columns}')
    for table, constraints in FOREIGN_KEYS.items():
        actual = layout['keys'].get(table, {}).get('foreign', [])
        for name, (columns, target, referred) in constraints.items():
            if not any(tuple(item['constrained_columns']) == columns
                       and item['referred_table'] == target
                       and tuple(item['referred_columns']) == referred for item in actual):
                errors.append(f'{table}: missing foreign key {name}')
    for table, constraints in CHECKS.items():
        actual = {item['name'] for item in layout['keys'].get(table, {}).get('checks', [])}
        for name in constraints:
            if name not in actual:
                errors.append(f'{table}: missing check {name}')
    return errors


def audit_counts(connection):
    """v4 검증 SQL을 실행하고 DB 내부 정합성 위반 건수를 반환한다.

    입력 connection은 SQLAlchemy Connection이다. 반환값은
    check_name과 violations를 가진 딕셔너리 목록이다. 활성 버전·회사 연결과
    ready 청크 목록의 개수·순번을 조회하며 행 내용이나 비밀정보는 출력하지 않는다.
    필수 v4 구조가 적용돼 있어야 하고 외부 Chroma 집합 비교는 별도 서비스 책임이다."""
    queries = (DESIGN_DIR / 'migrations' / '004_verify.sql').read_text(encoding='utf-8')
    counts = []
    for statement in queries.split(';'):
        if statement.strip():
            row = connection.execute(text(statement)).mappings().one()
            counts.append(dict(row))
    return counts


def main():
    """구조 조회·SQL 준비·v4 점검 중 하나를 수행하는 CLI 진입점.

    --inspect는 구조 JSON, --prepare 경로는 새 검토용 SQL 파일을 만들고,
    --check는 구조·DB 내부 정합성을 확인한다. DB에는 읽기 전용 조회만 수행한다.
    기존 출력 파일은 덮어쓰지 않는다. 성공은 0, 구조·데이터·접속·파일 오류는 1이다.
    DB 예외는 원문 대신 종류만 출력해 접속 비밀값 노출을 막고 Engine을 정리한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--inspect', action='store_true', help='비밀값 없는 구조 JSON 출력')
    action.add_argument('--prepare', type=Path, help='검토용 SQL 생성; DB 실행 없음')
    action.add_argument('--check', action='store_true', help='v4 구조·정합성 점검; 버전 변경 없음')
    args = parser.parse_args()
    engine = None
    try:
        engine = create_db_engine()
        with engine.connect() as connection:
            layout = snapshot(connection)
            if args.inspect:
                print(json.dumps(layout, ensure_ascii=False, indent=2, default=str))
            elif args.prepare:
                sql = prepare_sql(layout)
                # 회사 FK와 정규화 이메일 유일성에 걸리는 기존 데이터가 있으면 준비도 중단한다.
                orphan_count = connection.execute(text('''
                    SELECT COUNT(*) FROM documents d LEFT JOIN companies c ON c.id=d.company_id
                    WHERE c.id IS NULL
                ''')).scalar_one()
                email_count = connection.execute(text('''
                    SELECT COUNT(*) FROM (
                        SELECT LOWER(TRIM(email)) FROM users WHERE email IS NOT NULL
                        GROUP BY LOWER(TRIM(email)) HAVING COUNT(*) > 1
                        UNION ALL SELECT email FROM users WHERE CHAR_LENGTH(email) > 254
                    ) invalid_emails
                ''')).scalar_one()
                if orphan_count or email_count:
                    raise ValueError('Legacy company/email data needs repair before preparation')
                with args.prepare.open('x', encoding='utf-8') as output:
                    output.write(sql)
                print('MIGRATION_SQL_PREPARED: no database writes executed')
            else:
                if layout['version'] not in SUPPORTED_SCHEMA_VERSIONS:
                    raise ValueError('Unsupported schema version')
                errors = validate_v4(layout)
                if errors:
                    print(json.dumps({'layout_errors': errors}, ensure_ascii=False))
                    return 1
                counts = audit_counts(connection)
                print(json.dumps({'version': layout['version'], 'audit': counts}, ensure_ascii=False))
                if any(row['violations'] for row in counts):
                    return 1
        return 0
    except (OSError, ValueError) as error:
        # ValueError는 고정 진단문만 사용한다. DB 예외 원문은 아래에서 숨긴다.
        print(f'SCHEMA_CHECK_FAILED: {error}')
        return 1
    except Exception as error:
        print(f'SCHEMA_CHECK_FAILED: {type(error).__name__}; check local configuration and schema')
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
