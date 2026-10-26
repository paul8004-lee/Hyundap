"""질문·답변·출처 화면 — UI 담당: 박경환.

기준 명세: AI_문서검색_통합_기능명세서 (MOD-034).
B06/B07/B13/B14/B22/B23: 현재 질문과 그 답변의 출처만 표시한다.
FUN-164 build_chat_page. 질문 제출(FUN-165 on_question_submit)은
app.py와 controller.py에서 챗봇 담당 함수에 연결한다.
요약(FUN-166 on_summary_request)은 검색 화면의 선택 문서 상세에 있으며 이곳에 중복하지 않는다.
백엔드의 답변 번호·문서/버전 식별자를 유지하고 대화 이력(P3)은 만들지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from html import escape
from typing import Any

import gradio as gr
from .services import DOC_TYPES


CHAT_CSS = """
#hd-chat-panel { gap: 14px; margin-top: 20px; }
#hd-chat-panel .hd-chat-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
#hd-chat-panel .hd-chat-heading h2 { margin: 0; color: #19212b; font-size: 18px; line-height: 28px; font-weight: 700; }
#hd-chat-panel .hd-chat-heading p { margin: 4px 0 0; font-size: 13px; color: #667389; line-height: 21px; }
#hd-chat-panel .hd-chat-label { background: #edf3fb; color: #062e5e; padding: 4px 10px; border-radius: 99px; font-size: 11px; font-weight: 700; white-space: nowrap; }
#hd-chat-panel .hd-scope-hint { color: #667389; font-size: 12px; line-height: 20px; margin: 0; }
#hd-chat-question textarea { min-height: 92px; }
#hd-chat-actions { align-items: end; }
#hd-chat-submit { max-width: 160px; min-height: 42px; margin-left: auto; }
#hd-chat-answer { border-top: 1px solid #e9edf3; padding-top: 16px; }
#hd-chat-answer .prose { color: #19212b; font-size: 14px; line-height: 1.85; }
#hd-chat-answer:empty { display: none; }
.hd-citation { border: 1px solid #dfe4ed; background: #f7f9fc; border-radius: 12px; padding: 16px; overflow-wrap: anywhere; }
.hd-citation-title { display: flex; align-items: baseline; gap: 8px; color: #19212b; font-size: 14px; line-height: 22px; font-weight: 700; }
.hd-citation-number { color: #062e5e; white-space: nowrap; }
.hd-citation-meta { display: flex; flex-wrap: wrap; gap: 6px 12px; color: #667389; font-size: 12px; line-height: 20px; margin-top: 6px; }
.hd-citation blockquote { margin: 12px 0 0; padding: 0 0 0 12px; border-left: 3px solid #bdcde1; color: #39465a; font-size: 13px; line-height: 1.8; white-space: pre-wrap; }
.hd-citation-empty { font-size: 13px; line-height: 21px; color: #667389; margin: 0; }
#hd-citation-open { min-height: 38px; max-width: 180px; }
@media (max-width: 640px) {
  #hd-chat-panel .hd-chat-heading { flex-wrap: wrap; gap: 8px; }
  #hd-chat-submit { max-width: none; width: 100%; }
}
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

    This text goes into gr.Markdown(sanitize_html=True); it is never inserted into
    gr.HTML. Citation choices are built from the same response by app.py.
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


def render_scope_hint(scope: str, selected_name: str | None = None) -> str:
    """B06/B22: make the active question boundary visible before submission."""
    if scope == "선택 문서":
        message = (
            f"선택한 문서 ‘{selected_name}’에서 답변의 근거를 찾아요."
            if selected_name else "검색 결과에서 문서를 선택해 주세요. 선택한 문서에 관해 질문할 수 있어요."
        )
    else:
        message = "현재 적용한 회사·문서 유형 범위에서 답변의 근거를 찾아요."
    return f'<p class="hd-scope-hint">{escape(message)}</p>'


def build_chat_page() -> dict[str, Any]:
    """FUN-164: construct the single-question panel; app.py binds all events."""
    with gr.Column(elem_id="hd-chat-panel", elem_classes=["hd-card"]) as container:
        gr.HTML(
            '<div class="hd-chat-heading"><div><h2>문서에 질문하기</h2>'
            '<p>필요한 내용을 질문하고, 답변의 근거를 함께 확인하세요.</p></div>'
            '<span class="hd-chat-label">문서 기반 AI 답변</span></div>'
        )
        scope = gr.Radio(
            ["선택 문서", "현재 검색 범위"], value="선택 문서",
            label="질문 범위", interactive=True,
            elem_id="hd-chat-scope", elem_classes=["hd-select"],
        )
        scope_hint = gr.HTML(render_scope_hint("선택 문서"))
        question = gr.Textbox(
            label="질문", placeholder="예: 이 문서의 주요 내용과 핵심 수치를 알려줘",
            lines=3, max_lines=6, max_length=4000,
            elem_id="hd-chat-question", elem_classes=["hd-input"],
        )
        with gr.Row(elem_id="hd-chat-actions"):
            submit = gr.Button(
                "질문하기", variant="primary", interactive=True,
                elem_id="hd-chat-submit", elem_classes=["hd-button"],
            )
        feedback = gr.HTML("", elem_classes=["hd-feedback"])
        answer = gr.Markdown(
            "", sanitize_html=True, line_breaks=True,
            elem_id="hd-chat-answer",
        )
        citation_choice = gr.Dropdown(
            choices=[], value=None, label="출처 확인", interactive=True,
            visible=False, elem_classes=["hd-select"],
        )
        citation_detail = gr.HTML("", visible=False)
        open_source = gr.Button(
            "이 문서 상세 보기", variant="secondary", visible=False,
            elem_id="hd-citation-open", elem_classes=["hd-button"],
        )
    return {
        "container": container, "scope": scope, "question": question,
        "submit": submit, "feedback": feedback, "answer": answer,
        "citation_choice": citation_choice, "citation_detail": citation_detail,
        "open_source": open_source, "scope_hint": scope_hint,
    }
