"""search/quality/metrics.py — 검색 순위 평가 지표. 외부 호출 없이 계산만 한다 (Mock 시험 가능).

담당: 이준영 (이슈 #237)

함수:
    doc_key — 파일명에서 문서 코드(HRI-SYN-MAN-001)를 뽑는다.
    first_rank — 정답 문서가 처음 나온 순위(1부터). 없으면 None.
    hit_at_k / reciprocal_rank — 상위 k 적중 여부 / 순위의 역수.
    recall_at_k — 관련 문서 정답 집합이 있을 때만 계산한다. 집합이 없으면 None.
    find_leaks — 질문한 회사가 아닌 회사의 문서가 결과에 든 경우를 찾는다.
    evaluate_case — 질문 1건의 결과를 채점한다.
    summarize — 질문 유형별 지표와 표본 수를 모은다 (표본이 적으면 표시).
    regression_verdict — 회귀 기준(제안값)으로 판정한다.
"""
from __future__ import annotations

import re
from typing import Optional

# 문서 코드: 영문 2~3덩어리 + 숫자 3자리 (HRI-SYN-MAN-001, 공개 자료는 MVX-PUB-026). 파일명 앞부분에서 찾는다
DOC_KEY_PATTERN = re.compile(r"[A-Za-z]{2,}-[A-Za-z]{2,}(?:-[A-Za-z]{2,})?-\d{3}")

# 순위로 채점하는 질문 유형. 나머지(cross_company, no_evidence)는 노출·거부 확인용이다
RANKED_TYPES = ("code", "keyword", "natural", "synonym", "exclusion")
OTHER_TYPES = ("cross_company", "no_evidence")
ALL_TYPES = RANKED_TYPES + OTHER_TYPES

RECALL_KS = (3, 5, 10)

# 회귀 기준 (제안값, 팀 합의 전). 합의되면 이 숫자만 바꾼다
MIN_SAMPLES = 10   # 유형별 표본이 이보다 적으면 판정하지 않고 '표본 부족'으로 표시
HIT3_MIN = 0.9     # 유형별 Hit@3 최소값
LEAKS_MAX = 0      # 타사 문서 노출 허용 건수
UNKNOWN = "(알 수 없음)"


def doc_key(file_name) -> Optional[str]:
    """파일명에서 문서 코드를 대문자로 뽑는다. 없으면 None."""
    match = DOC_KEY_PATTERN.search(str(file_name or ""))
    return match.group(0).upper() if match else None


def first_rank(ranked: list[str], gold: list[str]) -> Optional[int]:
    """정답 문서 중 하나가 처음 나온 순위(1부터). 정답이 하나도 없으면 None."""
    gold_set = {g.upper() for g in gold}
    for index, key in enumerate(ranked, start=1):
        if key in gold_set:
            return index
    return None


def hit_at_k(rank: Optional[int], k: int) -> bool:
    return rank is not None and rank <= k


def reciprocal_rank(rank: Optional[int]) -> float:
    return 1.0 / rank if rank else 0.0


def recall_at_k(ranked: list[str], relevant: Optional[list[str]], k: int) -> Optional[float]:
    """상위 k개 안에 든 관련 문서 비율. 관련 문서 정답 집합이 없으면 None (계산하지 않는다)."""
    if not relevant:
        return None
    relevant_set = {r.upper() for r in relevant}
    found = relevant_set & set(ranked[:k])
    return len(found) / len(relevant_set)


def find_leaks(ranked: list[str], asker: str) -> list[str]:
    """질문한 회사(asker, 예: 'hri') 접두사가 아닌 문서를 돌려준다. 코드를 못 읽은 문서는 노출로 세지 않는다."""
    prefix = asker.upper() + "-"
    return [key for key in ranked if key != UNKNOWN and not key.startswith(prefix)]


def evaluate_case(case: dict, results: list[dict]) -> dict:
    """질문 1건을 채점한다. results는 검색 결과 문서 목록(file_name, score 포함)이다."""
    ranked = [doc_key(r.get("file_name")) or UNKNOWN for r in results]
    asker = case.get("asker") or case["company"]
    leaks = find_leaks(ranked, asker)
    scores = [r.get("score") for r in results if isinstance(r.get("score"), (int, float))]
    outcome = {
        "id": case["id"],
        "type": case["type"],
        "result_count": len(results),
        "top_score": round(max(scores), 4) if scores else None,
        "top_ranked": ranked[:5],
        "rank": None,
        "recall": {},
        "leaks": leaks,
        "unknown_docs": ranked.count(UNKNOWN),
    }

    if case["type"] in RANKED_TYPES:
        rank = first_rank(ranked, case["gold_docs"])
        outcome["rank"] = rank
        outcome["recall"] = {k: recall_at_k(ranked, case.get("relevant_docs"), k) for k in RECALL_KS}
        outcome["verdict"] = "PASS" if hit_at_k(rank, 3) and not leaks else "FAIL"
        outcome["note"] = f"정답 {case['gold_docs'][0]} 순위 {rank or '없음'}"
    elif case["type"] == "cross_company":
        outcome["verdict"] = "FAIL" if leaks else "PASS"
        outcome["note"] = f"타사 문서 {len(leaks)}건 노출" if leaks else "타사 문서 노출 없음"
    else:  # no_evidence: 결과가 비면 통과, 결과가 있으면 점수를 남기고 사람이 본다
        if leaks:
            outcome["verdict"] = "FAIL"
            outcome["note"] = f"타사 문서 {len(leaks)}건 노출"
        elif not results:
            outcome["verdict"] = "PASS"
            outcome["note"] = "결과 없음"
        else:
            outcome["verdict"] = "INFO"
            outcome["note"] = f"결과 {len(results)}건, 최고 점수 {outcome['top_score']} (판정하지 않음)"
    return outcome


def _mean(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def summarize(outcomes: list[dict], min_samples: int = MIN_SAMPLES) -> dict:
    """유형별 표본 수와 지표를 모은다. ERROR(실행 오류)는 지표에서 빼고 건수만 센다."""
    valid = [o for o in outcomes if o.get("verdict") != "ERROR"]
    by_type: dict[str, dict] = {}
    for qtype in ALL_TYPES:
        group = [o for o in valid if o["type"] == qtype]
        if not group:
            continue
        entry = {"n": len(group), "sufficient": len(group) >= min_samples,
                 "leaks": sum(len(o["leaks"]) for o in group)}
        if qtype in RANKED_TYPES:
            ranks = [o["rank"] for o in group]
            entry["hit1"] = _mean([float(hit_at_k(r, 1)) for r in ranks])
            entry["hit3"] = _mean([float(hit_at_k(r, 3)) for r in ranks])
            entry["mrr"] = _mean([reciprocal_rank(r) for r in ranks])
            entry["recall"] = {}
            for k in RECALL_KS:
                values = [o["recall"][k] for o in group if o["recall"].get(k) is not None]
                entry["recall"][k] = {"value": _mean(values), "n": len(values)}
        by_type[qtype] = entry
    return {
        "total": len(outcomes),
        "errors": len(outcomes) - len(valid),
        "by_type": by_type,
        "leaks_total": sum(len(o["leaks"]) for o in valid),
    }


def regression_verdict(summary: dict, hit3_min: float = HIT3_MIN, leaks_max: int = LEAKS_MAX) -> dict:
    """회귀 기준(제안값)으로 판정한다. 표본이 적은 유형은 PASS·FAIL을 내지 않고 '표본 부족'으로 둔다."""
    types = {}
    for qtype, entry in summary["by_type"].items():
        if qtype not in RANKED_TYPES:
            types[qtype] = "판정 대상 아님"
        elif not entry["sufficient"]:
            types[qtype] = "표본 부족"
        else:
            types[qtype] = "PASS" if entry["hit3"] >= hit3_min else "FAIL"
    leaks = "PASS" if summary["leaks_total"] <= leaks_max else "FAIL"
    judged = [v for v in types.values() if v in ("PASS", "FAIL")]
    overall = "FAIL" if leaks == "FAIL" or "FAIL" in judged else ("PASS" if judged else "표본 부족")
    return {"leaks": leaks, "types": types, "overall": overall}
