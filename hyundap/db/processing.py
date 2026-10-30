"""같은 호스트·원본 저장 루트를 쓰는 프로세스들의 업로드 흐름을 보호한다.

SQL 행 잠금과 조건부 갱신은 그대로 유지한다. 이 파일 잠금은 여러 호스트의
분산 잠금이 아니므로 별도 파일시스템을 쓰는 서버끼리의 보호로 사용하면 안 된다.
"""
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from filelock import FileLock, Timeout
from hyundap.common.config import get_settings
from .errors import DbError


@lru_cache(maxsize=1)
def workflow_file_lock(root):
    """같은 스레드의 게시·복구 중첩 호출도 재진입할 수 있도록 잠금 객체를 공유한다."""
    return FileLock(str(Path(root) / '.hyundap-workflow.lock'))


@contextmanager
def processing_lock():
    """최대 30초 대기한다. 프로세스 종료 시 OS가 잠금을 회수하며 파일은 삭제하지 않는다."""
    try:
        root = Path(get_settings().upload_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        lock = workflow_file_lock(str(root))
        with lock.acquire(timeout=30):
            yield
    except Timeout:
        raise DbError('CONFLICT') from None
