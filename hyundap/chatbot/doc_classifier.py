"""chatbot/doc_classifier.py — 업로드 문서의 유형과 업로드 회사 일치 여부를 OpenAI Decisions API로 자동 분류.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-127, 명세 밖 추가 기능)

업로드 중 수집(ingest)이 정규화 직후의 문서로 부른다. 업로드 권한은 DB가 먼저 확인하므로 여기서는 확인하지 않는다.
- classify_common_document: 정규화된 문서를 API 1회로 분류한다. 결과는 수집 보고서를 거쳐 업로드 작업 기록(index_jobs.report의 ingest)에 남는다.
- decide_doc_type: 자동 분류 업로드(#261)에서 분류 결과로 실제 저장할 유형을 정한다.
  확신도가 기준 미만이거나 분류에 실패하면 현재 유형(새 문서는 임시값 other, 새 버전은 기존 유형)을 유지하고 사유 코드를 남긴다.
Decisions API는 글 대신 보기 중 하나·확률로 답하는 OpenAI API다(현재 gpt-6-luna만 지원, 출력 요금 없음).
답이 정해진 보기·확률로만 오므로 본문에 섞인 지시문이 답의 형식을 바꾸지는 못한다. 다만 본문 내용에 따라 고르는 유형은
달라질 수 있다(자동 분류 업로드에서는 그 문서 자신의 저장 유형에만 영향). 업로드 회사·권한은 분류 결과로 바꾸지 않는다.

함수:
    FUN-434 classify_common_document — 정규화된 문서(CommonDocument)를 분류한다(예외를 올리지 않음).
        run_classification — 파일명·회사명·본문 목록으로 API를 1번 부르고 결과를 채운다.
    FUN-435 decide_doc_type — 분류 결과와 현재 유형으로 (최종 유형, 대체 저장 여부, 사유 코드)를 정한다(예외를 올리지 않음).
    FUN-430 build_classify_input — 파일명 + 앞쪽 본문(본문 목록 순서대로 CLASSIFY_SAMPLE_CHARS까지)으로 입력 글을 만든다.
    FUN-431 build_classify_questions — 유형(choice)·회사명 기재(predicate)·회사 일치(predicate) 질문을 만든다.
    FUN-432 read_classify_answers — 응답에서 유형·확신도·회사 일치를 꺼낸다.
    FUN-433 get_classify_client — Decisions API를 부를 OpenAI 클라이언트를 키마다 한 번만 만든다.
"""
import math
from collections.abc import Mapping

from hyundap.common.schemas import DEFAULT_DOC_TYPE, DOC_TYPE_CODES, DOC_TYPES

from .constants import (CLASSIFY_APPLY_MIN_CONFIDENCE, CLASSIFY_COMPANY_NAMED_MIN, CLASSIFY_DOC_TYPE_HINTS,
                        CLASSIFY_FALLBACK_FAILED, CLASSIFY_FALLBACK_LOW_CONFIDENCE, CLASSIFY_MATCH_NO_MAX,
                        CLASSIFY_MATCH_YES_MIN, CLASSIFY_MAX_RETRIES, CLASSIFY_MODEL, CLASSIFY_SAMPLE_CHARS,
                        CLASSIFY_TIMEOUT_SECONDS)
from .models import ChatbotError, DocClassification

_CLIENTS = {}                 # API 키 -> OpenAI 클라이언트


def classify_common_document(doc):
    """받는 것: ingest 정규화가 끝난 CommonDocument / 돌려주는 것: DocClassification
    업로드 흐름 안에서 부르므로 접근 확인은 하지 않는다(업로드 권한은 DB가 먼저 확인). 어떤 오류도 올리지 않고 status='failed'로 알린다."""
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
        # 원인은 예외 이름만(챗봇 오류는 코드, 예: 키 없음 → CONFIG_MISSING). 오류 문구에는 키 일부가 섞일 수 있다
        result.status = 'failed'
        result.detail = error.code if isinstance(error, ChatbotError) else type(error).__name__
        return result
    result.auto_doc_type, result.confidence, result.company_match = auto_doc_type, confidence, company_match
    result.matches_admin_type = (auto_doc_type == result.admin_doc_type) if auto_doc_type else None
    return result


def decide_doc_type(classification, current_doc_type):
    """받는 것: 분류 결과(DocClassification 또는 수집 보고서 dict — classify_status·auto_doc_type·doc_type_confidence),
    현재 유형(새 문서는 임시값 'other', 새 버전은 기존 유형) / 돌려주는 것: (doc_type, doc_type_fallback, doc_type_fallback_reason)
    분류 성공·목록 안 유형·확신도 CLASSIFY_APPLY_MIN_CONFIDENCE 이상이면 AI 유형을 쓴다(AI가 'other'라고 해도 대체 저장이 아니다).
    유형은 나왔지만 확신도가 모자라면 LOW_CONFIDENCE, 그 밖(분류 실패·빈 유형·잘못된 값)은 CLASSIFY_FAILED로 현재 유형을 유지한다.
    현재 유형이 목록 밖이면 DEFAULT_DOC_TYPE('other')을 쓴다. 예외를 올리지 않는다."""
    if isinstance(classification, DocClassification):
        status, auto_doc_type, confidence = classification.status, classification.auto_doc_type, classification.confidence
    elif isinstance(classification, Mapping):
        status = classification.get('classify_status')
        auto_doc_type, confidence = classification.get('auto_doc_type'), classification.get('doc_type_confidence')
    else:
        status = auto_doc_type = confidence = None
    keep = current_doc_type if isinstance(current_doc_type, str) and current_doc_type in DOC_TYPE_CODES else DEFAULT_DOC_TYPE
    # bool은 숫자로 받지 않고 0~1 사이의 유한한 값만 확신도로 인정한다 (jobs_repo 검사와 같은 기준)
    valid = (status == 'success' and isinstance(auto_doc_type, str) and auto_doc_type in DOC_TYPE_CODES
             and type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1)
    if not valid:
        return keep, True, CLASSIFY_FALLBACK_FAILED
    if confidence < CLASSIFY_APPLY_MIN_CONFIDENCE:
        return keep, True, CLASSIFY_FALLBACK_LOW_CONFIDENCE
    return auto_doc_type, False, None


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
    키가 없으면 ChatbotError(CONFIG_MISSING). run_classification이 이 오류도 status='failed'로 바꾼다."""
    from openai import OpenAI
    from hyundap.common.config import get_settings
    api_key = (get_settings().openai_api_key or '').strip()
    if not api_key:
        raise ChatbotError('CONFIG_MISSING')
    if api_key not in _CLIENTS:
        _CLIENTS[api_key] = OpenAI(api_key=api_key, timeout=CLASSIFY_TIMEOUT_SECONDS, max_retries=CLASSIFY_MAX_RETRIES)
    return _CLIENTS[api_key]
