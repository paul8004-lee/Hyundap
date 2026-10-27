# 현답 Gradio UI 스펙

| 항목 | 내용 |
|---|---|
| 문서 버전 | 0.4 — 검색창 통합 질문·짧은 답변·기존 스플릿뷰 유지 |
| 작성일 | 2026-10-26 |
| UI 수정일 | 2026-10-27 |
| UI 담당 | 박경환 |
| 대상 | `hyundap/app.py`, `hyundap/ui/` |
| 실행 환경 | Python 3.11 이상, Gradio 6.30.0 |
| 기본 실행 | 백엔드 연결 모드 (`backend`), 로그인만 체험 계정 임시 유지 |
| 실서비스 상태 | 검색·질문·요약·상세·업로드 등록; 실제 인증·다운로드·외부 통합 검수 대기 |
| 디자인 참고 | [현답 Figma](https://www.figma.com/design/XAd6q46W9rUAlnlAmi4JD1/) — 로그인 `9:157`·`6:8417`, 검색 `68:9`·`9:183` |

이 문서는 현재 작성된 UI의 기능과 연결 규격을 설명한다. 팀 전체의 최종 백엔드 명세를 확정하는 문서는 아니다. 화면 개발용 옵션·예시 데이터와 실제 서비스 계약을 구분한다. 최초 연결 검토는 develop `d5913e2`와 검색 PR #143 `276a942`를 함께 둔 분리 snapshot에서 진행했고, 이후 최신 develop `98d428e`를 작업 브랜치에 병합했다. 백엔드는 팀의 변경을 그대로 반영하며 UI 담당이 직접 수정하지 않는다.

## 1. 목적과 구현 범위

사용자가 소속 회사 또는 허용된 회사의 문서를 검색하고, 검색 결과 범위의 짧은 답변과 선택 문서의 정보·발췌문·요약·출처를 확인한다. 관리자는 수동 업로드 폼에서 문서 등록을 요청한다.

| 구분 | 현재 범위 |
|---|---|
| 공통 | 헤더, 역할별 메뉴, 입력·버튼, 로딩·오류·빈 결과 안내, 출처 모달 |
| 인증 | 로그인 입력, 일반/관리자 선택, 실패 안내, 로그아웃, 요청 중 감지한 세션 만료 처리 |
| 검색 | 키워드·회사·문서 유형 필터, 초기화, 결과 목록, 선택 문서 상세 |
| 문서 활용 | 발췌문·원문 위치, 선택 문서 요약과 포함/제외 범위, 다운로드 연결점 |
| 질문 | 검색 입력을 재사용한 단일 질문, 반환된 문서 범위의 짧은 답변, 기존 스플릿뷰의 근거·출처 확인 |
| 관리자 | PDF/DOCX/PPTX 1개 업로드 입력, 형식·크기 검사, 현재 제출 결과 |

### 1.1 예시 모드와 실제 연결 모드

| 구분 | `demo` | `backend` |
|---|---|---|
| 서비스 | `MockServices` | `BackendServices` + `BackendBindings` |
| 로그인 | 화면 확인용 계정·메모리 세션 | 사용자 요청으로 같은 체험 계정 3개만 임시 연결; 실제 인증 제공자 대기 |
| 검색 | 고정 예시 문서 | PR #143 검색 함수 등록; 실제 서버 세션 필요 |
| 답변·요약 | 예시 발췌문·범위 정보 | PR #134 질문·요약 함수 등록; 실제 서버 세션 필요 |
| 업로드 | 입력 검사와 예시 결과 | DB 업로드 대표 함수 등록; 실제 서버 세션 필요 |
| 다운로드 | 예시임을 표시한 `.txt` 파일 | 공개 함수 대기: `DOWNLOAD_NOT_CONNECTED` |
| 미연결·실패 | 예시 규칙에 따른 안내 | 명시적 오류; 예시 모드로 자동 전환하지 않음 |

예시 업로드는 업무 문서 보관·본문 추출·DB 등록·색인을 수행하지 않고 검색 결과에도 추가하지 않는다. 파일 선택 과정에서 Gradio의 임시 파일은 생성될 수 있다. 예시 답변은 실제 AI 생성 결과가 아니다.

backend 모드의 임시 인증은 명시적으로 지정한 설정이며 실제 인증 실패 시 자동으로 예시 계정으로 전환하는 기능이 아니다. `session_id` 또는 `token`이 없는 계정은 `AUTH_BACKEND_PENDING` 안내와 함께 모든 보호된 업무 호출을 차단한다. 기존 체험 계정에는 `session_id`가 없다. 이때 로그인 화면으로 되돌리지 않고 임시 로그인 상태를 유지한다. 전체 예시 검색·답변·업로드 흐름은 `--mode demo`에서 확인한다.

### 1.2 이번 범위에서 제외한 기능

- 계정·권한 관리, 별도 로그·작업·사용 이력 관리, 삭제·재색인, 저장 원본 재시도, 직원 업로드, 대화 이력, 고급 버전 관리, 추가 파일 형식.
- 회원가입·계정 활성화·비밀번호 변경 화면, 원문 PDF 뷰어, 특정 원문 페이지 직접 열기.
- 서버 페이지네이션, 자동 새로고침, 업로드 작업 상태 폴링, 답변 토큰 스트리밍.

위 목록은 현재 화면 구현 범위의 제외 항목이다. 전체 명세의 우선순위를 다시 분류한 목록이 아니며, v3 명세의 계정 관리 P1 항목을 P3로 취급하지 않는다.

## 2. 파일 구조와 역할

아래 경로는 저장소 최상위 기준이다. 기존 백엔드 모듈은 UI 수정 범위에 포함하지 않는다.

```text
hyundap/
├─ app.py                   # 화면 조립, Gradio 이벤트, 출처 모달, 실행
├─ tests/                   # UI 검사 16개 이전 완료 (PR #140)
│  ├─ test_ui_controller.py
│  └─ test_ui_services.py
└─ ui/
   ├─ __init__.py
   ├─ login_page.py         # 로그인 화면
   ├─ search_page.py        # 통합 검색/질문 입력·짧은 답변·목록·상세·요약
   ├─ chat_page.py          # 짧은 답변 발췌·출처 표시 도우미
   ├─ admin_page.py         # 관리자 업로드 폼·현재 결과
   ├─ components.py         # 테마, 헤더, 피드백, 배지, CSS 읽기
   ├─ controller.py         # 이벤트 처리·브라우저별 화면 상태
   ├─ services.py           # UI 서비스 규격과 실제 함수 호출 어댑터
   ├─ backend_bindings.py   # 실제 함수 등록·임시 인증 제공자·서버 세션 확인
   ├─ backend_documents.py  # 접근 권한·활성 버전 확인, 상세·업로드 응답 변환
   ├─ backend_feedback.py   # 검색·챗봇·DB 오류 변환, 세션 만료 원인 보존
   ├─ backend_check.py      # 외부 호출 없는 함수·설치 목록·설정 준비 상태 검사
   ├─ mock_services.py      # 화면 확인용 계정·문서·응답
   ├─ tokens.css            # 공통 디자인 값
   ├─ common.css            # 공통 스타일
   ├─ login.css             # 로그인 카드·탭·입력 스타일
   ├─ assets/
   │  ├─ password-visibility.svg
   │  └─ login-tabs.js
   ├─ README.md             # 실행·연결 안내
   └─ docs/spec.md          # 이 문서
```

호출 흐름은 `Gradio 이벤트 → UIController → UIServices 규격 → 예시 서비스 또는 실제 담당 함수`다. 페이지 모듈은 컴포넌트와 표시용 HTML/Markdown을 만들고, 이벤트 연결은 `app.py`, 처리와 상태 변경은 `controller.py`가 담당한다.

`app.py`는 `hyundap.ui` 경로로 불러오며, UI 내부 모듈은 상대 import를 사용한다. CSS와 아이콘은 `components.UI_DIR`을 기준으로 읽는다. 설치 목록은 저장소 최상위 `requirements.txt` 하나를 사용하며 이번 UI 연결 작업에서 변경하지 않는다.

팀 import 기준은 저장소 루트의 `hyundap` 패키지다. 다른 폴더는 `from hyundap.<폴더>...`, UI 내부는 상대 import를 사용한다. 앱은 저장소 최상위에서 `python -m hyundap.app`으로 실행하며, `app.py`에서 `sys.path`를 변경하지 않는다. 실제 인증·설정·테스트 문서를 사용하는 통합 동작은 함수 등록과 별도로 검증한다.

## 3. 역할과 회사 범위

| 역할 코드 | 로그인 선택 | 검색 회사 | 업로드 메뉴 | 업로드 회사 |
|---|---|---|---|---|
| `employee` | 일반 사용자 | 소속 회사 고정 | 숨김 | 사용 불가 |
| `company_admin` | 관리자 | 소속 회사 고정 | 표시 | 소속 회사 고정 |
| `platform_admin` | 관리자 | 허용 회사 전체 또는 개별 회사 | 표시 | 허용 회사 중 1개 선택 |

로그인 화면의 역할 선택은 접속할 화면 유형이다. 실제 권한은 인증 함수가 반환한 역할을 따른다. 선택한 유형과 계정 역할이 다르면 로그인 진행을 거부하고 안내한다. 현재 세 역할 모두 로그인 성공 후 문서 검색 화면으로 이동한다.

문서 유형은 [공통 문서 유형 PR #126](https://github.com/paul8004-lee/Hyundap/pull/126)의 `DOC_TYPES`를 사용한다. [DB·인증 계약 PR #125](https://github.com/paul8004-lee/Hyundap/pull/125)의 DB CHECK 제약에 맞춘 `manual`(매뉴얼), `policy`(규정), `guide`(가이드), `other`(기타)다. `services.py`에서 `from hyundap.common.schemas import DOC_TYPES as COMMON_DOC_TYPES`로 가져와 `[(label, code) for code, label in COMMON_DOC_TYPES]`로 변환한다. 공통 목록은 `(코드, 표시명)`, Gradio 선택지는 `(표시명, 코드)` 순서다.

`services.company_choices()`는 검증된 세션의 `allowed_company_ids`를 기준으로 선택지를 만든다. `companies`의 `company_id`·`company_name` 및 사용자의 소속 회사 이름으로 표시명을 구성하며, 이름이 없으면 ID를 표시한다. 허용 목록 밖 회사는 추가하지 않는다. 실제 인증 제공자는 DB·auth에서 확인한 회사 목록을 세션에 제공해야 한다. 회사 `hri`·`gte`·`bpa`의 예시 `COMPANIES`는 전체 demo 또는 임시 로그인에서만 사용한다.

예시 문서의 기존 유형은 위 네 값으로 정리했다. 이는 데모 데이터만의 변경이며 실제 백엔드가 반환한 알 수 없는 유형을 임의로 `other`로 바꾸는 정책이 아니다.

메뉴와 입력창 제한은 사용 편의 기능이다. 실제 조회·업로드·다운로드의 권한과 회사 범위는 백엔드에서 다시 검증해야 한다. 예시 서비스는 메모리의 세션 기록을 기준으로 이를 검사한다.

## 4. 화면별 동작

### 4.1 로그인 — `login_page.py`

- 일반 사용자/관리자 전환, 아이디·비밀번호 입력, 비밀번호 표시/숨김, 로그인 버튼을 제공한다.
- 로그인 버튼과 비밀번호 입력 후 Enter가 같은 로그인 동작에 연결된다.
- 아이디의 앞뒤 공백을 제거한다. 비밀번호는 원문을 전달한다.
- 처리 중 로그인 버튼을 잠그고, 실패 시 이유를 화면에 표시한다.
- 성공 시 비밀번호 입력을 비우고 사용자·회사 범위에 맞춰 검색 화면을 준비한다.
- `must_change_password=True`이면 로그인 진행을 막고 변경 절차를 안내한다. 실제 비밀번호 변경 화면은 없다.
- 로그아웃 시 화면의 문서·질문·출처·업로드 상태를 초기화한다. 서버 로그아웃 실패 시에는 서버 세션 종료 확인이 필요하다는 안내를 표시한다.
- 기본 backend 모드에서도 체험 계정 자동 입력 버튼을 제공한다. 임시 계정으로 보호된 업무를 요청하면 실제 서버 세션이 필요하다는 안내를 표시하고 업무 함수는 호출하지 않는다.

### 4.2 검색 시작·결과 — `search_page.py`

- 검색/질문 입력칸, 회사, 문서 유형, 검색·초기화 버튼과 예시 검색어 버튼을 제공한다.
- 검색 버튼 또는 검색어 입력 후 Enter로 조회한다. 빈 검색어는 입력 오류로 처리한다.
- 검색 결과를 먼저 표시하고, 결과가 있으면 같은 입력으로 짧은 AI 답변을 요청한다. 답변은 검색 입력칸 바로 아래 별도 안내 영역에 표시한다.
- 필터를 바꾸면 기존 결과·선택 문서·요약·답변·출처를 초기화한다. 새 조건의 조회는 사용자가 다시 검색할 때 실행한다.
- 초기화하면 검색어는 빈 값, 문서 유형은 모든 유형, 회사는 역할에 따른 기본 범위가 된다.
- 결과 열은 파일명·유형·원문 위치·색인 시각(KST)이다. 데이터는 읽기 전용이며 유형 코드를 변경하지 않는다. 셀은 HTML 이스케이프 후 13px 공통 글꼴로 표시한다. 파일명은 말줄임과 전체 이름 툴팁, 날짜·시간은 두 줄로 표시하고 유형·위치의 임의 줄바꿈을 막는다.
- 첫 결과의 상세 조회를 시도하고, 이후 행을 누르면 선택 문서를 바꾼다. 선택 행을 강조한다.
- 왼쪽 결과 카드의 높이는 내용에 맞추고 오른쪽 상세와 나란히 둔다. 960px 이하에서는 세로 배치하며 표가 좁으면 가로 스크롤을 제공한다.
- 표시 건수는 **반환된 문서 목록의 길이**다. `total_hits`는 청크 수이므로 전체 문서 수로 표시하지 않는다.
- 실제 검색 어댑터는 현재 `limit=10`을 전달한다. 페이지 이동·다음 페이지 요청은 구현하지 않았다.
- 검색 성공 후 상세 조회만 실패하면 목록은 유지하고 상세 오류를 표시한다. 선택 문서가 없으면 요약·출처·다운로드 버튼을 비활성화한다.

### 4.3 문서 상세·요약·다운로드 — `search_page.py`

- 선택 문서의 파일명·회사·유형·버전 번호·색인 시각과 발췌문·원문 위치를 표시한다.
- 위치는 백엔드의 `location_label`을 유지한다. 예: `p. 3`, `슬라이드 5`, `섹션 2`.
- 본문 전체 뷰어는 제공하지 않으며, 현재 상세 영역은 반환된 발췌문을 보여 준다.
- 실제 상세 래퍼는 DB에서 활성 버전을 확인하고 `verify_documents_access`로 읽기 전후의 접근 권한·버전을 검증한다. 벡터 저장소의 청크 식별자·회사·문서·버전·활성 상태를 확인한 뒤 최대 4개의 실제 발췌문(각 최대 1,200자)을 만든다. 제공되지 않는 색인 시각·파일 크기는 임의로 채우지 않는다.
- 요약 버튼을 눌러 선택 문서를 요약한다. 요약 본문과 전체/부분 여부, 사용한 근거 수, 포함·제외 범위를 표시한다.
- `is_full`이 명시적으로 `True`일 때만 전체 범위 요약으로 표시한다.
- 요약 재요청 시 이전 요약·범위를 지우고, 실패 결과에 이전 성공 내용을 남기지 않는다.
- 실제 요약 함수에는 문서 ID를 전달하고, 반환된 `version_id`가 선택 버전과 다르면 변경 오류를 표시한다.
- 다운로드 재요청 시 이전 링크를 지운다. 실제 서버 세션 검사 후에도 다운로드 공개 함수가 없어 현재는 `DOWNLOAD_NOT_CONNECTED`로 안내한다. 원본 경로를 추측하거나 DB 저장 경로를 바로 노출하지 않는다.
- 예시 모드는 버튼과 파일 안내에서 예시 파일임을 명시한다.

### 4.4 검색창 AI 답변·출처 — `search_page.py`, `chat_page.py`

- 맨 아래 별도 AI 질답 영역과 `선택 문서 / 현재 검색 범위` 선택은 표시하지 않는다. 검색 입력칸 하나에서 현재 질문 1개를 처리하며 대화 이력은 제공하지 않는다.
- 입력은 최대 2,000자다. 연결한 챗봇의 `MAX_QUESTION_CHARS=2000`과 같은 제한을 적용한다.
- 검색이 반환한 문서 목록을 먼저 보여 준 뒤 같은 입력 문장으로 `ask`를 호출한다. 회사·문서 유형 조건과 **이번 검색 결과의 `document_ids`**를 전달해 질문 대상을 화면에 나온 문서로 한정한다. 결과가 없으면 `ask`를 호출하지 않는다.
- 새 검색 시 이전 답변·답변 출처를 지운다. 결과 행 선택은 같은 검색의 답변이나 진행 중인 답변 요청을 초기화하지 않는다.
- 답변은 첫 문장을 최대 80자로 발췌해 검색창 바로 아래에 표시한다. 출처 번호·출처 칩과 Markdown 장식을 제거한 텍스트를 HTML 이스케이프한다. 이는 UI 발췌이며 질문에 길이 제한 지시를 덧붙이거나 백엔드 LLM의 생성 방식을 바꾸지 않는다.
- 답변의 출처 정보는 내부에서 보존하고, 기존 오른쪽 스플릿뷰의 근거 문장·출처 확인에서 사용한다. 선택된 문서 ID와 버전이 일치하는 답변 출처만 연결해 다른 문서의 근거가 섞이지 않게 한다.
- `NO_EVIDENCE`와 질문 오류는 별도 안내로 처리하며 검색 결과 목록은 유지한다. 출처를 열 때는 문서를 재조회해 권한·버전을 다시 확인한다.
- 질문 호출에는 `document_ids`를 전달하지만 `version_id`는 전달하지 않는다. 현재 계약은 질문 대상 버전 고정을 보장하지 않으며, 응답 출처의 버전 정보를 보존한다.

### 4.5 출처 모달 — `app.py`

- 문서 상세의 `출처 확인`은 상세를 재조회해 권한·버전을 확인한 뒤 해당 문서·버전의 출처를 모달로 표시한다. 일치하는 답변 출처가 없으면 선택 문서의 기존 출처·발췌 정보를 사용한다.
- 닫기 버튼과 Esc를 지원하며, Tab 이동을 모달 안으로 제한하고 닫힌 뒤 이전 포커스로 돌아간다.
- 삭제·재색인·연결 중단 등의 확인/취소 모달은 이번 범위에 없다.

### 4.6 관리자 수동 업로드 — `admin_page.py`

- 회사·문서 유형·파일 1개를 입력하고 `문서 등록`으로 제출한다.
- 허용 확장자는 `.pdf`, `.docx`, `.pptx`이며 대소문자를 구분하지 않는다.
- 허용 크기는 **1~104,857,600바이트(100MiB)**다. 빈 파일, 접근할 수 없는 파일, 허용 외 확장자, 제한 초과 파일을 안내한다.
- 통합 명세 F61·CLS-040에 따라 **0바이트 파일은 `CORRUPTED_FILE`**로 거부한다. `EMPTY_DOCUMENT`는 파일에서 추출한 글자가 없는 경우(스캔본 등)를 뜻하며 수집 담당 함수가 판단한다.
- 확장자·크기 검사는 빠른 입력 검사다. 실제 파일 형식·손상·암호화·스캔 PDF/내용 추출 가능 여부는 수집 담당 함수가 판단해야 한다.
- 예시 중복 검사는 같은 예시 로그인 세션·회사에 제출한 파일의 SHA-256을 기준으로 한다. 실제 시스템 전체의 중복 검사를 대신하지 않는다.
- 실패 시 회사·유형·파일 입력을 유지한다. 재제출 시 이전 결과 카드를 먼저 비운다.
- 현재 제출의 상태·단계·메시지 등을 표시하며, 별도 전체 작업 목록이나 자동 상태 갱신은 없다.
- `job_status=succeeded`만으로 검색 가능한 문서라고 표시하지 않는다. `searchable` 값을 별도로 사용한다.
- 예시 성공은 `입력 확인 완료`이며 `searchable=False`다.
- 실제 업로드는 `db.upload_service.upload_document(actor=, source_path=, metadata=)` 한 번으로 위임한다. metadata에는 `company_id`, `doc_type`, `data_category="operational"`을 전달한다. 저장·추출·청킹·임베딩·발행 순서는 백엔드가 담당한다.
- 백엔드의 `status=success`는 `succeeded`·`searchable=True`로, `failed`는 실패·`searchable=False`로 변환한다. `recovery_required`와 오류 코드를 유지하고 화면 메시지는 허용된 코드별 안내로 제한한다. 백엔드 예외 원문을 표시하지 않는다.

## 5. 백엔드 연결 규격

### 5.1 연결 방식과 책임

현재 설계는 **Python 함수 직접 호출**이다. UI와 백엔드의 함수 이름이 같을 필요는 없다. `backend_bindings.py`의 `create_bindings()`에 검색·질문·요약·상세·업로드와 오류 변환기를 등록했다. 인자 또는 응답이 다르면 연결용 래퍼와 normalizer를 사용한다. 백엔드 모듈은 필요한 시점에 불러오며 누락된 의존성으로 로그인 화면까지 시작하지 못하는 상황을 피한다.

UI가 의존하는 `UIServices` 메서드는 모두 async 규격이다. 실제 연결 함수는 동기·async 둘 다 가능하다. 동기 함수는 작업 스레드에서, async 함수는 await로 호출한다. 파일 추출·청킹·임베딩·DB 저장의 내부 순서는 UI가 조립하지 않는다.

아래 표는 **현재 어댑터가 호출하는 규격**이며, 팀이 최종 확정한 모든 공개 함수의 목록이라는 의미는 아니다. `검증 사용자`는 SPEC-AUTH-001의 `validate_session` 결과를 `validated_session_to_actor()`로 변환한 dict다. `user_id`, `company_id`, `role`, `session_id`를 포함하고 검증된 `token`을 `_session_token`으로 전달한다. 별도 `_session_token`이나 `auth_version` 입력을 요구하지 않는다. 실제 세션·사용자·권한 버전은 `sessions_repo.current_actor`가 DB에서 재검증한다. UI는 서버 세션을 발급하지 않는다.

| UI 메서드 | 현재 바인딩과 실제 전달 인자 | 반환 규격 |
|---|---|---|
| `login(username, password)` | 지정한 인증 제공자의 `login(username=, password=)`; 기본은 임시 `MockServices` | 세션 dict |
| `logout(session)` | 지정한 인증 제공자의 `logout(session=)` | UI에서 반환값 미사용 |
| `validate_session(session)` | 지정한 인증 제공자의 `validate_session(session=)` | 검증된 사용자/세션 dict |
| `search(session, query, company_ids, doc_types)` | `hyundap.search.search(user=검증 사용자, query=, company_ids=, doc_types=, limit=10)` | 검색 응답 |
| `detail(session, document_id, version_id)` | `backend_documents.get_document(user=검증 사용자, document_id=, version_id=)` | 문서 dict |
| `ask(session, question, company_ids, doc_types, document_ids)` | `hyundap.chatbot.ask.ask(question=, filters=, user=검증 사용자)` | 답변 응답 |
| `summarize(session, document_id, version_id)` | `hyundap.chatbot.summary.summarize_document(document_id=, user=검증 사용자)` | 요약 응답; 결과 버전 비교 |
| `upload(session, file_path, company_id, doc_type)` | `backend_documents.upload_document(user=검증 사용자, file_path=, company_id=, doc_type=)` → DB 업로드 함수 | 현재 업로드 결과 dict |
| `download(session, document_id, version_id)` | `download_original(user=검증 사용자, document_id=, version_id=)` | 현재 `DOWNLOAD_NOT_CONNECTED`; 후속 계약은 준비된 로컬 파일의 `str` 또는 `Path` |

- 전체 필터의 UI 값은 `__all__`이며 호출 시 `None`으로 변환한다. 개별 회사·유형은 길이 1의 목록으로 전달한다.
- 질문 `filters`는 `company_ids`, `doc_types`, `document_ids`를 갖는 dict다. PR #134의 `hyundap.chatbot.ask.ask`는 dict·`ChatFilters`·`None`을 받으므로 `filters_factory` 없이 연결할 수 있다. 명시적 변환이 필요하면 `from hyundap.chatbot.models import ChatFilters`와 `filters_factory=lambda d: ChatFilters(**d)`를 사용할 수 있다.
- 요약 함수는 `hyundap.chatbot.summary.summarize_document(document_id, user)`다. 현재 UI 어댑터의 호출 인자와 일치하며 반환 `version_id`로 선택 문서 버전을 비교한다.
- `normalizers[바인딩 이름]`는 원래 응답을 받아 UI 규격으로 바꾼다. 이후 dict·dataclass·Pydantic `model_dump()`·Enum을 기본 자료형으로 변환한다.
- 오류 dict를 정상 문서·요약 응답으로 전달하지 않는다. 업로드의 계약상 `status=failed`는 현재 제출의 실패 결과로 표시하며, 예외와 구분한다.
- 상세 래퍼는 `db.documents_repo.get_documents_with_active_version` → `auth.permissions.verify_documents_access` → `embedding.vector_store.get_document_chunks` → 접근 재검증을 사용한다. 요청 버전과 활성 버전이 다르면 `DOCUMENT_CHANGED`다. 요약은 반환 버전을 별도로 비교한다.
- 다운로드는 현재 URL·바이너리 자체를 받는 계약이 아니다. 담당자가 권한·활성 버전을 검증하고 로컬 원본을 준비하는 공개 함수를 제공해야 한다. 보관·정리 책임도 함께 확정한다.
- 실제 인증 제공자를 준비하면 `create_bindings(authentication=...)`에 지정한다. 인증·DB 백엔드 파일은 이번 UI 작업에서 수정하지 않는다.

### 5.2 화면이 사용하는 응답 데이터

| 데이터 | 필드와 기준 |
|---|---|
| 로그인 세션 | `user_id`, `display_name`, `role`, `company_id`, `company_name`, `allowed_company_ids`, 비어 있지 않은 `token`을 UI 어댑터가 요구한다. `must_change_password`는 선택 필드다. 실제 업무에는 SPEC-AUTH-001의 `session_id`도 필요하다. `token`은 서버 내부 actor의 `_session_token`으로 전달하며, 인증 결과에 `auth_version`과 별도 `_session_token`을 요구하지 않는다. 세션 비밀값은 화면에 출력하지 않는다. |
| 세션 재검증 | 최소 `user_id`, `role`, `company_id`, `allowed_company_ids`가 필요하다. `token` 생략 시 기존 토큰을 사용한다. 비밀번호 변경 플래그 생략 시 기존 값을 유지하므로 변경 완료 후 명시적 `False`가 필요하다. |
| 회사 선택지 | `allowed_company_ids`와 `companies=[{"company_id": ..., "company_name": ...}]`를 사용한다. 허용 ID를 기준으로 하며 이름 누락 시 ID 표시. 예시 회사 목록은 demo·임시 인증에 한정한다. |
| 검색 응답 | `results` 목록이 필수다. `query`, `total_hits`, `company_ids`, `doc_types`, `elapsed_sec` 등이 함께 올 수 있다. |
| 문서 | 동작에 필요한 `document_id`, 회사 범위 이동을 위한 `company_id`; 버전 확인을 위한 `version_id`. 표시 필드는 `file_name`, `company_name`, `doc_type`, `version_no`, `indexed_at`, `location_label`, `excerpt`, `citations`다. |
| 문서 출처 | `document_id`, `version_id`, `file_name`, `company_name`, `location_label`, `excerpt`; 선택 필드 `number`, `chunk_id`, `company_id`, `doc_type`, `version_no`, `score`. 이동 가능한 출처에는 문서 식별자가 필요하다. |
| 답변 | PR #134의 `ChatResponse`: `status` (`ANSWERED`/`NO_EVIDENCE`), `answer`, `citations`, `citations_by_company`, `document_ids`, `message`, `error_code`. UI가 읽는 필드명과 일치하며 회사별 묶음 등 추가 정보도 변환 과정에서 보존한다. |
| 요약 | `document_id`, `version_id`, `summary`, `is_full`, `included_range`, `excluded_range`, `total_chunks`, `used_chunks`; `citations`가 올 수 있으나 요약 전용 출처 목록 UI는 없다. |
| 업로드 결과 | `status` 또는 `job_status`, `file_name`, `company_name`, `doc_type`, `stage` 또는 `step`, `message`, `error_code`, `searchable`; 선택 필드 `document_id`, `job_id`, `recovery_required`. 예시 결과에는 `demo`, `simulated`를 사용한다. |

위 필드 중 일부는 화면 사용 조건이며 모든 필드를 어댑터가 엄격하게 검사하는 것은 아니다. 실제 연결 시 정상·빈 결과·오류 응답 예시를 함께 받아 누락과 자료형을 검수한다. 파일 크기나 점수처럼 응답에 있어도 현재 상세 UI에 표시하지 않는 필드가 있다.

## 6. 상태·오류 처리

### 6.1 화면 상태

`ViewState`는 사용자, 결과 목록, 선택 문서, 답변 출처, 모달 출처, 회사·유형·검색어와 요청 순서를 저장한다. 브라우저별 Gradio State 키로 구분하며 비밀번호는 보관하지 않는다.

| 동작 | 초기화 또는 유지 규칙 |
|---|---|
| 새 검색·필터 변경·초기화 | 기존 결과·상세·요약·답변·출처·다운로드 표시 초기화, 이전 답변 요청 무효화 |
| 결과 행의 문서 선택 | 이전 문서의 요약·출처 모달·다운로드 초기화, 같은 검색의 짧은 답변·답변 출처·진행 중인 답변 요청 유지 |
| 출처 확인 | 선택 문서·버전 재조회 및 접근 권한 재검증 |
| 메뉴 이동 | 진행 중인 이전 검색 답변 요청 무효화 |
| 요약·다운로드·업로드 재요청 | 이전 성공 결과를 먼저 제거 |
| 로그아웃·세션 만료 | 로그인 화면 복귀, 업무 화면과 관련 결과 표시 초기화 |

검색 답변과 선택 문서 작업의 요청 순서를 구분한다. 결과 행을 선택해도 같은 검색의 답변 요청은 유지하며, 새 검색·필터 변경·초기화·세션 변경·메뉴 이동은 이전 답변 요청을 무효화한다. 각 요청은 자신의 유효한 요청 순서를 확인해 오래된 응답이 현재 화면을 덮지 않게 한다. 이 처리는 백엔드 작업 자체의 취소나 등록 요청의 중복 부작용 방지까지 보장하지 않는다.

Gradio 화면 상태 TTL과 예시 세션 기본 유효시간은 각각 3,600초다. 실제 인증 세션 만료 정책은 인증 담당 함수가 정한다. 현재 UI는 주기적으로 만료를 조회하지 않고, 요청에서 인증 오류를 받았을 때 로그인 화면으로 돌아간다.

### 6.2 오류 규격

화면 오류의 공통 형태는 `UIServiceError(code, message, status)`다. 등록한 `backend_feedback.to_feedback(error)`는 검색 오류 변환기를 먼저 적용하고 챗봇 오류 변환기와 알려진 auth·DB·수집 오류를 화면용 `error_code`, `message`, `status`로 변환한다. 기존 `UIServiceError`는 유지한다. 모르는 예외의 `str(error)`나 내부 경로·접속정보는 출력하지 않는다.

알려진 검색·챗봇·권한 예외의 명시적인 원인 체인을 최대 8단계까지 확인해 감싸진 `SESSION_EXPIRED`도 유지한다. 순환 원인은 중단하며, 이름만 같은 임의 예외나 임의 `code` 속성을 인증 오류로 인정하지 않는다. 업로드 오류 메시지는 `safe_upload_message()`의 코드별 안전 문구를 사용한다.

| 코드·조건 | 처리 |
|---|---|
| `SESSION_EXPIRED`, `UNAUTHENTICATED`, `AUTH_REQUIRED` 또는 `status=session_expired` | 로그인 화면 복귀 및 이전 결과 표시 제거 |
| `INVALID_CREDENTIALS`, `LOGIN_REQUIRED`, `LOGIN_ROLE` | 로그인 실패·입력 안내 |
| `PASSWORD_CHANGE_REQUIRED` | 비밀번호 변경 절차 안내; 변경 화면은 미구현 |
| `AUTH_BACKEND_PENDING` | 실제 서버 세션이 없는 업무 요청 차단; 임시 로그인 유지 |
| `QUERY_REQUIRED`, `QUESTION_REQUIRED`, `SELECTION_REQUIRED`, `SOURCE_REQUIRED`, `INVALID_FILTER` | 입력·문서·출처 선택 또는 필터 확인 요청 |
| `ACCESS_DENIED`, `FORBIDDEN`, `FILTER_CONFLICT` | 권한·회사·문서 범위 오류 안내; 실제 검증은 백엔드 책임 |
| `DOCUMENT_CHANGED`, `DOCUMENT_UNAVAILABLE`, `STATE_CHANGED` | 문서·권한 상태 변경 또는 접근 실패 안내와 재선택 유도 |
| `FILE_REQUIRED`, `FILE_UNAVAILABLE`, `UNSUPPORTED_FORMAT`, `FILE_TOO_LARGE` | 업로드 입력 안내 |
| `CORRUPTED_FILE` | 손상 또는 0바이트 파일. UI는 0바이트를 검사하며 그 외 손상 판정은 수집 담당 함수가 수행 |
| `EMPTY_DOCUMENT` | 추출한 글자가 없는 문서(스캔본 등). 수집 단계에서 판단하며 0바이트 오류와 구분해 안내 |
| `METADATA_REQUIRED`, `DUPLICATE_FILE` | 필수 정보·중복 제출 안내; 실제 중복 판정은 DB 업로드 함수에 위임 |
| `BACKEND_NOT_CONNECTED`, `BACKEND_DEPENDENCY_MISSING`, `DEPENDENCY_MISSING`, `INVALID_RESPONSE`, `INVALID_SESSION` | 함수·의존성 연결 또는 응답 규격 점검 안내 |
| `DOWNLOAD_NOT_CONNECTED` | 실제 원문 다운로드 공개 함수 연결 대기 |
| `DB_UNAVAILABLE`, `RECOVERY_REQUIRED` 또는 `recovery_required=True` | 저장소 장애·복구 필요 안내; 성공이나 검색 가능 상태로 표시하지 않음 |
| `DOWNLOAD_UNAVAILABLE`, `BACKEND_ERROR`, `UI_ERROR` | 해당 기능의 실패 안내 |

`INVALID_SESSION`은 현재 코드에서 자동 로그인 복귀 대상으로 분류하지 않는다. 실제 인증 오류를 연결할 때는 만료·미인증과 응답 형식 오류를 구분해서 매핑해야 한다. 문서 삭제·요약 실패에 대한 별도 자동 복구는 없다.

## 7. 공통 디자인·표시 기준

| 항목 | 현재 값·원칙 |
|---|---|
| 배경/카드 | `#f1f3f6` / `#ffffff` |
| 기본 텍스트/구분선 | `#101820` / `#dfe4ed` |
| 일반 사용자/관리자 헤더 | 네이비 `#032b5c` / 그린 `#1d7c4e` |
| 콘텐츠/카드 | 최대 너비 1,120px, 기본 모서리 16px |
| 로그인 | 최대 420px 흰 카드·36px 패딩·16px 모서리. 일반/관리자 구조 동일, 강조색만 파랑/초록 전환. 헤더 띠·장식·카드 하단 권한 안내 제거. 560px 이하 상단 배치·좌우 16px·카드 패딩 28px 22px |
| 검색 결과 | 왼쪽 목록·오른쪽 상세 유지, 왼쪽 카드는 내용 높이, 표 본문 13px·색인 날짜/시간 두 줄, 960px 이하 세로 배치 |
| 짧은 AI 답변 | 검색창 바로 아래 약 40px 안내 영역, 첫 문장 최대 80자, 답변 내 출처 표시 없음 |
| 문장 표시 | HTML에 넣는 문서·오류·짧은 답변 텍스트는 이스케이프, 요약은 HTML 정화를 사용하는 Markdown |
| 처리 중 안내 | 스피너와 상태 문구, 해당 제출 버튼 잠금, 완료·실패 후 해제 |
| 접근성 | 입력 라벨, 버튼 키보드 사용, 출처 모달 포커스·Esc·Tab, 모션 감소 설정 고려 |

전체 Figma 스크린샷을 배경으로 붙이지 않고 실제 Gradio 입력·목록·버튼으로 구현한다. 스타일 변경 시 `tokens.css`, `common.css`, 각 페이지의 CSS 및 `app.py`의 조립 스타일을 함께 확인한다.

## 8. B01~B32 작업 대응표

표의 **구현**은 현재 UI 범위에서 동작한다는 뜻이다. **함수 등록**은 실제 인증·DB·LLM 통합 검수 완료를 뜻하지 않는다. 기본 backend 모드의 임시 계정은 보호된 업무를 호출할 수 없으며, 예시 흐름은 demo 모드에서 확인한다. 기존 작업명의 API 항목은 현재 구조에서는 Python 함수 연결로 구현한다.

| ID | 작업 | 현재 상태·담당 위치 |
|---|---|---|
| B01 | 디자인·버튼·입력 컨트롤 | 구현 — `components.py`, CSS, 각 페이지 |
| B02 | 전체 레이아웃·사이드바·메뉴 | 공통 헤더·메뉴·화면 전환 구현 — `app.py`; 별도 사이드바 없음 |
| B03 | 공통 모달·확인/취소 | 출처 모달·닫기 구현 — `app.py`; 관리 동작 확인/취소는 제외 |
| B04 | 검색 페이지·필터 | 구현 — `search_page.py`, `controller.py` |
| B05 | 검색 결과·상세 이동 | 구현 — 결과 행 선택·상세 조회; 서버 페이지 이동 없음 |
| B06 | 챗봇 기본 화면·질문 범위 | 검색창으로 통합, 이번 검색 결과 문서 범위 적용 — `search_page.py`, `controller.py` |
| B07 | 질문 입력·전송 상태 | 검색 입력 재사용·결과 우선 표시·답변 진행 처리 — `search_page.py`, `controller.py`, `app.py` |
| B08 | 로그인 입력 | 구현 — `login_page.py` |
| B09 | 문서 상세·본문 | 메타정보·발췌문 구현; 전체 원문 뷰어 제외 |
| B10 | 요약·부분 범위·다운로드 | UI·예시 구현, 실제 요약 함수 등록; 원문 다운로드 공개 함수 대기 |
| B11 | 수동 업로드 폼 | 관리자용 구현 — `admin_page.py` |
| B12 | 파일 선택·형식/크기 검사 | 확장자·크기 검사 구현; 본문 검사는 연결한 DB 업로드·수집 함수에 위임 |
| B13 | 답변·출처 번호 | 짧은 답변에는 번호 미표시, 출처 정보는 기존 스플릿뷰에서 확인 — `chat_page.py`, `controller.py` |
| B14 | 출처 확인·원문 이동 | 출처 모달·문서/버전 재확인 구현; 원문 뷰어 제외 |
| B15 | 동작별 확인 모달 문구·규격 | 출처 닫기만 적용; 삭제·재색인·연결 중단 확인 모달 제외 |
| B16 | 역할별 메뉴 | 구현 — 직원 검색, 관리자 검색·업로드 |
| B17 | 로그인 실패·세션 만료 | UI·예시 검증 구현; 실제 인증 연결 대기 |
| B18 | 검색 실패·빈 결과·갱신 지연 | 오류·빈 결과·요청 중 안내 구현; 주기적 갱신 없음 |
| B19 | 역할·수집 방식별 관리 버튼 | 관리자 업로드 메뉴만 구현; 수집 방식별 관리 동작 제외 |
| B20 | 문서 변경·접근 불가·요약 실패 | 오류 표시·버전 비교·이전 결과 제거 구현; 실제 응답 검수 대기 |
| B21 | 업로드 오류·입력 유지 | 구현 — `controller.py`, `admin_page.py` |
| B22 | 답변 오류·필터 충돌 | 오류 안내·범위 검사 구현; 검색·챗봇 오류 변환기 및 원인 체인의 세션 만료 처리 등록 |
| B23 | 출처 변경·삭제·재질문 | 재조회 오류 안내·수동 재질문 흐름; 자동 재질문·삭제 동작 없음 |
| B24 | 로그인·세션·역할별 이동 연결 | 체험 인증 3계정 임시 유지, 실제 인증 제공자 대기; 서버 세션 없는 업무 차단; 초기 화면은 검색 |
| B25 | 검색·상세·요약·다운로드 연결 | PR #143 검색·상세 래퍼·실제 요약 등록; 다운로드 공개 함수 대기 |
| B26 | 업로드·삭제·재색인 연결 | DB 업로드 대표 함수·응답 변환 등록; 삭제·재색인 제외 |
| B27 | 질문·답변·출처 연결 | PR #134 ask 함수·dict 필터·응답 필드 연결 및 오류 변환 등록 |
| B28 | 출처 열기·버전 확인 연결 | 상세 읽기 전후 접근·활성 버전 재검증 및 요약 반환 버전 비교; 다운로드 대기 |
| B29 | 인증·권한·세션 검수 | 예시·컨트롤러 자동 검사; 실제 인증 통합 검수 대기 |
| B30 | 검색·상세·요약·다운로드 검수 | 예시·컨트롤러 검사; 실제 문서 통합 검수 대기 |
| B31 | 업로드·문서 관리·모달 검수 | 현재 업로드 범위 검사·출처 모달 확인; 제외 동작 검수 없음 |
| B32 | 질문·출처·문서 이동 검수 | 예시 흐름·지연 응답 검사; 실제 답변 품질 검수 대기 |

현재 A 담당의 폴더 연결·처리 목록·분류 검토 화면은 이 UI 납품 범위에 포함하지 않는다. 이전 A/B 전체 목록을 모두 완료한 상태로 해석하지 않는다.

## 9. 검수 기준과 실행

저장소 최상위에서 실행한다.

```powershell
python -m pip install -r requirements.txt
python -m hyundap.app
```

전체 예시 화면 흐름과 외부 호출 없는 준비 상태 검사는 각각 다음 명령을 사용한다.

```powershell
python -m hyundap.app --mode demo
python -m hyundap.app --check-backend
```

`--check-backend`는 공개 함수의 import·callable 여부, 루트 requirements의 설치 버전, 필수 설정 `DATABASE_URL`·`OPENAI_API_KEY`의 존재 여부와 선택 설정의 적용 여부를 확인한다. DB 연결·LLM 호출·벡터 저장소 생성·업로드는 하지 않으며 비밀값을 출력하지 않는다. 인증·다운로드 대기를 포함해 준비되지 않은 항목이 있으면 종료 코드 1을 반환한다. import 성공만으로 실제 통합 성공을 판정하지 않는다.

같은 환경에서 UI 검사를 실행할 수 있다.

```powershell
python -m unittest hyundap.tests.test_ui_controller hyundap.tests.test_ui_services -v
```

리뷰 요청으로 저장소에 추가한 연결·인증 계약·준비 점검 검사까지 함께 실행하려면 다음 명령을 사용한다.

```powershell
python -m unittest discover -s hyundap/tests -t . -p "test_ui*.py" -v
```

체험 로그인은 일반 사용자 `employee`, 관리자 `manager` 또는 `admin`, 공통 비밀번호 `demo1234`다. 기본 backend 모드에서는 임시 로그인만 가능하며 업무 요청은 `AUTH_BACKEND_PENDING`으로 차단한다. 전체 demo 모드에서 `점검`을 검색하면 결과·짧은 AI 답변·오른쪽 문서 상세/출처·요약 흐름을 확인할 수 있다. 상세 실행 방법은 [UI README](../README.md)를 따른다.

최신 develop `98d428e` 병합으로 검색 PR #143, 수집 PR #136, 챗봇 PR #144와 임베딩 PR #151 구현을 가져왔다. UI 담당이 백엔드 파일을 별도로 수정하지는 않았다. 의존성 설치·실제 서버 세션·DB 및 LLM 설정을 갖춘 환경에서 별도의 통합 검수가 필요하다.

| 검수 항목 | 합격 기준 |
|---|---|
| 로그인·역할 | 실패 메시지 표시, 직원 업로드 차단, 관리자 회사 범위 제한 |
| 임시 인증 분리 | backend의 임시 세션으로 보호된 업무가 호출되지 않고 로그인 상태가 유지됨 |
| 검색·상세 | 선택 행과 오른쪽 문서 일치, 빈 결과 안내, 상세만 실패해도 목록 유지 |
| 질문·출처 | 결과 표시 후 검색 결과 문서 ID로 질문, 첫 문장 최대 80자·인라인 출처 없음, 오른쪽 출처의 문서 ID/버전 일치·재검증, 근거 없음 안내 |
| 요약 | 전체/부분과 포함/제외 범위 표시, 버전 변경 감지, 실패 후 이전 요약 제거 |
| 업로드 | 빈 파일·허용 외 형식·100MiB 초과·예시 중복·권한 검사, 입력 유지 |
| 다운로드 | demo의 예시 파일 구분·실패 후 이전 링크 제거; backend의 미연결 안내 및 실제 세션 검사 |
| 상태 격리 | 브라우저별 상태 분리, 같은 검색의 행 선택 후 답변 유지, 새 검색·필터·메뉴 이동 전 지연 답변 무시, 만료 후 결과 표시 제거 |
| 화면·모달 | 스타일·아이콘 정상 로드, 모달 닫기·Esc·포커스 복귀 |

기존 UI 검증 기록은 `hyundap/tests/test_ui_services.py` 6개, `test_ui_controller.py` 10개로 총 16개다. 테스트는 예시 서비스와 가짜 연결 함수 기반이며 실제 DB·인증·LLM·문서 저장 통합 결과를 의미하지 않는다.

검색 연결 검사의 가짜 함수는 keyword-only `user` 인자를 요구한다. 따라서 `actor`로 호출하는 실수가 다시 들어오면 실패한다. UI 검사 16개는 `hyundap/tests/test_ui_controller.py`·`test_ui_services.py`로 이전했다(PR #140).

최초 연결 작업에서는 분리 snapshot의 실제 PR 코드에 대해 연결 검사 37개와 기존 UI 검사 16개, 총 53개가 통과했다. 리뷰 요청에 따라 임시 위치의 연결 검사를 `hyundap/tests/test_ui_backend.py`, `test_ui_backend_documents.py`, `test_ui_backend_feedback.py`로 옮기고, 인증 계약 회귀 검사와 `test_ui_backend_check.py`의 준비 점검 검사도 추가했다. 개인 경로나 `sys.path` 우회 없이 저장소의 unittest discover로 실행한다. 외부 DB·벡터 저장소·LLM·업로드는 대체 객체로 검사하며 실제 데이터 통합 검수와 구분한다. 기본 backend·전체 demo의 Gradio 화면 생성과 브라우저 employee 로그인 → 검색 → 인증 준비 안내·로그인 유지도 확인했다.

최신 develop `98d428e` 병합 후 실제 작업 저장소에서 연결 검사 37개와 UI 검사 16개를 재실행해 모두 통과했다. 공개 함수 14개의 import와 backend·demo 화면 생성도 확인했다. 외부 서비스는 여전히 대체 객체로 검사했으므로 실제 데이터 통합 완료를 의미하지 않는다.

리뷰 반영 후 프로젝트 `.venv`에서 저장소 전체 검사는 241개 실행, 성공(실제 MySQL 필요 35개 건너뜀)이다. 신규 연결·인증 계약·준비 점검 검사는 49개이며 기존 UI 16개를 합쳐 UI 관련 검사는 65개다. 사용자가 설치한 PyMySQL 1.1.2를 프로젝트 가상환경에서 읽었으며 임시 패키지 경로나 `PYTHONPATH` 변경 없이 재검증했다. 공개 함수 14개의 import와 backend·demo 화면 생성도 성공했다. 검증 환경의 SQLAlchemy는 2.1.6으로 루트 요구 버전 2.0.55와 차이가 있다.

## 10. 실제 통합 실행에 남은 사항

1. SPEC-AUTH-001에 따라 구현 중인 `hyundap.auth.session_service`의 `login`·`logout`·`validate_session`을 `create_bindings(authentication=...)`에 연결한다. 검증 결과는 화면용 사용자·역할·허용 회사·회사 표시명과 `session_id`, `token`을 제공한다. UI는 이 `token`을 DB 내부 `_session_token` 필드로 전달하며, 별도 `auth_version`·`_session_token` 반환을 요구하지 않는다. 현재 기본 인증은 체험 계정으로 유지한다.
2. 권한·활성 버전을 재검증하고 로컬 원본 파일을 준비하는 다운로드 공개 함수, 임시 파일 정리 책임. 이 함수가 제공될 때까지 `DOWNLOAD_NOT_CONNECTED`를 유지한다.
3. 루트 requirements 설치, DB·LLM 설정, 사용 가능한 실제 테스트 계정·문서. 검색·ingest 구현은 최신 develop 병합으로 반영했다. 필수 설정값은 화면이나 문서에 넣지 않는다.
4. 실제 인증·허용 회사 범위·문서 변경/삭제·세션 만료·근거 없음·요약 실패·중복 업로드·복구 필요 상황의 통합 검수. 업로드 후 검색 가능 여부까지 실제 환경에서 확인한다.
5. 질문 대상 문서의 버전 고정 정책. 현재 질문은 검색 결과 문서 ID를 전달하고 답변 출처의 버전 정보를 보존하며, 특정 과거 버전을 고정하는 계약은 없다.

검색·질문·요약·상세·업로드 함수 등록과 응답·오류 변환은 이번 UI 연결부에 반영했다. 실제 인증·다운로드까지 준비된 환경의 통합 검수를 마친 뒤 전체 연결 완료로 표시한다. 기존 auth·DB·문서 처리 코드와 루트 설치 목록은 UI 담당 범위에서 변경하지 않는다.

## 11. 리뷰 후속 반영 상태

[DB·인증 계약 PR #125](https://github.com/paul8004-lee/Hyundap/pull/125), [공통 문서 유형 PR #126](https://github.com/paul8004-lee/Hyundap/pull/126)과 UI 리뷰에 대한 현재 처리 범위다.

| 항목 | 상태 |
|---|---|
| 문서 유형 코드 충돌 | UI 선택값·데모 문서 유형을 `manual/policy/guide/other`로 수정 |
| 검색 호출 인자 | `user=`로 수정하고 keyword-only 가짜 함수로 계약 검사 |
| 설치·실행 방식 | UI 전용 의존성 파일과 앱의 uv script 블록 제거, 루트 `requirements.txt` 설치 방식으로 안내 |
| 공통 문서 유형 | `hyundap.common.schemas.DOC_TYPES`를 가져와 Gradio 선택지 순서로 변환; `guide` 표시명은 `가이드` |
| 회사 목록 | 검증 세션의 허용 ID·회사 목록으로 선택지 구성; 예시 `COMPANIES`는 demo·임시 인증에 한정 |
| import 기준 | 저장소 루트의 `hyundap` 패키지 기준; `python -m hyundap.app`으로 실행 통일, 앱의 `sys.path` 우회 제거 |
| 테스트 담당 | PR #140에서 UI 검사 16개를 `hyundap/tests/test_ui_controller.py`·`test_ui_services.py`로 이전 완료; 기존 UI 테스트 원본 제거 |
| 연결 검사·인증 계약 리뷰 | `hyundap/tests/test_ui_backend*.py`로 연결 검사 추가; SPEC-AUTH-001의 `token` → DB `_session_token` 변환, `auth_version` 요구 제거 |
| 실제 함수 등록 | 검색 PR #143·챗봇 PR #134·상세 래퍼·DB 업로드 대표 함수 및 오류 변환기 등록 |
| 인증·다운로드 | 사용자 요청으로 체험 로그인 임시 유지, 서버 세션 없는 업무 요청 차단; 실제 인증·다운로드 공개 함수 대기 |
| PR 본문·화면 | 사용자 검토용 초안·캡처를 준비하며 PR 생성·수정은 사용자가 진행 |

완료로 표시한 항목은 UI 코드와 문서의 수정 상태다. 함수 등록·응답 변환과 실제 DB·인증·검색·LLM 통합 실행은 구분한다. 현재 실제 인증·다운로드와 환경 준비가 남아 있으며, 이 항목의 통합 검수 전에는 전체 백엔드 연결 완료로 표시하지 않는다.

### 로그인 디자인·접근성 보완

- 공통 글꼴은 Pretendard 1.3.9 CDN이며 연결 불가 시 시스템 글꼴로 표시한다. 로그인 스타일은 `login.css`, 공통 글꼴·피드백은 `components.py`/`tokens.css`에서 관리한다.
- `login-tabs.js`는 Gradio HTML의 공개 value/watch API로 기존 로그인 유형 문자열을 전달한다. tablist/tab, aria-selected, 방향키·Home·End, 키보드 포커스와 동작 줄이기를 지원한다.
- 전체 demo 및 backend의 임시 인증에서 employee/manager/admin 버튼을 제공한다. 해당 탭과 아이디·비밀번호만 채우며 인증을 자동 실행하지 않는다. 실제 인증 제공자를 지정한 모드에는 표시하지 않는다.
- INVALID_CREDENTIALS에만 두 입력창과 접근성 오류 상태를 표시하고 비밀번호 아래에 “아이디 또는 비밀번호가 맞지 않아요.”를 표시한다. 입력 누락·권한 유형·세션 만료·서버 오류는 별도 원인 안내를 유지한다.
- 인증 함수 호출·역할 판정·성공 후 화면 이동은 기존 controller/app 흐름을 유지한다.
