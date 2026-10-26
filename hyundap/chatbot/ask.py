"""chatbot/ask.py — 질문 검사부터 근거 검색·답변·반환 전 재검증까지 연결.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-121)

구현 예정 함수:
    FUN-401 ask — 질문·검색·답변·인용 검증 흐름을 연결한다.
    FUN-402 validate_question — 질문 공백과 길이 제한을 검사한다.
    FUN-403 retrieve_evidence — 권한과 필터가 적용된 근거 청크를 받는다.
    FUN-404 select_evidence — 답변에 사용할 근거를 고른다.
    FUN-405 generate_answer — 질문과 근거를 LLM 체인에 전달한다.
    FUN-406 build_no_evidence_response — 근거가 없을 때 답변 유보 결과를 만든다.
    FUN-407 recheck_before_return — 반환 직전 auth에 문서·버전 접근을 다시 확인한다.

현재 파일은 설계 뼈대다. 위 설명과 명세 목록은 담당 역할을 안내하며 실제 기능 구현을 뜻하지 않는다."""
