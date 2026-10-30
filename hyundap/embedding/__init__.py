"""embedding/__init__.py - 공개 함수 모음

Owner: 박영준
Spec: AI_문서검색_통합_기능명세서 (MOD-100)

다른 폴더는 아래 공개 함수만 가져다 쓴다.

    from hyundap.embedding import embed_and_store, activate_version, delete_vectors   # db / 업로드 흐름
    from hyundap.embedding import embed_query, build_where_filter, query_vectors      # search
    from hyundap.embedding import embed_query, get_document_chunks                    # chatbot

(import 기준은 이슈 #131: 다른 폴더는 hyundap. 전체 경로. 저장소 맨 위에서 실행한다.)
"""
from .embedder import (
    build_chunk_metadata,
    build_embedding_text,
    check_embedding_health,
    embed_and_store,
    embed_texts,
    embed_chunks,
    embed_query,
    fit_to_char_limit,
    format_report_log,
    get_embedding_model,
    validate_embeddings,
)
from .models import (
    EmbeddedChunk,
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingFailure,
    EmbedStoreReport,
    VectorHit,
    VectorStoreError,
)
from .vector_store import (
    activate_version,
    build_where_filter,
    check_vector_store_health,
    collection_name_for_model,
    delete_vectors,
    filter_new_chunks,
    get_vector_store,
    get_document_chunks,
    get_version_chunk_ids,
    query_vectors,
    upsert_chunks,
)

__all__ = [
    # 임베딩 생성 (embedder.py)
    "get_embedding_model", "build_embedding_text", "fit_to_char_limit", "build_chunk_metadata",
    "embed_texts", "embed_chunks", "validate_embeddings", "embed_and_store",
    "embed_query", "check_embedding_health", "format_report_log",
    # 벡터 저장·조회 (vector_store.py)
    "collection_name_for_model", "get_vector_store", "filter_new_chunks", "upsert_chunks",
    "build_where_filter", "query_vectors", "activate_version", "delete_vectors",
    "get_version_chunk_ids", "get_document_chunks", "check_vector_store_health",
    # 결과 형식·오류 (models.py)
    "EmbeddedChunk", "EmbeddingFailure", "EmbeddingBatch", "EmbedStoreReport", "VectorHit",
    "EmbeddingError", "VectorStoreError",
]
