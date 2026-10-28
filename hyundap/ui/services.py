"""B24~B28: 담당 Python 함수와 Gradio 화면 사이의 연결 규격.

이 파일은 API 서버/백엔드 구현이 아니다. 실제 담당 함수가 준비되면
BackendBindings에 명시적으로 지정한다. 입력 이름이 다르면 작은 래퍼로 맞춘다.
미연결 실서비스가 화면 예시 모드로 자동 전환되는 동작은 없다.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from hyundap.common.schemas import DOC_TYPES as COMMON_DOC_TYPES


# 회사 목록은 화면 개발용이다. 실제 목록은 DB companies와 auth의 권한 조회에 연결한다.
COMPANIES = [
    ("현대경제연구원", "hri"),
    ("그린테크에너지", "gte"),
    ("블루파트너스자산운용", "bpa"),
]
# common은 (코드, 표시명), Gradio 선택지는 (표시명, 코드)를 사용한다.
DOC_TYPES = [(label, code) for code, label in COMMON_DOC_TYPES]
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx"}


def _company_names(user: dict, *, demo: bool = False) -> dict[str, str]:
    """허용 목록 안의 검증된 표시명만 모은다. 없는 이름은 추측하지 않는다."""
    allowed = list(dict.fromkeys(user.get("allowed_company_ids") or []))
    names = dict((code, name) for name, code in COMPANIES) if demo else {}
    for company in user.get("companies") or []:
        if isinstance(company, dict) and company.get("company_id") in allowed:
            name = company.get("company_name")
            if isinstance(name, str) and name.strip():
                names[company["company_id"]] = name.strip()
    own_company = user.get("company_id")
    own_name = user.get("company_name")
    if own_company in allowed and isinstance(own_name, str) and own_name.strip():
        names[own_company] = own_name.strip()
    return {code: names[code] for code in allowed if code in names}


def company_choices(user: dict, *, demo: bool = False) -> list[tuple[str, str]]:
    """회사명과 내부 ID를 분리한다. 누락된 이름 대신 ID·UUID를 노출하지 않는다."""
    allowed = list(dict.fromkeys(user.get("allowed_company_ids") or []))
    names = _company_names(user, demo=demo)
    return [(names.get(code, f"회사명 확인 필요 ({index})"), code)
            for index, code in enumerate(allowed, 1)]


class UIServiceError(Exception):
    """B17~B23: 화면에 안전하게 보여 줄 오류 코드·메시지·상태."""

    def __init__(self, code: str, message: str, status: str = "error"):
        super().__init__(message)
        self.code = code
        self.error_code = code
        self.message = message
        self.status = status


class UIServices(Protocol):
    """화면이 의존하는 규격. 실제 제공 함수 이름은 BackendBindings에서 연결."""

    demo: bool

    async def login(self, username: str, password: str) -> dict: ...
    async def logout(self, session: dict) -> None: ...
    async def validate_session(self, session: dict) -> dict: ...
    async def search(self, session: dict, query: str, company_ids=None, doc_types=None) -> dict: ...
    async def detail(self, session: dict, document_id: str, version_id=None) -> dict: ...
    async def ask(self, session: dict, question: str, company_ids=None, doc_types=None, document_ids=None) -> dict: ...
    async def summarize(self, session: dict, document_id: str, version_id=None) -> dict: ...
    async def upload(self, session: dict, file_path: str, company_id: str, doc_type: str) -> dict: ...
    async def download(self, session: dict, document_id: str, version_id=None) -> str: ...


def to_plain(value: Any) -> Any:
    """dict/dataclass/Pydantic 응답을 화면에서 읽을 기본 자료형으로 변환."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_plain(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return to_plain(value.model_dump())
    return value


async def call_function(function: Callable, *args, **kwargs) -> Any:
    """동기 백엔드는 작업 스레드에서, async 백엔드는 await로 호출."""
    if inspect.iscoroutinefunction(function):
        return await function(*args, **kwargs)
    result = await asyncio.to_thread(function, *args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def validate_upload_file(file_path: str | Path) -> Path:
    """B12: 빠른 입력 검사만 수행. 실제 형식·손상·암호화 검사는 ingest 책임."""
    if not file_path:
        raise UIServiceError("FILE_REQUIRED", "업로드할 파일을 선택해 주세요.", "input_error")
    path = Path(file_path)
    if not path.is_file():
        raise UIServiceError("FILE_UNAVAILABLE", "선택한 파일을 읽을 수 없습니다. 다시 선택해 주세요.", "input_error")
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise UIServiceError("UNSUPPORTED_FORMAT", "PDF, DOCX, PPTX 파일만 업로드할 수 있습니다.", "input_error")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise UIServiceError("FILE_UNAVAILABLE", "파일 정보를 읽을 수 없습니다.", "input_error") from error
    if size == 0:
        # 통합 명세 F61·CLS-040: 0바이트는 손상, EMPTY_DOCUMENT는 추출한 글자가 없는 경우다.
        raise UIServiceError("CORRUPTED_FILE", "비어 있는 파일(0바이트)은 업로드할 수 없습니다.", "input_error")
    if size > MAX_UPLOAD_BYTES:
        raise UIServiceError("FILE_TOO_LARGE", "파일은 100MiB 이하로 선택해 주세요.", "input_error")
    return path


@dataclass
class BackendBindings:
    """실제 담당 함수 연결 지점. None인 기능은 명확한 미연결 오류를 반환.

    아직 계약이 없는 auth/detail/upload/download는 아래 UI 입력을 받는 래퍼를
    연결한다. 백엔드 함수명이 같아야 할 필요는 없다.

    login(username=, password=), logout(session=), validate_session(session=)
    detail(user=, document_id=, version_id=)
    upload(user=, file_path=, company_id=, doc_type=)
    download(user=, document_id=, version_id=) -> 실제 로컬 파일 경로

    search(user=, query=, company_ids=, doc_types=, limit=)는 통합 명세 5장을 따른다.
    ask/summarize_document도 user=로 검증된 사용자 정보를 전달한다.
    user_factory는 검증된 세션을 공통 UserContext로, filters_factory는 dict를
    실제 ChatFilters로 바꾼다. 계약이 확정되면 해당 생성자를 지정한다.
    normalizers에는 기능 이름별 응답 변환 함수를 지정할 수 있다.
    company_names(session=)는 권한 안의 회사 ID·표시명 목록만 보강한다.
    """

    login: Callable | None = None
    logout: Callable | None = None
    validate_session: Callable | None = None
    search: Callable | None = None
    detail: Callable | None = None
    ask: Callable | None = None
    summarize_document: Callable | None = None
    upload: Callable | None = None
    download: Callable | None = None
    user_factory: Callable | None = None
    filters_factory: Callable | None = None
    to_feedback: Callable | None = None
    normalizers: dict[str, Callable] = field(default_factory=dict)
    temporary_auth: bool = False
    company_names: Callable | None = None


class BackendServices:
    """B24~B28 실제 호출 어댑터. 권한/만료/문서 상태는 백엔드가 재검증."""

    demo = False

    def __init__(self, bindings: BackendBindings | None = None):
        self.bindings = bindings or BackendBindings()
        self.temporary_auth = self.bindings.temporary_auth

    async def _call(self, name: str, **kwargs) -> Any:
        function = getattr(self.bindings, name, None)
        if function is None:
            raise UIServiceError("BACKEND_NOT_CONNECTED", f"{name} 담당 함수가 아직 연결되지 않았습니다. 함수 계약을 확인해 연결해 주세요.")
        try:
            response = await call_function(function, **kwargs)
            normalizer = self.bindings.normalizers.get(name)
            if normalizer:
                response = await call_function(normalizer, response)
            return to_plain(response)
        except UIServiceError:
            raise
        except Exception as error:
            if self.bindings.to_feedback is not None:
                try:
                    feedback = to_plain(await call_function(self.bindings.to_feedback, error))
                    if isinstance(feedback, dict) and feedback.get("message"):
                        raise UIServiceError(str(feedback.get("error_code") or "BACKEND_ERROR"), str(feedback["message"]), str(feedback.get("status") or "failed")) from error
                except UIServiceError:
                    raise
                except Exception:
                    pass
            # DB 접속 문자열/내부 traceback 등을 사용자 화면에 노출하지 않는다.
            raise UIServiceError("BACKEND_ERROR", "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.", "failed") from error

    @staticmethod
    def _dict(value: Any, name: str) -> dict:
        if not isinstance(value, dict):
            raise UIServiceError("INVALID_RESPONSE", f"{name} 반환 형식이 화면 연결 규격과 다릅니다. 응답 변환을 확인해 주세요.")
        return value

    async def _with_company_names(self, result: dict, previous: dict | None = None) -> dict:
        """인증이 제공하지 않은 회사명만 보강한다. 표시명은 권한 판단에 쓰지 않는다."""
        allowed = list(dict.fromkeys(result.get("allowed_company_ids") or []))
        names = {}
        if previous and previous.get("user_id") == result.get("user_id") and previous.get("session_id") == result.get("session_id"):
            # 재검증한 허용 ID와 교집합만 유지한다. 권한 자체는 캐시하지 않는다.
            names.update({code: name for code, name in _company_names(previous).items() if code in allowed})
        names.update(_company_names(result, demo=self.temporary_auth))
        if (not result.get("must_change_password")
                and any(code not in names for code in allowed)
                and self.bindings.company_names is not None):
            try:
                companies = await self._call("company_names", session=result)
                if not isinstance(companies, list):
                    raise UIServiceError("INVALID_RESPONSE", "회사 목록 형식을 확인해 주세요.")
                names.update(_company_names({**result, "companies": companies}))
            except Exception as error:
                if isinstance(error, UIServiceError) and (error.code in {"SESSION_EXPIRED", "ACCESS_DENIED", "AUTH_REQUIRED", "UNAUTHENTICATED"} or error.status == "session_expired"):
                    raise
                # 이름 조회 실패로 이미 발급된 로그인 세션을 버리지 않는다.
                # 내부 오류 원문·토큰은 기록하지 않으며, 업무 권한 검증은 그대로 수행한다.
                logging.getLogger(__name__).warning("Company display names unavailable (%s)", type(error).__name__)
        return {**result, "companies": [{"company_id": code, "company_name": names[code]}
                                         for code in allowed if code in names]}

    async def login(self, username: str, password: str) -> dict:
        if not username or not username.strip() or not password:
            raise UIServiceError("LOGIN_REQUIRED", "아이디와 비밀번호를 입력해 주세요.", "input_error")
        result = self._dict(await self._call("login", username=username.strip(), password=password), "로그인")
        required = {"user_id", "display_name", "role", "company_id", "company_name", "allowed_company_ids", "token"}
        if required - result.keys() or not result.get("token"):
            raise UIServiceError("INVALID_SESSION", "로그인 응답의 사용자·세션 정보를 확인해 주세요.")
        return await self._with_company_names({**result, "demo": False})

    async def logout(self, session: dict) -> None:
        await self._call("logout", session=session)

    async def validate_session(self, session: dict) -> dict:
        if not session or not session.get("token"):
            raise UIServiceError("SESSION_EXPIRED", "로그인이 필요합니다. 다시 로그인해 주세요.", "session_expired")
        result = self._dict(await self._call("validate_session", session=session), "세션 확인")
        # 백엔드가 반환한 최신 역할/회사 정보를 사용한다. 화면의 값을 신뢰하지 않는다.
        required = {"user_id", "role", "company_id", "allowed_company_ids"}
        if required - result.keys():
            raise UIServiceError("INVALID_SESSION", "세션 확인 응답의 사용자 정보를 확인해 주세요.")
        return await self._with_company_names({
            **result,
            "token": result.get("token", session["token"]),
            # 인증 확인 결과가 이 플래그를 생략하면 로그인 때의 변경 요구를 유지.
            # 변경 완료 후에는 auth가 명시적으로 False를 반환해야 한다.
            "must_change_password": result.get("must_change_password", bool(session.get("must_change_password"))),
            "demo": False,
        }, previous=session)

    async def _user(self, session: dict) -> Any:
        return await self._actor(await self.validate_session(session))

    async def _actor(self, checked: dict) -> Any:
        """검증된 세션만 실제 업무 함수가 요구하는 사용자 형태로 변환한다."""
        if checked.get("must_change_password"):
            raise UIServiceError("PASSWORD_CHANGE_REQUIRED", "비밀번호 변경이 필요합니다. 인증 담당의 비밀번호 변경 기능을 연결해 주세요.", "denied")
        factory = self.bindings.user_factory
        return await call_function(factory, checked) if factory else checked

    async def search(self, session: dict, query: str, company_ids=None, doc_types=None) -> dict:
        if not query or not query.strip():
            raise UIServiceError("QUERY_REQUIRED", "검색어를 입력해 주세요.", "input_error")
        response = self._dict(await self._call("search", user=await self._user(session), query=query.strip(), company_ids=company_ids or None, doc_types=doc_types or None, limit=10), "검색")
        results = response.get("results")
        if not isinstance(results, list):
            raise UIServiceError("INVALID_RESPONSE", "검색 결과 목록 형식을 확인해 주세요.")
        # total_hits는 청크 수다. 화면의 문서 건수는 len(results)를 사용한다.
        return {**response, "results": [self._document(item) for item in results]}

    @classmethod
    def _document(cls, value: Any) -> dict:
        document = dict(cls._dict(value, "문서"))
        citations = document.get("citations") or []
        if not isinstance(citations, list):
            raise UIServiceError("INVALID_RESPONSE", "문서 출처 목록 형식을 확인해 주세요.")
        first = citations[0] if citations and isinstance(citations[0], dict) else {}
        document.setdefault("location_label", first.get("location_label", "위치 정보 없음"))
        document.setdefault("excerpt", first.get("excerpt", ""))
        document.setdefault("indexed_at", None)
        document.setdefault("size_bytes", None)
        document["citations"] = citations
        return document

    async def detail(self, session: dict, document_id: str, version_id=None) -> dict:
        return self._document(await self._call("detail", user=await self._user(session), document_id=document_id, version_id=version_id))

    async def ask(self, session: dict, question: str, company_ids=None, doc_types=None, document_ids=None) -> dict:
        if not question or not question.strip():
            raise UIServiceError("QUESTION_REQUIRED", "질문을 입력해 주세요.", "input_error")
        filters = {"company_ids": company_ids or None, "doc_types": doc_types or None, "document_ids": document_ids or None}
        if self.bindings.filters_factory:
            filters = await call_function(self.bindings.filters_factory, filters)
        return self._dict(await self._call("ask", question=question.strip(), filters=filters, user=await self._user(session)), "질문")

    async def summarize(self, session: dict, document_id: str, version_id=None) -> dict:
        # 초안 summarize_document에는 version_id 인자가 없다. 결과 버전을 비교한다.
        response = self._dict(await self._call("summarize_document", document_id=document_id, user=await self._user(session)), "요약")
        if version_id and response.get("version_id") != version_id:
            raise UIServiceError("DOCUMENT_CHANGED", "문서 버전이 바뀌었습니다. 문서를 다시 선택한 뒤 요약해 주세요.", "input_error")
        return response

    async def upload(self, session: dict, file_path: str, company_id: str, doc_type: str) -> dict:
        checked = await self.validate_session(session)
        user = await self._actor(checked)
        path = validate_upload_file(file_path)
        result = self._dict(await self._call("upload", user=user, file_path=str(path), company_id=company_id, doc_type=doc_type), "업로드")
        return {**result, "company_name": _company_names(checked, demo=self.temporary_auth).get(company_id, "회사명 확인 필요")}

    async def download(self, session: dict, document_id: str, version_id=None) -> str:
        result = await self._call("download", user=await self._user(session), document_id=document_id, version_id=version_id)
        if not isinstance(result, (str, Path)) or not Path(result).is_file():
            raise UIServiceError("DOWNLOAD_UNAVAILABLE", "다운로드할 파일을 준비하지 못했습니다.")
        return str(result)
