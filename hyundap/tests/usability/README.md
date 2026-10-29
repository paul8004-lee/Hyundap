# MVP 사용성 테스트

실제 Railway DB·OpenAI·로컬 Chroma에 연결된 화면을 사람이 쓰는 순서대로 조작하며 확인하는 기록 폴더입니다. 자동 테스트(`test*.py`)가 아니므로 `unittest discover`에는 포함되지 않습니다.

## 환경

| 항목 | 값 |
|---|---|
| 작업 폴더 | `C:\projects\hyundap-mvp` (develop `1b693b8` 기준 분리 작업 폴더, detached) |
| Python | `C:\projects\hyundap-mvp\.venv` (requirements.txt 설치 완료) |
| 설정 | `C:\projects\hyundap-mvp\.env` (`DATABASE_URL`, `OPENAI_API_KEY`) |
| DB | Railway `railway`, 최종 v4 (schema_version=4, 테이블 11개) |
| 업로드 원본·Chroma | `C:\projects\hyundap-mvp\data\uploads`, `data\chroma_db` (기본값) |
| 테스트 계정 | `C:\projects\hyundap-mvp\data\mvp_accounts.json` (비밀번호 포함, git 제외 폴더) |
| 예시 문서 | `G:\.shortcut-targets-by-id\1YhbkqVbEwGcxlczbdRbz4L5exCoFEIsA\1차 프로젝트_3팀\구버전\목업 데이터\02_가상자료\` |
| 정답 질문 | 같은 위치의 `03_목록과검증\검색테스트_정답300.csv` |

## 실행과 종료

```powershell
cd C:\projects\hyundap-mvp
.venv\Scripts\python.exe hyundap\tests\usability\run_gui.py
```

브라우저에서 `http://127.0.0.1:7860`을 엽니다. 종료는 실행 창에서 Ctrl+C입니다.

`run_gui.py`는 `hyundap.app.build_app`을 그대로 쓰고 로그인 제공자만 `hyundap.auth.session_service`로 바꿉니다. 저장소의 기본 실행(`python -m hyundap.app`)은 체험 로그인 그대로입니다.

## 계정

| 아이디 | 역할 | 회사 |
|---|---|---|
| `mvp-admin@hyundap.test` | platform_admin | 전체 (hri·mvx·hel) |
| `mvp-manager@hyundap.test` | company_admin | hri (현대경제연구원) |
| `mvp-employee@hyundap.test` | employee | hri |
| `mvp-mvx@hyundap.test` | employee | mvx (현대무벡스) |

비밀번호는 네 계정 공통이며 `data\mvp_accounts.json`에 있습니다. 공유 DB의 계정이므로 팀 채팅·GitHub 등 공개된 곳에 적지 않습니다.

## 규칙

1. 시나리오는 `TEST_PLAN.md` 순서대로 진행하고, 결과는 `RESULTS.md`에 시나리오별로 기록합니다.
2. 기록 항목: 실행 일시, 계정, 조작 순서, 기대 결과, 실제 결과(화면 문구 그대로), 판정(PASS / FAIL / 관찰), 사용성 메모.
3. 화면 캡처를 저장할 수 있으면 `screenshots\` 아래에 `S<번호>_<짧은설명>.png`로 둡니다.
4. 오류가 나면 `RESULTS.md`에 재현 순서를 적고, 실행 창(콘솔) 출력의 마지막 부분을 함께 남깁니다. 비밀번호·토큰·API 키는 기록하지 않습니다.
5. 앱 코드는 이 테스트에서 수정하지 않습니다. 발견한 문제는 담당 모듈과 함께 `RESULTS.md`의 "발견 사항"에 적습니다.
6. `data\`(계정 비밀번호·업로드 원본·Chroma)는 커밋하지 않습니다. 저장소에는 이 폴더의 문서와 실행기만 둡니다.

## 참고: 공유 DB의 다른 데이터

2026-10-27 03:11(UTC)에 다른 팀원의 점검으로 보이는 `테스트 회사 72947edcadb5` 회사, `example.invalid` 계정, PDF 문서 1건이 생성되어 있습니다. 이 테스트에서 만든 데이터가 아니므로 수정·삭제하지 않습니다.
