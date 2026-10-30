"""최종 v4 직접 생성과 관리용 실측 검증. 기존 DB는 자동 변경하지 않는다."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from .connection import create_db_engine

DESIGN_DIR = Path(__file__).resolve().parents[2] / 'docs' / 'db-design'


def _read_layout(connection, *, exclude_company_view=False):
    """버전 기록 전에도 컬럼·인덱스·FK·CHECK를 읽는다. 업무 행은 읽지 않는다."""
    # 업무 데이터 대신 DB 메타데이터를 읽어 테이블 구조를 모은다.
    tables = {}
    query = '''
        SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,CHARACTER_SET_NAME,
               COLLATION_NAME,GENERATION_EXPRESSION,COLUMN_DEFAULT,EXTRA
        FROM information_schema.columns WHERE table_schema=DATABASE()
    '''
    if exclude_company_view:
        query += ''' AND NOT (TABLE_NAME='company_information' AND TABLE_NAME IN
            (SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema=DATABASE() AND TABLE_TYPE='VIEW'))'''
    query += ' ORDER BY TABLE_NAME,ORDINAL_POSITION'
    column_rows = connection.execute(text(query)).mappings()
    # 첫 번째 반복은 컬럼 행마다 테이블 이름과 컬럼 이름으로 묶는다.
    # 예: tables['documents']['id']에 id 컬럼의 속성을 저장한다.
    for row in column_rows:
        table_name = row['TABLE_NAME']
        if table_name not in tables:
            tables[table_name] = {}
        tables[table_name][row['COLUMN_NAME']] = dict(row)
    # SQLAlchemy inspector로 테이블별 키, 제약 조건, 인덱스를 읽는다.
    reader = inspect(connection)
    keys = {}
    # 테이블별 제약과 인덱스를 같은 이름의 컬럼 목록과 함께 보관한다.
    # 두 번째 반복은 각 테이블의 키와 제약을 조회한다. 컬럼 행 반복은 이미 끝났다.
    for table in tables:
        keys[table] = {
            'primary': reader.get_pk_constraint(table)['constrained_columns'],
            'unique': reader.get_unique_constraints(table),
            'foreign': reader.get_foreign_keys(table),
            'checks': reader.get_check_constraints(table),
            'indexes': reader.get_indexes(table),
            'engine': reader.get_table_options(table).get('mysql_engine'),
        }
    # 세 번째 바깥 반복은 DB가 반환한 CHECK 제약을 하나씩 읽는다.
    for table, name, enforced in connection.execute(text('''
        SELECT TABLE_NAME,CONSTRAINT_NAME,ENFORCED FROM information_schema.table_constraints
        WHERE constraint_schema=DATABASE() AND constraint_type='CHECK'
    ''')):
        # 안쪽 반복은 현재 테이블에 보관된 CHECK 중 이름이 같은 항목을 찾는다.
        # 다른 테이블의 CHECK 목록은 이 안쪽 반복에 들어오지 않는다.
        for check in keys[table]['checks']:
            if check['name'] == name:
                check['enforced'] = enforced
                # 테이블 안에서 CHECK 이름은 유일하므로 현재 제약의 탐색만 끝낸다.
                break
    return {'tables': tables, 'keys': keys}


def snapshot(connection):
    """관리 연결의 전체 구조와 단일 버전 행을 반환한다. 조회 오류는 전달한다."""
    # 구조와 함께 현재 스키마 버전 행이 정확히 하나인지 확인한다.
    layout = _read_layout(connection)
    versions = connection.execute(text('SELECT version FROM schema_migrations')).scalars().all()
    if len(versions) != 1:
        raise ValueError('schema_migrations must have exactly one current-version row')
    return dict(layout, version=versions[0])


def _ascii_literal(match):
    """re.sub가 찾은 문자셋 표기에서 ASCII 문자열의 접두사만 제거한다.

    group(0)은 접두사를 포함한 전체 일치 부분, group(1)은 따옴표로 감싼 값이다.
    한글 등 비ASCII 값은 문자셋 의미가 있으므로 원래 표기를 그대로 반환한다.
    """
    literal = match.group(1)
    if literal.isascii():
        return literal
    return match.group(0)


def _expression(value):
    """MySQL 메타데이터 표현식의 공백·식별자 인용 표기만 정규화한다."""
    if value is None:
        return None
    # MySQL은 ALTER CHECK 때 동일한 ASCII 상수의 문자셋 표기를 다시 쓴다.
    value = re.sub(r"_(?:ascii|utf8mb4)('(?:''|[^'])*')", _ascii_literal, str(value))
    parts = re.findall(r"'(?:''|[^'])*'|[^'\s`]+", value)
    normalized_parts = []
    # 문자열 값은 대소문자까지 보존하고, SQL 이름과 연산자 부분만 소문자로 통일한다.
    for part in parts:
        if part.startswith("'"):
            normalized_parts.append(part)
        else:
            normalized_parts.append(part.lower())
    return ''.join(normalized_parts)


def _structure(layout):
    """컬럼 속성과 모든 제약을 순서 독립 dict로 만들어 구조를 비교한다."""
    # 컬럼과 제약을 비교 가능한 사전으로 정리하고 이름별로 보관한다.
    result = {}
    # 바깥 반복은 테이블 하나씩 처리한다. 아래의 각 목록 반복은 현재 테이블에만 적용된다.
    for table, columns in layout['tables'].items():
        keys = layout['keys'][table]
        column_details = {}
        # 현재 테이블의 컬럼별 속성을 비교할 형태로 모은다.
        for name, row in columns.items():
            column_details[name] = [
                row['COLUMN_TYPE'].lower(), row['IS_NULLABLE'],
                row['CHARACTER_SET_NAME'], row['COLLATION_NAME'],
                row['COLUMN_DEFAULT'], _expression(row['GENERATION_EXPRESSION']),
                row['EXTRA'].lower(),
            ]
        unique = {}
        # 컬럼 반복이 끝난 뒤 UNIQUE 제약을 별도로 정리한다.
        for item in keys['unique']:
            unique[item['name']] = item['column_names']
        indexes = {}
        # 이어지는 반복들은 중첩이 아니라 인덱스, 외래키, CHECK를 각각 순서대로 다룬다.
        for item in keys['indexes']:
            indexes[item['name']] = [item['column_names'], bool(item['unique'])]
        foreign = {}
        # 앞의 인덱스 반복이 끝나면 외래키 목록을 돌며 참조 테이블과 컬럼을 정리한다.
        for item in keys['foreign']:
            foreign[item['name']] = [item['constrained_columns'], item['referred_table'],
                                     item['referred_columns'], item.get('options', {})]
        checks = {}
        # 마지막으로 CHECK 목록을 돌며 조건식과 적용 여부를 정리한다.
        for item in keys['checks']:
            checks[item['name']] = [_expression(item['sqltext']), item.get('enforced')]
        result[table] = {
            'columns': column_details,
            'engine': keys['engine'],
            'primary': keys['primary'],
            'unique': unique,
            'indexes': indexes,
            'foreign': foreign,
            'checks': checks,
        }
    return result


def validate_v4(layout):
    """확정 계약과 모든 테이블·컬럼 속성·제약을 비교해 구조 오류 목록을 반환한다.

    같은 버전 숫자를 가진 과거 혼합 v4도 다른 구조면 거부한다.
    버전 기록 전 생성 검증에도 사용하며 버전 행 자체는 snapshot이 검사한다.
    """
    # 저장된 v4 계약과 실측 구조를 비교해 차이를 오류 목록으로 모은다.
    expected = json.loads((DESIGN_DIR / 'schema_v4_contract.json').read_text(encoding='utf-8'))
    actual = _structure(layout)
    errors = []
    # 계약 또는 DB 한쪽에만 있는 테이블도 누락 또는 추가로 보고한다.
    for table in sorted(expected.keys() | actual.keys()):
        if table not in expected or table not in actual:
            errors.append(f'{table}: unexpected or missing table')
            continue
        # 바깥 반복에서 선택한 테이블의 각 속성만 비교한다.
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
    # 빈 DB인지와 생성 조건을 확인한 뒤 v4 SQL을 적용하고 검증한다.
    if not isinstance(engine, Engine) or engine.dialect.name != 'mysql':
        raise ValueError('MySQL 8.4 or later required')
    with engine.connect() as connection:
        server = str(connection.scalar(text('SELECT VERSION()')))
        if 'mariadb' in server.lower():
            raise ValueError('MySQL 8.4 or later required')
        # 버전의 앞 두 숫자만 비교한다. 예: 8.4.13은 (8, 4)가 된다.
        version_parts = server.split('-')[0].split('.')[:2]
        version_numbers = []
        for part in version_parts:
            version_numbers.append(int(part))
        if tuple(version_numbers) < (8, 4):
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
            sql_lines = []
            # 먼저 설명 줄을 제외한다. 아래 문장 실행 반복과 별도로 끝나는 반복이다.
            for line in source.splitlines():
                if not line.lstrip().startswith('--'):
                    sql_lines.append(line)
            script = '\n'.join(sql_lines)
            # SQL 파일을 세미콜론 기준으로 나누고 비어 있지 않은 문장만 순서대로 실행한다.
            # 이 반복은 스키마 생성 명령에서만 실행되며 일반 조회에서는 실행되지 않는다.
            for statement in script.split(';'):
                if statement.strip():
                    connection.execute(text(statement))
            if validate_v4(_read_layout(connection)):
                raise ValueError('Created structure differs from final v4; inspect partial DDL')
            connection.execute(text('INSERT INTO schema_migrations (version) VALUES (4)'))
            # 검증 후 버전 기록을 확정한다. MySQL DDL은 별도 자동 커밋될 수 있다.
            connection.commit()
        finally:
            connection.execute(text('SELECT RELEASE_LOCK(:name)'), {'name': lock})


def audit_counts(connection):
    """읽기 전용 정합성 위반 건수를 반환한다. 외부 벡터 검사는 포함하지 않는다."""
    # 검증 SQL마다 위반 건수 한 행을 읽어 사전 목록으로 반환한다.
    queries = (DESIGN_DIR / 'schema_v4_verify.sql').read_text(encoding='utf-8')
    counts = []
    for sql in queries.split(';'):
        if not sql.strip():
            continue
        # 검사 SQL 하나가 반환하는 위반 건수 행을 결과 목록에 추가한다.
        row = connection.execute(text(sql)).mappings().one()
        counts.append(dict(row))
    return counts


def main():
    """명시적 --create 또는 읽기 전용 --inspect/--check를 실행한다. 비밀값은 출력하지 않는다."""
    # 선택된 명령에 따라 생성 또는 읽기 전용 점검을 실행한다.
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
            for row in counts:
                if row['violations']:
                    return 1
            return 0
    except Exception as error:
        print(f'SCHEMA_CHECK_FAILED: {type(error).__name__}; inspect configuration and partial DDL')
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
