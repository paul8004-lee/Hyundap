"""ingest/pipeline.py — 파일 검증부터 청크 생성까지 실행하는 수집 흐름.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-218 ingest_document — 검증·로딩·정규화·청킹을 순서대로 실행하고 IngestResult를 돌려준다.
    FUN-219 build_ingest_report — 사용한 파서·청킹 설정·경고·구간/조각 수를 보고서로 구성한다.
    classify_normalized — (업로드에서 classify=True일 때) 정규화된 문서를 챗봇 분류기로 분류해 보고서 값을 만든다.
    apply_doc_type — (자동 분류 업로드에서 apply_classification=True일 때) 분류 결과로 최종 문서 유형을 정해 청크에 반영한다."""

import logging
import os
from importlib.metadata import version

from .chunker import chunk_document
from .loaders import load_document
from hyundap.common.config import get_settings
from hyundap.common.schemas import DEFAULT_DOC_TYPE, DOC_TYPE_CODES, HEADER_KEYS
from .models import (
    EMPTY_DOCUMENT,
    FILE_UNAVAILABLE,
    INVALID_DOC_TYPE,
    STATUS_FAILED,
    STATUS_SUCCESS,
    IngestError,
    IngestResult,
)
from .normalizer import normalize_document
from .validator import validate_upload

PARSERS = {".pdf": "pypdf", ".docx": "python-docx", ".pptx": "python-pptx", ".xlsx": "openpyxl"}
# 품질 판단이 아닌 진단용 임시 기준: 공백 제외 20자 미만.
SHORT_CHUNK_CHARS = 20
# 분류 결과에서 보고서로 옮기는 값 4개
CLASSIFY_KEYS = ("auto_doc_type", "doc_type_confidence", "company_match", "classify_status")

logger = logging.getLogger(__name__)


def ingest_document(file_path, upload_info, find_by_hash=None, file_hash=None, classify=False,
                    apply_classification=False, classification=None):
    """검증→로드→정규화→(분류)→청킹을 순서대로 실행하고 결과를 IngestResult로 돌려준다.

    classify=True이면 정규화 직후 문서 유형·회사 일치를 분류해 보고서에 넣는다(업로드 흐름만 켠다).
    classify만 켜면 분류 결과는 청크와 doc_type을 바꾸지 않으며, 분류가 실패해도 수집은 계속한다.
    apply_classification=True이면 분류까지 하고, 그 결과로 정한 최종 유형을 청킹 전에 넣어 모든 청크의 doc_type이 된다.
    classification은 이미 분류한 결과(재시도 때 재사용)이며, 있으면 분류기를 다시 부르지 않는다."""
    upload_info = dict(upload_info)
    if "doc_type" not in upload_info:
        upload_info["doc_type"] = DEFAULT_DOC_TYPE

    for key in HEADER_KEYS:
        if key not in upload_info:
            raise ValueError(f"upload_info에 {key} 가 없습니다")
        if key != "doc_type" and (not isinstance(upload_info[key], str) or not upload_info[key].strip()):
            raise ValueError(f"upload_info의 {key} 값이 올바르지 않습니다")

    try:
        if not isinstance(upload_info["doc_type"], str) or upload_info["doc_type"] not in DOC_TYPE_CODES:
            raise IngestError(INVALID_DOC_TYPE)
        validate_upload(file_path, upload_info["company_id"], find_by_hash, file_hash)
        doc = load_document(file_path, upload_info)
        doc = normalize_document(doc)
        if len(doc.sections) == 0:
            raise IngestError(EMPTY_DOCUMENT)
        if apply_classification:
            classification = apply_doc_type(doc, classification, upload_info["doc_type"])
        elif classify:
            classification = classify_normalized(doc)
        else:
            classification = {}
        chunks = chunk_document(doc)
        report = build_ingest_report(file_path, doc, chunks)
        report.update(classification)
    except IngestError as e:
        return IngestResult(status=STATUS_FAILED, error_code=e.code, error_message=e.message)
    except (FileNotFoundError, PermissionError):
        error = IngestError(FILE_UNAVAILABLE)
        return IngestResult(status=STATUS_FAILED, error_code=error.code, error_message=error.message)

    return IngestResult(status=STATUS_SUCCESS, chunks=chunks, report=report)


def classify_normalized(doc):
    """정규화된 문서를 챗봇 분류기(chatbot.doc_classifier)로 분류해 보고서에 넣을 값을 돌려준다.

    값: auto_doc_type·doc_type_confidence·company_match·classify_status. 어떤 오류도 올리지 않는다."""
    try:
        from hyundap.chatbot.doc_classifier import classify_common_document
        result = classify_common_document(doc)
    except Exception as error:
        # 작업 기록에는 원인을 저장하지 않으므로 로그로 남긴다. 오류 문구에는 키 일부가 섞일 수 있어 예외 이름만 쓴다.
        logger.warning("문서 자동 분류 실패: %s", type(error).__name__)
        return {"classify_status": "failed"}
    if result.status != "success":
        # 키 누락·모델 권한 문제 등으로 모든 업로드가 조용히 failed가 되지 않도록 원인(예외 이름)을 남긴다.
        logger.warning("문서 자동 분류 실패: %s", result.detail or "원인 미상")
    return {
        "auto_doc_type": result.auto_doc_type,
        "doc_type_confidence": result.confidence,
        "company_match": result.company_match,
        "classify_status": result.status,
    }


def apply_doc_type(doc, classification, current_doc_type):
    """분류 결과로 최종 문서 유형을 정해 doc.header에 넣고, 보고서에 넣을 값을 딕셔너리로 돌려준다.

    classification이 None이면 지금 분류하고, 딕셔너리가 있으면(재시도) 그대로 쓴다.
    최종 유형을 정하는 규칙은 챗봇의 decide_doc_type 한 곳에 있다. 청커가 doc.header에서 doc_type을 복사하므로
    청킹 전에 넣으면 모든 청크가 같은 유형을 가진다. 값: 분류 값 4개(있는 것만)와 doc_type·doc_type_fallback·doc_type_fallback_reason."""
    from hyundap.chatbot.doc_classifier import decide_doc_type
    if classification is None:
        classification = classify_normalized(doc)
    doc_type, fallback, reason = decide_doc_type(classification, current_doc_type)
    doc.header["doc_type"] = doc_type

    values = {}
    for key in CLASSIFY_KEYS:
        if key in classification:
            values[key] = classification[key]
    values["doc_type"] = doc_type
    values["doc_type_fallback"] = fallback
    values["doc_type_fallback_reason"] = reason
    return values


def build_ingest_report(file_path, doc, chunks):
    """사용한 파서와 청킹 설정, 경고, 구간·조각 개수를 딕셔너리로 모은다."""
    extension = os.path.splitext(file_path)[1].lower()
    parser = PARSERS[extension]
    settings = get_settings()
    report = {
        "parser": parser,
        "parser_version": version(parser),
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "warnings": doc.warnings,
        "section_count": len(doc.sections),
        "chunk_count": len(chunks),
        "short_chunk_count": sum(len(chunk.text.strip()) < SHORT_CHUNK_CHARS for chunk in chunks),
    }
    ocr_stats = getattr(doc, "ocr", None)
    if ocr_stats:   # OCR 상태와, 이미지·스캔 쪽 중 글자를 읽은 수·읽지 못한 수
        report["ocr_status"] = ocr_stats["status"]
        report["ocr_read_count"] = ocr_stats["read"]
        report["ocr_unread_count"] = ocr_stats["unread"]
    return report
