"""chatbot/summary.py — 선택 문서의 청크를 나눠 요약하고 포함 범위를 계산.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-124)

함수:
    FUN-420 summarize_document — 선택 문서 요약 입구 (이름 미정 D10, 제안 이름으로 구현)
        summarize_document_load — 문서 청크를 받아 ChunkHit 목록으로 바꾼다.
        summarize_document_to_hit — VectorHit 1건을 ChunkHit로 바꾼다.
        summarize_document_partials — 8개씩 부분 요약하고 실패한 묶음을 가려낸다.
        summarize_document_empty_error — 부분 요약이 없을 때 원인(근거 확인 실패·내용 없음)에 맞는 오류를 만든다.
        summarize_document_response — 포함·제외 범위를 붙여 SummaryResponse를 만든다.
    FUN-421 summarize_batch — 청크 묶음별 중간 요약을 만든다.
    FUN-422 merge_summaries — 중간 요약을 하나의 문서 요약으로 합친다.
        merge_summaries_group — 부분 요약 묶음 1개를 합치고 인용을 검사한다.
        merge_summaries_allowed — 묶음에 나온 이름표만 고른다.
    FUN-423 compute_coverage — 전체 청크 중 요약에 포함한 범위를 계산한다.
    FUN-424 limit_summary_chunks — 요약 입력 상한과 제외 범위를 정한다.
"""
from . import citations as cit
from .ask import recheck_before_return
from .constants import MAX_SUMMARY_CHUNKS, SUMMARY_BATCH_SIZE
from .models import ChatbotError, SummaryResponse, map_dependency_error
from .prompts import build_summary_chain


def summarize_document(document_id, user):
    """받는 것: 문서 ID, 사용자 / 돌려주는 것: SummaryResponse
    공개 청크 조회 → ChunkHit 변환 → 문서·버전 접근 확인 → 상한 → 8개씩 부분 요약 → 병합 → 범위 → 출처 → 재검증.
    접근 확인은 auth가 (문서ID, 버전ID) 쌍을 받으므로 청크를 받은 뒤 실제 버전으로 한다. 확인 전에는 LLM을 부르지 않는다."""
    hits = summarize_document_load(document_id)
    if not hits:
        raise ChatbotError('NO_EVIDENCE', '요약할 내용이 없습니다.')
    recheck_before_return(user, hits)
    used_hits, _ = limit_summary_chunks(hits)
    aliases = cit.assign_aliases(used_hits)
    partials, skipped = summarize_document_partials(aliases)
    if not partials:  # 묶음이 전부 인용 검사에 걸렸으면 내용이 없는 게 아니라 근거 확인 실패다
        raise summarize_document_empty_error(skipped)
    summary = merge_summaries(partials, aliases)
    used = cit.extract_cited_aliases(summary)
    cited_hits = [aliases[alias] for alias in used]
    citations = [cit.build_citation(hit, number) for number, hit in enumerate(cited_hits, 1)]
    recheck_before_return(user, cited_hits)
    return summarize_document_response(document_id, hits, used_hits, skipped, summary, used, citations)


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


def summarize_document_partials(aliases):
    """받는 것: 요약에 쓸 이름표 사전 / 돌려주는 것: (부분 요약 목록, 인용 검사에 걸려 뺀 묶음 목록)
    8개씩 부분 요약한다. 표지·그림뿐인 묶음(NO_EVIDENCE)은 그냥 건너뛰고,
    이름표 없는 문장이 섞인 묶음(INVALID_CITATION)은 빼되 제외 범위에 표시하도록 돌려준다."""
    keys, chain = list(aliases), build_summary_chain('partial')
    partials, skipped = [], []
    for i in range(0, len(keys), SUMMARY_BATCH_SIZE):
        batch = {k: aliases[k] for k in keys[i:i + SUMMARY_BATCH_SIZE]}
        try:
            partials.append(summarize_batch(batch, chain)[0])
        except ChatbotError as error:
            if error.code not in ('NO_EVIDENCE', 'INVALID_CITATION'):
                raise
            if error.code == 'INVALID_CITATION':
                skipped.append(list(batch.values()))
    return partials, skipped


def summarize_document_empty_error(skipped):
    """받는 것: 인용 검사에 걸려 뺀 묶음 목록 / 돌려주는 것: 부분 요약이 하나도 없을 때 올릴 ChatbotError
    뺀 묶음이 있으면 근거 확인 실패(INVALID_CITATION), 없으면 표지·그림뿐이라 요약할 내용 없음(NO_EVIDENCE)."""
    if skipped:
        return ChatbotError('INVALID_CITATION', '문서 근거를 확인할 수 있는 요약을 만들지 못했습니다. 다시 시도해 주세요.')
    return ChatbotError('NO_EVIDENCE', '요약할 내용이 없습니다.')


def summarize_document_response(document_id, hits, used_hits, skipped, summary, used, citations):
    """받는 것: 문서 ID, 전체·사용 청크, 뺀 묶음, 요약 원문, 쓰인 이름표, 출처 / 돌려주는 것: SummaryResponse
    인용 검사에 걸려 뺀 묶음이 있으면 제외 범위에 '근거 확인 실패로 제외 p.9~p.16'을 덧붙이고 전체 요약이 아니라고 표시한다."""
    included, excluded, is_full = compute_coverage(hits, used_hits)
    if skipped:
        ranges = ', '.join(f'{b[0].location_label}~{b[-1].location_label}' for b in skipped)
        excluded = ' · '.join(filter(None, [excluded, f'근거 확인 실패로 제외 {ranges}']))
        is_full = False
    return SummaryResponse(
        document_id=document_id, version_id=used_hits[0].version_id,
        summary=cit.render_answer(summary, used)[0], citations=citations,
        included_range=included, excluded_range=excluded, is_full=is_full,
        total_chunks=len(hits), used_chunks=len(used_hits),
    )


def summarize_batch(batch_aliases, chain=None):
    """받는 것: 이 묶음의 이름표 사전(최대 8개), 부분 요약 체인(없으면 새로 만듦)
    돌려주는 것: (부분 요약, 쓰인 이름표)"""
    chain = chain or build_summary_chain('partial')
    try:
        text = chain.invoke({'evidence': cit.format_evidence(batch_aliases)})
    except Exception as error:
        raise map_dependency_error(error, stage='llm') from error
    return text.strip(), cit.validate_citations(text, batch_aliases)


def merge_summaries(partials, aliases, batch_size=SUMMARY_BATCH_SIZE):
    """받는 것: 부분 요약 목록, 문서 전체 이름표 / 돌려주는 것: 최종 요약 1개
    8개씩 묶어 합치는 것을 하나가 남을 때까지 반복하고, 단계마다 인용을 검사한다.
    병합 결과는 입력 부분 요약에 실제로 나온 이름표만 쓸 수 있다 (LLM이 본 적 없는 청크 인용 차단)."""
    chain = None
    while len(partials) > 1:
        chain = chain or build_summary_chain('merge')
        partials = [merge_summaries_group(partials[i:i + batch_size], aliases, chain)
                    for i in range(0, len(partials), batch_size)]
    return partials[0]


def merge_summaries_group(group, aliases, chain):
    """받는 것: 부분 요약 묶음, 문서 전체 이름표, 병합 체인 / 돌려주는 것: 합친 요약 1개
    병합 결과는 이 묶음에 실제로 나온 이름표만 쓸 수 있다."""
    allowed = merge_summaries_allowed(group, aliases)
    try:
        text = chain.invoke({'evidence': '\n\n'.join(group)})
    except Exception as error:
        raise map_dependency_error(error, stage='llm') from error
    cit.validate_citations(text, allowed)
    return text.strip()


def merge_summaries_allowed(group, aliases):
    """받는 것: 부분 요약 묶음, 문서 전체 이름표 / 돌려주는 것: 묶음에 나온 이름표만 담은 사전"""
    return {a: aliases[a] for part in group for a in cit.extract_cited_aliases(part) if a in aliases}


def compute_coverage(all_hits, used_hits):
    """받는 것: 문서 전체 청크, 요약에 쓴 청크 / 돌려주는 것: (포함 범위, 제외 범위, 전체 요약 여부)"""
    first, last = used_hits[0].location_label, used_hits[-1].location_label
    included = f'포함 {first}~{last}' if first != last else f'포함 {first}'
    if len(used_hits) == len(all_hits):
        return included, '', True
    return included, f'제외 {all_hits[len(used_hits)].location_label} 이후', False


def limit_summary_chunks(hits):
    """받는 것: 청크 목록 / 돌려주는 것: (앞에서부터 MAX_SUMMARY_CHUNKS(64)개, 나머지)"""
    return hits[:MAX_SUMMARY_CHUNKS], hits[MAX_SUMMARY_CHUNKS:]

