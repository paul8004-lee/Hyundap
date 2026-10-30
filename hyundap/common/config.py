"""common/config.py — 환경변수와 로컬 파일에서 공통 설정을 읽는 모듈.

담당: 이성민. 최초 설정을 캐시하며 API 키 누락 판정은 실제 호출 모듈이 담당한다.
환경변수 우선순위와 경로·숫자 기본값 처리만 수행하고 외부 API는 호출하지 않는다."""
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_EMBEDDING_MODEL = 'text-embedding-3-large'
DEFAULT_CHAT_MODEL = 'gpt-5'
DEFAULT_MIN_SCORE = 0.3
DEFAULT_TOP_K_MAX = 80
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 150
DEFAULT_SESSION_TTL_MINUTES = 480
# 문서 하나에서 OCR로 읽는 쪽·그림 수 상한. 쪽당 2~5초(CPU 4코어 실측)라 50쪽이면 최악 2~4분이다.
# 그림은 장당 약 0.5초라 100개여도 1분 안팎이다. 업로드는 한 요청 안에서 끝날 때까지 이어지므로 길게 잡지 않는다.
DEFAULT_OCR_MAX_PAGES = 50
DEFAULT_OCR_MAX_FIGURES = 100


@dataclass(frozen=True)
class Settings:
    """여러 모듈이 함께 읽는 변경 불가능한 설정 묶음.

    모델명·저장 경로·검색 제한값·청크 크기·OCR 설정(상한·스레드 수·예열)·문서 자동 분류 사용 여부를 보관한다. API 키는 없을 수 있으며,
    실제 API를 호출하는 모듈이 필요 여부를 검사한다. 필드 값을 직접 수정할 수 없다."""
    openai_api_key: str | None
    openai_embedding_model: str
    openai_chat_model: str
    chroma_db_path: Path
    upload_dir: Path
    search_min_score: float
    search_top_k_max: int
    chunk_size: int
    chunk_overlap: int
    session_ttl_minutes: int
    ocr_max_pages: int = DEFAULT_OCR_MAX_PAGES      # 0이면 글자 없는 쪽 전체 OCR을 하지 않는다
    ocr_max_figures: int = DEFAULT_OCR_MAX_FIGURES  # 0이면 쪽·슬라이드 안의 그림 OCR을 하지 않는다
    ocr_threads: int = field(default_factory=lambda: default_ocr_threads())   # OCR이 쓰는 CPU 스레드 수(get_settings와 같은 기본값)
    ocr_warmup: bool = True                         # 서버 시작 때 OCR 엔진을 미리 불러올지
    doc_classify: bool = False                      # 업로드 중 문서 유형·회사 일치 자동 분류(OpenAI)를 할지


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


def _read_bool(name: str, default: bool) -> bool:
    """환경변수를 켜짐·꺼짐으로 읽는다.

    0·false·no·off(대소문자 무관)는 False, 1·true·yes·on은 True다. 값이 없거나 그 밖의 글자면 default를 반환한다."""
    value = (_read(name) or '').lower()
    if value in ('0', 'false', 'no', 'off'):
        return False
    if value in ('1', 'true', 'yes', 'on'):
        return True
    return default


def default_ocr_threads() -> int:
    """OCR 스레드 수 기본값. 코어가 3개 이상이면 하나를 남겨 같은 서버의 검색·챗봇 요청과 덜 경쟁하게 한다.

    4코어 실측으로 4개는 쪽당 2.6초, 3개는 3.1초(+18%), 2개는 4.0초, 1개는 6.5초라 코어가 2개 이하이면 전부 쓴다."""
    cores = os.cpu_count() or 1
    return cores if cores <= 2 else cores - 1


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스 환경변수와 레포 루트 .env에서 공통 설정을 읽는다.

    반환값은 Settings다. 이미 설정된 환경변수가 .env보다 우선하고,
    잘못된 숫자·누락된 일반 설정에는 기본값을 사용한다. API 키가 없으면 None이다.
    청크 겹침은 청크 크기보다 작아야 하며, 그렇지 않으면 겹침 기본값을 쓴다.
    최초 결과를 캐시하므로 설정을 다시 읽으려면 get_settings.cache_clear()를 호출한다.
    이 함수는 모델 생성·외부 API 호출·폴더 생성을 수행하지 않는다."""
    load_dotenv(PROJECT_ROOT / '.env', override=False)
    chunk_size = _read_number('CHUNK_SIZE', DEFAULT_CHUNK_SIZE, int, lambda v: v >= 1)
    # 겹침이 청크 크기 이상이면 텍스트 분할기가 오류를 내므로 청크 크기보다 작은 값만 받는다.
    overlap_default = DEFAULT_CHUNK_OVERLAP if DEFAULT_CHUNK_OVERLAP < chunk_size else 0
    return Settings(
        openai_api_key=_read('OPENAI_API_KEY'),
        openai_embedding_model=_read('OPENAI_EMBEDDING_MODEL') or DEFAULT_EMBEDDING_MODEL,
        openai_chat_model=_read('OPENAI_CHAT_MODEL') or DEFAULT_CHAT_MODEL,
        chroma_db_path=_read_path('CHROMA_DB_PATH', 'data/chroma_db'),
        upload_dir=_read_path('UPLOAD_DIR', 'data/uploads'),
        search_min_score=_read_number('SEARCH_MIN_SCORE', DEFAULT_MIN_SCORE, float, lambda v: 0 <= v <= 1),
        search_top_k_max=_read_number('SEARCH_TOP_K_MAX', DEFAULT_TOP_K_MAX, int, lambda v: v >= 1),
        chunk_size=chunk_size,
        chunk_overlap=_read_number('CHUNK_OVERLAP', overlap_default, int, lambda v: 0 <= v < chunk_size),
        # 로그인 세션 유효 시간(분). 5분~7일 밖이거나 숫자가 아니면 기본 480분을 쓴다.
        session_ttl_minutes=_read_number('SESSION_TTL_MINUTES', DEFAULT_SESSION_TTL_MINUTES, int,
                                         lambda v: 5 <= v <= 10080),
        # OCR 설정. 상한이 0이면 해당 OCR을 끄고, 숫자가 아니거나 범위 밖이면 기본값을 쓴다.
        ocr_max_pages=_read_number('OCR_MAX_PAGES', DEFAULT_OCR_MAX_PAGES, int, lambda v: 0 <= v <= 100000),
        ocr_max_figures=_read_number('OCR_MAX_FIGURES', DEFAULT_OCR_MAX_FIGURES, int, lambda v: 0 <= v <= 100000),
        ocr_threads=_read_number('OCR_THREADS', default_ocr_threads(), int, lambda v: 1 <= v <= 256),
        ocr_warmup=_read_bool('OCR_WARMUP', True),
        # 업로드마다 외부 API를 한 번 더 부르므로 기본은 끄고, 1·true 등으로 명시할 때만 켠다.
        doc_classify=_read_bool('DOC_CLASSIFY', False),
    )
