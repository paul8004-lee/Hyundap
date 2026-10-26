"""auth/__init__.py — 회사별 접근 권한 패키지.

담당: 전재경
기준 명세: AI_문서검색_통합_기능명세서

연계 모듈: auth.permissions
연동 계약은 docs/db-design/DB_contract.md를 따른다. 실제 구현 여부는 각 파일에서 확인한다.

로그인·비밀번호 처리·세션 발급과 폐기는 별도 인증 담당자의 범위다.
이 패키지는 전달받은 서버 세션을 바탕으로 회사·문서 접근 권한만 판정한다.
모듈 간 순환 import를 막기 위해 패키지에서는 오류 형식만 공개한다."""

from .errors import AccessError

__all__ = ['AccessError']
