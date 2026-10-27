# DB 접근 권한 명세

책임: 전재경의 DB 작업 중 회사 격리·문서 접근 재검증 범위다. 로그인·비밀번호 해싱/검증·세션 발급/폐기는 아래 「로그인·세션 명세」 장에서 정의한다. UI 담당의 기존 로그인 화면은 변경하지 않는다. [채택 계약](../../../docs/db-design/DB_contract.md)을 따른다.

| 기능 | 함수 계약 | 연계·책임 경계 |
|---|---|---|
| F81 / FUN-253 | get_allowed_company_ids(actor) → set[str] | 이준영은 선택 회사와 교집합·범위 밖 선택 검증 |
| F126 / FUN-407 | verify_documents_access(actor,refs) → None 또는 예외 | 소용우 chatbot은 예외 변환·답변 전체 반환 제어 |

actor는 로그인·세션 기능이 서버에서 제공하는 user_id, company_id, role, session_id, auth_version과 서버 내부 `_session_token`을 포함한다. DB의 `sessions_repo.current_actor`는 토큰 SHA-256과 DB token_hash, 계정·회사 활성, 만료·폐기, users/sessions.auth_version 일치를 읽어 검사한다. 세션 발급·연장·폐기를 수행하지 않는다. UI 바인딩(B24) 연결은 미완료이며 세션을 발급받지 않으면 보호된 DB 함수를 호출할 수 없다.

브라우저가 보낸 역할·회사는 권한 근거로 쓰지 않는다. `_session_token`을 UI·로그·JSON·LLM 입력에 넣지 않는다. 앱 역할은 소문자 3종이며 기존 대문자 역할을 매핑한다. 기존 사용자·세션 컬럼은 데이터 구조로 유지한다.

```python
from hyundap.auth.permissions import get_allowed_company_ids, verify_documents_access

# actor는 로그인·세션 기능이 서버에서 전달한 문맥이다.
companies = get_allowed_company_ids(actor)
verify_documents_access(actor, [(document_id, version_id)])
```

세션·사용자·회사는 각각 PK로 조회한다. DB 장애는 DbError(DB_UNAVAILABLE)로 전달하며 빈 권한 집합으로 숨기지 않는다. chatbot이 STATE_CHANGED를 자기 오류로 변환하고 최종 답변 반환을 중단한다.

테스트는 합성 사용자·세션 행을 직접 준비해 DB 접근 검증을 확인한다. 로그인 성공이나 비밀번호 검증의 테스트가 아니다. 공유 DB v4 적용·실측과 UI 로그인 바인딩 연결은 아직 완료하지 않았다.

# 로그인·세션 명세

책임: 이성민의 아이디·비밀번호 확인, 세션 발급·재검증·폐기, 초기 계정 등록 범위다. 상태: 구현(SPEC-AUTH-001, `session_service.py`·`passwords.py`·`session_columns.py`·`accounts.py`·`create_account.py`). DB 통합 테스트는 격리 MySQL에서 아직 실행하지 않았다. 기존 users/sessions 컬럼만 쓰며 DDL·requirements.txt는 바꾸지 않는다. 해시·토큰은 표준 라이브러리(`hashlib.scrypt`, `secrets`, `hmac.compare_digest`)로 처리한다.

| 함수 | 계약 | 실패 |
|---|---|---|
| login(username, password) → dict | 아이디는 `users.login_identifier`(소문자·공백 제거 email)와 비교. 성공 시 세션 1건 발급 | AccessError(INVALID_CREDENTIALS), DbError(DB_UNAVAILABLE) |
| validate_session(session) → dict | login이 반환한 dict의 `session_id`·`token`으로 `sessions_repo.current_actor` 재검증 | AccessError(SESSION_EXPIRED / ACCESS_DENIED), DbError(DB_UNAVAILABLE) |
| logout(session) → None | `session_id`와 토큰 해시가 모두 맞는 세션만 `revoked_at` 기록 | 없는·만료·폐기된 세션도 None. DB 장애만 DbError |
| create_account(email, role, company_id, password) → user_id | 비밀번호 해시 후 users 1행 추가. 운영자 CLI가 호출 | DbError(INVALID_INPUT / CONFLICT / DB_UNAVAILABLE) |

login·validate_session 반환 dict는 같은 형태다.

| 필드 | 값 |
|---|---|
| user_id, role, company_id | DB 값 그대로. 역할은 소문자 3종으로 매핑. company_id는 DB 값이 NULL일 때만 None |
| display_name | 컬럼이 없으므로 email의 `@` 앞부분 |
| company_name | 소속 회사의 `companies.name`. company_id가 NULL이면 None |
| allowed_company_ids | `allowed_companies` 결과를 정렬한 목록 |
| token, session_id | 발급 토큰과 세션 ID. UI가 다시 넘기는 값이며 화면·로그에 출력하지 않는다 |

비밀번호는 `scrypt$<n>$<r>$<p>$<salt>$<hash>` 문자열(base64, 255자 이하)로 `users.password_hash`에 저장한다. 기본값은 n=2^15, r=8, p=1, salt 16바이트, 결과 32바이트다. scrypt 호출마다 `maxmem = 128*r*(n+2) + 128*r*p + 32 MiB`(기본값에서 약 64 MiB)를 넘긴다. 저장값은 n이 2^14~2^16의 2 거듭제곱, r 1~8, p 1~2, n < 2^(16·r)일 때만 쓴다(OpenSSL은 n=2^16, r=1을 거부한다). 범위 밖·형식 오류·NULL은 scrypt를 부르기 전에 실패로 보고 더미 검증 1회 뒤 False를 반환한다. 범위 안의 저장값에서 scrypt가 `ValueError`를 내면 더미 검증 1회를 더 하고 False를 반환한다. 검증 함수는 예외를 내지 않는다. 더미 해시는 같은 파라미터로 미리 만든 상수 문자열이며, 기본 파라미터를 바꾸면 다시 만들어야 한다. 비교는 상수 시간으로 한다. 로그인 시 재해시는 하지 않는다.

없는 아이디, 틀린 비밀번호, 비활성 사용자, password_hash NULL, 알 수 없는 역할, 회사 없는 비 platform_admin, 회사가 지정됐지만 없거나 비활성인 사용자(역할 무관), 활성 회사가 하나도 없을 때의 platform_admin은 모두 INVALID_CREDENTIALS 하나로 응답한다. 이 검사는 `sessions_repo._check_company_role`·`allowed_companies`와 같으므로 login은 ACCESS_DENIED·SESSION_EXPIRED를 내지 않는다. 안내는 「아이디 또는 비밀번호가 올바르지 않습니다.」로 고정하고, 모든 실패에서 scrypt를 정확히 1회 수행해 응답 시간을 맞춘다. DB 장애는 실패 응답으로 바꾸지 않는다. 동시 로그인 시도마다 scrypt 메모리 약 32 MiB를 쓰며, 동시 실행 제한은 두지 않는다(수용한 위험. login_attempts 제한과 함께 후속 작업). scrypt는 DB 연결을 잡은 로그인 트랜잭션 안에서 실행하므로 동시 로그인이 몰리면 연결 풀이 고갈돼 다른 보호된 DB 함수까지 멈출 수 있다. 이것도 수용한 위험이며, 트랜잭션 밖 검증 또는 동시 실행 제한은 같은 후속 작업에서 다룬다.

토큰은 `secrets.token_urlsafe(32)`(43자), 세션 ID는 uuid4다. DB에는 토큰의 SHA-256만 저장한다. sessions.auth_version은 users.auth_version을 복사하고, expires_at은 `UTC_TIMESTAMP(6)` + `SESSION_TTL_MINUTES`(기본 480분)로 SQL에서 계산한다. sessions에 기존 Railway v3 컬럼(`csrf_token_hash`, `created_at`, `last_seen_at`, `idle_expires_at`, `absolute_expires_at`)이 남아 있으면(`runtime.get_table_columns`의 구조 캐시로 확인. DDL 직후에는 `runtime.clear_schema_cache` 필요) 그 값도 채운다. 시간은 `legacy.epoch_now()`와 같은 UTC Unix 초이며 `DB_LEGACY_TIME_UNIT=milliseconds`일 때만 밀리초다. 그 밖의 값은 기본값으로 돌아가지 않고 `DbError(INVALID_STATE)`로 거부한다. created_at = last_seen_at = 현재, idle_expires_at = absolute_expires_at = 현재 + TTL이다. `csrf_token_hash`는 세션 토큰과 별개인 `secrets.token_urlsafe(32)`의 SHA-256이며 원문은 저장·반환하지 않는다. 컬럼이 없으면 위 v4 INSERT를 그대로 쓴다. 사용자 조회와 세션 추가는 한 트랜잭션이다. 새 로그인은 기존 세션을 폐기하지 않는다. 재검증은 만료를 연장하지 않는다. 브라우저가 보낸 role·company_id는 읽지 않는다.

초기 계정은 운영자가 DB 접속 권한으로 CLI를 실행해 만든다. 비밀번호는 기본으로 getpass 입력(2회 확인)으로 받는다. 비대화형 실행에서만 비밀 저장소에서 export한 `HYUNDAP_ACCOUNT_PASSWORD`를 쓰며, 명령줄에 직접 적지 않는다. CLI는 읽은 직후 이 환경변수를 지운다. 비밀번호는 명령 인자·로그·출력에 남기지 않는다. 길이는 12~1024자다. 역할은 platform_admin / company_admin / employee 중 하나이며, platform_admin은 회사 없이, 나머지는 활성 회사 ID와 함께 만든다. 첫 platform_admin도 같은 CLI로 만든다. 같은 login_identifier는 CONFLICT다. 저장 직전에 이메일은 앞뒤 공백을 지우고 소문자로 바꾸며, 역할은 기존 역할 코드(platform_admin→SUPER_ADMIN, company_admin→COMPANY_ADMIN, employee→EMPLOYEE)로 바꿔 저장한다(CLI 입력은 앱 역할 이름 그대로). users에 `created_at`·`updated_at`이 있으면(Railway 기준 기본값 없는 필수 컬럼) `legacy.epoch_now()` 값으로 둘 다 채우고, 없으면 채우지 않는다.

```python
from hyundap.auth.session_service import login, logout, validate_session

session = login(username='admin@example.com', password=password)
current = validate_session(session=session)  # DB 기준 최신 역할·회사
logout(session=session)
# 초기 계정: python -m hyundap.auth.create_account --email admin@example.com --role platform_admin
```

UI 연결은 박경환 담당 `backend_bindings.py`에서 `login`, `logout`, `validate_session` 이름으로 위 함수를 등록한다. must_change_password는 반환하지 않는다. 로그인 시도 제한(login_attempts), 비밀번호 변경·초기화, display_name 컬럼 추가는 이 범위가 아니다.
