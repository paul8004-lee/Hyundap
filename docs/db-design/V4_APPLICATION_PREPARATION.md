# DB v4 단일화 적용 준비서

> 후속 결정: #168 구현 중 사용자 지시로 v3 이행 도구·호환 설정·운영 전환 절차를 폐기했다. 아래 보존/이행 제안은 과거 준비 이력이다. 현재 계약은 [V4_INITIALIZATION.md](V4_INITIALIZATION.md), 구현 검증은 [V4_REFACTOR_VALIDATION.md](V4_REFACTOR_VALIDATION.md)를 따른다.

작성일: 2026-10-27. 대상 브랜치: feature/db.
기준 커밋: bbb10da323edc76b95f71039155b070bb8eddfb2.
포함된 develop: 059cefee4c715a4f7f3ffb6a40fa7e4bbd2c0192.
상위 개선안: [DB_V4_SIMPLIFICATION_PLAN.md](DB_V4_SIMPLIFICATION_PLAN.md).

상태: 코드·DDL 적용을 위한 계약 초안과 작업 순서 준비. 이 문서는 구현 완료·운영 적용 승인서가 아니다.
현재 feature/db는 develop을 포함하며, 인증 PR #167은 검토 시점 OPEN이므로 병합된 것으로 가정하지 않는다.
현재 업무 코드·DDL·Railway·접속 비밀값은 변경하지 않았다.

## 1. 적용 범위와 고정 원칙

최종 목표는 빈 DB에 최종 v4를 직접 만들고 내부 업무 메서드는 그 구조만 사용하는 것이다.
단순히 기존 schema_migrations 숫자를 4로 바꾸거나 기존 테이블을 자동 삭제하지 않는다.
기존 v3 이행 코드는 전환이 끝날 때까지 보존하고, 완료 후 런타임에서 분리하는 순서다.

| 항목 | 준비 기준 |
|---|---|
| 코드 형식 | 기능별 모듈·일반 함수·dict 유지. 새 클래스 래퍼 없음 |
| 외부 계약 | 메인 진입 함수의 입력·반환·오류 및 보조 함수의 호출자 TX 참여 유지 |
| 시간 | BIGINT는 UTC Unix 초, DATETIME(6)은 UTC. 밀리초 데이터는 별도 확인 없이 초로 재해석하지 않음 |
| 보호 기능 | 현재 권한·회사 격리·해시 중복·manifest·CAS·복구·실패 롤백 유지 |
| 버전 의미 | 확정된 물리 계약 하나에 버전 하나. 이미 배포된 다른 v4가 있다면 새 변경은 v5 등 별도 버전 필요 |

아래 컬럼 정리는 최종 DDL 작성 기준의 제안이다. 현재 DB에서 DROP/ALTER가 실행됐다는 뜻이 아니다.
DDL 작성 전 인증 발급 계약과 상태·동시성 역할을 대조해 확정한다.

## 2. 최종 테이블과 컬럼 정리 초안

최종 테이블 수는 기존 업무 8개 + 문서 버전/청크 2개 + schema_migrations 1개, 총 11개를 유지한다.

| 테이블 | 기준 역할·유지 필드 | 정리·확인 항목 |
|---|---|---|
| companies | id, name, is_active, created_at, updated_at | 기존 회사 키·시간 값·활성 조건 보존 |
| users | id, company_id, email, login_identifier, role, is_active, auth_version, password_hash, 시간·필요한 google_sub | 기존 password는 삭제 후보. 실제 계정 이행 없이는 삭제하지 않음. role 저장 코드와 정규화 email CHECK 확정 |
| sessions | id, user_id, token_hash, auth_version, csrf_token_hash, 기존 시간·유휴/절대 만료, revoked_at 및 필요한 선택 문서 | 유휴/절대 만료는 유지. expires_at이 절대 만료와 중복인지 인증 담당과 확정한 뒤 단일 기준으로 조회/발급을 함께 변경 |
| documents | id, company_id, doc_type, publication_status, deleted_at, active_version_id, uploaded_by, 시간 | 파일 메타데이터 중복 및 기존 상태·세대·작업 포인터 제거 후보 검토. 공개 상태와 처리 상태를 같은 의미로 합치지 않음 |
| document_versions | id, document_id, company_id, version_no, file_name, storage_path, sha256, file_size, version_status, data_category, expected_chunk_count, created_at | 파일 메타데이터의 기준 테이블. media_type의 필요 여부·소속 확정. 파일명 255·경로 1024·크기 상한 유지 |
| document_chunks | id, version_id, chunk_index, content_hash | 현재 ID·순서·해시·FK·유일성 유지. 본문/벡터는 Chroma |
| index_jobs | id, version_id, 필요한 document_id/company_id, status, stage, attempts, report, 오류·시간 | attempt_no→attempts 통합. generation/content_revision 및 임대 필드는 실제 역할 확인 후 정리. 활성 작업 보호와 버전당 작업 유일성 보존 |
| oauth_states | 현재 state/binding/nonce 해시·redirect·시간·사용 여부 | 인증 범위의 구조 보존, 기능 확장 없음 |
| audit_logs | 현재 actor/company/document·action/result/request·details·시간 | 기존 기록 의미·FK 보존. 신규 감사 기능 추가 없음 |
| login_attempts | key_hash, attempts, window_started_at | 기존 구조 보존, 로그인 제한 구현은 인증 담당 범위 |
| schema_migrations | 현재 적용 버전의 단일 행 | 모든 생성·구조 검증이 끝나야 최종 버전 기록. 부분 생성에 4를 기록하지 않음 |

### 동시성·상태 보존 조건

attempt_no를 없애더라도 재시도 횟수는 attempts에 보존한다.
status(QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED)와 stage(received/ingest/embedding/verify/publish/complete)는 분리한다.
기존 uq_active_job의 문서당 활성 작업 보호는 제거하지 않는다. document_id·상태 기반 보호를 유지하거나
동등한 제약을 제시하고, 다른 활성 작업과 재시도가 충돌할 때 전체 롤백하는 테스트를 통과시켜야 한다.
generation, content_revision, version_no는 서로 같은 번호로 간주하지 않는다.
index_jobs의 version_id와 document/company를 함께 남기면 복합 FK로 소속 불일치도 막는다.

## 3. 모듈·메서드 적용표

아래는 구현할 책임 배정이며 현재 함수가 삭제·변경됐다는 의미가 아니다. 클래스는 모두 없음이다.

| 모듈 | 메인/보조 | 적용할 변경 |
|---|---|---|
| schema.py | 신규 메인 create_v4_schema(engine) | 빈 DB 전용 직접 생성 진입점. 버전·구조 검증·재실행 거부 제공. 이름/서명은 구현 전 명세에 확정 |
| schema.py | 보조 snapshot, validate_v4, audit_counts | 최종 전체 테이블·타입·NULL·기본값·생성식·FK/UNIQUE/CHECK를 검증하도록 기준 통일 |
| connection.py | 메인 연결 점검 | 최종 구조를 만족하는 v4만 업무 준비 상태로 허용. 버전 숫자만으로 성공하지 않음 |
| documents_repo.py | create_document_version 및 기존 보조 함수 | legacy.insert를 명시적 INSERT로 교체. 중복 파일 정보·generation 호환 분기 정리, 회사/문서 잠금·원자 저장 유지 |
| jobs_repo.py | record_stage_result 및 상태 저장 보조 | 상태·단계·보고서·종료 시각을 한 TX/필요한 UPDATE에 통합. 종료/재시도 책임을 이 모듈로 이동 |
| upload_service.py | 기존 메인 흐름 유지, 저장 보조 수정 | storage_key 호환 UPDATE 제거 후보, 고유 원본 경로·같은 후보 재시도 유지. 파일/외부 호출 순서는 별도 확대하지 않음 |
| version_service.py | 게시·복구 메인 유지 | jobs_repo의 확정된 상태 저장 계약 호출. restored만 실패 종료, restore_pending 보호·최신 포인터 CAS 유지 |
| sessions_repo.py | current_actor 및 만료 검사 보조 | 최종 필수 컬럼을 직접 읽고 존재 검사 제거. 인증 담당 발급·검증·로그아웃의 같은 시간/만료 계약 사용 |
| runtime.py | 기존 TX·입력 검사 | 업무 구조 조회가 없어지면 get_table_columns/clear_schema_cache 제거 검토. 관리 실측은 schema에 유지 |
| legacy.py | 과도기 호환 함수 | 모든 호출 제거를 확인한 뒤 insert/finish_job/reset_job_for_retry 정리. epoch_now는 인증 사용처 확인 후 유지/공통 이관 |

### 직접 생성 진입점의 실행 계약

create_v4_schema(engine)은 제안된 유일한 생성 진입점이다. 앱 시작·import·일반 연결 점검에서 자동 호출하지 않는다.
기존 업무 TX 연결 대신 명시적으로 전달된 Engine의 전용 연결을 사용해 MySQL DDL의 암시적 커밋이
호출자 업무 TX에 섞이지 않게 한다. 비어 있지 않은 DB·지원하지 않는 서버/키 조건은 쓰기 전에 거부한다.

실행 순서는 빈 DB 확인 → 최종 테이블 생성 → 순환 FK 추가 → 구조/정합성 확인 → 버전 기록이다.
documents↔document_versions 및 documents↔index_jobs 관련 순환 참조를 고려한다.
검증은 버전 행이 아직 없을 때도 구조 메타데이터를 검사할 수 있도록 현재 snapshot의 역할을 분리한다.
DDL 전체를 BEGIN/ROLLBACK으로 취소할 수 있다고 가정하지 않는다. 부분 실패 시 자동 DROP·덮어쓰기·강제 재실행을 하지 않는다.

## 4. PR #167과의 선행 연결 조건

검토 대상 head ea6d89a에서는 실제 v3 DDL을 보존한 v4에서 create_account가
created_at/updated_at 누락으로 MySQL 1364, 소문자 role·혼합 email은 CHECK 3819를 재현했다.
이는 최종 v4 계약의 사용자 INSERT·역할 코드·이메일 정규화를 명시해야 하는 근거다.
PR이 후속 수정됐다면 실제 코드를 다시 대조하며, 이 준비 문서가 인증 코드를 대신 수정하지 않는다.

| 연계 대상 | 확정할 내용 | 차단되는 단계 |
|---|---|---|
| 인증 accounts | 필수 시간 공급, DB role 코드, lower/trim email, password_hash 저장 | 최종 users DDL 및 계정 생성 회귀 |
| 인증 session_service/session_columns | csrf·생성/활동/유휴·절대 만료·폐기 타입과 신규/삭제 컬럼 | sessions 컬럼 제거·시간 함수 이관 |
| 작업자/운영 | 기존 큐·임대·generation/content_revision을 읽는 프로세스 존재 여부 | 기존 작업 필드 제거·실제 Railway 전환 |
| UI/검색 | 기존 11필드 DTO 및 세션→서버 actor 전달 유지 | 최종 회귀·실서비스 연결 확인 |

인증 측에 legacy.epoch_now 의존이 남으면 함수부터 삭제하지 않는다. 기존 역할 코드 변경과 세션 정책 삭제는
편의상 결정하지 않고 저장/발급 양쪽을 같은 계약으로 적용한다. 타 담당자에게 메시지를 발송하거나
열린 PR을 수정하는 일은 이번 준비 범위에서 수행하지 않았다.

## 5. 구현 순서와 수용 테스트

| 순서 | 작업 단위 | 완료 조건 |
|---|---|---|
| 1 | 위 필드 유지/통합/제거 및 인증 계약을 확정하고 최종 CREATE SQL 작성 | 11개 테이블·순환 FK·상태·시간·필수값 검토 가능, 기존 데이터 매핑 별도 |
| 2 | 직접 생성 함수·빈 DB 안전 검사 구현 | 기존 테이블이면 아무것도 변경하지 않고 거부, 정상 생성 뒤 구조 일치, 부분 실패 시 버전 미확정 |
| 3 | 저장·작업·세션 코드를 v4 고정 SQL로 교체 | 공개 진입 계약 유지, 업무 경로의 메타데이터 조회 및 상태 이중 동기화 제거 |
| 4 | fixture를 최종 v4 직접 생성으로 바꾸고 전체 회귀 | 아래 필수 시나리오와 최종 전체 테스트 건너뜀 없이 통과 |
| 5 | 기존 이행 코드·호환 설정을 런타임에서 분리하고 운영 전환안 검토 | 남은 호출 0 확인, 실제 자료·백업 복원·인증/작업자 준비 확인 후 별도 승인 |

필수 시나리오는 회사별 중복/동시 생성·현재 후보 제외, manifest 불변/개수 모순,
권한·만료·문서 상태 변경, 원자 저장 실패·게시 CAS·오래된 복구, 정리 대기/완료 뒤 다음 후보,
동일 ID 재시도·이력·활성 작업 충돌 전체 롤백, 계정 생성→로그인→검증→로그아웃이다.
현재 최소 fixture와 실제 v3 재현 fixture가 제공하던 제약 보호를 새 생성 fixture에서도 검사한다.
자동 회귀에서 실제 API·운영 문서를 사용하지 않고 외부 수집/벡터는 대역으로 구분한다.

현재 test_schema_migration의 v3 BASELINE은 신규 생성 기준에서 제외할 후보지만,
운영 전환 완료 전 기존 이행을 검증하는 별도 테스트까지 먼저 삭제하지 않는다.
새 초기화·새 저장 코드가 통과한 뒤 정상 fixture를 전환하고, 일회성 이행 테스트는 별도 보존/폐기를 결정한다.

## 6. 비교 기준과 준비 완료 상태

2026-10-27 기준 AST 정적 집계: 최상위 함수가 있는 DB Python 모듈 11개, 최상위 함수 100개,
해당 파일 2,460행(독스트링·빈 줄 포함). __init__.py와 오류 클래스 전용 모듈은 이 수치에서 제외한다.
호환 업무 구조 조회는 get_table_columns 사용 지점 6곳이다. 함수·모듈·분기·SQL 횟수의
전후 기준을 동일하게 맞춰 측정하며, 전체 코드 감소율이나 속도 개선 비율을 미리 단정하지 않는다.

준비 완료: 기준 브랜치/커밋 확인, 11개 테이블 초안, 필드 정리 후보, 메서드 책임 배정,
인증/작업자 연결 조건, 직접 생성 안전 계약, 테스트·운영 전환 순서를 정리했다.
미실행: 최종 SQL 생성, 런타임 코드 개편, 기존 함수 삭제, 신규 테스트 실행, Railway 쓰기.
다음 구현 작업은 위 계약을 기준으로 빈 DB용 최종 CREATE SQL과 초기화 수용 테스트를 작성하는 것이다.
