"""DB·벡터 적재 완전성, 조건부 게시, 부분 활성화 실패의 복구를 연결한다.

SQL 트랜잭션은 DB 검사·상태 변경에만 사용한다. 외부 벡터 함수는 커밋 후 호출한다.
P0는 공용 앱 서버의 단일 프로세스 실행이며 다중 프로세스 작업 큐를 제공하지 않는다.
"""

import hashlib
import json
from dataclasses import fields, is_dataclass
from threading import RLock

from sqlalchemy import text

from hyundap.auth.errors import AccessError
from .errors import DbError
from .processing import processing_lock
from .runtime import require_id, transaction
from .sessions_repo import current_actor
from . import jobs_repo


# ponytail: P0 단일 프로세스의 게시/복구를 직렬화한다. 다중 작업자는 외부 작업 임대가 필요하다.
PUBLICATION_LOCK = RLock()
PENDING_PHASES = ('pending_activation', 'restore_pending')


def get_embedding_backend(embedding=None):
    """벡터 처리 함수 5개를 이름으로 찾을 수 있는 dict로 반환한다.

    필요한 이름은 embed_and_store, get_version_chunk_ids, get_document_chunks,
    activate_version, delete_vectors다. 모두 호출 가능한 함수여야 한다.
    입력이 없으면 팀 모듈을 연결한다. 테스트용 객체나 함수 dict도 받을 수 있다.
    getattr는 객체에서 이름에 해당하는 함수를 읽으며, 함수가 없으면 None을 준다.
    준비되지 않은 함수는 아래 반복문에서 미준비 오류로 거부한다.
    """
    # 벡터 저장소에서 필요한 함수를 준비하고 모두 호출 가능한지 확인한다.
    required_names = ('embed_and_store', 'get_version_chunk_ids', 'get_document_chunks',
                      'activate_version', 'delete_vectors')
    if embedding is None:
        from hyundap.embedding import embedder, vector_store
        backend = {
            'embed_and_store': getattr(embedder, 'embed_and_store', None),
            'get_version_chunk_ids': getattr(vector_store, 'get_version_chunk_ids', None),
            'get_document_chunks': getattr(vector_store, 'get_document_chunks', None),
            'activate_version': getattr(vector_store, 'activate_version', None),
            'delete_vectors': getattr(vector_store, 'delete_vectors', None),
        }
    elif isinstance(embedding, dict):
        backend = embedding
    else:
        # 기존 테스트 객체도 계속 사용할 수 있도록 메서드를 함수 dict로 옮긴다.
        backend = {}
        # 객체를 받은 경우에만 첫 번째 반복으로 메서드를 사전에 옮긴다.
        for name in required_names:
            backend[name] = getattr(embedding, name, None)
    # 두 번째 반복은 입력 형태와 관계없이 모든 필수 함수가 호출 가능한지 검사한다.
    # 위 반복 안에 중첩된 것이 아니라 그 반복이 끝난 후 실행된다.
    for name in required_names:
        function = backend.get(name)
        if not callable(function):
            raise DbError('INTEGRATION_UNAVAILABLE')
    return backend


def as_mapping(value):
    """외부 결과를 dict로 읽는다. dataclass는 바깥 필드만 옮기고 내부 객체는 보존한다.

    fields는 dataclass의 필드 목록이며 getattr로 각 필드의 현재 값을 읽는다.
    청크 목록과 Chunk 객체는 그대로 전달한다. 알 수 없는 자료형은 거부한다.
    """
    # dataclass의 바깥 필드만 사전으로 옮겨 내부 청크 객체는 그대로 둔다.
    if is_dataclass(value) and not isinstance(value, type):
        result = {}
        # 필드 값을 직접 읽으므로 청크 객체를 재귀적으로 사전 변환하지 않는다.
        # dataclass의 필드 이름을 하나씩 꺼내 현재 객체의 값을 사전에 옮긴다.
        # 예: status 필드는 result['status']에 저장하며 내부 객체는 그대로 둔다.
        for field in fields(value):
            field_name = field.name
            result[field_name] = getattr(value, field_name)
        value = result
    if not isinstance(value, dict):
        raise DbError('INVALID_INPUT')
    return value


def call_external(function, *args, **kwargs):
    """팀 외부 함수의 예외 원문을 안전한 처리 실패로 변환한다. 비밀값은 전달하지 않는다."""
    # 외부 오류는 안전한 DB 오류로 바꾸되 이미 알려진 오류는 그대로 전달한다.
    try:
        # 받은 위치 인자와 이름 인자를 원래 외부 함수에 그대로 전달한다.
        return function(*args, **kwargs)
    except (DbError, AccessError):
        raise
    except Exception:
        raise DbError('PROCESSING_FAILED') from None


def read_candidate(database, document_id, version_id, *, lock=False):
    """문서와 버전을 일치시켜 읽는다. 잠금이 필요하면 문서→버전 순서를 따른다."""
    # 문서와 버전이 존재하며 서로 같은 문서와 회사에 속하는지 확인한다.
    # lock=True일 때만 SELECT에 FOR UPDATE를 붙여 행을 잠근다.
    suffix = ' FOR UPDATE' if lock else ''
    document = database.execute(text('SELECT * FROM documents WHERE id=:id' + suffix),
                                {'id': require_id(document_id)}).mappings().one_or_none()
    version = database.execute(text('SELECT * FROM document_versions WHERE id=:id' + suffix),
                               {'id': require_id(version_id)}).mappings().one_or_none()
    if not document or not version:
        raise DbError('INVALID_STATE')
    if version['document_id'] != document_id:
        raise DbError('INVALID_STATE')
    if version['company_id'] != document['company_id']:
        raise DbError('INVALID_STATE')
    return dict(document), dict(version)


def manifest_snapshot(database, version):
    """원장과 후보 메타데이터의 해시를 만든다. 외부 검사 이후 변경 여부를 다시 비교한다."""
    # 현재 원장과 버전 정보를 묶어 나중에 변경 여부를 비교할 값을 만든다.
    manifest = [dict(row) for row in database.execute(text('''
        SELECT id,chunk_index,content_hash FROM document_chunks WHERE version_id=:id ORDER BY chunk_index
    '''), {'id': version['id']}).mappings()]
    fields = ('id', 'document_id', 'company_id', 'sha256', 'file_size', 'storage_path',
              'data_category', 'expected_chunk_count')
    payload = [{key: version[key] for key in fields}, manifest]
    # 정렬된 JSON의 해시로 외부 검사 중 원장이나 후보 정보의 변경을 확인한다.
    seal = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return manifest, seal


def validate_completeness(document_id, version_id, *, embedding=None):
    """DB 기준 ID·순번·본문 해시와 실제 벡터의 ID·본문·회사/문서/버전을 대조한다.

    성공 여부·개수·누락/초과/잘못된 ID와 재검증용 _seal을 반환한다. 벡터 조회 중
    SQL 잠금을 유지하지 않는다. 개수나 저장 보고서만으로 통과시키지 않는다.
    """
    # DB 원장을 읽은 뒤 외부 저장소의 청크와 비교한다.
    backend = get_embedding_backend(embedding)
    with transaction() as database:
        # 먼저 DB에서 문서·버전과 기준 원장을 읽고 트랜잭션을 종료한다.
        document, version = read_candidate(database, document_id, version_id)
        manifest, seal = manifest_snapshot(database, version)

    # DB 조회가 끝난 다음 외부 저장소를 읽어 SQL 잠금을 오래 잡지 않는다.
    ids = call_external(backend['get_version_chunk_ids'], document_id, version_id)
    # ID 목록과 본문 목록은 각각 조회한다. 서로의 개수와 내용은 아래 비교에서 확인한다.
    hits = call_external(backend['get_document_chunks'], document_id, version_id, active_only=False)
    identity = {'document_id': document_id, 'version_id': version_id,
                'company_id': document['company_id']}
    return compare_stored_chunks(identity, version, manifest, seal, ids, hits)


def chunk_matches_manifest(hit, expected, seen, identity):
    """벡터 조회 결과 하나가 DB 원장의 식별자·본문과 같은지 bool로 반환한다.

    expected는 청크 ID를 키로 가진 원장 dict, seen은 이미 검사한 ID 집합이다.
    identity에는 호출자가 요청한 문서·버전 ID와 DB에서 읽은 회사 ID가 들어 있다.
    중복 청크와 다른 회사·문서·버전의 청크는 정상으로 인정하지 않는다.
    """
    # 청크 ID와 소속을 확인한 뒤 본문의 해시를 DB 원장과 비교한다.
    identifier = hit.get('chunk_id')
    if not isinstance(identifier, str) or identifier not in expected or identifier in seen:
        return False
    # 본문과 메타데이터의 자료형이 예상과 다르면 정상 청크가 아니다.
    metadata = hit.get('metadata')
    content = hit.get('text')
    if not isinstance(metadata, dict) or not isinstance(content, str):
        return False
    if metadata.get('document_id') != identity['document_id']:
        return False
    if metadata.get('version_id') != identity['version_id']:
        return False
    if metadata.get('company_id') != identity['company_id']:
        return False

    row = expected[identifier]
    if metadata.get('chunk_index', row['chunk_index']) != row['chunk_index']:
        return False
    # 읽은 본문의 해시를 계산해 DB에 기록된 해시와 비교한다.
    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
    return content_hash == row['content_hash']


def compare_stored_chunks(identity, version, manifest, seal, ids, hits):
    """DB 원장과 외부 청크 목록을 비교해 완전성 결과 dict를 만든다.

    IDs의 집합뿐 아니라 중복·순번·본문도 검사한다. seal은 게시 직전 원장이
    바뀌지 않았는지 다시 확인할 때 사용하므로 결과에 그대로 포함한다.
    """
    # 개수, 순번, 내용 비교 결과를 모아 최종 완전성을 판단한다.
    comparison = collect_chunk_comparison(identity, manifest, ids, hits)
    expected_count = version['expected_chunk_count']
    # 원장, ID 조회, 본문 조회와 중복 제거 후 개수가 모두 같아야 한다.
    counts_match = (expected_count == len(manifest) == len(ids) == len(comparison['actual_ids'])
                    == len(hits) == len(comparison['seen_ids']))
    # 청크 순번은 0부터 예상 개수 직전까지 빠짐없이 이어져야 한다.
    indexes_match = comparison['indexes'] == list(range(expected_count))
    ids_match = comparison['seen_ids'] == comparison['actual_ids']
    no_missing_or_extra = not comparison['missing_ids'] and not comparison['unexpected_ids']
    contents_match = ids_match and no_missing_or_extra and not comparison['invalid_ids']
    version_is_ready = version['version_status'] in ('processing', 'ready') and expected_count > 0
    ok = version_is_ready and counts_match and indexes_match and contents_match
    return {'ok': bool(ok), 'expected_count': expected_count, 'actual_count': len(ids),
            'missing_ids': comparison['missing_ids'], 'unexpected_ids': comparison['unexpected_ids'],
            'invalid_ids': comparison['invalid_ids'], '_seal': seal}


def collect_chunk_comparison(identity, manifest, ids, hits):
    """원장·실제 청크를 대조해 개수 판정에 필요한 자료를 dict로 반환한다.

    indexes는 DB 순번 목록, actual_ids는 ID 조회 결과, seen_ids는 본문 조회에서 읽은 ID다.
    missing_ids/unexpected_ids/invalid_ids는 누락·초과·잘못된 내용의 ID 목록이다.
    버전 상태나 최종 성공 여부는 여기서 결정하지 않는다.
    """
    # DB 원장과 벡터 저장소의 ID를 집합으로 비교해 누락과 초과를 찾는다.
    if not isinstance(ids, list) or not isinstance(hits, list):
        raise DbError('INVALID_INPUT')
    # 첫 번째 반복은 외부 저장소가 반환한 ID의 자료형을 검증한다.
    for identifier in ids:
        if not isinstance(identifier, str):
            raise DbError('INVALID_INPUT')

    expected_chunks_by_id = {}
    chunk_indexes = []
    # 두 번째 반복은 DB 원장을 ID별 사전과 순번 목록으로 정리한다.
    # 첫 반복의 ids와 두 번째 반복의 manifest는 서로 다른 자료다.
    for row in manifest:
        expected_chunks_by_id[row['id']] = row
        chunk_indexes.append(row['chunk_index'])
    # 집합은 ID 비교에 쓰며 원래 목록의 중복은 개수 비교에서 확인한다.
    actual_chunk_ids = set(ids)
    seen_chunk_ids, invalid_chunk_ids = inspect_chunk_contents(identity, expected_chunks_by_id, hits)
    return {
        'indexes': chunk_indexes, 'actual_ids': actual_chunk_ids, 'seen_ids': seen_chunk_ids,
        'missing_ids': sorted(expected_chunks_by_id.keys() - actual_chunk_ids),
        'unexpected_ids': sorted(actual_chunk_ids - expected_chunks_by_id.keys()),
        'invalid_ids': invalid_chunk_ids,
    }


def inspect_chunk_contents(identity, expected, hits):
    """실제 청크의 내용·소속·중복을 검사하고 (확인한 ID 집합, 잘못된 ID 목록)을 반환한다.

    identity는 문서·버전·회사 ID dict, expected는 청크 ID별 DB 원장이다.
    저장 개수와 순번 판정은 수행하지 않고 각 청크의 내용 검사만 담당한다.
    """
    # 각 결과를 검사하면서 이미 본 ID를 기록해 중복도 찾아낸다.
    invalid_chunk_ids = []
    seen_chunk_ids = set()
    # 벡터 저장소가 돌려준 결과를 하나씩 검사한다.
    # seen_chunk_ids는 앞에서 검사한 ID를 기억하므로 같은 ID가 다시 나오면 중복이다.
    # 잘못된 항목도 검사한 ID로 기록해 이후에 같은 ID가 나왔는지 추적한다.
    for item in hits:
        hit = as_mapping(item)
        chunk_id = hit.get('chunk_id')
        # 이미 본 ID도 실패로 기록하고 다음 중복을 찾도록 seen에 남긴다.
        if not chunk_matches_manifest(hit, expected, seen_chunk_ids, identity):
            if isinstance(chunk_id, str):
                invalid_chunk_ids.append(chunk_id)
            else:
                invalid_chunk_ids.append('<invalid>')
        if isinstance(chunk_id, str):
            seen_chunk_ids.add(chunk_id)
    return seen_chunk_ids, invalid_chunk_ids


def read_job(database, version_id, *, lock=False):
    """버전당 하나인 작업과 안전한 JSON 기록을 읽는다. 중복 작업은 자동 선택하지 않는다."""
    # 버전에 연결된 작업이 정확히 하나인지 확인하고 저장 보고서를 읽는다.
    rows = database.execute(text('SELECT * FROM index_jobs WHERE version_id=:id' +
                                 (' FOR UPDATE' if lock else '')), {'id': version_id}).mappings().all()
    if len(rows) != 1:
        raise DbError('INVALID_STATE')
    job = dict(rows[0])
    reports = json.loads(job['report']) if isinstance(job['report'], str) else (job['report'] or {})
    if not isinstance(reports, dict):
        raise DbError('INVALID_STATE')
    return job, reports


def write_publication(database, job, reports, publication, *, complete=False, error_code=None):
    """복구에 필요한 이전 포인터·공개 상태·단계를 작업 JSON에 원자적으로 저장한다."""
    # 게시 단계에 맞는 작업 상태와 복구 정보를 보고서에 함께 기록한다.
    reports['_publication'] = publication
    phase = publication['phase']
    if complete:
        status = 'SUCCEEDED'
    elif phase in ('restored', 'superseded'):
        status = 'FAILED'
    elif phase in ('pending_activation', 'restore_pending'):
        status = 'QUEUED'
    else:
        status = None
    jobs_repo.store_job_result(database, job['id'], 'complete' if complete else 'publish', reports,
        status=status, error_code=error_code, error_message='게시 상태 복구 필요' if error_code else None)


def ensure_no_pending_publication(database, document_id, *, except_job=None):
    """같은 문서에 미복구 외부 전환이 있으면 새 게시를 막는다. 호출자는 문서 행을 잠근다."""
    # 아직 복구가 끝나지 않은 게시가 있으면 다음 게시와 충돌시킨다.
    pending = database.execute(text('''
        SELECT j.id FROM index_jobs j JOIN document_versions v ON v.id=j.version_id
        WHERE v.document_id=:id AND JSON_UNQUOTE(JSON_EXTRACT(j.report,'$._publication.phase'))
          IN ('pending_activation','restore_pending')
    '''), {'id': document_id}).scalars().all()
    if any(identifier != except_job for identifier in pending):
        raise DbError('CONFLICT')


def require_manager(actor, database, company_id):
    """현재 세션·관리자 역할·회사 권한을 확인한다. 입력 문맥의 역할은 신뢰하지 않는다."""
    # 세션을 다시 읽고 관리자 역할과 대상 회사의 활성 상태를 확인한다.
    current = current_actor(actor, connection=database, lock=True)
    if (current['role'] not in ('platform_admin', 'company_admin')
            or (current['role'] != 'platform_admin' and current['company_id'] != company_id)):
        raise AccessError('ACCESS_DENIED')
    active = database.execute(text('SELECT is_active FROM companies WHERE id=:id FOR UPDATE'),
                              {'id': company_id}).scalar_one_or_none()
    if not active:
        raise AccessError('ACCESS_DENIED')
    return current


def publish_version(actor, document_id, candidate_id, expected_active_id, *, embedding=None):
    """완전한 운영 후보만 조건부 게시하고 커밋 후 벡터를 활성화한다.

    외부 검사 후 잠금 안에서 원장·권한·상태를 재확인한다. 기대 포인터가 다르면
    CONFLICT다. 외부 실패는 조건부 복구 후 ACTIVATION_FAILED, 복구 실패는
    RECOVERY_REQUIRED다. 정상 완료한 동일 후보의 재호출은 멱등 성공이다.
    """
    backend = get_embedding_backend(embedding)
    with processing_lock(), PUBLICATION_LOCK:
        # 1. 권한 확인 → 2. 실제 벡터 검사 → 3. DB 전환 → 4. 벡터 활성화 순서다.
        company_id = check_publication_access(actor, document_id, candidate_id)
        result = validate_completeness(document_id, candidate_id, embedding=backend)
        if not result['ok']:
            raise DbError('INCOMPLETE_INDEX')
        needs_activation = prepare_publication(
            actor=actor,
            document_id=document_id,
            candidate_id=candidate_id,
            company_id=company_id,
            expected_active_id=expected_active_id,
            seal=result['_seal'],
        )
        if not needs_activation:
            return  # 같은 후보가 이미 게시 완료된 경우다.

        # prepare_publication의 DB 커밋이 끝났으므로 외부 호출 동안 SQL 잠금은 없다.
        activate_candidate(backend, document_id, candidate_id, result['expected_count'])
        finish_publication(document_id, candidate_id)


def check_publication_access(actor, document_id, candidate_id):
    """외부 벡터를 읽기 전에 현재 관리자 권한을 확인하고 문서의 회사 ID를 반환한다."""
    # 외부 벡터 조회 전에 현재 사용자와 대상 회사의 접근 권한을 확인한다.
    with transaction() as database:
        current = current_actor(actor, connection=database)
        document, _ = read_candidate(database, document_id, candidate_id)
        if current['role'] not in ('platform_admin', 'company_admin'):
            raise AccessError('ACCESS_DENIED')
        if current['role'] != 'platform_admin' and current['company_id'] != document['company_id']:
            raise AccessError('ACCESS_DENIED')
        active_company = database.execute(text('SELECT is_active FROM companies WHERE id=:id'),
                                         {'id': document['company_id']}).scalar_one_or_none()
        if not active_company:
            raise AccessError('ACCESS_DENIED')
        return document['company_id']


def read_publishable_candidate(database, actor, document_id, candidate_id, company_id, seal):
    """잠금 안에서 권한·상태·원장 변경을 검사하고 (문서 dict, 버전 dict)를 반환한다.

    외부 검사 중 데이터가 바뀔 수 있으므로 최초 확인 결과만 믿지 않는다.
    잠금 순서는 세션·계정 → 회사 → 문서 → 버전으로 유지한다.
    """
    # 외부 검사 이후에도 권한, 문서 상태, 원장이 바뀌지 않았는지 다시 확인한다.
    current = current_actor(actor, connection=database, lock=True)
    active_company = database.execute(text('SELECT is_active FROM companies WHERE id=:id FOR UPDATE'),
                                     {'id': company_id}).scalar_one_or_none()
    if not active_company:
        raise AccessError('ACCESS_DENIED')
    document, version = read_candidate(database, document_id, candidate_id, lock=True)
    if document['company_id'] != company_id:
        raise DbError('CONFLICT')
    if current['role'] not in ('platform_admin', 'company_admin'):
        raise AccessError('ACCESS_DENIED')
    if current['role'] != 'platform_admin' and current['company_id'] != document['company_id']:
        raise AccessError('ACCESS_DENIED')
    if document['deleted_at'] is not None or document['publication_status'] == 'blocked':
        raise DbError('INVALID_STATE')
    if version['data_category'] != 'operational':
        raise DbError('INVALID_STATE')
    if version['version_status'] not in ('processing', 'ready'):
        raise DbError('INVALID_STATE')
    _, current_seal = manifest_snapshot(database, version)
    if current_seal != seal:
        raise DbError('CONFLICT')
    return document, version


def prepare_publication(actor, document_id, candidate_id, company_id, expected_active_id, seal):
    """DB 활성 버전과 복구 기록을 함께 저장한다. 벡터 활성화가 필요하면 True다.

    이미 같은 후보의 게시가 끝났으면 False를 반환한다. expected_active_id는
    호출자가 처음 본 활성 버전이며, 그 사이 다른 버전이 게시됐으면 충돌로 중단한다.
    """
    with transaction() as database:
        document, version = read_publishable_candidate(
            database=database,
            actor=actor,
            document_id=document_id,
            candidate_id=candidate_id,
            company_id=company_id,
            seal=seal,
        )
        job, reports = read_job(database, candidate_id, lock=True)
        previous = reports.get('_publication', {})
        # 같은 후보가 게시 완료되었다면 외부 활성화를 다시 하지 않는다.
        already_active = document['active_version_id'] == candidate_id
        already_finished = previous.get('phase') == 'activated'
        if already_active and already_finished and document['publication_status'] == 'published':
            return False

        check_publication_conflict(database, document, version, expected_active_id)
        store_pending_publication(
            database=database, document=document, candidate_id=candidate_id,
            expected_active_id=expected_active_id, job=job, reports=reports,
        )
    return True


def check_publication_conflict(database, document, version, expected_active_id):
    """잠근 문서·후보 버전이 다른 게시와 경쟁하지 않는지 검사한다. 반환값은 없다.

    미복구 작업, 달라진 활성 ID, 현재 버전보다 오래된 후보는 거부한다.
    호출자의 트랜잭션과 문서 잠금을 사용하며 데이터를 변경하지 않는다.
    """
    # 기대했던 활성 버전과 현재 포인터를 비교해 다른 게시와의 충돌을 막는다.
    ensure_no_pending_publication(database, document['id'])
    # 다른 버전이 게시됐다면 현재 포인터를 덮어쓰지 않는다.
    if document['active_version_id'] != expected_active_id:
        raise DbError('CONFLICT')
    if expected_active_id is not None:
        active_number = database.execute(text('SELECT version_no FROM document_versions WHERE id=:id'),
                                         {'id': expected_active_id}).scalar_one_or_none()
        if active_number is None or version['version_no'] <= active_number:
            raise DbError('CONFLICT')


def store_pending_publication(*, database, document, candidate_id, expected_active_id, job, reports):
    """검사된 후보를 DB 활성 버전으로 바꾸고 벡터 활성화 대기 기록을 함께 저장한다.

    document/job/reports는 같은 트랜잭션에서 잠가 읽은 자료다. 반환값은 없으며
    실패하면 호출자의 트랜잭션이 버전 상태·문서 포인터·작업 기록을 함께 취소한다.
    """
    publication = {'phase': 'pending_activation', 'candidate_id': candidate_id,
                   'previous_active_id': expected_active_id,
                   'previous_publication_status': document['publication_status']}
    database.execute(text("UPDATE document_versions SET version_status='ready' WHERE id=:id"), {'id': candidate_id})
    # <=>는 NULL도 비교한다. 이전 활성 버전이 예상과 같은 문서만 갱신한다.
    changed = database.execute(text('''
        UPDATE documents SET active_version_id=:candidate,publication_status='published'
        WHERE id=:id AND active_version_id <=> :previous AND deleted_at IS NULL
          AND publication_status IN ('draft','published')
    '''), {'candidate': candidate_id, 'id': document['id'], 'previous': expected_active_id})
    if changed.rowcount != 1:
        raise DbError('CONFLICT')
    write_publication(database, job, reports, publication)


def activate_candidate(backend, document_id, candidate_id, expected_count):
    """DB 커밋 후 후보 벡터를 활성화한다. 실패하면 복구를 시도하고 안전한 오류를 낸다."""
    # 벡터 활성화 수를 확인하고 실패하면 DB와 벡터 복구를 시도한다.
    try:
        # 활성화 결과는 정수 개수여야 하며 예상 청크 수와 일치해야 한다.
        activated = backend['activate_version'](document_id, candidate_id)
        if type(activated) is not int or activated != expected_count:
            raise DbError('INCOMPLETE_INDEX')
    except Exception:
        recovered = recover_publication(document_id, candidate_id, embedding=backend)
        if recovered['phase'] == 'restored':
            raise DbError('ACTIVATION_FAILED') from None
        raise DbError('RECOVERY_REQUIRED') from None


def finish_publication(document_id, candidate_id):
    """벡터 활성화 후 DB 상태가 그대로인지 확인하고 게시 완료를 기록한다. 반환값은 없다."""
    # 외부 활성화 이후에도 후보와 문서 상태가 유효한지 확인하고 완료를 기록한다.
    with transaction() as database:
        document, version = read_candidate(database, document_id, candidate_id, lock=True)
        job, reports = read_job(database, candidate_id, lock=True)
        publication = reports.get('_publication', {})
        active_company = database.execute(text('SELECT is_active FROM companies WHERE id=:id'),
                                         {'id': document['company_id']}).scalar_one_or_none()
        if document['active_version_id'] != candidate_id or publication.get('phase') != 'pending_activation':
            raise DbError('RECOVERY_REQUIRED')
        if document['deleted_at'] is not None or document['publication_status'] != 'published':
            raise DbError('RECOVERY_REQUIRED')
        if version['version_status'] != 'ready' or version['data_category'] != 'operational':
            raise DbError('RECOVERY_REQUIRED')
        if not active_company:
            raise DbError('RECOVERY_REQUIRED')
        publication['phase'] = 'activated'
        write_publication(database, job, reports, publication, complete=True)


def recover_publication(document_id, version_id, *, embedding=None):
    """서버 운영자가 미완료 게시를 이전 상태로 복구한다. 사용자 입력으로 직접 노출하지 않는다.

    DB 포인터가 여전히 후보인 경우만 되돌리고, 이전 버전은 현재도 적격해야 한다.
    이전 벡터 재활성화·후보 정리는 SQL TX 밖에서 수행한다. 실패하면 restore_pending을
    유지해 새 게시를 막고 명시적 재시도를 기다린다. 더 최신 포인터는 덮지 않는다.
    """
    backend = get_embedding_backend(embedding)
    with processing_lock(), PUBLICATION_LOCK:
        # DB 되돌리기 → 외부 벡터 복구 → DB 완료 기록으로 나눈다.
        recovery = prepare_recovery(document_id, version_id)
        if recovery['phase'] != 'restore_pending':
            return {'phase': recovery['phase']}

        restored = restore_vectors(backend, document_id, version_id, recovery)
        if not restored:
            return {'phase': 'restore_pending'}
        finish_recovery(document_id, version_id)
        return {'phase': 'restored'}


def check_previous_version(database, document, document_id, previous_id):
    """이전 버전을 다시 공개할 수 있는지와 예상 청크 수를 (bool, 개수)로 반환한다.

    문서·회사가 차단되었거나 이전 버전이 운영용 ready 상태가 아니면 복원하지 않는다.
    이전 버전이 없는 첫 게시의 실패도 처리하므로 개수는 None일 수 있다.
    """
    # 이전 버전과 회사가 현재도 공개 가능한지 확인해 복구 대상을 결정한다.
    eligible = document['deleted_at'] is None and document['publication_status'] != 'blocked'
    active_company = database.execute(text('SELECT is_active FROM companies WHERE id=:id'),
                                     {'id': document['company_id']}).scalar_one_or_none()
    eligible = eligible and bool(active_company)
    previous_count = None
    if previous_id is not None:
        previous_count = database.execute(text('''
            SELECT expected_chunk_count FROM document_versions WHERE id=:id
              AND document_id=:document AND company_id=:company
              AND version_status='ready' AND data_category='operational' FOR UPDATE
        '''), {'id': previous_id, 'document': document_id, 'company': document['company_id']}).scalar_one_or_none()
        eligible = eligible and previous_count is not None
    return eligible, previous_count


def prepare_recovery(document_id, version_id):
    """DB 포인터를 조건부로 되돌리고 외부 복구에 필요한 값들을 dict로 반환한다.

    새 버전이 이미 게시됐다면 superseded를 반환해 덮어쓰지 않는다. 복구 대상이
    None인 경우도 기록해 두어, 벡터 삭제에 실패한 뒤 같은 복구를 다시 실행할 수 있다.
    """
    with transaction() as database:
        document, _ = read_candidate(database, document_id, version_id, lock=True)
        job, reports = read_job(database, version_id, lock=True)
        publication = reports.get('_publication', {})
        phase = publication.get('phase')
        if phase not in PENDING_PHASES:
            return {'phase': phase or 'not_pending'}
        previous_id = publication.get('previous_active_id')
        previous_is_eligible, previous_count = check_previous_version(
            database, document, document_id, previous_id)
        # 다른 게시가 포인터를 변경했다면 그 값을 덮어쓰지 않는다.
        allowed_pointers = (version_id, previous_id, publication.get('restore_target_id'))
        if document['active_version_id'] not in allowed_pointers:
            publication['phase'] = 'superseded'
            write_publication(database, job, reports, publication, error_code='CONFLICT')
            return {'phase': 'superseded'}

        restore_target_id = restore_database_pointer(
            database=database, document=document, version_id=version_id,
            publication=publication, eligible=previous_is_eligible,
        )

        restore_previous = (previous_is_eligible and previous_id is not None
                            and restore_target_id == previous_id)
        # DB 복구 대상과 외부 복구 대기 상태를 같은 트랜잭션에서 저장한다.
        publication['phase'] = 'restore_pending'
        write_publication(database, job, reports, publication, error_code='RECOVERY_REQUIRED')
    return {'phase': 'restore_pending', 'previous_id': previous_id,
            'previous_count': previous_count, 'restore_previous': restore_previous}


def restore_database_pointer(*, database, document, version_id, publication, eligible):
    """현재 포인터가 실패 후보일 때만 되돌리고 복구 후 활성 버전 ID를 반환한다.

    eligible은 이전 버전 공개 가능 여부다. 공개할 수 없으면 None으로 비우고
    문서를 blocked로 둔다. publication dict에는 None도 복구 대상으로 기록한다.
    작업 기록 저장은 호출자가 맡으며, 같은 트랜잭션에서 함께 확정된다.
    """
    current_active_id = document['active_version_id']
    if current_active_id != version_id:
        return current_active_id
    if eligible:
        restore_target_id = publication.get('previous_active_id')
        restore_status = publication['previous_publication_status']
    else:
        restore_target_id = None
        restore_status = 'blocked'
    database.execute(text('''
        UPDATE documents SET active_version_id=:previous,publication_status=:status WHERE id=:id
    '''), {'previous': restore_target_id, 'status': restore_status, 'id': document['id']})
    # 호출자가 이 사전을 같은 트랜잭션에서 작업 기록에 저장한다.
    publication['restore_target_id'] = restore_target_id
    return restore_target_id


def restore_vectors(backend, document_id, version_id, recovery):
    """SQL 잠금 없이 이전 벡터 복원과 후보 삭제를 수행한다. 모두 끝나면 True다.

    실패를 False로 돌려주면 호출자는 DB의 restore_pending 기록을 그대로 유지한다.
    다음 명시적 복구 요청에서 이 단계를 다시 실행할 수 있다.
    """
    # DB 잠금 없이 이전 벡터를 복원한 다음 실패 후보 벡터를 삭제한다.
    try:
        if recovery['restore_previous']:
            previous_id = recovery['previous_id']
            result = validate_completeness(document_id, previous_id, embedding=backend)
            if not result['ok']:
                raise DbError('INCOMPLETE_INDEX')
            activated = backend['activate_version'](document_id, previous_id)
            if type(activated) is not int or activated != recovery['previous_count']:
                raise DbError('INCOMPLETE_INDEX')
        # 이전 벡터 복원이 필요하고 성공한 뒤 실패 후보 벡터를 삭제한다.
        backend['delete_vectors'](document_id, version_id)
    except Exception:
        return False
    return True


def finish_recovery(document_id, version_id):
    """후보가 비활성인지 마지막으로 확인하고 실패 상태·복구 완료를 저장한다. 반환값은 없다."""
    # 후보가 더 이상 활성 버전이 아닌지 확인한 후 복구 완료를 기록한다.
    with transaction() as database:
        document, _ = read_candidate(database, document_id, version_id, lock=True)
        job, reports = read_job(database, version_id, lock=True)
        publication = reports['_publication']
        # 후보가 아직 활성 포인터라면 실패 처리하지 않고 재복구를 요구한다.
        if document['active_version_id'] == version_id:
            raise DbError('RECOVERY_REQUIRED')
        database.execute(text("UPDATE document_versions SET version_status='failed' WHERE id=:id"), {'id': version_id})
        publication['phase'] = 'restored'
        write_publication(database, job, reports, publication, error_code='ACTIVATION_FAILED')
