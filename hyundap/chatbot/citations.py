"""chatbot/citations.py — 인용 번호를 검증하고 원본 출처를 구성.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-123)

함수:
    FUN-411 assign_aliases — 근거마다 인용용 이름표를 부여한다.
    FUN-412 format_evidence — 이름표와 청크를 프롬프트용 근거 문자열로 만든다.
    FUN-413 extract_cited_aliases — 답변에서 사용된 인용 이름표를 추출한다.
    FUN-414 validate_citations — 인용 누락이나 제공하지 않은 이름표 사용을 검사한다.
        validate_citations_answer — 빈 답변·너무 긴 답변을 거부한다.
        validate_citations_aliases — 이름표 유무와 준 적 없는 이름표를 검사한다.
        validate_citations_line — 줄 1개의 문장마다 이름표가 있는지 검사한다.
        validate_citations_heading — 줄 전체가 마크다운 제목('### A사'·'**A사**')인지 판정한다.
        check_line_aliases — 줄 1개에 준 적 없는 이름표가 있는지 검사한다 (요약 스트리밍용).
        validate_citations_split — 줄을 문장으로 나눈다.
        validate_citations_exempt — 이름표 검사를 통과하는 문장인지 판정한다.
    FUN-415 build_citation — 원본 문서·버전·위치에 연결되는 출처를 만든다.
    FUN-416 make_excerpt — 화면 표시 길이에 맞춰 본문 발췌를 만든다.
    FUN-417 format_source_label — 회사·파일·위치를 읽기 쉬운 출처 문구로 만든다.
    FUN-418 render_answer — 내부 인용 표기를 화면용 답변으로 바꾼다.
    FUN-419 group_citations_by_company — 출처를 회사별로 묶는다.

자료형:
    AliasLineRenderer — 검사를 통과한 줄의 [E3]을 등장 순 번호로 바꾼다 (요약 스트리밍용, render_answer와 같은 번호 규칙).

근거(hit)는 search.models.ChunkHit 형식이다 (chunk_id, document_id, version_id, version_no,
company_id, company_name, doc_type, file_name, section_no, location_label, chunk_index, text, score).
"""
import re

from .constants import EXCERPT_MAX_CHARS, MAX_ANSWER_CHARS, NO_EVIDENCE_HINTS
from .models import ChatbotError, Citation

ALIAS_PATTERN = re.compile(r'\[(E\d+)\]')
# 숫자 바로 뒤 마침표('1. 항목'·'2026. 11. 3.')에서는 문장을 자르지 않는다
SENTENCE_END = re.compile(r'(?<=\D[.!?])\s+')
# 문장 앞에 온 이름표. '…합니다. [E1]'·'…합니다. [E1] 다음 문장'처럼 마침표 뒤에 따로 붙은 이름표를 찾는다
LEADING_ALIASES = re.compile(r'(?:\[E\d+\]\s*)+[.!?]?\s*')
# 줄 전체가 마크다운 제목인 경우: '### 제목' / '**제목**' / '__제목__' (뒤에 ':' 하나는 허용)
HEADING_LINE = re.compile(r'#{1,6}\s+(.+?)\s*:?|\*\*([^*]+)\*\*\s*:?|__([^_]+)__\s*:?')
HEADING_MAX_CHARS = 40
# 제목이 이 끝말로 끝나면 문장으로 본다. '개요'·'중요'처럼 '요'로 끝나는 명사는 통과시킨다
HEADING_SENTENCE_ENDINGS = ('.', '!', '?', '다', '니다', '세요', '해요', '어요', '아요', '에요', '예요', '네요', '죠')
# version_id는 필수로 두지 않는다: 공유 DB는 아직 스키마 v3라 문서 버전이 없어 빈 값이 올 수 있다 (v4 적용 후 재검토)
REQUIRED_FIELDS = ('company_name', 'file_name', 'document_id', 'location_label')


def assign_aliases(hits):
    """받는 것: 근거 목록 / 돌려주는 것: {'E1': 근거1, 'E2': 근거2, …} (순서 유지)"""
    return {f'E{i}': hit for i, hit in enumerate(hits, 1)}


def format_evidence(aliases):
    """받는 것: 이름표 사전 / 돌려주는 것: LLM에 넘길 근거 문자열('[E1] 회사 · 파일 · 위치' + 본문)"""
    return '\n\n'.join(f'[{alias}] {format_source_label(hit)}\n{hit.text}' for alias, hit in aliases.items())


def extract_cited_aliases(answer):
    """받는 것: 답변 / 돌려주는 것: 답변에 나온 [E번호]를 처음 나온 순서대로 중복 없이"""
    return list(dict.fromkeys(ALIAS_PATTERN.findall(answer)))


def validate_citations(answer, aliases, max_chars=MAX_ANSWER_CHARS):
    """받는 것: 답변, 이름표 사전 / 돌려주는 것: 실제로 쓰인 이름표 목록
    빈 답변·너무 긴 답변·준 적 없는 이름표·이름표 없는 문장이 있는 답변은 거부한다.
    제목 줄('A사:' 등)과 '확인할 수 없습니다' 같은 유보 문장은 이름표가 없어도 된다.
    번호 목록('1. …')·날짜('2026. 11. 3.')의 마침표에서는 문장을 자르지 않고,
    마침표 뒤에 붙은 이름표('…합니다. [E1]')는 바로 앞 문장의 근거로 인정한다."""
    answer = validate_citations_answer(answer, max_chars)
    used = validate_citations_aliases(answer, aliases)
    for line in answer.splitlines():
        validate_citations_line(line)
    return used


def validate_citations_answer(answer, max_chars):
    """받는 것: 답변, 최대 글자 수 / 돌려주는 것: 앞뒤 공백을 지운 답변 (비었거나 너무 길면 INVALID_ANSWER)"""
    answer = (answer or '').strip()
    if not answer or len(answer) > max_chars:
        raise ChatbotError('INVALID_ANSWER')
    return answer


def validate_citations_aliases(answer, aliases):
    """받는 것: 답변, 이름표 사전 / 돌려주는 것: 쓰인 이름표 목록
    이름표가 하나도 없으면 유보 답변(NO_EVIDENCE)인지 인용 누락(INVALID_CITATION)인지 가르고,
    준 적 없는 이름표를 쓰면 INVALID_CITATION."""
    used = extract_cited_aliases(answer)
    if not used:
        # LLM이 근거에서 답을 못 찾고 유보 문구만 쓴 경우는 근거 없음으로 처리한다
        if any(hint in answer for hint in NO_EVIDENCE_HINTS):
            raise ChatbotError('NO_EVIDENCE')
        raise ChatbotError('INVALID_CITATION')
    if any(alias not in aliases for alias in used):
        raise ChatbotError('INVALID_CITATION')
    return used


def validate_citations_line(line):
    """받는 것: 답변 1줄 / 돌려주는 것: 없음 (이름표 없이 끝난 문장이 있으면 INVALID_CITATION)
    문장 앞에 온 이름표('… . [E1] 다음 문장')는 앞에 이름표 없이 끝난 문장이 있으면 그 문장의 근거로 본다.
    다음 줄의 이름표로는 앞 줄 문장을 인정하지 않는다.
    '### A사'·'**A사**' 같은 마크다운 제목 줄은 이름표가 없어도 된다 (validate_citations_heading)."""
    if validate_citations_heading(line):
        return
    uncited = False  # 이름표 없이 끝난 문장이 남아 있는지
    for sentence in validate_citations_split(line):
        lead = LEADING_ALIASES.match(sentence)
        if lead and (uncited or not sentence[lead.end():]):
            uncited = False
            sentence = sentence[lead.end():]
        if uncited:
            raise ChatbotError('INVALID_CITATION')
        uncited = not validate_citations_exempt(sentence)
    if uncited:
        raise ChatbotError('INVALID_CITATION')


def validate_citations_heading(line):
    """받는 것: 답변 1줄 / 돌려주는 것: 마크다운 제목 줄이면 True
    '### 현대경제연구원', '**현대경제연구원**', '__A사__:'처럼 줄 전체가 제목인 경우만 본다.
    문장처럼 끝나거나(HEADING_SENTENCE_ENDINGS: . ! ? 다 니다 세요 해요 …) HEADING_MAX_CHARS(40자)를 넘으면 제목으로 보지 않아,
    ('개요'·'중요'·'필요'처럼 '요'로 끝나는 명사 제목은 통과한다)
    굵게 쓴 무인용 문장('**기한은 6 업무시간입니다**')이 검사를 빠져나가지 못한다."""
    match = HEADING_LINE.fullmatch(line.strip())
    if not match:
        return False
    title = (match.group(1) or match.group(2) or match.group(3)).strip()
    return (0 < len(title) <= HEADING_MAX_CHARS and not title.endswith(HEADING_SENTENCE_ENDINGS)
            and not ALIAS_PATTERN.search(title))


def validate_citations_split(line):
    """받는 것: 답변 1줄 / 돌려주는 것: 문장 목록 (목록 기호 '-*#'와 공백을 지움)
    숫자 바로 뒤 마침표('1. 항목'·'2026. 11. 3.')에서는 자르지 않는다."""
    return [sentence.strip(' -*#') for sentence in SENTENCE_END.split(line.strip())]


def validate_citations_exempt(sentence):
    """받는 것: 문장 1개 / 돌려주는 것: 이름표 검사를 통과하면 True
    이름표가 있는 문장, 빈 문장, ':'로 끝나는 제목 줄, 유보 표현이 든 문장이 해당한다."""
    return (not sentence or sentence.endswith(':') or bool(ALIAS_PATTERN.search(sentence))
            or any(hint in sentence for hint in NO_EVIDENCE_HINTS))


def build_citation(hit, number):
    """받는 것: 근거 1건(ChunkHit), 화면 번호 / 돌려주는 것: Citation"""
    if any(not getattr(hit, name, None) for name in REQUIRED_FIELDS):
        raise ChatbotError('INVALID_CITATION', '출처 정보가 부족한 근거가 있습니다.')
    return Citation(
        number=number, chunk_id=hit.chunk_id, document_id=hit.document_id, version_id=hit.version_id,
        version_no=getattr(hit, 'version_no', None), company_id=getattr(hit, 'company_id', ''),
        company_name=hit.company_name, file_name=hit.file_name, doc_type=getattr(hit, 'doc_type', ''),
        location_label=hit.location_label, excerpt=make_excerpt(hit.text), score=getattr(hit, 'score', None),
    )


def make_excerpt(text, max_chars=EXCERPT_MAX_CHARS):
    """받는 것: 청크 본문 / 돌려주는 것: 화면용 발췌문 (공백 정리, 최대 max_chars자, 넘으면 '…')"""
    text = ' '.join((text or '').split())
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    if ' ' in cut:
        cut = cut.rsplit(' ', 1)[0]
    return cut + '…'


def format_source_label(hit):
    """받는 것: 근거 1건 / 돌려주는 것: '회사 · 파일 · 위치' 문구 (빈 값은 건너뜀)"""
    parts = (getattr(hit, 'company_name', ''), getattr(hit, 'file_name', ''), getattr(hit, 'location_label', ''))
    return ' · '.join(part for part in parts if part)


def render_answer(answer, used):
    """받는 것: 답변, 쓰인 이름표(등장 순) / 돌려주는 것: ([E3]을 [1]로 바꾼 답변, {이름표: 번호})
    정규식으로 이름표 단위로 바꾸므로 E1과 E10이 섞이지 않는다."""
    numbers = {alias: i for i, alias in enumerate(used, 1)}
    return ALIAS_PATTERN.sub(lambda m: f'[{numbers[m.group(1)]}]', answer.strip()), numbers


def group_citations_by_company(citations):
    """받는 것: 출처 목록 / 돌려주는 것: {회사명: [출처, …]} (처음 나온 회사 순)"""
    groups = {}
    for citation in citations:
        groups.setdefault(citation.company_name, []).append(citation)
    return groups


def check_line_aliases(line, aliases):
    """받는 것: 답변 1줄, 허용 이름표 / 돌려주는 것: 없음 (준 적 없는 이름표가 있으면 INVALID_CITATION)"""
    if any(alias not in aliases for alias in ALIAS_PATTERN.findall(line)):
        raise ChatbotError('INVALID_CITATION')


class AliasLineRenderer:
    """검사를 통과한 줄의 [E12]를 처음 나온 순서대로 [1]로 바꾼다 (render_answer와 같은 번호 규칙)."""

    def __init__(self):
        self.numbers = {}

    def render(self, line):
        return ALIAS_PATTERN.sub(lambda m: f'[{self.numbers.setdefault(m.group(1), len(self.numbers) + 1)}]', line)
