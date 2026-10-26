"""search/filters.py — 허용 회사·문서 유형을 검색 필터로 조합.

담당: 이준영
기준 명세: AI_문서검색_통합_기능명세서 (MOD-053)

구현 예정 함수:
    FUN-253 resolve_company_scope — auth 허용 회사와 사용자의 선택 범위를 결합한다.
    FUN-254 normalize_doc_types — 문서 유형의 공백·중복·허용값을 정리한다.
    FUN-255 build_search_filter — 회사·문서 유형·문서 ID 조건을 조합한다.

현재 파일은 설계 뼈대다. 위 설명과 명세 목록은 담당 역할을 안내하며 실제 기능 구현을 뜻하지 않는다."""
