"""원본 저장→수집→청크 원장→임베딩→검증→게시를 연결하는 DB 담당 흐름.

다른 담당 모듈은 실제 함수를 준비한 뒤 기본 경로로 연결된다. 테스트는 함수를 주입한다.
서버가 파일 해시·크기·저장 경로를 정하며 외부 작업 동안 SQL 잠금을 유지하지 않는다.
"""

import hashlib
import json
import os
import re
from uuid import uuid4
from pathlib import Path
from threading import RLock

from sqlalchemy import text

from hyundap.common.config import get_settings
from . import chunks_repo, documents_repo, jobs_repo, legacy, versions_repo, version_service
from .errors import DbError
from .runtime import get_table_columns, require_id, transaction


# ponytail: P0의 단일 앱 프로세스에서 업로드·같은 후보 재시도를 순차 실행한다.
WORKFLOW_LOCK = RLock()


def get_ingest_function(function=None):
    """주입한 함수 또는 팀 수집 진입점을 선택한다. 미구현 뼈대는 안전하게 거부한다."""
    if function is None:
        from hyundap.ingest import pipeline
        function = getattr(pipeline, 'ingest_document', None)
    if not callable(function):
        raise DbError('INTEGRATION_UNAVAILABLE')
    return function


def file_digest(path):
    """실제 파일을 1MiB씩 읽어 SHA-256과 크기를 구한다. 비어 있거나 상한을 넘으면 거부한다."""
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break  # 더 읽을 내용이 없으면 반복을 끝낸다.
            size += len(block)
            if size > 104857600:
                raise DbError('INVALID_INPUT')
            digest.update(block)
    if size == 0:
        raise DbError('INVALID_INPUT')
    return digest.hexdigest(), size


def source_destination(root, company_id, document_id, version_id, suffix):
    """안전한 식별자로 순수 경로를 계산한다. 실제 링크·루트 검사는 TX 밖에서 수행한다."""
    for identifier in (company_id, document_id, version_id):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,36}', require_id(identifier)):
            raise DbError('INVALID_INPUT')
    suffix = suffix if re.fullmatch(r'\.[a-zA-Z0-9]{1,12}', suffix) else '.bin'
    destination = root / company_id / document_id / version_id / ('source' + suffix)
    if not destination.is_relative_to(root):
        raise DbError('INVALID_INPUT')
    return destination


def copy_original(source, destination, expected_hash, expected_size):
    """기존 파일을 덮지 않고 복사하면서 다시 해시를 검사한다. 복사 중 원본 변경도 검출한다."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.source-' + uuid4().hex + '.part')
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open('rb') as incoming, temporary.open('xb') as outgoing:
            while True:
                block = incoming.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > 104857600:
                    raise DbError('INVALID_INPUT')
                digest.update(block)
                outgoing.write(block)
            # Python의 쓰기 버퍼를 비우고 운영체제에도 파일 내용 저장을 요청한다.
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if digest.hexdigest() != expected_hash or size != expected_size:
            raise DbError('CONFLICT')
        # 같은 파일시스템의 hard link는 기존 목적지를 덮지 않고 완전한 파일만 공개한다.
        os.link(temporary, destination)
    finally:
        # 성공하면 목적지 이름이 같은 파일을 가리키므로 임시 이름만 지운다.
        temporary.unlink(missing_ok=True)


def failure_code(error):
    """예외에서 안전한 코드만 가져온다. 원시 메시지·URL·접속 비밀값은 저장하지 않는다."""
    code = error if isinstance(error, str) else getattr(error, 'code', None)
    return code if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code) else 'PROCESSING_FAILED'


def record_upload_failure(ids, stage, error, backend):
    """게시 미완료는 복구에 맡기고, 비활성 처리 후보만 실패 기록·벡터 정리한다."""
    code = failure_code(error)
    with version_service.PUBLICATION_LOCK:
        with transaction() as database:
            document, _ = version_service.read_candidate(database, ids['document_id'], ids['version_id'], lock=True)
            job, reports = version_service.read_job(database, ids['version_id'], lock=True)
            phase = reports.get('_publication', {}).get('phase')
            if phase in version_service.PENDING_PHASES or document['active_version_id'] == ids['version_id']:
                return dict(ids, status='failed', error_code='RECOVERY_REQUIRED', recovery_required=True)
            if phase == 'restored':
                return dict(ids, status='failed', error_code=code, recovery_required=False)
        if reports.get(stage, {}).get('status') not in ('partial', 'failed'):
            jobs_repo.record_stage_result(ids['job_id'], {'stage': stage, 'status': 'failed', 'error_code': code})
        versions_repo.mark_failed(ids['version_id'])
        cleanup_pending = False
        try:
            backend['delete_vectors'](ids['document_id'], ids['version_id'])
        except Exception:
            cleanup_pending = True
        with transaction() as database:
            job, reports = version_service.read_job(database, ids['version_id'], lock=True)
            reports['_cleanup_pending'] = cleanup_pending
            database.execute(text('UPDATE index_jobs SET report=:report,error_code=:code WHERE id=:id'),
                             {'report': json.dumps(reports, ensure_ascii=False), 'code': code, 'id': job['id']})
        return dict(ids, status='failed', error_code=code, recovery_required=cleanup_pending)


def read_attempt_source(ids):
    """문서·버전 ID로 저장 원본을 확인하고 (파일 경로, 수집용 정보 dict)를 반환한다.

    DB 조회를 끝낸 뒤 파일을 읽는다. 파일의 해시·크기가 저장 당시와 다르면 중단한다.
    """
    with transaction() as database:
        document, version = version_service.read_candidate(
            database, ids['document_id'], ids['version_id'])
        company_name = database.execute(
            text('SELECT name FROM companies WHERE id=:id'),
            {'id': document['company_id']},
        ).scalar_one()

    source = Path(version['storage_path'])
    if file_digest(source) != (version['sha256'], version['file_size']):
        raise DbError('CONFLICT')
    info = {
        'document_id': ids['document_id'],
        'version_id': ids['version_id'],
        'company_id': document['company_id'],
        'company_name': company_name,
        'doc_type': document['doc_type'],
        'file_name': version['file_name'],
    }
    return source, info


def save_ingest_chunks(ids, company_id, result):
    """성공한 수집 결과의 청크를 검사·기록하고 원래 청크 목록을 반환한다.

    DB에는 식별자·순번·본문 해시만 저장한다. 임베딩에 넘길 원래 청크 객체는 유지한다.
    """
    chunks = result.get('chunks')
    if not isinstance(chunks, list) or not chunks:
        raise DbError('INVALID_INPUT')
    manifest = []
    for chunk in chunks:
        row = version_service.as_mapping(chunk)
        if row.get('document_id') != ids['document_id']:
            raise DbError('INVALID_INPUT')
        if row.get('version_id') != ids['version_id']:
            raise DbError('INVALID_INPUT')
        if row.get('company_id') != company_id:
            raise DbError('INVALID_INPUT')
        if not isinstance(row.get('text'), str):
            raise DbError('INVALID_INPUT')
        content_hash = hashlib.sha256(row['text'].encode('utf-8')).hexdigest()
        manifest.append({
            'id': row.get('chunk_id'),
            'chunk_index': row.get('chunk_index'),
            'content_hash': content_hash,
        })
    chunks_repo.save_manifest(ids['version_id'], manifest)
    report = version_service.as_mapping(result.get('report') or {})
    report = dict(report)
    if 'warnings' in report:
        warnings = report['warnings']
        if not isinstance(warnings, list) or any(not isinstance(value, str) for value in warnings):
            raise DbError('INVALID_INPUT')
        report['warning_count'] = len(warnings)
        report.pop('warnings')
    jobs_repo.record_stage_result(
        ids['job_id'], dict(report, stage='ingest', status='success'))
    return chunks


def verify_uploaded_chunks(ids, backend):
    """DB 청크 목록과 벡터 저장 결과를 대조하고 성공 보고서를 남긴다. 반환값은 없다."""
    complete = version_service.validate_completeness(
        ids['document_id'], ids['version_id'], embedding=backend)
    if not complete['ok']:
        raise DbError('INCOMPLETE_INDEX')
    jobs_repo.record_stage_result(ids['job_id'], {
        'stage': 'verify', 'status': 'success', 'total': complete['expected_count'],
    })


def ingest_and_save_chunks(source, info, ids, ingest_function):
    """원본 Path와 수집 정보로 수집 단계를 실행하고 (청크 목록, 오류 코드)를 반환한다.

    성공하면 청크·보고서를 저장하고 (chunks, None)을 반환한다. 수집기가 실패 결과를
    반환하면 (None, 안전한 오류 코드)다. 실패 후보 정리는 run_attempt가 담당한다.
    """
    def find_by_hash(company_id, sha256):
        """서버의 현재 후보 문맥에만 묶인 조회다. 임의 회사·해시 제외를 허용하지 않는다."""
        if company_id != info['company_id']:
            raise DbError('INVALID_INPUT')
        return documents_repo.find_duplicate_document(
            company_id, sha256, exclude_version_id=ids['version_id'])

    raw_result = version_service.call_external(
        ingest_function, str(source), info, find_by_hash=find_by_hash)
    result = version_service.as_mapping(raw_result)
    if result.get('status') != 'success':
        return None, failure_code(result.get('error_code'))
    chunks = save_ingest_chunks(ids, info['company_id'], result)
    return chunks, None


def embed_and_record_result(chunks, job_id, backend):
    """청크 목록을 임베딩하고 단계 보고서를 저장한다. 성공은 None, 실패는 오류 코드다.

    부분 성공 보고서도 먼저 저장해 실패 원인을 보존한다. 후보 정리·다음 단계로 이동할지
    결정하는 책임은 run_attempt에 둔다. 외부 임베딩 호출 중 DB 잠금을 유지하지 않는다.
    """
    raw_report = version_service.call_external(backend['embed_and_store'], chunks)
    report = version_service.as_mapping(raw_report)
    jobs_repo.record_stage_result(job_id, dict(report, stage='embedding'))
    if report.get('status') != 'success':
        return failure_code(report.get('error_code'))
    return None


def run_attempt(actor, ids, expected_active_id, ingest_function, backend):
    """원본 확인 → 수집 → 임베딩 → 검증 → 게시 순서로 실행하고 결과 dict를 반환한다.

    ids는 document_id/version_id/job_id를 가진 dict다. stage는 실패한 단계를 기록한다.
    각 보조 함수의 DB 작업이 끝난 뒤 외부 함수를 호출하므로 SQL 잠금을 오래 잡지 않는다.
    """
    stage = 'received'
    try:
        source, info = read_attempt_source(ids)
        jobs_repo.record_stage_result(ids['job_id'], {'stage': stage, 'status': 'success'})

        stage = 'ingest'
        chunks, error_code = ingest_and_save_chunks(source, info, ids, ingest_function)
        if error_code is not None:
            return record_upload_failure(ids, stage, error_code, backend)

        stage = 'embedding'
        error_code = embed_and_record_result(chunks, ids['job_id'], backend)
        if error_code is not None:
            return record_upload_failure(ids, stage, error_code, backend)

        stage = 'verify'
        verify_uploaded_chunks(ids, backend)

        stage = 'publish'
        version_service.publish_version(
            actor, ids['document_id'], ids['version_id'], expected_active_id, embedding=backend)
        return dict(ids, status='success', error_code=None, recovery_required=False)
    except (DbError, OSError) as error:
        return record_upload_failure(ids, stage, error, backend)
    except Exception as error:
        # 예상하지 못한 코드 오류도 실패 원장을 남긴 뒤 호출자에게 다시 전달한다.
        record_upload_failure(ids, stage, error, backend)
        raise


def prepare_upload_source(source_path, metadata, upload_dir):
    """서버 파일을 읽어 (원본 Path, 저장 루트 Path, 저장 정보 dict)를 준비한다.

    해시·크기·경로는 사용자가 보낸 값을 쓰지 않는다. 이 함수는 DB 잠금 밖에서 호출한다.
    """
    try:
        source = Path(source_path).resolve(strict=True)
        if not source.is_file():
            raise DbError('INVALID_INPUT')
        digest, size = file_digest(source)
    except (OSError, TypeError):
        raise DbError('INVALID_INPUT') from None

    if upload_dir is None:
        upload_dir = get_settings().upload_dir
    root = Path(upload_dir).resolve()
    values = dict(metadata)
    values['file_name'] = metadata.get('file_name', source.name)
    values['sha256'] = digest
    values['file_size'] = size
    values['storage_path'] = 'pending/' + uuid4().hex
    values['data_category'] = metadata.get('data_category', 'operational')
    return source, root, values


def create_upload_candidate(actor, company_id, values, source, root):
    """후보와 최종 저장 경로를 한 번에 저장하고 (ID dict, 이전 활성 ID, 목적지 Path)를 반환한다.

    with transaction 안의 변경은 모두 성공해야 함께 확정된다. 파일 복사는 이 함수 밖이다.
    """
    with transaction() as database:
        version_service.require_manager(actor, database, company_id)
        expected_active_id = None
        if values.get('document_id') is not None:
            row = database.execute(
                text('SELECT active_version_id FROM documents WHERE id=:id FOR UPDATE'),
                {'id': require_id(values['document_id'])},
            ).first()
            if row:
                expected_active_id = row[0]
        ids = documents_repo.create_document_version(actor, values, connection=database)
        destination = source_destination(
            root, company_id, ids['document_id'], ids['version_id'], source.suffix)
        database.execute(
            text('UPDATE document_versions SET storage_path=:path WHERE id=:id'),
            {'path': str(destination), 'id': ids['version_id']},
        )
        # 기존 문서의 첫 원본 포인터는 보존하고 새 문서만 최종 경로로 확정한다.
        if not values.get('document_id') and 'storage_key' in {
                column['name'] for column in get_table_columns(database, 'documents')}:
            database.execute(text('UPDATE documents SET storage_key=:path WHERE id=:id'),
                             {'path': str(destination), 'id': ids['document_id']})
        database.execute(
            text('UPDATE index_jobs SET attempts=1,report=:report WHERE id=:id'),
            {'id': ids['job_id'], 'report': json.dumps({'_upload_root': str(root)})},
        )
    return ids, expected_active_id, destination


def upload_document(actor, source_path, metadata, *, ingest_document=None, embedding=None, upload_dir=None):
    """관리자 업로드의 원본 저장부터 게시까지 연결하고 결과 ID·상태를 반환한다.

    metadata는 company_id/doc_type, 선택 data_category/document_id/file_name이다.
    해시·크기·경로는 서버가 결정한다. 후보 생성 전 권한·입력 오류는 예외이며 후보 이후
    예상 처리 실패는 status=failed로 반환한다. 실패 원본은 같은 후보 재시도를 위해 보존한다.
    """
    if not isinstance(metadata, dict):
        raise DbError('INVALID_INPUT')
    company_id = require_id(metadata.get('company_id'))
    with WORKFLOW_LOCK:
        with transaction() as database:
            version_service.require_manager(actor, database, company_id)
        ingest_function = get_ingest_function(ingest_document)
        backend = version_service.get_embedding_backend(embedding)
        source, root, values = prepare_upload_source(source_path, metadata, upload_dir)
        ids, expected_active_id, destination = create_upload_candidate(
            actor, company_id, values, source, root)

        # 후보 저장이 확정된 다음 원본을 복사한다. 복사 실패도 같은 후보에 기록한다.
        try:
            if not destination.resolve().is_relative_to(root):
                raise DbError('INVALID_INPUT')
            copy_original(source, destination, values['sha256'], values['file_size'])
        except (DbError, OSError) as error:
            return record_upload_failure(ids, 'received', error, backend)
        return run_attempt(actor, ids, expected_active_id, ingest_function, backend)


def check_retry_allowed(document, version, reports):
    """문서·버전·보고서 dict가 재시도 가능한 상태인지 확인한다. 반환값은 없다."""
    if version['version_status'] not in ('failed', 'processing'):
        raise DbError('INVALID_STATE')
    if document['active_version_id'] == version['id']:
        raise DbError('INVALID_STATE')
    if document['deleted_at'] is not None:
        raise DbError('INVALID_STATE')
    if document['publication_status'] == 'blocked':
        raise DbError('INVALID_STATE')
    phase = reports.get('_publication', {}).get('phase')
    if phase in version_service.PENDING_PHASES:
        raise DbError('INVALID_STATE')


def prepare_retry(actor, version_id):
    """권한·후보를 잠가 재시도를 준비하고 ID·이전 활성 ID·원본 정보를 dict로 반환한다.

    과거 보고서를 _attempts에 보존하고 새 시도를 received부터 시작한다.
    여기서는 경로 문자열만 다루며 실제 파일 확인은 DB 변경 확정 후에 한다.
    """
    with transaction() as database:
        document, version, job, reports = read_retry_records(database, actor, version_id)
        check_retry_allowed(document, version, reports)
        version_service.ensure_no_pending_publication(database, document['id'])
        root = Path(reports.get('_upload_root', ''))
        source = Path(version['storage_path'])
        if '_upload_root' not in reports:
            raise DbError('INVALID_STATE')

        reset_report = build_retry_report(root, reports)
        reset_retry_records(database, version_id, job['id'], reset_report)
        ids = {'document_id': document['id'], 'version_id': version_id, 'job_id': job['id']}
    return {
        'ids': ids,
        'expected_active_id': document['active_version_id'],
        'root': root,
        'source': source,
        'sha256': version['sha256'],
        'file_size': version['file_size'],
    }


def read_retry_records(database, actor, version_id):
    """관리자 권한으로 재시도 대상의 (문서, 버전, 작업, 보고서) dict들을 읽는다.

    전달받은 같은 DB 연결에서 계정·회사 → 문서·버전 → 작업 순서로 잠근다.
    재시도 가능한 상태인지 판단하거나 DB 값을 변경하지는 않는다.
    """
    reference = database.execute(
        text('SELECT document_id,company_id FROM document_versions WHERE id=:id'),
        {'id': version_id},
    ).mappings().one_or_none()
    if not reference:
        raise DbError('INVALID_STATE')
    version_service.require_manager(actor, database, reference['company_id'])
    document, version = version_service.read_candidate(
        database, reference['document_id'], version_id, lock=True)
    job, reports = version_service.read_job(database, version_id, lock=True)
    return document, version, job, reports


def build_retry_report(root, reports):
    """저장 루트 Path와 기존 보고서 dict로 이전 시도를 보존한 새 보고서 dict를 만든다.

    원래 보고서를 수정하거나 DB에 저장하지 않는다. 현재 시도는 _attempts 목록의
    마지막에 추가하고, 새 단계별 결과를 쓸 자리는 비워 둔다.
    """
    history = reports.get('_attempts', [])
    previous = {}
    for key, value in reports.items():
        if key != '_attempts':
            previous[key] = value
    return {'_upload_root': str(root), '_attempts': history + [previous]}


def reset_retry_records(database, version_id, job_id, report):
    """같은 DB 연결에서 버전·작업을 재시도 시작 상태로 저장한다. 반환값은 없다.

    prepare_retry가 권한과 상태를 확인하고 만든 보고서 dict를 받는다.
    직접 커밋하지 않으므로 두 UPDATE 중 하나라도 실패하면 함께 취소된다.
    """
    database.execute(
        text("UPDATE document_versions SET version_status='processing' WHERE id=:id"),
        {'id': version_id},
    )
    database.execute(text('''
        UPDATE index_jobs SET stage='received', attempts=attempts+1, report=:report,
                              error_code=NULL, error_message=NULL
        WHERE id=:id
    '''), {'id': job_id, 'report': json.dumps(report, ensure_ascii=False)})
    legacy.reset_job_for_retry(database, job_id)


def retry_upload(actor, version_id, *, ingest_document=None, embedding=None, source_path=None):
    """재시도 준비 → 경로 확인 → 이전 벡터 정리 → 처리 순서로 실행하고 결과 dict를 반환한다.

    실패 또는 중단된 후보의 문서·버전·작업 ID를 그대로 쓴다. 게시 복구가 남으면 거부한다.
    source_path는 저장 원본이 없을 때 같은 파일을 다시 제공하는 선택 인자다.
    기존 파일은 덮지 않으며 원본이 있으면 저장된 파일로 run_attempt를 실행한다.
    """
    require_id(version_id)
    with WORKFLOW_LOCK:
        ingest_function = get_ingest_function(ingest_document)
        backend = version_service.get_embedding_backend(embedding)
        retry = prepare_retry(actor, version_id)
        ids = retry['ids']

        # prepare_retry의 DB 변경이 확정된 뒤 파일과 외부 벡터 저장소에 접근한다.
        try:
            root = retry['root'].resolve()
            source = retry['source'].resolve()
            if not source.is_relative_to(root):
                raise DbError('INVALID_STATE')
        except (DbError, OSError) as error:
            return record_upload_failure(ids, 'received', error, backend)
        try:
            backend['delete_vectors'](ids['document_id'], version_id)
        except Exception as error:
            return record_upload_failure(ids, 'received', error, backend)
        if source_path is not None:
            try:
                replacement = Path(source_path).resolve(strict=True)
                copy_original(replacement, source, retry['sha256'], retry['file_size'])
            except (DbError, OSError) as error:
                return record_upload_failure(ids, 'received', error, backend)
        return run_attempt(actor, ids, retry['expected_active_id'], ingest_function, backend)
