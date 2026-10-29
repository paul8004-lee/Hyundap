"""search/__init__.py — 문서 검색·청크 검색의 공개 기능 패키지.

담당: 이준영
기준 명세: AI_문서검색_통합_기능명세서 (MOD-050)

다른 폴더는 이 세 가지만 쓴다. 안쪽 함수는 바뀔 수 있으므로 직접 부르지 않는다.
    search        — 화면(ui)용 문서 단위 검색
    search_chunks — 챗봇(chatbot)용 청크 단위 검색
    SearchError   — 검색 오류
화면 오류 문구 변환은 hyundap.search.errors.to_feedback을 쓴다. 사용법은 README.md.
"""
from .errors import SearchError
from .search import search, search_chunks

__all__ = ["search", "search_chunks", "SearchError"]
