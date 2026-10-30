# DB 전 파일 학습 중심 리팩토링 — 선정 목록과 검증 보고서

기준: 2026-10-28, `db-p3-test`, HEAD `ee77c11`과 기존 미커밋 회사별 DB 분리 패치.
이 문서는 이번 리팩토링을 시작하기 전에 선정 목록을 작성하고 완료 결과를 채운다.
이전 회사별 DB 구현·리팩토링과 이번 추가 변경은 구분한다.

## 학습 근거와 보존 규칙

`C:/workspace/chatbot/langchain-web-chatbot`의 학습 노트 40개를 검토했다.
함수, if, for, dict/list, try/except, with가 확인됐으며 ContextVar·RLock·contextmanager·lru_cache는
파싱 가능한 학습 코드에서 확인되지 않았다. 문법 등장 여부를 개인의 숙련도로 단정하지 않는다.
기존 P1/P2 작성 규칙의 일반 함수·명시적 조건/반복·역할별 보조 함수·한국어 설명을 적용한다.

반환 필드·오류 코드·검사 순서·원본 입력 보존·트랜잭션 범위·SQL 잠금 순서·회사 격리를 유지한다.
정규식의 입력 검증, ContextVar, RLock, contextmanager, Engine 캐시는 보존하고 필요 이유를 설명한다.
외부 인증/UI 코드와 Railway를 변경하지 않는다. 실행은 새 격리 MySQL과 테스트 대역으로 검증한다.

## 전체 파일 검토

| 파일 | 검토 결과와 이번 처리 |
| --- | --- |
| `__init__.py` | 패키지 설명과 오류 공개만 있어 유지 |
| `clock.py` | UTC 정수 초 함수가 짧고 명확해 유지 |
| `errors.py` | 기존 예외 클래스·고정 메시지 계약 유지 |
| `chunks_repo.py` | 입력 검증/저장 분리와 중첩 반복 설명이 이미 있어 유지 |
| `sessions_repo.py` | 세션→사용자→회사 순서와 보조 함수가 명확해 유지 |
| `versions_repo.py` | 이력 반환·실패 처리 흐름이 명시적이므로 유지 |
| `tenancy.py` | 직전 리팩토링의 명시적 조건문과 문맥/잠금 설명 유지 |
| `company_admin.py` | 직전 리팩토링의 회사 생성 5단계와 실패 설명 유지 |
| `connection.py` | URL 선택·긴 유효성 조건을 순서대로 분리 |
| `runtime.py` | 연결 풀 선택을 with 문 밖으로 분리 |
| `documents_repo.py` | 내포식·조건부 반환·필수 필드 집합 검사를 명시적 반복으로 변경 |
| `jobs_repo.py` | any/sum 생성식과 최소 개수 조건식을 풀어 쓰기 |
| `schema.py` | lambda·내포식·SQL 순회·버전 변환·CLI 위반 판정을 단계별로 변경 |
| `split_schema.py` | MySQL 버전 변환 과정을 명시적으로 변경 |
| `upload_service.py` | 경고 검사와 재시도 회사 선택을 명시적으로 변경 |
| `version_service.py` | 원장 스냅샷·작업 보고서·게시 상태·회사 선택·미완료 검사 개선 |

`hyundap/db/docs/spec.md`도 역할·계약을 대조했다. `.py` 16개 전부가 검토 대상이며,
수정 이점을 찾은 8개 파일만 변경한다. 줄 수 감소보다 업무 흐름을 읽는 순서를 우선한다.

## 사전 선정 목록 — 이 순서로 실행

| 순서 | 함수 | 변경 방식 | 검증 | 상태 |
| --- | --- | --- | --- | --- |
| 1 | `connection.create_db_engine`, `runtime.transaction` | URL 기본값·필수값 검사, 선택 Engine을 중간 변수와 if로 분리 | 접속 설정·오류 비노출·기본 runtime | 완료: 10개 통과 |
| 2 | `documents_repo.find_duplicate_document`, `get_documents_with_active_version`, `validate_metadata` | 조건부 반환, ID 집합/행 변환/필드 복사를 명시적 for로 작성 | 중복 제외·입력 경계·상세/접근 조회 | 완료: 23개 통과 |
| 3 | `jobs_repo._ingest_metrics`, `_check_ingest_count` | 단계 필드 검사와 실제 개수 누적, 최소값을 if/for로 작성 | 보고서 경계·수집 개수 정합성·실패 시 보존 | 완료: 36개 통과 |
| 4 | `schema._expression` | lambda를 이름 있는 보조 함수로 분리, 문자열 조각별 정규화 | 인용 문자열·대소문자·문자셋 보존 비교 | 완료: 3개 통과 |
| 5 | `schema.create_v4_schema`, `audit_counts`, `main`, `split_schema.create_split_schema` | 버전 숫자 변환, SQL 설명 줄 제거/조회, 위반 판정 순서 표시 | 실제 MySQL 스키마·제약·DDL 실패·분리 모드 | 완료: 23개 통과 |
| 6 | `version_service.read_candidate`, `manifest_snapshot`, `read_job` | 잠금 접미사, 원장/버전 필드 생성, JSON 분기를 명시화 | 후보 소속·스냅샷 해시·작업 보고서·완전성 | 완료: 4개 통과 |
| 7 | `version_service.write_publication`, `ensure_no_pending_publication`, `publish_version`, `recover_publication` | 게시 단계·오류 문구·충돌 검사·회사 선택 분리 | 게시·부분 실패·복구·동시성 회귀 | 완료: 8개 통과 |
| 8 | `upload_service.save_ingest_chunks`, `retry_upload` | 경고 원소별 검사와 재시도 회사 선택 분리 | 원본 보존·경고 비노출·재시도 회귀 | 완료: 통합 5개 + 연동 11개 통과 |

항목별 관련 검증이 통과한 뒤 다음 항목으로 이동한다. 마지막에 전체 테스트를 실행한다.
신규 기능·스키마 변경·API 호출은 추가하지 않는다.

## 최종 결과

선정한 8단계의 구현과 관련 테스트를 모두 완료했다. 최종 전체 회귀 결과는 아래에 기록한다.

이번 추가 작업은 Python 파일 8개, 기존 함수 21개 수정과 보조 함수 1개 추가다.
새 보조 함수는 `schema._ascii_literal(match)`이며 클래스 없는 모듈 함수다.
`_expression()`의 `re.sub()`가 문자열 일치 부분마다 호출한다. 기존 lambda의 역할에 이름을 붙인 것이다.
공개 함수 이름·인자·반환값은 유지했고 SQL 테이블, 상태 전이, 권한 규칙을 변경하지 않았다.

### 변경 전후 예시

문서 ID의 검증과 중복 제거를 한 줄에서 각각의 동작으로 풀었다.

```python
# 변경 전
ids = sorted({require_id(value) for value in document_ids})

# 변경 후
unique_ids = set()
for value in document_ids:
    unique_ids.add(require_id(value))
ids = sorted(unique_ids)
```

원장의 실제 개수는 생성식을 sum에 넘기는 대신 누적 과정을 표시했다.
행을 한 번씩 읽는 점과 중간 목록을 만들지 않는 점은 같다.

```python
# 변경 전
actual = sum(1 for _ in rows)

# 변경 후
actual = 0
for _ in rows:
    actual += 1
```

작업 보고서는 문자열 JSON, 이미 해석한 dict, 빈 값의 처리 경로를 나눴다.
실제 값의 유효성 검사는 이 분기 다음에 기존대로 수행한다.

```python
stored_report = job['report']
if isinstance(stored_report, str):
    reports = json.loads(stored_report)
elif stored_report:
    reports = stored_report
else:
    reports = {}
```

### 유지한 기술과 이유

| 기술 | 유지 이유와 읽는 기준 |
| --- | --- |
| ContextVar·회사별 RLock | 요청의 회사 분리와 동일 회사 처리 보호. 회사 문맥 설정/복원과 잠금 범위를 한 쌍으로 읽는다. |
| contextmanager·yield·try/finally | 성공·실패 모두에서 트랜잭션과 문맥을 정리한다. 호출자가 넘긴 연결의 커밋 책임도 보존한다. |
| SQLAlchemy text·바인딩·FOR UPDATE | 값의 안전한 전달과 경쟁 요청의 데이터 보호에 필요하다. SQL 실행 순서와 잠금 범위를 유지한다. |
| 정규식·해시·원자적 파일 공개 | 입력 허용 범위, 내용 변경 검출, 기존 원본 덮어쓰기 방지 계약이다. 단순 문자열 검사나 일반 복사로 대체하지 않는다. |
| dataclass 변환·getattr·inspect.signature | 다른 담당 모듈의 기존 자료형·함수 계약을 받기 위한 경계 처리다. 업무 내부로 확장하지 않는다. |

이번 수정 직전 대비 DB 폴더의 조건부 표현식 12개, 리스트/dict/set 내포식 6개,
생성식 7개, lambda 1개를 명시적 구문 또는 이름 있는 함수로 바꿨다.
이 문법 자체를 금지하는 규칙은 아니며, 현재 사용자의 학습과 흐름 추적에 맞춘 선택이다.

### 검증 기록

단계별 테스트 수에는 같은 경로의 재검증이 포함되므로 합산하여 전체 테스트 수로 사용하지 않는다.
1·2·4단계와 8단계 연동 테스트는 외부 DB 없이 실행했고, 3·5·6·7·8단계의 통합 검증은
새 로컬 MySQL 8.4.13에서 실행했다. 실제 OpenAI/Chroma 호출은 대역을 사용했다.

문자열 정규화에는 새 회귀 테스트 1개(하위 사례 5개)를 추가했다.
None, ASCII 문자셋 접두사, 따옴표 이스케이프, 한글 문자셋 표기, 리터럴 대소문자 보존을 검증한다.

최종 전체 명령: `python -m unittest discover -s hyundap/tests -t .`
전체 실행 결과: **437개 통과, 실패·오류·건너뜀 0개**. 실행 시간 86.208초, 프로세스 종료 코드 0.
기존 436개에 문자열 정규화 회귀 테스트 1개가 추가됐다.

모든 단계의 실행 로그는 이번 세션의 임시 폴더 `learning-step3.log`, `learning-step5.log`,
`learning-step6.log`, `learning-step7.log`, `learning-step8.log`, `learning-final.log`에 남겼다.
컴파일 및 `git diff --check`도 통과했다. DB Python 파일의 한자·깨진 문자 검사에서도 검출 사항이 없었다. Railway 데이터 변경·커밋·푸시는 이번 작업에 포함하지 않는다.
