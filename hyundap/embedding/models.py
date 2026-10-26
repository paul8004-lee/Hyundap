"""embedding/models.py - 결과 형식·오류

Owner: 박영준
Spec: AI_문서검색_통합_기능명세서 (MOD-103)

Classes:
    CLS-101 EmbeddedChunk
    CLS-102 EmbeddingFailure
    CLS-103 EmbeddingBatch
    CLS-104 EmbedStoreReport
    CLS-105 VectorHit
    CLS-106 EmbeddingError
    CLS-107 VectorStoreError

아래 "팀 코드와 연결되는 자리" 두 곳(load_embedding_settings, get_chunk_value)은
hyundap/common/config.py·schemas.py(이성민)와 연결되는 곳이며, 연결이 바뀌면 이 파일 안에서만 고친다.
import 기준은 이슈 #131: 다른 폴더는 hyundap. 전체 경로, 같은 폴더 안은 상대 경로(.models).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from hyundap.common.config import get_settings

if TYPE_CHECKING:  # 실행할 때는 불러오지 않는다(타입 표시용)
    from hyundap.common.schemas import Chunk

# ---------------------------------------------------------------------------
# 결과 데이터 (CLS-101 ~ CLS-105)
# ---------------------------------------------------------------------------


@dataclass
class EmbeddedChunk:
    """CLS-101. 임베딩이 끝난 청크 1개와 벡터·메타데이터·잘림 여부."""

    chunk: Chunk
    vector: list[float]
    metadata: dict
    truncated: bool = False


@dataclass
class EmbeddingFailure:
    """CLS-102. 임베딩·저장에 실패한 청크 1개와 실패 원인. message에는 비밀 값을 넣지 않는다."""

    chunk_id: str
    code: str
    message: str
    retryable: bool = False


@dataclass
class EmbeddingBatch:
    """CLS-103. embed_chunks의 결과 묶음(성공 목록 + 실패 목록)."""

    items: list[EmbeddedChunk] = field(default_factory=list)
    failures: list[EmbeddingFailure] = field(default_factory=list)


@dataclass
class EmbedStoreReport:
    """CLS-104. embed_and_store의 결과 보고서.

    status: "success"(실패 0) | "partial"(일부 실패) | "failed"(저장·건너뜀 모두 0)
    """

    document_id: str
    version_id: str
    company_id: str
    model_name: str
    total: int = 0
    skipped_existing: int = 0
    embedded: int = 0
    stored: int = 0
    truncated_count: int = 0
    failures: list[EmbeddingFailure] = field(default_factory=list)
    status: str = "failed"
    elapsed_sec: float = 0.0


@dataclass
class VectorHit:
    """CLS-105. 벡터 조회 결과 1개. score는 0~1 유사도이고, 문서 순서 조회에서는 None."""

    chunk_id: str
    text: str
    metadata: dict
    score: Optional[float] = None


# ---------------------------------------------------------------------------
# 오류 (CLS-106, CLS-107)
# ---------------------------------------------------------------------------


class EmbeddingError(Exception):
    """CLS-106. 임베딩 단계 오류. retryable이 True면 나중에 다시 시도할 수 있다."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class VectorStoreError(Exception):
    """CLS-107. 벡터 저장소 단계 오류."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# 팀 코드와 연결되는 자리 (이 아래만 나중에 고친다)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmbeddingSettings:
    """embedding이 쓰는 설정 3개. DB 접속 정보 등 다른 값은 읽지 않는다."""

    openai_api_key: str
    model_name: str
    chroma_path: str


def load_embedding_settings() -> EmbeddingSettings:
    """설정을 읽는다. (연결 자리 ①: hyundap/common/config.py)

    hyundap.common.config.get_settings() 한 곳에서만 읽는다(이슈 #141).
    .env 읽기·기본값·경로 계산은 common/config.py가 맡으므로 여기서 다시 하지 않는다.
    get_settings()가 실패하면 그 오류를 그대로 올린다(조용히 다른 값으로 대체하지 않는다).
    API 키가 없으면(None) 빈 문자열로 담고, NO_API_KEY 오류는 get_embedding_model에서 낸다.
    """
    s = get_settings()
    return EmbeddingSettings(
        openai_api_key=(s.openai_api_key or "").strip(),
        model_name=s.openai_embedding_model.strip(),
        chroma_path=str(s.chroma_db_path),
    )


def get_chunk_value(chunk: Chunk | dict, name: str, default: Any = None) -> Any:
    """청크에서 필드 값을 읽는다. (연결 자리 ②: hyundap/common/schemas.py의 Chunk)

    Chunk가 dataclass·pydantic 같은 객체여도, dict여도 같은 방식으로 읽는다.
    Chunk의 필드 이름이 명세(CLS-100)와 다르면 이 함수에서만 이름을 맞춘다.
    """
    if isinstance(chunk, dict):
        return chunk.get(name, default)
    return getattr(chunk, name, default)
