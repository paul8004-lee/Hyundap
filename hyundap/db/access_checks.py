"""이미 DB에서 재검증한 사용자에 적용하는 순수 관리 권한 정책. SQL·잠금은 호출자 책임이다."""
from hyundap.auth.errors import AccessError


def require_company_manager(current, company_id):
    """관리자 역할과 대상 회사 일치를 검사한다. 입력 actor를 직접 받지 않는다."""
    if current['role'] not in ('platform_admin', 'company_admin'):
        raise AccessError('ACCESS_DENIED')
    if current['role'] != 'platform_admin' and current['company_id'] != company_id:
        raise AccessError('ACCESS_DENIED')
