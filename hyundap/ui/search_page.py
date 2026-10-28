"""B04/B05/B09/B10/B18/B20: 검색과 선택 문서 상세. UI 담당: 박경환.

기준 명세: AI_문서검색_통합_기능명세서 (MOD-033).
FUN-159 build_search_page: 검색 조건·결과 목록·상세 영역을 구성한다.
검색(FUN-160 on_search_submit), 선택(FUN-161 on_document_select) 이벤트는
app.py → ui/controller.py → 담당 함수 연결부 순서로 흐른다.
피그마 68:9 / 9:183의 검색 바·분할 카드를 Gradio 컴포넌트로 구현한다.
"""
from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

import gradio as gr
from .services import DOC_TYPES

SEARCH_CSS = """
#hd-search-page {gap:14px;}
#hd-search-intro {text-align:center;padding:64px 16px 8px;}
#hd-search-intro h1 {font-size:48px;letter-spacing:-2px;line-height:1.2;margin:0 0 18px;color:var(--hd-page-accent,#032b5c);}
#hd-search-intro p {font-size:16px;color:#657184;margin:0;}
#hd-search-bar {max-width:840px;margin:8px auto 0;align-items:center;gap:0;background:white;border:1px solid #dfe4ed;border-radius:32px;padding:7px 8px 7px 18px;box-shadow:0 3px 12px #102b4c05;flex-wrap:nowrap;}
#hd-type {flex:0 0 115px!important;min-width:115px!important;max-width:115px;box-shadow:none!important;background:transparent!important;}
#hd-type .wrap {border:0!important;box-shadow:none!important;background:transparent!important;}
#hd-filter-reset {flex:0 0 64px!important;min-width:64px!important;max-width:64px;min-height:36px!important;height:36px;border:1px solid #dfe4ed;border-radius:10px;background:transparent;font-size:12px;white-space:nowrap!important;padding:0 8px!important;margin:0 8px!important;}
#hd-keyword {border:0!important;border-left:1px solid #dfe4ed!important;border-radius:0!important;background:transparent!important;box-shadow:none!important;min-width:100px!important;}
#hd-keyword textarea {border:0!important;background:transparent!important;box-shadow:none!important;min-height:36px!important;line-height:24px;padding:6px 14px!important;}
#hd-search-submit {flex:0 0 74px!important;min-width:74px!important;max-width:74px;border-radius:24px!important;height:42px;min-height:42px;}
#hd-inline-answer {max-width:760px;margin:0 auto;width:100%;}
#hd-inline-answer .hd-inline-answer {display:flex;align-items:baseline;gap:10px;min-height:40px;box-sizing:border-box;padding:10px 14px;border-radius:10px;background:#edf0f4;color:#344256;font-size:13px;line-height:20px;}
#hd-inline-answer .hd-inline-answer-label {flex:none;white-space:nowrap;color:var(--hd-page-accent,#032b5c);font-weight:650;}
#hd-inline-answer .hd-inline-answer-text {display:-webkit-box;min-width:0;overflow:hidden;-webkit-line-clamp:1;-webkit-box-orient:vertical;word-break:keep-all;overflow-wrap:anywhere;}
#hd-search-feedback {max-width:760px;margin:0 auto;width:100%;}
#hd-search-feedback:not(:has(.hd-notice)) {display:none;}
#hd-search-feedback .hd-notice {min-height:40px;padding:10px 16px;box-sizing:border-box;align-items:center;font-size:13px;line-height:1.5;}
#hd-quick-searches {justify-content:center;gap:8px;max-width:550px;margin:0 auto 20px;}
#hd-quick-searches button {flex:0 0 auto;min-width:70px;border:1px solid #dfe4ed;border-radius:20px;background:white;box-shadow:none;font-size:12px;}
#hd-search-columns {gap:16px;align-items:stretch;}
#hd-results-card {padding:0;gap:0!important;overflow:hidden;align-self:flex-start;min-height:0;flex:0 0 calc((100% - 16px) * .455)!important;}
#hd-results-heading {padding:14px 20px 12px;}
#hd-results-heading h2 {margin:0;color:#162537;font-size:15px;font-weight:650;line-height:1.5;}
#hd-result-table {border:0!important;border-radius:0!important;box-shadow:none!important;overflow-x:auto;}
#hd-result-table .table-wrap {border:0!important;min-width:480px;overflow-x:auto;}
#hd-result-table table {width:100%!important;min-width:480px;table-layout:fixed;font-size:13px!important;}
#hd-result-table table,#hd-result-table th,#hd-result-table th *,#hd-result-table td,#hd-result-table td *,#hd-result-table [role="gridcell"],#hd-result-table [role="gridcell"] * {font-family:var(--hd-font,Pretendard,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif)!important;}
#hd-result-table td,#hd-result-table td *,#hd-result-table [role="gridcell"],#hd-result-table [role="gridcell"] * {font-size:13px!important;letter-spacing:-.012em;}
#hd-result-table th {padding:0!important;border:0!important;border-bottom:1px solid #e7ebf1!important;background:#f7f8fa!important;color:#647085!important;font-weight:500!important;white-space:nowrap;word-break:keep-all;}
#hd-result-table th,#hd-result-table th * {font-size:12px!important;}
#hd-result-table th>.cell-wrap {padding:10px 12px!important;}
#hd-result-table .cell-menu-button {display:none!important;}
#hd-result-table .selection-button {display:none!important;}
#hd-result-table td,#hd-result-table [role="gridcell"] {padding:0!important;border:0!important;border-bottom:1px solid #edf0f5!important;background:#fff!important;color:#344256;vertical-align:middle;cursor:pointer;word-break:keep-all;overflow:hidden;}
#hd-result-table td>.cell-wrap,#hd-result-table [role="gridcell"]>.cell-wrap {display:flex;align-items:center;box-sizing:border-box;min-height:60px;padding:12px 10px!important;}
#hd-result-table .cell-wrap>.wrap {display:block;min-width:0;max-width:100%;width:100%;}
#hd-result-table td :is(.prose,.html-container),#hd-result-table [role="gridcell"] :is(.prose,.html-container) {max-width:100%;margin:0;}
#hd-result-table tbody tr:hover td,#hd-result-table [role="row"]:hover [role="gridcell"] {background:var(--hd-row-hover,#f0f5fc)!important;}
#hd-result-table td.selected,#hd-result-table [role="gridcell"].selected {background:var(--hd-row-selected,#eaf1fb)!important;}
#hd-result-table tr:has(.hd-file-active) td,#hd-result-table [role="row"]:has(.hd-file-active) [role="gridcell"] {background:var(--hd-row-selected,#eaf1fb)!important;}
#hd-result-table tr:has(.hd-file-active) td:first-child,#hd-result-table [role="row"]:has(.hd-file-active) [role="gridcell"].first-column {box-shadow:inset 3px 0 var(--hd-page-accent,#032b5c);}
#hd-result-table .hd-file-cell {display:flex;align-items:center;gap:7px;min-width:0;line-height:1.6;}
#hd-result-table .hd-file-name {display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600;color:#162537;line-height:1.6;}
#hd-result-table .hd-file-tag {display:inline-block;flex:none;padding:1px 4px;border:1px solid #dfe5ed;border-radius:4px;background:#f8fafc;color:#738095;font-size:10px!important;line-height:1.5;font-weight:500;}
#hd-result-table .hd-cell-text {display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.6;}
#hd-result-table .hd-date-cell {display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-variant-numeric:tabular-nums;line-height:1.4;}
#hd-result-table .hd-date-cell span {display:block;}
#hd-result-table .hd-date-time {color:#7a8596;font-size:11px!important;}
#hd-result-caption {padding:12px 20px;margin:0;color:#657184;font-size:12px;line-height:1.5;border-top:1px solid #edf0f5;}
#hd-document-detail {padding:24px;gap:14px;min-height:520px;flex:1 1 0!important;}
.hd-document-heading h2 {margin:0 0 12px;font-size:22px;line-height:1.4;letter-spacing:-.6px;color:#101820;overflow-wrap:anywhere;}
.hd-document-meta {color:#657184;line-height:1.8;font-size:12px;}
.hd-document-meta span+span::before {content:' · ';padding:0 4px;}
.hd-summary-card {background:#edf0f4!important;border:0!important;border-radius:15px!important;padding:18px!important;gap:8px!important;}
.hd-summary-title {color:var(--hd-page-accent,#032b5c);font-size:14px;font-weight:700;}
#hd-summary-body {background:transparent;border:0;box-shadow:none;font-size:13px;}
#hd-summary-body .prose {font-size:13px;line-height:1.8;color:#334156;}
#hd-summary-coverage {color:#657184;font-size:12px;}
#hd-summary-request {align-self:flex-start;max-width:120px;min-width:110px;min-height:34px;font-size:12px;border:1px solid #cad6e6;background:white;color:var(--hd-page-accent,#032b5c);}
.hd-evidence-box {border:1px solid #dfe4ed;border-radius:12px;padding:16px;font-size:13px;}
.hd-evidence-label {font-size:12px;color:#657184;margin-bottom:12px;}
.hd-evidence-box blockquote {margin:0;border-left:3px solid var(--hd-page-accent,#032b5c);padding-left:12px;color:#263448;line-height:1.75;white-space:pre-wrap;}
.hd-ai-note {font-size:11px;color:#657184;line-height:1.7;margin:0;}
#hd-document-actions {margin-top:auto;gap:8px;} #hd-document-actions button {max-width:160px;min-width:110px;font-size:12px;min-height:38px;border:1px solid #dfe4ed;border-radius:999px;}
#hd-download-file {font-size:12px;} #hd-empty-results {text-align:center;padding:60px 20px;color:#657184;}
@media(max-width:960px) {
 #hd-search-columns {flex-direction:column;}
 #hd-results-card,#hd-document-detail {min-width:0!important;width:100%;min-height:0;flex:0 0 auto!important;}
}
@media(max-width:760px) {
 #hd-search-intro {padding-top:36px;} #hd-search-intro h1 {font-size:38px;}
 #hd-search-bar {flex-wrap:wrap;border-radius:18px;padding:10px;gap:8px;}
 #hd-type {order:1;min-width:115px!important;max-width:none;flex:1 1 115px!important;}
 #hd-keyword {order:2;flex:1 1 120px!important;border-left:0!important;}
 #hd-filter-reset {order:3;margin:0!important;}
 #hd-search-submit {order:4;}
 #hd-inline-answer .hd-inline-answer-text {-webkit-line-clamp:2;}
 #hd-result-table {max-height:320px;}
}
"""


def document_rows(documents: list[dict], selected_id: str | None = None) -> list[list[str]]:
    """B05: total_hits는 청크 수. 문서 건수는 받은 목록 길이로 표시."""
    types = {value: label for label, value in DOC_TYPES}
    rows = []
    for doc in documents:
        name = str(doc.get("file_name") or "문서명 미제공")
        ext = Path(name).suffix.lstrip(".").upper()
        active = " hd-file-active" if doc.get("document_id") == selected_id else ""
        tag = f'<span class="hd-file-tag">{escape(ext)}</span>' if ext else ""
        filename = f'<span class="hd-file-cell" title="{escape(name)}">{tag}<span class="hd-file-name{active}">{escape(name)}</span></span>'
        doc_type = str(types.get(doc.get("doc_type"), doc.get("doc_type") or "미분류"))
        location = str(doc.get("location_label") or "미제공")
        date = str(doc.get("indexed_at") or "미제공")
        date_text = escape(date)
        if len(date) >= 16 and date[4:5] == "-" and date[7:8] == "-" and date[10:11] in ("T", " "):
            date_text = f'<span>{escape(date[5:10])}</span><span class="hd-date-time">{escape(date[11:16])}</span>'
        rows.append([
            filename,
            f'<span class="hd-cell-text" title="{escape(doc_type)}">{escape(doc_type)}</span>',
            f'<span class="hd-cell-text" title="{escape(location)}">{escape(location)}</span>',
            f'<span class="hd-date-cell" title="{escape(date)}">{date_text}</span>',
        ])
    return rows


def render_document(doc: dict | None) -> str:
    if not doc:
        return '<div class="hd-document-heading"><h2>문서를 선택해 주세요</h2><p class="hd-document-meta">검색 결과를 누르면 문서 정보와 근거 문장을 확인할 수 있어요.</p></div>'
    types = {value: label for label, value in DOC_TYPES}
    values = [doc.get("company_name"), types.get(doc.get("doc_type"), doc.get("doc_type")), f'버전 {doc["version_no"]}' if doc.get("version_no") is not None else None, f'색인 {doc["indexed_at"]}' if doc.get("indexed_at") else None]
    meta = "".join(f"<span>{escape(str(value))}</span>" for value in values if value)
    return f'<div class="hd-document-heading"><h2>{escape(str(doc.get("file_name") or "문서명 미제공"))}</h2><div class="hd-document-meta">{meta}</div></div>'


def render_evidence(doc: dict | None) -> str:
    if not doc:
        return ""
    location = escape(str(doc.get("location_label") or "위치 정보 미제공"))
    number = f'[{escape(str(doc["number"]))}] ' if doc.get("number") is not None else ""
    excerpt = escape(str(doc.get("excerpt") or "제공된 발췌문이 없습니다."))
    return f'<div class="hd-evidence-box"><div class="hd-evidence-label">{number}근거 문장 · {location}</div><blockquote>{excerpt}</blockquote></div>'


def render_coverage(response: dict) -> str:
    """B10: 부분 요약을 전체 요약으로 표시하지 않는다."""
    coverage = "전체 범위 요약" if response.get("is_full") is True else "부분 범위 요약"
    used, total = response.get("used_chunks"), response.get("total_chunks")
    if used is not None and total is not None:
        coverage += f" · 근거 {used}/{total}개"
    def text_value(value):
        return ", ".join(map(str, value)) if isinstance(value, list) else str(value or "미제공")
    return f'<div><strong>{escape(coverage)}</strong><br>포함: {escape(text_value(response.get("included_range")))}<br>제외: {escape(text_value(response.get("excluded_range")))}</div>'


def build_search_page(companies: list, doc_types: list, demo_mode: bool = True) -> dict[str, Any]:
    """FUN-159: 화면 생성만 담당. 데이터 접근은 ui/services.py 경유."""
    with gr.Column(elem_id="hd-search-page") as container:
        intro = gr.HTML('<h1>Hyundap</h1><p>문서를 찾고, 필요한 내용을 요약해 드립니다.</p>', elem_id="hd-search-intro")
        with gr.Row(elem_id="hd-search-bar"):
            doc_type = gr.Dropdown(choices=[("모든 유형", "__all__"), *doc_types], value="__all__", label="문서 유형", show_label=False, filterable=False, interactive=True, container=False, min_width=135, elem_id="hd-type")
            reset = gr.Button("초기화", variant="secondary", min_width=64, elem_id="hd-filter-reset")
            keyword = gr.Textbox(placeholder="문서를 검색하거나 질문해 보세요", label="검색어", show_label=False, container=False, lines=1, max_lines=1, max_length=2000, scale=4, min_width=120, elem_id="hd-keyword")
            submit = gr.Button("검색", variant="primary", elem_id="hd-search-submit", elem_classes=["hd-button"])
        answer = gr.HTML("", visible=False, elem_id="hd-inline-answer")
        feedback = gr.HTML("", elem_id="hd-search-feedback")
        with gr.Row(elem_id="hd-quick-searches") as quick:
            quick_buttons = [gr.Button(word, size="sm", min_width=70) for word in ["점검", "경제전망", "연구윤리", "산업동향"]]
        empty = gr.HTML("", visible=False, elem_id="hd-empty-results")
        with gr.Row(visible=False, elem_id="hd-search-columns") as results_area:
            with gr.Column(scale=5, min_width=360, elem_id="hd-results-card", elem_classes=["hd-card"]):
                gr.HTML('<h2>검색 결과</h2>', elem_id="hd-results-heading")
                table = gr.Dataframe(value=[], headers=["파일명", "유형", "원문 위치", "색인(KST)"], datatype=["html"] * 4, type="array", interactive=False, show_label=False, wrap=True, buttons=[], column_widths=["48%", "14%", "18%", "20%"], max_height=440, elem_id="hd-result-table")
                caption = gr.HTML("", elem_id="hd-result-caption")
            with gr.Column(scale=6, min_width=400, elem_id="hd-document-detail", elem_classes=["hd-card"]):
                detail = gr.HTML(render_document(None))
                with gr.Column(elem_classes=["hd-summary-card"]):
                    gr.HTML('<div class="hd-summary-title">AI 파일 요약</div>')
                    summary = gr.Markdown("선택한 문서를 요약해 주요 내용을 확인하세요.", sanitize_html=True, elem_id="hd-summary-body")
                    coverage = gr.HTML("", elem_id="hd-summary-coverage")
                    summarize = gr.Button("요약하기", interactive=False, elem_id="hd-summary-request")
                evidence = gr.HTML("")
                gr.HTML('<p class="hd-ai-note">AI가 생성한 요약과 답변은 정확하지 않을 수 있습니다. 중요한 내용은 출처를 함께 확인하세요.</p>')
                detail_feedback = gr.HTML("")
                with gr.Row(elem_id="hd-document-actions"):
                    source = gr.Button("출처 확인", interactive=False, variant="primary")
                    download = gr.Button("예시 파일 받기" if demo_mode else "다운로드", interactive=False, variant="secondary")
                download_file = gr.File(label="예시 파일 · 실제 원문이 아닙니다" if demo_mode else "다운로드 파일", interactive=False, visible=False, elem_id="hd-download-file")
    return dict(container=container, intro=intro, keyword=keyword, submit=submit, answer=answer, feedback=feedback, quick=quick, quick_buttons=quick_buttons, doc_type=doc_type, reset=reset, empty=empty, results_area=results_area, table=table, caption=caption, detail=detail, summary=summary, coverage=coverage, summarize=summarize, evidence=evidence, detail_feedback=detail_feedback, source=source, download=download, download_file=download_file)
