"""common/schemas.py

Owner: 이성민
Spec: AI_문서검색_통합_기능명세서

Classes:
    Section
    CLS-042 CommonDocument
    CLS-100 Chunk

Shared data formats passed between ingest, embedding, search and chatbot.
Change only after telling the team (see 작업폴더_구조.md).
"""
from dataclasses import dataclass, field

# Keys every CommonDocument.header must carry.
HEADER_KEYS = ('document_id', 'version_id', 'company_id', 'company_name', 'doc_type', 'file_name')


@dataclass
class Section:
    """One page (PDF), heading block (DOCX) or slide (PPTX)."""
    section_no: int
    location_label: str  # e.g. 'p.3', '섹션 2 (개요)', '슬라이드 5'
    text: str


@dataclass
class CommonDocument:
    """Loader output in the same shape for every file format: header + sections."""
    header: dict
    sections: list[Section] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self):
        missing = [key for key in HEADER_KEYS if key not in self.header]
        if missing:
            raise ValueError(f'CommonDocument.header is missing keys: {", ".join(missing)}')


@dataclass
class Chunk:
    """One chunk made by ingest and embedded by embedding."""
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
    """Build a chunk id unique within a version, e.g. 'v1:00003'."""
    return f'{version_id}:{chunk_index:05d}'
