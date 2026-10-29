# 이전 v3 → v4 이행 절차 폐기

2026-10-27 사용자 결정으로 v3 이행 도구·SQL·호환 설정을 폐기했다. 이 경로는 기존 문서 링크를 위한 안내만 남기며 실행할 이행 절차는 제공하지 않는다.

현재 방식은 [최종 v4 빈 DB 직접 생성](V4_INITIALIZATION.md)이다. `schema --prepare`, `--prepare-legacy`, `legacy.insert`와 004 이행 SQL은 지원하지 않는다. 기존 Railway DB를 자동 변경하지 않는다.
