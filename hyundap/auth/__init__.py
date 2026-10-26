"""auth/__init__.py — 로그인과 회사별 접근 권한 패키지.

담당: 전재경
기준 명세: AI_문서검색_통합_기능명세서

연계 모듈: auth.login, auth.permissions
연동 계약은 docs/db-design/DB_contract.md를 따른다. 실제 구현 여부는 각 파일에서 확인한다.

로그인·토큰 인증·로그아웃은 auth.login, 권한 판정은 auth.permissions를 사용한다.
모듈 간 순환 import를 막기 위해 패키지에서는 오류 형식만 공개한다."""

from .errors import AccessError

__all__ = ['AccessError']
