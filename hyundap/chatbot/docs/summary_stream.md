# 요약 실시간 표시 연결 안내 (chatbot → ui)

작성: 소용우 · 2026-10-28 · 대상: ui(박경환)

요약을 구간별 직렬 요약으로 바꾸면서 `summarize_document_stream`을 추가했다. 이 문서는 ui가 요약 글을 실시간으로 보여 주려면 무엇을 바꾸면 되는지 정리한다.

## 1. 먼저: ui가 바꾸지 않아도 되는 것

| 항목 | 상태 |
|---|---|
| `summarize_document(document_id, user)` | 이름·인자·반환(SummaryResponse) 그대로. 지금 연결(`backend_bindings.summarize_document`, `BackendServices.summarize`)은 수정 없이 동작한다 |
| 반환 필드 | `document_id`·`version_id`·`summary`·`citations`·`included_range`·`excluded_range`·`is_full`·`total_chunks`·`used_chunks` 그대로. `cited_ratio`(float)가 추가됐지만 읽지 않아도 된다 |
| 오류 코드 | 새 코드 없음. `to_feedback` 매핑 그대로 |
| 기존 ui 테스트 | develop 기준 ui·chatbot 테스트 전부 통과 (`test_ui_backend`의 요약 테스트 포함) |
| `app.py` | 요약 버튼은 이미 `concurrency_limit=None`, 큐는 `default_concurrency_limit=4`라 바꿀 것 없음 |

화면에서 저절로 달라지는 것: 제외 범위 문구에 사유가 붙는다(`예산 초과로 제외 p.231~p.300`, `근거 확인 실패로 제외 p.9~p.16`). 예전 64청크 상한이 없어져 큰 문서도 대부분 "전체 범위 요약"이 된다.

아무것도 안 바꾸면 화면은 지금처럼 로딩 문구를 띄우고 완성된 요약을 한 번에 보여 준다. 실시간 표시가 필요하면 아래 3~5절을 반영한다.

## 2. 이벤트 규약

`hyundap.chatbot.summary.summarize_document_stream(document_id, user)`는 **동기 제너레이터**다. 아래 dict를 차례로 내보내고, 오류는 지금과 같이 예외(`ChatbotError` 등)로 던진다.

| type | 필드 | 언제 | ui가 할 일 |
|---|---|---|---|
| `status` | `stage`(`wait`·`step`·`final`), `message`, `step`(`[현재, 전체]`, wait에는 없음) | 다른 요약이 진행 중이라 자리를 기다릴 때(최대 1초), 구간 요약을 시작할 때 | 로딩 문구를 `message`로 바꾼다 |
| `delta` | `text` | 마지막 단계에서 **인용 검사를 통과한 줄**이 하나 완성될 때마다. 줄 끝 `\n` 포함, 출처 번호는 이미 `[1]` 형식 | 요약 칸 글 뒤에 이어 붙인다 |
| `reset` | `reason` | 인용 검사 실패로 마지막 단계를 다시 만들 때, 입력이 길어 다시 나눌 때, 끝난 뒤 문서 권한 재검증이 실패할 때 | 요약 칸을 비우고 `reason`을 로딩 문구로 보여 준다 |
| `done` | `response`(SummaryResponse 객체) | 마지막. 이후 이벤트 없음 | `to_plain`으로 dict로 바꿔 지금 `do_summarize`처럼 요약·범위·버튼을 표시한다 |

보장하는 것:

- `delta`를 모두 이어 붙인 글은 `done.response.summary`와 같다 (`reset` 이후 것만). 최종 표시는 `response["summary"]`로 덮어쓰면 된다.
- 출처 번호는 스트리밍 중과 `done`의 `citations[].number`가 같다.
- 근거 없는 문장은 `delta`로 나가지 않는다 (줄 단위 검사).
- `reset` 뒤 오류 예외가 오면 이미 보낸 글은 버려야 한다 (권한 재검증 실패 등).

### 순서 예

작은 문서(1구간):

```
status(final, '요약 생성 중', [1, 1]) → delta × n → done
```

큰 문서(3구간):

```
status(step, '구간 1/3 요약 중') → status(step, '구간 2/3 요약 중') → status(final, '구간 3/3 요약 중') → delta × n → done
```

중간 구간은 화면에 글이 없다. 23만 토큰 구간 하나에 1~2분이 걸리므로 그동안 `status` 문구만 바뀐다.

인용 실패 후 재시도:

```
status → delta × 2 → reset('근거 확인에 실패해 다시 생성합니다.') → delta × n → done
```

다른 사용자가 요약 중일 때 (서버 전체 동시 요약 1개, 대기는 1초까지):

```
status(wait, '다른 요약이 끝나기를 기다리는 중입니다.') → 1초 안에 자리가 나면 status(final, …) → …
                                                      → 자리가 안 나면 ChatbotError(LLM_FAILED, retryable=True,
                                                         '다른 요약이 진행 중입니다. 잠시 후 다시 시도해 주세요.')
```

대기를 짧게 둔 이유: ui는 백엔드 함수를 `asyncio.to_thread`로 부르므로, 요약이 오래 기다리면 대기 요청마다 공용 스레드 풀을 하나씩 붙잡아 검색·질문·세션 확인까지 멈춘다. 이 오류는 지금 `to_feedback`으로 다른 오류와 같이 안내 문구가 나가므로 ui가 따로 처리할 것은 없다. `summarize_document`(지금 ui 연결)도 같은 오류를 던진다.

## 3. 반영할 곳 1 — `ui/backend_bindings.py`, `ui/services.py` (BackendBindings)

`BackendBindings`에 필드를 하나 추가하고 바인딩한다.

```python
# services.py — BackendBindings
summarize_document_stream: Callable | None = None

# backend_bindings.py
def summarize_document_stream(**kwargs):
    """선택 문서 요약을 이벤트로 받는 실제 함수 호출."""
    return require_function("hyundap.chatbot.summary", "summarize_document_stream")(**kwargs)

# create_bindings(...) 안
summarize_document_stream=summarize_document_stream,
```

## 4. 반영할 곳 2 — `ui/services.py` (`BackendServices.summarize_stream`)

동기 제너레이터를 작업 스레드에서 한 단계씩 꺼내는 비동기 제너레이터를 추가한다. `call_function`은 함수를 한 번 실행해 반환값 하나만 받으므로 제너레이터에는 쓸 수 없다.

```python
async def summarize_stream(self, session: dict, document_id: str, version_id=None):
    """요약 이벤트를 하나씩 넘긴다. done의 결과는 dict로 바꾸고 버전을 비교한다."""
    function = self.bindings.summarize_document_stream
    if function is None:
        raise UIServiceError("BACKEND_NOT_CONNECTED", "summarize_document_stream 담당 함수가 아직 연결되지 않았습니다.")
    events = function(document_id=document_id, user=await self._user(session))  # 호출만으로는 실행되지 않는다
    finished = object()
    try:
        while True:
            event = await asyncio.to_thread(next, events, finished)
            if event is finished:
                return
            if event["type"] == "done":
                response = self._dict(to_plain(event["response"]), "요약")
                if version_id and response.get("version_id") != version_id:
                    raise UIServiceError("DOCUMENT_CHANGED", "문서 버전이 바뀌었습니다. 문서를 다시 선택한 뒤 요약해 주세요.", "input_error")
                event = {"type": "done", "response": response}
            yield event
    except UIServiceError:
        raise
    except Exception as error:
        raise await self._to_ui_error(error) from error
    finally:
        await asyncio.to_thread(events.close)  # 중간에 멈춰도 chatbot의 동시 요약 자리를 돌려준다
```

`_to_ui_error`는 지금 `_call`의 `except Exception` 블록(to_feedback으로 UIServiceError 만들기, 실패하면 BACKEND_ERROR)을 그대로 떼어 낸 함수다. `_call`도 그 함수를 쓰게 바꾸면 오류 처리가 한 곳에 남는다.

**`finally`의 `events.close()`는 꼭 넣는다.** 사용자가 다른 문서를 고르거나 로그아웃해서 화면이 이벤트를 더 받지 않을 때, 제너레이터를 닫아야 chatbot이 잡고 있던 동시 요약 자리(1개)를 돌려준다. 닫지 않으면 가비지 컬렉션 전까지 다른 사용자의 요약이 `wait`에 머문다.

데모(`mock_services.py`)에도 같은 이름의 메서드를 둔다:

```python
async def summarize_stream(self, session, document_id, version_id=None):
    response = await self.summarize(session, document_id, version_id)
    yield {"type": "status", "stage": "final", "message": "요약 생성 중", "step": [1, 1]}
    for line in response["summary"].splitlines(keepends=True):
        yield {"type": "delta", "text": line}
    yield {"type": "done", "response": response}
```

## 5. 반영할 곳 3 — `ui/controller.py`

`run`은 지금 `result = await getattr(self, f"do_{action}")(...)`로 결과를 한 번 받는다. 요약만 비동기 제너레이터로 받도록 `try` 안에서 갈라 준다. 오류·세션 만료·늦은 응답 처리는 `run`의 기존 `except`가 그대로 맡는다.

```python
# run 안, result = await getattr(...) 바로 앞
if action == "summarize" and hasattr(self.service, "summarize_stream"):
    async for patch in self.do_summarize_stream(ctx, ticket):
        if not self.current(ctx, ticket):
            return
        yield patch
    return
```

```python
async def do_summarize_stream(self, ctx, ticket):
    if not ctx.selected:
        raise UIServiceError("SELECTION_REQUIRED", "먼저 문서를 선택해 주세요.", "input_error")
    doc = deepcopy(ctx.selected)
    target, text = self.feedback_target("summarize"), ""
    async for event in self.service.summarize_stream(ctx.user, doc["document_id"], doc.get("version_id")):
        if not self.current(ctx, ticket):
            raise StaleResponse()          # 다른 문서 선택·로그아웃: 이전 요약 글을 새 화면에 섞지 않는다
        kind = event["type"]
        if kind == "status":
            yield {target: set_loading(event["message"])}
        elif kind == "delta":
            text += event["text"]
            yield {self.search["summary"]: text}
        elif kind == "reset":
            text = ""
            yield {self.search["summary"]: "", target: set_loading(event["reason"])}
        elif kind == "done":
            response = event["response"]
            yield {**self.busy_patch("summarize", False),
                   self.search["summary"]: str(response.get("summary") or "요약 내용을 제공받지 못했습니다."),
                   self.search["coverage"]: render_coverage(response), self.search["detail_feedback"]: "",
                   self.search["summarize"]: gr.update(value="다시 요약", interactive=True)}
```

- `done`의 패치는 지금 `do_summarize`가 돌려주는 것과 같다. 기존 `do_summarize`는 지워도 되고, `summarize_stream`이 없는 서비스용으로 남겨 둬도 된다.
- 요약 칸(`gr.Markdown`)은 줄 단위로 갱신된다. 글자 단위가 아니므로 갱신 횟수는 요약 줄 수(보통 20~60번)다.
- 요약 중 오류가 나면 이미 받은 글이 칸에 남는다. 기존 `test_failed_summary_does_not_keep_old_success`처럼 오류 때 요약 칸을 비우는지 확인한다 (`run`의 오류 분기에서 summarize면 `self.search["summary"]: ""`를 같이 보내면 된다).

## 6. ui 쪽 테스트에 넣을 것

| 시험 | 확인할 것 |
|---|---|
| delta 누적 | delta 3개 → 요약 칸이 1줄, 2줄, 3줄로 늘고 done 뒤 `response["summary"]`로 끝난다 |
| reset | delta 뒤 reset → 칸이 비고 로딩 문구가 `reason`, 이후 delta부터 다시 쌓인다 |
| 늦은 응답 | 요약 도중 다른 문서 선택 → 이전 요약 이벤트가 화면에 안 들어간다, 제너레이터 `close` 호출됨 |
| 오류 | delta 뒤 예외(ChatbotError INVALID_CITATION·STATE_CHANGED) → 요약 칸 비움, 오류 안내 |
| 세션 만료 | 이벤트 도중 SESSION_EXPIRED → 로그인 화면 |
| 버전 바뀜 | done의 `version_id`가 선택 문서와 다르면 DOCUMENT_CHANGED |
| 대기 | status(wait) → 로딩 문구가 "다른 요약이 끝나기를 기다리는 중입니다.", 1초 뒤 오류면 "다른 요약이 진행 중입니다. 잠시 후 다시 시도해 주세요." 안내 |
| 데모 | `MockServices.summarize_stream`으로 같은 흐름 |

chatbot 쪽 동작 시험은 `hyundap/tests/test_chatbot_summary.py`에 있다 (이벤트 순서, 무인용 줄 미노출, reset, 재시도·호출 상한 등).

## 7. 시간 감각 (gpt-5, reasoning_effort=low 기준 시뮬레이션 추정)

| 문서 | 호출 | 첫 줄까지 | 끝까지 |
|---|---|---|---|
| 글 위주 약 150~250쪽 이하 (23만 토큰 이하) | 1 | 수십 초 | 약 1~2분 |
| 약 250~480쪽 | 2 | 약 2분 | 약 3.5~4분 |
| 약 480쪽 이상 | 3 | 약 4분 | 약 5.5분 |

실제 API 측정 전 값이다. ui 쪽에 시간 제한(타임아웃)은 두지 않는다. chatbot이 호출마다 300초 제한을 둔다.
