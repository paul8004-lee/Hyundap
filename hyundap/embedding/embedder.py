"""embedding/embedder.py — 문서 청크·검색어 임베딩과 적재 결과 보고.

담당: 박영준
기준 명세: AI_문서검색_통합_기능명세서 (MOD-101)

구현 예정 함수:
    FUN-300 get_embedding_model — 공통 설정에 맞는 임베딩 모델을 준비한다.
    FUN-301 build_embedding_text — 청크에서 임베딩에 사용할 문자열을 만든다.
    FUN-302 fit_to_token_limit — 모델 입력 토큰 상한에 맞춰 텍스트를 조정한다.
    FUN-303 build_chunk_metadata — 출처·회사·버전 정보를 벡터 메타데이터로 만든다.
    FUN-304 embed_batch_with_retry — 재시도 가능한 오류에 한해 묶음 임베딩을 재시도한다.
    FUN-305 embed_chunks — 문서 청크 묶음의 임베딩을 생성한다.
    FUN-306 validate_embeddings — 벡터 개수·차원 등 적재 전 조건을 검사한다.
    FUN-307 embed_and_store — 청크를 임베딩·비공개 저장하고 결과 보고서를 돌려준다.
    FUN-308 embed_query — 검색어나 질문을 청크와 같은 모델로 임베딩한다.
    FUN-309 check_embedding_health — 임베딩 모델과 호출 설정의 사용 가능 상태를 확인한다.

현재 파일은 설계 뼈대다. 위 설명과 명세 목록은 담당 역할을 안내하며 실제 기능 구현을 뜻하지 않는다."""
