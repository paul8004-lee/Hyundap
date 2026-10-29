# chatbot — 근거 기반 답변·요약 작업 명세

| 항목 | 내용 |
|---|---|
| 담당 | 소용우 |
| 브랜치 | `feat/chatbot` (chatbot 작업은 이 브랜치 하나에서 진행) |
| 기준 문서 | 통합 기능명세서 `docs/spec.md` 3.5절 (드라이브 `문서검색_통합_기능명세서.xlsm.xlsx`, 10/26 11:26) · DB·인증 연동 계약 `docs/db-design/DB_contract.md` · 드라이브 `chatbot_기능명세서_소용우_초안.xlsx` |
| 번호 | 통합본 번호를 쓴다. 기능 F120~F128, 함수 FUN-401~427, 클래스 CLS-120~125, 모듈 MOD-120~126 |
| import | 이슈 #133 기준. 다른 폴더는 `from hyundap.<폴더>...`, chatbot 안은 상대 경로(`from .models ...`). 실행은 저장소 맨 위에서 |
| 상태 | P1(질문 답변)·P2(요약) 구현, 코드 리뷰 반영 완료 · 큰 함수 7개를 하위 함수로 나눔 · `common.config` 연결 완료 · search(#143)·auth·embedding이 develop에 구현돼 실제 함수와 연결됨 (실제 OpenAI·MySQL·Chroma로 끝까지 실행은 통합 테스트에서 확인) |

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
| MOD-120 | `__init__.py` | 다른 폴더에 내보낼 이름 | `ask`, `summarize_document`, `classify_document`, `to_feedback` (결과 클래스는 `chatbot.models`) |
| MOD-121 | `ask.py` | 질문 1개 처리 흐름 | FUN-401~407 |
| MOD-122 | `prompts.py` | 프롬프트·LLM 체인 | FUN-408~410 |
| MOD-123 | `citations.py` | 이름표·인용 검사·출처 | FUN-411~419 |
| MOD-124 | `summary.py` | 선택 문서 요약 (P2) | FUN-420~424 |
| MOD-125 | `models.py` | 오류·결과 데이터 형식. ChatFilters·Citation·ChatResponse·SummaryResponse·DocClassification의 정의 위치 (D04 결정, common.schemas 아님) | FUN-425~427, CLS-120·121·123·124·125·126 |
| MOD-126 | `constants.py` | 기본값·오류 문구 | 상수 |
| MOD-127 | `doc_classifier.py` | 문서 유형·업로드 회사 일치 자동 분류 (명세 밖 추가, 10/29) | FUN-429~434 |

## 3. 처리 흐름

### 흐름 A — 질문 1개 처리 (`ask`)

| 순서 | 단계 | 함수 | 들어가는 것 → 나오는 것 | 실패하면 |
|---|---|---|---|---|
| A1 | 화면이 질문 전달 | ui `on_question_submit` → `ask` | 질문, 필터, 사용자 | - |
| A2 | 질문 검사 | `validate_question` | 질문 → 공백 정리된 질문 | EMPTY_QUESTION / QUESTION_TOO_LONG |
| A3 | 근거 받기 | `retrieve_evidence` → `search.search_chunks` | 질문·필터·사용자 → ChunkHit 목록(점수순) | RETRIEVAL_FAILED / ACCESS_DENIED |
| A4 | 근거 고르기 | `select_evidence` | 같은 청크 하나만, 문서당 상한(근거 문서가 1개면 8, 여러 개면 4)·전체 8개 | - |
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
| B4 | 구간 계획 | `plan_summary_segments` | tiktoken으로 센 토큰 기준. 한 호출 입력 23만 토큰(`SUMMARY_INPUT_TOKEN_BUDGET`)에서 넘겨받는 요약 몫·프롬프트 고정분을 뺀 단계 상한으로 1~3구간. 고르게 나눠지면 고르게, 3구간을 넘는 뒤쪽은 제외 |
| B5 | 이름표 | `assign_aliases` | 사용 청크 전체에 E1~En 한 번만 |
| B6 | 구간별 직렬 요약 | `summarize_rolling` → `summarize_step` | 구간 1 요약 → (구간 1 요약 + 구간 2 원문) 요약 → … 호출은 구간 수만큼, 최대 3회, 항상 하나씩. 각 단계는 **그 단계가 본 이름표(이번 구간 + 이전 요약)만 허용**. 인용 오류는 총 3회 안에서 같은 단계 재시도. 중간 구간이 끝내 실패하면 그 구간만 빼고 이전 요약을 넘김 |
| B7 | 최종 단계 스트리밍 | `stream_summary` | 줄이 완성될 때마다 인용 검사, **통과한 줄만** `delta`로 화면에 보냄. 스트리밍이 거절되면(조직 인증) 한 번에 받음. 입력 길이 초과로 거절되면 상한을 15% 줄여 B4부터 한 번 다시 |
| B8 | 범위 계산 | `compute_coverage`, `format_excluded` | `포함 p.1~p.230`, 제외는 `예산 초과로 제외 p.231~p.300` · `근거 확인 실패로 제외 p.9~p.16`. 뺀 것이 있으면 is_full=False |
| B9 | 출처·번호 | `collect_citations`, `render_answer` | 화면 번호는 스트리밍 중(`AliasLineRenderer`)과 같은 규칙 |
| B10 | 재검증 | `recheck_before_return` | 실패하면 `reset` 후 오류 |
| B11 | 반환 | `done` 이벤트의 SummaryResponse | `summarize_document`는 이 결과만 돌려준다 |

이벤트(`summarize_document_stream`): `status`(진행 단계) · `delta`(검사를 통과한 화면용 줄) · `reset`(보낸 글을 지우고 다시) · `done`(SummaryResponse). 서버 전체에서 요약은 한 번에 1개만 돈다(`SUMMARY_MAX_CONCURRENT`). 자리가 없으면 1초만 기다리고 '다른 요약이 진행 중입니다' 안내(`LLM_FAILED`, 재시도 가능)로 끝난다. UI 연결 방법은 [summary_stream.md](summary_stream.md).

### 호출 트리

보조 함수(이름이 `모함수_`로 시작)는 모함수 기능을 단계별로 나눈 것이다. 새 기능은 없다.

```
ui.services.BackendServices.ask (박경환)
└─ ask(question, filters, user)                       FUN-401
   ├─ validate_question                               FUN-402
   ├─ ask_normalize_filters                           FUN-401 보조  dict·None → ChatFilters
   ├─ retrieve_evidence                               FUN-403
   │   └─ search.search_chunks(user, query, company_ids, doc_types, document_ids, top_k)   (이준영)
   ├─ select_evidence                                 FUN-404
   │   ├─ select_evidence_dedupe                      FUN-404 보조  같은 청크 1개
   │   └─ select_evidence_limit                       FUN-404 보조  문서당 max(4, 8 // 문서 수)·전체 8
   ├─ build_no_evidence_response                      FUN-406  (근거 0개일 때)
   ├─ assign_aliases → format_evidence                FUN-411, 412 (format_source_label 417)
   ├─ generate_answer                                 FUN-405
   │   └─ build_answer_chain → get_chat_model         FUN-409, 408 → common.config.get_settings (이성민)
   ├─ ask_check_answer                                FUN-401 보조  유보 답변이면 None
   │   └─ validate_citations                          FUN-414
   │       ├─ validate_citations_answer               FUN-414 보조  빈 답·길이
   │       ├─ validate_citations_aliases              FUN-414 보조  이름표 유무·없는 이름표 (extract_cited_aliases 413)
   │       └─ validate_citations_line × 줄            FUN-414 보조  문장마다 이름표
   │           ├─ validate_citations_heading          FUN-414 보조  마크다운 제목 줄이면 검사 생략
   │           ├─ validate_citations_split            FUN-414 보조  문장 나누기
   │           └─ validate_citations_exempt           FUN-414 보조  제목 줄·유보 문장
   ├─ build_citation × n                              FUN-415 (make_excerpt 416)
   ├─ recheck_before_return                           FUN-407
   │   └─ auth.permissions.verify_documents_access(user, refs)                           (전재경)
   └─ ask_build_response                              FUN-401 보조
       └─ render_answer, group_citations_by_company   FUN-418, 419
   오류: map_dependency_error(427) → ChatbotError(425) → to_feedback(426)
         map_dependency_error_access · _search · _llm (427 보조)
         to_feedback_access · to_feedback_status (426 보조)

ui.services.BackendServices.summarize (박경환)
└─ summarize_document(document_id, user)              FUN-420  스트림을 끝까지 돌려 SummaryResponse만
   └─ summarize_document_stream                       FUN-420 보조  이벤트 제너레이터
      ├─ acquire_summary_slot                         FUN-420 보조  동시 요약 1개
      ├─ summarize_document_load                      FUN-420 보조  청크 조회 (오류 → RETRIEVAL_FAILED)
      │   ├─ embedding.get_document_chunks            (박영준)
      │   └─ summarize_document_to_hit × n            FUN-420 보조  VectorHit → ChunkHit
      ├─ recheck_before_return (사전 확인)            FUN-407 → verify_documents_access (전재경)
      ├─ plan_summary_segments                        FUN-424
      │   ├─ count_summary_tokens → get_summary_encoder  FUN-424 보조  tiktoken o200k
      │   ├─ step_token_limit                         FUN-424 보조
      │   ├─ pack_segments                            FUN-424 보조
      │   └─ trim_overflow                            FUN-424 보조
      ├─ assign_aliases                               FUN-411
      ├─ summarize_rolling                            FUN-421
      │   └─ summarize_step × 구간                    FUN-421 보조  재시도·실패 처리 (can_retry)
      │       ├─ build_step_evidence · allowed_step_aliases   FUN-421 보조
      │       ├─ invoke_summary (중간 구간)           FUN-421 보조 → build_summary_chain('step')  FUN-410
      │       └─ stream_summary (마지막 구간)         FUN-422 → build_summary_chain('final')
      │           ├─ stream_pieces                    FUN-422 보조  스트리밍 거절 시 한 번에
      │           ├─ split_complete_lines             FUN-422 보조
      │           └─ emit_line × 줄                   FUN-422 보조  validate_citations_line · check_line_aliases · AliasLineRenderer
      ├─ collect_citations                            FUN-423 보조  build_citation × n (FUN-415)
      ├─ recheck_before_return                        FUN-407
      └─ summarize_document_response                  FUN-423 보조
          ├─ compute_coverage · format_excluded · compute_cited_ratio   FUN-423
          └─ render_answer                            FUN-418
```

## 4. 함수 명세

### ask.py

| ID | 함수 | 입력 | 출력 | 처리·규칙 |
|---|---|---|---|---|
| FUN-401 | `ask` | `question`, `filters`(ChatFilters·dict, None이면 허용 범위 전체), `user` | ChatResponse | 흐름 A 전체 |
| 401 보조 | `ask_normalize_filters` | `filters` | ChatFilters | dict는 `ChatFilters(**dict)`, None은 빈 ChatFilters. 모르는 키 → RETRIEVAL_FAILED('검색 조건 형식이 올바르지 않습니다.') |
| 401 보조 | `ask_check_answer` | `answer`, `aliases` | 쓰인 이름표 또는 None | `validate_citations`가 NO_EVIDENCE면 None(→ 유보 응답), 그 밖의 오류는 그대로 |
| 401 보조 | `ask_build_response` | `answer`, `used`, `citations` | ChatResponse(`ANSWERED`) | `render_answer`·`group_citations_by_company`, 답변에 쓰인 문서 ID |
| FUN-402 | `validate_question` | `question`, `max_chars=2000` | 공백 정리된 질문 | 문자열 아님·공백뿐 → EMPTY_QUESTION, 2000자 초과 → QUESTION_TOO_LONG |
| FUN-403 | `retrieve_evidence` | `question`, `ChatFilters`, `user` | `list[ChunkHit]` | `search_chunks(user=, query=, company_ids=, doc_types=, document_ids=, top_k=TOP_K×2)`. min_score는 넘기지 않음(search 설정 0.3). import도 try 안에서 해 search가 없을 때(ImportError)도 FUN-427로 RETRIEVAL_FAILED |
| FUN-404 | `select_evidence` | `hits` | `list[ChunkHit]` | 같은 chunk_id는 점수 높은 것 하나 → 점수순 → 문서당 상한 → 전체 8개 |
| 404 보조 | `select_evidence_dedupe` | `hits` | `list[ChunkHit]` | chunk_id마다 점수가 가장 높은 것 1개 |
| 404 보조 | `select_evidence_limit` | 점수순 `hits` | `list[ChunkHit]` | 문서당 상한 = max(MAX_PER_DOCUMENT 4, TOP_K 8 // 근거에 나온 문서 수) → 문서 1개면 8, 2개 이상이면 4. 전체 TOP_K(8) (#182) |
| FUN-405 | `generate_answer` | `question`, `evidence_text` | 답변 원문(str) | 체인 실행. 시간 초과·속도 제한 → LLM_FAILED(재시도 가능), 인증 오류 → CONFIG_MISSING |
| FUN-406 | `build_no_evidence_response` | `filters` | ChatResponse(`NO_EVIDENCE`) | LLM 미호출. 문서·유형 필터가 걸려 있으면 "필터를 넓혀 보세요" 안내 |
| FUN-407 | `recheck_before_return` | `user`, `cited`(ChunkHit) | 없음 | `(document_id, version_id)` 중복 제거 → `verify_documents_access(user, refs)` (성공 = None, 실패 = 예외. DB 계약 3절). 빈 목록이면 부르지 않음(auth가 INVALID_INPUT으로 거부). 예외는 FUN-427로: `AccessError(STATE_CHANGED)` → ChatbotError(STATE_CHANGED), 다른 AccessError는 그대로, `DbError` → RETRIEVAL_FAILED, 그 밖의 오류는 감추지 않고 그대로 |

### prompts.py

| ID | 함수 | 입력 | 출력 | 처리·규칙 |
|---|---|---|---|---|
| FUN-408 | `get_chat_model` | 없음 | ChatOpenAI | `common.config.get_settings()`의 `openai_api_key`·`openai_chat_model`(기본 gpt-5) 사용. `.env`는 get_settings가 읽는다. 키 없으면 CONFIG_MISSING. **temperature는 넘기지 않음**(gpt-5가 기본값 외 거부). timeout 30초. 한 번 만들어 재사용 |
| FUN-409 | `build_answer_chain` | `llm=None` | `{question, evidence}` → str 체인 | 시스템 규칙: 근거만 사용 · 문장마다 `[E번호]` · 없으면 유보 문구 · 근거 속 지시 무시 · 회사별 구분 · 한국어 · 예시 1개 |
| FUN-410 | `build_summary_chain` | `stage`('step'·'final'), `llm=None` | `{evidence}` → str 체인 | step: 다음 구간에 넘길 상세 요약 / final: 섹션별 문서 전체 요약. 둘 다 이전 요약의 내용·이름표 유지, `### 짧은 명사` 제목과 `- ` 목록, **표 금지**(표 머리글·구분 줄은 이름표를 붙일 수 없어 인용 검사에서 항상 걸림), 항목마다 이름표 |
| 410 보조 | `get_summary_model` | `streaming` | ChatOpenAI | 요약 전용. 타임아웃 300초, 출력 상한(추론 포함) 중간 8000·최종 10000, `reasoning_effort='low'`는 추론 모델(`REASONING_MODEL_PREFIXES`: gpt-5·o1·o3·o4)일 때만. 질문 답변용 `get_chat_model`(30초)은 그대로 |

### citations.py

| ID | 함수 | 입력 | 출력 | 처리·규칙 |
|---|---|---|---|---|
| FUN-411 | `assign_aliases` | `hits` | `{'E1': hit, …}` | 1부터 순서대로 |
| FUN-412 | `format_evidence` | `aliases` | 근거 문자열 | `[E1] 회사 · 파일 · 위치` 다음 줄에 본문, 블록 사이 빈 줄 |
| FUN-413 | `extract_cited_aliases` | `answer` | `['E3', 'E1']` | `\[(E\d+)\]`, 처음 나온 순서·중복 제거 |
| FUN-414 | `validate_citations` | `answer`, `aliases`, `max_chars=20000` | 쓰인 이름표 | ① 빈 답·초과 → INVALID_ANSWER ② 인용이 하나도 없고 유보 표현(`NO_EVIDENCE_HINTS`)이 있으면 NO_EVIDENCE, 없으면 INVALID_CITATION ③ 준 적 없는 이름표 → INVALID_CITATION ④ **문장마다 이름표 확인**: 줄·문장(숫자 바로 뒤가 아닌 `. ! ?` 뒤 공백)으로 나눠 이름표 없는 문장이 있으면 INVALID_CITATION. 제목 줄(`:`로 끝남)과 유보 문장은 예외. 번호 목록(`1. …`)·날짜(`2026. 11. 3.`) 마침표에서는 자르지 않음. 마침표 뒤에 온 이름표(`…합니다. [E1]`, `…합니다. [E1] 다음 문장 [E2].`)는 같은 줄 바로 앞 문장의 근거로 인정. 다음 줄 이름표는 인정 안 함 |
| 414 보조 | `validate_citations_answer` | `answer`, `max_chars` | 공백 정리된 답변 | 빈 답·초과 → INVALID_ANSWER (①) |
| 414 보조 | `validate_citations_aliases` | `answer`, `aliases` | 쓰인 이름표 | ②·③ |
| 414 보조 | `validate_citations_line` | 답변 1줄 | 없음 | ④. 마크다운 제목 줄이면 바로 통과. 문장 앞 이름표(`LEADING_ALIASES`)는 앞에 무인용 문장이 있으면 그 문장의 근거 |
| 414 보조 | `validate_citations_heading` | 답변 1줄 | bool | 줄 전체가 `### 제목`·`**제목**`·`__제목__`(뒤 `:` 허용)이고, 제목이 40자 이하이며 문장 끝말(`HEADING_SENTENCE_ENDINGS`: `.` `!` `?` `다` `니다` `세요` `해요` `어요` `아요` `에요` `예요` `네요` `죠`)로 끝나지 않고 이름표가 없으면 True. `개요`·`중요`·`필요`처럼 `요`로 끝나는 명사 제목은 통과 (예전 규칙은 `요` 한 글자로 막아 요약의 `### 개요`가 거부됨) |
| 414 보조 | `check_line_aliases` | 답변 1줄, 허용 이름표 | 없음 | 준 적 없는 이름표가 있으면 INVALID_CITATION. 요약 스트리밍에서 줄을 화면에 보내기 전에 쓴다 |
| 414 보조 | `validate_citations_split` | 답변 1줄 | 문장 목록 | `SENTENCE_END`로 나누고 목록 기호 `-*#`·공백 제거 |
| 414 보조 | `validate_citations_exempt` | 문장 1개 | bool | 이름표 있음·빈 문장·`:` 제목 줄·유보 표현이면 True |
| FUN-415 | `build_citation` | `hit`, `number` | Citation | 필수 필드(company_name, file_name, document_id, location_label)가 비면 INVALID_CITATION. **version_id는 필수 아님**(실제 DB에 버전이 없어 빈 값 가능) |
| FUN-416 | `make_excerpt` | `text`, `max_chars=300` | 발췌문 | 공백 정리, 넘으면 마지막 공백에서 자르고 `…` |
| FUN-417 | `format_source_label` | `hit` | `A사 · 교육안내.pdf · p.2` | 빈 값은 건너뜀. 위치는 ingest의 location_label 그대로. 통합본은 입력을 metadata로 적었지만 ChunkHit 결정에 따라 hit을 받음 |
| FUN-418 | `render_answer` | `answer`, `used` | (`…[1]…`, {이름표: 번호}) | 정규식으로 이름표 단위 치환 (E1·E10 섞임 없음) |
| FUN-419 | `group_citations_by_company` | `citations` | `{회사명: [Citation]}` | 처음 나온 회사 순 |
| 자료형 | `AliasLineRenderer.render` | 검사를 통과한 줄 | 화면용 줄 | `[E3]`을 처음 나온 순서대로 `[1]`로. `render_answer`와 같은 번호 규칙이라 스트리밍 중 번호와 최종 출처 번호가 같다 |

### summary.py

| ID | 함수 | 입력 | 출력 | 처리·규칙 |
|---|---|---|---|---|
| FUN-420 | `summarize_document` | `document_id`, `user` | SummaryResponse | `summarize_document_stream`을 끝까지 돌려 `done`의 결과만. ui가 지금 부르는 입구라 이름·반환 형식 유지. 함수 이름은 미정(D10)이라 제안 이름으로 구현 |
| 420 보조 | `summarize_document_stream` | `document_id`, `user` | 이벤트 제너레이터 | 흐름 B 전체. 청크를 받은 뒤 실제 버전으로 접근 확인(B3). 입력 길이 초과면 상한 × `SUMMARY_REPLAN_RATIO`(0.85)로 한 번 다시 나눔. 재검증 실패는 `reset` 후 오류 |
| 420 보조 | `acquire_summary_slot` | 없음 | (기다릴 때) status 이벤트 | 서버 전체 동시 요약 `SUMMARY_MAX_CONCURRENT`(1)개. 한 요약이 분당 토큰 한도를 크게 써서 동시에 돌면 질문 답변까지 한도 초과로 실패. 자리가 없으면 `SUMMARY_SLOT_WAIT_SECONDS`(1초)만 기다리고 `LLM_FAILED`(재시도 가능, '다른 요약이 진행 중입니다. 잠시 후 다시 시도해 주세요.'). ui가 작업 스레드(`asyncio.to_thread`)에서 부르므로 끝없이 기다리면 대기 요청마다 공용 스레드 풀을 붙잡아 검색·질문까지 멈춘다 (PR 리뷰 재현: 대기 12건으로 풀 소진) |
| 420 보조 | `summary_event` | 종류, 필드 | `{'type': 종류, …}` | 이벤트 형식을 한 곳에서 |
| 420 보조 | `summarize_document_load` | `document_id` | `list[ChunkHit]` | embedding·search import와 청크 조회를 try 안에서 → 실패·ImportError는 RETRIEVAL_FAILED. metadata 형식 오류는 그대로 올림 |
| 420 보조 | `summarize_document_to_hit` | VectorHit, ChunkHit 클래스 | ChunkHit | metadata 키로 변환, version_no·score는 None |
| FUN-421 | `summarize_rolling` | SummaryPlan, 이름표 사전 | (최종 요약 원문, 인용 검사로 뺀 구간) | 구간마다 status, 앞 구간 요약을 다음 입력에. 마지막 구간만 스트리밍 |
| 421 보조 | `summarize_step` | 마지막 여부, 구간 이름표, 이전 요약, 전체 이름표, 호출 수, 남은 단계 | (요약 또는 None, 호출 수, 버렸는지) | INVALID_CITATION·INVALID_ANSWER는 `can_retry`면 재시도(마지막 단계는 먼저 `reset`). 중간 NO_EVIDENCE는 건너뜀. 중간 실패는 구간만 제외, 마지막 실패는 오류 |
| 421 보조 | `build_step_evidence` | 구간 이름표, 이전 요약 | 입력 문자열 | `이전 요약:` / `이번 구간 근거:`로 나눠 적음 |
| 421 보조 | `allowed_step_aliases` | 구간 이름표, 이전 요약, 전체 이름표 | 이름표 사전 | 이번 구간 + 이전 요약에 나온 이름표만 (예전 `merge_summaries_allowed`와 같은 원칙) |
| 421 보조 | `can_retry` | 호출 수, 남은 단계 | bool | `호출 수 + 1 + 남은 단계 ≤ 3` → 1구간 최대 3회 중 재시도, 2구간 최대 3회, 3구간 재시도 없음 |
| 421 보조 | `invoke_summary` | 입력, 허용 이름표 | 중간 요약 | `step` 체인 한 번 호출 + `validate_citations(…, MAX_SUMMARY_CHARS)` |
| FUN-422 | `stream_summary` | 입력, 허용 이름표 | delta 이벤트들 → 최종 요약 원문 | `final` 체인 스트리밍. 줄 완성마다 `emit_line`, 끝에 마지막 줄·전체 검사 |
| 422 보조 | `stream_pieces` | 체인, 입력 | 문자열 조각 | 스트리밍 거절(`is_stream_rejected`)이면 invoke 한 번. stream 없는 체인(테스트 대역)도 invoke. 그 밖 오류는 `to_llm_error` |
| 422 보조 | `emit_line` | 줄, 허용 이름표, 렌더러, 줄 끝 | delta 이벤트 | `validate_citations_line` + `check_line_aliases` 통과 후 화면 번호로 |
| 422 보조 | `split_complete_lines` | 버퍼 | (완성된 줄들, 남은 조각) | 마지막 줄바꿈 기준 |
| 422 보조 | `is_stream_rejected` · `is_context_overflow` · `to_llm_error` | 예외 | bool / 오류 | `map_dependency_error`에 넘기기 전에 판별. 길이 초과는 내부 신호 `_ContextOverflow`(화면에 안 나감) |
| FUN-423 | `compute_coverage` | 전체 청크, 사용 청크 | (포함 범위, 제외 범위, is_full) | 포함 범위만 응답에 씀 (제외 범위는 `format_excluded`) |
| 423 보조 | `collect_citations` | 요약 원문, 이름표 | (쓰인 이름표, 출처, 인용 청크) | |
| 423 보조 | `format_excluded` | 제외 청크, 사유 | `사유 p.7~p.10` | 사유: `예산 초과로 제외`, `근거 확인 실패로 제외` |
| 423 보조 | `compute_cited_ratio` | 쓰인 이름표, 사용 청크 | float | 인용 청크 비율. 일부 구간이 요약에서 빠졌는지 보는 지표 |
| 423 보조 | `summarize_document_response` | 문서 ID, 전체·사용 청크, 예산 초과 청크, 뺀 구간, 요약, 이름표, 출처 | SummaryResponse | 뺀 것이 있으면 제외 범위에 사유, is_full=False |
| FUN-424 | `plan_summary_segments` | `hits`, 단계 상한 | SummaryPlan | 상한 단위로 담아 구간 수(≤3)를 정하고 고르게 나눠지면 고르게. 청크는 쪼개지 않음 |
| 424 보조 | `get_summary_encoder` · `count_summary_tokens` · `step_token_limit` · `pack_segments` · `trim_overflow` | | | 인코더 1회 로드(실패 시 글자×2), 토큰×1.03, 23만−8천−1천, 상한까지 담기, 앞 3구간 외 제외 |

### doc_classifier.py (명세 밖 추가, 10/29)

OpenAI Decisions API(`gpt-6-luna`)는 글 대신 보기 중 하나·확률로 답한다. 입구는 두 개다: 화면에서 선택한 문서(`classify_document`)와, 업로드 중 수집 정규화 직후의 문서(`classify_common_document`). 업로드 경로 결과는 수집 보고서를 거쳐 업로드 작업 기록(`index_jobs.report`의 `ingest`)에 남는다. 관리자 doc_type과 청크는 바꾸지 않는다.

업로드 연결(다른 폴더, 10/29): `ingest/pipeline.py`의 `ingest_document(..., classify=False)`가 `classify=True`일 때 정규화 직후 `classify_normalized`로 이 함수를 부르고, `db/upload_service.py`의 `ingest_and_save_chunks`가 실제 업로드에서만 `classify=True`를 넘긴다(수집기가 이 인자를 받을 때만). `db/jobs_repo.py`는 수집 단계 보고서의 `auto_doc_type`·`doc_type_confidence`·`company_match`·`classify_status`를 검사해 저장한다.

| ID | 함수 | 입력 | 출력 | 처리·규칙 |
|---|---|---|---|---|
| FUN-429 | `classify_document` | `document_id`, `user` | DocClassification | 공개 청크 조회(`summarize_document_load`) → 접근 확인(`recheck_before_return`, API 호출 전) → 같은 버전이면 보관한 결과 → Decisions API 1회. 조회 실패 RETRIEVAL_FAILED, 청크 없음 NO_EVIDENCE, 권한 오류는 그대로. 분류 실패는 `status='failed'` |
| 429 보조 | `classify_hits` | ChunkHit 목록 | DocClassification | 키 없음·호출 오류·거부 응답도 예외 대신 failed(detail은 예외 이름만). 관리자 유형과 같은지(`matches_admin_type`) 계산 |
| FUN-434 | `classify_common_document` | ingest 정규화 후 CommonDocument | DocClassification | 업로드 흐름 안이라 접근 확인·메모리 보관 없음. 본문이 없으면 API를 부르지 않고 failed(EMPTY_DOCUMENT). 어떤 오류도 올리지 않음 |
| 429·434 보조 | `run_classification` | 결과, 파일명, 회사명, 본문 목록 | DocClassification | 두 입구 공통. API 1회, 실패는 failed |
| 429 보조 | `remember` · `recall` | 결과 / 버전 ID | 없음 / 결과 | 성공한 결과만 버전별로 메모리에 보관, `CLASSIFY_CACHE_SIZE`(256) 넘으면 오래된 것부터 삭제 |
| FUN-430 | `build_classify_input` | 파일명, 본문 목록 | str | `파일명: …` + 앞쪽 본문 `CLASSIFY_SAMPLE_CHARS`(6000자)까지 (청크 경로는 chunk_index 순으로 넘김) |
| FUN-431 | `build_classify_questions` | 업로드 회사명 | 질문 목록 | `doc_type`(choice, DOC_TYPES 4종)·회사명이 있으면 `company_named`·`company_match`(predicate) |
| FUN-432 | `read_classify_answers` | Decisions 응답 | (유형, 확신도, 회사 일치) | 유형 답 없음·거부 → ValueError. 목록 밖 유형 → ('', 0.0). 회사명 기재 확률 0.5 이상일 때만 일치 확률 0.7 이상 yes, 0.3 이하 no |
| FUN-433 | `get_classify_client` | 없음 | OpenAI 클라이언트 | 키는 `common.config.get_settings()`. 없으면 CONFIG_MISSING. 20초 제한·재시도 1번, 키마다 한 번만 생성 |

**DocClassification (CLS-126, models.py):** `document_id`, `version_id`, `admin_doc_type`(관리자 유형), `auto_doc_type`(manual·policy·guide·other·''), `confidence`(0~1), `matches_admin_type`(bool, 자동 유형이 비면 None), `company_match`('yes'·'no'·''), `status`('success'·'failed'), `detail`.

### models.py

| ID | 함수 | 입력 | 출력 | 처리·규칙 |
|---|---|---|---|---|
| FUN-425 | `ChatbotError.__init__` | `code`, `message=None`, `retryable=False` | 예외 | code는 ERROR_CODES 중 하나, 문구 없으면 기본 문구 |
| FUN-426 | `to_feedback` | 예외 | `{status, message, error_code}` | 8절 표. auth `AccessError`는 STATE_CHANGED면 상태 변경 안내, SESSION_EXPIRED면 재로그인 안내(session_expired), 나머지는 권한 없음 안내. 내부 예외 원문은 넣지 않음 |
| FUN-427 | `map_dependency_error` | 예외, `stage`('retrieve'·'llm'·'summary'·'recheck') | ChatbotError(또는 원래 권한 예외) | AccessError(STATE_CHANGED) → STATE_CHANGED, 다른 AccessError → 그대로, DbError → RETRIEVAL_FAILED(DB_UNAVAILABLE이면 재시도 가능), 'recheck' 단계의 그 밖의 오류 → 그대로, SearchError 권한 계열 → ACCESS_DENIED, 'retrieve'·'summary' 단계 → RETRIEVAL_FAILED, 'llm' 단계 → 8절 규칙 |
| 426 보조 | `to_feedback_access` | AccessError | 화면 안내 dict | STATE_CHANGED → failed, SESSION_EXPIRED → session_expired(재로그인), 나머지 → ACCESS_DENIED(denied) |
| 426 보조 | `to_feedback_status` | 오류 코드 | 화면 상태 | `FEEDBACK_STATUS` 표, 없으면 failed |
| 427 보조 | `map_dependency_error_access` | AccessError | ChatbotError 또는 원래 예외 | STATE_CHANGED만 ChatbotError로 |
| 427 보조 | `map_dependency_error_search` | SearchError | ChatbotError | SESSION_EXPIRED → SESSION_EXPIRED(재로그인), 권한 코드 → ACCESS_DENIED, 나머지 → RETRIEVAL_FAILED |
| 427 보조 | `map_dependency_error_llm` | 예외 클래스 이름 | ChatbotError | 인증 → CONFIG_MISSING, 시간 초과·속도 제한·연결 → LLM_FAILED(재시도), 그 밖 → LLM_FAILED |

## 5. 데이터 형식

### 받는 것

| 형식 | 담당 | 필드 | 쓰는 곳 |
|---|---|---|---|
| ChunkHit (CLS-051) | 이준영 `search/models.py` | chunk_id, document_id, version_id, version_no, company_id, company_name, doc_type, file_name, section_no, location_label, chunk_index, text, score | 질문·요약의 모든 근거 |
| VectorHit (CLS-105) | 박영준 `embedding/models.py` | chunk_id, text, metadata(ChunkMetadata CLS-108), score | 요약 경로에서만 받고 FUN-420 안에서 ChunkHit로 변환 |
| Settings | 이성민 `common/config.py` (구현됨) | openai_api_key, openai_chat_model 외 | FUN-408 |
| user (UserContext, CLS-122) | 전재경 auth | user_id, company_id, role, … | 열어 보지 않고 search·auth에 그대로 넘김 |

### 주는 것 (`hyundap/chatbot/models.py`)

| 클래스 | 소스 위치 | 필드 |
|---|---|---|
| ChatFilters (CLS-121) | `chatbot/models.py` | company_ids, doc_types, document_ids (모두 None 가능 = 허용 범위 전체) |
| Citation (CLS-123) | `chatbot/models.py` | number, chunk_id, document_id, version_id, version_no, company_id, company_name, file_name, doc_type, location_label, excerpt, score |
| ChatResponse (CLS-124) | `chatbot/models.py` | status(`ANSWERED`·`NO_EVIDENCE`), answer, citations, citations_by_company, document_ids, message, error_code |
| SummaryResponse (CLS-125) | `chatbot/models.py` | document_id, version_id, summary, citations, included_range, excluded_range, is_full, total_chunks, used_chunks, cited_ratio(추가, 기본값 0.0 — ui는 읽지 않아도 됨) |
| SummaryPlan (내부) | `chatbot/models.py` | segments, excluded, total_tokens — summary 안에서만 씀 |

가져오는 법 (이슈 #133): chatbot 안에서는 `from .models import ChatFilters, Citation, ChatResponse, SummaryResponse`, 다른 폴더에서는 `from hyundap.chatbot.models import ChatFilters`.

UI(박경환)는 결과를 dict로 바꿔 쓴다. 질문 결과에서 `status`·`answer`·`citations`·`message`·`error_code`를, 요약 결과에서 `document_id`·`version_id`·`summary`·`is_full`·`included_range`·`excluded_range`·`total_chunks`·`used_chunks`를 읽으며 위 필드와 이름이 같다. 필드 이름을 바꿀 때는 박경환과 먼저 맞춘다.

`document_ids`는 ui가 문서를 바꿀 때 이전 답변이 섞이지 않게 확인하는 데 쓴다(통합본 L29). 결과·필터 클래스 4개는 chatbot만 만들고 ingest·embedding·search는 쓰지 않으므로 `chatbot/models.py`에 둔다 (D04, 이성민 결정 10/26. 통합본 MOD-125의 "공용 스키마는 common.schemas" 문구는 이 결정으로 바뀜). search의 `SearchCitation`(CLS-052)과 합치기로 하면 그때 common으로 옮긴다.

## 6. 다른 폴더와의 연결

| 함수 | 담당 | 현재 상태 | 지금 코드의 처리 |
|---|---|---|---|
| `common.config.get_settings` | 이성민 | **구현됨** (`Settings` 반환, `.env` 읽음, 기본 모델 gpt-5) | 그대로 사용 |
| `hyundap.search.search.search_chunks` (FUN-244) | 이준영 | **구현됨** (#143) | 호출할 때 try 안에서 import. 오류는 FUN-427로 변환 |
| `hyundap.search.models.ChunkHit` (CLS-051) | 이준영 | **구현됨** (#143) | 질문 경로는 속성 이름으로만 읽음. 요약 경로(`summarize_document_load`)는 이 클래스로 만듦 |
| `search.errors.SearchError` (CLS-055) | 이준영 | **구현됨** (#143) | 클래스 이름과 `code`·`retryable` 속성으로 구분. `SESSION_EXPIRED`는 재로그인 안내, 권한 코드는 ACCESS_DENIED, 나머지는 RETRIEVAL_FAILED |
| `hyundap.auth.permissions.verify_documents_access` | 전재경 | **구현됨** (`(actor, list[(document_id, version_id)])` → None, 실패는 `auth.errors.AccessError(code)`·`db.errors.DbError(code)`. 빈 version_id는 DbError(INVALID_INPUT)) | 호출할 때 import. 예외 코드별 처리는 FUN-407·427 |
| `hyundap.embedding.vector_store.get_document_chunks` (FUN-319) | 박영준 | **구현됨** (metadata 키에 요약이 읽는 9개 모두 있음) | `summarize_document_load`에서 try 안에서 import |
| ui `BackendServices.ask`·`summarize`·`_call` | 박경환 | 구현됨, `create_bindings()`는 아직 비어 있음 | `ask`, `summarize_document`, `to_feedback`을 제공. filters는 dict 그대로 받으므로 `filters_factory`는 선택 |

## 7. 기본값·규칙

| 이름 | 값 | 근거 |
|---|---|---|
| TOP_K / MAX_PER_DOCUMENT | 8 / 4 | 제안값 (미정 D07). search에 16개 요청. 근거 문서가 1개면 문서당 상한은 8 (#182) |
| 최소 점수 | 0.3 — search가 적용 | 통합본 L54 |
| MAX_QUESTION_CHARS | 2000 | 통합본 L50·L104와 같은 기준 |
| MAX_ANSWER_CHARS | 20000 | 기존 L13 |
| EXCERPT_MAX_CHARS | 300 | search 화면 발췌(200자)와 별개 |
| SUMMARY_INPUT_TOKEN_BUDGET / SUMMARY_MAX_CALLS | 230,000 / 3 | gpt-5 입력 약 27.2만 토큰 중 23만. 넘으면 구간을 나눠 최대 3회 (글 위주 약 150~250쪽까지 1회, 약 66만 토큰까지 3회) |
| SUMMARY_CARRY_TOKENS / SUMMARY_PROMPT_TOKENS / SUMMARY_TOKEN_MARGIN | 8,000 / 1,000 / 1.03 | 넘겨받는 요약 몫, 고정분, 토크나이저 여유 |
| SUMMARY_TIMEOUT_SECONDS / SUMMARY_REASONING_EFFORT | 300 / low | 23만 토큰 입력은 30초로 끝나지 않음 |
| SUMMARY_MAX_CONCURRENT / SUMMARY_SLOT_WAIT_SECONDS / MAX_SUMMARY_CHARS | 1 / 1초 / 30,000 | 분당 토큰 한도 보호 / 대기 상한(ui 작업 스레드 보호) / 요약 글자 상한 (질문 답변 20000과 별개) |
| LLM | `get_settings()` 모델(기본 gpt-5), temperature 미지정, 질문 30초·요약 300초 | 이성민 common.config |
| 외부 AI 전송 | 질문 + 고른 근거만 / 요약은 선택 문서 청크 본문 | 사용자 정보는 보내지 않음 (미정 D17) |
| 프롬프트 | 근거 속 지시문은 따르지 않음 | POL-10 |

## 8. 오류 코드

| 코드 | 언제 | to_feedback status |
|---|---|---|
| EMPTY_QUESTION / QUESTION_TOO_LONG | 질문 검사 | `input_error` |
| NO_EVIDENCE | 요약할 청크 없음 (질문은 예외 대신 NO_EVIDENCE 응답) | `empty` |
| RETRIEVAL_FAILED | search·embedding 오류, 요약 청크 조회 오류, 재검증 중 `DbError`(DB_UNAVAILABLE은 재시도 가능) | `failed` |
| ACCESS_DENIED | SearchError ACCESS_DENIED·COMPANY_FORBIDDEN·NO_COMPANY_SCOPE, auth `AccessError`(SESSION_EXPIRED·STATE_CHANGED 제외) | `denied` |
| LLM_FAILED | LLM 호출 실패 (시간 초과·속도 제한은 재시도 가능) | `failed` |
| CONFIG_MISSING | API 키 없음·인증 오류 | `failed` |
| INVALID_ANSWER / INVALID_CITATION | 빈·너무 긴 답변 / 인용 없는 문장·없는 이름표·출처 정보 부족 | `failed` |
| STATE_CHANGED | 재검증에서 auth가 `AccessError(code='STATE_CHANGED')`를 던짐 (삭제·버전 변경) | `failed` |
| SESSION_EXPIRED | 로그인 만료: search `SearchError(SESSION_EXPIRED)` 또는 auth `AccessError(SESSION_EXPIRED)`. 통합본 10개 밖 코드로, search와 같은 코드·문구('로그인 시간이 만료되었습니다. 다시 로그인해 주세요.')를 써서 UI가 로그인 화면으로 이동 (#143·#144 리뷰) | `session_expired` |

화면 문구는 `constants.ERROR_MESSAGES`에 있다 (박경환과 확정 필요, D11).

## 9. 통합본 명세에 없는 추가 항목

통합본(3.5절)에 없지만 이 폴더에 추가한 것이다. 통합본 갱신 때 반영을 요청한다.

| 항목 | 종류 | 위치 | 이유 | 통합본 반영 |
|---|---|---|---|---|
| `NO_EVIDENCE_HINTS` | 상수 | constants.py | LLM이 유보 문구를 바꿔 말해도 근거 없음으로 처리 (리뷰 반영) | 선택 |
| `ERROR_MESSAGES`, `ERROR_CODES` | 상수 | constants.py | 오류 코드별 화면 문구 (D11) | 요청 |
| `ACCESS_ERROR_NAMES`, `SEARCH_ACCESS_CODES` | 상수 | constants.py | 다른 폴더 오류를 이름·코드로 구분 (auth·db 클래스는 생겼고 이 방식으로 동작, SearchError는 아직 없음) | 선택 |
| `FEEDBACK_STATUS` | 상수 | constants.py | 오류 코드 → 화면 상태 표 (`to_feedback_status`) | 내부용 |
| `SENTENCE_END`, `LEADING_ALIASES`, `REQUIRED_FIELDS`, `ALIAS_PATTERN` | 상수 | citations.py | 문장 나누기(숫자 뒤 마침표 제외)·문장 앞 이름표·필수 출처 필드·이름표 정규식 | 내부용 |
| ask의 dict 필터 받기 | 입력 형태 | ask.py `ask_normalize_filters` | UI가 dict를 넘김 (be58de7, 이성민) | 요청 |
| 보조 함수 23개 (`모함수_…`) | 함수 | 각 파일 | 큰 함수를 단계별로 나눔. 새 기능 없음 | 선택 (통합본에 하위 항목으로) |
| `LLM_TIMEOUT_SECONDS` | 상수 | constants.py | LLM 호출 30초 제한 | 선택 |
| Citation `version_no`, ChatResponse `citations_by_company`·`document_ids` | 클래스 필드 | models.py | WF-15 버전 표시, FR-06 회사별 출처(D08), ui 화면 섞임 방지(L29) | 요청 (D08) |
| `map_dependency_error`의 stage 값 `'recheck'` | 인자 값 | models.py | 재검증 중 auth·DB 오류를 계약대로 바꾸고, 예상 못 한 오류는 감추지 않으려고 | 요청 |
| `classify_document`(FUN-429~433), `DocClassification`(CLS-126), `CLASSIFY_*` 상수 | 기능·클래스·상수 | doc_classifier.py, models.py, constants.py | 문서 유형·업로드 회사 일치 자동 분류 (10/29). 확신도 기준을 정하기 전이라 화면 연결은 아직 없음 | 요청 |

**기능은 통합본 FUN-401~427 그대로다.** 큰 함수 7개는 모함수 이름으로 시작하는 보조 함수 23개로 나눴다(10/26, 3절 호출 트리). 명세 밖 기능은 없다.
**삭제한 것:**

- 리뷰 반영 중 `_setting`(설정 읽기 보조 함수)과 `DEFAULT_CHAT_MODEL`·`LLM_TEMPERATURE` 상수를 지웠다. `common.config.get_settings()`가 생겨 필요 없어졌다.
- **가짜 함수 자리(개발용 주입 인자)를 코드에서 뺐다:** `ask`의 `retriever`·`checker`·`chain`, `retrieve_evidence`의 `retriever`, `generate_answer`의 `chain`, `recheck_before_return`의 `checker`, `summarize_document`의 `checker`·`get_chunks`·`llm`, `merge_summaries`의 `chain`, 임시 객체(SimpleNamespace) 대체. 통합본 FUN-403·405·407·420은 이 자리를 "개발 중 가짜 자리"로 적어 두었으므로 통합본 갱신 때 삭제를 요청한다. 시험은 코드 밖에서 가짜 모듈을 끼워 한다(12절).
- **명세 밖 함수·인자를 뺐다:** `vector_to_chunk_hit`(제안 FUN-428 → VectorHit 변환은 `summarize_document` 안으로), `_to_filters`(dict 필터 변환 → 통합본대로 ChatFilters만 받음), `retrieve_evidence`의 `top_k`, `select_evidence`의 `max_per_document`·`max_total`, `limit_summary_chunks`의 `max_chunks`(모두 constants 값을 직접 씀), 오류 문구 `SESSION_EXPIRED`(통합본 오류 코드 10개만 유지).
- 남긴 것: `build_answer_chain`·`build_summary_chain`의 `llm`(통합본 인자). (`summarize_batch`는 10/28 요약 개편으로 없어짐)

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
| 8 | 부분 요약 LLM 호출이 순서대로라 느림 | 고침 — 8청크 묶음 9회 호출을 토큰 기준 최대 3회 직렬 요약으로 바꿈 (10/28 요약 개편) |
| 9 | 요약 단계 형식 오류를 LLM 실패로 분류 | 고침 — 청크 조회만 RETRIEVAL_FAILED, 형식 오류는 그대로 (FUN-420·427) |
| 10 | 문서 내용이 실제와 다름 | 고침 — 이 문서와 README 갱신 |
| 11 | (DB 계약 반영) 재검증이 `False` 반환을 기다림 — 계약은 예외만 씀 | 고침 — 예외만 처리, STATE_CHANGED 변환 (FUN-407·427) |
| 12 | (DB 계약 반영) AccessError(STATE_CHANGED)도 "권한 없음"으로 안내 | 고침 — STATE_CHANGED만 구분, 나머지는 통합본 코드 안에서 ACCESS_DENIED (FUN-426) |
| 13 | (DB 계약 반영) DbError 처리 없음 | 고침 — RETRIEVAL_FAILED로 변환, 원시 오류 숨김 (FUN-427) |
| 14 | (DB 계약 반영) 요약 사전 확인이 버전ID 없이 `(문서ID, None)` | 고침 — 청크를 받은 뒤 실제 버전으로 확인, 확인 전 LLM 미호출 (FUN-420) |
| 15 | 가짜 함수 자리가 제출 코드에 남음 | 고침 — 주입 인자·임시 객체 삭제 (9절) |
| 16 | 명세 밖 함수·인자가 남음 (`vector_to_chunk_hit`, `_to_filters`, 개수 인자) | 고침 — 통합본 FUN-401~427 함수·인자만 남김 (9절) |
| 17 | import가 팀 기준과 다름 (이슈 #133) | 고침 — 다른 폴더는 `hyundap.` 경로, chatbot 안은 상대 경로. 저장소 맨 위에서 `python -c "import hyundap.chatbot.ask"` 통과 |
| 18 | 번호 목록·날짜 마침표에서 문장이 잘려 정상 답변 거부 | 고침 (be58de7, 이성민) — 숫자 바로 뒤 마침표에서는 자르지 않음 (FUN-414) |
| 19 | 마침표 뒤에 따로 붙은 이름표(`…합니다. [E1]`) 미인정 | 고침 (be58de7, 이성민) — 같은 줄 앞 문장의 근거로 인정 (FUN-414) |
| 20 | UI dict 필터가 RETRIEVAL_FAILED | 고침 (be58de7, 이성민) — ask가 dict를 ChatFilters로 바꿈 (FUN-401) |
| 21 | 한 줄 `문장. [E1] 문장. [E2]` 거부 | 고침 — 문장 앞 이름표를 앞 무인용 문장의 근거로 봄 (`validate_citations_line`) |
| 22 | search 미구현 ImportError가 변환 밖이라 화면에 일반 오류 | 고침 — import를 try 안으로, RETRIEVAL_FAILED (`retrieve_evidence`·`summarize_document_load`) |
| 23 | dict 필터 키 오타가 TypeError → 화면에 일반 오류 | 고침 — RETRIEVAL_FAILED('검색 조건 형식이 올바르지 않습니다.') (`ask_normalize_filters`) |
| 24 | 부분 요약 하나가 인용 검사에 걸리면 요약 전체 실패 | 고침 — 그 묶음만 빼고 제외 범위에 표시, is_full=False (`summarize_document_partials`·`_response`) |
| 25 | 큰 함수 7개 | 고침 — 모함수 이름의 보조 함수 23개로 나눔 |
| 26 | 숫자로 끝난 무인용 문장이 다음 문장과 묶여 통과 | 남김 — 날짜 규칙(18)과 겹쳐 고치면 날짜가 거부됨. 한국어 답변에선 드묾 |
| 27 | (#144 리뷰 1) 세션 만료가 재로그인 안내로 가지 않음 — SearchError(SESSION_EXPIRED)는 RETRIEVAL_FAILED, AccessError(SESSION_EXPIRED)는 ACCESS_DENIED로 바뀜 | 고침 — 오류 코드 SESSION_EXPIRED 추가, status `session_expired`로 전달 (`map_dependency_error_search`·`to_feedback_access`, `FEEDBACK_STATUS`) |
| 28 | (#144 리뷰 2) 요약 묶음이 모두 인용 검사에 걸리면 '요약할 내용이 없습니다'(NO_EVIDENCE)로 안내 | 고침 — 근거 확인 실패(INVALID_CITATION)로 구분 (`summarize_document_empty_error`) |
| 29 | (#144 리뷰 3) 명세 상태 문구가 #143 병합 전 내용 | 고침 — 0절 상태·6절 search 연결 상태 갱신 |
| 30 | (#182) 질문 범위 '선택 문서'에서 문서당 4개 제한 때문에 정답 청크(5·8위)가 빠져 답을 못 찾음 | 고침 — 문서당 상한을 근거 문서 수로 정함: 문서 1개면 8 (`select_evidence_limit`) |
| 31 | 여러 회사 답변에서 AI가 `**A사**`·`### A사` 같은 마크다운 제목 줄을 쓰면 무인용 문장으로 보고 답변 전체를 거부 | 고침 — 줄 전체가 짧은 제목이면 통과, 문장처럼 끝나면 그대로 검사 (`validate_citations_heading`) |

## 11. 미정 사항과 지금 코드의 가정

| 번호 | 내용 | 지금 코드 | 확인할 사람 |
|---|---|---|---|
| D19 | 요약 경로 근거 형식(VectorHit) | `summarize_document_to_hit`에서 ChunkHit로 변환 | 박영준 공유 |
| D10 | 요약 함수 이름·길이 | `summarize_document`, 입력 23만 토큰·최대 3회 직렬 호출 (청크 64개 상한 폐지) | 박경환 |
| D04 | 결과·필터 클래스 위치 | **확정 (10/26)** — 네 개 모두 `chatbot/models.py` | 이성민 결정, 통합본 반영은 이성민 |
| D07 | 근거 개수 | 문서당 max(4, 8 // 문서 수)·전체 8 (#182로 문서 1개일 때 8) | 이준영·이성민 (정확도 테스트 후) |
| D08 | 여러 회사 질문 | 필터 범위 전체에서 8개, 회사별 답변·출처 | 팀 |
| D11 | 오류 문구 | `ERROR_MESSAGES` 초안 | 박경환 |
| - | 버전 정보 | db 코드는 v4(`document_versions`) 기준이고 embedding도 version_id를 필수로 저장한다. 공유 DB에 v4가 적용됐는지는 확인 필요. 빈 version_id는 출처는 만들지만 재검증에서 RETRIEVAL_FAILED | 전재경 (#119) |
| - | 문장 단위 인용 검사 | 실제 gpt-5 답변이 문장마다 이름표를 붙이는지 확인 필요. 너무 자주 거부되면 프롬프트 예시 보강 | 통합 테스트 |

## 12. 테스트

저장소: `python -m unittest hyundap.tests.test_chatbot` — 인용 검사·필터 12개 (이성민 작성, 12/12 통과).

저장소 밖 (`tests/`는 이성민 담당이라 이관은 D16에서 협의): 함수 나누기·오류 수정 후 아래 58개 58/58, 오류 수정·보조 함수 검사 16개 16/16 통과. chatbot 코드에는 가짜 자리가 없으므로, 아직 없는 `hyundap.search.search`·`hyundap.search.models`·`hyundap.auth.permissions`·`hyundap.embedding.vector_store`를 시험 쪽에서 `sys.modules`에 가짜 모듈로 끼우고, LLM은 `hyundap.chatbot.prompts.get_chat_model`을 가짜 모델(`FakeListChatModel`)로 바꿔 저장소 맨 위 기준으로 **58개**를 확인했다 (58/58 통과).

- 질문 흐름: 정상 답변·출처·회사별 묶음, search_chunks 인자, 재검증 인자, 근거 없음·유보, 지어낸 이름표·인용 없음·빈 답변 거부, SearchError 변환, 질문 길이, E1·E10 치환, 문서당 4개
- DB 계약: AccessError STATE_CHANGED·ACCESS_DENIED·SESSION_EXPIRED(→ 재로그인 안내), DbError, 예상 못 한 오류 노출, 요약 사전 확인이 실제 버전으로·권한 없으면 LLM 호출 전 중단
- 요약(`test_chatbot_summary.py`): 1·2·3구간 호출 수와 순서, 고른 구간 나누기, 3구간 초과 제외 범위, 무인용 줄·없는 이름표 줄이 화면에 안 나감, 재시도 상한 3회, 중간 구간 실패 시 체인 유지, 스트리밍 거절 시 invoke, 길이 초과 시 재계획, 제목 규칙(개요 통과·…입니다 거부), 요약 모델 설정, 동시 실행 슬롯 해제
- 모듈마다 통합본 함수만 있는지·인자 모양이 통합본과 같은지, `__all__`
- 리뷰 수정: 문장별 인용, 바꿔 말한 유보, version_id 빈 값, 병합 이름표 제한, `get_settings`로 gpt-5 모델 생성, 키 없음 CONFIG_MISSING

- 문서 분류(`test_chatbot_doc_classifier.py`, 13개): 입력 6000자·순서, 질문 3개, 회사 일치 기준, 목록 밖 유형·거부 응답, 관리자 유형 비교, 접근 확인이 API 호출보다 먼저·권한 없으면 호출 안 함, 청크 없음·조회 실패, API 실패는 failed(키 노출 없음), 키 없음, 같은 버전 보관(접근 확인은 매번), 보관 개수 상한, 실제 클라이언트 설정. 실제 API로 업로드 문서 1건과 합성 문서 16건×2(맞는·다른 회사)를 확인했다(유형 16/16, 회사 일치 yes 13/13·no 13/13, 회사명 없음 빈 값 6/6).

실제 OpenAI 호출과 다른 폴더 실제 함수 연결은 통합 테스트(10/29~)에서 확인한다.

## 13. 일정

| 날짜 | 할 일 |
|---|---|
| 10/27 (Day 4) | 이 명세와 1차 코드 공유, 미정 사항(D07·D10·D11·D19) 확인 |
| 10/28 (Day 5) | develop PR · merge |
| 10/29 (Day 6) | 실제 search·auth·embedding 연결, 실제 gpt-5로 인용 형식 확인, 통합 테스트 |
| 10/30 (Day 7) | 권한 테스트(다른 회사 문서 노출), 근거 개수 조정 |
