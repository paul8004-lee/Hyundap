"""db/__init__.py — 문서·버전·작업 기록을 저장하는 DB 패키지.

담당: 전재경
기준 명세: AI_문서검색_통합_기능명세서

연계 모듈: db.documents_repo
연동 계약은 docs/db-design/DB_contract.md를 따른다. 실제 구현 여부는 각 파일에서 확인한다.

조회·저장은 각 *_repo 모듈에서 제공하며 패키지에서는 공통 오류 형식만 공개한다.
업로드·벡터 호출·버전 게시·복구는 upload_service와 version_service에서 제공한다."""

from .errors import DbError

__all__ = ['DbError']
