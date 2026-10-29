"""FUN-176/177 · B02/B16/B24~B32: 현답 UI 조립·실행.

담당: 박경환
기준 명세: AI_문서검색_통합_기능명세서 (MOD-037).
FUN-176 build_app: 각 화면·공통 스타일·이벤트와 서비스 연결부를 조립한다.
FUN-177 main: 실행 옵션을 읽고 Gradio 앱을 실행한다.

저장소 최상위에서 설치: python -m pip install -r requirements.txt
저장소 최상위에서 실행: python -m hyundap.app
팀 import 기준은 저장소 루트의 hyundap 패키지이며 실행 경로를 우회하지 않는다.
기본은 실제 백엔드와 인증 함수를 연결하는 모드다.
전체 예시 실행은 --mode demo. 연결 환경 점검은 --check-backend.
백엔드/DB/수집 모듈은 이 파일에서 구현하거나 변경하지 않는다.
"""
from __future__ import annotations

import argparse
import os
from html import escape
from uuid import uuid4

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr

from hyundap.ui.admin_page import ADMIN_CSS, build_admin_page
from hyundap.ui.chat_page import CHAT_CSS
from hyundap.ui.components import apply_common_style, brand_html, get_theme
from hyundap.ui.controller import UIController
from hyundap.ui.component_contract import validate_component_contract
from hyundap.ui.login_page import build_login_page
from hyundap.ui.search_page import SEARCH_CSS, build_search_page
from hyundap.ui.services import COMPANIES, DOC_TYPES, BackendServices, UIServices


APP_CSS = """
#hd-shell {--hd-page-accent:#032b5c;--hd-row-hover:#f0f5fc;--hd-row-selected:#eaf1fb;}
#hd-shell.hd-role-admin {--hd-page-accent:var(--hd-green);--hd-row-hover:#f1f8f3;--hd-row-selected:#e7f3ec;}
#hd-shell .html-container {padding:0!important;}
#hd-topnav {flex:0 0 60px!important;height:60px;background:var(--hd-page-accent,#032b5c)!important;}
#hd-shell.hd-role-admin #hd-topnav {background:var(--hd-green)!important;}
#hd-topnav-row {flex-wrap:nowrap;}
#hd-topnav #hd-nav-search,#hd-topnav #hd-nav-upload,#hd-topnav #hd-nav-logout {color:#fff!important;}
#hd-nav-brand {flex:0 0 160px;min-width:150px;}
#hd-nav-search,#hd-nav-upload {flex:0 0 96px;min-width:90px;max-width:110px;}
#hd-topnav-row .hd-nav-button.primary {border-bottom-color:white!important;color:white!important;font-weight:700!important;}
#hd-nav-user {margin-left:auto;min-width:80px;max-width:230px;}
#hd-nav-user .hd-brand {display:none;}
#hd-nav-user .hd-header-summary {justify-content:flex-end;}
#hd-nav-logout {flex:0 0 76px;min-width:76px;max-width:76px;white-space:nowrap;padding:8px!important;font-size:12px!important;}
#hd-topnav .hd-brand-name,#hd-topnav .hd-role,#hd-topnav .hd-user-summary>span:last-child {color:#fff!important;}
#hd-topnav .hd-avatar {color:var(--hd-page-accent)!important;}
#hd-content {padding-top:20px!important;}
#hd-mode {display:none;}
#hd-type {padding:0!important;border:0!important;border-radius:10px!important;margin:0!important;}
#hd-shell .hd-notice span {color:inherit;}
#hd-search-submit {background:var(--hd-page-accent)!important;border-color:var(--hd-page-accent)!important;color:white!important;}
#hd-shell.hd-role-admin #hd-search-submit:hover {background:#176b43!important;border-color:#176b43!important;}
#hd-shell.hd-role-admin #hd-document-actions button.primary {background:var(--hd-green)!important;border-color:var(--hd-green)!important;color:#fff!important;}
#hd-results-card.hd-card {padding:0!important;}
#hd-modal {position:fixed!important;z-index:1000;inset:0;width:100%;height:100dvh;background:rgb(8 20 35 / 42%);display:flex;align-items:center;justify-content:center;padding:24px!important;box-sizing:border-box;}
#hd-modal-card {flex:0 0 auto!important;width:600px;max-width:100%;min-height:0;max-height:85dvh;overflow:auto;padding:24px!important;background:white;border:1px solid #dfe4ed;border-radius:18px;box-shadow:0 16px 60px #09213b30;gap:18px;}
#hd-modal-heading h2 {margin:0;color:#101820;font-size:20px;}
#hd-modal-close {max-width:100px;margin-left:auto;border:1px solid #dfe4ed;border-radius:10px;min-height:38px;}
@media(max-width:640px) {
 #hd-topnav-row {width:calc(100% - 24px);gap:4px!important;flex-wrap:wrap;}
 #hd-nav-brand {min-width:130px;flex-basis:130px;}
 #hd-nav-user {max-width:130px;} #hd-nav-search,#hd-nav-upload {min-width:70px;max-width:85px;}
 #hd-nav-logout {min-width:60px;max-width:60px;} .hd-user-summary span:last-child {display:none;}
 #hd-content {width:calc(100% - 28px)!important;}
}
@media(prefers-reduced-motion:reduce) {.hd-spinner {animation:none;}}
"""

# B03/B14: 자체 지정한 모달 요소의 키보드 포커스만 관리한다.
# Gradio 내부 DOM/스토어를 수정하지 않으며 Escape/Tab으로도 닫기·이동 가능.
APP_JS = """() => {
  let previous = null, opened = false;
  const sync = () => {
    const overlay = document.getElementById('hd-modal');
    const card = document.getElementById('hd-modal-card');
    const shown = !!(overlay && card && overlay.getClientRects().length);
    if (shown && !opened) {
      previous = document.activeElement;
      card.setAttribute('role', 'dialog'); card.setAttribute('aria-modal', 'true');
      card.setAttribute('aria-label', '문서 출처 확인'); card.tabIndex = -1;
      card.focus();
    }
    if (!shown && opened && previous && previous.isConnected) previous.focus();
    opened = shown;
  };
  new MutationObserver(sync).observe(document.body, {subtree:true, childList:true, attributes:true, attributeFilter:['style','class']});
  document.addEventListener('keydown', (event) => {
    if (!opened) return;
    const card = document.getElementById('hd-modal-card');
    if (event.key === 'Escape') { event.preventDefault(); document.getElementById('hd-modal-close')?.click(); }
    if (event.key === 'Tab') {
      const controls = Array.from(card.querySelectorAll('button,input,select,textarea,a[href],[tabindex="0"]')).filter(x => x.getClientRects().length && !x.disabled);
      if (!controls.length) { event.preventDefault(); card.focus(); return; }
      const first=controls[0], last=controls[controls.length-1];
      if (event.shiftKey && (document.activeElement===first || document.activeElement===card)) {event.preventDefault();last.focus();}
      else if (!event.shiftKey && (document.activeElement===last || document.activeElement===card)) {event.preventDefault();first.focus();}
    }
  });
}"""


def build_app(service: UIServices | None = None, *, mode="backend") -> gr.Blocks:
    """FUN-176: 동일 화면에 예시 서비스 또는 실제 담당 함수 어댑터를 주입."""
    if service is None:
        if mode == "demo":
            from hyundap.ui.mock_services import MockServices
            service = MockServices()
        elif mode == "backend":
            from hyundap.auth import session_service
            from hyundap.ui.backend_bindings import create_bindings
            service = BackendServices(create_bindings(authentication=session_service))
        else:
            raise ValueError("mode must be demo or backend")
    demo_mode = bool(service.demo)
    temporary_auth = bool(getattr(service, "temporary_auth", False))
    companies = COMPANIES if demo_mode or temporary_auth else []
    mode_note = "예시 데이터 · 실제 백엔드 미연결" if demo_mode else (
        "임시 로그인 · 실제 문서 처리는 서버 인증 연결 후 이용할 수 있습니다."
        if temporary_auth else "문서의 근거를 확인하고, 함께 활용하세요."
    )
    with gr.Blocks(title="Hyundap · 문서 검색", analytics_enabled=False, fill_width=True) as demo:
        key = gr.State(value=lambda: uuid4().hex, time_to_live=3600)
        login = build_login_page(demo_mode=demo_mode or temporary_auth)
        with gr.Column(visible=False, elem_id="hd-shell", elem_classes=["hd-app"]) as container:
            with gr.Column(elem_id="hd-topnav", elem_classes=["hd-nav"]):
                with gr.Row(elem_id="hd-topnav-row", elem_classes=["hd-nav-inner"]):
                    gr.HTML(brand_html(inverse=True), elem_id="hd-nav-brand")
                    nav_search = gr.Button("문서 검색", variant="primary", elem_id="hd-nav-search", elem_classes=["hd-nav-button"])
                    upload_nav = gr.Button("문서 업로드", visible=False, elem_id="hd-nav-upload", elem_classes=["hd-nav-button"])
                    account = gr.HTML("", elem_id="hd-nav-user")
                    logout = gr.Button("로그아웃", elem_id="hd-nav-logout", elem_classes=["hd-account"])
            gr.HTML(mode_note, elem_id="hd-mode")
            with gr.Column(elem_id="hd-content", elem_classes=["hd-content"]):
                search = build_search_page(companies, DOC_TYPES, demo_mode)
                # 질문은 검색 입력으로 받으며 답변·출처는 검색 스플릿뷰에 표시한다.
                admin = build_admin_page(companies, DOC_TYPES, demo_mode)
            # 회사 범위는 화면 왼쪽 아래에 표시하고 관리자 선택 기능도 유지한다.
            with gr.Row(elem_id="hd-shell-footer", elem_classes=["hd-footer"]):
                search["company"] = gr.Dropdown(
                    choices=[("전체 회사", "__all__"), *companies], value="__all__",
                    label="회사", show_label=False, interactive=True, filterable=False,
                    container=False, min_width=0, elem_id="hd-company",
                )
                gr.HTML(f'<span>{escape(mode_note)}</span>', elem_id="hd-footer-note", elem_classes=["hd-plain-html"])
        with gr.Column(visible=False, elem_id="hd-modal") as modal:
            with gr.Column(elem_id="hd-modal-card"):
                gr.HTML("<h2>문서 출처 확인</h2>", elem_id="hd-modal-heading")
                modal_body = gr.HTML("")
                modal_close = gr.Button("닫기", elem_id="hd-modal-close")
        shell = dict(container=container, nav_search=nav_search, upload_nav=upload_nav, account=account, logout=logout, modal=modal, modal_body=modal_body, modal_close=modal_close)
        controller = UIController(service, login, search, admin, shell)
        key.delete_callback = controller.discard
        # 반환 dict의 일부 항목만 갱신한다. 다른 화면/입력은 그대로 유지한다.
        outputs = list(dict.fromkeys(component for refs in [login, search, admin, shell] for component in refs.values() if isinstance(component, (gr.components.Component, gr.layouts.Column, gr.layouts.Row))))
        validate_component_contract(
            {"login": login, "search": search, "admin": admin, "shell": shell},
            registered=demo.blocks, outputs=outputs,
        )

        def handler(action):
            async def callback(session_key, *values):
                async for patch in controller.run(action, session_key, *values):
                    yield patch
            callback.__name__ = f"on_{action}"
            return callback

        def bind(event, action, inputs, **kwargs):
            return event(handler(action), inputs=[key, *inputs], outputs=outputs, concurrency_limit=None, api_visibility="private", show_progress="hidden", **kwargs)

        bind(login["submit"].click, "login", [login["username"], login["password"], login["role"]])
        bind(login["password"].submit, "login", [login["username"], login["password"], login["role"]])
        bind(logout.click, "logout", [])
        bind(search["submit"].click, "search", [search["keyword"], search["company"], search["doc_type"]])
        bind(search["keyword"].submit, "search", [search["keyword"], search["company"], search["doc_type"]])
        bind(search["company"].input, "filter", [search["company"], search["doc_type"]], trigger_mode="always_last")
        bind(search["doc_type"].input, "filter", [search["company"], search["doc_type"]], trigger_mode="always_last")
        bind(search["reset"].click, "reset", [])
        for button, word in zip(search["quick_buttons"], ["점검", "경제전망", "연구윤리", "산업동향"]):
            def make_quick(value):
                async def quick_search(session_key, company, doc_type):
                    yield {search["keyword"]: value}
                    async for patch in controller.run("search", session_key, value, company, doc_type):
                        yield patch
                return quick_search
            button.click(make_quick(word), inputs=[key, search["company"], search["doc_type"]], outputs=outputs, concurrency_limit=None, api_visibility="private", show_progress="hidden")

        async def on_document_select(session_key, event: gr.SelectData):
            index = event.index[0] if isinstance(event.index, (list, tuple)) else event.index
            async for patch in controller.run("select", session_key, index):
                yield patch
        search["table"].select(on_document_select, inputs=[key], outputs=outputs, concurrency_limit=None, api_visibility="private", show_progress="hidden")
        bind(search["summarize"].click, "summarize", [])
        bind(search["source"].click, "source", [])
        bind(search["evidence_prev"].click, "evidence_prev", [])
        bind(search["evidence_next"].click, "evidence_next", [])
        bind(search["download"].click, "download", [])
        bind(admin["submit"].click, "upload", [admin["file"], admin["company"], admin["doc_type"]])
        async def on_upload_input(session_key, file_path):
            # 큐를 거치지 않는 입력 검사는 스트리밍 대신 최종 변경을 한 번에 반환한다.
            result = {}
            async for patch in controller.run("upload_input", session_key, file_path):
                result.update(patch)
            return result

        # 파일 교체·제거와 메타정보 변경은 이전 처리 결과를 즉시 지운다.
        for event in (admin["file"].change, admin["company"].input, admin["doc_type"].input):
            event(on_upload_input, inputs=[key, admin["file"]], outputs=outputs,
                  trigger_mode="always_last", queue=False, api_visibility="private", show_progress="hidden")
        bind(modal_close.click, "close", [])

        def navigation(destination):
            async def navigate(session_key):
                async for patch in controller.run("nav", session_key, destination):
                    yield patch
            return navigate
        for button, destination in [(nav_search, "search"), (upload_nav, "upload")]:
            button.click(navigation(destination), inputs=[key], outputs=outputs, concurrency_limit=None, api_visibility="private", show_progress="hidden")

    # 로컬 회귀 검사·디버깅에서 화면 상태와 연결을 확인하는 Python 접근점.
    # 공개 API나 인증 근거로 사용하지 않는다.
    demo.hyundap_controller = controller
    demo.hyundap_css = apply_common_style() + SEARCH_CSS + CHAT_CSS + ADMIN_CSS + APP_CSS
    return demo.queue(default_concurrency_limit=4)


def main():
    """FUN-177: 실제 인증 또는 예시 모드로 내 컴퓨터에서 실행."""
    parser = argparse.ArgumentParser(description="Hyundap Gradio UI")
    parser.add_argument("--mode", choices=["demo", "backend"], default="backend")
    parser.add_argument("--check-backend", action="store_true", help="외부 호출 없이 백엔드 함수·설정 준비 상태 확인")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    if args.check_backend:
        from hyundap.ui.backend_check import print_backend_status
        raise SystemExit(print_backend_status())
    demo = build_app(mode=args.mode)
    from hyundap.ingest import ocr
    ocr.start_warm_up()   # OCR 엔진을 백그라운드에서 미리 불러온다(.env의 OCR_WARMUP=0이면 건너뜀). 첫 스캔 문서 업로드 지연을 줄인다.
    demo.launch(server_name=args.host, server_port=args.port, share=False, theme=get_theme(), css=demo.hyundap_css, js=APP_JS, footer_links=[], show_error=False, max_file_size=100 * 1024 * 1024)


if __name__ == "__main__":
    main()
