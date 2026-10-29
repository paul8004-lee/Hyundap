"""회사별 중복 조회·문서/후보 버전 생성·검색용 원시 행과 상세 메타데이터 조회."""

from uuid import uuid4

from sqlalchemy import bindparam, text

from hyundap.auth.errors import AccessError
from hyundap.auth.permissions import allowed_companies
from hyundap.common.schemas import DEFAULT_DOC_TYPE, DOC_TYPE_CODES
from .errors import DbError
from .classification import validate_auto_request
from .runtime import require_hash, require_id, transaction
from .sessions_repo import current_actor


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
    # 제외 후보가 있으면 먼저 같은 회사와 같은 해시의 버전인지 확인한다.
    values = {'company': require_id(company_id), 'sha256': require_hash(sha256)}
    if exclude_version_id is not None:
        values['exclude'] = require_id(exclude_version_id)
    with transaction(connection) as database:
        if exclude_version_id is not None:
            # 제외할 버전이 같은 회사와 해시에 실제로 속하는지 먼저 확인한다.
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
        # one_or_none()은 한 행 또는 None을 반환하며 여러 행이면 오류가 난다.
        row = database.execute(text(query), values).mappings().one_or_none()
        return dict(row) if row else None


def get_documents_with_active_version(document_ids, *, connection=None):
    """문서 ID 목록으로 검색에 필요한 문서와 활성 버전 정보를 조회한다.

    document_ids는 문자열 ID의 list 또는 tuple이다. 연결을 전달받으면 해당 연결을
    사용한다. 반환값은 검색용 11필드 dict의 list이며 없는 ID는 제외한다.
    빈 입력은 DB에 접속하지 않고 []를 반환한다. 결과 순서는 입력 순서와 다를 수 있다.

    서버 내부 함수다. DTO 변환·사용자 회사 범위·공개 판정은 search에서 수행한다.
    잘못된 입력이나 DB 장애는 DbError로 알리며 장애를 빈 검색 결과로 바꾸지 않는다.
    """
    # ID를 반복해서 검사할 수 있도록 목록 또는 튜플만 받는다.
    if not isinstance(document_ids, (list, tuple)):
        raise DbError('INVALID_INPUT')
    # 각 ID를 검증하고 중복을 제거한 뒤 정렬한다.
    ids = sorted({require_id(value) for value in document_ids})
    if not ids:
        return []
    # expanding=True는 ID 개수에 맞게 SQL의 IN 조건을 안전하게 준비한다.
    query = text(DOCUMENT_INFO_SQL).bindparams(bindparam('ids', expanding=True))
    with transaction(connection) as database:
        # 조회된 행을 검색 모듈에서 읽을 수 있는 dict로 바꾼다.
        return [dict(row) for row in database.execute(query, {'ids': ids}).mappings()]


def get_document_detail(actor, document_id, *, version_id=None, connection=None):
    """현재 사용자가 읽을 수 있는 활성 운영 문서의 상세 정보 dict를 반환한다.

    version_id는 화면이 기억한 버전이다. 생략하면 현재 활성 버전을 선택한다.
    없는 문서·비공개·버전 변경은 STATE_CHANGED, 다른 회사는 ACCESS_DENIED다.
    세션 오류는 SESSION_EXPIRED, 잘못된 ID는 INVALID_INPUT으로 전달한다.
    size_bytes는 활성 버전의 파일 크기이며 indexed_at은 근거가 없어 None이다.
    내부 경로·토큰·본문은 반환하지 않는다. 외부 연결은 커밋하거나 닫지 않는다.

    이 결과는 조회 시점의 정보다. 이후 본문·요약 등 외부 작업을 수행하는 호출자는
    반환 직전에 verify_documents_access로 같은 문서·버전을 다시 검사해야 한다.
    """
    # 1단계: DB에 연결하기 전에 문서 ID와 선택적으로 받은 버전 ID를 검사한다.
    document_id = require_id(document_id)
    if version_id is not None:
        version_id = require_id(version_id)
    with transaction(connection) as database:
        # 2단계: 화면에서 전달한 역할 대신 현재 세션과 DB의 회사 권한을 사용한다.
        current = current_actor(actor, connection=database)
        allowed = allowed_companies(current, connection=database)
        # 3단계: 문서와 활성 버전을 한 번에 읽는다. 회사·문서가 다른 버전은 연결하지 않는다.
        # LEFT JOIN이므로 활성 버전이 없는 문서도 읽은 뒤 안전하게 거부할 수 있다.
        row = database.execute(text('''
            SELECT d.id AS document_id,d.company_id,c.name AS company_name,d.doc_type,
                   d.publication_status,d.deleted_at,d.active_version_id,
                   v.version_no,v.version_status,v.data_category,v.file_name,v.file_size
            FROM documents d JOIN companies c ON c.id=d.company_id
            LEFT JOIN document_versions v ON v.id=d.active_version_id
              AND v.document_id=d.id AND v.company_id=d.company_id
            WHERE d.id=:id
        '''), {'id': document_id}).mappings().one_or_none()
        _check_detail_access(row, allowed, version_id)
        # 4단계: 허용한 필드만 새 사전에 담는다. 조회용 상태 필드도 외부로 내보내지 않는다.
        # created_at은 후보 생성 시각이고 작업 finished_at은 실패 종료일 수도 있으므로
        # 둘 중 어느 것도 색인 완료 시각으로 바꾸어 표시하지 않는다.
        return {
            'document_id': row['document_id'], 'company_id': row['company_id'],
            'company_name': row['company_name'], 'version_id': row['active_version_id'],
            'file_name': row['file_name'], 'doc_type': row['doc_type'],
            'version_no': row['version_no'], 'size_bytes': row['file_size'],
            'indexed_at': None,
        }


def _check_detail_access(document, allowed_company_ids, version_id):
    """상세 조회 행의 회사·공개 상태·요청 버전을 검사한다. 성공하면 반환값은 없다.

    일반 상세 조회는 auth의 문서 접근 규칙과 동일하게 공개된 활성 운영 버전만 허용한다.
    관리자용 과거 버전 이력 조회는 versions_repo의 별도 계약을 따른다.
    """
    # 문서가 없으면 이후 필드를 읽지 않는다. 다른 회사라면 상태보다 권한을 먼저 거부한다.
    if document is None:
        raise AccessError('STATE_CHANGED')
    if document['company_id'] not in allowed_company_ids:
        raise AccessError('ACCESS_DENIED')
    if document['deleted_at'] is not None or document['publication_status'] != 'published':
        raise AccessError('STATE_CHANGED')
    if document['active_version_id'] is None or document['version_status'] != 'ready':
        raise AccessError('STATE_CHANGED')
    if document['data_category'] != 'operational':
        raise AccessError('STATE_CHANGED')
    # 버전을 지정했을 때만 비교한다. 과거 버전을 요청해도 현재 버전으로 몰래 대체하지 않는다.
    if version_id is not None and document['active_version_id'] != version_id:
        raise AccessError('STATE_CHANGED')


def validate_metadata(metadata):
    """업로드 계약의 필수 값·길이·분류·해시를 검사해 새 dict로 돌려준다.

    파일 검증·원본 저장은 여기서 수행하지 않는다. storage_path는 서버가 정한 내부 경로다.
    doc_type 생략은 공통 기본값이며 명시적 None/빈 값/미등록 코드는 INVALID_DOC_TYPE이다.
    입력을 변경하거나 DB에 연결하지 않는다.
    """
    # 필수 항목을 새 사전에 복사한 뒤 값과 범위를 차례로 확인한다.
    if not isinstance(metadata, dict):
        raise DbError('INVALID_INPUT')
    required_fields = ('company_id', 'file_name', 'storage_path', 'sha256', 'file_size', 'data_category')
    if not set(required_fields) <= metadata.keys():
        raise DbError('INVALID_INPUT')
    # 필요한 필드만 복사하므로 원본 metadata는 변경하지 않는다.
    result = {name: metadata[name] for name in required_fields}
    automatic = validate_auto_request(metadata)
    if automatic:
        result['auto_classify'] = True
    result['doc_type'] = 'other' if automatic else metadata.get('doc_type', DEFAULT_DOC_TYPE)
    require_id(result['company_id'])
    require_hash(result['sha256'])
    if not isinstance(result['doc_type'], str) or result['doc_type'] not in DOC_TYPE_CODES:
        raise DbError('INVALID_DOC_TYPE')
    if result['data_category'] not in ('operational', 'evaluation'):
        raise DbError('INVALID_INPUT')
    # 파일명과 내부 경로의 빈 문자열, 최대 길이, 널 문자를 검사한다.
    # 첫 번째 반복은 file_name과 255를, 두 번째 반복은 storage_path와 1024를 꺼낸다.
    # 두 값은 같은 검사를 받지만 각자 다른 길이 제한을 적용한다.
    for field_name, max_length in (('file_name', 255), ('storage_path', 1024)):
        value = result[field_name]
        if not isinstance(value, str) or not value.strip() or len(value) > max_length or '\x00' in value:
            raise DbError('INVALID_INPUT')
    file_size = result['file_size']
    if type(file_size) is not int or not 1 <= file_size <= 104857600:
        raise DbError('INVALID_INPUT')
    if metadata.get('document_id') is not None:
        result['document_id'] = require_id(metadata['document_id'])
    return result


def create_document_version(actor, metadata, *, connection=None):
    """관리자 권한 확인 후 문서·후보 버전·작업을 같은 짧은 TX에서 생성한다.

    반환값은 document_id/version_id/job_id dict다. 문서 ID를 주면 새 버전을 만든다.
    회사 행을 잠가 동시 중복 확인·생성을 직렬화하고 문서 잠금으로 버전 번호를 할당한다.
    버전 없는 문서가 남은 회사는 신규 업로드를 거부한다. 원본 저장은 #122다.
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
    # 전달받은 역할을 그대로 믿지 않고 현재 세션과 회사 상태를 다시 확인한다.
    current = current_actor(actor, connection=database, lock=True)
    if current['role'] not in ('platform_admin', 'company_admin'):
        raise AccessError('ACCESS_DENIED')
    if current['role'] != 'platform_admin' and current['company_id'] != data['company_id']:
        raise AccessError('ACCESS_DENIED')
    active = database.execute(text('SELECT is_active FROM companies WHERE id=:id FOR UPDATE'),
                              {'id': data['company_id']}).scalar_one_or_none()
    if not active:
        raise AccessError('ACCESS_DENIED')
    # 버전이 없는 문서가 남아 있으면 업로드를 중단한다.
    orphan_document = database.execute(text('''
        SELECT d.id FROM documents d WHERE d.company_id=:company
          AND NOT EXISTS (SELECT 1 FROM document_versions v WHERE v.document_id=d.id)
        LIMIT 1
    '''), {'company': data['company_id']}).first()
    if orphan_document:
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
    # 문서 ID가 있으면 기존 문서를 잠가 검사하고 없으면 새 문서를 생성한다.
    if 'document_id' in data:
        document_id = data['document_id'] or str(uuid4())
        document = database.execute(text('''
            SELECT company_id,doc_type,deleted_at,publication_status FROM documents
            WHERE id=:id FOR UPDATE
        '''), {'id': document_id}).mappings().one_or_none()
        if not document or document['company_id'] != data['company_id']:
            raise AccessError('ACCESS_DENIED')
        if document['deleted_at'] is not None or document['publication_status'] == 'blocked':
            raise DbError('INVALID_STATE')
        if not data.get('auto_classify') and document['doc_type'] != data['doc_type']:
            raise DbError('INVALID_INPUT')
    else:
        document_id = str(uuid4())
        database.execute(text('''
            INSERT INTO documents (id,company_id,doc_type,uploaded_by)
            VALUES (:id,:company_id,:doc_type,:uploaded_by)
        '''), dict(data, id=document_id))
    return document_id


def _insert_version_and_job(database, document_id, data):
    """문서 ID와 검증된 업로드 dict로 버전·작업을 저장하고 세 가지 ID를 dict로 반환한다.

    앞 단계에서 잠근 같은 DB 연결을 사용한다. 작업 저장이 실패하면 버전과 새 문서도
    호출자의 트랜잭션에서 함께 취소된다. 이 함수는 직접 커밋하지 않는다.
    """
    # 문서별 다음 버전 번호를 정하고 버전과 작업을 같은 연결에 저장한다.
    # 기존 버전이 없을 때 COALESCE가 최대 번호 대신 0을 사용한다.
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
    database.execute(text('''
        INSERT INTO index_jobs (id,version_id,document_id,company_id,requested_by)
        VALUES (:id,:version_id,:document_id,:company_id,:requested_by)
    '''), {'id': job_id, 'version_id': version_id, 'document_id': document_id,
          'company_id': data['company_id'], 'requested_by': data['uploaded_by']})
    return {'document_id': document_id, 'version_id': version_id, 'job_id': job_id}
