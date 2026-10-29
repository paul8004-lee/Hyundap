"""chatbot/doc_classifier.py — 선택 문서의 유형과 업로드 회사 일치 여부를 OpenAI Decisions API로 자동 분류.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-127, 명세 밖 추가 기능)

입구 두 개:
- classify_document: 화면에서 선택한 문서. 청크 조회 → 접근 확인(API 호출 전) → 같은 버전이면 보관한 결과 → API 1회.
- classify_common_document: 업로드 중 수집(ingest) 정규화 직후의 문서. 업로드 권한은 이미 확인됐으므로 바로 API 1회.
  결과는 수집 보고서를 거쳐 업로드 작업 기록(index_jobs.report의 ingest)에 남는다. 관리자가 고른 doc_type은 바꾸지 않는다.
Decisions API는 글 대신 보기 중 하나·확률로 답하는 OpenAI API다(현재 gpt-6-luna만 지원, 출력 요금 없음).
답이 정해진 보기·확률로만 오므로 본문에 섞인 지시문이 결과 값을 바꾸지 못한다.

함수:
    FUN-429 classify_document — 분류 입구. 권한·조회 오류는 요약과 같이 올리고, 분류 실패는 status='failed'로 돌려준다.
        classify_hits — 청크 목록을 분류해 DocClassification을 만든다(예외를 올리지 않음).
    FUN-434 classify_common_document — 정규화된 문서(CommonDocument)를 분류한다(예외를 올리지 않음).
        run_classification — 파일명·회사명·본문 목록으로 API를 1번 부르고 결과를 채운다(두 입구 공통).
        remember / recall — 버전별 결과를 메모리에 보관하고 꺼낸다.
    FUN-430 build_classify_input — 파일명 + 앞쪽 본문(본문 목록 순서대로 CLASSIFY_SAMPLE_CHARS까지)으로 입력 글을 만든다.
    FUN-431 build_classify_questions — 유형(choice)·회사명 기재(predicate)·회사 일치(predicate) 질문을 만든다.
    FUN-432 read_classify_answers — 응답에서 유형·확신도·회사 일치를 꺼낸다.
    FUN-433 get_classify_client — Decisions API를 부를 OpenAI 클라이언트를 키마다 한 번만 만든다.
"""
import threading
from collections import OrderedDict

from hyundap.common.schemas import DOC_TYPE_CODES, DOC_TYPES

from .ask import recheck_before_return
from .constants import (CLASSIFY_CACHE_SIZE, CLASSIFY_COMPANY_NAMED_MIN, CLASSIFY_DOC_TYPE_HINTS, CLASSIFY_MATCH_NO_MAX,
                        CLASSIFY_MATCH_YES_MIN, CLASSIFY_MAX_RETRIES, CLASSIFY_MODEL, CLASSIFY_SAMPLE_CHARS,
                        CLASSIFY_TIMEOUT_SECONDS)
from .models import ChatbotError, DocClassification
from .summary import summarize_document_load

_CLIENTS = {}                 # API 키 -> OpenAI 클라이언트
_RESULTS = OrderedDict()      # version_id -> DocClassification (성공한 결과만, 오래된 것부터 지운다)
_RESULTS_LOCK = threading.Lock()


def classify_document(document_id, user):
    """받는 것: 문서 ID, 사용자 / 돌려주는 것: DocClassification
    요약과 같이 공개 청크를 읽고 접근을 확인한 뒤에만 API를 부른다. 같은 버전을 다시 부르면 보관한 결과를 쓴다.
    조회 실패는 RETRIEVAL_FAILED, 내용 없음은 NO_EVIDENCE, 권한 오류는 그대로 올린다. 분류 실패는 status='failed'."""
    hits = summarize_document_load(document_id)
    if not hits:
        raise ChatbotError('NO_EVIDENCE', '분류할 내용이 없습니다.')
    recheck_before_return(user, hits)
    cached = recall(hits[0].version_id)
    if cached is not None:
        return cached
    result = classify_hits(hits)
    if result.status == 'success':
        remember(result)
    return result


def classify_hits(hits):
    """받는 것: 한 문서(버전)의 ChunkHit 목록 / 돌려주는 것: DocClassification
    키가 없거나 호출·응답이 잘못되면 status='failed'로 돌려주고 예외를 올리지 않는다."""
    first = hits[0]
    result = DocClassification(document_id=first.document_id, version_id=first.version_id, admin_doc_type=first.doc_type)
    ordered = sorted(hits, key=lambda hit: hit.chunk_index if isinstance(hit.chunk_index, int) else float('inf'))
    return run_classification(result, first.file_name, first.company_name, [hit.text for hit in ordered])


def classify_common_document(doc):
    """받는 것: ingest 정규화가 끝난 CommonDocument / 돌려주는 것: DocClassification
    업로드 흐름 안에서 부르므로 접근 확인·메모리 보관은 하지 않는다. 어떤 오류도 올리지 않고 status='failed'로 알린다."""
    header = getattr(doc, 'header', None) or {}
    result = DocClassification(document_id=str(header.get('document_id', '')), version_id=str(header.get('version_id', '')),
                               admin_doc_type=str(header.get('doc_type', '')))
    texts = [getattr(section, 'text', '') for section in (getattr(doc, 'sections', None) or [])]
    if not any((text or '').strip() for text in texts):  # 본문이 없으면 API를 부르지 않는다
        result.status, result.detail = 'failed', 'EMPTY_DOCUMENT'
        return result
    return run_classification(result, header.get('file_name'), header.get('company_name'), texts)


def run_classification(result, file_name, company_name, texts):
    """받는 것: 채울 DocClassification, 파일명, 업로드 회사명, 본문 목록(앞에서부터) / 돌려주는 것: 같은 DocClassification
    Decisions API를 1번 부른다. 키 없음·호출 오류·거부 응답은 status='failed', detail은 예외 이름만."""
    try:
        client = get_classify_client()
        decision = client.decisions.create(
            model=CLASSIFY_MODEL,
            input=build_classify_input(file_name, texts),
            questions=build_classify_questions(company_name),
        )
        auto_doc_type, confidence, company_match = read_classify_answers(decision)
    except Exception as error:
        result.status, result.detail = 'failed', type(error).__name__
        return result
    result.auto_doc_type, result.confidence, result.company_match = auto_doc_type, confidence, company_match
    result.matches_admin_type = (auto_doc_type == result.admin_doc_type) if auto_doc_type else None
    return result


def remember(result):
    """받는 것: 성공한 DocClassification / 돌려주는 것: 없음. CLASSIFY_CACHE_SIZE를 넘으면 오래된 것부터 지운다."""
    with _RESULTS_LOCK:
        _RESULTS[result.version_id] = result
        _RESULTS.move_to_end(result.version_id)
        while len(_RESULTS) > CLASSIFY_CACHE_SIZE:
            _RESULTS.popitem(last=False)


def recall(version_id):
    """받는 것: 버전 ID / 돌려주는 것: 보관한 DocClassification 또는 None"""
    with _RESULTS_LOCK:
        return _RESULTS.get(version_id)


def build_classify_input(file_name, texts):
    """받는 것: 파일명, 본문 목록(앞에서부터) / 돌려주는 것: '파일명: …' + 앞쪽 본문(CLASSIFY_SAMPLE_CHARS까지)"""
    parts, used = [], 0
    for text in texts:
        text = (text or '').strip()[:CLASSIFY_SAMPLE_CHARS - used]
        if not text:
            continue
        parts.append(text)
        used += len(text)
        if used >= CLASSIFY_SAMPLE_CHARS:
            break
    return f'파일명: {file_name or ""}\n\n본문:\n' + '\n'.join(parts)


def build_classify_questions(company_name):
    """받는 것: 업로드 회사명 / 돌려주는 것: Decisions API 질문 목록
    doc_type(choice)은 항상, 회사명이 있으면 company_named·company_match(predicate)를 더한다."""
    questions = [{
        'type': 'choice',
        'name': 'doc_type',
        'instructions': '회사 문서의 본문과 작성 목적을 보고 가장 알맞은 문서 유형 하나를 고르세요.',
        'choices': [{'value': code, 'description': f'{name}: {CLASSIFY_DOC_TYPE_HINTS[code]}'} for code, name in DOC_TYPES],
    }]
    company = (company_name or '').strip()
    if company:
        questions.append({
            'type': 'predicate',
            'name': 'company_named',
            'instructions': '본문이나 파일명에 회사 이름이 직접 적혀 있는가?',
        })
        questions.append({
            'type': 'predicate',
            'name': 'company_match',
            'instructions': f"이 문서는 '{company}'의 문서인가? 본문에 적힌 회사가 '{company}'와 같은 회사이면 참이다.",
        })
    return questions


def read_classify_answers(decision):
    """받는 것: Decisions API 응답 / 돌려주는 것: (유형, 확신도, 회사 일치)
    유형 답이 없거나 거부면 ValueError. 목록 밖 유형은 ('', 0.0)으로 비운다.
    회사 일치는 company_named 확률이 CLASSIFY_COMPANY_NAMED_MIN 이상일 때만 'yes'·'no'로 정한다."""
    answers = {answer.name: answer for answer in (decision.answers or [])}
    doc = answers.get('doc_type')
    if doc is None or doc.type != 'choice':
        raise ValueError('문서 유형 답이 없습니다(거부 응답 포함).')
    known = doc.choice in DOC_TYPE_CODES
    auto_doc_type = doc.choice if known else ''
    confidence = round(float(doc.confidence or 0.0), 3) if known else 0.0
    company_match = ''
    named, match = answers.get('company_named'), answers.get('company_match')
    if named is not None and match is not None and named.type == match.type == 'predicate':
        if float(named.probability or 0.0) >= CLASSIFY_COMPANY_NAMED_MIN:
            probability = float(match.probability or 0.0)
            if probability >= CLASSIFY_MATCH_YES_MIN:
                company_match = 'yes'
            elif probability <= CLASSIFY_MATCH_NO_MAX:
                company_match = 'no'
    return auto_doc_type, confidence, company_match


def get_classify_client():
    """받는 것: 없음 / 돌려주는 것: OpenAI 클라이언트 (API 키는 common.config.get_settings()에서 읽는다)
    키가 없으면 ChatbotError(CONFIG_MISSING). classify_hits가 이 오류도 status='failed'로 바꾼다."""
    from openai import OpenAI
    from hyundap.common.config import get_settings
    api_key = (get_settings().openai_api_key or '').strip()
    if not api_key:
        raise ChatbotError('CONFIG_MISSING')
    if api_key not in _CLIENTS:
        _CLIENTS[api_key] = OpenAI(api_key=api_key, timeout=CLASSIFY_TIMEOUT_SECONDS, max_retries=CLASSIFY_MAX_RETRIES)
    return _CLIENTS[api_key]
