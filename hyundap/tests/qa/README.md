# 기능 점검 (WBS 4.1.x)

공유 Railway DB에 테스트 문서를 올리고 형식·필터·권한·예외·정확도 시나리오를 자동으로 실행합니다.

| 파일 | 내용 |
|---|---|
| `TEST_SCENARIOS.md` | 시나리오와 기대 결과 (4.1.1) |
| `corpus.json` | 올릴 문서 25건과 정확도 질문 10개 |
| `upload_corpus.py` | 문서 업로드. 이미 올린 문서는 건너뜀 |
| `run_qa.py` | 시나리오 실행. 결과는 `results/run-*.json` |
| `TEST_REPORT.md` | 테스트 결과서 (4.1.8) |

## 실행

저장소 맨 위에서, 프로젝트 의존성이 설치된 가상환경으로 실행합니다.

```
python hyundap/tests/qa/upload_corpus.py
python hyundap/tests/qa/run_qa.py                 # 전체
python hyundap/tests/qa/run_qa.py permission      # 분류 하나만
```

필요한 것:

- `.env` (DB 접속, OpenAI 키)
- 공유 드라이브 `1차 프로젝트_3팀/구버전/목업 데이터`가 G: 드라이브에 연결되어 있을 것 (`corpus.json`의 `source_root`)
- `data/qa_accounts.json` — 계정별 `username`·`password`·`company_id`. 비밀번호가 들어 있으므로 커밋하지 않습니다(`data/`는 gitignore)

업로드 결과(문서 ID)는 `data/qa_state.json`에, 예외 점검용으로 만든 파일은 `data/qa_samples/`에 저장됩니다.

## 주의

- 실제 공유 DB에 문서를 올리고 OpenAI를 호출합니다. 예외 점검(E1~E11)은 실행할 때마다 실패 기록이 DB에 남습니다.
- DB가 다시 초기화되면 `data/qa_state.json`을 지우고 업로드부터 다시 실행합니다.
