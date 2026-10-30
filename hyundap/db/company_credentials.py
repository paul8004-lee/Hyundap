"""시험용 회사 DB 계정을 Git 제외 로컬 설정에 보관한다.

이 파일은 평문 비밀번호를 포함한다. 운영 비밀 저장소를 대신하지 않으며
같은 호스트의 프로세스는 별도 파일 잠금으로 읽기·저장을 조정한다.
"""

import json
from contextlib import contextmanager
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
from filelock import FileLock, Timeout

from .errors import DbError


DEFAULT_PATH = Path(__file__).resolve().parents[2] / '.env.company-databases.json'
_lock = RLock()
LOCK_TIMEOUT = 10


@contextmanager
def credential_file_lock(path):
    """교체되지 않는 별도 잠금 파일로 전체 읽기·수정·저장 구간을 보호한다.

    데이터 파일 자체를 잠그면 os.replace 이후 다른 파일을 잠글 수 있다.
    잠금 파일은 삭제하지 않는다. 프로세스 종료 시 OS가 잠금을 해제한다.
    """
    path = Path(path).resolve()
    lock = FileLock(str(path) + '.lock', timeout=LOCK_TIMEOUT)
    try:
        lock.acquire()
    except (OSError, Timeout):
        raise DbError('DB_UNAVAILABLE') from None
    try:
        yield
    finally:
        lock.release()


def credential_source():
    """시험 파일과 호스팅 환경변수를 명시적으로 구분한다."""
    source = os.environ.get('HYUNDAP_COMPANY_CREDENTIALS_SOURCE', 'file')
    if source not in ('file', 'environment'):
        raise DbError('CONFIG_MISSING')
    return source


def credential_path():
    """상대 경로는 저장소 루트 기준으로 읽어 PC·작업 폴더에 의존하지 않는다."""
    path = Path(os.environ.get('HYUNDAP_COMPANY_CREDENTIALS_FILE', str(DEFAULT_PATH)))
    return path if path.is_absolute() else DEFAULT_PATH.parent / path


def _validate(company_id, username, password):
    """설정에서 읽거나 저장할 회사 ID와 계정·비밀번호를 검사한다."""
    if not isinstance(company_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,36}', company_id):
        raise DbError('INVALID_INPUT')
    if not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9_]{1,32}', username):
        raise DbError('INVALID_INPUT')
    if not isinstance(password, str) or not 16 <= len(password) <= 256:
        raise DbError('INVALID_INPUT')


def _load(path):
    """설정 파일을 읽는다. 손상된 파일은 빈 설정으로 덮어쓰지 않는다."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (OSError, ValueError, UnicodeError):
        raise DbError('CONFIG_MISSING') from None


def _load_config(path):
    """호스팅 비밀이 없거나 손상되면 로컬 파일로 대체하지 않는다."""
    if credential_source() == 'file':
        # Windows에서 읽기 핸들과 파일 교체도 겹치지 않도록 같은 잠금을 쓴다.
        with credential_file_lock(path):
            return _load(path)
    try:
        data = json.loads(os.environ['HYUNDAP_COMPANY_DATABASE_CREDENTIALS'])
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (KeyError, ValueError):
        raise DbError('CONFIG_MISSING') from None


def read_credential(company_id, *, path=DEFAULT_PATH):
    """설정에서 회사 항목을 찾아 (MySQL 계정명, 비밀번호) 튜플을 반환한다.

    파일/환경변수 선택은 _load_config가 담당한다. 여기서는 회사 항목의
    존재·자료형·값을 검사한다. 누락된 설정을 다른 회사 계정으로 대체하지 않는다.
    """
    with _lock:
        # 1. 설정 전체를 dict로 읽고, 2. 요청한 회사의 dict 하나를 꺼낸다.
        data = _load_config(Path(path))
        entry = data.get(company_id)
        if not isinstance(entry, dict):
            raise DbError('CONFIG_MISSING')
        # 3. 각 필드가 없으면 None이 되며, 아래 검증에서 거부한다.
        username = entry.get('username')
        password = entry.get('password')
        _validate(company_id, username, password)
        # 비밀번호는 연결 생성에만 사용한다. 반환값을 로그나 화면에 출력하지 않는다.
        return username, password


def has_credential(company_id, *, path=DEFAULT_PATH):
    """새 계정 생성 전에 기존 회사 설정이 있는지 확인한다."""
    if not isinstance(company_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,36}', company_id):
        raise DbError('INVALID_INPUT')
    with _lock:
        return company_id in _load_config(Path(path))


def save_credential(company_id, username, password, *, path=DEFAULT_PATH):
    """새 회사 계정을 파일에 저장한다. 같은 값의 재요청은 아무것도 쓰지 않는다.

    기존 값 변경은 CONFLICT다. 새 내용을 임시 파일에 완성한 다음 경로를 교체하여
    쓰기 도중 실패해도 기존 파일을 보존한다. 반환값은 없으며 환경변수에는 쓰지 않는다.
    """
    _validate(company_id, username, password)
    if credential_source() != 'file':
        raise DbError('CONFIG_MISSING')
    path = Path(path).resolve()
    with _lock, credential_file_lock(path):
        # 잠금을 얻은 뒤 최신 내용을 읽어야 다른 프로세스의 신규 회사를 보존한다.
        data = _load(path)
        entry = {'username': username, 'password': password}
        if company_id in data:
            if data[company_id] != entry:
                raise DbError('CONFLICT')
            return
        # 여기부터는 아직 등록되지 않은 회사만 처리한다. 다른 회사의 항목은 그대로 둔다.
        data[company_id] = entry
        temp_path = None
        try:
            # 목적지와 같은 폴더에 만들어 같은 파일시스템 안에서 교체할 수 있게 한다.
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                             prefix='.env.company-databases-', delete=False) as file:
                temp_path = Path(file.name)
                json.dump(data, file, ensure_ascii=False)
                # Python 버퍼를 비운 뒤 운영체제에도 저장을 요청한다.
                file.flush()
                os.fsync(file.fileno())
            # 파일을 닫은 뒤 완성본으로 한 번에 교체한다. Windows에서도 열린 파일을 옮기지 않는다.
            os.replace(temp_path, path)
        except OSError:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise DbError('CONFIG_MISSING') from None
