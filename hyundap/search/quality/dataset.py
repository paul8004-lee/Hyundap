"""search/quality/dataset.py — 검색 정답셋(golden_search.json)을 읽고 형식을 확인한다.

담당: 이준영 (이슈 #237)

질문 1건의 항목:
    id, type, company(문서가 있는 회사), asker(질문하는 회사, 없으면 company), query,
    gold_docs(정답 문서 코드), relevant_docs(관련 문서 정답 집합, 있을 때만 Recall@k 계산),
    gold_location(근거 위치, 모르면 null), expected_answer(예상 답변, 모르면 null),
    forbidden_companies(타사 요구 질문에서 나오면 안 되는 회사), source(출처), status(seed/draft), regression(연결된 사례)
status: seed = 기존 QA·이슈에서 확인된 질문, draft = 정답을 문서 코드로만 정한 초안(실제 실행으로 확인 필요)
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .metrics import ALL_TYPES, RANKED_TYPES, DOC_KEY_PATTERN

DEFAULT_PATH = Path(__file__).with_name("golden_search.json")
COMPANIES = ("mvx", "hel", "hri")
STATUSES = ("seed", "draft")


def load_cases(path: Path = DEFAULT_PATH) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["cases"]


def validate_case(case: dict, known_keys: set[str] | None = None) -> list[str]:
    """질문 1건의 문제점 목록. 비어 있으면 정상이다. known_keys가 있으면 정답 문서 코드가 실제 코퍼스에 있는지도 본다."""
    problems = []
    for field in ("id", "type", "company", "query", "status", "source"):
        if not case.get(field):
            problems.append(f"{field} 없음")
    if case.get("type") not in ALL_TYPES:
        problems.append(f"알 수 없는 유형 {case.get('type')}")
    if case.get("company") not in COMPANIES:
        problems.append(f"알 수 없는 회사 {case.get('company')}")
    if case.get("asker") is not None and case["asker"] not in COMPANIES:
        problems.append(f"알 수 없는 질문 회사 {case['asker']}")
    if case.get("status") not in STATUSES:
        problems.append(f"알 수 없는 status {case.get('status')}")
    gold = case.get("gold_docs") or []
    if case.get("type") in RANKED_TYPES and not gold:
        problems.append("순위 질문인데 gold_docs 없음")
    relevant = case.get("relevant_docs") or []
    for key in list(gold) + list(relevant):
        if not DOC_KEY_PATTERN.fullmatch(key):
            problems.append(f"문서 코드 형식 아님 {key}")
        elif known_keys is not None and key not in known_keys:
            problems.append(f"코퍼스에 없는 문서 {key}")
    if relevant and not set(gold) <= set(relevant):
        problems.append("relevant_docs에 gold_docs가 모두 들어 있어야 함")
    if case.get("type") == "cross_company" and not case.get("forbidden_companies"):
        problems.append("cross_company인데 forbidden_companies 없음")
    return problems


def validate_cases(cases: list[dict], known_keys: set[str] | None = None) -> dict[str, list[str]]:
    """문제가 있는 질문만 {id: 문제 목록}으로 돌려준다. id 중복도 잡는다."""
    found = {}
    ids = Counter(case.get("id") for case in cases)
    for case in cases:
        problems = validate_case(case, known_keys)
        if ids[case.get("id")] > 1:
            problems.append("id 중복")
        if problems:
            found[str(case.get("id"))] = problems
    return found


def count_by_type(cases: list[dict]) -> dict[str, dict[str, int]]:
    """유형별 질문 수와 status별 수."""
    table: dict[str, dict[str, int]] = {}
    for case in cases:
        row = table.setdefault(case["type"], {"n": 0, "seed": 0, "draft": 0})
        row["n"] += 1
        row[case["status"]] += 1
    return table
