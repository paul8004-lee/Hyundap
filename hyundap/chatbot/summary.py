"""chatbot/summary.py — 선택 문서를 토큰 예산 안에서 구간별로 차례로 요약하고, 마지막 단계를 줄 단위로 스트리밍.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-124)

흐름: 청크 조회 → 접근 확인 → 구간 계획(1~3구간) → 구간 1 요약 → (구간 1 요약 + 구간 2 원문) 요약 → … → 출처 → 재검증.
LLM 호출은 구간 수만큼(최대 SUMMARY_MAX_CALLS=3회) 차례로 하고, 화면에는 마지막 단계만 검사를 통과한 줄 단위로 보낸다.

함수:
    FUN-420 summarize_document — 선택 문서 요약 입구. 스트림을 끝까지 돌려 SummaryResponse만 돌려준다.
        summarize_document_stream — 요약 전체를 진행하며 이벤트(status·delta·reset·done)를 내보낸다.
        acquire_summary_slot — 서버 전체에서 요약을 한 번에 SUMMARY_MAX_CONCURRENT개만 돌린다 (대기는 SUMMARY_SLOT_WAIT_SECONDS까지).
        summary_event — 이벤트 dict를 만든다.
        summarize_document_load — 문서 청크를 받아 ChunkHit 목록으로 바꾼다.
        summarize_document_to_hit — VectorHit 1건을 ChunkHit로 바꾼다.
    FUN-424 plan_summary_segments — 토큰 수로 구간(1~3개)과 제외 청크를 정한다.
        get_summary_encoder — tiktoken 인코더를 한 번만 불러온다.
        count_summary_tokens — 문자열의 토큰 수를 센다.
        step_token_limit — 한 단계에 넣을 수 있는 원문 토큰 상한.
        pack_segments — 청크 순서대로 상한까지 담아 구간을 나눈다.
        trim_overflow — 앞 구간만 쓰고 나머지 청크를 제외한다.
    FUN-421 summarize_rolling — 구간을 차례로 요약하며 앞 구간 요약을 다음 구간에 넘긴다.
        summarize_step — 한 단계를 실행하고 재시도·실패를 처리한다.
        build_step_evidence — 이전 요약과 이번 구간 근거를 나눠 LLM 입력을 만든다.
        allowed_step_aliases — 이 단계에서 인용해도 되는 이름표를 고른다.
        can_retry — 재시도해도 총 호출 상한을 넘지 않는지 본다.
        invoke_summary — 중간 단계: 한 번에 받아 인용을 검사한다.
    FUN-422 stream_summary — 마지막 단계: 줄마다 인용을 검사하고 통과한 줄만 내보낸다.
        stream_pieces — 체인에서 조각을 받는다. 스트리밍이 거절되면 한 번에 받는다.
        emit_line — 줄 1개를 검사하고 화면용 delta 이벤트로 만든다.
        split_complete_lines — 버퍼를 완성된 줄과 남은 조각으로 나눈다.
        is_stream_rejected — 스트리밍 거절(조직 인증 필요) 오류인지 본다.
        is_context_overflow — 입력 길이 초과 오류인지 본다.
        to_llm_error — LLM 단계 예외를 내부 신호 또는 ChatbotError로 바꾼다.
    FUN-423 compute_coverage — 전체 청크 중 요약에 포함한 범위를 계산한다.
        collect_citations — 요약에 쓰인 이름표로 출처를 만든다.
        format_excluded — 제외 범위 문구('예산 초과로 제외 p.7~p.10')를 만든다.
        compute_cited_ratio — 사용한 청크 중 인용된 청크 비율을 계산한다.
        summarize_document_response — 포함·제외 범위를 붙여 SummaryResponse를 만든다.
"""
import math
import threading
from functools import lru_cache

from . import citations as cit
from .ask import recheck_before_return
from .constants import (MAX_SUMMARY_CHARS, SUMMARY_BUSY_MESSAGE, SUMMARY_CARRY_TOKENS, SUMMARY_INPUT_TOKEN_BUDGET,
                        SUMMARY_MAX_CALLS, SUMMARY_MAX_CONCURRENT, SUMMARY_PROMPT_TOKENS, SUMMARY_REPLAN_RATIO,
                        SUMMARY_SLOT_WAIT_SECONDS, SUMMARY_TOKEN_MARGIN)
from .models import ChatbotError, SummaryPlan, SummaryResponse, map_dependency_error
from .prompts import build_summary_chain

_SLOT = threading.BoundedSemaphore(SUMMARY_MAX_CONCURRENT)
# 같은 단계를 다시 생성하면 고쳐질 수 있는 오류 (이름표 없는 문장·준 적 없는 이름표·빈 요약)
RETRYABLE_CODES = ('INVALID_CITATION', 'INVALID_ANSWER')


class _ContextOverflow(Exception):
    """입력 길이 초과로 거절됨. 구간을 다시 나눠야 한다는 내부 신호 (화면에 나가지 않는다)."""


def summarize_document(document_id, user):
    """받는 것: 문서 ID, 사용자 / 돌려주는 것: SummaryResponse
    summarize_document_stream을 끝까지 돌려 done 이벤트의 결과만 돌려준다. ui가 지금 부르는 입구다."""
    for event in summarize_document_stream(document_id, user):
        if event['type'] == 'done':
            return event['response']


def summarize_document_stream(document_id, user):
    """받는 것: 문서 ID, 사용자 / 돌려주는 것: 이벤트 제너레이터 (status·delta·reset·done, 오류는 ChatbotError)
    청크 조회 → 접근 확인(LLM 호출 전) → 구간 계획 → 차례로 요약 → 출처 → 인용 문서 재검증 → done.
    입력 길이 초과로 거절되면 단계 상한을 SUMMARY_REPLAN_RATIO만큼 줄여 한 번만 다시 나눈다."""
    yield from acquire_summary_slot()
    try:
        hits = summarize_document_load(document_id)
        if not hits:
            raise ChatbotError('NO_EVIDENCE', '요약할 내용이 없습니다.')
        recheck_before_return(user, hits)
        limit = step_token_limit()
        for attempt in range(2):
            plan = plan_summary_segments(hits, limit)
            used_hits = [hit for segment in plan.segments for hit in segment]
            aliases = cit.assign_aliases(used_hits)
            try:
                raw, skipped = yield from summarize_rolling(plan, aliases)
                break
            except _ContextOverflow:
                if attempt:
                    raise ChatbotError('LLM_FAILED') from None
                limit = int(limit * SUMMARY_REPLAN_RATIO)
                yield summary_event('reset', reason='입력이 길어 다시 나눠 요약합니다.')
        used, citations, cited_hits = collect_citations(raw, aliases)
        try:
            recheck_before_return(user, cited_hits)
        except Exception:
            yield summary_event('reset', reason='문서 상태가 바뀌었습니다.')
            raise
        yield summary_event('done', response=summarize_document_response(
            document_id, hits, used_hits, plan.excluded, skipped, raw, used, citations))
    finally:
        _SLOT.release()


def acquire_summary_slot():
    """받는 것: 없음 / 돌려주는 것: (기다릴 때만) status 이벤트
    한 요약이 계정 분당 토큰 한도를 크게 쓰므로, 동시에 돌면 질문 답변까지 한도 초과로 실패한다.
    기다림은 SUMMARY_SLOT_WAIT_SECONDS까지만 한다. ui는 이 함수를 작업 스레드(asyncio.to_thread)에서 부르므로
    끝없이 기다리면 대기 요청마다 공용 스레드 풀을 하나씩 붙잡아 검색·질문·세션 확인까지 멈춘다."""
    if _SLOT.acquire(blocking=False):
        return
    yield summary_event('status', stage='wait', message='다른 요약이 끝나기를 기다리는 중입니다.')
    if not _SLOT.acquire(timeout=SUMMARY_SLOT_WAIT_SECONDS):
        raise ChatbotError('LLM_FAILED', SUMMARY_BUSY_MESSAGE, retryable=True)


def summary_event(kind, **fields):
    """받는 것: 이벤트 종류('status'·'delta'·'reset'·'done'), 필드 / 돌려주는 것: {'type': 종류, …}"""
    return {'type': kind, **fields}


def summarize_document_load(document_id):
    """받는 것: 문서 ID / 돌려주는 것: 공개 청크를 chunk_index 순 ChunkHit 목록으로
    embedding·search가 없거나(ImportError) 조회가 실패하면 RETRIEVAL_FAILED. 형식(metadata 키) 오류는 감추지 않는다."""
    try:
        from hyundap.embedding.vector_store import get_document_chunks
        from hyundap.search.models import ChunkHit
        vector_hits = list(get_document_chunks(document_id))
    except Exception as error:
        raise map_dependency_error(error, stage='summary') from error
    return [summarize_document_to_hit(hit, ChunkHit) for hit in vector_hits]


def summarize_document_to_hit(hit, chunk_hit_class):
    """받는 것: VectorHit 1건, ChunkHit 클래스 / 돌려주는 것: ChunkHit
    질문 경로와 같은 근거 형식으로 맞춘다. version_no·score는 요약 경로에 없어 비운다."""
    meta = hit.metadata
    return chunk_hit_class(
        chunk_id=hit.chunk_id, document_id=meta['document_id'], version_id=meta['version_id'],
        version_no=None, company_id=meta['company_id'], company_name=meta['company_name'],
        doc_type=meta['doc_type'], file_name=meta['file_name'], section_no=meta.get('section_no'),
        location_label=meta['location_label'], chunk_index=meta['chunk_index'], text=hit.text, score=None,
    )


def plan_summary_segments(hits, limit):
    """받는 것: 청크 목록, 단계당 원문 토큰 상한 / 돌려주는 것: SummaryPlan(구간 목록, 제외 청크, 총 토큰)
    상한 단위로 담아 필요한 구간 수(최대 SUMMARY_MAX_CALLS)를 정하고, 그 수로 고르게 나눠지면 고르게 나눈다.
    청크 크기 때문에 고르게 안 나눠지면 상한 단위 그대로 쓴다. 넘치는 뒤쪽 청크는 제외한다."""
    tokens = [count_summary_tokens(cit.format_evidence({'E0': hit})) for hit in hits]
    greedy = pack_segments(hits, tokens, limit)
    count = min(SUMMARY_MAX_CALLS, len(greedy))
    balanced = pack_segments(hits, tokens, math.ceil(sum(tokens) / count))
    segments, excluded = trim_overflow(balanced if len(balanced) == count else greedy, count)
    return SummaryPlan(segments=segments, excluded=excluded, total_tokens=sum(tokens))


@lru_cache(maxsize=1)
def get_summary_encoder():
    """받는 것: 없음 / 돌려주는 것: tiktoken o200k_base 인코더 (불러오지 못하면 None)
    tiktoken은 처음 쓸 때 파일을 내려받으므로 한 번만 불러오고, 인터넷이 막힌 서버에서는 None으로 대체 계산하게 한다."""
    try:
        import tiktoken
        return tiktoken.get_encoding('o200k_base')
    except Exception:
        return None


def count_summary_tokens(text):
    """받는 것: 문자열 / 돌려주는 것: 토큰 수 × SUMMARY_TOKEN_MARGIN (인코더가 없으면 글자 1개 = 토큰 2개로 넉넉히)"""
    encoder = get_summary_encoder()
    count = len(encoder.encode(text)) if encoder else len(text) * 2
    return math.ceil(count * SUMMARY_TOKEN_MARGIN)


def step_token_limit():
    """받는 것: 없음 / 돌려주는 것: 한 단계 원문 토큰 상한 (입력 예산 - 넘겨받는 요약 몫 - 프롬프트 고정분)"""
    return SUMMARY_INPUT_TOKEN_BUDGET - SUMMARY_CARRY_TOKENS - SUMMARY_PROMPT_TOKENS


def pack_segments(hits, tokens, cap):
    """받는 것: 청크 목록, 청크별 토큰, 구간 상한 / 돌려주는 것: 구간 목록
    청크 순서대로 담다가 cap을 넘기 직전에 새 구간을 연다. 청크는 출처 단위라 쪼개지 않는다."""
    segments, current, size = [], [], 0
    for hit, token in zip(hits, tokens):
        if current and size + token > cap:
            segments.append(current)
            current, size = [], 0
        current.append(hit)
        size += token
    if current:
        segments.append(current)
    return segments


def trim_overflow(segments, count):
    """받는 것: 구간 목록, 쓸 구간 수 / 돌려주는 것: (앞 count개 구간, 나머지 구간의 청크)"""
    return segments[:count], [hit for segment in segments[count:] for hit in segment]


def summarize_rolling(plan, aliases):
    """받는 것: SummaryPlan, 사용 청크 전체의 이름표 사전 / 돌려주는 것: (최종 요약 원문, 인용 검사로 뺀 구간 목록)
    구간마다 status를 내보내고, 앞 구간 요약을 다음 구간 입력에 넣는다. 마지막 구간만 스트리밍한다.
    중간 구간이 끝내 실패하면 그 구간만 빼고 직전 요약을 그대로 넘긴다."""
    alias_of = {id(hit): alias for alias, hit in aliases.items()}
    carry, skipped, calls, total = '', [], 0, len(plan.segments)
    for number, segment in enumerate(plan.segments, 1):
        last = number == total
        yield summary_event('status', stage='final' if last else 'step', step=[number, total],
                            message=f'구간 {number}/{total} 요약 중' if total > 1 else '요약 생성 중')
        segment_aliases = {alias_of[id(hit)]: hit for hit in segment}
        text, calls, failed = yield from summarize_step(last, segment_aliases, carry, aliases, calls, total - number)
        if text:
            carry = text
        elif failed:
            skipped.append(segment)
    return carry, skipped


def summarize_step(last, segment_aliases, carry, aliases, calls, remaining):
    """받는 것: 마지막 단계 여부, 이번 구간 이름표, 넘겨받은 요약, 전체 이름표, 지금까지 호출 수, 남은 단계 수
    돌려주는 것: (요약 원문 또는 None, 호출 수, 인용 검사로 버렸는지)
    인용 오류는 can_retry가 허락하면 같은 단계를 다시 한다 (마지막 단계는 그 전에 reset을 보낸다).
    중간 단계의 '내용 없음'(표지·그림뿐)은 조용히 건너뛰고, 마지막 단계의 실패는 그대로 올린다."""
    evidence = build_step_evidence(segment_aliases, carry)
    allowed = allowed_step_aliases(segment_aliases, carry, aliases)
    while True:
        calls += 1
        try:
            if last:
                return (yield from stream_summary(evidence, allowed)), calls, False
            return invoke_summary(evidence, allowed), calls, False
        except ChatbotError as error:
            if error.code == 'NO_EVIDENCE' and not last:
                return None, calls, False
            if error.code not in RETRYABLE_CODES:
                raise
            if can_retry(calls, remaining):
                if last:
                    yield summary_event('reset', reason='근거 확인에 실패해 다시 생성합니다.')
                continue
            if last:
                raise
            return None, calls, True


def build_step_evidence(segment_aliases, carry):
    """받는 것: 이번 구간 이름표, 넘겨받은 요약 / 돌려주는 것: LLM 입력 문자열
    이미 요약된 내용(이전 요약)과 원문 근거를 나눠 적어야 LLM이 이전 요약의 이름표를 지키며 새 내용을 더한다."""
    body = cit.format_evidence(segment_aliases)
    return f'이전 요약:\n{carry}\n\n이번 구간 근거:\n{body}' if carry else body


def allowed_step_aliases(segment_aliases, carry, aliases):
    """받는 것: 이번 구간 이름표, 넘겨받은 요약, 전체 이름표 / 돌려주는 것: 이 단계에서 인용해도 되는 이름표 사전
    이 단계의 LLM이 본 것은 이번 구간 원문과 이전 요약뿐이므로 그 밖의 이름표는 본 적 없는 청크 인용이다."""
    carried = {alias: aliases[alias] for alias in cit.extract_cited_aliases(carry) if alias in aliases}
    return {**segment_aliases, **carried}


def can_retry(calls, remaining):
    """받는 것: 지금까지 호출 수, 남은 단계 수 / 돌려주는 것: 다시 해도 총 SUMMARY_MAX_CALLS회 이하인지"""
    return calls + 1 + remaining <= SUMMARY_MAX_CALLS


def invoke_summary(evidence, allowed):
    """받는 것: LLM 입력, 허용 이름표 / 돌려주는 것: 중간 요약 원문 (인용 검사 통과)
    중간 요약은 화면에 보내지 않으므로 한 번에 받아 전체를 검사한다."""
    chain = build_summary_chain('step')
    try:
        text = chain.invoke({'evidence': evidence})
    except Exception as error:
        raise to_llm_error(error) from error
    cit.validate_citations(text, allowed, MAX_SUMMARY_CHARS)
    return text.strip()


def stream_summary(evidence, allowed):
    """받는 것: LLM 입력, 허용 이름표 / 돌려주는 것: delta 이벤트들, 끝나면 이름표가 든 최종 요약 원문
    줄이 완성될 때마다 검사하고 통과한 줄만 화면용으로 바꿔 내보낸다. 이름표 없는 문장은 화면에 나가지 않는다.
    끝나면 마지막 줄과 전체(빈 요약·길이·이름표)를 한 번 더 검사한다."""
    renderer, raw, buffer = cit.AliasLineRenderer(), '', ''
    for piece in stream_pieces(build_summary_chain('final'), evidence):
        raw += piece
        lines, buffer = split_complete_lines(buffer + piece)
        for line in lines:
            yield emit_line(line, allowed, renderer, '\n')
    if buffer:
        yield emit_line(buffer, allowed, renderer, '')
    cit.validate_citations(raw, allowed, MAX_SUMMARY_CHARS)
    return raw.strip()


def stream_pieces(chain, evidence):
    """받는 것: 체인, LLM 입력 / 돌려주는 것: 문자열 조각들
    스트리밍이 거절되면(조직 인증 필요) 한 번에 받아 조각 1개로 돌려준다.
    stream이 없는 체인(invoke만 있는 테스트 대역)도 같은 방식으로 받는다."""
    values = {'evidence': evidence}
    try:
        if not hasattr(chain, 'stream'):
            yield chain.invoke(values)
            return
        yield from chain.stream(values)
    except Exception as error:
        if not is_stream_rejected(error):
            raise to_llm_error(error) from error
        try:
            yield chain.invoke(values)
        except Exception as retry_error:
            raise to_llm_error(retry_error) from retry_error


def emit_line(line, allowed, renderer, end):
    """받는 것: 줄 1개(이름표 원문), 허용 이름표, 렌더러, 줄 끝 문자 / 돌려주는 것: delta 이벤트
    문장마다 이름표가 있는지와 준 적 없는 이름표가 없는지 확인한 뒤 [E3]을 화면 번호 [1]로 바꾼다."""
    cit.validate_citations_line(line)
    cit.check_line_aliases(line, allowed)
    return summary_event('delta', text=renderer.render(line) + end)


def split_complete_lines(buffer):
    """받는 것: 아직 검사하지 않은 글 / 돌려주는 것: (완성된 줄 목록, 줄바꿈 뒤에 남은 조각)"""
    head, sep, rest = buffer.rpartition('\n')
    return (head.split('\n'), rest) if sep else ([], buffer)


def is_stream_rejected(error):
    """받는 것: LLM 예외 / 돌려주는 것: 스트리밍 거절(조직 인증 필요) 오류면 True
    map_dependency_error에 넘기기 전에 판별해야 한다 (넘기면 LLM_FAILED·CONFIG_MISSING으로 바뀐다)."""
    return type(error).__name__ in ('BadRequestError', 'PermissionDeniedError') and 'stream' in str(error).lower()


def is_context_overflow(error):
    """받는 것: LLM 예외 / 돌려주는 것: 입력 길이 초과로 거절된 오류면 True"""
    text = str(error).lower()
    return 'context_length_exceeded' in text or 'maximum context length' in text


def to_llm_error(error):
    """받는 것: LLM 단계 예외 / 돌려주는 것: 길이 초과면 _ContextOverflow, 나머지는 map_dependency_error 결과"""
    return _ContextOverflow() if is_context_overflow(error) else map_dependency_error(error, stage='llm')


def collect_citations(raw, aliases):
    """받는 것: 요약 원문, 이름표 사전 / 돌려주는 것: (쓰인 이름표, 출처 목록, 인용 청크 목록)"""
    used = cit.extract_cited_aliases(raw)
    cited_hits = [aliases[alias] for alias in used]
    return used, [cit.build_citation(hit, number) for number, hit in enumerate(cited_hits, 1)], cited_hits


def compute_coverage(all_hits, used_hits):
    """받는 것: 문서 전체 청크, 요약에 쓴 청크 / 돌려주는 것: (포함 범위, 제외 범위, 전체 요약 여부)"""
    first, last = used_hits[0].location_label, used_hits[-1].location_label
    included = f'포함 {first}~{last}' if first != last else f'포함 {first}'
    if len(used_hits) == len(all_hits):
        return included, '', True
    return included, f'제외 {all_hits[len(used_hits)].location_label} 이후', False


def format_excluded(hits, reason):
    """받는 것: 제외한 청크(연속), 사유 / 돌려주는 것: '사유 p.7~p.10' (청크가 1개면 '사유 p.7')"""
    first, last = hits[0].location_label, hits[-1].location_label
    return f'{reason} {first}~{last}' if first != last else f'{reason} {first}'


def compute_cited_ratio(used, used_hits):
    """받는 것: 쓰인 이름표, 요약에 쓴 청크 / 돌려주는 것: 인용된 청크 비율 (소수 셋째 자리)
    긴 입력에서 일부 구간이 요약에서 빠졌는지 볼 때 쓰는 지표다."""
    return round(len(used) / len(used_hits), 3) if used_hits else 0.0


def summarize_document_response(document_id, hits, used_hits, excluded, skipped, raw, used, citations):
    """받는 것: 문서 ID, 전체·사용 청크, 예산 초과로 뺀 청크, 인용 검사로 뺀 구간, 요약 원문, 쓰인 이름표, 출처
    돌려주는 것: SummaryResponse. 뺀 것이 있으면 제외 범위에 사유를 적고 전체 요약이 아니라고 표시한다."""
    included, _, _ = compute_coverage(hits, used_hits)
    parts = ([format_excluded(excluded, '예산 초과로 제외')] if excluded else []) + \
        [format_excluded(segment, '근거 확인 실패로 제외') for segment in skipped]
    return SummaryResponse(
        document_id=document_id, version_id=used_hits[0].version_id,
        summary=cit.render_answer(raw, used)[0], citations=citations,
        included_range=included, excluded_range=' · '.join(parts), is_full=not parts,
        total_chunks=len(hits), used_chunks=len(used_hits), cited_ratio=compute_cited_ratio(used, used_hits),
    )
