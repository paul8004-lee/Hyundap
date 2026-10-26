"""chatbot/summary.py — 선택 문서의 청크를 나눠 요약하고 포함 범위를 계산.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-124)

함수:
    FUN-420 summarize_document — 선택 문서 요약 입구 (이름 미정 D10, 제안 이름으로 구현)
    FUN-421 summarize_batch — 청크 묶음별 중간 요약을 만든다.
    FUN-422 merge_summaries — 중간 요약을 하나의 문서 요약으로 합친다.
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
    from hyundap.embedding.vector_store import get_document_chunks
    from hyundap.search.models import ChunkHit
    try:
        vector_hits = list(get_document_chunks(document_id))
    except Exception as error:
        raise map_dependency_error(error, stage='summary') from error
    # 질문 경로와 같은 근거 형식(ChunkHit)으로 맞춘다. version_no·score는 요약 경로에 없어 비운다. 형식 오류는 감추지 않는다
    hits = [
        ChunkHit(
            chunk_id=hit.chunk_id, document_id=hit.metadata['document_id'], version_id=hit.metadata['version_id'],
            version_no=None, company_id=hit.metadata['company_id'], company_name=hit.metadata['company_name'],
            doc_type=hit.metadata['doc_type'], file_name=hit.metadata['file_name'],
            section_no=hit.metadata.get('section_no'), location_label=hit.metadata['location_label'],
            chunk_index=hit.metadata['chunk_index'], text=hit.text, score=None,
        )
        for hit in vector_hits
    ]
    if not hits:
        raise ChatbotError('NO_EVIDENCE', '요약할 내용이 없습니다.')
    recheck_before_return(user, hits)
    used_hits, _ = limit_summary_chunks(hits)
    aliases = cit.assign_aliases(used_hits)
    keys = list(aliases)
    partial_chain = build_summary_chain('partial')
    partials = []
    for i in range(0, len(keys), SUMMARY_BATCH_SIZE):
        try:
            partials.append(summarize_batch({k: aliases[k] for k in keys[i:i + SUMMARY_BATCH_SIZE]}, partial_chain)[0])
        except ChatbotError as error:
            if error.code != 'NO_EVIDENCE':  # 표지·그림뿐인 묶음은 건너뛰고 나머지로 요약한다
                raise
    if not partials:
        raise ChatbotError('NO_EVIDENCE', '요약할 내용이 없습니다.')
    summary = merge_summaries(partials, aliases)
    used = cit.extract_cited_aliases(summary)
    cited_hits = [aliases[alias] for alias in used]
    citations = [cit.build_citation(hit, number) for number, hit in enumerate(cited_hits, 1)]
    recheck_before_return(user, cited_hits)
    included, excluded, is_full = compute_coverage(hits, used_hits)
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
        merged = []
        for i in range(0, len(partials), batch_size):
            group = partials[i:i + batch_size]
            allowed = {a: aliases[a] for part in group for a in cit.extract_cited_aliases(part) if a in aliases}
            try:
                text = chain.invoke({'evidence': '\n\n'.join(group)})
            except Exception as error:
                raise map_dependency_error(error, stage='llm') from error
            cit.validate_citations(text, allowed)
            merged.append(text.strip())
        partials = merged
    return partials[0]


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

