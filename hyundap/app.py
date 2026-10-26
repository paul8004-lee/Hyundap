"""FUN-176/177 · B02/B16/B24~B32: 현답 UI 조립·실행.

저장소 최상위에서 설치: python -m pip install -r requirements.txt
설치한 Python 환경으로 실행: python hyundap/app.py
현재 python -m hyundap.app도 지원하며, 팀 import 기준 확정 후 함께 통일한다.
기본은 예시 모드. 실제 함수는 hyundap/ui/backend_bindings.py에 연결한다.
백엔드/DB/수집 모듈은 이 파일에서 구현하거나 변경하지 않는다.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from uuid import uuid4

# B02: VS Code의 'Python 파일 실행'과 패키지 실행이 동일한 모듈을 사용한다.
# 파일을 직접 실행할 때만 저장소 경로를 추가한다. 작업 폴더는 변경하지 않는다.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr

from hyundap.ui.admin_page import ADMIN_CSS, build_admin_page
from hyundap.ui.chat_page import CHAT_CSS, build_chat_page
from hyundap.ui.components import brand_html, footer_html, get_css, get_theme
from hyundap.ui.controller import UIController
from hyundap.ui.login_page import build_login_page
from hyundap.ui.search_page import SEARCH_CSS, build_search_page
from hyundap.ui.services import COMPANIES, DOC_TYPES, BackendServices


APP_CSS = """
#hd-shell .html-container {padding:0!important;}
#hd-topnav {flex:0 0 60px!important;height:60px;}
#hd-topnav:has([data-role="company_admin"]),#hd-topnav:has([data-role="platform_admin"]) {background:#1d7c4e!important;}
#hd-topnav-row {flex-wrap:nowrap;}
#hd-nav-brand {flex:0 0 160px;min-width:150px;}
#hd-nav-search,#hd-nav-upload {flex:0 0 96px;min-width:90px;max-width:110px;}
#hd-topnav-row .hd-nav-button.primary {border-bottom-color:white!important;color:white!important;font-weight:700!important;}
#hd-nav-user {margin-left:auto;min-width:80px;max-width:230px;}
#hd-nav-user .hd-brand {display:none;}
#hd-nav-user .hd-header-summary {justify-content:flex-end;}
#hd-nav-logout {flex:0 0 76px;min-width:76px;max-width:76px;white-space:nowrap;padding:8px!important;font-size:12px!important;}
#hd-topnav .hd-brand-name,#hd-topnav .hd-role,#hd-topnav .hd-user-summary>span:last-child {color:#fff!important;}
#hd-topnav .hd-avatar {color:#032b5c!important;}
#hd-content {padding-top:20px!important;}
#hd-mode {display:none;}
#hd-search-toolbar>.form {flex:0 0 180px!important;min-width:180px!important;}
#hd-type {padding:0!important;border:0!important;border-radius:10px!important;margin:0!important;}
#hd-filter-reset {border:1px solid #dfe4ed;border-radius:10px;background:transparent;font-size:12px;}
#hd-shell .hd-notice span {color:inherit;}
#hd-result-table td * {font-size:12px;}
#hd-search-submit {background:#032b5c!important;border-color:#032b5c!important;color:white!important;}
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


def build_app(service=None, *, mode="demo") -> gr.Blocks:
    """FUN-176: 동일 화면에 예시 서비스 또는 실제 담당 함수 어댑터를 주입."""
    if service is None:
        if mode == "demo":
            from hyundap.ui.mock_services import MockServices
            service = MockServices()
        elif mode == "backend":
            from hyundap.ui.backend_bindings import create_bindings
            service = BackendServices(create_bindings())
        else:
            raise ValueError("mode must be demo or backend")
    demo_mode = bool(service.demo)
    with gr.Blocks(title="Hyundap · 문서 검색", analytics_enabled=False, fill_width=True) as demo:
        key = gr.State(value=lambda: uuid4().hex, time_to_live=3600)
        login = build_login_page(demo_mode=demo_mode)
        with gr.Column(visible=False, elem_id="hd-shell", elem_classes=["hd-app"]) as container:
            with gr.Column(elem_id="hd-topnav", elem_classes=["hd-nav"]):
                with gr.Row(elem_id="hd-topnav-row", elem_classes=["hd-nav-inner"]):
                    gr.HTML(brand_html(inverse=True), elem_id="hd-nav-brand")
                    nav_search = gr.Button("문서 검색", variant="primary", elem_id="hd-nav-search", elem_classes=["hd-nav-button"])
                    upload_nav = gr.Button("문서 업로드", visible=False, elem_id="hd-nav-upload", elem_classes=["hd-nav-button"])
                    account = gr.HTML("", elem_id="hd-nav-user")
                    logout = gr.Button("로그아웃", elem_id="hd-nav-logout", elem_classes=["hd-account"])
            gr.HTML("예시 데이터 모드 · 실제 백엔드 미연결" if demo_mode else "팀 백엔드 연결 모드", elem_id="hd-mode")
            with gr.Column(elem_id="hd-content", elem_classes=["hd-content"]):
                search = build_search_page(COMPANIES, DOC_TYPES, demo_mode)
                chat = build_chat_page()
                chat["container"].visible = False
                admin = build_admin_page(COMPANIES, DOC_TYPES, demo_mode)
            gr.HTML(footer_html("예시 데이터 · 실제 백엔드 미연결" if demo_mode else "문서의 근거를 확인하고, 함께 활용하세요."), elem_classes=["hd-footer", "hd-plain-html"])
        with gr.Column(visible=False, elem_id="hd-modal") as modal:
            with gr.Column(elem_id="hd-modal-card"):
                gr.HTML("<h2>문서 출처 확인</h2>", elem_id="hd-modal-heading")
                modal_body = gr.HTML("")
                modal_close = gr.Button("닫기", elem_id="hd-modal-close")
        shell = dict(container=container, nav_search=nav_search, upload_nav=upload_nav, account=account, logout=logout, modal=modal, modal_body=modal_body, modal_close=modal_close)
        controller = UIController(service, login, search, chat, admin, shell)
        key.delete_callback = controller.discard
        # 반환 dict의 일부 항목만 갱신한다. 다른 화면/입력은 그대로 유지한다.
        outputs = list(dict.fromkeys(component for refs in [login, search, chat, admin, shell] for component in refs.values() if isinstance(component, (gr.components.Component, gr.layouts.Column, gr.layouts.Row))))

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
        bind(search["download"].click, "download", [])
        bind(chat["submit"].click, "ask", [chat["question"], chat["scope"]])
        bind(chat["scope"].input, "scope", [chat["scope"]], trigger_mode="always_last")
        bind(chat["citation_choice"].input, "citation", [chat["citation_choice"]])
        bind(chat["open_source"].click, "open_citation", [chat["citation_choice"]])
        bind(admin["submit"].click, "upload", [admin["file"], admin["company"], admin["doc_type"]])
        bind(modal_close.click, "close", [])

        def navigation(destination):
            async def navigate(session_key):
                async for patch in controller.run("nav", session_key, destination):
                    yield patch
            return navigate
        for button, destination in [(nav_search, "search"), (upload_nav, "upload")]:
            button.click(navigation(destination), inputs=[key], outputs=outputs, concurrency_limit=None, api_visibility="private", show_progress="hidden")

    demo.hyundap_controller = controller
    demo.hyundap_css = get_css() + SEARCH_CSS + CHAT_CSS + ADMIN_CSS + APP_CSS
    return demo.queue(default_concurrency_limit=4)


def main():
    """FUN-177: 기본은 내 컴퓨터에서만 열리는 화면 개발용 앱."""
    parser = argparse.ArgumentParser(description="Hyundap Gradio UI")
    parser.add_argument("--mode", choices=["demo", "backend"], default="demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    demo = build_app(mode=args.mode)
    demo.launch(server_name=args.host, server_port=args.port, share=False, theme=get_theme(), css=demo.hyundap_css, js=APP_JS, footer_links=[], show_error=False, max_file_size=100 * 1024 * 1024)


if __name__ == "__main__":
    main()
