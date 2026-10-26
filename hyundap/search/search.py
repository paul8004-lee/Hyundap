"""search/search.py — 벡터 후보를 DB 정보와 결합해 검색 결과로 변환.

담당: 이준영
기준 명세: AI_문서검색_통합_기능명세서 (MOD-054, 제약 L50~L54·L56~L58·L60~L62)

함수:
    FUN-240 validate_query — 검색어 앞뒤 공백을 지우고 1~2000자인지 확인한다.
    FUN-241 validate_range — limit·top_k·min_score가 허용 범위인지 확인한다.
    FUN-242 get_search_settings — 공통 설정(common.config)에서 최소 점수·조회 상한을 읽는다.
    FUN-243 search — 화면용. 문서 단위 검색 결과(SearchResponse)를 반환한다.
    FUN-244 search_chunks — 챗봇용. 근거 청크(ChunkHit) 목록을 점수 높은 순으로 반환한다.
    FUN-245 run_chunk_search — 벡터 조회 → 최소 점수 → DB 공개 조건까지의 공통 단계.
    FUN-246 filter_by_min_score — 최소 점수보다 낮거나 점수가 없는 후보를 뺀다.
    FUN-247 fetch_document_info — DB 일괄 조회 행을 DocumentInfo로 바꾼다.
    FUN-248 is_visible — 공개·삭제·활성 버전·준비 완료·운영 자료·회사 조건을 확인한다.
    FUN-249 to_chunk_hit — 벡터 후보와 DB 정보를 ChunkHit으로 합친다.
    FUN-250 group_hits_by_document — 청크를 문서별로 묶고 점수순으로 정렬한다.
    FUN-251 to_citation — ChunkHit을 화면용 출처(SearchCitation)로 바꾼다.
    FUN-252 make_excerpt — 본문을 200자 발췌로 줄인다.

화면 검색과 챗봇 검색은 run_chunk_search를 함께 써서 권한·점수·공개 기준이 항상 같다.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from hyundap.common.config import get_settings

from .errors import build_error, is_db_error, to_search_error
from .filters import build_search_filter, normalize_doc_types, resolve_company_scope
from .models import (
    ChunkHit,
    DocumentInfo,
    SearchCitation,
    SearchResponse,
    SearchResult,
)

# 숫자 기준 (제약_운영 L50~L54, L60). 최소 점수(0.3)·조회 상한(80)은 common.config 설정값을 쓴다
MAX_QUERY_CHARS = 2000
MAX_LIMIT = 20
DEFAULT_LIMIT = 10
CHUNKS_PER_DOCUMENT = 4
DEFAULT_TOP_K = 10
EXCERPT_MAX_CHARS = 200


# ── 다른 폴더 호출 지점 ───────────────────────────────────────────────────
# 호출할 때 import한다. search를 import만 할 때는 OpenAI·Chroma·DB 드라이버가 필요 없고,
# 시험에서는 이 창구 함수만 가짜로 바꿔 끼우면 된다.
def _embed_query(query: str):
    """embedding.embedder.embed_query → 질문 벡터. OpenAI 일시 오류는 embedding이 최대 3번 재시도한다."""
    from hyundap.embedding.embedder import embed_query

    return embed_query(query)


def _query_vectors(vector, where, top_k: int):
    """embedding.vector_store.query_vectors → VectorHit 목록 (score = 1 − cosine 거리, 0~1, 높은 순)."""
    from hyundap.embedding.vector_store import query_vectors

    return query_vectors(vector, where, top_k=top_k)  # top_k는 키워드 전용 인자


def _load_document_rows(document_ids: list[str]) -> list[dict]:
    """db.documents_repo.get_documents_with_active_version → 11필드 dict 목록 (DB_contract §3)."""
    from hyundap.db.documents_repo import get_documents_with_active_version

    return get_documents_with_active_version(document_ids)


# ── 입력 검증·설정 ─────────────────────────────────────────────────────────
def validate_query(query) -> str:
    """FUN-240: 검색어 앞뒤 공백을 지우고 1~2000자인지 확인한다."""
    if not isinstance(query, str):
        raise build_error("EMPTY_QUERY")
    q = query.strip()
    if not q:
        raise build_error("EMPTY_QUERY")
    if len(q) > MAX_QUERY_CHARS:
        raise build_error("QUERY_TOO_LONG")
    return q


def validate_range(value, name: str, low, high):
    """FUN-241: 숫자가 허용 범위 안인지 확인한다.

    limit·chunks_per_document·top_k는 개수라 정수만(2.5 → INVALID_LIMIT), min_score는 소수도 허용한다.
    bool(True/False)은 파이썬에서 1/0으로 취급되므로 숫자로 보지 않는다."""
    if name == "min_score":
        code, allowed_types = "INVALID_MIN_SCORE", (int, float)
    else:
        code, allowed_types = "INVALID_LIMIT", (int,)
    if isinstance(value, bool) or not isinstance(value, allowed_types):
        raise build_error(code)
    if not (low <= value <= high):  # NaN도 여기서 걸린다
        raise build_error(code)
    return value


def get_search_settings() -> dict:
    """FUN-242: 검색 설정값을 읽는다. 없을 때의 기본값(0.3, 80)과 범위 검사는 common.config가 처리한다."""
    s = get_settings()
    return {"min_score": s.search_min_score, "top_k_max": s.search_top_k_max}


# ── 결과 거르기·가공 ───────────────────────────────────────────────────────
def filter_by_min_score(hits: list, min_score: float) -> list:
    """FUN-246: 점수가 없거나 기준보다 낮은 결과를 뺀다. 순서는 유지한다."""
    return [hit for hit in hits if hit.score is not None and hit.score >= min_score]


def is_visible(info: Optional[DocumentInfo], hit, company_ids: list[str]) -> bool:
    """FUN-248: 공개 조건 5가지 + 회사 범위·회사 일치를 모두 만족하는지 판단한다 (DB 기준)."""
    if info is None:
        return False
    meta = hit.metadata or {}
    return (
        info.publication_status == "published"
        and not info.deleted_at
        and info.active_version_id is not None
        and str(meta.get("version_id")) == str(info.active_version_id)
        and info.version_status == "ready"
        and info.data_category == "operational"
        and info.company_id in company_ids
        and str(meta.get("company_id")) == str(info.company_id)
    )


def to_chunk_hit(hit, info: DocumentInfo) -> ChunkHit:
    """FUN-249: 벡터 결과와 DB 문서 정보를 합친다.

    회사명·파일명·버전 번호는 최신인 DB 값, 섹션·위치 문구·순번은 ingest가 넣은 메타데이터 값을 쓴다
    (위치 문구를 search가 다시 만들지 않는다)."""
    meta = hit.metadata or {}
    return ChunkHit(
        chunk_id=hit.chunk_id,
        document_id=info.document_id,
        version_id=meta.get("version_id"),
        version_no=info.version_no,
        company_id=info.company_id,
        company_name=info.company_name,
        doc_type=info.doc_type,
        file_name=info.file_name,
        section_no=meta.get("section_no") if meta.get("section_no") != "" else None,  # Chroma는 None을 ""로 저장
        location_label=meta.get("location_label", ""),
        chunk_index=meta.get("chunk_index"),
        text=hit.text,
        score=hit.score,
    )


def make_excerpt(text: str, max_chars: int = EXCERPT_MAX_CHARS) -> str:
    """FUN-252: 연속 공백을 한 칸으로 줄이고, 길면 잘라 말줄임표를 붙인다."""
    s = re.sub(r"\s+", " ", text or "").strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip() + "…"


def to_citation(hit: ChunkHit) -> SearchCitation:
    """FUN-251: ChunkHit 1개를 화면용 출처 객체로 바꾼다."""
    return SearchCitation(
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        version_id=hit.version_id,
        company_name=hit.company_name,
        file_name=hit.file_name,
        location_label=hit.location_label,
        excerpt=make_excerpt(hit.text),
        score=hit.score,
    )


def group_hits_by_document(
    hits: list[ChunkHit],
    limit: int,
    chunks_per_document: int = CHUNKS_PER_DOCUMENT,
) -> list[SearchResult]:
    """FUN-250: 청크를 문서별로 묶고, 문서 점수(최고 청크 점수) 내림차순·파일명순으로 limit개 남긴다."""
    ordered = sorted(hits, key=lambda h: h.score, reverse=True)  # 안정 정렬: 같은 점수는 들어온 순서 유지
    groups: dict[str, list[ChunkHit]] = {}
    for hit in ordered:
        groups.setdefault(hit.document_id, []).append(hit)

    results: list[SearchResult] = []
    for chunks in groups.values():
        first = chunks[0]
        results.append(
            SearchResult(
                document_id=first.document_id,
                version_id=first.version_id,
                version_no=first.version_no,
                company_id=first.company_id,
                company_name=first.company_name,
                doc_type=first.doc_type,
                file_name=first.file_name,
                score=first.score,
                citations=[to_citation(c) for c in chunks[:chunks_per_document]],
            )
        )
    results.sort(key=lambda r: (-r.score, r.file_name or ""))
    return results[:limit]


# ── DB 조회·공통 검색 ──────────────────────────────────────────────────────
def fetch_document_info(document_ids: list[str]) -> dict[str, DocumentInfo]:
    """FUN-247: 문서 ID 목록으로 DB에서 문서·활성 버전·회사명을 한 번에 읽는다. DB에 없는 ID는 담지 않는다.

    DbError(DB 장애 등)는 SEARCH_UNAVAILABLE로 바꾸고, 예상하지 못한 오류는 그대로 올린다 (DB_contract §2)."""
    if not document_ids:
        return {}
    try:
        rows = _load_document_rows(document_ids)
    except Exception as exc:
        if is_db_error(exc):
            raise to_search_error(exc) from exc  # DbError → SEARCH_UNAVAILABLE
        raise  # 예상하지 못한 오류는 숨기지 않는다 (DB_contract §2)
    infos: dict[str, DocumentInfo] = {}
    for row in rows:
        info = DocumentInfo(
            document_id=row["document_id"],
            company_id=row["company_id"],
            company_name=row["company_name"],
            doc_type=row["doc_type"],
            publication_status=row["publication_status"],
            deleted_at=row.get("deleted_at"),
            active_version_id=row.get("active_version_id"),
            version_no=row.get("version_no"),
            version_status=row.get("version_status"),
            data_category=row.get("data_category"),
            file_name=row.get("file_name"),
        )
        infos[info.document_id] = info
    return infos


def run_chunk_search(
    query: str,
    company_ids: list[str],
    doc_types: Optional[list[str]],
    document_ids: Optional[list[str]],
    top_k: int,
    min_score: float,
) -> list[ChunkHit]:
    """FUN-245: 회사 범위가 정해진 뒤의 공통 검색 단계. search와 search_chunks가 함께 쓴다."""
    where = build_search_filter(company_ids, doc_types, document_ids)
    try:
        vector = _embed_query(query)
        raw = _query_vectors(vector, where, top_k)
    except Exception as exc:
        raise to_search_error(exc) from exc

    raw = filter_by_min_score(raw, min_score)
    if not raw:
        return []

    document_id_list = list(dict.fromkeys(h.metadata.get("document_id") for h in raw))
    infos = fetch_document_info(document_id_list)

    hits: list[ChunkHit] = []
    for hit in raw:
        info = infos.get(hit.metadata.get("document_id"))
        if is_visible(info, hit, company_ids):
            hits.append(to_chunk_hit(hit, info))
    return hits  # raw가 점수순이라 그대로 점수순


# ── 공개 함수 ─────────────────────────────────────────────────────────────
def search(
    user,
    query: str,
    company_ids: Optional[list[str]] = None,
    doc_types: Optional[list[str]] = None,
    limit: int = DEFAULT_LIMIT,
    chunks_per_document: int = CHUNKS_PER_DOCUMENT,
    min_score: Optional[float] = None,
) -> SearchResponse:
    """FUN-243: 화면용 통합 검색. 근거 청크를 찾아 문서 단위 결과로 묶어 반환한다.

    user: 서버가 만든 로그인 사용자 문맥(auth 형식 dict). search는 auth에 그대로 넘긴다.
    company_ids·doc_types: None이면 허용 범위 전체·전체 유형. limit: 결과 문서 수(1~20).
    결과가 없으면 오류가 아니라 빈 results를 돌려준다. 문제가 있으면 SearchError."""
    started = time.monotonic()
    q = validate_query(query)
    validate_range(limit, "limit", 1, MAX_LIMIT)
    validate_range(chunks_per_document, "limit", 1, MAX_LIMIT)
    if min_score is not None:
        validate_range(min_score, "min_score", 0.0, 1.0)
    settings = get_search_settings()
    top_k = min(limit * chunks_per_document, settings["top_k_max"])
    scope = resolve_company_scope(user, company_ids)
    types = normalize_doc_types(doc_types)

    hits = run_chunk_search(
        q,
        scope,
        types,
        None,
        top_k,
        min_score if min_score is not None else settings["min_score"],
    )
    results = group_hits_by_document(hits, limit, chunks_per_document)
    return SearchResponse(
        query=q,
        results=results,
        total_hits=len(hits),
        company_ids=scope,
        doc_types=types,
        elapsed_sec=time.monotonic() - started,
    )


def search_chunks(
    user,
    query: str,
    company_ids: Optional[list[str]] = None,
    doc_types: Optional[list[str]] = None,
    document_ids: Optional[list[str]] = None,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
) -> list[ChunkHit]:
    """FUN-244: 챗봇용 청크 검색. 권한·조건·공개 조건을 적용해 질문과 가까운 청크를 점수순으로 반환한다.

    document_ids: 특정 문서로 좁혀 질문할 때. None이면 제한 없음, 빈 목록이면 고른 문서가 없으므로 빈 결과.
    top_k: 기본 10, 상한은 설정값(기본 80). min_score: 없으면 설정값(기본 0.3).
    문서로 묶지 않고 ChunkHit 목록을 그대로 준다."""
    q = validate_query(query)
    settings = get_search_settings()
    if top_k is None:
        top_k = DEFAULT_TOP_K
    validate_range(top_k, "top_k", 1, settings["top_k_max"])
    if min_score is None:
        min_score = settings["min_score"]
    validate_range(min_score, "min_score", 0.0, 1.0)
    scope = resolve_company_scope(user, company_ids)  # 권한·세션 확인은 빈 결과보다 먼저 한다
    types = normalize_doc_types(doc_types)
    if document_ids is not None:
        # 공백·중복을 정리한 뒤 남는 문서가 없으면 빈 결과. 빈 조건을 넘기면 전체 검색으로 넓어지기 때문이다
        document_ids = list(dict.fromkeys(str(d).strip() for d in document_ids if d is not None and str(d).strip()))
        if not document_ids:
            return []
    return run_chunk_search(q, scope, types, document_ids, top_k, min_score)
