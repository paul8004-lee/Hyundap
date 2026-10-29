"""UI 구성 계약: 컨트롤러가 갱신할 실제 Gradio 컴포넌트를 앱 생성 시 확인한다.

없는 키를 자동 생성하는 테스트 대역으로는 실행 중인 화면을 검증하지 않는다.
이 검사는 DB·인증 함수를 호출하지 않으며 사용자 입력이나 세션을 기록하지 않는다.
"""
from collections.abc import Mapping

import gradio as gr


REQUIRED_REFS = {
    "login": ("container", "role", "username", "password", "submit", "feedback", "demo_hint"),
    "search": ("container", "intro", "company", "keyword", "submit", "answer", "feedback", "quick", "doc_type", "reset", "empty", "results_area", "table", "caption", "detail", "summary", "coverage", "summarize", "evidence", "evidence_nav", "evidence_prev", "evidence_next", "evidence_position", "detail_feedback", "source", "download", "download_file"),
    "admin": ("container", "company", "doc_type", "file", "submit", "feedback", "result"),
    "shell": ("container", "nav_search", "upload_nav", "account", "footer_company", "logout", "modal", "modal_body", "modal_close"),
}


def validate_component_contract(groups, *, registered, outputs):
    """필수 키·실제 컴포넌트·이벤트 출력 등록을 확인한다."""
    issues = []
    valid_types = (gr.components.Component, gr.layouts.Column, gr.layouts.Row)
    output_ids = {id(component) for component in outputs}
    for group, names in REQUIRED_REFS.items():
        refs = groups.get(group)
        if not isinstance(refs, Mapping):
            issues.append(group)
            continue
        for name in names:
            label = f"{group}.{name}"
            # refs[name]만 읽으면 __missing__이 키 누락을 숨길 수 있다.
            if name not in refs:
                issues.append(label + " (missing)")
                continue
            component = refs[name]
            if not isinstance(component, valid_types):
                issues.append(label + " (invalid component)")
            elif registered.get(component._id) is not component:
                issues.append(label + " (not registered)")
            elif id(component) not in output_ids:
                issues.append(label + " (not in event outputs)")
    if issues:
        raise ValueError("UI component contract: " + ", ".join(issues))
