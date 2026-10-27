"""Gradio 앱을 import하지 않는 DB 시험 어댑터. 합성 인증 전용이다."""
import asyncio
from pathlib import Path

from sqlalchemy import text

from hyundap.auth.permissions import verify_documents_access
from hyundap.db import documents_repo, upload_service, version_service
from hyundap.db.errors import DbError
from hyundap.ui.mock_services import MockServices
from hyundap.ui.services import BackendServices, COMPANIES, UIServiceError, to_plain
from hyundap.search import search as search_documents
from hyundap.search.errors import SearchError
from ..test_db_runtime import session_fixture


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
