"""동일 MySQL 서버의 공통 인증 DB와 회사 업무 DB를 선택하는 시험용 모듈.

접속 대상은 공통 DB의 등록 정보에서만 읽는다. 요청이 보낸 URL을 사용하지 않는다.
회사 정보는 ContextVar로 요청별 분리하며 환경변수/전역 Engine을 요청 중 바꾸지 않는다.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import re
import hashlib
import logging
from pathlib import Path
from threading import RLock
from filelock import FileLock, Timeout

from sqlalchemy import text

from .connection import create_db_engine
from .errors import DbError
from . import runtime


# 현재 요청의 회사만 보관한다. 동시 요청끼리 회사 값이 섞이지 않도록 일반 전역 변수와 구분한다.
_company = ContextVar('hyundap_company', default=None)
_engines = {}
_engine_lock = RLock()
_workflow_locks = {}
_publication_locks = {}
logger = logging.getLogger(__name__)


def quoted_database(name):
    """DB 이름은 서버 관리 식별자만 허용하고 SQL 식별자로 인용한다."""
    if not isinstance(name, str) or re.fullmatch(r'[a-zA-Z0-9_]{1,64}', name) is None:
        raise DbError('INVALID_INPUT')
    return '`' + name + '`'


def company_for_reference(*, document_id=None, version_id=None, job_id=None, connection=None):
    """내용 없이 저장한 전역 식별자 목록에서 대상 회사를 찾는다."""
    fields = {'document_id': document_id, 'version_id': version_id, 'job_id': job_id}
    clauses = []
    params = {}
    # 반복 한 번에 식별자 하나를 검사한다. 받은 식별자는 모두 같은 등록 행에 속해야 한다.
    for name, value in fields.items():
        if value is not None:
            params[name] = runtime.require_id(value)
            clauses.append(name + '=:' + name)
    if not clauses:
        raise DbError('INVALID_INPUT')
    if runtime.isolated_company_connection(connection):
        from .company_routines import lookup_company_route
        return lookup_company_route(connection, document_id, version_id, job_id)
    route_table = runtime.shared_table('document_routes')
    condition = ' AND '.join(clauses)
    query = text('SELECT DISTINCT company_id FROM ' + route_table + ' WHERE ' + condition)
    with runtime.transaction(connection, control=True) as database:
        rows = database.execute(query, params).scalars().all()
    if len(rows) != 1:
        raise DbError('INVALID_STATE')
    return rows[0]


def get_company_engine(company_id):
    """활성 등록만 연결한다. 최대 32개 회사 풀을 유지하고 초과 시 명시적으로 거부한다.

    사용 중인 Engine을 자동 폐기하지 않는다. 시험 규모를 넘으면 풀 수명 정책 확장이 필요하다.
    등록 상태는 캐시하지 않으므로 중지한 회사는 기존 풀을 통해서도 새 요청을 열 수 없다.
    """
    company_id = runtime.require_id(company_id)
    isolated_accounts = runtime.company_accounts_enabled()
    control = runtime.get_engine()
    with runtime.transaction(control=True) as database:
        row = database.execute(text('''
            SELECT r.database_name FROM company_databases r JOIN companies c ON c.id=r.company_id
            WHERE r.company_id=:id AND r.status='ready' AND c.is_active=1
        '''), {'id': company_id}).mappings().one_or_none()
    if row is None:
        raise DbError('INVALID_STATE')
    name = row['database_name']
    quoted_database(name)
    if name == control.url.database:
        raise DbError('INVALID_STATE')
    # 공통 DB 연결·회사·등록 DB가 모두 같을 때만 같은 풀을 재사용한다.
    # 회사 ID만 키로 쓰면 다른 공통 DB의 동일 ID와 연결이 섞일 수 있다.
    credential_settings = None
    if isolated_accounts:
        from .company_credentials import credential_path, credential_source
        credential_settings = (credential_source(), str(credential_path()))
    key = (control, company_id, name, isolated_accounts, credential_settings)
    with _engine_lock:
        # 회사 활성 상태는 위에서 매번 검사했다. 사용 가능한 기존 풀은 바로 반환한다.
        if key in _engines:
            return _engines[key]
        # 여기부터는 아직 풀을 만들지 않은 회사다. 생성까지 잠가 중복 생성을 막는다.
        if len(_engines) >= 32:
            logger.warning('company engine capacity reached: %s', len(_engines))
            raise DbError('DB_UNAVAILABLE')
        company_url = control.url.set(database=name)
        if isolated_accounts:
            from .company_credentials import credential_path, read_credential
            username, password = read_credential(company_id, path=credential_path())
            company_url = company_url.set(username=username, password=password)
        # 비밀번호를 포함한 URL은 연결 생성에만 사용하고 로그에는 출력하지 않는다.
        connection_url = company_url.render_as_string(hide_password=False)
        engine = create_db_engine(connection_url)
        try:
            from .split_schema import validate_company
            with engine.connect() as connection:
                if validate_company(connection, control.url.database, company_id):
                    raise DbError('INVALID_STATE')
        except Exception:
            engine.dispose()
            raise DbError('DB_UNAVAILABLE') from None
        # 구조 검사를 통과한 풀만 보관한다. 실패한 풀은 다음 호출에 재사용하지 않는다.
        _engines[key] = engine
        logger.info('company engine created; cached=%s', len(_engines))
        return engine


def routed_engine(*, company_id=None, document_id=None, version_id=None, job_id=None, connection=None):
    """회사 또는 업무 ID로 회사 Engine을 선택한다. 대상을 받지 않으면 None이다.

    ID의 실제 소유 회사와 현재 요청의 회사가 다르면 INVALID_STATE로 거부한다.
    등록부를 읽고 연결 풀을 재사용하지만 업무 데이터를 변경하지는 않는다.
    connection을 받으면 호출자의 미커밋 식별자 등록도 같은 연결로 확인한다.
    """
    # 1. 문서·버전·작업 ID 중 하나라도 받으면 등록부에서 실제 소유 회사를 찾는다.
    if document_id is not None or version_id is not None or job_id is not None:
        owner = company_for_reference(document_id=document_id, version_id=version_id, job_id=job_id, connection=connection)
        if company_id is not None and company_id != owner:
            raise DbError('INVALID_STATE')
        company_id = owner
    # 2. 현재 처리 중인 회사가 있으면 식별자의 소유 회사와 대조한다.
    selected = _company.get()
    if selected is not None and company_id is not None and selected != company_id:
        raise DbError('INVALID_STATE')
    # 3. 명시적으로 받은 회사가 우선이며, 없으면 현재 요청의 회사를 사용한다.
    target = company_id
    if not target:
        target = selected
    if target is None:
        return None
    return get_company_engine(target)


@contextmanager
def company_scope(company_id):
    """with 본문이 처리할 회사를 고정하고 종료 시 이전 회사 문맥으로 돌아간다.

    ContextVar는 일반 전역 변수와 달리 실행 문맥별 값을 가진다. 동시 요청이 서로의
    회사 값을 바꾸지 않게 하며, 중첩 호출에서도 다른 회사로 전환하는 것은 거부한다.
    """
    if not runtime.split_layout():
        yield
        return
    company_id = runtime.require_id(company_id)
    if _company.get() not in (None, company_id):
        raise DbError('INVALID_STATE')
    get_company_engine(company_id)
    # token은 회사 ID가 아니라 변경 전 문맥을 복원하는 표식이다.
    token = _company.set(company_id)
    try:
        # 호출자의 with 본문(업로드/게시 등)이 실행된다. 성공·실패 모두 아래 finally로 돌아온다.
        yield
    finally:
        # 처리 실패나 중첩 호출에서도 이전 회사 문맥으로 반드시 돌아간다.
        _company.reset(token)


@contextmanager
def processing_lock(fallback, *, publication=False):
    """분리 모드는 회사별 OS 파일 잠금, 기존 단일 DB 모드는 전역 Python 잠금을 쓴다.

    분리 모드는 같은 호스트·같은 원본 저장 루트를 쓰는 프로세스끼리 OS 파일 잠금을
    공유한다. 프로세스가 죽으면 OS가 잠금을 해제한다. 여러 서버의 분산 임대는 아니다.
    DB 트랜잭션을 외부 임베딩 호출 동안 유지하지 않는다. 같은 스레드의 중첩 게시·복구는
    캐시된 동일 FileLock의 재진입으로 처리한다. 잠금 파일을 작업 종료 때 직접 지우지 않는다.
    """
    if not runtime.split_layout():
        with fallback:
            yield
        return
    selected = _company.get()
    if selected is None:
        raise DbError('INVALID_STATE')
    # 업로드 전체 잠금과 게시 잠금은 목적이 다르므로 기존 두 목록을 유지한다.
    if publication:
        locks = _publication_locks
    else:
        locks = _workflow_locks
    from hyundap.common.config import get_settings
    control = runtime.get_engine().url
    root = Path(get_settings().upload_dir).resolve() / '.locks'
    # 계정·비밀번호는 파일 이름에 넣지 않는다. 같은 서버/DB/회사/작업 종류만 공유한다.
    identity = f'{control.host}:{control.port}:{control.database}:{selected}:{publication}'
    name = hashlib.sha256(identity.encode()).hexdigest()
    key = (str(root), name)
    with _engine_lock:
        # 잠금 사전에 새 객체를 넣는 짧은 구간만 보호한다. 회사 작업 전체의 공통 잠금이 아니다.
        if key not in locks:
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise DbError('DB_UNAVAILABLE') from None
            locks[key] = FileLock(str(root / (name + '.lock')), timeout=30)
        lock = locks[key]
    try:
        lock.acquire()
    except (Timeout, OSError):
        raise DbError('DB_UNAVAILABLE') from None
    try:
        yield
    finally:
        lock.release()


def invalidate_company_engine(company_id):
    """해당 회사의 캐시만 제거한다. 진행 중 연결·작업 잠금은 강제로 끊지 않는다.

    다른 프로세스의 캐시는 바꾸지 않는다. 비밀 교체는 요청을 중지한 뒤 모든 워커에
    실행하거나 재시작한다. 다음 요청이 최신 비밀을 읽어 새 풀을 만든다.
    """
    company_id = runtime.require_id(company_id)
    with _engine_lock:
        keys = [key for key in _engines if key[1] == company_id]
        for key in keys:
            engine = _engines.pop(key)
            engine.dispose()
        # 잠금 사전을 지우면 이미 실행 중인 작업과 새 잠금이 병존하므로 건드리지 않는다.
        logger.info('company engines invalidated; removed=%s cached=%s', len(keys), len(_engines))
        return len(keys)


def close_company_engines():
    """앱 종료/시험 정리 시 회사 연결 풀을 닫는다. 처리 중에는 호출하지 않는다."""
    with _engine_lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
        _workflow_locks.clear()
        _publication_locks.clear()


def authorize_company(actor, company_id=None, *, manager=False):
    """회사 DB를 열기 전에 공통 DB의 현재 세션과 역할·허용 회사를 확인한다."""
    if not runtime.split_layout():
        return
    from hyundap.auth.permissions import allowed_companies
    from hyundap.auth.errors import AccessError
    from .sessions_repo import current_actor
    with runtime.transaction(control=True) as database:
        current = current_actor(actor, connection=database)
        allowed = allowed_companies(current, connection=database)
        if manager and current['role'] not in ('platform_admin', 'company_admin'):
            raise AccessError('ACCESS_DENIED')
        if company_id is not None and company_id not in allowed:
            raise AccessError('ACCESS_DENIED')
