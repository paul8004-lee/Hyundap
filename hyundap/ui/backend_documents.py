"""B25/B26/B28: 제공된 문서·권한·업로드 함수를 화면 규격으로 연결한다.

SQL이나 원본 저장소를 직접 다루지 않는다. 실제 권한 검사는 auth가 수행하며,
문서 상세는 조회 전후에 같은 문서·버전을 재검증한다.
"""
from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from pathlib import Path

from .services import UIServiceError, to_plain, validate_upload_file


MAX_DETAIL_CITATIONS = 4
MAX_EXCERPT_CHARS = 1200


def _function(module_name, name):
    """호출할 때 담당 모듈을 불러온다. 미제공 함수와 누락 패키지를 구분한다."""
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        missing = error.name or ""
        if missing == module_name or module_name.startswith(missing + "."):
            raise UIServiceError(
                "BACKEND_NOT_CONNECTED", "문서 담당 모듈이 아직 준비되지 않았습니다. 최신 팀 코드를 확인해 주세요."
            ) from None
        if missing.split(".")[0] in {
            "sqlalchemy", "pymysql", "dotenv", "chromadb", "openai", "tiktoken",
            "langchain_core", "langchain_openai", "langchain_community",
            "langchain_text_splitters", "pypdf", "docx", "pptx",
        }:
            raise UIServiceError(
                "DEPENDENCY_MISSING", "문서 연결에 필요한 패키지가 없습니다. 루트 requirements.txt 설치를 확인해 주세요."
            ) from None
        raise
    function = getattr(module, name, None)
    if not callable(function):
        raise UIServiceError(
            "BACKEND_NOT_CONNECTED", "문서 담당 함수가 아직 준비되지 않았습니다. 최신 팀 코드를 확인해 주세요."
        )
    return function


def _document_row(rows, document_id):
    """조회 응답에서 요청한 문서 하나와 활성 버전을 확인한다."""
    rows = to_plain(rows)
    if not isinstance(rows, list):
        raise UIServiceError("INVALID_RESPONSE", "문서 조회 응답 형식을 확인해 주세요.")
    if not rows:
        raise UIServiceError("DOCUMENT_UNAVAILABLE", "문서를 찾을 수 없습니다. 다시 검색해 주세요.")
    if len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("document_id") != document_id:
        raise UIServiceError("INVALID_RESPONSE", "요청한 문서와 조회 결과가 일치하지 않습니다.")
    row = rows[0]
    if not row.get("company_id") or not row.get("active_version_id"):
        raise UIServiceError("DOCUMENT_UNAVAILABLE", "현재 공개된 문서를 확인할 수 없습니다. 다시 검색해 주세요.")
    return row


def _citations(hits, document):
    """모든 청크의 소속을 확인하고 앞부분의 실제 발췌만 표시용으로 만든다."""
    hits = to_plain(hits)
    if not isinstance(hits, list):
        raise UIServiceError("INVALID_RESPONSE", "문서 본문 응답 형식을 확인해 주세요.")
    expected = {
        "document_id": document["document_id"],
        "version_id": document["active_version_id"],
        "company_id": document["company_id"],
    }
    citations = []
    seen = set()
    for hit in hits:
        if not isinstance(hit, dict) or not isinstance(hit.get("metadata"), dict):
            raise UIServiceError("INVALID_RESPONSE", "문서 출처 정보를 확인할 수 없습니다.")
        metadata = hit["metadata"]
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise UIServiceError("DOCUMENT_CHANGED", "문서와 출처의 버전 또는 소속이 다릅니다. 다시 검색해 주세요.")
        chunk_id, text = hit.get("chunk_id"), hit.get("text")
        if not isinstance(chunk_id, str) or not chunk_id or chunk_id in seen or not isinstance(text, str):
            raise UIServiceError("INVALID_RESPONSE", "문서 출처 응답 형식을 확인해 주세요.")
        if metadata.get("chunk_id") != chunk_id or metadata.get("is_active") is not True:
            raise UIServiceError("DOCUMENT_CHANGED", "문서 출처의 공개 상태가 변경되었습니다. 다시 검색해 주세요.")
        seen.add(chunk_id)
        if len(citations) >= MAX_DETAIL_CITATIONS or not text.strip():
            continue
        citations.append({
            **expected,
            "number": len(citations) + 1,
            "chunk_id": chunk_id,
            "file_name": document.get("file_name"),
            "company_name": document.get("company_name"),
            "doc_type": document.get("doc_type"),
            "version_no": document.get("version_no"),
            "location_label": metadata.get("location_label") or "위치 정보 미제공",
            "excerpt": text[:MAX_EXCERPT_CHARS],
        })
    if not citations:
        raise UIServiceError("DOCUMENT_UNAVAILABLE", "문서의 공개된 본문을 찾을 수 없습니다. 다시 검색해 주세요.")
    return citations


def get_document(*, user, document_id, version_id=None):
    """권한이 있는 활성 문서의 정보와 최대 네 개의 실제 발췌를 반환한다."""
    read_documents = _function("hyundap.db.documents_repo", "get_documents_with_active_version")
    verify_access = _function("hyundap.auth.permissions", "verify_documents_access")
    read_chunks = _function("hyundap.embedding.vector_store", "get_document_chunks")
    document = _document_row(read_documents([document_id]), document_id)
    active_version = document["active_version_id"]
    pairs = [(document_id, active_version)]
    verify_access(user, pairs)
    if version_id is not None and version_id != active_version:
        raise UIServiceError("DOCUMENT_CHANGED", "문서 버전이 변경되었습니다. 다시 검색해 주세요.")
    citations = _citations(read_chunks(document_id, active_version, active_only=True), document)
    # 본문을 읽는 사이 계정·회사·문서 공개 상태가 바뀌었는지 다시 확인한다.
    verify_access(user, pairs)
    return {
        "document_id": document_id,
        "version_id": active_version,
        "company_id": document["company_id"],
        "company_name": document.get("company_name"),
        "file_name": document.get("file_name"),
        "doc_type": document.get("doc_type"),
        "version_no": document.get("version_no"),
        "indexed_at": None,
        "size_bytes": None,
        "location_label": citations[0]["location_label"],
        "excerpt": citations[0]["excerpt"],
        "citations": citations,
    }


def upload_document(*, user, file_path, company_id, doc_type=None):
    """DB 담당의 업로드 전체 흐름을 호출하고 게시 결과를 UI 상태로 변환한다."""
    from .backend_feedback import safe_upload_message

    submit = _function("hyundap.db.upload_service", "upload_document")
    path = validate_upload_file(file_path)
    metadata = {"company_id": company_id, "data_category": "operational"}
    if doc_type is not None:
        metadata["doc_type"] = doc_type
    result = to_plain(submit(
        actor=user,
        source_path=str(path),
        metadata=metadata,
    ))
    if not isinstance(result, dict) or result.get("status") not in {"success", "failed"}:
        raise UIServiceError("INVALID_RESPONSE", "업로드 처리 결과 형식을 확인해 주세요.")
    succeeded = result["status"] == "success"
    recovery = result.get("recovery_required", False)
    if type(recovery) is not bool or (succeeded and recovery):
        raise UIServiceError("INVALID_RESPONSE", "업로드의 완료·복구 상태가 일치하지 않습니다.")
    if succeeded and any(not result.get(key) for key in ("document_id", "version_id", "job_id")):
        raise UIServiceError("INVALID_RESPONSE", "업로드 결과의 문서·작업 식별자가 없습니다.")
    error_code = result.get("error_code")
    if error_code is not None and (not isinstance(error_code, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_code) is None):
        raise UIServiceError("INVALID_RESPONSE", "업로드 오류 코드 형식을 확인해 주세요.")
    if succeeded and error_code:
        raise UIServiceError("INVALID_RESPONSE", "업로드의 완료·오류 상태가 일치하지 않습니다.")
    response = {
        "document_id": result.get("document_id"),
        "version_id": result.get("version_id"),
        "job_id": result.get("job_id"),
        "status": "succeeded" if succeeded else "failed",
        "searchable": succeeded,
        "file_name": Path(path).name,
        "company_id": company_id,
        "doc_type": doc_type,
        "error_code": error_code if succeeded else error_code or "PROCESSING_FAILED",
        "recovery_required": recovery,
        "message": "문서 등록과 검색 공개가 완료되었습니다." if succeeded else safe_upload_message(error_code or "PROCESSING_FAILED", recovery_required=recovery),
    }
    if succeeded:
        response["stage"] = "complete"
    account = to_plain(user)
    if isinstance(account, Mapping) and account.get("company_id") == company_id and account.get("company_name"):
        response["company_name"] = account["company_name"]
    return response
