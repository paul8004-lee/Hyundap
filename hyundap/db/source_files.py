"""원본 저장과 다운로드가 공유하는 경로 및 무결성 검사."""
import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4
from .errors import DbError
from .runtime import require_id

MAX_ORIGINAL_BYTES = 100 * 1024 * 1024


def file_digest(path):
    """실제 파일을 1MiB씩 읽어 SHA-256과 크기를 구한다. 비어 있거나 상한을 넘으면 거부한다."""
    # 파일을 나누어 읽으며 크기와 해시를 함께 계산한다.
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while True:
            # 한 번에 1MiB씩 읽으므로 파일 전체를 메모리에 올리지 않는다.
            block = stream.read(1024 * 1024)
            if not block:
                break  # 더 읽을 내용이 없으면 반복을 끝낸다.
            size += len(block)
            # 읽은 크기가 100MiB를 넘으면 입력 오류로 중단한다.
            if size > MAX_ORIGINAL_BYTES:
                raise DbError('INVALID_INPUT')
            digest.update(block)
    if size == 0:
        raise DbError('INVALID_INPUT')
    return digest.hexdigest(), size


def source_destination(root, company_id, document_id, version_id, suffix):
    """안전한 식별자로 순수 경로를 계산한다. 실제 링크·루트 검사는 TX 밖에서 수행한다."""
    # 경로에 쓸 식별자를 검사하고 허용되지 않은 확장자는 .bin으로 대체한다.
    # 회사·문서·버전 ID를 차례대로 확인한다. 세 값 중 하나라도 잘못되면 중단한다.
    # 예: 회사 ID가 '../company'이면 안전한 경로를 만들기 전에 거부한다.
    for identifier in (company_id, document_id, version_id):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,36}', require_id(identifier)):
            raise DbError('INVALID_INPUT')
    if not re.fullmatch(r'\.[a-zA-Z0-9]{1,12}', suffix):
        suffix = '.bin'
    # 서버 루트 아래 회사, 문서, 버전 순서로 경로를 구성한다.
    destination = root / company_id / document_id / version_id / ('source' + suffix)
    if not destination.is_relative_to(root):
        raise DbError('INVALID_INPUT')
    return destination


def copy_original(source, destination, expected_hash, expected_size):
    """기존 파일을 덮지 않고 복사하면서 다시 해시를 검사한다. 복사 중 원본 변경도 검출한다."""
    # 임시 파일에 복사한 내용을 검증한 다음 최종 경로에 연결한다.
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.source-' + uuid4().hex + '.part')
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open('rb') as incoming, temporary.open('xb') as outgoing:
            while True:
                block = incoming.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > MAX_ORIGINAL_BYTES:
                    raise DbError('INVALID_INPUT')
                digest.update(block)
                outgoing.write(block)
            # Python의 쓰기 버퍼를 비우고 운영체제에도 파일 내용 저장을 요청한다.
            outgoing.flush()
            os.fsync(outgoing.fileno())
        # 복사 중 원본이 바뀌었는지 최초 해시와 크기로 다시 확인한다.
        if digest.hexdigest() != expected_hash or size != expected_size:
            raise DbError('CONFLICT')
        # 같은 파일시스템의 hard link는 기존 목적지를 덮지 않고 완전한 파일만 공개한다.
        os.link(temporary, destination)
    finally:
        # 성공하면 목적지 이름이 같은 파일을 가리키므로 임시 이름만 지운다.
        temporary.unlink(missing_ok=True)


def resolve_stored_source(source, root):
    """DB의 원본 경로와 저장 루트가 절대 경로인지 검사하고 루트 내부 Path를 반환한다.

    파일이 아직 없을 수 있어 존재 여부는 검사하지 않는다. 링크가 루트 밖을 가리키면
    INVALID_STATE다. 파일 시스템 검사는 호출자의 DB 트랜잭션이 끝난 뒤 수행한다.
    """
    source, root = Path(source), Path(root)
    if not source.is_absolute() or not root.is_absolute():
        raise DbError('INVALID_STATE')
    try:
        resolved_root = root.resolve()
        resolved_source = source.resolve()
    except (OSError, RuntimeError):
        raise DbError('INVALID_STATE') from None
    if not resolved_source.is_relative_to(resolved_root):
        raise DbError('INVALID_STATE')
    return resolved_source
