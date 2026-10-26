"""common/schemas.py — 수집·임베딩·검색·챗봇 사이에 전달하는 공통 데이터 형식.

담당: 이성민. Section, CommonDocument(CLS-042), Chunk(CLS-100), 문서 유형 목록(DOC_TYPES)을 제공한다.
필드명은 모듈 간 계약이므로 자료형 변경 시 팀에 공유한다."""
from dataclasses import dataclass, field

# 모든 CommonDocument.header에 반드시 포함해야 하는 키다.
HEADER_KEYS = ('document_id', 'version_id', 'company_id', 'company_name', 'doc_type', 'file_name')

# 문서 유형 (코드, 화면 표시 이름). DB_contract.md 채택안이며 db.schema의 CHECK 제약과 같아야 한다.
# 회사 목록은 DB companies 테이블·auth 권한 함수가 제공하므로 여기에 두지 않는다.
DOC_TYPES = (
    ('manual', '매뉴얼'),
    ('policy', '규정'),
    ('guide', '가이드'),
    ('other', '기타'),
)
DOC_TYPE_CODES = frozenset(code for code, _ in DOC_TYPES)
DEFAULT_DOC_TYPE = 'other'  # 미분류 문서, DB 기본값과 같다.


@dataclass
class Section:
    """문서의 페이지·제목 구간·슬라이드 한 단위.

    section_no는 위치 번호, location_label은 p.3 같은 표시 문구,
    text는 추출한 본문이다. 파일 형식이 달라도 이 자료형으로 전달한다."""
    section_no: int
    location_label: str  # 표시 예: 'p.3', '섹션 2 (개요)', '슬라이드 5'
    text: str


@dataclass
class CommonDocument:
    """확장자별 로더가 공통으로 반환하는 문서.

    header에는 문서·버전·회사·유형·파일명 식별 정보가 들어간다.
    sections는 Section 목록, warnings는 처리 경고 목록이다.
    필수 헤더 키가 없으면 생성 시 ValueError가 발생한다. 목록은 문서마다 따로 생성된다."""
    header: dict
    sections: list[Section] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self):
        """데이터 객체 생성 직후 필수 헤더 키의 존재를 검사한다.

        필드 값의 타입이나 권한까지 검증하는 함수는 아니다.
        누락된 키가 있으면 ValueError로 잘못된 공통 문서의 전달을 막는다."""
        missing = [key for key in HEADER_KEYS if key not in self.header]
        if missing:
            raise ValueError(f'CommonDocument.header is missing keys: {", ".join(missing)}')


@dataclass
class Chunk:
    """수집 모듈이 만들고 임베딩·검색 모듈이 전달하는 청크 한 건.

    text는 분할된 본문이며 chunk_id와 chunk_index는 버전 안의 청크를 식별한다.
    문서·회사·버전·파일명·위치 필드는 출처 표시와 검색 범위 확인에 사용한다.
    이 객체 자체는 접근 권한이나 데이터베이스 저장을 수행하지 않는다."""
    chunk_id: str
    document_id: str
    version_id: str
    company_id: str
    company_name: str
    doc_type: str
    file_name: str
    section_no: int
    location_label: str
    chunk_index: int
    text: str


def make_chunk_id(version_id: str, chunk_index: int) -> str:
    """버전 ID와 문서 안 순번으로 청크 ID 문자열을 만든다.

    입력 version_id는 문자열, chunk_index는 정수다.
    예: make_chunk_id("v1", 3)은 "v1:00003"을 반환한다.
    최소 5자리로 표시할 뿐 순번 범위·UUID 유효성 검증은 하지 않으므로 호출자가 검사한다."""
    return f'{version_id}:{chunk_index:05d}'
