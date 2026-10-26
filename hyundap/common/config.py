"""common/config.py — 환경변수와 로컬 파일에서 공통 설정을 읽는 모듈.

담당: 이성민. 최초 설정을 캐시하며 API 키 누락 판정은 실제 호출 모듈이 담당한다.
환경변수 우선순위와 경로·숫자 기본값 처리만 수행하고 외부 API는 호출하지 않는다."""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_EMBEDDING_MODEL = 'text-embedding-3-small'
DEFAULT_CHAT_MODEL = 'gpt-5'
DEFAULT_MIN_SCORE = 0.3
DEFAULT_TOP_K_MAX = 80


@dataclass(frozen=True)
class Settings:
    """여러 모듈이 함께 읽는 변경 불가능한 설정 묶음.

    모델명·저장 경로·검색 제한값을 보관한다. API 키는 없을 수 있으며,
    실제 API를 호출하는 모듈이 필요 여부를 검사한다. 필드 값을 직접 수정할 수 없다."""
    openai_api_key: str | None
    openai_embedding_model: str
    openai_chat_model: str
    chroma_db_path: Path
    upload_dir: Path
    search_min_score: float
    search_top_k_max: int


def _read(name: str) -> str | None:
    """이름으로 환경변수를 읽고 공백을 제거한다.

    입력 name은 환경변수 이름이다. 값이 없거나 공백뿐이면 None을 반환한다."""
    value = os.environ.get(name, '').strip()
    return value or None


def _read_path(name: str, default: str) -> Path:
    """환경변수 또는 기본 경로를 절대 경로로 해석한다.

    입력 name은 환경변수 이름, default는 기본 경로 문자열이다.
    상대 경로는 레포 루트를 기준으로 바꾸고 pathlib.Path를 반환한다.
    폴더를 생성하거나 파일을 읽지는 않는다."""
    path = Path(_read(name) or default)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _read_number(name: str, default, cast, is_valid):
    """환경변수를 숫자로 바꾸고 허용 범위를 검사한다.

    name은 환경변수 이름, default는 대체값, cast는 int/float 같은 변환 함수,
    is_valid는 숫자의 허용 여부를 반환하는 함수다. 값 누락·변환 실패·범위 밖이면
    default를 반환한다. 변환 함수의 ValueError만 정상적인 입력 실패로 처리한다."""
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
    """프로세스 환경변수와 레포 루트 .env에서 공통 설정을 읽는다.

    반환값은 Settings다. 이미 설정된 환경변수가 .env보다 우선하고,
    잘못된 숫자·누락된 일반 설정에는 기본값을 사용한다. API 키가 없으면 None이다.
    최초 결과를 캐시하므로 설정을 다시 읽으려면 get_settings.cache_clear()를 호출한다.
    이 함수는 모델 생성·외부 API 호출·폴더 생성을 수행하지 않는다."""
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
