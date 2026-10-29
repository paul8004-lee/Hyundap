# DB P2 최종 연계 검증과 인계 — #206

## 기준과 완료 범위

2026-10-28, `feature/db`의 `93bd178`에서 분기한 `db-p2-04` 작업 트리 기준이다.
이 문서에서 완료는 DB 소유 구현·계약·회귀 검증 범위다. 실제 서비스 P2 E2E 완료를 뜻하지 않는다.
이번 단계는 업무 함수를 추가하지 않고 통합 시나리오 3개와 최신 계약 문서를 추가했다.

| 이슈 | 구현 커밋 | 함수·모듈·역할 | 결과 |
|---|---|---|---|
| #203 | f36aa66 | documents_repo.get_document_detail 메인, _check_detail_access 보조 | 상세 9필드, 현재 권한/활성 버전 검사 |
| #204 | f2c4254 | versions_repo.list_document_versions 기존 메인 | 이력 10필드, is_active 및 관리자 범위 |
| #205 | aa943c8 | upload_service.resolve_stored_source 신규 보조, read_attempt_source/retry_upload 보완 | 저장 원본 우선, 경로 경계·접근 오류 정규화 |
| #206 | 93bd178 위 미커밋 변경 | test_upload_workflow의 P2 통합 시나리오 | 세 기능과 기존 UI 어댑터 연결 검증 |

새 업무 클래스는 없다. 일반 함수·dict·명시적 if/for와 단계별 한국어 주석을 유지한다.
기존 P1 게시·복구·보고서·검색 원시 11필드, 트랜잭션 소유권 및 외부 호출 순서를 유지한다.
v3 이행은 폐기 상태다. documents.storage_key는 현재 스키마에 없으며 복원하지 않는다.
원본 경로·해시·크기는 버전 행에, 공개 선택은 documents.active_version_id에 저장한다.

## UI 담당에게 전달할 계약

| 함수 | 반환/오류 | 연결 시 주의점 |
|---|---|---|
| get_document_detail(actor, document_id, version_id=선택버전) | document_id/company_id/company_name/version_id/file_name/doc_type/version_no/size_bytes/indexed_at | size_bytes는 파일 바이트 수. indexed_at=None을 정보 없음으로 표시 |
| list_document_versions(actor, document_id) | 기존 9필드 + is_active(bool), version_no 오름차순 | 관리자 전용. is_active는 공개 접근 허가가 아님. 삭제/차단 이력도 조회 가능 |
| verify_documents_access(actor, [(document_id, version_id)]) | 성공 None; SESSION_EXPIRED/ACCESS_DENIED/STATE_CHANGED | 본문·요약 등 외부 작업 후 반환 직전 새 조회로 재검증 |

상세의 없는 문서·비공개·요청 버전 불일치는 STATE_CHANGED, 회사 위반은 ACCESS_DENIED다.
관리 이력의 없는 문서·직원·회사 위반은 ACCESS_DENIED다. 공통으로 잘못된 ID는
INVALID_INPUT, DB 장애는 DB_UNAVAILABLE이며 원시 예외·경로·토큰은 화면에 표시하지 않는다.

```python
from hyundap.db.documents_repo import get_document_detail
from hyundap.auth.permissions import verify_documents_access

detail = get_document_detail(actor, document_id, version_id=selected_version_id)
# UI/embedding 담당은 detail['version_id']로 본문을 읽는다.
# 본문을 읽은 뒤 아래 재검증을 통과해야 응답을 표시한다.
verify_documents_access(actor, [(detail['document_id'], detail['version_id'])])
```

현재 `ui.backend_documents.get_document`는 기존 검색용 조회를 사용하여 size_bytes와
indexed_at을 None으로 반환한다. 위 신규 함수로 메타데이터 조회를 연결하고 size_bytes를
전달하는 변경은 UI 담당 후속 작업이다. 기존 본문/출처 처리와 조회 전후 재검증을 유지해야 한다.
현재 어댑터의 기존 동작을 검증한 결과를 신규 함수 화면 연결 완료로 표현하지 않는다.

## 검증 행렬

| 범위 | 검사 근거 |
|---|---|
| 세 회사·세 역할 | test_p2_three_companies_and_roles_share_detail_history_and_original_contracts: 세 회사 공개 문서에 플랫폼/회사 관리자/직원 실제 DB 권한 적용 |
| 기존 UI 연계 | test_p2_existing_ui_adapter_reads_real_database_and_rechecks_session: 실제 DB/auth·UI 어댑터, 본문만 대역. 읽는 중 실제 세션 폐기 후 반환 차단 |
| 실패·재시도·활성 변경 | test_p2_failed_version_retry_updates_detail_and_history_preserving_originals: 이전 상세/포인터 보호 → 같은 후보 재시도 → 상세/이력 전환, 두 원본 보존 |
| 접근·오류 | test_access_queries, test_db_runtime: 만료/폐기·비활성 회사·삭제/차단·없는 문서·이전 버전·내부 경로 미노출·호출자 롤백 |
| P1 및 저장 회귀 | 전체 discovery: 검색 11필드·보고서·게시·재시도·복구·포인터 보호·원본 경로/복사 및 기존 UI/요약 단위 계약 |

UI 연계 테스트는 `_function`에서 embedding의 get_document_chunks만 대체한다.
DB 조회와 auth 권한 검사는 실제 격리 MySQL에 연결한다. 본문 대역의 chunk_id/is_active
메타데이터는 UI 계약에 맞춰 제공한다. 실제 Chroma 영속성·검색 정확도 검증은 아니다.

## 실행 환경과 재현 조건

Python 3.11, 새 localhost MySQL 8.4.13, 빈 전용 DB 5개를 사용한다.
DB_RUNTIME_TEST_URL=127.0.0.1/hyundap_runtime_test,
DB_CONTRACT_TEST_URL=127.0.0.1/hyundap_contract_test,
DB_AUTH_TEST_URL=127.0.0.1/hyundap_auth_test,
DB_WORKFLOW_TEST_URL=127.0.0.1/hyundap_workflow_test,
DB_SCHEMA_TEST_URL=127.0.0.1/hyundap_schema_test에 맞는 전체 SQLAlchemy URL을
테스트 프로세스 환경변수에 지정한다. 기존 데이터가 있는 DB를 재사용하지 않는다.
각 테스트 준비 코드가 최종 v4를 직접 생성하며 삭제로 초기화하지 않는다.

```text
python -m unittest discover -s hyundap/tests -t .
git diff --check
```

requirements의 python-docx 1.2.0/python-pptx 1.0.2/pypdf 6.21.2는 이전 단계의
임시 PYTHONPATH 경로에서 제공하고 기존 Pillow 12.3.0을 유지했다.
원본은 TemporaryDirectory, 수집·임베딩은 대역이다. Railway/실제 OpenAI API에는 접속하지 않는다.
심볼릭 링크 생성은 Windows 권한 오류 1314로 불가능하여 resolve 결과 대역으로 검사한다.
건너뜀 수와 관계없이 실제 링크 I/O 미검증이라는 한계를 유지한다.

## 남은 담당 간 연계

### 최종 실행 결과

2026-10-28, `93bd178` 위의 #206 미커밋 테스트·문서 변경 기준:
**389개 통과, 실패 0·오류 0·건너뜀 0**, 실행 시간 37.363초.
신규 통합 테스트는 3개이며 별도 업무 함수 추가는 없다.
컴파일·git diff --check·변경 파일의 한자/깨진 문자 검사도 통과했다.
검증 후 이번 작업의 격리 MySQL 서버를 종료했다.

### 후속 연결

1. UI 담당: 상세 신규 함수와 파일 크기 연결, 이력 표시 시 추가 필드 반영.
2. UI/auth 담당: 실제 로그인·브라우저 흐름에서 만료/권한 변경·버전 변경 안내 확인.
3. embedding/chatbot 담당과 공동: 실제 Chroma 본문·요약·출처의 버전 정합성 E2E 검증.

다운로드·원본 미리보기·P3 계정/로그/삭제/재색인 관리는 이번 완료 조건에 포함하지 않는다.
이전 P1/P2-01~03의 결과는 당시 기록으로 보존하며 최신 결과로 덮어쓰지 않는다.
