"""embedding/embedder.py - 임베딩 생성 (수업 범위 리팩토링판)

Owner: 박영준
Spec: AI_문서검색_통합_기능명세서 (MOD-101)

수업 범위: langchain_openai.OpenAIEmbeddings(text-embedding-3-small), embed_query, embed_documents,
          numpy, collections.Counter, time, try/except, openai SDK(수업의 OpenAI SDK 기본)의 예외 클래스.
뺀 것: tiktoken(토큰 수 계산), 직접 만든 재시도 반복문, re 마스킹, 스레드 시간 제한, lru_cache.
시간 제한·재시도: OpenAIEmbeddings의 request_timeout·max_retries 인자에 맡긴다(직접 만든 재시도 코드 없음).

Functions:
    FUN-300 get_embedding_model   FUN-301 build_embedding_text   FUN-302 fit_to_char_limit
    FUN-303 build_chunk_metadata  FUN-304 embed_texts            FUN-305 embed_chunks
    FUN-306 validate_embeddings   FUN-307 embed_and_store        FUN-308 embed_query
    FUN-309 check_embedding_health
"""
import time
from collections import Counter
from typing import Any, Optional

# numpy: 벡터 검증(NaN·0 확인)에 사용 (수업에서 코사인 계산에 쓴 라이브러리)
import numpy as np
# 수업에서 배운 OpenAI 임베딩 클래스
from langchain_openai import OpenAIEmbeddings
# openai SDK의 예외 클래스: 오류 종류(인증·한도·연결 등)를 구분해 재시도 가능 여부를 정하는 데만 쓴다
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from .models import (
    EmbeddedChunk,
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingFailure,
    EmbedStoreReport,
    VectorStoreError,
    get_api_key,
    get_chunk_value,
    get_model_name,
)
# 저장 단계 함수(미색인 선별, 저장)는 vector_store에서 가져온다
from .vector_store import filter_new_chunks, upsert_chunks

# 한 청크를 임베딩에 넣을 수 있는 글자 수 상한. 한글은 글자당 토큰이 많아 넉넉히 낮게 잡았다
MAX_INPUT_CHARS = 4000   # 글자 수 상한. 한글은 글자당 토큰이 많아 8191토큰 한도보다 넉넉히 낮게 잡았다.
# 검색어 길이 상한(search와 같은 값)
MAX_QUERY_CHARS = 2000   # 검색어·질문 길이 상한 (search의 MAX_QUERY_CHARS와 같은 값)
# 한 번에 임베딩 API로 보내는 청크 수
DEFAULT_BATCH_SIZE = 64

# OpenAI 응답 대기 시간(초)·라이브러리 자동 재시도 횟수. 용도별로 다르게 둔다(OpenAIEmbeddings의 request_timeout, max_retries 인자)
DOC_TIMEOUT_SEC, DOC_MAX_RETRIES = 30, 3        # 문서 청크 묶음 임베딩(업로드)
QUERY_TIMEOUT_SEC, QUERY_MAX_RETRIES = 10, 2    # 검색어·질문 임베딩(사용자가 기다리므로 짧게)
HEALTH_TIMEOUT_SEC, HEALTH_MAX_RETRIES = 5, 0   # 상태 점검(기존과 같은 5초, 재시도 없음)

# 같은 (모델, 키, 대기 시간, 재시도 횟수)면 OpenAIEmbeddings를 다시 만들지 않고 재사용하기 위한 보관함
_models: dict[tuple, Any] = {}  # (모델, 키, 대기 시간, 재시도) -> OpenAIEmbeddings. 같은 설정이면 객체를 재사용한다.


# ---------------------------------------------------------------------------
# 모델 준비
# ---------------------------------------------------------------------------


# FUN-300. OpenAIEmbeddings 객체를 돌려준다. 청크용·질문용이 항상 같은 모델을 쓰게 하는 단일 통로
def get_embedding_model(
    model_name: Optional[str] = None,
    *,
    timeout: float = DOC_TIMEOUT_SEC,
    max_retries: int = DOC_MAX_RETRIES,
):
    """FUN-300. OpenAIEmbeddings 객체를 만들고 같은 설정이면 재사용한다. API 키가 없으면 NO_API_KEY.

    timeout(초)·max_retries는 OpenAIEmbeddings의 request_timeout·max_retries로 넘긴다. 용도(문서·질문·점검)마다 다르게 쓴다.
    """
    # 키가 없으면 여기서 NO_API_KEY로 멈춘다
    api_key = get_api_key()
    if not api_key:
        raise EmbeddingError("NO_API_KEY", "OPENAI_API_KEY가 설정되어 있지 않습니다. .env를 확인하세요.")
    name = get_model_name(model_name)
    key = (name, api_key, timeout, max_retries)
    # 처음 요청이면 만들어 보관하고, 두 번째부터는 보관한 것을 돌려준다
    if key not in _models:
        _models[key] = OpenAIEmbeddings(
            model=name, api_key=api_key, request_timeout=timeout, max_retries=max_retries,
        )
    return _models[key]


# ---------------------------------------------------------------------------
# 입력 구성·길이 점검·메타데이터
# ---------------------------------------------------------------------------


# FUN-301. 임베딩에 넣을 글 만들기. 앞뒤 공백만 지우고 회사명·문서 유형은 글에 섞지 않는다
def build_embedding_text(chunk) -> str:
    """FUN-301. 임베딩에 넣을 글을 만든다. 청크 text의 앞뒤 공백만 지운다."""
    text = get_chunk_value(chunk, "text", "")
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        # 본문이 비면 임베딩할 수 없으므로 이 청크는 실패로 처리
        raise EmbeddingError("EMPTY_TEXT", "청크 본문이 비어 있습니다.")
    return text


# FUN-302. 글자 수가 상한을 넘으면 잘라서 (글, 잘렸는지)를 돌려준다
def fit_to_char_limit(text: str, max_chars: int = MAX_INPUT_CHARS) -> tuple[str, bool]:
    """FUN-302. 글자 수가 상한을 넘으면 잘라서 (처리된 글, 잘림 여부)를 돌려준다."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


# Chroma 메타데이터는 str·int·float·bool만 가능하다. None은 빈 문자열, 그 밖의 값은 문자열로 바꾼다
def _plain(value: Any) -> Any:
    """Chroma 메타데이터에 넣을 수 있는 값(str·int·float·bool)으로 바꾼다. None은 빈 문자열."""
    if value is None:
        return ""
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


# FUN-303. Chroma에 같이 저장할 메타데이터를 만든다. is_active는 False(비공개)로 시작한다
def build_chunk_metadata(chunk, *, model_name: str, truncated: bool = False) -> dict:
    """FUN-303. Chroma에 함께 저장할 메타데이터를 만든다. is_active는 False로 시작한다.

    chunk_id·document_id·version_id·company_id·doc_type 중 하나라도 비면 MISSING_METADATA.
    """
    # 청크에서 그대로 옮겨 담을 항목들
    keys = ("chunk_id", "document_id", "version_id", "company_id", "company_name",
            "doc_type", "file_name", "section_no", "location_label", "chunk_index")
    meta = {key: _plain(get_chunk_value(chunk, key)) for key in keys}
    meta["embedding_model"] = model_name
    meta["truncated"] = bool(truncated)
    # 검증 전에는 검색에 나오지 않도록 비공개로 저장(공개는 activate_version이 한다)
    meta["is_active"] = False
    meta["embedded_at"] = int(time.time())
    # 이 5개는 검색 조건·출처 표시에 꼭 필요하므로 비어 있으면 MISSING_METADATA로 거부
    for key in ("chunk_id", "document_id", "version_id", "company_id", "doc_type"):
        if str(meta[key]).strip() == "":
            raise EmbeddingError("MISSING_METADATA", f"필수 값이 비어 있습니다: {key}")
        meta[key] = str(meta[key])
    return meta


# ---------------------------------------------------------------------------
# 임베딩 호출
# ---------------------------------------------------------------------------


# 오류 문구에 API 키가 섞여 있으면 *** 로 가린다(로그·화면 노출 방지). 정규식 대신 str.replace 사용
def _mask_secrets(text: str) -> str:
    """오류 문구에 API 키가 섞여 있으면 *** 로 가린다. (정규식 대신 str.replace)"""
    api_key = get_api_key()
    return text.replace(api_key, "***") if api_key else text


# OpenAI 오류를 (오류 코드, 재시도 가능 여부)가 담긴 EmbeddingError로 바꾼다. 재시도 반복문은 없고 종류만 구분한다
def _api_error(exc: Exception) -> EmbeddingError:
    """openai 예외 클래스로 오류 종류를 구분한다. 다시 시도해도 소용없는 오류(인증·잘못된 요청·한도 소진)는 retryable=False.

    일시 오류(속도 제한·연결 끊김·시간 초과·서버 오류)는 retryable=True. 이 값은 db 작업 기록과 UI 재시도 안내에 쓰인다.
    """
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        code, retryable = "AUTH_ERROR", False
    elif isinstance(exc, (BadRequestError, NotFoundError, UnprocessableEntityError)):
        code, retryable = "BAD_REQUEST", False
    elif isinstance(exc, RateLimitError):
        # 같은 429라도 '한도 소진'은 기다려도 해결되지 않는다
        if "insufficient_quota" in str(exc) or "exceeded your current quota" in str(exc).lower():
            code, retryable = "QUOTA_EXCEEDED", False
        else:
            code, retryable = "RATE_LIMIT", True
    elif isinstance(exc, (APITimeoutError, TimeoutError)):  # APITimeoutError는 APIConnectionError의 하위라 먼저 검사
        code, retryable = "TIMEOUT", True
    elif isinstance(exc, (APIConnectionError, ConnectionError)):
        code, retryable = "CONNECTION", True
    elif isinstance(exc, InternalServerError):
        code, retryable = "SERVER_ERROR", True
    else:
        code, retryable = "UNKNOWN_ERROR", False
    return EmbeddingError(code, _mask_secrets(f"{type(exc).__name__}: {exc}")[:300], retryable=retryable)


# FUN-304. 글 여러 개를 한 번에 임베딩 API로 보낸다. 실패하면 오류 종류에 맞는 EmbeddingError
def embed_texts(texts: list[str]) -> list[list[float]]:
    """FUN-304. 글 한 묶음을 OpenAIEmbeddings.embed_documents로 임베딩한다.

    응답 대기·재시도는 OpenAIEmbeddings(request_timeout, max_retries)가 맡는다. 실패하면 _api_error가 구분한 코드로 올린다.
    """
    if not texts:
        return []
    # 모델 준비(키 없으면 여기서 오류)
    model = get_embedding_model()  # NO_API_KEY는 여기서 바로 올라간다.
    try:
        # 수업에서 배운 OpenAIEmbeddings의 문서용 임베딩 호출(embed_query의 여러 개 버전)
        vectors = model.embed_documents(list(texts))
    except Exception as exc:
        raise _api_error(exc) from None
    # 보낸 개수와 받은 개수가 다르면 응답이 이상한 것
    if len(vectors) != len(texts):
        raise EmbeddingError("BAD_RESPONSE", f"입력 {len(texts)}개에 결과 {len(vectors)}개가 돌아왔습니다.")
    return [[float(x) for x in v] for v in vectors]


# ---------------------------------------------------------------------------
# 청크 임베딩·검증
# ---------------------------------------------------------------------------


# 오류를 '실패 기록(EmbeddingFailure)'으로 바꾼다. 메시지의 비밀 값은 가린다
def _failure(chunk, error: EmbeddingError) -> EmbeddingFailure:
    return EmbeddingFailure(
        chunk_id=str(get_chunk_value(chunk, "chunk_id", "")),
        code=error.code,
        message=_mask_secrets(error.message),
        retryable=error.retryable,
    )


# FUN-305. 청크 여러 개를 임베딩한다. 한 청크가 실패해도 멈추지 않고 나머지를 계속 처리한다
def embed_chunks(chunks: list, *, batch_size: int = DEFAULT_BATCH_SIZE) -> EmbeddingBatch:
    """FUN-305. 청크를 batch_size개씩 묶어 입력 구성·길이 점검·임베딩을 거친다.

    실패한 청크(빈 글, 필수 메타데이터 없음, 실패한 묶음)는 failures에 담고 나머지는 계속 처리한다.
    """
    result = EmbeddingBatch()
    if not chunks:
        return result
    model_name = get_model_name()

    # 1단계: 청크마다 글 만들기·길이 자르기·메타데이터 만들기(API 호출 전에 걸러낸다)
    prepared = []  # (청크, 글, 메타데이터, 잘림)
    for chunk in chunks:
        try:
            text, truncated = fit_to_char_limit(build_embedding_text(chunk))
            meta = build_chunk_metadata(chunk, model_name=model_name, truncated=truncated)
            prepared.append((chunk, text, meta, truncated))
        except EmbeddingError as error:
            result.failures.append(_failure(chunk, error))

    # 2단계: batch_size개씩 묶어 API로 보낸다(0 이하가 들어와도 최소 1)
    size = max(1, int(batch_size))
    for start in range(0, len(prepared), size):
        group = prepared[start:start + size]
        try:
            # 묶음 단위로 임베딩. 묶음이 실패하면 그 묶음의 청크 모두를 실패로 기록하고 다음 묶음으로 넘어간다
            vectors = embed_texts([item[1] for item in group])
        except EmbeddingError as error:
            for item in group:
                result.failures.append(_failure(item[0], error))
            continue
        for (chunk, _text, meta, truncated), vector in zip(group, vectors):
            result.items.append(EmbeddedChunk(chunk=chunk, vector=vector, metadata=meta, truncated=truncated))
    return result


# FUN-306. 쓸 수 없는 벡터(길이 다름·NaN·무한대·전부 0)의 위치 목록을 돌려준다. 빈 목록이면 모두 정상
def validate_embeddings(vectors: list[list[float]], expected_dim: Optional[int] = None) -> list[int]:
    """FUN-306. 차원이 다르거나 NaN·무한대가 있거나 전부 0인 벡터의 위치 목록을 돌려준다(빈 목록이면 정상).

    expected_dim을 안 주면 가장 많은 벡터가 가진 길이를 기준으로 삼는다(Counter).
    """
    if not vectors:
        return []
    if expected_dim is None:
        # 기준 길이를 안 주면 가장 많은 벡터의 길이를 기준으로 삼는다(Counter)
        expected_dim = Counter(len(v) for v in vectors).most_common(1)[0][0]
    bad = []
    for index, vector in enumerate(vectors):
        array = np.array(vector, dtype=float)
        if len(vector) != expected_dim or expected_dim == 0 or not np.isfinite(array).all() or not array.any():
            bad.append(index)
    return bad


# ---------------------------------------------------------------------------
# 일괄 실행
# ---------------------------------------------------------------------------


# 한 번에 같은 문서·버전·회사의 청크만 처리하는지, chunk_id가 겹치지 않는지 확인하고 (문서, 버전, 회사)를 돌려준다
def _check_scope(chunks: list) -> tuple[str, str, str]:
    """한 번에 넘긴 청크가 같은 문서·버전·회사인지, chunk_id가 겹치지 않는지 확인한다."""
    if not chunks:
        raise EmbeddingError("EMPTY_CHUNKS", "임베딩할 청크가 없습니다.")
    scopes = set()
    for c in chunks:
        scopes.add(tuple(str(get_chunk_value(c, key, "")) for key in ("document_id", "version_id", "company_id")))
    if len(scopes) != 1:
        raise EmbeddingError("MIXED_SCOPE", "한 번에 같은 문서·버전·회사의 청크만 처리할 수 있습니다.")
    ids = [str(get_chunk_value(c, "chunk_id", "")) for c in chunks]
    if len(set(ids)) != len(ids):
        raise EmbeddingError("DUPLICATE_CHUNK_ID", "같은 chunk_id가 두 번 이상 들어 있습니다.")
    return next(iter(scopes))


# FUN-307. 업로드 흐름이 부르는 대표 함수. 묶음 검사 → 미색인 선별 → 임베딩 → 검증 → 비공개 저장 → 보고서
def embed_and_store(chunks: list, *, batch_size: int = DEFAULT_BATCH_SIZE) -> EmbedStoreReport:
    """FUN-307. 업로드 처리 흐름이 부르는 대표 함수.

    묶음 검사 → 미색인 선별 → 임베딩 → 벡터 검증 → Chroma 저장(비공개) 순으로 실행하고 보고서를 돌려준다.
    공개(activate_version)는 하지 않는다. status가 partial이어도 호출한 쪽은 실패로 취급해야 한다.
    """
    started = time.time()
    document_id, version_id, company_id = _check_scope(chunks)
    report = EmbedStoreReport(
        document_id=document_id, version_id=version_id, company_id=company_id,
        model_name=get_model_name(), total=len(chunks),
    )

    # 이미 저장된 청크는 다시 임베딩하지 않는다(비용 절약, 재실행해도 안전)
    new_chunks = filter_new_chunks(chunks)  # VectorStoreError(COMPANY_MISMATCH 등)는 그대로 올린다.
    report.skipped_existing = len(chunks) - len(new_chunks)

    batch = embed_chunks(new_chunks, batch_size=batch_size)
    report.failures.extend(batch.failures)

    items = batch.items
    # 저장 전에 벡터를 검증해 쓸 수 없는 것은 INVALID_VECTOR로 기록하고 저장에서 뺀다
    bad = validate_embeddings([item.vector for item in items])
    for index in bad:
        report.failures.append(EmbeddingFailure(
            chunk_id=str(get_chunk_value(items[index].chunk, "chunk_id", "")),
            code="INVALID_VECTOR", message="차원이 다르거나 NaN·무한대·전부 0인 벡터입니다.", retryable=True,
        ))
    good = [item for i, item in enumerate(items) if i not in bad]
    report.embedded = len(good)
    report.truncated_count = sum(1 for item in good if item.truncated)

    if good:
        try:
            # 좋은 벡터만 Chroma에 저장(비공개 상태)
            report.stored = upsert_chunks(
                [i.chunk for i in good], [i.vector for i in good], [i.metadata for i in good], batch_size=batch_size,
            )
        except VectorStoreError as error:
            for i in good:
                report.failures.append(EmbeddingFailure(
                    chunk_id=str(get_chunk_value(i.chunk, "chunk_id", "")),
                    code=error.code, message=error.message, retryable=True,
                ))
            report.stored = 0

    # 결과 판정: 실패 없으면 success, 실패가 있어도 저장·건너뜀이 있으면 partial, 하나도 없으면 failed. partial도 호출한 쪽은 실패로 취급해야 한다
    if not report.failures:
        report.status = "success"
    elif report.stored + report.skipped_existing > 0:
        report.status = "partial"
    else:
        report.status = "failed"
    report.elapsed_sec = round(time.time() - started, 3)
    return report


# ---------------------------------------------------------------------------
# 질문 임베딩·상태 점검
# ---------------------------------------------------------------------------


# 글 하나를 주어진 모델로 임베딩하고 벡터가 정상인지 확인한다(embed_query와 상태 점검이 함께 쓴다)
def _embed_one(text: str, model) -> list[float]:
    try:
        vector = [float(x) for x in model.embed_query(text)]
    except Exception as exc:
        raise _api_error(exc) from None
    if validate_embeddings([vector]):
        raise EmbeddingError("INVALID_VECTOR", "질문 벡터가 비정상입니다.", retryable=True)
    return vector


# FUN-308. 검색어·질문 하나를 청크와 같은 모델로 임베딩한다(OpenAIEmbeddings.embed_query). 1~2000자만 허용
def embed_query(query: str) -> list[float]:
    """FUN-308. 검색어·질문을 청크와 같은 모델로 임베딩한다(OpenAIEmbeddings.embed_query). 1~2000자만 허용.

    사용자가 기다리는 호출이라 대기 시간을 짧게 둔다(QUERY_TIMEOUT_SEC, QUERY_MAX_RETRIES).
    """
    text = query.strip() if isinstance(query, str) else ""
    if not text:
        raise EmbeddingError("EMPTY_QUERY", "검색어가 비어 있습니다.")
    if len(text) > MAX_QUERY_CHARS:
        raise EmbeddingError("QUERY_TOO_LONG", f"검색어는 {MAX_QUERY_CHARS}자 이하여야 합니다.")
    return _embed_one(text, get_embedding_model(timeout=QUERY_TIMEOUT_SEC, max_retries=QUERY_MAX_RETRIES))


# FUN-309. 시험 문장 하나를 임베딩해 모델·키가 동작하는지 점검한다. 예외 대신 ok=False 딕셔너리로 알려준다
def check_embedding_health() -> dict:
    """FUN-309. 시험 문장을 임베딩해 모델·API 키가 동작하는지 점검한다. 오류는 ok=False로 돌려준다.

    최대 HEALTH_TIMEOUT_SEC(5초)만 기다리고 재시도하지 않는다.
    """
    try:
        model_name = get_model_name()
        vector = _embed_one(
            "health check", get_embedding_model(timeout=HEALTH_TIMEOUT_SEC, max_retries=HEALTH_MAX_RETRIES),
        )
        return {"ok": True, "model": model_name, "dim": len(vector), "detail": "정상"}
    except EmbeddingError as error:
        return {"ok": False, "model": "", "dim": 0, "detail": f"{error.code}: {error.message}"}
    except Exception as exc:
        return {"ok": False, "model": "", "dim": 0, "detail": f"점검 중 오류: {type(exc).__name__}"}
