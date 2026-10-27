"""사용성 테스트 전용 실행기. 화면은 그대로 쓰고 로그인만 실제 인증으로 바꾼다.

저장소 맨 위(hyundap-mvp)에서 실행: .venv/Scripts/python.exe hyundap/tests/usability/run_gui.py
127.0.0.1:7860에서만 열린다. app.py의 기본 체험 로그인은 바꾸지 않는다.
"""
import sys
from pathlib import Path

# 저장소 맨 위를 import 경로에 넣어 hyundap 패키지를 찾게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import hyundap.auth.session_service as real_auth  # noqa: E402
from hyundap.app import APP_JS, build_app  # noqa: E402
from hyundap.ui.backend_bindings import create_bindings  # noqa: E402
from hyundap.ui.components import get_theme  # noqa: E402
from hyundap.ui.services import BackendServices  # noqa: E402

service = BackendServices(create_bindings(authentication=real_auth))
demo = build_app(service)
demo.launch(server_name='127.0.0.1', server_port=7860, share=False, theme=get_theme(),
            css=demo.hyundap_css, js=APP_JS, footer_links=[], show_error=False,
            max_file_size=100 * 1024 * 1024)
