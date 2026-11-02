"""B24~B28 화면 개발용 예시 함수. 실제 로그인·DB·LLM·색인을 실행하지 않는다.

회사/권한/세션 오류도 확인할 수 있도록 예시 서버 상태를 메모리에 둔다.
반환 객체는 매번 복사하므로 한 브라우저의 선택/수정이 다른 세션에 섞이지 않는다.
실제 함수 연결은 services.BackendBindings에서 수행한다.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .services import COMPANIES, DOC_TYPES, UIServiceError, validate_upload_file, validate_upload_metadata


_COMPANY_NAMES = {code: label for label, code in COMPANIES}
_TYPE_CODES = {code for _, code in DOC_TYPES}
_ACCOUNTS = {
    "employee": {"display_name": "김현답", "role": "employee", "company_id": "hri"},
    "manager": {"display_name": "박관리", "role": "company_admin", "company_id": "hri"},
    "admin": {"display_name": "최관리", "role": "platform_admin", "company_id": "hri"},
}


def _document(identifier, company, file_name, doc_type, location, excerpt, *, size=248320, version=1):
    document_id = f"demo-{company}-{identifier}"
    version_id = f"{document_id}-v{version}"
    citation = {
        "number": 1,
        "chunk_id": f"{version_id}-chunk-1",
        "document_id": document_id,
        "version_id": version_id,
        "company_id": company,
        "company_name": _COMPANY_NAMES[company],
        "file_name": file_name,
        "doc_type": doc_type,
        "location_label": location,
        "excerpt": excerpt,
        "score": 0.92,
    }
    return {
        "document_id": document_id,
        "version_id": version_id,
        "version_no": version,
        "company_id": company,
        "company_name": _COMPANY_NAMES[company],
        "doc_type": doc_type,
        "file_name": file_name,
        "location_label": location,
        "excerpt": excerpt,
        "indexed_at": "2026-10-20 09:30",
        "size_bytes": size,
        "score": 0.92,
        "publication_status": "published",
        "version_status": "ready",
        "citations": [citation],
        "demo": True,
    }


# Figma에 보이는 파일명을 사용한 고정 예시. 실제 회사 문서가 아니다.
# 예시 분류만 DB 허용 코드 4개에 맞춘다. 실제 백엔드의 문서 유형을 자동 변환하지 않는다.
_DOCUMENTS = (
    _document("manual", "hri", "점검 매뉴얼.pdf", "manual", "p. 3", "정기 점검은 월 1회 실시합니다. 점검 담당자는 설비 상태와 안전장치 작동 여부를 확인하고 점검표에 결과를 기록합니다. 이상이 발견되면 사용을 중지하고 관리자에게 보고합니다.", version=2),
    _document("education", "hri", "교육 자료.pptx", "guide", "슬라이드 5", "신규 입사자는 입사 후 7일 이내에 안전 교육을 이수합니다. 교육은 작업 전 점검, 비상 연락 체계, 보호구 사용 방법으로 구성됩니다.", size=1420288),
    _document("checklist", "hri", "월간 점검표.docx", "other", "섹션 2 (점검 항목)", "점검표에는 점검 일자, 설비명, 점검 담당자, 점검 결과와 조치 내용을 기록합니다. 미조치 항목은 담당자와 완료 예정일을 함께 기재합니다.", size=48128),
    _document("security", "hri", "정보보안 규정.pdf", "policy", "p. 8", "회사 문서는 승인된 계정으로만 열람합니다. 계정 정보의 공유는 금지하며 문서를 외부로 전달할 때에는 담당 관리자의 승인을 받아야 합니다.", size=524288),
    _document("report", "hri", "분기 운영 보고서.pdf", "other", "p. 4", "이번 분기에는 정기 점검 이행률과 교육 이수 현황을 검토했습니다. 미완료 항목은 담당자별 조치 계획을 수립하여 다음 회의에서 확인합니다.", size=812032),
    _document("guide", "hri", "업무 안내.docx", "guide", "섹션 1 (업무 시작)", "업무 시작 전 공지 사항과 담당 업무를 확인합니다. 자료가 필요한 경우 문서 검색에서 회사와 문서 유형을 선택한 뒤 검색어를 입력합니다.", size=34816),
    _document("contract", "hri", "표준 용역 계약서.docx", "other", "섹션 3 (업무 범위)", "계약 당사자는 업무 범위, 수행 기간 및 산출물을 사전에 합의합니다. 변경 사항은 서면 확인 후 적용하며 산출물 검수 기준을 함께 정합니다.", size=74240),
    _document("manual", "gte", "태양광 설비 점검 매뉴얼.pdf", "manual", "p. 12", "태양광 설비는 월 1회 정기 점검합니다. 모듈 오염, 접속함 상태와 인버터 경보를 확인하고 점검 기록을 보관합니다.", size=2097152),
    _document("education", "gte", "현장 안전 교육.pptx", "guide", "슬라이드 7", "현장 작업자는 작업 전 보호구를 착용하고 안전 점검을 진행합니다. 전기 설비 점검은 작업 책임자의 승인과 차단 확인 후 시작합니다.", size=1310720),
    _document("report", "gte", "에너지 운영 보고서.pdf", "other", "p. 6", "월별 발전량과 설비 점검 이력을 함께 확인하여 운영 효율을 검토합니다. 이상 추이는 현장 점검 계획에 반영합니다.", size=912384),
    _document("policy", "bpa", "투자 업무 규정.pdf", "policy", "p. 9", "투자 검토 과정에서는 자료의 최신성과 이해상충 여부를 확인합니다. 검토 의견과 승인 내역은 지정된 문서 양식에 기록합니다.", size=672768),
    _document("checklist", "bpa", "내부통제 점검표.docx", "other", "섹션 2 (확인 사항)", "내부통제 점검은 분기별로 실시합니다. 문서 접근 권한, 승인 절차 준수와 증빙 보관 상태를 확인하여 담당 관리자에게 보고합니다.", size=58368),
)


@dataclass
class _SessionRecord:
    public: dict
    expires_at: float
    uploaded_hashes: set[tuple[str, str]] = field(default_factory=set)


def demo_classification(file_name: str) -> dict:
    """화면 검수용 고정 결과. 이름에 규정/매뉴얼/안내/기타/실패가 없으면 낮은 확신도 예시."""
    stem = Path(file_name).stem.lower()
    values = (("policy", ("규정", "policy")), ("manual", ("매뉴얼", "manual")),
              ("guide", ("안내", "가이드", "guide")), ("other", ("기타", "other")))
    kind = next((code for code, words in values if any(word in stem for word in words)), None)
    if "실패" in stem or "failed" in stem:
        return {"doc_type": "other", "doc_type_fallback": True,
                "doc_type_fallback_reason": "CLASSIFY_FAILED", "auto_doc_type": "",
                "doc_type_confidence": 0.0, "classify_status": "failed", "company_match": ""}
    return {"doc_type": kind or "other", "doc_type_fallback": kind is None,
            "doc_type_fallback_reason": None if kind else "LOW_CONFIDENCE",
            "auto_doc_type": kind or "guide", "doc_type_confidence": 0.94 if kind else 0.55,
            "classify_status": "success", "company_match": ""}


class MockServices:
    """화면 확인 전용. employee / manager / admin, 비밀번호 demo1234."""

    demo = True

    def __init__(self, session_ttl_seconds: float = 3600, clock: Callable[[], float] = time.monotonic, *, auto_classify: bool = True):
        self.auto_classify = auto_classify
        self._ttl = session_ttl_seconds
        self._clock = clock
        self._sessions: dict[str, _SessionRecord] = {}
        self._lock = threading.RLock()

    def _checked(self, session: dict) -> _SessionRecord:
        # 클라이언트가 보낸 role/company 값은 믿지 않고 토큰으로 서버 상태를 조회.
        token = session.get("token") if isinstance(session, dict) else None
        with self._lock:
            record = self._sessions.get(token)
            if record is None or record.expires_at <= self._clock():
                if token:
                    self._sessions.pop(token, None)
                raise UIServiceError("SESSION_EXPIRED", "세션이 만료되었습니다. 다시 로그인해 주세요.", "session_expired")
            return record

    async def login(self, username: str, password: str) -> dict:
        username = (username or "").strip()
        if not username or not password:
            raise UIServiceError("LOGIN_REQUIRED", "아이디와 비밀번호를 입력해 주세요.", "input_error")
        account = _ACCOUNTS.get(username)
        if account is None or not secrets.compare_digest(str(password), "demo1234"):
            raise UIServiceError("INVALID_CREDENTIALS", "아이디 또는 비밀번호가 올바르지 않습니다.", "input_error")
        token = secrets.token_urlsafe(32)
        company = account["company_id"]
        session = {
            "user_id": f"demo-{username}",
            "display_name": account["display_name"],
            "role": account["role"],
            "company_id": company,
            "company_name": _COMPANY_NAMES[company],
            "allowed_company_ids": list(_COMPANY_NAMES) if account["role"] == "platform_admin" else [company],
            "token": token,
            "demo": True,
            "auth_version": 1,
            "must_change_password": False,
        }
        with self._lock:
            # 만료된 예시 세션은 새 로그인 때 정리한다.
            self._sessions = {key: value for key, value in self._sessions.items() if value.expires_at > self._clock()}
            self._sessions[token] = _SessionRecord(copy.deepcopy(session), self._clock() + self._ttl)
        return copy.deepcopy(session)

    async def logout(self, session: dict) -> None:
        with self._lock:
            self._sessions.pop((session or {}).get("token"), None)

    async def validate_session(self, session: dict) -> dict:
        return copy.deepcopy(self._checked(session).public)

    def _scope(self, session: dict, company_ids=None, doc_types=None) -> tuple[list[str], list[str]]:
        record = self._checked(session)
        allowed = record.public["allowed_company_ids"]
        if company_ids is not None and (not isinstance(company_ids, (list, tuple)) or any(not isinstance(item, str) for item in company_ids)):
            raise UIServiceError("INVALID_FILTER", "회사 선택값을 확인해 주세요.", "input_error")
        companies = list(company_ids) if company_ids else list(allowed)
        if any(company not in allowed for company in companies):
            raise UIServiceError("ACCESS_DENIED", "접근할 수 없는 회사가 선택되어 있습니다.", "denied")
        if doc_types is not None and (not isinstance(doc_types, (list, tuple)) or any(item not in _TYPE_CODES for item in doc_types)):
            raise UIServiceError("INVALID_FILTER", "문서 유형을 다시 선택해 주세요.", "input_error")
        return companies, list(doc_types or [])

    def _find(self, session: dict, document_id: str, version_id=None) -> dict:
        record = self._checked(session)
        document = next((item for item in _DOCUMENTS if item["document_id"] == document_id), None)
        if document is None:
            raise UIServiceError("DOCUMENT_UNAVAILABLE", "문서를 찾을 수 없습니다. 검색 결과를 다시 확인해 주세요.", "empty")
        if document["company_id"] not in record.public["allowed_company_ids"]:
            raise UIServiceError("ACCESS_DENIED", "이 문서에 접근할 권한이 없습니다.", "denied")
        if version_id and document["version_id"] != version_id:
            raise UIServiceError("DOCUMENT_CHANGED", "문서 버전이 변경되었습니다. 문서를 다시 선택해 주세요.", "input_error")
        return document

    @staticmethod
    def _matches(document: dict, query: str) -> bool:
        words = re.findall(r"[가-힣A-Za-z0-9]+", query.lower())
        stop_words = {"알려줘", "알려주세요", "해주세요", "어떻게", "돼", "뭐야", "있나요", "무엇인가요", "내용", "문서", "대한", "대해", "요약해줘"}
        words = [word for word in words if word not in stop_words]
        searchable = f"{document['file_name']} {document['excerpt']} {document['company_name']}".lower()
        # 검색 UI 시연용 단순 문자열 비교이며 벡터 검색/LLM 판정을 흉내 내지 않는다.
        return bool(words) and any(word in searchable for word in words)

    async def search(self, session: dict, query: str, company_ids=None, doc_types=None) -> dict:
        companies, types = self._scope(session, company_ids, doc_types)
        query = (query or "").strip()
        if not query:
            raise UIServiceError("QUERY_REQUIRED", "검색어를 입력해 주세요.", "input_error")
        results = [item for item in _DOCUMENTS if item["company_id"] in companies and (not types or item["doc_type"] in types) and self._matches(item, query)]
        return copy.deepcopy({
            "query": query,
            "results": results[:10],
            "total_hits": sum(len(item["citations"]) for item in results),
            "company_ids": companies,
            "doc_types": types or None,
            "elapsed_sec": 0.02,
            "demo": True,
        })

    async def detail(self, session: dict, document_id: str, version_id=None) -> dict:
        return copy.deepcopy(self._find(session, document_id, version_id))

    async def ask(self, session: dict, question: str, company_ids=None, doc_types=None, document_ids=None) -> dict:
        companies, types = self._scope(session, company_ids, doc_types)
        question = (question or "").strip()
        if not question:
            raise UIServiceError("QUESTION_REQUIRED", "질문을 입력해 주세요.", "input_error")
        if document_ids:
            documents = [self._find(session, identifier) for identifier in document_ids]
            if any(item["company_id"] not in companies or (types and item["doc_type"] not in types) for item in documents):
                raise UIServiceError("FILTER_CONFLICT", "선택한 문서가 현재 회사·문서 유형 필터에 포함되지 않습니다.", "input_error")
        else:
            documents = list(_DOCUMENTS)
        documents = [item for item in documents if item["company_id"] in companies and (not types or item["doc_type"] in types) and self._matches(item, question)]
        if not documents:
            return {"status": "NO_EVIDENCE", "answer": "", "citations": [], "document_ids": [], "message": "선택한 범위의 예시 문서에서 관련 근거를 찾지 못했습니다. 질문이나 필터를 바꿔 주세요.", "error_code": None, "demo": True}
        citations = []
        for number, document in enumerate(documents[:3], start=1):
            citation = copy.deepcopy(document["citations"][0])
            citation["number"] = number
            citations.append(citation)
        answer = "\n\n".join(f"{citation['excerpt']} [{citation['number']}]" for citation in citations)
        return {
            "status": "ANSWERED",
            "answer": answer,
            "citations": citations,
            "citations_by_company": {company: [copy.deepcopy(item) for item in citations if item["company_id"] == company] for company in dict.fromkeys(item["company_id"] for item in citations)},
            "document_ids": [item["document_id"] for item in citations],
            "message": "예시 문장의 발췌입니다. 실제 AI가 생성한 답변이 아닙니다.",
            "error_code": None,
            "demo": True,
        }

    async def summarize(self, session: dict, document_id: str, version_id=None) -> dict:
        document = self._find(session, document_id, version_id)
        is_full = document_id != "demo-hri-manual"
        return {
            "document_id": document["document_id"],
            "version_id": document["version_id"],
            "summary": document["excerpt"],
            "citations": copy.deepcopy(document["citations"]),
            "is_full": is_full,
            "included_range": "문서 전체 (화면 확인용 예시)" if is_full else "p. 1–8 (화면 확인용 예시)",
            "excluded_range": [] if is_full else ["p. 9–12: 예시 부분 요약 범위 밖"],
            "total_chunks": 1 if is_full else 12,
            "used_chunks": 1 if is_full else 8,
            "demo": True,
            "message": "예시 요약입니다. 실제 문서를 읽거나 AI를 호출하지 않았습니다.",
        }

    async def summarize_stream(self, session: dict, document_id: str, version_id=None):
        yield {"type": "status", "stage": "final", "message": "요약을 생성하고 있어요.", "step": [1, 1]}
        response = await self.summarize(session, document_id, version_id)
        for line in response["summary"].splitlines(keepends=True):
            yield {"type": "delta", "text": line}
        yield {"type": "done", "response": response}

    async def upload(self, session: dict, file_path: str, company_id: str, doc_type: str | None = None) -> dict:
        record = self._checked(session)
        if record.public["role"] not in {"company_admin", "platform_admin"}:
            raise UIServiceError("ACCESS_DENIED", "문서 업로드는 관리자만 사용할 수 있습니다.", "denied")
        automatic = self.auto_classify and doc_type is None
        validate_upload_metadata(company_id, doc_type, auto_classify=automatic)
        self._scope(session, [company_id], None if automatic else [doc_type])
        path = validate_upload_file(file_path)

        def file_hash():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            return digest.hexdigest()

        try:
            fingerprint = await asyncio.to_thread(file_hash)
        except OSError as error:
            raise UIServiceError("FILE_UNAVAILABLE", "파일을 읽지 못했습니다. 다시 선택해 주세요.", "input_error") from error
        with self._lock:
            record = self._checked(session)
            duplicate_key = (company_id, fingerprint)
            if duplicate_key in record.uploaded_hashes:
                raise UIServiceError("DUPLICATE_FILE", "이 예시 세션에서 같은 회사에 제출한 파일입니다.", "input_error")
            record.uploaded_hashes.add(duplicate_key)
        # 파일명에 따른 고정 예시이며 실제 AI 분류/원본 보관/추출/색인은 하지 않는다.
        classification = demo_classification(path.name) if automatic else {
            "doc_type": doc_type, "doc_type_fallback": False, "doc_type_fallback_reason": None,
        }
        return {
            "status": "simulated",
            "job_status": "succeeded",
            "job_id": f"demo-upload-{secrets.token_hex(6)}",
            "step": "validate",
            "document_id": None,
            "version_id": None,
            "file_name": path.name,
            "company_id": company_id,
            "company_name": _COMPANY_NAMES[company_id],
            **classification,
            "size_bytes": path.stat().st_size,
            "searchable": False,
            "simulated": True,
            "demo": True,
            "message": "입력 확인 예시가 완료되었습니다. 원본 저장·본문 추출·색인을 하지 않았으며 검색 결과에 추가되지 않습니다.",
            "error_code": None,
        }

    async def retry_upload(self, session: dict, version_id: str) -> dict:
        """데모는 저장된 후보를 만들지 않으므로 실제 재처리 성공을 흉내내지 않는다."""
        self._checked(session)
        raise UIServiceError("RETRY_UNAVAILABLE", "예시 모드에는 다시 처리할 저장 파일이 없습니다.", "input_error")

    async def download(self, session: dict, document_id: str, version_id=None) -> str:
        document = self._find(session, document_id, version_id)
        # 원본 파일인 것처럼 다른 확장자를 붙이지 않는다. 예시임을 파일에도 명시.
        text = (
            "현답 화면 확인용 예시 파일\n"
            "이 파일은 회사 원본 문서가 아닙니다. 실제 문서 다운로드 기능이 연결되지 않았습니다.\n\n"
            f"표시 문서명: {document['file_name']}\n"
            f"회사: {document['company_name']}\n"
            f"위치: {document['location_label']}\n\n"
            f"예시 발췌:\n{document['excerpt']}\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", suffix=".txt", prefix="hyundap_demo_", delete=False) as stream:
            stream.write(text)
            return stream.name
