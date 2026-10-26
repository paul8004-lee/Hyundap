"""현답 통합 로그인 화면 — Owner: 박경환 / MOD-032.

FUN-156 build_login_page: Figma 9:157 / 6:8417의 일반·관리자 로그인.
B08: 실제 아이디·비밀번호 입력. B01/B02: 공통 디자인과 레이아웃.
B16/B17: 역할 선택 안내·로그인 오류·세션 만료 표시 공간.
FUN-157 on_login_submit 연결은 app.py에서 백엔드가 제공하는 함수에 위임합니다.
역할 탭은 로그인 종류 선택일 뿐이며 실제 권한은 인증 결과로 결정합니다.
"""

from __future__ import annotations

import gradio as gr

from .components import UI_DIR, brand_html, footer_html


def _role_content(role: str) -> tuple[str, str, str, str]:
    admin = role == "관리자"
    kind = "admin" if admin else "employee"
    title = "관리자 로그인" if admin else "로그인"
    description = "관리자 계정으로 로그인해 주세요." if admin else "회사 계정으로 로그인해 주세요."
    # Figma의 '출입 확인됨 10:53'은 로그인 성공을 오해하게 하므로 실제 인증 전에는 표시하지 않습니다.
    scope = "관리자 계정" if admin else "문서 검색"
    banner = f'<div class="hd-login-band hd-login-band--{kind}"><strong>{role}</strong><span>{scope}</span><i aria-hidden="true"></i></div>'
    heading = f'<div class="hd-login-heading"><h1>{title}</h1><p>{description}</p></div>'
    permission = '<div class="hd-login-permission">✓&nbsp; 권한에 맞는 관리 화면으로 이동합니다.</div>' if admin else ""
    destination = "기업 관리자는 소속 회사, 전체 관리자는<br>접근 가능한 회사의 문서와 업로드 화면을 이용합니다." if admin else "로그인하면 문서 검색 화면으로 이동합니다."
    return banner, heading, permission, f'<p class="hd-login-destination">{destination}</p>'


def build_login_page(demo_mode: bool = True) -> dict:
    """FUN-156: UI만 생성하고 인증 입력·출력 컴포넌트를 app.py에 반환합니다."""
    banner_value, heading_value, permission_value, destination_value = _role_content("일반 사용자")
    with gr.Column(elem_id="hd-login", elem_classes=["hd-login"], min_width=0) as container:
        gr.HTML(f'<div class="hd-login-brand">{brand_html()}</div>', elem_classes=["hd-plain-html"])
        with gr.Column(elem_classes=["hd-login-main"], min_width=0):
            with gr.Column(elem_id="hd-login-card", elem_classes=["hd-login-card"], min_width=0):
                role_banner = gr.HTML(banner_value, elem_classes=["hd-plain-html", "hd-login-band-wrap"])
                with gr.Column(elem_id="hd-login-form", elem_classes=["hd-login-form"], min_width=0):
                    role = gr.Radio(
                        choices=["일반 사용자", "관리자"], value="일반 사용자", label="로그인 사용자 유형",
                        show_label=False, container=False, interactive=True,
                        elem_id="hd-login-role", elem_classes=["hd-role-tabs"],
                    )
                    heading = gr.HTML(heading_value, elem_classes=["hd-plain-html", "hd-login-heading-wrap"])
                    username = gr.Textbox(
                        label="아이디", value="", placeholder="아이디를 입력하세요", lines=1, max_lines=1, interactive=True, autofocus=True,
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
                    permission_hint = gr.HTML(permission_value, elem_classes=["hd-plain-html", "hd-permission-wrap"], visible=False)
                    submit = gr.Button("로그인", variant="primary", elem_id="hd-login-submit", elem_classes=["hd-button", "hd-button--primary", "hd-login-submit"])
                    destination_hint = gr.HTML(destination_value, elem_classes=["hd-plain-html"])
                    feedback = gr.HTML("", elem_id="hd-login-feedback", elem_classes=["hd-plain-html"])
            demo_hint = gr.HTML(
                '<div class="hd-demo-login-note"><strong>화면 체험용 계정</strong><br>일반 사용자 <code>employee</code> · 기업 관리자 <code>manager</code> · 전체 관리자 <code>admin</code><br>공통 비밀번호 <code>demo1234</code></div>',
                visible=demo_mode, elem_classes=["hd-plain-html", "hd-demo-login-wrap"],
            )
        gr.HTML(footer_html("계정이나 비밀번호 문의는 소속 회사 IT 지원팀으로 연락해 주세요."), elem_classes=["hd-plain-html", "hd-login-footer"])

    def change_role(value: str):
        banner, title, permission, destination = _role_content(value)
        return banner, title, gr.update(value=permission, visible=value == "관리자"), destination, ""

    role.change(change_role, inputs=role, outputs=[role_banner, heading, permission_hint, destination_hint, feedback], queue=False, show_progress="hidden")
    # 입력값을 서버로 보내지 않고 브라우저에서 표시 여부만 바꿉니다.
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
        "role_banner": role_banner, "heading": heading, "permission_hint": permission_hint, "destination_hint": destination_hint,
    }
