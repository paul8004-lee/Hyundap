"""일반 함수형 저장 코드가 공유하는 연결·트랜잭션·입력 검증 도구."""

import re
from contextlib import contextmanager
from functools import lru_cache
from threading import RLock
from weakref import WeakKeyDictionary

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .connection import create_db_engine
from .errors import DbError


_schema_columns = WeakKeyDictionary()
_schema_lock = RLock()


def get_table_columns(connection, table):
    """Engine별 읽기 전용 테이블 구조를 캐시하고 컬럼 메타데이터 tuple을 반환한다.

    connection과 table은 내부 DB 연결·테이블 이름이다. 최초 요청은 호출자 연결로
    구조만 조회하며 업무 행·DB 쓰기·커밋은 없다. DDL 완료 후 clear_schema_cache가 필요하다.
    Engine은 약한 참조로 보관해 캐시가 회수를 막지 않는다. 반환 메타데이터는 수정하지 않는다.
    """
    with _schema_lock:
        tables = _schema_columns.setdefault(connection.engine, {})
        if table not in tables:
            tables[table] = tuple(inspect(connection).get_columns(table))
        return tables[table]


def clear_schema_cache(engine=None):
    """지정 Engine 또는 전체 구조 캐시를 비운다. 반환값과 DB 접근·커밋은 없다.

    쓰기를 중지한 DDL/이행 완료 후 또는 Engine 설정 변경 시 사용한다. 기본은 전체 삭제다.
    """
    with _schema_lock:
        if engine is None:
            _schema_columns.clear()
        else:
            _schema_columns.pop(engine, None)


@lru_cache(maxsize=1)
def get_engine():
    """프로세스에서 연결 풀 하나를 재사용한다. 환경 변경 후에는 캐시를 비워야 한다.

    생성된 Engine은 앱 종료 시 dispose()한다. 설정 오류는 안전한 DB 오류로 바꾼다.
    테스트에서는 이 함수를 격리 DB Engine으로 대체한다.
    """
    try:
        return create_db_engine()
    except ValueError:
        raise DbError('DB_UNAVAILABLE') from None


@contextmanager
def transaction(connection=None):
    """연결이 없으면 짧은 트랜잭션을 열고, 있으면 호출자의 트랜잭션에 참여한다.

    외부 연결은 여기서 커밋·종료하지 않는다. 정상 종료는 커밋, 예외는 롤백한다.
    중복 키는 CONFLICT, 나머지 DB 오류는 DB_UNAVAILABLE로 숨겨 전달한다.
    호출자는 이 블록 안에서 파일·LLM·Chroma 작업을 수행하지 않는다.
    """
    try:
        if connection is not None:
            yield connection
        else:
            with get_engine().begin() as database:
                yield database
    except IntegrityError as error:
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

    레거시 ID를 UUID로 강제 변환하지 않는다. 새 ID는 저장 함수가 UUID로 생성한다.
    """
    if not isinstance(value, str) or not value.strip() or len(value) > 36:
        raise DbError('INVALID_INPUT')
    return value


def require_hash(value):
    """소문자 SHA-256 hex 문자열을 확인하고 그대로 반환한다."""
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise DbError('INVALID_INPUT')
    return value
