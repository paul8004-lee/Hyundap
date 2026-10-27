"""chatbot/ask.py — 질문 검사부터 근거 검색·답변·반환 전 재검증까지 연결.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-121)

함수:
    FUN-401 ask — 질문·검색·답변·인용 검증 흐름을 연결한다.
        ask_normalize_filters — 필터(ChatFilters·dict·None)를 ChatFilters로 맞춘다.
        ask_check_answer — 답변 인용을 검사하고 유보 답변이면 None을 준다.
        ask_build_response — 화면용 ChatResponse를 만든다.
    FUN-402 validate_question — 질문 공백과 길이 제한을 검사한다.
    FUN-403 retrieve_evidence — 권한과 필터가 적용된 근거 청크를 받는다.
    FUN-404 select_evidence — 답변에 사용할 근거를 고른다.
        select_evidence_dedupe — 같은 청크는 점수가 가장 높은 것 하나만 남긴다.
        select_evidence_limit — 문서당·전체 근거 개수를 제한한다.
    FUN-405 generate_answer — 질문과 근거를 LLM 체인에 전달한다.
    FUN-406 build_no_evidence_response — 근거가 없을 때 답변 유보 결과를 만든다.
    FUN-407 recheck_before_return — 반환 직전 auth에 문서·버전 접근을 다시 확인한다.

다른 폴더 함수(hyundap.search.search.search_chunks, hyundap.auth.permissions.verify_documents_access)는
아직 구현 전이라 호출할 때 가져온다 (import 기준은 이슈 #133).
"""
from . import citations as cit
from .constants import MAX_PER_DOCUMENT, MAX_QUESTION_CHARS, NO_EVIDENCE_MESSAGE, TOP_K
from .models import ChatbotError, ChatFilters, ChatResponse, map_dependency_error
from .prompts import build_answer_chain


def ask(question, filters, user):
    """받는 것: 질문, 필터(ChatFilters·dict·None), 로그인 사용자 / 돌려주는 것: ChatResponse
    질문 검사 → 근거 받기·고르기 → 답변 생성 → 인용 검사 → 출처 → 반환 직전 재검증 → 화면용 정리."""
    question = validate_question(question)
    filters = ask_normalize_filters(filters)
    hits = select_evidence(retrieve_evidence(question, filters, user))
    if not hits:
        return build_no_evidence_response(filters)
    aliases = cit.assign_aliases(hits)
    answer = generate_answer(question, cit.format_evidence(aliases))
    used = ask_check_answer(answer, aliases)
    if used is None:
        return build_no_evidence_response(filters)
    cited_hits = [aliases[alias] for alias in used]
    citations = [cit.build_citation(hit, number) for number, hit in enumerate(cited_hits, 1)]
    recheck_before_return(user, cited_hits)
    return ask_build_response(answer, used, citations)


def ask_normalize_filters(filters):
    """받는 것: ChatFilters·dict·None / 돌려주는 것: ChatFilters (None이면 허용 범위 전체)
    UI는 필터를 dict로 넘길 수 있다. 모르는 키가 있으면 화면에 일반 오류가 아니라 검색 실패 안내가 가도록 바꾼다."""
    if isinstance(filters, dict):
        try:
            filters = ChatFilters(**filters)
        except TypeError as error:
            raise ChatbotError('RETRIEVAL_FAILED', '검색 조건 형식이 올바르지 않습니다.') from error
    return filters or ChatFilters()


def ask_check_answer(answer, aliases):
    """받는 것: LLM 답변, 이름표 사전 / 돌려주는 것: 쓰인 이름표 목록 (LLM이 유보만 답했으면 None)"""
    try:
        return cit.validate_citations(answer, aliases)
    except ChatbotError as error:
        if error.code == 'NO_EVIDENCE':
            return None
        raise


def ask_build_response(answer, used, citations):
    """받는 것: 답변, 쓰인 이름표, 출처 목록 / 돌려주는 것: 화면용 ChatResponse(status='ANSWERED')"""
    display, _ = cit.render_answer(answer, used)
    return ChatResponse(
        status='ANSWERED', answer=display, citations=citations,
        citations_by_company=cit.group_citations_by_company(citations),
        document_ids=list(dict.fromkeys(c.document_id for c in citations)),
    )


def validate_question(question, max_chars=MAX_QUESTION_CHARS):
    """받는 것: 질문 / 돌려주는 것: 앞뒤 공백을 지운 질문 (빈 질문·max_chars 초과는 거부)"""
    if not isinstance(question, str) or not question.strip():
        raise ChatbotError('EMPTY_QUESTION')
    question = question.strip()
    if len(question) > max_chars:
        raise ChatbotError('QUESTION_TOO_LONG')
    return question


def retrieve_evidence(question, filters, user):
    """받는 것: 질문, ChatFilters, 사용자 / 돌려주는 것: ChunkHit 목록 (점수 내림차순)
    회사 범위·최소 점수·공개 조건·평가 자료 제외는 search.search_chunks가 처리한다.
    search가 아직 없을 때의 ImportError도 RETRIEVAL_FAILED로 바뀌도록 가져오기를 try 안에 둔다."""
    try:
        from hyundap.search.search import search_chunks
        return list(search_chunks(
            user=user, query=question, company_ids=filters.company_ids, doc_types=filters.doc_types,
            document_ids=filters.document_ids, top_k=TOP_K * 2,  # 다음 단계에서 줄이므로 넉넉히
        ))
    except Exception as error:
        raise map_dependency_error(error, stage='retrieve') from error


def select_evidence(hits):
    """받는 것: ChunkHit 목록 / 돌려주는 것: 같은 청크는 하나만, 문서당·전체 개수를 줄인 목록 (점수 높은 순)"""
    best = select_evidence_dedupe(hits)
    return select_evidence_limit(sorted(best, key=lambda h: h.score or 0, reverse=True))


def select_evidence_dedupe(hits):
    """받는 것: ChunkHit 목록 / 돌려주는 것: chunk_id마다 점수가 가장 높은 근거 1개씩"""
    best = {}
    for hit in hits:
        kept = best.get(hit.chunk_id)
        if kept is None or (hit.score or 0) > (kept.score or 0):
            best[hit.chunk_id] = hit
    return list(best.values())


def select_evidence_limit(hits):
    """받는 것: 점수 높은 순 ChunkHit 목록 / 돌려주는 것: 문서당 MAX_PER_DOCUMENT(4)개, 전체 TOP_K(8)개까지"""
    picked, per_document = [], {}
    for hit in hits:
        count = per_document.get(hit.document_id, 0)
        if count < MAX_PER_DOCUMENT:
            per_document[hit.document_id] = count + 1
            picked.append(hit)
    return picked[:TOP_K]


def generate_answer(question, evidence_text):
    """받는 것: 질문, 근거 문자열 / 돌려주는 것: [E번호]가 달린 답변 원문"""
    chain = build_answer_chain()
    try:
        return chain.invoke({'question': question, 'evidence': evidence_text}).strip()
    except Exception as error:
        raise map_dependency_error(error, stage='llm') from error


def build_no_evidence_response(filters):
    """받는 것: ChatFilters / 돌려주는 것: LLM을 부르지 않은 유보 응답(status='NO_EVIDENCE')"""
    narrowed = bool(filters and (filters.document_ids or filters.doc_types))
    return ChatResponse(
        status='NO_EVIDENCE', answer=NO_EVIDENCE_MESSAGE,
        message='문서 유형·선택 문서 필터를 넓혀 다시 질문해 보세요.' if narrowed else '',
    )


def recheck_before_return(user, cited):
    """받는 것: 사용자, 답변에 쓰인 근거 / 돌려주는 것: 없음(통과)
    인용 문서·버전을 지금도 볼 수 있는지 auth에 다시 확인한다. auth는 통과하면 None, 아니면 예외를 던진다.
    AccessError(STATE_CHANGED)는 ChatbotError(STATE_CHANGED)로, DbError는 RETRIEVAL_FAILED로 바꾸고
    나머지 권한 예외는 그대로 올린다. 어느 경우든 만든 답변은 돌려주지 않는다."""
    refs = list(dict.fromkeys((hit.document_id, hit.version_id) for hit in cited))
    if not refs:  # auth는 빈 목록을 INVALID_INPUT으로 거부한다
        return
    from hyundap.auth.permissions import verify_documents_access
    try:
        verify_documents_access(user, refs)
    except Exception as error:
        raise map_dependency_error(error, stage='recheck') from error

