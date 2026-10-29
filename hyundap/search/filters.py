"""search/filters.py — 허용 회사·문서 유형을 검색 필터로 조합.

담당: 이준영
기준 명세: AI_문서검색_통합_기능명세서 (MOD-053, 제약 L55·L59)

함수:
    FUN-253 resolve_company_scope — auth 허용 회사와 사용자가 고른 회사를 맞춰 실제 검색 범위를 정한다.
    FUN-254 normalize_doc_types — 문서 유형의 공백·중복을 정리하고 허용 값(DOC_TYPE_CODES)인지 확인한다.
    FUN-255 build_search_filter — 회사·문서 유형·문서 ID 조건을 벡터 검색 조건(where)으로 만든다.
    (명세 외) pick_company_scope — 범위 계산만 하는 순수 함수 (auth 없이 시험 가능).

권한 판정(누가 어느 회사를 볼 수 있는가)은 auth 책임이다. search는 그 결과를 검색 조건으로 적용만 한다.
화면에서 고른 회사를 그대로 믿지 않고 반드시 허용 회사와 대조한다.
"""
from __future__ import annotations

from typing import Optional

from hyundap.common.schemas import DOC_TYPE_CODES

from .errors import SearchError, build_error, to_search_error


# ── 다른 폴더 호출 지점 ───────────────────────────────────────────────────
# 호출할 때 import한다. search를 import만 할 때는 DB 드라이버·Chroma가 필요 없고,
# 시험에서는 이 창구 함수만 가짜로 바꿔 끼우면 된다.
def _load_allowed_company_ids(user) -> set[str]:
    """auth.permissions.get_allowed_company_ids(user) → set[str] (DB_contract §3).

    허용 범위가 없으면 auth가 AccessError(ACCESS_DENIED)를 낸다."""
    from hyundap.auth.permissions import get_allowed_company_ids

    return get_allowed_company_ids(user)


def _build_where_filter(company_ids, doc_types, document_ids):
    """embedding.vector_store.build_where_filter 호출 (ENT-102). 공개(is_active) 청크 조건은 embedding이 붙인다."""
    from hyundap.embedding.vector_store import build_where_filter

    return build_where_filter(company_ids, doc_types=doc_types, document_ids=document_ids)


# ── 공개 함수 ─────────────────────────────────────────────────────────────
def pick_company_scope(allowed: Optional[set[str]], requested: Optional[list[str]]) -> list[str]:
    """허용 회사 목록과 사용자가 고른 회사 목록으로 실제 검색 범위를 정한다 (auth 호출 없는 순수 로직).

    허용 없음 → NO_COMPANY_SCOPE / 선택 없음 → 허용 전체 / 허용 밖 선택 → COMPANY_FORBIDDEN."""
    if not allowed:
        raise build_error("NO_COMPANY_SCOPE")
    if not requested:
        return sorted(allowed)  # set은 순서가 없어 정렬로 결과 순서를 고정한다
    allowed_set = set(allowed)
    if any(company_id not in allowed_set for company_id in requested):
        raise build_error("COMPANY_FORBIDDEN")
    return list(dict.fromkeys(requested))  # 중복 제거, 입력 순서 유지


def resolve_company_scope(user, requested_company_ids: Optional[list[str]] = None) -> list[str]:
    """FUN-253: auth 허용 회사 ∩ 사용자 선택 = 실제 검색할 회사 ID 목록."""
    try:
        allowed = _load_allowed_company_ids(user)
    except SearchError:
        raise
    except Exception as exc:
        raise to_search_error(exc) from exc
    return pick_company_scope(allowed, requested_company_ids)


def normalize_doc_types(doc_types: Optional[list[str]]) -> Optional[list[str]]:
    """FUN-254: 문서 유형 입력을 정리하고 허용 값인지 확인한다. 없으면 None(전체 유형)."""
    if not doc_types:
        return None
    cleaned: list[str] = []
    for value in doc_types:
        text = value.strip() if isinstance(value, str) else ""
        if text and text not in cleaned:
            cleaned.append(text)
    if any(value not in DOC_TYPE_CODES for value in cleaned):  # manual·policy·guide·other
        raise build_error("INVALID_DOC_TYPE")
    return cleaned or None


def build_search_filter(
    company_ids: list[str],
    doc_types: Optional[list[str]] = None,
    document_ids: Optional[list[str]] = None,
):
    """FUN-255: 회사·유형·문서 ID 조건을 벡터 검색 조건(where)으로 만든다. 공개 여부는 여기서 정하지 않는다."""
    if not company_ids:
        raise build_error("NO_COMPANY_SCOPE")
    try:
        return _build_where_filter(company_ids, doc_types, document_ids)
    except SearchError:
        raise
    except Exception as exc:
        raise to_search_error(exc) from exc  # VectorStoreError(NO_COMPANY_SCOPE)도 여기서 바뀜
