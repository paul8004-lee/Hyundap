"""버전별 청크 ID·순번·해시 기준 목록의 영속화. 본문·벡터는 저장하지 않는다."""

from sqlalchemy import text

from .errors import DbError
from .runtime import require_hash, require_id, transaction


def save_manifest(version_id, chunks, *, connection=None):
    """처리 중인 버전에 완전한 청크 목록을 한 번 저장한다. 같은 목록 재시도는 허용한다.

    chunks는 id/chunk_index/content_hash를 가진 dict 목록이다. 순번은 0부터 연속이며
    입력 순서는 무관하다. 기존 목록의 추가·누락·내용 변경은 CONFLICT로 거부한다.
    버전 행 잠금과 목록 저장·예상 개수 변경을 같은 TX에서 수행한다.
    """
    require_id(version_id)
    if not isinstance(chunks, list) or not 1 <= len(chunks) <= 100000:
        raise DbError('INVALID_INPUT')
    normalized = []
    for chunk in chunks:
        if not isinstance(chunk, dict) or not {'id', 'chunk_index', 'content_hash'} <= chunk.keys():
            raise DbError('INVALID_INPUT')
        index = chunk['chunk_index']
        if type(index) is not int or not 0 <= index < 100000 or chunk['id'] != f'{version_id}:{index:05d}':
            raise DbError('INVALID_INPUT')
        normalized.append({'id': chunk['id'], 'chunk_index': index,
                           'content_hash': require_hash(chunk['content_hash'])})
    normalized.sort(key=lambda row: row['chunk_index'])
    if [row['chunk_index'] for row in normalized] != list(range(len(normalized))):
        raise DbError('INVALID_INPUT')
    with transaction(connection) as database:
        status = database.execute(text('SELECT version_status FROM document_versions WHERE id=:id FOR UPDATE'),
                                  {'id': version_id}).scalar_one_or_none()
        if status != 'processing':
            raise DbError('INVALID_STATE')
        existing = [dict(row) for row in database.execute(text('''
            SELECT id,chunk_index,content_hash FROM document_chunks WHERE version_id=:id ORDER BY chunk_index
        '''), {'id': version_id}).mappings()]
        if existing:
            if existing != normalized:
                raise DbError('CONFLICT')
            return
        database.execute(text('''
            INSERT INTO document_chunks (id,version_id,chunk_index,content_hash)
            VALUES (:id,:version_id,:chunk_index,:content_hash)
        '''), [dict(row, version_id=version_id) for row in normalized])
        database.execute(text('UPDATE document_versions SET expected_chunk_count=:count WHERE id=:id'),
                         {'count': len(normalized), 'id': version_id})


def get_expected_chunk_ids(version_id, *, connection=None):
    """서버 내부 완전성 비교용 ID 목록을 순번대로 반환한다. 없는 버전은 빈 목록이다."""
    with transaction(connection) as database:
        return list(database.execute(text('''
            SELECT id FROM document_chunks WHERE version_id=:id ORDER BY chunk_index
        '''), {'id': require_id(version_id)}).scalars())
