"""chatbot/constants.py — 챗봇 제한값과 사용자 안내 문구.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-126)
"""

# 근거 개수 (미정 D07: 정확도 테스트 후 조정)
TOP_K = 8                    # LLM에 넘길 근거 최대 개수
MAX_PER_DOCUMENT = 4         # 여러 문서일 때 한 문서에서 쓸 근거 최대 개수 (문서 1개면 TOP_K까지, #182)
# 최소 점수(0.3)는 search.search_chunks가 적용하므로 여기서는 두지 않는다

# 길이 제한
MAX_QUESTION_CHARS = 2000    # embedding.embed_query·search 검색어와 같은 기준
MAX_ANSWER_CHARS = 20000
EXCERPT_MAX_CHARS = 300      # 출처 발췌 길이 (search 화면 발췌 200자와는 별개)

# 요약: 구간별 직렬 요약 (gpt-5 입력 약 27.2만 토큰 중 23만까지 쓴다)
SUMMARY_INPUT_TOKEN_BUDGET = 230_000   # 한 호출 입력 상한 (프롬프트 전체 기준)
SUMMARY_CARRY_TOKENS = 8_000           # 다음 구간에 넘기는 이전 요약 몫
SUMMARY_PROMPT_TOKENS = 1_000          # 시스템 프롬프트·형식 고정분
SUMMARY_MAX_CALLS = 3                  # 문서 1개 요약의 LLM 호출 상한 (= 최대 구간 수)
SUMMARY_TOKEN_MARGIN = 1.03            # 토크나이저 차이·메시지 형식 토큰 여유
SUMMARY_REPLAN_RATIO = 0.85            # 입력 길이 초과로 거절되면 단계 상한을 이만큼 줄여 다시 나눈다
SUMMARY_MAX_CONCURRENT = 1             # 서버 전체 동시 요약 수 (분당 토큰 한도 보호)
SUMMARY_SLOT_WAIT_SECONDS = 1          # 자리가 없을 때 기다리는 최대 시간. 지나면 '진행 중' 안내 (ui 작업 스레드를 붙잡는 시간 상한)
SUMMARY_BUSY_MESSAGE = '다른 요약이 진행 중입니다. 잠시 후 다시 시도해 주세요.'
MAX_SUMMARY_CHARS = 30_000             # 요약 글자 상한 (질문 답변 MAX_ANSWER_CHARS와 별개)
# 요약 LLM: 큰 입력이라 질문 답변(30초)과 따로 둔다
SUMMARY_TIMEOUT_SECONDS = 300
SUMMARY_REASONING_EFFORT = 'low'       # gpt-5·o 계열에만 넘긴다
SUMMARY_STEP_OUTPUT_TOKENS = 8_000     # 중간 요약 출력 상한 (추론 토큰 포함)
SUMMARY_FINAL_OUTPUT_TOKENS = 10_000   # 최종 요약 출력 상한 (추론 토큰 포함)

# LLM: API 키와 요약 모델은 common.config.get_settings()가 정한다 (기본 gpt-5)
LLM_TIMEOUT_SECONDS = 30
# 질문 답변 모델. 10/30 실측(44문항×3회)에서 정답률은 gpt-5와 같고(99.2% 대 98.5%) 지연 중앙값 0.96초 대 5.71초,
# 비용 1,000건 $3.34 대 $5.78이라 답변만 gpt-4o로 둔다. 요약은 gpt-4o가 핵심 사실을 47%만 담아 gpt-5를 유지한다
ANSWER_MODEL = 'gpt-4o'

NO_EVIDENCE_MESSAGE = '제공된 문서에서 확인할 수 없습니다.'
# LLM이 유보 문구를 조금 바꿔 말해도 근거 없음으로 알아보기 위한 표현 (명세 밖 추가 상수)
NO_EVIDENCE_HINTS = ('확인할 수 없', '찾을 수 없', '관련 내용이 없', '정보가 없')

# 오류 코드와 화면 안내 문구 (미정 D11: 박경환과 문구 확정)
ERROR_MESSAGES = {
    'EMPTY_QUESTION': '질문을 입력해 주세요.',
    'QUESTION_TOO_LONG': f'질문은 {MAX_QUESTION_CHARS}자 이하로 입력해 주세요.',
    'RETRIEVAL_FAILED': '문서 검색에 실패했습니다. 잠시 후 다시 시도해 주세요.',
    'NO_EVIDENCE': NO_EVIDENCE_MESSAGE,
    'LLM_FAILED': '답변 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.',
    'INVALID_ANSWER': '답변을 만들지 못했습니다. 질문을 바꿔 다시 시도해 주세요.',
    'INVALID_CITATION': '근거를 확인할 수 없는 답변이라 표시하지 않았습니다.',
    'ACCESS_DENIED': '이 문서에 접근할 권한이 없습니다.',
    'STATE_CHANGED': '문서 상태가 바뀌었습니다. 다시 질문해 주세요.',
    'CONFIG_MISSING': 'AI 설정을 확인해 주세요.',
    # 통합본 10개 밖 코드. UI가 로그인 화면으로 돌아가도록 search(SearchError)와 같은 코드·문구를 쓴다 (#143·#144 리뷰)
    'SESSION_EXPIRED': '로그인 시간이 만료되었습니다. 다시 로그인해 주세요.',
}
ERROR_CODES = tuple(ERROR_MESSAGES)
# to_feedback이 화면에 넘기는 상태. 여기 없는 코드는 'failed'. session_expired면 UI가 로그인 화면으로 이동한다
FEEDBACK_STATUS = {
    'EMPTY_QUESTION': 'input_error',
    'QUESTION_TOO_LONG': 'input_error',
    'NO_EVIDENCE': 'empty',
    'ACCESS_DENIED': 'denied',
    'SESSION_EXPIRED': 'session_expired',
}

# 다른 폴더 오류를 클래스 이름과 code 속성으로 구분한다 (auth.AccessError·db.DbError·search.SearchError)
# 세션 만료(SESSION_EXPIRED)는 권한 없음이 아니라 재로그인 안내로 따로 보낸다
ACCESS_ERROR_NAMES = ('AccessError',)
SEARCH_ACCESS_CODES = ('ACCESS_DENIED', 'COMPANY_FORBIDDEN', 'NO_COMPANY_SCOPE')

# 문서 자동 분류 (doc_classifier.py, 명세 밖 추가 기능). OpenAI Decisions API는 현재 gpt-6-luna만 지원한다
CLASSIFY_MODEL = 'gpt-6-luna'
CLASSIFY_TIMEOUT_SECONDS = 10          # 업로드 중(WORKFLOW_LOCK 안)에도 불리므로 최악 대기를 약 10초로 묶는다
CLASSIFY_MAX_RETRIES = 0               # 재시도하지 않는다. 실패해도 업로드는 계속되고 classify_status='failed'가 남는다 (자동 분류 업로드면 현재 유형 유지·사유 CLASSIFY_FAILED)
CLASSIFY_SAMPLE_CHARS = 6000           # 분류에 보내는 앞쪽 본문 글자 수 상한 (긴 문서도 입력 크기 일정)
CLASSIFY_COMPANY_NAMED_MIN = 0.5       # 본문에 회사명이 적혀 있을 확률이 이 값 이상일 때만 회사 일치를 판단한다
CLASSIFY_MATCH_YES_MIN = 0.7           # 회사 일치 확률이 이상이면 'yes'
CLASSIFY_MATCH_NO_MAX = 0.3            # 이하이면 'no', 그 사이는 판단 보류('')
# 자동 분류 업로드의 최종 유형 규칙 (decide_doc_type, #261). 기준 미만·실패면 현재 유형을 유지하고 사유 코드를 남긴다
CLASSIFY_APPLY_MIN_CONFIDENCE = 0.8    # AI 유형을 실제 유형으로 쓰는 확신도 하한 (임시값, 실제 문서 평가 후 확정)
CLASSIFY_FALLBACK_LOW_CONFIDENCE = 'LOW_CONFIDENCE'   # 대체 저장 사유: AI 유형은 나왔지만 확신도가 기준 미만
CLASSIFY_FALLBACK_FAILED = 'CLASSIFY_FAILED'          # 대체 저장 사유: AI 유형을 얻지 못함(API 실패·거부 응답·목록 밖 유형)
# 유형 코드별 판단 기준. 코드·표시 이름은 common.schemas.DOC_TYPES를 그대로 쓴다
CLASSIFY_DOC_TYPE_HINTS = {
    'manual': '제품·장비·시스템의 사용법, 조작·설치·점검 방법을 단계별로 설명하는 문서',
    'policy': '사내 규정·정책·지침·기준처럼 지켜야 할 규칙과 책임을 정한 문서',
    'guide': '업무 안내·절차 소개·온보딩처럼 일을 어떻게 하는지 알려 주는 안내 문서',
    'other': '위 셋에 해당하지 않는 문서(보고서·회의록·소개 자료·언론 보도 등)',
}
