"""권한 있는 관리자의 버전 이력 조회와 실패 후보 상태 기록."""

from sqlalchemy import text

from hyundap.auth.errors import AccessError
from hyundap.auth.permissions import allowed_companies
from .errors import DbError
from .runtime import require_id, transaction
from .sessions_repo import current_actor


def list_document_versions(actor, document_id, *, connection=None):
    """회사 권한이 있는 관리자의 버전 이력을 순번대로 반환한다. 내부 경로는 제외한다.

    일반 직원에게 비공개·과거 버전 관리 이력을 공개하지 않는다. 검색 정보 조회는 별도다.
    """
    with transaction(connection) as database:
        current = current_actor(actor, connection=database)
        allowed_company_ids = allowed_companies(current, connection=database)
        document_company_id = database.execute(text('SELECT company_id FROM documents WHERE id=:id'),
                                               {'id': require_id(document_id)}).scalar_one_or_none()
        if current['role'] not in ('platform_admin', 'company_admin') or document_company_id not in allowed_company_ids:
            raise AccessError('ACCESS_DENIED')
        return [dict(row) for row in database.execute(text('''
            SELECT id AS version_id,document_id,version_no,file_name,file_size,version_status,
                   data_category,expected_chunk_count,created_at
            FROM document_versions WHERE document_id=:id ORDER BY version_no
        '''), {'id': document_id}).mappings()]


def mark_failed(version_id, *, connection=None):
    """서버 내부에서 비활성 처리 후보만 failed로 바꾼다. 정상 활성 버전은 보존한다.

    게시 흐름과 동일하게 문서→버전 순서로 잠근다. 상세 실패 사유는 jobs_repo가 기록한다.
    이미 실패한 후보의 재호출은 성공으로 처리한다.
    """
    with transaction(connection) as database:
        document_id = database.execute(text('SELECT document_id FROM document_versions WHERE id=:id'),
                                       {'id': require_id(version_id)}).scalar_one_or_none()
        if document_id is None:
            raise DbError('INVALID_STATE')
        active_version_id = database.execute(text('SELECT active_version_id FROM documents WHERE id=:id FOR UPDATE'),
                                             {'id': document_id}).scalar_one_or_none()
        version_status = database.execute(text('SELECT version_status FROM document_versions WHERE id=:id FOR UPDATE'),
                                          {'id': version_id}).scalar_one_or_none()
        if active_version_id == version_id or version_status not in ('processing', 'failed'):
            raise DbError('INVALID_STATE')
        database.execute(text("UPDATE document_versions SET version_status='failed' WHERE id=:id"),
                         {'id': version_id})
