"""관리자 수동 업로드 화면 — UI 담당: 박경환.

기준 명세: AI_문서검색_통합_기능명세서 (MOD-035).
B11/B12/B16/B21: 회사·파일 입력과 현재 업로드 결과를 표시한다.
FUN-167 build_admin_page. 업로드 제출(FUN-168 on_upload_submit)은
app.py와 controller.py에서 담당 함수에 연결한다.
FUN-169 load_account_options, FUN-170 on_role_change_submit,
FUN-171 load_log_view의 계정·권한 변경·처리 로그는 P3 또는 범위 보류로 구현하지 않는다.
파일 추출·청킹·임베딩·저장은 백엔드의 통합 진입 함수가 수행한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from html import escape
from typing import Any

import gradio as gr

from .services import DOC_TYPES, MAX_UPLOAD_BYTES, ALLOWED_EXTENSIONS


ADMIN_CSS = """
#hd-upload-page { max-width: 760px; margin: 30px auto 72px; gap: 20px; }
#hd-upload-page .hd-upload-heading h1 { font-size: 24px; line-height: 36px; font-weight: 700; margin: 0; color: #25282c; }
#hd-upload-page .hd-upload-heading p { color: #667389; font-size: 13px; line-height: 21px; margin: 6px 0 0; }
#hd-upload-form { gap: 20px; padding: 26px; border-radius: 16px; border: 1px solid #dfe4ed; background: white; }
#hd-upload-form .hd-upload-section-title { color: #19212b; font-size: 16px; font-weight: 700; line-height: 24px; margin: 0; }
#hd-upload-form .hd-upload-intro { color: #667389; font-size: 13px; line-height: 21px; margin: 5px 0 0; }
#hd-upload-file { background: #fafbfd; border-radius: 12px; }
#hd-upload-file .upload-container { min-height: 180px; border-color: #cad3df; }
#hd-upload-submit { min-height: 44px; background: #1d7c4e; border-color: #1d7c4e; }
#hd-upload-submit:hover { background: #176b43; border-color: #176b43; }
.hd-upload-note { border-radius: 12px; background: #edf0f4; padding: 12px 16px; color: #667389; font-size: 12px; line-height: 21px; }
.hd-upload-note strong { color: #39465a; font-weight: 600; }
.hd-upload-result { border: 1px solid #dfe4ed; border-radius: 14px; background: #fff; padding: 20px; overflow-wrap: anywhere; }
.hd-upload-batch { display: grid; gap: 12px; }
.hd-upload-batch-summary { margin: 0; color: #39465a; font-size: 13px; line-height: 21px; }
.hd-upload-result-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.hd-upload-result-top h2 { font-size: 16px; line-height: 24px; color: #19212b; margin: 0; font-weight: 700; }
.hd-upload-status { border-radius: 99px; font-size: 12px; font-weight: 700; line-height: 18px; padding: 4px 10px; background: #edf0f4; color: #667389; white-space: nowrap; }
.hd-upload-status.running { background: #eaf1fa; color: #195a9c; }
.hd-upload-status.succeeded { background: #e2f2e9; color: #196a44; }
.hd-upload-status.failed { background: #fdeeee; color: #b42318; }
.hd-upload-result dl { margin: 15px 0 0; }
.hd-upload-result dl > div { display: grid; grid-template-columns: 104px minmax(0, 1fr); gap: 12px; padding: 9px 0; border-bottom: 1px solid #e9edf3; font-size: 13px; line-height: 21px; }
.hd-upload-result dt { color: #667389; }
.hd-upload-result dd { color: #19212b; margin: 0; overflow-wrap: anywhere; }
.hd-upload-result-message { font-size: 13px; line-height: 22px; color: #39465a; margin: 14px 0 0; white-space: pre-wrap; }
.hd-upload-demo { color: #8a5b00; font-size: 12px; line-height: 20px; background: #fff8e7; border-radius: 8px; padding: 10px 12px; margin: 12px 0 0; }
@media (max-width: 640px) {
  #hd-upload-page { margin-top: 24px; }
  #hd-upload-form { padding: 20px; }
  .hd-upload-result dl > div { grid-template-columns: 76px minmax(0, 1fr); }
}
"""


_STATUS_LABELS = {
    "queued": "처리 대기", "running": "처리 중",
    "succeeded": "처리 완료", "failed": "처리 실패",
}
_STAGE_LABELS = {
    "validate": "파일 확인", "extract": "내용 추출",
    "normalize": "내용 정리", "chunk": "내용 분할",
    "embed": "검색 데이터 생성", "store": "저장", "publish": "공개 준비",
    "received": "접수", "ingest": "내용 추출·정리", "embedding": "검색 데이터 생성",
    "verify": "등록 결과 확인", "complete": "처리 완료",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if callable(getattr(value, "model_dump", None)):
        return value.model_dump()
    return {}


def render_upload_result(result: Any) -> str:
    """B21: render only the current request; success alone is not 'searchable'.

    The UI adapter supplies status/job_status, file_name, company_name, doc_type,
    message, error_code, stage, optional searchable bool, and demo bool.
    Unknown states stay unknown instead of being rewritten as successful uploads.
    """
    data = _mapping(result)
    if not data:
        return ""
    status = str(data.get("job_status") or data.get("status") or "").lower()
    status_label = _STATUS_LABELS.get(status, "상태 확인 필요")
    status_class = status if status in _STATUS_LABELS else "unknown"
    if status == "succeeded" and data.get("demo") and data.get("simulated"):
        status_label = "입력 확인 완료"
    rows: list[tuple[str, str]] = []
    for label, key in (("파일명", "file_name"), ("회사", "company_name"), ("문서 유형", "doc_type")):
        if data.get(key) is not None:
            value = str(data[key])
            if key == "doc_type":
                value = {code: name for name, code in DOC_TYPES}.get(value, "문서 유형 확인 필요")
            rows.append((label, value))
    stage = data.get("stage") or data.get("step")
    if stage:
        rows.append(("진행 단계", _STAGE_LABELS.get(str(stage), "진행 상태 확인 필요")))
    searchable = data.get("searchable")
    if searchable is True:
        rows.append(("검색 상태", "검색 가능"))
    elif searchable is False:
        rows.append(("검색 상태", "아직 검색에 사용되지 않아요"))
    elif status == "succeeded":
        rows.append(("검색 상태", "검색 가능 여부 확인 필요"))
    if data.get("error_code"):
        rows.append(("오류 코드", str(data["error_code"])))
    rows_html = "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>"
        for label, value in rows
    )
    default_messages = {
        "queued": "파일이 접수되었어요. 처리가 끝나면 검색 가능 여부를 확인할 수 있어요.",
        "running": "문서를 처리하고 있어요. 아직 검색에 사용되지 않을 수 있어요.",
        "succeeded": "문서 처리가 완료되었어요. 검색 가능 여부는 위 상태를 확인해 주세요.",
        "failed": "파일을 처리하지 못했어요. 오류 안내를 확인한 뒤 파일을 수정해 다시 제출해 주세요.",
    }
    message = str(data.get("message") or default_messages.get(status, "처리 상태를 확인할 수 없어요."))
    demo_html = (
        '<p class="hd-upload-demo">화면 확인용 예시 결과입니다. 실제 문서 저장이나 검색 등록은 수행하지 않았어요.</p>'
        if data.get("demo") else ""
    )
    return (
        '<section class="hd-upload-result" aria-live="polite">'
        '<div class="hd-upload-result-top"><h2>이번 업로드 결과</h2>'
        f'<span class="hd-upload-status {status_class}">{escape(status_label)}</span></div>'
        f'<dl>{rows_html}</dl><p class="hd-upload-result-message">{escape(message)}</p>'
        f'{demo_html}</section>'
    )


def render_batch_upload_results(results: list[dict], total: int) -> str:
    """파일별 결과를 그대로 보여 주고 아직 끝나지 않은 파일을 구분한다."""
    completed = sum(str(item.get("job_status") or item.get("status") or "").lower() == "succeeded" for item in results)
    failed = sum(str(item.get("job_status") or item.get("status") or "").lower() == "failed" for item in results)
    pending = len(results) - completed - failed
    summary = f"{len(results)}/{total}건 처리 · 완료 {completed}건 · 실패 {failed}건"
    if pending:
        summary += f" · 상태 확인 필요 {pending}건"
    cards = "".join(render_upload_result(item) for item in results)
    return f'<div class="hd-upload-batch"><p class="hd-upload-batch-summary">{escape(summary)}</p>{cards}</div>'


def build_admin_page(
    companies: Sequence[tuple[str, str]],
    doc_types: Sequence[tuple[str, str]],
    demo_mode: bool = True,
) -> dict[str, Any]:
    """FUN-167: build admin-only upload form; app.py controls role visibility.

    companies/doc_types are (label, backend_value) options from the UI service.
    Company-admin sessions receive one fixed company. Backend functions still
    enforce authorization; hidden controls are not an authorization boundary.
    """
    company_options = list(companies)
    with gr.Column(visible=False, elem_id="hd-upload-page", elem_classes=["hd-content"]) as container:
        gr.HTML(
            '<div class="hd-upload-heading"><h1>문서 업로드</h1>'
            '<p>회사 문서를 등록하고 검색과 AI 답변에 활용하세요.</p></div>'
        )
        with gr.Column(elem_id="hd-upload-form", elem_classes=["hd-card"]):
            gr.HTML(
                '<div><h2 class="hd-upload-section-title">문서 정보</h2>'
                '<p class="hd-upload-intro">문서를 등록할 회사를 선택해 주세요.</p></div>'
            )
            with gr.Row():
                company = gr.Dropdown(
                    company_options, value=company_options[0][1] if len(company_options) == 1 else None,
                    label="회사", interactive=len(company_options) != 1,
                    elem_classes=["hd-select"],
                )
            file = gr.File(
                label="업로드할 파일", type="filepath", file_count="multiple",
                # 형식은 선택 직후와 제출 시 공통 검증한다. Gradio의 영문 선행 오류를 피한다.
                file_types=None,
                elem_id="hd-upload-file",
            )
            gr.HTML(
                '<div class="hd-upload-note"><strong>PDF · DOCX · PPTX</strong> 파일을 여러 개 선택할 수 있어요. 선택한 회사가 모든 파일에 적용됩니다.<br>'
                '파일당 최대 100MiB(104,857,600바이트)이며, 빈 파일은 등록할 수 없어요.<br>'
                '파일별로 순서대로 처리합니다. 실패한 파일은 결과를 확인하고 다시 선택해 주세요.<br>'
                '처리 중 입력을 바꾸면 현재 파일은 등록될 수 있고 나머지 파일은 중단됩니다.<br>'
                '암호화된 파일과 내용 추출이 필요한 스캔 PDF는 지원하지 않아요.</div>'
            )
            if demo_mode:
                gr.HTML(
                    '<div class="hd-upload-demo">화면 확인용 모드입니다. 선택한 파일로 입력 검증과 예시 결과를 확인할 수 있으며, 실제 문서로 등록되지는 않아요.</div>'
                )
            submit = gr.Button(
                "문서 등록", variant="primary", interactive=False, elem_id="hd-upload-submit",
                elem_classes=["hd-button"],
            )
            feedback = gr.HTML("", elem_id="hd-upload-feedback", elem_classes=["hd-feedback"])
        result = gr.HTML("", elem_id="hd-upload-result")
    return {
        "container": container, "company": company,
        "file": file, "submit": submit, "feedback": feedback, "result": result,
    }
