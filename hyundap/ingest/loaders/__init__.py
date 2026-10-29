"""ingest/loaders/__init__.py — 확장자에 맞는 문서 로더 선택.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-207 load_document — 파일 확장자에 맞는 로더를 선택한다."""


import os

from .docx_loader import load_docx
from .pdf_loader import load_pdf
from .pptx_loader import load_pptx
from .xlsx_loader import load_xlsx
from ..models import UNSUPPORTED_FORMAT, IngestError

LOADERS = {
    ".pdf": load_pdf,
    ".docx": load_docx,
    ".pptx": load_pptx,
    ".xlsx": load_xlsx,
}


def load_document(file_path, upload_info):
    """확장자에 맞는 로더를 골라 호출하고 공통 문서 형식을 돌려준다."""
    extension = os.path.splitext(file_path)[1].lower()
    if extension not in LOADERS:
        raise IngestError(UNSUPPORTED_FORMAT)
    loader = LOADERS[extension]
    return loader(file_path, upload_info)