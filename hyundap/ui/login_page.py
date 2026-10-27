"""B01/B08/B16/B17: 역할별 강조색만 달라지는 현답 로그인 화면.

인증·권한 검사·화면 이동은 기존 app.py/controller.py에 위임한다.
탭 값은 기존 계약인 '일반 사용자'/'관리자'를 그대로 전달한다.
체험 계정 버튼은 예시 모드 또는 임시 인증 사용 시 표시하며 자동 로그인하지 않는다.
"""
from __future__ import annotations

import gradio as gr

from .components import FONT_HEAD, UI_DIR, brand_html, footer_html

ROLE_TEMPLATE = """
<div class="hd-login-tabs" role="tablist" aria-label="로그인 사용자 유형" data-mode="employee">
  <span class="hd-login-tab-indicator" aria-hidden="true"></span>
  <button type="button" role="tab" id="hd-login-employee-tab" data-value="일반 사용자"
          aria-selected="true" aria-controls="hd-login-form" tabindex="0">일반 사용자</button>
  <button type="button" role="tab" id="hd-login-admin-tab" data-value="관리자"
          aria-selected="false" aria-controls="hd-login-form" tabindex="-1">관리자</button>
</div>
<div class="hd-login-heading">
  <h1 id="hd-login-title">로그인</h1>
  <p>회사 계정으로 로그인해 주세요.</p>
</div>
"""


def build_login_page(demo_mode: bool = True) -> dict:
    """FUN-156: 화면·입력만 구성하고 기존 인증 이벤트에 컴포넌트를 반환."""
    with gr.Column(elem_id="hd-login", elem_classes=["hd-login"], min_width=0) as container:
        gr.HTML(f'<div class="hd-login-brand">{brand_html()}</div>', elem_classes=["hd-plain-html", "hd-login-header"])
        with gr.Column(elem_classes=["hd-login-main"], min_width=0):
            with gr.Column(elem_classes=["hd-login-stack"], min_width=0):
                with gr.Column(elem_id="hd-login-card", elem_classes=["hd-login-card"], min_width=0):
                    # HTML의 공개 value/watch API로 값과 ARIA를 동기화한다.
                    role = gr.HTML(
                        "일반 사용자", html_template=ROLE_TEMPLATE,
                        js_on_load=(UI_DIR / "assets" / "login-tabs.js").read_text(encoding="utf-8"),
                        head=FONT_HEAD, apply_default_css=False,
                        elem_id="hd-login-role", elem_classes=["hd-plain-html"],
                    )
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
                        feedback = gr.HTML(
                            "", elem_id="hd-login-feedback", elem_classes=["hd-plain-html"],
                            js_on_load=(UI_DIR / "assets" / "login-feedback.js").read_text(encoding="utf-8"),
                        )
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

    # 편집·탭 전환은 인증을 실행하지 않고 이전 입력 오류만 지운다.
    for event in (role.change, username.input, password.input):
        event(None, inputs=[], outputs=[feedback], js="() => ''", queue=False, show_progress="hidden")
    for button, account in zip(demo_buttons, ("employee", "manager", "admin")):
        selected_role = "일반 사용자" if account == "employee" else "관리자"
        if demo_mode:
            button.click(None, inputs=[], outputs=[username, password, role, feedback],
                         js=f"() => ['{account}', 'demo1234', '{selected_role}', '']", queue=False, show_progress="hidden")
    password_toggle.click(None, inputs=[], outputs=[], queue=False, js="""() => {
      const input = document.querySelector('#hd-login-password input');
      const button = document.querySelector('#hd-password-toggle');
      if (!input) return;
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      button?.setAttribute('aria-label', visible ? '비밀번호 숨기기' : '비밀번호 표시');
      button?.setAttribute('aria-pressed', String(visible));
    }""")
    return {
        "container": container, "role": role, "username": username, "password": password,
        "submit": submit, "feedback": feedback, "demo_hint": demo_hint,
    }
