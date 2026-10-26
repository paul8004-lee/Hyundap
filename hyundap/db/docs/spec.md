# DB 명세

책임: 전재경. 기준 계약은 [DB_contract.md](../../../docs/db-design/DB_contract.md), 물리 설계는 [DB_schema.md](../../../docs/db-design/DB_schema.md), 적용 절차는 [MIGRATION_v4.md](../../../docs/db-design/MIGRATION_v4.md).

| 명세 기능 | DB 제공 책임 | 연계 담당 |
|---|---|---|
| F39 | 원본·문서·버전 정보 저장/조회 | 김혜리·박경환 |
| F63 / FUN-206 | 회사별 해시 중복 조회·최종 중복 보호 | 김혜리 |
| FUN-247 | 활성 버전·회사 11필드 일괄 행 조회 | 이준영; DTO 변환·공개 판정은 search |
| F41 / F107 | 처리 보고서·실패 사유 영속화 | 김혜리·박영준; 관리 UI는 박경환 |
| F110~112 | 완전성 판정·DB 포인터·embedding 호출 | 박영준; Chroma 내부는 embedding |

#118은 계약 작성, #119는 구조·마이그레이션 준비다. #121 저장 함수와 #122 DB 업로드·게시·복구 서비스를 db02 작업 트리에서 구현했다. 실제 수집/임베딩 모듈은 아직 뼈대여서 실연동 완료와는 구분한다.

저장소 맨 위에서 `python -m hyundap.db.schema --inspect/--check`로 구조를 확인한다. SQL 생성은 `python -m hyundap.db.schema --prepare docs\db-design\migrations\004_local.sql`이며 DB 쓰기를 실행하지 않는다. 실제 공유 DB는 아직 미점검이다. 일반 함수·dict/list와 기존 SQLAlchemy text 연결을 재사용한다.

## 구현 파일과 트랜잭션

| 파일 | 제공 기능 |
|---|---|
| sessions_repo.py | 외부 인증 담당이 관리한 세션의 소유 증명·현재 권한 읽기 검증. 발급·폐기 제외 |
| documents_repo.py | 중복 조회·11필드 일괄 조회·문서/후보/작업 원자적 생성 |
| chunks_repo.py | 청크 기준 목록 저장·동일 재시도·변경 충돌·순번 조회 |
| versions_repo.py | 관리자 버전 이력·비활성 처리 후보 실패 표시 |
| jobs_repo.py / runtime.py / errors.py | 단계별 안전한 보고서·트랜잭션·입력 검증·공통 오류 |
| upload_service.py / version_service.py | 원본 저장·수집/적재 호출·동일 후보 재시도·실제 청크 검증·포인터 CAS·게시 실패 복구 |

생략 가능한 `connection=`을 제공하면 호출자가 시작한 TX에 참여하고 스스로 커밋하지 않는다. 생략 시 공유 Engine의 짧은 TX를 사용한다. 앱 종료 시 `db.runtime.get_engine().dispose()`로 풀을 닫고, 설정 변경 시 dispose 후 get_engine.cache_clear()를 호출한다.

새 후보 생성은 현재 세션·관리자 역할·회사 활성·레거시 이행 여부를 검사한다. 회사 행과 기존 문서 행 잠금으로 동시 중복·버전 번호 경쟁을 보호한다. 후보나 작업 INSERT가 실패하면 새 문서도 함께 롤백한다. 임베딩·파일 저장을 TX 안에서 실행하지 않는다.

manifest는 처리 중인 버전에만 저장한다. 같은 전체 목록의 재시도는 허용하고 해시 변경·추가·누락은 CONFLICT다. ready 버전과 정상 활성 버전을 mark_failed로 변경하지 않는다. 보고서는 단계별로 보존하며 이미 성공한 단계의 변경과 앞 단계의 지연 결과를 거부한다. 원시 오류 메시지 대신 안전한 코드만 저장한다.

`find_duplicate_document`, `get_documents_with_active_version`, manifest·작업·실패 저장은 인증된 서버 처리 흐름의 내부 함수다. 브라우저에 직접 공개하지 않는다. 회사·버전 접근 판단은 auth를 거쳐야 한다. 검색 DTO·is_visible·UI·Chroma 내부는 수정하지 않았다.

## 실행과 검증

`python -m pip install -r requirements-db.txt` 후 저장소 맨 위에서 실행한다. 오프라인 테스트는 `python -m unittest hyundap.tests.test_db_runtime.OfflineRuntimeTests -v`다. 실제 저장 테스트는 빈 로컬 MySQL의 DB_RUNTIME_TEST_URL(host=127.0.0.1, database=hyundap_runtime_test)을 설정하고 `python -m unittest hyundap.tests.test_db_runtime -v`로 실행한다. 기존 테이블이 있으면 중단한다.

테스트는 대표 v3를 v4로 이행한 합성 fixture다. 실제 공유 DB의 추가 필수 컬럼·enum·기존 자료와의 호환성은 #119 실측 뒤 확인해야 한다. 실제 DB의 DDL/DML, 커밋·푸시·PR 작성은 이번 작업에서 하지 않는다.

#122의 입력·반환·기본 어댑터·실패 원장·운영 복구와 선행 조건은 [WORKFLOW_122.md](../../../docs/db-design/WORKFLOW_122.md)를 따른다. 원본 보관 경로는 앱 서버가 관리하며 외부 벡터 호출은 SQL TX 밖에서 수행한다. 순수 ID 개수 검사로 완전성을 확정하지 않는다. 단일 앱 프로세스 운영이 전제다.

로그인 범위 제거 전 검증 기록: #127/#128 B안 적용 후 루트 실행 명령은 `python -m unittest discover -s hyundap/tests -t . -v`다. #122 워크플로 18개를 포함한 전체 53개 테스트가 통과했고, 마지막 원본 저장·재시도 경계 보완 후 워크플로 18개를 새 DB에서 다시 검증했다. 수집·벡터 대역을 사용한 DB 연계 검증이며 실제 팀 API 실연동 완료로 표시하지 않는다.

로그인 범위 제거 전 2026-10-26 로컬 검증: Python 3.11 / SQLAlchemy 2.0.55 / PyMySQL 1.1.2 / python-dotenv 1.2.4 / MySQL 8.4.13에서 `python -m unittest discover -s hyundap/tests -t . -v`의 35개 테스트가 모두 통과했다. DB_RUNTIME_TEST_URL과 DB_MIGRATION_TEST_URL은 각각 빈 로컬 전용 DB를 가리켰다. 인증·회사 격리·상태 변경·청크 목록·실패 롤백·동시 업로드와 기존 마이그레이션을 포함하며 건너뛴 테스트는 없다. 컴파일·공백 검사와 한글 독스트링 점검도 통과했다.


로그인·비밀번호 해싱/검증·세션 발급/폐기는 DB 담당 범위에서 제외했다. 테스트는 합성 사용자·세션 행을 준비하여 DB 접근 검증만 확인한다. 실제 인증 담당과의 연결은 미완료다.

## 메인 함수와 보조 함수 읽는 순서

메인 함수는 처리 순서를 보여 주고, 보조 함수는 한 단계의 검사나 저장을 담당한다. 일반 함수·dict·명시적인 if/for를 사용하며 새 클래스나 프레임워크를 추가하지 않는다. 보조 함수의 입력·반환값은 각 한글 독스트링에서 확인한다. 아래 보조 함수는 서버 내부 단계이며 별도 사용자 API가 아니다.

| 메인 함수 | 처리 순서와 주요 보조 함수 |
|---|---|
| `normalize_report` | `_report_dict` 입력 변환 → `_report_identity` 기본 정보 → `_report_metrics` 수치 → `_report_failures` 실패 상세 → 성공/실패 모순 검사 |
| `current_actor` | `_session_credentials` 입력 확인 → `_read_valid_session` 세션 조회 → `_read_active_user` 사용자 조회 → `_check_company_role` 회사·역할 검사 |
| `run_attempt` | `read_attempt_source` 원본 확인 → `ingest_and_save_chunks` 수집 단계 → `embed_and_record_result` 임베딩 단계 → `verify_uploaded_chunks` → 게시 |
| `upload_document` | 권한 확인 → `prepare_upload_source` 원본 정보 준비 → `create_upload_candidate` 후보 저장 → 원본 복사 → `run_attempt` |
| `retry_upload` | `prepare_retry` 기존 후보 준비 → 경로 확인 → 기존 벡터 정리 → 필요 시 원본 보충 → `run_attempt` |
| `validate_completeness` | DB 기준 목록 조회 → 실제 벡터 조회 → `compare_stored_chunks` 목록 대조·`chunk_matches_manifest` 개별 내용 검사 |
| `publish_version` | `check_publication_access` 권한 → 완전성 확인 → `prepare_publication` DB 전환 → `activate_candidate` 벡터 활성화 → `finish_publication` 완료 기록 |
| `recover_publication` | `prepare_recovery` DB 복구 준비 → `restore_vectors` 벡터 복구 → `finish_recovery` 완료 기록 |

`with transaction()`은 안쪽 DB 변경이 모두 성공해야 함께 확정되는 범위다. 파일 읽기·복사와 외부 벡터 호출은 이 범위가 끝난 뒤 실행한다. `WORKFLOW_LOCK`과 `PUBLICATION_LOCK`은 한 앱 프로세스에서 작업이 겹치지 않게 하는 기존 잠금이다. 보조 함수만 따로 호출해 메인 함수의 잠금·권한 확인을 건너뛰지 않는다.

게시 시 `expected_active_id`는 처음 확인한 활성 버전이다. DB 값이 달라졌으면 다른 작업이 먼저 게시한 것이므로 중단한다. `_seal`은 완전성 검사 전후의 DB 기준 목록이 같은지 비교하는 값이다. 이 검사와 실패 복구 순서는 함수 분리 후에도 유지한다.

### 보조 함수 안에서도 책임을 나누는 기준

조회 함수는 필요한 행과 잠금을 가져오고, 검사 함수는 통과 여부를 판단하며, 조립 함수는 메모리의 dict를 만들고, 저장 함수는 SQL 변경을 수행한다. 조회에 꼭 필요한 권한 검사와 대상 존재 확인은 조회 단계에 함께 둔다. 단순한 한 줄마다 함수를 만들지는 않는다.

| 흐름 담당 함수 | 책임별 보조 함수 |
|---|---|
| `record_stage_result` | `_read_stage_target` 조회 → `_check_stage_result` 입력·단계 검사 → `_merge_stage_reports` 보고서 조립 → `_store_stage_result` 저장 |
| `run_attempt` | 수집·임베딩 단계 실행은 각각 `ingest_and_save_chunks`·`embed_and_record_result`가 담당하고, 실패 단계 선택·후보 정리는 상위 함수가 담당 |
| `prepare_retry` | `read_retry_records` 권한·잠금 조회 → `check_retry_allowed` 상태 검사 → `build_retry_report` 이력 조립 → `reset_retry_records` 저장 |
| `compare_stored_chunks` | `collect_chunk_comparison` 비교 자료 수집 → `inspect_chunk_contents`로 청크별 내용 검사 → 상위 함수에서 전체 완전성 판정 |
| `prepare_publication` | `read_publishable_candidate` 권한·원장 확인 → 완료된 재호출 확인 → `check_publication_conflict` 경쟁 검사 → `store_pending_publication` 전환·복구 기록 저장 |
| `prepare_recovery` | 현재 복구 단계 조회 → `check_previous_version` 이전 버전 적격성 확인 → 최신 포인터 보호 → `restore_database_pointer` 조건부 포인터 복구 → 복구 대기 기록 |

예를 들어 `record_stage_result`의 네 단계는 같은 DB 연결을 사용한다. 마지막 저장이 실패하면 그 트랜잭션이 취소된다. 보조 함수가 직접 새 트랜잭션을 열거나 커밋하지 않으므로 책임 분리가 저장의 원자성을 바꾸지 않는다. 외부에서 연결을 전달했다면 최종 커밋·롤백은 그 연결을 연 호출자가 담당한다.
