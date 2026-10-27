# 최종 v4 초기화 — #168

2026-10-27 사용자 결정: v3 이행 도구와 운영 전환 경로를 폐기한다. 빈 DB에 최종 v4를 직접 생성하며 기존 DB를 수정하거나 자동 삭제하지 않는다. 과거 혼합 v4도 이 계약과 다르면 지원하지 않는다.

## 실행

1. MySQL 8.4 이상의 **새 빈 DB**를 준비한다. 기존 Railway URL을 초기화용으로 재사용하지 않는다.
2. 프로세스 환경변수 또는 Git에서 제외된 `.env`의 `DATABASE_URL`을 그 DB로 지정한다. 계정에는 초기화 DDL 권한이 필요하다.
3. 저장소 루트에서 명시적으로 실행한다.

```powershell
python -m hyundap.db.schema --create
python -m hyundap.db.schema --check
python -m hyundap.db.connection
```

`--create`만 DB를 생성한다. `--inspect`, `--check`, 연결 점검과 앱 import는 초기화하지 않는다. 앱의 첫 Engine 준비에서도 최종 계약을 검증하며 이후 업무 SQL에는 구조 조회가 없다.

## 생성과 실패 계약

`schema.create_v4_schema(engine)`은 호출자가 명시한 SQLAlchemy Engine의 전용 연결을 사용한다. 업무 Connection이나 기존 트랜잭션을 받지 않는다.

생성 순서는 서버/빈 DB 확인 → DB별 초기화 잠금 → 11개 테이블과 순환 FK 생성 → 전체 구조 검증 → 단일 버전 행 4 기록이다. 동시 초기화는 거부한다. 기존 테이블·뷰가 있으면 DDL 전에 거부한다.

MySQL DDL은 암시적으로 커밋된다. 실패 시 이미 생성된 객체가 남을 수 있으며 자동 DROP·덮어쓰기·강제 재실행은 하지 않는다. 중간 DDL 오류에는 버전 테이블이 없을 수 있고, 마지막 구조 검증 실패에는 버전 테이블만 비어 있다. 이 상태는 성공이 아니다. 원인을 수정한 뒤 별도의 새 빈 DB에서 재실행한다.

## 단일 기준

| 파일 | 역할 |
|---|---|
| `schema_v4.sql` | 사람이 검토할 최종 CREATE DDL |
| `schema_v4_contract.json` | MySQL 8.4.13에서 위 DDL로 생성한 전체 구조의 고정 검증 기준 |
| `schema_v4_verify.sql` | 게시·manifest·소속·활성 포인터의 읽기 전용 정합성 점검 |

검증 기준은 컬럼 타입·NULL·문자셋/정렬·기본값·생성식·EXTRA, InnoDB, PK, 인덱스, UNIQUE, FK/삭제 동작, CHECK 식과 ENFORCED를 포함한다. DDL을 바꾸면 별도 격리 DB에서 실측·검토한 계약도 함께 갱신한다. 앱이 접속한 DB를 기준으로 계약을 자동 재작성하지 않는다.

JSON의 각 컬럼 값 순서는 `[타입, NULL허용, 문자셋, 정렬, 기본값, 생성식, EXTRA]`다. 버전 숫자만 4로 바꿔도 과거 구조는 검증을 통과하지 못한다.

## 인증·작업 계약

사용자 역할은 `platform_admin/company_admin/employee`로 저장한다. 이메일 표시값과 별개로 `LOWER(TRIM(email))` 생성 컬럼을 로그인·중복 판정에 사용한다. 생성/갱신 시간 기본값은 DB의 UTC Unix 초다. 기존 평문 `password` 컬럼은 없다.

세션의 CSRF 해시·유휴/절대 만료·권한 버전은 필수다. `expires_at`은 인증 PR #167의 현재 계약 때문에 유지하며 세 만료 조건을 모두 검사한다. 인증 정책을 임의로 합치지 않는다. BIGINT는 항상 UTC Unix 초이며 단위 변경 환경변수는 사용하지 않는다. DATETIME(6)은 UTC다.

작업의 상태와 단계는 구분한다. attempts만 시도 횟수를 저장하며 version_no는 문서 버전 번호다. 이전 큐의 generation/content_revision/임대 컬럼과 문서의 중복 파일 메타데이터는 없다. 버전당 작업 하나·문서당 활성 작업 하나를 UNIQUE로 보호한다. 관계형 소속은 복합 FK로 보호한다. 다중 프로세스 임대 작업자는 제공하지 않는다.

## 검증

테스트 URL은 각 파일이 지정한 127.0.0.1의 빈 전용 DB만 허용한다.

| 환경변수 | 전용 DB |
|---|---|
| `DB_SCHEMA_TEST_URL` | `hyundap_schema_test` (수용 테스트가 같은 서버에 새 `hyundap_v4_*` DB도 생성) |
| `DB_RUNTIME_TEST_URL` | `hyundap_runtime_test` |
| `DB_WORKFLOW_TEST_URL` | `hyundap_workflow_test` |
| `DB_CONTRACT_TEST_URL` | `hyundap_contract_test` |

```powershell
python -m unittest discover -s hyundap/tests -t .
```

테스트는 실제 MySQL과 합성 자료를 사용한다. 수집·벡터·외부 API는 대역으로 검증하며 수동 API/UI 스크립트는 `hyundap/tests/smoke/`에서 명시적으로 실행한다. 이번 작업에서 Railway DDL/DML이나 외부 API 호출은 수행하지 않는다.
