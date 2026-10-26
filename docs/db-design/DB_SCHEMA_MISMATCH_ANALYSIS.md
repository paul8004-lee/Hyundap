# 실제 DB v3와 P1 코드 구조 불일치 분석

검증일: 2026-10-26. 대상: `.env`에 설정된 MySQL 8.4.11.
실제 DB에는 읽기 전용 조회만 수행했다. 접속 정보·비밀번호·업무 데이터는 기록하지 않는다.

## 결론

연결 장애나 데이터 35행의 손상이 아니다. 실제 DB의 기존 v3 물리 구조와 현재 P1 코드가 요구하는 v4 구조 및 기존 컬럼에 대한 가정이 다르다.
스키마 버전 숫자가 3이라는 사실만으로 테스트의 v3 fixture와 동일한 구조라고 판단할 수 없다.
실제 DB에 어떤 과거 변경이 적용됐는지는 이번 구조 조회만으로 단정하지 않는다.

## 35개 진단의 구성

| 분류 | 진단 수 | 원인 |
|---|---:|---|
| 신규 테이블 부재에서 파생 | 20 | document_versions·document_chunks 부재로 컬럼 그룹, PK, UNIQUE, FK, CHECK가 각각 실패 |
| 기존 테이블의 v4 확장 부재 | 10 | documents·users·sessions·index_jobs의 컬럼 그룹 4개, 로그인 UNIQUE 1개, FK 2개, CHECK 3개 |
| 기존 컬럼 타입 차이 | 5 | 아래 타입 비교 참조 |
| 합계 | 35 | 검사 메시지 수이며 누락 컬럼 수나 손상 행 수가 아님 |

누락 컬럼 그룹은 documents(active_version_id, doc_type, publication_status),
users(auth_version, login_identifier, password_hash), sessions(auth_version, expires_at),
index_jobs(attempts, report, stage, version_id)이다.

| 컬럼 | 실제 타입 | P1 요구 타입 |
|---|---|---|
| documents.deleted_at | BIGINT | DATETIME(6) |
| sessions.revoked_at | BIGINT | DATETIME(6) |
| sessions.token_hash | VARCHAR(64), utf8mb4_0900_bin | CHAR(64), ascii_bin |
| index_jobs.error_code | LONGTEXT | VARCHAR(64) |
| index_jobs.error_message | LONGTEXT | VARCHAR(1000) |

조회 당시 documents·sessions·index_jobs는 각각 0행이었다. 따라서 기존 시간값의 초/밀리초 단위나 오류 문자열 축소 시 손실 여부를 실제 값으로 검증한 것은 아니다.

## 마이그레이션 생성기의 중단 이유

`hyundap/db/schema.py:155`의 prepare_sql은 기존 타입이 예상과 다르면 중단한다.
실제 메타데이터에서 첫 오류는 `Incompatible existing type: documents.deleted_at`이다.

실제 DB를 변경하지 않고 메타데이터 사본만 바꿔 원인을 분리했다.

1. 타입 차이 5개만 사본에서 수정하면 `Session token hash requires ascii_bin collation`으로 중단한다.
2. 사본의 token_hash 문자셋·콜레이션까지 ascii/ascii_bin으로 맞추면 SQL 34개를 생성한다.

이는 생성기의 보호 조건을 확인한 결과다. SQL을 실행하거나 마이그레이션 안전성을 입증한 결과가 아니다.
validate_v4의 35개 진단에는 token_hash 콜레이션 검사가 별도 항목으로 포함되지 않아, 35개만으로 전체 호환성을 판단할 수 없다.

## 추가 발견: v4 추가 이후에도 남는 저장 계약 불일치

현재 INSERT와 실제 DB의 NOT NULL·기본값 없는 컬럼을 대조했다.
생성 컬럼 및 자동 증가 컬럼은 제외했다. 실제 DB에 INSERT를 실행하지 않았다.

| 대상 | 현재 INSERT가 제공하는 컬럼 | 별도로 제공해야 하는 기존 필수 컬럼 |
|---|---|---|
| documents | id, company_id, doc_type | filename, storage_key, media_type, size_bytes, sha256, uploaded_by, created_at, updated_at (8개) |
| index_jobs | id, version_id | document_id, company_id, generation, content_revision, attempt_no, created_at, updated_at (7개) |

근거는 `hyundap/db/documents_repo.py`의 _prepare_document 및 _insert_version_and_job이다.
현 상태에서는 이 필수값을 공급하지 않으므로, 컬럼 추가만으로 실제 저장 경로가 호환되지 않는다.

세션 역시 의미 매핑이 필요하다. 실제 DB는 idle_expires_at·absolute_expires_at을 사용하지만,
`hyundap/db/sessions_repo.py:69`는 expires_at과 UTC_TIMESTAMP(6)을 비교한다.
두 만료 정책을 어떻게 보존할지 정하지 않고 새 expires_at만 추가하는 것으로는 충분하지 않다.

## 격리 테스트 75개가 통과한 이유

`hyundap/tests/test_schema_migration.py:32`의 v3 fixture는 최소 구조다.
documents에는 실제 DB의 필수 메타데이터 컬럼들이 없고 deleted_at은 이미 DATETIME(6)이다.
sessions는 id·user_id, index_jobs는 id만 가진다.
따라서 75개 통과는 해당 fixture의 v4 이행 및 기능을 검증하지만, 실제 v3 구조와의 호환성을 보장하지 않는다.

## 후속 수정 순서

1. 실제 DDL을 격리 DB에 재현하고 현재 마이그레이션·문서 저장·세션 조회 실패를 회귀 테스트로 고정한다.
2. 날짜 단위, 세션 만료, 기존 필수값 공급, 인증 필드의 보존 정책을 정해 코드와 마이그레이션을 맞춘다.
3. 격리 DB에서 v4 전환 후 저장·조회·권한·롤백을 통합 검증하고 실제 DB 적용 SQL을 검토한다.

이번 검증에서는 실제 DB의 DDL/DML 및 schema_migrations 버전을 변경하지 않았다.
