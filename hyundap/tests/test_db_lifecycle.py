"""ASGI 정상 종료에 회사·공통 풀 정리가 연결되는지 확인한다."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from starlette.applications import Starlette
from starlette.testclient import TestClient
from hyundap.db import lifecycle


class LifecycleTests(TestCase):
    def test_shutdown_disposes_existing_pools_and_clears_control_cache(self):
        engine = Mock()
        getter = Mock(return_value=engine)
        getter.cache_info.return_value = SimpleNamespace(currsize=1)
        with patch.object(lifecycle.runtime, 'get_engine', getter), \
                patch.object(lifecycle.tenancy, 'close_company_engines') as close:
            with TestClient(Starlette(lifespan=lifecycle.service_lifespan)):
                close.assert_not_called()
            close.assert_called_once()
            engine.dispose.assert_called_once()
            getter.cache_clear.assert_called_once()

    def test_shutdown_does_not_create_a_new_database_connection(self):
        getter = Mock()
        getter.cache_info.return_value = SimpleNamespace(currsize=0)
        with patch.object(lifecycle.runtime, 'get_engine', getter), \
                patch.object(lifecycle.tenancy, 'close_company_engines'):
            lifecycle.close_service_engines()
        getter.assert_not_called()

    def test_gradio_application_runs_database_shutdown_hook(self):
        import gradio as gr
        from gradio.routes import App
        with gr.Blocks(analytics_enabled=False) as blocks:
            gr.Textbox()
        app = App.create_app(blocks, app_kwargs={'lifespan': lifecycle.service_lifespan})
        with patch.object(lifecycle, 'close_service_engines') as close:
            with TestClient(app) as client:
                # launch 이전 App.create_app 시험은 HTML theme을 준비하지 않는다.
                # 실제 Gradio HTTP 라우터와 수명 훅을 확인하고 화면 검증과 구분한다.
                self.assertEqual(client.get('/config').status_code, 200)
                close.assert_not_called()
            close.assert_called_once()
