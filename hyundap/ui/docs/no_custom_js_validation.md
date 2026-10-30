# 자체 JavaScript 제거 검수 — 2026-10-30

## 변경 범위

- `hyundap/app.py`: 모달용 사용자 스크립트와 `launch(js=...)` 제거. 다른 담당자의 QA 실행기가 import하는 `APP_JS`는 `None`으로 유지.
- `hyundap/ui/login_page.py`: 기본 Radio로 역할 선택, Python 콜백으로 체험 계정 입력·비밀번호 보기·오류 안내 초기화.
- `hyundap/ui/login.css`: Radio의 checked 상태를 이용해 탭 모양·이동 효과·강조색·제목을 표시. 기본 라디오 키보드 동작을 보존.
- `hyundap/ui/controller.py`: 로그인 성공·로그아웃·세션 만료 시 비밀번호 값과 보기 상태를 함께 초기화.
- `assets/login-tabs.js`, `assets/login-feedback.js` 삭제. README와 명세를 갱신.

Gradio 자체 브라우저 런타임은 그대로 사용한다. 특히 설치된 Gradio HTML 컴포넌트는 기본 `js_on_load`에 클릭 이벤트 전달 코드를 포함한다. 이는 라이브러리 기본값이며 앱에서 작성한 스크립트가 아니다. 모든 HTML 컴포넌트가 이 기본값만 사용하는지 검사했다. 사용자 작성 JavaScript를 Python 문자열·HTML 이벤트 속성으로 옮겨 남기지 않는다.

## 자동 검사

프로젝트 `.venv/Scripts/python.exe`로 실행했다. DB 전용 테스트 환경변수 5개를 빈 값으로 지정했으며 실제 DB·유료 AI를 호출하지 않았다.

```text
Run: 109; failures: 0; errors: 0; skipped: 0
```

기존 `test_ui*.py` 검사, 신규 자체 JS 제거 회귀 검사 10개, 기존 Gradio 콜백 처리 검사 2개를 포함한다. 직접 작성한 JS 자산·스크립트 훅이 없는지, 컴포넌트 구성, 체험 입력의 비인증 동작, 비밀번호 값 없이 type만 변경하는지, 로그인·로그아웃·세션 만료의 마스킹 복원, 실패 안내·초기화, 검색·요약·출처·메뉴 이벤트를 검사했다.

검사 원본과 실행기는 사용자 작업 폴더에 보존했으며 `hyundap/tests/`는 수정하지 않았다.

```powershell
& 'C:/workspace/Hyundap/.venv/Scripts/python.exe' -X utf8 -B 'C:/Users/user/Documents/ChatGPT/프론트엔드/.ui-review-work/run_no_js_tests.py'
```

- 새 검사: `.ui-review-work/test_ui_no_custom_js.py`
- 상세 결과: `.ui-review-work/no-custom-js-tests.log`
- 실행 설정: `http://127.0.0.1:7861/config`, 역할 컴포넌트 `radio`, launch script `None`, 사용자 이벤트 JS 0개 확인.

## 브라우저 확인 범위

`python -m hyundap.app --mode demo --port 7861`로 실행한 Aside 브라우저에서 로그인 카드 배치, 일반/관리자 전환 시 제목·강조색, 체험 관리자 입력과 비밀번호 보기 전환을 확인했다. 이는 데모 화면 확인이며 실제 서버 인증이 아니다.

이후 UI 도구가 사용자 조작을 감지하여 추가 클릭을 중단했다. 브라우저의 로그인 이벤트 완료·후속 문서 동작·모바일 전체 검수는 완료로 기록하지 않는다. Python/Gradio 콜백 검사와 실제 브라우저 검수는 별개다. 이 기록은 다운로드 PR #265의 검증 또는 승인으로 사용하지 않는다.

## 변경되는 동작

- 자체 JavaScript였던 출처창 Esc 닫기·포커스 가두기·이전 포커스 복원은 제거했다. 출처창은 기존 Python 이벤트와 닫기 버튼으로 사용한다.
- 입력별 `aria-invalid` DOM 자동 삽입은 제거했다. 오류는 기존 `role="alert"` 문구와 CSS 테두리로 표시한다.
- 실제 인증·라우팅·검색·요약·업로드 서비스 계약은 변경하지 않는다.
