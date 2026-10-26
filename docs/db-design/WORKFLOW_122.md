# 업로드·적재·게시·복구 — #122

구현 위치는 `hyundap/db/upload_service.py`, `version_service.py`다. 기존 #120 인증과 #121 저장 함수를 사용한다. 모든 실행은 저장소 루트에서 하며 다른 폴더 import는 `hyundap.*`, 같은 폴더는 상대 import를 쓴다(#127/#128, B안).

## 구현과 명세 대응

| #122 작업 | 구현 |
|---|---|
| 권한·후보·원본·수집 연결 | upload_document: 현재 관리자·대상 회사 검사, 서버 해시·크기 계산, 문서/후보/작업·저장 경로 원자 기록, 원본 저장, ingest_document 호출 |
| 임베딩·청크·보고서 연결 | 수집 결과의 Chunk 객체 유지, 소속·순번 검사, UTF-8 본문 해시 원장 저장, embed_and_store 호출, 단계별 보고서 저장 |
| 적재 완전성 | validate_completeness: DB 예상 ID/개수/순번과 실제 ID 집합·중복·VectorHit 본문 해시·회사/문서/버전 비교 |
| 안전한 게시·실패 정리 | publish_version: 외부 검사 후 원장 seal·현재 권한·회사·상태 재검사, ready 전환·기대 포인터 CAS, 커밋 후 활성화, 실패 시 조건부 복구·후보 벡터 정리 |
| 실패·경쟁·재시도 검증 | 부분 적재·잘못된 소속/본문·원장 변경·게시 경쟁·외부 활성화/정리 실패·차단/삭제·권한 변경·멱등 호출·동일 후보 재시도 테스트 |

F39/F72/F107/F110~F112와 FUN-168/218/307/316~318의 DB 담당 연계 부분이다. 수집·임베딩 내부 알고리즘과 UI는 각 담당자 책임으로 유지한다.

## 호출 계약

| 함수 | 입력·반환 |
|---|---|
| upload_document(actor, source_path, metadata, *, ingest_document=None, embedding=None, upload_dir=None) | metadata는 company_id/doc_type과 선택 document_id/file_name/data_category. 해시·크기·경로는 서버 결정. document_id/version_id/job_id/status/error_code/recovery_required dict 반환 |
| retry_upload(actor, version_id, *, ingest_document=None, embedding=None, source_path=None) | 저장 원본으로 동일 문서·버전·작업을 재사용. 저장 실패로 원본이 없으면 동일 해시·크기의 원본을 source_path로 재공급 가능 |
| validate_completeness(document_id, version_id, *, embedding=None) | ok/개수/누락/초과/잘못된 ID와 내부 재검증용 _seal 반환 |
| publish_version(actor, document_id, candidate_id, expected_active_id, *, embedding=None) | 성공 None. 기대 포인터 충돌 CONFLICT, 부적격·불완전 후보 거부, 완료한 동일 후보는 멱등 성공 |
| recover_publication(document_id, version_id, *, embedding=None) | 서버 운영용 명시적 복구. phase=restored/restore_pending/superseded/activated/not_pending 반환 |

인증·입력·의존성 오류는 후보 생성 전에 예외로 전달한다. 후보 생성 후 예상 처리 실패는 status=failed와 안전한 코드로 반환한다. 외부 모듈의 예외 원문·비밀값은 반환하거나 기록하지 않는다. 내부 프로그래밍 오류는 실패 기록 후 다시 전달한다. `_session_token`이 있는 actor는 서버 안에서만 사용한다.

```python
from hyundap.auth.login import authenticate
from hyundap.db.upload_service import upload_document, retry_upload
from hyundap.db.version_service import recover_publication

actor = authenticate(server_session_token)
result = upload_document(actor, server_uploaded_path, {
    'company_id': selected_company_id,
    'doc_type': 'manual',
    'data_category': 'operational',
})
# 처리 실패 후 같은 후보를 재개할 때:
result = retry_upload(actor, result['version_id'])
# 게시 복구가 필요한 경우 운영자 내부 경로에서 먼저 실행:
recovery = recover_publication(document_id, version_id)
```

위 예시는 실제 수집·벡터 함수가 제공된 뒤 실행한다. source_path는 서버가 업로드 후 확정한 파일 경로다. 클라이언트의 임의 서버 파일 경로를 그대로 넘기거나 이 함수를 브라우저에 직접 공개하지 않는다.

## 외부 담당 함수의 연결 조건

김혜리의 `hyundap.ingest.pipeline.ingest_document(path, upload_info)`는 dict 또는 IngestResult dataclass를 반환한다. status, chunks, report, error_code가 계약 대상이다. chunks는 `common.schemas.Chunk` 객체 그대로 임베딩 담당에게 전달한다.

박영준의 기본 어댑터는 아래 함수를 요구한다. 테스트에서는 같은 메서드를 가진 객체를 embedding 인자로 주입한다.

| 외부 함수 | 요구 사항 |
|---|---|
| embed_and_store(chunks) | success/partial/failed와 개수·실패 목록. partial은 게시 불가 |
| get_version_chunk_ids(document_id, version_id) | 비활성 후보를 포함한 실제 ID 목록. 중복도 검출 가능해야 함 |
| get_document_chunks(document_id, version_id, active_only=False) | VectorHit의 chunk_id/text/metadata 제공. 회사·문서·버전과 실제 본문 해시 검증에 필요 |
| activate_version(document_id, version_id) | 새 버전 ON·다른 버전 OFF 후 정확한 활성 청크 수(int)를 반환 |
| delete_vectors(document_id, version_id) | 지정 후보만 정리. 이전 정상 버전은 삭제하지 않음 |

현재 체크아웃의 ingest/embedding 파일은 실제 함수가 없는 설계 뼈대다. 기본 어댑터는 이를 `INTEGRATION_UNAVAILABLE`로 거부한다. fake 외부 모듈 테스트 통과를 실제 파싱·임베딩 API·Chroma 실연동 완료로 보지 않는다. `get_document_chunks(active_only=False)` 제공도 완전성 검사 연동의 선행 조건이다.

## 파일·트랜잭션 경계

원본은 앱 서버가 관리하는 upload_dir/company/document/version/source.ext에 보관한다. 경로 구성 요소를 제한하고 저장 루트 포함 여부를 실제 파일 작업 전에 확인한다. 업로드 저장소와 원본 임시 경로는 앱 서버 소유여야 한다.

원본을 1MiB씩 읽어 해시·크기를 계산한다. 후보·작업·최종 저장 경로·저장 루트는 같은 SQL TX로 기록한다. 커밋 후 임시 파일에 복사하며 다시 해시·크기를 비교하고 fsync 후 같은 파일시스템의 hard link로 완전한 파일만 공개한다. 기존 목적지는 덮지 않고 임시 부분 파일은 제거한다. DB는 파일 저장 완료 전 후보를 공개하지 않는다.

원본 복사·경로 resolve·수집·임베딩·벡터 조회/활성화/삭제 중 SQL 행 잠금을 유지하지 않는다. 외부 검사 후에는 DB 원장과 후보 메타데이터의 seal을 잠금 안에서 재검사하므로 검사 도중 원장이 바뀌면 게시를 거부한다.

## 게시·복구·재시도

게시 순서는 기존 명세의 SQL 포인터 전환 → Chroma activate_version이다. 새 벡터 ON/OFF 분리 API를 요구하지 않는다. 처리 중 일시 검색 누락이 생길 수 있지만 DB의 공개·활성 버전 조건이 부적격 결과 반환을 막는다.

작업 JSON의 `_publication`에 candidate_id, previous_active_id, previous_publication_status, phase를 기록한다. phase는 pending_activation → activated, 실패 시 restore_pending → restored다. 같은 문서에 pending 상태가 있으면 새 게시를 거부한다. 이전 포인터를 바꾸는 경우 현재 포인터가 아직 후보인지 검사하고, 이전 버전·회사·문서가 현재도 적격일 때만 이전 벡터를 재활성화한다. 차단·삭제 상태를 되살리지 않으며 되돌릴 수 없는 이전 버전은 포인터 NULL/blocked로 남긴다. NULL 복구 대상도 restore_target_id로 보관해 정리 재시도를 잃지 않는다.

활성화 또는 정리가 실패하면 `recover_publication`을 명시적으로 재호출한다. 더 최신 포인터가 이미 존재하면 superseded로 기록하고 덮어쓰지 않는다. 이 경우 실패 후보의 잔여 벡터는 운영자가 현재 포인터를 확인하고 별도로 정리한다. 활성화 성공 후 최종 SQL 기록 실패도 pending 원장으로 복구할 수 있다.

게시 전 실패 후보의 벡터 정리 실패는 `_cleanup_pending`으로 남긴다. `retry_upload`가 같은 후보를 재개할 때 먼저 후보 벡터를 정리한다. 이전 단계 보고서는 `_attempts`에 보존하고 attempts를 증가시킨다. 저장 원본이 없으면 동일 원본을 재공급할 수 있지만 이미 존재하는 손상 원본을 자동 덮어쓰지는 않는다. 손상 파일은 운영자가 확인한다. 정상 활성 버전·다른 회사·삭제/차단 문서는 재시도 대상이 아니다.

P0는 단일 앱 프로세스의 업로드/게시/복구 직렬 실행이다. Python 잠금과 DB pending 원장은 이 운영 전제에서 사용한다. 다중 프로세스 워커·임대·자동 복구 스케줄러는 추가하지 않았다.

## 검증

저장소 루트에서 `python -m unittest discover -s hyundap/tests -t . -v`를 실행한다. 워크플로 통합 테스트는 DB_WORKFLOW_TEST_URL(host=127.0.0.1, database=hyundap_workflow_test)의 빈 일회용 DB만 허용한다. 기존 테이블이 있으면 중단한다. 다른 두 fixture는 DB_RUNTIME_TEST_URL과 DB_MIGRATION_TEST_URL을 별도 빈 DB로 설정한다.

현재 검증은 실제 MySQL과 임시 원본 파일, fake 수집기·벡터 저장소 조합이다. 실제 공유 DB 접속·기존 데이터 이행은 #119, 팀 외부 모듈과의 실제 연결은 해당 담당 함수 제공 후 남아 있다. 모든 변경은 db02의 미커밋 로컬 작업 트리이며 커밋·푸시·PR은 보류한다.

2026-10-26 검증: Python 3.11 / SQLAlchemy 2.0.55 / MySQL 8.4.13에서 루트 전체 테스트 53개가 건너뜀 없이 통과했다. 원본 경로·작업 정보의 원자 기록과 재시도 경로 검사를 마지막 보완한 뒤 전용 새 DB에서 워크플로 18개도 다시 통과했다. 짧은 절대 import·sys.path 조작이 없고, 모든 모듈·클래스·함수의 한글 독스트링, Python 컴파일과 공백 검사를 확인했다. 실제 외부 API 호출은 없었다.
