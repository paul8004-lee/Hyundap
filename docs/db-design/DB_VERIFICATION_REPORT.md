# db_tset · DB 실제 검증 결과

검증일: 2026-10-26. 접속 비밀값·호스트·계정명·원문 데이터는 기록하지 않는다.

## 판정

격리 MySQL v4 환경의 저장·조회·권한·게시·복구 테스트는 정상이다.
반면 사용자가 .env에 설정한 실제 DB는 접속은 되지만 schema version=3이며,
현재 P1 코드가 요구하는 v4 구조와 맞지 않아 바로 서비스 저장에 사용할 수 없다.
실제 DB에는 읽기 전용 트랜잭션만 사용했으며 DDL/DML을 실행하지 않았다.

## 1. 새 격리 DB 회귀 테스트

기존 UI 시험 DB와 분리된 새 MySQL 8.4.13 인스턴스에 빈 전용 DB 3개를 생성했다.
Python 3.11.9 / SQLAlchemy 2.0.55 / PyMySQL 1.1.2 환경에서 다음 테스트를 실행했다.

```powershell
python -m unittest hyundap.tests.test_semantic_ui_bridge hyundap.tests.test_ingest_bridge hyundap.tests.test_access_queries hyundap.tests.test_ingest_storage hyundap.tests.test_document_storage hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

결과: **75개 통과, 실패 0, 오류 0, 건너뜀 0**.

| 범위 | 확인한 동작 |
|---|---|
| 구조·저장·조회 | v3 fixture의 v4 이행, 제약, 11필드·NULL·빈 입력·조회 장애 |
| 중복·원자성·경쟁 | 회사별 중복·다른 회사 허용·현재 후보 제외·최종 생성 보호·작업 INSERT 실패 롤백·동시 버전 번호 |
| 세션·권한 | 위조 문맥·만료·폐기·auth_version·비활성 회사/계정·삭제·활성 버전 변경 |
| 청크·보고서 | manifest 멱등성·변경 충돌·실제/예상 청크 수·단계 이력·원문 제외·warning_count·콜백 전달 |
| 게시·복구 | 게시 경쟁·외부 실패 대역·부분 적재·포인터/버전 롤백·최신 포인터 보호·첫 게시 실패 복구·동일 ID 재시도 |

벡터/외부 장애는 회귀 테스트에서 기존 대역을 사용한다. 실제 임베딩 시험은
SEMANTIC_SEARCH_TEST.md에서 별도로 기록했다. 검증 전용 인스턴스는 종료했고 UI 서버용 DB는 유지했다.

## 2. 실제 임베딩 UI의 저장 데이터 점검

실제 수집기·임베딩·Chroma 경로로 저장한 합성 문서의 MySQL 상태를 읽기 전용 점검했다.

| 항목 | 결과 |
|---|---|
| schema version / 구조 오류 | 4 / 0건 |
| published 문서 / ready 버전 / manifest 청크 | 각각 3건 |
| complete 작업 | 3건 |
| 성공 수집 chunk_count / warning_count 기록 | 각각 3건 |
| 공개 버전·ready manifest·문서 회사·버전 회사·활성 포인터 정합성 | 5개 검사 모두 위반 0건 |

이 점검은 MySQL 정합성 결과다. 실제 검색 상위 결과 검증은 앞선 의미 검색 시험에 포함돼 있다.

## 3. .env의 실제 DB 읽기 전용 실측

서버 MySQL 8.4.11, 연결 성공, schema_migrations.version=3.
validate_v4 결과 **구조 불일치 진단 35건**이며 이는 데이터 오류 35행이라는 뜻이 아니다.

대표 차이는 문서의 활성 버전·유형·공개 상태 컬럼, 사용자 auth_version·정규화 로그인 컬럼,
세션 만료/권한 버전 컬럼, 작업의 버전·단계·보고서·시도 컬럼과 버전·청크 저장 구조,
복합 FK·UNIQUE·CHECK 제약이다. 일부 기존 컬럼 타입도 현재 코드의 요구와 다르다.

실측상 document_versions·document_chunks 테이블은 없다. documents.deleted_at과
sessions.revoked_at은 bigint, sessions.token_hash는 varchar(64),
index_jobs.error_code·error_message는 longtext다. 날짜 정수의 단위와 NULL 의미 등은
추가 확인 없이 추정 변환하지 않는다.

검토용 SQL 생성도 시도했으나 기존 documents.deleted_at 타입이 예상과 달라
prepare_sql이 `Incompatible existing type: documents.deleted_at`으로 중단했다.
이 보호 조건을 우회하거나 스키마 버전 숫자만 4로 바꾸지 않았다.

다음 작업은 실측된 기존 타입·자료를 보존할 변환 정책과 v3→v4 검토용 SQL을 준비하는 것이다.
실제 DB 적용은 이번 검증 범위에 포함하지 않는다.
