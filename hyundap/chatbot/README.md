# chatbot — 근거 기반 답변·요약

담당: 소용우 · 자세한 명세: [`docs/spec.md`](docs/spec.md)

사용자 질문에 대해 **search가 찾아 준 근거 청크로만 답변**하고, 답변 문장마다 **회사·파일·위치 출처**를 붙여 돌려줍니다. 선택한 문서를 요약하는 기능(P2)도 이 폴더에 있습니다.

## 다른 폴더에서 쓰는 법

```python
from hyundap.chatbot import ask, summarize_document, to_feedback
from hyundap.chatbot.models import ChatFilters

try:
    result = ask('신입 교육은 언제 시작해?', ChatFilters(doc_types=['manual']), user)   # 필터는 ChatFilters·dict·None
    # result.answer        → '신입 교육은 11월 3일에 시작합니다 [1].'
    # result.citations     → [Citation(number=1, company_name='A사', file_name='교육안내.pdf', location_label='p.2', excerpt=…)]
    # result.status        → 'ANSWERED' 또는 'NO_EVIDENCE'
except Exception as error:
    feedback = to_feedback(error)   # {'status': 'input_error'|'empty'|'denied'|'failed', 'message': …, 'error_code': …}
```

| 함수 | 하는 일 |
|---|---|
| `ask(question, filters, user)` | 질문 → 근거 검색(search) → LLM 답변 → 인용 검사 → 권한 재확인(auth) → 답변·출처 |
| `summarize_document(document_id, user)` | 선택 문서를 8청크씩 나눠 요약하고 합친 뒤, 요약 범위와 출처를 함께 반환 (P2) |
| `to_feedback(error)` | 오류를 화면 안내 문구로 바꿈 (내부 오류 원문은 숨김) |

## 파일

| 파일 | 내용 |
|---|---|
| `ask.py` | 질문 1개 처리 흐름 |
| `prompts.py` | 답변·요약 프롬프트, OpenAI 연결 |
| `citations.py` | 근거 이름표(E1, E2…), 인용 검사, 출처 만들기 |
| `summary.py` | 선택 문서 요약 |
| `models.py` | 오류 클래스, 오류 변환, 필터·결과 클래스 4개(`ChatFilters`, `Citation`, `ChatResponse`, `SummaryResponse`). 이 네 개는 chatbot만 쓰므로 common이 아니라 여기에 둡니다 (팀 결정). 다른 폴더에서는 `from hyundap.chatbot.models import ChatFilters` |
| `constants.py` | 기본값(근거 8개, 발췌 300자 등)과 오류 안내 문구 |

## 지켜지는 규칙

- 근거로 받은 문서 내용만 사용하고, 근거가 없으면 LLM을 부르지 않고 "제공된 문서에서 확인할 수 없습니다"로 답합니다.
- 답변의 모든 문장에 출처 이름표가 있어야 합니다. 출처 없는 문장이 섞이거나 준 적 없는 출처를 쓰면 그 답변은 화면에 보내지 않습니다 (제목 줄과 "확인할 수 없습니다" 같은 문장은 예외).
- 답변을 돌려주기 직전에 인용한 문서·버전을 지금도 볼 수 있는지 auth에 다시 확인합니다. auth가 오류를 던지면(권한 없음·로그인 만료·문서 상태 변경·DB 장애) 만든 답변은 버립니다 (DB 계약 `docs/db-design/DB_contract.md`).
- 요약은 문서 청크를 받은 뒤 그 실제 버전으로 먼저 접근을 확인하고, 통과해야 LLM을 부릅니다.
- 검색 범위(회사·권한·공개 상태)는 search가, 권한 판정은 auth가 정합니다. chatbot은 DB에 직접 접속하지 않습니다.

## 실행 전 준비

- 모든 실행은 저장소 맨 위 폴더에서 합니다 (이슈 #133). 다른 폴더는 `from hyundap.<폴더>...`, chatbot 안은 `from .models ...`처럼 가져옵니다.
- 저장소 맨 위 `.env`에 `OPENAI_API_KEY`가 있어야 합니다. 모델은 `hyundap.common.config.get_settings()`가 정합니다 (기본 gpt-5, `.env`의 `OPENAI_CHAT_MODEL`로 변경).
- 코드에는 가짜 함수 자리가 없습니다. search가 구현되기 전에는 질문·요약 모두 RETRIEVAL_FAILED("문서 검색에 실패했습니다")로 끝납니다. 시험할 때는 시험 코드에서 가짜 모듈을 끼웁니다 ([`docs/spec.md` 12절](docs/spec.md)).

## 통합 명세에 없는 추가 항목

통합 기능명세서에 없는데 이 폴더에 더한 것입니다. 자세한 이유와 목록은 [`docs/spec.md` 9절](docs/spec.md)에 있습니다.

| 추가한 것 | 왜 |
|---|---|
| 오류 문구·유보 표현 등 상수 (constants.py) | 오류 코드별 화면 문구, LLM이 "확인할 수 없음"을 바꿔 말해도 알아보려고 |
| Citation의 `version_no`, ChatResponse의 `citations_by_company`·`document_ids` | 출처에 버전 표시, 회사별 출처 묶음, 화면에서 다른 문서 답변이 섞이지 않게 |
| `map_dependency_error`의 단계 값 `'recheck'` | 재검증 중 auth·DB 오류를 DB 계약대로 바꾸려고 |

기능은 통합본 FUN-401~427만 있습니다. 큰 함수는 모함수 이름으로 시작하는 보조 함수로 나눴습니다 (예: `validate_citations` → `validate_citations_line`). 전체 호출 트리는 [`docs/spec.md` 3절](docs/spec.md).

**뺀 것:**

- 통합본이 "개발 중 가짜 자리"로 적어 둔 인자(`retriever`, `checker`, `chain`, `get_chunks`, `llm`)와 임시 객체 대체 코드. 통합본 갱신 때 삭제를 요청합니다.
- 명세 밖 함수·인자: `vector_to_chunk_hit`(요약 근거 변환은 `summarize_document` 안으로), `_to_filters`(dict 필터 변환은 be58de7에서 `ask` 안으로 다시 들어오고, 지금은 보조 함수 `ask_normalize_filters`), 개수 인자(`top_k` 등 — constants 값을 씀), 오류 문구 `SESSION_EXPIRED`(로그인 만료도 "권한 없음"으로 안내).
