# 현답 Gradio 화면 실행·연결 안내

Python 3.11 이상, Gradio 6.30.0 기준입니다. 수정 범위는 `hyundap/app.py`와 `hyundap/ui/`입니다.
Figma의 색상·배치·카드를 실제 Gradio 입력창과 버튼으로 재현했습니다. 화면 스크린샷을 앱 배경으로 붙인 구현이 아닙니다.

화면별 동작, 파일 역할, B01~B32 진행 상태와 백엔드 연결 규격은 [UI 스펙 문서](docs/spec.md)를 참고하세요.

## 실행

저장소 최상위 폴더(`C:\workspace\Hyundap`, `hyundap` 폴더와 팀 README가 있는 위치)에서 실행합니다.

```powershell
python -m pip install -r requirements.txt
python -m hyundap.app
```

VS Code에서도 저장소 최상위가 작업 경로인 터미널에서 위 명령으로 실행합니다.

설치 목록은 저장소 최상위의 `requirements.txt`를 사용합니다. 팀 import 규칙에 따라 다른 폴더는 `from hyundap.<폴더>...`, UI 내부는 상대 import를 사용합니다. `app.py`의 `sys.path` 변경 코드를 제거하고 모듈 실행으로 통일했습니다. 실제 백엔드 함수 연결 후 통합 검수는 별도로 진행합니다.

루트에 예전 `app.py`를 새로 만들 필요는 없습니다. 이전 `ui/`에 남은 `__pycache__`는 실행 대상이 아닙니다. CSS·아이콘도 현재 UI 파일을 기준으로 읽으므로 작업 폴더에 따라 경로가 달라지지 않습니다.

브라우저에서 `http://127.0.0.1:7860`을 엽니다. 포트가 사용 중이면 `python -m hyundap.app --port 7861`로 실행합니다.
기본 실행은 **예시 데이터 모드**이며 외부 공유 링크를 만들지 않습니다.

| 로그인 화면 선택 | 아이디 | 역할·범위 |
|---|---|---|
| 일반 사용자 | `employee` | 소속 회사 문서 검색 |
| 관리자 | `manager` | 소속 회사 검색·문서 업로드 |
| 관리자 | `admin` | 허용 회사 전체 검색·문서 업로드 |

공통 예시 비밀번호는 `demo1234`입니다. 로그인 후 `점검`을 검색하면 결과·상세·질문·출처·요약 흐름을 확인할 수 있습니다.

## 구현 범위

- 로그인·실패 안내·역할별 메뉴·로그아웃·세션 만료 처리.
- 검색어·회사/문서 유형 필터·초기화·결과 목록·선택 문서 상세.
- 선택 문서 또는 현재 검색 범위에 대한 질문·답변·번호별 출처·출처 문서 이동.
- 선택 문서 요약·전체/부분 여부·포함/제외 범위 표시.
- 관리자 수동 업로드: PDF/DOCX/PPTX 1개, 1바이트~100MiB(104,857,600바이트), 현재 제출 결과·오류.
- 로딩·중복 요청 방지·빈 결과·권한/오류 안내·문서 변경 시 이전 답변/요약 초기화.

예시 모드는 실제 인증 서버·DB·LLM을 호출하지 않습니다. 업로드는 입력 검사와 예시 결과만 제공하며 **원본 저장·본문 추출·색인을 수행하지 않습니다.** 다운로드는 회사 원본 대신 예시임을 명시한 `.txt` 파일을 제공합니다.

P3인 권한 변경·별도 로그/이력 관리·삭제·재색인·저장 원본 재시도·직원 업로드·대화 이력·고급 버전 관리·추가 파일 형식은 제외했습니다. 기존 Figma의 폴더 연결·자동 스캔·분류 검토 화면도 현재 수동 업로드 범위에 포함하지 않았습니다.

## 파일별 역할

| 파일 | 역할 | 관련 작업 |
|---|---|---|
| `hyundap/app.py` | 화면 조립·출처 모달·이벤트 연결·실행 | B02/B03/B14/B16/B24~B32 |
| `hyundap/ui/controller.py` | 세션별 화면 상태·요청 처리·늦은 응답 방지 | B07/B17~B23/B29~B32 |
| `hyundap/ui/login_page.py` | 로그인 입력·일반/관리자 화면 | B08/B17 |
| `hyundap/ui/search_page.py` | 검색·필터·목록·상세·요약 | B04/B05/B09/B10/B18/B20 |
| `hyundap/ui/chat_page.py` | 질문·답변·출처 표시 | B06/B07/B13/B14/B22/B23 |
| `hyundap/ui/admin_page.py` | 관리자 업로드 폼·현재 결과 | B11/B12/B16/B21 |
| `hyundap/ui/components.py`, `tokens.css`, `common.css`, `assets/` | 공통 스타일·안내·디자인 자산 | B01/B02/B17~B23 |
| `hyundap/ui/services.py` | 화면용 입력/반환 규격·실제 함수 호출 어댑터 | B24~B28 |
| `hyundap/ui/backend_bindings.py` | 담당자가 제공한 실제 함수 등록 | B24~B28 |
| `hyundap/ui/mock_services.py` | 화면 확인용 계정·문서·예시 응답 | B29~B32 검수 지원 |

## 백엔드 함수 연결

현재 UI에 필요한 담당 함수는 아직 등록하지 않았습니다. 최신 저장소의 기존 백엔드 구현은 그대로 보존하며, 합의되지 않은 함수 연결을 임의로 추가하지 않았습니다.
`hyundap/ui/backend_bindings.py`의 `create_bindings()`에서 `BackendBindings`에 실제 담당 함수를 지정합니다. 입력·반환값이 다르면 이 연결부의 래퍼/normalizer에서 맞춥니다. 화면 이벤트 이름과 백엔드 함수 이름이 같을 필요는 없습니다.

```powershell
python -m hyundap.app --mode backend
```

실제 모드는 연결되지 않은 기능에 오류 안내를 표시합니다. 실패를 예시 데이터로 자동 대체하지 않습니다.

챗봇 PR #134의 현재 규격은 다음과 같습니다. 실제 함수 등록은 인증·검색 등 필요한 연결과 함께 진행합니다.

- 질문: `from hyundap.chatbot.ask import ask` — `ask(question, filters, user)`. `filters`는 `dict`, `ChatFilters`, `None`을 받으므로 UI의 dict를 그대로 전달할 수 있으며 `filters_factory`는 선택 사항입니다.
- 결과: `status`, `answer`, `citations`, `citations_by_company`, `document_ids`, `message`, `error_code`를 반환합니다. 현재 UI가 사용하는 필드명과 일치하며 dataclass 응답은 어댑터가 dict로 변환합니다.
- 요약: `from hyundap.chatbot.summary import summarize_document` — `summarize_document(document_id, user)`. 요약 내용·문서 버전·포함/제외 범위의 필드명도 UI와 일치합니다.
- 오류: `from hyundap.chatbot.models import to_feedback`. 실제 함수 연결 시 오류 변환기도 함께 등록합니다.

| 필요한 연결 | 담당자에게 받을 내용 |
|---|---|
| 로그인·세션 | 전재경: 함수 파일/import 경로, 사용자·역할·허용 회사·세션·만료·비밀번호 변경 필요 여부 |
| 검색 | 이준영: `search()` 인자와 결과/출처 자료형, 필터 코드, 검색 개수 기준 |
| 질문·요약 | 소용우: `ask()`, `summarize_document()`, `to_feedback()`, 사용자/필터 타입과 출처 번호·문서 버전 |
| 상세·다운로드 | 전재경: 권한과 문서/버전을 재검증하는 함수, 상세 응답, 다운로드 파일 반환 방식 |
| 업로드 | 김혜리·전재경: 업로드 전체를 접수하는 대표 함수, 임시 파일 보관 책임, 작업 상태·실패 규격 |
| 실행 환경 | 이성민: 실제 실행 코드/브랜치, 공통 타입, 설치 목록·설정·테스트 계정/문서 |

공통 요청: **실행 가능한 파일과 의존 모듈, 함수 이름, 입력값/자료형, 반환값/자료형, 정상·빈 결과·실패 예시, 오류 처리, 동기/async 여부**입니다. 화면은 추출·청킹·임베딩·저장 함수를 직접 조립하지 않습니다.
`hyundap/ui/services.py`는 동기 함수를 작업 스레드에서 호출하고 async 함수를 기다립니다. 인증·권한 판정은 실제 백엔드가 수행해야 합니다. 메뉴 숨김만으로 권한을 보장하지 않습니다.
검색의 `total_hits`는 문서 전체 개수가 아닌 청크 수이므로 현재 받은 문서 목록 길이를 표시합니다. 작업 성공과 검색 가능 여부도 구분합니다.

검색 어댑터는 `search(user=검증 사용자, query=, company_ids=, doc_types=, limit=10)`으로 호출합니다. 문서 유형 선택값은 DB CHECK 제약에 맞춘 `manual`(매뉴얼), `policy`(규정), `guide`(가이드), `other`(기타)입니다. 예시 문서의 유형도 이 네 값으로 정리했으며, 실제 백엔드의 알 수 없는 유형을 임의로 `other`로 바꾸지는 않습니다.

문서 유형은 [공통 문서 유형 PR #126](https://github.com/paul8004-lee/Hyundap/pull/126)의 목록을 사용합니다. `services.py`에서 `from hyundap.common.schemas import DOC_TYPES as COMMON_DOC_TYPES`로 가져와 `[(label, code) for code, label in COMMON_DOC_TYPES]`로 변환합니다. 공통 목록의 `(코드, 표시명)`을 Gradio가 받는 `(표시명, 코드)` 순서로 맞추며, `guide`의 표시명은 공통 목록에 따라 `가이드`로 표시합니다.

회사 목록 `hri`·`gte`·`bpa`는 여전히 화면 개발용 예시입니다. 공통 설계상 실제 회사 목록은 `common`의 정적 목록이 아니라 DB `companies` 테이블과 auth 권한 함수가 제공합니다. 조회 함수가 제공되기 전까지 예시 `COMPANIES`를 유지합니다.

## 검증 기록

- 서비스 검사 6건, 화면 컨트롤러 검사 10건 통과.
- 재실행: 저장소 맨 위에서 `python -m unittest hyundap.tests.test_ui_controller hyundap.tests.test_ui_services -v` (Gradio 설치 환경).
- Gradio 6.30.0 전체 화면 구성 검사: 예시·실제 연결 모드의 컴포넌트 생성 확인.
- 브라우저에서 로그인→검색→부분 요약→질문/출처→해당 문서 이동·출처 모달/Esc·예시 파일 준비 확인.
- 관리자 업로드 폼·입력 누락 안내·회사 변경 후 검색 범위 적용 확인.
- 실제 백엔드·DB·LLM 통합 검수는 담당 함수 연결 후 별도로 수행해야 합니다.

UI 검사 16개는 `hyundap/tests/test_ui_controller.py`·`test_ui_services.py`로 이전했습니다(#127). 검색 연결 검사의 가짜 함수는 keyword-only `user` 인자를 요구해 예전 `actor` 호출이 다시 들어오면 실패하도록 했습니다.

## 리뷰 후속 반영

[DB·인증 계약 PR #125](https://github.com/paul8004-lee/Hyundap/pull/125), [공통 문서 유형 PR #126](https://github.com/paul8004-lee/Hyundap/pull/126)과 UI 리뷰를 기준으로 반영한 범위입니다.

- [x] 문서 유형을 DB 허용 코드 네 개로 맞추고 예시 문서 유형을 정리했습니다.
- [x] 검색 호출 인자를 `user=`로 맞추고 연결 검사를 갱신했습니다.
- [x] UI 전용 의존성 파일·앱의 uv script 블록을 제거하고 루트 설치 명령으로 통일했습니다.
- [x] 문서 유형은 `hyundap.common.schemas.DOC_TYPES`에서 가져와 Gradio 선택지 순서로 변환합니다.
- [ ] DB·auth 회사 목록 조회 함수가 제공되면 예시 `COMPANIES`를 교체합니다.
- [x] 팀 import 규칙에 맞춰 저장소 최상위의 `python -m hyundap.app` 실행으로 통일하고 앱의 경로 우회를 제거했습니다.
- [ ] 실제 백엔드 함수 등록 후 UI와 백엔드의 통합 동작을 검수합니다.
- [x] PR #140에서 UI 검사 16개를 `hyundap/tests/test_ui_controller.py`·`test_ui_services.py`로 이전하고 기존 UI 테스트 원본을 제거했습니다.

공통 문서 유형 연결은 실제 DB·인증·검색·LLM 연결 완료를 뜻하지 않습니다. 회사 목록 조회와 실제 담당 함수 연결은 계속 대기 상태입니다. PR 본문과 화면 캡처는 사용자 검토용으로 준비하며 PR 생성·수정은 사용자가 진행합니다.
