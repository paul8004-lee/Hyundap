"""chatbot/__init__.py — 질의응답·요약의 공개 기능 패키지.

담당: 소용우
기준 명세: AI_문서검색_통합_기능명세서 (MOD-120)

다른 폴더(ui 등)에서는 ask·summarize_document·to_feedback만 가져다 쓴다.
결과 클래스(ChatResponse 등)가 필요하면 chatbot.models에서 가져온다.
업로드 자동 분류는 ingest가 chatbot.doc_classifier의 classify_common_document·decide_doc_type을 직접 부른다.
"""
from .ask import ask
from .models import to_feedback
from .summary import summarize_document

__all__ = ['ask', 'summarize_document', 'to_feedback']
