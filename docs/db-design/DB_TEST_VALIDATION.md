# db_test 브랜치 DB·메서드 동작 검증

검증일: 2026-10-26. 기존 db_tset에서 db_test를 만들고 feature/db의
4ce3778을 병합했다. 아래 결과는 병합 후 발견한 수정까지 포함한다.

## 결과

**108개 통과, 실패 0, 오류 0, 건너뜀 0.**
Python 3.11.9 / SQLAlchemy 2.0.55 / PyMySQL 1.1.2 / 별도 로컬 MySQL 8.4.13에서 실행했다.
기존 사용자 DB와 UI용 DB는 재사용하지 않았다.

| 검증 범위 | 결과 |
|---|---|
| 문서 메타데이터·회사별 중복·후보 생성·트랜잭션 롤백·동시성 | 통과 |
| 수집 콜백·보고서 정규화·manifest·개수 정합성 | 통과 |
| 현재 세션·권한·초/밀리초 만료·기본 UTC Unix 초 | 통과 |
| 원본 저장·다중 문서 업로드·동일 문서 다음 버전·게시·실패 복구 | 통과 |
| 검색·문장형 질문 전달·검색 오류와 권한 처리 | 통과. 외부 API·벡터 호출은 테스트 대역 |

## 발견한 문제와 수정

1. test_legacy_db의 업로드 테스트 중간 lambda가 새 find_by_hash 인자를 받지 못했다.
   표준 functools.partial로 바꿔 실제 수집 콜백 계약을 전달한다.
2. upload_service의 공통 임시 storage_path='pending'이 실제 v3 storage_key UNIQUE와 충돌했다.
   임시 경로에 UUID를 붙이고 신규 문서의 첫 원본 경로를 최종 목적지로 같은 TX에서 확정한다.
   기존 문서의 첫 원본 포인터는 다음 버전 업로드 시 덮어쓰지 않는다.

별도 문서 두 개의 업로드와 storage_key/storage_path 일치를 검사하는 회귀 테스트를 추가했다.
수정 전 CONFLICT를 재현했고, 수정 후 해당 테스트 및 동일 문서 두 차례 게시가 통과했다.

## Railway DB 읽기 전용 점검

실제 .env 연결 성공, MySQL 8.4.11, 스키마 버전 3.
v4 구조 진단 36개는 종전 35개에 token_hash의 ascii_bin 검사 1개를 추가한 결과다.
일반 SQL 준비는 여전히 Incompatible existing type: documents.deleted_at으로 차단된다.
실제 DB의 DDL/DML·마이그레이션 버전은 변경하지 않았다.
따라서 로컬 메서드 검증 성공은 실제 Railway DB에서 P1이 준비됐다는 의미가 아니다.

## 재실행

각 테스트가 허용하는 127.0.0.1의 빈 전용 DB에 테스트 URL을 설정한 뒤 실행한다.
실제 Railway DATABASE_URL을 테스트 URL로 사용하지 않는다.

```powershell
python -m unittest hyundap.tests.test_access_queries hyundap.tests.test_ingest_storage hyundap.tests.test_document_storage hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow hyundap.tests.test_legacy_db hyundap.tests.test_ingest_bridge hyundap.tests.test_semantic_ui_bridge hyundap.tests.test_search
```

이번 실행은 실제 OpenAI 임베딩 호출이나 브라우저 UI 조작을 포함하지 않는다.
실제 임베딩을 사용했던 이전 결과는 SEMANTIC_SEARCH_TEST.md에 별도로 기록되어 있다.

## 2026-10-27 실패·재시도 상태 전이 보완

추가 회귀 3개로 복구 완료 후 기존 작업 종료와 다음 버전 생성, 외부 재시도 콜백 전
상태·시도 횟수·임대 초기화, 활성 작업 충돌 시 후보·작업 전체 롤백을 검증했다.
수정 전 3개 모두 실패했고 수정 후 통과했다. 별도 빈 MySQL 8.4.13에서 같은 11개 모듈을
재실행한 결과는 **111개 통과, 실패 0, 오류 0, 건너뜀 0**이다.
이전 108개 결과는 당시 이력이며, 현재 추가 검증 범위는 2026-10-27 검토 보고서의 조치 완료 절을 따른다.
Railway 실제 DB 변경이나 새 외부 임베딩 API 호출은 포함하지 않는다.

## PR #166 피드백 반영 후 전체 검증

2026-10-27, Python 3.11.9 / SQLAlchemy 2.0.55 / PyMySQL 1.1.2 /
새 로컬 MySQL 8.4.13의 빈 전용 DB 4개를 준비해 전체 테스트를 실행했다.

```powershell
python -m unittest discover -s hyundap/tests -t .
```

결과는 **240개 통과, 실패 0, 오류 0, 건너뜀 0**이다. 이전 111개는 DB·검색 관련
선택 모듈의 결과이고, 이번 240개는 전체 발견 테스트의 결과다.
테이블 구조 캐시의 Engine/연결 재사용·격리·DDL 후 초기화·조회 실패 재시도 4개와
시험 어댑터 import 시 hyundap.app/Gradio를 불러오지 않는 새 프로세스 검사 1개를 추가했다.

수동 API/UI 점검은 tests/smoke로 이동하고 공용 시험 어댑터는 smoke.services로 분리했다.
실제 수동 API 호출은 이번 실행에 포함하지 않는다. DB·수동 점검 코드 및 변경 Python
독스트링의 한자·깨진 문자도 검사해 남은 항목이 없음을 확인했다.
향후 v4 단일화 계획은 DB_V4_SIMPLIFICATION_PLAN.md에 저장했으며 아직 적용안이다.
