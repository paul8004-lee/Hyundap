"""chatbot/models.py — 챗봇 내부 데이터와 오류 형식.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-125)

함수:
    FUN-425 ChatbotError.__init__ — 오류 코드·안내 문구·재시도 여부를 담는다.
    FUN-426 to_feedback — 내부 오류를 사용자 안내로 바꾼다.
        to_feedback_access — auth AccessError를 화면 안내로 바꾼다.
        to_feedback_status — 오류 코드를 화면 상태로 바꾼다.
    FUN-427 map_dependency_error — 외부 모듈 오류를 챗봇 오류로 변환한다.
        map_dependency_error_access — AccessError(STATE_CHANGED)만 챗봇 오류로 바꾼다.
        map_dependency_error_search — SearchError를 ACCESS_DENIED·RETRIEVAL_FAILED로 바꾼다.
        map_dependency_error_llm — LLM 오류를 CONFIG_MISSING·LLM_FAILED로 바꾼다.

자료형:
    CLS-120 ChatbotError
    CLS-121 ChatFilters
    CLS-123 Citation
    CLS-124 ChatResponse
    CLS-125 SummaryResponse
    CLS-126 DocClassification — 문서 자동 분류 결과 (명세 밖 추가)
    SummaryPlan — 요약 구간 계획 (summary.plan_summary_segments 결과, 명세 밖 내부 자료형)

결과·필터 클래스 4개는 chatbot만 만들고 쓰므로 common이 아니라 이 파일에 둔다 (D04, 이성민 결정 10/26).
다른 폴더에서는 `from hyundap.chatbot.models import ChatFilters`처럼 가져온다.
"""
from dataclasses import dataclass, field

from .constants import ACCESS_ERROR_NAMES, ERROR_CODES, ERROR_MESSAGES, FEEDBACK_STATUS, SEARCH_ACCESS_CODES


class ChatbotError(Exception):
    """chatbot 단계 오류. 화면 안내용 코드·문구와 재시도 가능 여부를 담는다."""

    def __init__(self, code, message=None, retryable=False):
        """받는 것: 오류 코드(ERROR_CODES 중 하나), 안내 문구(없으면 기본 문구), 재시도 가능 여부"""
        if code not in ERROR_CODES:
            raise ValueError(f'unknown chatbot error code: {code}')
        self.code = code
        self.message = message or ERROR_MESSAGES[code]
        self.retryable = retryable
        super().__init__(self.message)


@dataclass
class ChatFilters:
    """화면에서 넘어온 검색 조건. None이면 허용 범위 전체이며, 허용 범위를 넓히지 않고 좁히기만 한다."""
    company_ids: list[str] | None = None
    doc_types: list[str] | None = None
    document_ids: list[str] | None = None


@dataclass
class Citation:
    """답변 근거 1건. 회사명·파일명·위치는 ChunkHit 값을 그대로 옮긴다."""
    number: int
    chunk_id: str
    document_id: str
    version_id: str
    version_no: int | None
    company_id: str
    company_name: str
    file_name: str
    doc_type: str
    location_label: str
    excerpt: str
    score: float | None = None


@dataclass
class ChatResponse:
    """질문 결과. 오류는 이 객체가 아니라 예외로 전달하고 to_feedback으로 화면 문구를 만든다."""
    status: str                                   # 'ANSWERED' 또는 'NO_EVIDENCE'
    answer: str
    citations: list[Citation] = field(default_factory=list)
    citations_by_company: dict[str, list[Citation]] = field(default_factory=dict)
    document_ids: list[str] = field(default_factory=list)
    message: str = ''
    error_code: str | None = None


@dataclass
class SummaryResponse:
    """요약 결과와 포함 범위. 일부 청크만 요약했으면 is_full=False."""
    document_id: str
    version_id: str
    summary: str
    citations: list[Citation] = field(default_factory=list)
    included_range: str = ''
    excluded_range: str = ''
    is_full: bool = True
    total_chunks: int = 0
    used_chunks: int = 0
    cited_ratio: float = 0.0  # 사용 청크 중 인용된 비율 (명세 밖 추가 필드, ui는 읽지 않아도 된다)



@dataclass
class DocClassification:
    """문서 자동 분류 결과 (doc_classifier.classify_common_document). 수집 헤더의 doc_type은 바꾸지 않고 나란히 돌려준다.
    자동 분류 업로드(#261)에서 실제 저장할 유형은 doc_classifier.decide_doc_type이 이 결과로 정한다.
    분류에 실패하면 status='failed'이고 유형·일치 값은 비어 있다. detail에는 예외 이름만 넣는다(키 노출 방지)."""
    document_id: str
    version_id: str
    admin_doc_type: str                       # 관리자가 업로드 때 고른 유형
    auto_doc_type: str = ''                   # manual·policy·guide·other 또는 ''(모름)
    confidence: float = 0.0                   # auto_doc_type 확신도(0~1)
    matches_admin_type: bool | None = None    # auto_doc_type이 비면 None
    company_match: str = ''                   # 업로드 회사와 같으면 'yes', 다르면 'no', 본문에 회사명이 없거나 애매하면 ''
    status: str = 'success'                   # 'success' 또는 'failed'
    detail: str = ''


@dataclass
class SummaryPlan:
    """요약 구간 계획. segments는 차례로 요약할 청크 묶음(최대 SUMMARY_MAX_CALLS개), excluded는 예산을 넘어 뺀 청크."""
    segments: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    total_tokens: int = 0


def to_feedback(error):
    """받는 것: 예외 / 돌려주는 것: 화면 안내용 {status, message, error_code}. 내부 예외 원문은 넣지 않는다.
    auth AccessError는 STATE_CHANGED면 다시 질문 안내, SESSION_EXPIRED면 재로그인 안내, 나머지는 권한 없음 안내."""
    if type(error).__name__ in ACCESS_ERROR_NAMES:
        return to_feedback_access(error)
    if isinstance(error, ChatbotError):
        return {'status': to_feedback_status(error.code), 'message': error.message, 'error_code': error.code}
    return {'status': 'failed', 'message': '요청을 처리하지 못했습니다.', 'error_code': None}


def to_feedback_access(error):
    """받는 것: auth AccessError / 돌려주는 것: 화면 안내 dict
    STATE_CHANGED면 다시 질문 안내(failed), SESSION_EXPIRED면 재로그인 안내(session_expired),
    나머지(ACCESS_DENIED 등)는 권한 없음 안내(denied)."""
    code = getattr(error, 'code', None)
    code = code if code in ('STATE_CHANGED', 'SESSION_EXPIRED') else 'ACCESS_DENIED'
    return {'status': to_feedback_status(code), 'message': ERROR_MESSAGES[code], 'error_code': code}


def to_feedback_status(code):
    """받는 것: 오류 코드 / 돌려주는 것: 화면 상태 (input_error·empty·denied·failed)"""
    return FEEDBACK_STATUS.get(code, 'failed')


def map_dependency_error(error, stage):
    """받는 것: 다른 폴더(search·auth·embedding·LLM)에서 난 예외, 단계('retrieve'·'llm'·'summary'·'recheck')
    돌려주는 것: ChatbotError. 이미 ChatbotError인 것과 권한 오류(AccessError)는 그대로 돌려주되,
    AccessError(STATE_CHANGED)는 ChatbotError(STATE_CHANGED)로 바꾼다 (DB 계약 2절)."""
    name = type(error).__name__
    if isinstance(error, ChatbotError):
        return error
    if name in ACCESS_ERROR_NAMES:
        return map_dependency_error_access(error)
    if name == 'DbError':  # 재검증 중 DB 장애 등. 원시 DB 오류는 화면에 내보내지 않는다
        return ChatbotError('RETRIEVAL_FAILED', retryable=getattr(error, 'code', None) == 'DB_UNAVAILABLE')
    if stage == 'recheck':  # 재검증 중 예상 못 한 오류는 감추지 않고 그대로 올린다
        return error
    if name == 'SearchError':
        return map_dependency_error_search(error)
    if stage in ('retrieve', 'summary') or name in ('EmbeddingError', 'VectorStoreError'):
        return ChatbotError('RETRIEVAL_FAILED', retryable=bool(getattr(error, 'retryable', False)))
    return map_dependency_error_llm(name)


def map_dependency_error_access(error):
    """받는 것: auth AccessError / 돌려주는 것: STATE_CHANGED면 ChatbotError(STATE_CHANGED), 나머지는 그대로"""
    return ChatbotError('STATE_CHANGED') if getattr(error, 'code', None) == 'STATE_CHANGED' else error


def map_dependency_error_search(error):
    """받는 것: search SearchError / 돌려주는 것: 세션 만료면 SESSION_EXPIRED, 권한 코드면 ACCESS_DENIED, 나머지는 RETRIEVAL_FAILED"""
    if getattr(error, 'code', None) == 'SESSION_EXPIRED':
        return ChatbotError('SESSION_EXPIRED')
    if getattr(error, 'code', None) in SEARCH_ACCESS_CODES:
        return ChatbotError('ACCESS_DENIED')
    return ChatbotError('RETRIEVAL_FAILED', retryable=bool(getattr(error, 'retryable', False)))


def map_dependency_error_llm(name):
    """받는 것: LLM 단계 예외 클래스 이름 / 돌려주는 것: CONFIG_MISSING(키 오류) 또는 LLM_FAILED(시간 초과 등은 재시도 가능)"""
    if 'Authentication' in name or 'PermissionDenied' in name:
        return ChatbotError('CONFIG_MISSING')
    if any(key in name for key in ('Timeout', 'RateLimit', 'APIConnection')):
        return ChatbotError('LLM_FAILED', retryable=True)
    return ChatbotError('LLM_FAILED')
