"""ingest/pipeline.py — 파일 검증부터 청크 생성까지 실행하는 수집 흐름.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

구현 예정 함수:
    FUN-218 ingest_document — 검증·로딩·정규화·청킹을 순서대로 실행한다.
    FUN-219 build_ingest_report — 수집 처리 결과와 경고를 보고서로 구성한다.
    
파일 검증(6단계)과 처리 보고서(7단계)는 아직 추가 전"""

import os
from importlib.metadata import version

from .chunker import CHUNK_OVERLAP, CHUNK_SIZE, chunk_document
from .loaders import load_document
from hyundap.common.schemas import DEFAULT_DOC_TYPE, HEADER_KEYS
from .models import (
    EMPTY_DOCUMENT,
    STATUS_FAILED,
    STATUS_SUCCESS,
    IngestError,
    IngestResult,
)
from .normalizer import normalize_document
from .validator import validate_upload

PARSERS = {".pdf": "pypdf", ".docx": "python-docx", ".pptx": "python-pptx"}


def ingest_document(file_path, upload_info, find_by_hash=None):
    """검증→로드→정규화→청킹을 순서대로 실행하고 결과를 IngestResult로 돌려준다."""
    upload_info = dict(upload_info)
    if not upload_info.get("doc_type"):
        upload_info["doc_type"] = DEFAULT_DOC_TYPE

    for key in HEADER_KEYS:
        if key not in upload_info:
            raise ValueError(f"upload_info에 {key} 가 없습니다")

    try:
        validate_upload(file_path, upload_info["company_id"], find_by_hash)
        doc = load_document(file_path, upload_info)
        doc = normalize_document(doc)
        if len(doc.sections) == 0:
            raise IngestError(EMPTY_DOCUMENT)
        chunks = chunk_document(doc)
        report = build_ingest_report(file_path, doc, chunks)
    except IngestError as e:
        return IngestResult(status=STATUS_FAILED, error_code=e.code, error_message=e.message)

    return IngestResult(status=STATUS_SUCCESS, chunks=chunks, report=report)


def build_ingest_report(file_path, doc, chunks):
    """사용한 파서와 청킹 설정, 경고, 구간·조각 개수를 딕셔너리로 모은다."""
    extension = os.path.splitext(file_path)[1].lower()
    parser = PARSERS[extension]
    return {
        "parser": parser,
        "parser_version": version(parser),
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "warnings": doc.warnings,
        "section_count": len(doc.sections),
        "chunk_count": len(chunks),
    }