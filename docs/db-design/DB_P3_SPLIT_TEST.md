# 공통 인증 DB · 회사별 업무 DB 시험 구현

`db-p3-test`에서 시험한다. 기준 develop은 `ee77c11`이다. 기존 Railway와 데이터를
변경하는 이행 도구가 아니다. 기존 단일 v4는 기본값으로 유지한다.

## 저장 구조

| 위치 | 테이블과 역할 |
| --- | --- |
| 공통 DB | companies, users, sessions, oauth_states, audit_logs, login_attempts, schema_migrations |
| 공통 등록부 | company_databases: 회사별 DB 이름과 준비 상태. document_routes: 문서·버전·작업 ID와 회사 ID만 저장 |
| 회사 DB | documents, document_versions, document_chunks, index_jobs, schema_migrations, tenant_identity |

본문·청크 원장은 회사 DB에 저장한다. 원본 파일과 Chroma 벡터는 기존 저장소를 사용하며
이번 구현이 물리적으로 분리하지 않는다. 기존 회사 메타데이터와 접근 검증을 계속 사용한다.
공통 세션의 선택 문서 ID와 감사 로그 문서 ID는 회사 DB 문서의 식별자이므로 기존 로컬 문서 FK를
제거한다. 실제 문서 읽기에서는 현재 세션·회사 권한·활성 버전을 다시 검사한다.

## 주요 함수와 연결

1. `company_admin.provision_company`: 회사와 `creating` 등록 → 새 DB 생성 → 동일 v4 원장
   생성·소속 CHECK·공통 사용자/회사 FK 검증 → 업무 계정 DML 부여 → `ready` 및 회사 활성화.
   `create_split_schema`, `validate_control`, `validate_company`가 구조 검증을 담당한다.
   같은 ID의 ready 재요청은 같은 DB를 반환한다. 실패 시 `failed`와 비활성 회사를 남기고
   부분 생성 DB를 자동 삭제하거나 재생성하지 않는다.
2. `tenancy.company_scope`, `routed_engine`, `get_company_engine`: 요청 회사와 식별자의 소유 회사를
   대조하고 서버 등록부의 DB만 선택한다. 요청이 보낸 URL은 받지 않는다. 문맥은 ContextVar이며
   요청마다 전역 DATABASE_URL을 바꾸지 않는다. 비활성 회사는 기존 연결 풀이 있어도 거부한다.
3. `runtime.transaction`: 회사 원장과 공통 식별자 등록을 같은 MySQL 연결의 트랜잭션으로 저장한다.
   실패하면 둘 다 롤백한다. 로그인·계정 발급은 `control=True`로 공통 DB를 사용하고, 원장에서
   권한을 확인할 때는 같은 연결에서 공통 테이블을 DB 이름으로 한정해 읽는다.
4. 기존 업로드·재시도·게시·복구·청크 저장·보고서 저장·상세/이력 조회는 회사 선택을 거쳐 실행한다.
   여러 회사의 검색 결과 조회는 식별자 등록부로 묶어 회사별로 조회하고 기존 반환 필드를 유지한다.
   `processing_lock`은 분리 모드에서 회사별 잠금을 사용해 다른 회사의 긴 수집을 기다리지 않게 한다.

신규 모듈도 일반 함수 구조를 유지한다. 신규 업무 클래스는 없다.

### 학습 문법 중심의 가독성 보완

- `tenancy.py`: `any()`의 반복식과 조건부 반환을 명시적인 if로 풀었다. 회사 DB URL 생성,
  캐시 키의 세 가지 기준, 요청 문맥 복원과 잠금 선택 이유를 단계별로 설명했다.
- `documents_repo.py`: 회사별 문서 목록을 `setdefault().append()` 대신
  회사 확인 → 빈 목록 생성 → 문서 추가 순서로 작성했다. 두 반복문의 역할을 구분했다.
- `split_schema.py`: 조건부 표현식과 내포식을 if/for로 풀었다. 테이블 반복과
  현재 테이블의 외래키 반복이 각각 무엇을 검사하는지 설명했다.
- `company_admin.py`: 회사 등록 → DB 생성 → 구조 검증 → 업무 권한 부여 → 활성화 순서를
  코드에 표시하고 URL 생성도 중간 변수로 나눴다. 실패 기록과 풀 정리의 역할도 설명했다.

요청 분리를 위한 ContextVar, 회사별 RLock, 연결과 문맥을 정리하는 contextmanager,
Engine 캐시는 유지한다. 공개 함수와 내부 처리 함수는 회사 문맥을 적용하는 경계를 유지한다.
SQL 파일을 별도로 복제하지 않고 기존 v4 원본을 선택하는 방식도 유지한다.

가독성 보완 후 재검증(2026-10-28): 격리 MySQL의 분리 모드 테스트 11개 통과 후,
최종 코드에서 전체 436개가 통과했다. 실패·오류·건너뜀은 0개이며 컴파일과 diff 검사도 통과했다.

## 설정 및 실행

별도의 **새 빈 시험 DB**와 MySQL 8.4 이상이 필요하다. 공통 DB와 회사 DB는 반드시
**동일 MySQL 서버**에 둔다. 서버 간 분산 트랜잭션은 구현하지 않았다.

개인 설정에 아래 이름으로 지정한다. URL과 비밀번호는 Git에 올리지 않는다.

```dotenv
HYUNDAP_DB_LAYOUT=split
DATABASE_URL=<공통 시험 DB의 업무 계정 URL>
DB_PROVISION_URL=<같은 서버·공통 시험 DB의 관리자 계정 URL>
```

관리자는 빈 공통 DB 자체를 먼저 준비한다. 초기화 계정은 CREATE DATABASE/TABLE,
ALTER 및 공통 부모 테이블 REFERENCES, 업무 계정에 대한 GRANT 권한 등이 필요하다.
업무 계정은 공통 DB의 인증·등록부 DML이 필요하다. 회사 생성 함수가 새 회사 DB에 DML을 부여한다.
이 명령은 기존 단일 DB에서 실행하는 명령이 아니다.

```powershell
python -m hyundap.db.company_admin --create-control
python -m hyundap.db.company_admin --company-name "시험 회사"
python -m hyundap.db.connection
```

회사 추가는 위 운영자 함수/명령을 통해 실행한다. UI의 회사 생성 화면은 추가하지 않았다.
일반 앱 요청에 초기화 관리자 권한을 전달하지 않는다. `single` 모드에서는 기존 경로가 동작한다.
연결 설정이나 모드를 변경하면 앱을 재시작한다. 실행 중 환경변수를 바꿔 모드를 전환하지 않는다.

## 검증과 제한

`hyundap/tests/test_split_tenancy.py`는 `DB_SPLIT_TEST_URL`이 지정된 로컬
`127.0.0.1/hyundap_split_test`의 빈 DB에서만 통합 시험한다. 실행 계정에는 로컬 시험용 DDL/GRANT
권한이 필요하다. 테스트별 임의 회사 DB를 생성하며 실제 Railway를 대상으로 실행하지 않는다.
수집과 임베딩은 대역을 사용하므로 실제 OpenAI 비용과 Chroma 외부 호출은 없다.

```powershell
python -m unittest hyundap.tests.test_split_tenancy -v
```

검증 대상은 공통 로그인, 회사별 실제 원장 저장, 권한/잘못된 연결 거부, 재시도/게시 복구,
서로 다른 회사 동시 처리, 생성 실패·멱등성, 소속 변조, 회사 비활성화,
공통/회사 원장의 동시 롤백, 실제 공통 Engine 초기화와 제약 변조 검출이다.

2026-10-28 최종 검증: 기준 `ee77c11`에 이 패치를 적용하고 새 격리 MySQL 8.4.13에서
`python -m unittest discover -s hyundap/tests -t .`를 실행했다.
분리 모드 신규 테스트 11개와 기존 단일 v4·인증·검색·UI 회귀를 포함해 **436개 통과,
실패·오류·건너뜀 0개**다. 컴파일 검사와 `git diff --check`도 통과했다.
브라우저의 실제 조작 및 실제 OpenAI/Chroma 통합 검증을 뜻하지 않는다.

잠금은 **단일 앱 프로세스**에서 회사별로 적용된다. 여러 워커의 실행 임대나 작업 큐는 별도 과제다.
회사별 Engine은 최대 32개이며 초과하면 명시적으로 거부한다. 임의로 사용 중인 풀을 폐기하지 않는다.
실제 Railway 전환·기존 데이터 이행·백업·권한 배포·실제 벡터 저장소 동시성 검증은 포함하지 않는다.
DDL은 암묵 커밋되므로 회사 생성 전체를 원자적으로 되돌릴 수 없다. 실패 상태를 확인한 관리자가
부분 생성 DB와 권한을 점검해야 한다.

MySQL 제약 이름은 스키마 안에서 유일해야 하므로 회사 소속 CHECK는 테이블별 이름을 쓴다.
참고: [MySQL CHECK 계약](https://dev.mysql.com/doc/refman/8.4/en/create-table-check-constraints.html),
[외래키와 REFERENCES 권한](https://dev.mysql.com/doc/refman/8.4/en/create-table-foreign-keys.html).
