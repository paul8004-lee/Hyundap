# DB 최종 v4 구현 명세 — #168

인증 PR #167 병합 후 계정·세션 저장과 fixture도 고정 v4에 맞췄다. legacy·구조 캐시를 복원하지 않았다. 최신 검증은 [인증 정합화 보고서](../../../docs/db-design/V4_AUTH_ALIGNMENT_2026-10-27.md)의 전체 332개 통과를 따른다.

대상: feature/db. 2026-10-27 사용자 결정으로 v3 이행·과거 혼합 v4 호환은 폐기했다. 현재 생성 진입점은 `schema.create_v4_schema(engine)` 하나다. [초기화 절차](../../../docs/db-design/V4_INITIALIZATION.md), [물리 계약](../../../docs/db-design/DB_schema.md), [공개 함수 계약](../../../docs/db-design/DB_contract.md)을 따른다.

## 구조와 저장 계약

11개 최종 테이블을 빈 DB에 직접 생성하고 구조 검증 뒤 버전 4를 기록한다. 기존 DB·부분 생성 DB는 거부하며 자동 DROP은 없다. 관리 실측은 매번 조회하고, 업무 Engine은 최초 준비 시 최종 계약을 검사한다. 이후 업무 SQL에는 컬럼 존재 검사·구조 캐시·동적 INSERT가 없다.

파일 정보·해시·경로·크기의 기준은 document_versions다. documents에는 회사·공개/삭제 상태·활성 포인터·업로더를 저장한다. 작업의 상태(status)와 단계(stage)는 구분하고 시도 횟수는 attempts에만 기록한다. 문서당 활성 작업 하나와 버전당 작업 하나를 DB에서 보호한다.

작업 결과는 `jobs_repo.store_job_result`에서 단계·보고서·오류·상태·종료 시각을 한 UPDATE로 저장한다. 게시/복구 대기에는 QUEUED와 활성 보호를 유지하고, 복구 완료 또는 최신 포인터로 대체된 작업은 FAILED로 종료한다. 재시도는 `jobs_repo.reset_job_for_retry`에서 QUEUED·received·attempts 증가·보고서 이력·시간 초기화를 한 번에 저장한다. 다른 활성 작업과 충돌하면 버전 변경까지 함께 취소된다.

BIGINT 시간은 UTC Unix 초, DATETIME(6)은 UTC다. 세션은 필수 CSRF 해시·권한 버전과 expires_at·유휴/절대 만료를 모두 검사한다. 사용자의 역할·회사 입력은 신뢰하지 않고 현재 DB 값을 재조회한다. 운영 인증 발급 구현은 별도 인증 모듈 책임이다.

## 입력·반환·오류·트랜잭션

기존 공개 메인 함수의 서명과 dict/list 반환을 유지한다. 검색용 원시 조회는 document_id/company_id/company_name/doc_type/publication_status/deleted_at/active_version_id/version_no/version_status/data_category/file_name의 11필드다. 없는 ID는 누락하며 빈 입력은 DB 접근 없이 []다.

문서 생성은 `{document_id,version_id,job_id}`를 반환한다. 업로드 메타데이터의 doc_type 누락은 공통 기본값이며 명시적 잘못된 값은 INVALID_INPUT이다. manifest는 버전 ID 기반 청크 ID·연속 순번·해시 목록이고 본문·벡터는 저장하지 않는다. 보고서는 허용 필드만 정규화하며 원문·비밀값·원시 예외를 저장하지 않는다.

주요 오류는 INVALID_INPUT/INVALID_STATE/DUPLICATE_FILE/CONFLICT/DB_UNAVAILABLE, 접근 오류는 SESSION_EXPIRED/ACCESS_DENIED/STATE_CHANGED다. 함수가 외부 connection을 받으면 호출자 TX에 참여하며 직접 커밋·종료하지 않는다. 생략하면 runtime.transaction이 커밋/롤백을 소유한다. 초기화 DDL은 업무 TX와 별개다.

## 모듈과 메인·보조 함수

업무 코드는 모듈 함수이며 새 클래스는 없다. 오류 형식 `errors.DbError`만 예외 클래스다. 테스트의 unittest.TestCase와 수동 시험 어댑터 클래스는 업무 서비스 클래스가 아니다. 아래 목록은 현재 소스의 함수 서명을 기준으로 작성했다.

| 모듈 | 함수 서명 | 역할 | 클래스 |
|---|---|---|---|
| `chunks_repo` | `save_manifest(version_id, chunks, *, connection=None)` | 메인 | 없음 |
| `chunks_repo` | `_normalize_manifest(version_id, chunks)` | 보조 | 없음 |
| `chunks_repo` | `_chunk_index(chunk)` | 보조 | 없음 |
| `chunks_repo` | `get_expected_chunk_ids(version_id, *, connection=None)` | 메인 | 없음 |
| `clock` | `epoch_now()` | 보조 | 없음 |
| `connection` | `create_db_engine()` | 메인 | 없음 |
| `connection` | `main()` | 메인 | 없음 |
| `documents_repo` | `find_duplicate_document(company_id, sha256, *, exclude_version_id=None, connection=None)` | 메인 | 없음 |
| `documents_repo` | `get_documents_with_active_version(document_ids, *, connection=None)` | 메인 | 없음 |
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
| `upload_service` | `failure_code(error)` | 보조 | 없음 |
| `upload_service` | `record_upload_failure(ids, stage, error, backend)` | 보조 | 없음 |
| `upload_service` | `read_attempt_source(ids)` | 보조 | 없음 |
| `upload_service` | `save_ingest_chunks(ids, company_id, result)` | 보조 | 없음 |
| `upload_service` | `verify_uploaded_chunks(ids, backend)` | 보조 | 없음 |
| `upload_service` | `ingest_and_save_chunks(source, info, ids, ingest_function)` | 보조 | 없음 |
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
