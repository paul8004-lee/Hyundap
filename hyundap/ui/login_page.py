"""B01/B08/B16/B17: Gradio 기본 입력과 Python 이벤트로 구성한 로그인 화면.

직접 작성한 JavaScript를 사용하지 않는다. 표시용 역할 선택은 인증 권한을
결정하지 않으며, 로그인 처리와 화면 이동은 app.py/controller.py에 위임한다.
"""
from __future__ import annotations

from functools import partial

import gradio as gr

from .components import FONT_HEAD, UI_DIR, brand_html, footer_html

LOGIN_HEADING = """
<div class="hd-login-heading hd-login-heading--employee">
  <h1>로그인</h1><p>회사 계정으로 로그인해 주세요.</p>
</div>
<div class="hd-login-heading hd-login-heading--admin">
  <h1>관리자 로그인</h1><p>문서 검색과 업로드를 관리할 수 있어요.</p>
</div>
"""


def clear_login_feedback() -> str:
    """입력 또는 역할이 바뀌면 이전 오류 안내만 지운다."""
    return ""


def toggle_password_visibility(visible: bool):
    """비밀번호 값을 읽거나 돌려보내지 않고 입력창의 표시 방식만 바꾼다."""
    visible = not visible
    return (gr.update(type="text" if visible else "password"), visible,
            gr.update(value="비밀번호 숨기기" if visible else "비밀번호 표시"))


def fill_demo_account(account: str):
    """허용된 체험 계정만 채운다. 인증 함수를 호출하지 않는다."""
    if account not in {"employee", "manager", "admin"}:
        raise ValueError("Unknown demo account")
    role = "일반 사용자" if account == "employee" else "관리자"
    return (account, gr.update(value="demo1234", type="password"), role, "", False,
            gr.update(value="비밀번호 표시"))


def build_login_page(demo_mode: bool = True) -> dict:
    """FUN-156: 화면·입력과 표시 이벤트만 구성하고 인증용 참조를 반환한다."""
    password_visible = gr.State(False)
    with gr.Column(elem_id="hd-login", elem_classes=["hd-login"], min_width=0) as container:
        gr.HTML(f'<div class="hd-login-brand">{brand_html()}</div>', elem_classes=["hd-plain-html", "hd-login-header"])
        with gr.Column(elem_classes=["hd-login-main"], min_width=0):
            with gr.Column(elem_classes=["hd-login-stack"], min_width=0):
                with gr.Column(elem_id="hd-login-card", elem_classes=["hd-login-card"], min_width=0):
                    role = gr.Radio(
                        ["일반 사용자", "관리자"], value="일반 사용자",
                        label="로그인 사용자 유형", show_label=False, container=False,
                        interactive=True, elem_id="hd-login-role", elem_classes=["hd-login-tabs"],
                    )
                    gr.HTML(LOGIN_HEADING, head=FONT_HEAD, elem_classes=["hd-plain-html"])
                    with gr.Column(elem_id="hd-login-form", elem_classes=["hd-login-form"], min_width=0):
                        username = gr.Textbox(
                            label="아이디", value="", placeholder="아이디를 입력하세요", lines=1, max_lines=1, interactive=True,
                            html_attributes=gr.InputHTMLAttributes(autocomplete="username", autocapitalize="none", spellcheck=False),
                            elem_id="hd-login-username", elem_classes=["hd-input", "hd-login-field"],
                        )
                        with gr.Column(elem_classes=["hd-password-wrap"], min_width=0):
                            password = gr.Textbox(
                                label="비밀번호", value="", placeholder="비밀번호를 입력하세요", type="password", lines=1, max_lines=1, interactive=True,
                                html_attributes=gr.InputHTMLAttributes(autocomplete="current-password", enterkeyhint="go"),
                                elem_id="hd-login-password", elem_classes=["hd-input", "hd-login-field"],
                            )
                            password_toggle = gr.Button("비밀번호 표시", icon=str(UI_DIR / "assets" / "password-visibility.svg"), size="sm", scale=0, min_width=36, elem_id="hd-password-toggle", elem_classes=["hd-password-toggle"])
                        feedback = gr.HTML("", elem_id="hd-login-feedback", elem_classes=["hd-plain-html"])
                        submit = gr.Button("로그인", variant="primary", elem_id="hd-login-submit", elem_classes=["hd-button", "hd-button--primary", "hd-login-submit"])
                with gr.Row(visible=demo_mode, elem_id="hd-demo-login", elem_classes=["hd-demo-login-wrap"]) as demo_hint:
                    gr.HTML("<span>체험 계정</span>", elem_classes=["hd-plain-html", "hd-demo-text"])
                    demo_buttons = []
                    for index, account in enumerate(("employee", "manager", "admin")):
                        if index:
                            gr.HTML('<span aria-hidden="true">·</span>', elem_classes=["hd-plain-html", "hd-demo-text"])
                        demo_buttons.append(gr.Button(account, size="sm", scale=0, min_width=0, elem_classes=["hd-demo-account"]))
                    gr.HTML("<span>/ 비밀번호 demo1234</span>", elem_classes=["hd-plain-html", "hd-demo-text"])
        gr.HTML(footer_html("계정 문의는 소속 회사 IT 지원팀으로 연락해 주세요."), elem_classes=["hd-plain-html", "hd-login-footer"])

    # UI 전용 콜백에는 업무 인증이나 비밀번호 입력값을 전달하지 않는다.
    for event in (role.change, username.input, password.input):
        event(clear_login_feedback, inputs=[], outputs=[feedback], queue=False,
              api_visibility="private", show_progress="hidden")
    if demo_mode:
        for button, account in zip(demo_buttons, ("employee", "manager", "admin")):
            button.click(partial(fill_demo_account, account), inputs=[],
                         outputs=[username, password, role, feedback, password_visible, password_toggle],
                         queue=False, api_visibility="private", show_progress="hidden")
    password_toggle.click(toggle_password_visibility, inputs=[password_visible],
                          outputs=[password, password_visible, password_toggle],
                          queue=False, api_visibility="private", show_progress="hidden")
    return {
        "container": container, "role": role, "username": username, "password": password,
        "submit": submit, "feedback": feedback, "demo_hint": demo_hint,
        "password_visible": password_visible, "password_toggle": password_toggle,
    }
