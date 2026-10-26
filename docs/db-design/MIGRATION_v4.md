# v3 → v4 준비·적용·검증 절차

대상 #119. 마이그레이션 준비 도구는 SELECT·SHOW 등 읽기 전용 조회만 실행한다. 공유 DB의 DDL·DML 적용은 하지 않았다. 실제 공유 DB 접속 설정은 이번 작업 환경에 없으므로, 실제 구조 비교 항목은 미완료다.

## 1. 준비 — DB 읽기 권한으로 실행

레포 최상위 `.env` 또는 프로세스 환경의 DATABASE_URL을 사용한다. 비밀번호는 이슈·명령행·출력에 붙이지 않는다. Python 3.11과 requirements-db.txt 의존성을 사용한다. 명령은 `hyundap` 폴더에서 실행한다.

```powershell
python -m db.connection
python -m db.schema --inspect
python -m db.schema --prepare ..\docs\db-design\migrations\004_local.sql
```

`--inspect`는 구조만 출력한다. `--prepare`는 실제 키 charset/collation을 읽어 FK 정의에 반영하며 SQL 파일이 이미 있으면 덮어쓰지 않는다. 생성 파일은 해당 DB의 검토용 산출물이다. 환경별 파일은 Git에서 제외한다. 이 도구는 mysql CLI 실행이나 원격 쓰기를 하지 않는다.

지원 전제: MySQL 8.4/InnoDB, 단일 현재 버전 행=3, 기존 companies/users/documents/sessions/index_jobs 및 호환 보조 테이블. ID는 id VARCHAR(36), documents.company_id는 companies.id와 호환. 다른 버전·키 타입·기존 대상 컬럼 타입 불일치·부분 적용된 v4 테이블은 중단한다. 자동 이름 변경·삭제·타입 축소는 하지 않는다.

준비 시 문서 회사 orphan, 정규화 이메일 중복·254자 초과도 건수로 확인한다. DB 예외 원문·사용자 행은 출력하지 않는다. 준비 이후 데이터가 바뀔 수 있으므로 실제 적용 직전 쓰기를 중지하고 다시 점검한다.

## 2. 적용 전 검토 — 관리자

1. 사용자 쓰기·작업 실행을 중지한다. 기존 스키마·데이터 백업과 별도 DB 복원 시험을 확보한다.
2. 생성 SQL을 검토한다. 기존 필수 컬럼·enum·기본값·업무 상태와 새 계약의 호환성을 확인한다. DDL 권한 없는 팀원 계정은 적용하지 않는다.
3. legacy 문서는 draft/활성 포인터 NULL로 남는 것을 확인한다. 기존 파일·벡터를 추측해서 ready로 만들지 않는다. 기존 건수와 PK를 기록해 적용 후 비교한다.
4. 기존 세션이 새 인증에서 무효화되는 정책과 비밀번호 해시 없는 계정의 로그인이 거부되는 정책을 확인한다. OAuth 테이블·기록을 삭제하지 않는다.
5. 동일 MySQL 버전의 복제 DB에 먼저 적용하고 아래 검증을 통과시킨다. 이 레포의 격리 fixture는 실제 DB 복제본이 아니다.

## 3. DDL 적용 — 이번 작업에서는 실행하지 않음

관리자의 MySQL 인터랙티브 세션에서 검토된 `004_local.sql`을 SOURCE로 실행한다. `--force`를 사용하지 않는다. SQL은 기존 컬럼을 보존하며 새 컬럼·테이블·제약만 추가한다. 작업 중 쓰기를 재개하지 않는다. `004_tables.sql`은 placeholder가 있는 템플릿이므로 직접 실행하지 않는다.

DDL은 암시적 커밋을 일으킬 수 있어 전체 SQL을 BEGIN/ROLLBACK으로 되돌릴 수 없다. 중간 오류가 나면 즉시 중지하고 마지막 성공 문장을 기록한다. 현재 버전 표시는 3으로 남는다. 생성기를 재실행해 부분 적용을 무시하지 않는다. 백업을 별도 DB로 복원해 재검증하거나, 실제 적용 상태를 비교한 관리자가 남은 문장만 검토해 실행한다. 자동 DROP 롤백은 제공하지 않는다.

## 4. 검증 후 버전 표시

```powershell
python -m db.schema --check
```

이 단계는 버전 표시가 아직 3이어도 v4 컬럼·UNIQUE·FK·CHECK와 DB 내부 정합성을 검사한다. 모든 위반 건수가 0이어야 한다. `--check`는 버전 번호를 변경하지 않는다. UNIQUE/FK는 구조로, 데이터 정합성은 `004_verify.sql`로 검사한다. 외부 Chroma 검증은 #122에 남긴다.

동일 관리자 세션에서 `004_finalize.sql`을 SOURCE로 실행한다. `changed_version_rows`가 정확히 1이고 위 점검이 성공한 것을 확인한 뒤 `COMMIT;`을 직접 실행한다. 1이 아니면 `ROLLBACK;` 후 버전 행부터 조사한다. finalize 파일 자체는 COMMIT하지 않는다. 실행 후 `python -m db.connection`과 `python -m db.schema --check`를 다시 실행한다.

기존 연결 점검은 버전 3도 허용하지만 `version_storage_ready=False`로 표시한다. 버전 4는 필요한 구조가 확인돼야 성공한다. 이 표시는 인증·repo·업로드 서비스 구현 완료나 실제 파일/벡터 이행 완료를 뜻하지 않는다.

## 5. 복구·재개·검증 범위

DDL 이후 장애: 쓰기 중지 유지 → 적용 문장·버전·위반 건수 확인 → 복제 DB/백업을 기반으로 복구 계획 검토. 새 버전 데이터를 기록한 이후 옛 앱으로 단순 전환하면 기존 문서와 활성 버전이 달라질 수 있으므로 자동 다운그레이드하지 않는다. 운영 복원은 관리자 작업이다.

레거시 문서의 명시적 가져오기·재색인과 실제 DB/원본/Chroma 정합성은 #122에서 구현한다. 가져오기가 끝나지 않은 회사는 신규 업로드를 막아 기존 해시가 새 중복 조회에서 누락되는 것을 방지한다. 새 앱 쓰기는 #120~#122 준비와 해당 이행 검증이 끝나야 재개한다.

### 재현 가능한 격리 테스트

`hyundap/tests/test_schema_migration.py`는 DB_MIGRATION_TEST_URL의 host=127.0.0.1, database=hyundap_schema_test만 허용한다. 기존 테이블이 하나라도 있으면 중단한다. 운영 URL을 넣지 않는다. MySQL 8.4의 빈 일회용 DB를 만든 후 환경변수에 로컬 테스트 URL을 설정하고 실행한다.

```powershell
python -m unittest tests.test_db_connection tests.test_schema_migration -v
```

DB URL이 없으면 통합 테스트는 명시적으로 skip된다. 테스트는 대표 v3 구조를 직접 만들고 데이터 보존·서로 다른 collation 반영·회사/FK·중복·CHECK·부분 manifest 탐지·버전 4 연결 검사를 확인한다. 이는 실제 공유 DB의 스키마/데이터 이행 성공 증거가 아니다.

공식 근거: [MySQL FK의 타입·문자셋·collation 조건](https://dev.mysql.com/doc/refman/8.4/en/create-table-foreign-keys.html), [DDL 암시적 커밋](https://dev.mysql.com/doc/refman/8.4/en/implicit-commit.html).
