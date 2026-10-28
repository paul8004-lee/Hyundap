# DB 최종 v4 구현 명세 — #168

인증 PR #167 병합 후 계정·세션 저장과 fixture도 고정 v4에 맞췄다. [인증 정합화 보고서](../../../docs/db-design/V4_AUTH_ALIGNMENT_2026-10-27.md)의 332개 통과는 당시 이력이며, 현재 P1 범위의 검증 기준과 한계는 [현행 검증 기록](../../../docs/db-design/DB_P1_CURRENT_VALIDATION.md)을 따른다.

대상: feature/db. 2026-10-27 사용자 결정으로 v3 이행·과거 혼합 v4 호환은 폐기했다. 현재 생성 진입점은 `schema.create_v4_schema(engine)` 하나다. [초기화 절차](../../../docs/db-design/V4_INITIALIZATION.md), [물리 계약](../../../docs/db-design/DB_schema.md), [공개 함수 계약](../../../docs/db-design/DB_contract.md)을 따른다.

## P2 현재 상태 — 2026-10-28 / #203~#206

P2 상세 조회·관리자 버전 이력·원본 보존은 구현했고, #206에서 세 기능의 연결을 검증한다.
상단의 기존 P1 검증 수치는 당시 이력이며 P2 실행 결과를 뜻하지 않는다.
현재 계약·검증 범위와 UI 담당 전달 사항은
[P2 최종 연계 검증](../../../docs/db-design/P2_04_INTEGRATION_VALIDATION.md)을 우선 확인한다.
기존 UI 어댑터의 P2 상세 함수 연결과 실제 브라우저/Chroma/요약 E2E는 별도 후속 항목이다.

## 구조와 저장 계약

11개 최종 테이블을 빈 DB에 직접 생성하고 구조 검증 뒤 버전 4를 기록한다. 기존 DB·부분 생성 DB는 거부하며 자동 DROP은 없다. 관리 실측은 매번 조회하고, 업무 Engine은 최초 준비 시 최종 계약을 검사한다. 이후 업무 SQL에는 컬럼 존재 검사·구조 캐시·동적 INSERT가 없다.

파일 정보·해시·경로·크기의 기준은 document_versions다. documents에는 회사·공개/삭제 상태·활성 포인터·업로더를 저장한다. 작업의 상태(status)와 단계(stage)는 구분하고 시도 횟수는 attempts에만 기록한다. 문서당 활성 작업 하나와 버전당 작업 하나를 DB에서 보호한다.

작업 결과는 `jobs_repo.store_job_result`에서 단계·보고서·오류·상태·종료 시각을 한 UPDATE로 저장한다. 게시/복구 대기에는 QUEUED와 활성 보호를 유지하고, 복구 완료 또는 최신 포인터로 대체된 작업은 FAILED로 종료한다. 재시도는 `jobs_repo.reset_job_for_retry`에서 QUEUED·received·attempts 증가·보고서 이력·시간 초기화를 한 번에 저장한다. 다른 활성 작업과 충돌하면 버전 변경까지 함께 취소된다.

BIGINT 시간은 UTC Unix 초, DATETIME(6)은 UTC다. 세션은 필수 CSRF 해시·권한 버전과 expires_at·유휴/절대 만료를 모두 검사한다. 사용자의 역할·회사 입력은 신뢰하지 않고 현재 DB 값을 재조회한다. 운영 인증 발급 구현은 별도 인증 모듈 책임이다.

## 입력·반환·오류·트랜잭션

기존 공개 메인 함수의 서명과 dict/list 반환을 유지한다. 검색용 원시 조회는 document_id/company_id/company_name/doc_type/publication_status/deleted_at/active_version_id/version_no/version_status/data_category/file_name의 11필드다. 없는 ID는 누락하며 빈 입력은 DB 접근 없이 []다.

문서 생성은 `{document_id,version_id,job_id}`를 반환한다. 업로드 메타데이터의 doc_type 누락은 공통 기본값이며 명시적 잘못된 값은 INVALID_DOC_TYPE이다. 없는 파일이나 읽기 권한이 없는 원본은 FILE_UNAVAILABLE이다. 후보 생성 전 실패는 작업 원장을 만들지 않는다. manifest는 버전 ID 기반 청크 ID·연속 순번·해시 목록이고 본문·벡터는 저장하지 않는다. 보고서는 허용 필드만 정규화하며 원문·비밀값·원시 예외를 저장하지 않는다.

주요 오류는 INVALID_INPUT/INVALID_DOC_TYPE/FILE_UNAVAILABLE/INVALID_STATE/DUPLICATE_FILE/CONFLICT/DB_UNAVAILABLE, 접근 오류는 SESSION_EXPIRED/ACCESS_DENIED/STATE_CHANGED다. 함수가 외부 connection을 받으면 호출자 TX에 참여하며 직접 커밋·종료하지 않는다. 생략하면 runtime.transaction이 커밋/롤백을 소유한다. 초기화 DDL은 업무 TX와 별개다.

## 모듈과 메인·보조 함수

### #207 미사용 코드 정리 — 2026-10-28

업무 호출이 없는 `chunks_repo.get_expected_chunk_ids`를 삭제했다. 테스트 2곳은
SQL로 청크 롤백·멱등 저장 결과를 직접 확인하여 기존 검증을 유지한다.
실제 완전성 검증의 manifest_snapshot/compare_stored_chunks 경로는 그대로 사용한다.
미참조 상수 schema.SUPPORTED_SCHEMA_VERSIONS와 connection의 text import도 제거했다.
get_document_detail/list_document_versions/retry_upload 및 하위 보조 함수는 유지한다.
이전 P1 계획의 함수명은 당시 이력이며 현재 공개 API가 아니다.

검증(2026-10-28): 최신 develop 6e307ba 위의 정리 작업 트리에서 격리 MySQL 8.4.13과
빈 테스트 DB 5개로 `python -m unittest discover -s hyundap/tests -t .`를 실행했다.
전체 359개 통과, 실패·오류·건너뜀 0개(38.971초). 이전 389개와의 수 차이는 최신 develop의
임베딩 테스트 재구성에 따른 것이며 이번 정리로 테스트를 삭제하지 않았다.
파일 시스템·외부 벡터 등의 기존 대역 범위는 유지하고 Railway·실제 API에는 접속하지 않았다.

### P2-03 원본·버전 연결 — #205

현재 v4의 원본 경로·해시·크기는 `document_versions`에만 저장한다.
`documents.storage_key` 및 별도 최초 원본 포인터는 제거된 과거 구조다.
최초 버전의 원본은 해당 버전 행과 파일로 보존되고 `active_version_id`는 공개 버전 선택만 담당한다.

`upload_service.resolve_stored_source(source, root)`를 추가해 재시도와 원본 재조회에서
절대 경로 및 링크 해석 후 루트 내부 여부를 공통 검사한다. 이 파일 시스템 작업은 DB TX 밖이다.
`read_attempt_source`는 작업 보고서의 저장 루트를 읽고 경로·해시·크기를 확인한다.
파일 없음·읽기 권한 오류는 FILE_UNAVAILABLE, 루트 밖/상대 경로는 INVALID_STATE,
해시·크기 불일치는 CONFLICT다. 재시도에서 저장 원본이 있으면 재공급 경로는 무시하고
기존 원본을 검증한다. 저장 원본이 없을 때만 같은 해시·크기의 원본을 재공급할 수 있다.

조회 응답의 내부 경로 비노출, 버전별 파일 보존 및 검증 근거는
[P2-03 원본 보존 계약](../../../docs/db-design/P2_03_ORIGINAL_STORAGE_CONTRACT.md)에 기록한다.

### P2-02 관리자 버전 이력 — #204

`versions_repo.list_document_versions(actor, document_id, *, connection=None)`를 재사용한다.
새 클래스·보조 함수·저장 테이블 없이 기존 9필드에 `is_active: bool`만 추가했다.
기존 필드와 version_no 오름차순은 유지한다. 문서/회사/버전을 같은 SELECT에서 연결하여
현재 활성 포인터 일치 여부를 계산한다. `is_active`는 검색·본문 접근 허가가 아니다.

회사 관리자는 자신의 활성 회사, 플랫폼 관리자는 활성 회사들의 이력만 조회한다.
직원·없는 문서·회사 범위 위반은 ACCESS_DENIED, 세션 무효는 SESSION_EXPIRED다.
잘못된 ID는 DB 접근 전에 INVALID_INPUT, DB 장애는 DB_UNAVAILABLE로 전달한다.
삭제·차단·비공개 문서의 과거/실패/처리중 이력도 관리자 조회에서는 기존대로 유지한다.
문서는 있지만 버전이 없으면 []이며, 내부 경로·토큰·원시 오류는 반환하지 않는다.
호출자가 제공한 연결을 커밋/종료하지 않고 조회만 수행한다.

호출부 검색에서는 저장소 테스트만 확인되었으며 기존 키 사용은 유지된다.
외부 호출자가 키 개수를 고정했다면 10필드 계약으로 맞춰야 한다. UI의 실제 이력 화면은
이번 작업에 포함하지 않는다. 구현·검증 세부 사항은
[P2-02 버전 이력 계약](../../../docs/db-design/P2_02_VERSION_HISTORY_CONTRACT.md)을 따른다.

### P2-01 상세 메타데이터 조회 — #203

`documents_repo.get_document_detail(actor, document_id, *, version_id=None, connection=None)`는
상세 전용 메인 함수다. `_check_detail_access(document, allowed_company_ids, version_id)`는
회사·공개 상태·요청 버전을 검사하는 보조 함수이며 두 함수 모두 클래스 없는 모듈 함수다.
검색용 `get_documents_with_active_version`의 기존 11필드와 호출 규격은 변경하지 않는다.

반환값은 `document_id/company_id/company_name/version_id/file_name/doc_type/version_no/size_bytes/indexed_at`
9필드 dict다. `size_bytes`는 활성 버전의 `file_size`이고 `indexed_at`은 항상 `None`이다.
후보의 생성 시각과 작업 종료 시각은 색인 완료 시각을 보장하지 않으므로 대체하지 않는다.
내부 경로·토큰·본문·벡터·관리용 상태는 반환하지 않는다.

현재 세션과 DB의 역할·회사를 재검증한다. 회사 밖 접근은 `ACCESS_DENIED`,
없는 문서·삭제·비공개·활성 버전 없음·ready가 아닌 버전·평가 자료·요청 버전 불일치는
`STATE_CHANGED`다. 입력 ID 오류는 `INVALID_INPUT`, 세션 무효는 `SESSION_EXPIRED`,
DB 장애는 안전한 `DB_UNAVAILABLE`로 전달한다. 권한 있는 직원도 공개 운영 문서를 조회할 수 있다.
과거 버전 조회나 다운로드 권한을 새로 제공하지 않는다.

```python
from hyundap.db.documents_repo import get_document_detail
from hyundap.auth.permissions import verify_documents_access

detail = get_document_detail(actor, document_id, version_id=selected_version_id)
# UI/embedding 담당은 detail의 문서·버전으로 본문을 읽는다.
# 본문을 읽는 동안 공개 상태나 세션이 바뀔 수 있으므로 반환 직전에 재검증한다.
verify_documents_access(actor, [(detail['document_id'], detail['version_id'])])
```

이 함수의 정보는 조회 시점 기준이다. `connection`을 전달하면 호출자 트랜잭션에 참여하고
커밋·종료하지 않는다. 긴 트랜잭션의 스냅샷을 최신 권한 보장으로 간주하지 않으며,
외부 작업 후 재검증은 새 조회 경계에서 수행한다. UI의 기존 조회 전후 재검증은 유지해야 한다.
현재 UI 어댑터는 기존 11필드 함수를 사용한다. UI 담당이 상세 함수를 연결하고
`size_bytes`를 전달하는 변경은 후속 연계이며 이번 DB 변경으로 화면 연결 완료를 주장하지 않는다.

검증: `test_access_queries`의 합성 SQLite는 권한·상태·반환·오류 및 기존 검색 계약을,
`test_db_runtime`의 격리 MySQL은 최종 v4 조회와 호출자 롤백 소유권을 확인한다.
2026-10-28 전체 375개 통과(실패·오류·건너뜀 0)의 실행 조건과 UI 인계 사항은
[P2-01 상세 조회 계약](../../../docs/db-design/P2_01_DETAIL_CONTRACT.md)에 기록했다.

업무 코드는 모듈 함수이며 새 클래스는 없다. 오류 형식 `errors.DbError`만 예외 클래스다. 테스트의 unittest.TestCase와 수동 시험 어댑터 클래스는 업무 서비스 클래스가 아니다. 아래 목록은 현재 소스의 함수 서명을 기준으로 작성했다.

| 모듈 | 함수 서명 | 역할 | 클래스 |
|---|---|---|---|
| `chunks_repo` | `save_manifest(version_id, chunks, *, connection=None)` | 메인 | 없음 |
| `chunks_repo` | `_normalize_manifest(version_id, chunks)` | 보조 | 없음 |
| `chunks_repo` | `_chunk_index(chunk)` | 보조 | 없음 |
| `clock` | `epoch_now()` | 보조 | 없음 |
| `connection` | `create_db_engine()` | 메인 | 없음 |
| `connection` | `main()` | 메인 | 없음 |
| `documents_repo` | `find_duplicate_document(company_id, sha256, *, exclude_version_id=None, connection=None)` | 메인 | 없음 |
| `documents_repo` | `get_documents_with_active_version(document_ids, *, connection=None)` | 메인 | 없음 |
| `documents_repo` | `get_document_detail(actor, document_id, *, version_id=None, connection=None)` | 메인 | 없음 |
| `documents_repo` | `_check_detail_access(document, allowed_company_ids, version_id)` | 보조 | 없음 |
| `documents_repo` | `validate_metadata(metadata)` | 메인 | 없음 |
| `documents_repo` | `create_document_version(actor, metadata, *, connection=None)` | 메인 | 없음 |
| `documents_repo` | `_check_candidate_creation(database, actor, data)` | 보조 | 없음 |
| `documents_repo` | `_prepare_document(database, data)` | 보조 | 없음 |
| `documents_repo` | `_insert_version_and_job(database, document_id, data)` | 보조 | 없음 |
| `jobs_repo` | `normalize_report(report)` | 메인 | 없음 |
| `jobs_repo` | `_report_dict(report)` | 보조 | 없음 |
| `jobs_repo` | `_report_identity(report)` | 보조 | 없음 |
| `jobs_repo` | `_report_metrics(report)` | 보조 | 없음 |
| `jobs_repo` | `_ingest_metrics(report, stage)` | 보조 | 없음 |
| `jobs_repo` | `_report_failures(report)` | 보조 | 없음 |
| `jobs_repo` | `_failure_detail(failure)` | 보조 | 없음 |
| `jobs_repo` | `record_stage_result(job_id, report, *, connection=None)` | 메인 | 없음 |
| `jobs_repo` | `_read_stage_target(database, job_id)` | 보조 | 없음 |
| `jobs_repo` | `_check_ingest_count(database, version_id, count)` | 보조 | 없음 |
| `jobs_repo` | `_check_stage_result(job, identity, clean)` | 보조 | 없음 |
| `jobs_repo` | `_merge_stage_reports(stored_report, clean)` | 보조 | 없음 |
| `jobs_repo` | `_store_stage_result(database, job_id, clean, reports)` | 보조 | 없음 |
| `jobs_repo` | `store_job_result(database, job_id, stage, report, *, status=None, error_code=None, error_message=None)` | 보조 | 없음 |
| `jobs_repo` | `reset_job_for_retry(database, job_id, report)` | 보조 | 없음 |
| `runtime` | `get_engine()` | 보조 | 없음 |
| `runtime` | `transaction(connection=None)` | 보조 | 없음 |
| `runtime` | `require_id(value)` | 보조 | 없음 |
| `runtime` | `require_hash(value)` | 보조 | 없음 |
| `schema` | `_read_layout(connection)` | 보조 | 없음 |
| `schema` | `snapshot(connection)` | 메인 | 없음 |
| `schema` | `_expression(value)` | 보조 | 없음 |
| `schema` | `_structure(layout)` | 보조 | 없음 |
| `schema` | `validate_v4(layout)` | 메인 | 없음 |
| `schema` | `create_v4_schema(engine)` | 메인 | 없음 |
| `schema` | `audit_counts(connection)` | 메인 | 없음 |
| `schema` | `main()` | 메인 | 없음 |
| `sessions_repo` | `token_hash(token)` | 보조 | 없음 |
| `sessions_repo` | `current_actor(actor, *, connection, lock=False)` | 메인 | 없음 |
| `sessions_repo` | `_session_credentials(actor)` | 보조 | 없음 |
| `sessions_repo` | `_read_valid_session(connection, session_id, digest, lock)` | 보조 | 없음 |
| `sessions_repo` | `_read_active_user(connection, session, lock)` | 보조 | 없음 |
| `sessions_repo` | `_check_company_role(connection, user, lock)` | 보조 | 없음 |
| `upload_service` | `get_ingest_function(function=None)` | 보조 | 없음 |
| `upload_service` | `file_digest(path)` | 보조 | 없음 |
| `upload_service` | `source_destination(root, company_id, document_id, version_id, suffix)` | 보조 | 없음 |
| `upload_service` | `copy_original(source, destination, expected_hash, expected_size)` | 보조 | 없음 |
| `upload_service` | `resolve_stored_source(source, root)` | 보조 | 없음 |
| `upload_service` | `failure_code(error)` | 보조 | 없음 |
| `upload_service` | `record_upload_failure(ids, stage, error, backend)` | 보조 | 없음 |
| `upload_service` | `read_attempt_source(ids)` | 보조 | 없음 |
| `upload_service` | `save_ingest_chunks(ids, company_id, result)` | 보조 | 없음 |
| `upload_service` | `verify_uploaded_chunks(ids, backend)` | 보조 | 없음 |
| `upload_service` | `ingest_and_save_chunks(source, info, ids, ingest_function, file_hash=None)` | 보조 | 없음 |
| `upload_service` | `embed_and_record_result(chunks, job_id, backend)` | 보조 | 없음 |
| `upload_service` | `run_attempt(actor, ids, expected_active_id, ingest_function, backend)` | 메인 | 없음 |
| `upload_service` | `prepare_upload_source(source_path, metadata, upload_dir)` | 보조 | 없음 |
| `upload_service` | `create_upload_candidate(actor, company_id, values, source, root)` | 보조 | 없음 |
| `upload_service` | `upload_document(actor, source_path, metadata, *, ingest_document=None, embedding=None, upload_dir=None)` | 메인 | 없음 |
| `upload_service` | `check_retry_allowed(document, version, reports)` | 보조 | 없음 |
| `upload_service` | `prepare_retry(actor, version_id)` | 메인 | 없음 |
| `upload_service` | `read_retry_records(database, actor, version_id)` | 보조 | 없음 |
| `upload_service` | `build_retry_report(root, reports)` | 보조 | 없음 |
| `upload_service` | `reset_retry_records(database, version_id, job_id, report)` | 보조 | 없음 |
| `upload_service` | `retry_upload(actor, version_id, *, ingest_document=None, embedding=None, source_path=None)` | 메인 | 없음 |
| `version_service` | `get_embedding_backend(embedding=None)` | 보조 | 없음 |
| `version_service` | `as_mapping(value)` | 보조 | 없음 |
| `version_service` | `call_external(function, *args, **kwargs)` | 보조 | 없음 |
| `version_service` | `read_candidate(database, document_id, version_id, *, lock=False)` | 보조 | 없음 |
| `version_service` | `manifest_snapshot(database, version)` | 보조 | 없음 |
| `version_service` | `validate_completeness(document_id, version_id, *, embedding=None)` | 메인 | 없음 |
| `version_service` | `chunk_matches_manifest(hit, expected, seen, identity)` | 보조 | 없음 |
| `version_service` | `compare_stored_chunks(identity, version, manifest, seal, ids, hits)` | 보조 | 없음 |
| `version_service` | `collect_chunk_comparison(identity, manifest, ids, hits)` | 보조 | 없음 |
| `version_service` | `inspect_chunk_contents(identity, expected, hits)` | 보조 | 없음 |
| `version_service` | `read_job(database, version_id, *, lock=False)` | 보조 | 없음 |
| `version_service` | `write_publication(database, job, reports, publication, *, complete=False, error_code=None)` | 보조 | 없음 |
| `version_service` | `ensure_no_pending_publication(database, document_id, *, except_job=None)` | 보조 | 없음 |
| `version_service` | `require_manager(actor, database, company_id)` | 보조 | 없음 |
| `version_service` | `publish_version(actor, document_id, candidate_id, expected_active_id, *, embedding=None)` | 메인 | 없음 |
| `version_service` | `check_publication_access(actor, document_id, candidate_id)` | 보조 | 없음 |
| `version_service` | `read_publishable_candidate(database, actor, document_id, candidate_id, company_id, seal)` | 보조 | 없음 |
| `version_service` | `prepare_publication(actor, document_id, candidate_id, company_id, expected_active_id, seal)` | 메인 | 없음 |
| `version_service` | `check_publication_conflict(database, document, version, expected_active_id)` | 보조 | 없음 |
| `version_service` | `store_pending_publication(*, database, document, candidate_id, expected_active_id, job, reports)` | 보조 | 없음 |
| `version_service` | `activate_candidate(backend, document_id, candidate_id, expected_count)` | 보조 | 없음 |
| `version_service` | `finish_publication(document_id, candidate_id)` | 보조 | 없음 |
| `version_service` | `recover_publication(document_id, version_id, *, embedding=None)` | 메인 | 없음 |
| `version_service` | `check_previous_version(database, document, document_id, previous_id)` | 보조 | 없음 |
| `version_service` | `prepare_recovery(document_id, version_id)` | 메인 | 없음 |
| `version_service` | `restore_database_pointer(*, database, document, version_id, publication, eligible)` | 보조 | 없음 |
| `version_service` | `restore_vectors(backend, document_id, version_id, recovery)` | 보조 | 없음 |
| `version_service` | `finish_recovery(document_id, version_id)` | 보조 | 없음 |
| `versions_repo` | `list_document_versions(actor, document_id, *, connection=None)` | 메인 | 없음 |
| `versions_repo` | `mark_failed(version_id, *, connection=None)` | 메인 | 없음 |

## 검증 범위

`test_schema_v4`는 직접 생성·기존 객체 무변경 거부·DDL/검증 실패 시 버전 미확정·동시 초기화·실제 제약·구조 훼손을 검증한다. `test_db_runtime`, `test_upload_workflow`, `test_final_db_contract`는 최종 DDL을 직접 사용해 회사 격리·경쟁·원자성·manifest·게시 CAS·복구·재시도를 검증한다. SQLite 입력/조회 테스트와 실제 MySQL 결과는 구분한다.

수동 API/UI 스크립트는 tests/smoke에 두며 Gradio는 수동 main에서만 import한다. 최종 검증 결과와 인증 PR #167 대조 범위는 [리팩토링 검증 보고서](../../../docs/db-design/V4_REFACTOR_VALIDATION.md)에 기록한다. Railway DB는 변경하지 않았다.
