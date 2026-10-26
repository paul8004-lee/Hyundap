"""common/config.py

Owner: 이성민
Spec: AI_문서검색_통합_기능명세서

Functions:
    get_settings

Reads settings from the process environment or the project .env.
Missing values fall back to defaults; a missing API key stays None so each
caller can raise its own error (NO_API_KEY, CONFIG_MISSING).
"""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_EMBEDDING_MODEL = 'text-embedding-3-small'
DEFAULT_CHAT_MODEL = 'gpt-4o-mini'
DEFAULT_MIN_SCORE = 0.3
DEFAULT_TOP_K_MAX = 80


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_embedding_model: str
    openai_chat_model: str
    chroma_db_path: Path
    upload_dir: Path
    search_min_score: float
    search_top_k_max: int


def _read(name: str) -> str | None:
    value = os.environ.get(name, '').strip()
    return value or None


def _read_path(name: str, default: str) -> Path:
    path = Path(_read(name) or default)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _read_number(name: str, default, cast, is_valid):
    value = _read(name)
    if value is None:
        return default
    try:
        number = cast(value)
    except ValueError:
        return default
    return number if is_valid(number) else default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the shared settings. Read once and reused; call get_settings.cache_clear() to reload."""
    load_dotenv(PROJECT_ROOT / '.env', override=False)
    return Settings(
        openai_api_key=_read('OPENAI_API_KEY'),
        openai_embedding_model=_read('OPENAI_EMBEDDING_MODEL') or DEFAULT_EMBEDDING_MODEL,
        openai_chat_model=_read('OPENAI_CHAT_MODEL') or DEFAULT_CHAT_MODEL,
        chroma_db_path=_read_path('CHROMA_DB_PATH', 'data/chroma_db'),
        upload_dir=_read_path('UPLOAD_DIR', 'data/uploads'),
        search_min_score=_read_number('SEARCH_MIN_SCORE', DEFAULT_MIN_SCORE, float, lambda v: 0 <= v <= 1),
        search_top_k_max=_read_number('SEARCH_TOP_K_MAX', DEFAULT_TOP_K_MAX, int, lambda v: v >= 1),
    )
