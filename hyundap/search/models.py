"""search/models.py — 검색 입력·문서 정보·청크 결과·출처의 자료형.

담당: 이준영
기준 명세: AI_문서검색_통합_기능명세서 (MOD-051)

자료형:
    CLS-050 DocumentInfo   — DB에서 읽은 문서 1건 + 활성 버전 정보 (공개 판단용)
    CLS-051 ChunkHit       — 검색된 청크 1개 + 문서 정보 (search_chunks 결과, 챗봇이 사용)
    CLS-052 SearchCitation — 화면용 근거 한 줄 (발췌 200자)
    CLS-053 SearchResult   — 화면 목록의 문서 1개 + 근거 최대 4개
    CLS-054 SearchResponse — search()의 최종 결과

ChunkHit의 필드 이름은 chatbot이 그대로 읽고, 요약에서는 직접 만든다. 이름을 바꾸거나
기본값 없는 필드를 추가하면 chatbot이 깨지므로 바꾸기 전에 소용우와 협의한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class DocumentInfo:
    """DB에서 읽은 문서 1건과 활성 버전 정보 (CLS-050). 공개 조건 확인·ChunkHit 만들기에 쓴다."""

    document_id: str
    company_id: str
    company_name: str
    doc_type: str
    publication_status: str
    deleted_at: Optional[datetime]
    active_version_id: Optional[str]
    version_no: Optional[int]
    version_status: Optional[str]
    data_category: Optional[str]
    file_name: Optional[str]  # 활성 버전이 없으면 None (DB_contract §2, LEFT JOIN)


@dataclass
class ChunkHit:
    """검색된 청크 1개와 그 문서 정보 (CLS-051). search_chunks의 반환 원소."""

    chunk_id: str
    document_id: str
    version_id: str
    version_no: Optional[int]
    company_id: str
    company_name: str
    doc_type: str
    file_name: Optional[str]  # DB에 활성 버전 파일명이 없으면 None
    section_no: Optional[int]
    location_label: str
    chunk_index: Optional[int]
    text: str
    score: Optional[float]  # 챗봇 요약은 점수 없이 ChunkHit을 만든다(None)


@dataclass
class SearchCitation:
    """검색 결과 안의 근거 청크 1개 (CLS-052). 화면 표시용."""

    chunk_id: str
    document_id: str
    version_id: str
    company_name: str
    file_name: Optional[str]
    location_label: str
    excerpt: str
    score: float  # search 경로에서는 filter_by_min_score가 점수 없는 결과를 빼므로 항상 숫자


@dataclass
class SearchResult:
    """검색된 문서 1개와 그 문서의 근거 청크 목록 (CLS-053)."""

    document_id: str
    version_id: str
    version_no: Optional[int]
    company_id: str
    company_name: str
    doc_type: str
    file_name: Optional[str]
    score: float  # 문서 점수 = 가장 높은 청크 점수
    citations: list[SearchCitation] = field(default_factory=list)


@dataclass
class SearchResponse:
    """search()의 반환값 (CLS-054). 결과 목록과 실제로 적용된 회사·유형 조건."""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    total_hits: int = 0
    company_ids: list[str] = field(default_factory=list)
    doc_types: Optional[list[str]] = None
    elapsed_sec: float = 0.0
