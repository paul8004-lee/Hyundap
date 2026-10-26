"""ingest/chunker.py — 섹션 경계를 보존하며 공통 문서를 청크로 분할.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

구현 예정 함수:
    FUN-215 to_langchain_documents — 공통 섹션을 위치 정보가 있는 LangChain Document로 바꾼다.
    FUN-216 build_chunk — 청크 ID·순번·출처 정보를 보존한 Chunk를 만든다.
    FUN-217 chunk_document — 섹션을 넘지 않도록 텍스트를 분할한다.

현재 파일은 설계 뼈대다. 위 설명과 명세 목록은 담당 역할을 안내하며 실제 기능 구현을 뜻하지 않는다."""
