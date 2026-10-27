"""ingest/validator.py — 업로드 형식·크기·암호화·손상·파일 해시 검증.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-200 validate_upload — 파일 형식부터 중복까지 정해진 순서로 검사한다.
    FUN-201 check_file_format — 확장자와 파일 시그니처로 지원 형식을 검사한다.
    FUN-202 check_file_size — 파일 크기가 허용 범위 안인지 확인한다.
    FUN-203 check_not_encrypted — 암호화된 문서를 거부한다.
    FUN-204 check_not_corrupted — 파일을 정상적으로 읽을 수 있는지 검사한다.
    FUN-205 compute_file_hash — 파일 내용의 SHA-256을 계산한다.
    FUN-206 check_duplicate — DB 조회 함수로 회사별 동일 파일을 확인한다."""


import hashlib
import os
import zipfile

from pypdf import PdfReader

from .models import (
    CORRUPTED_FILE,
    DUPLICATE_FILE,
    ENCRYPTED_FILE,
    FILE_TOO_LARGE,
    UNSUPPORTED_FORMAT,
    IngestError,
)

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".pptx")
MAX_FILE_BYTES = 104857600
OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def check_file_format(file_path):
    """확장자와 파일 앞부분으로 지원 형식인지 확인하고 확장자를 돌려준다."""
    extension = os.path.splitext(file_path)[1].lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise IngestError(UNSUPPORTED_FORMAT)

    with open(file_path, "rb") as f:
        head = f.read(8)
    if head == b"":
        return extension

    if extension == ".pdf":
        ok = head.startswith(b"%PDF-")
    else:
        ok = head.startswith(b"PK") or head == OLE2_SIGNATURE
    if not ok:
        raise IngestError(UNSUPPORTED_FORMAT)
    return extension


def check_file_size(file_path, max_bytes=MAX_FILE_BYTES):
    """파일 크기가 1바이트 이상 100MiB 이하인지 확인하고 크기를 돌려준다."""
    size = os.path.getsize(file_path)
    if size == 0:
        raise IngestError(CORRUPTED_FILE)
    if size > max_bytes:
        raise IngestError(FILE_TOO_LARGE)
    return size


def open_pdf(file_path):
    """PDF를 열어 reader를 돌려준다. 열 수 없으면 None을 돌려준다."""
    try:
        return PdfReader(file_path)
    except Exception:
        return None


def check_not_encrypted(file_path, extension, reader=None):
    """암호가 걸린 문서를 걸러낸다. PDF는 열어 둔 reader를 넘기면 다시 열지 않는다."""
    if extension == ".pdf":
        if reader is None:
            reader = open_pdf(file_path)
        if reader is None:
            return
        if reader.is_encrypted:
            raise IngestError(ENCRYPTED_FILE)
        return
    with open(file_path, "rb") as f:
        head = f.read(8)
    if head == OLE2_SIGNATURE:
        raise IngestError(ENCRYPTED_FILE)


def check_not_corrupted(file_path, extension, reader=None):
    """깨진 파일을 걸러낸다. PDF는 열어 둔 reader를 넘기면 다시 열지 않는다."""
    if extension == ".pdf":
        if reader is None:
            reader = open_pdf(file_path)
        if reader is None:
            raise IngestError(CORRUPTED_FILE)
        try:
            page_count = len(reader.pages)
        except Exception:
            raise IngestError(CORRUPTED_FILE)
        if page_count < 1:
            raise IngestError(CORRUPTED_FILE)
        return

    if extension == ".docx":
        required = "word/document.xml"
    else:
        required = "ppt/presentation.xml"
    try:
        with zipfile.ZipFile(file_path) as z:
            bad = z.testzip()
            names = z.namelist()
    except zipfile.BadZipFile:
        raise IngestError(CORRUPTED_FILE)
    if bad is not None or required not in names:
        raise IngestError(CORRUPTED_FILE)


def compute_file_hash(file_path):
    """파일 전체의 SHA-256 해시를 1MiB씩 읽어 계산한다."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            sha.update(block)
    return sha.hexdigest()


def check_duplicate(file_hash, company_id, find_by_hash=None):
    """같은 회사에 같은 해시의 문서가 이미 있으면 중복 오류를 낸다."""
    if find_by_hash is None:
        return
    if find_by_hash(company_id, file_hash):
        raise IngestError(DUPLICATE_FILE)


def validate_upload(file_path, company_id, find_by_hash=None):
    """업로드 파일 검사를 정해진 순서(형식→용량→암호화→손상→해시→중복)로 실행한다.

    해시·중복 검사는 find_by_hash를 넘겼을 때만 하고, 넘기지 않으면 건너뛴다(db가 전담)."""
    extension = check_file_format(file_path)
    size_bytes = check_file_size(file_path)
    reader = None
    if extension == ".pdf":
        reader = open_pdf(file_path)
    check_not_encrypted(file_path, extension, reader)
    check_not_corrupted(file_path, extension, reader)
    # 해시·중복 검사는 find_by_hash를 받았을 때만 한다. 없으면 file_hash는 None이다.
    file_hash = None
    if find_by_hash is not None:
        file_hash = compute_file_hash(file_path)
        check_duplicate(file_hash, company_id, find_by_hash)
    return {
        "file_name": os.path.basename(file_path),
        "extension": extension,
        "size_bytes": size_bytes,
        "file_hash": file_hash,
    }