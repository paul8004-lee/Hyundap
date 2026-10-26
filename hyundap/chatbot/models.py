"""chatbot/models.py — 챗봇 내부 데이터와 오류 형식.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-125)

구현 예정 함수:
    FUN-425 ChatbotError.__init__
    FUN-426 to_feedback — 내부 오류를 사용자 안내로 바꾼다.
    FUN-427 map_dependency_error — 외부 모듈 오류를 챗봇 오류로 변환한다.

구현 예정 자료형:
    CLS-120 ChatbotError

현재 파일은 설계 뼈대다. 위 설명과 명세 목록은 담당 역할을 안내하며 실제 기능 구현을 뜻하지 않는다."""
