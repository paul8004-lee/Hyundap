"""chatbot/constants.py — 챗봇 제한값과 사용자 안내 문구.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-126)
"""

# 근거 개수 (미정 D07: 정확도 테스트 후 조정)
TOP_K = 8                    # LLM에 넘길 근거 최대 개수
MAX_PER_DOCUMENT = 4         # 한 문서에서 쓸 근거 최대 개수
# 최소 점수(0.3)는 search.search_chunks가 적용하므로 여기서는 두지 않는다

# 길이 제한
MAX_QUESTION_CHARS = 2000    # embedding.embed_query·search 검색어와 같은 기준
MAX_ANSWER_CHARS = 20000
EXCERPT_MAX_CHARS = 300      # 출처 발췌 길이 (search 화면 발췌 200자와는 별개)

# 요약 (미정 D10)
SUMMARY_BATCH_SIZE = 8
MAX_SUMMARY_CHUNKS = 64

# LLM: 모델명·API 키는 common.config.get_settings()가 정한다 (기본 gpt-5)
LLM_TIMEOUT_SECONDS = 30

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
}
ERROR_CODES = tuple(ERROR_MESSAGES)
# to_feedback이 화면에 넘기는 상태. 여기 없는 코드는 'failed'
FEEDBACK_STATUS = {
    'EMPTY_QUESTION': 'input_error',
    'QUESTION_TOO_LONG': 'input_error',
    'NO_EVIDENCE': 'empty',
    'ACCESS_DENIED': 'denied',
}

# 다른 폴더 오류를 이름으로 구분한다. 이름(AccessError·DbError)은 DB 계약으로 확정됐지만 클래스가 아직 없어 import하지 않는다
ACCESS_ERROR_NAMES = ('AccessError',)
SEARCH_ACCESS_CODES = ('ACCESS_DENIED', 'COMPANY_FORBIDDEN', 'NO_COMPANY_SCOPE')
