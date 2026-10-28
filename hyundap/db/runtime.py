"""일반 함수형 저장 코드가 공유하는 연결·트랜잭션·입력 검증 도구."""

import re
from contextlib import contextmanager
from functools import lru_cache
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .connection import create_db_engine
from .errors import DbError


@lru_cache(maxsize=1)
def get_engine():
    """프로세스에서 연결 풀 하나를 재사용한다. 환경 변경 후에는 캐시를 비워야 한다.

    생성된 Engine은 앱 종료 시 dispose()한다. 설정 오류는 안전한 DB 오류로 바꾼다.
    테스트에서는 이 함수를 격리 DB Engine으로 대체한다.
    """
    # 최초 호출에서 엔진과 v4 구조를 확인하고 이후에는 캐시된 엔진을 쓴다.
    engine = None
    try:
        engine = create_db_engine()
        from .schema import snapshot, validate_v4
        # 첫 연결에서 실제 테이블 구조를 확인하고 계약과 다르면 사용하지 않는다.
        with engine.connect() as connection:
            if validate_v4(snapshot(connection)):
                raise DbError('INVALID_STATE')
        return engine
    except (ValueError, SQLAlchemyError, DbError):
        if engine is not None:
            engine.dispose()
        raise DbError('DB_UNAVAILABLE') from None


@contextmanager
def transaction(connection=None):
    """연결이 없으면 짧은 트랜잭션을 열고, 있으면 호출자의 트랜잭션에 참여한다.

    외부 연결은 여기서 커밋·종료하지 않는다. 정상 종료는 커밋, 예외는 롤백한다.
    중복 키는 CONFLICT, 나머지 DB 오류는 DB_UNAVAILABLE로 숨겨 전달한다.
    호출자는 이 블록 안에서 파일·LLM·Chroma 작업을 수행하지 않는다.
    """
    # 전달받은 연결은 그대로 사용하고 연결이 없을 때만 새 트랜잭션을 연다.
    try:
        if connection is not None:
            # 연결을 만든 쪽이 트랜잭션을 관리하므로 여기서는 커밋하거나 닫지 않는다.
            # yield는 호출자의 with 블록에 연결을 전달하고 블록이 끝나면 여기로 돌아온다.
            yield connection
        else:
            # 전달받은 연결이 없으면 엔진에서 새 트랜잭션을 열어 호출자에게 전달한다.
            # 직접 연 트랜잭션은 정상 종료 시 커밋하고 예외 시 롤백한다.
            with get_engine().begin() as database:
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
