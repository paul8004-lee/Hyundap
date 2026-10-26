# db01 · DB P1 구현 준비 (DB 담당 범위 한정)

확인일: 2026-10-26. 기준 커밋: develop `98d428e`, 현재 브랜치 db01.
사용자 요청에 따라 Desktop의 `DB_P1_구현_계획_DB담당_범위한정.md`를 검토했다.
첨부 문서의 제안 서명은 아직 구현 계약으로 확정하지 않는다. 이 기록은 이전의
P1 전체 연동 준비 범위를 대체하며, 코드 구현·DB 실측 완료를 의미하지 않는다.

## 1. 작업 경계

DB 산출물은 스키마 비교, 저장소 함수 변경, 입력·반환·오류·트랜잭션 계약,
DB 정합성 테스트다. 파일 처리, 수집 콜백, 모델·Chroma 호출, 인증 발급,
검색·챗봇·UI 연결과 전체 통합 구축은 이번 구현 범위에서 제외한다.
기존 혼합 서비스는 삭제·이동하지 않고 필요한 SQL 부분만 검토한다.
다른 담당자 배정이나 메시지 전송, 공유 DB DDL/DML은 수행하지 않았다.

## 2. 실제 코드 확인과 변경 후보

| 작업 | 현재 근거 | DB 준비 결정 | 예상 |
|---|---|---|---|
| 스키마 호환성 | schema.py의 snapshot/prepare_sql/validate_v4/audit_counts 구현됨 | 기존 읽기 전용 점검 재사용. 접속 설정 부재로 실제 구조 비교만 대기 | 2~4시간 |
| 메타데이터·중복 | documents_repo.find_duplicate_document에 제외 인자 없음. validate_metadata는 doc_type 필수·허용값 하드코딩. 생성은 회사 잠금 뒤 최종 중복 검사 | 제외 조회 계약과 공통 DOC_TYPE_CODES/DEFAULT_DOC_TYPE 사용 여부를 확정한 뒤 저장소만 수정 | 2~3시간 |
| 청크·보고서 | chunks_repo.save_manifest가 ID·연속 순번·해시·재저장 동일성 검사. jobs_repo 허용 목록에 수집 전용 필드 없음 | 청크 저장 재사용. 보고서 필드별 타입·범위와 count 일치 검사 위치 확정 | 2~3시간 |
| 권한 상태 조회 | sessions_repo.current_actor와 auth.permissions의 회사 조회·접근 재검증 구현됨. DOCUMENT_INFO_SQL 11필드 존재 | DB 함수 테스트와 문서 점검 우선. 인증 정책·호출자 연결 변경 없음 | 1~2시간 |
| SQL 전이·회귀 | version_service의 prepare_publication/store_pending_publication/restore_database_pointer 등 구현됨 | 기존 SQL 경계·조건부 갱신·롤백·동시성 검증 재사용. 외부 실행 흐름은 변경하지 않음 | 2~3시간 |

원안은 9~15시간 추정이다. 세부 단계별 환경·계약·검증 시간을 나눈 최신 추정은 약 11~19시간이며 신규 MySQL 구축·접속 대기·계약 응답 시간은 별도다.

세부 실행 순서·완료 기준·의존 관계와 등록된 GitHub 이슈 #152~#163은
[P1_IMPLEMENTATION_STEPS.md](P1_IMPLEMENTATION_STEPS.md)에 정리했다.

## 3. 구현 전 확정할 저장 계약

1. 중복 조회 제안: `find_duplicate_document(company_id, sha256, *, exclude_version_id=None, connection=None)` → dict 또는 None. 제외 ID는 서버가 확정한 후보만 사용하며 같은 연결에서 존재·회사·해시를 검증한다. 잘못된 ID/회사/해시는 INVALID_INPUT으로 거부하는 안을 계약에 명시한다. 인자 생략 시 삭제·실패 포함 기존 정책 유지. 후보 최종 생성은 제외 인자를 사용하지 않는다.
2. 중복 보호: `_check_candidate_creation`의 회사 행 잠금과 v4의 UNIQUE(company_id, sha256)를 유지한다. 조회 자체는 동시성 보호가 아니다. 이 UNIQUE가 있으면 같은 회사·해시의 다른 버전은 공존하지 못하므로, '다른 기존 버전 중복' 테스트는 실제 제약과 양립하게 구성하고 이를 위해 제약을 약화하지 않는다.
3. 보고서 제안: parser/parser_version은 합의된 값·길이만, chunk_size는 양의 정수, chunk_overlap은 0 이상이며 둘 다 있으면 size 미만, section_count/chunk_count/warning_count는 bool을 제외한 0 이상 정수. warnings 원문은 저장하지 않는다. 경고 요약 생산은 호출자 책임이다. 청크 수 일치 확인은 같은 연결에서 저장된 expected_chunk_count와 비교할지 먼저 확정한다. normalize_report 단독으로는 청크 목록을 받지 않는다.
4. 조회: 현재 서버 세션 증명을 DB에서 재검증한다. 11필드 반환의 활성 버전 부재는 버전 필드 None, 없는 문서는 누락, 빈 ID 목록은 [], DB 장애는 DB_UNAVAILABLE이다. 접근 검사는 잘못된 입력 INVALID_INPUT, 만료 SESSION_EXPIRED, 권한 밖 ACCESS_DENIED, 문서·버전 상태 변경 STATE_CHANGED를 유지한다.
5. 트랜잭션: connection 인자가 있으면 호출자 TX에 참여하며 커밋·종료하지 않는다. 인자가 없으면 runtime.transaction이 TX를 소유한다. 중복 키 CONFLICT와 사전 중복 검사 DUPLICATE_FILE의 차이를 문서화한다. 파일·외부 API 실행은 SQL TX에 포함하지 않는다.

위 내용은 제안/검토 결과이며 DB_contract.md의 기존 확정 계약을 아직 변경하지 않았다.

## 4. DB 검증 준비

기존 실행 결과: Python 3.11.9, 아래 45개 중 10개 통과·35개 skip·실패 0.
코드 변경이 없어 동일 테스트를 반복 실행하지 않았다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

| 확인할 항목 | 재사용할 테스트/보완 위치 |
|---|---|
| 삭제·실패 포함 중복·다른 회사 동일 해시 | test_db_runtime의 test_duplicate_failed_deleted_and_cross_company_files 확장: 제외 미사용/자기 후보 제외/잘못된 제외 문맥 |
| 저장 원자성·경쟁 생성 | test_failed_job_insert_rolls_back_document_and_version, test_concurrent_duplicate_upload_creates_only_one_candidate |
| 청크·보고서 | test_manifest_idempotency_conflict_and_active_version_protection, test_reports_preserve_stages_and_reject_late_results + 수집 필드·bool·범위·수치 모순·비밀값 제거 사례 |
| 현재 권한·문서 상태 | 기존 만료·폐기·auth_version·비활성·삭제·평가·버전 변경 테스트 재사용. 빈 version_id 사례 명시 확인 |
| 게시·복구 SQL | 기존 workflow 테스트의 입력·외부 대역을 재사용해 포인터 충돌/동일 ID/이력/롤백 검증. 실제 Chroma 성공으로 보고하지 않음 |

테스트 전용 빈 MySQL만 사용한다. 기존 자료가 있는 DB는 테스트 준비를 중단한다.

| 환경변수 | 허용 호스트 | DB 이름 |
|---|---|---|
| DB_MIGRATION_TEST_URL | 127.0.0.1 | hyundap_schema_test |
| DB_RUNTIME_TEST_URL | 127.0.0.1 | hyundap_runtime_test |
| DB_WORKFLOW_TEST_URL | 127.0.0.1 | hyundap_workflow_test |

현재 루트 .env 및 DATABASE_URL·위 테스트 환경변수는 없다. 실제 스키마와 MySQL
35개 테스트는 미검증이다. SQLAlchemy 설치 2.1.4는 requirements.txt의 2.0.55와
다르므로 고정 버전 환경 검증이 남아 있다. 기존 전역 패키지는 변경하지 않았다.
DOCX/PPTX 파서 설치와 Chroma 전체 통합은 DB 준비의 선행 조건으로 두지 않는다.

## 5. 착수 순서와 보존 상태

1. documents_repo의 제외 조회 입력·오류·트랜잭션 계약을 먼저 확정한다.
2. 저장소 수정과 해당 DB 테스트를 함께 추가하고 DB_contract.md에 반영한다.
3. 보고서 생산부와 분리하여 저장 허용 필드 계약·DB 검사를 구현한다.
4. 격리 MySQL 준비 후 권한 조회·SQL 전이·동시성 회귀를 검증한다.
5. DB 구현 완료, 실제 구조 점검 대기, 외부 연계 대기를 각각 기록한다.

기존 feature/db 수정은 `feature/db local change before db01 preparation` stash에
보존돼 있다. 개인 참고 파일은 수정하지 않았다. 이번 준비에서는 커밋·push를 하지 않았다.
첫 기능 묶음(문서 메타데이터·중복·후보 생성)은 로컬 코드·계약·테스트에 반영했다.
현재 검증 결과는 test_document_storage 포함 51개 중 15개 통과·36개 MySQL skip이다.
MySQL 잠금·동시성·원자성은 미검증이며 관련 이슈는 완료 처리하지 않는다.
기능별 묶음과 증거는 P1_IMPLEMENTATION_STEPS.md 첫 표에 기록했다.
다음 구현 대상은 청크·보고서 저장 정합성(#157~#159)이다.

문서 저장 묶음은 `e7ec32b`로 커밋하고 origin/db01에 push했다.
같은 브랜치에서 다음 묶음의 기획을
[P1_CHUNK_REPORT_PLAN.md](P1_CHUNK_REPORT_PLAN.md)에 작성했다.
새 보고서 필드·청크 수 비교·잠금 순서·개별 저장소 TX의 완료 조건을 구분한다.
