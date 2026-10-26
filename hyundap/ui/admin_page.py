"""ui/admin_page.py — 업로드·권한 설정·처리 로그 화면.

담당: 박경환
기준 명세: AI_문서검색_통합_기능명세서 (MOD-035)

구현 예정 함수:
    FUN-167 build_admin_page — 관리 화면의 구성 요소와 이벤트를 연결한다.
    FUN-168 on_upload_submit — 선택 파일을 저장·수집 담당 흐름에 전달하고 결과를 표시한다.
    FUN-169 load_account_options — auth·DB가 제공하는 계정·역할 선택지를 표시한다.
    FUN-170 on_role_change_submit — 확인한 역할 변경 요청을 담당 함수에 전달한다.
    FUN-171 load_log_view — DB가 제공하는 처리 기록을 화면에 표시한다.

현재 파일은 설계 뼈대다. 위 설명과 명세 목록은 담당 역할을 안내하며 실제 기능 구현을 뜻하지 않는다."""
