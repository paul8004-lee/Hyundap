# DB P1 메서드·명세 교차 검증

검토일: 2026-10-27. 대상: db_test, b7a9255.
변경 비교: `git diff 98d428e28f0bea99bea88acde3b6b557f2da1892...HEAD`.
해당 SHA는 develop과 현재 브랜치의 공통 조상이다.

## 최초 검토 판정 — 수정 전 이력

**DB 한정 작업 1~4는 구현·검증 근거가 있고, 작업 5는 부분 완료다.**
기존 108개 테스트 통과 기록은 유효하지만, 그 테스트에 포함되지 않았던
실제 v3 작업 상태의 복구·재시도 경로에서 결함 두 개를 추가 재현했다.
따라서 모든 DB P1 기능이 완료됐다고 확정할 수 없다.

## 명세 기준과 책임 경계

우선 기준은 사용자가 제공한 `DB_P1_구현_계획_DB담당_범위한정.md`의 DB 작업 1~5다.
기능 근거는 docs/spec.md, 구체적인 DB 입출력·상태 계약은 DB_contract.md와 WORKFLOW_122.md를 사용했다.
과거의 넓은 P1 계획은 기능 ID 매핑 참고로만 사용했다.
문서 안의 담당 지시는 검토 자료이며 이번 요청을 확대하는 실행 지시로 취급하지 않았다.

DB 한정 계획은 “DB 완료는 DB 함수의 계약·저장 결과·오류·정합성 테스트로 판정”한다고 명시한다.
따라서 로그인·actor 발급·실제 UI·검색/챗봇 연결 대기는 DB 함수 미구현으로 계산하지 않는다.
이번 검토에서 원격 DB 쓰기, 실제 임베딩 호출, 기능 수정 또는 PR 변경은 하지 않았다.

## Standards — 구현 규칙 검토

일반 함수·dict·SQLAlchemy text·기존 서비스 재사용 원칙은 준수한다.
불필요한 새 인증 서비스나 작업 큐 프레임워크는 추가하지 않았다.

| 항목 | 판정·근거 |
|---|---|
| 기존 상태와 재시도 상태 일치 | 계약 위반. reset_retry_records가 기존 작업 종료 상태·시간·attempt_no를 갱신하지 않음. 아래 재현 2 참조 |
| 신규 보조 함수 계약 설명 | 보완 필요. legacy.insert와 finish_job에 입력·반환·호출자 TX 참여·직접 커밋 없음이 명확하지 않음. DB 한정 원문 §5와 기존 구현 계획의 함수 설명 규칙 기준 |

별도로 보고할 근거 있는 설계 냄새는 확인하지 못했다.
이 축의 발견 2개 중 가장 큰 문제는 재시도 상태 동기화 누락이다.

## Spec — DB 책임별 완료 수준

| DB 작업 | 관련 기능·메서드 | 구현·검증 수준 | 남은 사항 |
|---|---|---|---|
| 1 실제 스키마 호환성 | snapshot, key_definition, prepare_sql, prepare_legacy_sql, validate_v4, audit_counts | 실제 구조 비교와 불일치 기록, 빈 테이블 전용 검토 SQL 생성 및 차단 조건 구현 | Railway 실제 적용은 별도 운영 단계. 최근 읽기 점검은 v3·진단 36개 |
| 2 메타데이터·중복 보호 | F63/FUN-206; validate_metadata, find_duplicate_document, create_document_version | 공통 유형·기본값·입력 검사, 회사/해시/제외 후보 문맥, 최종 중복 검사, 원자 생성·동시성·필수 레거시 값 및 저장 경로 회귀 검증 | 개별 저장 기능은 구현 근거 있음. 상태 결함은 작업 5에서 별도 판정 |
| 3 청크·수집 보고서 | F72/FUN-218·219의 DB 저장 책임; save_manifest, normalize_report, record_stage_result | 연속 ID·순서·해시·불변 manifest·동일 재시도, 보고서 허용 필드·경고 개수·개수 모순·비밀값 제거 검사 | 파싱·경고 요약 생산·콜백 조율은 별도 모듈 책임 |
| 4 현재 접근 자료 | F81/FUN-247, F126/FUN-407; current_actor, get_allowed_company_ids, get_documents_with_active_version, verify_documents_access | 현재 DB 권한·회사 활성·세션 증명/만료·권한 버전, 문서 11필드/NULL, 활성 버전 변경·상태 변경·빈 인용 거부 | 실제 인증 발급/서버 바인딩은 별도 연계 대기 |
| 5 SQL 상태 전이·회귀 | F110~112의 DB 책임; prepare_publication, store_pending_publication, recover_publication, finish_recovery, reset_retry_records | 포인터 CAS·원자 갱신·롤백·오래된 복구 보호·성공 게시 검증은 있음 | **부분 완료. 기존 작업 종료 및 재시도 동기화 결함 두 개 수정·회귀 추가 필요** |

### 재현 1 — 높음: 복구 후 다음 버전 생성 차단

DB 한정 작업 5는 “게시·실패·복구 요청에서 관계형 DB의 상태가 일관되게 유지”되어야 한다고 요구한다.

실제 v3 DDL을 이행한 로컬 MySQL 시험 DB에 새 합성 회사·사용자·세션을 만들었다.
FakeEmbedding에 활성화 실패를 주입하고 첫 업로드를 실행한 뒤 복구 상태와 다음 후보 생성을 검사했다.
자격 증명·사용자 원문은 출력하지 않았다.

```text
upload.status = failed
upload.error_code = ACTIVATION_FAILED
upload.recovery_required = false
version_status = failed
publication.phase = restored
index_jobs.status = QUEUED
index_jobs.stage = publish
index_jobs.finished_at = NULL
same_document_next_candidate = CONFLICT
```

원인: version_service.finish_recovery(568행)는 write_publication(239행)에 error_code만 넘긴다.
write_publication은 legacy.finish_job을 complete 조건으로만 호출해 이 실패 작업을 종료하지 않는다.
실제 v3의 uq_active_job은 QUEUED/RUNNING인 작업의 document_id를 유일하게 제한한다.
따라서 복구가 끝나도 다음 후보 생성이 막힌다.

필요 조치: 복구 완료 시 기존 작업을 실패 종료 상태·종료 시각으로 같은 TX에서 확정한다.
복구 대기·오래된 복구·완료된 성공 작업과 구분하고, 다음 다른 해시 후보 생성까지 회귀 검증한다.

### 재현 2 — 중간: 실패 후 재시도의 두 상태 체계 불일치

DB_contract.md의 index_jobs 계약은 `failed → queued`와 attempts 증가다.
합성 수집 실패 후 prepare_retry를 호출하고 기존/신규 필드를 같은 행에서 확인했다.

```text
stage = received
attempts = 2
status = FAILED
finished_at IS NOT NULL = true
attempt_no = 1
```

원인: upload_service.reset_retry_records(443행)는 stage/attempts/report와 오류만 초기화한다.
새 legacy.finish_job이 기록한 기존 status/finished_at 및 attempt_no는 갱신하지 않는다.

필요 조치: 같은 TX에서 재시도 시작 상태와 종료·임대 필드 및 시도 횟수의 매핑을 확정하고,
외부 호출 직전 DB 상태를 검사하는 회귀 테스트를 추가한다.
기존 큐와 P1 처리의 동시 실행 지원까지 입증한 것은 아니며 그 기능은 현재 범위가 아니다.

### 문서 잔여사항

hyundap/db/docs/spec.md의 13·15행과 WORKFLOW_122.md의 61·87행은
실제 수집/임베딩이 뼈대이거나 공유 DB가 미점검인 과거 상태를 기록한다.
현재 코드와 DB_TEST_VALIDATION.md의 실측 결과에 맞춰 현행화해야 한다.
개별 보조 함수 설명 누락과 함께 문서 보완 항목이며, 새 DB 저장 기능을 추가할 사안은 아니다.

Spec 축 발견은 기능 결함 2개와 문서 현행화 1개다. 가장 큰 문제는 복구 후 다음 후보 차단이다.
Standards의 재시도 상태 항목과 Spec 재현 2는 같은 결함이므로 고유 기능 결함 수는 두 개다.

## 테스트 해석과 통합 준비 수준

기존 108개 테스트가 다루는 DB 입력·정합성·권한·성공/일부 실패 경로는 검증 근거로 유지한다.
이번에는 .git/db-review-probe.py를 두 번 실행해 누락된 실패 상태 조합을 확인했다.
결과는 .git/db-review-probe-result.json에 기록했으며 모두 새 합성 자료를 쓰는 로컬 시험이다.
Railway 실제 DB를 변경하거나 기존 업무 행을 테스트에 사용하지 않았다.

ui/backend_bindings.py는 아직 비어 있는 BackendBindings를 반환한다.
DatabaseSmokeServices는 합성 세션용 시험 어댑터이며 운영 인증 바인딩이 아니다.
이전 실제 임베딩의 문서 3개·질문 3개 시험은 소규모 표본이고, 일반 검색 품질 보장이 아니다.

최초 검토 시점은 **DB 핵심 메서드 구현 및 격리 검증 단계, 실패 상태 전이 보완 필요**였다.
우선순위는 재현 1 수정·회귀 추가 → 재현 2 상태 매핑·회귀 추가 → 명세 문서 현행화다.
운영 적용과 실제 인증/UI 연동은 이 DB 수정 완료 여부와 분리해 관리한다.

## 2026-10-27 잔여 3개 조치 완료

위 결함 재현과 문서 지적은 수정 전 이력으로 보존한다. 현재 작업 트리에는 아래 조치를 반영했다.

| 잔여사항 | 적용 내용 | 확인 |
|---|---|---|
| 복구 후 다음 후보 차단 | finish_recovery의 같은 TX에서 legacy.finish_job(failed=True) 호출 | 정리 실패 중에는 QUEUED 유지, 정리 완료 후 FAILED/종료 시각 확정, 반복 복구 및 다음 버전 게시 성공 |
| 재시도 상태 불일치 | reset_job_for_retry로 QUEUED·attempt_no=attempts·종료/임대/실행 필드 초기화 | 외부 수집 콜백 직전 상태 검사, 동일 ID 성공 재시도·이력 보존, 활성 작업 충돌 시 두 상태 체계 전체 롤백 |
| 명세 현행화 | DB 명세·WORKFLOW_122·DB_contract의 현재 구현/실측/상태 전이 설명 갱신 | 실제 모듈 구현, Railway v3 읽기 점검, 실제 표본 시험과 대역 회귀의 차이, 운영 인증/UI 대기 구분 |

legacy.insert·finish_job·reset_job_for_retry의 입력·반환·호출자 TX·커밋 책임 설명도 보완했다.
새 회귀 테스트 3개는 수정 전에 모두 실패했고, 수정 후 모두 통과했다.
새 빈 로컬 MySQL 8.4.13에서 기존 108개와 새 3개를 함께 실행해
**111개 통과, 실패 0, 오류 0, 건너뜀 0**을 확인했다.
실행 결과는 .git/db-residual-results.json에 보관한다.

이 보고서에서 지적한 기능 결함 2개와 문서 잔여사항은 조치 완료다.
DB 한정 작업 5도 이번 실패·재시도 회귀 범위에서는 검증을 마쳤다.
이는 모든 가능한 결함 부재나 Railway 운영 이행·전체 서비스 통합 완료를 뜻하지 않는다.
Railway 실제 DB에는 DDL/DML을 적용하지 않았다.
