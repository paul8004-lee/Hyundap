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
    # 입력을 검증한 뒤 버전을 잠그고 기존 원장과 새 원장을 비교한다.
    require_id(version_id)
    normalized = _normalize_manifest(version_id, chunks)
    with transaction(connection, version_id=version_id) as database:
        # FOR UPDATE로 버전 행을 잠가 같은 버전의 원장 저장이 겹치지 않도록 한다.
        status = database.execute(text('SELECT version_status FROM document_versions WHERE id=:id FOR UPDATE'),
                                  {'id': version_id}).scalar_one_or_none()
        if status != 'processing':
            raise DbError('INVALID_STATE')
        result_rows = database.execute(text('''
            SELECT id,chunk_index,content_hash FROM document_chunks WHERE version_id=:id ORDER BY chunk_index
        '''), {'id': version_id}).mappings()
        existing = []
        # 첫 번째 반복: DB에서 읽은 행을 비교하기 쉬운 dict 목록으로 옮긴다.
        for row in result_rows:
            existing.append(dict(row))
        # 이미 원장이 있으면 완전히 같은 재호출만 허용하고 다른 내용은 충돌로 처리한다.
        if existing:
            if existing != normalized:
                raise DbError('CONFLICT')
            return
        # 각 행에 버전 ID를 더해 일괄 INSERT에 사용할 새 목록을 만든다.
        insert_rows = []
        # 두 번째 반복: 입력 원장에 version_id를 붙여 INSERT할 목록을 만든다.
        # 위 반복의 result_rows와 여기의 normalized는 서로 다른 목록이다.
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
    # 각 항목의 필수 값과 버전에 속한 청크 ID를 확인한다.
    # 바깥 반복: 청크 목록에서 청크 하나씩 꺼낸다.
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise DbError('INVALID_INPUT')
        # 안쪽 반복: 현재 청크 하나에 필수 키 세 개가 모두 있는지 확인한다.
        # 예를 들어 청크가 2개라면 각 청크마다 키 3개를 검사한다.
        for field in ('id', 'chunk_index', 'content_hash'):
            if field not in chunk:
                raise DbError('INVALID_INPUT')
        chunk_index = chunk['chunk_index']
        if type(chunk_index) is not int:
            raise DbError('INVALID_INPUT')
        if not 0 <= chunk_index < 100000:
            raise DbError('INVALID_INPUT')
        # 청크 ID는 버전 ID와 다섯 자리 순번을 조합한다. 예: version:00001.
        expected_id = f'{version_id}:{chunk_index:05d}'
        if chunk['id'] != expected_id:
            raise DbError('INVALID_INPUT')
        normalized.append({'id': chunk['id'], 'chunk_index': chunk_index,
                           'content_hash': require_hash(chunk['content_hash'])})
    # 입력 순서와 관계없이 정렬한 뒤 순번의 누락과 중복을 검사한다.
    normalized.sort(key=_chunk_index)
    # 앞의 중첩 반복이 모두 끝난 다음, 정렬된 청크를 다시 순서대로 검사한다.
    # enumerate는 (예상 순번, 청크)를 함께 주므로 0, 1, 2...와 실제 순번을 비교한다.
    for expected_index, chunk in enumerate(normalized):
        if chunk['chunk_index'] != expected_index:
            raise DbError('INVALID_INPUT')
    return normalized


def _chunk_index(chunk):
    """청크 dict에서 정렬 기준인 순번을 반환한다. sort가 항목마다 이 함수를 호출한다."""
    return chunk['chunk_index']
