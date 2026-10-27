# db_tset · 실제 문장형 검색 시험

검증일: 2026-10-26. 운영 DB·실문서 대신 전용 로컬 MySQL과 합성 문서를 사용했다.
API 키와 DATABASE_URL 값은 기록하지 않으며 루트 .env는 Git에서 제외한다.

## 변경과 연결

기존 DB 시험 UI의 문자열 포함 검사를 제거하고 `search.search()`를 호출한다.
업로드는 실제 ingest → embed_and_store → Chroma 비공개 저장 → DB 완전성 확인 →
게시/활성화로 연결한다. 조회 결과는 기존 to_plain·BackendServices._document로 변환한다.
인증만 합성 manager 세션이며 질문 답변 생성·요약 LLM은 연결하지 않았다.
기존 키워드 시험 서버와 구분되는 새 UI 포트는 7862다.

## 환경과 결과

MySQL 8.4.13, SQLAlchemy 2.0.55, 실제 Chroma 1.5.9,
OpenAI `text-embedding-3-small`을 사용했다. 최소 점수는 기존 기본값 0.3을 유지했다.
프로젝트에 저장된 DATABASE_URL을 시험 자료로 변경하지 않고 별도 127.0.0.1:33180
시험 DB를 사용했다. Chroma는 실행별 새 임시 경로에 저장한다.

| 문장형 질문 | 기대/실제 1위 | 유사도 | 판정 |
|---|---|---|---|
| 엘리베이터 상태를 확인하는 주기는 어떻게 되나요? | maintenance.docx | 0.3046 | 통과 |
| 업무 때문에 이동하면서 쓴 돈은 어떻게 돌려받나요? | expenses.docx | 0.4166 | 통과 |
| 며칠 쉬려면 언제까지 신청해야 하나요? | leave.docx | 0.4560 | 통과 |

무관한 질문 '토성의 고리는 어떤 물질로 이루어져 있나요?'는 결과 0건이다.
문장형 3/3의 첫 결과는 맞았지만 일부 질문에 다른 문서도 하위 결과로 포함됐다.
첫 질문 점수는 0.3 경계에 가깝다. 이 작은 합성 표본으로 일반적인 재현율·정밀도를
보장하지 않으며 운영 문서·다양한 표현으로 별도 평가가 필요하다.

## 재현과 UI 확인

실제 API 시험은 `python -m hyundap.tests.smoke.semantic_embedding_smoke`로 실행한다.
SEMANTIC_TEST_URL은 127.0.0.1의 빈 hyundap_semantic_eval_test 또는
8자리 hex 접미사가 붙은 전용 DB만 허용한다. 기존 테이블이 있으면 중단한다.
합성 DOCX 3개를 실제 수집·색인하고 질문 3개와 무관한 질문 1개를 실행한다.
결과 JSON은 .git/semantic-embedding-result.json에 남으며 첫 결과가 틀리면 실패한다.
Windows의 Chroma 열린 파일 때문에 임시 폴더 일부가 종료 시 남을 수 있다.
첫 실행의 임시 폴더 정리 오류를 보완한 뒤 새 시험 DB에서 정상 종료를 확인했다.

UI는 `python -m hyundap.tests.smoke.ui_db_smoke`이며 UI_DB_TEST_URL은 전용 시험 DB,
UI_DB_TEST_PORT=7862, UI_SEMANTIC_SEED=1로 실행했다.
현재 주소는 http://127.0.0.1:7862, 합성 계정은 manager / demo1234다.
기존 가짜 벡터 자료는 자동 이행하지 않으므로 새 화면에서 문서를 다시 업로드한다.

API 대역을 사용하는 연결·검색 회귀 검증은 다음 명령으로 27개 통과했다.

```powershell
python -m unittest hyundap.tests.test_semantic_ui_bridge hyundap.tests.test_ingest_bridge hyundap.tests.test_search
```

DB 관련 검증은 이전 격리 MySQL 8.4.13 실행에서 73개 모두 통과했다.
실제 문장형 시험 성공과 브라우저 화면 조작 검증은 구분한다.
