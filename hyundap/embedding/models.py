"""embedding/models.py - 결과 형식·오류 (수업 범위 리팩토링판)

Owner: 박영준
Spec: AI_문서검색_통합_기능명세서 (MOD-103)

수업 범위: 일반 class·Exception 상속, typing, 팀 공통 get_settings().
예외(dataclass): 결과 5종은 팀 코드가 dataclass로 읽고(db의 is_dataclass/asdict, VectorHit 위치 인자 생성)
                 있어서 수업 밖이지만 @dataclass를 유지한다. pydantic으로 바꾸면 db 업로드가 INVALID_INPUT으로 멈춘다.

Classes:
    CLS-101 EmbeddedChunk / CLS-102 EmbeddingFailure / CLS-103 EmbeddingBatch
    CLS-104 EmbedStoreReport / CLS-105 VectorHit / CLS-106 EmbeddingError / CLS-107 VectorStoreError / CLS-109 ClassifyResult
"""
# dataclass: 값을 담는 클래스를 짧게 만드는 파이썬 기본 기능 (팀 db 코드가 이 형태로 읽어서 유지)
# field(default_factory=list): 리스트 기본값을 객체마다 새로 만들기 위해 사용
from dataclasses import dataclass, field
from typing import Any, Optional

# 팀 공통 설정 함수. 모델 이름·API 키·저장 폴더는 여기서만 읽는다(이슈 #141)
from hyundap.common.config import get_settings


# ---------------------------------------------------------------------------
# 결과 데이터 (CLS-101 ~ CLS-105)
# ---------------------------------------------------------------------------


@dataclass
# CLS-101. 임베딩이 끝난 청크 1개의 묶음 (청크 + 벡터 + 저장용 메타데이터 + 잘림 여부)
class EmbeddedChunk:
    """CLS-101. 임베딩이 끝난 청크 1개와 벡터·메타데이터·잘림 여부."""

    # 원본 청크(Chunk 객체 또는 dict)
    chunk: Any
    # 임베딩 벡터(숫자 1536개 등)
    vector: list[float]
    # Chroma에 함께 저장할 정보(회사·문서·버전·공개 여부 등)
    metadata: dict
    # 글이 너무 길어 잘렸는지 여부
    truncated: bool = False


@dataclass
# CLS-102. 실패한 청크 1개와 그 원인
class EmbeddingFailure:
    """CLS-102. 임베딩·저장에 실패한 청크 1개와 실패 원인. message에는 비밀 값을 넣지 않는다."""

    chunk_id: str
    # 오류 코드(EMPTY_TEXT, EMBED_FAILED 등)
    code: str
    message: str
    # True면 나중에 다시 시도해 볼 만한 실패
    retryable: bool = False


@dataclass
# CLS-103. embed_chunks의 결과 (성공 목록 + 실패 목록)
class EmbeddingBatch:
    """CLS-103. embed_chunks의 결과 묶음(성공 목록 + 실패 목록)."""

    # 성공한 청크들
    items: list[EmbeddedChunk] = field(default_factory=list)
    # 실패한 청크들
    failures: list[EmbeddingFailure] = field(default_factory=list)


@dataclass
# CLS-104. embed_and_store의 결과 보고서. db 쪽이 이 값을 읽어 작업 기록을 남긴다
class EmbedStoreReport:
    """CLS-104. embed_and_store의 결과 보고서.

    status: success(실패 0) | partial(일부 실패) | failed(저장·건너뜀 모두 0)
    """

    document_id: str
    version_id: str
    company_id: str
    model_name: str
    # 받은 청크 수
    total: int = 0
    # 이미 저장돼 있어 건너뛴 수
    skipped_existing: int = 0
    # 임베딩에 성공한 수
    embedded: int = 0
    # Chroma에 실제로 저장한 수
    stored: int = 0
    # 길어서 잘린 청크 수
    truncated_count: int = 0
    failures: list[EmbeddingFailure] = field(default_factory=list)
    # success(실패 0) / partial(일부 실패) / failed(저장·건너뜀 모두 0)
    status: str = "failed"
    # 걸린 시간(초)
    elapsed_sec: float = 0.0
    # 문서 분류 결과(classifier). 분류 실패는 status에 영향을 주지 않는다
    auto_doc_type: str = ""
    doc_type_confidence: float = 0.0
    company_match: str = ""
    # skipped(키 없음·새 청크 없음) / success / failed
    classify_status: str = "skipped"


@dataclass
# CLS-109. 문서 분류 결과. 값을 모르면 빈 문자열(Chroma 메타데이터에 None을 넣을 수 없다)
class ClassifyResult:
    """CLS-109. classify_document의 결과.

    auto_doc_type: DOC_TYPES 코드(manual·policy·guide·other) 중 하나 또는 빈 문자열
    doc_type_confidence: auto_doc_type의 확신도(0~1, Decisions API의 confidence). 분류 전·실패면 0
    company_match: 본문의 작성 회사가 업로드한 회사와 같으면 yes, 다르면 no,
                   본문에 회사 이름이 없거나 확률이 애매하면 빈 문자열
    """

    auto_doc_type: str = ""
    doc_type_confidence: float = 0.0
    company_match: str = ""
    # skipped(분류 꺼짐·키 없음) / success / failed
    status: str = "skipped"
    # 실패 원인(예외 이름 등). 비밀 값은 넣지 않는다
    detail: str = ""


@dataclass
# CLS-105. 벡터 조회 결과 1개. search·chatbot이 이 형태로 받는다
class VectorHit:
    """CLS-105. 벡터 조회 결과 1개. score는 0~1 유사도이고, 문서 순서 조회에서는 None."""

    chunk_id: str
    text: str
    metadata: dict
    # 유사도 0~1. 문서 순서 조회(요약용)에서는 None
    score: Optional[float] = None


# ---------------------------------------------------------------------------
# 오류 (CLS-106, CLS-107)
# ---------------------------------------------------------------------------


# CLS-106. 임베딩 단계 오류. code로 원인을 구분하고 retryable로 재시도 가능 여부를 알린다
class EmbeddingError(Exception):
    """CLS-106. 임베딩 단계 오류. retryable이 True면 나중에 다시 시도할 수 있다."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        # 오류 메시지는 '코드: 설명' 형태로 만든다
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


# CLS-107. 벡터 저장소(Chroma) 단계 오류
class VectorStoreError(Exception):
    """CLS-107. 벡터 저장소 단계 오류."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# 팀 코드와 연결되는 자리
# ---------------------------------------------------------------------------


# 임베딩 모델 이름을 정한다. 인자가 있으면 그것을, 없으면 설정의 OPENAI_EMBEDDING_MODEL을 쓴다
def get_model_name(model_name: Optional[str] = None) -> str:
    """임베딩 모델 이름. 인자가 없으면 팀 공통 설정(OPENAI_EMBEDDING_MODEL)을 쓴다. (이슈 #141)"""
    return (model_name or "").strip() or get_settings().openai_embedding_model.strip()


# OpenAI API 키를 읽는다. 없으면 빈 문자열(오류는 get_embedding_model에서 낸다)
def get_api_key() -> str:
    """OpenAI API 키. 없으면 빈 문자열(NO_API_KEY 오류는 get_embedding_model에서 낸다)."""
    return (get_settings().openai_api_key or "").strip()


# Chroma가 파일을 저장할 폴더(CHROMA_DB_PATH)
def get_chroma_path() -> str:
    """Chroma 저장 폴더(CHROMA_DB_PATH)."""
    return str(get_settings().chroma_db_path)


# 청크에서 필드 값 하나를 읽는다. dict든 객체든 같은 방식으로 읽기 위한 함수
def get_chunk_value(chunk: Any, name: str, default: Any = None) -> Any:
    """청크에서 필드 값을 읽는다. 객체여도 dict여도 같은 방식으로 읽는다. (연결 자리: common/schemas.py의 Chunk)"""
    # dict이면 .get으로, 아니면(객체) getattr로 읽는다
    if isinstance(chunk, dict):
        return chunk.get(name, default)
    return getattr(chunk, name, default)
