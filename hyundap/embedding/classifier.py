"""embedding/classifier.py - 임베딩 때 문서 유형·회사 일치 분류 (OpenAI Decisions API)

Owner: 박영준
Spec: AI_문서검색_통합_기능명세서 (MOD-104)

수업 범위: openai SDK(OpenAI 클라이언트), 일반 파이썬(dict·list).
Decisions API(client.decisions.create)는 글 대신 보기 중 하나·확률을 돌려주는 OpenAI API다.
현재 gpt-6-luna만 지원하며 임베딩과 같은 OPENAI_API_KEY를 쓴다. 출력 토큰 요금은 없다.

문서(버전)마다 한 번만 부르고 결과를 그 문서의 모든 청크 메타데이터에 붙인다.
관리자가 고른 doc_type은 바꾸지 않는다(DB documents.doc_type과 어긋나지 않도록 auto_doc_type에 따로 둔다).
분류가 실패해도 업로드는 계속된다. 답은 정해진 보기·확률로만 오므로 본문에 섞인 지시문이 값을 바꾸지 못한다.

Functions:
    FUN-321 get_classifier_client      FUN-322 build_classify_input
    FUN-323 build_classify_questions   FUN-324 read_classify_answers
    FUN-325 classify_document
"""
from typing import Any

from openai import OpenAI

from hyundap.common.config import get_settings
from hyundap.common.schemas import DOC_TYPE_CODES, DOC_TYPES

from .models import ClassifyResult, get_api_key, get_chunk_value

# 업로드 중 한 번만 부르므로 질문 임베딩보다 길게, 재시도는 한 번만
CLASSIFY_TIMEOUT_SEC, CLASSIFY_MAX_RETRIES = 20, 1
# 분류에 보내는 본문 길이 상한(앞쪽 청크부터 채운다). 긴 문서도 호출 1번·입력 크기 일정
SAMPLE_CHARS = 6000
# 본문에 작성 회사 이름이 적혀 있을 확률이 이 값 이상일 때만 회사 일치를 판단한다
COMPANY_NAMED_MIN = 0.5
# 회사 일치 확률 기준. 이상이면 yes, 이하이면 no, 그 사이는 판단 보류(빈 문자열)
MATCH_YES_MIN, MATCH_NO_MAX = 0.7, 0.3

# 유형 코드별 판단 기준. 코드·표시 이름은 common/schemas.py의 DOC_TYPES를 그대로 쓴다
DOC_TYPE_HINTS = {
    "manual": "제품·장비·시스템의 사용법, 조작·설치·점검 방법을 단계별로 설명하는 문서",
    "policy": "사내 규정·정책·지침·기준처럼 지켜야 할 규칙과 책임을 정한 문서",
    "guide": "업무 안내·절차 소개·온보딩처럼 일을 어떻게 하는지 알려 주는 안내 문서",
    "other": "위 셋에 해당하지 않는 문서(보고서·회의록·소개 자료·언론 보도 등)",
}

# 같은 키면 OpenAI 클라이언트를 다시 만들지 않고 재사용하기 위한 보관함
_clients: dict[str, Any] = {}


# FUN-321. Decisions API를 부를 OpenAI 클라이언트. 분류가 꺼져 있거나 키가 없으면 None(분류를 건너뛴다)
def get_classifier_client():
    """FUN-321. OpenAI 클라이언트를 만들고 같은 키면 재사용한다. DOC_CLASSIFY=0이거나 키가 없으면 None."""
    api_key = get_api_key()
    if not (get_settings().doc_classify and api_key):
        return None
    if api_key not in _clients:
        _clients[api_key] = OpenAI(api_key=api_key, timeout=CLASSIFY_TIMEOUT_SEC, max_retries=CLASSIFY_MAX_RETRIES)
    return _clients[api_key]


# 청크 순번. 숫자가 아니면 맨 뒤로 보낸다
def _order(chunk) -> float:
    index = get_chunk_value(chunk, "chunk_index", 0)
    return index if isinstance(index, (int, float)) and not isinstance(index, bool) else float("inf")


# FUN-322. 파일명과 앞쪽 본문(SAMPLE_CHARS까지)으로 분류 입력 글을 만든다
def build_classify_input(chunks: list) -> str:
    """FUN-322. 앞쪽 청크부터 SAMPLE_CHARS까지 본문을 모아 파일명과 함께 입력 글을 만든다."""
    parts, used = [], 0
    for chunk in sorted(chunks, key=_order):
        text = str(get_chunk_value(chunk, "text", "") or "").strip()
        if not text:
            continue
        text = text[:SAMPLE_CHARS - used]
        parts.append(text)
        used += len(text)
        if used >= SAMPLE_CHARS:
            break
    file_name = get_chunk_value(chunks[0], "file_name", "") if chunks else ""
    return f"파일명: {file_name}\n\n본문:\n" + "\n".join(parts)


# FUN-323. Decisions API 질문 목록. 유형(choice) 1개, 업로드 회사가 있으면 회사 질문(predicate) 2개
def build_classify_questions(company_name: str) -> list[dict]:
    """FUN-323. doc_type(choice)과, 회사 이름이 있으면 company_named·company_match(predicate)를 만든다."""
    questions = [{
        "type": "choice",
        "name": "doc_type",
        "instructions": "회사 문서의 본문과 작성 목적을 보고 가장 알맞은 문서 유형 하나를 고르세요.",
        "choices": [{"value": code, "description": f"{name}: {DOC_TYPE_HINTS[code]}"} for code, name in DOC_TYPES],
    }]
    company = str(company_name or "").strip()
    if company:
        questions.append({
            "type": "predicate",
            "name": "company_named",
            "instructions": "본문이나 파일명에 회사 이름이 직접 적혀 있는가?",
        })
        questions.append({
            "type": "predicate",
            "name": "company_match",
            "instructions": f"이 문서는 '{company}'의 문서인가? 본문에 적힌 회사가 '{company}'와 같은 회사이면 참이다.",
        })
    return questions


# FUN-324. Decisions API 응답에서 유형·확신도·회사 일치를 꺼낸다. 유형 답이 없으면(거부 포함) ValueError
def read_classify_answers(decision) -> ClassifyResult:
    """FUN-324. answers를 이름별로 읽는다. 회사 일치는 company_named 확률이 COMPANY_NAMED_MIN 이상일 때만 정한다."""
    answers = {answer.name: answer for answer in (decision.answers or [])}
    doc = answers.get("doc_type")
    if doc is None or doc.type != "choice":
        raise ValueError("문서 유형 답이 없습니다(거부 응답 포함).")
    # 목록 밖 유형이면 유형·확신도를 함께 비운다(빈 유형에 확신도만 남지 않게)
    known = doc.choice in DOC_TYPE_CODES
    result = ClassifyResult(
        auto_doc_type=doc.choice if known else "",
        doc_type_confidence=round(float(doc.confidence or 0.0), 3) if known else 0.0,
        status="success",
    )
    named, match = answers.get("company_named"), answers.get("company_match")
    if named is not None and match is not None and named.type == match.type == "predicate":
        if float(named.probability or 0.0) >= COMPANY_NAMED_MIN:
            probability = float(match.probability or 0.0)
            if probability >= MATCH_YES_MIN:
                result.company_match = "yes"
            elif probability <= MATCH_NO_MAX:
                result.company_match = "no"
    return result


# FUN-325. 한 문서(버전)의 청크로 유형·회사 일치를 분류한다. 예외를 올리지 않고 status로 알려준다
def classify_document(chunks: list) -> ClassifyResult:
    """FUN-325. 분류가 꺼져 있거나 키가 없으면 skipped, 호출·응답 오류는 failed.

    업로드를 멈추지 않도록 어떤 오류도 올리지 않는다. detail에는 예외 이름만 넣는다(키 노출 방지).
    """
    if not chunks:
        return ClassifyResult()
    try:
        client = get_classifier_client()
        if client is None:
            return ClassifyResult()
        decision = client.decisions.create(
            model=get_settings().openai_classify_model,
            input=build_classify_input(chunks),
            questions=build_classify_questions(get_chunk_value(chunks[0], "company_name", "")),
        )
        return read_classify_answers(decision)
    except Exception as exc:
        return ClassifyResult(status="failed", detail=type(exc).__name__)
