"""분리 DB 시험의 운영자 전용 초기화·회사 생성 명령. 웹 요청에 직접 노출하지 않는다.

    DB_PROVISION_URL은 DDL/GRANT 권한을 가진 별도 운영자 연결이다. 평상시 업무 계정과 분리한다.
    기존 DB를 삭제하거나 v4 단일 DB를 자동 이행하지 않는다.
"""

import argparse
from contextlib import contextmanager
import os
import re
import secrets
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .connection import create_db_engine
from .errors import DbError, mysql_error_number
from . import runtime
from .split_schema import create_split_schema
from .tenancy import quoted_database
from .company_credentials import credential_path as local_credential_path, credential_source, has_credential, save_credential
from .company_routines import procedure_statements


def _require_admin_target(admin_engine, control):
    """Require the same MySQL server, explicit port and control database."""
    if admin_engine.dialect.name != 'mysql':
        raise DbError('INVALID_INPUT')
    admin_target = (admin_engine.url.host, admin_engine.url.port, admin_engine.url.database)
    control_target = (control.url.host, control.url.port, control.url.database)
    if admin_target != control_target:
        raise DbError('INVALID_INPUT')


@contextmanager
def admin_transaction(admin_engine):
    """등록부 변경도 준비 관리자 연결에서 수행한다. 일반 서비스 권한을 늘리지 않는다."""
    try:
        with admin_engine.begin() as connection:
            with runtime.transaction(connection, control=True) as database:
                yield database
    except SQLAlchemyError:
        raise DbError('DB_UNAVAILABLE') from None


def create_company_db_user(company_id, *, admin_engine, credential_path=None, _allow_creating=False):
    """준비된 회사 DB에 전용 계정과 인증·라우팅 프로시저를 시험용으로 만든다.

    계정은 잠긴 상태로 만들고 권한과 로컬 설정 저장이 성공한 뒤에만 잠금을 푼다.
    DDL이 실패하면 부분 객체와 잠긴 계정을 보존하며 자동 삭제하지 않는다.
    기존 앱의 공통 계정 연결을 자동으로 바꾸지 않는다.
    """
    # 1. 입력과 대상 DB를 검사한다. 관리자 계정이어도 다른 서버/공통 DB를 지정할 수 없다.
    company_id = runtime.require_id(company_id)
    # 호스팅 변수는 실행 중 갱신할 수 없다. 비밀 저장 연동이 준비되기 전 계정을 만들지 않는다.
    if credential_source() != 'file':
        raise DbError('CONFIG_MISSING')
    if credential_path is None:
        credential_path = local_credential_path()
    if not runtime.split_layout():
        raise DbError('INVALID_STATE')
    control = runtime.get_engine()
    _require_admin_target(admin_engine, control)
    with admin_transaction(admin_engine) as database:
        row = database.execute(text('''
            SELECT r.database_name,r.status,c.is_active FROM company_databases r
            JOIN companies c ON c.id=r.company_id WHERE r.company_id=:id
        '''), {'id': company_id}).mappings().one_or_none()
    if row is None:
        raise DbError('INVALID_STATE')
    ready = row['status'] == 'ready' and bool(row['is_active'])
    creating = _allow_creating and row['status'] == 'creating' and not row['is_active']
    if not ready and not creating:
        raise DbError('INVALID_STATE')
    database_name = row['database_name']
    company_database = quoted_database(database_name)
    if database_name == control.url.database:
        raise DbError('INVALID_STATE')
    if has_credential(company_id, path=credential_path):
        raise DbError('CONFLICT')
    username = 'tenant_' + uuid4().hex[:20]
    password = secrets.token_urlsafe(32)
    statements = procedure_statements(control.url.database, company_id, username)
    company_engine = None
    account_engine = None
    unlocked = False
    try:
        company_url = admin_engine.url.set(database=database_name)
        company_engine = create_db_engine(company_url.render_as_string(hide_password=False))
        from .split_schema import validate_company
        with company_engine.connect() as database:
            if validate_company(database, control.url.database, company_id):
                raise DbError('INVALID_STATE')
        # 2. 잠긴 계정을 만든다. 이후 준비가 실패해도 미완료 계정으로 로그인할 수 없다.
        with admin_engine.connect() as database:
            database.execute(text("CREATE USER :user@'%' IDENTIFIED BY :password ACCOUNT LOCK"),
                             {'user': username, 'password': password})
            # 3. 첫 for는 문서 업무 표 4개에 읽기/쓰기를, 다음 for는 식별 표 2개에 읽기만 준다.
            # 두 반복은 중첩이 아니다. 각 반복에서 현재 표 하나의 권한만 부여한다.
            for name in ('documents', 'document_versions', 'document_chunks', 'index_jobs'):
                target_table = company_database + '.' + quoted_database(name)
                grant_sql = 'GRANT SELECT,INSERT,UPDATE,DELETE ON ' + target_table + " TO :user@'%'"
                database.execute(text(grant_sql), {'user': username})
            for name in ('tenant_identity', 'schema_migrations'):
                target_table = company_database + '.' + quoted_database(name)
                grant_sql = 'GRANT SELECT ON ' + target_table + " TO :user@'%'"
                database.execute(text(grant_sql), {'user': username})
        with company_engine.connect() as database:
            # 테이블 권한 부여가 끝난 뒤 프로시저 4개와 VIEW 1개를 해당 회사 DB에 생성한다.
            for statement in statements:
                database.execute(text(statement))
        with admin_engine.connect() as database:
            view_sql = 'GRANT SELECT ON ' + company_database + ".`company_information` TO :user@'%'"
            database.execute(text(view_sql), {'user': username})
            # 마지막 for는 프로시저 하나씩 실행 권한만 준다. 공통 인증 표의 SELECT 권한은 주지 않는다.
            for name in ('company_authenticate', 'company_register_route', 'company_lookup_route', 'company_lock_status'):
                target_procedure = company_database + '.' + quoted_database(name)
                grant_sql = 'GRANT EXECUTE ON PROCEDURE ' + target_procedure + " TO :user@'%'"
                database.execute(text(grant_sql), {'user': username})
        # 4. 재시작 후 사용할 비밀 설정을 먼저 저장한 다음 계정 잠금을 해제한다.
        save_credential(company_id, username, password, path=credential_path)
        with admin_engine.connect() as database:
            database.execute(text("ALTER USER :user@'%' ACCOUNT UNLOCK"), {'user': username})
        unlocked = True
        # 5. 새 계정으로 실제 연결해 자기 소속과 공통 users 직접 조회 차단을 확인한다.
        account_url = admin_engine.url.set(username=username, password=password, database=database_name)
        account_engine = create_db_engine(account_url.render_as_string(hide_password=False))
        with account_engine.connect() as database:
            if database.scalar(text('SELECT CURRENT_USER()')) != username + '@%':
                raise DbError('INVALID_STATE')
            if database.scalar(text('SELECT company_id FROM tenant_identity')) != company_id:
                raise DbError('INVALID_STATE')
            try:
                database.execute(text('SELECT id FROM ' + quoted_database(control.url.database) + '.users LIMIT 1'))
            except SQLAlchemyError as error:
                if mysql_error_number(error) not in (1044, 1142):
                    raise
            else:
                raise DbError('INVALID_STATE')
    except Exception:
        # 잠금 해제 뒤의 검사 실패는 다시 잠근다. DDL 전체를 rollback으로 취소할 수는 없다.
        if unlocked:
            try:
                with admin_engine.connect() as database:
                    database.execute(text("ALTER USER :user@'%' ACCOUNT LOCK"), {'user': username})
            except SQLAlchemyError:
                raise DbError('RECOVERY_REQUIRED') from None
        raise DbError('PROVISION_FAILED') from None
    finally:
        if account_engine is not None:
            account_engine.dispose()
        if company_engine is not None:
            company_engine.dispose()
    return {'company_id': company_id, 'database_name': database_name, 'username': username}


def provision_company(name, *, admin_engine, company_id=None):
    """운영자 권한으로 회사 DB를 생성·검증한 후에만 ready로 등록한다.

    동일 company_id의 ready 재요청은 기존 DB 이름을 반환한다. creating/failed는 자동 재실행하지
    않는다. DDL/권한 부여는 원자 롤백이 아니므로 실패 상태와 부분 DB를 보존한다.
    회사 ID는 생략 시 서버 UUID이며, DB 이름은 회사명 대신 서버 UUID로 생성한다.
    """
    if not runtime.split_layout():
        raise DbError('INVALID_STATE')
    isolated_accounts = runtime.company_accounts_enabled()
    if isolated_accounts and credential_source() != 'file':
        raise DbError('CONFIG_MISSING')
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise DbError('INVALID_INPUT')
    if not company_id:
        company_id = str(uuid4())
    company_id = runtime.require_id(company_id)
    if re.fullmatch(r'[a-zA-Z0-9_-]{1,36}', company_id) is None:
        raise DbError('INVALID_INPUT')
    control = runtime.get_engine()
    _require_admin_target(admin_engine, control)
    database_name = 'tenant_' + uuid4().hex
    # 1. 공통 DB에 비활성 회사와 생성 중 상태를 저장한다. 아직 앱에서 사용할 수 없다.
    with admin_transaction(admin_engine) as database:
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
        # 4. 선택한 계정 모드의 권한을 준비한다. 전용 계정 모드는 비밀 설정 저장까지 끝낸다.
        if isolated_accounts:
            create_company_db_user(company_id, admin_engine=admin_engine, _allow_creating=True)
        else:
            # 공통 계정 모드는 현재 업무 계정에 새 회사 DB의 읽기/쓰기 권한을 준다.
            # 등록부 쓰기는 관리자 연결로 옮겼으므로 실제 업무 계정을 별도로 조회한다.
            with control.connect() as database:
                account = database.scalar(text('SELECT CURRENT_USER()'))
            user, host = account.rsplit('@', 1)
            with admin_engine.connect() as database:
                database.execute(text('GRANT SELECT,INSERT,UPDATE,DELETE ON ' + quoted_database(database_name) +
                                      '.* TO :user@:host'), {'user': user, 'host': host})
        # 5. 생성·검증·권한 부여가 끝난 회사만 ready로 바꾸고 활성화한다.
        with admin_transaction(admin_engine) as database:
            database.execute(text("UPDATE company_databases SET status='ready',error_code=NULL WHERE company_id=:id"),
                             {'id': company_id})
            database.execute(text('UPDATE companies SET is_active=1 WHERE id=:id'), {'id': company_id})
    except Exception as error:
        # 실패 회사는 비활성 상태로 남긴다. 부분 생성 DB를 삭제하거나 재실행하지 않는다.
        with admin_transaction(admin_engine) as database:
            database.execute(text("UPDATE company_databases SET status='failed',error_code='PROVISION_FAILED' WHERE company_id=:id"),
                             {'id': company_id})
        if isinstance(error, DbError) and error.code == 'RECOVERY_REQUIRED':
            raise
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
    action.add_argument('--create-company-account', metavar='COMPANY_ID')
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
            _require_admin_target(engine, control)
            create_split_schema(engine)
            print('CONTROL_SCHEMA_CREATED')
        elif args.create_company_account:
            result = create_company_db_user(args.create_company_account, admin_engine=engine)
            print('COMPANY_ACCOUNT_READY:', result['company_id'], result['username'])
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
