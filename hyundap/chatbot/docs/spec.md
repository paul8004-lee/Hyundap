# chatbot — 근거 기반 답변·요약 작업 명세

| 항목 | 내용 |
|---|---|
| 담당 | 소용우 |
| 브랜치 | `feat/chatbot` (chatbot 작업은 이 브랜치 하나에서 진행) |
| 기준 문서 | 통합 기능명세서 `docs/spec.md` 3.5절 (드라이브 `문서검색_통합_기능명세서.xlsm.xlsx`, 10/26 11:26) · DB·인증 연동 계약 `docs/db-design/DB_contract.md` · 드라이브 `chatbot_기능명세서_소용우_초안.xlsx` |
| 번호 | 통합본 번호를 쓴다. 기능 F120~F128, 함수 FUN-401~427, 클래스 CLS-120~125, 모듈 MOD-120~126 |
| import | 이슈 #133 기준. 다른 폴더는 `from hyundap.<폴더>...`, chatbot 안은 상대 경로(`from .models ...`). 실행은 저장소 맨 위에서 |
| 상태 | P1(질문 답변)·P2(요약) 1차 구현, 코드 리뷰 반영 완료 · `common.config` 연결 완료 · DB 계약(재검증 예외) 반영 완료 · search·auth·embedding은 상대 쪽 구현 전이라 실제 연결 전 |

## 1. 한 줄 요약

사용자 질문에 대해 **search가 찾아 준 근거 청크로 LLM 답변을 만들고**, 답변의 모든 문장이 실제 근거를 인용했는지 검사한 뒤 **회사·파일·위치·발췌 출처**를 붙여 화면에 돌려준다. P2로 선택 문서 요약을 만든다.

### 담당 범위

| 구분 | 내용 |
|---|---|
| 직접 만든다 | 질문 검사, 근거 고르기(개수 제한), 답변 프롬프트·LLM 호출, 인용 검사, 출처 생성, 화면용 번호 변환, 오류 코드·안내 문구, 선택 문서 요약 |
| 호출만 한다 | 근거 청크 검색 `search.search_chunks`(이준영), 반환 직전 재검증 `auth.permissions.verify_documents_access`(전재경), 요약용 청크 조회 `embedding.get_document_chunks`(박영준), 설정 읽기 `common.config.get_settings`(이성민) |
| 하지 않는다 | 회사 범위 계산·검색 조건 구성·최소 점수(0.3) 적용·공개 조건·평가 자료 제외(search), 권한 판정(auth), 위치 문구 생성(ingest), 화면 표시·로딩(ui), DB 직접 접속 |

## 2. 파일 구성

| 모듈 | 파일 | 책임 | 함수·클래스 |
|---|---|---|---|
| MOD-120 | `__init__.py` | 다른 폴더에 내보낼 이름 | `ask`, `summarize_document`, `to_feedback` (결과 클래스는 `chatbot.models`) |
| MOD-121 | `ask.py` | 질문 1개 처리 흐름 | FUN-401~407 |
| MOD-122 | `prompts.py` | 프롬프트·LLM 체인 | FUN-408~410 |
| MOD-123 | `citations.py` | 이름표·인용 검사·출처 | FUN-411~419 |
| MOD-124 | `summary.py` | 선택 문서 요약 (P2) | FUN-420~424 |
| MOD-125 | `models.py` | 오류·데이터 형식 | FUN-425~427, CLS-120~125 |
| MOD-126 | `constants.py` | 기본값·오류 문구 | 상수 |

## 3. 처리 흐름

### 흐름 A — 질문 1개 처리 (`ask`)

| 순서 | 단계 | 함수 | 들어가는 것 → 나오는 것 | 실패하면 |
|---|---|---|---|---|
| A1 | 화면이 질문 전달 | ui `on_question_submit` → `ask` | 질문, 필터, 사용자 | - |
| A2 | 질문 검사 | `validate_question` | 질문 → 공백 정리된 질문 | EMPTY_QUESTION / QUESTION_TOO_LONG |
| A3 | 근거 받기 | `retrieve_evidence` → `search.search_chunks` | 질문·필터·사용자 → ChunkHit 목록(점수순) | RETRIEVAL_FAILED / ACCESS_DENIED |
| A4 | 근거 고르기 | `select_evidence` | 같은 청크 하나만, 문서당 4·전체 8개 | - |
| A5 | 근거 없으면 끝 | `build_no_evidence_response` | 0개면 LLM을 부르지 않고 유보 응답 | - |
| A6 | 이름표 붙이기 | `assign_aliases` → `format_evidence` | 근거 → `[E1] A사 · 교육안내.pdf · p.2` + 본문 | - |
| A7 | 답변 생성 | `generate_answer` → `build_answer_chain` → OpenAI | 질문 + 근거 → `…[E1]…` | LLM_FAILED / CONFIG_MISSING |
| A8 | 인용 검사 | `validate_citations` | 답변 → 쓰인 이름표. 유보 표현만 있으면 A5 응답 | INVALID_ANSWER / INVALID_CITATION |
| A9 | 출처 만들기 | `build_citation` × n | 쓰인 근거 → Citation 목록 | INVALID_CITATION |
| A10 | 반환 직전 재검증 | `recheck_before_return` → `verify_documents_access` | 사용자 + (문서ID, 버전ID) → 통과 | 권한 예외 / STATE_CHANGED |
| A11 | 화면용 정리 | `render_answer`, `group_citations_by_company` | `[E3]` → `[1]`, 회사별 출처 → ChatResponse | - |
| A12 | 오류 안내 | `to_feedback` → ui `show_feedback` | 예외 → `{status, message, error_code}` | - |

### 흐름 B — 선택 문서 요약 (`summarize_document`, P2)

| 순서 | 단계 | 함수 | 내용 |
|---|---|---|---|
| B1 | 화면이 요약 요청 | ui `on_summary_request` → `summarize_document` | 문서 ID, 사용자 |
| B2 | 문서 청크 받기 | `embedding.get_document_chunks` | 공개 버전 청크(VectorHit)를 순서대로. 조회 오류는 RETRIEVAL_FAILED |
| B2-1 | 형식 맞추기 | `summarize_document` 안에서 | VectorHit(metadata) → `search.models.ChunkHit`. 형식 오류는 감추지 않고 그대로 드러냄 |
| B3 | 문서 접근 확인 | `recheck_before_return(user, 청크)` → `verify_documents_access` | 받은 청크의 실제 (문서ID, 버전ID)로 확인. 볼 수 없으면 **LLM 호출 전에** 중단 (DB 계약은 버전ID 없는 확인을 받지 않음) |
| B4 | 상한 적용 | `limit_summary_chunks` | 64개 넘으면 뒤는 제외 |
| B5 | 이름표 | `assign_aliases` | 문서 전체에 E1~En 한 번만 |
| B6 | 부분 요약 | `summarize_batch` × (8개씩) | 묶음마다 요약 + 인용 검사. **내용 없는 묶음(NO_EVIDENCE)은 건너뜀**, 전부 비면 NO_EVIDENCE |
| B7 | 병합 | `merge_summaries` | 8개씩 합치기를 하나 남을 때까지. **입력 부분 요약에 나온 이름표만 허용** |
| B8 | 범위 계산 | `compute_coverage` | `포함 p.1~p.64`, `제외 p.65 이후`, is_full |
| B9 | 출처·번호 | `build_citation`, `render_answer` | |
| B10 | 재검증 | `recheck_before_return` | |
| B11 | 반환 | SummaryResponse | |

### 호출 트리

```
ui.chat_page.on_question_submit (박경환)
└─ ask(question, filters, user)                       FUN-401
   ├─ validate_question                               FUN-402
   ├─ retrieve_evidence                               FUN-403
   │   └─ search.search_chunks(user, query, company_ids, doc_types, document_ids, top_k)   (이준영)
   ├─ select_evidence                                 FUN-404
   ├─ build_no_evidence_response                      FUN-406  (근거 0개일 때)
   ├─ assign_aliases → format_evidence                FUN-411, 412 (format_source_label 417)
   ├─ generate_answer                                 FUN-405
   │   └─ build_answer_chain → get_chat_model         FUN-409, 408 → common.config.get_settings (이성민)
   ├─ validate_citations                              FUN-414 (extract_cited_aliases 413)
   ├─ build_citation × n                              FUN-415 (make_excerpt 416)
   ├─ recheck_before_return                           FUN-407
   │   └─ auth.permissions.verify_documents_access(user, refs)                           (전재경)
   └─ render_answer, group_citations_by_company       FUN-418, 419
   오류: map_dependency_error(427) → ChatbotError(425) → to_feedback(426)

ui.chat_page.on_summary_request (박경환)
└─ summarize_document(document_id, user)              FUN-420
   ├─ embedding.get_document_chunks                   (박영준)
   ├─ VectorHit → ChunkHit (함수 안에서)
   ├─ recheck_before_return (사전 확인)               FUN-407 → verify_documents_access (전재경)
   ├─ limit_summary_chunks → assign_aliases           FUN-424, 411
   ├─ summarize_batch × 반복 → build_summary_chain    FUN-421, 410
   ├─ merge_summaries                                 FUN-422
   ├─ compute_coverage                                FUN-423
   ├─ build_citation, render_answer                   FUN-415, 418
   └─ recheck_before_return                           FUN-407
```

## 4. 함수 명세

### ask.py

| ID | 함수 | 입력 | 출력 | 처리·규칙 |
|---|---|---|---|---|
| FUN-401 | `ask` | `question`, `filters`(ChatFilters, None이면 허용 범위 전체), `user` | ChatResponse | 흐름 A 전체 |
| FUN-402 | `validate_question` | `question`, `max_chars=2000` | 공백 정리된 질문 | 문자열 아님·공백뿐 → EMPTY_QUESTION, 2000자 초과 → QUESTION_TOO_LONG |
| FUN-403 | `retrieve_evidence` | `question`, `ChatFilters`, `user` | `list[ChunkHit]` | `search_chunks(user=, query=, company_ids=, doc_types=, document_ids=, top_k=TOP_K×2)`. min_score는 넘기지 않음(search 설정 0.3). 오류는 FUN-427로 변환 |
| FUN-404 | `select_evidence` | `hits` | `list[ChunkHit]` | 같은 chunk_id는 점수 높은 것 하나 → 점수순 → 문서당 4개 → 전체 8개 |
| FUN-405 | `generate_answer` | `question`, `evidence_text` | 답변 원문(str) | 체인 실행. 시간 초과·속도 제한 → LLM_FAILED(재시도 가능), 인증 오류 → CONFIG_MISSING |
| FUN-406 | `build_no_evidence_response` | `filters` | ChatResponse(`NO_EVIDENCE`) | LLM 미호출. 문서·유형 필터가 걸려 있으면 "필터를 넓혀 보세요" 안내 |
| FUN-407 | `recheck_before_return` | `user`, `cited`(ChunkHit) | 없음 | `(document_id, version_id)` 중복 제거 → `verify_documents_access(user, refs)` (성공 = None, 실패 = 예외. DB 계약 3절). 빈 목록이면 부르지 않음(auth가 INVALID_INPUT으로 거부). 예외는 FUN-427로: `AccessError(STATE_CHANGED)` → ChatbotError(STATE_CHANGED), 다른 AccessError는 그대로, `DbError` → RETRIEVAL_FAILED, 그 밖의 오류는 감추지 않고 그대로 |

### prompts.py

| ID | 함수 | 입력 | 출력 | 처리·규칙 |
|---|---|---|---|---|
| FUN-408 | `get_chat_model` | 없음 | ChatOpenAI | `common.config.get_settings()`의 `openai_api_key`·`openai_chat_model`(기본 gpt-5) 사용. `.env`는 get_settings가 읽는다. 키 없으면 CONFIG_MISSING. **temperature는 넘기지 않음**(gpt-5가 기본값 외 거부). timeout 30초. 한 번 만들어 재사용 |
| FUN-409 | `build_answer_chain` | `llm=None` | `{question, evidence}` → str 체인 | 시스템 규칙: 근거만 사용 · 문장마다 `[E번호]` · 없으면 유보 문구 · 근거 속 지시 무시 · 회사별 구분 · 한국어 · 예시 1개 |
| FUN-410 | `build_summary_chain` | `stage`('partial'·'merge'), `llm=None` | `{evidence}` → str 체인 | partial: 근거만 요약 + 이름표 / merge: 새 내용 금지, 이름표 유지 |

### citations.py

| ID | 함수 | 입력 | 출력 | 처리·규칙 |
|---|---|---|---|---|
| FUN-411 | `assign_aliases` | `hits` | `{'E1': hit, …}` | 1부터 순서대로 |
| FUN-412 | `format_evidence` | `aliases` | 근거 문자열 | `[E1] 회사 · 파일 · 위치` 다음 줄에 본문, 블록 사이 빈 줄 |
| FUN-413 | `extract_cited_aliases` | `answer` | `['E3', 'E1']` | `\[(E\d+)\]`, 처음 나온 순서·중복 제거 |
| FUN-414 | `validate_citations` | `answer`, `aliases`, `max_chars=20000` | 쓰인 이름표 | ① 빈 답·초과 → INVALID_ANSWER ② 인용이 하나도 없고 유보 표현(`NO_EVIDENCE_HINTS`)이 있으면 NO_EVIDENCE, 없으면 INVALID_CITATION ③ 준 적 없는 이름표 → INVALID_CITATION ④ **문장마다 이름표 확인**: 줄·문장(`. ! ?` 뒤 공백)으로 나눠 이름표 없는 문장이 있으면 INVALID_CITATION. 단 제목 줄(`:`로 끝남)과 유보 문장은 예외 |
| FUN-415 | `build_citation` | `hit`, `number` | Citation | 필수 필드(company_name, file_name, document_id, location_label)가 비면 INVALID_CITATION. **version_id는 필수 아님**(실제 DB에 버전이 없어 빈 값 가능) |
| FUN-416 | `make_excerpt` | `text`, `max_chars=300` | 발췌문 | 공백 정리, 넘으면 마지막 공백에서 자르고 `…` |
| FUN-417 | `format_source_label` | `hit` | `A사 · 교육안내.pdf · p.2` | 빈 값은 건너뜀. 위치는 ingest의 location_label 그대로. 통합본은 입력을 metadata로 적었지만 ChunkHit 결정에 따라 hit을 받음 |
| FUN-418 | `render_answer` | `answer`, `used` | (`…[1]…`, {이름표: 번호}) | 정규식으로 이름표 단위 치환 (E1·E10 섞임 없음) |
| FUN-419 | `group_citations_by_company` | `citations` | `{회사명: [Citation]}` | 처음 나온 회사 순 |

### summary.py

| ID | 함수 | 입력 | 출력 | 처리·규칙 |
|---|---|---|---|---|
| FUN-420 | `summarize_document` | `document_id`, `user` | SummaryResponse | 흐름 B 전체. 청크를 받은 뒤 실제 버전으로 접근 확인(B3). 내용 없는 묶음은 건너뜀. 청크 조회 오류만 RETRIEVAL_FAILED로 바꾸고 형식 오류는 그대로 올림. 함수 이름은 미정(D10)이라 제안 이름으로 구현 |
| FUN-421 | `summarize_batch` | 이름표 최대 8개, `chain=None` | (부분 요약, 쓰인 이름표) | partial 체인 + 인용 검사 |
| FUN-422 | `merge_summaries` | 부분 요약 목록, 전체 이름표, `batch_size=8` | 최종 요약 | 8개씩 병합 반복. 병합 결과는 **입력 부분 요약에 나온 이름표만** 허용 |
| FUN-423 | `compute_coverage` | 전체 청크, 사용 청크 | (포함 범위, 제외 범위, is_full) | 일부만 쓰면 is_full=False (WF-12) |
| FUN-424 | `limit_summary_chunks` | `hits` | (사용, 제외) | 앞에서부터 MAX_SUMMARY_CHUNKS(64)개 |

### models.py

| ID | 함수 | 입력 | 출력 | 처리·규칙 |
|---|---|---|---|---|
| FUN-425 | `ChatbotError.__init__` | `code`, `message=None`, `retryable=False` | 예외 | code는 ERROR_CODES 중 하나, 문구 없으면 기본 문구 |
| FUN-426 | `to_feedback` | 예외 | `{status, message, error_code}` | 8절 표. auth `AccessError`는 STATE_CHANGED면 상태 변경 안내, 나머지(ACCESS_DENIED·SESSION_EXPIRED 등)는 권한 없음 안내. 내부 예외 원문은 넣지 않음 |
| FUN-427 | `map_dependency_error` | 예외, `stage`('retrieve'·'llm'·'summary'·'recheck') | ChatbotError(또는 원래 권한 예외) | AccessError(STATE_CHANGED) → STATE_CHANGED, 다른 AccessError → 그대로, DbError → RETRIEVAL_FAILED(DB_UNAVAILABLE이면 재시도 가능), 'recheck' 단계의 그 밖의 오류 → 그대로, SearchError 권한 계열 → ACCESS_DENIED, 'retrieve'·'summary' 단계 → RETRIEVAL_FAILED, 'llm' 단계 → 8절 규칙 |

## 5. 데이터 형식

### 받는 것

| 형식 | 담당 | 필드 | 쓰는 곳 |
|---|---|---|---|
| ChunkHit (CLS-051) | 이준영 `search/models.py` | chunk_id, document_id, version_id, version_no, company_id, company_name, doc_type, file_name, section_no, location_label, chunk_index, text, score | 질문·요약의 모든 근거 |
| VectorHit (CLS-105) | 박영준 `embedding/models.py` | chunk_id, text, metadata(ChunkMetadata CLS-108), score | 요약 경로에서만 받고 FUN-420 안에서 ChunkHit로 변환 |
| Settings | 이성민 `common/config.py` (구현됨) | openai_api_key, openai_chat_model 외 | FUN-408 |
| user (UserContext, CLS-122) | 전재경 auth | user_id, company_id, role, … | 열어 보지 않고 search·auth에 그대로 넘김 |

### 주는 것 (`models.py`)

| 클래스 | 필드 |
|---|---|
| ChatFilters (CLS-121) | company_ids, doc_types, document_ids (모두 None 가능 = 허용 범위 전체) |
| Citation (CLS-123) | number, chunk_id, document_id, version_id, version_no, company_id, company_name, file_name, doc_type, location_label, excerpt, score |
| ChatResponse (CLS-124) | status(`ANSWERED`·`NO_EVIDENCE`), answer, citations, citations_by_company, document_ids, message, error_code |
| SummaryResponse (CLS-125) | document_id, version_id, summary, citations, included_range, excluded_range, is_full, total_chunks, used_chunks |

`document_ids`는 ui가 문서를 바꿀 때 이전 답변이 섞이지 않게 확인하는 데 쓴다(통합본 L29). 이성민의 `common/schemas.py`(10/26 반영)에는 아직 이 클래스들이 없어 `chatbot/models.py`에 둔다(D04).

## 6. 다른 폴더와의 연결

| 함수 | 담당 | 현재 상태 | 지금 코드의 처리 |
|---|---|---|---|
| `common.config.get_settings` | 이성민 | **구현됨** (`Settings` 반환, `.env` 읽음, 기본 모델 gpt-5) | 그대로 사용 |
| `hyundap.search.search.search_chunks` (FUN-244) | 이준영 | 이름·인자 확정, 구현 전 (파일만 있음) | 호출할 때 import. 구현 전에는 ImportError |
| `hyundap.search.models.ChunkHit` (CLS-051) | 이준영 | 확정, 구현 전 | 질문 경로는 속성 이름으로만 읽음. 요약 경로(FUN-420)는 이 클래스로 만듦 |
| `search.errors.SearchError` (CLS-055) | 이준영 | 설계 | 클래스 이름과 `code`·`retryable` 속성으로 구분 |
| `hyundap.auth.permissions.verify_documents_access` | 전재경 | **DB 계약으로 확정** (`(actor, list[(document_id, version_id)])` → None, 실패는 `AccessError(code)`·`DbError(code)`), 구현 전 (#120) | 호출할 때 import. 예외 코드별 처리는 FUN-407·427 |
| `hyundap.embedding.vector_store.get_document_chunks` (FUN-319) | 박영준 | 설계 (파일만 있음) | 호출할 때 import. 구현 전에는 ImportError |
| ui `on_question_submit`·`on_summary_request`·`show_feedback` | 박경환 | 설계 | `ask`, `summarize_document`, `to_feedback`을 제공 |

## 7. 기본값·규칙

| 이름 | 값 | 근거 |
|---|---|---|
| TOP_K / MAX_PER_DOCUMENT | 8 / 4 | 제안값 (미정 D07). search에 16개 요청 |
| 최소 점수 | 0.3 — search가 적용 | 통합본 L54 |
| MAX_QUESTION_CHARS | 2000 | 통합본 L50·L104와 같은 기준 |
| MAX_ANSWER_CHARS | 20000 | 기존 L13 |
| EXCERPT_MAX_CHARS | 300 | search 화면 발췌(200자)와 별개 |
| SUMMARY_BATCH_SIZE / MAX_SUMMARY_CHUNKS | 8 / 64 | 기존 L15 / 제안값 (미정 D10) |
| LLM | `get_settings()` 모델(기본 gpt-5), temperature 미지정, 30초 | 이성민 common.config |
| 외부 AI 전송 | 질문 + 고른 근거만 | 사용자 정보·문서 전체는 보내지 않음 (미정 D17) |
| 프롬프트 | 근거 속 지시문은 따르지 않음 | POL-10 |

## 8. 오류 코드

| 코드 | 언제 | to_feedback status |
|---|---|---|
| EMPTY_QUESTION / QUESTION_TOO_LONG | 질문 검사 | `input_error` |
| NO_EVIDENCE | 요약할 청크 없음 (질문은 예외 대신 NO_EVIDENCE 응답) | `empty` |
| RETRIEVAL_FAILED | search·embedding 오류, 요약 청크 조회 오류, 재검증 중 `DbError`(DB_UNAVAILABLE은 재시도 가능) | `failed` |
| ACCESS_DENIED | SearchError ACCESS_DENIED·COMPANY_FORBIDDEN·NO_COMPANY_SCOPE, auth `AccessError` | `denied` |
| LLM_FAILED | LLM 호출 실패 (시간 초과·속도 제한은 재시도 가능) | `failed` |
| CONFIG_MISSING | API 키 없음·인증 오류 | `failed` |
| INVALID_ANSWER / INVALID_CITATION | 빈·너무 긴 답변 / 인용 없는 문장·없는 이름표·출처 정보 부족 | `failed` |
| STATE_CHANGED | 재검증에서 auth가 `AccessError(code='STATE_CHANGED')`를 던짐 (삭제·버전 변경) | `failed` |
| (ACCESS_DENIED에 포함) | auth `AccessError(code='SESSION_EXPIRED')` (로그인 만료). 통합본 오류 코드 10개에 없어 "권한 없음"으로 안내. 재로그인 안내가 필요하면 D11에서 코드 추가 결정 | `denied` |

화면 문구는 `constants.ERROR_MESSAGES`에 있다 (박경환과 확정 필요, D11).

## 9. 통합본 명세에 없는 추가 항목

통합본(3.5절)에 없지만 이 폴더에 추가한 것이다. 통합본 갱신 때 반영을 요청한다.

| 항목 | 종류 | 위치 | 이유 | 통합본 반영 |
|---|---|---|---|---|
| `NO_EVIDENCE_HINTS` | 상수 | constants.py | LLM이 유보 문구를 바꿔 말해도 근거 없음으로 처리 (리뷰 반영) | 선택 |
| `ERROR_MESSAGES`, `ERROR_CODES` | 상수 | constants.py | 오류 코드별 화면 문구 (D11) | 요청 |
| `ACCESS_ERROR_NAMES`, `SEARCH_ACCESS_CODES` | 상수 | constants.py | 이름(AccessError·DbError)은 DB 계약으로 확정됐지만 클래스가 아직 없어 이름·코드로 구분 | 클래스 생기면 import로 정리 |
| `SENTENCE_END`, `REQUIRED_FIELDS`, `ALIAS_PATTERN` | 상수 | citations.py | 문장 나누기·필수 출처 필드·이름표 정규식 | 내부용 |
| `LLM_TIMEOUT_SECONDS` | 상수 | constants.py | LLM 호출 30초 제한 | 선택 |
| Citation `version_no`, ChatResponse `citations_by_company`·`document_ids` | 클래스 필드 | models.py | WF-15 버전 표시, FR-06 회사별 출처(D08), ui 화면 섞임 방지(L29) | 요청 (D04·D08) |
| 결과 클래스 4개 위치 | 위치 | models.py | 통합본은 `common/schemas.py`라고 적었지만 이성민 파일이고 아직 없음 | D04 |
| `map_dependency_error`의 stage 값 `'recheck'` | 인자 값 | models.py | 재검증 중 auth·DB 오류를 계약대로 바꾸고, 예상 못 한 오류는 감추지 않으려고 | 요청 |

**함수는 통합본 FUN-401~427 외에 하나도 없다.** 명세 밖 함수·인자는 모두 뺐다(아래).
**삭제한 것:**

- 리뷰 반영 중 `_setting`(설정 읽기 보조 함수)과 `DEFAULT_CHAT_MODEL`·`LLM_TEMPERATURE` 상수를 지웠다. `common.config.get_settings()`가 생겨 필요 없어졌다.
- **가짜 함수 자리(개발용 주입 인자)를 코드에서 뺐다:** `ask`의 `retriever`·`checker`·`chain`, `retrieve_evidence`의 `retriever`, `generate_answer`의 `chain`, `recheck_before_return`의 `checker`, `summarize_document`의 `checker`·`get_chunks`·`llm`, `merge_summaries`의 `chain`, 임시 객체(SimpleNamespace) 대체. 통합본 FUN-403·405·407·420은 이 자리를 "개발 중 가짜 자리"로 적어 두었으므로 통합본 갱신 때 삭제를 요청한다. 시험은 코드 밖에서 가짜 모듈을 끼워 한다(12절).
- **명세 밖 함수·인자를 뺐다:** `vector_to_chunk_hit`(제안 FUN-428 → VectorHit 변환은 `summarize_document` 안으로), `_to_filters`(dict 필터 변환 → 통합본대로 ChatFilters만 받음), `retrieve_evidence`의 `top_k`, `select_evidence`의 `max_per_document`·`max_total`, `limit_summary_chunks`의 `max_chunks`(모두 constants 값을 직접 씀), 오류 문구 `SESSION_EXPIRED`(통합본 오류 코드 10개만 유지).
- 남긴 것: `summarize_batch`의 `chain`(요약 묶음마다 체인을 다시 만들지 않으려고 실제로 씀), `build_answer_chain`·`build_summary_chain`의 `llm`(통합본 인자).

## 10. 코드 리뷰 반영 현황 (10/26)

| # | 문제 | 처리 |
|---|---|---|
| 1 | version_id 필수라 실제 DB에서 출처가 모두 거부될 수 있음 | 고침 — 필수 필드에서 제외 (FUN-415) |
| 2 | 인용 없는 문장이 섞여도 통과 | 고침 — 문장마다 이름표 확인 (FUN-414) |
| 3 | `.env`의 API 키를 못 읽음 | 고침 — `get_settings()` 사용 (FUN-408) |
| 3-1 | (최신 develop 반영 후 발견) 설정 속성 이름 불일치·gpt-5 temperature 거부 | 고침 — `settings.openai_api_key`, temperature 미지정 |
| 4 | 권한 오류를 이름 `AccessError`로만 구분 | 해결 — DB 계약에서 `AccessError(code)`·`DbError(code)`로 확정. 클래스가 생기면 이름 비교를 import로 바꿈 |
| 5 | 내용 없는 묶음 하나로 요약 전체 실패 | 고침 — 그 묶음만 건너뜀 (FUN-420) |
| 6 | 병합 검사가 문서 전체 이름표를 허용 | 고침 — 입력 부분 요약의 이름표만 허용 (FUN-422) |
| 7 | 유보 판단이 문구 완전 일치에만 의존 | 고침 — `NO_EVIDENCE_HINTS` 표현 목록 (FUN-414) |
| 8 | 부분 요약 LLM 호출이 순서대로라 느림 | 보류 — 동시 호출하려면 FUN-421 입력이 바뀌어 통합본 변경 필요 (P2) |
| 9 | 요약 단계 형식 오류를 LLM 실패로 분류 | 고침 — 청크 조회만 RETRIEVAL_FAILED, 형식 오류는 그대로 (FUN-420·427) |
| 10 | 문서 내용이 실제와 다름 | 고침 — 이 문서와 README 갱신 |
| 11 | (DB 계약 반영) 재검증이 `False` 반환을 기다림 — 계약은 예외만 씀 | 고침 — 예외만 처리, STATE_CHANGED 변환 (FUN-407·427) |
| 12 | (DB 계약 반영) AccessError(STATE_CHANGED)도 "권한 없음"으로 안내 | 고침 — STATE_CHANGED만 구분, 나머지는 통합본 코드 안에서 ACCESS_DENIED (FUN-426) |
| 13 | (DB 계약 반영) DbError 처리 없음 | 고침 — RETRIEVAL_FAILED로 변환, 원시 오류 숨김 (FUN-427) |
| 14 | (DB 계약 반영) 요약 사전 확인이 버전ID 없이 `(문서ID, None)` | 고침 — 청크를 받은 뒤 실제 버전으로 확인, 확인 전 LLM 미호출 (FUN-420) |
| 15 | 가짜 함수 자리가 제출 코드에 남음 | 고침 — 주입 인자·임시 객체 삭제 (9절) |
| 16 | 명세 밖 함수·인자가 남음 (`vector_to_chunk_hit`, `_to_filters`, 개수 인자) | 고침 — 통합본 FUN-401~427 함수·인자만 남김 (9절) |
| 17 | import가 팀 기준과 다름 (이슈 #133) | 고침 — 다른 폴더는 `hyundap.` 경로, chatbot 안은 상대 경로. 저장소 맨 위에서 `python -c "import hyundap.chatbot.ask"` 통과 |

## 11. 미정 사항과 지금 코드의 가정

| 번호 | 내용 | 지금 코드 | 확인할 사람 |
|---|---|---|---|
| D19 | 요약 경로 근거 형식(VectorHit) | `summarize_document` 안에서 ChunkHit로 변환 (별도 함수 없음) | 박영준 공유 |
| D10 | 요약 함수 이름·길이 | `summarize_document`, 청크 64개 | 박경환 |
| D04 | Citation·ChatResponse 위치 | `chatbot/models.py` | 이성민 (common/schemas.py) |
| D07 | 근거 개수 | 문서당 4·전체 8 | 이준영·이성민 (정확도 테스트 후) |
| D08 | 여러 회사 질문 | 필터 범위 전체에서 8개, 회사별 답변·출처 | 팀 |
| D11 | 오류 문구 | `ERROR_MESSAGES` 초안 | 박경환 |
| - | 버전 정보 | 공유 DB는 아직 스키마 v3라 `document_versions`·`version_no`가 없다. DB 계약의 v4(버전·청크 표)가 적용되면 version_id가 늘 채워진다. 그 전까지 version_id 빈 값도 허용 | 전재경 (#119) |
| - | 문장 단위 인용 검사 | 실제 gpt-5 답변이 문장마다 이름표를 붙이는지 확인 필요. 너무 자주 거부되면 프롬프트 예시 보강 | 통합 테스트 |

## 12. 테스트

`tests/`는 이성민 담당 폴더라 작성 주체가 정해지면(D16) `tests/test_chatbot.py`로 옮긴다. chatbot 코드에는 가짜 자리가 없으므로, 아직 없는 `hyundap.search.search`·`hyundap.search.models`·`hyundap.auth.permissions`·`hyundap.embedding.vector_store`를 시험 쪽에서 `sys.modules`에 가짜 모듈로 끼우고, LLM은 `hyundap.chatbot.prompts.get_chat_model`을 가짜 모델(`FakeListChatModel`)로 바꿔 저장소 맨 위 기준으로 **58개**를 확인했다 (58/58 통과).

- 질문 흐름: 정상 답변·출처·회사별 묶음, search_chunks 인자, 재검증 인자, 근거 없음·유보, 지어낸 이름표·인용 없음·빈 답변 거부, SearchError 변환, 질문 길이, E1·E10 치환, 문서당 4개
- DB 계약: AccessError STATE_CHANGED·ACCESS_DENIED·SESSION_EXPIRED(→ 권한 없음), DbError, 예상 못 한 오류 노출, 요약 사전 확인이 실제 버전으로·권한 없으면 LLM 호출 전 중단
- 요약: 64/70 범위, 이름표 번호, 빈 묶음 건너뛰기, 청크 조회 오류, 형식 오류 노출
- 모듈마다 통합본 함수만 있는지·인자 모양이 통합본과 같은지, `__all__`
- 리뷰 수정: 문장별 인용, 바꿔 말한 유보, version_id 빈 값, 병합 이름표 제한, `get_settings`로 gpt-5 모델 생성, 키 없음 CONFIG_MISSING

실제 OpenAI 호출과 다른 폴더 실제 함수 연결은 통합 테스트(10/29~)에서 확인한다.

## 13. 일정

| 날짜 | 할 일 |
|---|---|
| 10/27 (Day 4) | 이 명세와 1차 코드 공유, 미정 사항(D04·D07·D10·D11·D19) 확인 |
| 10/28 (Day 5) | develop PR · merge |
| 10/29 (Day 6) | 실제 search·auth·embedding 연결, 실제 gpt-5로 인용 형식 확인, 통합 테스트 |
| 10/30 (Day 7) | 권한 테스트(다른 회사 문서 노출), 근거 개수 조정 |
