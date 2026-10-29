"""권한 있는 관리자의 버전 이력 조회와 실패 후보 상태 기록."""

from sqlalchemy import text

from hyundap.auth.errors import AccessError
from hyundap.auth.permissions import allowed_companies
from .errors import DbError
from .runtime import require_id, transaction
from .sessions_repo import current_actor


def list_document_versions(actor, document_id, *, connection=None):
    """회사 권한이 있는 관리자의 버전 이력을 순번대로 반환한다. 내부 경로는 제외한다.

    기존 9필드에 is_active(bool)를 추가한다. 이는 활성 포인터 일치 여부이며 공개 권한은 아니다.
    삭제·차단 문서도 권한 있는 관리자는 이력을 조회한다. 버전이 없으면 []다.
    없는 문서·직원·회사 범위 위반은 ACCESS_DENIED, 세션 무효는 SESSION_EXPIRED다.
    잘못된 ID는 INVALID_INPUT, DB 장애는 DB_UNAVAILABLE로 전달한다.
    일반 직원에게 비공개·과거 버전 관리 이력을 공개하지 않는다. 검색 정보 조회는 별도다.
    외부 연결은 호출자의 TX에 참여하며 커밋·종료하지 않는다. 결과는 조회 시점 기준이다.
    """
    # 1단계: 입력 ID를 먼저 검사하여 잘못된 요청으로 DB에 연결하지 않는다.
    document_id = require_id(document_id)
    from .runtime import split_layout
    company_id = None
    if split_layout():
        from .tenancy import company_for_reference, authorize_company
        authorize_company(actor, manager=True)
        try:
            company_id = company_for_reference(document_id=document_id, connection=connection)
        except DbError as error:
            if error.code == 'INVALID_STATE':
                raise AccessError('ACCESS_DENIED') from None
            raise
        authorize_company(actor, company_id, manager=True)
    with transaction(connection, company_id=company_id) as database:
        # 2단계: 전달된 역할을 믿지 않고 현재 세션·사용자·회사 정보를 다시 확인한다.
        current = current_actor(actor, connection=database)
        if current['role'] not in ('platform_admin', 'company_admin'):
            raise AccessError('ACCESS_DENIED')
        # 조회 가능한 회사 범위를 가져와 대상 문서 회사와 비교한다.
        allowed_company_ids = allowed_companies(current, connection=database)
        document_company_id = database.execute(text('SELECT company_id FROM documents WHERE id=:id'),
                                               {'id': document_id}).scalar_one_or_none()
        if document_company_id not in allowed_company_ids:
            raise AccessError('ACCESS_DENIED')
        # 3단계: 이력과 활성 포인터를 같은 SELECT에서 읽어 서로 다른 시점의 값을 섞지 않는다.
        # 문서·회사 양쪽을 연결하고 앞서 확인한 회사로 제한한다. 상태 필터는 의도적으로 없다.
        rows = database.execute(text('''
            SELECT v.id AS version_id,v.document_id,v.version_no,v.file_name,v.file_size,
                   v.version_status,v.data_category,v.expected_chunk_count,v.created_at,
                   CASE WHEN v.id=d.active_version_id THEN 1 ELSE 0 END AS is_active
            FROM document_versions v JOIN documents d
              ON d.id=v.document_id AND d.company_id=v.company_id
            WHERE d.id=:id AND d.company_id=:company ORDER BY v.version_no
        '''), {'id': document_id, 'company': document_company_id}).mappings()
        # 한 번의 반복에서 행 하나를 dict 하나로 바꾼다. 기존 필드는 그대로 보존한다.
        # MySQL/SQLite가 돌려주는 0·1을 Python bool로 통일하여 호출자가 DB 종류를 몰라도 된다.
        versions = []
        for row in rows:
            version = dict(row)
            version['is_active'] = bool(version['is_active'])
            versions.append(version)
        return versions


def mark_failed(version_id, *, connection=None):
    """서버 내부에서 비활성 처리 후보만 failed로 바꾼다. 정상 활성 버전은 보존한다.

    게시 흐름과 동일하게 문서→버전 순서로 잠근다. 상세 실패 사유는 jobs_repo가 기록한다.
    이미 실패한 후보의 재호출은 성공으로 처리한다.
    """
    # 문서와 버전을 순서대로 잠가 현재 활성 버전이 실패 처리되지 않게 한다.
    with transaction(connection, version_id=version_id) as database:
        document_id = database.execute(text('SELECT document_id FROM document_versions WHERE id=:id'),
                                       {'id': require_id(version_id)}).scalar_one_or_none()
        if document_id is None:
            raise DbError('INVALID_STATE')
        active_version_id = database.execute(text('SELECT active_version_id FROM documents WHERE id=:id FOR UPDATE'),
                                             {'id': document_id}).scalar_one_or_none()
        version_status = database.execute(text('SELECT version_status FROM document_versions WHERE id=:id FOR UPDATE'),
                                          {'id': version_id}).scalar_one_or_none()
        # 활성 버전이나 허용된 상태가 아닌 버전은 변경하지 않는다.
        if active_version_id == version_id or version_status not in ('processing', 'failed'):
            raise DbError('INVALID_STATE')
        database.execute(text("UPDATE document_versions SET version_status='failed' WHERE id=:id"),
                         {'id': version_id})
