"""회사별 현재 권한 조회와 문서 접근 재검증."""

from sqlalchemy import bindparam, text

from .errors import AccessError
from hyundap.db.errors import DbError
from hyundap.db.runtime import require_id, transaction
from hyundap.db.sessions_repo import current_actor


def allowed_companies(current, *, connection):
    """재인증한 사용자 문맥으로 현재 유효한 회사 ID 집합을 반환하는 내부 함수."""
    query = 'SELECT id FROM companies WHERE is_active=1'
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
        rows = {row['id']: row for row in database.execute(query, {'ids': sorted({r[0] for r in refs})}).mappings()}
        for document_id, version_id in refs:
            row = rows.get(document_id)
            if row and row['company_id'] not in allowed:
                raise AccessError('ACCESS_DENIED')
            if (not row or row['deleted_at'] is not None or row['publication_status'] != 'published'
                    or row['active_version_id'] != version_id or row['version_status'] != 'ready'
                    or row['data_category'] != 'operational'):
                raise AccessError('STATE_CHANGED')
