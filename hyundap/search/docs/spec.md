# search — 통합 검색 작업 명세

담당: 이준영 · 사용 안내: [`../README.md`](../README.md) · 연동 계약: [`DB_contract.md`](../../../docs/db-design/DB_contract.md) · 기준: 통합 기능명세서 F80~F92, MOD-050~054, CLS-050~055, FUN-240~257, L50~L64

## 1. 한 줄 요약

로그인한 사용자가 볼 수 있는 문서 안에서만 질문과 비슷한 청크를 찾아 점수 높은 순으로 돌려준다. 화면용 `search`(문서 단위)와 챗봇용 `search_chunks`(청크 단위)가 같은 공통 단계(`run_chunk_search`)를 써서 권한·점수·공개 기준이 같다.

### 담당 범위

| 하는 일 (search) | 하지 않는 일 (담당) |
|---|---|
| 검색어·숫자 인자 검사 | 권한 판정·세션 검증 (auth, 전재경) |
| auth 허용 회사 ∩ 사용자 선택으로 검색 범위 결정 | DB 저장·조회 SQL (db, 전재경) |
| 문서 유형 정리·검증 | 임베딩·벡터 저장·공개 전환 (embedding, 박영준) |
| 벡터 조회 조건 조합, 최소 점수 적용 | 위치 문구 생성 (ingest, 김혜리) |
| DB 기준 공개 조건 재확인 (DocumentInfo 변환·is_visible) | 답변·출처 문장·요약 (chatbot, 소용우) |
| 결과 조립(문서 묶기·발췌), 오류 변환, 화면 안내 변환 | 화면 표시·연결 (ui, 박경환) |

## 2. 파일 구성

| 파일 | 모듈 ID | 내용 |
|---|---|---|
| `__init__.py` | MOD-050 | 공개: `search`, `search_chunks`, `SearchError` |
| `models.py` | MOD-051 | 자료형 CLS-050~054 |
| `errors.py` | MOD-052 | CLS-055 `SearchError`, FUN-256·257, 오류 표, `to_feedback` |
| `filters.py` | MOD-053 | FUN-253~255 회사 범위·유형·검색 조건 |
| `search.py` | MOD-054 | FUN-240~252 검사·설정·조회·거르기·가공 |
| 시험 | - | `hyundap/tests/test_search.py` |

## 3. 처리 흐름

```
search(user, query, company_ids, doc_types, limit, chunks_per_document, min_score)        FUN-243
 ├ validate_query · validate_range · get_search_settings                                    FUN-240~242
 ├ top_k = min(limit × chunks_per_document, top_k_max)
 ├ resolve_company_scope (auth.get_allowed_company_ids ∩ company_ids)                       FUN-253
 ├ normalize_doc_types (DOC_TYPE_CODES)                                                     FUN-254
 ├ run_chunk_search                                                                         FUN-245
 │   ├ build_search_filter → embedding.build_where_filter (회사·유형·문서·is_active)          FUN-255
 │   ├ embedding.embed_query → embedding.query_vectors(top_k=)
 │   ├ filter_by_min_score                                                                  FUN-246
 │   ├ fetch_document_info → db.get_documents_with_active_version (1회)                     FUN-247
 │   ├ is_visible (8조건)                                                                   FUN-248
 │   └ to_chunk_hit                                                                         FUN-249
 └ group_hits_by_document → to_citation → make_excerpt → SearchResponse                     FUN-250~252

search_chunks(user, query, company_ids, doc_types, document_ids, top_k, min_score)          FUN-244
 └ 검사·설정·범위·유형 → (document_ids=[]면 빈 결과) → run_chunk_search → list[ChunkHit]
```

다른 폴더 오류는 `to_search_error`(FUN-256)가 `SearchError`로 바꾼다.

## 4. 함수 명세

### search.py

| ID | 함수 | 입력 → 출력 | 규칙·오류 |
|---|---|---|---|
| FUN-240 | `validate_query(query)` | str → 정리된 str | 앞뒤 공백 제거, 문자열 아님·빈 값 `EMPTY_QUERY`, 2000자 초과 `QUERY_TOO_LONG` |
| FUN-241 | `validate_range(value, name, low, high)` | 숫자 → 그대로 | `min_score`는 int·float, 나머지(limit·chunks_per_document·top_k)는 **int만**. bool 거부. 범위 밖 `INVALID_MIN_SCORE` / `INVALID_LIMIT` |
| FUN-242 | `get_search_settings()` | → `{min_score, top_k_max}` | `hyundap.common.config.get_settings()`의 `search_min_score`·`search_top_k_max`. 기본값·범위 처리는 config |
| FUN-243 | `search(user, query, company_ids=None, doc_types=None, limit=10, chunks_per_document=4, min_score=None)` | → `SearchResponse` | limit 1~20, chunks_per_document 1~20. 결과 없음은 빈 목록 |
| FUN-244 | `search_chunks(user, query, company_ids=None, doc_types=None, document_ids=None, top_k=None, min_score=None)` | → `list[ChunkHit]` 점수 내림차순 | top_k 기본 10, 1~top_k_max. min_score 기본 설정값. `document_ids=None` 제한 없음, 정리 후 빈 목록이면 `[]` 반환(권한 확인 후) |
| FUN-245 | `run_chunk_search(query, company_ids, doc_types, document_ids, top_k, min_score)` | → `list[ChunkHit]` | 임베딩·벡터 오류는 `to_search_error`. 최소 점수 후 비면 DB 조회 없이 `[]` |
| FUN-246 | `filter_by_min_score(hits, min_score)` | → list | 점수 None 또는 기준 미만 제거, 순서 유지 |
| FUN-247 | `fetch_document_info(document_ids)` | → `dict[str, DocumentInfo]` | 빈 입력 `{}`. DB에 없는 ID는 누락(숨김). `DbError` → `SEARCH_UNAVAILABLE`, 그 외 예외는 그대로 올림 |
| FUN-248 | `is_visible(info, hit, company_ids)` | → bool | 아래 7절의 8조건 모두 참일 때만 True |
| FUN-249 | `to_chunk_hit(hit, info)` | → `ChunkHit` | 회사명·파일명·버전 번호·유형은 DB, version_id·section_no·location_label·chunk_index는 메타데이터. section_no `""` → None |
| FUN-250 | `group_hits_by_document(hits, limit, chunks_per_document=4)` | → `list[SearchResult]` | 문서 점수 = 최고 청크 점수, (점수 내림차순, 파일명순), 상위 limit개, 문서당 근거 chunks_per_document개 |
| FUN-251 | `to_citation(hit)` | → `SearchCitation` | 발췌는 make_excerpt |
| FUN-252 | `make_excerpt(text, max_chars=200)` | → str | 연속 공백 1칸, 200자 초과 시 잘라 `…` |
| (창구) | `_embed_query`, `_query_vectors`, `_load_document_rows` | - | 다른 폴더 함수 호출 지점. 호출할 때 import(시험에서 바꿔 끼움) |

### filters.py

| ID | 함수 | 입력 → 출력 | 규칙·오류 |
|---|---|---|---|
| FUN-253 | `resolve_company_scope(user, requested_company_ids=None)` | → `list[str]` | auth 오류는 `to_search_error` 후 `pick_company_scope` |
| (명세 외) | `pick_company_scope(allowed, requested)` | set, list → `list[str]` | 허용 없음 `NO_COMPANY_SCOPE`, 선택 없음 → 허용 전체(정렬), 허용 밖 선택 `COMPANY_FORBIDDEN`, 정상 → 선택(중복 제거·순서 유지) |
| FUN-254 | `normalize_doc_types(doc_types)` | → `list[str]` 또는 None | 빈 입력 None(전체). 공백·빈 값·중복 제거. `DOC_TYPE_CODES` 밖 `INVALID_DOC_TYPE` |
| FUN-255 | `build_search_filter(company_ids, doc_types=None, document_ids=None)` | → where dict | 회사 없음 `NO_COMPANY_SCOPE`. `embedding.build_where_filter` 호출(공개 is_active 조건 포함). 공개 확정은 DB(FUN-248) |
| (창구) | `_load_allowed_company_ids`, `_build_where_filter` | - | auth·embedding 호출 지점 |

### errors.py

| ID | 이름 | 내용 |
|---|---|---|
| CLS-055 / FUN-257 | `SearchError(code, message='', status=400, retryable=False)` | message가 비면 `ERROR_MESSAGES[code]` |
| FUN-256 | `to_search_error(exc)` | 8절 변환표. 원문은 로그에만 |
| (명세 외) | `build_error(code, retryable=False)` | `ERROR_STATUS`로 HTTP 상태를 채운 SearchError |
| (명세 외) | `is_access_error(exc)`, `is_db_error(exc)` | `hyundap.auth.errors.AccessError`, `hyundap.db.errors.DbError` 여부 |
| (명세 외) | `to_feedback(error)` | SearchError → `{status, message, error_code}`(UI 6.2 규격), 그 외 None |

## 5. 데이터 형식 (`models.py`, dataclass)

| 자료형 | 필드 |
|---|---|
| CLS-050 `DocumentInfo` | document_id, company_id, company_name, doc_type, publication_status, deleted_at, active_version_id, version_no, version_status, data_category, file_name — DB 계약 11필드와 같은 이름. 버전 관련·file_name은 None 가능 |
| CLS-051 `ChunkHit` | chunk_id, document_id, version_id, version_no, company_id, company_name, doc_type, file_name, section_no, location_label, chunk_index, text, score — **chatbot이 이름으로 읽고 요약에서 직접 생성**. score·file_name·version_no·section_no·chunk_index는 None 가능 |
| CLS-052 `SearchCitation` | chunk_id, document_id, version_id, company_name, file_name, location_label, excerpt, score |
| CLS-053 `SearchResult` | document_id, version_id, version_no, company_id, company_name, doc_type, file_name, score, citations |
| CLS-054 `SearchResponse` | query, results, total_hits(청크 수), company_ids, doc_types, elapsed_sec |

## 6. 다른 폴더와의 연결

| 방향 | 함수 | 계약 |
|---|---|---|
| ui → search | `search(user=, query=, company_ids=, doc_types=, limit=10)` | 전체 필터는 None. 오류는 `to_feedback` |
| chatbot → search | `search_chunks(user=, query=, company_ids=, doc_types=, document_ids=, top_k=16)` | `ChunkHit` 목록 |
| search → auth | `hyundap.auth.permissions.get_allowed_company_ids(user)` | set[str]. 허용 없음·세션 문제는 `AccessError` |
| search → db | `hyundap.db.documents_repo.get_documents_with_active_version(ids)` | 11필드 dict 목록, 없는 ID 누락, 장애는 `DbError` |
| search → embedding | `embed_query(q)`, `build_where_filter(company_ids, *, doc_types, document_ids)`, `query_vectors(vector, where, *, top_k)` | score = 1 − cosine 거리(0~1), 메타데이터 None은 `""`로 저장됨 |
| search → common | `get_settings()`, `DOC_TYPE_CODES` | 기본 0.3·80, manual/policy/guide/other |

`user`는 서버가 만든 사용자 문맥이며 search는 내용을 보지 않고 auth에 그대로 넘긴다.

## 7. 기본값·규칙 (제약_운영 L50~L64)

| ID | 항목 | 값 |
|---|---|---|
| L50 | 검색어 길이 | 1~2000자 (`MAX_QUERY_CHARS`) |
| L51 | 결과 문서 수 | 1~20, 기본 10 (`MAX_LIMIT`, `DEFAULT_LIMIT`), 정수만 |
| L52 | 문서당 근거 | 기본 4 (`CHUNKS_PER_DOCUMENT`) |
| L53 | 벡터 조회 개수 | search: limit × chunks_per_document, 상한 설정 80 / search_chunks: 기본 10 (`DEFAULT_TOP_K`) |
| L54 | 최소 점수 | 설정 `SEARCH_MIN_SCORE`, 기본 0.3 (0~1) |
| L55 | 회사 범위 | auth 허용 회사 ∩ 사용자 선택 |
| L56·L57 | 공개 조건·판단 기준 | DB 기준 8조건 (아래) |
| L58 | 평가 자료 | 조회 뒤 제외 (결과 수가 줄 수 있음) |
| L59 | 문서 유형 | `DOC_TYPE_CODES` 정확히 일치 |
| L60 | 발췌 | 최대 200자 (`EXCERPT_MAX_CHARS`) |
| L61 | 정렬 | 문서 점수 내림차순, 같으면 파일명순 |
| L62 | 위치 표시 | ingest의 location_label 그대로 |
| L63 | 내부 메시지 | 화면 노출 금지, 로그에만 |
| L64 | 오류 문구 | `ERROR_MESSAGES` 12개 |

공개 조건 8가지(`is_visible`): ① DB에 문서 있음 ② published ③ deleted_at 없음 ④ 청크 version_id = active_version_id ⑤ version_status = ready ⑥ data_category = operational ⑦ 문서 회사가 허용 범위 안 ⑧ 청크 company_id = DB company_id.

## 8. 오류 코드

| 코드 | HTTP | UI status | 원인 |
|---|---|---|---|
| EMPTY_QUERY | 400 | input_error | 검색어 없음 (embedding EMPTY_QUERY 포함) |
| QUERY_TOO_LONG | 400 | input_error | 2000자 초과 (embedding QUERY_TOO_LONG 포함) |
| INVALID_LIMIT | 400 | input_error | limit·chunks_per_document·top_k 범위 밖 또는 정수 아님 |
| INVALID_MIN_SCORE | 400 | input_error | min_score 0~1 밖 |
| INVALID_DOC_TYPE | 400 | input_error | 허용되지 않은 유형 |
| NO_COMPANY_SCOPE | 403 | denied | 허용 회사 없음 (VectorStoreError NO_COMPANY_SCOPE 포함) |
| COMPANY_FORBIDDEN | 403 | denied | 허용 밖 회사 선택 |
| ACCESS_DENIED | 403 | denied | AccessError(ACCESS_DENIED·STATE_CHANGED) |
| SESSION_EXPIRED | 401 | session_expired | AccessError(SESSION_EXPIRED) |
| EMBEDDING_UNAVAILABLE | 503 | failed | 그 밖의 EmbeddingError (retryable은 embedding 값) |
| SEARCH_UNAVAILABLE | 503 | failed | 그 밖의 VectorStoreError(재시도 가능), DbError(DB_UNAVAILABLE만 재시도 가능) |
| SEARCH_FAILED | 500 | failed | 예상하지 못한 오류 (원인은 로그). 단, DB 조회 함수 안의 예상 밖 오류는 변환하지 않고 그대로 올림 |

## 9. 통합본 명세에 없는 추가·변경 항목

| 항목 | 이유 |
|---|---|
| 오류 코드 `SESSION_EXPIRED` (11→12개) | UI가 로그인 화면으로 돌아가도록 (UI 오류 규격) |
| `to_feedback`, `UI_STATUS` | UI는 문자열 status로 오류를 표시 |
| `build_error` | 코드별 문구·HTTP 상태 자동 채움 |
| `is_access_error`, `is_db_error` | auth·db 오류 판별 |
| `pick_company_scope` | 범위 계산을 auth 없이 시험 |
| FUN-243·244·253 인자 `actor` → `user` | 통합 명세 5장 결정, UI·chatbot 호출과 통일 |
| `chunks_per_document` 범위 검사 | 0·음수로 조회 개수가 깨지지 않게 |
| `document_ids=[]` → 빈 결과 | 선택이 비었을 때 전체 검색으로 넓어지지 않게 (리뷰 반영) |

## 10. 코드 리뷰 반영 현황

| 리뷰 항목 | 처리 |
|---|---|
| chatbot 세션 만료가 검색 실패로 표시 | search는 SESSION_EXPIRED 전달. chatbot 쪽 반영 요청 |
| limit·top_k 소수 입력 | 정수만 허용, 소수는 INVALID_LIMIT |
| document_ids=[] 전체 검색 | 빈 결과로 변경 (권한 확인은 먼저 수행) |
| 패키지가 모듈 이름을 가림 | 명세 MOD-050대로 유지. 시험은 `importlib.import_module('hyundap.search.search')` + `patch.object` (README) |
| 테스트 없음 | `hyundap/tests/test_search.py` 추가 |
| 이 문서가 비어 있음 | 작성 |

## 11. 알려진 한계·남은 일

- 평가 자료·비공개 문서·한 문서의 청크 독점 때문에 결과 문서 수가 limit보다 적을 수 있다.
- 최소 점수 0.3은 임시값(POL-13). 실제 문서로 확인 후 `.env`로 조정.
- 실제 MySQL·실제 OpenAI 임베딩으로는 아직 확인하지 못했다(가짜 함수·실제 Chroma로 확인).
- STATE_CHANGED는 search가 부르는 함수에서는 나오지 않아 ACCESS_DENIED로 묶었다.

## 12. 테스트

저장소 맨 위에서 실행한다. OpenAI·DB·auth 없이 창구 함수를 가짜로 바꿔 끼운다.

```bash
python -m unittest hyundap.tests.test_search -v
```

| 묶음 | 확인 내용 |
|---|---|
| InputTests | 검색어 정리·길이, 정수 검사, min_score 소수, top_k 계산 |
| ScopeTests | 회사 범위, 허용 회사 없음, 유형 정리, document_ids 빈 목록, 세션 만료 우선 |
| VisibilityTests | 최소 점수, 공개 조건 8가지, DB 값 사용·section_no |
| ResultTests | 문서 묶기·정렬·limit, 발췌 200자, 빈 결과 |
| ErrorTests | 오류 변환표, 원문 숨김, 검색 중 오류, DB 버그 그대로, to_feedback |
| ContractTests | ChunkHit 13필드, DocumentInfo 11필드 |
