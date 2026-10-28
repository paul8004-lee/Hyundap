# DB P1 세부 구현 단계와 GitHub 이슈

## 기능별 구현 묶음과 현재 진행

| 묶음 | 이슈 | 진행과 완료 판단 |
|---|---|---|
| A. 문서 메타데이터·중복·후보 생성 | #154, #155, #156 | 로컬 코드·계약·테스트 반영. 공통 유형과 누락 기본값, 검증된 현재 후보 제외, 최종 중복 검사 유지. MySQL 원자성·동시성은 대기 |
| B. 청크·보고서 저장 정합성 | #157, #158, #159 | 로컬 구현 반영. 청크 경계 테스트, 수집 보고서 허용 필드, 성공 보고서의 예상·실제 청크 수 비교와 버전→작업 잠금. 실제 MySQL 검증 대기 |
| C. 현재 세션·회사·문서 상태 조회 | #160 | 기존 구현 재사용. 합성 SQLite에서 실제 조회 SQL을 실행하는 회귀 8개 통과. 실제 MySQL 검증 대기. 인증 생성·검색·챗봇 연결 변경 없음 |
| D. 게시·실패·재시도·복구 SQL | #161, #162 | 게시 기록 실패 롤백·오래된 복구의 최신 포인터 보호·첫 게시 실패 복구 테스트 3개 추가. MySQL 8.0.17의 기존 마이그레이션 collation 오류로 실제 실행 대기 |
| E. 환경·스키마·최종 증거 | #152, #153, #163 | 격리 환경은 각 묶음의 MySQL 검증 선행. 실제 DB 실측은 독립 대기 가능. 마지막에 고정 버전 회귀와 문서 대조 |

2026-10-26 묶음 A 결과: 51개 중 15개 통과·36개 MySQL 설정 부재로 skip.
`test_document_storage.py`의 신규 5개는 입력·실제 SQLite 조회 SQL·최종 생성 제외 미사용을 검증한다.
`test_db_runtime.py`에는 실제 MySQL용 제외 문맥 검증 1개를 추가했으며 현재 skip이다.
SQLite 성공은 MySQL 잠금/제약/동시성 검증을 대체하지 않는다. 기존 전역 DB 의존성 버전 차이도 남아 있다.

```powershell
python -m unittest hyundap.tests.test_document_storage hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

다음 구현은 묶음 B다. 새 기능의 MySQL 증거가 확보되기 전 관련 이슈를 닫지 않는다.

후속 진행: 묶음 B는 `43dace6`로 push했고, 묶음 C는 조회 계약 테스트 8개를 추가했다.
최신 검증은 아래 명령으로 68개 중 30개 통과·38개 MySQL skip·실패 0이다.
세션 시간 함수는 SQLite fixture의 고정 시각 대역이며 실제 MySQL 만료 처리·잠금 검증과 구분한다.

```powershell
python -m unittest hyundap.tests.test_access_queries hyundap.tests.test_ingest_storage hyundap.tests.test_document_storage hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

다음 구현 묶음은 D(#161·#162)의 게시·복구 SQL 검증이다.

묶음 D 진행: test_upload_workflow.py에 SQL 전이 회귀 3개를 추가했다.
기존 MySQL 서비스와 분리된 127.0.0.1:33179 임시 MySQL 8.0.17에서 전체 DB 검증을
시도했으나 v4 마이그레이션의 ck_v4_content_hash 추가 중 오류 1267이 발생했다.
오류에는 utf8mb4_0900_ai_ci와 utf8mb4_unicode_ci가 충돌하며 document_chunks의
기존 ck_v4_chunk_id와 혼합 정렬 규칙을 사용하는 구조가 포함돼 있었다.
정확한 서버 버전별 원인은 미확정이다. 운영 구조/생성 SQL을 임의 변경하지 않았다.
마이그레이션 테스트 1개와 runtime/workflow fixture 준비 2개가 오류로 중단되어
새 게시/복구 테스트의 실제 MySQL 성공 증거는 없다. 임시 서버는 종료했다.

설정 없는 로컬 기준선은 71개 중 30개 통과·41개 skip·실패 0이다.
다음 작업은 기존 검증 기록과 같은 격리 MySQL 8.4 및 팀 고정 DB 패키지 환경에서
마이그레이션과 #161·#162를 재실행하는 것이다. 실제 공유 DB 실측(#153)은 별도 대기다.

기준: db01 / develop `98d428e`, 2026-10-26. DB 담당 범위만 포함한다. 각 단계는 기존 코드 재사용을 우선하며 완료 체크는 실제 검증 후 수행한다.

| 단계 | 이슈 | 예상 | 선행 |
|---|---|---|---|
| 01 | [#152 격리 MySQL 테스트 실행 조건과 기준선 확정](https://github.com/paul8004-lee/Hyundap/issues/152) | 30~60분 | 없음 |
| 02 | [#153 실제 스키마 읽기 전용 비교와 이행 필요사항 기록](https://github.com/paul8004-lee/Hyundap/issues/153) | 2~4시간 | 없음 |
| 03 | [#154 문서 메타데이터 저장 규격과 공통 문서 유형 정합성 보완](https://github.com/paul8004-lee/Hyundap/issues/154) | 30~60분 | 없음 |
| 04 | [#155 회사별 중복 조회의 현재 후보 제외 옵션 구현](https://github.com/paul8004-lee/Hyundap/issues/155) | 45~75분 | #154 |
| 05 | [#156 후보 생성의 중복 방지·원자성·트랜잭션 회귀 검증](https://github.com/paul8004-lee/Hyundap/issues/156) | 30~60분 | #152, #154, #155 |
| 06 | [#157 청크 manifest 저장 계약과 멱등성 검증](https://github.com/paul8004-lee/Hyundap/issues/157) | 30~45분 | #152 |
| 07 | [#158 수집 보고서 허용 필드와 타입·범위 검사 구현](https://github.com/paul8004-lee/Hyundap/issues/158) | 45~75분 | 없음 |
| 08 | [#159 단계 보고서 영속화·청크 수 정합성·시도 이력 검증](https://github.com/paul8004-lee/Hyundap/issues/159) | 45~75분 | #152, #157, #158 |
| 09 | [#160 현재 세션·회사·활성 문서 조회 계약 회귀 검증](https://github.com/paul8004-lee/Hyundap/issues/160) | 1~2시간 | #152 |
| 10 | [#161 게시 SQL의 포인터 보호·조건부 갱신·원자성 검증](https://github.com/paul8004-lee/Hyundap/issues/161) | 45~75분 | #152, #156, #157 |
| 11 | [#162 실패·재시도·복구 SQL 기록과 최신 포인터 보호 검증](https://github.com/paul8004-lee/Hyundap/issues/162) | 45~75분 | #161, #159 |
| 12 | [#163 DB P1 최종 회귀·계약 문서·완료 상태 정리](https://github.com/paul8004-lee/Hyundap/issues/163) | 30~60분 | #153, #154, #155, #156, #157, #158, #159, #160, #161, #162 |

실제 스키마 점검(02)은 접속 대기 시 독립적으로 보류한다. 다른 단계는 격리 DB와 계약 준비 상태에 따라 진행할 수 있다. 단계별 시간은 세분화 후 합계 약 11~19시간이며 신규 환경 구축·외부 응답·운영 적용 대기는 별도다.

## 단계 01 · [#152](https://github.com/paul8004-lee/Hyundap/issues/152)

## 목적과 기준

격리 MySQL 테스트 실행 조건과 기준선 확정. db01 / develop 98d428e 기준 DB P1 잔여 보완·검증 단계다. 기존 전체 기능의 재구현이 아니다.

| 항목 | 내용 |
|---|---|
| 단계 | DB-P1-01 (수동 관리 번호, 공식 WBS 번호 아님) |
| 예상 순수 작업량 | 30~60분 |
| 선행 | 없음 |
| 관련 기존 이슈 | #119 #121 |
| 대상 파일·함수 | requirements.txt, test_db_connection.py, test_schema_migration.py, test_db_runtime.py, test_upload_workflow.py |

## 세부 실행 순서

- [ ] 1. Python 3.11.9와 팀 고정 DB 패키지 버전을 별도 환경에서 확인하고 전역 설치는 유지한다.
- [ ] 2. 127.0.0.1의 hyundap_schema_test/runtime_test/workflow_test 전용 빈 DB와 URL 검사를 확인한다. 자격 증명은 Git에 저장하지 않는다.
- [ ] 3. 기존 자료가 있는 DB에서는 fixture 준비가 중단되는지 확인한다.
- [ ] 4. 기존 45개 테스트의 실행·통과·skip·실패 수와 DB 종류/버전을 기록한다.
- [ ] 5. 고정 버전 환경에서 미검증인 항목을 표시하고 후속 단계의 재현 명령을 남긴다.

## 완료 기준과 검증

전용 DB/호스트 제한, 기존 자료 보호, 재현 명령 및 실제 실행 결과가 기록된다. MySQL 미준비 시 통합 검증을 완료 처리하지 않는다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

현재 기준선은 45개 중 10개 통과·35개 격리 MySQL 설정 부재로 skip이다. 이전 결과는 이 이슈의 신규 구현 검증으로 간주하지 않는다. 변경에 맞는 테스트를 실행하고 명령·버전·통과/skip/실패·대역 범위를 기록한다.

## 책임과 대기 처리

관계형 DB 저장·조회·제약·트랜잭션·정합성만 다룬다. 파일 처리·ingest 콜백·외부 실행 흐름·Chroma 내부·인증 발급·검색/챗봇/UI 연결은 제외한다. 기존 혼합 서비스는 SQL 경계만 재사용하며 이동/재작성하지 않는다.

테스트 DB는 127.0.0.1의 빈 hyundap_schema_test / hyundap_runtime_test / hyundap_workflow_test만 사용한다. 기존 자료가 있는 대상은 fixture 준비를 중단한다. 공유 DB DDL/DML·실문서·실제 모델 API 테스트는 수행하지 않는다.

실제 DB 접속 또는 외부 계약이 없으면 해당 항목만 대기로 표시하고 DB 구현·실제 구조 점검·외부 연계 상태를 구분한다. 기존 이슈/WBS를 자동 종료·재배정하지 않는다.

근거: 사용자 제공 DB 담당 범위 한정 계획(2026-10-26), docs/db-design/P1_DEVELOPMENT_READY.md. 준비 문서는 현재 로컬 미커밋 상태다.


## 단계 02 · [#153](https://github.com/paul8004-lee/Hyundap/issues/153)

## 목적과 기준

실제 스키마 읽기 전용 비교와 이행 필요사항 기록. db01 / develop 98d428e 기준 DB P1 잔여 보완·검증 단계다. 기존 전체 기능의 재구현이 아니다.

| 항목 | 내용 |
|---|---|
| 단계 | DB-P1-02 (수동 관리 번호, 공식 WBS 번호 아님) |
| 예상 순수 작업량 | 2~4시간 |
| 선행 | 없음 |
| 관련 기존 이슈 | #119 |
| 대상 파일·함수 | connection.py, schema.py, DB_schema.md, MIGRATION_v4.md |

## 세부 실행 순서

- [ ] 1. 기존 snapshot/validate_v4/audit_counts로 컬럼·키·문자셋·상태·위반 건수를 읽기 전용 확인한다.
- [ ] 2. v3/v4 구조와 저장소가 요구하는 구조의 비교표를 작성한다.
- [ ] 3. 회사/문서/버전 FK, 회사+해시 UNIQUE, 청크 순번 제약과 실제 인덱스를 비교한다.
- [ ] 4. 필요할 때 prepare_sql로 검토용 SQL만 생성하고 비호환 구조는 중단 사유로 남긴다.
- [ ] 5. 실측 차이·코드 영향·운영 적용 대기를 분리해 기록한다. 실제 본문·자격 증명은 출력하지 않는다.

## 완료 기준과 검증

실측 비교표와 필요한 조치가 검토 가능하다. DATABASE_URL 미준비 시 이 단계만 대기하며 다른 저장소 작업을 막지 않는다. 공유 DB DDL/DML은 수행하지 않는다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

현재 기준선은 45개 중 10개 통과·35개 격리 MySQL 설정 부재로 skip이다. 이전 결과는 이 이슈의 신규 구현 검증으로 간주하지 않는다. 변경에 맞는 테스트를 실행하고 명령·버전·통과/skip/실패·대역 범위를 기록한다.

## 책임과 대기 처리

관계형 DB 저장·조회·제약·트랜잭션·정합성만 다룬다. 파일 처리·ingest 콜백·외부 실행 흐름·Chroma 내부·인증 발급·검색/챗봇/UI 연결은 제외한다. 기존 혼합 서비스는 SQL 경계만 재사용하며 이동/재작성하지 않는다.

테스트 DB는 127.0.0.1의 빈 hyundap_schema_test / hyundap_runtime_test / hyundap_workflow_test만 사용한다. 기존 자료가 있는 대상은 fixture 준비를 중단한다. 공유 DB DDL/DML·실문서·실제 모델 API 테스트는 수행하지 않는다.

실제 DB 접속 또는 외부 계약이 없으면 해당 항목만 대기로 표시하고 DB 구현·실제 구조 점검·외부 연계 상태를 구분한다. 기존 이슈/WBS를 자동 종료·재배정하지 않는다.

근거: 사용자 제공 DB 담당 범위 한정 계획(2026-10-26), docs/db-design/P1_DEVELOPMENT_READY.md. 준비 문서는 현재 로컬 미커밋 상태다.


## 단계 03 · [#154](https://github.com/paul8004-lee/Hyundap/issues/154)

## 목적과 기준

문서 메타데이터 저장 규격과 공통 문서 유형 정합성 보완. db01 / develop 98d428e 기준 DB P1 잔여 보완·검증 단계다. 기존 전체 기능의 재구현이 아니다.

| 항목 | 내용 |
|---|---|
| 단계 | DB-P1-03 (수동 관리 번호, 공식 WBS 번호 아님) |
| 예상 순수 작업량 | 30~60분 |
| 선행 | 없음 |
| 관련 기존 이슈 | #121 #118 #149 |
| 대상 파일·함수 | documents_repo.validate_metadata, common/schemas.py(참조), DB_contract.md |

## 세부 실행 순서

- [ ] 1. 필수 필드·문자열 길이·ID·SHA-256·파일 크기의 현재 저장 계약을 표로 고정한다.
- [ ] 2. 공통 DOC_TYPE_CODES를 참조하고 DEFAULT_DOC_TYPE 적용 지점을 확정한다. 누락 기본값과 명시적 잘못된 값 거부를 구분한다.
- [ ] 3. 입력 dict를 변경하지 않고 허용 저장 필드만 새 dict로 반환하는 동작을 유지한다.
- [ ] 4. 문서 유형 외 data_category·문자열 NUL·bool 크기 입력 등 경계값을 확인한다.
- [ ] 5. DB 함수 테스트와 계약 문서만 보완하고 ingest 오류 정책·파일 검사·공통 코드 체계는 변경하지 않는다.

## 완료 기준과 검증

허용 유형·누락/잘못된 유형·길이 경계·bool/음수/초과 크기·잘못된 해시를 검사하고 INVALID_INPUT과 반환 필드 계약이 일치한다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

현재 기준선은 45개 중 10개 통과·35개 격리 MySQL 설정 부재로 skip이다. 이전 결과는 이 이슈의 신규 구현 검증으로 간주하지 않는다. 변경에 맞는 테스트를 실행하고 명령·버전·통과/skip/실패·대역 범위를 기록한다.

## 책임과 대기 처리

관계형 DB 저장·조회·제약·트랜잭션·정합성만 다룬다. 파일 처리·ingest 콜백·외부 실행 흐름·Chroma 내부·인증 발급·검색/챗봇/UI 연결은 제외한다. 기존 혼합 서비스는 SQL 경계만 재사용하며 이동/재작성하지 않는다.

테스트 DB는 127.0.0.1의 빈 hyundap_schema_test / hyundap_runtime_test / hyundap_workflow_test만 사용한다. 기존 자료가 있는 대상은 fixture 준비를 중단한다. 공유 DB DDL/DML·실문서·실제 모델 API 테스트는 수행하지 않는다.

실제 DB 접속 또는 외부 계약이 없으면 해당 항목만 대기로 표시하고 DB 구현·실제 구조 점검·외부 연계 상태를 구분한다. 기존 이슈/WBS를 자동 종료·재배정하지 않는다.

근거: 사용자 제공 DB 담당 범위 한정 계획(2026-10-26), docs/db-design/P1_DEVELOPMENT_READY.md. 준비 문서는 현재 로컬 미커밋 상태다.


## 단계 04 · [#155](https://github.com/paul8004-lee/Hyundap/issues/155)

## 목적과 기준

회사별 중복 조회의 현재 후보 제외 옵션 구현. db01 / develop 98d428e 기준 DB P1 잔여 보완·검증 단계다. 기존 전체 기능의 재구현이 아니다.

| 항목 | 내용 |
|---|---|
| 단계 | DB-P1-04 (수동 관리 번호, 공식 WBS 번호 아님) |
| 예상 순수 작업량 | 45~75분 |
| 선행 | #154 |
| 관련 기존 이슈 | #121 #113 |
| 대상 파일·함수 | documents_repo.find_duplicate_document, DB_contract.md, test_db_runtime.py |

## 세부 실행 순서

- [ ] 1. 제안 서명 find_duplicate_document(company_id, sha256, *, exclude_version_id=None, connection=None)의 입력·반환·오류를 확정한다.
- [ ] 2. 제외 인자가 있으면 같은 연결에서 해당 버전의 존재·회사·해시 일치를 검증하며 잘못된 문맥은 INVALID_INPUT으로 거부한다.
- [ ] 3. 바인딩된 제외 조건을 추가하고 옵션 생략 시 삭제/실패 포함 기존 조회 정책을 유지한다.
- [ ] 4. dict 또는 None 반환과 호출자 연결 참여를 유지하고 콜백·업로드 호출부는 수정하지 않는다.
- [ ] 5. 자기 후보 제외와 다른 회사/해시/없는 ID를 테스트하고 최종 생성 경로는 제외 인자를 사용하지 않도록 확인한다.

## 완료 기준과 검증

기존 조회·자기 후보 제외·부적절한 제외 문맥·교차 회사가 검증된다. UNIQUE(company_id,sha256)와 모순되는 중복 fixture를 만들거나 제약을 약화하지 않는다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

현재 기준선은 45개 중 10개 통과·35개 격리 MySQL 설정 부재로 skip이다. 이전 결과는 이 이슈의 신규 구현 검증으로 간주하지 않는다. 변경에 맞는 테스트를 실행하고 명령·버전·통과/skip/실패·대역 범위를 기록한다.

## 책임과 대기 처리

관계형 DB 저장·조회·제약·트랜잭션·정합성만 다룬다. 파일 처리·ingest 콜백·외부 실행 흐름·Chroma 내부·인증 발급·검색/챗봇/UI 연결은 제외한다. 기존 혼합 서비스는 SQL 경계만 재사용하며 이동/재작성하지 않는다.

테스트 DB는 127.0.0.1의 빈 hyundap_schema_test / hyundap_runtime_test / hyundap_workflow_test만 사용한다. 기존 자료가 있는 대상은 fixture 준비를 중단한다. 공유 DB DDL/DML·실문서·실제 모델 API 테스트는 수행하지 않는다.

실제 DB 접속 또는 외부 계약이 없으면 해당 항목만 대기로 표시하고 DB 구현·실제 구조 점검·외부 연계 상태를 구분한다. 기존 이슈/WBS를 자동 종료·재배정하지 않는다.

근거: 사용자 제공 DB 담당 범위 한정 계획(2026-10-26), docs/db-design/P1_DEVELOPMENT_READY.md. 준비 문서는 현재 로컬 미커밋 상태다.


## 단계 05 · [#156](https://github.com/paul8004-lee/Hyundap/issues/156)

## 목적과 기준

후보 생성의 중복 방지·원자성·트랜잭션 회귀 검증. db01 / develop 98d428e 기준 DB P1 잔여 보완·검증 단계다. 기존 전체 기능의 재구현이 아니다.

| 항목 | 내용 |
|---|---|
| 단계 | DB-P1-05 (수동 관리 번호, 공식 WBS 번호 아님) |
| 예상 순수 작업량 | 30~60분 |
| 선행 | #152, #154, #155 |
| 관련 기존 이슈 | #121 |
| 대상 파일·함수 | documents_repo.create_document_version/_check_candidate_creation/_insert_version_and_job, runtime.transaction, test_db_runtime.py |

## 세부 실행 순서

- [ ] 1. 세션/사용자/회사 잠금 순서와 회사 잠금 뒤 최종 중복 검사를 확인한다.
- [ ] 2. 최종 생성에서 제외 옵션 미사용과 DUPLICATE_FILE/고유키 CONFLICT의 차이를 명시한다.
- [ ] 3. 문서·버전·작업 저장 도중 실패를 주입해 전체 롤백을 확인한다.
- [ ] 4. 같은 회사 동일 해시의 동시 생성 1건 성공과 동일 문서의 서로 다른 버전 번호를 검증한다.
- [ ] 5. connection 전달 시 임의 커밋/종료가 없고 생략 시 자체 TX가 소유되는지 검증한다.

## 완료 기준과 검증

실제 격리 MySQL에서 중복 경쟁·원자 롤백·버전 번호 경쟁이 통과한다. SQL TX에 파일/외부 호출을 추가하지 않는다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

현재 기준선은 45개 중 10개 통과·35개 격리 MySQL 설정 부재로 skip이다. 이전 결과는 이 이슈의 신규 구현 검증으로 간주하지 않는다. 변경에 맞는 테스트를 실행하고 명령·버전·통과/skip/실패·대역 범위를 기록한다.

## 책임과 대기 처리

관계형 DB 저장·조회·제약·트랜잭션·정합성만 다룬다. 파일 처리·ingest 콜백·외부 실행 흐름·Chroma 내부·인증 발급·검색/챗봇/UI 연결은 제외한다. 기존 혼합 서비스는 SQL 경계만 재사용하며 이동/재작성하지 않는다.

테스트 DB는 127.0.0.1의 빈 hyundap_schema_test / hyundap_runtime_test / hyundap_workflow_test만 사용한다. 기존 자료가 있는 대상은 fixture 준비를 중단한다. 공유 DB DDL/DML·실문서·실제 모델 API 테스트는 수행하지 않는다.

실제 DB 접속 또는 외부 계약이 없으면 해당 항목만 대기로 표시하고 DB 구현·실제 구조 점검·외부 연계 상태를 구분한다. 기존 이슈/WBS를 자동 종료·재배정하지 않는다.

근거: 사용자 제공 DB 담당 범위 한정 계획(2026-10-26), docs/db-design/P1_DEVELOPMENT_READY.md. 준비 문서는 현재 로컬 미커밋 상태다.


## 단계 06 · [#157](https://github.com/paul8004-lee/Hyundap/issues/157)

## 목적과 기준

청크 manifest 저장 계약과 멱등성 검증. db01 / develop 98d428e 기준 DB P1 잔여 보완·검증 단계다. 기존 전체 기능의 재구현이 아니다.

| 항목 | 내용 |
|---|---|
| 단계 | DB-P1-06 (수동 관리 번호, 공식 WBS 번호 아님) |
| 예상 순수 작업량 | 30~45분 |
| 선행 | #152 |
| 관련 기존 이슈 | #121 #122 |
| 대상 파일·함수 | chunks_repo.save_manifest/_normalize_manifest/get_expected_chunk_ids, test_db_runtime.py |

이력 주의(2026-10-28, #207): 위 표는 당시 계획이다. `get_expected_chunk_ids`는 업무 호출이
없어 폐기했다. 현재 완전성 검증은 version_service의 manifest_snapshot/compare_stored_chunks를
사용하며, 저장 회귀 테스트는 SQL로 직접 확인한다.

## 세부 실행 순서

- [ ] 1. 기존 id/chunk_index/content_hash 저장 규격과 본문 미저장 정책을 명시한다.
- [ ] 2. 0부터 연속 순번·version_id 기반 ID·소문자 SHA-256·최대 개수·bool 거부를 확인한다.
- [ ] 3. 처리 중 버전에만 저장하고 ready/활성 버전 보호가 유지되는지 확인한다.
- [ ] 4. 동일 목록 재저장은 허용하고 누락·추가·내용 변경은 CONFLICT로 거부하는 테스트를 보완한다.
- [ ] 5. manifest와 expected_chunk_count의 같은 TX 저장 및 실패 롤백을 검증한다.

## 완료 기준과 검증

잘못된 순번/ID/해시·멱등 재저장·변경 충돌·상태 보호·롤백이 검증된다. 파싱/청킹/dataclass 변환/Chroma 처리는 수정하지 않는다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

현재 기준선은 45개 중 10개 통과·35개 격리 MySQL 설정 부재로 skip이다. 이전 결과는 이 이슈의 신규 구현 검증으로 간주하지 않는다. 변경에 맞는 테스트를 실행하고 명령·버전·통과/skip/실패·대역 범위를 기록한다.

## 책임과 대기 처리

관계형 DB 저장·조회·제약·트랜잭션·정합성만 다룬다. 파일 처리·ingest 콜백·외부 실행 흐름·Chroma 내부·인증 발급·검색/챗봇/UI 연결은 제외한다. 기존 혼합 서비스는 SQL 경계만 재사용하며 이동/재작성하지 않는다.

테스트 DB는 127.0.0.1의 빈 hyundap_schema_test / hyundap_runtime_test / hyundap_workflow_test만 사용한다. 기존 자료가 있는 대상은 fixture 준비를 중단한다. 공유 DB DDL/DML·실문서·실제 모델 API 테스트는 수행하지 않는다.

실제 DB 접속 또는 외부 계약이 없으면 해당 항목만 대기로 표시하고 DB 구현·실제 구조 점검·외부 연계 상태를 구분한다. 기존 이슈/WBS를 자동 종료·재배정하지 않는다.

근거: 사용자 제공 DB 담당 범위 한정 계획(2026-10-26), docs/db-design/P1_DEVELOPMENT_READY.md. 준비 문서는 현재 로컬 미커밋 상태다.


## 단계 07 · [#158](https://github.com/paul8004-lee/Hyundap/issues/158)

## 목적과 기준

수집 보고서 허용 필드와 타입·범위 검사 구현. db01 / develop 98d428e 기준 DB P1 잔여 보완·검증 단계다. 기존 전체 기능의 재구현이 아니다.

| 항목 | 내용 |
|---|---|
| 단계 | DB-P1-07 (수동 관리 번호, 공식 WBS 번호 아님) |
| 예상 순수 작업량 | 45~75분 |
| 선행 | 없음 |
| 관련 기존 이슈 | #121 #118 |
| 대상 파일·함수 | jobs_repo.normalize_report/_report_metrics, DB_contract.md, test_db_runtime.py |

## 세부 실행 순서

- [ ] 1. parser/parser_version 허용값·길이와 필드별 선택/필수 여부를 저장 계약에 명시한다. 미합의 항목은 결정 필요로 표시한다.
- [ ] 2. chunk_size>0, chunk_overlap>=0 및 함께 있으면 overlap<size를 검사한다. bool은 정수로 허용하지 않는다.
- [ ] 3. section_count/chunk_count/warning_count는 bool 제외 0 이상 정수로 검사한다.
- [ ] 4. warnings 원문·본문·토큰·원시 예외는 저장하지 않고 경고 개수 생산은 호출자 책임으로 유지한다.
- [ ] 5. 입력 미변경·unknown 필드 제거·성공 보고서와 실패 정보 모순 및 기존 embedding 보고서 회귀를 검증한다.

## 완료 기준과 검증

허용 필드 보존, 잘못된 자료형/범위/상호 모순 거부, 비밀값 미저장과 embedding 회귀가 통과한다. 새 테이블·범용 serializer·보고서 생산부 변경은 없다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

현재 기준선은 45개 중 10개 통과·35개 격리 MySQL 설정 부재로 skip이다. 이전 결과는 이 이슈의 신규 구현 검증으로 간주하지 않는다. 변경에 맞는 테스트를 실행하고 명령·버전·통과/skip/실패·대역 범위를 기록한다.

## 책임과 대기 처리

관계형 DB 저장·조회·제약·트랜잭션·정합성만 다룬다. 파일 처리·ingest 콜백·외부 실행 흐름·Chroma 내부·인증 발급·검색/챗봇/UI 연결은 제외한다. 기존 혼합 서비스는 SQL 경계만 재사용하며 이동/재작성하지 않는다.

테스트 DB는 127.0.0.1의 빈 hyundap_schema_test / hyundap_runtime_test / hyundap_workflow_test만 사용한다. 기존 자료가 있는 대상은 fixture 준비를 중단한다. 공유 DB DDL/DML·실문서·실제 모델 API 테스트는 수행하지 않는다.

실제 DB 접속 또는 외부 계약이 없으면 해당 항목만 대기로 표시하고 DB 구현·실제 구조 점검·외부 연계 상태를 구분한다. 기존 이슈/WBS를 자동 종료·재배정하지 않는다.

근거: 사용자 제공 DB 담당 범위 한정 계획(2026-10-26), docs/db-design/P1_DEVELOPMENT_READY.md. 준비 문서는 현재 로컬 미커밋 상태다.


## 단계 08 · [#159](https://github.com/paul8004-lee/Hyundap/issues/159)

## 목적과 기준

단계 보고서 영속화·청크 수 정합성·시도 이력 검증. db01 / develop 98d428e 기준 DB P1 잔여 보완·검증 단계다. 기존 전체 기능의 재구현이 아니다.

| 항목 | 내용 |
|---|---|
| 단계 | DB-P1-08 (수동 관리 번호, 공식 WBS 번호 아님) |
| 예상 순수 작업량 | 45~75분 |
| 선행 | #152, #157, #158 |
| 관련 기존 이슈 | #121 #122 |
| 대상 파일·함수 | jobs_repo.record_stage_result/_check_stage_result/_merge_stage_reports, DB_contract.md, test_db_runtime.py |

## 세부 실행 순서

- [ ] 1. 전달받은 chunk_count와 저장된 expected_chunk_count 비교 위치·실행 조건·오류를 확정한다. normalize_report에는 DB 조회를 넣지 않는다.
- [ ] 2. 같은 연결에서 버전·작업 소속과 저장된 청크 수를 확인하고 모순 입력의 부분 저장을 막는다.
- [ ] 3. 성공 단계 보호와 오래된 지연 보고서 거부 동작을 검증한다.
- [ ] 4. 재시도 이전 시도 기록과 다른 단계 JSON이 보존되는지 확인한다.
- [ ] 5. 원시 메시지 없이 DB 계약 오류를 반환하고 조회 장애를 성공/빈 보고서로 숨기지 않는다.

## 완료 기준과 검증

청크 수 모순 시 저장 취소, 소속 불일치 거부, 성공 단계 보호, 이전 시도/단계 보존이 실제 격리 MySQL에서 통과한다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

현재 기준선은 45개 중 10개 통과·35개 격리 MySQL 설정 부재로 skip이다. 이전 결과는 이 이슈의 신규 구현 검증으로 간주하지 않는다. 변경에 맞는 테스트를 실행하고 명령·버전·통과/skip/실패·대역 범위를 기록한다.

## 책임과 대기 처리

관계형 DB 저장·조회·제약·트랜잭션·정합성만 다룬다. 파일 처리·ingest 콜백·외부 실행 흐름·Chroma 내부·인증 발급·검색/챗봇/UI 연결은 제외한다. 기존 혼합 서비스는 SQL 경계만 재사용하며 이동/재작성하지 않는다.

테스트 DB는 127.0.0.1의 빈 hyundap_schema_test / hyundap_runtime_test / hyundap_workflow_test만 사용한다. 기존 자료가 있는 대상은 fixture 준비를 중단한다. 공유 DB DDL/DML·실문서·실제 모델 API 테스트는 수행하지 않는다.

실제 DB 접속 또는 외부 계약이 없으면 해당 항목만 대기로 표시하고 DB 구현·실제 구조 점검·외부 연계 상태를 구분한다. 기존 이슈/WBS를 자동 종료·재배정하지 않는다.

근거: 사용자 제공 DB 담당 범위 한정 계획(2026-10-26), docs/db-design/P1_DEVELOPMENT_READY.md. 준비 문서는 현재 로컬 미커밋 상태다.


## 단계 09 · [#160](https://github.com/paul8004-lee/Hyundap/issues/160)

## 목적과 기준

현재 세션·회사·활성 문서 조회 계약 회귀 검증. db01 / develop 98d428e 기준 DB P1 잔여 보완·검증 단계다. 기존 전체 기능의 재구현이 아니다.

| 항목 | 내용 |
|---|---|
| 단계 | DB-P1-09 (수동 관리 번호, 공식 WBS 번호 아님) |
| 예상 순수 작업량 | 1~2시간 |
| 선행 | #152 |
| 관련 기존 이슈 | #120 #121 |
| 대상 파일·함수 | sessions_repo.current_actor, documents_repo.get_documents_with_active_version, auth/permissions.py(기존 DB 조회 계약 검증), test_db_runtime.py |

## 세부 실행 순서

- [ ] 1. 서버 문맥의 role/company_id를 신뢰하지 않고 기존 세션 증명과 DB 현재 값을 읽는 경계를 확인한다.
- [ ] 2. 만료·폐기·auth_version 변경·비활성 계정/회사·위조 회사/역할을 합성 데이터로 검사한다.
- [ ] 3. 11필드 이름/타입/NULL, 없는 ID 누락·빈 입력 []·DB 장애 DB_UNAVAILABLE을 확인한다.
- [ ] 4. 문서 삭제·비공개·평가·ready 아님·활성 버전 변경·빈 version_id의 거부 사례를 확인한다.
- [ ] 5. SESSION_EXPIRED/ACCESS_DENIED/STATE_CHANGED/INVALID_INPUT을 계약에 명시한다. 인증 구현이나 search/chatbot 호출부는 수정하지 않는다.

## 완료 기준과 검증

기존 DB 조회·재검증 함수의 계약 테스트가 통과한다. 새로운 인증 정책·토큰 발급·선택 회사 교집합·DTO·인용 처리 변경 없이 DB 결과만 검증한다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

현재 기준선은 45개 중 10개 통과·35개 격리 MySQL 설정 부재로 skip이다. 이전 결과는 이 이슈의 신규 구현 검증으로 간주하지 않는다. 변경에 맞는 테스트를 실행하고 명령·버전·통과/skip/실패·대역 범위를 기록한다.

## 책임과 대기 처리

관계형 DB 저장·조회·제약·트랜잭션·정합성만 다룬다. 파일 처리·ingest 콜백·외부 실행 흐름·Chroma 내부·인증 발급·검색/챗봇/UI 연결은 제외한다. 기존 혼합 서비스는 SQL 경계만 재사용하며 이동/재작성하지 않는다.

테스트 DB는 127.0.0.1의 빈 hyundap_schema_test / hyundap_runtime_test / hyundap_workflow_test만 사용한다. 기존 자료가 있는 대상은 fixture 준비를 중단한다. 공유 DB DDL/DML·실문서·실제 모델 API 테스트는 수행하지 않는다.

실제 DB 접속 또는 외부 계약이 없으면 해당 항목만 대기로 표시하고 DB 구현·실제 구조 점검·외부 연계 상태를 구분한다. 기존 이슈/WBS를 자동 종료·재배정하지 않는다.

근거: 사용자 제공 DB 담당 범위 한정 계획(2026-10-26), docs/db-design/P1_DEVELOPMENT_READY.md. 준비 문서는 현재 로컬 미커밋 상태다.


## 단계 10 · [#161](https://github.com/paul8004-lee/Hyundap/issues/161)

## 목적과 기준

게시 SQL의 포인터 보호·조건부 갱신·원자성 검증. db01 / develop 98d428e 기준 DB P1 잔여 보완·검증 단계다. 기존 전체 기능의 재구현이 아니다.

| 항목 | 내용 |
|---|---|
| 단계 | DB-P1-10 (수동 관리 번호, 공식 WBS 번호 아님) |
| 예상 순수 작업량 | 45~75분 |
| 선행 | #152, #156, #157 |
| 관련 기존 이슈 | #122 |
| 대상 파일·함수 | version_service.prepare_publication/read_publishable_candidate/check_publication_conflict/store_pending_publication, test_upload_workflow.py |

## 세부 실행 순서

- [ ] 1. 기존 SQL 경계의 입력·기대 활성 ID·manifest seal·잠금·반환 규격을 정리한다.
- [ ] 2. 적격 후보만 ready 및 published 포인터로 전환하고 작업 기록과 같은 TX로 저장하는지 확인한다.
- [ ] 3. 기대 활성 ID 불일치·오래된 후보·미복구 작업·동시 게시 충돌을 검사한다.
- [ ] 4. 중간 SQL 실패의 전체 롤백과 같은 후보 게시의 멱등 결과를 확인한다.
- [ ] 5. 외부 함수는 기존 대역/준비 입력만 사용하고 활성화·호출 순서를 변경하지 않는다.

## 완료 기준과 검증

포인터/버전/작업 기록의 원자성, 경쟁 1건 성공, 최신 포인터 보호와 외부 호출 중 SQL 잠금 미유지가 검증된다. 실제 Chroma 성공은 완료 근거로 사용하지 않는다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

현재 기준선은 45개 중 10개 통과·35개 격리 MySQL 설정 부재로 skip이다. 이전 결과는 이 이슈의 신규 구현 검증으로 간주하지 않는다. 변경에 맞는 테스트를 실행하고 명령·버전·통과/skip/실패·대역 범위를 기록한다.

## 책임과 대기 처리

관계형 DB 저장·조회·제약·트랜잭션·정합성만 다룬다. 파일 처리·ingest 콜백·외부 실행 흐름·Chroma 내부·인증 발급·검색/챗봇/UI 연결은 제외한다. 기존 혼합 서비스는 SQL 경계만 재사용하며 이동/재작성하지 않는다.

테스트 DB는 127.0.0.1의 빈 hyundap_schema_test / hyundap_runtime_test / hyundap_workflow_test만 사용한다. 기존 자료가 있는 대상은 fixture 준비를 중단한다. 공유 DB DDL/DML·실문서·실제 모델 API 테스트는 수행하지 않는다.

실제 DB 접속 또는 외부 계약이 없으면 해당 항목만 대기로 표시하고 DB 구현·실제 구조 점검·외부 연계 상태를 구분한다. 기존 이슈/WBS를 자동 종료·재배정하지 않는다.

근거: 사용자 제공 DB 담당 범위 한정 계획(2026-10-26), docs/db-design/P1_DEVELOPMENT_READY.md. 준비 문서는 현재 로컬 미커밋 상태다.


## 단계 11 · [#162](https://github.com/paul8004-lee/Hyundap/issues/162)

## 목적과 기준

실패·재시도·복구 SQL 기록과 최신 포인터 보호 검증. db01 / develop 98d428e 기준 DB P1 잔여 보완·검증 단계다. 기존 전체 기능의 재구현이 아니다.

| 항목 | 내용 |
|---|---|
| 단계 | DB-P1-11 (수동 관리 번호, 공식 WBS 번호 아님) |
| 예상 순수 작업량 | 45~75분 |
| 선행 | #161, #159 |
| 관련 기존 이슈 | #122 |
| 대상 파일·함수 | versions_repo.mark_failed, version_service.prepare_recovery/restore_database_pointer/finish_recovery, 기존 retry 저장 함수의 SQL 경계, test_upload_workflow.py |

## 세부 실행 순서

- [ ] 1. 실패 후보 상태·오류·작업 기록의 DB 전이와 활성/ready 보호를 확인한다.
- [ ] 2. 동일 후보/작업 ID 재시도와 이전 시도 이력 보존을 SQL 경계에서 검증한다.
- [ ] 3. 조건부 복구가 최신 게시 포인터를 덮지 않으며 이전 적격 버전만 복구하는지 확인한다.
- [ ] 4. 차단/삭제/이전 버전 부적격·중간 SQL 실패·복구 반복 입력을 검증한다.
- [ ] 5. 기존 혼합 서비스는 이동/재작성하지 않고 외부 정리·파일·재시도 흐름 변경은 별도 연계 사항으로 기록한다.

## 완료 기준과 검증

실패/복구의 DB 정합성, 같은 ID/이력 유지, 오래된 복구의 최신 포인터 보호와 롤백이 통과한다. Chroma 정리·전체 복구 조율은 범위 밖이다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

현재 기준선은 45개 중 10개 통과·35개 격리 MySQL 설정 부재로 skip이다. 이전 결과는 이 이슈의 신규 구현 검증으로 간주하지 않는다. 변경에 맞는 테스트를 실행하고 명령·버전·통과/skip/실패·대역 범위를 기록한다.

## 책임과 대기 처리

관계형 DB 저장·조회·제약·트랜잭션·정합성만 다룬다. 파일 처리·ingest 콜백·외부 실행 흐름·Chroma 내부·인증 발급·검색/챗봇/UI 연결은 제외한다. 기존 혼합 서비스는 SQL 경계만 재사용하며 이동/재작성하지 않는다.

테스트 DB는 127.0.0.1의 빈 hyundap_schema_test / hyundap_runtime_test / hyundap_workflow_test만 사용한다. 기존 자료가 있는 대상은 fixture 준비를 중단한다. 공유 DB DDL/DML·실문서·실제 모델 API 테스트는 수행하지 않는다.

실제 DB 접속 또는 외부 계약이 없으면 해당 항목만 대기로 표시하고 DB 구현·실제 구조 점검·외부 연계 상태를 구분한다. 기존 이슈/WBS를 자동 종료·재배정하지 않는다.

근거: 사용자 제공 DB 담당 범위 한정 계획(2026-10-26), docs/db-design/P1_DEVELOPMENT_READY.md. 준비 문서는 현재 로컬 미커밋 상태다.


## 단계 12 · [#163](https://github.com/paul8004-lee/Hyundap/issues/163)

## 목적과 기준

DB P1 최종 회귀·계약 문서·완료 상태 정리. db01 / develop 98d428e 기준 DB P1 잔여 보완·검증 단계다. 기존 전체 기능의 재구현이 아니다.

| 항목 | 내용 |
|---|---|
| 단계 | DB-P1-12 (수동 관리 번호, 공식 WBS 번호 아님) |
| 예상 순수 작업량 | 30~60분 |
| 선행 | #153, #154, #155, #156, #157, #158, #159, #160, #161, #162 |
| 관련 기존 이슈 | #118 #119 #120 #121 #122 |
| 대상 파일·함수 | DB_contract.md, DB_schema.md, DB/auth 명세, P1_DEVELOPMENT_READY.md, DB 테스트 기록 |

## 세부 실행 순서

- [ ] 1. 각 세부 단계의 실제 변경·재사용·검증 증거와 미해결 계약을 확인한다.
- [ ] 2. 동일 고정 버전 환경에서 DB 관련 테스트를 실행하고 실행 명령·버전·통과/skip/실패와 대역 범위를 기록한다.
- [ ] 3. 함수 서명·입력/반환 예·NULL·오류·TX 소유권을 실제 구현과 문서에서 대조한다.
- [ ] 4. DB 구현 완료/실제 구조 점검 대기/운영 적용 대기/외부 연계 대기를 구분한다.
- [ ] 5. 기존 이슈나 WBS를 자동 완료/재배정하지 않고 잔여 연계 사항과 후속 검증 조건을 남긴다.

## 완료 기준과 검증

저장·조회·정합성 테스트 결과와 계약이 일치한다. 실제 구조 점검이 대기면 전체 완료로 과장하지 않고 해당 상태를 별도로 명시한다.

```powershell
python -m unittest hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

현재 기준선은 45개 중 10개 통과·35개 격리 MySQL 설정 부재로 skip이다. 이전 결과는 이 이슈의 신규 구현 검증으로 간주하지 않는다. 변경에 맞는 테스트를 실행하고 명령·버전·통과/skip/실패·대역 범위를 기록한다.

## 책임과 대기 처리

관계형 DB 저장·조회·제약·트랜잭션·정합성만 다룬다. 파일 처리·ingest 콜백·외부 실행 흐름·Chroma 내부·인증 발급·검색/챗봇/UI 연결은 제외한다. 기존 혼합 서비스는 SQL 경계만 재사용하며 이동/재작성하지 않는다.

테스트 DB는 127.0.0.1의 빈 hyundap_schema_test / hyundap_runtime_test / hyundap_workflow_test만 사용한다. 기존 자료가 있는 대상은 fixture 준비를 중단한다. 공유 DB DDL/DML·실문서·실제 모델 API 테스트는 수행하지 않는다.

실제 DB 접속 또는 외부 계약이 없으면 해당 항목만 대기로 표시하고 DB 구현·실제 구조 점검·외부 연계 상태를 구분한다. 기존 이슈/WBS를 자동 종료·재배정하지 않는다.

근거: 사용자 제공 DB 담당 범위 한정 계획(2026-10-26), docs/db-design/P1_DEVELOPMENT_READY.md. 준비 문서는 현재 로컬 미커밋 상태다.
