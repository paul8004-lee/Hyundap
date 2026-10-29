"""일반 함수형 저장 코드가 공유하는 연결·트랜잭션·입력 검증 도구."""

import re
import os
from contextlib import contextmanager
from functools import lru_cache
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .connection import create_db_engine
from .errors import DbError


def split_layout():
    """시험용 분리 모드는 명시적으로 켠다. 기존 v4 DB를 자동 변환하지 않는다."""
    from hyundap.common.config import get_settings
    get_settings()
    mode = os.environ.get('HYUNDAP_DB_LAYOUT', 'single')
    if mode not in ('single', 'split'):
        raise DbError('INVALID_STATE')
    return mode == 'split'


def company_accounts_enabled():
    """회사별 계정 모드 설정을 검증한다. 잘못된 설정은 공유 계정으로 대체하지 않는다."""
    value = os.environ.get('HYUNDAP_DB_COMPANY_ACCOUNTS', '0')
    if value not in ('0', '1'):
        raise DbError('INVALID_INPUT')
    if value == '1' and not split_layout():
        raise DbError('INVALID_STATE')
    return value == '1'


def isolated_company_connection(connection):
    """명시적으로 활성화한 회사 계정 연결인지 확인한다."""
    if connection is None or not company_accounts_enabled():
        return False
    return split_layout() and connection.engine.url.database != get_engine().url.database


def shared_table(name, *, connection=None):
    """서버가 정한 공통 테이블만 식별자로 만든다. 사용자 입력 SQL은 받지 않는다."""
    if name not in ('companies', 'users', 'sessions', 'document_routes'):
        raise DbError('INVALID_INPUT')
    if not split_layout():
        return name
    if name == 'companies' and isolated_company_connection(connection):
        return '`company_information`'
    from .tenancy import quoted_database
    return quoted_database(get_engine().url.database) + '.`' + name + '`'


@lru_cache(maxsize=1)
def get_engine():
    """프로세스에서 연결 풀 하나를 재사용한다. 환경 변경 후에는 캐시를 비워야 한다.

    생성된 Engine은 앱 종료 시 dispose()한다. 설정 오류는 안전한 DB 오류로 바꾼다.
    테스트에서는 이 함수를 격리 DB Engine으로 대체한다.
    @lru_cache(maxsize=1)은 인자가 없는 이 함수의 성공 반환값 하나를 기억한다.
    매 요청마다 연결 풀을 새로 만들지 않는다. get_engine.cache_clear()는 기억만 지우므로
    기존 풀의 dispose()와 처리 중 요청 종료는 설정을 바꾸는 쪽에서 별도로 관리해야 한다.
    """
    # 최초 호출에서 엔진과 v4 구조를 확인하고 이후에는 캐시된 엔진을 쓴다.
    engine = None
    try:
        engine = create_db_engine()
        from .schema import snapshot, validate_v4
        # 첫 연결에서 실제 테이블 구조를 확인하고 계약과 다르면 사용하지 않는다.
        with engine.connect() as connection:
            if split_layout():
                from .split_schema import validate_control
                errors = validate_control(connection)
            else:
                errors = validate_v4(snapshot(connection))
            if errors:
                raise DbError('INVALID_STATE')
        return engine
    except (ValueError, SQLAlchemyError, DbError):
        if engine is not None:
            engine.dispose()
        raise DbError('DB_UNAVAILABLE') from None


@contextmanager
def transaction(connection=None, *, company_id=None, document_id=None, version_id=None, job_id=None, control=False):
    """연결이 없으면 짧은 트랜잭션을 열고, 있으면 호출자의 트랜잭션에 참여한다.

    외부 연결은 여기서 커밋·종료하지 않는다. 정상 종료는 커밋, 예외는 롤백한다.
    중복 키는 CONFLICT, 나머지 DB 오류는 DB_UNAVAILABLE로 숨겨 전달한다.
    호출자는 이 블록 안에서 파일·LLM·Chroma 작업을 수행하지 않는다.

    @contextmanager는 함수를 with 문에서 쓸 수 있게 한다. yield database에서
    잠시 멈추고 호출자의 with 본문이 실행된 뒤 다시 돌아온다. 본문에서 예외가 나면
    그 예외도 yield 자리로 돌아오므로 아래 except에서 안전한 오류로 바꿀 수 있다.
    begin()으로 직접 연 경우만 종료 시 확정/취소한다. 전달받은 연결은 예외를 변환해
    다시 던질 뿐 직접 rollback하지 않으며, 연결을 연 호출자가 취소를 결정해야 한다.
    """
    # 전달받은 연결은 그대로 사용하고 연결이 없을 때만 새 트랜잭션을 연다.
    try:
        engine = None
        if split_layout() and not control:
            from .tenancy import routed_engine
            engine = routed_engine(company_id=company_id, document_id=document_id,
                                   version_id=version_id, job_id=job_id, connection=connection)
        if connection is not None:
            # 다른 회사/서버 연결을 전달해서 라우팅 검사를 우회하지 못하게 한다.
            if engine is not None and connection.engine.url != engine.url:
                raise DbError('INVALID_STATE')
            # 연결을 만든 쪽이 트랜잭션을 관리하므로 여기서는 커밋하거나 닫지 않는다.
            # yield는 호출자의 with 블록에 연결을 전달하고 블록이 끝나면 여기로 돌아온다.
            yield connection
        else:
            # 전달받은 연결이 없으면 엔진에서 새 트랜잭션을 열어 호출자에게 전달한다.
            # 직접 연 트랜잭션은 정상 종료 시 커밋하고 예외 시 롤백한다.
            # 회사 Engine이 선택되지 않았을 때 공통 Engine을 사용한다.
            selected_engine = engine
            if not selected_engine:
                selected_engine = get_engine()
            with selected_engine.begin() as database:
                yield database
    except IntegrityError as error:
        # 드라이버 원본 오류의 첫 번째 인자에서 MySQL 오류 번호를 확인한다.
        arguments = getattr(error.orig, 'args', ())
        error_number = None
        if arguments:
            error_number = arguments[0]
        # MySQL 오류 번호 1062는 같은 고유 키를 중복 저장하려 했다는 뜻이다.
        if error_number == 1062:
            raise DbError('CONFLICT') from None
        raise DbError('DB_UNAVAILABLE') from None
    except SQLAlchemyError:
        raise DbError('DB_UNAVAILABLE') from None


def require_id(value):
    """기존 문자열 ID를 보존하며 비어 있거나 36자를 넘는 ID를 거부한다.

    문자열 ID를 UUID로 강제 변환하지 않는다. 새 ID는 저장 함수가 UUID로 생성한다.
    """
    # ID 자체는 바꾸지 않고 문자열 여부와 공백, 길이만 검사한다.
    if not isinstance(value, str) or not value.strip() or len(value) > 36:
        raise DbError('INVALID_INPUT')
    # 검사에 strip()을 사용해도 반환값의 앞뒤 공백은 제거하지 않는다.
    return value


def require_hash(value):
    """소문자 SHA-256 hex 문자열을 확인하고 그대로 반환한다."""
    # SHA-256 해시는 소문자 16진수 64자리만 허용한다.
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise DbError('INVALID_INPUT')
    return value
