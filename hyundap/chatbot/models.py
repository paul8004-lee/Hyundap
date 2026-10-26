"""chatbot/models.py — 챗봇 내부 데이터와 오류 형식.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-125)

함수:
    FUN-425 ChatbotError.__init__ — 오류 코드·안내 문구·재시도 여부를 담는다.
    FUN-426 to_feedback — 내부 오류를 사용자 안내로 바꾼다.
    FUN-427 map_dependency_error — 외부 모듈 오류를 챗봇 오류로 변환한다.

자료형:
    CLS-120 ChatbotError
    CLS-121 ChatFilters
    CLS-123 Citation          (common/schemas.py 반영 전 임시 위치, 미정 D04)
    CLS-124 ChatResponse      (common/schemas.py 반영 전 임시 위치, 미정 D04)
    CLS-125 SummaryResponse   (common/schemas.py 반영 전 임시 위치, 미정 D10)
"""
from dataclasses import dataclass, field

from .constants import ACCESS_ERROR_NAMES, ERROR_CODES, ERROR_MESSAGES, SEARCH_ACCESS_CODES


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


def to_feedback(error):
    """받는 것: 예외 / 돌려주는 것: 화면 안내용 {status, message, error_code}. 내부 예외 원문은 넣지 않는다.
    auth AccessError는 STATE_CHANGED면 다시 질문 안내, 나머지(ACCESS_DENIED·SESSION_EXPIRED 등)는 권한 없음 안내."""
    if type(error).__name__ in ACCESS_ERROR_NAMES:
        if getattr(error, 'code', None) == 'STATE_CHANGED':
            return {'status': 'failed', 'message': ERROR_MESSAGES['STATE_CHANGED'], 'error_code': 'STATE_CHANGED'}
        return {'status': 'denied', 'message': ERROR_MESSAGES['ACCESS_DENIED'], 'error_code': 'ACCESS_DENIED'}
    if isinstance(error, ChatbotError):
        if error.code in ('EMPTY_QUESTION', 'QUESTION_TOO_LONG'):
            status = 'input_error'
        elif error.code == 'NO_EVIDENCE':
            status = 'empty'
        elif error.code == 'ACCESS_DENIED':
            status = 'denied'
        else:
            status = 'failed'
        return {'status': status, 'message': error.message, 'error_code': error.code}
    return {'status': 'failed', 'message': '요청을 처리하지 못했습니다.', 'error_code': None}


def map_dependency_error(error, stage):
    """받는 것: 다른 폴더(search·auth·embedding·LLM)에서 난 예외, 단계('retrieve'·'llm'·'summary'·'recheck')
    돌려주는 것: ChatbotError. 이미 ChatbotError인 것과 권한 오류(AccessError)는 그대로 돌려주되,
    AccessError(STATE_CHANGED)는 ChatbotError(STATE_CHANGED)로 바꾼다 (DB 계약 2절)."""
    name = type(error).__name__
    if isinstance(error, ChatbotError):
        return error
    if name in ACCESS_ERROR_NAMES:
        return ChatbotError('STATE_CHANGED') if getattr(error, 'code', None) == 'STATE_CHANGED' else error
    if name == 'DbError':  # 재검증 중 DB 장애 등. 원시 DB 오류는 화면에 내보내지 않는다
        return ChatbotError('RETRIEVAL_FAILED', retryable=getattr(error, 'code', None) == 'DB_UNAVAILABLE')
    if stage == 'recheck':  # 재검증 중 예상 못 한 오류는 감추지 않고 그대로 올린다
        return error
    retryable = bool(getattr(error, 'retryable', False))
    if name == 'SearchError':
        if getattr(error, 'code', None) in SEARCH_ACCESS_CODES:
            return ChatbotError('ACCESS_DENIED')
        return ChatbotError('RETRIEVAL_FAILED', retryable=retryable)
    if stage in ('retrieve', 'summary') or name in ('EmbeddingError', 'VectorStoreError'):
        return ChatbotError('RETRIEVAL_FAILED', retryable=retryable)
    if 'Authentication' in name or 'PermissionDenied' in name:
        return ChatbotError('CONFIG_MISSING')
    if any(key in name for key in ('Timeout', 'RateLimit', 'APIConnection')):
        return ChatbotError('LLM_FAILED', retryable=True)
    return ChatbotError('LLM_FAILED')
