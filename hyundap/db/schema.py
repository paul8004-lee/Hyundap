"""최종 v4 직접 생성과 관리용 실측 검증. 기존 DB는 자동 변경하지 않는다."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from .connection import create_db_engine

SUPPORTED_SCHEMA_VERSIONS = (4,)
DESIGN_DIR = Path(__file__).resolve().parents[2] / 'docs' / 'db-design'


def _read_layout(connection):
    """버전 기록 전에도 컬럼·인덱스·FK·CHECK를 읽는다. 업무 행은 읽지 않는다."""
    tables = {}
    for row in connection.execute(text('''
        SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,CHARACTER_SET_NAME,
               COLLATION_NAME,GENERATION_EXPRESSION,COLUMN_DEFAULT,EXTRA
        FROM information_schema.columns WHERE table_schema=DATABASE()
        ORDER BY TABLE_NAME,ORDINAL_POSITION
    ''')).mappings():
        tables.setdefault(row['TABLE_NAME'], {})[row['COLUMN_NAME']] = dict(row)
    reader = inspect(connection)
    keys = {}
    for table in tables:
        keys[table] = {
            'primary': reader.get_pk_constraint(table)['constrained_columns'],
            'unique': reader.get_unique_constraints(table),
            'foreign': reader.get_foreign_keys(table),
            'checks': reader.get_check_constraints(table),
            'indexes': reader.get_indexes(table),
            'engine': reader.get_table_options(table).get('mysql_engine'),
        }
    for table, name, enforced in connection.execute(text('''
        SELECT TABLE_NAME,CONSTRAINT_NAME,ENFORCED FROM information_schema.table_constraints
        WHERE constraint_schema=DATABASE() AND constraint_type='CHECK'
    ''')):
        for check in keys[table]['checks']:
            if check['name'] == name:
                check['enforced'] = enforced
    return {'tables': tables, 'keys': keys}


def snapshot(connection):
    """관리 연결의 전체 구조와 단일 버전 행을 반환한다. 조회 오류는 전달한다."""
    layout = _read_layout(connection)
    versions = connection.execute(text('SELECT version FROM schema_migrations')).scalars().all()
    if len(versions) != 1:
        raise ValueError('schema_migrations must have exactly one current-version row')
    return dict(layout, version=versions[0])


def _expression(value):
    """MySQL 메타데이터 표현식의 공백·식별자 인용 표기만 정규화한다."""
    if value is None:
        return None
    # MySQL은 ALTER CHECK 때 동일한 ASCII 상수의 문자셋 표기를 다시 쓴다.
    value = re.sub(r"_(?:ascii|utf8mb4)('(?:''|[^'])*')",
                   lambda match: match[1] if match[1].isascii() else match[0], str(value))
    parts = re.findall(r"'(?:''|[^'])*'|[^'\s`]+", str(value))
    return ''.join(part if part.startswith("'") else part.lower() for part in parts)


def _structure(layout):
    """컬럼 속성과 모든 제약을 순서 독립 dict로 만들어 구조를 비교한다."""
    result = {}
    for table, columns in layout['tables'].items():
        keys = layout['keys'][table]
        result[table] = {
            'columns': {name: [row['COLUMN_TYPE'].lower(), row['IS_NULLABLE'],
                row['CHARACTER_SET_NAME'], row['COLLATION_NAME'],
                row['COLUMN_DEFAULT'], _expression(row['GENERATION_EXPRESSION']),
                row['EXTRA'].lower()] for name, row in columns.items()},
            'engine': keys['engine'],
            'primary': keys['primary'],
            'unique': {item['name']: item['column_names'] for item in keys['unique']},
            'indexes': {item['name']: [item['column_names'], bool(item['unique'])] for item in keys['indexes']},
            'foreign': {item['name']: [item['constrained_columns'], item['referred_table'],
                item['referred_columns'], item.get('options', {})] for item in keys['foreign']},
            'checks': {item['name']: [_expression(item['sqltext']), item.get('enforced')]
                       for item in keys['checks']},
        }
    return result


def validate_v4(layout):
    """확정 계약과 모든 테이블·컬럼 속성·제약을 비교해 구조 오류 목록을 반환한다.

    같은 버전 숫자를 가진 과거 혼합 v4도 다른 구조면 거부한다.
    버전 기록 전 생성 검증에도 사용하며 버전 행 자체는 snapshot이 검사한다.
    """
    expected = json.loads((DESIGN_DIR / 'schema_v4_contract.json').read_text(encoding='utf-8'))
    actual = _structure(layout)
    errors = []
    for table in sorted(expected.keys() | actual.keys()):
        if table not in expected or table not in actual:
            errors.append(f'{table}: unexpected or missing table')
            continue
        for field in expected[table]:
            if actual[table][field] != expected[table][field]:
                errors.append(f'{table}: incompatible {field}')
    if layout.get('version', 4) != 4:
        errors.append('schema version mismatch')
    return errors


def create_v4_schema(engine):
    """명시적으로 받은 Engine의 빈 MySQL DB에 최종 v4를 직접 생성한다.

    전용 연결의 DDL 실행과 전체 구조 검증 뒤에만 버전 4를 기록한다.
    기존 객체·지원하지 않는 서버는 쓰기 전에 거부한다. DDL은 암시적 커밋되므로
    실패 시 자동 삭제·덮어쓰기·재시도하지 않는다. 업무 TX 연결은 받지 않는다.
    """
    if not isinstance(engine, Engine) or engine.dialect.name != 'mysql':
        raise ValueError('MySQL 8.4 or later required')
    with engine.connect() as connection:
        server = str(connection.scalar(text('SELECT VERSION()')))
        if 'mariadb' in server.lower() or tuple(map(int, server.split('-')[0].split('.')[:2])) < (8, 4):
            raise ValueError('MySQL 8.4 or later required')
        database = connection.scalar(text('SELECT DATABASE()'))
        if not database:
            raise ValueError('Select an empty database before creation')
        lock = 'hyundap.schema.' + hashlib.sha256(database.encode()).hexdigest()[:40]
        if connection.scalar(text('SELECT GET_LOCK(:name,0)'), {'name': lock}) != 1:
            raise ValueError('Schema creation is already running')
        try:
            if connection.scalar(text('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()')):
                raise ValueError('Refuse a nonempty database')
            # 로컬 계약 파일 오류는 DDL 실행 전 발견한다.
            json.loads((DESIGN_DIR / 'schema_v4_contract.json').read_text(encoding='utf-8'))
            connection.execute(text("SET time_zone='+00:00'"))
            source = (DESIGN_DIR / 'schema_v4.sql').read_text(encoding='utf-8')
            script = '\n'.join(line for line in source.splitlines() if not line.lstrip().startswith('--'))
            for statement in script.split(';'):
                if statement.strip():
                    connection.execute(text(statement))
            if validate_v4(_read_layout(connection)):
                raise ValueError('Created structure differs from final v4; inspect partial DDL')
            connection.execute(text('INSERT INTO schema_migrations (version) VALUES (4)'))
            connection.commit()
        finally:
            connection.execute(text('SELECT RELEASE_LOCK(:name)'), {'name': lock})


def audit_counts(connection):
    """읽기 전용 정합성 위반 건수를 반환한다. 외부 벡터 검사는 포함하지 않는다."""
    queries = (DESIGN_DIR / 'schema_v4_verify.sql').read_text(encoding='utf-8')
    return [dict(connection.execute(text(sql)).mappings().one())
            for sql in queries.split(';') if sql.strip()]


def main():
    """명시적 --create 또는 읽기 전용 --inspect/--check를 실행한다. 비밀값은 출력하지 않는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--create', action='store_true', help='빈 DB에 최종 v4 생성')
    action.add_argument('--inspect', action='store_true')
    action.add_argument('--check', action='store_true')
    args = parser.parse_args()
    engine = None
    try:
        engine = create_db_engine()
        if args.create:
            create_v4_schema(engine)
            print('SCHEMA_V4_CREATED')
            return 0
        with engine.connect() as connection:
            layout = snapshot(connection)
            if args.inspect:
                print(json.dumps(layout, ensure_ascii=False, default=str))
                return 0
            errors = validate_v4(layout)
            if errors:
                print(json.dumps({'layout_errors': errors}))
                return 1
            counts = audit_counts(connection)
            print(json.dumps({'version': layout['version'], 'audit': counts}))
            return int(any(row['violations'] for row in counts))
    except Exception as error:
        print(f'SCHEMA_CHECK_FAILED: {type(error).__name__}; inspect configuration and partial DDL')
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
