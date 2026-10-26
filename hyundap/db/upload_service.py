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
from . import chunks_repo, documents_repo, jobs_repo, versions_repo, version_service
from .errors import DbError
from .runtime import require_id, transaction


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
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
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
    digest, size = hashlib.sha256(), 0
    try:
        with source.open('rb') as incoming, temporary.open('xb') as outgoing:
            for block in iter(lambda: incoming.read(1024 * 1024), b''):
                size += len(block)
                if size > 104857600:
                    raise DbError('INVALID_INPUT')
                digest.update(block)
                outgoing.write(block)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if digest.hexdigest() != expected_hash or size != expected_size:
            raise DbError('CONFLICT')
        # 같은 파일시스템의 hard link는 기존 목적지를 덮지 않고 완전한 파일만 공개한다.
        os.link(temporary, destination)
    finally:
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
            backend.delete_vectors(ids['document_id'], ids['version_id'])
        except Exception:
            cleanup_pending = True
        with transaction() as database:
            job, reports = version_service.read_job(database, ids['version_id'], lock=True)
            reports['_cleanup_pending'] = cleanup_pending
            database.execute(text('UPDATE index_jobs SET report=:report,error_code=:code WHERE id=:id'),
                             {'report': json.dumps(reports, ensure_ascii=False), 'code': code, 'id': job['id']})
        return dict(ids, status='failed', error_code=code, recovery_required=cleanup_pending)


def run_attempt(actor, ids, expected_active_id, ingest_function, backend):
    """저장된 원본으로 한 번의 수집·적재·검증·게시 시도를 실행한다. 외부 작업은 TX 밖이다."""
    stage = 'received'
    try:
        with transaction() as database:
            document, version = version_service.read_candidate(database, ids['document_id'], ids['version_id'])
            name = database.execute(text('SELECT name FROM companies WHERE id=:id'),
                                    {'id': document['company_id']}).scalar_one()
        source = Path(version['storage_path'])
        if file_digest(source) != (version['sha256'], version['file_size']):
            raise DbError('CONFLICT')
        jobs_repo.record_stage_result(ids['job_id'], {'stage': stage, 'status': 'success'})
        stage = 'ingest'
        info = {'document_id': ids['document_id'], 'version_id': ids['version_id'],
                'company_id': document['company_id'], 'company_name': name,
                'doc_type': document['doc_type'], 'file_name': version['file_name']}
        result = version_service.as_mapping(version_service.call_external(ingest_function, str(source), info))
        if result.get('status') != 'success':
            return record_upload_failure(ids, stage, failure_code(result.get('error_code')), backend)
        chunks = result.get('chunks')
        if not isinstance(chunks, list) or not chunks:
            raise DbError('INVALID_INPUT')
        manifest = []
        for chunk in chunks:
            row = version_service.as_mapping(chunk)
            if (row.get('document_id') != ids['document_id'] or row.get('version_id') != ids['version_id']
                    or row.get('company_id') != document['company_id'] or not isinstance(row.get('text'), str)):
                raise DbError('INVALID_INPUT')
            manifest.append({'id': row.get('chunk_id'), 'chunk_index': row.get('chunk_index'),
                             'content_hash': hashlib.sha256(row['text'].encode('utf-8')).hexdigest()})
        chunks_repo.save_manifest(ids['version_id'], manifest)
        report = version_service.as_mapping(result.get('report') or {})
        jobs_repo.record_stage_result(ids['job_id'], dict(report, stage=stage, status='success'))
        stage = 'embedding'
        report = version_service.as_mapping(version_service.call_external(backend.embed_and_store, chunks))
        jobs_repo.record_stage_result(ids['job_id'], dict(report, stage=stage))
        if report.get('status') != 'success':
            return record_upload_failure(ids, stage, failure_code(report.get('error_code')), backend)
        stage = 'verify'
        complete = version_service.validate_completeness(ids['document_id'], ids['version_id'], embedding=backend)
        if not complete['ok']:
            raise DbError('INCOMPLETE_INDEX')
        jobs_repo.record_stage_result(ids['job_id'], {'stage': stage, 'status': 'success',
                                                    'total': complete['expected_count']})
        stage = 'publish'
        version_service.publish_version(actor, ids['document_id'], ids['version_id'], expected_active_id, embedding=backend)
        return dict(ids, status='success', error_code=None, recovery_required=False)
    except (DbError, OSError) as error:
        return record_upload_failure(ids, stage, error, backend)
    except Exception as error:
        # 예상하지 못한 코드 오류도 실패 원장을 남긴 뒤 호출자에게 다시 전달한다.
        record_upload_failure(ids, stage, error, backend)
        raise


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
        try:
            source = Path(source_path).resolve(strict=True)
            if not source.is_file():
                raise DbError('INVALID_INPUT')
            digest, size = file_digest(source)
        except (OSError, TypeError):
            raise DbError('INVALID_INPUT') from None
        root = Path(upload_dir if upload_dir is not None else get_settings().upload_dir).resolve()
        values = dict(metadata, file_name=metadata.get('file_name', source.name), sha256=digest,
                      file_size=size, storage_path='pending', data_category=metadata.get('data_category', 'operational'))
        with transaction() as database:
            version_service.require_manager(actor, database, company_id)
            expected = None
            if metadata.get('document_id') is not None:
                row = database.execute(text('SELECT active_version_id FROM documents WHERE id=:id FOR UPDATE'),
                                       {'id': require_id(metadata['document_id'])}).first()
                expected = row[0] if row else None
            ids = documents_repo.create_document_version(actor, values, connection=database)
            destination = source_destination(root, company_id, ids['document_id'], ids['version_id'], source.suffix)
            database.execute(text('UPDATE document_versions SET storage_path=:path WHERE id=:id'),
                             {'path': str(destination), 'id': ids['version_id']})
            database.execute(text('UPDATE index_jobs SET attempts=1,report=:report WHERE id=:id'),
                             {'id': ids['job_id'], 'report': json.dumps({'_upload_root': str(root)})})
        try:
            if not destination.resolve().is_relative_to(root):
                raise DbError('INVALID_INPUT')
            copy_original(source, destination, digest, size)
        except (DbError, OSError) as error:
            return record_upload_failure(ids, 'received', error, backend)
        return run_attempt(actor, ids, expected, ingest_function, backend)


def retry_upload(actor, version_id, *, ingest_document=None, embedding=None, source_path=None):
    """저장된 원본으로 같은 문서·버전·작업 ID를 재시도한다. 새 중복 후보를 만들지 않는다.

    실패 또는 중단된 processing 후보만 허용하고 게시 복구가 남은 작업은 먼저 복구한다.
    과거 보고서를 _attempts에 보존하며 시도 횟수를 증가시킨다. 저장 원본이 없을 때만
    source_path로 같은 해시·크기의 원본을 다시 제공할 수 있다. 기존 파일은 덮지 않는다.
    """
    require_id(version_id)
    with WORKFLOW_LOCK:
        ingest_function = get_ingest_function(ingest_document)
        backend = version_service.get_embedding_backend(embedding)
        with transaction() as database:
            reference = database.execute(text('SELECT document_id,company_id FROM document_versions WHERE id=:id'),
                                         {'id': version_id}).mappings().one_or_none()
            if not reference:
                raise DbError('INVALID_STATE')
            version_service.require_manager(actor, database, reference['company_id'])
            document, version = version_service.read_candidate(database, reference['document_id'], version_id, lock=True)
            job, reports = version_service.read_job(database, version_id, lock=True)
            if (version['version_status'] not in ('failed', 'processing') or document['active_version_id'] == version_id
                    or document['deleted_at'] is not None or document['publication_status'] == 'blocked'
                    or reports.get('_publication', {}).get('phase') in version_service.PENDING_PHASES):
                raise DbError('INVALID_STATE')
            version_service.ensure_no_pending_publication(database, document['id'])
            root = Path(reports.get('_upload_root', ''))
            source = Path(version['storage_path'])
            if '_upload_root' not in reports:
                raise DbError('INVALID_STATE')
            history = reports.get('_attempts', [])
            previous = {key: value for key, value in reports.items() if key != '_attempts'}
            reset = {'_upload_root': str(root), '_attempts': history + [previous]}
            database.execute(text("UPDATE document_versions SET version_status='processing' WHERE id=:id"), {'id': version_id})
            database.execute(text("UPDATE index_jobs SET stage='received',attempts=attempts+1,report=:report,error_code=NULL,error_message=NULL WHERE id=:id"),
                             {'id': job['id'], 'report': json.dumps(reset, ensure_ascii=False)})
            ids = {'document_id': document['id'], 'version_id': version_id, 'job_id': job['id']}
            expected = document['active_version_id']
        try:
            root, source = root.resolve(), source.resolve()
            if not source.is_relative_to(root):
                raise DbError('INVALID_STATE')
        except (DbError, OSError) as error:
            return record_upload_failure(ids, 'received', error, backend)
        try:
            backend.delete_vectors(ids['document_id'], version_id)
        except Exception as error:
            return record_upload_failure(ids, 'received', error, backend)
        if source_path is not None:
            try:
                copy_original(Path(source_path).resolve(strict=True), source, version['sha256'], version['file_size'])
            except (DbError, OSError) as error:
                return record_upload_failure(ids, 'received', error, backend)
        return run_attempt(actor, ids, expected, ingest_function, backend)
