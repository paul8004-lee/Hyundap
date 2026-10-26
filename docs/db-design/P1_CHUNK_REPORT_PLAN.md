# 묶음 B · 청크와 수집 보고서 저장 기획

기준: db01, 2026-10-26. 대상 이슈 #157·#158·#159.
현재 문서 저장 묶음의 커밋은 `e7ec32b`다. 이 문서는 다음 구현의 기획이며
아래 변경을 이미 구현하거나 수집 담당과 합의했다는 뜻이 아니다.

## 1. 확인한 현재 동작과 문제

| 위치 | 현재 동작 | 이번 묶음의 판단 |
|---|---|---|
| chunks_repo.save_manifest | 버전 잠금 뒤 청크 목록과 expected_chunk_count를 같은 TX에 저장. 동일 목록 재시도 허용 | 기존 구현을 우선 재사용하고 계약 테스트 보강 |
| jobs_repo.normalize_report | 단계·식별자·embedding 수치·실패 정보만 허용 | 수집 전용 필드를 명시적으로 추가 |
| jobs_repo.record_stage_result | 작업 행 잠금, 소속·단계 확인, 기존 성공 보호 후 JSON 저장 | 수집 성공 보고서에 개수가 있으면 DB manifest와 비교 |
| ingest.pipeline.build_ingest_report | parser/parser_version, chunk_size/overlap, section_count/chunk_count와 warnings 원문 생성 | 생산부는 수정하지 않는다. warnings에서 warning_count를 추측/생성하지 않음 |
| upload_service.save_ingest_chunks | manifest 저장 후 보고서를 별도 TX로 저장 | 전체 결과의 원자성을 주장하지 않는다. 이번 변경은 각 저장소 함수의 TX만 보장 |

## 2. 구현 순서와 산출물

### B1. 청크 저장 경계 검증 · #157 · 30~45분

대상은 chunks_repo.py와 DB 테스트다. 입력은 기존
`save_manifest(version_id, chunks, *, connection=None)`을 유지한다.

1. ID·순번·해시·1~100000개 규칙과 bool 순번 거부를 오프라인 테스트로 확인한다.
2. 순서가 다른 동일 목록은 같은 정규화 결과인지 확인한다.
3. 격리 MySQL에서 동일 목록 재시도·누락/추가/해시 변경 충돌을 검증한다.
4. processing 외 상태의 거부와 실패 시 청크/예상 개수 롤백을 확인한다.
5. 입력 설명·오류·연결 소유권을 DB_contract.md와 대조한다.

완료 증거: 순번 누락/중복/잘못된 ID/잘못된 해시/활성 버전 보호 테스트와
실제 MySQL 결과. SQLite는 MySQL 잠금 검증의 대체 증거로 사용하지 않는다.

### B2. 수집 보고서 정규화 · #158 · 45~75분

공개 서명 `normalize_report(report)`는 유지한다. 기존 `_report_metrics`를
확장하거나 수집 필드를 검사하는 보조 함수 하나를 추가한다. 새 클래스·테이블은 없다.

| 필드 | 제안 검사 | 누락 정책 |
|---|---|---|
| parser | pypdf/python-docx/python-pptx 중 문자열 | 기존 보고서 호환을 위해 생략 가능 |
| parser_version | 1~64자, ASCII 영숫자 및 `._+-`만 허용 | 생략 가능. parser 없이 단독 제공 허용 여부는 구현 전에 확정 |
| chunk_size | bool 제외 정수, 0 초과 | 생략 가능 |
| chunk_overlap | bool 제외 정수, 0 이상. size와 함께 있으면 size 미만 | 생략 가능 |
| section_count/chunk_count/warning_count | bool 제외 정수, 0 이상 | 생략 가능 |

1. 필드의 명시적 None·bool·음수·빈 문자열·허용 범위 밖 값을 INVALID_INPUT으로 거부한다.
2. 새 수집 필드는 ingest 단계에서만 받을지 확정하고 다른 단계의 잘못된 필드 혼입을 테스트한다.
3. warnings 원문·본문·토큰·원시 예외·미등록 필드를 제거하고 입력 dict는 변경하지 않는다.
4. 기존 dict/dataclass 입력, 성공/실패 모순 거부와 embedding 보고서 결과를 유지한다.
5. 수집 담당에게 필요한 전달 규격을 문서로 남긴다. 실제 경고 요약 생성·연결은 구현하지 않는다.

완료 증거: 허용 필드 보존, 타입/범위/size-overlap 모순, 비밀값 제거,
embedding 회귀를 DB 없이 확인한다. 파서 허용값 확대는 임의 추측하지 않는다.

### B3. 저장 시 청크 수·이력 정합성 · #159 · 45~75분

공개 서명 `record_stage_result(job_id, report, *, connection=None)`는 유지한다.
정규화는 메모리 검사만 하고 DB 비교는 저장 경계에서 수행한다.

1. 검증 조건은 `stage=ingest`, `status=success`, chunk_count가 명시된 경우로 제안한다. 기존 개수 없는 보고서는 허용하며 실패 보고서 0개는 성공 적재로 취급하지 않는다.
2. 명시된 count가 저장 버전의 expected_chunk_count와 같은지, 실제 document_chunks 행 수와도 비교할지 확정한다. 비교 생략·부적합 상태·불일치의 오류는 각각 계약에 기록한다. 모순 입력은 INVALID_INPUT, 저장 버전 상태 부적격은 INVALID_STATE가 후보 규칙이다.
3. 기존 코드의 작업 행 잠금과 manifest의 버전 행 잠금을 조사해 잠금 순서를 먼저 확정한다. 다른 경로가 버전→작업 순서라면 작업→버전 잠금을 새로 추가하지 않는다. 필요하면 같은 TX의 선행 조회→버전 잠금→작업 잠금→소속 재확인 방식으로 맞춘다.
4. 검사 실패 시 JSON·stage·오류 열을 갱신하지 않는지 검증한다. 성공 단계 재기록 보호, 늦은 단계 거부, 다른 단계와 `_attempts` 보존을 함께 확인한다.
5. 호출자 연결의 임의 커밋·종료가 없는지 확인한다. manifest와 보고서를 하나의 TX로 묶는 호출부 변경은 이번 구현에 포함하지 않는다.

완료 증거: 실제 격리 MySQL의 개수 일치/모순·잘못된 소속·성공 덮어쓰기 거부·
이전 시도 보존·중간 실패 롤백. 잠금 순서가 정리되기 전 DB 비교 코드를 확정하지 않는다.

## 3. 검증 실행과 완료 구분

```powershell
python -m unittest hyundap.tests.test_document_storage hyundap.tests.test_db_connection hyundap.tests.test_schema_migration hyundap.tests.test_db_runtime hyundap.tests.test_upload_workflow
```

새 보고서 단위 테스트를 별도 파일로 추가하면 위 명령에도 포함한다.
현재 검증 기준선은 51개 중 15개 통과·36개 MySQL 설정 부재로 skip이다.
통합 테스트는 기존 호스트/빈 DB 제한을 유지하고 고정 버전 환경에서 실행한다.
DB 설정이 없으면 정규화 구현과 오프라인 검증을 진행하고 MySQL 검증은 대기로 남긴다.

세 단계의 순수 예상 작업량은 2~3시간 15분이다. 격리 MySQL 환경 구축·계약 응답은 별도다.
다음 실행은 B1의 기존 manifest 경계 테스트 확인과 B2의 허용 필드 단위 테스트 작성이다.
