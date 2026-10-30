# DB 파일·함수 전수 목록과 학습 기준 판단

2026-10-28 `test-01` 작업 트리 기준. 현재 코드의 선언을 AST로 집계하고 함수 본문을 읽어 개별 판단을 기록했다. 원본 학습 자료나 과거 검토 보고서는 수정하지 않는다.

주요 진입점은 외부 계약·관리 명령이며, 보조/내부 함수는 상위 흐름 또는 다른 DB 함수에서 사용한다. 이름 앞 밑줄만으로 공개 여부를 결정하지 않았다. 이 표는 동적 호출 커버리지나 함수 삭제 판정표가 아니다.

판단: **코드 정리 11개 / 설명 보강 8개 / 유지 125개 = 144개**. 클래스는 기존 예외 `DbError` 한 개이며 새 클래스를 만들지 않는다.

[작업 계획·변경 결과·테스트](DB_LEARNER_REFACTOR_2026-10-28.md)

## __init__.py

db/__init__.py — 문서·버전·작업 기록을 저장하는 DB 패키지.

- 최상위 import: `.errors: DbError`
- 상수·상태: `__all__`
- 클래스: 없음 — 일반 함수 구조

함수 없음. `DbError`를 패키지에서 공개하는 역할이다.

## chunks_repo.py

버전별 청크 ID·순번·해시 기준 목록의 영속화. 본문·벡터는 저장하지 않는다.

- 최상위 import: `sqlalchemy: text`; `.errors: DbError`; `.runtime: require_hash, require_id, transaction`
- 상수·상태: 없음
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`save_manifest(version_id, chunks, *, connection=None)`](../../hyundap/db/chunks_repo.py#L9) | 주요 진입점 · 처리 중인 버전에 완전한 청크 목록을 한 번 저장한다. 같은 목록 재시도는 허용한다. | 유지 | 기존 원장 비교와 신규 일괄 INSERT의 두 for는 서로 다른 자료를 처리하며 TX를 함께 유지한다. |
| [`_normalize_manifest(version_id, chunks)`](../../hyundap/db/chunks_repo.py#L53) | 보조/내부 · 청크 목록의 필수 값·ID·해시·연속 순번을 검사하고 순번대로 정렬한 새 목록을 반환한다. | 유지 | 청크별 필수 키 검사와 정렬 후 연속 순번 검사는 목적이 다르므로 중첩/후속 반복을 유지한다. |
| [`_chunk_index(chunk)`](../../hyundap/db/chunks_repo.py#L89) | 보조/내부 · 청크 dict에서 정렬 기준인 순번을 반환한다. sort가 항목마다 이 함수를 호출한다. | 유지 | sort가 호출할 명명된 함수라 lambda보다 학습자가 추적하기 쉽다. |

## clock.py

최종 스키마의 BIGINT 시간은 UTC Unix 초로 저장한다.

- 최상위 import: `time`
- 상수·상태: 없음
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`epoch_now()`](../../hyundap/db/clock.py#L6) | 보조/내부 · 현재 UTC Unix 초를 정수로 반환한다. 설정으로 단위를 바꾸지 않는다. | 유지 | 정수 나눗셈으로 Unix 초를 반환한다. 단위 변경이나 부동소수점 변환이 필요 없다. |

## company_admin.py

분리 DB 시험의 운영자 전용 초기화·회사 생성 명령. 웹 요청에 직접 노출하지 않는다.

- 최상위 import: `argparse`; `os`; `re`; `secrets`; `uuid: uuid4`; `sqlalchemy: text`; `sqlalchemy.exc: SQLAlchemyError`; `.connection: create_db_engine`; `.errors: DbError`; `.: runtime`; `.split_schema: create_split_schema`; `.tenancy: quoted_database`; `.company_credentials: credential_path, credential_source, has_credential, save_credential`; `.company_routines: procedure_statements`
- 상수·상태: 없음
- 클래스: 없음 — 일반 함수 구조

직접 실행 블록: `__name__ == "__main__"`이면 `main()`의 종료 코드를 `SystemExit`으로 반환한다.

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`create_company_db_user(company_id, *, admin_engine, credential_path=None, _allow_creating=False)`](../../hyundap/db/company_admin.py#L25) | 주요 진입점 · 준비된 회사 DB에 전용 계정과 인증·라우팅 프로시저를 시험용으로 만든다. | 코드 정리 | 주소 비교·SQL 조립을 이름 있는 변수로 나누고 잠금 생성→권한→파일 저장→해제→연결 검증을 표시한다. |
| [`provision_company(name, *, admin_engine, company_id=None)`](../../hyundap/db/company_admin.py#L141) | 주요 진입점 · 운영자 권한으로 회사 DB를 생성·검증한 후에만 ready로 등록한다. | 코드 정리 | 접속 대상 비교를 분리하고 5단계 상태 전이를 설명한다. DDL 실패를 자동 롤백할 수 있다고 오해하지 않게 한다. |
| [`main()`](../../hyundap/db/company_admin.py#L223) | 주요 진입점 · 새 빈 공통 DB 생성 또는 회사 등록을 명시적으로 실행한다. 비밀 URL은 출력하지 않는다. | 유지 | argparse의 세 가지 명령 분기와 finally의 연결 정리가 이미 직접 드러난다. 웹 기능으로 옮기지 않는다. |

## company_credentials.py

시험용 회사 DB 계정을 Git 제외 로컬 설정에 보관한다.

- 최상위 import: `json`; `os`; `pathlib: Path`; `re`; `tempfile`; `threading: RLock`; `.errors: DbError`
- 상수·상태: `DEFAULT_PATH`, `_lock`
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`credential_source()`](../../hyundap/db/company_credentials.py#L21) | 보조/내부 · 시험 파일과 호스팅 환경변수를 명시적으로 구분한다. | 유지 | 환경변수 값 두 개만 허용하는 짧은 if이며 잘못된 값의 자동 대체를 막는다. |
| [`credential_path()`](../../hyundap/db/company_credentials.py#L29) | 보조/내부 · 시험 프로세스의 별도 경로 또는 기본 Git 제외 경로를 반환한다. | 유지 | 학습한 pathlib와 환경변수의 기본값 사용만으로 경로가 결정된다. |
| [`_validate(company_id, username, password)`](../../hyundap/db/company_credentials.py#L34) | 보조/내부 · 설정에서 읽거나 저장할 회사 ID와 계정·비밀번호를 검사한다. | 유지 | ID→계정명→비밀번호의 검사 순서와 정규식 경계가 직접 보인다. |
| [`_load(path)`](../../hyundap/db/company_credentials.py#L44) | 보조/내부 · 설정 파일을 읽는다. 손상된 파일은 빈 설정으로 덮어쓰지 않는다. | 유지 | 파일 없음과 파일 손상을 구분한다. 손상을 빈 dict로 바꾸면 기존 비밀을 덮어쓰므로 유지한다. |
| [`_load_config(path)`](../../hyundap/db/company_credentials.py#L57) | 보조/내부 · 호스팅 비밀이 없거나 손상되면 로컬 파일로 대체하지 않는다. | 유지 | 파일/호스팅 환경변수 분기가 명확하며 실패 시 로컬 대체를 하지 않는 계약을 유지한다. |
| [`read_credential(company_id, *, path=DEFAULT_PATH)`](../../hyundap/db/company_credentials.py#L70) | 보조/내부 · 설정에서 회사 항목을 찾아 (MySQL 계정명, 비밀번호) 튜플을 반환한다. | 코드 정리 | 회사 항목 선택→계정명 추출→비밀번호 추출→검증→튜플 반환으로 줄을 나눈다. |
| [`has_credential(company_id, *, path=DEFAULT_PATH)`](../../hyundap/db/company_credentials.py#L90) | 보조/내부 · 새 계정 생성 전에 기존 회사 설정이 있는지 확인한다. | 유지 | 저장 전 회사 키 존재 검사만 담당한다. 연결 검증이나 계정 생성과 합치지 않는다. |
| [`save_credential(company_id, username, password, *, path=DEFAULT_PATH)`](../../hyundap/db/company_credentials.py#L98) | 보조/내부 · 새 회사 계정을 파일에 저장한다. 같은 값의 재요청은 아무것도 쓰지 않는다. | 코드 정리 | 충돌 거부와 동일 값 반환을 명시하고 임시 파일·flush·fsync·replace의 실패 보존 이유를 설명한다. |

## company_routines.py

회사 DB에서 사용할 인증·라우팅 프로시저의 시험 계약.

- 최상위 import: `re`; `sqlalchemy: text`; `sqlalchemy.exc: SQLAlchemyError`; `hyundap.auth.errors: AccessError`; `.errors: DbError`; `.runtime: require_id`; `.tenancy: quoted_database`
- 상수·상태: 없음
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`procedure_statements(control_database, company_id, account_name)`](../../hyundap/db/company_routines.py#L18) | 보조/내부 · 검증된 서버 식별자로 회사 전용 인증·라우팅 SQL을 만든다. | 설명 보강 | Python은 SQL 문자열을 만드는 단계다. 실제 DB 실행·SQL 변수·DEFINER·SIGNAL과 다섯 반환 SQL의 역할을 설명한다. |
| [`procedure_statements.read_or_lock(query)`](../../hyundap/db/company_routines.py#L41) | 중첩 보조 함수 · 조회 SQL 하나를 받아 잠금 유무를 선택하는 SQL 문자열로 반환한다. | 설명 보강 | 네 SELECT에 쓰는 동일 SQL 분기를 생성한다. 중첩 함수가 데이터를 조회하는 것이 아님을 설명한다. |
| [`_raise_routine_error(error)`](../../hyundap/db/company_routines.py#L151) | 보조/내부 · DB 예외에서 오류 번호를 읽어 안전한 앱 예외를 발생시킨다. 반환값은 없다. | 코드 정리 | 짧은 조건식을 명시적 if로 풀고 1644/1062 오류의 안전한 대응을 설명한다. |
| [`authenticate_company_actor(database, actor, *, lock=False)`](../../hyundap/db/company_routines.py#L176) | 보조/내부 · 회사 연결에서 세션·회사 권한을 검증하고 현재 사용자 정보를 반환한다. | 설명 보강 | CALL→행 한 개→dict→서버 토큰 보존의 순서를 설명한다. 인증 규칙은 프로시저에 유지한다. |
| [`register_company_route(database, document_id, version_id, job_id)`](../../hyundap/db/company_routines.py#L199) | 보조/내부 · 같은 트랜잭션의 회사 문서·버전·작업을 확인한 뒤 공통 라우팅을 등록한다. | 설명 보강 | 세 ID를 검증하고 동일 연결로 CALL한다. 직접 commit하면 공동 롤백이 깨지는 이유를 설명한다. |
| [`lookup_company_route(database, document_id=None, version_id=None, job_id=None)`](../../hyundap/db/company_routines.py#L213) | 보조/내부 · 회사 계정에서 자기 회사의 문서·버전·작업 라우팅을 조회한다. | 유지 | 조회 목록의 길이가 정확히 1인 경우만 반환하는 단순 분기다. 다중 회사 결과를 임의 선택하지 않는다. |
| [`lock_company_status(database)`](../../hyundap/db/company_routines.py#L225) | 보조/내부 · 회사 행을 잠그고 현재 활성 상태를 반환한다. | 유지 | 한 CALL의 단일 값과 안전한 예외 변환만 필요하므로 더 나눌 이유가 없다. |

## connection.py

db/connection.py — 로컬 접속 설정으로 MySQL 연결을 만들고 상태를 점검.

- 최상위 import: `os`; `pathlib: Path`; `dotenv: load_dotenv`; `sqlalchemy: create_engine`; `sqlalchemy.engine: Engine, make_url`; `sqlalchemy.exc: ArgumentError`
- 상수·상태: 없음
- 클래스: 없음 — 일반 함수 구조

직접 실행 블록: `__name__ == "__main__"`이면 `main()`의 종료 코드를 `SystemExit`으로 반환한다.

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`create_db_engine(database_url=None)`](../../hyundap/db/connection.py#L14) | 주요 진입점 · 로컬 접속 설정을 검사하고 지연 연결 방식의 SQLAlchemy Engine을 만든다. | 유지 | 학습한 .env·Path·명시적 검사 위에 SQLAlchemy 옵션을 둔다. Engine은 지연 연결임을 이미 설명한다. |
| [`main()`](../../hyundap/db/connection.py#L60) | 주요 진입점 · 현재 DB의 연결과 지원 스키마 구조를 읽기 전용으로 확인한다. | 유지 | 읽기 전용 점검→결과 출력→finally 종료가 순차적이며 비밀 URL을 출력하지 않는다. |

## documents_repo.py

회사별 중복 조회·문서/후보 버전 생성·검색용 원시 행과 상세 메타데이터 조회.

- 최상위 import: `uuid: uuid4`; `sqlalchemy: bindparam, text`; `hyundap.auth.errors: AccessError`; `hyundap.auth.permissions: allowed_companies`; `hyundap.common.schemas: DEFAULT_DOC_TYPE, DOC_TYPE_CODES`; `.errors: DbError`; `.runtime: require_hash, require_id, transaction, shared_table, split_layout, isolated_company_connection`; `.sessions_repo: current_actor`
- 상수·상태: `DOCUMENT_INFO_SQL`
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`find_duplicate_document(company_id, sha256, *, exclude_version_id=None, connection=None)`](../../hyundap/db/documents_repo.py#L26) | 주요 진입점 · 같은 회사·해시의 삭제/실패 포함 후보를 dict로 반환한다. 없으면 None이다. | 유지 | 제외 후보 검사 후 조회하는 순서가 명확하다. 회사·해시 경계를 생략하면 자기 중복/타사 제외 문제가 생긴다. |
| [`get_documents_with_active_version(document_ids, *, connection=None)`](../../hyundap/db/documents_repo.py#L61) | 주요 진입점 · 문서 ID 목록으로 검색에 필요한 문서와 활성 버전 정보를 조회한다. | 코드 정리 | 테이블 선택→SQL 치환→IN 바인딩을 중간 변수로 나눈다. 회사별 묶음 조회와 모호한 소유자 제외는 유지한다. |
| [`get_document_detail(actor, document_id, *, version_id=None, connection=None)`](../../hyundap/db/documents_repo.py#L129) | 주요 진입점 · 현재 사용자가 읽을 수 있는 활성 운영 문서의 상세 정보 dict를 반환한다. | 유지 | 입력→회사 권한→활성 버전 조회→허용 필드 반환의 4단계가 이미 명시돼 있다. |
| [`_check_detail_access(document, allowed_company_ids, version_id)`](../../hyundap/db/documents_repo.py#L186) | 보조/내부 · 상세 조회 행의 회사·공개 상태·요청 버전을 검사한다. 성공하면 반환값은 없다. | 유지 | 권한/상태/선택 버전 검사를 순차 if로 표현했다. 거부 순서에 따라 오류 코드가 달라져 유지한다. |
| [`validate_metadata(metadata)`](../../hyundap/db/documents_repo.py#L208) | 보조/내부 · 업로드 계약의 필수 값·길이·분류·해시를 검사해 새 dict로 돌려준다. | 유지 | 필수 키를 먼저 복사한 뒤 값을 검사한다. bool을 정수로 받지 않는 경계와 원본 dict 불변을 보존한다. |
| [`create_document_version(actor, metadata, *, connection=None)`](../../hyundap/db/documents_repo.py#L247) | 주요 진입점 · 관리자 권한 확인 후 문서·후보 버전·작업을 같은 짧은 TX에서 생성한다. | 유지 | 권한 확인→문서 준비→버전/작업 저장의 짧은 메인 함수다. 세 호출이 동일 TX를 사용한다. |
| [`_check_candidate_creation(database, actor, data)`](../../hyundap/db/documents_repo.py#L263) | 보조/내부 · 권한·기존 구조·중복을 검사하고 DB에서 재검증한 현재 사용자 dict를 반환한다. | 유지 | 세션·역할·회사 잠금·고아 문서·중복 검사를 순차 처리한다. 반복 인증을 불필요한 중복으로 지우지 않는다. |
| [`_prepare_document(database, data)`](../../hyundap/db/documents_repo.py#L297) | 보조/내부 · 검증된 업로드 dict로 기존 문서를 확인하거나 새 문서를 만들고 문서 ID를 반환한다. | 유지 | 기존/신규 문서의 if/else가 업무의 두 경우를 그대로 보여준다. 버전 순번을 위한 문서 잠금 유지. |
| [`_insert_version_and_job(database, document_id, data)`](../../hyundap/db/documents_repo.py#L326) | 보조/내부 · 문서 ID와 검증된 업로드 dict로 버전·작업을 저장하고 세 가지 ID를 dict로 반환한다. | 코드 정리 | 회사 계정/공통 계정 라우팅을 if/else로 분리하고 같은 ID dict 반환은 한 곳으로 모은다. |

## errors.py

DB 호출자가 원시 SQL·접속 정보 없이 구분할 수 있는 오류 코드.

- 최상위 import: 없음
- 상수·상태: `DbError.MESSAGES`
- 클래스: DbError

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`DbError.__init__(self, code)`](../../hyundap/db/errors.py#L24) | 예외 클래스 메서드 · 정의된 code를 보관하고 사용자 데이터가 없는 안내를 예외 문자열로 설정한다. | 유지 | Exception의 최소 확장으로 code와 고정 메시지를 제공한다. 호출자 except 계약상 이 클래스는 필요하다. |

## jobs_repo.py

수집·임베딩 처리 보고서를 비밀정보 없이 단계별로 저장한다.

- 최상위 import: `json`; `math`; `re`; `dataclasses: asdict, is_dataclass`; `sqlalchemy: text`; `.errors: DbError`; `.runtime: require_id, transaction`; `.clock: epoch_now`
- 상수·상태: `STAGES`, `COUNT_FIELDS`, `INGEST_COUNT_FIELDS`
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`normalize_report(report)`](../../hyundap/db/jobs_repo.py#L20) | 보조/내부 · dict/dataclass 보고서에서 저장해도 되는 필드만 새 dict로 반환한다. | 유지 | 자료형→기본 정보→수치→실패의 함수 호출 순서가 노트북의 단계 연결과 유사하다. |
| [`_report_dict(report)`](../../hyundap/db/jobs_repo.py#L40) | 보조/내부 · dict 또는 dataclass 입력을 dict로 반환하고 나머지 자료형은 거부한다. | 유지 | dataclass를 dict로 받는 팀 계약이 필요하다. 청크 객체를 보존하는 as_mapping과는 복사 의미가 달라 합치지 않는다. |
| [`_report_identity(report)`](../../hyundap/db/jobs_repo.py#L50) | 보조/내부 · 보고서 dict에서 단계·상태·문서 식별자를 검사해 새 dict로 반환한다. | 유지 | 기본 단계 설정 후 ID별 for로 검사한다. 누락과 명시적 잘못된 값을 구분한다. |
| [`_report_metrics(report)`](../../hyundap/db/jobs_repo.py#L70) | 보조/내부 · 보고서 dict에서 개수·모델명·소요 시간을 검사해 새 dict로 반환한다. | 유지 | 필드별 숫자·모델명·시간 검사가 독립적이다. bool·NaN·무한대 차단을 유지한다. |
| [`_ingest_metrics(report, stage)`](../../hyundap/db/jobs_repo.py#L97) | 보조/내부 · 수집 전용 필드의 타입·범위를 검사한다. 경고 원문은 읽거나 저장하지 않는다. | 유지 | 비수집 단계는 조기 반환한다. 수치별 최소값과 두 필드 사이 관계를 이미 명시적 if로 표현했다. |
| [`_report_failures(report)`](../../hyundap/db/jobs_repo.py#L141) | 보조/내부 · 보고서 dict에서 안전한 오류 코드와 실패 목록만 새 dict로 반환한다. | 유지 | 실패 목록을 한 번 순회하여 안전한 필드만 담는다. 정제 단계와 저장 단계를 합치지 않는다. |
| [`_failure_detail(failure)`](../../hyundap/db/jobs_repo.py#L162) | 보조/내부 · 실패 항목 하나에서 코드·청크 ID·재시도 여부만 새 dict로 반환한다. | 코드 정리 | 중첩 get을 키 존재 if/else로 바꾸며 명시적 code=None의 거부를 보존한다. |
| [`record_stage_result(job_id, report, *, connection=None)`](../../hyundap/db/jobs_repo.py#L187) | 주요 진입점 · 작업의 단계별 결과를 같은 TX에서 기록한다. 이전 단계의 지연 기록은 거부한다. | 유지 | 같은 TX에서 조회→상태/개수 확인→병합→저장한다. 외부 입력 정제는 TX 전에 완료한다. |
| [`_read_stage_target(database, job_id)`](../../hyundap/db/jobs_repo.py#L206) | 보조/내부 · 작업 ID로 저장 대상을 조회하고 (작업 행, 문서·버전·회사 ID dict)를 반환한다. | 유지 | 참조 읽기 후 버전→작업 잠금을 유지한다. 단순 JOIN으로 바꾸면 잠금 순서 계약을 잃을 수 있다. |
| [`_check_ingest_count(database, version_id, count)`](../../hyundap/db/jobs_repo.py#L229) | 보조/내부 · 잠근 버전의 예상·실제 청크 수와 보고서를 비교한다. 데이터는 변경하지 않는다. | 유지 | FOR UPDATE로 현재 청크를 읽고 for로 센다. 단순 과거 snapshot COUNT로 바꾸지 않는다. |
| [`_check_stage_result(job, identity, clean)`](../../hyundap/db/jobs_repo.py#L246) | 보조/내부 · 정규화한 보고서가 이 작업의 대상과 단계에 맞는지 검사한다. 반환값은 없다. | 유지 | ID별 비교와 단계 순번 비교가 분리돼 있다. 이미 지난 단계의 늦은 보고를 막는다. |
| [`_merge_stage_reports(stored_report, clean)`](../../hyundap/db/jobs_repo.py#L266) | 보조/내부 · 저장된 JSON 또는 dict에 새 단계 결과를 반영해 보고서 dict를 반환한다. | 유지 | JSON/dict를 구분하고 원본을 복사한다. 성공 결과의 동일 재호출만 허용한다. |
| [`_store_stage_result(database, job_id, clean, reports)`](../../hyundap/db/jobs_repo.py#L290) | 보조/내부 · 검사가 끝난 보고서 dict와 현재 단계·안전한 오류 문구를 DB에 저장한다. 반환값은 없다. | 유지 | 안전 문구·종료 상태를 if로 결정한 뒤 저장 함수를 호출한다. |
| [`store_job_result(database, job_id, stage, report, *, status=None, error_code=None, error_message=None)`](../../hyundap/db/jobs_repo.py#L310) | 보조/내부 · 검증된 단계·보고서·종료 상태를 한 UPDATE로 저장한다. 호출자 TX에 참여한다. | 유지 | SQL COALESCE/CASE가 상태 생략과 종료 시간을 한 UPDATE에 반영한다. 여러 쓰기로 분리하지 않는다. |
| [`reset_job_for_retry(database, job_id, report)`](../../hyundap/db/jobs_repo.py#L330) | 보조/내부 · 검증된 작업을 같은 TX에서 재시도 상태로 저장한다. 반환값·직접 커밋은 없다. | 유지 | 단일 UPDATE로 횟수 증가와 상태 초기화를 묶어 기존 보고서 이력과 공동 롤백을 지킨다. |

## runtime.py

일반 함수형 저장 코드가 공유하는 연결·트랜잭션·입력 검증 도구.

- 최상위 import: `re`; `os`; `contextlib: contextmanager`; `functools: lru_cache`; `sqlalchemy.exc: IntegrityError, SQLAlchemyError`; `.connection: create_db_engine`; `.errors: DbError`
- 상수·상태: 없음
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`split_layout()`](../../hyundap/db/runtime.py#L13) | 보조/내부 · 시험용 분리 모드는 명시적으로 켠다. 기존 v4 DB를 자동 변환하지 않는다. | 유지 | 설정 로드 후 single/split 두 값만 검사한다. 모드 오타를 자동 전환하지 않는다. |
| [`company_accounts_enabled()`](../../hyundap/db/runtime.py#L23) | 보조/내부 · 회사별 계정 모드 설정을 검증한다. 잘못된 설정은 공유 계정으로 대체하지 않는다. | 유지 | 0/1과 split 전제 조건을 별도로 검사해 잘못된 모드의 공유 계정 대체를 막는다. |
| [`isolated_company_connection(connection)`](../../hyundap/db/runtime.py#L33) | 보조/내부 · 명시적으로 활성화한 회사 계정 연결인지 확인한다. | 유지 | 연결 없음/모드 꺼짐을 먼저 반환하고 DB 주소를 비교한다. 짧은 조건이므로 유지한다. |
| [`shared_table(name, *, connection=None)`](../../hyundap/db/runtime.py#L40) | 보조/내부 · 서버가 정한 공통 테이블만 식별자로 만든다. 사용자 입력 SQL은 받지 않는다. | 유지 | 고정 허용 테이블과 회사 전용 VIEW 선택을 담당한다. 일반 문자열 입력을 SQL에 붙이는 도구로 확대하지 않는다. |
| [`get_engine()`](../../hyundap/db/runtime.py#L53) | 보조/내부 · 프로세스에서 연결 풀 하나를 재사용한다. 환경 변경 후에는 캐시를 비워야 한다. | 설명 보강 | lru_cache가 함수 결과를 재사용하는 이유와 환경변수 변경 시 초기화 필요를 설명한다. |
| [`transaction(connection=None, *, company_id=None, document_id=None, version_id=None, job_id=None, control=False)`](../../hyundap/db/runtime.py#L84) | 보조/내부 · 연결이 없으면 짧은 트랜잭션을 열고, 있으면 호출자의 트랜잭션에 참여한다. | 설명 보강 | yield 전/후의 흐름과 외부 연결 소유권을 설명한다. 예외 변환과 라우팅 검사를 유지한다. |
| [`require_id(value)`](../../hyundap/db/runtime.py#L134) | 보조/내부 · 기존 문자열 ID를 보존하며 비어 있거나 36자를 넘는 ID를 거부한다. | 유지 | 검사에 strip을 써도 반환 문자열은 바꾸지 않는다. UUID만 받도록 강화하면 기존 계약이 달라진다. |
| [`require_hash(value)`](../../hyundap/db/runtime.py#L146) | 보조/내부 · 소문자 SHA-256 hex 문자열을 확인하고 그대로 반환한다. | 유지 | 정규식으로 소문자 64자리만 받는 짧은 입력 경계다. |

## schema.py

최종 v4 직접 생성과 관리용 실측 검증. 기존 DB는 자동 변경하지 않는다.

- 최상위 import: `argparse`; `hashlib`; `json`; `re`; `pathlib: Path`; `sqlalchemy: inspect, text`; `sqlalchemy.engine: Engine`; `.connection: create_db_engine`
- 상수·상태: `DESIGN_DIR`
- 클래스: 없음 — 일반 함수 구조

직접 실행 블록: `__name__ == "__main__"`이면 `main()`의 종료 코드를 `SystemExit`으로 반환한다.

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`_read_layout(connection, *, exclude_company_view=False)`](../../hyundap/db/schema.py#L17) | 보조/내부 · 버전 기록 전에도 컬럼·인덱스·FK·CHECK를 읽는다. 업무 행은 읽지 않는다. | 유지 | 컬럼→테이블 키→CHECK 적용 상태의 세 반복이 서로 다른 정보를 모은다. 중첩 탐색의 범위도 주석에 있다. |
| [`snapshot(connection)`](../../hyundap/db/schema.py#L67) | 주요 진입점 · 관리 연결의 전체 구조와 단일 버전 행을 반환한다. 조회 오류는 전달한다. | 유지 | 구조 읽기와 버전 행 정확히 1개 검사가 명확하다. 관리 점검을 캐시로 바꾸지 않는다. |
| [`_ascii_literal(match)`](../../hyundap/db/schema.py#L77) | 보조/내부 · re.sub가 찾은 문자셋 표기에서 ASCII 문자열의 접두사만 제거한다. | 유지 | re.sub 콜백이 ASCII 접두사만 제거한다. 한글 값의 문자셋 의미를 유지해야 한다. |
| [`_expression(value)`](../../hyundap/db/schema.py#L89) | 보조/내부 · MySQL 메타데이터 표현식의 공백·식별자 인용 표기만 정규화한다. | 유지 | 문자열 상수와 SQL 토큰을 for/if로 구분한다. 값의 대소문자를 모두 낮추면 계약 검사가 틀린다. |
| [`_structure(layout)`](../../hyundap/db/schema.py#L106) | 보조/내부 · 컬럼 속성과 모든 제약을 순서 독립 dict로 만들어 구조를 비교한다. | 유지 | 테이블별 컬럼·UNIQUE·인덱스·FK·CHECK를 독립 for로 정규화한다. 단일 복잡한 내포식으로 합치지 않는다. |
| [`validate_v4(layout)`](../../hyundap/db/schema.py#L151) | 주요 진입점 · 확정 계약과 모든 테이블·컬럼 속성·제약을 비교해 구조 오류 목록을 반환한다. | 유지 | 테이블 합집합과 속성별 비교가 누락·추가·차이를 모두 잡는다. 버전 숫자만 비교할 수 없다. |
| [`create_v4_schema(engine)`](../../hyundap/db/schema.py#L175) | 주요 진입점 · 명시적으로 받은 Engine의 빈 MySQL DB에 최종 v4를 직접 생성한다. | 유지 | 빈 DB 확인·서버 잠금·SQL 실행·실측 검증·버전 기록 순서가 필요하다. 자동 DROP/이행을 추가하지 않는다. |
| [`audit_counts(connection)`](../../hyundap/db/schema.py#L229) | 주요 진입점 · 읽기 전용 정합성 위반 건수를 반환한다. 외부 벡터 검사는 포함하지 않는다. | 유지 | 검증 SQL 한 문장당 결과 dict 하나를 모으는 명시적 for다. |
| [`main()`](../../hyundap/db/schema.py#L243) | 주요 진입점 · 명시적 --create 또는 읽기 전용 --inspect/--check를 실행한다. 비밀값은 출력하지 않는다. | 유지 | create/inspect/check 명령과 실패 반환 코드가 직접 표현돼 있다. |

## sessions_repo.py

별도 인증 담당자가 발급한 세션을 조회해 DB 접근 권한을 재검증한다.

- 최상위 import: `hashlib`; `sqlalchemy: text`; `hyundap.auth.errors: AccessError`; `.clock: epoch_now`; `.runtime: shared_table`
- 상수·상태: `ROLE_MAP`
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`token_hash(token)`](../../hyundap/db/sessions_repo.py#L23) | 보조/내부 · 32바이트 이상 토큰의 문자열 형태를 검사하고 DB 비교용 SHA-256을 반환한다. | 유지 | 세션 토큰의 형식과 SHA-256 비교용 해시만 처리한다. 비밀번호 해시 함수와 혼동하지 않는다. |
| [`current_actor(actor, *, connection, lock=False)`](../../hyundap/db/sessions_repo.py#L31) | 주요 진입점 · 서버 actor dict와 DB 연결을 받아 현재 권한을 담은 새 actor dict를 반환한다. | 유지 | 회사 계정이면 프로시저, 나머지는 세션→사용자→회사 검사로 연결된다. 결과는 현재 DB 값으로 만든다. |
| [`_session_credentials(actor)`](../../hyundap/db/sessions_repo.py#L63) | 보조/내부 · 서버 actor dict의 입력을 검사하고 (세션 ID, 비교용 토큰 해시)를 반환한다. | 유지 | actor 자료형→토큰→세션 ID 순서가 명확하고 권한 정보를 신뢰하지 않는다. |
| [`_read_valid_session(connection, session_id, digest, lock)`](../../hyundap/db/sessions_repo.py#L75) | 보조/내부 · ID·토큰 해시가 일치하고 만료·폐기되지 않은 세션 DB 행을 반환한다. | 유지 | 잠금 접미사를 명시적 if로 정하고 만료·폐기·해시를 한 SELECT로 확인한다. |
| [`_read_active_user(connection, session, lock)`](../../hyundap/db/sessions_repo.py#L95) | 보조/내부 · 세션 DB 행에 연결된 활성 사용자를 반환하고 권한 버전 변경을 거부한다. | 유지 | 활성 여부와 auth_version 비교가 독립 if다. 무효 세션을 자동 갱신하지 않는다. |
| [`_check_company_role(connection, user, lock)`](../../hyundap/db/sessions_repo.py#L111) | 보조/내부 · 사용자 DB 행의 역할·소속 회사 유효성을 검사하고 표준 역할명을 반환한다. | 유지 | 허용 역할과 소속 회사 활성 조건이 명확하다. 플랫폼 관리자도 소속이 있으면 검사한다. |

## split_schema.py

빈 DB 전용 공통/회사 분리 스키마. 기존 v4 SQL을 분리해 제약을 재사용한다.

- 최상위 import: `json`; `re`; `hashlib`; `sqlalchemy: text`; `.schema: DESIGN_DIR, _read_layout, _structure, _expression`; `.tenancy: quoted_database`
- 상수·상태: `BUSINESS_TABLES`, `CONTROL_TABLES`, `REGISTRY_SQL`, `ROUTES_SQL`, `IDENTITY_SQL`
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`_extra_contract(name)`](../../hyundap/db/split_schema.py#L48) | 보조/내부 · 신규 등록부와 소속 표의 고정 계약이다. 실측 결과로 기대값을 만들지 않는다. | 유지 | 등록부/회사 소속 표의 고정 기대값을 dict로 선언한다. 실제 DB를 읽어 기대값으로 삼으면 검증이 무의미하다. |
| [`_statements(kind, control_name=None)`](../../hyundap/db/split_schema.py#L81) | 보조/내부 · 고정 v4 SQL에서 해당 역할의 테이블만 선택한다. 관리 DB 참조만 명시적으로 바꾼다. | 유지 | 주석 제거→허용 테이블 선택→공통 FK 참조 변경이 순서대로 드러난다. 고정 SQL만 처리한다. |
| [`_base_errors(connection, role, company_id=None, control_name=None, *, layout=None)`](../../hyundap/db/split_schema.py#L117) | 보조/내부 · 기본 계약을 비교하고 (오류, 실측 layout, 비교용 구조)를 반환한다. | 유지 | 한 실측 구조로 기본 계약과 회사 CHECK/FK를 검사한다. 비교용 dict 변경과 원본 보존을 구분한다. |
| [`validate_control(connection, *, layout=None)`](../../hyundap/db/split_schema.py#L170) | 주요 진입점 · 공통 DB의 인증/등록 구조를 검사한다. 같은 점검의 실측 layout만 재사용한다. | 유지 | 기본 구조와 두 등록 테이블을 검사한다. 안쪽 FK 반복은 현재 등록 테이블에만 적용된다. |
| [`validate_company(connection, control_name, company_id)`](../../hyundap/db/split_schema.py#L185) | 주요 진입점 · 회사 DB의 인증 테이블 부재·소속·공통 FK·원장 제약을 검사한다. | 유지 | 테이블 집합→소속 구조→회사 값 순서로 검사한다. 회사 ID만 같다고 구조 검사를 생략하지 않는다. |
| [`create_split_schema(engine, *, control_name=None, company_id=None)`](../../hyundap/db/split_schema.py#L197) | 주요 진입점 · 명시한 빈 DB만 생성한다. 실패 후 자동 DROP/재실행은 하지 않는다. | 유지 | 서버/빈 DB 확인 후 역할별 DDL·회사 CHECK·v4 기록·검증을 수행한다. 실패의 부분 DDL은 보존한다. |

## tenancy.py

동일 MySQL 서버의 공통 인증 DB와 회사 업무 DB를 선택하는 시험용 모듈.

- 최상위 import: `contextlib: contextmanager`; `contextvars: ContextVar`; `re`; `threading: RLock`; `sqlalchemy: text`; `.connection: create_db_engine`; `.errors: DbError`; `.: runtime`
- 상수·상태: `_company`, `_engines`, `_engine_lock`, `_workflow_locks`, `_publication_locks`
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`quoted_database(name)`](../../hyundap/db/tenancy.py#L27) | 보조/내부 · DB 이름은 서버 관리 식별자만 허용하고 SQL 식별자로 인용한다. | 유지 | 관리 식별자를 검증한 뒤 백틱으로 감싼다. SQL 값 바인딩과 식별자 인용의 역할이 다르다. |
| [`company_for_reference(*, document_id=None, version_id=None, job_id=None, connection=None)`](../../hyundap/db/tenancy.py#L34) | 보조/내부 · 내용 없이 저장한 전역 식별자 목록에서 대상 회사를 찾는다. | 유지 | 주어진 식별자를 for로 AND 조건에 모으고 유일한 회사만 반환한다. 입력 ID를 SQL에 직접 넣지 않는다. |
| [`get_company_engine(company_id)`](../../hyundap/db/tenancy.py#L59) | 보조/내부 · 활성 등록만 연결한다. 최대 32개 회사 풀을 유지하고 초과 시 명시적으로 거부한다. | 코드 정리 | 캐시 키가 있으면 조기 반환하여 새 Engine 경로를 한 단계 펼친다. 상태 확인·32개 상한·실패 폐기를 유지한다. |
| [`routed_engine(*, company_id=None, document_id=None, version_id=None, job_id=None, connection=None)`](../../hyundap/db/tenancy.py#L114) | 보조/내부 · 회사 또는 업무 ID로 회사 Engine을 선택한다. 대상을 받지 않으면 None이다. | 유지 | 실제 소유자→현재 요청 회사→선택 결과의 세 단계가 이미 읽기 쉽다. |
| [`company_scope(company_id)`](../../hyundap/db/tenancy.py#L141) | 보조/내부 · with 본문이 처리할 회사를 고정하고 종료 시 이전 회사 문맥으로 돌아간다. | 설명 보강 | ContextVar의 token은 이전 문맥 복원 표식이며 yield가 with 본문으로 이동하는 지점임을 설명한다. |
| [`processing_lock(fallback, *, publication=False)`](../../hyundap/db/tenancy.py#L164) | 보조/내부 · 분리 모드는 회사별 Python 잠금, 기존 모드는 기존 전역 잠금을 사용한다. | 설명 보강 | 회사별 업로드/게시 잠금 사전의 선택과 생성 보호를 설명한다. SQL 잠금이나 다중 프로세스 잠금으로 오해하지 않는다. |
| [`close_company_engines()`](../../hyundap/db/tenancy.py#L189) | 보조/내부 · 앱 종료/시험 정리 시 회사 연결 풀을 닫는다. 처리 중에는 호출하지 않는다. | 유지 | 회사별 풀을 for로 닫고 사전을 비운다. 실행 중 요청에 호출하지 않는 수명 계약 유지. |
| [`authorize_company(actor, company_id=None, *, manager=False)`](../../hyundap/db/tenancy.py#L199) | 보조/내부 · 회사 DB를 열기 전에 공통 DB의 현재 세션과 역할·허용 회사를 확인한다. | 유지 | 공통 DB에서 현재 세션·관리자·회사 범위를 검사하는 짧은 사전 권한 경계다. |

## upload_service.py

원본 저장→수집→청크 원장→임베딩→검증→게시를 연결하는 DB 담당 흐름.

- 최상위 import: `hashlib`; `inspect`; `json`; `os`; `re`; `uuid: uuid4`; `pathlib: Path`; `threading: RLock`; `sqlalchemy: text`; `hyundap.common.config: get_settings`; `.: chunks_repo, documents_repo, jobs_repo, versions_repo, version_service`; `.errors: DbError`; `.runtime: require_id, transaction, shared_table, split_layout`; `.tenancy: company_scope, company_for_reference, processing_lock, authorize_company`
- 상수·상태: `WORKFLOW_LOCK`
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`get_ingest_function(function=None)`](../../hyundap/db/upload_service.py#L29) | 보조/내부 · 주입한 함수 또는 팀 수집 진입점을 선택한다. 미구현 뼈대는 안전하게 거부한다. | 유지 | 주입 함수 또는 팀 기본 함수 선택 후 callable 검사라 학습한 함수 전달과 연결된다. |
| [`file_digest(path)`](../../hyundap/db/upload_service.py#L40) | 보조/내부 · 실제 파일을 1MiB씩 읽어 SHA-256과 크기를 구한다. 비어 있거나 상한을 넘으면 거부한다. | 유지 | 1MiB씩 읽는 while로 메모리 상한을 유지한다. 전체 read로 단축하면 큰 파일에서 부담이 커진다. |
| [`source_destination(root, company_id, document_id, version_id, suffix)`](../../hyundap/db/upload_service.py#L61) | 보조/내부 · 안전한 식별자로 순수 경로를 계산한다. 실제 링크·루트 검사는 TX 밖에서 수행한다. | 유지 | 각 ID 검증 후 Path로 회사/문서/버전 경로를 만든다. 실제 파일 작업과 분리한다. |
| [`copy_original(source, destination, expected_hash, expected_size)`](../../hyundap/db/upload_service.py#L78) | 보조/내부 · 기존 파일을 덮지 않고 복사하면서 다시 해시를 검사한다. 복사 중 원본 변경도 검출한다. | 유지 | 복사하면서 해시·크기를 검증하고 hard link로 기존 원본 덮어쓰기를 막는다. 쉬운 단순 복사로 대체하지 않는다. |
| [`failure_code(error)`](../../hyundap/db/upload_service.py#L109) | 보조/내부 · 예외에서 안전한 코드만 가져온다. 원시 메시지·URL·접속 비밀값은 저장하지 않는다. | 유지 | 문자열/예외의 code를 선택한 뒤 허용 형식만 반환한다. 원시 예외 메시지는 보존 대상이 아니다. |
| [`record_upload_failure(ids, stage, error, backend)`](../../hyundap/db/upload_service.py#L121) | 보조/내부 · 게시 미완료는 복구에 맡기고, 비활성 처리 후보만 실패 기록·벡터 정리한다. | 유지 | 게시 복구가 필요한 경우를 먼저 반환한다. 실패 기록·외부 정리·정리 실패 기록의 순서를 보존한다. |
| [`resolve_stored_source(source, root)`](../../hyundap/db/upload_service.py#L154) | 보조/내부 · DB의 원본 경로와 저장 루트가 절대 경로인지 검사하고 루트 내부 Path를 반환한다. | 유지 | Path 해석 뒤 루트 내부인지 확인한다. 절대 경로만으로 링크 탈출을 방지할 수 없어 resolve가 필요하다. |
| [`read_attempt_source(ids)`](../../hyundap/db/upload_service.py#L173) | 보조/내부 · 문서·버전 ID로 저장 원본을 확인하고 (파일 경로, 수집 정보, 검증한 해시)를 반환한다. | 유지 | DB 읽기를 끝낸 뒤 파일 해시·크기를 검사한다. 파일 I/O를 TX 안으로 옮기지 않는다. |
| [`save_ingest_chunks(ids, company_id, result)`](../../hyundap/db/upload_service.py#L209) | 보조/내부 · 성공한 수집 결과의 청크를 검사·기록하고 원래 청크 목록을 반환한다. | 유지 | 청크별 소속·본문 검사 후 원장 저장, 별도 경고 반복은 문자열 검증이다. 원본 청크 객체는 반환에 유지한다. |
| [`verify_uploaded_chunks(ids, backend)`](../../hyundap/db/upload_service.py#L258) | 보조/내부 · DB 청크 목록과 벡터 저장 결과를 대조하고 성공 보고서를 남긴다. 반환값은 없다. | 유지 | 완전성 검사 결과를 확인한 뒤 성공 보고서를 저장하는 짧은 연결 함수다. |
| [`ingest_and_save_chunks(source, info, ids, ingest_function, file_hash=None)`](../../hyundap/db/upload_service.py#L270) | 보조/내부 · 원본 Path와 수집 정보로 수집 단계를 실행하고 (청크 목록, 오류 코드)를 반환한다. | 유지 | 회사에 묶인 콜백과 선택적 file_hash 지원을 유지한다. inspect는 기존 수집기 인자 호환 때문에 필요하다. |
| [`ingest_and_save_chunks.find_by_hash(company_id, sha256)`](../../hyundap/db/upload_service.py#L277) | 중첩 보조 함수 · 서버의 현재 후보 문맥에만 묶인 조회다. 임의 회사·해시 제외를 허용하지 않는다. | 유지 | 부모 함수의 회사·후보 ID를 기억하는 콜백이다. 임의 회사 접근이나 자기 자신 중복 판정을 막는다. |
| [`embed_and_record_result(chunks, job_id, backend)`](../../hyundap/db/upload_service.py#L300) | 보조/내부 · 청크 목록을 임베딩하고 단계 보고서를 저장한다. 성공은 None, 실패는 오류 코드다. | 유지 | 외부 임베딩→보고서 저장→오류 코드 반환으로 순서가 명확하다. 부분 실패도 보고서를 남긴다. |
| [`run_attempt(actor, ids, expected_active_id, ingest_function, backend)`](../../hyundap/db/upload_service.py#L316) | 보조/내부 · 원본 확인 → 수집 → 임베딩 → 검증 → 게시 순서로 실행하고 결과 dict를 반환한다. | 유지 | 학습한 순차 체인처럼 원본→수집→임베딩→검증→게시가 드러난다. 각 stage의 실패 기록은 유지한다. |
| [`prepare_upload_source(source_path, metadata, upload_dir)`](../../hyundap/db/upload_service.py#L355) | 보조/내부 · 서버 파일을 읽어 (원본 Path, 저장 루트 Path, 저장 정보 dict)를 준비한다. | 유지 | 파일 검사 후 metadata 복사와 서버 계산 필드를 명시적으로 채운다. 사용자 해시를 신뢰하지 않는다. |
| [`create_upload_candidate(actor, company_id, values, source, root)`](../../hyundap/db/upload_service.py#L384) | 보조/내부 · 후보와 최종 저장 경로를 한 번에 저장하고 (ID dict, 이전 활성 ID, 목적지 Path)를 반환한다. | 유지 | 같은 TX의 후보와 원본 경로 저장 후 반환한다. 실제 파일 복사는 밖에서 수행한다. |
| [`upload_document(actor, source_path, metadata, *, ingest_document=None, embedding=None, upload_dir=None)`](../../hyundap/db/upload_service.py#L415) | 주요 진입점 · 회사별 DB 문맥에서 기존 원본→게시 처리를 실행한다. 인증은 공통 DB에서 재검증한다. | 유지 | 입력 검사→공통 권한→회사 문맥→내부 흐름으로 역할이 명확하다. |
| [`_upload_document(actor, source_path, metadata, *, company_id, ingest_document=None, embedding=None, upload_dir=None)`](../../hyundap/db/upload_service.py#L426) | 보조/내부 · 관리자 업로드의 원본 저장부터 게시까지 연결하고 결과 ID·상태를 반환한다. | 유지 | 잠금 안에서 준비·후보·원본·실행을 연결한다. 후보 이후 실패는 실패 원장으로 기록한다. |
| [`check_retry_allowed(document, version, reports)`](../../hyundap/db/upload_service.py#L454) | 보조/내부 · 문서·버전·보고서 dict가 재시도 가능한 상태인지 확인한다. 반환값은 없다. | 유지 | 재시도 금지 조건을 각각 if로 보여준다. 활성 버전이나 미완료 게시를 재시도하지 않는다. |
| [`prepare_retry(actor, version_id)`](../../hyundap/db/upload_service.py#L470) | 보조/내부 · 권한·후보를 잠가 재시도를 준비하고 ID·이전 활성 ID·원본 정보를 dict로 반환한다. | 유지 | 잠금 조회→허용 검사→이력 구성→상태 초기화를 같은 TX에 둔다. 파일 검사는 이후 단계다. |
| [`read_retry_records(database, actor, version_id)`](../../hyundap/db/upload_service.py#L500) | 보조/내부 · 관리자 권한으로 재시도 대상의 (문서, 버전, 작업, 보고서) dict들을 읽는다. | 유지 | 관리자→문서/버전→작업 잠금 순서를 명확히 유지한다. |
| [`build_retry_report(root, reports)`](../../hyundap/db/upload_service.py#L520) | 보조/내부 · 저장 루트 Path와 기존 보고서 dict로 이전 시도를 보존한 새 보고서 dict를 만든다. | 유지 | 이전 시도 목록을 보존하고 현재 보고서만 새 dict로 모은다. 중첩 이력 복제를 막는다. |
| [`reset_retry_records(database, version_id, job_id, report)`](../../hyundap/db/upload_service.py#L537) | 보조/내부 · 같은 DB 연결에서 버전·작업을 재시도 시작 상태로 저장한다. 반환값은 없다. | 유지 | 버전 UPDATE와 작업 초기화가 같은 연결을 쓰므로 한쪽 실패 시 함께 롤백한다. |
| [`retry_upload(actor, version_id, *, ingest_document=None, embedding=None, source_path=None)`](../../hyundap/db/upload_service.py#L551) | 주요 진입점 · 전역 식별자로 회사 DB를 선택한 뒤 기존 후보를 재시도한다. | 유지 | ID 검사→관리자 확인→회사 조회→회사 문맥→재시도 호출의 진입 경계다. |
| [`_retry_upload(actor, version_id, *, ingest_document=None, embedding=None, source_path=None)`](../../hyundap/db/upload_service.py#L564) | 보조/내부 · 재시도 준비 → 경로 확인 → 이전 벡터 정리 → 처리 순서로 실행하고 결과 dict를 반환한다. | 유지 | 원본 검증·기존 벡터 정리·선택적 원본 재공급·재실행을 순서대로 수행한다. 기존 원본은 덮지 않는다. |

## version_service.py

DB·벡터 적재 완전성, 조건부 게시, 부분 활성화 실패의 복구를 연결한다.

- 최상위 import: `hashlib`; `json`; `dataclasses: fields, is_dataclass`; `threading: RLock`; `sqlalchemy: text`; `hyundap.auth.errors: AccessError`; `.errors: DbError`; `.runtime: require_id, transaction, shared_table, split_layout, isolated_company_connection`; `.tenancy: company_scope, company_for_reference, processing_lock, authorize_company`; `.sessions_repo: current_actor`; `.: jobs_repo`
- 상수·상태: `PUBLICATION_LOCK`, `PENDING_PHASES`
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`get_embedding_backend(embedding=None)`](../../hyundap/db/version_service.py#L27) | 보조/내부 · 벡터 처리 함수 5개를 이름으로 찾을 수 있는 dict로 반환한다. | 유지 | 기본 모듈/dict/객체 입력을 함수 dict로 맞춘다. 복사 반복과 callable 검사 반복은 대상/목적이 다르다. |
| [`as_mapping(value)`](../../hyundap/db/version_service.py#L65) | 보조/내부 · 외부 결과를 dict로 읽는다. dataclass는 바깥 필드만 옮기고 내부 객체는 보존한다. | 유지 | dataclass의 바깥 필드만 for로 복사해 내부 청크 정체성을 유지한다. asdict로 통합하지 않는다. |
| [`call_external(function, *args, **kwargs)`](../../hyundap/db/version_service.py#L86) | 보조/내부 · 팀 외부 함수의 예외 원문을 안전한 처리 실패로 변환한다. 비밀값은 전달하지 않는다. | 유지 | args/kwargs를 그대로 전달하는 공통 경계다. 안전 오류는 보존하고 외부 예외 원문을 숨긴다. |
| [`read_candidate(database, document_id, version_id, *, lock=False)`](../../hyundap/db/version_service.py#L98) | 보조/내부 · 문서와 버전을 일치시켜 읽는다. 잠금이 필요하면 문서→버전 순서를 따른다. | 유지 | 문서→버전 순서로 조회·잠그고 소속을 검사한다. 존재만 확인하는 단순 조회로 줄이지 않는다. |
| [`manifest_snapshot(database, version)`](../../hyundap/db/version_service.py#L118) | 보조/내부 · 원장과 후보 메타데이터의 해시를 만든다. 외부 검사 이후 변경 여부를 다시 비교한다. | 유지 | 원장 목록과 버전 필드 dict를 두 for로 만든다. seal은 외부 검사 중 DB 변경을 잡는다. |
| [`validate_completeness(document_id, version_id, *, embedding=None)`](../../hyundap/db/version_service.py#L140) | 주요 진입점 · DB 기준 ID·순번·본문 해시와 실제 벡터의 ID·본문·회사/문서/버전을 대조한다. | 유지 | DB snapshot을 읽고 TX 종료 후 외부 벡터를 비교한다. 보고된 개수만 믿지 않는다. |
| [`chunk_matches_manifest(hit, expected, seen, identity)`](../../hyundap/db/version_service.py#L162) | 보조/내부 · 벡터 조회 결과 하나가 DB 원장의 식별자·본문과 같은지 bool로 반환한다. | 유지 | ID·중복·자료형·소속·순번·본문 해시를 단계별로 검사하고 bool을 반환한다. |
| [`compare_stored_chunks(identity, version, manifest, seal, ids, hits)`](../../hyundap/db/version_service.py#L193) | 보조/내부 · DB 원장과 외부 청크 목록을 비교해 완전성 결과 dict를 만든다. | 유지 | 개수/순번/ID/본문/버전 상태를 이름 있는 변수로 이미 분리했다. |
| [`collect_chunk_comparison(identity, manifest, ids, hits)`](../../hyundap/db/version_service.py#L217) | 보조/내부 · 원장·실제 청크를 대조해 개수 판정에 필요한 자료를 dict로 반환한다. | 유지 | ID 검증과 원장 색인을 각각 for로 만들고 집합 차이로 누락/초과를 계산한다. 추가 전체 탐색이 불필요하다. |
| [`inspect_chunk_contents(identity, expected, hits)`](../../hyundap/db/version_service.py#L250) | 보조/내부 · 실제 청크의 내용·소속·중복을 검사하고 (확인한 ID 집합, 잘못된 ID 목록)을 반환한다. | 유지 | seen 집합으로 중복을 추적하고 잘못된 ID 목록을 모은다. 중복도 seen에 남겨 다음 검사를 유지한다. |
| [`read_job(database, version_id, *, lock=False)`](../../hyundap/db/version_service.py#L276) | 보조/내부 · 버전당 하나인 작업과 안전한 JSON 기록을 읽는다. 중복 작업은 자동 선택하지 않는다. | 유지 | 한 작업만 허용하고 JSON/dict/빈 결과를 명시적 분기로 읽는다. |
| [`write_publication(database, job, reports, publication, *, complete=False, error_code=None)`](../../hyundap/db/version_service.py#L299) | 보조/내부 · 복구에 필요한 이전 포인터·공개 상태·단계를 작업 JSON에 원자적으로 저장한다. | 유지 | phase/complete에 따른 작업 상태를 if/elif로 정한다. 안전 오류 문구와 복구 기록을 함께 쓴다. |
| [`ensure_no_pending_publication(database, document_id, *, except_job=None)`](../../hyundap/db/version_service.py#L321) | 보조/내부 · 같은 문서에 미복구 외부 전환이 있으면 새 게시를 막는다. 호출자는 문서 행을 잠근다. | 유지 | 미완료 작업 한 건의 존재만 확인한다. 전체 목록 반복으로 되돌릴 이유가 없다. |
| [`company_is_active(database, company_id, *, lock=False)`](../../hyundap/db/version_service.py#L339) | 보조/내부 · 대상 회사의 활성 값을 반환한다. 조회 대상이 없으면 None이다. | 코드 정리 | 잠금 접미사를 명시적 if로 만들고 회사 계정은 소유 확인 후 프로시저를 호출하는 이유를 설명한다. |
| [`require_manager(actor, database, company_id)`](../../hyundap/db/version_service.py#L362) | 보조/내부 · 현재 세션·관리자 역할·회사 권한을 확인한다. 입력 문맥의 역할은 신뢰하지 않는다. | 코드 정리 | 복합 조건을 역할 검사와 회사 검사로 나눈다. 동일 오류와 잠금 순서를 유지한다. |
| [`publish_version(actor, document_id, candidate_id, expected_active_id, *, embedding=None)`](../../hyundap/db/version_service.py#L379) | 주요 진입점 · 회사 범위를 고정한 뒤 검증·CAS·벡터 공개·복구의 기존 순서를 실행한다. | 유지 | 공통 권한과 회사 문맥을 확정한 뒤 내부 게시를 호출한다. 공개 경계로서 유지한다. |
| [`_publish_version(actor, document_id, candidate_id, expected_active_id, *, embedding=None)`](../../hyundap/db/version_service.py#L390) | 보조/내부 · 완전한 운영 후보만 조건부 게시하고 커밋 후 벡터를 활성화한다. | 유지 | 권한→벡터 검사→DB 전환→외부 활성화→완료의 순서가 명시돼 있다. |
| [`check_publication_access(actor, document_id, candidate_id)`](../../hyundap/db/version_service.py#L420) | 보조/내부 · 외부 벡터를 읽기 전에 현재 관리자 권한을 확인하고 문서의 회사 ID를 반환한다. | 유지 | 벡터 조회 전에 세션·문서·회사 권한을 검증한다. 이후 재검증과 시간 목적이 다르다. |
| [`read_publishable_candidate(database, actor, document_id, candidate_id, company_id, seal)`](../../hyundap/db/version_service.py#L436) | 보조/내부 · 잠금 안에서 권한·상태·원장 변경을 검사하고 (문서 dict, 버전 dict)를 반환한다. | 유지 | 외부 작업 뒤 잠금 안에서 권한·상태·seal을 재검사한다. 앞선 검사와 중복이라고 지우지 않는다. |
| [`prepare_publication(actor, document_id, candidate_id, company_id, expected_active_id, seal)`](../../hyundap/db/version_service.py#L466) | 보조/내부 · DB 활성 버전과 복구 기록을 함께 저장한다. 벡터 활성화가 필요하면 True다. | 유지 | 멱등 성공은 조기 반환하며 충돌 검사 후 대기 기록을 같은 TX에 쓴다. |
| [`check_publication_conflict(database, document, version, expected_active_id)`](../../hyundap/db/version_service.py#L497) | 보조/내부 · 잠근 문서·후보 버전이 다른 게시와 경쟁하지 않는지 검사한다. 반환값은 없다. | 유지 | 미복구 작업→기대 포인터→버전 번호를 순차 검사한다. |
| [`store_pending_publication(*, database, document, candidate_id, expected_active_id, job, reports)`](../../hyundap/db/version_service.py#L515) | 보조/내부 · 검사된 후보를 DB 활성 버전으로 바꾸고 벡터 활성화 대기 기록을 함께 저장한다. | 유지 | 조건부 UPDATE 결과 행 수로 경쟁을 감지한다. 포인터와 복구 기록을 한 TX에 보관한다. |
| [`activate_candidate(backend, document_id, candidate_id, expected_count)`](../../hyundap/db/version_service.py#L536) | 보조/내부 · DB 커밋 후 후보 벡터를 활성화한다. 실패하면 복구를 시도하고 안전한 오류를 낸다. | 유지 | 활성화 개수 확인과 실패 복구의 책임이 하나의 짧은 함수에 있다. |
| [`finish_publication(document_id, candidate_id)`](../../hyundap/db/version_service.py#L551) | 보조/내부 · 벡터 활성화 후 DB 상태가 그대로인지 확인하고 게시 완료를 기록한다. 반환값은 없다. | 유지 | 외부 활성화 뒤 현재 상태를 다시 확인하고 완료를 기록한다. 재확인은 경쟁 상태 방지용이다. |
| [`recover_publication(document_id, version_id, *, embedding=None)`](../../hyundap/db/version_service.py#L571) | 주요 진입점 · 서버 내부 복구 요청을 해당 회사 DB로만 라우팅한다. | 유지 | 식별자로 회사 문맥을 고정하는 서버 내부 진입점이다. |
| [`_recover_publication(document_id, version_id, *, embedding=None)`](../../hyundap/db/version_service.py#L580) | 보조/내부 · 서버 운영자가 미완료 게시를 이전 상태로 복구한다. 사용자 입력으로 직접 노출하지 않는다. | 유지 | DB 복구 준비→외부 벡터 복구→완료 기록을 순차 연결한다. |
| [`check_previous_version(database, document, document_id, previous_id)`](../../hyundap/db/version_service.py#L601) | 보조/내부 · 이전 버전을 다시 공개할 수 있는지와 예상 청크 수를 (bool, 개수)로 반환한다. | 유지 | 이전 버전의 현재 공개 자격과 개수를 반환한다. 이전 버전이 없는 첫 게시도 처리한다. |
| [`prepare_recovery(document_id, version_id)`](../../hyundap/db/version_service.py#L622) | 보조/내부 · DB 포인터를 조건부로 되돌리고 외부 복구에 필요한 값들을 dict로 반환한다. | 유지 | 복구 불필요/최신 포인터 보호/복구 대기의 분기가 직접 보인다. None 복구 대상도 기록한다. |
| [`restore_database_pointer(*, database, document, version_id, publication, eligible)`](../../hyundap/db/version_service.py#L659) | 보조/내부 · 현재 포인터가 실패 후보일 때만 되돌리고 복구 후 활성 버전 ID를 반환한다. | 유지 | 현재 포인터가 후보일 때만 이전 값/차단 상태로 변경한다. 안전 조건을 단순 대입으로 대체하지 않는다. |
| [`restore_vectors(backend, document_id, version_id, recovery)`](../../hyundap/db/version_service.py#L683) | 보조/내부 · SQL 잠금 없이 이전 벡터 복원과 후보 삭제를 수행한다. 모두 끝나면 True다. | 유지 | 이전 벡터 검증·활성화 후 후보 벡터를 삭제한다. 실패는 False로 돌려 대기 기록을 유지한다. |
| [`finish_recovery(document_id, version_id)`](../../hyundap/db/version_service.py#L706) | 보조/내부 · 후보가 비활성인지 마지막으로 확인하고 실패 상태·복구 완료를 저장한다. 반환값은 없다. | 유지 | 후보 비활성 확인 후 버전 실패와 복구 완료를 같은 TX로 기록한다. |

## versions_repo.py

권한 있는 관리자의 버전 이력 조회와 실패 후보 상태 기록.

- 최상위 import: `sqlalchemy: text`; `hyundap.auth.errors: AccessError`; `hyundap.auth.permissions: allowed_companies`; `.errors: DbError`; `.runtime: require_id, transaction`; `.sessions_repo: current_actor`
- 상수·상태: 없음
- 클래스: 없음 — 일반 함수 구조

| 함수/인자 · 위치 | 역할/현재 동작 | 판단 | 근거·읽는 방법 |
|---|---|---|---|
| [`list_document_versions(actor, document_id, *, connection=None)`](../../hyundap/db/versions_repo.py#L12) | 주요 진입점 · 회사 권한이 있는 관리자의 버전 이력을 순번대로 반환한다. 내부 경로는 제외한다. | 유지 | 권한→문서 회사→이력 조회→bool 변환의 단계가 명확하다. 비공개 이력을 일반 상세와 합치지 않는다. |
| [`mark_failed(version_id, *, connection=None)`](../../hyundap/db/versions_repo.py#L67) | 주요 진입점 · 서버 내부에서 비활성 처리 후보만 failed로 바꾼다. 정상 활성 버전은 보존한다. | 유지 | 문서→버전 잠금 후 비활성 후보만 실패로 바꾼다. 활성 포인터 보호를 유지한다. |

## 파일별 집계

| 파일 | 정의 수 |
|---|---:|
| `__init__.py` | 0 |
| `chunks_repo.py` | 3 |
| `clock.py` | 1 |
| `company_admin.py` | 3 |
| `company_credentials.py` | 8 |
| `company_routines.py` | 7 |
| `connection.py` | 2 |
| `documents_repo.py` | 9 |
| `errors.py` | 1 |
| `jobs_repo.py` | 15 |
| `runtime.py` | 8 |
| `schema.py` | 9 |
| `sessions_repo.py` | 6 |
| `split_schema.py` | 6 |
| `tenancy.py` | 8 |
| `upload_service.py` | 25 |
| `version_service.py` | 31 |
| `versions_repo.py` | 2 |

합계 18개 파일 / 144개 정의 = 모듈 함수 141개 + 중첩 함수 2개 + 클래스 메서드 1개. 함수 밖의 import·상수·잠금 사전·SQL 문자열·CLI 실행 블록도 검토했다. SQL/JSON 계약 파일은 변경하지 않았다.

## 노트북 전수 확인 기록

실행은 하지 않고 JSON의 코드 셀·실행 번호·출력 유무를 확인했다. 파일명은 학습 자료를 찾기 위한 표기이며 실행 번호는 성공이나 본인 작성을 증명하지 않는다.

| 노트북 | 코드 셀 | 실행 번호 있는 셀 | 출력 있는 셀 |
|---|---:|---:|---:|
| 261001_langchain_1.ipynb | 7 | 7 | 3 |
| 261006_langchain_2.ipynb | 13 | 13 | 9 |
| 261007_Runnable.ipynb | 10 | 3 | 5 |
| 261008_toolcalling.ipynb | 11 | 11 | 7 |
| Day_Lesson1_Test_전재경.ipynb | 5 | 4 | 4 |
| Lesson1006_Practice.ipynb | 21 | 17 | 13 |
| ToolCalling_실무용_과제.ipynb | 33 | 30 | 22 |
| ToolCalling_실무용_과제_원본.ipynb | 37 | 0 | 0 |
| [Day1_1]실습.ipynb | 20 | 17 | 15 |
| [Day1_2]실습.ipynb | 22 | 6 | 6 |
| [Day1_3]_종합_실습.ipynb | 13 | 11 | 10 |
| [Day1_4]실습.ipynb | 18 | 13 | 12 |
| [Day1_5]실습.ipynb | 19 | 14 | 13 |
| [Day1_6]실습.ipynb | 22 | 22 | 20 |
| [Day1_7]_1일차_최종_실습.ipynb | 17 | 17 | 14 |
| [Day5_1]실습_RAG개념과_단순RAG.ipynb | 11 | 11 | 11 |
| [Day5_1]실습_현대경제연구소_사내규정챗봇_빈칸.ipynb | 13 | 11 | 12 |
| [Day5_2]실습_DocumentLoader와_TextSplitter.ipynb | 11 | 11 | 11 |
| [Day5_2]실습_현대경제연구소_규정문서_불러오기와_쪼개기_빈칸.ipynb | 16 | 3 | 6 |
| [Day5_3]실습_Embedding과_VectorStore.ipynb | 12 | 11 | 12 |
| [Day5_과제]한빛시립도서관_책봇_RAG종합실습 (1).ipynb | 24 | 5 | 6 |
| [Day6_1]실습_Retriever와_LCEL_RAG체인.ipynb | 13 | 13 | 13 |
| [Day6_2]실습_검색품질개선.ipynb | 13 | 6 | 12 |
| [Day7_1]실습_대화형_RAG와_LangGraph.ipynb | 12 | 11 | 11 |
| [Day8_1]심화1_RAG_고도화_실습_1-4단계.ipynb | 9 | 0 | 0 |
| [Day8_1]심화1_RAG_고도화_실습_셀5-12.ipynb | 10 | 4 | 5 |
| [Day8_1]심화1_RAG_고도화_전략_멀티모달.ipynb | 15 | 13 | 13 |
| [시험]CLI_페르소나_챗봇.ipynb | 14 | 7 | 6 |
| [시험]_소득세법_멀티RAG챗봇.ipynb | 20 | 0 | 0 |
| lesson01.ipynb | 11 | 0 | 0 |
| lesson02.ipynb | 7 | 0 | 0 |
| toolcalling_Pratice.ipynb | 26 | 26 | 26 |
| 과제_고객VOC분석.ipynb | 19 | 12 | 14 |
| 과제_고객VOC분석_1.ipynb | 20 | 19 | 20 |
| 과제_고객VOC분석_2.ipynb | 19 | 18 | 18 |
| 과제_고객VOC분석_3.ipynb | 19 | 0 | 0 |
| 실습_뉴스모니터링.ipynb | 16 | 11 | 14 |
| 실습_이력서.ipynb | 11 | 0 | 0 |
| 심화2_하이브리드검색_리랭킹_기초실습.ipynb | 8 | 7 | 8 |
| 종합실습_소득세법_멀티RAG챗봇_워크북.ipynb | 27 | 0 | 0 |

일반 함수·분기·반복·dict/list·Path/JSON·LCEL/RAG가 학습 근거다. 40개 노트북 코드 셀에서 `sqlalchemy`, `@contextmanager`, `ContextVar`, `RLock` 표기는 찾지 못했다. 이 구조들은 숙련을 가정하지 않고 필요성과 읽는 순서를 별도로 설명한다.
