"""기존 v3 필수 컬럼을 보존하면서 P1 INSERT 값을 공급한다.

프로젝트 BIGINT 시간 정책은 UTC Unix 초다. 기존 밀리초 시스템과의
명시적 호환에만 DB_LEGACY_TIME_UNIT=milliseconds를 사용한다.
"""

import os
import time

from sqlalchemy import inspect, text

from .errors import DbError


def epoch_now():
    """UTC Unix 초를 기본으로 반환한다. 빈 값·알 수 없는 단위는 거부한다."""
    unit = os.getenv('DB_LEGACY_TIME_UNIT', 'seconds')
    if unit not in ('seconds', 'milliseconds'):
        raise DbError('INVALID_STATE')
    return time.time_ns() // (1_000_000_000 if unit == 'seconds' else 1_000_000)


def insert(connection, table, values, legacy_values):
    """허용된 두 테이블에 실제 존재하는 레거시 컬럼만 추가해 INSERT한다.

    임의 테이블·컬럼을 호출자 입력으로 받지 않는다. 알려지지 않은 기본값 없는
    필수 컬럼은 조용히 생략하지 않고 INVALID_STATE로 차단한다.
    values/legacy_values는 서버 내부 컬럼·값 dict이며 반환값은 없다.
    전달받은 연결의 TX에서 저장하고 직접 커밋하거나 연결을 닫지 않는다.
    """
    if table not in ('documents', 'index_jobs'):
        raise DbError('INVALID_INPUT')
    columns = {column['name']: column for column in inspect(connection).get_columns(table)}
    supplied = dict(values)
    for name, value in legacy_values.items():
        if name in columns:
            supplied[name] = value() if callable(value) else value
    for name, column in columns.items():
        if (name not in supplied and not column['nullable'] and column.get('default') is None
                and not column.get('computed') and not column.get('autoincrement')):
            raise DbError('INVALID_STATE')
    for name, value in supplied.items():
        limit = getattr(columns[name]['type'], 'length', None)
        if isinstance(value, str) and limit is not None and len(value) > limit:
            raise DbError('INVALID_INPUT')
    names = ','.join(f'`{name}`' for name in supplied)
    parameters = ','.join(f':{name}' for name in supplied)
    connection.execute(text(f'INSERT INTO `{table}` ({names}) VALUES ({parameters})'), supplied)


def finish_job(connection, job_id, *, complete=False, failed=False):
    """작업 ID와 완료/실패 표시로 기존 작업을 종료한다. 반환값은 없다.

    기존 컬럼이 있는 DB에만 상태·종료 시간을 저장해 활성 작업 UNIQUE를 해제한다.
    호출자의 같은 TX에 참여하며 직접 커밋하거나 연결을 닫지 않는다.
    """
    if not (complete or failed):
        return
    columns = {column['name'] for column in inspect(connection).get_columns('index_jobs')}
    if {'status', 'finished_at', 'updated_at'} <= columns:
        connection.execute(text('''
            UPDATE index_jobs SET status=:status,finished_at=:now,updated_at=:now WHERE id=:id
        '''), {'status': 'SUCCEEDED' if complete else 'FAILED', 'now': epoch_now(), 'id': job_id})


def reset_job_for_retry(connection, job_id):
    """P1 attempts 증가 후 기존 작업도 재시도 시작 상태로 맞춘다. 반환값은 없다.

    입력은 호출자 TX의 연결과 검증된 작업 ID다. 기존 종료·실행·임대 필드를 비우고
    attempt_no를 증가한 attempts와 맞춘다. 기존 활성 작업과 충돌하면 DB 오류를
    전달해 호출자의 버전/P1 상태 변경까지 함께 롤백하게 한다. 직접 커밋하지 않는다.
    """
    columns = {column['name'] for column in inspect(connection).get_columns('index_jobs')}
    if not {'status', 'attempt_no', 'finished_at', 'updated_at'} <= columns:
        return
    updates = ["status='QUEUED'", 'attempt_no=attempts', 'finished_at=NULL', 'updated_at=:now']
    for name in ('lease_token', 'lease_expires_at', 'heartbeat_at', 'started_at'):
        if name in columns:
            updates.append(f'`{name}`=NULL')
    connection.execute(text('UPDATE index_jobs SET ' + ','.join(updates) + ' WHERE id=:id'),
                       {'now': epoch_now(), 'id': job_id})
