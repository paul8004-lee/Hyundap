"""로컬 수동 UI 점검. 실행 시에만 Gradio·실제 임베딩 API를 사용한다.

실행: python -m hyundap.tests.smoke.ui_db_smoke
UI_DB_TEST_URL은 로컬 빈 시험 DB만 허용한다. 운영 인증/LLM 구현이 아니다.
"""
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from hyundap.common.config import get_settings
from hyundap.db import runtime, schema
from .services import DatabaseSmokeServices


def main():
    from hyundap.app import APP_JS, build_app
    from hyundap.ui.components import get_theme

    url = make_url(os.environ['UI_DB_TEST_URL'])
    if url.host != '127.0.0.1' or url.database not in ('hyundap_ui_test', 'hyundap_semantic_test'):
        raise RuntimeError('전용 로컬 UI 시험 DB만 허용합니다.')
    engine = create_engine(url, isolation_level='READ COMMITTED', hide_parameters=True,
                           connect_args={'init_command': "SET time_zone='+00:00'"})
    try:
        schema.create_v4_schema(engine)
        runtime.get_engine = lambda: engine
        with TemporaryDirectory(prefix='hyundap-ui-originals-', ignore_cleanup_errors=True) as directory:
            # UI 시험이 기존 벡터 자료와 섞이지 않도록 새 경로를 실행마다 사용한다.
            os.environ['CHROMA_DB_PATH'] = str(Path(directory) / 'chroma')
            get_settings.cache_clear()
            service = DatabaseSmokeServices(engine, Path(directory))
            if os.environ.get('UI_SEMANTIC_SEED') == '1':
                from .semantic_embedding_smoke import seed_samples
                asyncio.run(seed_samples(service, Path(directory)))
                print('SEMANTIC_UI_READY: 3 synthetic documents indexed with real embeddings', flush=True)
            app = build_app(service)
            app.title = 'Hyundap DB 시험 · 실제 임베딩·Chroma · 인증 대역'
            port = int(os.environ.get('UI_DB_TEST_PORT', '7861'))
            app.launch(server_name='127.0.0.1', server_port=port, share=False,
                       theme=get_theme(), css=app.hyundap_css, js=APP_JS, footer_links=[])
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
