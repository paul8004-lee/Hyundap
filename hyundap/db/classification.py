"""자동 유형 적용 계약. 분류 판단은 수집 담당이 수행하고 DB는 결정값을 검증한다."""

from hyundap.common.schemas import DOC_TYPE_CODES
from .errors import DbError


DECISION_FIELDS = ('doc_type', 'doc_type_fallback', 'doc_type_fallback_reason')
CLASSIFICATION_FIELDS = ('auto_doc_type', 'doc_type_confidence', 'company_match', 'classify_status')


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
