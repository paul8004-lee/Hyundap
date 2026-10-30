"""원본 저장→수집→청크 원장→임베딩→검증→게시를 연결하는 DB 담당 흐름.

다른 담당 모듈은 실제 함수를 준비한 뒤 기본 경로로 연결된다. 테스트는 함수를 주입한다.
서버가 파일 해시·크기·저장 경로를 정하며 외부 작업 동안 SQL 잠금을 유지하지 않는다.
"""

import hashlib
import json
import re
from uuid import uuid4
from pathlib import Path
from threading import RLock

from sqlalchemy import text

from hyundap.common.config import get_settings
from . import chunks_repo, documents_repo, jobs_repo, versions_repo, version_service
from .errors import DbError
from .classification import (validate_auto_request, decision_fields, reusable_classification, classification_fields)
from .ingest_adapter import call_ingest
from .source_files import file_digest, source_destination, resolve_stored_source, copy_original
from .runtime import require_id, transaction, shared_table, split_layout
from .tenancy import company_scope, company_for_reference, processing_lock, authorize_company


# 단일 DB 모드의 잠금이다. 분리 모드는 tenancy의 회사별 OS 잠금을 사용한다.
WORKFLOW_LOCK = RLock()


def get_ingest_function(function=None):
    """주입한 함수 또는 팀 수집 진입점을 선택한다. 미구현 뼈대는 안전하게 거부한다."""
    # 별도 함수가 주어지지 않으면 수집 모듈의 기본 진입점을 찾는다.
    if function is None:
        from hyundap.ingest import pipeline
        function = getattr(pipeline, 'ingest_document', None)
    if not callable(function):
        raise DbError('INTEGRATION_UNAVAILABLE')
    return function





def failure_code(error):
    """예외에서 안전한 코드만 가져온다. 원시 메시지·URL·접속 비밀값은 저장하지 않는다."""
    # 저장 가능한 형식의 오류 코드만 남기고 나머지는 공통 코드로 바꾼다.
    if isinstance(error, str):
        code = error
    else:
        code = getattr(error, 'code', None)
    if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code):
        return code
    return 'PROCESSING_FAILED'


def upload_result(ids, *, connection=None, **status):
    """이번 시도의 수집 성공 정보만 반환한다. 실패 시 유형은 공개값이 아닌 후보값이다."""
    with transaction(connection) as database:
        _, reports = version_service.read_job(database, ids['version_id'])
        document, _ = version_service.read_candidate(database, ids['document_id'], ids['version_id'])
    result = dict(ids, **status)
    ingest = reports.get('ingest', {})
    if ingest.get('status') == 'success':
        decision = decision_fields(ingest)
        if not decision and not reports.get('_auto_classify'):
            decision = dict(doc_type=document['doc_type'], doc_type_fallback=False, doc_type_fallback_reason=None)
        result.update(decision)
        result.update(classification_fields(ingest))
    return result


def record_upload_failure(ids, stage, error, backend):
    """게시 미완료는 복구에 맡기고, 비활성 처리 후보만 실패 기록·벡터 정리한다."""
    code = failure_code(error)
    with processing_lock(version_service.PUBLICATION_LOCK, publication=True):
        # 게시 중이거나 이미 활성화된 후보는 게시 복구 절차에서 처리한다.
        with transaction() as database:
            document, _ = version_service.read_candidate(database, ids['document_id'], ids['version_id'], lock=True)
            job, reports = version_service.read_job(database, ids['version_id'], lock=True)
            phase = reports.get('_publication', {}).get('phase')
            if phase in version_service.PENDING_PHASES or document['active_version_id'] == ids['version_id']:
                return upload_result(ids, connection=database, status='failed', error_code='RECOVERY_REQUIRED', recovery_required=True)
            if phase == 'restored':
                return upload_result(ids, connection=database, status='failed', error_code=code, recovery_required=False)
        stage_status = reports.get(stage, {}).get('status')
        if stage_status not in ('partial', 'failed'):
            jobs_repo.record_stage_result(ids['job_id'], {'stage': stage, 'status': 'failed', 'error_code': code})
        versions_repo.mark_failed(ids['version_id'])

        # 외부 벡터 삭제는 SQL 트랜잭션 밖에서 수행한다.
        cleanup_pending = False
        try:
            backend['delete_vectors'](ids['document_id'], ids['version_id'])
        except Exception:
            cleanup_pending = True
        # 삭제 실패도 기록해 이후 복구가 필요한지 알 수 있게 한다.
        with transaction() as database:
            job, reports = version_service.read_job(database, ids['version_id'], lock=True)
            reports['_cleanup_pending'] = cleanup_pending
            database.execute(text('UPDATE index_jobs SET report=:report,error_code=:code WHERE id=:id'),
                             {'report': json.dumps(reports, ensure_ascii=False), 'code': code, 'id': job['id']})
        return upload_result(ids, status='failed', error_code=code, recovery_required=cleanup_pending)




def read_attempt_source(ids):
    """문서·버전 ID로 저장 원본을 확인하고 (파일 경로, 수집 정보, 검증한 해시)를 반환한다.

    DB 조회를 끝낸 뒤 파일을 읽는다. 파일의 해시·크기가 저장 당시와 다르면 중단한다.
    """
    # DB에서 원본 정보를 읽은 다음 트랜잭션 밖에서 실제 파일을 검증한다.
    with transaction(document_id=ids['document_id'], version_id=ids['version_id']) as database:
        document, version = version_service.read_candidate(
            database, ids['document_id'], ids['version_id'])
        _, reports = version_service.read_job(database, ids['version_id'])
        company_name = database.execute(
            text(f"SELECT name FROM {shared_table('companies', connection=database)} WHERE id=:id"),
            {'id': document['company_id']},
        ).scalar_one()

    # DB 조회가 끝난 뒤 파일을 읽어 저장 당시 해시와 크기를 비교한다.
    if '_upload_root' not in reports:
        raise DbError('INVALID_STATE')
    source = resolve_stored_source(version['storage_path'], reports['_upload_root'])
    try:
        actual_digest = file_digest(source)
    except (FileNotFoundError, PermissionError):
        raise DbError('FILE_UNAVAILABLE') from None
    if actual_digest != (version['sha256'], version['file_size']):
        raise DbError('CONFLICT')
    info = {
        'document_id': ids['document_id'],
        'version_id': ids['version_id'],
        'company_id': document['company_id'],
        'company_name': company_name,
        'doc_type': document['doc_type'],
        'file_name': version['file_name'],
    }
    if reports.get('_auto_classify'):
        info['_auto_classify'] = True
        info['_classification'] = reusable_classification(reports)
    return source, info, version['sha256']


def save_ingest_chunks(ids, company_id, result, *, automatic=False):
    """성공한 수집 결과의 청크를 검사·기록하고 원래 청크 목록을 반환한다.

    DB에는 식별자·순번·본문 해시만 저장한다. 임베딩에 넘길 원래 청크 객체는 유지한다.
    """
    decision = decision_fields(version_service.as_mapping(result.get('report') or {}), required=automatic)
    chunks = result.get('chunks')
    if not isinstance(chunks, list) or not chunks:
        raise DbError('INVALID_INPUT')
    # 원래 청크는 임베딩에 넘기고 DB에는 식별자와 본문 해시만 저장한다.
    manifest = []
    # 수집기가 반환한 청크를 하나씩 검사한다. 매 반복에서 현재 청크 하나만 다룬다.
    # 소속과 본문을 확인한 뒤 그 청크의 ID·순번·해시를 manifest에 추가한다.
    for chunk in chunks:
        chunk_data = version_service.as_mapping(chunk)
        if chunk_data.get('document_id') != ids['document_id']:
            raise DbError('INVALID_INPUT')
        if chunk_data.get('version_id') != ids['version_id']:
            raise DbError('INVALID_INPUT')
        if chunk_data.get('company_id') != company_id:
            raise DbError('INVALID_INPUT')
        if automatic and chunk_data.get('doc_type') != decision['doc_type']:
            raise DbError('INVALID_INPUT')
        if not isinstance(chunk_data.get('text'), str):
            raise DbError('INVALID_INPUT')
        # 원문 대신 SHA-256 해시를 원장에 남겨 이후 변조를 확인한다.
        content_hash = hashlib.sha256(chunk_data['text'].encode('utf-8')).hexdigest()
        manifest.append({
            'id': chunk_data.get('chunk_id'),
            'chunk_index': chunk_data.get('chunk_index'),
            'content_hash': content_hash,
        })
    chunks_repo.save_manifest(ids['version_id'], manifest)

    # 경고 원문은 저장하지 않고 개수만 단계 보고서에 남긴다.
    report = version_service.as_mapping(result.get('report') or {})
    report = dict(report)
    if 'warnings' in report:
        warnings = report['warnings']
        if not isinstance(warnings, list):
            raise DbError('INVALID_INPUT')
        # 청크 원장 반복은 이미 끝났다. 여기서는 경고 목록의 각 값이 문자열인지 검사한다.
        for value in warnings:
            if not isinstance(value, str):
                raise DbError('INVALID_INPUT')
        report['warning_count'] = len(warnings)
        report.pop('warnings')
    jobs_repo.record_stage_result(
        ids['job_id'], dict(report, stage='ingest', status='success'))
    return chunks


def verify_uploaded_chunks(ids, backend):
    """DB 청크 목록과 벡터 저장 결과를 대조하고 성공 보고서를 남긴다. 반환값은 없다."""
    # 원장과 벡터가 일치할 때만 검증 성공 보고서를 기록한다.
    complete = version_service.validate_completeness(
        ids['document_id'], ids['version_id'], embedding=backend)
    if not complete['ok']:
        raise DbError('INCOMPLETE_INDEX')
    jobs_repo.record_stage_result(ids['job_id'], {
        'stage': 'verify', 'status': 'success', 'total': complete['expected_count'],
    })


def ingest_and_save_chunks(source, info, ids, ingest_function, file_hash=None):
    """원본 Path와 수집 정보로 수집 단계를 실행하고 (청크 목록, 오류 코드)를 반환한다.

    검증한 해시는 해당 선택 인자를 지원하는 수집기에만 전달한다. 성공하면 청크·보고서를 저장하고 (chunks, None)을 반환한다. 수집기가 실패 결과를
    반환하면 (None, 안전한 오류 코드)다. 실패 후보 정리는 run_attempt가 담당한다.
    """
    # 중복 조회 콜백은 현재 회사 범위에 묶고 현재 후보는 조회에서 제외한다.
    def find_by_hash(company_id, sha256):
        """서버의 현재 후보 문맥에만 묶인 조회다. 임의 회사·해시 제외를 허용하지 않는다."""
        if company_id != info['company_id']:
            raise DbError('INVALID_INPUT')
        return documents_repo.find_duplicate_document(
            company_id, sha256, exclude_version_id=ids['version_id'])

    automatic = info.get('_auto_classify', False)
    public_info = {key: value for key, value in info.items() if not key.startswith('_')}
    result = call_ingest(
        ingest_function, source, public_info, find_by_hash=find_by_hash, file_hash=file_hash,
        classify=get_settings().doc_classify, automatic=automatic,
        classification=info.get('_classification'))
    if result.get('status') != 'success':
        return None, failure_code(result.get('error_code'))
    chunks = save_ingest_chunks(ids, info['company_id'], result, automatic=automatic)
    return chunks, None


def embed_and_record_result(chunks, job_id, backend):
    """청크 목록을 임베딩하고 단계 보고서를 저장한다. 성공은 None, 실패는 오류 코드다.

    부분 성공 보고서도 먼저 저장해 실패 원인을 보존한다. 후보 정리·다음 단계로 이동할지
    결정하는 책임은 run_attempt에 둔다. 외부 임베딩 호출 중 DB 잠금을 유지하지 않는다.
    """
    # 외부 임베딩 결과를 받은 뒤 성공과 부분 실패 모두 보고서에 기록한다.
    raw_report = version_service.call_external(backend['embed_and_store'], chunks)
    report = version_service.as_mapping(raw_report)
    # 부분 실패도 정제된 단계 보고서로 저장한 뒤 오류 코드를 반환한다.
    jobs_repo.record_stage_result(job_id, dict(report, stage='embedding'))
    if report.get('status') != 'success':
        return failure_code(report.get('error_code'))
    return None


def run_attempt(actor, ids, expected_active_id, ingest_function, backend):
    """원본 확인 → 수집 → 임베딩 → 검증 → 게시 순서로 실행하고 결과 dict를 반환한다.

    ids는 document_id/version_id/job_id를 가진 dict다. stage는 실패한 단계를 기록한다.
    각 보조 함수의 DB 작업이 끝난 뒤 외부 함수를 호출하므로 SQL 잠금을 오래 잡지 않는다.
    """
    # 실패한 단계를 알 수 있도록 각 작업 전에 stage를 갱신한다.
    stage = 'received'
    try:
        source, info, file_hash = read_attempt_source(ids)
        jobs_repo.record_stage_result(ids['job_id'], {'stage': stage, 'status': 'success'})

        stage = 'ingest'
        chunks, error_code = ingest_and_save_chunks(source, info, ids, ingest_function, file_hash)
        if error_code is not None:
            return record_upload_failure(ids, stage, error_code, backend)

        stage = 'embedding'
        error_code = embed_and_record_result(chunks, ids['job_id'], backend)
        if error_code is not None:
            return record_upload_failure(ids, stage, error_code, backend)

        # 임베딩 후 DB 원장과 실제 벡터 청크를 대조한다.
        stage = 'verify'
        verify_uploaded_chunks(ids, backend)

        # 검증에 성공한 후보만 게시 함수로 넘긴다.
        stage = 'publish'
        # 게시 전 공개 반환값을 확보한다. 게시 후 조회 실패로 성공 계약이 축소되지 않는다.
        success = upload_result(ids, status='success', error_code=None, recovery_required=False)
        version_service.publish_version(
            actor, ids['document_id'], ids['version_id'], expected_active_id, embedding=backend)
    except (DbError, OSError) as error:
        return record_upload_failure(ids, stage, error, backend)
    except Exception as error:
        # 예상하지 못한 코드 오류도 실패 원장을 남긴 뒤 호출자에게 다시 전달한다.
        record_upload_failure(ids, stage, error, backend)
        raise

    # 게시 성공 후에는 DB를 다시 읽지 않고 준비한 공개 결과를 반환한다.
    return success


def prepare_upload_source(source_path, metadata, upload_dir):
    """서버 파일을 읽어 (원본 Path, 저장 루트 Path, 저장 정보 dict)를 준비한다.

    해시·크기·경로는 사용자가 보낸 값을 쓰지 않는다. 이 함수는 DB 잠금 밖에서 호출한다.
    """
    # 파일 경로를 확인하고 서버가 직접 계산한 해시와 크기를 사용한다.
    try:
        source = Path(source_path).resolve(strict=True)
        if not source.is_file():
            raise DbError('INVALID_INPUT')
        digest, size = file_digest(source)
    except (FileNotFoundError, PermissionError):
        raise DbError('FILE_UNAVAILABLE') from None
    except (OSError, TypeError, ValueError):
        raise DbError('INVALID_INPUT') from None

    if upload_dir is None:
        upload_dir = get_settings().upload_dir
    root = Path(upload_dir).resolve()
    # 입력을 복사한 뒤 해시, 크기, 내부 경로는 서버가 계산한 값을 채운다.
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
        # 기존 문서라면 게시 시 비교할 현재 활성 버전을 잠가 읽는다.
        expected_active_id = None
        if values.get('document_id') is not None:
            active_version_row = database.execute(
                text('SELECT active_version_id FROM documents WHERE id=:id FOR UPDATE'),
                {'id': require_id(values['document_id'])},
            ).first()
            if active_version_row:
                expected_active_id = active_version_row[0]
        ids = documents_repo.create_document_version(actor, values, connection=database)
        destination = source_destination(
            root, company_id, ids['document_id'], ids['version_id'], source.suffix)
        # 후보 경로와 최초 시도 정보를 후보 생성과 함께 확정한다.
        database.execute(
            text('UPDATE document_versions SET storage_path=:path WHERE id=:id'),
            {'path': str(destination), 'id': ids['version_id']},
        )
        database.execute(
            text('UPDATE index_jobs SET attempts=1,report=:report WHERE id=:id'),
            {'id': ids['job_id'], 'report': json.dumps(dict({'_upload_root': str(root)}, **({'_auto_classify': True} if values.get('auto_classify') else {})))},
        )
    return ids, expected_active_id, destination


def upload_document(actor, source_path, metadata, *, ingest_document=None, embedding=None, upload_dir=None):
    """회사별 DB 문맥에서 기존 원본→게시 처리를 실행한다. 인증은 공통 DB에서 재검증한다."""
    if not isinstance(metadata, dict):
        raise DbError('INVALID_INPUT')
    automatic = validate_auto_request(metadata)
    if automatic and not get_settings().doc_classify:
        raise DbError('INVALID_INPUT')
    company_id = require_id(metadata.get('company_id'))
    authorize_company(actor, company_id, manager=True)
    with company_scope(company_id):
        return _upload_document(actor, source_path, metadata, company_id=company_id, ingest_document=ingest_document,
                                embedding=embedding, upload_dir=upload_dir)


def _upload_document(actor, source_path, metadata, *, company_id, ingest_document=None, embedding=None, upload_dir=None):
    """관리자 업로드의 원본 저장부터 게시까지 연결하고 결과 ID·상태를 반환한다.

    company_id는 공개 진입점에서 검증한 값이다. metadata는 company_id/doc_type,
    선택 data_category/document_id/file_name이다.
    해시·크기·경로는 서버가 결정한다. 후보 생성 전 권한·입력 오류는 예외이며 후보 이후
    예상 처리 실패는 status=failed로 반환한다. 실패 원본은 같은 후보 재시도를 위해 보존한다.
    """
    # 입력 형식은 공개 진입점에서 확인했다. 잠금을 얻은 뒤 현재 관리자 권한은 다시 확인한다.
    with processing_lock(WORKFLOW_LOCK):
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
    # 활성 버전이나 삭제, 차단된 문서는 재시도 대상으로 허용하지 않는다.
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
    # 현재 세션과 후보 상태를 다시 검사하고 이전 시도를 보존한다.
    with transaction(version_id=version_id) as database:
        document, version, job, reports = read_retry_records(database, actor, version_id)
        check_retry_allowed(document, version, reports)
        if reports.get('_auto_classify') and reusable_classification(reports) is None and not get_settings().doc_classify:
            raise DbError('INVALID_STATE')
        version_service.ensure_no_pending_publication(database, document['id'])
        upload_root = Path(reports.get('_upload_root', ''))
        stored_source = Path(version['storage_path'])
        if '_upload_root' not in reports:
            raise DbError('INVALID_STATE')

        # 실제 경로와 파일은 트랜잭션이 끝난 뒤 retry_upload에서 검사한다.
        reset_report = build_retry_report(upload_root, reports)
        reset_retry_records(database, version_id, job['id'], reset_report)
        ids = {'document_id': document['id'], 'version_id': version_id, 'job_id': job['id']}
    return {
        'ids': ids,
        'expected_active_id': document['active_version_id'],
        'root': upload_root,
        'source': stored_source,
        'sha256': version['sha256'],
        'file_size': version['file_size'],
    }


def read_retry_records(database, actor, version_id):
    """관리자 권한으로 재시도 대상의 (문서, 버전, 작업, 보고서) dict들을 읽는다.

    전달받은 같은 DB 연결에서 계정·회사 → 문서·버전 → 작업 순서로 잠근다.
    재시도 가능한 상태인지 판단하거나 DB 값을 변경하지는 않는다.
    """
    # 버전이 속한 문서를 찾고 재시도에 필요한 문서, 버전, 작업을 읽는다.
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
    # 기존 시도 목록은 유지하고 직전 보고서를 마지막 시도로 추가한다.
    previous_attempts = reports.get('_attempts', [])
    latest_attempt = {}
    # 현재 보고서의 키와 값을 하나씩 읽어 직전 시도의 기록을 만든다.
    # _attempts는 별도 history로 이미 보관하므로 직전 기록에 다시 넣지 않는다.
    for key, value in reports.items():
        if key != '_attempts':
            latest_attempt[key] = value
    result = {'_upload_root': str(root), '_attempts': previous_attempts + [latest_attempt]}
    if reports.get('_auto_classify'):
        result['_auto_classify'] = True
    return result


def reset_retry_records(database, version_id, job_id, report):
    """같은 DB 연결에서 버전·작업을 재시도 시작 상태로 저장한다. 반환값은 없다.

    prepare_retry가 권한과 상태를 확인하고 만든 보고서 dict를 받는다.
    직접 커밋하지 않으므로 두 UPDATE 중 하나라도 실패하면 함께 취소된다.
    """
    # 버전과 작업의 재시도 상태를 호출자의 같은 트랜잭션에서 갱신한다.
    database.execute(
        text("UPDATE document_versions SET version_status='processing' WHERE id=:id"),
        {'id': version_id},
    )
    jobs_repo.reset_job_for_retry(database, job_id, report)


def retry_upload(actor, version_id, *, ingest_document=None, embedding=None, source_path=None):
    """전역 식별자로 회사 DB를 선택한 뒤 기존 후보를 재시도한다."""
    version_id = require_id(version_id)
    authorize_company(actor, manager=True)
    company_id = None
    if split_layout():
        company_id = company_for_reference(version_id=version_id)
    authorize_company(actor, company_id, manager=True)
    with company_scope(company_id):
        return _retry_upload(actor, version_id, ingest_document=ingest_document,
                             embedding=embedding, source_path=source_path)


def _retry_upload(actor, version_id, *, ingest_document=None, embedding=None, source_path=None):
    """재시도 준비 → 경로 확인 → 이전 벡터 정리 → 처리 순서로 실행하고 결과 dict를 반환한다.

    실패 또는 중단된 후보의 문서·버전·작업 ID를 그대로 쓴다. 게시 복구가 남으면 거부한다.
    source_path는 저장 원본이 없을 때 같은 파일을 다시 제공하는 선택 인자다.
    기존 파일은 덮지 않으며 원본이 있으면 저장된 파일로 run_attempt를 실행한다.
    """
    # 저장된 원본 또는 다시 제공된 원본을 확인하고 같은 후보로 재시도한다.
    # version_id 형식은 retry_upload가 확인했다. 후보의 현재 상태는 prepare_retry에서 검사한다.
    # 같은 프로세스에서 업로드와 재시도 흐름을 순차 실행한다.
    with processing_lock(WORKFLOW_LOCK):
        ingest_function = get_ingest_function(ingest_document)
        backend = version_service.get_embedding_backend(embedding)
        retry_info = prepare_retry(actor, version_id)
        ids = retry_info['ids']

        # prepare_retry의 DB 변경이 확정된 뒤 파일과 외부 벡터 저장소에 접근한다.
        try:
            source = resolve_stored_source(retry_info['source'], retry_info['root'])
        except (DbError, OSError) as error:
            return record_upload_failure(ids, 'received', error, backend)
        # 이전 시도에서 남은 벡터를 삭제한 뒤 같은 후보를 다시 처리한다.
        try:
            backend['delete_vectors'](ids['document_id'], version_id)
        except Exception as error:
            return record_upload_failure(ids, 'received', error, backend)
        if source_path is not None:
            try:
                # 저장 원본이 있으면 재공급 경로는 사용하지 않는다. 기존 파일을 먼저 보존하고
                # run_attempt에서 저장 원본의 해시·크기를 확인한다. 변조된 원본도 덮지 않는다.
                if not source.exists():
                    replacement = Path(source_path).resolve(strict=True)
                    copy_original(replacement, source, retry_info['sha256'], retry_info['file_size'])
            except (FileNotFoundError, PermissionError):
                return record_upload_failure(ids, 'received', DbError('FILE_UNAVAILABLE'), backend)
            except (DbError, OSError) as error:
                return record_upload_failure(ids, 'received', error, backend)
        return run_attempt(actor, ids, retry_info['expected_active_id'], ingest_function, backend)
