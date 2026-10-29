"""B24~B28: 팀 PR의 실제 함수를 UI 서비스 규격에 연결한다.

검색 #143, 챗봇 #134, DB 업로드 #139를 사용한다. 인증팀 구현 전에는
기존 체험 계정으로 화면을 연다. 임시 로그인은 DB 서버 세션을 발급하지
않으며, 실제 문서 접근 시 백엔드의 권한 검증을 우회하지 않는다.
"""
from __future__ import annotations

from importlib import import_module

from .backend_documents import auto_classify_enabled, get_document, upload_document
from .backend_companies import list_company_names
from .backend_feedback import to_feedback
from .services import BackendBindings, UIServiceError


def require_function(module_name: str, name: str):
    """기능을 사용할 때 모듈을 읽어 예시 모드와 앱 시작은 독립적으로 유지."""
    try:
        module = import_module(module_name)
    except ImportError:
        raise UIServiceError(
            "BACKEND_DEPENDENCY_MISSING",
            "백엔드 모듈 또는 설치 패키지가 준비되지 않았습니다. 실행 환경 점검 결과를 확인해 주세요.",
        ) from None
    function = getattr(module, name, None)
    if not callable(function):
        raise UIServiceError(
            "BACKEND_NOT_CONNECTED",
            "이 기능의 백엔드 구현이 현재 실행 코드에 없습니다. 해당 PR 반영 후 다시 실행해 주세요.",
        )
    return function


def search(**kwargs):
    """PR #143: user/query/company_ids/doc_types/limit를 그대로 전달."""
    return require_function("hyundap.search", "search")(**kwargs)


def ask(**kwargs):
    """PR #134: UI의 dict 필터를 지원하는 실제 질문 함수 호출."""
    return require_function("hyundap.chatbot.ask", "ask")(**kwargs)


def summarize_document(**kwargs):
    """선택 문서의 실제 요약 함수 호출. 결과 버전은 서비스가 재검사."""
    return require_function("hyundap.chatbot.summary", "summarize_document")(**kwargs)


def summarize_document_stream(**kwargs):
    """스트리밍 PR 반영 전에는 기존 완료형 요약으로 동작한다."""
    try:
        function = require_function("hyundap.chatbot.summary", "summarize_document_stream")
    except UIServiceError as error:
        if error.code != "BACKEND_NOT_CONNECTED":
            raise

        def fallback():
            yield {"type": "status", "message": "요약을 생성하고 있어요."}
            yield {"type": "done", "response": summarize_document(**kwargs)}

        return fallback()
    return function(**kwargs)


def validated_session_to_actor(session: dict) -> dict:
    """SPEC-AUTH-001의 검증 결과를 DB가 재검증할 서버 actor로 변환한다."""
    if not session.get("session_id") or not session.get("token"):
        raise UIServiceError(
            "AUTH_BACKEND_PENDING",
            "임시 로그인으로 화면을 열었습니다. 실제 문서 처리는 인증팀의 서버 세션 연결 후 사용할 수 있습니다.",
            "input_error",
        )
    required = {"user_id", "company_id", "role", "session_id"}
    if required - session.keys():
        raise UIServiceError("INVALID_SESSION", "서버 세션의 사용자·권한 정보를 확인해 주세요.")
    # 인증의 공개 token을 DB 내부 필드로 전달한다. auth_version은 DB가 다시 읽는다.
    return {**{name: session[name] for name in required}, "_session_token": session["token"]}


def download_original(**kwargs):
    """권한 검증을 포함한 원문 다운로드 공개 함수는 DB팀 제공 대기."""
    raise UIServiceError(
        "DOWNLOAD_NOT_CONNECTED",
        "원문 다운로드는 백엔드 다운로드 함수 연결 후 사용할 수 있습니다.",
    )


def create_bindings(authentication=None) -> BackendBindings:
    """실제 업무 함수와 인증 제공자를 등록한다. 기본 인증만 기존 예시 사용.

    authentication은 UI 규격의 login/logout/validate_session을 제공한다.
    실제 인증 제공자는 SPEC-AUTH-001의 token/session_id와 사용자·회사 정보를
    반환한다. token은 actor의 _session_token으로 전달하며 서버 상태에만 보관한다.
    """
    temporary_auth = authentication is None
    if temporary_auth:
        from .mock_services import MockServices
        authentication = MockServices()
    return BackendBindings(
        login=authentication.login,
        logout=authentication.logout,
        validate_session=authentication.validate_session,
        search=search,
        detail=get_document,
        ask=ask,
        summarize_document=summarize_document,
        summarize_document_stream=summarize_document_stream,
        upload=upload_document,
        auto_classify_enabled=auto_classify_enabled,
        download=download_original,
        user_factory=validated_session_to_actor,
        to_feedback=to_feedback,
        temporary_auth=temporary_auth,
        company_names=None if temporary_auth else list_company_names,
    )
