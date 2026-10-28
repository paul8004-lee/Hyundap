# DB 담당 P1 현행 검증 기록

기준: `feature/db`의 `2d2be91`(PR #186 병합), 2026-10-27. 이 문서는 사용자가 제공한 **DB 담당 범위 한정 P1 계획**의 작업 1~5를 기준으로 한다. 이 기록 이후의 코드 변경까지 검증한 것으로 해석하지 않는다.

## 범위와 판정

| 작업 | DB 담당 범위의 현재 근거 | 판정 |
|---|---|---|
| 1. 실제 스키마 | Railway 읽기 전용 `snapshot`·`validate_v4`·`audit_counts` | 확인 완료. 데이터가 없는 문서 경로는 아래 한계를 따른다. |
| 2. 메타데이터·중복 | `documents_repo.validate_metadata`, `find_duplicate_document`, `create_document_version`; 입력·중복·동시 생성 테스트 | 구현·격리 DB 회귀 확인 |
| 3. 청크·보고서 | `chunks_repo.save_manifest`, `jobs_repo.normalize_report`·`record_stage_result`; manifest·보고서 검증 | 구현·격리 DB 회귀 확인 |
| 4. 세션·회사·문서 조회 | `sessions_repo.current_actor`, `auth.permissions.get_allowed_company_ids`·`verify_documents_access`, `documents_repo.get_documents_with_active_version` | 현재 상태 재검증과 회사 격리 회귀 확인 |
| 5. SQL 상태 전이 | `version_service.prepare_publication`·`prepare_recovery`·`finish_recovery`, `upload_service.reset_retry_records`·`jobs_repo.reset_job_for_retry` | 게시 포인터 보호·실패·재시도·복구 회귀 확인 |

DB 담당 범위에서 P1 구현·회귀는 완료로 판단한다. 수집·임베딩·검색·인증 발급·UI 및 여러 저장소를 포함한 서비스 전체 완료 판정은 별개다. DB 함수와 외부 호출이 함께 있는 기존 흐름에서 이 표의 판정은 DB 저장·조회·제약·트랜잭션 계약에 한정한다.

## 격리 MySQL 전체 회귀

- 실행 기준: `2d2be91`, Python 3.11.9, 격리 MySQL 8.4.13.
- 결과: **369개 실행, 실패 0, 오류 0, 건너뜀 0**. 수집·벡터·외부 서비스는 테스트에 따라 대역을 사용했다. 실제 OpenAI API와 Railway 쓰기는 수행하지 않았다.
- 실행: 격리 MySQL 인스턴스의 `127.0.0.1`에 테스트 전용 빈 DB를 준비하고 아래 환경변수에 각각 해당 DB URL을 설정한 뒤 실행했다. URL의 계정·비밀번호는 기록하지 않는다.

| 환경변수 | 전용 빈 데이터베이스 |
|---|---|
| `DB_SCHEMA_TEST_URL` | `hyundap_schema_test` |
| `DB_RUNTIME_TEST_URL` | `hyundap_runtime_test` |
| `DB_WORKFLOW_TEST_URL` | `hyundap_workflow_test` |
| `DB_CONTRACT_TEST_URL` | `hyundap_contract_test` |
| `DB_AUTH_TEST_URL` | `hyundap_auth_test` |

```powershell
python -m unittest discover -s hyundap/tests -t .
```

위 명령은 테스트를 실행한다. DB 준비는 별도이며 반드시 전용 격리 인스턴스와 빈 DB에서 수행한다. 공유 DB를 테스트 대상으로 사용하거나 기존 데이터를 삭제해 준비하지 않는다. 테스트의 호스트·DB 이름 제한과 빈 DB 조건을 유지한다. 환경변수가 없으면 MySQL 테스트는 건너뛰므로 위 결과와 같은 검증이 아니다.

## Railway 읽기 전용 확인

같은 기준 코드의 `snapshot(connection)`, `validate_v4(layout)`, `audit_counts(connection)`로 접속해 다음 결과를 확인했다.

- 스키마 버전 4, 11개 테이블, `validate_v4` 불일치 0건.
- `published_version`, `ready_manifest`, `document_company`, `version_document_company`, `active_identity` 정합성 검사 각각 위반 0건.
- 검사 시 문서 업무 데이터가 비어 있었으므로 실제 문서가 존재할 때의 게시·검색·벡터 정합성을 이 결과만으로 입증할 수 없다. 읽기 전용 검사이며 DDL·DML은 수행하지 않았다.

## 연계와 이력

인증 모듈의 로그인·세션 구현은 존재하며 실제 인증을 명시적으로 주입한 시험 이력이 있다. 다만 `ui.backend_bindings.create_bindings(authentication=None)`의 기본값은 `MockServices`이므로 기본 UI 실행이 실제 인증을 사용한다고 단정하지 않는다. 실제 수집·임베딩·벡터·UI의 전체 경로와 오류 정책은 담당 모듈과 별도 검증한다.

이전 [인증 정합화 검증](V4_AUTH_ALIGNMENT_2026-10-27.md)의 332개 통과와 과거 P1 점검 결과는 각 문서에 기록된 당시 코드와 환경에 대한 이력이다. 현재 최종 스키마는 v4 직접 생성만 지원한다. 폐기된 이전 스키마 이행 기능을 복원하거나 운영 전환 경로를 제공하지 않는다.
