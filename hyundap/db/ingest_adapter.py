"""기존·현재 수집 함수의 호출 차이를 DB 작업과 분리하는 어댑터.

파일·청크 저장이나 권한 판단은 하지 않는다. 검증된 공개 메타데이터와 별도 처리 옵션을
받아 선택 인자를 맞추고, 기존 외부 호출 오류 변환 및 dict/dataclass 반환 계약을 적용한다.
"""
import inspect

from .errors import DbError
from . import version_service
from .classification import decision_fields


def call_ingest(function, source, metadata, *, find_by_hash, file_hash=None,
                classify=False, automatic=False, classification=None):
    """선택 인자 지원 여부는 이곳에서만 판별하고 동일 후보의 재사용 결정값을 보존한다."""
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        parameters = {}
    kwargs = {'find_by_hash': find_by_hash}
    if file_hash is not None and 'file_hash' in parameters:
        kwargs['file_hash'] = file_hash
    if classify and 'classify' in parameters:
        kwargs['classify'] = True
    previous = None
    if automatic:
        if not {'apply_classification', 'classification'} <= parameters.keys():
            raise DbError('INTEGRATION_UNAVAILABLE')
        if classification is None and not classify:
            raise DbError('INVALID_STATE')
        if classification is not None:
            previous = decision_fields(classification, required=True)
        kwargs.update(apply_classification=True,
                      classification=None if classification is None else dict(classification))
    result = version_service.as_mapping(
        version_service.call_external(function, str(source), dict(metadata), **kwargs))
    if previous is not None and result.get('status') == 'success':
        report = version_service.as_mapping(result.get('report') or {})
        if decision_fields(report, required=True) != previous:
            raise DbError('CONFLICT')
    return result
