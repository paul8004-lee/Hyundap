"""현답 공통 UI — Owner: 박경환 / MOD-036.

B01/B02: Figma 색상·폰트·공통 레이아웃과 입력 컨트롤.
B17/B18/B20/B21/B22/B23: 오류·빈 결과·처리 중 안내.
FUN-172 apply_common_style / FUN-173 set_loading / FUN-175 show_feedback.
백엔드 인증·권한·데이터 접근을 수행하지 않는 화면 전용 모듈입니다.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Mapping

import gradio as gr

UI_DIR = Path(__file__).resolve().parent


def get_css() -> str:
    """Figma 디자인 토큰과 공통 스타일을 Gradio launch(css=...)에 전달합니다."""
    return "\n".join((UI_DIR / name).read_text(encoding="utf-8") for name in ("tokens.css", "common.css"))


def apply_common_style() -> str:
    """FUN-172: 기존 팀 명세 이름을 유지한 공통 스타일 진입점."""
    return get_css()


def get_theme() -> gr.themes.Base:
    """B01: 브라우저 다크 모드에서도 Figma의 밝은 색상을 유지합니다."""
    values = {
        "body_background_fill": "#f1f3f6", "body_text_color": "#101820",
        "block_background_fill": "#ffffff", "block_border_color": "#dfe4ed",
        "block_label_text_color": "#101820", "input_background_fill": "#ffffff",
        "input_border_color": "#c9d2df", "button_primary_background_fill": "#101820",
        "button_primary_background_fill_hover": "#243240", "button_primary_text_color": "#ffffff",
        "button_secondary_background_fill": "#ffffff", "button_secondary_background_fill_hover": "#edf0f5",
        "button_secondary_text_color": "#101820", "button_secondary_border_color": "#c9d2df",
    }
    # 기존 shared 데모의 Font 객체 사용 방식을 유지합니다. 외부 폰트 다운로드가 없습니다.
    theme = gr.themes.Base(
        primary_hue="blue", secondary_hue="blue", neutral_hue="slate",
        font=[gr.themes.Font("Noto Sans KR"), gr.themes.Font("Malgun Gothic"), gr.themes.Font("Arial"), gr.themes.Font("sans-serif")],
    ).set(**values)
    palette = theme.to_dict()["theme"]
    return theme.set(**{key: palette[key[:-5]] for key in palette if key.endswith("_dark") and key[:-5] in palette})


def show_feedback(message: str | None = "", status: str = "info") -> str:
    """FUN-175: 백엔드 메시지를 이스케이프하여 안전하게 표시합니다."""
    if not message:
        return ""
    aliases = {"failed": "error", "failure": "error", "denied": "error", "input_error": "warning", "processing": "loading", "ready": "success"}
    status = aliases.get(status, status)
    status = status if status in {"info", "success", "error", "warning", "empty", "loading"} else "info"
    icon = "hd-spinner" if status == "loading" else "hd-notice-dot"
    role = "alert" if status == "error" else "status"
    return f'<div class="hd-notice hd-notice--{status}" role="{role}" aria-live="polite"><span class="{icon}" aria-hidden="true"></span><span>{escape(str(message))}</span></div>'


def set_loading(message: str = "처리 중입니다. 잠시만 기다려 주세요.") -> str:
    """FUN-173: 버튼 잠금은 호출 화면에서, 처리 중 문구는 공통 UI에서 관리합니다."""
    return show_feedback(message, "loading")


def status_badge(label: str, status: str = "info") -> str:
    allowed = {"info", "success", "error", "warning", "neutral", "loading"}
    status = status if status in allowed else "neutral"
    return f'<span class="hd-badge hd-badge--{status}">{escape(str(label))}</span>'


def brand_html(role_label: str = "", *, inverse: bool = False) -> str:
    """B02/B16: Figma 원본의 텍스트 H 심볼과 역할 배지."""
    mode = " hd-brand--inverse" if inverse else ""
    badge = f'<span class="hd-role">{escape(role_label)}</span>' if role_label else ""
    return f'<div class="hd-brand{mode}"><span class="hd-brand-symbol" aria-hidden="true">H</span><span class="hd-brand-name">Hyundap</span>{badge}</div>'


def render_header(user: Mapping[str, Any] | None = None) -> str:
    """표시용 헤더. 화면 이동은 app.py의 실제 Gradio 버튼에 연결합니다."""
    user = user or {}
    name = str(user.get("display_name") or user.get("name") or user.get("username") or "사용자")
    role_code = str(user.get("role") or "employee")
    role = str(user.get("role_label") or {"employee": "일반 사용자", "company_admin": "기업 관리자", "platform_admin": "전체 관리자"}.get(role_code, "사용자"))
    return f'<div class="hd-header-summary" data-role="{escape(role_code, quote=True)}">' + brand_html(role, inverse=True) + f'<div class="hd-user-summary"><span class="hd-avatar" aria-hidden="true">{escape(name[:1])}</span><span title="{escape(role, quote=True)}">{escape(name)}</span></div></div>'


def section_heading(title: str, description: str = "") -> str:
    detail = f'<p>{escape(description)}</p>' if description else ""
    return f'<div class="hd-section-heading"><h1>{escape(title)}</h1>{detail}</div>'


def footer_html(note: str = "") -> str:
    return f'<div class="hd-footer-inner"><span>© Hyundap</span><span>{escape(note)}</span></div>'
