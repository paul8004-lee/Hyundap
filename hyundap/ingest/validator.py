"""ingest/validator.py — 업로드 형식·크기·암호화·손상·파일 해시 검증.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-200 validate_upload — 파일 형식부터 중복까지 정해진 순서로 검사한다.
    FUN-201 check_file_format — 확장자와 파일 시그니처로 지원 형식을 검사한다.
    FUN-202 check_file_size — 파일 크기가 허용 범위 안인지 확인한다.
    FUN-203 check_not_encrypted — 암호화된 문서를 거부한다. (OLE2는 암호/구형/기타로 구분)
    FUN-204 check_not_corrupted — 파일을 정상적으로 읽을 수 있는지 검사한다.
    FUN-205 compute_file_hash — 파일 내용의 SHA-256을 계산한다.
    FUN-206 check_duplicate — DB 조회 함수로 회사별 동일 파일을 확인한다."""


import hashlib
import os
import re
import zipfile
import zlib

from pypdf import PdfReader

from .models import (
    CORRUPTED_FILE,
    DUPLICATE_FILE,
    ENCRYPTED_FILE,
    FILE_TOO_LARGE,
    LEGACY_FORMAT,
    UNSUPPORTED_FORMAT,
    IngestError,
)

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".pptx", ".xlsx")
MAX_FILE_BYTES = 104857600
OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# OLE2 컨테이너 안의 스트림 이름은 UTF-16LE로 저장된다. 암호 Office 문서는 EncryptedPackage·EncryptionInfo를,
# 구형 .doc·.ppt는 각각 WordDocument·PowerPoint Document를 가진다 (#145).
OLE2_ENCRYPTED_NAMES = ("EncryptedPackage", "EncryptionInfo")
OLE2_LEGACY_NAMES = ("WordDocument", "PowerPoint Document")
# 구형 엑셀(.xls)의 본문 스트림 이름. 확장자가 .xlsx일 때만 구형으로 본다(.docx·.pptx에 든 Workbook은 기존처럼 그 밖의 형식이다).
OLE2_LEGACY_XLS_NAME = "Workbook"

# 확장자별 필수 파일과, 다른 형식임을 알려 주는 파일 (확장자만 바꾼 파일을 UNSUPPORTED_FORMAT으로 구분)
ZIP_REQUIRED = {".docx": "word/document.xml", ".pptx": "ppt/presentation.xml", ".xlsx": "xl/workbook.xml"}
# xl/workbook.bin은 .xlsb, content.xml은 OpenDocument(.ods/.odt) — 손상이 아니라 지원하지 않는 형식이다
ZIP_OTHER_FORMAT_MARKERS = ("word/document.xml", "ppt/presentation.xml", "xl/workbook.xml", "xl/workbook.bin", "content.xml")
ZIP_MACRO_MARKER = "vbaProject.bin"
# zip을 읽다가 파일이 깨졌을 때 라이브러리가 내는 예외 (BadZipFile만 잡으면 zlib.error 등이 그대로 올라온다)
ZIP_READ_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError)


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


def find_ole2_stream_names(file_path, names, block_size=1024 * 1024):
    """OLE2 파일에 주어진 이름의 스트림(UTF-16LE)이 들어 있는지 찾아, 찾은 이름의 집합을 돌려준다.

    OLE2를 해석하지 않고 바이트를 훑으므로 의존성이 없다. 블록 경계에 걸린 이름도 찾도록 겹쳐 읽는다."""
    targets = {name: name.encode("utf-16-le") for name in names}
    overlap = max(len(raw) for raw in targets.values()) - 1
    found = set()
    tail = b""
    with open(file_path, "rb") as f:
        while True:
            block = f.read(block_size)
            if not block:
                break
            data = tail + block
            for name, raw in targets.items():
                if raw in data:
                    found.add(name)
            if len(found) == len(targets):
                break
            tail = data[-overlap:]
    return found


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
        legacy_names = OLE2_LEGACY_NAMES + ((OLE2_LEGACY_XLS_NAME,) if extension == ".xlsx" else ())
        found = find_ole2_stream_names(file_path, OLE2_ENCRYPTED_NAMES + legacy_names)
        if any(name in found for name in OLE2_ENCRYPTED_NAMES):
            raise IngestError(ENCRYPTED_FILE)
        if any(name in found for name in legacy_names):
            raise IngestError(LEGACY_FORMAT)
        raise IngestError(UNSUPPORTED_FORMAT)   # 암호도 구형 Word·PowerPoint도 아닌 OLE2 (xls 등)


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

    required = ZIP_REQUIRED[extension]
    try:
        with zipfile.ZipFile(file_path) as z:
            bad = z.testzip()
            names = z.namelist()
    except ZIP_READ_ERRORS:
        raise IngestError(CORRUPTED_FILE)
    except (FileNotFoundError, PermissionError):
        raise   # 파일을 못 여는 문제는 손상이 아니다(pipeline이 FILE_UNAVAILABLE로 처리한다)
    except OSError:   # 중앙 디렉터리 값이 깨져 seek가 실패하는 등 (Errno 22). testzip이 압축 파일을 끝까지 읽을 때 난다
        raise IngestError(CORRUPTED_FILE)
    if bad is not None:
        raise IngestError(CORRUPTED_FILE)
    if required not in names:
        # 다른 Office 형식(xlsx, pptx↔docx)이면 손상이 아니라 형식 오류다
        if any(marker in names for marker in ZIP_OTHER_FORMAT_MARKERS):
            raise IngestError(UNSUPPORTED_FORMAT)
        raise IngestError(CORRUPTED_FILE)
    if any(name.endswith(ZIP_MACRO_MARKER) for name in names):
        raise IngestError(UNSUPPORTED_FORMAT)   # 매크로 포함 문서(.docm/.pptm)는 범위 밖


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


def validate_upload(file_path, company_id, find_by_hash=None, file_hash=None):
    """업로드 파일 검사를 정해진 순서(형식→용량→암호화→손상→해시→중복)로 실행한다.

    중복 조회가 있을 때 검증된 해시를 재사용하고, 없으면 파일에서 계산한다."""
    extension = check_file_format(file_path)
    size_bytes = check_file_size(file_path)
    reader = None
    if extension == ".pdf":
        reader = open_pdf(file_path)
    check_not_encrypted(file_path, extension, reader)
    check_not_corrupted(file_path, extension, reader)
    if file_hash is not None and (not isinstance(file_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", file_hash)):
        raise ValueError("file_hash must be a lowercase SHA-256 digest")
    if find_by_hash is not None:
        if file_hash is None:
            file_hash = compute_file_hash(file_path)
        check_duplicate(file_hash, company_id, find_by_hash)
    return {
        "file_name": os.path.basename(file_path),
        "extension": extension,
        "size_bytes": size_bytes,
        "file_hash": file_hash,
    }
