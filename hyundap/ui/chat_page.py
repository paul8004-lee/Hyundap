"""질문·답변·출처 화면 — UI 담당: 박경환.

기준 명세: AI_문서검색_통합_기능명세서 (MOD-034).
B06/B07/B13/B14/B22/B23: 현재 질문과 그 답변의 출처만 표시한다.
질문은 검색창에서 입력하며 app.py와 controller.py가 챗봇 담당 함수에 연결한다.
짧은 답변은 검색창 아래에, 출처는 기존 문서 상세 영역에 표시한다.
요약(FUN-166 on_summary_request)은 검색 화면의 선택 문서 상세에 있으며 이곳에 중복하지 않는다.
백엔드의 답변 번호·문서/버전 식별자를 유지하고 대화 이력(P3)은 만들지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from html import escape
import re
from typing import Any

from .services import DOC_TYPES, UIServiceError


CHAT_CSS = """
.hd-citation { border: 1px solid #dfe4ed; background: #f7f9fc; border-radius: 12px; padding: 16px; overflow-wrap: anywhere; }
.hd-citation-title { display: flex; align-items: baseline; gap: 8px; color: #19212b; font-size: 14px; line-height: 22px; font-weight: 700; }
.hd-citation-number { color: var(--hd-page-accent, #062e5e); white-space: nowrap; }
.hd-citation-meta { display: flex; flex-wrap: wrap; gap: 6px 12px; color: #667389; font-size: 12px; line-height: 20px; margin-top: 6px; }
.hd-citation blockquote { margin: 12px 0 0; padding: 0 0 0 12px; border-left: 3px solid #bdcde1; color: #39465a; font-size: 13px; line-height: 1.8; white-space: pre-wrap; }
.hd-citation-empty { font-size: 13px; line-height: 21px; color: #667389; margin: 0; }
.hd-citation-list { display: grid; gap: 12px; }
"""


def _mapping(value: Any) -> Mapping[str, Any]:
    """Allow UI view dictionaries and backend dataclasses without changing contracts."""
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if callable(getattr(value, "model_dump", None)):
        return value.model_dump()
    return {}


def render_answer(response: Any) -> str:
    """B13: preserve the backend answer and its citation numbering exactly.

    render_compact_answer가 이 문자열을 짧게 가공하고 HTML 이스케이프한다.
    출처 메타정보는 같은 응답에서 controller가 기존 상세·모달에 연결한다.
    """
    if not response:
        return ""
    if isinstance(response, str):
        return response
    data = _mapping(response)
    answer = data.get("answer")
    if answer:
        return str(answer)
    if str(data.get("status", "")).upper() == "NO_EVIDENCE":
        return str(data.get("message") or "이 질문에 답할 근거를 찾지 못했어요. 질문이나 검색 범위를 바꿔 주세요.")
    return ""


def checked_answer_citations(response: Any) -> list[dict]:
    """#188: 표시 전에 답변의 고유 인용 번호와 실제 출처를 대조한다.

    반복 인용은 허용한다. 누락된 출처를 추측하거나 번호를 다시 매기지
    않는다. 짧은 답변에서 번호를 숨기더라도 원본 응답을 먼저 검사한다.
    """
    data = _mapping(response)
    if str(data.get("status", "")).upper() != "ANSWERED":
        return []
    answer, citations = data.get("answer"), data.get("citations")
    invalid = UIServiceError(
        "INVALID_CITATION",
        "답변과 출처 정보가 일치하지 않아 답변을 표시하지 못했어요. 다시 검색해 주세요.",
        "failed",
    )
    if not isinstance(answer, str) or not isinstance(citations, list) or not citations:
        raise invalid
    sources, numbers = [], set()
    for citation in citations:
        source = dict(_mapping(citation))
        number = source.get("number")
        if (type(number) is not int or number < 1 or number in numbers
                or not source.get("document_id") or not source.get("version_id")):
            raise invalid
        numbers.add(number)
        sources.append(source)
    cited = {int(number) for group in re.findall(r"\[([0-9]+(?:\s*[,，]\s*[0-9]+)*)\]", answer)
             for number in re.split(r"\s*[,，]\s*", group)}
    if cited != numbers or re.search(r"\[E[0-9]+\]", answer):
        raise invalid
    return sources


def render_compact_answer(response: Any) -> str:
    """B13: 백엔드 답변을 보존하고 화면에서 최대 두 줄을 표시한다.

    출처 번호는 표시하지 않으며 원문 출처 정보는 controller가 별도로 보관한다.
    """
    data = _mapping(response)
    if str(data.get("status", "")).upper() == "NO_EVIDENCE":
        text = "답변할 근거를 찾지 못했어요."
    else:
        text = render_answer(response).strip()
        # 숫자 인용 링크/번호만 제거한다. 금액·기간 등의 일반 숫자는 유지한다.
        text = re.sub(r"\[\d+(?:\s*[,，]\s*\d+)*\]\([^)]*\)", "", text)
        text = re.sub(r"\[\d+(?:\s*[,，]\s*\d+)*\]", "", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)", "", text)
        text = text.replace("**", "").replace("__", "").replace("`", "")
        text = re.sub(r"\s+", " ", text).strip()
        text = text or "답변을 제공받지 못했어요."
    return (
        '<div class="hd-inline-answer" role="status" aria-live="polite">'
        '<span class="hd-inline-answer-label">AI 답변</span>'
        f'<span class="hd-inline-answer-text">{escape(text)}</span></div>'
    )


def render_citation(citation: Any) -> str:
    """B14/B23: show the exact source location and excerpt, escaping all fields."""
    source = _mapping(citation)
    if not source:
        return '<p class="hd-citation-empty">출처를 선택하면 근거 문장과 원문 위치를 확인할 수 있어요.</p>'
    number = source.get("number")
    number_html = (
        f'<span class="hd-citation-number">[{escape(str(number))}]</span>'
        if number is not None else ""
    )
    file_name = escape(str(source.get("file_name") or "문서명 미제공"))
    company = str(source.get("company_name") or "")
    location = str(source.get("location_label") or "위치 정보 미제공")
    code = str(source.get("doc_type") or "")
    doc_type = {value: label for label, value in DOC_TYPES}.get(code, code)
    version_no = source.get("version_no")
    metadata = [company, doc_type, location]
    if version_no is not None:
        metadata.append(f"버전 {version_no}")
    meta_html = "".join(f"<span>{escape(item)}</span>" for item in metadata if item)
    excerpt = escape(str(source.get("excerpt") or "이 출처의 발췌문이 제공되지 않았어요."))
    return (
        '<article class="hd-citation">'
        f'<div class="hd-citation-title">{number_html}<span>{file_name}</span></div>'
        f'<div class="hd-citation-meta">{meta_html}</div>'
        f'<blockquote>{excerpt}</blockquote>'
        '</article>'
    )
