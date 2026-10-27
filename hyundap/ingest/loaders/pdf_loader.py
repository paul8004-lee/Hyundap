"""ingest/loaders/pdf_loader.py — PDF 페이지 텍스트를 공통 섹션으로 변환.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-208 load_pdf — PDF 페이지 텍스트를 공통 문서로 읽는다."""

import zlib

from langchain_community.document_loaders import PyPDFLoader
from pypdf.errors import PyPdfError

from .base import build_location_label
from hyundap.common.schemas import CommonDocument, Section
from ..models import CORRUPTED_FILE, IngestError

# 깨진 PDF를 읽을 때 pypdf가 내는 예외들 (Ascii85/UTF-16 해석 실패, 미지원 필터, 압축 오류 포함).
# 이 목록은 load() 호출 한 줄에만 적용하므로 패키지 미설치(ImportError)·코드 실수는 가리지 않는다.
PDF_READ_ERRORS = (PyPdfError, ValueError, UnicodeDecodeError, NotImplementedError, zlib.error, KeyError, IndexError)


def load_pdf(file_path, upload_info):
    """PDF를 페이지별로 읽어 페이지마다 섹션 1개를 가진 공통 문서 형식으로 바꾼다."""
    try:
        pages = PyPDFLoader(file_path).load()
    except PDF_READ_ERRORS:
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
