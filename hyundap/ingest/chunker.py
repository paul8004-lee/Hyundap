"""ingest/chunker.py — 섹션 경계를 보존하며 공통 문서를 청크로 분할.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-215 to_langchain_documents — 공통 섹션을 위치 정보가 있는 LangChain Document로 바꾼다.
    FUN-216 build_chunk — 청크 ID·순번·출처 정보를 보존한 Chunk를 만든다.
    FUN-217 chunk_document — 섹션을 넘지 않도록 텍스트를 분할한다."""



from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from hyundap.common.config import get_settings
from hyundap.common.schemas import Chunk, make_chunk_id
from .models import EMPTY_DOCUMENT, IngestError


def to_langchain_documents(doc):
    """섹션 1개를 LangChain Document 1개로 바꾼다."""
    documents = []
    for section in doc.sections:
        metadata = dict(doc.header)
        metadata["section_no"] = section.section_no
        metadata["location_label"] = section.location_label
        documents.append(Document(page_content=section.text, metadata=metadata))
    return documents


def build_chunk(piece, chunk_index):
    """자른 조각 1개를 Chunk로 만든다."""
    text = piece.page_content.strip()
    if text == "":
        raise IngestError(EMPTY_DOCUMENT)

    meta = piece.metadata
    return Chunk(
        chunk_id=make_chunk_id(str(meta["version_id"]), chunk_index),
        document_id=str(meta["document_id"]),
        version_id=str(meta["version_id"]),
        company_id=str(meta["company_id"]),
        company_name=meta["company_name"],
        doc_type=meta["doc_type"],
        file_name=meta["file_name"],
        section_no=meta["section_no"],
        location_label=meta["location_label"],
        chunk_index=chunk_index,
        text=text,
    )


def chunk_document(doc, chunk_size=None, chunk_overlap=None):
    """정규화된 문서를 섹션마다 따로 잘라 Chunk 목록으로 돌려준다.

    크기·겹침을 넘기지 않으면 common 설정(CHUNK_SIZE·CHUNK_OVERLAP, 기본 1000·150)을 쓴다."""
    settings = get_settings()
    if chunk_size is None:
        chunk_size = settings.chunk_size
    if chunk_overlap is None:
        chunk_overlap = settings.chunk_overlap
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    pieces = splitter.split_documents(to_langchain_documents(doc))

    chunks = []
    for index, piece in enumerate(pieces):
        chunks.append(build_chunk(piece, index))
    return chunks