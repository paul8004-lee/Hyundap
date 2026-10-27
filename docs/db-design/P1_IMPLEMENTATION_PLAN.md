# DB P1 구현 계획

검토일: 2026-10-26 · 기준 브랜치: feature/db · 기준 커밋: 575b53e

이 문서는 구현 계획이다. 기능 추가·공유 DB 변경·다른 담당자 재배정·GitHub 이슈 등록을 수행한 결과가 아니다. 기존 .gitignore 수정은 별도 사용자 작업으로 유지한다.

## 1. P1의 의미와 담당 범위

기존 DB 문서의 P0는 선행 작업 순서를 뜻한다. 원본 기능명세서의 P1은 필수 기능 단계이므로, P0 다음에 새로운 관리 기능을 추가한다는 의미로 해석하지 않는다. 이번 P1은 기존 DB 저장·권한·게시 구현을 실제 수집·임베딩·검색·챗봇과 연결하고 검증하는 작업이다.

원본은 `C:\Users\user\Downloads\문서검색_통합_기능명세서.xlsm.xlsx`를 읽기 전용으로 확인했다. 문서 안의 담당 지시는 분석 근거이며, 로그인·비밀번호 처리·세션 발급/갱신/폐기를 제외한 사용자의 현재 범위를 우선한다.

| 원본 명세 | 우선순위·DB 책임 | 현재 판정 |
|---|---|---|
| 기능명세 A15:N15 F63 / 함수메서드 A31:N31 FUN-206 | P1. 회사별 SHA-256 중복 조회와 최종 저장 보호 | DB 구현됨. 수집기의 중복 검사 콜백과 현재 후보 처리 계약 보완 필요 |
| 기능명세 A24:N24 F72 / 함수메서드 A43:N44 FUN-218·219 | P1 수집 흐름이 반환한 청크·처리 결과의 DB 저장 연계 | DB 호출 흐름 구현됨. 최신 수집 PR의 결과·보고서 필드 연결 필요 |
| 기능명세 A26:N26 F81 / 제약_운영 A18:G19 L57·58 / FUN-247 | P1. 허용 회사와 검색 판단용 DB 정보 제공. 검색 교집합·DTO·공개 판정은 search 책임 | 회사 조회·활성 버전 11필드 구현됨. 실제 search 연결 검증 필요 |
| 기능명세 A48:N50 F110·111·112 / FUN-316·317·318 | 팀 P1. DB 완전성 판정·게시·실패 정리 호출. 벡터 내부 처리는 embedding 책임 | DB·embedding 구현됨. 실제 MySQL+Chroma 연결 검증 필요 |
| 기능명세 A60:N60 F126 / 함수메서드 A90:N90 FUN-407 | P1. 답변 반환 직전 인용 문서·버전의 현재 접근 재검증 | DB 접근 검사·chatbot 호출 구현됨. 서버 actor 전달과 변경 상황 통합 검증 필요 |

범위에서 제외한다: F39 상세 화면은 P2이며 원문은 다운로드·미리보기를 이번 추가 범위에서 제외한다. F40 계정 권한 관리와 F41 로그 관리 화면은 기능명세에서 P3다. F111의 실패 정리는 P1이지만 관리자 삭제·재색인 연계는 P3다. WBS #74 search_logs는 소용우로 배정돼 있으며, DB P1로 임의 편입하지 않는다.

## 2. 현재 구현과 연결 상태

- `documents_repo`, `chunks_repo`, `jobs_repo`, `sessions_repo`, `auth.permissions` 및 업로드·게시·복구 서비스는 구현돼 있다. 같은 기능을 새 서비스나 클래스로 다시 만들지 않는다.
- embedding PR #137은 develop에 병합돼 있다. 실제 로컬 Chroma와 DB 자료형·함수 연결은 일부 검증됐지만, 실제 MySQL을 포함한 전체 수집→게시 검증과 OpenAI 실제 호출까지 완료된 것은 아니다.
- 현재 브랜치의 ingest와 search는 뼈대다. 다만 열린 PR #136의 최신 커밋 de04d50에는 실제 `ingest_document(file_path, upload_info, find_by_hash=None)`와 검증·보고서 생성이 있다. PR 코드를 현재 브랜치에 병합한 상태는 아니다.
- 최신 ingest는 콜백을 주지 않으면 중복 조회를 생략한다. 현재 DB는 수집기를 2개 인자로 호출하므로 수집기 콜백이 연결되지 않는다. 콜백을 단순히 연결하면 이미 생성한 자기 후보를 중복으로 볼 수 있어, 연결과 후보 제외를 함께 설계한다.
- 수집 보고서는 `parser`, `parser_version`, `chunk_size`, `chunk_overlap`, `warnings`, `section_count`, `chunk_count`다. 현재 DB 허용 목록에는 없어 성공 여부만 남고 이 값들은 누락된다.
- 공유 DB 구조 실측·관리자 적용 완료 근거는 이 검토에서 확인하지 못했다. 합성 fixture의 테스트 성공과 실제 DB 이행 완료는 구분한다.

## 3. 실행할 미션 5개

### 미션 1. 실제 DB 호환성과 적용 준비 확인

명세 필수 저장 기능의 선행 조건이며 #119의 잔여 확인이다. 기능명세에 새 화면이 있는 작업은 아니다.

- 접근 파일: `hyundap/db/connection.py`, `schema.py`, `docs/db-design/MIGRATION_v4.md`, `DB_schema.md`, `migrations/004_local*.sql`.
- 사용할 기존 함수: `snapshot`, `prepare_sql`, `validate_v4`, `audit_counts`. 실측 결과가 요구할 때만 저장 SQL의 컬럼 매핑을 수정한다.
- 작업: 읽기 전용으로 실제 필수 컬럼·키·문자셋·상태 enum·기존 자료를 점검한다. v3이면 검토용 SQL을 만들고, v4이면 검증 결과를 남긴다. 점검 자료에는 비밀값·사용자 원문을 넣지 않는다.
- 완료 기준: 실제 구조와 DB 코드의 불일치를 목록화하고 각 변경/재사용 판단이 검토 가능하다. 공유 DB DDL/DML 적용은 운영 담당의 별도 절차다.
- 협의: 이성민·실제 DB 운영 담당. 예상 작업량 2~4시간, 접속 준비와 관리자 적용 대기 별도.

### 미션 2. 수집 중복 검사·문서 유형·입력 오류 계약 연결

근거: F63, F72, FUN-205·206·218, #113·#130.

- 변경 파일: `hyundap/db/documents_repo.py`, `upload_service.py`, `docs/db-design/DB_contract.md`, `WORKFLOW_122.md`.
- 메인 흐름: `upload_document`, `retry_upload`, `run_attempt`, `find_duplicate_document`의 기존 책임을 유지한다.
- 보조 함수: `prepare_upload_source`와 `validate_metadata`는 공통 `DEFAULT_DOC_TYPE`·`DOC_TYPE_CODES`를 사용한다. `get_ingest_function`·`ingest_and_save_chunks`의 연결 지점에서 실제 콜백 전달을 맞춘다.
- 제안: DB 조회에 서버가 확정한 현재 버전 제외 옵션을 추가하고, ingest의 2개 인자 콜백이 이 옵션을 사용하도록 경계 함수를 둔다. 현재 후보와 회사·해시가 일치하는 내부 처리에서만 사용하며, 후보 생성 전의 최종 중복 검사는 제외 옵션 없이 수행한다. ingest에 제3 인자를 전달하는 방식과 기존 테스트 대역의 인자는 김혜리와 먼저 맞춘다.
- 문서 유형: 누락은 `other`, 허용되지 않은 값은 거부한다. 새 버전 생성 시 기존 문서 유형과의 불일치 처리도 테스트한다.
- 입력 오류: DB가 먼저 0바이트·100MiB 초과를 거부하는 경로와 ingest의 전용 오류 코드를 비교하고, 검증 순서·표시 코드를 합의한다. 원본 복사 시 재해시·기존 파일 보호는 유지한다.
- 완료 기준: 최초 업로드 성공, 같은 회사 기존 중복 거부, 다른 회사 동일 파일 허용, 자기 후보 오판 없음, 실패 후보의 동일 ID 재시도 성공, 문서 유형 기본값·잘못된 값 처리 통과.
- 협의: 김혜리(검증·콜백·오류), 이성민(공통 문서 유형). 예상 작업량 3~5시간.

### 미션 3. 실제 수집 보고서의 저장 항목 연결

근거: FUN-219의 처리 보고, F72 수집 결과 연계. F41 관리 화면을 구현하는 작업은 아니다.

- 변경 파일: `hyundap/db/jobs_repo.py`, 필요 시 `upload_service.py`, DB 계약과 처리 흐름 문서.
- 메인 함수: `normalize_report`, `record_stage_result`를 사용한다.
- 보조 함수: 수집 보고서의 설정·수치를 검사하는 함수 하나를 의미 있는 책임으로 추가하거나 `_report_metrics`를 보완한다. 기존 실패 상세·단계 검사·저장 함수는 재사용한다.
- 저장 제안: 파서/버전 문자열, 청킹 크기/겹침, 섹션 수, 청크 수를 타입·범위 검사 후 저장한다. 경고는 코드·개수 등 안전한 표현과 원문 보관 범위를 김혜리와 확정한 후 적용한다. 임의 dict 전체를 그대로 저장하지 않는다.
- 완료 기준: 실제 `build_ingest_report`의 합의된 필드가 단계별 JSON에 남고, 재시도 이전 기록이 보존되며, 임의 본문·토큰·원시 예외 문자열은 저장하지 않는다. embedding 보고서와 기존 성공 단계 보호도 계속 통과한다.
- 협의: 김혜리(보고서 필드·경고), 박영준(embedding 보고서 회귀 확인). 예상 작업량 2~3시간.

### 미션 4. 회사 범위·반환 직전 재검증의 서버 연결

근거: F81·F126·FUN-247·FUN-407, 제약 L57·L58.

- 확인/변경 파일: `hyundap/db/sessions_repo.py`, `documents_repo.py`, `hyundap/auth/permissions.py`, 관련 계약·테스트. 다른 담당자의 search/chatbot 구현은 협의된 연결부만 공동 검토한다.
- 메인 함수: `current_actor`, `get_allowed_company_ids`, `get_documents_with_active_version`, `verify_documents_access`.
- 설계: 인증 담당이 검증된 서버용 actor를 전달한다. DB는 현재 세션·계정·회사 상태를 읽어 확인한다. user_id·session_id·auth_version·서버용 토큰 필드의 전달 방식만 맞추며 로그인·비밀번호·세션 발급 기능은 추가하지 않는다.
- 완료 기준: search가 같은 11필드로 DTO를 만들고, 회사 밖 검색·평가 자료·차단/삭제·비활성 버전이 검색/응답에 사용되지 않는다. 생성 도중 권한이나 활성 버전이 바뀌면 chatbot의 반환 직전 검사에서 차단된다. 빈 version_id는 잘못된 인용으로 거부하고 호출자가 자기 오류 규격으로 변환한다.
- 협의: 인증 담당(담당자 확정 필요), 이준영(search), 소용우(chatbot). 예상 작업량 2~3시간.

### 미션 5. 실제 MySQL+수집기+Chroma 통합 검증과 문서 현행화

근거: F72·F110·F111·F112 및 앞선 저장/권한 계약의 완료 검증.

- 접근 파일: 기존 `hyundap/tests/test_db_runtime.py`, `test_upload_workflow.py`, `test_schema_migration.py`와 필요 시 실제 연계 전용 테스트 파일. `DB_contract.md`, `WORKFLOW_122.md`, `hyundap/db/docs/spec.md`의 낡은 구현 상태를 수정한다.
- 모델 API는 우선 대역을 사용하고 실제 수집기·로컬 Chroma·격리 MySQL을 연결한다. 기존 `FakeEmbedding` 테스트만 통과한 상태와 구분한다. 실제 API 검증은 자격 증명·비용 범위가 별도로 준비된 뒤 수행한다.
- 시나리오: 지원 형식 정상 처리, 기존/자기 후보 중복, 수집 실패, 부분 적재, 정상 게시, 동일 ID 재시도, 권한/원장 변경, 외부 활성화·정리 실패 후 복구. 게시 전 SQL 잠금과 외부 호출의 경계도 확인한다.
- 완료 기준: 기존 회귀 테스트와 합의한 실제 연계 시나리오가 통과하고, 테스트 대역/실제 구성요소·미검증 범위·실행 명령이 문서에 남는다. 실제 공유 DB나 기존 문서는 테스트 대상으로 쓰지 않는다.
- 협의: 김혜리·박영준·이준영·소용우. 예상 작업량 3~5시간, 다른 모듈 병합/연결 대기 별도.

## 4. 진행 순서와 구현 방식

미션 1은 실제 DB 호환성의 선행 조건이다. 접속 준비를 기다리는 동안 미션 2·3은 격리 DB에서 진행할 수 있다. 미션 4는 인증·search 연결 계약 준비와 병행하고, 미션 5에서 통합 완료를 판단한다. 예상 순수 작업량 합계는 12~20시간이며 외부 승인·접속·담당 모듈 준비 시간은 포함하지 않는다.

일반 함수·dict·명시적인 if/for를 사용한다. 학습에 도움이 되는 메인/보조 함수 책임 분리는 유지하고, 범용 파이프라인 클래스·작업 큐·새 인증 프레임워크는 추가하지 않는다. 각 함수의 한글 설명에 입력·반환·DB 변경 여부와 트랜잭션 책임을 적는다.

UI의 `user/file_path`를 DB의 `actor/source_path/metadata`로 바꾸는 바인딩, `success`를 화면의 `succeeded`로 바꾸는 표시 변환은 UI 담당의 후속 연동 과제다. 이를 DB P1 핵심 기능의 신규 화면 개발로 포함하지 않는다.

이 계획은 로컬 검토안이다. 실제 구현·이슈 등록·커밋·push·PR은 아직 수행하지 않았다. 첫 실행 작업은 미션 2의 중복 콜백·현재 후보 제외·doc_type 계약을 김혜리·이성민과 확인하는 것이다. 공유 DB 점검은 접속 준비가 되면 미션 1로 병행한다.

## 5. 메서드 구현 계획을 읽는 기준

이하 서명은 구현할 계약의 제안이며 실행 코드가 아니다. `재사용`은 이미 있는 함수를 그대로 호출/검증한다는 뜻이고, `수정`은 기존 함수의 책임 안에서 동작을 보완한다는 뜻이며, `신규 제안`은 구현 전에 담당자와 계약을 맞춰야 한다는 뜻이다.

메인 함수는 외부에서 시작하는 기능이나 하나의 처리 흐름을 관리한다. 보조 함수는 입력 검사·조회·순수 자료 조립·저장 등 한 책임을 담당한다. 같은 함수가 상위 흐름의 보조 함수이면서 자기 내부에서는 작은 흐름을 관리할 수 있다. 함수 분할은 학습을 돕는 기준으로 유지하며, 단순히 줄 수를 줄이기 위해 합치지 않는다.

`actor`는 인증 담당이 서버에서 제공한 dict다. `ids`는 DB가 생성한 document_id/version_id/job_id dict다. `connection` 또는 `database`는 이미 열린 SQLAlchemy 연결이다. 연결을 받은 보조 함수는 새 트랜잭션을 열거나 커밋하지 않는다. 파일·수집·Chroma 호출은 SQL 트랜잭션 밖에서 실행한다.

## 6. 미션별 메인 함수·보조 함수 상세

### 6.1 미션 1 — 실제 DB 점검과 저장 SQL 호환성

파일: `hyundap/db/schema.py`, `connection.py`. 기존 점검 기능을 우선 재사용한다. 읽기 점검을 위해 새 관리 서비스나 스키마 버전을 만들지 않는다.

**메인 함수**

| 함수·구분 | 입력 → 반환 | 처리 계획·오류 |
|---|---|---|
| `schema.main()` · 재사용 | CLI의 `--inspect`, `--prepare PATH`, `--check` → 종료 코드 | 실제 버전 확인 후 모드별 기존 보조 함수를 호출한다. SQL 생성과 DB 검증의 결과를 기록한다. 알 수 없는/부분 이행 구조는 중단하며, 쓰기 적용 기능은 추가하지 않는다. |
| `connection.main()` · 재사용 | 로컬 접속 설정 → 종료 코드 | 연결·버전·UTC 설정과 지원 구조를 점검한다. 출력에는 URL·비밀번호·실제 문서 내용을 넣지 않는다. |

**보조 함수**

| 함수·구분 | 입력 → 반환 | 단일 책임·DB 영향 |
|---|---|---|
| `create_db_engine()` · 재사용 | 환경/로컬 설정 → Engine | 연결 옵션 준비. 생성만으로 실제 연결하지 않으며 호출자가 사용 후 닫는다. |
| `snapshot(connection)` · 재사용 | 읽기 연결 → layout dict | 컬럼·기본 키·유일성·외래 키·버전 등 실제 메타데이터 수집. DB 변경 없음. |
| `key_definition(column)` · 재사용 | 키 컬럼 정보 → DDL 타입 문자열 | 지원 키 타입·문자셋·collation 확인. 알 수 없는 키는 ValueError. |
| `prepare_sql(layout)` · 재사용, 실측 시 필요한 부분만 수정 | v3 layout → 검토용 SQL 문자열 | 기존 컬럼과 새 컬럼의 호환성을 확인하고 SQL을 만든다. 기존 데이터나 버전 마커를 직접 변경하지 않는다. |
| `validate_v4(layout)` · 재사용 | v4 layout → 구조 오류 목록 | 필요한 컬럼·키·제약 확인. 빈 목록의 의미는 구조 검사 통과이며 전체 서비스 검증 완료는 아니다. |
| `audit_counts(connection)` · 재사용 | v4 읽기 연결 → check_name/violations 목록 | 문서·버전·청크의 DB 내부 정합성 위반 건수 조회. |

호출 순서: `main → create_db_engine → snapshot → 버전에 맞는 prepare_sql 또는 validate_v4/audit_counts → 연결 종료`. 실측에서 NOT NULL·enum·키 불일치를 찾은 경우에만 해당 repo SQL 변경안을 별도 목록으로 만든다. 공유 DB 마이그레이션 실행 메서드는 이번 계획에 추가하지 않는다.

완료 확인: 실제 버전·키/필수 컬럼 비교표, 생성 SQL/검사 결과, 관리자 적용 필요 여부가 남아야 한다. 테스트에서는 기존 `test_schema_migration.py`의 보존·중복·FK·CHECK 시나리오를 유지한다.

### 6.2 미션 2 — 업로드 입력·자기 후보 중복 제외·수집 호출

파일: `hyundap/db/documents_repo.py`, `upload_service.py`, 필요 시 `errors.py`. 현재 공개 인자는 최대한 유지하고 `find_by_hash` 전달 계약만 명시적으로 추가한다.

**메인 함수**

| 함수·구분 | 입력 → 반환 | 처리 계획·호출자 |
|---|---|---|
| `upload_document(actor, source_path, metadata, *, ingest_document=None, embedding=None, upload_dir=None)` · 수정 | 서버 actor·원본 경로·메타데이터 → 기존 결과 dict | 권한 → 공통 유형/파일 정보 → 후보 생성 → 원본 복사 → 실행 순서 유지. 후보 생성 전 오류는 예외, 생성 후 예상 실패는 기존 실패 결과 규칙을 유지한다. |
| `retry_upload(actor, version_id, *, ingest_document=None, embedding=None, source_path=None)` · 수정 | 서버 actor·실패/중단 후보 → 같은 ID의 결과 dict | 기존 prepare_retry와 원본 확인을 재사용하고 동일 후보에 대한 중복 콜백을 다시 구성한다. 신규 버전이나 작업을 만들지 않는다. |
| `create_document_version(actor, metadata, *, connection=None)` · 수정 | 저장 메타데이터 → document_id/version_id/job_id | 공통 문서 유형 검사를 사용한다. 회사 잠금 아래의 최종 중복 확인에는 제외 버전을 절대로 주지 않는다. 기존 세 보조 함수와 원자 저장을 유지한다. |
| `run_attempt(actor, ids, expected_active_id, ingest_function, backend)` · 수정 | 기존 실행 인자 → 성공/실패 결과 dict | 원본·수집 정보와 실제 해시를 확인하고 중복 콜백을 준비해 수집 단계에 전달한다. stage 추적·임베딩·검증·게시·실패 정리 책임은 유지한다. |

**보조 함수**

| 함수·구분 | 제안 입력 → 반환 | 구현할 내용 |
|---|---|---|
| `normalize_doc_type(value)` · 신규 제안, documents_repo에 배치 | 유형 값 → 허용 코드 str | None·빈/공백 문자열은 DEFAULT_DOC_TYPE. 문자열은 공백 제거 후 DOC_TYPE_CODES에 있는지 검사. bool·숫자·알 수 없는 코드는 INVALID_INPUT. DB와 업로드가 이 한 함수를 재사용한다. |
| `validate_metadata(metadata)` · 수정 | 원본 metadata dict → 새 저장 dict | 필수 필드 목록에서 doc_type의 '누락 즉시 거부'를 제거하고 normalize_doc_type 결과를 넣는다. ID·해시·크기·파일명·분류 검사는 유지한다. 기존 문서에 새 버전을 추가할 때 유형이 다르면 기존 정책대로 거부한다. |
| `prepare_upload_source(source_path, metadata, upload_dir)` · 수정 | 경로·입력 metadata → source/root/values | 서버가 경로·크기·해시를 결정하고 유형 정규화 결과를 values에 넣는다. SQL 연결을 열지 않는다. |
| `file_digest(path)` · 수정 제안 | Path → `(sha256, size)` | 1MiB while 읽기와 100MiB 상한 유지. 0바이트는 CORRUPTED_FILE, 초과는 FILE_TOO_LARGE로 명확히 전달하는 안을 제안한다. DbError의 허용 코드에 추가하되, 다른 구조 입력 오류는 INVALID_INPUT을 유지한다. 전체 형식→용량 검사 순서는 ingest/UI 담당과 별도 확정한다. |
| `find_duplicate_document(company_id, sha256, *, exclude_version_id=None, connection=None)` · 수정 제안 | 회사·해시·선택 서버 후보 ID → 중복 dict 또는 None | 기존 회사+해시 조건에 선택적으로 `v.id <> :exclude_version_id`를 더한다. 값은 바인딩한다. 삭제·실패 포함 정책과 다른 회사 허용은 유지한다. 옵션 생략 시 기존 동작과 같아야 한다. |
| `read_attempt_source(ids)` · 수정 제안 | DB 생성 ids → source/info/expected_hash | 원본이 DB 해시·크기와 같은지 확인한다. 내부 호출 반환값에 expected_hash를 추가해 중복 콜백이 원본과 다른 해시를 받는 상황도 거부할 수 있게 한다. 호출부도 함께 수정한다. |
| `build_duplicate_lookup(ids, company_id, expected_hash)` · 신규 제안, upload_service에 배치 | 서버 후보 정보 → `find_by_hash(company_id, file_hash)` 함수 | 현재 후보 ID·회사·해시를 함수 내부에 고정한다. 반환된 작은 함수는 전달된 회사/해시가 고정 값과 다르면 INVALID_INPUT, 같으면 해당 버전만 제외한 DB 조회를 수행한다. 사용자 입력으로 임의 제외 ID를 받지 않는다. |
| `ingest_and_save_chunks(source, info, ids, ingest_function, find_by_hash)` · 수정 제안 | 기존 인자+중복 조회 함수 → `(chunks, error_code)` | `ingest_function(str(source), info, find_by_hash=find_by_hash)`를 호출한다. 기존 dict/dataclass 변환과 save_ingest_chunks를 재사용한다. 콜백 불일치를 숨기려고 TypeError 뒤에 2개 인자로 다시 호출하지 않는다. |
| `get_ingest_function(function=None)` · 설명/계약 수정 | 주입 함수 또는 기본 모듈 → callable | 실제 수집기와 테스트 대역 모두 find_by_hash 키워드를 받도록 문서·테스트를 맞춘다. 함수를 준비하는 단계에서 임의의 수집기를 실행해 인자를 시험하지 않는다. |

`build_duplicate_lookup`가 반환하는 함수는 '함수 안에서 함수를 만들고 반환하는' 작은 클로저다. 익숙한 일반 함수와 dict만 사용하며, 고정할 값과 입력·반환을 한글로 설명한다. 범용 콜백 클래스·partial·시그니처 자동 추론은 추가하지 않는다.

신규 업로드 호출 순서:

```text
upload_document
  → prepare_upload_source → normalize_doc_type / file_digest
  → create_upload_candidate → create_document_version [회사 잠금·최종 중복 검사·원자 저장]
  → copy_original [SQL 밖]
  → run_attempt → read_attempt_source → build_duplicate_lookup
  → ingest_and_save_chunks [SQL 밖, find_by_hash 전달]
  → 기존 임베딩·완전성·게시 흐름
```

재시도는 `retry_upload → prepare_retry → 기존 벡터 정리/원본 확인 → 같은 run_attempt`로 연결한다. 예를 들어 현재 후보 V2의 해시 H를 검사하면 V2만 제외하고, 같은 회사의 다른 V1이 H를 가지고 있으면 중복이다. 신규 후보 생성 전에는 V2 제외 옵션 없이 검사하므로 실패·삭제 파일을 새 후보로 우회할 수 없다.

트랜잭션: 후보 생성은 기존 하나의 짧은 TX. 수집 중 콜백 조회는 별도 읽기 TX이고, 파일·수집 처리 전체를 하나의 SQL TX로 묶지 않는다. 이 조회가 동시 중복 저장을 막는 장치는 아니며 최종 회사 잠금과 DB 제약이 그 책임을 가진다.

완료 테스트: 기존 중복·자기 후보·다른 회사·삭제/실패 후보·동일 ID 재시도, 누락/공백/잘못된 유형, 다른 회사/해시를 콜백에 전달한 요청 거부, 콜백이 실제로 호출됐는지 확인한다. 기존 수집 대역 함수도 `find_by_hash=None`을 명시적으로 받도록 갱신한다. 다른 담당의 실제 수집 구현은 임의 수정하지 않는다.

### 6.3 미션 3 — 수집 보고서의 허용 항목과 저장

파일: `hyundap/db/jobs_repo.py`, `upload_service.py`. 새로운 보고서 저장 테이블이나 범용 serializer는 만들지 않는다.

**메인 함수**

| 함수·구분 | 입력 → 반환 | 처리 계획 |
|---|---|---|
| `normalize_report(report)` · 수정 | dict/dataclass → 안전한 새 dict | 기존 기본 정보·embedding 수치·실패 상세 검사를 유지하고 stage=ingest일 때 수집 설정·수치 검사를 추가한다. 다른 stage의 보고서 규칙은 넓히지 않는다. |
| `record_stage_result(job_id, report, *, connection=None)` · 재사용 | 작업 ID·보고서 → None | 현재 조회 → 대상/단계 검사 → 보고서 조립 → 저장 순서를 유지한다. 수집 전용 필드를 별도 SQL 컬럼으로 복제하지 않고 단계 JSON에 보관한다. |

**보조 함수**

| 함수·구분 | 입력 → 반환 | 구현할 내용 |
|---|---|---|
| `_report_ingest_details(report)` · 신규 제안 | ingest 보고서 dict → 허용 항목 dict | 파서 이름/버전·청킹 설정·섹션/청크 수의 타입과 범위를 검사한다. 없는 필드는 만들지 않고 존재하는 잘못된 값은 INVALID_INPUT. warning_count는 warnings를 저장하기 전 검사한 개수로 계산한다. |
| `_report_warning_summary(warnings)` · 신규 제안, 경고 계약 확정 후 | 경고 목록 → warning_count와 합의된 안전한 경고 코드/개수 dict | 현재 원문 경고는 길이 제한·개수 계산의 입력으로만 받고 DB에는 기본적으로 개수만 저장한다. 코드 목록을 생산할 주체와 규칙이 합의되면 알려진 코드만 추가한다. 경고 문자열에서 임의로 의미를 추측하지 않는다. |
| `_report_dict`, `_report_identity`, `_report_metrics`, `_report_failures`, `_failure_detail` · 재사용 | 기존 보고서 → 검증 결과 dict | 기존 변환·상태/식별자·embedding 수치·실패 정보 규칙을 그대로 사용한다. |
| `_read_stage_target`, `_check_stage_result`, `_merge_stage_reports`, `_store_stage_result` · 재사용 | 같은 DB 연결·작업·안전한 보고서 → 조회/검사/조립/저장 | 이전 성공 단계 변경 거부와 재시도 기록 보존을 유지한다. 저장 함수가 새 TX를 열지 않는다. |
| `save_ingest_chunks(ids, company_id, result)` · 수정 | 성공한 수집 결과 → 원래 Chunk 목록 | 실제 report를 normalize_report 경로로 전달한다. 청크 수가 실제 chunks 길이와 다르면 INVALID_INPUT으로 거부한다. 파서 옵션이 없는 기존 테스트 보고서는 필요한 필드만 선택적으로 받을 수 있게 한다. |

수집 허용 필드의 구체적 제안:

| 필드 | 검사·저장 제안 |
|---|---|
| parser | 현재 수집 계약의 pypdf/python-docx/python-pptx 중 하나. 허용 목록은 김혜리와 확정 |
| parser_version | 비어 있지 않은 짧은 버전 문자열. 길이·허용 문자 검사, 원시 패키지 오류는 저장하지 않음 |
| chunk_size / chunk_overlap | bool을 제외한 정수. size > 0, overlap >= 0. 둘 다 있으면 overlap < size |
| section_count / chunk_count | bool을 제외한 0 이상 정수. 성공한 수집 결과에서는 실제 목록과 불일치하지 않아야 함 |
| warnings → warning_count | 목록인지 확인 후 개수 저장. 원문 저장·상세 코드 추가는 합의 전까지 보류 |

호출 순서: `ingest_and_save_chunks → save_ingest_chunks → 청크/보고서 일치 검사 → record_stage_result → normalize_report → _report_ingest_details → 기존 단계별 저장`. 파서·섹션 추출·보고서 원본 생성은 ingest 담당이 수행한다.

완료 테스트: 실제 FUN-219 보고서 저장, 잘못된 수치/모순된 overlap 거부, chunk_count 불일치 거부, 원문 경고·본문·토큰 미저장, embedding dataclass 보고서 회귀, 성공 단계 보호, 이전 시도 이력 보존. schema 변경은 없다.

### 6.4 미션 4 — 현재 사용자·회사·인용 버전 검증 연결

파일: `hyundap/db/sessions_repo.py`, `documents_repo.py`, `hyundap/auth/permissions.py`. 기본 계획은 기존 메서드 재사용과 실제 호출 계약 검증이며, 인증 기능이나 중복된 권한 서비스를 새로 만들지 않는다.

**메인 함수**

| 함수·구분 | 입력 → 반환 | 연결 계획·실패 규칙 |
|---|---|---|
| `current_actor(actor, *, connection, lock=False)` · 재사용 | 서버 actor·DB 연결 → 현재 권한 actor dict | 세션 ID와 서버용 토큰의 소유 증명으로 현재 사용자·회사·역할을 재조회한다. role/company_id/auth_version 입력값을 권한 근거로 믿지 않는다. 만료/폐기/불일치는 기존 SESSION_EXPIRED 등 오류 규칙을 유지한다. |
| `get_allowed_company_ids(actor)` · 재사용 | 서버 actor → set[str] | 현재 권한으로 활성 회사 범위를 반환한다. 사용자 선택 회사와의 교집합 계산은 search가 담당한다. |
| `get_documents_with_active_version(document_ids, *, connection=None)` · 재사용 | ID 목록 → 11필드 행 목록 | search의 FUN-247에 전달한다. 빈 입력은 [], 없는 문서는 누락, 조회 장애는 DB_UNAVAILABLE. DTO 변환·is_visible은 search가 담당한다. |
| `verify_documents_access(actor, document_version_pairs)` · 재사용 | 서버 actor·인용 문서/버전 쌍 → None | 실제 답변에 쓰인 인용을 모두 확인한다. 허용 밖 회사는 ACCESS_DENIED, 삭제·활성 버전·공개/ready/operational 변경은 STATE_CHANGED. 잘못된 빈 ID/입력은 기존 INVALID_INPUT. |

**보조 함수**

| 함수·구분 | 입력 → 반환 | 유지할 책임 |
|---|---|---|
| `_session_credentials(actor)`, `token_hash(token)` | actor/서버 토큰 → 세션 ID·비교 해시 | 서버 문맥의 필수 값 검사와 비교값 생성. 로그인·비밀번호 해시는 처리하지 않음 |
| `_read_valid_session(connection, session_id, digest, lock)` | 같은 연결·소유 증명 → 세션 행 | 토큰 일치·미만료·미폐기 조건 조회 |
| `_read_active_user(connection, session, lock)` | 세션 행 → 사용자 행 | 계정 활성과 auth_version 일치 확인 |
| `_check_company_role(connection, user, lock)` | 사용자 행 → 표준 역할 str | 역할 매핑·소속/회사 활성 검사 |
| `allowed_companies(current, *, connection)` | 현재 actor → set[str] | 실제 허용 회사 조회. 비어 있으면 기존 접근 거부 |
| `_check_document_state(document, version_id)` | 문서 행·요청 버전 → None | 공개·삭제·활성 버전·ready·운영 데이터 조건을 차례로 검사 |

search 연결: `search_chunks/search → get_allowed_company_ids → 선택 회사 검증 → 벡터 검색 → get_documents_with_active_version → search의 공개/평가 자료 제외`. chatbot 연결: `ask → 실제 인용 문서/버전 목록 → verify_documents_access → 통과한 답변 반환`.

외부 인증 담당이 서버에서 actor를 만드는 경계는 아직 계약 확인 대상이다. UI의 token을 무조건 `_session_token`으로 이름만 바꾸거나 브라우저가 보낸 session_id/role을 신뢰하는 어댑터는 구현하지 않는다. 기존 인증 구현이 DB의 세션 구조와 다르면 실제 발급 주체와 합의한 연결만 추가한다.

완료 테스트: 회사·역할 위조, 만료/폐기/권한 버전 변경, 인용 문서 삭제·차단·새 활성 버전, 평가 자료 제외, 빈 version_id와 DB 장애. 실제 search가 아직 없으면 연결 검증을 미완료로 남기며 테스트 대역 성공만으로 완료 판정하지 않는다.

### 6.5 미션 5 — 실제 구성요소를 사용하는 통합 테스트 메서드

파일 제안: `hyundap/tests/test_db_p1_integration.py`. 기존 순수 DB/대역 테스트 파일을 대체하지 않고, 실제 수집기·로컬 Chroma·격리 MySQL 연결을 확인하는 시나리오만 둔다. 이 파일은 구현 계획이며 아직 생성하지 않았다.

**시나리오 진입 테스트**

| 테스트 메서드 제안 | 검증할 메인 함수·완료 조건 |
|---|---|
| `test_real_ingest_upload_publishes_supported_formats` | upload_document. 합성 PDF/DOCX/PPTX 각각 실제 수집기를 거쳐 원본·기준 목록·보고서·DB 포인터·Chroma 활성 상태 일치 |
| `test_duplicate_callback_excludes_only_current_candidate` | find_duplicate_document·ingest_and_save_chunks. 조회 콜백 실행, 자기 후보 제외, 다른 회사/해시 오용 거부, 기존 동일 회사 파일 중복 거부 |
| `test_ingest_report_is_persisted_without_raw_warning_text` | normalize_report·record_stage_result. 실제 파서/설정/개수는 저장, 합의 전 원문 경고·임의 비밀 필드는 제외 |
| `test_failed_candidate_retries_with_same_identifiers` | retry_upload. 의도적 수집/임베딩 실패 후 같은 문서·버전·작업과 보관 원본으로 복구 |
| `test_permission_or_active_version_change_blocks_return` | get_allowed_company_ids·verify_documents_access. 처리 중 권한/활성 버전 변경을 반환 전에 감지. 실제 search/chatbot 연결 준비 여부도 결과에 명시 |
| `test_activation_or_cleanup_failure_preserves_previous_version` | publish_version·recover_publication. 실제 Chroma 호출 경계에 실패를 주입해 기존 버전 복원·정리 재시도·최신 포인터 보호 확인 |

**테스트 보조 함수·준비 메서드**

| 함수·구분 | 입력 → 반환 | 책임 |
|---|---|---|
| `setUpClass` · 신규 테스트 준비 | 전용 테스트 DB 설정 → 클래스의 Engine | 127.0.0.1과 별도 DB 이름을 검사하고 빈 DB에 합성 기준 구조를 준비. 기존 테이블이 있으면 중단. 실제 공유 DB 사용 금지 |
| `setUp` · 신규 테스트 준비 | 각 테스트 시작 → 격리 파일/Chroma 경로·합성 계정 | 환경 설정·데이터 경로를 테스트 영역으로 고정. 실제 임베딩 API를 대역으로 바꾸고 로컬 Chroma·실제 수집기는 유지 |
| 기존 `session_fixture(engine, user_id, company_id)` · 재사용 | 합성 사용자 ID → 서버 actor dict | 테스트 SQL로만 세션을 준비한다. 운영 로그인/세션 발급 메서드는 만들지 않는다. |
| `write_sample_document(directory, extension)` · 신규 테스트 보조 제안 | 임시 폴더·지원 확장자 → 합성 파일 Path | 형식별 라이브러리로 짧은 테스트 본문 파일 생성. 회사 원본·목업 자료를 저장소에 복사하지 않는다. |
| 기존 FakeModel 또는 기존 fake_vector · 재사용 | 텍스트 목록 → 결정적인 테스트 벡터 | 네트워크 API 없이 같은 입력에 같은 결과를 주는 기존 대역 재사용. 새 벡터 저장소 대역은 만들지 않음 |
| unittest cleanup 등록 · 재사용 | 생성한 Engine/임시 경로/설정 패치 → 정리 | 연결·임시 파일·패치를 테스트 종료 시 정리. 테스트용 DB는 격리 컨테이너를 종료해 폐기하며 운영 데이터 삭제 SQL을 쓰지 않음 |

개별 assertion마다 보조 함수를 만들지는 않는다. 테스트 본문에서 DB의 활성 ID, 버전 상태, 작업 기록과 Chroma의 ID/활성 상태를 나란히 확인해 시나리오를 읽을 수 있게 한다. 실제 MySQL·수집기·Chroma가 하나라도 대역이면 결과 기록에 구분한다.

## 7. 구현 착수 전 확정할 메서드 계약

| 결정 | 우리 측 제안 | 협의 담당 |
|---|---|---|
| ingest의 중복 조회 콜백 | find_by_hash 키워드를 실제 수집기와 테스트 대역 모두 받음. 콜백 생략으로 동작을 우회하지 않음 | 김혜리 |
| 제외 후보의 신뢰 경계 | DB 생성 ids·서버 확인 회사/해시만 고정하고 현재 버전 하나만 제외. 최종 생성 검사는 제외 없이 유지 | 김혜리·DB 담당 |
| 유형·파일 오류 | 공통 DEFAULT_DOC_TYPE/DOC_TYPE_CODES, 0바이트/용량 오류 코드 및 검증 순서 통일 | 이성민·김혜리·UI 담당 |
| 보고서와 경고 | 실제 FUN-219 필드 저장, 원문 경고는 합의 전 개수만 저장 | 김혜리·DB 담당 |
| actor의 제공 주체 | 외부 인증 담당이 서버에서 제공. DB가 로그인·비밀번호·세션 발급을 대신하지 않음 | 인증 담당·이준영·소용우 |

실측 없이 스키마를 바꾸거나, 협의되지 않은 인자를 실제 다른 담당 코드에 추가하지 않는다. 먼저 미션 2·3의 계약과 테스트 입력을 확정한 뒤 메인 함수 → 책임별 보조 함수 → 통합 시나리오 순서로 구현한다.
