# 최종 v4 리팩토링 구현·검증 — #168

2026-10-27, feature/db 로컬 작업 트리. 비교 기준 HEAD `bbb10da323edc76b95f71039155b070bb8eddfb2`.
검증은 커밋 전 작업 트리에서 실행했으며, 이 보고서는 해당 구현 커밋에 함께 포함한다.

## 결과

**전체 290개 통과, 실패 0, 오류 0, 건너뜀 0.** 새 격리 MySQL 8.4.13 인스턴스의 빈 전용 DB에서 14.920초 동안 실행했다. Python 3.11.9 / SQLAlchemy 2.0.55 / PyMySQL 1.1.2 / python-dotenv 1.2.4를 사용했다.

```powershell
python -m unittest discover -s hyundap/tests -t .
```

테스트 URL·DB 이름·권한 조건은 [초기화 안내](V4_INITIALIZATION.md)의 검증 절을 따른다. 실행 원본은 `.git/v4-tests.log`, 집계는 `.git/v4-results.json`에 보관하며 비밀값을 커밋하지 않는다. 일반 테스트 명령과 동일한 unittest discover를 환경 설정용 로컬 runner에서 실행했다.

## 적용 범위

| 항목 | 최종 구현 |
|---|---|
| 생성 | `schema.create_v4_schema(engine)` 하나로 11개 최종 테이블 직접 생성. DB별 초기화 잠금·기존 객체 거부·구조 검증 후 버전 기록 |
| 저장 | 문서·버전·작업의 고정 INSERT. 파일 메타데이터는 버전 테이블에만 저장. 기존 문서 storage_key 갱신 제거 |
| 작업 결과 | `jobs_repo.store_job_result`로 보고서·단계·오류·상태·종료 시각을 한 UPDATE에 저장 |
| 재시도 | `jobs_repo.reset_job_for_retry`로 attempts만 증가하고 상태·시각·보고서 이력을 같은 TX에서 저장 |
| 접근 | 현재 DB 권한·회사·토큰·권한 버전·유휴/절대 만료/인증 expires_at을 검사. 최종 소문자 역할 코드 사용 |
| 구조 조회 | Engine 최초 준비와 관리 실측에서만 전체 검증. 업무 경로의 컬럼 조회·구조 캐시·무효화 함수 제거 |
| 폐기 | v3 준비 함수·이행 CLI·004 SQL·대표/실제 v3 fixture·legacy.py·이전 캐시 테스트 제거. 운영 전환은 사용자 지시로 범위 제외 |

## 모듈·함수 배치

새 업무 클래스는 없다. 기존 일반 함수·dict 구조를 유지한다. 전체 실제 함수 서명과 메인/보조 분류는 [DB 명세](../../hyundap/db/docs/spec.md)에 기록했다.

| 모듈 | 메인 | 주요 보조 |
|---|---|---|
| schema | create_v4_schema, snapshot, validate_v4, audit_counts, main | _read_layout, _structure, _expression |
| documents_repo | create_document_version, validate_metadata, find_duplicate_document | _check_candidate_creation, _prepare_document, _insert_version_and_job |
| jobs_repo | record_stage_result, normalize_report | store_job_result, reset_job_for_retry, _store_stage_result |
| upload_service / version_service | 기존 업로드·재시도·게시·복구 메인 유지 | 새 jobs_repo 저장 함수 호출 |
| clock / sessions_repo / runtime | 기존 접근·TX 메인 유지 | epoch_now는 UTC Unix 초 고정. 세션 필수 컬럼을 직접 조회 |

## 필수 시나리오

최종 DDL 기반 테스트가 회사별 중복·현재 후보 제외·동시 중복 1건 성공·버전 번호 경쟁, 문서/버전/작업 원자 생성, manifest 입력·멱등·불변성과 예상 개수 갱신 실패 롤백, 보고서 개수 모순·이력·비밀 필드 제거를 검증한다.

권한 위조·계정/회사 비활성·만료·문서 상태 변경, 게시 CAS 경쟁·포인터/작업 저장 실패 롤백·외부 활성화 중 행 잠금 미유지, 오래된 복구·정리 대기 보호·복구 완료 후 다음 후보·동일 ID 재시도·활성 작업 충돌 롤백을 통과했다. 이전 최소 fixture에서 빠져 있던 활성 작업 제약은 최종 fixture에서도 항상 적용한다. 게시만 시험하는 여러 ready 후보는 fixture에서 색인 작업을 명시적으로 종료해 준비한다.

직접 생성 수용 테스트는 기존 객체/행의 무변경 거부, 동시 초기화 거부, 부분 DDL 실패의 성공 버전 미기록, 최종 검증 실패 시 빈 버전 테이블, 실제 FK·UNIQUE·CHECK·CSRF 필수값과 컬럼 타입/NULL/기본값/생성식·인덱스·FK·CHECK 강제 여부의 훼손 탐지를 포함한다. 기본값 문자열의 대소문자는 보존한다. MySQL ALTER CHECK가 동일 ASCII 상수의 `_utf8mb4`를 `_ascii`로 바꾸는 표기 차이만 정규화한다.

## 인증 PR #167 실제 메서드 교차 시험

대상 head: `ea6d89a07b541a6ce386be373ef983ebb4c94517` (별도 검토 worktree). 해당 PR의 accounts/passwords/session_service/session_columns와 공통 설정을 현재 DB 모듈·최종 직접 생성 DDL에 연결했다. 인증 파일은 수정하거나 현재 브랜치에 병합하지 않았다.

**platform_admin / company_admin / employee 세 역할 모두 계정 생성 → 로그인 → 세션 검증 → 로그아웃 → 폐기 후 SESSION_EXPIRED를 통과했다.** 혼합 대소문자 이메일도 정규화 로그인 키로 조회했다. 이전 필수 시간 누락은 최종 DB 기본값으로 공급되며 역할 코드는 인증 PR과 같은 소문자다. CSRF와 기존 시간 순서 제약을 유지했다.

이는 전체 테스트 290개와 별도의 합성 계정 교차 시험이다. 해당 PR의 UI 바인딩·모든 인증 요구·후속 head 변경까지 검증한 것은 아니다. 설정은 해당 PR 구현을 로드했고 DB Engine만 격리 DB로 대체했다. 기록: `.git/v4-auth-probe.py`, `.git/v4-auth-results.json`.

## 동일 기준 정적 비교

최상위 함수가 있는 DB Python 파일만 집계한다. __init__.py·예외 클래스 전용 파일은 제외하고 독스트링·빈 줄은 포함한다. SQL/JSON/문서·테스트는 이 Python 행 수에 포함하지 않는다.

| 항목 | 기준 HEAD | 현재 |
|---|---:|---:|
| 함수 포함 모듈 | 11 | 11 |
| 최상위 함수 | 100 | 98 |
| Python 행 | 2,460 | 2,139 |
| 업무 컬럼 구조 조회 지점 | 6 | 0 |

Python 행은 321행 감소했다. 새 최종 DDL·고정 구조 계약·수용 테스트가 추가되므로 저장소 전체가 같은 비율로 줄었다거나 응답 속도가 향상됐다고 해석하지 않는다.

## 미수행과 전달 상태

Railway 접속·DDL/DML·자료 변환·운영 전환·실제 OpenAI/Chroma 호출은 수행하지 않았다. 공유 DB는 그대로다. 기존 v3 및 과거 혼합 v4로 향하는 앱은 최종 구조 검사에서 거부되므로 새 빈 최종 DB가 필요하다.

로컬 구현과 검증을 완료했다. 이후 커밋·푸시·PR 병합 상태는 Git 기록과 #168의 최신 진행 기록을 따른다. 이 검증은 운영 DB 적용을 포함하지 않는다.
