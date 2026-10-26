"""search/errors.py — 하위 모듈 오류를 검색 오류로 변환하고, 화면 안내 형식으로 바꾼다.

담당: 이준영
기준 명세: AI_문서검색_통합_기능명세서 (MOD-052, 제약 L63·L64)

함수:
    FUN-256 to_search_error — embedding·auth·db 오류를 SearchError로 바꾼다. 원문은 로그에만 남긴다.
    FUN-257 SearchError.__init__ — 코드·문구·HTTP 상태·재시도 여부를 저장한다.
    (명세 외) build_error — 코드에 맞는 문구·HTTP 상태를 채워 SearchError를 만든다.
    (명세 외) is_access_error, is_db_error — auth AccessError·db DbError인지 확인한다.
    (명세 외) to_feedback — SearchError를 UI 오류 안내 {status, message, error_code}로 바꾼다.

자료형:
    CLS-055 SearchError

상수: ERROR_MESSAGES(화면 문구), ERROR_STATUS(HTTP 상태), UI_STATUS(UI 상태 문자열). 오류 코드 12개.
"""
from __future__ import annotations

import logging

from hyundap.auth.errors import AccessError
from hyundap.db.errors import DbError
from hyundap.embedding.models import EmbeddingError, VectorStoreError

logger = logging.getLogger(__name__)

# 오류 코드 → 사용자 문구(안). 문구는 박경환과 확정 (참고_오류코드 시트 11개 + SESSION_EXPIRED)
# SESSION_EXPIRED는 UI가 로그인 화면으로 돌아가도록 추가했다(통합 명세서 미반영).
ERROR_MESSAGES: dict[str, str] = {
    "EMPTY_QUERY": "검색어를 입력해 주세요.",
    "QUERY_TOO_LONG": "검색어는 2000자 이하로 입력해 주세요.",
    "INVALID_LIMIT": "결과 개수 설정이 올바르지 않습니다.",
    "INVALID_MIN_SCORE": "검색 설정이 올바르지 않습니다.",
    "INVALID_DOC_TYPE": "선택한 문서 유형을 찾을 수 없습니다.",
    "NO_COMPANY_SCOPE": "검색할 수 있는 회사가 없습니다. 관리자에게 문의해 주세요.",
    "COMPANY_FORBIDDEN": "선택한 회사의 문서를 볼 권한이 없습니다.",
    "ACCESS_DENIED": "접근 권한이 없습니다.",
    "SESSION_EXPIRED": "로그인 시간이 만료되었습니다. 다시 로그인해 주세요.",
    "EMBEDDING_UNAVAILABLE": "검색 서비스가 잠시 응답하지 않습니다. 잠시 후 다시 시도해 주세요.",
    "SEARCH_UNAVAILABLE": "검색 서비스가 잠시 응답하지 않습니다. 잠시 후 다시 시도해 주세요.",
    "SEARCH_FAILED": "검색 중 오류가 발생했습니다.",
}

# 오류 코드 → HTTP 상태 (참고_오류코드 시트, SESSION_EXPIRED는 401)
ERROR_STATUS: dict[str, int] = {
    "EMPTY_QUERY": 400,
    "QUERY_TOO_LONG": 400,
    "INVALID_LIMIT": 400,
    "INVALID_MIN_SCORE": 400,
    "INVALID_DOC_TYPE": 400,
    "NO_COMPANY_SCOPE": 403,
    "COMPANY_FORBIDDEN": 403,
    "ACCESS_DENIED": 403,
    "SESSION_EXPIRED": 401,
    "EMBEDDING_UNAVAILABLE": 503,
    "SEARCH_UNAVAILABLE": 503,
    "SEARCH_FAILED": 500,
}

# 오류 코드 → UI 화면 상태 (ui/docs/spec.md 6.2, ui/controller.py).
# session_expired면 UI가 로그인 화면으로 돌아가고, 나머지는 message를 오류 안내로 표시한다.
UI_STATUS: dict[str, str] = {
    "EMPTY_QUERY": "input_error",
    "QUERY_TOO_LONG": "input_error",
    "INVALID_LIMIT": "input_error",
    "INVALID_MIN_SCORE": "input_error",
    "INVALID_DOC_TYPE": "input_error",
    "NO_COMPANY_SCOPE": "denied",
    "COMPANY_FORBIDDEN": "denied",
    "ACCESS_DENIED": "denied",
    "SESSION_EXPIRED": "session_expired",
    "EMBEDDING_UNAVAILABLE": "failed",
    "SEARCH_UNAVAILABLE": "failed",
    "SEARCH_FAILED": "failed",
}


class SearchError(Exception):
    """검색 단계 오류. 화면에 보일 코드·문구, HTTP 상태, 재시도 가능 여부를 담는다."""

    def __init__(
        self,
        code: str,
        message: str = "",
        status: int = 400,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, "")
        self.status = status
        self.retryable = retryable
        super().__init__(self.message)


def is_access_error(exc: Exception) -> bool:
    """auth 접근 오류(AccessError: SESSION_EXPIRED·ACCESS_DENIED·STATE_CHANGED)인지 확인한다."""
    return isinstance(exc, AccessError)


def is_db_error(exc: Exception) -> bool:
    """DB 오류(DbError: DB_UNAVAILABLE 등)인지 확인한다."""
    return isinstance(exc, DbError)


def build_error(code: str, retryable: bool = False) -> SearchError:
    """코드에 맞는 문구·HTTP 상태를 채워 SearchError를 만든다 (명세 외 보조 함수)."""
    return SearchError(code, status=ERROR_STATUS.get(code, 400), retryable=retryable)


def to_search_error(exc: Exception) -> SearchError:
    """하위 폴더 오류를 화면에 보낼 SearchError로 바꾼다. 원래 메시지는 로그에만 남긴다."""
    # 1) 이미 SearchError면 그대로
    if isinstance(exc, SearchError):
        return exc

    # 2) 임베딩 오류
    if isinstance(exc, EmbeddingError):
        code = getattr(exc, "code", "")
        if code in ("EMPTY_QUERY", "QUERY_TOO_LONG"):
            return build_error(code)
        logger.warning("임베딩 오류: %r", exc)
        return build_error("EMBEDDING_UNAVAILABLE", retryable=bool(getattr(exc, "retryable", False)))

    # 3) 벡터 저장소 오류. VectorStoreError에는 재시도 여부가 없어 저장소 장애로 보고 재시도 가능으로 둔다
    if isinstance(exc, VectorStoreError):
        if getattr(exc, "code", "") == "NO_COMPANY_SCOPE":
            return build_error("NO_COMPANY_SCOPE")
        logger.warning("벡터 저장소 오류: %r", exc)
        return build_error("SEARCH_UNAVAILABLE", retryable=True)

    # 4) auth 오류 (DB_contract §2: AccessError(code)). 세션 만료는 UI가 로그인 화면으로 돌아가도록 따로 둔다.
    #    STATE_CHANGED는 search가 부르는 함수에서는 나오지 않으므로 ACCESS_DENIED로 묶는다
    if is_access_error(exc):
        code = getattr(exc, "code", "")
        logger.warning("auth 오류: %s", code)
        return build_error("SESSION_EXPIRED" if code == "SESSION_EXPIRED" else "ACCESS_DENIED")

    # 5) DB 오류 (DB_contract §2: DbError(code)) → 검색 일시 장애. DB 장애일 때만 재시도 가능
    if is_db_error(exc):
        code = getattr(exc, "code", "")
        logger.warning("DB 오류: %s", code)
        return build_error("SEARCH_UNAVAILABLE", retryable=code == "DB_UNAVAILABLE")

    # 6) 나머지 (코드 버그 등). 정상 결과로 숨기지 않고 500 오류로 알리며, 원인은 로그에 남긴다
    logger.exception("예상하지 못한 검색 오류", exc_info=exc)
    return build_error("SEARCH_FAILED")


def to_feedback(error: Exception) -> dict | None:
    """SearchError를 UI 오류 안내 형식 {status, message, error_code}로 바꾼다 (ui/services.py의 to_feedback 규격).

    SearchError가 아니면 None을 돌려준다. UI는 message가 없으면 일반 오류 안내를 쓰므로,
    UI 연결부에서 다른 폴더의 to_feedback과 차례로 불러도 된다."""
    if not isinstance(error, SearchError):
        return None
    return {
        "status": UI_STATUS.get(error.code, "failed"),
        "message": error.message,
        "error_code": error.code,
    }
