"""수집·임베딩 처리 보고서를 비밀정보 없이 단계별로 저장한다."""

import json
import math
import re
from dataclasses import asdict, is_dataclass

from sqlalchemy import text

from hyundap.common.schemas import DOC_TYPE_CODES

from .errors import DbError
from .runtime import require_id, transaction
from .clock import epoch_now


STAGES = ('received', 'ingest', 'embedding', 'verify', 'publish', 'complete')
COUNT_FIELDS = ('total', 'skipped_existing', 'embedded', 'stored', 'truncated_count')
INGEST_COUNT_FIELDS = ('chunk_size', 'chunk_overlap', 'section_count', 'chunk_count', 'warning_count', 'short_chunk_count')
# 수집(정규화 직후) 문서 자동 분류 결과(chatbot.doc_classifier). 값 목록은 분류기와 같다.
CLASSIFY_FIELDS = ('auto_doc_type', 'doc_type_confidence', 'company_match', 'classify_status')


def normalize_report(report):
    """dict/dataclass 보고서에서 저장해도 되는 필드만 새 dict로 반환한다.

    기본 정보 → 처리 수치 → 실패 상세 순서로 검사한다. 입력은 수정하지 않는다.
    원문·토큰·예외 메시지는 복사하지 않으며 잘못된 값은 INVALID_INPUT으로 거부한다.
    """
    # 원본 보고서를 바꾸지 않고 저장이 허용된 값만 새 사전에 모은다.
    report = _report_dict(report)
    clean = _report_identity(report)
    clean.update(_report_metrics(report))
    clean.update(_ingest_metrics(report, clean['stage']))
    clean.update(_classify_fields(report, clean['stage']))
    clean.update(_report_failures(report))

    # 성공 보고서에 실패 내용이 함께 있으면 서로 모순되므로 저장하지 않는다.
    if clean['status'] == 'success':
        if 'error_code' in clean or clean['failures']:
            raise DbError('INVALID_INPUT')
    return clean


def _report_dict(report):
    """dict 또는 dataclass 입력을 dict로 반환하고 나머지 자료형은 거부한다."""
    # dataclass는 사전으로 변환하고 나머지는 사전인지 확인한다.
    if is_dataclass(report) and not isinstance(report, type):
        report = asdict(report)
    if not isinstance(report, dict):
        raise DbError('INVALID_INPUT')
    return report


def _report_identity(report):
    """보고서 dict에서 단계·상태·문서 식별자를 검사해 새 dict로 반환한다."""
    # 단계와 상태를 먼저 확인한 뒤 포함된 식별자를 검증한다.
    # 단계가 없으면 기본값을 사용하며 모델명이 있으면 embedding으로 본다.
    default_stage = 'ingest'
    if 'model_name' in report:
        default_stage = 'embedding'
    stage = report.get('stage', default_stage)
    status = report.get('status')
    if stage not in STAGES or status not in ('success', 'partial', 'failed'):
        raise DbError('INVALID_INPUT')
    clean = {'stage': stage, 'status': status}
    # 문서·버전·회사 ID를 하나씩 살펴본다. 없는 필드는 생략하고 있는 ID만 검사한다.
    # 예: document_id만 들어오면 그 값만 clean에 추가한다.
    for field in ('document_id', 'version_id', 'company_id'):
        if field in report:
            clean[field] = require_id(report[field])
    return clean


def _report_metrics(report):
    """보고서 dict에서 개수·모델명·소요 시간을 검사해 새 dict로 반환한다."""
    # 개수, 모델명, 시간은 각 필드에 허용된 형식과 범위를 검사한다.
    clean = {}
    # total, embedded 등 개수 필드를 순회한다. 보고서에 있는 필드만 확인한다.
    # 이번 반복의 value는 현재 field의 값이며, 앞선 ID 반복과 별개다.
    for field in COUNT_FIELDS:
        if field in report:
            value = report[field]
            # bool을 개수로 받지 않도록 정확한 int 타입과 음수 여부를 확인한다.
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
    # 수집 전용 필드가 다른 단계에 섞이지 않도록 먼저 검사한다.
    fields = ('parser', 'parser_version') + INGEST_COUNT_FIELDS
    if stage != 'ingest':
        # 수집 단계가 아니라면 수집 전용 필드가 하나라도 포함되는 즉시 거부한다.
        for field in fields:
            if field in report:
                raise DbError('INVALID_INPUT')
        # 수집 전용 값이 없는 다른 단계라면 이후 수집 필드 검사는 필요 없다.
        return {}
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
    # chunk_size, chunk_count 등 수집 단계 전용 숫자를 하나씩 검사한다.
    # 현재 필드가 chunk_size일 때만 최소값 1을 적용하고 나머지는 0을 허용한다.
    for field in INGEST_COUNT_FIELDS:
        if field in report:
            value = report[field]
            # chunk_size는 최소 1이고 다른 수집 개수는 0부터 허용한다.
            minimum = 0
            if field == 'chunk_size':
                minimum = 1
            if type(value) is not int or value < minimum:
                raise DbError('INVALID_INPUT')
            clean[field] = value
    # 두 값이 있으면 겹침 길이는 청크 크기보다 작아야 한다.
    if 'chunk_size' in clean and 'chunk_overlap' in clean:
        if clean['chunk_overlap'] >= clean['chunk_size']:
            raise DbError('INVALID_INPUT')
    if 'short_chunk_count' in clean and 'chunk_count' in clean:
        if clean['short_chunk_count'] > clean['chunk_count']:
            raise DbError('INVALID_INPUT')
    return clean


def _classify_fields(report, stage):
    """수집 단계의 문서 분류 결과(유형·확신도·회사 일치·분류 상태)를 검사해 새 dict로 반환한다.

    분류는 실패해도 업로드는 성공시키는 부가 기능이므로, 값 하나라도 허용 범위 밖이면 수집 기록 전체를
    거부하지 않고 분류 값을 모두 빼고 classify_status='failed'만 남긴다. 분류 실패 원인 문구는 저장하지 않는다.
    수집이 아닌 단계에 분류 값이 섞이면 호출 쪽 계약 오류이므로 INVALID_INPUT이다.
    """
    if not any(field in report for field in CLASSIFY_FIELDS):
        return {}
    if stage != 'ingest':
        raise DbError('INVALID_INPUT')
    clean = {field: report[field] for field in CLASSIFY_FIELDS if field in report}
    auto_doc_type = clean.get('auto_doc_type', '')
    confidence = clean.get('doc_type_confidence', 0.0)
    valid = (
        isinstance(auto_doc_type, str) and (auto_doc_type == '' or auto_doc_type in DOC_TYPE_CODES)
        # bool은 숫자로 받지 않고 0~1 사이의 유한한 값만 허용한다.
        and type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1
        and clean.get('company_match', '') in ('yes', 'no', '')
        and clean.get('classify_status') in ('success', 'failed')
    )
    return clean if valid else {'classify_status': 'failed'}


def _report_failures(report):
    """보고서 dict에서 안전한 오류 코드와 실패 목록만 새 dict로 반환한다."""
    # 오류 코드와 실패 항목을 검증하고 안전한 정보만 남긴다.
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
    # 실패 목록의 각 항목을 _failure_detail에 넘긴다.
    # 원본 항목 전체가 아니라 검사된 코드·청크 ID·재시도 여부만 저장한다.
    for failure in failures:
        clean['failures'].append(_failure_detail(failure))
    return clean


def _failure_detail(failure):
    """실패 항목 하나에서 코드·청크 ID·재시도 여부만 새 dict로 반환한다."""
    # 실패 항목에서는 허용한 코드, 청크 ID, 재시도 여부만 가져온다.
    failure = _report_dict(failure)
    # code 키가 있으면 그 값을 우선한다. None/빈 값도 오류이므로 다른 코드로 대체하지 않는다.
    # code 키 자체가 없을 때만 수집 모듈이 사용하는 error_code를 읽는다.
    if 'code' in failure:
        failure_code = failure['code']
    else:
        failure_code = failure.get('error_code')
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
    with transaction(connection, job_id=job_id) as database:
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
                           {'id': job_id}).mappings().one_or_none()
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
    actual = 0
    # 조회한 청크 행을 한 번씩 세므로 실제 원장 개수를 확인한다.
    for _ in rows:
        actual += 1
    if count <= 0 or count != version['expected_chunk_count'] or count != actual:
        raise DbError('INVALID_INPUT')


def _check_stage_result(job, identity, clean):
    """정규화한 보고서가 이 작업의 대상과 단계에 맞는지 검사한다. 반환값은 없다.

    식별자 생략은 허용한다. 값을 전달했다면 DB와 같아야 하며, 이미 진행한 단계보다
    앞선 단계의 결과는 늦게 도착한 기록이므로 거부한다. DB를 변경하지 않는다.
    """
    # 보고서의 식별자와 단계가 현재 작업과 일치하는지 확인한다.
    # identity의 각 ID 이름과 DB에서 읽은 값을 한 쌍씩 확인한다.
    # 보고서에 해당 ID가 없으면 건너뛰고, 있으면 DB 값과 같은지 비교한다.
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
    # 이미 성공한 단계의 내용이 달라지는 재기록은 충돌로 처리한다.
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
    # 안전한 오류 코드와 단계 상태를 작업 저장 형식으로 바꾼다.
    code = clean.get('error_code')
    message = None
    if code:
        message = f'처리 실패: {code}'
    status = None
    if clean['status'] in ('failed', 'partial'):
        status = 'FAILED'
    elif clean['stage'] == 'complete':
        # 정규화된 상태에서 failed/partial을 제외하면 success만 남는다.
        status = 'SUCCEEDED'
    store_job_result(database, job_id, clean['stage'], reports, status=status,
                     error_code=code, error_message=message)


def store_job_result(database, job_id, stage, report, *, status=None,
                     error_code=None, error_message=None):
    """검증된 단계·보고서·종료 상태를 한 UPDATE로 저장한다. 호출자 TX에 참여한다.

    내부 서비스 전용 보조 함수이며 상태 전이 권한은 호출자가 검사한다.
    status 생략은 현재 상태·종료 시간을 보존한다. 직접 커밋하지 않는다.
    """
    # status가 생략되면 SQL의 COALESCE가 기존 작업 상태를 보존한다.
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
    # 기존 시도 횟수를 한 번 올리고 종료 상태와 오류 정보를 비운다.
    database.execute(text('''
        UPDATE index_jobs SET status='QUEUED',stage='received',attempts=attempts+1,
            report=:report,error_code=NULL,error_message=NULL,started_at=NULL,
            finished_at=NULL,updated_at=:now WHERE id=:id
    '''), {'id': job_id, 'report': json.dumps(report, ensure_ascii=False), 'now': epoch_now()})
