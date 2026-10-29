"""B17/B18/B20~B23: 팀 백엔드 오류를 화면 안내로 변환한다.

검색·챗봇 변환기를 사용하되 세션 만료는 먼저 보존한다. 선택적 백엔드
모듈은 오류를 처리할 때 가져오므로 미설치 모듈 때문에 UI import가 실패하지 않는다.
알 수 없는 예외의 원문·접속 정보·파일 경로는 사용자에게 반환하지 않는다.
"""
from __future__ import annotations

from importlib import import_module


_GENERIC_MESSAGE = "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."
_SESSION_MESSAGE = "로그인 시간이 만료되었습니다. 다시 로그인해 주세요."
_RECOVERY_MESSAGE = "문서 게시 상태 확인이 필요합니다. 관리자에게 복구를 요청해 주세요."
_DB_MESSAGES = {
    "DB_UNAVAILABLE": "데이터 저장소에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
    "DUPLICATE_FILE": "같은 회사에 동일한 내용의 파일이 이미 등록되어 있습니다.",
    "CONFLICT": "다른 처리 결과와 충돌했습니다. 현재 상태를 확인한 뒤 다시 시도해 주세요.",
    "INVALID_STATE": "현재 문서 상태에서는 처리할 수 없습니다. 상태를 확인해 주세요.",
    "INVALID_INPUT": "입력값이 올바르지 않습니다. 선택한 파일과 입력 내용을 확인해 주세요.",
    "INVALID_DOC_TYPE": "문서 유형이 올바르지 않습니다. 매뉴얼·규정·가이드·기타 중에서 선택해 주세요.",
    "FILE_UNAVAILABLE": "업로드한 파일을 읽을 수 없습니다. 다시 업로드해 주세요.",
    "INTEGRATION_UNAVAILABLE": "문서 처리에 필요한 백엔드 연결이 아직 준비되지 않았습니다.",
    "PROCESSING_FAILED": "문서를 처리하지 못했습니다. 파일을 확인한 뒤 다시 시도해 주세요.",
    "INCOMPLETE_INDEX": "문서 색인이 완성되지 않았습니다. 관리자에게 확인을 요청해 주세요.",
    "ACTIVATION_FAILED": "문서 검색 공개를 완료하지 못했습니다. 관리자에게 확인을 요청해 주세요.",
    "RECOVERY_REQUIRED": _RECOVERY_MESSAGE,
}
_UPLOAD_MESSAGES = {
    **_DB_MESSAGES,
    "UNSUPPORTED_FORMAT": "PDF, DOCX, PPTX, XLSX 파일만 업로드할 수 있습니다.",
    "FILE_TOO_LARGE": "파일은 100MiB 이하로 선택해 주세요.",
    "ENCRYPTED_FILE": "암호가 걸린 파일은 등록할 수 없습니다. 암호를 해제한 뒤 다시 올려 주세요.",
    "CORRUPTED_FILE": "파일이 손상되었거나 비어 있어 열 수 없습니다. 파일을 확인해 주세요.",
    "EMPTY_DOCUMENT": "문서에서 글자를 읽을 수 없습니다. 글자가 없는 문서이거나 스캔한 이미지 문서일 수 있습니다.",
    "LEGACY_FORMAT": "구형 형식은 지원하지 않습니다. .docx, .pptx, .xlsx로 저장한 뒤 다시 올려 주세요.",
    "NO_API_KEY": "문서 처리에 필요한 AI 설정을 확인해 주세요.",
    "CONFIG_MISSING": "문서 처리에 필요한 AI 설정을 확인해 주세요.",
    "AUTHENTICATION": "문서 처리에 필요한 AI 인증 설정을 확인해 주세요.",
    "RATE_LIMIT": "문서 처리 서비스의 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.",
    "CONNECTION": "문서 처리 서비스에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    "TIMEOUT": "문서 처리 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.",
    "STORE_UNAVAILABLE": "문서 색인 저장소에 연결하지 못했습니다. 관리자에게 확인을 요청해 주세요.",
}
_ALLOWED_STATUSES = {"input_error", "denied", "failed", "empty", "session_expired", "error"}


def safe_upload_message(error_code, recovery_required=False) -> str:
    """반환 결과의 오류 코드를 고정 안내로 바꾼다. 원래 error_message는 표시하지 않는다."""
    if recovery_required:
        return _RECOVERY_MESSAGE
    if isinstance(error_code, str):
        return _UPLOAD_MESSAGES.get(error_code, _DB_MESSAGES["PROCESSING_FAILED"])
    return _DB_MESSAGES["PROCESSING_FAILED"]


def _optional_module(name):
    try:
        return import_module(name)
    except ImportError:
        return None


def _is_error(error, module, class_name):
    error_class = getattr(module, class_name, None)
    return isinstance(error_class, type) and isinstance(error, error_class)


def _has_expired_session(error, known_classes) -> bool:
    """실제 팀 예외만 검사한다. 래핑된 만료 원인을 최대 8단계까지 확인한다."""
    seen = set()
    current = error
    for _ in range(8):
        if not isinstance(current, BaseException) or id(current) in seen:
            break
        seen.add(id(current))
        if isinstance(current, known_classes) and getattr(current, "code", None) == "SESSION_EXPIRED":
            return True
        current = current.__cause__
    return False


def _converted_feedback(module, error):
    """팀 변환기의 공개 필드만 사용하며 변환기 실패도 원문 없이 처리한다."""
    converter = getattr(module, "to_feedback", None)
    if not callable(converter):
        return None
    try:
        result = converter(error)
    except Exception:
        return None
    if not isinstance(result, dict) or not isinstance(result.get("message"), str) or not result["message"]:
        return None
    status = result.get("status")
    return {
        "status": status if status in _ALLOWED_STATUSES else "failed",
        "message": result["message"],
        "error_code": result.get("error_code"),
    }


def to_feedback(error) -> dict:
    """검색 → 챗봇 → 접근·DB·수집 오류 순으로 화면용 세 필드만 반환한다."""
    access = _optional_module("hyundap.auth.errors")
    search = _optional_module("hyundap.search.errors")
    chatbot = _optional_module("hyundap.chatbot.models")
    db = _optional_module("hyundap.db.errors")
    ingest = _optional_module("hyundap.ingest.models")
    known_classes = tuple(
        error_class
        for module, name in ((access, "AccessError"), (search, "SearchError"), (chatbot, "ChatbotError"))
        if isinstance(error_class := getattr(module, name, None), type)
    )
    if _has_expired_session(error, known_classes):
        return {"status": "session_expired", "message": _SESSION_MESSAGE, "error_code": "SESSION_EXPIRED"}

    # 챗봇 변환기는 다른 예외도 일반 오류로 바꾸므로 실제 클래스일 때만 사용한다.
    for module, name in ((search, "SearchError"), (chatbot, "ChatbotError")):
        if _is_error(error, module, name):
            feedback = _converted_feedback(module, error)
            if feedback is not None:
                return feedback

    code = getattr(error, "code", None)
    if _is_error(error, access, "AccessError"):
        if code == "INVALID_CREDENTIALS":
            # 없는 계정·잘못된 비밀번호 등 인증 실패의 구체적인 원인은 구분하지 않는다.
            return {"status": "input_error", "message": "아이디 또는 비밀번호가 올바르지 않습니다.", "error_code": code}
        if code == "STATE_CHANGED":
            return {"status": "failed", "message": "문서 상태가 변경되었습니다. 문서를 다시 선택해 주세요.", "error_code": code}
        if code == "ACCESS_DENIED":
            return {"status": "denied", "message": "이 문서 또는 회사에 접근할 권한이 없습니다.", "error_code": code}
    if _is_error(error, db, "DbError") and isinstance(code, str) and code in _DB_MESSAGES:
        status = "input_error" if code in {"INVALID_INPUT", "INVALID_DOC_TYPE", "FILE_UNAVAILABLE", "DUPLICATE_FILE"} else "failed"
        return {"status": status, "message": _DB_MESSAGES[code], "error_code": code}
    if _is_error(error, ingest, "IngestError") and isinstance(code, str) and code in _UPLOAD_MESSAGES:
        return {"status": "input_error", "message": safe_upload_message(code), "error_code": code}
    return {"status": "failed", "message": _GENERIC_MESSAGE, "error_code": "BACKEND_ERROR"}
