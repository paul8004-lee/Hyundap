"""embedding/vector_store.py - 벡터 저장·조회 (수업 범위 리팩토링판)

Owner: 박영준
Spec: AI_문서검색_통합_기능명세서 (MOD-102)

수업 범위: langchain_chroma.Chroma (collection_name, collection_metadata={"hnsw:space": "cosine"},
          persist_directory, get, delete, similarity_search 계열의 relevance score, _collection.count()),
          메타데이터 filter(where), 일반 파이썬(dict·list·sorted·zip).
뺀 것: chromadb.PersistentClient 직접 사용, re(정규식).

Functions:
    FUN-310 collection_name_for_model   FUN-311 get_vector_store    FUN-312 filter_new_chunks
    FUN-313 upsert_chunks               FUN-314 build_where_filter  FUN-315 query_vectors
    FUN-316 activate_version            FUN-317 delete_vectors      FUN-318 get_version_chunk_ids
    FUN-319 get_document_chunks         FUN-320 check_vector_store_health
"""
import time
from typing import Any, Optional

# 수업에서 배운 LangChain의 Chroma 벡터 DB 클래스
from langchain_chroma import Chroma

from .models import (
    VectorHit,
    VectorStoreError,
    get_chroma_path,
    get_chunk_value,
    get_model_name,
)

# 저장 메타데이터 키 규약 (값은 str·int·float·bool만, None 불가)
# 저장 메타데이터 키 규약. build_chunk_metadata가 만드는 딕셔너리의 키 목록(None 값 불가)
METADATA_KEYS = (
    "chunk_id", "document_id", "version_id", "company_id", "company_name",
    "doc_type", "file_name", "section_no", "location_label", "chunk_index",
    "embedding_model", "truncated", "is_active", "embedded_at",
)

# 컬렉션 이름 앞부분. 모델마다 컬렉션을 따로 두어 서로 다른 모델 벡터가 섞이지 않게 한다
COLLECTION_PREFIX = "chunks__"
# get·update·delete를 한 번에 500개씩 나눠 부르는 크기
_BATCH = 500  # get·update·delete를 나눠 부르는 크기
# 같은 (경로, 컬렉션)이면 Chroma를 다시 열지 않고 재사용하기 위한 보관함
_stores: dict[tuple, Any] = {}  # (저장 경로, 컬렉션 이름) -> Chroma. 같은 설정이면 재사용한다.


# ---------------------------------------------------------------------------
# 컬렉션 준비
# ---------------------------------------------------------------------------


# FUN-310. 모델 이름으로 컬렉션 이름(chunks__모델)을 만든다. Chroma가 허용하지 않는 문자는 -로 바꾼다
def collection_name_for_model(model_name: str) -> str:
    """FUN-310. 모델 이름으로 컬렉션 이름(chunks__<모델>)을 만든다. 허용 밖 문자는 -로 바꾼다."""
    cleaned = ""
    for ch in (model_name or "").strip():
        cleaned += ch if (ch.isascii() and ch.isalnum()) or ch in "._-" else "-"
    cleaned = cleaned.strip("._-") or "default"
    return (COLLECTION_PREFIX + cleaned)[:512]


# FUN-311. 모델별 Chroma 저장소를 열거나 만든다(cosine 거리). 이전 이름: get_collection
def get_vector_store(model_name: Optional[str] = None):
    """FUN-311. 모델별 Chroma 저장소(cosine)를 열거나 만든다. 같은 설정이면 재사용한다.

    질문·청크 임베딩은 embedder가 따로 만들어 넣으므로 embedding_function은 지정하지 않는다.
    """
    try:
        path = get_chroma_path()
        name = collection_name_for_model(get_model_name(model_name))
        if (path, name) not in _stores:
            _stores[(path, name)] = Chroma(
                collection_name=name,
                persist_directory=path,
                # 수업에서 배운 설정. 유사도를 cosine 거리로 계산한다
                collection_metadata={"hnsw:space": "cosine"},
            )
        return _stores[(path, name)]
    except Exception as exc:
        raise VectorStoreError("STORE_UNAVAILABLE", f"벡터 저장소를 열 수 없습니다: {type(exc).__name__}") from None


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------


# FUN-312. 이미 저장된 chunk_id는 빼고 새 청크만 골라낸다. 남의 청크를 덮어쓰지 않도록 충돌을 검사한다
def filter_new_chunks(chunks: list) -> list:
    """FUN-312. 이미 저장된 chunk_id는 빼고 새 청크만 골라낸다(입력 순서 유지).

    같은 chunk_id가 다른 회사로 저장돼 있으면 COMPANY_MISMATCH,
    같은 회사인데 다른 문서·버전이면 CHUNK_ID_CONFLICT로 멈춘다(남의 청크를 덮어쓰지 않기 위해).
    """
    if not chunks:
        return []
    store = get_vector_store()
    ids = [str(get_chunk_value(c, "chunk_id", "")) for c in chunks]
    existing: dict[str, dict] = {}
    try:
        for start in range(0, len(ids), _BATCH):
            # 저장소에서 이 ID들이 이미 있는지 메타데이터와 함께 조회
            found = store.get(ids=ids[start:start + _BATCH], include=["metadatas"])
            for cid, meta in zip(found["ids"], found["metadatas"] or []):
                existing[cid] = meta or {}
    except Exception as exc:
        raise VectorStoreError("STORE_UNAVAILABLE", f"저장된 청크를 조회할 수 없습니다: {type(exc).__name__}") from None

    new_chunks = []
    for chunk, cid in zip(chunks, ids):
        meta = existing.get(cid)
        if meta is None:
            new_chunks.append(chunk)
            continue
        if str(meta.get("company_id", "")) != str(get_chunk_value(chunk, "company_id", "")):
            # 같은 ID인데 회사가 다르면 다른 회사 자료를 건드릴 수 있어 즉시 중단
            raise VectorStoreError("COMPANY_MISMATCH", f"같은 chunk_id가 다른 회사로 저장돼 있습니다: {cid}")
        if (str(meta.get("document_id", "")) != str(get_chunk_value(chunk, "document_id", ""))
                or str(meta.get("version_id", "")) != str(get_chunk_value(chunk, "version_id", ""))):
            raise VectorStoreError("CHUNK_ID_CONFLICT", f"같은 chunk_id가 다른 문서·버전으로 저장돼 있습니다: {cid}")
    return new_chunks


# FUN-313. 글·벡터·메타데이터를 chunk_id 기준으로 저장한다(같은 청크를 두 번 저장해도 하나만 남는다)
def upsert_chunks(chunks: list, vectors: list[list[float]], metadatas: list[dict], *, batch_size: int = 64) -> int:
    """FUN-313. 청크 글·벡터·메타데이터를 chunk_id 기준으로 저장한다. 같은 청크를 두 번 저장해도 하나만 남는다.

    이미 만들어 둔 벡터를 그대로 넣기 위해 Chroma의 _collection.upsert를 쓴다(수업의 _collection.count()와 같은 방식).
    저장한 개수를 돌려준다.
    """
    if not (len(chunks) == len(vectors) == len(metadatas)):
        raise VectorStoreError(
            "LENGTH_MISMATCH",
            f"청크·벡터·메타데이터 개수가 다릅니다: {len(chunks)}/{len(vectors)}/{len(metadatas)}",
        )
    if not chunks:
        return 0
    store = get_vector_store()
    size = max(1, int(batch_size))
    stored = 0
    try:
        for start in range(0, len(chunks), size):
            part = chunks[start:start + size]
            # 이미 만든 벡터를 그대로 넣기 위해 Chroma 내부 컬렉션의 upsert를 쓴다(수업의 _collection.count()와 같은 접근)
            store._collection.upsert(
                ids=[str(get_chunk_value(c, "chunk_id")) for c in part],
                embeddings=vectors[start:start + size],
                documents=[str(get_chunk_value(c, "text", "")) for c in part],
                metadatas=metadatas[start:start + size],
            )
            stored += len(part)
    except Exception as exc:
        raise VectorStoreError(
            "UPSERT_FAILED", f"벡터 저장에 실패했습니다({stored}개 저장 후): {type(exc).__name__}", stored=stored,
        ) from None
    return stored


# ---------------------------------------------------------------------------
# 조건 만들기·조회
# ---------------------------------------------------------------------------


# None·빈 문자열을 빼고, 순서를 지키며 중복을 없앤다
def _clean_list(values: Optional[list[str]]) -> list[str]:
    """None·빈 문자열을 빼고 순서를 유지한 채 중복을 없앤다."""
    result = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


# 값이 1개면 {키: 값}, 여러 개면 {키: {$in: 목록}} 형식의 조건을 만든다
def _in_condition(key: str, values: list[str]) -> dict:
    return {key: values[0]} if len(values) == 1 else {key: {"$in": values}}


# Chroma filter는 조건이 1개면 그대로, 2개 이상이면 $and로 묶어야 한다
def _and(conditions: list[dict]) -> dict:
    """Chroma filter는 조건이 1개면 그대로, 2개 이상이면 $and로 묶어야 한다."""
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


# FUN-314. 기업·문서 유형·문서 ID·공개 여부 조건을 Chroma filter로 만든다. 회사 목록이 비면 거부(전체 검색 차단)
def build_where_filter(
    company_ids: list[str],
    *,
    doc_types: Optional[list[str]] = None,
    document_ids: Optional[list[str]] = None,
    active_only: bool = True,
) -> dict:
    """FUN-314. 기업·문서 유형·문서 ID·공개 여부 조건을 Chroma filter 형식으로 만든다.

    허용 회사 목록이 비면 NO_COMPANY_SCOPE로 거부한다(전체 검색 차단).
    """
    companies = _clean_list(company_ids)
    if not companies:
        # 로그인 사용자가 볼 수 있는 회사가 없으면 조회 자체를 막는다(접근 권한 보호)
        raise VectorStoreError("NO_COMPANY_SCOPE", "허용 회사 목록이 비어 있어 조회할 수 없습니다.")
    conditions = [_in_condition("company_id", companies)]
    types = _clean_list(doc_types)
    if types:
        conditions.append(_in_condition("doc_type", types))
    docs = _clean_list(document_ids)
    if docs:
        conditions.append(_in_condition("document_id", docs))
    # 공개된(is_active=True) 청크만 검색 대상으로 한다
    if active_only:
        conditions.append({"is_active": True})
    return _and(conditions)


# filter 안에 회사 제한이 반드시 있는지 확인한다. $and는 하나라도, $or는 모든 가지가 회사 조건을 가져야 인정
def _has_company_condition(where: Any) -> bool:
    """filter 안에 회사(company_id) 제한이 반드시 걸려 있는지 확인한다.

    $and는 가지 하나라도, $or는 모든 가지가 회사 조건을 가져야 인정한다.
    """
    if not isinstance(where, dict) or not where:
        return False
    if "company_id" in where:
        value = where["company_id"]
        if isinstance(value, dict):
            return bool(value.get("$in"))  # 빈 $in은 인정하지 않는다
        return value not in (None, "")
    if "$and" in where:
        return any(_has_company_condition(branch) for branch in where["$and"])
    if "$or" in where:
        branches = where["$or"]
        return bool(branches) and all(_has_company_condition(branch) for branch in branches)
    return False


# FUN-315. 질문 벡터와 가까운 청크를 조건 안에서 찾아 유사도 높은 순으로 돌려준다
def query_vectors(query_vector: list[float], where: dict, *, top_k: int = 10) -> list[VectorHit]:
    """FUN-315. 질문 벡터와 가까운 청크를 filter 조건 안에서 찾아 유사도 높은 순으로 돌려준다.

    Chroma.similarity_search_by_vector_with_relevance_scores를 쓰고, 돌려받은 거리를 유사도(1 − 거리)로 바꾼다.
    filter에 회사 조건이 없으면 NO_COMPANY_SCOPE로 거부한다.
    """
    # 회사 조건 없는 조회는 거부(다른 회사 자료가 검색에 섞이는 사고 방지)
    if not _has_company_condition(where):
        raise VectorStoreError("NO_COMPANY_SCOPE", "회사 조건이 없는 조회는 허용되지 않습니다.")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise VectorStoreError("QUERY_FAILED", "top_k는 1 이상의 정수여야 합니다.")
    if not query_vector:
        raise VectorStoreError("QUERY_FAILED", "질문 벡터가 비어 있습니다.")
    store = get_vector_store()
    try:
        # 수업의 similarity_search_with_relevance_scores와 같은 계열. 질문 벡터를 이미 만들어 두었으므로 벡터로 바로 검색
        results = store.similarity_search_by_vector_with_relevance_scores(
            list(query_vector), k=top_k, filter=where,
        )
    except Exception as exc:
        raise VectorStoreError("QUERY_FAILED", f"벡터 조회에 실패했습니다: {type(exc).__name__}") from None
    hits = []
    # 돌려받는 값은 cosine '거리'(작을수록 비슷함)
    for doc, distance in results:  # 이 메서드가 돌려주는 값은 cosine '거리'(가까울수록 작다)
        meta = dict(doc.metadata or {})
        hits.append(VectorHit(
            chunk_id=str(meta.get("chunk_id", "")),
            text=doc.page_content or "",
            metadata=meta,
            # 유사도 = 1 − 거리. 0~1 범위를 벗어나지 않게 보정
            score=max(0.0, min(1.0, 1.0 - float(distance))),  # 유사도 = 1 − 거리, 0~1로 보정
        ))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits


# ---------------------------------------------------------------------------
# 공개·삭제·목록
# ---------------------------------------------------------------------------


# 문서(와 버전)를 고르는 조건. 버전을 안 주면 문서 전체
def _version_where(document_id: str, version_id: Optional[str]) -> dict:
    conditions = [{"document_id": str(document_id)}]
    if version_id is not None:
        conditions.append({"version_id": str(version_id)})
    return _and(conditions)


# 조건에 맞는 항목을 모두 읽고, 저장소 오류는 VectorStoreError로 바꾼다
def _get_all(store, where: dict, include: list[str]) -> dict:
    try:
        return store.get(where=where, include=include)
    except Exception as exc:
        raise VectorStoreError("STORE_UNAVAILABLE", f"벡터 저장소 조회에 실패했습니다: {type(exc).__name__}") from None


# 메타데이터 전체를 다시 써서 is_active만 바꾼다(부분 갱신 동작에 기대지 않는다)
def _set_active(store, ids: list[str], metadatas: list[dict], active: bool) -> None:
    """메타데이터 전체를 다시 써서 is_active만 바꾼다."""
    for start in range(0, len(ids), _BATCH):
        part_meta = [{**(m or {}), "is_active": active} for m in metadatas[start:start + _BATCH]]
        store._collection.update(ids=ids[start:start + _BATCH], metadatas=part_meta)


# FUN-316. 검증을 통과한 새 버전을 공개하고 같은 문서의 이전 버전은 내린다. 새 것을 먼저 켜서 검색이 끊기지 않게 한다
def activate_version(document_id: str, version_id: str) -> int:
    """FUN-316. 검증을 통과한 새 버전의 청크를 공개(is_active=True)하고 같은 문서의 다른 버전은 내린다.

    새 버전을 먼저 켜고 이전 버전을 내려 검색이 끊기지 않게 한다. 공개된 청크 수를 돌려준다.
    해당 버전 청크가 0개면 VERSION_NOT_FOUND.
    """
    store = get_vector_store()
    target = _get_all(store, _version_where(document_id, version_id), ["metadatas"])
    if not target["ids"]:
        raise VectorStoreError("VERSION_NOT_FOUND", f"저장된 청크가 없습니다: document={document_id}, version={version_id}")
    try:
        # 1단계: 새 버전 공개(이 순간 새·옛 버전이 잠시 함께 보일 수 있지만 검색이 비지는 일은 없다)
        _set_active(store, target["ids"], target["metadatas"], True)  # 1) 새 버전 먼저 켜기
        others = _get_all(
            store,
            {"$and": [{"document_id": str(document_id)}, {"version_id": {"$ne": str(version_id)}}, {"is_active": True}]},
            ["metadatas"],
        )
        if others["ids"]:
            _set_active(store, others["ids"], others["metadatas"], False)  # 2) 이전 버전 내리기
    except VectorStoreError:
        raise
    except Exception as exc:
        raise VectorStoreError("STORE_UNAVAILABLE", f"버전 공개 중 오류가 났습니다: {type(exc).__name__}") from None
    return len(target["ids"])


# FUN-317. 문서(또는 특정 버전)의 벡터를 삭제하고 삭제 개수를 돌려준다. 지울 것이 없으면 0
def delete_vectors(document_id: str, version_id: Optional[str] = None) -> int:
    """FUN-317. 문서(또는 특정 버전)의 벡터를 삭제하고 삭제한 개수를 돌려준다. 지울 것이 없으면 0."""
    store = get_vector_store()
    ids = _get_all(store, _version_where(document_id, version_id), [])["ids"]
    try:
        for start in range(0, len(ids), _BATCH):
            store.delete(ids=ids[start:start + _BATCH])
    except Exception as exc:
        raise VectorStoreError("DELETE_FAILED", f"벡터 삭제에 실패했습니다: {type(exc).__name__}") from None
    return len(ids)


# 정렬용 순번. chunk_index가 숫자가 아니면 맨 뒤로 보낸다
def _chunk_index(meta: dict) -> float:
    index = (meta or {}).get("chunk_index", 0)
    return index if isinstance(index, (int, float)) and not isinstance(index, bool) else float("inf")


# FUN-318. 한 버전의 청크 ID를 chunk_index 순으로 돌려준다(db가 '다 들어갔는지' 비교할 때 사용)
def get_version_chunk_ids(document_id: str, version_id: str) -> list[str]:
    """FUN-318. 해당 버전으로 저장된 청크 ID를 chunk_index 순으로 돌려준다(적재 완전성 비교용)."""
    store = get_vector_store()
    found = _get_all(store, _version_where(document_id, version_id), ["metadatas"])
    pairs = sorted(zip(found["ids"], found["metadatas"] or []), key=lambda p: _chunk_index(p[1]))
    return [cid for cid, _ in pairs]


# FUN-319. 한 문서의 청크를 순서대로 돌려준다(요약용, score=None). 챗봇 요약이 이 함수를 부른다
def get_document_chunks(document_id: str, version_id: Optional[str] = None, *, active_only: bool = True) -> list[VectorHit]:
    """FUN-319. 한 문서의 청크를 chunk_index 순으로 돌려준다(score=None, 요약용).

    active_only=True이면 공개된 청크만 준다. 공개 전 버전을 보려면 version_id와 active_only=False를 함께 준다.
    """
    store = get_vector_store()
    conditions = [{"document_id": str(document_id)}]
    if version_id is not None:
        conditions.append({"version_id": str(version_id)})
    if active_only:
        conditions.append({"is_active": True})
    found = _get_all(store, _and(conditions), ["documents", "metadatas"])
    rows = zip(found["ids"], found["documents"] or [], found["metadatas"] or [])
    ordered = sorted(rows, key=lambda r: _chunk_index(r[2]))
    return [VectorHit(chunk_id=cid, text=doc or "", metadata=dict(meta or {}), score=None) for cid, doc, meta in ordered]


# ---------------------------------------------------------------------------
# 상태 점검
# ---------------------------------------------------------------------------


# FUN-320. 저장소가 열리는지, 전체·공개 벡터 수가 몇 개인지 확인한다. 예외 대신 ok=False 딕셔너리로 알려준다
def check_vector_store_health() -> dict:
    """FUN-320. 저장소가 열리는지, 저장된 벡터 수와 공개된 벡터 수를 확인한다. 오류는 ok=False로 돌려준다."""
    result: dict[str, Any] = {"ok": False, "collection": "", "total_vectors": 0, "active_vectors": 0, "detail": ""}
    started = time.time()
    try:
        store = get_vector_store()
        result["collection"] = store._collection.name
        # 수업에서 배운 _collection.count()로 저장된 개수 확인
        result["total_vectors"] = store._collection.count()
        result["active_vectors"] = len(store.get(where={"is_active": True}, include=[])["ids"])
        result["ok"] = True
        result["detail"] = f"정상 ({time.time() - started:.2f}초)"
    except VectorStoreError as exc:
        result["detail"] = f"{exc.code}: {exc.message}"
    except Exception as exc:
        result["detail"] = f"점검 중 오류: {type(exc).__name__}"
    return result
