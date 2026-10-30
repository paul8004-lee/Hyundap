"""자동 유형 적용 계약. 분류 판단은 수집 담당이 수행하고 DB는 결정값을 검증한다."""

import math

from hyundap.common.schemas import DOC_TYPE_CODES
from .errors import DbError


DECISION_FIELDS = ('doc_type', 'doc_type_fallback', 'doc_type_fallback_reason')
CLASSIFICATION_FIELDS = ('auto_doc_type', 'doc_type_confidence', 'company_match', 'classify_status')


def classification_fields(report):
    """저장·반환이 공유하는 네 분류 필드 계약. 미실행은 {}, 실패는 안전한 기본값이다.

    불완전한 과거 보고서도 처리한다. 잘못된 값은 분류 실패로 정규화하고 원문·예외는
    옮기지 않는다. 최종 문서 유형 결정값은 decision_fields가 별도로 검증한다.
    """
    if not any(key in report for key in CLASSIFICATION_FIELDS):
        return {}
    failed = dict(auto_doc_type='', doc_type_confidence=0.0, company_match='', classify_status='failed')
    if report.get('classify_status') != 'success':
        return failed
    values = {key: report.get(key, failed[key]) for key in CLASSIFICATION_FIELDS}
    kind, confidence = values['auto_doc_type'], values['doc_type_confidence']
    valid = (
        isinstance(kind, str) and (kind == '' or kind in DOC_TYPE_CODES)
        and type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1
        and values['company_match'] in ('yes', 'no', '')
        and values['classify_status'] in ('success', 'failed')
    )
    return values if valid else failed


def validate_auto_request(metadata):
    """자동 구분값은 정확한 True만 허용하고 수동 유형과 동시에 받지 않는다."""
    if 'auto_classify' not in metadata:
        return False
    if metadata['auto_classify'] is not True or 'doc_type' in metadata:
        raise DbError('INVALID_INPUT')
    return True


def decision_fields(report, *, required=False):
    """세 결정 필드를 함께 검사한다. 잘못된 결정값은 조용히 버리지 않는다."""
    present = any(key in report for key in DECISION_FIELDS)
    if not present and not required:
        return {}
    if not all(key in report for key in DECISION_FIELDS):
        raise DbError('INVALID_INPUT')
    kind = report['doc_type']
    fallback = report['doc_type_fallback']
    reason = report['doc_type_fallback_reason']
    if not isinstance(kind, str) or kind not in DOC_TYPE_CODES or type(fallback) is not bool:
        raise DbError('INVALID_INPUT')
    if (fallback and reason not in ('LOW_CONFIDENCE', 'CLASSIFY_FAILED')) or (not fallback and reason is not None):
        raise DbError('INVALID_INPUT')
    return {key: report[key] for key in DECISION_FIELDS}


def reusable_classification(reports):
    """같은 후보의 최근 성공 수집에서 결정한 유형과 분류 결과를 함께 재사용한다."""
    attempts = [reports] + list(reversed(reports.get('_attempts', [])))
    for attempt in attempts:
        ingest = attempt.get('ingest', {})
        if ingest.get('status') != 'success' or ingest.get('classify_status') != 'success':
            continue
        decision = decision_fields(ingest)
        if decision:
            return dict(decision, **{key: ingest[key] for key in CLASSIFICATION_FIELDS if key in ingest})
    return None
