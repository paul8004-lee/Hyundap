"""ingest/models.py — 업로드 처리 결과와 수집 오류 형식.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

자료형:
    CLS-040 IngestError
    CLS-041 IngestResult
"""

from dataclasses import dataclass, field
from typing import Optional

from hyundap.common.schemas import Chunk

# 오류 이름표
UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
ENCRYPTED_FILE = "ENCRYPTED_FILE"
CORRUPTED_FILE = "CORRUPTED_FILE"
DUPLICATE_FILE = "DUPLICATE_FILE"
EMPTY_DOCUMENT = "EMPTY_DOCUMENT"
INVALID_DOC_TYPE = "INVALID_DOC_TYPE"
FILE_UNAVAILABLE = "FILE_UNAVAILABLE"
LEGACY_FORMAT = "LEGACY_FORMAT"

# 오류 이름표별 기본 문구예요. 문구는 임시안이에요.
ERROR_MESSAGES = {
    UNSUPPORTED_FORMAT: "PDF, DOCX, PPTX 파일만 업로드할 수 있습니다.",
    FILE_TOO_LARGE: "파일은 100MiB 이하로 선택해 주세요.",
    ENCRYPTED_FILE: "암호가 걸린 파일은 등록할 수 없습니다. 암호를 해제한 뒤 다시 올려 주세요.",
    CORRUPTED_FILE: "파일이 손상되었거나 비어 있어 열 수 없습니다. 파일을 확인한 뒤 다시 올려 주세요.",
    DUPLICATE_FILE: "같은 회사에 동일한 내용의 파일이 이미 등록되어 있습니다.",
    EMPTY_DOCUMENT: "문서에서 글자를 읽을 수 없습니다. 글자가 없거나 인식할 수 없는 이미지 문서입니다.",
    INVALID_DOC_TYPE: "문서 유형이 올바르지 않습니다. 매뉴얼·규정·가이드·기타 중에서 선택해 주세요.",
    FILE_UNAVAILABLE: "업로드한 파일을 읽을 수 없습니다. 다시 업로드해 주세요.",
    LEGACY_FORMAT: "구형 형식은 지원하지 않습니다. .docx 또는 .pptx로 저장한 뒤 다시 올려 주세요.",
}

# 결과 상태 2가지
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


class IngestError(Exception):
    """파일 검사·읽기·청킹 중 입력 때문에 생기는 오류. code와 message를 담는다."""

    def __init__(self, code, message=""):
        if message == "":
            message = ERROR_MESSAGES.get(code, code)
        super().__init__(code, message)   # args에 둘 다 남겨 traceback·로그·pickle에서 사라지지 않게 한다
        self.code = code
        self.message = message

    def __str__(self):
        return f"{self.code}: {self.message}"


@dataclass
class IngestResult:
    """ingest_document의 결과. 성공이면 chunks·report, 실패면 error_code·error_message를 채운다.

    db의 as_mapping이 dataclass만 읽을 수 있어서 dataclass로 둔다. 목록·딕셔너리 기본값은
    객체마다 따로 만들어지도록 field(default_factory=...)를 쓴다."""
    status: str
    chunks: list[Chunk] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
