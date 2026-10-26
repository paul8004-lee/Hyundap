"""ingest/loaders/pdf_loader.py — PDF 페이지 텍스트를 공통 섹션으로 변환.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-208 load_pdf — PDF 페이지 텍스트를 공통 문서로 읽는다."""

from langchain_community.document_loaders import PyPDFLoader

from .base import build_location_label
from hyundap.common.schemas import CommonDocument, Section
from ..models import CORRUPTED_FILE, IngestError


def load_pdf(file_path, upload_info):
    """PDF를 페이지별로 읽어 페이지마다 섹션 1개를 가진 공통 문서 형식으로 바꾼다."""
    try:
        pages = PyPDFLoader(file_path).load()
    except Exception:
        raise IngestError(CORRUPTED_FILE)

    doc = CommonDocument(header=dict(upload_info))
    for page in pages:
        section_no = page.metadata["page"] + 1
        text = page.page_content
        if text.strip() == "":
            doc.warnings.append(f"p.{section_no}: 글자가 없어 건너뜀")
            continue
        doc.sections.append(
            Section(
                section_no=section_no,
                location_label=build_location_label("page", section_no),
                text=text,
            )
        )
    return doc
