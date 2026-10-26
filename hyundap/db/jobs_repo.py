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
    """보고서를 허용 필드만 가진 dict로 바꾼다. 임의 원문·토큰·예외 메시지는 버린다.

    dict 또는 dataclass를 받는다. stage 생략 시 model_name이 있으면 embedding,
    아니면 ingest다. 실패 상세는 안전한 코드·청크 식별자만 저장한다.
    """
    if is_dataclass(report) and not isinstance(report, type):
        report = asdict(report)
    if not isinstance(report, dict):
        raise DbError('INVALID_INPUT')
    stage = report.get('stage', 'embedding' if 'model_name' in report else 'ingest')
    status = report.get('status')
    if stage not in STAGES or status not in ('success', 'partial', 'failed'):
        raise DbError('INVALID_INPUT')
    clean = {'stage': stage, 'status': status}
    for field in ('document_id', 'version_id', 'company_id'):
        if field in report:
            clean[field] = require_id(report[field])
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
        if is_dataclass(failure) and not isinstance(failure, type):
            failure = asdict(failure)
        if not isinstance(failure, dict):
            raise DbError('INVALID_INPUT')
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
        clean['failures'].append(item)
    if status == 'success' and (code is not None or failures):
        raise DbError('INVALID_INPUT')
    return clean


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
