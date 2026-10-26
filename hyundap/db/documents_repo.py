"""회사별 중복 조회·문서/후보 버전 생성·검색용 원시 행 일괄 조회."""

import mimetypes
from uuid import uuid4

from sqlalchemy import bindparam, inspect, text

from hyundap.auth.errors import AccessError
from hyundap.common.schemas import DEFAULT_DOC_TYPE, DOC_TYPE_CODES
from .errors import DbError
from .runtime import require_hash, require_id, transaction
from .sessions_repo import current_actor
from . import legacy


DOCUMENT_INFO_SQL = '''
    SELECT d.id AS document_id,d.company_id,c.name AS company_name,d.doc_type,
           d.publication_status,d.deleted_at,d.active_version_id,
           v.version_no,v.version_status,v.data_category,v.file_name
    FROM documents d JOIN companies c ON c.id=d.company_id
    LEFT JOIN document_versions v ON v.id=d.active_version_id
      AND v.document_id=d.id AND v.company_id=d.company_id
    WHERE d.id IN :ids
'''


def find_duplicate_document(company_id, sha256, *, exclude_version_id=None, connection=None):
    """같은 회사·해시의 삭제/실패 포함 후보를 dict로 반환한다. 없으면 None이다.

    권한 확인된 서버 처리에서만 사용한다. 제외 ID는 서버가 확정한 현재 후보이며
    같은 회사·해시의 존재하는 버전이어야 한다. 불일치는 INVALID_INPUT이다.
    연결을 받으면 호출자 TX에 참여하고 커밋하지 않는다. 생략하면 자체 읽기 TX다.
    삭제/실패 후보도 포함하며 조회만으로 동시 중복을 막지는 못한다.
    """
    values = {'company': require_id(company_id), 'sha256': require_hash(sha256)}
    if exclude_version_id is not None:
        values['exclude'] = require_id(exclude_version_id)
    with transaction(connection) as database:
        if exclude_version_id is not None:
            candidate = database.execute(text('''
                SELECT id FROM document_versions
                WHERE id=:exclude AND company_id=:company AND sha256=:sha256
            '''), values).scalar_one_or_none()
            if candidate is None:
                raise DbError('INVALID_INPUT')
        query = '''
            SELECT d.id AS document_id,v.id AS version_id,v.version_status,d.deleted_at
            FROM document_versions v JOIN documents d ON d.id=v.document_id AND d.company_id=v.company_id
            WHERE v.company_id=:company AND v.sha256=:sha256
        '''
        if exclude_version_id is not None:
            query += ' AND v.id<>:exclude'
        row = database.execute(text(query), values).mappings().one_or_none()
        return dict(row) if row else None


def get_documents_with_active_version(document_ids, *, connection=None):
    """검색용 11필드 원시 행 목록을 반환한다. 없는 ID는 누락하고 빈 입력은 []다.

    서버 내부 함수다. DTO 변환·사용자 회사 범위·공개 판정은 search에서 수행한다.
    DB 장애는 DbError이며 빈 검색 결과로 바꾸지 않는다. 입력 순서는 보장하지 않는다.
    """
    if not isinstance(document_ids, (list, tuple)):
        raise DbError('INVALID_INPUT')
    ids = sorted({require_id(value) for value in document_ids})
    if not ids:
        return []
    query = text(DOCUMENT_INFO_SQL).bindparams(bindparam('ids', expanding=True))
    with transaction(connection) as database:
        return [dict(row) for row in database.execute(query, {'ids': ids}).mappings()]


def validate_metadata(metadata):
    """업로드 계약의 필수 값·길이·분류·해시를 검사해 새 dict로 돌려준다.

    파일 검증·원본 저장은 여기서 수행하지 않는다. storage_path는 서버가 정한 내부 경로다.
    doc_type 생략은 공통 기본값이며 명시적 None/빈 값/미등록 코드는 INVALID_INPUT이다.
    입력을 변경하거나 DB에 연결하지 않는다.
    """
    if not isinstance(metadata, dict):
        raise DbError('INVALID_INPUT')
    fields = ('company_id', 'file_name', 'storage_path', 'sha256', 'file_size', 'data_category')
    if not set(fields) <= metadata.keys():
        raise DbError('INVALID_INPUT')
    result = {name: metadata[name] for name in fields}
    result['doc_type'] = metadata.get('doc_type', DEFAULT_DOC_TYPE)
    require_id(result['company_id'])
    require_hash(result['sha256'])
    if not isinstance(result['doc_type'], str) or result['doc_type'] not in DOC_TYPE_CODES:
        raise DbError('INVALID_INPUT')
    if result['data_category'] not in ('operational', 'evaluation'):
        raise DbError('INVALID_INPUT')
    for name, limit in (('file_name', 255), ('storage_path', 1024)):
        value = result[name]
        if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
            raise DbError('INVALID_INPUT')
    size = result['file_size']
    if type(size) is not int or not 1 <= size <= 104857600:
        raise DbError('INVALID_INPUT')
    if metadata.get('document_id') is not None:
        result['document_id'] = require_id(metadata['document_id'])
    return result


def create_document_version(actor, metadata, *, connection=None):
    """관리자 권한 확인 후 문서·후보 버전·작업을 같은 짧은 TX에서 생성한다.

    반환값은 document_id/version_id/job_id dict다. 문서 ID를 주면 새 버전을 만든다.
    회사 행을 잠가 동시 중복 확인·생성을 직렬화하고 문서 잠금으로 버전 번호를 할당한다.
    미이행 레거시 문서가 있는 회사는 계약대로 신규 업로드를 거부한다. 원본 저장은 #122다.
    """
    data = validate_metadata(metadata)
    with transaction(connection) as database:
        # 세 단계가 같은 연결을 사용하므로 일부 저장만 성공한 상태가 남지 않는다.
        current = _check_candidate_creation(database, actor, data)
        data['uploaded_by'] = current['user_id']
        document_id = _prepare_document(database, data)
        return _insert_version_and_job(database, document_id, data)


def _check_candidate_creation(database, actor, data):
    """권한·기존 구조·중복을 검사하고 DB에서 재검증한 현재 사용자 dict를 반환한다.

    호출자의 같은 트랜잭션에서 세션·사용자·회사 순서로 잠근다.
    회사 잠금은 다른 요청의 중복 확인과 후보 생성이 동시에 진행되지 않게 한다.
    """
    current = current_actor(actor, connection=database, lock=True)
    if current['role'] not in ('platform_admin', 'company_admin'):
        raise AccessError('ACCESS_DENIED')
    if current['role'] != 'platform_admin' and current['company_id'] != data['company_id']:
        raise AccessError('ACCESS_DENIED')
    active = database.execute(text('SELECT is_active FROM companies WHERE id=:id FOR UPDATE'),
                              {'id': data['company_id']}).scalar_one_or_none()
    if not active:
        raise AccessError('ACCESS_DENIED')
    legacy = database.execute(text('''
        SELECT d.id FROM documents d WHERE d.company_id=:company
          AND NOT EXISTS (SELECT 1 FROM document_versions v WHERE v.document_id=d.id)
        LIMIT 1
    '''), {'company': data['company_id']}).first()
    if legacy:
        raise DbError('INVALID_STATE')
    duplicate = find_duplicate_document(data['company_id'], data['sha256'], connection=database)
    if duplicate:
        raise DbError('DUPLICATE_FILE')
    return current


def _prepare_document(database, data):
    """검증된 업로드 dict로 기존 문서를 확인하거나 새 문서를 만들고 문서 ID를 반환한다.

    기존 문서는 회사·상태·유형이 맞아야 한다. 문서 잠금은 버전 번호를 저장할 때까지
    호출자의 트랜잭션에서 유지하므로 같은 문서에 버전 번호가 중복되지 않는다.
    """
    document_id = data.get('document_id')
    if not document_id:
        document_id = str(uuid4())
    if 'document_id' in data:
        document = database.execute(text('''
            SELECT company_id,doc_type,deleted_at,publication_status FROM documents
            WHERE id=:id FOR UPDATE
        '''), {'id': document_id}).mappings().one_or_none()
        if not document or document['company_id'] != data['company_id']:
            raise AccessError('ACCESS_DENIED')
        if document['deleted_at'] is not None or document['publication_status'] == 'blocked':
            raise DbError('INVALID_STATE')
        if document['doc_type'] != data['doc_type']:
            raise DbError('INVALID_INPUT')
    else:
        legacy.insert(database, 'documents',
                      {'id': document_id, 'company_id': data['company_id'], 'doc_type': data['doc_type']},
                      {'filename': data['file_name'], 'storage_key': data['storage_path'],
                       'media_type': mimetypes.guess_type(data['file_name'])[0] or 'application/octet-stream',
                       'size_bytes': data['file_size'], 'sha256': data['sha256'],
                       'uploaded_by': data['uploaded_by'], 'created_at': legacy.epoch_now,
                       'updated_at': legacy.epoch_now})
    return document_id


def _insert_version_and_job(database, document_id, data):
    """문서 ID와 검증된 업로드 dict로 버전·작업을 저장하고 세 가지 ID를 dict로 반환한다.

    앞 단계에서 잠근 같은 DB 연결을 사용한다. 작업 저장이 실패하면 버전과 새 문서도
    호출자의 트랜잭션에서 함께 취소된다. 이 함수는 직접 커밋하지 않는다.
    """
    number = database.execute(text('''
        SELECT COALESCE(MAX(version_no),0)+1 FROM document_versions WHERE document_id=:id
    '''), {'id': document_id}).scalar_one()
    version_id = str(uuid4())
    job_id = str(uuid4())
    values = dict(data, id=version_id, document_id=document_id, version_no=number)
    database.execute(text('''
        INSERT INTO document_versions
         (id,document_id,company_id,version_no,file_name,storage_path,sha256,file_size,data_category)
        VALUES (:id,:document_id,:company_id,:version_no,:file_name,:storage_path,:sha256,:file_size,:data_category)
    '''), values)
    legacy_columns = {column['name'] for column in inspect(database).get_columns('documents')}
    revision = {'generation': 0, 'content_revision': 0}
    if {'generation', 'content_revision'} <= legacy_columns:
        database.execute(text('UPDATE documents SET generation=generation+1 WHERE id=:id'), {'id': document_id})
        revision = database.execute(text('SELECT generation,content_revision FROM documents WHERE id=:id'),
                                    {'id': document_id}).mappings().one()
    legacy.insert(database, 'index_jobs', {'id': job_id, 'version_id': version_id},
                  {'document_id': document_id, 'company_id': data['company_id'],
                   'generation': revision['generation'], 'content_revision': revision['content_revision'],
                   'attempt_no': 1, 'requested_by': data['uploaded_by'],
                   'created_at': legacy.epoch_now, 'updated_at': legacy.epoch_now})
    return {'document_id': document_id, 'version_id': version_id, 'job_id': job_id}
