"""수집·임베딩 처리 보고서를 비밀정보 없이 단계별로 저장한다."""

import json
import math
import re
from dataclasses import asdict, is_dataclass

from sqlalchemy import text

from .errors import DbError
from .runtime import require_id, transaction
from .clock import epoch_now


STAGES = ('received', 'ingest', 'embedding', 'verify', 'publish', 'complete')
COUNT_FIELDS = ('total', 'skipped_existing', 'embedded', 'stored', 'truncated_count')
INGEST_COUNT_FIELDS = ('chunk_size', 'chunk_overlap', 'section_count', 'chunk_count', 'warning_count', 'short_chunk_count')


def normalize_report(report):
    """dict/dataclass 보고서에서 저장해도 되는 필드만 새 dict로 반환한다.

    기본 정보 → 처리 수치 → 실패 상세 순서로 검사한다. 입력은 수정하지 않는다.
    원문·토큰·예외 메시지는 복사하지 않으며 잘못된 값은 INVALID_INPUT으로 거부한다.
    """
    report = _report_dict(report)
    clean = _report_identity(report)
    clean.update(_report_metrics(report))
    clean.update(_ingest_metrics(report, clean['stage']))
    clean.update(_report_failures(report))

    # 성공 보고서에 실패 내용이 함께 있으면 서로 모순되므로 저장하지 않는다.
    if clean['status'] == 'success':
        if 'error_code' in clean or clean['failures']:
            raise DbError('INVALID_INPUT')
    return clean


def _report_dict(report):
    """dict 또는 dataclass 입력을 dict로 반환하고 나머지 자료형은 거부한다."""
    if is_dataclass(report) and not isinstance(report, type):
        report = asdict(report)
    if not isinstance(report, dict):
        raise DbError('INVALID_INPUT')
    return report


def _report_identity(report):
    """보고서 dict에서 단계·상태·문서 식별자를 검사해 새 dict로 반환한다."""
    default_stage = 'ingest'
    if 'model_name' in report:
        default_stage = 'embedding'
    stage = report.get('stage', default_stage)
    status = report.get('status')
    if stage not in STAGES or status not in ('success', 'partial', 'failed'):
        raise DbError('INVALID_INPUT')
    clean = {'stage': stage, 'status': status}
    for field in ('document_id', 'version_id', 'company_id'):
        if field in report:
            clean[field] = require_id(report[field])
    return clean


def _report_metrics(report):
    """보고서 dict에서 개수·모델명·소요 시간을 검사해 새 dict로 반환한다."""
    clean = {}
    for field in COUNT_FIELDS:
        if field in report:
            value = report[field]
            if type(value) is not int or value < 0:
                raise DbError('INVALID_INPUT')
            clean[field] = value

    model = report.get('model_name')
    if model is not None:
        if not isinstance(model, str) or not re.fullmatch(r'[a-zA-Z0-9_./:-]{1,128}', model):
            raise DbError('INVALID_INPUT')
        clean['model_name'] = model
    elapsed = report.get('elapsed_sec')
    if elapsed is not None:
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            raise DbError('INVALID_INPUT')
        clean['elapsed_sec'] = elapsed
    return clean


def _ingest_metrics(report, stage):
    """수집 전용 필드의 타입·범위를 검사한다. 경고 원문은 읽거나 저장하지 않는다."""
    fields = ('parser', 'parser_version') + INGEST_COUNT_FIELDS
    if any(field in report for field in fields) and stage != 'ingest':
        raise DbError('INVALID_INPUT')
    clean = {}
    if 'parser' in report:
        parser = report['parser']
        if not isinstance(parser, str) or parser not in ('pypdf', 'python-docx', 'python-pptx'):
            raise DbError('INVALID_INPUT')
        clean['parser'] = parser
    if 'parser_version' in report:
        value = report['parser_version']
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}', value):
            raise DbError('INVALID_INPUT')
        clean['parser_version'] = value
    for field in INGEST_COUNT_FIELDS:
        if field in report:
            value = report[field]
            if type(value) is not int or value < (1 if field == 'chunk_size' else 0):
                raise DbError('INVALID_INPUT')
            clean[field] = value
    if 'chunk_size' in clean and 'chunk_overlap' in clean:
        if clean['chunk_overlap'] >= clean['chunk_size']:
            raise DbError('INVALID_INPUT')
    if 'short_chunk_count' in clean and 'chunk_count' in clean:
        if clean['short_chunk_count'] > clean['chunk_count']:
            raise DbError('INVALID_INPUT')
    return clean


def _report_failures(report):
    """보고서 dict에서 안전한 오류 코드와 실패 목록만 새 dict로 반환한다."""
    clean = {}
    code = report.get('error_code')
    if code is not None:
        if not isinstance(code, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code):
            raise DbError('INVALID_INPUT')
        clean['error_code'] = code
    failures = report.get('failures', [])
    if not isinstance(failures, list):
        raise DbError('INVALID_INPUT')
    clean['failure_count'] = len(failures)
    clean['failures'] = []
    for failure in failures:
        clean['failures'].append(_failure_detail(failure))
    return clean


def _failure_detail(failure):
    """실패 항목 하나에서 코드·청크 ID·재시도 여부만 새 dict로 반환한다."""
    failure = _report_dict(failure)
    failure_code = failure.get('code', failure.get('error_code'))
    if not isinstance(failure_code, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', failure_code):
        raise DbError('INVALID_INPUT')
    item = {'code': failure_code}
    chunk_id = failure.get('chunk_id')
    if chunk_id is not None:
        if not isinstance(chunk_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,36}:[0-9]{5}', chunk_id):
            raise DbError('INVALID_INPUT')
        item['chunk_id'] = chunk_id
    if 'retryable' in failure:
        if type(failure['retryable']) is not bool:
            raise DbError('INVALID_INPUT')
        item['retryable'] = failure['retryable']
    return item


def record_stage_result(job_id, report, *, connection=None):
    """작업의 단계별 결과를 같은 TX에서 기록한다. 이전 단계의 지연 기록은 거부한다.

    앞 단계 보고서를 보존하고 이미 성공한 단계는 동일 결과만 재호출할 수 있다.
    실패·부분 성공은 같은 단계의 재시도 결과로 갱신 가능하다. 작업자는 P0에서 하나다.
    수집 성공의 명시적 chunk_count는 processing 버전의 양수 예상·실제 수와 같아야 한다.
    연결 제공 시 호출자 TX에 참여하며 직접 커밋하지 않는다.
    """
    clean = normalize_report(report)
    with transaction(connection) as database:
        # 조회 → 입력과 작업의 일치 검사 → 보고서 갱신 → 저장을 같은 잠금 안에서 처리한다.
        job, identity = _read_stage_target(database, job_id)
        _check_stage_result(job, identity, clean)
        if clean['stage'] == 'ingest' and clean['status'] == 'success' and 'chunk_count' in clean:
            _check_ingest_count(database, job['version_id'], clean['chunk_count'])
        reports = _merge_stage_reports(job['report'], clean)
        _store_stage_result(database, job_id, clean, reports)


def _read_stage_target(database, job_id):
    """작업 ID로 저장 대상을 조회하고 (작업 행, 문서·버전·회사 ID dict)를 반환한다.

    버전 → 작업 순서로 잠가 manifest·게시·복구와 잠금 순서를 맞춘다.
    전달받은 연결만 사용하며 커밋과 잠금 해제는 트랜잭션을 연 호출자가 담당한다.
    """
    reference = database.execute(text('SELECT version_id FROM index_jobs WHERE id=:id'),
                                 {'id': require_id(job_id)}).scalar_one_or_none()
    if reference is None:
        raise DbError('INVALID_STATE')
    # manifest·게시·복구와 동일하게 버전 → 작업 순서로 잠근다.
    version = database.execute(text('SELECT document_id,company_id FROM document_versions WHERE id=:id FOR UPDATE'),
                               {'id': reference}).mappings().one_or_none()
    if version is None:
        raise DbError('INVALID_STATE')
    job = database.execute(text('SELECT version_id,stage,report FROM index_jobs WHERE id=:id FOR UPDATE'),
                           {'id': require_id(job_id)}).mappings().one_or_none()
    if not job or job['version_id'] != reference:
        raise DbError('INVALID_STATE')
    identity = dict(version, version_id=job['version_id'])
    return job, identity


def _check_ingest_count(database, version_id, count):
    """잠근 버전의 예상·실제 청크 수와 보고서를 비교한다. 데이터는 변경하지 않는다."""
    version = database.execute(text('SELECT version_status,expected_chunk_count FROM document_versions WHERE id=:id FOR UPDATE'),
                               {'id': version_id}).mappings().one()
    if version['version_status'] != 'processing':
        raise DbError('INVALID_STATE')
    # REPEATABLE READ에서도 선행 참조 조회의 과거 snapshot 대신 현재 목록을 확인한다.
    rows = database.execute(text('SELECT id FROM document_chunks WHERE version_id=:id FOR UPDATE'),
                            {'id': version_id}).scalars()
    actual = sum(1 for _ in rows)
    if count <= 0 or count != version['expected_chunk_count'] or count != actual:
        raise DbError('INVALID_INPUT')


def _check_stage_result(job, identity, clean):
    """정규화한 보고서가 이 작업의 대상과 단계에 맞는지 검사한다. 반환값은 없다.

    식별자 생략은 허용한다. 값을 전달했다면 DB와 같아야 하며, 이미 진행한 단계보다
    앞선 단계의 결과는 늦게 도착한 기록이므로 거부한다. DB를 변경하지 않는다.
    """
    for key, expected_value in identity.items():
        if key in clean and clean[key] != expected_value:
            raise DbError('INVALID_INPUT')
    if job['stage'] not in STAGES:
        raise DbError('CONFLICT')
    incoming_stage = STAGES.index(clean['stage'])
    saved_stage = STAGES.index(job['stage'])
    if incoming_stage < saved_stage:
        raise DbError('CONFLICT')


def _merge_stage_reports(stored_report, clean):
    """저장된 JSON 또는 dict에 새 단계 결과를 반영해 보고서 dict를 반환한다.

    다른 단계 기록은 보존한다. 이미 성공한 단계는 같은 결과만 다시 받을 수 있다.
    이 함수는 메모리의 보고서만 갱신하며 DB 저장은 하지 않는다.
    """
    # DB에서 읽은 JSON 문자열은 파싱하고, dict는 원본을 바꾸지 않도록 복사한다.
    if isinstance(stored_report, str):
        reports = json.loads(stored_report)
    elif stored_report:
        reports = dict(stored_report)
    else:
        reports = {}
    if not isinstance(reports, dict):
        raise DbError('INVALID_STATE')
    previous_stage_report = reports.get(clean['stage'])
    if (previous_stage_report and previous_stage_report.get('status') == 'success'
            and previous_stage_report != clean):
        raise DbError('CONFLICT')
    reports[clean['stage']] = clean
    return reports


def _store_stage_result(database, job_id, clean, reports):
    """검사가 끝난 보고서 dict와 현재 단계·안전한 오류 문구를 DB에 저장한다. 반환값은 없다.

    호출자가 넘긴 같은 트랜잭션에서만 갱신한다. 예외 원문은 오류 문구에 넣지 않는다.
    """
    code = clean.get('error_code')
    message = None
    if code:
        message = f'처리 실패: {code}'
    status = None
    if clean['status'] in ('failed', 'partial'):
        status = 'FAILED'
    elif clean['stage'] == 'complete' and clean['status'] == 'success':
        status = 'SUCCEEDED'
    store_job_result(database, job_id, clean['stage'], reports, status=status,
                     error_code=code, error_message=message)


def store_job_result(database, job_id, stage, report, *, status=None,
                     error_code=None, error_message=None):
    """검증된 단계·보고서·종료 상태를 한 UPDATE로 저장한다. 호출자 TX에 참여한다.

    내부 서비스 전용 보조 함수이며 상태 전이 권한은 호출자가 검사한다.
    status 생략은 현재 상태·종료 시간을 보존한다. 직접 커밋하지 않는다.
    """
    if status not in (None, 'QUEUED', 'SUCCEEDED', 'FAILED', 'CANCELLED') or stage not in STAGES:
        raise DbError('INVALID_INPUT')
    database.execute(text('''
        UPDATE index_jobs SET stage=:stage,report=:report,error_code=:code,error_message=:message,
            status=COALESCE(:status,status),updated_at=:now,
            finished_at=CASE WHEN :status='QUEUED' THEN NULL
                WHEN :status IS NOT NULL THEN :now ELSE finished_at END
        WHERE id=:id
    '''), {'id': job_id, 'stage': stage, 'report': json.dumps(report, ensure_ascii=False),
          'status': status, 'now': epoch_now(), 'code': error_code, 'message': error_message})


def reset_job_for_retry(database, job_id, report):
    """검증된 작업을 같은 TX에서 재시도 상태로 저장한다. 반환값·직접 커밋은 없다.

    다른 활성 작업과 UNIQUE 충돌하면 버전 변경까지 호출자의 TX에서 취소된다.
    시도 이력은 호출자가 만든 report에 보존하고 attempts만 한 번 증가시킨다.
    """
    database.execute(text('''
        UPDATE index_jobs SET status='QUEUED',stage='received',attempts=attempts+1,
            report=:report,error_code=NULL,error_message=NULL,started_at=NULL,
            finished_at=NULL,updated_at=:now WHERE id=:id
    '''), {'id': job_id, 'report': json.dumps(report, ensure_ascii=False), 'now': epoch_now()})
