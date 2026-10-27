# DB·접근 권한 연동 계약 v1 — #118

상태: 2026-10-26 사용자 지시에 따라 우리 측 제안을 채택했다. 각 팀원이 별도로 승인했다는 의미는 아니다. 미정 함수명·타입·정책은 아래 계약으로 결정한다. 구현은 #120~#122이며 이 문서는 실행 코드가 아니다.

기준: `문서검색_통합_기능명세서.xlsm.xlsx`(개요 B4: 2026-10-26 11:10), `DBA_preparation.txt`, `DBA_query_catalog.sql`. P0는 DB 선행 작업 순서이며 명세의 P1/P2/P3를 변경하지 않는다.

## 1. 책임과 채택 결정

2026-10-27 코드 배치 결정: DB는 기존 일반 함수·dict 중심 구조를 유지한다.
신규 코드는 기능 책임에 맞는 모듈에 배치하고, 클래스가 없으면 모듈 함수로 명시한다.
메인/보조 역할과 호출 관계를 [DB 명세의 코드 배치 목록](../../hyundap/db/docs/spec.md)에 기록한다.
운영 함수와 실제 클래스가 있는 시험 어댑터·테스트는 구분한다. 형식적인 클래스 래퍼는 추가하지 않는다.

후속 구조 개선안은 [DB_V4_SIMPLIFICATION_PLAN.md](DB_V4_SIMPLIFICATION_PLAN.md)에 별도 저장한다.
현재 호환 코드 유지와 최종 v4 단일화 제안을 구분하며, 운영 DDL 적용은 별도 검토 대상이다.

| 항목 | 채택 내용 | 명세 근거 / 연계 담당 |
|---|---|---|
| DB·권한 책임 | 전재경은 DB 저장·조회·접근 권한 재검증·완전성 판정·업로드 처리 연계를 구현한다. 로그인·비밀번호 해싱/검증·세션 발급/폐기는 담당 범위에서 제외한다. 기존 WBS 배정은 자동 변경하지 않는다. | 제약_운영 A9:G9 / 외부 인증 담당과 연계 |
| 검색 책임 | 이준영은 입력 검증·선택 회사 교집합·DocumentInfo 변환·is_visible·결과 조립을 구현한다. DB는 원시 행을 반환한다. | FUN-247/248/253, 참고_처리순서 A13:D14 |
| 수집 책임 | 김혜리는 형식·크기·암호화·손상 검증, SHA-256 계산, 파싱·정규화·청킹을 구현한다. | F60~F72 / #113 |
| 벡터 책임 | 박영준은 embed_and_store·get_version_chunk_ids·activate_version·delete_vectors 내부를 구현한다. DB는 호출하고 결과를 검증·저장한다. | F107, F110~F112 |
| 화면·공통·챗봇 | 박경환은 UI 연결·표시, 이성민은 공통 설정·공통 Chunk, 소용우는 답변·요약·오류 변환·최종 반환 제어를 구현한다. | FUN-157/168/407, MOD-037/121 |

| 결정 대상 | 채택안 | 원문 대비 |
|---|---|---|
| ID | 기존 PK `id VARCHAR(36)`를 유지하고 UUID 문자열을 자르지 않는다. DTO의 document_id로 alias한다. 모든 새 FK는 참조 키의 문자셋·collation을 그대로 사용한다. 실제 기존 키가 다르면 준비 도구가 중단한다. | 데이터정의 A6:I7의 VARCHAR(32)에 대한 우리 측 변경안 |
| 역할 | 앱은 platform_admin/company_admin/employee. 기존 SUPER_ADMIN/COMPANY_ADMIN/EMPLOYEE는 입출력 경계에서 각각 매핑하고 저장 enum을 강제 변환하지 않는다. 알 수 없는 역할은 거부한다. | F81, 기존 호환성 보완 |
| 문서 유형 | DB는 VARCHAR(50)의 업무 코드 저장. 공통 DOC_TYPES는 manual/policy/guide/other로 제안 채택한다. MIME과 분리하며 미분류 기존 문서는 other. search가 코드 검증하고 공통 설정 담당이 목록 제공한다. | 미정_목록 A123:G123 구체화 / 이준영·이성민 |
| 해시 중복 | 같은 회사+같은 SHA-256은 삭제·실패 포함 중복이다. 실패는 동일 후보 ID로 명시적 재시도하며 새 후보를 만들지 않는다. 다른 회사는 허용한다. 삭제 후 같은 파일의 새 업로드는 P0에서 허용하지 않는다. | F63의 삭제·실패 경계 구체화 / 김혜리 |
| 버전·청크 | version_no는 문서 안에서 1부터 증가. chunk_index는 버전 안에서 0부터 연속. chunk_id는 `{version_id}:{chunk_index:05d}`. 최대 100,000개(0~99,999); DB ID 길이는 64. 해시는 정규화된 최종 청크 text의 UTF-8 SHA-256. | 제약_운영 A34:G34 및 관련 미정 항목 구체화 / 김혜리·박영준 |
| 세션 읽기 검증 | 외부 인증 담당이 관리한 세션의 토큰 SHA-256·auth_version·계정 활성·만료·폐기를 확인한다. 발급·연장·폐기는 구현하지 않는다. 실제 인증 연동은 미완료다. | 데이터정의 A117:I117 및 DB 접근 보호 |
| 관리 단계 | F40/F41의 기능명세 P3를 채택. P0는 처리 기록 저장만 구현하고 관리 목록 UI·확장 조회·역할 변경 기능은 후속으로 둔다. | 기능명세 A9:N10과 실행경로/제약의 P2 충돌 해소 |
| Chroma 전환 | 현재 명세 순서인 DB 포인터 전환 → activate_version 호출 유지. 분리 ON/OFF API는 요구하지 않는다. 실패 시 답변·검색은 DB 조건으로 차단하고 재시도/조건부 복구한다. 일시 누락 가능성은 수용한다. | F110, FUN-316 / 박영준 |

## 2. 데이터·오류 계약

Python 3.11 일반 함수와 dict/list를 사용한다. 문서의 서명은 계약 표기이며 함수가 이미 구현됐다는 뜻은 아니다. 타입 모델이 필요하면 기존 소유자의 모델을 사용한다. DB는 search.models나 UI를 import하지 않는다. 모든 DATETIME(6)은 UTC로 기록하고 Python 경계에서 UTC로 해석한다. UI가 KST로 표시한다.

`UserContext`의 필수 값은 user_id:str, company_id:str|None, role:str, session_id:str, auth_version:int이다. platform_admin만 company_id=None을 허용한다. 전달받은 role/company_id를 권한의 근거로 신뢰하지 않고 session_id로 서버 측 현재 상태를 조회한다.

DB 접근 검사에서는 세션 ID 복사만으로 접근하지 못하도록 서버 내부 dict의 `_session_token`을 DB 해시와 비교한다. 이 비밀 필드는 UI 표시·로그·JSON 응답·프롬프트에서 제외한다. actor는 외부 인증 담당이 서버에서 구성해 전달하고 `sessions_repo.current_actor`는 현재 세션·계정·회사 상태를 읽어 재검증한다. 인증 담당의 실제 어댑터 연결은 미완료다. 브라우저가 보낸 actor dict를 인증된 문맥으로 사용하지 않는다.


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
| auth | SESSION_EXPIRED, ACCESS_DENIED | AccessError(code). 계정 존재 여부·비밀값은 메시지에 포함하지 않음 |
| auth 재검증 | STATE_CHANGED | AccessError(code='STATE_CHANGED'). 소용우가 ChatbotError(STATE_CHANGED)로 변환 |
| db | DB_UNAVAILABLE, DUPLICATE_FILE, CONFLICT, INVALID_STATE, INVALID_INPUT | DbError(code). 원시 DB 예외는 호출자·UI에 노출하지 않음 |
| 업로드·게시 | INTEGRATION_UNAVAILABLE, PROCESSING_FAILED, INCOMPLETE_INDEX, ACTIVATION_FAILED, RECOVERY_REQUIRED | 외부 함수 미준비·적재 불완전·게시 실패·복구 필요를 구분하며 원시 외부 예외를 숨김 |
| ingest/embedding | 기존 모듈 코드 유지 | DB는 저장 가능한 보고서로 기록. search/chatbot은 자기 모듈의 오류로 변환 |

DB 장애는 DB_UNAVAILABLE, search는 SEARCH_UNAVAILABLE로 변환한다. 없는 ID·빈 결과와 DB 장애를 혼동하지 않는다. 예상하지 못한 프로그래밍 오류는 정상 실패로 숨기지 않는다.

## 3. 공개 함수 계약

| 제공 함수 | 입력 → 출력 | 호출자 / 검증 |
|---|---|---|
| auth.permissions.get_allowed_company_ids(actor) | UserContext → set[str] | 이준영 FUN-253. 활성 회사만 반환, 허용 범위가 없으면 ACCESS_DENIED. 선택 회사 인자는 받지 않음 |
| auth.permissions.verify_documents_access(actor, refs) | list[tuple[document_id,version_id]] → None | 소용우 FUN-407. 빈 refs는 INVALID_INPUT. 현재 세션·권한과 모든 참조를 일괄 재확인. 하나라도 실패하면 예외 |
| db.documents_repo.find_duplicate_document(company_id, sha256, *, exclude_version_id=None, connection=None) | str,str → {document_id,version_id,version_status,deleted_at} 또는 None | 권한 확인된 서버 처리 전용. 제외 ID는 서버가 확정한 현재 후보이며 같은 회사·해시로 존재해야 함. 불일치 INVALID_INPUT. 삭제·실패 포함, 옵션 생략 시 기존 조회 유지. 후보 최종 생성에서는 제외하지 않음 |
| db.documents_repo.get_documents_with_active_version(document_ids) | list[str] → list[dict] | 이준영 FUN-247. 위 11필드. 빈 목록은 [], 없는 ID 누락. 서버 내부 조회이며 별도 공개 API 아님 |
| db.documents_repo.create_document_version(actor, metadata) | dict → {document_id,version_id,job_id} | 업로드 서비스. metadata: company_id,doc_type,file_name,storage_path,sha256,file_size,data_category,document_id(기존 문서면 지정). 권한·동시 중복·버전 번호 확인 후 짧은 TX |
| db.versions_repo.list_document_versions(actor, document_id) | str → list[dict] | 회사 권한이 있는 관리자용 버전 관리. 직원에게 과거·비공개 버전을 노출하지 않음. version_no 오름차순, 내부 경로 제외 |
| db.chunks_repo.save_manifest(version_id, chunks) | list[{id,chunk_index,content_hash}] → None | DB 처리 흐름. 소속 후보 확인, 청크 ID 규칙·연속 순번·중복·해시 검증. 동일 재시도는 허용, ID 같고 내용 다르면 CONFLICT |
| db.chunks_repo.get_expected_chunk_ids(version_id) | str → list[str] | 완전성 검사. chunk_index 순서. 단순 개수만 비교하지 않음 |
| db.jobs_repo.record_stage_result(job_id, report) | dict 또는 dataclass → None | 업로드 서비스. 식별자·개수·모델명·시간·상태·안전한 실패 코드만 단계별 JSON으로 보관. 원시 메시지·본문·임의 키 제외. P0는 단일 작업 실행자 |
| db.version_service.validate_completeness(document_id, version_id) | str,str → {ok,expected_count,actual_count,missing_ids,unexpected_ids} | DB가 embedding.get_version_chunk_ids 호출. 실패·빈 목록·중복·누락·초과는 통과 불가 |
| db.version_service.publish_version(actor, document_id, candidate_id, expected_active_id) | str,str,str 또는 None → None | ready·operational·완전성·권한 확인, 조건부 포인터 전환. CAS 충돌은 CONFLICT |

물리 경로는 저장소 루트를 실행 기준으로 사용하며 sys.path나 PYTHONPATH를 조작하지 않는다. `db.get_documents_with_active_version`이라는 원문 가정명은 위 documents_repo 경로로 확정한다. 구체적 클래스 구현은 #120/#121에서 제공한다.

DB 접근 재검증·저장 함수를 구현했다. 로그인·비밀번호 처리와 세션 발급/폐기 코드는 DB 작업에서 제거했다. 함수의 선택 인자 `connection=`은 내부 서비스가 하나의 트랜잭션으로 묶을 때만 사용한다. 제공한 연결은 호출자가 커밋·롤백하고, 생략하면 함수가 자체 TX를 관리한다. 실제 공유 DB의 기존 NOT NULL 컬럼·작업 상태 enum은 #119 실측 전이므로 호환 적용을 완료했다고 보지 않는다. 게시·벡터 연계는 아래 #122 계약에서 다룬다.

#122의 DB 업로드·완전성·게시·복구 서비스도 db02 로컬에서 구현했다. 상세 API·원본 저장·재시도·복구 원장은 [WORKFLOW_122.md](WORKFLOW_122.md)를 따른다. 입력 Chunk 객체를 보존하고 실제 VectorHit의 소속·본문 해시를 확인한다. 실제 팀 수집/embedding 함수는 아직 뼈대이므로 기본 연동은 미준비 오류로 거부한다. 테스트 대역의 성공을 실제 외부 연동 완료로 간주하지 않는다.

`IngestResult`는 김혜리 소유이며 status(success/failed), chunks, report, error_code, error_message를 유지한다. `EmbedStoreReport`는 박영준 소유이며 document_id,version_id,company_id,model_name,total,skipped_existing,embedded,stored,truncated_count,failures,status,elapsed_sec를 유지한다. `partial`은 게시 실패이며 재시도에서 skipped_existing을 포함한 실제 ID 집합을 검증한다.

최신 develop의 common.schemas.Chunk는 chunk_id/text 필드를 가진다. #122의 DB 처리 흐름에서 Chunk.chunk_id를 manifest의 id로 매핑하고 최종 text의 UTF-8 SHA-256을 content_hash로 계산한다. 공통 Chunk에 DB 전용 필드를 추가하지 않는다. 원본 기본 저장 위치는 common.config.get_settings().upload_dir이며 DB에는 해당 경로 정책에 따른 내부 storage_path를 저장한다.

### 호출 예시 (계약 예시, #120~#122 구현 후 실행)

```python
from hyundap.auth.permissions import get_allowed_company_ids, verify_documents_access
from hyundap.db.documents_repo import find_duplicate_document, get_documents_with_active_version

# actor는 외부 인증 담당이 서버에서 전달한 문맥이다. 실제 연결은 별도 구현한다.
allowed = get_allowed_company_ids(actor)  # set[str]; 검색 담당이 선택 회사와 교집합 계산
match = find_duplicate_document(company_id, sha256)  # dict 또는 None; ingest가 중복 오류로 변환
rows = get_documents_with_active_version(document_ids)  # list[dict]; search가 DTO로 변환
verify_documents_access(actor, cited_pairs)  # None 또는 AccessError/DbError
```

성공: 빈 문서 ID 목록은 []이다. 실패: 권한 밖 회사는 ACCESS_DENIED, 인용 후 활성 버전 변경은 STATE_CHANGED, DB 장애는 DB_UNAVAILABLE이다. chatbot은 예외 발생 시 생성된 답변 전체를 반환하지 않는다.

## 4. 상태·트랜잭션·실패 계약

### DB P1 수집 보고서 저장 보완 (db01, 2026-10-26)

`normalize_report(report)`는 ingest 단계에서 다음 선택 필드를 추가로 받는다.
parser는 pypdf/python-docx/python-pptx, parser_version은 1~64자의 ASCII 영숫자와
`._+-`이며 첫 글자는 영숫자다. 두 필드는 각각 생략할 수 있다.
chunk_size는 bool 제외 양의 정수, chunk_overlap은 bool 제외 0 이상 정수이며
둘 다 있으면 overlap < size다. section_count/chunk_count/warning_count는
bool 제외 0 이상 정수다. 명시적 None은 거부하며 다른 단계의 수집 전용 필드도
INVALID_INPUT이다. 기존 필드 없는 보고서는 계속 허용한다.

warnings 원문은 제거하며 warning_count를 자동으로 만들지 않는다. 생산·호출 담당이
안전한 개수를 전달한다. 임의 본문·비밀값·원시 오류 문구는 저장하지 않는다.

`record_stage_result`는 수집 성공에 chunk_count가 명시되면 processing 버전의
양수 expected_chunk_count와 현재 manifest 행 수가 모두 같아야 저장한다.
부적격 버전 상태는 INVALID_STATE, 개수 모순은 INVALID_INPUT이다.
버전 → 작업 순서로 잠근 뒤 소속을 재확인하고 청크 수는 현재 잠금 조회로 읽는다.
REPEATABLE READ에서도 선행 참조 조회의 과거 snapshot을 개수 비교에 사용하지 않는다.
검사·JSON·stage·오류 열 갱신은 같은 TX이며 호출자 연결을 커밋하지 않는다.
다른 단계와 `_attempts`는 보존하고 성공 결과 변경·늦은 단계 기록은 CONFLICT다.
manifest와 보고서의 호출부는 기존처럼 별도 TX이므로 전체 수집 결과의 원자성은
이 계약으로 추가 보장하지 않는다.

### 문서 메타데이터와 중복 조회 세부 규칙

`validate_metadata`는 공통 `DOC_TYPE_CODES`로 저장 유형을 검사한다. doc_type 키가
없으면 `DEFAULT_DOC_TYPE`을 사용하고, 명시적 None·빈 문자열·미등록 값은
`INVALID_INPUT`이다. 입력 dict를 변경하지 않으며 파일을 읽거나 DB에 접속하지 않는다.

중복 조회의 `exclude_version_id`는 외부 공개 요청에서 임의로 받지 않는다.
서버가 고정한 후보를 같은 DB 연결에서 존재·회사·해시 조건으로 검증한 뒤 제외한다.
검증 실패는 `INVALID_INPUT`, DB 장애는 `DB_UNAVAILABLE`이다. 옵션을 생략하면
삭제·실패 포함 기존 정책이다. 현재 v4의 회사+해시 UNIQUE는 동일 회사·해시의
다른 버전 공존을 차단하므로 자기 후보를 제외하면 보통 None이다.
이 조회는 쓰기나 동시성 보호를 수행하지 않는다.

`connection` 제공 시 호출자 트랜잭션에 참여하며 커밋·종료하지 않는다.
생략하면 자체 읽기 트랜잭션을 사용한다. 최종 후보 생성은 제외 옵션 없이
회사 잠금 아래 검사하며 사전 중복은 `DUPLICATE_FILE`, 고유키 경쟁은
`CONFLICT`다. 콜백/파일/업로드 흐름 변경은 이 저장소 보완에 포함하지 않는다.

| 대상 | 상태 전이 | 조건·실패 처리 |
|---|---|---|
| documents | draft → published / published → blocked | 게시 시 ready·operational 활성 버전 필수. deleted_at이 있으면 항상 접근 거부. 처리 실패 때문에 기존 공개 문서를 draft로 내리지 않음 |
| document_versions | processing → ready 또는 failed / failed → processing | ready는 양수 예상 수·완전한 manifest·벡터 ID 집합 검증 후 설정. failed 재시도는 같은 ID, 단일 실행자. ready 버전의 파일·manifest는 불변 |
| index_jobs | queued → running → succeeded 또는 failed / failed → queued | stage는 received/ingest/embedding/verify/publish/complete. 재시도는 같은 후보, attempts 증가. 단일 프로세스가 순차 실행하고 중복 실행은 거부 |
| sessions 읽기 | 외부 인증 담당이 관리한 상태 검사 | auth_version 불일치·만료·폐기·토큰 없음이면 DB 접근 거부. 상태 변경은 DB 담당 범위 밖 |

실제 v3 작업 컬럼이 있는 경우 P1 종료 상태도 status(SUCCEEDED/FAILED)와 finished_at에 반영한다. 복구 대기 작업은 활성 보호를 유지하고, 외부 정리까지 끝난 restored 작업만 실패 종료한다. 재시도는 기존 status=QUEUED, attempt_no=증가한 attempts로 맞추며 종료·실행·임대 필드를 비운다. 후보의 processing 전환과 이 변경은 같은 TX다. 다른 활성 작업이 있으면 CONFLICT로 전체 롤백한다. 이전 시도 보고서는 _attempts에 보존한다.

업로드의 권한·중복 확인과 문서/후보/작업 생성은 짧은 트랜잭션으로 묶는다. UNIQUE(company_id,sha256)는 동시에 도착한 새 후보의 최종 중복을 차단한다. 기존 문서 행 잠금 안에서 version_no를 할당한다. 파일 저장 실패는 후보·작업을 failed로 기록하고 기존 활성 버전은 유지한다. 외부 파일·ingest·Chroma 호출 중 SQL 행 잠금을 유지하지 않는다.

게시: 외부 완전성 확인 → 문서 잠금 → 후보의 ready/operational·회사·권한·불변 manifest 재확인 → expected_active_id 비교 후 SQL 포인터 변경·작업 단계 기록·커밋 → activate_version 호출 → 작업 성공. 최초 게시는 publication_status도 published로 전환한다. blocked/deleted 문서는 일반 게시 함수로 되살리지 않는다.

활성화 실패는 실패 사유와 재시도 대상을 기록한다. 포인터가 여전히 후보이고 이전 버전이 현재도 적격일 때만 이전 포인터로 조건부 복구한다. 초기 게시라면 draft/NULL로 복구한다. DB 복구와 Chroma 이전 버전 재활성화도 독립적으로 실패할 수 있으므로 작업 기록을 남긴다. 더 최신 포인터를 덮지 않는다. 정상 복구 전에는 조회가 누락될 수 있지만 부적격 버전을 반환하지 않는다. ON/OFF 분리 API나 무중단 보장은 추가하지 않는다.

DB 기준 공개 조건은 published, deleted_at IS NULL, hit version=active_version_id, ready, operational, 허용 회사, hit 회사=DB 회사이다. 회사·계정 활성 상태도 auth에서 확인한다. 검색·답변의 최종 제어 코드는 각 담당자 책임이다.

## 5. 이행·검증·남은 범위

스키마 준비는 #119, 회사 격리·접근 재검증은 #120, 저장 함수는 #121, 처리 흐름은 #122다. 레거시 문서는 새 컬럼 기본값 draft/active_version_id=NULL로 격리한다. 기존 파일·해시·벡터의 의미를 추측해 ready 버전으로 자동 변환하지 않는다. #122에서 명시적으로 가져오기 전에는 해당 회사의 신규 업로드를 막아 레거시 해시 중복 누락을 방지한다. 스키마 버전 4는 앱 전체 구현 완료나 데이터 이행 완료를 뜻하지 않는다.

검증은 회사 격리·빈 ID·일괄 조회 장애·동시 해시 중복·다른 회사 활성 버전 지정·청크 순번/ID 중복·권한 변경 재검증·부분 적재·게시 경쟁·외부 호출 실패를 대상으로 한다. #118은 계약 확정만 완료하며 런타임 검증 성공을 주장하지 않는다.

기존 WBS 담당 기록, 명세의 VARCHAR(32), DTO non-null 버전 필드, P2/P3 충돌은 위 채택안으로 구현 방향을 결정했다. 팀원에게 별도 메시지를 발송하거나 기존 WBS를 변경하지 않았다. 남은 외부 조건은 실제 DB 접속·점검과 다른 모듈 구현 제공이며, 협의 대기 항목으로 다시 분류하지 않는다.
