"""로컬 DB UI 확인 전용. 실제 MySQL·수집기·임베딩·Chroma, 합성 세션을 사용한다.

운영 인증/LLM 구현이 아니다. UI_DB_TEST_URL은 127.0.0.1의 빈 시험 DB만
허용하며 기존 DB에는 fixture를 적용하지 않는다. 임베딩은 실제 API를 호출한다.
"""
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from hyundap.app import APP_JS, build_app
from hyundap.auth.permissions import verify_documents_access
from hyundap.common.config import get_settings
from hyundap.db import documents_repo, runtime, schema, upload_service, version_service
from hyundap.db.errors import DbError
from hyundap.ui.components import get_theme
from hyundap.ui.mock_services import MockServices
from hyundap.ui.services import BackendServices, COMPANIES, UIServiceError, to_plain
from hyundap.search import search as search_documents
from hyundap.search.errors import SearchError
from .test_db_runtime import session_fixture
from .test_schema_migration import BASELINE, execute_script


class DatabaseSmokeServices(MockServices):
    """인증만 합성 계정을 사용하고 실제 업로드·벡터 검색 함수를 연결한다."""
    demo = False

    def __init__(self, engine, directory):
        super().__init__()
        self.engine = engine
        self.directory = directory
        self.embedding = version_service.get_embedding_backend()
        with engine.begin() as db:
            db.execute(text('INSERT INTO companies (id,name) VALUES (:id,:name)'),
                       [{'id': code, 'name': label} for label, code in COMPANIES])
            db.execute(text("INSERT INTO users (id,company_id,email,role) VALUES ('ui-manager','hri','ui@test.invalid','COMPANY_ADMIN')"))
        self.actor = session_fixture(engine, 'ui-manager', 'hri')

    async def login(self, username, password):
        if username != 'manager':
            raise UIServiceError('INVALID_CREDENTIALS', 'DB 시험 계정은 manager입니다.')
        return await super().login(username, password)

    async def upload(self, session, file_path, company_id, doc_type):
        self._checked(session)
        try:
            result = await asyncio.to_thread(upload_service.upload_document,
                self.actor, file_path, {'company_id': company_id, 'doc_type': doc_type},
                embedding=self.embedding, upload_dir=self.directory)
        except DbError as error:
            message = '같은 회사에 이미 저장된 파일입니다.' if error.code == 'DUPLICATE_FILE' else 'DB 시험 요청 실패: ' + error.code
            raise UIServiceError(error.code, message) from None
        return dict(result, status='succeeded' if result['status'] == 'success' else 'failed',
                    file_name=Path(file_path).name, searchable=result['status'] == 'success',
                    message='실제 MySQL·임베딩·Chroma 저장 및 게시 완료' if result['status'] == 'success'
                    else '처리 실패: ' + str(result.get('error_code')))

    async def search(self, session, query, company_ids=None, doc_types=None):
        self._checked(session)
        try:
            response = await asyncio.to_thread(search_documents, self.actor, query,
                company_ids=company_ids, doc_types=doc_types, limit=10)
        except SearchError as error:
            raise UIServiceError(error.code, error.message) from None
        plain = to_plain(response)
        return dict(plain, results=[BackendServices._document(row) for row in plain['results']])

    async def detail(self, session, document_id, version_id=None):
        self._checked(session)
        row = documents_repo.get_documents_with_active_version([document_id])[0]
        verify_documents_access(self.actor, [(document_id, version_id or row['active_version_id'])])
        hits = self.embedding['get_document_chunks'](document_id, row['active_version_id'])
        return dict(row, version_id=row['active_version_id'], excerpt=' '.join(h.text for h in hits),
                    citations=[], location_label='DB UI 시험', score=1.0)

    async def ask(self, *args, **kwargs):
        raise UIServiceError('TEST_SCOPE', '이 실행은 DB 업로드·조회 시험용입니다. LLM은 연결하지 않았습니다.')

    async def summarize(self, *args, **kwargs):
        raise UIServiceError('TEST_SCOPE', '이 실행은 DB 업로드·조회 시험용입니다. LLM은 연결하지 않았습니다.')


def main():
    url = make_url(os.environ['UI_DB_TEST_URL'])
    if url.host != '127.0.0.1' or url.database not in ('hyundap_ui_test', 'hyundap_semantic_test'):
        raise RuntimeError('전용 로컬 UI 시험 DB만 허용합니다.')
    engine = create_engine(url, isolation_level='READ COMMITTED', hide_parameters=True)
    try:
        with engine.begin() as db:
            if inspect(db).get_table_names():
                raise RuntimeError('기존 테이블이 있는 DB는 사용하지 않습니다.')
            execute_script(db, BASELINE)
            execute_script(db, schema.prepare_sql(schema.snapshot(db)))
            db.execute(text('UPDATE schema_migrations SET version=4'))
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
