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
    normalized = _normalize_manifest(version_id, chunks)
    with transaction(connection) as database:
        status = database.execute(text('SELECT version_status FROM document_versions WHERE id=:id FOR UPDATE'),
                                  {'id': version_id}).scalar_one_or_none()
        if status != 'processing':
            raise DbError('INVALID_STATE')
        result_rows = database.execute(text('''
            SELECT id,chunk_index,content_hash FROM document_chunks WHERE version_id=:id ORDER BY chunk_index
        '''), {'id': version_id}).mappings()
        existing = []
        for row in result_rows:
            existing.append(dict(row))
        if existing:
            if existing != normalized:
                raise DbError('CONFLICT')
            return
        insert_rows = []
        for row in normalized:
            insert_row = dict(row)
            insert_row['version_id'] = version_id
            insert_rows.append(insert_row)
        database.execute(text('''
            INSERT INTO document_chunks (id,version_id,chunk_index,content_hash)
            VALUES (:id,:version_id,:chunk_index,:content_hash)
        '''), insert_rows)
        database.execute(text('UPDATE document_versions SET expected_chunk_count=:count WHERE id=:id'),
                         {'count': len(normalized), 'id': version_id})


def _normalize_manifest(version_id, chunks):
    """청크 목록의 필수 값·ID·해시·연속 순번을 검사하고 순번대로 정렬한 새 목록을 반환한다."""
    if not isinstance(chunks, list) or not 1 <= len(chunks) <= 100000:
        raise DbError('INVALID_INPUT')
    normalized = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise DbError('INVALID_INPUT')
        for field in ('id', 'chunk_index', 'content_hash'):
            if field not in chunk:
                raise DbError('INVALID_INPUT')
        index = chunk['chunk_index']
        if type(index) is not int:
            raise DbError('INVALID_INPUT')
        if not 0 <= index < 100000:
            raise DbError('INVALID_INPUT')
        expected_id = f'{version_id}:{index:05d}'
        if chunk['id'] != expected_id:
            raise DbError('INVALID_INPUT')
        normalized.append({'id': chunk['id'], 'chunk_index': index,
                           'content_hash': require_hash(chunk['content_hash'])})
    normalized.sort(key=_chunk_index)
    for expected_index, chunk in enumerate(normalized):
        if chunk['chunk_index'] != expected_index:
            raise DbError('INVALID_INPUT')
    return normalized


def _chunk_index(chunk):
    """청크 dict에서 정렬 기준인 순번을 반환한다. sort가 항목마다 이 함수를 호출한다."""
    return chunk['chunk_index']


def get_expected_chunk_ids(version_id, *, connection=None):
    """서버 내부 완전성 비교용 ID 목록을 순번대로 반환한다. 없는 버전은 빈 목록이다."""
    with transaction(connection) as database:
        return list(database.execute(text('''
            SELECT id FROM document_chunks WHERE version_id=:id ORDER BY chunk_index
        '''), {'id': require_id(version_id)}).scalars())
