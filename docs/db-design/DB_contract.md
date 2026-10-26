# DB·인증 연동 계약 v1 — #118

상태: 2026-10-26 사용자 지시에 따라 우리 측 제안을 채택했다. 각 팀원이 별도로 승인했다는 의미는 아니다. 미정 함수명·타입·정책은 아래 계약으로 결정한다. 구현은 #120~#122이며 이 문서는 실행 코드가 아니다.

기준: `문서검색_통합_기능명세서.xlsm.xlsx`(개요 B4: 2026-10-26 11:10), `DBA_preparation.txt`, `DBA_query_catalog.sql`. P0는 DB 선행 작업 순서이며 명세의 P1/P2/P3를 변경하지 않는다.

## 1. 책임과 채택 결정

| 항목 | 채택 내용 | 명세 근거 / 연계 담당 |
|---|---|---|
| DB·auth 책임 | 전재경은 DB 저장·조회·인증·완전성 판정·업로드 처리 연계를 구현한다. WBS #71~#73의 소용우 배정은 기존 기록으로 보존하고 본 계약의 구현 분담을 우선한다. 기존 이슈를 자동 재배정하지 않는다. | 제약_운영 A8:G9 / 소용우 |
| 검색 책임 | 이준영은 입력 검증·선택 회사 교집합·DocumentInfo 변환·is_visible·결과 조립을 구현한다. DB는 원시 행을 반환한다. | FUN-247/248/253, 참고_처리순서 A13:D14 |
| 수집 책임 | 김혜리는 형식·크기·암호화·손상 검증, SHA-256 계산, 파싱·정규화·청킹을 구현한다. | F60~F72 / #113 |
| 벡터 책임 | 박영준은 embed_and_store·get_version_chunk_ids·activate_version·delete_vectors 내부를 구현한다. DB는 호출하고 결과를 검증·저장한다. | F107, F110~F112 |
| 화면·공통·챗봇 | 박경환은 UI 연결·표시, 이성민은 공통 설정·공통 Chunk, 소용우는 답변·요약·오류 변환·최종 반환 제어를 구현한다. | FUN-157/168/407, MOD-037/121 |

| 결정 대상 | 채택안 | 원문 대비 |
|---|---|---|
| ID | 기존 PK `id VARCHAR(36)`를 유지하고 UUID 문자열을 자르지 않는다. DTO의 document_id로 alias한다. 모든 새 FK는 참조 키의 문자셋·collation을 그대로 사용한다. 실제 기존 키가 다르면 준비 도구가 중단한다. | 데이터정의 A6:I7의 VARCHAR(32)에 대한 우리 측 변경안 |
| 역할 | 앱은 platform_admin/company_admin/employee. 기존 SUPER_ADMIN/COMPANY_ADMIN/EMPLOYEE는 입출력 경계에서 각각 매핑하고 저장 enum을 강제 변환하지 않는다. 알 수 없는 역할은 거부한다. | F81, 기존 호환성 보완 |
| 로그인 | identifier는 앞뒤 공백 제거·소문자화한 이메일이다. 이메일은 유일해야 한다. password는 공백 제거하지 않는다. 기존 OAuth 계정에 해시가 없으면 비밀번호 로그인을 거부한다. 자동 비밀번호 생성·재설정은 범위 밖이다. | FUN-157의 아이디 구체화 |
| 문서 유형 | DB는 VARCHAR(50)의 업무 코드 저장. 공통 DOC_TYPES는 manual/policy/guide/other로 제안 채택한다. MIME과 분리하며 미분류 기존 문서는 other. search가 코드 검증하고 공통 설정 담당이 목록 제공한다. | 미정_목록 A123:G123 구체화 / 이준영·이성민 |
| 해시 중복 | 같은 회사+같은 SHA-256은 삭제·실패 포함 중복이다. 실패는 동일 후보 ID로 명시적 재시도하며 새 후보를 만들지 않는다. 다른 회사는 허용한다. 삭제 후 같은 파일의 새 업로드는 P0에서 허용하지 않는다. | F63의 삭제·실패 경계 구체화 / 김혜리 |
| 버전·청크 | version_no는 문서 안에서 1부터 증가. chunk_index는 버전 안에서 0부터 연속. chunk_id는 `{version_id}:{chunk_index:05d}`. 최대 100,000개(0~99,999); DB ID 길이는 64. 해시는 정규화된 최종 청크 text의 UTF-8 SHA-256. | 제약_운영 A34:G34 및 관련 미정 항목 구체화 / 김혜리·박영준 |
| 세션 | 무작위 32바이트 이상 토큰은 로그인 결과에서만 반환, DB에는 SHA-256만 저장. 수명 8시간, 자동 연장 없음. user/session auth_version 일치·계정 활성·만료·폐기를 매 요청 확인한다. 역할 변경은 users.auth_version 증가로 무효화한다. | FUN-157, 데이터정의 A117:I117의 정책 구체화 |
| 관리 단계 | F40/F41의 기능명세 P3를 채택. P0는 처리 기록 저장만 구현하고 관리 목록 UI·확장 조회·역할 변경 기능은 후속으로 둔다. | 기능명세 A9:N10과 실행경로/제약의 P2 충돌 해소 |
| Chroma 전환 | 현재 명세 순서인 DB 포인터 전환 → activate_version 호출 유지. 분리 ON/OFF API는 요구하지 않는다. 실패 시 답변·검색은 DB 조건으로 차단하고 재시도/조건부 복구한다. 일시 누락 가능성은 수용한다. | F110, FUN-316 / 박영준 |

## 2. 데이터·오류 계약

Python 3.11 일반 함수와 dict/list를 사용한다. 문서의 서명은 계약 표기이며 함수가 이미 구현됐다는 뜻은 아니다. 타입 모델이 필요하면 기존 소유자의 모델을 사용한다. DB는 search.models나 UI를 import하지 않는다. 모든 DATETIME(6)은 UTC로 기록하고 Python 경계에서 UTC로 해석한다. UI가 KST로 표시한다.

`UserContext`의 필수 값은 user_id:str, company_id:str|None, role:str, session_id:str, auth_version:int이다. platform_admin만 company_id=None을 허용한다. 전달받은 role/company_id를 권한의 근거로 신뢰하지 않고 session_id로 서버 측 현재 상태를 조회한다.

03·04 구현에서는 세션 ID 복사만으로 인증되지 않도록 서버 내부 dict에 `_session_token`을 추가했다. 이 비밀 필드는 DB 해시와 비교하는 소유 증명이며 UI 표시·로그·JSON 응답·프롬프트에서 제외한다. 각 요청은 서버에 보관한 session_token으로 `auth.login.authenticate(token)`을 호출해 문맥을 다시 만든다. 브라우저가 보낸 actor dict를 인증된 문맥으로 사용하지 않는다. 다른 모듈에 직렬화할 공개 사용자 정보와 이 서버 인증 문맥을 구분한다.

로그인 결과: `{actor: UserContext, session_token: str, expires_at: datetime}`. UI는 토큰을 서버 측 세션에 보관하고 브라우저 표시·로그·모델 프롬프트에 넣지 않는다.

### DocumentInfo 원시 행 매핑 — 정확히 11필드

| 반환 키 | 물리 컬럼 | Python 타입 |
|---|---|---|
| document_id | documents.id | str |
| company_id | documents.company_id | str |
| company_name | companies.name | str |
| doc_type | documents.doc_type | str |
| publication_status | documents.publication_status | str |
| deleted_at | documents.deleted_at | datetime 또는 None |
| active_version_id | documents.active_version_id | str 또는 None |
| version_no | document_versions.version_no | int 또는 None |
| version_status | document_versions.version_status | str 또는 None |
| data_category | document_versions.data_category | str 또는 None |
| file_name | document_versions.file_name | str 또는 None |

LEFT JOIN에서 활성 버전이 없는 행도 반환한다. 따라서 버전 필드는 nullable이다. 이준영은 nullable DTO로 받고 is_visible에서 제외한다. 원문의 non-null 버전 필드를 안전하게 구체화한 변경이다. 조회에 성공한 실제 행만 반환하며 없는 문서 ID는 누락한다.

| 오류 소유자 | 코드 | 처리 |
|---|---|---|
| auth | INVALID_CREDENTIALS, SESSION_EXPIRED, ACCESS_DENIED | AccessError(code). 계정 존재 여부·비밀값은 메시지에 포함하지 않음 |
| auth 재검증 | STATE_CHANGED | AccessError(code='STATE_CHANGED'). 소용우가 ChatbotError(STATE_CHANGED)로 변환 |
| db | DB_UNAVAILABLE, DUPLICATE_FILE, CONFLICT, INVALID_STATE, INVALID_INPUT | DbError(code). 원시 DB 예외는 호출자·UI에 노출하지 않음 |
| 업로드·게시 | INTEGRATION_UNAVAILABLE, PROCESSING_FAILED, INCOMPLETE_INDEX, ACTIVATION_FAILED, RECOVERY_REQUIRED | 외부 함수 미준비·적재 불완전·게시 실패·복구 필요를 구분하며 원시 외부 예외를 숨김 |
| ingest/embedding | 기존 모듈 코드 유지 | DB는 저장 가능한 보고서로 기록. search/chatbot은 자기 모듈의 오류로 변환 |

DB 장애는 DB_UNAVAILABLE, search는 SEARCH_UNAVAILABLE로 변환한다. 없는 ID·빈 결과와 DB 장애를 혼동하지 않는다. 예상하지 못한 프로그래밍 오류는 정상 실패로 숨기지 않는다.

## 3. 공개 함수 계약

| 제공 함수 | 입력 → 출력 | 호출자 / 검증 |
|---|---|---|
| auth.login.login(identifier, password) | str,str → 로그인 결과 dict | 박경환 FUN-157. 계정·회사 활성, 해시·세션 검증. 비밀번호 해시 반환 금지 |
| auth.permissions.get_allowed_company_ids(actor) | UserContext → set[str] | 이준영 FUN-253. 활성 회사만 반환, 허용 범위가 없으면 ACCESS_DENIED. 선택 회사 인자는 받지 않음 |
| auth.permissions.verify_documents_access(actor, refs) | list[tuple[document_id,version_id]] → None | 소용우 FUN-407. 빈 refs는 INVALID_INPUT. 현재 세션·권한과 모든 참조를 일괄 재확인. 하나라도 실패하면 예외 |
| db.documents_repo.find_duplicate_document(company_id, sha256) | str,str → {document_id,version_id,version_status,deleted_at} 또는 None | 김혜리 FUN-206. 내부 서버 처리에서 이미 권한 확인된 회사만 사용. 64자 소문자 hex 검증 |
| db.documents_repo.get_documents_with_active_version(document_ids) | list[str] → list[dict] | 이준영 FUN-247. 위 11필드. 빈 목록은 [], 없는 ID 누락. 서버 내부 조회이며 별도 공개 API 아님 |
| db.documents_repo.create_document_version(actor, metadata) | dict → {document_id,version_id,job_id} | 업로드 서비스. metadata: company_id,doc_type,file_name,storage_path,sha256,file_size,data_category,document_id(기존 문서면 지정). 권한·동시 중복·버전 번호 확인 후 짧은 TX |
| db.versions_repo.list_document_versions(actor, document_id) | str → list[dict] | 회사 권한이 있는 관리자용 버전 관리. 직원에게 과거·비공개 버전을 노출하지 않음. version_no 오름차순, 내부 경로 제외 |
| db.chunks_repo.save_manifest(version_id, chunks) | list[{id,chunk_index,content_hash}] → None | DB 처리 흐름. 소속 후보 확인, 청크 ID 규칙·연속 순번·중복·해시 검증. 동일 재시도는 허용, ID 같고 내용 다르면 CONFLICT |
| db.chunks_repo.get_expected_chunk_ids(version_id) | str → list[str] | 완전성 검사. chunk_index 순서. 단순 개수만 비교하지 않음 |
| db.jobs_repo.record_stage_result(job_id, report) | dict 또는 dataclass → None | 업로드 서비스. 식별자·개수·모델명·시간·상태·안전한 실패 코드만 단계별 JSON으로 보관. 원시 메시지·본문·임의 키 제외. P0는 단일 작업 실행자 |
| db.version_service.validate_completeness(document_id, version_id) | str,str → {ok,expected_count,actual_count,missing_ids,unexpected_ids} | DB가 embedding.get_version_chunk_ids 호출. 실패·빈 목록·중복·누락·초과는 통과 불가 |
| db.version_service.publish_version(actor, document_id, candidate_id, expected_active_id) | str,str,str 또는 None → None | ready·operational·완전성·권한 확인, 조건부 포인터 전환. CAS 충돌은 CONFLICT |

물리 경로는 저장소 루트를 실행 기준으로 사용하며 sys.path나 PYTHONPATH를 조작하지 않는다. `db.get_documents_with_active_version`이라는 원문 가정명은 위 documents_repo 경로로 확정한다. 구체적 클래스 구현은 #120/#121에서 제공한다.

인증·저장 함수는 db02 작업 트리에서 구현했다. 함수의 선택 인자 `connection=`은 내부 서비스가 하나의 트랜잭션으로 묶을 때만 사용한다. 제공한 연결은 호출자가 커밋·롤백하고, 생략하면 함수가 자체 TX를 관리한다. 실제 공유 DB의 기존 NOT NULL 컬럼·작업 상태 enum은 #119 실측 전이므로 호환 적용을 완료했다고 보지 않는다. 게시·벡터 연계는 아래 #122 계약에서 다룬다.

#122의 DB 업로드·완전성·게시·복구 서비스도 db02 로컬에서 구현했다. 상세 API·원본 저장·재시도·복구 원장은 [WORKFLOW_122.md](WORKFLOW_122.md)를 따른다. 입력 Chunk 객체를 보존하고 실제 VectorHit의 소속·본문 해시를 확인한다. 실제 팀 수집/embedding 함수는 아직 뼈대이므로 기본 연동은 미준비 오류로 거부한다. 테스트 대역의 성공을 실제 외부 연동 완료로 간주하지 않는다.

`IngestResult`는 김혜리 소유이며 status(success/failed), chunks, report, error_code, error_message를 유지한다. `EmbedStoreReport`는 박영준 소유이며 document_id,version_id,company_id,model_name,total,skipped_existing,embedded,stored,truncated_count,failures,status,elapsed_sec를 유지한다. `partial`은 게시 실패이며 재시도에서 skipped_existing을 포함한 실제 ID 집합을 검증한다.

최신 develop의 common.schemas.Chunk는 chunk_id/text 필드를 가진다. #122의 DB 처리 흐름에서 Chunk.chunk_id를 manifest의 id로 매핑하고 최종 text의 UTF-8 SHA-256을 content_hash로 계산한다. 공통 Chunk에 DB 전용 필드를 추가하지 않는다. 원본 기본 저장 위치는 common.config.get_settings().upload_dir이며 DB에는 해당 경로 정책에 따른 내부 storage_path를 저장한다.

### 호출 예시 (계약 예시, #120~#122 구현 후 실행)

```python
from hyundap.auth.login import login
from hyundap.auth.permissions import get_allowed_company_ids, verify_documents_access
from hyundap.db.documents_repo import find_duplicate_document, get_documents_with_active_version

result = login(identifier, password)  # dict: actor, session_token, expires_at
actor = result['actor']
allowed = get_allowed_company_ids(actor)  # set[str]; 검색 담당이 선택 회사와 교집합 계산
match = find_duplicate_document(company_id, sha256)  # dict 또는 None; ingest가 중복 오류로 변환
rows = get_documents_with_active_version(document_ids)  # list[dict]; search가 DTO로 변환
verify_documents_access(actor, cited_pairs)  # None 또는 AccessError/DbError
```

성공: 빈 문서 ID 목록은 []이다. 실패: 권한 밖 회사는 ACCESS_DENIED, 인용 후 활성 버전 변경은 STATE_CHANGED, DB 장애는 DB_UNAVAILABLE이다. chatbot은 예외 발생 시 생성된 답변 전체를 반환하지 않는다.

## 4. 상태·트랜잭션·실패 계약

| 대상 | 상태 전이 | 조건·실패 처리 |
|---|---|---|
| documents | draft → published / published → blocked | 게시 시 ready·operational 활성 버전 필수. deleted_at이 있으면 항상 접근 거부. 처리 실패 때문에 기존 공개 문서를 draft로 내리지 않음 |
| document_versions | processing → ready 또는 failed / failed → processing | ready는 양수 예상 수·완전한 manifest·벡터 ID 집합 검증 후 설정. failed 재시도는 같은 ID, 단일 실행자. ready 버전의 파일·manifest는 불변 |
| index_jobs | queued → running → succeeded 또는 failed / failed → queued | stage는 received/ingest/embedding/verify/publish/complete. 재시도는 같은 후보, attempts 증가. 단일 프로세스가 순차 실행하고 중복 실행은 거부 |
| sessions | 유효 → 만료 또는 폐기 | auth_version 불일치도 무효. 해시 없는 기존 계정과 토큰 없는 기존 세션은 자동 인증하지 않음 |

업로드의 권한·중복 확인과 문서/후보/작업 생성은 짧은 트랜잭션으로 묶는다. UNIQUE(company_id,sha256)는 동시에 도착한 새 후보의 최종 중복을 차단한다. 기존 문서 행 잠금 안에서 version_no를 할당한다. 파일 저장 실패는 후보·작업을 failed로 기록하고 기존 활성 버전은 유지한다. 외부 파일·ingest·Chroma 호출 중 SQL 행 잠금을 유지하지 않는다.

게시: 외부 완전성 확인 → 문서 잠금 → 후보의 ready/operational·회사·권한·불변 manifest 재확인 → expected_active_id 비교 후 SQL 포인터 변경·작업 단계 기록·커밋 → activate_version 호출 → 작업 성공. 최초 게시는 publication_status도 published로 전환한다. blocked/deleted 문서는 일반 게시 함수로 되살리지 않는다.

활성화 실패는 실패 사유와 재시도 대상을 기록한다. 포인터가 여전히 후보이고 이전 버전이 현재도 적격일 때만 이전 포인터로 조건부 복구한다. 초기 게시라면 draft/NULL로 복구한다. DB 복구와 Chroma 이전 버전 재활성화도 독립적으로 실패할 수 있으므로 작업 기록을 남긴다. 더 최신 포인터를 덮지 않는다. 정상 복구 전에는 조회가 누락될 수 있지만 부적격 버전을 반환하지 않는다. ON/OFF 분리 API나 무중단 보장은 추가하지 않는다.

DB 기준 공개 조건은 published, deleted_at IS NULL, hit version=active_version_id, ready, operational, 허용 회사, hit 회사=DB 회사이다. 회사·계정 활성 상태도 auth에서 확인한다. 검색·답변의 최종 제어 코드는 각 담당자 책임이다.

## 5. 이행·검증·남은 범위

스키마 준비는 #119, 인증 구현은 #120, 저장 함수는 #121, 처리 흐름은 #122다. 레거시 문서는 새 컬럼 기본값 draft/active_version_id=NULL로 격리한다. 기존 파일·해시·벡터의 의미를 추측해 ready 버전으로 자동 변환하지 않는다. #122에서 명시적으로 가져오기 전에는 해당 회사의 신규 업로드를 막아 레거시 해시 중복 누락을 방지한다. 스키마 버전 4는 앱 전체 구현 완료나 데이터 이행 완료를 뜻하지 않는다.

검증은 로그인 정상/실패·회사 격리·빈 ID·일괄 조회 장애·동시 해시 중복·다른 회사 활성 버전 지정·청크 순번/ID 중복·권한 변경 재검증·부분 적재·게시 경쟁·외부 호출 실패를 대상으로 한다. #118은 계약 확정만 완료하며 런타임 검증 성공을 주장하지 않는다.

기존 WBS 담당 기록, 명세의 VARCHAR(32), DTO non-null 버전 필드, P2/P3 충돌은 위 채택안으로 구현 방향을 결정했다. 팀원에게 별도 메시지를 발송하거나 기존 WBS를 변경하지 않았다. 남은 외부 조건은 실제 DB 접속·점검과 다른 모듈 구현 제공이며, 협의 대기 항목으로 다시 분류하지 않는다.
