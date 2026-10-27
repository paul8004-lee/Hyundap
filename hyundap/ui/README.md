# 현답 Gradio 화면 실행·연결 안내

Python 3.11 이상, Gradio 6.30.0 기준입니다. UI 구현 범위는 `hyundap/app.py`와 `hyundap/ui/`이며, 자동 검사는 팀 테스트 위치인 `hyundap/tests/`에서 관리합니다.
Figma의 색상·배치·카드를 실제 Gradio 입력창과 버튼으로 재현했습니다. 화면 스크린샷을 앱 배경으로 붙인 구현이 아닙니다.

화면별 동작, 파일 역할, B01~B32 진행 상태와 백엔드 연결 규격은 [UI 스펙 문서](docs/spec.md)를 참고하세요.

## 실행

저장소 최상위 폴더(`C:\workspace\Hyundap`, `hyundap` 폴더와 팀 README가 있는 위치)에서 실행합니다.

```powershell
python -m pip install -r requirements.txt
python -m hyundap.app
```

VS Code에서도 저장소 최상위가 작업 경로인 터미널에서 위 명령으로 실행합니다.

설치 목록은 저장소 최상위의 `requirements.txt`를 사용하며 이번 UI 연결 작업에서 변경하지 않았습니다. 팀 import 규칙에 따라 다른 폴더는 `from hyundap.<폴더>...`, UI 내부는 상대 import를 사용합니다. `app.py`의 `sys.path` 변경 코드를 제거하고 모듈 실행으로 통일했습니다.

루트에 예전 `app.py`를 새로 만들 필요는 없습니다. 이전 `ui/`에 남은 `__pycache__`는 실행 대상이 아닙니다. CSS·아이콘도 현재 UI 파일을 기준으로 읽으므로 작업 폴더에 따라 경로가 달라지지 않습니다.

브라우저에서 `http://127.0.0.1:7860`을 엽니다. 포트가 사용 중이면 `python -m hyundap.app --port 7861`로 실행합니다.
기본 실행은 **백엔드 연결 모드(`backend`)**이며 외부 공유 링크를 만들지 않습니다. 사용자 요청에 따라 로그인만 기존 체험 계정 3개를 임시로 유지합니다. 실제 서버 세션이 없는 체험 계정으로 검색·질문·요약·상세·업로드·다운로드를 요청하면 `AUTH_BACKEND_PENDING`을 안내하고 요청을 차단합니다. 임시 로그인은 유지되며 실제 업무 데이터로 바뀌지 않습니다.

| 로그인 화면 선택 | 아이디 | 역할·범위 |
|---|---|---|
| 일반 사용자 | `employee` | 소속 회사 문서 검색 |
| 관리자 | `manager` | 소속 회사 검색·문서 업로드 |
| 관리자 | `admin` | 허용 회사 전체 검색·문서 업로드 |

공통 예시 비밀번호는 `demo1234`입니다. 위 역할·회사 범위는 임시 화면 확인용이며 실제 서버 권한이 아닙니다. 전체 화면 흐름을 예시 데이터로 확인하려면 다음 명령을 사용합니다. 이 모드에서 로그인 후 `점검`을 검색하면 결과·상세·질문·출처·요약 흐름을 확인할 수 있습니다.

```powershell
python -m hyundap.app --mode demo
```

함수·설치 패키지·필수 설정의 준비 상태만 확인하려면 다음 명령을 사용합니다. DB 연결·LLM 호출·업로드 없이 점검하며, 설정값이나 토큰은 출력하지 않습니다. 준비 항목이 남아 있으면 종료 코드 1을 반환합니다.

```powershell
python -m hyundap.app --check-backend
```

## 구현 범위

- 로그인·실패 안내·역할별 메뉴·로그아웃·세션 만료 처리.
- 검색어·회사/문서 유형 필터·초기화·결과 목록·선택 문서 상세.
- 선택 문서 또는 현재 검색 범위에 대한 질문·답변·번호별 출처·출처 문서 이동.
- 선택 문서 요약·전체/부분 여부·포함/제외 범위 표시.
- 관리자 수동 업로드: PDF/DOCX/PPTX 1개, 1바이트~100MiB(104,857,600바이트), 현재 제출 결과·오류.
- 로딩·중복 요청 방지·빈 결과·권한/오류 안내·문서 변경 시 이전 답변/요약 초기화.

예시 모드는 실제 인증 서버·DB·LLM을 호출하지 않습니다. 업로드는 입력 검사와 예시 결과만 제공하며 **원본 저장·본문 추출·색인을 수행하지 않습니다.** 다운로드는 회사 원본 대신 예시임을 명시한 `.txt` 파일을 제공합니다.

계정·권한 관리, 별도 로그/이력 관리, 삭제·재색인, 저장 원본 재시도, 직원 업로드, 대화 이력, 고급 버전 관리, 추가 파일 형식은 현재 화면 구현 범위에서 제외했습니다. 이는 전체 명세의 우선순위를 다시 분류한다는 뜻이 아니며, 계정 관리를 일괄 P3로 취급하지 않습니다. 기존 Figma의 폴더 연결·자동 스캔·분류 검토 화면도 현재 수동 업로드 범위에 포함하지 않았습니다.

## 파일별 역할

| 파일 | 역할 | 관련 작업 |
|---|---|---|
| `hyundap/app.py` | 화면 조립·출처 모달·이벤트 연결·실행 | B02/B03/B14/B16/B24~B32 |
| `hyundap/ui/controller.py` | 세션별 화면 상태·요청 처리·늦은 응답 방지 | B07/B17~B23/B29~B32 |
| `hyundap/ui/login_page.py` | 로그인 입력·일반/관리자 화면 | B08/B17 |
| `hyundap/ui/search_page.py` | 검색·필터·목록·상세·요약 | B04/B05/B09/B10/B18/B20 |
| `hyundap/ui/chat_page.py` | 질문·답변·출처 표시 | B06/B07/B13/B14/B22/B23 |
| `hyundap/ui/admin_page.py` | 관리자 업로드 폼·현재 결과 | B11/B12/B16/B21 |
| `hyundap/ui/components.py`, `tokens.css`, `common.css`, `login.css`, `assets/` | 공통 스타일·안내·디자인 자산 | B01/B02/B17~B23 |
| `hyundap/ui/services.py` | 화면용 입력/반환 규격·실제 함수 호출 어댑터 | B24~B28 |
| `hyundap/ui/backend_bindings.py` | 실제 함수 등록·임시 인증 제공자·서버 세션 확인 | B24~B28 |
| `hyundap/ui/backend_documents.py` | 권한·활성 버전을 재확인하는 상세 조회, DB 업로드 함수의 응답 변환 | B25/B26/B28 |
| `hyundap/ui/backend_feedback.py` | 검색·챗봇·DB 오류를 안전한 화면 안내로 변환, 세션 만료 원인 보존 | B17~B23/B24~B28 |
| `hyundap/ui/backend_check.py` | 외부 호출 없이 함수·설치 목록·설정 준비 상태 점검 | B29~B32 검수 지원 |
| `hyundap/ui/mock_services.py` | 화면 확인용 계정·문서·예시 응답 | B29~B32 검수 지원 |

## 백엔드 함수 연결

`hyundap/ui/backend_bindings.py`의 `create_bindings()`에 아래 실제 함수를 등록했습니다. 입력·반환값 차이는 UI 연결부에서 맞춥니다. 화면 이벤트 이름과 백엔드 함수 이름이 같을 필요는 없습니다. backend 모드에서 업무 호출 실패를 예시 데이터로 자동 대체하지 않습니다.

| 기능 | 현재 등록된 연결 |
|---|---|
| 로그인·세션 | 사용자 요청으로 `MockServices`의 로그인·검증·로그아웃만 임시 지정. 자동 fallback이 아님 |
| 검색 | PR #143의 `hyundap.search.search(user=, query=, company_ids=, doc_types=, limit=10)` |
| 질문 | `hyundap.chatbot.ask.ask(question=, filters=, user=)`; dict 필터 전달, `filters_factory` 불필요 |
| 요약 | `hyundap.chatbot.summary.summarize_document(document_id=, user=)`; 반환 버전 비교 |
| 문서 상세 | `backend_documents.get_document()`가 DB 활성 버전·auth 접근 권한·벡터 저장소의 실제 발췌문을 조회 |
| 수동 업로드 | `backend_documents.upload_document()` → `hyundap.db.upload_service.upload_document(actor=, source_path=, metadata=)` |
| 원문 다운로드 | 공개 함수 대기. 실제 세션 검사를 통과해도 `DOWNLOAD_NOT_CONNECTED` 안내 |
| 오류 | `backend_feedback.to_feedback()`가 검색·챗봇의 변환기를 연결하고 DB/권한 오류와 감싸진 세션 만료를 처리 |

질문의 결과 필드 `status`, `answer`, `citations`, `citations_by_company`, `document_ids`, `message`, `error_code`와 요약 필드는 UI와 일치합니다. dataclass 응답은 어댑터가 dict로 변환합니다. 상세는 본문 전체가 아니라 실제 청크에서 가져온 발췌문을 표시하며, 읽기 전후의 접근 권한과 활성 버전을 확인합니다. 업로드의 저장·추출·청킹·임베딩·발행 순서는 기존 DB 업로드 함수가 담당합니다.

실제 인증 제공자는 `create_bindings(authentication=...)`에 전달하며 UI 규격의 `login`·`logout`·`validate_session`을 제공해야 합니다. SPEC-AUTH-001의 반환 필드는 `user_id`, `display_name`, `role`, `company_id`, `company_name`, `allowed_company_ids`, `token`, `session_id`입니다. 변환 함수는 검증된 `token`을 DB 내부 actor의 `_session_token`으로 전달하고, 사용자 ID·회사 ID·역할·세션 ID만 함께 넘깁니다. 인증 결과에 `auth_version`이나 별도 `_session_token`을 요구하지 않으며 권한 버전과 실제 세션 유효성은 DB가 다시 확인합니다. `session_id` 또는 `token`이 없으면 `AUTH_BACKEND_PENDING`으로 업무 호출을 막고 임시 로그인은 유지합니다. 기존 체험 계정에는 `session_id`가 없으므로 이 제한이 그대로 적용됩니다.

인증 구현이 준비되면 `hyundap.auth.session_service`의 `login`·`logout`·`validate_session`을 인증 제공자로 연결할 예정입니다. 현재 기본 실행은 임시 로그인 + backend 모드이며, 예시 데이터로 검색·질문까지 확인하려면 `python -m hyundap.app --mode demo`를 사용합니다.

`hyundap/ui/services.py`는 동기 함수를 작업 스레드에서 호출하고 async 함수를 기다립니다. 실제 권한 판정은 백엔드가 수행하며 메뉴 숨김만으로 권한을 보장하지 않습니다. 아직 필요한 항목은 실제 인증 제공자, 권한을 검증하는 다운로드 공개 함수, 실행 환경·테스트 계정/문서와 실제 통합 검수입니다.
검색의 `total_hits`는 문서 전체 개수가 아닌 청크 수이므로 현재 받은 문서 목록 길이를 표시합니다. 작업 성공과 검색 가능 여부도 구분합니다.

검색 어댑터는 `search(user=검증 사용자, query=, company_ids=, doc_types=, limit=10)`으로 호출합니다. 문서 유형 선택값은 DB CHECK 제약에 맞춘 `manual`(매뉴얼), `policy`(규정), `guide`(가이드), `other`(기타)입니다. 예시 문서의 유형도 이 네 값으로 정리했으며, 실제 백엔드의 알 수 없는 유형을 임의로 `other`로 바꾸지는 않습니다.

문서 유형은 [공통 문서 유형 PR #126](https://github.com/paul8004-lee/Hyundap/pull/126)의 목록을 사용합니다. `services.py`에서 `from hyundap.common.schemas import DOC_TYPES as COMMON_DOC_TYPES`로 가져와 `[(label, code) for code, label in COMMON_DOC_TYPES]`로 변환합니다. 공통 목록의 `(코드, 표시명)`을 Gradio가 받는 `(표시명, 코드)` 순서로 맞추며, `guide`의 표시명은 공통 목록에 따라 `가이드`로 표시합니다.

`services.company_choices()`는 검증된 세션의 `allowed_company_ids`와 `companies` 목록으로 회사 선택지를 만듭니다. 표시명은 `company_name`을 사용하며 이름이 없으면 회사 ID를 표시합니다. 허용 범위 밖 회사는 선택지에 추가하지 않습니다. `hri`·`gte`·`bpa` 예시 목록은 전체 demo 또는 임시 로그인에서만 사용합니다. 실제 인증 연결 시 DB·auth에서 확인한 회사 목록을 반환해야 합니다.

최초 연결 검사는 develop `d5913e2`와 검색 PR #143 `276a942`를 함께 둔 분리 snapshot에서 진행했습니다. 이후 최신 develop `98d428e`를 작업 브랜치에 병합해 검색 PR #143, 수집 PR #136, 챗봇 PR #144와 임베딩 PR #151 변경을 반영했습니다. 백엔드 코드는 팀의 병합 내용을 그대로 가져왔으며 UI 담당이 직접 수정하지 않았습니다. 실제 실행 준비 상태는 `--check-backend`로 확인합니다. 필수 설정 키는 `DATABASE_URL`, `OPENAI_API_KEY`이며 실제 값은 문서나 화면에 표시하지 않습니다.

## 이번 연결 검증

- 리뷰 반영 후 프로젝트 `.venv`에서 저장소 전체 검사: 241개 실행, 성공(실제 MySQL이 필요한 35개 건너뜀). 신규 연결·인증·준비 점검 검사는 49개이며 기존 UI 16개를 합쳐 UI 관련 검사는 65개입니다. 사용자가 설치한 PyMySQL 1.1.2를 프로젝트 가상환경에서 읽었으며 임시 패키지 경로나 `PYTHONPATH` 변경 없이 재검증했습니다.
- 분리 작업 폴더에서 실제 PR 코드에 대한 연결 검사 37개와 기존 UI 검사 16개, 총 53개가 통과했습니다. DB·벡터 저장소·LLM·업로드의 외부 경계는 테스트 대체 객체로 검사했습니다.
- 최신 develop `98d428e` 병합 후 실제 작업 저장소에서도 같은 연결 검사 37개와 UI 검사 16개가 모두 통과했습니다. 공개 함수 14개의 import와 backend·demo 화면 생성을 확인했습니다.
- 실제 저장소에서 기본 backend 앱을 실행하고 브라우저의 employee 로그인 → 검색 → 인증 준비 안내 및 로그인 상태 유지를 확인했습니다.
- 기본 backend와 전체 demo 모두 Gradio 컴포넌트 생성이 성공했습니다. 최초 연결 검사 37개는 리뷰 요청에 따라 `hyundap/tests/test_ui_backend.py`, `test_ui_backend_documents.py`, `test_ui_backend_feedback.py`로 옮겼습니다. 인증 계약 회귀 검사와 `test_ui_backend_check.py`의 연결 준비 점검 검사도 추가했습니다.
- 검색·수집 구현은 최신 develop 병합으로 반영했습니다. PyMySQL 1.1.2 설치와 공개 함수 14개의 import, backend·demo 화면 생성을 확인했습니다. 로컬 검증 환경의 SQLAlchemy는 2.1.6으로 루트 요구 버전 2.0.55와 다르며 DB·OpenAI 설정도 필요합니다. 실제 서버 세션·원문 다운로드 함수도 제공 대기이므로 실제 데이터 통합 검수는 남아 있습니다.

## 기존 UI 검증 기록

UI와 연결 검사를 모두 실행하려면 저장소 맨 위에서 아래 명령을 사용합니다. 테스트 파일에는 개인 경로나 `sys.path` 변경이 없으며 실제 DB·LLM 호출은 대체합니다.

```powershell
python -m unittest discover -s hyundap/tests -t . -p "test_ui*.py" -v
```

- 서비스 검사 6건, 화면 컨트롤러 검사 10건 통과.
- 재실행: 저장소 맨 위에서 `python -m unittest hyundap.tests.test_ui_controller hyundap.tests.test_ui_services -v` (Gradio 설치 환경).
- Gradio 6.30.0 전체 화면 구성 검사: 예시·실제 연결 모드의 컴포넌트 생성 확인.
- 브라우저에서 로그인→검색→부분 요약→질문/출처→해당 문서 이동·출처 모달/Esc·예시 파일 준비 확인.
- 관리자 업로드 폼·입력 누락 안내·회사 변경 후 검색 범위 적용 확인.
- 위 기록은 기존 예시 서비스와 가짜 연결 함수 기반의 UI 검사입니다. 실제 인증 제공자·DB·LLM·원본 파일을 사용하는 통합 검수 완료를 뜻하지 않습니다.

UI 검사 16개는 `hyundap/tests/test_ui_controller.py`·`test_ui_services.py`로 이전했습니다(PR #140). 검색 연결 검사의 가짜 함수는 keyword-only `user` 인자를 요구해 예전 `actor` 호출이 다시 들어오면 실패하도록 했습니다.

## 리뷰 후속 반영

[DB·인증 계약 PR #125](https://github.com/paul8004-lee/Hyundap/pull/125), [공통 문서 유형 PR #126](https://github.com/paul8004-lee/Hyundap/pull/126)과 UI 리뷰를 기준으로 반영한 범위입니다.

- [x] 문서 유형을 DB 허용 코드 네 개로 맞추고 예시 문서 유형을 정리했습니다.
- [x] 검색 호출 인자를 `user=`로 맞추고 연결 검사를 갱신했습니다.
- [x] UI 전용 의존성 파일·앱의 uv script 블록을 제거하고 루트 설치 명령으로 통일했습니다.
- [x] 문서 유형은 `hyundap.common.schemas.DOC_TYPES`에서 가져와 Gradio 선택지 순서로 변환합니다.
- [x] 실제 세션의 허용 회사 ID·회사 목록으로 선택지를 구성하고, 예시 회사는 demo·임시 로그인에 한정합니다.
- [x] 팀 import 규칙에 맞춰 저장소 최상위의 `python -m hyundap.app` 실행으로 통일하고 앱의 경로 우회를 제거했습니다.
- [x] 검색·질문·요약·상세·업로드 함수와 오류 변환기를 UI 연결부에 등록했습니다.
- [x] 연결 검사를 `hyundap/tests/`에 추가하고, SPEC-AUTH-001의 `token`을 DB의 `_session_token`으로 전달하도록 맞췄습니다.
- [ ] 실제 인증·다운로드 공개 함수를 연결하고 설정·테스트 문서를 갖춘 환경에서 통합 동작을 검수합니다.
- [x] PR #140에서 UI 검사 16개를 `hyundap/tests/test_ui_controller.py`·`test_ui_services.py`로 이전하고 기존 UI 테스트 원본을 제거했습니다.

함수 등록과 응답 변환 완료는 실제 DB·인증·검색·LLM 통합 실행 완료를 뜻하지 않습니다. 현재 임시 로그인은 유지하며 실제 인증·다운로드 연결과 환경 준비가 남아 있습니다. PR 본문과 화면 캡처는 사용자 검토용으로 준비하며 PR 생성·수정은 사용자가 진행합니다.
