"""embedding/vector_store.py — Chroma 벡터 저장·조회·버전 활성화·삭제.

담당: 박영준
기준 명세: AI_문서검색_통합_기능명세서 (MOD-102)

구현 예정 함수:
    FUN-310 collection_name_for_model — 모델별 컬렉션 이름을 결정한다.
    FUN-311 get_collection — 설정된 Chroma 컬렉션을 연다.
    FUN-312 filter_new_chunks — 이미 적재된 청크를 구분해 재적재를 줄인다.
    FUN-313 upsert_chunks — 청크 ID를 기준으로 벡터와 메타데이터를 저장한다.
    FUN-314 build_where_filter — 회사 범위를 포함한 벡터 검색 조건을 만든다.
    FUN-315 query_vectors — 조건에 맞는 벡터 후보를 유사도 순으로 조회한다.
    FUN-316 activate_version — 새 버전 벡터를 켜고 같은 문서의 이전 버전을 내린다.
    FUN-317 delete_vectors — 문서 전체 또는 특정 버전의 벡터를 삭제한다.
    FUN-318 get_version_chunk_ids — 완전성 비교에 사용할 버전별 실제 청크 ID를 조회한다.
    FUN-319 get_document_chunks — 요약용 청크를 문서 안의 순서대로 조회한다.
    FUN-320 check_vector_store_health — 벡터 저장소와 컬렉션의 상태를 확인한다.

구현 예정 자료형:
    CLS-108 ChunkMetadata

현재 파일은 설계 뼈대다. 위 설명과 명세 목록은 담당 역할을 안내하며 실제 기능 구현을 뜻하지 않는다."""
