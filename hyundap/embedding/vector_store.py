"""embedding/vector_store.py - 벡터 저장·조회

Owner: 박영준
Spec: AI_문서검색_통합_기능명세서 (MOD-102)

Functions:
    FUN-310 collection_name_for_model
    FUN-311 get_collection
    FUN-312 filter_new_chunks
    FUN-313 upsert_chunks
    FUN-314 build_where_filter
    FUN-315 query_vectors
    FUN-316 activate_version
    FUN-317 delete_vectors
    FUN-318 get_version_chunk_ids
    FUN-319 get_document_chunks
    FUN-320 check_vector_store_health

Classes:
    CLS-108 ChunkMetadata  (클래스가 아니라 build_chunk_metadata가 만드는 딕셔너리 규약. 키 목록은 METADATA_KEYS)
"""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, Optional

from .models import (
    VectorHit,
    VectorStoreError,
    get_chunk_value,
    load_embedding_settings,
)

if TYPE_CHECKING:  # 실행할 때는 불러오지 않는다(타입 표시용)
    from hyundap.common.schemas import Chunk

# CLS-108 ChunkMetadata 키 규약 (값은 str·int·float·bool만, None 불가)
METADATA_KEYS = (
    "chunk_id", "document_id", "version_id", "company_id", "company_name",
    "doc_type", "file_name", "section_no", "location_label", "chunk_index",
    "embedding_model", "truncated", "is_active", "embedded_at",
)

COLLECTION_PREFIX = "chunks__"
_GET_BATCH = 500  # collection.get(ids=...)·update·delete를 나눠 부르는 크기
_clients: dict[str, Any] = {}  # 저장 경로별 Chroma 클라이언트(프로세스당 하나씩 재사용)


# ---------------------------------------------------------------------------
# 컬렉션 준비
# ---------------------------------------------------------------------------


def collection_name_for_model(model_name: str) -> str:
    """FUN-310. 모델 이름으로 컬렉션 이름(chunks__<모델>)을 만든다.

    Chroma 이름 규칙(영문·숫자·. _ -, 앞뒤는 영문·숫자)에 맞지 않는 문자는 -로 바꾼다.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", (model_name or "").strip())
    cleaned = cleaned.strip("._-") or "default"
    return f"{COLLECTION_PREFIX}{cleaned}"[:512]


def get_collection(model_name: Optional[str] = None):
    """FUN-311. Chroma 클라이언트를 한 번만 열고 모델별 컬렉션을 가져오거나 만든다(cosine).

    model_name을 안 주면 설정의 모델 이름을 쓴다.
    """
    try:
        import chromadb

        settings = load_embedding_settings()
        client = _clients.get(settings.chroma_path)
        if client is None:
            client = chromadb.PersistentClient(path=settings.chroma_path)
            _clients[settings.chroma_path] = client
        name = collection_name_for_model(model_name or settings.model_name)
        return client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})
    except VectorStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError("STORE_UNAVAILABLE", f"벡터 저장소를 열 수 없습니다: {type(exc).__name__}") from None


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------


def filter_new_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """FUN-312. 이미 저장된 chunk_id는 빼고 새 청크만 골라낸다(입력 순서 유지).

    같은 chunk_id가 다른 회사로 저장돼 있으면 COMPANY_MISMATCH,
    같은 회사인데 다른 문서·버전이면 CHUNK_ID_CONFLICT로 멈춘다(남의 청크를 덮어쓰지 않기 위해).
    """
    if not chunks:
        return []
    collection = get_collection()
    ids = [str(get_chunk_value(c, "chunk_id", "")) for c in chunks]
    existing: dict[str, dict] = {}
    try:
        for start in range(0, len(ids), _GET_BATCH):
            part = ids[start:start + _GET_BATCH]
            found = collection.get(ids=part, include=["metadatas"])
            for cid, meta in zip(found["ids"], found["metadatas"] or []):
                existing[cid] = meta or {}
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError("STORE_UNAVAILABLE", f"저장된 청크를 조회할 수 없습니다: {type(exc).__name__}") from None

    new_chunks = []
    for chunk, cid in zip(chunks, ids):
        meta = existing.get(cid)
        if meta is None:
            new_chunks.append(chunk)
            continue
        if str(meta.get("company_id", "")) != str(get_chunk_value(chunk, "company_id", "")):
            raise VectorStoreError("COMPANY_MISMATCH", f"같은 chunk_id가 다른 회사로 저장돼 있습니다: {cid}")
        if (str(meta.get("document_id", "")) != str(get_chunk_value(chunk, "document_id", ""))
                or str(meta.get("version_id", "")) != str(get_chunk_value(chunk, "version_id", ""))):
            raise VectorStoreError("CHUNK_ID_CONFLICT", f"같은 chunk_id가 다른 문서·버전으로 저장돼 있습니다: {cid}")
    return new_chunks


def upsert_chunks(chunks: list[Chunk], vectors: list[list[float]], metadatas: list[dict], *, batch_size: int = 64) -> int:
    """FUN-313. 청크 글·벡터·메타데이터를 chunk_id 기준으로 저장한다. 같은 청크를 두 번 저장해도 하나만 남는다.

    저장한 개수를 돌려준다.
    """
    if not (len(chunks) == len(vectors) == len(metadatas)):
        raise VectorStoreError(
            "LENGTH_MISMATCH",
            f"청크·벡터·메타데이터 개수가 다릅니다: {len(chunks)}/{len(vectors)}/{len(metadatas)}",
        )
    if not chunks:
        return 0
    collection = get_collection()
    size = max(1, int(batch_size))
    stored = 0
    try:
        for start in range(0, len(chunks), size):
            end = start + size
            collection.upsert(
                ids=[str(get_chunk_value(c, "chunk_id")) for c in chunks[start:end]],
                embeddings=vectors[start:end],
                documents=[str(get_chunk_value(c, "text", "")) for c in chunks[start:end]],
                metadatas=metadatas[start:end],
            )
            stored += len(chunks[start:end])
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError("UPSERT_FAILED", f"벡터 저장에 실패했습니다({stored}개 저장 후): {type(exc).__name__}") from None
    return stored


# ---------------------------------------------------------------------------
# 조건 만들기·조회
# ---------------------------------------------------------------------------


def _clean_list(values: Optional[list[str]]) -> list[str]:
    """None·빈 문자열을 빼고 순서를 유지한 채 중복을 없앤다."""
    seen: dict[str, None] = {}
    for value in values or []:
        text = str(value).strip()
        if text:
            seen.setdefault(text, None)
    return list(seen)


def _in_condition(key: str, values: list[str]) -> dict:
    return {key: values[0]} if len(values) == 1 else {key: {"$in": values}}


def _and(conditions: list[dict]) -> dict:
    """Chroma where는 조건이 1개면 그대로, 2개 이상이면 $and로 묶어야 한다."""
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def build_where_filter(
    company_ids: list[str],
    *,
    doc_types: Optional[list[str]] = None,
    document_ids: Optional[list[str]] = None,
    active_only: bool = True,
) -> dict:
    """FUN-314. 기업·문서 유형·문서 ID·공개 여부 조건을 Chroma where 형식으로 만든다.

    허용 회사 목록이 비면 NO_COMPANY_SCOPE로 거부한다(전체 검색 차단).
    """
    companies = _clean_list(company_ids)
    if not companies:
        raise VectorStoreError("NO_COMPANY_SCOPE", "허용 회사 목록이 비어 있어 조회할 수 없습니다.")
    conditions = [_in_condition("company_id", companies)]
    types = _clean_list(doc_types)
    if types:
        conditions.append(_in_condition("doc_type", types))
    docs = _clean_list(document_ids)
    if docs:
        conditions.append(_in_condition("document_id", docs))
    if active_only:
        conditions.append({"is_active": True})
    return _and(conditions)


def _has_company_condition(where: Any) -> bool:
    """where 안에 회사(company_id) 제한이 반드시 걸려 있는지 확인한다.

    $and는 가지 하나라도, $or는 모든 가지가 회사 조건을 가져야 인정한다.
    """
    if not isinstance(where, dict) or not where:
        return False
    if "company_id" in where:
        value = where["company_id"]
        if isinstance(value, dict):
            members = value.get("$in")
            return bool(members)  # 빈 $in은 인정하지 않는다
        return value not in (None, "")
    if "$and" in where:
        return any(_has_company_condition(branch) for branch in where["$and"])
    if "$or" in where:
        branches = where["$or"]
        return bool(branches) and all(_has_company_condition(branch) for branch in branches)
    return False


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def query_vectors(query_vector: list[float], where: dict, *, top_k: int = 10) -> list[VectorHit]:
    """FUN-315. 질문 벡터와 가까운 청크를 where 조건 안에서 찾아 유사도 높은 순으로 돌려준다.

    score = 1 − cosine 거리(0~1로 보정). where에 회사 조건이 없으면 NO_COMPANY_SCOPE로 거부한다.
    """
    if not _has_company_condition(where):
        raise VectorStoreError("NO_COMPANY_SCOPE", "회사 조건이 없는 조회는 허용되지 않습니다.")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise VectorStoreError("QUERY_FAILED", "top_k는 1 이상의 정수여야 합니다.")
    if not query_vector:
        raise VectorStoreError("QUERY_FAILED", "질문 벡터가 비어 있습니다.")
    collection = get_collection()
    try:
        raw = collection.query(
            query_embeddings=[list(query_vector)],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError("QUERY_FAILED", f"벡터 조회에 실패했습니다: {type(exc).__name__}") from None
    ids = (raw.get("ids") or [[]])[0]
    docs = (raw.get("documents") or [[]])[0]
    metas = (raw.get("metadatas") or [[]])[0]
    dists = (raw.get("distances") or [[]])[0]
    hits = [
        VectorHit(chunk_id=cid, text=doc or "", metadata=dict(meta or {}), score=_clamp01(1.0 - float(dist)))
        for cid, doc, meta, dist in zip(ids, docs, metas, dists)
    ]
    hits.sort(key=lambda h: h.score or 0.0, reverse=True)  # Chroma도 가까운 순이지만 정렬을 보장한다
    return hits


# ---------------------------------------------------------------------------
# 공개·삭제·목록
# ---------------------------------------------------------------------------


def _version_where(document_id: str, version_id: Optional[str]) -> dict:
    conditions = [{"document_id": str(document_id)}]
    if version_id is not None:
        conditions.append({"version_id": str(version_id)})
    return _and(conditions)


def _get_all(collection, where: dict, include: list[str]) -> dict:
    try:
        return collection.get(where=where, include=include)
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError("STORE_UNAVAILABLE", f"벡터 저장소 조회에 실패했습니다: {type(exc).__name__}") from None


def _set_active(collection, ids: list[str], metadatas: list[dict], active: bool) -> None:
    """메타데이터 전체를 다시 써서 is_active만 바꾼다(부분 갱신 동작에 기대지 않는다)."""
    for start in range(0, len(ids), _GET_BATCH):
        part_ids = ids[start:start + _GET_BATCH]
        part_meta = [{**(m or {}), "is_active": active} for m in metadatas[start:start + _GET_BATCH]]
        collection.update(ids=part_ids, metadatas=part_meta)


def activate_version(document_id: str, version_id: str) -> int:
    """FUN-316. 검증을 통과한 새 버전의 청크를 공개(is_active=True)하고 같은 문서의 다른 버전은 내린다.

    새 버전을 먼저 켜고 이전 버전을 내려 검색이 끊기지 않게 한다. 공개된 청크 수를 돌려준다.
    해당 버전 청크가 0개면 VERSION_NOT_FOUND.
    """
    collection = get_collection()
    target = _get_all(collection, _version_where(document_id, version_id), ["metadatas"])
    if not target["ids"]:
        raise VectorStoreError("VERSION_NOT_FOUND", f"저장된 청크가 없습니다: document={document_id}, version={version_id}")
    try:
        _set_active(collection, target["ids"], target["metadatas"], True)  # 1) 새 버전 먼저 켜기
        others = _get_all(
            collection,
            {"$and": [{"document_id": str(document_id)}, {"version_id": {"$ne": str(version_id)}}, {"is_active": True}]},
            ["metadatas"],
        )
        if others["ids"]:
            _set_active(collection, others["ids"], others["metadatas"], False)  # 2) 이전 버전 내리기
    except VectorStoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError("STORE_UNAVAILABLE", f"버전 공개 중 오류가 났습니다: {type(exc).__name__}") from None
    return len(target["ids"])


def delete_vectors(document_id: str, version_id: Optional[str] = None) -> int:
    """FUN-317. 문서(또는 특정 버전)의 벡터를 삭제하고 삭제한 개수를 돌려준다. 지울 것이 없으면 0."""
    collection = get_collection()
    found = _get_all(collection, _version_where(document_id, version_id), [])
    ids = found["ids"]
    try:
        for start in range(0, len(ids), _GET_BATCH):
            collection.delete(ids=ids[start:start + _GET_BATCH])
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError("DELETE_FAILED", f"벡터 삭제에 실패했습니다: {type(exc).__name__}") from None
    return len(ids)


def _chunk_order(meta: dict) -> tuple:
    index = (meta or {}).get("chunk_index", 0)
    return (0, index) if isinstance(index, (int, float)) and not isinstance(index, bool) else (1, 0)


def get_version_chunk_ids(document_id: str, version_id: str) -> list[str]:
    """FUN-318. 해당 버전으로 저장된 청크 ID를 chunk_index 순으로 돌려준다(적재 완전성 비교용)."""
    collection = get_collection()
    found = _get_all(collection, _version_where(document_id, version_id), ["metadatas"])
    pairs = sorted(zip(found["ids"], found["metadatas"] or []), key=lambda p: _chunk_order(p[1]))
    return [cid for cid, _ in pairs]


def get_document_chunks(document_id: str, version_id: Optional[str] = None, *, active_only: bool = True) -> list[VectorHit]:
    """FUN-319. 한 문서의 청크를 chunk_index 순으로 돌려준다(score=None, 요약용).

    active_only=True이면 공개된 청크만 준다. 따라서 버전을 안 주면 공개된 버전의 청크만 나오고,
    아직 공개 전인 버전을 보려면 version_id와 active_only=False를 함께 준다.
    """
    collection = get_collection()
    conditions = [{"document_id": str(document_id)}]
    if version_id is not None:
        conditions.append({"version_id": str(version_id)})
    if active_only:
        conditions.append({"is_active": True})
    found = _get_all(collection, _and(conditions), ["documents", "metadatas"])
    rows = zip(found["ids"], found["documents"] or [], found["metadatas"] or [])
    ordered = sorted(rows, key=lambda r: _chunk_order(r[2]))
    return [VectorHit(chunk_id=cid, text=doc or "", metadata=dict(meta or {}), score=None) for cid, doc, meta in ordered]


# ---------------------------------------------------------------------------
# 상태 점검
# ---------------------------------------------------------------------------


def check_vector_store_health() -> dict:
    """FUN-320. 컬렉션이 열리는지, 저장된 벡터 수와 공개된 벡터 수를 확인한다. 오류는 ok=False로 돌려준다."""
    result: dict[str, Any] = {
        "ok": False, "collection": "", "total_vectors": 0, "active_vectors": 0, "detail": "",
    }
    started = time.monotonic()
    try:
        collection = get_collection()
        result["collection"] = collection.name
        result["total_vectors"] = collection.count()
        active = collection.get(where={"is_active": True}, include=[])
        result["active_vectors"] = len(active["ids"])
        result["ok"] = True
        result["detail"] = f"정상 ({time.monotonic() - started:.2f}초)"
    except VectorStoreError as exc:
        result["detail"] = f"{exc.code}: {exc.message}"
    except Exception as exc:  # noqa: BLE001
        result["detail"] = f"점검 중 오류: {type(exc).__name__}"
    return result
