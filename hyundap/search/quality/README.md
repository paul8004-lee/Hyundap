# search/quality — 검색 순위 평가 (이슈 #237)

검색이 정답 문서를 몇 위에 올리는지, 다른 회사 문서가 섞이지 않는지를 **같은 방식으로 반복해서** 재는 도구입니다.
`search`의 동작은 바꾸지 않습니다. 담당: 이준영.

| 파일 | 내용 |
|---|---|
| `golden_search.json` | 정답셋. 질문 · 정답 문서 · 유형 · 출처 · 상태(seed/draft) |
| `dataset.py` | 정답셋 읽기와 형식 검사 |
| `metrics.py` | Hit@1·3, MRR, Recall@k, 타사 노출 검사, 유형별 요약, 회귀 판정 (계산만 함) |
| `run_eval.py` | 정답셋 확인(`--check`)과 실제 실행(`--run`), 기록 저장 |
| `import_answers.py` | 목업 데이터의 `검색테스트_정답300.csv`를 정답셋에 합침 |
| `../../tests/test_search_quality.py` | Mock 시험 25개 (API·DB 없이 계산·변환 검증) |

## 실행 (저장소 맨 위에서)

```bash
python -m unittest hyundap.tests.test_search_quality -v   # Mock 시험 (비용 없음)
python -m hyundap.search.quality.run_eval --check         # 정답셋 형식·유형별 표본 수 확인 (비용 없음)
python -m hyundap.search.quality.run_eval --run           # 실제 검색 실행 (실제 DB·OpenAI 호출)
```

`--run`에 필요한 것:

- `.env`(DB 접속, OpenAI 키)와 `data/qa_accounts.json` (둘 다 커밋하지 않습니다)
- 기존 QA 문서 25건이 올라가 있고, 그 벡터가 있는 PC (`hyundap/tests/qa/upload_corpus.py`)
- 질문 1건당 임베딩 1회가 듭니다. 답변 모델은 부르지 않습니다.

결과는 `data/quality/eval-<시각>.json`에 남습니다(`data/`는 gitignore).

## 평가 방식

| 질문 유형 | 채점 |
|---|---|
| `code` `keyword` `natural` `synonym` `exclusion` | 정답 문서가 상위 3위 안이면 PASS. Hit@1·3, MRR 계산 |
| `cross_company` | 질문한 회사가 아닌 회사 문서가 결과에 하나라도 있으면 FAIL |
| `no_evidence` | 결과가 없으면 PASS, 있으면 INFO(최고 점수만 기록, 판정하지 않음) |

- 모든 질문에서 **질문한 회사가 아닌 회사의 문서가 결과에 있으면 FAIL**입니다 (회사 코드는 파일명 앞부분으로 판별).
- **Recall@k**는 `relevant_docs`(관련 문서 정답 집합)가 있는 질문에서만 계산합니다. 없으면 `null`로 두고 평균에서도 뺍니다.
- 유형별 질문이 **10건 미만이면 PASS·FAIL을 내지 않고 "표본 부족"**으로 표시합니다.

## 회귀 기준 (제안값, 팀 합의 전)

| 항목 | 기준 |
|---|---|
| 유형별 Hit@3 | 0.9 이상 (표본 10건 이상일 때만 판정) |
| 타사 문서 노출 | 0건 |

숫자는 `metrics.py` 위쪽의 `MIN_SAMPLES` `HIT3_MIN` `LEAKS_MAX`만 바꾸면 됩니다.

## 기록 항목

결과 파일에 다음이 함께 남습니다: 실행 시각, 모드(real), git 커밋·수정 여부, 검색 설정(최소 점수·조회 상한·임베딩 모델),
정답셋 파일·유형별 표본 수, 호출 횟수, 기준값, 유형별 지표, 질문별 결과, 판정, 재현 명령.
비용은 금액으로 계산하지 않고 호출 횟수만 적습니다.

## 정답셋 구성 (74건)

| 유형 | 건수 | seed | draft | 비고 |
|---|---:|---:|---:|---|
| code (문서 코드) | 24 | 20 | 4 | 정답 CSV 20건(예상 답변·근거 위치 포함), 초안 4건 |
| natural (자연어) | 15 | 9 | 6 | 초안 6건은 **관련 문서 집합**이 있어 Recall@k 계산 |
| keyword (키워드) | 10 | 1 | 9 | 주제 문서가 2개인 3건은 관련 문서 집합 있음 |
| synonym (동의어) | 10 | 0 | 10 | 제목 낱말을 다른 말로 바꾼 질문 (예: AGV → 무인운반차) |
| exclusion (제외 조건) | 4 | 1 | 3 | **표본 부족** |
| cross_company (타사 요구) | 7 | 2 | 5 | 노출 여부만 확인 |
| no_evidence (근거 없음) | 4 | 1 | 3 | 판정하지 않고 기록만 |

- `seed`: 기존 QA(`tests/qa/corpus.json`), 이슈 P1, 목업 데이터 `검색테스트_정답300.csv`에서 정답이 정해진 질문.
- `draft`: 목업 데이터 `전체파일목록.csv`의 **문서 제목**만 근거로 만든 질문(35건)과, 문서 코드가 가리키는 문서를 정답으로 둔 질문(5건).
  문서 내용은 지어내지 않았고, 정답은 "그 제목·코드의 문서", 관련 문서 집합은 "같은 주제(제목 앞부분이 같은) 매뉴얼·교육자료"입니다. 실제 실행으로 확인이 필요합니다.
- 정답 문서는 모두 이미 공유 DB에 올라간 QA 문서 25건 안에 있습니다(`--check`가 확인).

정답 CSV를 다시 합치려면 (CSV는 공유 드라이브 `목업 데이터/03_목록과검증`, 저장소에 넣지 않음):

```bash
python -m hyundap.search.quality.import_answers "<CSV 경로>"          # QA 문서 25건에 해당하는 질문만
python -m hyundap.search.quality.import_answers "<CSV 경로>" --all    # 300문항 전부 (해당 문서를 올린 뒤)
```

## 한계

- 정답 CSV 300문항은 **모두 문서 코드가 들어간 질문**입니다(자료 안내문: "기본 확인용"). 코드 질문 결과만으로 검색 정확도를 판단하지 않습니다.
- 지금 측정할 수 있는 문서는 공유 DB에 올라간 25건뿐이라, CSV 300문항 중 20문항만 넣었습니다.
- `exclusion`은 4건이라 표본 부족입니다.
- 근거 위치는 CSV 값(예: "완료와 보존", "slide 4")을 그대로 넣었습니다. 앱이 보여 주는 위치 문구(`섹션 N`, `슬라이드 N`)와 형식이 달라, 근거 위치 채점(chatbot·ingest 몫)에서 맞춰 봐야 합니다.
- 상충 내용·악성 지시 문서 사례는 없습니다. 새 가상 문서가 필요합니다.
- `run_eval.py --run`은 실제로 실행해 보지 못했습니다(이 PC에 업로드된 문서 벡터와 QA 계정 파일이 없음). 가짜 결과로 전체 흐름만 확인했습니다.
- 답변 정답률, 근거 위치, 요약, 인용, OCR 평가는 이 폴더의 범위가 아닙니다(chatbot·ingest 담당).
