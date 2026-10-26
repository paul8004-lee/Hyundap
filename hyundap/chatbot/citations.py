"""chatbot/citations.py — 인용 번호를 검증하고 원본 출처를 구성.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-123)

구현 예정 함수:
    FUN-411 assign_aliases — 근거마다 인용용 이름표를 부여한다.
    FUN-412 format_evidence — 이름표와 청크를 프롬프트용 근거 문자열로 만든다.
    FUN-413 extract_cited_aliases — 답변에서 사용된 인용 이름표를 추출한다.
    FUN-414 validate_citations — 인용 누락이나 제공하지 않은 이름표 사용을 검사한다.
    FUN-415 build_citation — 원본 문서·버전·위치에 연결되는 출처를 만든다.
    FUN-416 make_excerpt — 화면 표시 길이에 맞춰 본문 발췌를 만든다.
    FUN-417 format_source_label — 회사·파일·위치를 읽기 쉬운 출처 문구로 만든다.
    FUN-418 render_answer — 내부 인용 표기를 화면용 답변으로 바꾼다.
    FUN-419 group_citations_by_company — 출처를 회사별로 묶는다.

현재 파일은 설계 뼈대다. 위 설명과 명세 목록은 담당 역할을 안내하며 실제 기능 구현을 뜻하지 않는다."""
