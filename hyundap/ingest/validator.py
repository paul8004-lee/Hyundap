"""ingest/validator.py — 업로드 형식·크기·암호화·손상·파일 해시 검증.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

구현 예정 함수:
    FUN-200 validate_upload — 파일 형식부터 중복까지 정해진 순서로 검사한다.
    FUN-201 check_file_format — 확장자와 파일 시그니처로 지원 형식을 검사한다.
    FUN-202 check_file_size — 파일 크기가 허용 범위 안인지 확인한다.
    FUN-203 check_not_encrypted — 암호화된 문서를 거부한다.
    FUN-204 check_not_corrupted — 파일을 정상적으로 읽을 수 있는지 검사한다.
    FUN-205 compute_file_hash — 파일 내용의 SHA-256을 계산한다.
    FUN-206 check_duplicate — DB 조회 함수로 회사별 동일 파일을 확인한다.

현재 파일은 설계 뼈대다. 위 설명과 명세 목록은 담당 역할을 안내하며 실제 기능 구현을 뜻하지 않는다."""
