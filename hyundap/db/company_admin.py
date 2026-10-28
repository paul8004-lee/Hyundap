"""분리 DB 시험의 운영자 전용 초기화·회사 생성 명령. 웹 요청에 직접 노출하지 않는다.

    DB_PROVISION_URL은 DDL/GRANT 권한을 가진 별도 운영자 연결이다. 평상시 업무 계정과 분리한다.
    기존 DB를 삭제하거나 v4 단일 DB를 자동 이행하지 않는다.
"""

import argparse
import os
import re
from uuid import uuid4

from sqlalchemy import text

from .connection import create_db_engine
from .errors import DbError
from . import runtime
from .split_schema import create_split_schema
from .tenancy import quoted_database


def provision_company(name, *, admin_engine, company_id=None):
    """운영자 권한으로 회사 DB를 생성·검증한 후에만 ready로 등록한다.

    동일 company_id의 ready 재요청은 기존 DB 이름을 반환한다. creating/failed는 자동 재실행하지
    않는다. DDL/권한 부여는 원자 롤백이 아니므로 실패 상태와 부분 DB를 보존한다.
    회사 ID는 생략 시 서버 UUID이며, DB 이름은 회사명 대신 서버 UUID로 생성한다.
    """
    if not runtime.split_layout():
        raise DbError('INVALID_STATE')
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise DbError('INVALID_INPUT')
    if not company_id:
        company_id = str(uuid4())
    company_id = runtime.require_id(company_id)
    if re.fullmatch(r'[a-zA-Z0-9_-]{1,36}', company_id) is None:
        raise DbError('INVALID_INPUT')
    control = runtime.get_engine()
    if (admin_engine.dialect.name != 'mysql' or
            (admin_engine.url.host, admin_engine.url.port, admin_engine.url.database) !=
            (control.url.host, control.url.port, control.url.database)):
        raise DbError('INVALID_INPUT')
    database_name = 'tenant_' + uuid4().hex
    # 1. 공통 DB에 비활성 회사와 생성 중 상태를 저장한다. 아직 앱에서 사용할 수 없다.
    with runtime.transaction(control=True) as database:
        registered = database.execute(text('SELECT database_name,status FROM company_databases WHERE company_id=:id FOR UPDATE'),
                                      {'id': company_id}).mappings().one_or_none()
        if registered:
            if registered['status'] == 'ready':
                return {'company_id': company_id, 'database_name': registered['database_name'], 'status': 'ready'}
            raise DbError('CONFLICT')
        database.execute(text('INSERT INTO companies (id,name,is_active) VALUES (:id,:name,0)'),
                         {'id': company_id, 'name': name.strip()})
        database.execute(text('''INSERT INTO company_databases (company_id,database_name,status)
                                 VALUES (:id,:database,'creating')'''),
                         {'id': company_id, 'database': database_name})
        account = database.scalar(text('SELECT CURRENT_USER()'))
    company_engine = None
    try:
        # 2. 관리자 연결로 새 DB를 만든다. DDL은 뒤에서 실패해도 자동 롤백되지 않는다.
        with admin_engine.connect() as database:
            database.execute(text('CREATE DATABASE ' + quoted_database(database_name) +
                                  ' CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin'))
        company_url = admin_engine.url.set(database=database_name)
        connection_url = company_url.render_as_string(hide_password=False)
        company_engine = create_db_engine(connection_url)
        # 3. 테이블과 제약을 생성하고 해당 회사의 DB인지 검증한다.
        create_split_schema(company_engine, control_name=control.url.database, company_id=company_id)
        # 4. 회사 업무 계정에는 새 DB의 DML만 준다. 관리자 DDL 권한을 앱에 전달하지 않는다.
        user, host = account.rsplit('@', 1)
        with admin_engine.connect() as database:
            database.execute(text('GRANT SELECT,INSERT,UPDATE,DELETE ON ' + quoted_database(database_name) +
                                  '.* TO :user@:host'), {'user': user, 'host': host})
        # 5. 생성·검증·권한 부여가 끝난 회사만 ready로 바꾸고 활성화한다.
        with runtime.transaction(control=True) as database:
            database.execute(text("UPDATE company_databases SET status='ready',error_code=NULL WHERE company_id=:id"),
                             {'id': company_id})
            database.execute(text('UPDATE companies SET is_active=1 WHERE id=:id'), {'id': company_id})
    except Exception:
        # 실패 회사는 비활성 상태로 남긴다. 부분 생성 DB를 삭제하거나 재실행하지 않는다.
        with runtime.transaction(control=True) as database:
            database.execute(text("UPDATE company_databases SET status='failed',error_code='PROVISION_FAILED' WHERE company_id=:id"),
                             {'id': company_id})
        raise DbError('PROVISION_FAILED') from None
    finally:
        # 성공·실패 모두 초기화에 사용한 풀을 정리한다. 앱의 업무 풀과는 별개다.
        if company_engine is not None:
            company_engine.dispose()
    return {'company_id': company_id, 'database_name': database_name, 'status': 'ready'}


def main():
    """새 빈 공통 DB 생성 또는 회사 등록을 명시적으로 실행한다. 비밀 URL은 출력하지 않는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--create-control', action='store_true')
    action.add_argument('--company-name')
    parser.add_argument('--company-id')
    args = parser.parse_args()
    engine = None
    try:
        if not runtime.split_layout():
            raise DbError('INVALID_STATE')
        # create_db_engine이 .env를 읽은 뒤 별도 운영자 URL을 검사한다.
        control = create_db_engine()
        control.dispose()
        url = os.environ.get('DB_PROVISION_URL')
        if not url:
            raise DbError('CONFIG_MISSING')
        engine = create_db_engine(url)
        if args.create_control:
            # dispose는 연결 풀을 닫는다. 이미 검증한 URL 설정은 그대로 읽을 수 있다.
            selected = control.url
            if (engine.url.host, engine.url.port, engine.url.database) != (selected.host, selected.port, selected.database):
                raise DbError('INVALID_INPUT')
            create_split_schema(engine)
            print('CONTROL_SCHEMA_CREATED')
        else:
            result = provision_company(args.company_name, admin_engine=engine, company_id=args.company_id)
            print('COMPANY_READY:', result['company_id'], result['database_name'])
        return 0
    except Exception as error:
        print('COMPANY_SETUP_FAILED:', getattr(error, 'code', type(error).__name__))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
