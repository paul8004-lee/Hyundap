"""search/keyword.py — 키워드(BM25) 점수와 순위 합치기(RRF).

담당: 이준영
기준: 하이브리드 검색 (명세 외, 피드백 "검색이 벡터 단독" 보완)

함수:
    tokenize — 글을 검색용 낱말 조각으로 나눈다 (영문·숫자는 통째로, 한글은 두 글자씩).
    bm25_scores — 질문 낱말과 청크 낱말로 BM25 점수를 계산한다.
    rank_by_keyword — 청크 후보를 BM25 점수순으로 줄 세운다 (겹치는 낱말이 없는 청크는 뺀다).
    rrf_merge — 여러 순위 목록을 순위만으로 합친다 (RRF).

외부(OpenAI·Chroma·DB)를 부르지 않는 순수 함수만 둔다. 후보를 가져오고 공개 조건을 확인하는 일은 search.py가 한다.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import replace

# BM25 기본값 (Robertson). K1: 같은 낱말이 여러 번 나올 때 점수가 늘어나는 정도, B: 긴 청크를 깎는 정도
BM25_K1 = 1.5
BM25_B = 0.75
# RRF 상수. 1위와 2위의 점수 차이가 너무 크지 않게 하는 관례값
RRF_K = 60

# 영문·숫자 낱말(하이픈으로 이은 코드 포함) 또는 한글 낱말
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*|[가-힣]+")

# 청크 ID → 낱말 조각. 같은 chunk_id는 같은 버전의 같은 본문이라(vector_store가 충돌을 막는다) 다시 나누지 않는다
_token_cache: dict[str, list[str]] = {}
_TOKEN_CACHE_MAX = 50000


def tokenize(text: str) -> list[str]:
    """글을 검색용 낱말 조각으로 나눈다.

    영문·숫자는 소문자로 통째로 쓰고, 하이픈 코드(WBST-500)는 통째와 조각(wbst, 500)을 함께 넣는다.
    한글은 형태소 분석 없이 두 글자씩 겹쳐 나눠 조사가 붙어도 겹치게 한다(승강기를 → 승강, 강기, 기를).
    한 글자 한글 낱말(및, 등, 조사만 남은 것)은 뺀다."""
    tokens: list[str] = []
    for word in TOKEN_PATTERN.findall(text or ""):
        if "가" <= word[0] <= "힣":
            tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
        else:
            word = word.lower()
            tokens.append(word)
            if "-" in word:
                tokens.extend(part for part in word.split("-") if part)
    return tokens


def _chunk_tokens(hit) -> list[str]:
    """청크 1개의 낱말 조각. 한 번 나눈 청크는 chunk_id로 기억해 둔다."""
    key = hit.chunk_id
    if key not in _token_cache:
        if len(_token_cache) >= _TOKEN_CACHE_MAX:
            _token_cache.clear()
        _token_cache[key] = tokenize(hit.text)
    return _token_cache[key]


def bm25_scores(query_tokens: list[str], documents: list[list[str]]) -> list[float]:
    """질문 낱말과 청크마다의 낱말 목록으로 BM25 점수를 계산한다. documents와 같은 순서로 돌려준다.

    드문 낱말(idf가 큼)일수록, 청크 안에 많이 나올수록 점수가 높고, 긴 청크는 조금 깎는다."""
    count = len(documents)
    terms = set(query_tokens)
    if not count or not terms:
        return [0.0] * count
    average_length = sum(len(doc) for doc in documents) / count or 1.0
    document_frequency = Counter(term for doc in documents for term in set(doc) & terms)

    scores = []
    for doc in documents:
        frequency = Counter(term for term in doc if term in terms)
        norm = BM25_K1 * (1 - BM25_B + BM25_B * len(doc) / average_length)
        score = 0.0
        for term, tf in frequency.items():
            df = document_frequency[term]
            idf = math.log(1 + (count - df + 0.5) / (df + 0.5))
            score += idf * tf * (BM25_K1 + 1) / (tf + norm)
        scores.append(score)
    return scores


def rank_by_keyword(query: str, candidates: list, limit: int) -> list:
    """청크 후보(VectorHit)를 BM25 점수순으로 limit개까지 돌려준다. score에는 BM25 점수를 넣는다.

    질문과 겹치는 낱말이 하나도 없는 청크(점수 0)는 빼고, 같은 점수는 들어온 순서를 유지한다."""
    scores = bm25_scores(tokenize(query), [_chunk_tokens(hit) for hit in candidates])
    ranked = sorted(
        (replace(hit, score=score) for hit, score in zip(candidates, scores) if score > 0),
        key=lambda hit: hit.score,
        reverse=True,
    )
    return ranked[:limit]


def rrf_merge(ranked_lists: list[list], k: int = RRF_K) -> list:
    """여러 순위 목록을 순위만으로 합친다 (Reciprocal Rank Fusion).

    청크마다 목록별 1/(k+순위)를 더하고, 모든 목록에서 1위일 때 1.0이 되도록 나눠 0~1로 맞춘다.
    BM25 점수와 벡터 유사도는 기준이 달라 그대로 더할 수 없으므로 순위만 쓴다.
    같은 청크는 처음 나온 목록의 객체를 쓰고, score를 합친 점수로 바꿔 높은 순으로 돌려준다."""
    if not ranked_lists:
        return []
    fused: dict[str, float] = {}
    first_seen: dict[str, object] = {}
    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked, start=1):
            fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + 1 / (k + rank)
            first_seen.setdefault(hit.chunk_id, hit)
    best = len(ranked_lists) / (k + 1)
    merged = [replace(first_seen[chunk_id], score=score / best) for chunk_id, score in fused.items()]
    return sorted(merged, key=lambda hit: hit.score, reverse=True)  # 안정 정렬: 같은 점수는 먼저 나온 순서
