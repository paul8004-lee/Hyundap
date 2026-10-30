# DB 학습 수준 기준 전수 검토·리팩터링

기준: 2026-10-28, `test-01` 작업 트리. 이번 작업 이전의 미커밋 P3 변경을 포함해 검토하며 해당 변경은 보존한다. 과거 125개 함수·21개 개선 보고서는 당시 기록으로 유지한다.

## 범위와 학습 근거

- `hyundap/db`의 Python 파일 18개, 정의 144개를 대상으로 한다. 중첩 함수와 `DbError.__init__`도 포함한다. SQL·JSON 계약과 `hyundap/db/docs/spec.md`는 보존해야 할 계약으로 참조한다.
- `LEARNER_CODE_CONTEXT.md`, `LEARNER_EVIDENCE.md`, 지정 폴더의 노트북 40개를 정적으로 확인했다. 노트북이나 외부 API는 실행하지 않았다.
- Day6_1의 `format_docs()`는 빈 list → for → append → join, VOC의 `make_ticket()`는 dict 조회 → 중간 변수 → 조건별 return 형태다. 도구 호출의 `get_weather()`는 튜플 해체와 이름 있는 필드를 사용한다. 심화2의 `rrf()`는 중첩 for의 바깥/안쪽 자료와 누적 dict를 구분한다.
- 일반 함수·dict/list·명시적 if/for·f-string·pathlib/json/re를 기준으로 한다. SQLAlchemy·트랜잭션·잠금·ContextVar의 숙련은 확인되지 않았으므로 별도 설명한다. 실행 번호·출력·첨부 화면을 독립 설계 능력의 증거로 해석하지 않는다.
- 코드 짧게 만들기보다 실행 순서가 읽히게 한다. 공개 인자·반환값·예외 코드·검증 순서·잠금·롤백·비밀값 보호는 유지한다. 새 클래스나 의존성은 추가하지 않는다.

## 수정 전 선정 목록

각 항목을 수정하고 관련 테스트를 통과한 뒤 다음 항목으로 이동한다. 같은 함수의 설명 보강은 코드 수정에 포함한다.

| 순서 | 함수 | 수정 이유와 방법 | 진행 |
|---|---|---|---|
| 1 | `company_routines._raise_routine_error` | 조건식 두 개를 명시적 if로 풀고 DB 오류 번호와 공개 오류의 대응 설명 | 완료 · 5개 통과 |
| 2 | `jobs_repo._failure_detail` | 중첩 get 대신 키 존재 여부에 따라 code/error_code 선택. 명시적 None은 계속 거부 | 완료 · 16개 통과 |
| 3 | `company_credentials.read_credential` | 설정 읽기·회사 선택·필드 추출·검증 순서와 반환값을 분리 | 완료 · 4개 통과 |
| 4 | `company_credentials.save_credential` | 기존 값 충돌/동일 값 반환을 순서대로 표현. 임시 파일과 원자 교체의 이유 설명 | 완료 · 5개 통과 |
| 5 | `documents_repo.get_documents_with_active_version` | 한 줄의 테이블 선택·SQL 치환·IN 바인딩을 중간 변수로 분리 | 완료 · 8개 통과 |
| 6 | `documents_repo._insert_version_and_job` | 두 라우팅 경로를 if/else로 표현하고 동일 반환 중복 제거 | 완료 · 3개 통과 |
| 7 | `tenancy.get_company_engine` | 캐시 재사용 시 조기 반환하여 새 연결 생성의 중첩 제거. 회사 상태 검사는 캐시보다 먼저 유지 | 완료 · 15개 통과 |
| 8 | `company_admin.create_company_db_user` | 접속 대상 비교와 GRANT SQL 조립을 명명된 변수로 분리. 생성·권한·저장·해제·확인의 단계 표시 | 완료 · 8개 통과 |
| 9 | `company_admin.provision_company` | 접속 대상 비교를 명시화하고 등록→DB 생성→스키마→권한→활성화의 단계 정리 | 완료 · 4개 통과 |
| 10 | `version_service.company_is_active` | 조건식의 잠금 접미사를 if로 표현하고 조회/프로시저 경로 설명 | 완료 · 3개 통과 |
| 11 | `version_service.require_manager` | 역할 검사와 회사 범위 검사를 별도 if로 표현. 평가 순서와 오류 유지 | 완료 · 4개 통과 |

설명 보강도 완료한 8개: `company_routines.procedure_statements`, 그 안의 `read_or_lock`, `authenticate_company_actor`, `register_company_route`, `runtime.get_engine`, `runtime.transaction`, `tenancy.company_scope`, `tenancy.processing_lock`.

나머지 125개 정의는 유지한다. 전체 목록과 개별 판단은 [DB_LEARNER_FUNCTION_INVENTORY.md](DB_LEARNER_FUNCTION_INVENTORY.md)에 기록한다.

## 학습 범위를 넘어도 유지해야 하는 구조

| 구조 | 쉬운 해석 | 유지 이유 |
|---|---|---|
| SQLAlchemy `Engine` / `Connection` | 연결을 빌려주는 창구 / 빌린 실제 연결 | 여러 요청이 쓰는 연결을 재사용하고 올바르게 반환 |
| `with transaction(...)`, `yield` | with 블록에 연결을 잠시 넘기고 끝나면 돌아옴 | 직접 연 TX만 커밋·롤백, 외부 TX는 호출자가 결정 |
| `FOR UPDATE`와 조건부 UPDATE | 읽은 행을 잠그고 예상했던 값일 때만 변경 | 동시에 게시·재시도해도 포인터와 순번을 보호 |
| `ContextVar`, `RLock` | 요청별 회사 기억 / 같은 프로세스의 작업 순서 조정 | 일반 전역 변수로 바꾸면 회사가 섞이거나 중복 처리 가능 |
| `SQL SECURITY DEFINER`, `CALL` | 회사 계정에 허용한 고정 작업만 관리 권한으로 실행 | 공통 인증 테이블 직접 접근을 허용하지 않고 같은 TX 유지 |
| 임시 파일 + `os.replace`, `fsync` | 새 설정을 완성한 뒤 한 번에 교체 | 중간 실패로 기존 비밀번호 설정 파일이 잘리지 않게 보호 |
| dataclass 변환과 함수 주입 | 다른 담당자의 결과를 dict로 읽고 함수를 인자로 받음 | 기존 수집·임베딩 계약과 객체 정체성 보존, 외부 API 없이 테스트 |

학습 순서: `clock/errors` → `chunks_repo/jobs_repo` → `documents_repo/versions_repo` → `upload_service/version_service` → `connection/runtime/tenancy` → 회사 계정·프로시저·스키마 관리. 한 함수씩 입력 → 조회/검사 → 변경 → 반환 → 실패 경로를 따라 읽는다.

## 검증 기록

함수별 수정 직후 검사 결과는 위 진행 열에 기록했다. 동일 회귀가 여러 단계에 포함되므로 합계를 서로 다른 테스트 개수로 해석하지 않는다.

| 순서 | 실제 실행한 검사 묶음 | 결과 |
|---|---|---|
| 1 | `test_company_routines` | 5개 통과 |
| 2 | `test_db_refactor` + `test_db_runtime.OfflineRuntimeTests` | 16개 통과 |
| 3 | `test_company_credentials` | 4개 통과 |
| 4 | `test_company_credentials` + 동일 저장/교체 실패 회귀 추가 | 5개 통과 |
| 5 | 회사별 배치 조회·모호한 소유자/장애·단일 DB 메타데이터 + 오프라인 계약 | 8개 통과 |
| 6 | 공통 라우팅/회사 원장 공동 롤백·회사 계정 격리/플랫폼 관리자·작업 INSERT 실패 롤백 | 3개 통과 |
| 7 | 비활성 회사 캐시 차단·계정 모드 전환·소속 변조·회사별 병렬 실행 + 기존 경계 회귀 | 15개 통과 |
| 8 | 회사 계정 격리·신규 회사 전용 계정·설정 저장 실패 잠금 + 자격 설정 회귀 | 8개 통과 |
| 9 | 회사 생성 실패/멱등성·별도 DML 계정·전용 계정 생성·CLI 대상 검사 | 4개 통과 |
| 10 | 회사 계정 격리·인증 잠금 플래그·비활성 회사 게시 차단 | 3개 통과 |
| 11 | 3개 회사/역할·무권한 업로드·임베딩 중 권한 변경·회사 계정 격리 | 4개 통과 |
| 설명 보강 후 | 프로시저·설정·경계 회귀 묶음 | 21개 통과 |

추가한 회귀는 세 가지다. 프로시저 오류 번호/메시지가 없거나 알 수 없는 경우에도 비밀을 노출하지 않는지, `code`와 `error_code`가 함께 있을 때 우선순위와 명시적 잘못된 값의 거부가 유지되는지, 동일 비밀 설정의 재요청과 파일 교체 실패가 기존 파일을 보존하는지 확인한다.

`hyundap/tests/run_isolated_mysql.py`에는 선택한 unittest 이름만 실행하는 선택 인자를 추가했다. 인자 없이 실행하면 종전과 같이 전체 테스트를 실행한다. 새 빈 로컬 MySQL 서버/DB만 사용하고 건너뜀이 있으면 실패로 반환하는 기준은 유지한다.

```powershell
# 로컬 MySQL 설치 경로를 MYSQL_BASEDIR에 지정한 상태에서 실행
.\.venv\Scripts\python.exe -m hyundap.tests.run_isolated_mysql hyundap.tests.test_company_routines
.\.venv\Scripts\python.exe -m hyundap.tests.run_isolated_mysql
```

정적 확인: 시작 작업 트리 대비 144개 정의의 이름·인자·기본값 유지, 본문 AST 변경 11개, 설명 보강 8개. `compileall`과 `git diff --check` 통과. Python 주석·문자열에서 대체문자/연속 물음표로 깨진 문자 후보 없음. 프로시저 생성 SQL은 대표 회사 ID 3개 × SQL 5개를 시작 코드와 비교해 문자열이 동일했다.

최종 전체 실행: **MySQL 8.4.13 / Python 3.11, 552개 통과, 실패 0·오류 0·건너뜀 0**, 테스트 본체 70.269초. 로컬 서버 초기화/종료 시간은 별도다. 로그는 Git 제외 `.git/learner-refactor-final-tests.log`에 있다. PDF 경고와 인증/임베딩 오류 문구는 오류 입력을 다루는 기존 회귀에서 출력됐으며 unittest 결과는 `OK`, 실행 종료 코드는 0이었다.

Railway와 실행 중인 앱은 변경하지 않았다. 수집/임베딩은 테스트 대역을 사용하며 실제 외부 API·브라우저·Railway 검증으로 해석하지 않는다.

## 바뀐 코드를 읽는 예

기존 `_failure_detail()`의 `failure.get('code', failure.get('error_code'))`는 다음처럼 풀었다.

```python
if 'code' in failure:
    failure_code = failure['code']
else:
    failure_code = failure.get('error_code')
```

`{'code': None, 'error_code': 'LEGACY_FORMAT'}`이면 `None`을 선택하고 다음 검사에서 거부한다. `code` 키가 없을 때만 `error_code`를 선택한다. 단순히 `code or error_code`로 바꾸면 이 계약이 달라지므로 사용하지 않았다.

`require_manager()`는 역할이 관리자인지 먼저 검사하고 회사 관리자의 소속을 다음 if에서 검사한다. 노트북 `make_ticket()`의 조건별 분기와 같은 방식이다. 회사 행 잠금과 현재 세션 재검증은 뒤의 쓰기까지 권한을 유지하기 위한 부분이라 삭제하지 않았다.

`get_company_engine()`은 회사의 현재 활성 상태를 읽은 **다음** 캐시가 있으면 반환한다. 캐시 조회를 함수 맨 앞으로 옮기면 이미 비활성화한 회사가 기존 연결 풀로 접근할 수 있으므로, 읽기 쉬운 코드로 바꾸더라도 이 순서는 유지했다.

## 이번 작업의 경계

DB 소스는 8개 파일을 수정했다. 함수 144개와 예외 클래스 1개는 그대로이며, 새로운 함수·클래스·패키지 의존성을 추가하지 않았다. 기존 SQL/JSON 스키마와 auth/UI/ingest/embedding 코드는 이번 작업에서 변경하지 않았다. 작업 전부터 있던 P3 미커밋 변경은 유지했다. 커밋·푸시는 실행하지 않았다.
