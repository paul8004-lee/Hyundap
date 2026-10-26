"""수집·임베딩 처리 보고서를 비밀정보 없이 단계별로 저장한다."""

import json
import math
import re
from dataclasses import asdict, is_dataclass

from sqlalchemy import text

from .errors import DbError
from .runtime import require_id, transaction


STAGES = ('received', 'ingest', 'embedding', 'verify', 'publish', 'complete')
COUNT_FIELDS = ('total', 'skipped_existing', 'embedded', 'stored', 'truncated_count')


def normalize_report(report):
    """dict/dataclass 보고서에서 저장해도 되는 필드만 새 dict로 반환한다.

    기본 정보 → 처리 수치 → 실패 상세 순서로 검사한다. 입력은 수정하지 않는다.
    원문·토큰·예외 메시지는 복사하지 않으며 잘못된 값은 INVALID_INPUT으로 거부한다.
    """
    report = _report_dict(report)
    clean = _report_identity(report)
    clean.update(_report_metrics(report))
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
    """
    clean = normalize_report(report)
    with transaction(connection) as database:
        job = database.execute(text('SELECT version_id,stage,report FROM index_jobs WHERE id=:id FOR UPDATE'),
                               {'id': require_id(job_id)}).mappings().one_or_none()
        if not job or job['version_id'] is None:
            raise DbError('INVALID_STATE')
        identity = database.execute(text('SELECT document_id,company_id FROM document_versions WHERE id=:id'),
                                    {'id': job['version_id']}).mappings().one()
        expected = dict(identity, version_id=job['version_id'])
        if any(clean.get(key, value) != value for key, value in expected.items()):
            raise DbError('INVALID_INPUT')
        if job['stage'] not in STAGES or STAGES.index(clean['stage']) < STAGES.index(job['stage']):
            raise DbError('CONFLICT')
        reports = json.loads(job['report']) if isinstance(job['report'], str) else (job['report'] or {})
        if not isinstance(reports, dict):
            raise DbError('INVALID_STATE')
        previous = reports.get(clean['stage'])
        if previous and previous.get('status') == 'success' and previous != clean:
            raise DbError('CONFLICT')
        reports[clean['stage']] = clean
        code = clean.get('error_code')
        database.execute(text('''
            UPDATE index_jobs SET stage=:stage,report=:report,error_code=:code,error_message=:message
            WHERE id=:id
        '''), {'stage': clean['stage'], 'report': json.dumps(reports, ensure_ascii=False),
              'code': code, 'message': f'처리 실패: {code}' if code else None, 'id': job_id})
