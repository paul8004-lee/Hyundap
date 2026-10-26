"""DB·벡터 적재 완전성, 조건부 게시, 부분 활성화 실패의 복구를 연결한다.

SQL 트랜잭션은 DB 검사·상태 변경에만 사용한다. 외부 벡터 함수는 커밋 후 호출한다.
P0는 공용 앱 서버의 단일 프로세스 실행이며 다중 프로세스 작업 큐를 제공하지 않는다.
"""

import hashlib
import json
from dataclasses import fields, is_dataclass
from threading import RLock
from types import SimpleNamespace

from sqlalchemy import text

from hyundap.auth.errors import AccessError
from .errors import DbError
from .runtime import require_id, transaction
from .sessions_repo import current_actor


# ponytail: P0 단일 프로세스의 게시/복구를 직렬화한다. 다중 작업자는 외부 작업 임대가 필요하다.
PUBLICATION_LOCK = RLock()
PENDING_PHASES = ('pending_activation', 'restore_pending')


def get_embedding_backend(embedding=None):
    """주입한 벡터 기능 또는 팀 embedding 모듈을 사용한다. 뼈대 모듈은 안전하게 거부한다."""
    names = ('embed_and_store', 'get_version_chunk_ids', 'get_document_chunks',
             'activate_version', 'delete_vectors')
    if embedding is None:
        from hyundap.embedding import embedder, vector_store
        functions = {name: getattr(embedder if name == 'embed_and_store' else vector_store, name, None)
                     for name in names}
        embedding = SimpleNamespace(**functions)
    if not all(callable(getattr(embedding, name, None)) for name in names):
        raise DbError('INTEGRATION_UNAVAILABLE')
    return embedding


def as_mapping(value):
    """외부 모듈의 dict/dataclass 결과를 읽는다. 알 수 없는 자료형은 정상 결과로 취급하지 않는다."""
    if is_dataclass(value) and not isinstance(value, type):
        value = {field.name: getattr(value, field.name) for field in fields(value)}
    if not isinstance(value, dict):
        raise DbError('INVALID_INPUT')
    return value


def call_external(function, *args, **kwargs):
    """팀 외부 함수의 예외 원문을 안전한 처리 실패로 변환한다. 비밀값은 전달하지 않는다."""
    try:
        return function(*args, **kwargs)
    except (DbError, AccessError):
        raise
    except Exception:
        raise DbError('PROCESSING_FAILED') from None


def read_candidate(database, document_id, version_id, *, lock=False):
    """문서와 버전을 일치시켜 읽는다. 잠금이 필요하면 문서→버전 순서를 따른다."""
    suffix = ' FOR UPDATE' if lock else ''
    document = database.execute(text('SELECT * FROM documents WHERE id=:id' + suffix),
                                {'id': require_id(document_id)}).mappings().one_or_none()
    version = database.execute(text('SELECT * FROM document_versions WHERE id=:id' + suffix),
                               {'id': require_id(version_id)}).mappings().one_or_none()
    if (not document or not version or version['document_id'] != document_id
            or version['company_id'] != document['company_id']):
        raise DbError('INVALID_STATE')
    return dict(document), dict(version)


def manifest_snapshot(database, version):
    """원장과 후보 메타데이터의 해시를 만든다. 외부 검사 이후 변경 여부를 다시 비교한다."""
    manifest = [dict(row) for row in database.execute(text('''
        SELECT id,chunk_index,content_hash FROM document_chunks WHERE version_id=:id ORDER BY chunk_index
    '''), {'id': version['id']}).mappings()]
    fields = ('id', 'document_id', 'company_id', 'sha256', 'file_size', 'storage_path',
              'data_category', 'expected_chunk_count')
    payload = [{key: version[key] for key in fields}, manifest]
    seal = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return manifest, seal


def validate_completeness(document_id, version_id, *, embedding=None):
    """DB 기준 ID·순번·본문 해시와 실제 벡터의 ID·본문·회사/문서/버전을 대조한다.

    성공 여부·개수·누락/초과/잘못된 ID와 재검증용 _seal을 반환한다. 벡터 조회 중
    SQL 잠금을 유지하지 않는다. 개수나 저장 보고서만으로 통과시키지 않는다.
    """
    backend = get_embedding_backend(embedding)
    with transaction() as database:
        document, version = read_candidate(database, document_id, version_id)
        manifest, seal = manifest_snapshot(database, version)
    ids = call_external(backend.get_version_chunk_ids, document_id, version_id)
    hits = call_external(backend.get_document_chunks, document_id, version_id, active_only=False)
    if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids) or not isinstance(hits, list):
        raise DbError('INVALID_INPUT')
    expected = {row['id']: row for row in manifest}
    actual = set(ids)
    invalid = []
    seen = set()
    for item in hits:
        hit = as_mapping(item)
        identifier = hit.get('chunk_id')
        metadata = hit.get('metadata')
        content = hit.get('text')
        valid = isinstance(identifier, str) and identifier in expected and identifier not in seen
        if valid:
            row = expected[identifier]
            valid = (isinstance(metadata, dict) and isinstance(content, str)
                     and metadata.get('document_id') == document_id
                     and metadata.get('version_id') == version_id
                     and metadata.get('company_id') == document['company_id']
                     and metadata.get('chunk_index', row['chunk_index']) == row['chunk_index']
                     and hashlib.sha256(content.encode('utf-8')).hexdigest() == row['content_hash'])
        if not valid:
            invalid.append(identifier if isinstance(identifier, str) else '<invalid>')
        if isinstance(identifier, str):
            seen.add(identifier)
    missing, unexpected = sorted(expected.keys() - actual), sorted(actual - expected.keys())
    count = version['expected_chunk_count']
    ok = (version['version_status'] in ('processing', 'ready') and count > 0
          and count == len(manifest) == len(ids) == len(actual) == len(hits) == len(seen)
          and [row['chunk_index'] for row in manifest] == list(range(count))
          and seen == actual and not missing and not unexpected and not invalid)
    return {'ok': bool(ok), 'expected_count': count, 'actual_count': len(ids),
            'missing_ids': missing, 'unexpected_ids': unexpected, 'invalid_ids': invalid, '_seal': seal}


def read_job(database, version_id, *, lock=False):
    """버전당 하나인 작업과 안전한 JSON 기록을 읽는다. 중복 작업은 자동 선택하지 않는다."""
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
    reports['_publication'] = publication
    database.execute(text('''
        UPDATE index_jobs SET stage=:stage,report=:report,error_code=:code,error_message=:message WHERE id=:id
    '''), {'stage': 'complete' if complete else 'publish', 'report': json.dumps(reports, ensure_ascii=False),
          'code': error_code, 'message': '게시 상태 복구 필요' if error_code else None, 'id': job['id']})


def ensure_no_pending_publication(database, document_id, *, except_job=None):
    """같은 문서에 미복구 외부 전환이 있으면 새 게시를 막는다. 호출자는 문서 행을 잠근다."""
    pending = database.execute(text('''
        SELECT j.id FROM index_jobs j JOIN document_versions v ON v.id=j.version_id
        WHERE v.document_id=:id AND JSON_UNQUOTE(JSON_EXTRACT(j.report,'$._publication.phase'))
          IN ('pending_activation','restore_pending')
    '''), {'id': document_id}).scalars().all()
    if any(identifier != except_job for identifier in pending):
        raise DbError('CONFLICT')


def require_manager(actor, database, company_id):
    """현재 세션·관리자 역할·회사 권한을 확인한다. 입력 문맥의 역할은 신뢰하지 않는다."""
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
    with PUBLICATION_LOCK:
        with transaction() as database:
            current = current_actor(actor, connection=database)
            document, _ = read_candidate(database, document_id, candidate_id)
            if (current['role'] not in ('platform_admin', 'company_admin')
                    or (current['role'] != 'platform_admin' and current['company_id'] != document['company_id'])):
                raise AccessError('ACCESS_DENIED')
            active_company = database.execute(text('SELECT is_active FROM companies WHERE id=:id'),
                                             {'id': document['company_id']}).scalar_one_or_none()
            if not active_company:
                raise AccessError('ACCESS_DENIED')
            company_id = document['company_id']
        result = validate_completeness(document_id, candidate_id, embedding=backend)
        if not result['ok']:
            raise DbError('INCOMPLETE_INDEX')
        with transaction() as database:
            # 계정→문서→버전→작업의 순서를 일관되게 유지한다.
            current = current_actor(actor, connection=database, lock=True)
            active_company = database.execute(text('SELECT is_active FROM companies WHERE id=:id FOR UPDATE'),
                                             {'id': company_id}).scalar_one_or_none()
            if not active_company:
                raise AccessError('ACCESS_DENIED')
            document, version = read_candidate(database, document_id, candidate_id, lock=True)
            if document['company_id'] != company_id:
                raise DbError('CONFLICT')
            if (current['role'] not in ('platform_admin', 'company_admin')
                    or (current['role'] != 'platform_admin' and current['company_id'] != document['company_id'])):
                raise AccessError('ACCESS_DENIED')
            if (document['deleted_at'] is not None or document['publication_status'] == 'blocked'
                    or version['data_category'] != 'operational'
                    or version['version_status'] not in ('processing', 'ready')):
                raise DbError('INVALID_STATE')
            _, current_seal = manifest_snapshot(database, version)
            if current_seal != result['_seal']:
                raise DbError('CONFLICT')
            job, reports = read_job(database, candidate_id, lock=True)
            previous = reports.get('_publication', {})
            if (document['active_version_id'] == candidate_id and previous.get('phase') == 'activated'
                    and document['publication_status'] == 'published'):
                return
            ensure_no_pending_publication(database, document_id)
            if document['active_version_id'] != expected_active_id:
                raise DbError('CONFLICT')
            if expected_active_id is not None:
                active_number = database.execute(text('SELECT version_no FROM document_versions WHERE id=:id'),
                                                 {'id': expected_active_id}).scalar_one_or_none()
                if active_number is None or version['version_no'] <= active_number:
                    raise DbError('CONFLICT')
            publication = {'phase': 'pending_activation', 'candidate_id': candidate_id,
                           'previous_active_id': expected_active_id,
                           'previous_publication_status': document['publication_status']}
            database.execute(text("UPDATE document_versions SET version_status='ready' WHERE id=:id"), {'id': candidate_id})
            changed = database.execute(text('''
                UPDATE documents SET active_version_id=:candidate,publication_status='published'
                WHERE id=:id AND active_version_id <=> :previous AND deleted_at IS NULL
                  AND publication_status IN ('draft','published')
            '''), {'candidate': candidate_id, 'id': document_id, 'previous': expected_active_id})
            if changed.rowcount != 1:
                raise DbError('CONFLICT')
            write_publication(database, job, reports, publication)
        try:
            activated = backend.activate_version(document_id, candidate_id)
            if type(activated) is not int or activated != result['expected_count']:
                raise DbError('INCOMPLETE_INDEX')
        except Exception:
            recovered = recover_publication(document_id, candidate_id, embedding=backend)
            raise DbError('ACTIVATION_FAILED' if recovered['phase'] == 'restored' else 'RECOVERY_REQUIRED') from None
        with transaction() as database:
            document, version = read_candidate(database, document_id, candidate_id, lock=True)
            job, reports = read_job(database, candidate_id, lock=True)
            publication = reports.get('_publication', {})
            active_company = database.execute(text('SELECT is_active FROM companies WHERE id=:id'),
                                             {'id': document['company_id']}).scalar_one_or_none()
            if (document['active_version_id'] != candidate_id or publication.get('phase') != 'pending_activation'
                    or document['deleted_at'] is not None or document['publication_status'] != 'published'
                    or version['version_status'] != 'ready' or version['data_category'] != 'operational'
                    or not active_company):
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
    with PUBLICATION_LOCK:
        with transaction() as database:
            document, _ = read_candidate(database, document_id, version_id, lock=True)
            job, reports = read_job(database, version_id, lock=True)
            publication = reports.get('_publication', {})
            phase = publication.get('phase')
            if phase not in PENDING_PHASES:
                return {'phase': phase or 'not_pending'}
            previous_id = publication.get('previous_active_id')
            eligible = document['deleted_at'] is None and document['publication_status'] != 'blocked'
            active_company = database.execute(text('SELECT is_active FROM companies WHERE id=:id'),
                                             {'id': document['company_id']}).scalar_one_or_none()
            eligible = eligible and bool(active_company)
            previous_count = None
            if previous_id is not None:
                previous = database.execute(text('''
                    SELECT expected_chunk_count FROM document_versions WHERE id=:id
                      AND document_id=:document AND company_id=:company
                      AND version_status='ready' AND data_category='operational' FOR UPDATE
                '''), {'id': previous_id, 'document': document_id, 'company': document['company_id']}).scalar_one_or_none()
                eligible = eligible and previous is not None
                previous_count = previous
            allowed_pointers = (version_id, previous_id, publication.get('restore_target_id'))
            if document['active_version_id'] not in allowed_pointers:
                publication['phase'] = 'superseded'
                write_publication(database, job, reports, publication, error_code='CONFLICT')
                return {'phase': 'superseded'}
            if document['active_version_id'] == version_id:
                target = previous_id if eligible else None
                database.execute(text('''
                    UPDATE documents SET active_version_id=:previous,publication_status=:status WHERE id=:id
                '''), {'previous': target,
                       'status': publication['previous_publication_status'] if eligible else 'blocked', 'id': document_id})
                publication['restore_target_id'] = target
            else:
                target = document['active_version_id']
            restore_previous = eligible and previous_id is not None and target == previous_id
            publication['phase'] = 'restore_pending'
            write_publication(database, job, reports, publication, error_code='RECOVERY_REQUIRED')
        try:
            if restore_previous:
                if not validate_completeness(document_id, previous_id, embedding=backend)['ok']:
                    raise DbError('INCOMPLETE_INDEX')
                activated = backend.activate_version(document_id, previous_id)
                if type(activated) is not int or activated != previous_count:
                    raise DbError('INCOMPLETE_INDEX')
            backend.delete_vectors(document_id, version_id)
        except Exception:
            return {'phase': 'restore_pending'}
        with transaction() as database:
            document, _ = read_candidate(database, document_id, version_id, lock=True)
            job, reports = read_job(database, version_id, lock=True)
            publication = reports['_publication']
            if document['active_version_id'] == version_id:
                raise DbError('RECOVERY_REQUIRED')
            database.execute(text("UPDATE document_versions SET version_status='failed' WHERE id=:id"), {'id': version_id})
            publication['phase'] = 'restored'
            write_publication(database, job, reports, publication, error_code='ACTIVATION_FAILED')
        return {'phase': 'restored'}
