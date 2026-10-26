"""embedding/embedder.py - 임베딩 생성

Owner: 박영준
Spec: AI_문서검색_통합_기능명세서 (MOD-101)

Functions:
    FUN-300 get_embedding_model
    FUN-301 build_embedding_text
    FUN-302 fit_to_token_limit
    FUN-303 build_chunk_metadata
    FUN-304 embed_batch_with_retry
    FUN-305 embed_chunks
    FUN-306 validate_embeddings
    FUN-307 embed_and_store
    FUN-308 embed_query
    FUN-309 check_embedding_health
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Optional

from .models import (
    EmbeddedChunk,
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingFailure,
    EmbedStoreReport,
    VectorStoreError,
    get_chunk_value,
    load_embedding_settings,
)
from .vector_store import filter_new_chunks, upsert_chunks

if TYPE_CHECKING:  # 실행할 때는 불러오지 않는다(타입 표시용)
    from hyundap.common.schemas import Chunk

MAX_INPUT_TOKENS = 8191        # text-embedding-3 계열 입력 상한
MAX_QUERY_CHARS = 2000         # 검색어·질문 길이 상한 (search의 MAX_QUERY_CHARS와 같은 값)
DEFAULT_BATCH_SIZE = 64
_FALLBACK_TOKENS_PER_CHAR = 2  # tiktoken을 쓸 수 없을 때의 보수적 추정(글자 1개 = 토큰 2개)

# 다시 시도하면 성공할 수 있는 오류 코드 (속도 제한·연결 끊김·시간 초과·서버 오류)
RETRYABLE_CODES = frozenset({"RATE_LIMIT", "CONNECTION", "TIMEOUT", "SERVER_ERROR"})

_SECRET_PATTERN = re.compile(r"(sk-[A-Za-z0-9_\-]{6,})|(Bearer\s+[A-Za-z0-9._\-]{10,})")


# ---------------------------------------------------------------------------
# 모델 준비
# ---------------------------------------------------------------------------


def _resolve_model_name(model_name: Optional[str]) -> str:
    return (model_name or "").strip() or load_embedding_settings().model_name


@lru_cache(maxsize=4)
def _build_model(model_name: str, api_key: str):
    from langchain_openai import OpenAIEmbeddings

    # 재시도는 embed_batch_with_retry가 직접 하므로 라이브러리 재시도는 끄고,
    # 길이 점검도 fit_to_token_limit이 하므로 끈다.
    return OpenAIEmbeddings(
        model=model_name,
        api_key=api_key,
        max_retries=0,
        check_embedding_ctx_length=False,
    )


def get_embedding_model(model_name: Optional[str] = None):
    """FUN-300. OpenAI 임베딩 모델 객체를 만들고 같은 설정이면 같은 객체를 재사용한다.

    청크용·질문용이 같은 모델을 쓰도록 모든 호출이 이 함수를 거친다. API 키가 없으면 NO_API_KEY.
    """
    settings = load_embedding_settings()
    if not settings.openai_api_key:
        raise EmbeddingError("NO_API_KEY", "OPENAI_API_KEY가 설정되어 있지 않습니다. .env를 확인하세요.")
    return _build_model(_resolve_model_name(model_name), settings.openai_api_key)


# ---------------------------------------------------------------------------
# 입력 구성·길이 점검·메타데이터
# ---------------------------------------------------------------------------


def build_embedding_text(chunk: Chunk) -> str:
    """FUN-301. 임베딩에 넣을 글을 만든다. 청크 text의 앞뒤 공백만 지우고, 회사·문서 유형은 글에 섞지 않는다."""
    text = get_chunk_value(chunk, "text", "")
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        raise EmbeddingError("EMPTY_TEXT", "청크 본문이 비어 있습니다.")
    return text


@lru_cache(maxsize=8)
def _get_encoding(model_name: str):
    """tiktoken 인코딩을 가져온다. 모델을 모르면 cl100k_base, 아예 못 가져오면 None(→ 글자 수로 추정)."""
    try:
        import tiktoken

        try:
            return tiktoken.encoding_for_model(model_name)
        except KeyError:
            return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 - 설치 안 됨, 인코딩 파일 다운로드 실패 등
        return None


def fit_to_token_limit(text: str, max_tokens: int = MAX_INPUT_TOKENS, model_name: Optional[str] = None) -> tuple[str, bool]:
    """FUN-302. 토큰 수를 세어 상한을 넘으면 상한까지 잘라내고 (처리된 글, 잘림 여부)를 돌려준다."""
    encoding = _get_encoding(_resolve_model_name(model_name))
    if encoding is not None:
        tokens = encoding.encode(text, disallowed_special=())
        if len(tokens) <= max_tokens:
            return text, False
        return encoding.decode(tokens[:max_tokens]), True
    # tiktoken을 쓸 수 없는 환경: 글자 1개를 토큰 2개로 보수적으로 추정한다.
    if len(text) * _FALLBACK_TOKENS_PER_CHAR <= max_tokens:
        return text, False
    return text[: max_tokens // _FALLBACK_TOKENS_PER_CHAR], True


def _plain(value: Any) -> Any:
    """Chroma 메타데이터에 넣을 수 있는 값(str·int·float·bool)으로 바꾼다. None은 빈 문자열."""
    if value is None:
        return ""
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def build_chunk_metadata(chunk: Chunk, *, model_name: str, truncated: bool = False) -> dict:
    """FUN-303. Chroma에 함께 저장할 메타데이터(CLS-108)를 만든다. is_active는 False로 시작한다.

    chunk_id·document_id·version_id·company_id·doc_type 중 하나라도 비면 MISSING_METADATA.
    """
    meta = {
        "chunk_id": _plain(get_chunk_value(chunk, "chunk_id")),
        "document_id": _plain(get_chunk_value(chunk, "document_id")),
        "version_id": _plain(get_chunk_value(chunk, "version_id")),
        "company_id": _plain(get_chunk_value(chunk, "company_id")),
        "company_name": _plain(get_chunk_value(chunk, "company_name")),
        "doc_type": _plain(get_chunk_value(chunk, "doc_type")),
        "file_name": _plain(get_chunk_value(chunk, "file_name")),
        "section_no": _plain(get_chunk_value(chunk, "section_no")),
        "location_label": _plain(get_chunk_value(chunk, "location_label")),
        "chunk_index": _plain(get_chunk_value(chunk, "chunk_index")),
        "embedding_model": model_name,
        "truncated": bool(truncated),
        "is_active": False,
        "embedded_at": int(time.time()),
    }
    for key in ("chunk_id", "document_id", "version_id", "company_id", "doc_type"):
        value = meta[key]
        if value == "" or (isinstance(value, str) and not value.strip()):
            raise EmbeddingError("MISSING_METADATA", f"필수 값이 비어 있습니다: {key}")
        meta[key] = str(value)
    return meta


# ---------------------------------------------------------------------------
# 임베딩 호출 (재시도 포함)
# ---------------------------------------------------------------------------


def _mask_secrets(text: str) -> str:
    """오류 문구에 API 키가 섞여 있으면 가린다."""
    return _SECRET_PATTERN.sub("***", text)


def _classify_error(exc: Exception) -> tuple[str, bool]:
    """외부 오류를 (코드, 재시도 가능 여부)로 분류한다. openai 라이브러리의 클래스 이름·상태 코드를 본다."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if name == "RateLimitError" or status == 429:
        if "insufficient_quota" in text or "exceeded your current quota" in text:
            return "QUOTA_EXCEEDED", False  # 기다려도 해결되지 않는 한도 초과
        return "RATE_LIMIT", True
    if name == "APITimeoutError" or isinstance(exc, TimeoutError):
        return "TIMEOUT", True
    if name == "APIConnectionError" or isinstance(exc, ConnectionError):
        return "CONNECTION", True
    if isinstance(status, int) and status >= 500:
        return "SERVER_ERROR", True
    if name in ("AuthenticationError", "PermissionDeniedError") or status in (401, 403):
        return "AUTH_ERROR", False
    if name in ("BadRequestError", "UnprocessableEntityError", "NotFoundError") or status in (400, 404, 422):
        return "BAD_REQUEST", False
    return "UNKNOWN_ERROR", False


def embed_batch_with_retry(texts: list[str], *, max_retries: int = 3, base_delay: float = 1.0) -> list[list[float]]:
    """FUN-304. 글 한 묶음을 임베딩 API로 보낸다.

    일시 오류(속도 제한·연결 끊김·시간 초과·서버 오류)는 base_delay·2·4초 간격으로 최대 max_retries번 다시 시도하고,
    고칠 수 없는 오류는 바로 EmbeddingError로 올린다. 재시도를 모두 쓰고도 실패하면 retryable=True로 기록한다.
    """
    if not texts:
        return []
    model = get_embedding_model()  # NO_API_KEY는 여기서 바로 올라간다.
    attempt = 0
    while True:
        try:
            vectors = model.embed_documents(list(texts))
            if len(vectors) != len(texts):
                raise EmbeddingError("BAD_RESPONSE", f"입력 {len(texts)}개에 결과 {len(vectors)}개가 돌아왔습니다.")
            return [list(map(float, v)) for v in vectors]
        except EmbeddingError:
            raise
        except Exception as exc:  # noqa: BLE001 - 외부 라이브러리 오류를 분류해서 처리
            code, retryable = _classify_error(exc)
            message = _mask_secrets(f"{type(exc).__name__}: {exc}")[:300]
            if retryable and attempt < max_retries:
                time.sleep(base_delay * (2 ** attempt))
                attempt += 1
                continue
            raise EmbeddingError(code, message, retryable=retryable) from None


# ---------------------------------------------------------------------------
# 청크 임베딩·검증
# ---------------------------------------------------------------------------


def _failure(chunk: Any, error: EmbeddingError) -> EmbeddingFailure:
    return EmbeddingFailure(
        chunk_id=str(get_chunk_value(chunk, "chunk_id", "")),
        code=error.code,
        message=_mask_secrets(error.message),
        retryable=error.retryable,
    )


def embed_chunks(chunks: list[Chunk], *, batch_size: int = DEFAULT_BATCH_SIZE) -> EmbeddingBatch:
    """FUN-305. 청크를 batch_size개씩 묶어 입력 구성·길이 점검·재시도 호출을 거쳐 임베딩한다.

    실패한 청크(빈 글, 필수 메타데이터 없음, 끝내 실패한 묶음)는 failures에 담고 나머지는 계속 처리한다.
    """
    result = EmbeddingBatch()
    if not chunks:
        return result
    model_name = _resolve_model_name(None)

    prepared: list[tuple[Any, str, dict, bool]] = []  # (청크, 글, 메타데이터, 잘림)
    for chunk in chunks:
        try:
            text, truncated = fit_to_token_limit(build_embedding_text(chunk), model_name=model_name)
            meta = build_chunk_metadata(chunk, model_name=model_name, truncated=truncated)
            prepared.append((chunk, text, meta, truncated))
        except EmbeddingError as error:
            result.failures.append(_failure(chunk, error))

    size = max(1, int(batch_size))
    for start in range(0, len(prepared), size):
        group = prepared[start:start + size]
        try:
            vectors = embed_batch_with_retry([item[1] for item in group])
        except EmbeddingError as error:
            result.failures.extend(_failure(item[0], error) for item in group)
            continue
        for (chunk, _text, meta, truncated), vector in zip(group, vectors):
            result.items.append(EmbeddedChunk(chunk=chunk, vector=vector, metadata=meta, truncated=truncated))
    return result


def validate_embeddings(vectors: list[list[float]], expected_dim: Optional[int] = None) -> list[int]:
    """FUN-306. 차원이 다르거나 NaN·무한대가 있거나 전부 0인 벡터의 위치 목록을 돌려준다(빈 목록이면 정상).

    expected_dim을 안 주면 가장 많은 벡터가 가진 길이를 기준으로 삼는다.
    """
    if not vectors:
        return []
    if expected_dim is None:
        expected_dim = Counter(len(v) for v in vectors).most_common(1)[0][0]
    bad = []
    for index, vector in enumerate(vectors):
        if (
            len(vector) != expected_dim
            or expected_dim == 0
            or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector)
            or not any(x != 0 for x in vector)
        ):
            bad.append(index)
    return bad


# ---------------------------------------------------------------------------
# 일괄 실행
# ---------------------------------------------------------------------------


def _check_scope(chunks: list[Chunk]) -> tuple[str, str, str]:
    """한 번에 넘긴 청크가 같은 문서·버전·회사인지, chunk_id가 겹치지 않는지 확인한다."""
    if not chunks:
        raise EmbeddingError("EMPTY_CHUNKS", "임베딩할 청크가 없습니다.")
    scopes = {
        tuple(str(get_chunk_value(c, key, "")) for key in ("document_id", "version_id", "company_id"))
        for c in chunks
    }
    if len(scopes) != 1:
        raise EmbeddingError("MIXED_SCOPE", "한 번에 같은 문서·버전·회사의 청크만 처리할 수 있습니다.")
    ids = [str(get_chunk_value(c, "chunk_id", "")) for c in chunks]
    if len(set(ids)) != len(ids):
        raise EmbeddingError("DUPLICATE_CHUNK_ID", "같은 chunk_id가 두 번 이상 들어 있습니다.")
    return next(iter(scopes))


def embed_and_store(chunks: list[Chunk], *, batch_size: int = DEFAULT_BATCH_SIZE) -> EmbedStoreReport:
    """FUN-307. 업로드 처리 흐름이 부르는 대표 함수.

    묶음 검사 → 미색인 선별 → 임베딩 → 벡터 검증 → Chroma 저장(비공개) 순으로 실행하고 보고서를 돌려준다.
    공개(activate_version)는 하지 않는다. 실패가 하나라도 있으면 status는 partial 또는 failed이며,
    호출한 쪽은 partial도 실패로 취급해야 한다.
    """
    started = time.monotonic()
    document_id, version_id, company_id = _check_scope(chunks)
    model_name = _resolve_model_name(None)
    report = EmbedStoreReport(
        document_id=document_id, version_id=version_id, company_id=company_id,
        model_name=model_name, total=len(chunks),
    )

    new_chunks = filter_new_chunks(chunks)  # VectorStoreError(COMPANY_MISMATCH 등)는 그대로 올린다.
    report.skipped_existing = len(chunks) - len(new_chunks)

    batch = embed_chunks(new_chunks, batch_size=batch_size)
    report.failures.extend(batch.failures)

    items = batch.items
    bad = set(validate_embeddings([item.vector for item in items]))
    for index in sorted(bad):
        report.failures.append(EmbeddingFailure(
            chunk_id=str(get_chunk_value(items[index].chunk, "chunk_id", "")),
            code="INVALID_VECTOR", message="차원이 다르거나 NaN·무한대·전부 0인 벡터입니다.", retryable=True,
        ))
    good = [item for i, item in enumerate(items) if i not in bad]
    report.embedded = len(good)
    report.truncated_count = sum(1 for item in good if item.truncated)

    if good:
        try:
            report.stored = upsert_chunks(
                [i.chunk for i in good], [i.vector for i in good], [i.metadata for i in good], batch_size=batch_size,
            )
        except VectorStoreError as error:
            report.failures.extend(
                EmbeddingFailure(
                    chunk_id=str(get_chunk_value(i.chunk, "chunk_id", "")),
                    code=error.code, message=error.message, retryable=True,
                )
                for i in good
            )
            report.stored = 0

    if not report.failures:
        report.status = "success"
    elif report.stored + report.skipped_existing > 0:
        report.status = "partial"
    else:
        report.status = "failed"
    report.elapsed_sec = round(time.monotonic() - started, 3)
    return report


# ---------------------------------------------------------------------------
# 질문 임베딩·상태 점검
# ---------------------------------------------------------------------------


def embed_query(query: str) -> list[float]:
    """FUN-308. 검색어·질문을 청크와 같은 모델로 임베딩한다. 앞뒤 공백을 지운 뒤 1~2000자만 허용한다."""
    text = query.strip() if isinstance(query, str) else ""
    if not text:
        raise EmbeddingError("EMPTY_QUERY", "검색어가 비어 있습니다.")
    if len(text) > MAX_QUERY_CHARS:
        raise EmbeddingError("QUERY_TOO_LONG", f"검색어는 {MAX_QUERY_CHARS}자 이하여야 합니다.")
    vector = embed_batch_with_retry([text])[0]
    if validate_embeddings([vector]):
        raise EmbeddingError("INVALID_VECTOR", "질문 벡터가 비정상입니다.", retryable=True)
    return vector


_health_cache: dict[str, Any] = {"until": 0.0, "model": "", "result": None}


def check_embedding_health(*, timeout: float = 5.0, cache_seconds: int = 60) -> dict:
    """FUN-309. 시험 문장을 임베딩해 모델·API 키가 동작하는지 점검한다. 오류는 ok=False로 돌려준다.

    최대 timeout초 기다리고, 성공한 결과는 cache_seconds초 동안 재사용한다(실제 API 호출 비용을 줄이기 위해).
    """
    try:
        model_name = _resolve_model_name(None)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "model": "", "dim": 0, "detail": f"설정 오류: {type(exc).__name__}"}

    now = time.monotonic()
    cached = _health_cache["result"]
    if cache_seconds > 0 and cached is not None and _health_cache["model"] == model_name and now < _health_cache["until"]:
        return {**cached, "detail": cached["detail"] + " (캐시)"}

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(embed_batch_with_retry, ["health check"], max_retries=0)
        vector = future.result(timeout=timeout)[0]
        if validate_embeddings([vector]):
            result = {"ok": False, "model": model_name, "dim": len(vector), "detail": "비정상 벡터가 돌아왔습니다."}
        else:
            result = {"ok": True, "model": model_name, "dim": len(vector), "detail": "정상"}
            _health_cache.update(until=now + cache_seconds, model=model_name, result=result)
    except FutureTimeout:
        result = {"ok": False, "model": model_name, "dim": 0, "detail": f"{timeout}초 안에 응답이 없습니다."}
    except EmbeddingError as error:
        result = {"ok": False, "model": model_name, "dim": 0, "detail": f"{error.code}: {error.message}"}
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "model": model_name, "dim": 0, "detail": f"점검 중 오류: {type(exc).__name__}"}
    finally:
        executor.shutdown(wait=False)  # 시간 초과 시 기다리지 않는다.
    return result
