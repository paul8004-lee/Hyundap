"""회사별 현재 권한 조회와 문서 접근 재검증."""

from sqlalchemy import bindparam, text

from .errors import AccessError
from hyundap.db.errors import DbError
from hyundap.db.runtime import require_id, transaction, shared_table, split_layout
from hyundap.db.sessions_repo import current_actor


def allowed_companies(current, *, connection):
    """재인증한 사용자 문맥으로 현재 유효한 회사 ID 집합을 반환하는 내부 함수."""
    query = f"SELECT id FROM {shared_table('companies')} WHERE is_active=1"
    values = {}
    if current['role'] != 'platform_admin':
        query += ' AND id=:company_id'
        values['company_id'] = current['company_id']
    result = set(connection.execute(text(query), values).scalars())
    if not result:
        raise AccessError('ACCESS_DENIED')
    return result


def get_allowed_company_ids(actor):
    """세션을 재검증하고 허용 회사 집합을 반환한다. 사용자 선택 회사와의 조합은 search 책임이다."""
    with transaction() as database:
        return allowed_companies(current_actor(actor, connection=database), connection=database)


def verify_documents_access(actor, document_version_pairs):
    """답변·요약 반환 직전에 모든 인용 문서·버전을 새 DB 조회로 확인한다.

    입력은 (문서 ID, 버전 ID) 쌍의 비어 있지 않은 목록이다. 권한 밖 회사는
    ACCESS_DENIED, 삭제·공개·버전 상태 변경은 STATE_CHANGED다. 일부만 통과시켜
    반환하지 않으며 성공 시 None이다. DB 장애는 빈 결과가 아닌 DbError로 전달한다.
    """
    if not isinstance(document_version_pairs, (list, tuple)) or not document_version_pairs:
        raise DbError('INVALID_INPUT')
    refs = set()
    for pair in document_version_pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise DbError('INVALID_INPUT')
        refs.add((require_id(pair[0]), require_id(pair[1])))
    if split_layout():
        # 여러 회사의 인용은 회사별로 읽되 하나라도 실패하면 전체 응답을 거부한다.
        from hyundap.db.documents_repo import get_document_detail
        for document_id, version_id in refs:
            get_document_detail(actor, document_id, version_id=version_id)
        return
    with transaction() as database:
        current = current_actor(actor, connection=database)
        allowed = allowed_companies(current, connection=database)
        query = text('''
            SELECT d.id,d.company_id,d.publication_status,d.deleted_at,d.active_version_id,
                   v.version_status,v.data_category
            FROM documents d LEFT JOIN document_versions v
              ON v.id=d.active_version_id AND v.document_id=d.id AND v.company_id=d.company_id
            WHERE d.id IN :ids
        ''').bindparams(bindparam('ids', expanding=True))
        document_ids = set()
        for document_id, version_id in refs:
            document_ids.add(document_id)
        result_rows = database.execute(query, {'ids': sorted(document_ids)}).mappings()
        documents_by_id = {}
        for row in result_rows:
            documents_by_id[row['id']] = row
        for document_id, version_id in refs:
            row = documents_by_id.get(document_id)
            if row and row['company_id'] not in allowed:
                raise AccessError('ACCESS_DENIED')
            _check_document_state(row, version_id)


def _check_document_state(document, version_id):
    """조회한 문서 행이 요청한 운영 버전으로 공개 중인지 확인한다. 반환값은 없다."""
    if not document:
        raise AccessError('STATE_CHANGED')
    if document['deleted_at'] is not None:
        raise AccessError('STATE_CHANGED')
    if document['publication_status'] != 'published':
        raise AccessError('STATE_CHANGED')
    if document['active_version_id'] != version_id:
        raise AccessError('STATE_CHANGED')
    if document['version_status'] != 'ready':
        raise AccessError('STATE_CHANGED')
    if document['data_category'] != 'operational':
        raise AccessError('STATE_CHANGED')
