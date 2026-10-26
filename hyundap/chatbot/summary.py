"""chatbot/summary.py — 선택 문서의 청크를 나눠 요약하고 포함 범위를 계산.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-124)

구현 예정 함수:
    FUN-420 (함수 이름 미정)
    FUN-421 summarize_batch — 청크 묶음별 중간 요약을 만든다.
    FUN-422 merge_summaries — 중간 요약을 하나의 문서 요약으로 합친다.
    FUN-423 compute_coverage — 전체 청크 중 요약에 포함한 범위를 계산한다.
    FUN-424 limit_summary_chunks — 요약 입력 상한과 제외 범위를 정한다.

현재 파일은 설계 뼈대다. 위 설명과 명세 목록은 담당 역할을 안내하며 실제 기능 구현을 뜻하지 않는다."""
