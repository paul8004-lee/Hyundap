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

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

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

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_CHROMA_DB_PATH = "data/chroma_db"
_REPO_ROOT = Path(__file__).resolve().parents[2]  # hyundap/embedding/models.py -> 저장소 맨 위
_log = logging.getLogger(__name__)
_warned_fallback = False


@dataclass(frozen=True)
class EmbeddingSettings:
    """embedding이 쓰는 설정 3개. DB 접속 정보 등 다른 값은 읽지 않는다."""

    openai_api_key: str
    model_name: str
    chroma_path: str


def load_embedding_settings() -> EmbeddingSettings:
    """설정을 읽는다. (연결 자리 ①: hyundap/common/config.py)

    1) hyundap.common.config.get_settings()가 있으면 그 값을 쓴다(속성·키 이름이 같을 때).
    2) 없거나 필요한 값이 비어 있으면 환경변수(.env 포함)로 채운다.
    반환 형식(EmbeddingSettings)은 바뀌지 않으므로 호출하는 쪽은 고칠 것이 없다.
    """
    global _warned_fallback
    team: Any = None
    try:  # common.config를 못 불러와도 멈추지 않고 환경변수로 대체한다(단, 조용히 넘어가지는 않는다).
        from hyundap.common.config import get_settings

        team = get_settings()
    except Exception as exc:  # noqa: BLE001 - 모듈 없음·python-dotenv 미설치·잘못된 실행 위치 등
        team = None
        if not _warned_fallback:  # 같은 경고가 반복되지 않게 한 번만 남긴다.
            _warned_fallback = True
            _log.warning(
                "hyundap.common.config를 불러오지 못해 환경변수로 대체합니다(%s). "
                "저장소 맨 위 폴더에서 실행했는지, python-dotenv가 설치돼 있는지 확인하세요.",
                type(exc).__name__,
            )

    def from_team(*names: str) -> str:
        for name in names:
            value = team.get(name) if isinstance(team, dict) else getattr(team, name, None)
            if value:
                return str(value)
        return ""

    _load_dotenv_once()
    api_key = from_team("OPENAI_API_KEY", "openai_api_key") or os.environ.get("OPENAI_API_KEY", "")
    model = (
        from_team("OPENAI_EMBEDDING_MODEL", "openai_embedding_model", "embedding_model")
        or os.environ.get("OPENAI_EMBEDDING_MODEL", "")
        or DEFAULT_EMBEDDING_MODEL
    )
    path = (
        from_team("CHROMA_DB_PATH", "chroma_db_path", "chroma_path")
        or os.environ.get("CHROMA_DB_PATH", "")
        or DEFAULT_CHROMA_DB_PATH
    )
    chroma_path = Path(path)
    if not chroma_path.is_absolute():
        chroma_path = _REPO_ROOT / chroma_path
    return EmbeddingSettings(api_key.strip(), model.strip(), str(chroma_path))


_dotenv_loaded = False


def _load_dotenv_once() -> None:
    """저장소 맨 위 .env를 한 번만 읽는다(이미 있는 환경변수는 덮어쓰지 않음). dotenv가 없으면 건너뛴다."""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    try:
        from dotenv import load_dotenv

        load_dotenv(_REPO_ROOT / ".env", override=False)
    except Exception:  # noqa: BLE001 - python-dotenv 미설치 등
        pass


def get_chunk_value(chunk: Chunk | dict, name: str, default: Any = None) -> Any:
    """청크에서 필드 값을 읽는다. (연결 자리 ②: hyundap/common/schemas.py의 Chunk)

    Chunk가 dataclass·pydantic 같은 객체여도, dict여도 같은 방식으로 읽는다.
    Chunk의 필드 이름이 명세(CLS-100)와 다르면 이 함수에서만 이름을 맞춘다.
    """
    if isinstance(chunk, dict):
        return chunk.get(name, default)
    return getattr(chunk, name, default)
