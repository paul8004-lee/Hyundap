# search — 통합 검색

담당: 이준영 · 기준: 통합 기능명세서 F80~F92 · [DB 계약](../../docs/db-design/DB_contract.md)

**로그인한 사람이 볼 수 있는 문서 안에서만**, 질문과 비슷한 내용을 찾아 **점수 높은 순**으로 돌려줍니다.
화면(ui)은 문서 목록이 필요해서 `search()`를, 챗봇(chatbot)은 답변 근거가 필요해서 `search_chunks()`를 부릅니다.
두 함수는 같은 엔진(`run_chunk_search`)을 써서 권한·점수·공개 기준이 항상 같습니다.

## 다른 폴더에서 쓰는 법

```python
from hyundap.search import search, search_chunks, SearchError
from hyundap.search.errors import to_feedback

# 화면(ui) — 문서 단위
try:
    response = search(user=user, query='휴가 규정', company_ids=None, doc_types=['policy'], limit=10)
    # response.results[0].file_name       → '휴가규정.pdf'
    # response.results[0].citations[0]    → SearchCitation(location_label='p.3', excerpt='연차는 …(200자)', score=0.82, …)
    # response.company_ids / doc_types    → 실제로 적용된 조건
except SearchError as error:
    feedback = to_feedback(error)   # {'status': 'input_error'|'denied'|'session_expired'|'failed', 'message': …, 'error_code': …}

# 챗봇(chatbot) — 청크 단위
hits = search_chunks(user=user, query='신입 교육은 언제?', company_ids=None, doc_types=None,
                     document_ids=None, top_k=16)
# hits → [ChunkHit(chunk_id, document_id, version_id, version_no, company_id, company_name,
#                  doc_type, file_name, section_no, location_label, chunk_index, text, score), …]
```

| 함수 | 받는 것 | 돌려주는 것 |
|---|---|---|
| `search(user, query, company_ids=None, doc_types=None, limit=10)` | 로그인 사용자, 검색어, 회사·유형 조건(없으면 전체), 결과 문서 수(1~20) | `SearchResponse` (결과 없으면 빈 목록, 오류 아님) |
| `search_chunks(user, query, company_ids=None, doc_types=None, document_ids=None, top_k=None, min_score=None)` | 위 + 특정 문서 범위, 조회 개수(기본 10), 최소 점수(기본 설정값) | `ChunkHit` 목록 (점수 높은 순) |
| `to_feedback(error)` | 예외 | `SearchError`면 화면 안내 dict, 아니면 `None` |

- `user`는 서버가 만든 로그인 사용자 문맥(auth 형식 dict)입니다. search는 내용을 보지 않고 auth에 그대로 넘깁니다.
- 회사·유형을 "전체"로 하려면 `None`을 넘깁니다. 문서 유형은 `manual`·`policy`·`guide`·`other`만 받습니다.
- 안쪽 함수(`is_visible` 등)는 바뀔 수 있으니 직접 부르지 말고 위 함수만 써 주세요.

## 검색 한 번의 흐름

```
search() / search_chunks()
 ① 검색어·숫자 검사          validate_query, validate_range
 ② 설정 읽기                 get_search_settings     → 최소 점수 0.3, 조회 상한 80 (common.config)
 ③ 회사 범위                 resolve_company_scope   → auth 허용 회사 ∩ 사용자가 고른 회사
 ④ 유형 정리                 normalize_doc_types     → 공백·중복 정리, 허용 값 검사
 ⑤ 벡터 검색 조건            build_search_filter     → 회사 + 유형 + 문서 + 공개(is_active)
 ⑥ 질문 벡터화·벡터 조회      embedding.embed_query, embedding.query_vectors
 ⑦ 최소 점수 거르기          filter_by_min_score     → 관련 없는 결과 제거
 ⑧ DB 최신 상태 조회(1회)     fetch_document_info     → db.get_documents_with_active_version
 ⑨ 공개 판단                 is_visible              → 아래 8가지 조건
 ⑩ 결과 합치기               to_chunk_hit            → search_chunks는 여기서 끝
 ⑪ 문서로 묶기·발췌 (화면만)   group_hits_by_document, to_citation, make_excerpt
```

## 지켜지는 규칙

- **다른 회사 문서는 나오지 않습니다.** 화면에서 고른 회사를 그대로 믿지 않고 auth가 준 허용 회사와 대조합니다(허용 밖이면 `COMPANY_FORBIDDEN`). 벡터 조회 조건에도 회사가 반드시 들어갑니다.
- **벡터 결과를 그대로 믿지 않고 DB로 다시 확인합니다.** 벡터 저장소는 저장할 때의 정보라 이후의 삭제·버전 교체를 모를 수 있습니다. 아래 8가지가 모두 맞아야 결과에 나옵니다.

  | # | 조건 | 막는 것 |
  |---|---|---|
  | 1 | DB에 문서가 있음 | 지워진 문서 |
  | 2 | 게시됨(`published`) | 초안·차단 문서 |
  | 3 | 삭제 안 됨 | 삭제 문서 |
  | 4 | 청크 버전 = 현재 활성 버전 | 옛 버전 내용 |
  | 5 | 버전 준비 완료(`ready`) | 처리 중·실패 버전 |
  | 6 | 운영 자료(`operational`) | 평가용 자료 |
  | 7 | 문서 회사가 허용 범위 안 | 다른 회사 문서 |
  | 8 | 청크 회사 = DB 문서 회사 | 저장 실수로 섞인 청크 |

- **최소 점수(0.3)보다 낮은 결과는 뺍니다.** 벡터 검색은 관련이 없어도 가장 가까운 것을 항상 돌려주기 때문입니다. 점수는 `1 − cosine 거리`(0~1)입니다.
- 회사명·파일명·버전 번호는 DB 값, 위치 문구(`p.3`, `슬라이드 5` 등)는 ingest가 넣은 값을 그대로 씁니다.
- 문서 점수는 그 문서에서 가장 높은 청크 점수이고, 같은 점수면 파일명순입니다(같은 입력이면 항상 같은 순서).
- 내부 오류 원문(서버 주소·SQL 등)은 화면에 보내지 않고 로그에만 남깁니다.

## 오류 코드 (12개)

| 코드 | HTTP | UI status | 언제 |
|---|---|---|---|
| `EMPTY_QUERY` | 400 | input_error | 검색어가 비었음 |
| `QUERY_TOO_LONG` | 400 | input_error | 검색어 2000자 초과 |
| `INVALID_LIMIT` | 400 | input_error | limit·top_k 범위 밖 |
| `INVALID_MIN_SCORE` | 400 | input_error | min_score가 0~1 밖 |
| `INVALID_DOC_TYPE` | 400 | input_error | 허용되지 않은 문서 유형 |
| `NO_COMPANY_SCOPE` | 403 | denied | 검색할 수 있는 회사가 없음 |
| `COMPANY_FORBIDDEN` | 403 | denied | 허용 밖 회사를 고름 |
| `ACCESS_DENIED` | 403 | denied | auth 접근 거부 |
| `SESSION_EXPIRED` | 401 | session_expired | 로그인 만료 → UI가 로그인 화면으로 |
| `EMBEDDING_UNAVAILABLE` | 503 | failed | 질문 임베딩 실패(API 키·속도 제한 등). 재시도 여부는 embedding 판단을 따름 |
| `SEARCH_UNAVAILABLE` | 503 | failed | 벡터 저장소·DB 장애 (재시도 가능) |
| `SEARCH_FAILED` | 500 | failed | 예상하지 못한 오류 |

다른 폴더 오류는 `to_search_error`가 위 코드로 바꿉니다: `EmbeddingError` → 같은 입력 코드 또는 `EMBEDDING_UNAVAILABLE`, `VectorStoreError` → `NO_COMPANY_SCOPE` 또는 `SEARCH_UNAVAILABLE`, `AccessError` → `SESSION_EXPIRED` 또는 `ACCESS_DENIED`, `DbError` → `SEARCH_UNAVAILABLE`.
DB 조회 함수 안의 예상하지 못한 오류(코드 버그)는 숨기지 않고 그대로 올립니다(DB 계약 2절).

## 파일

| 파일 | 내용 |
|---|---|
| `search.py` | 검색 본체: 입력 검사, 설정, 벡터 조회, 점수·공개 거르기, 결과 가공, `search`·`search_chunks` |
| `filters.py` | 회사 범위(권한 결과 적용), 문서 유형 정리, 벡터 검색 조건 |
| `errors.py` | `SearchError`, 오류 변환(`to_search_error`), 화면 안내 변환(`to_feedback`), 오류 문구·상태 표 |
| `models.py` | 자료형 5개: `DocumentInfo`, `ChunkHit`, `SearchCitation`, `SearchResult`, `SearchResponse` |
| `__init__.py` | 공개 함수 3개만 내보냄 |

## 사용하는 다른 폴더 함수

| 폴더(담당) | 함수 |
|---|---|
| auth (전재경) | `hyundap.auth.permissions.get_allowed_company_ids(user)` → 허용 회사 set |
| db (전재경) | `hyundap.db.documents_repo.get_documents_with_active_version(ids)` → 문서 11필드 dict 목록 |
| embedding (박영준) | `embed_query`, `build_where_filter`, `query_vectors(vector, where, top_k=)` |
| common (이성민) | `get_settings().search_min_score`·`search_top_k_max`, `DOC_TYPE_CODES` |

다른 폴더 함수는 `_embed_query`, `_query_vectors`, `_load_document_rows`, `_load_allowed_company_ids`, `_build_where_filter` 창구 함수 안에서 호출할 때 import합니다. 이름이 바뀌면 이 창구만 고치면 되고, 시험에서는 이 창구만 가짜로 바꿔 끼우면 됩니다.

## 실행·설정

- 모든 실행은 저장소 맨 위 폴더에서 합니다. 다른 폴더는 `from hyundap.<폴더>…`, search 안은 `from .models …`처럼 가져옵니다.
- 저장소 맨 위 `.env`: `OPENAI_API_KEY`(질문 임베딩), `DATABASE_URL`(DB 조회), 선택으로 `SEARCH_MIN_SCORE`(기본 0.3)·`SEARCH_TOP_K_MAX`(기본 80).
- import 확인: `python -c "import hyundap.search.search"`

## 통합 명세에 없는 추가 항목

| 추가한 것 | 왜 |
|---|---|
| 오류 코드 `SESSION_EXPIRED` | 로그인 만료 시 UI가 로그인 화면으로 돌아가게 (UI 오류 규격) |
| `to_feedback`, `UI_STATUS` (errors.py) | UI는 오류를 `{status, message, error_code}` 문자열 상태로 받음. 없으면 "요청을 처리하지 못했습니다"만 표시됨 |
| `build_error` (errors.py) | 코드에 맞는 문구·HTTP 상태를 표에서 채워 실수 방지 |
| `is_access_error`, `is_db_error` (errors.py) | auth·db 오류 판별 |
| `pick_company_scope` (filters.py) | 회사 범위 계산을 auth 없이 시험하려고 분리 |
| 함수 인자 `actor` → `user` | 통합 명세 5장 결정 (UI·chatbot 호출 방식과 통일) |

## 알려진 한계

- 평가 자료(`evaluation`)와 비공개 문서는 벡터 조회 **뒤에** 빼므로, 이런 문서가 상위를 차지하면 결과 문서 수가 `limit`보다 적을 수 있습니다.
- 한 문서의 청크가 상위를 독차지해도 결과 문서 수가 `limit`보다 적을 수 있습니다(조회 개수 = limit × 4, 상한 80).
- 최소 점수 0.3은 임시값입니다(팀 미정 POL-13). 실제 문서로 확인 후 `.env`로 조정합니다.
- OpenAI 일시 오류 시 embedding이 1·2·4초 간격으로 재시도하므로 검색이 몇 초 늦어질 수 있습니다.