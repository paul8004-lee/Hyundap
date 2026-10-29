"""search/quality/import_answers.py — 목업 데이터의 검색테스트_정답300.csv를 정답셋(golden_search.json)에 합친다.

담당: 이준영 (이슈 #237)

저장소 맨 위에서 실행한다. CSV는 공유 드라이브 '목업 데이터/03_목록과검증'에 있다 (저장소에 넣지 않는다).

    python -m hyundap.search.quality.import_answers <CSV 경로>          # 기존 QA 문서 25건에 해당하는 질문만
    python -m hyundap.search.quality.import_answers <CSV 경로> --all    # 300문항 전부 (문서를 더 올린 뒤)
    python -m hyundap.search.quality.import_answers <CSV 경로> --dry-run

같은 질문 문장이 이미 있으면 새로 넣지 않고 예상 답변·근거 위치·출처만 채운다. 정답 문서가 서로 다르면 멈춘다.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from .dataset import DEFAULT_PATH

CSV_NAME = "검색테스트_정답300.csv"
COMPANY_CODES = {"현대무벡스": "mvx", "현대엘리베이터": "hel", "현대경제연구원": "hri"}


def row_to_case(row: dict) -> dict:
    """CSV 1행을 정답셋 질문 1건으로 바꾼다. 모든 질문에 문서 코드가 들어 있어 유형은 code다."""
    company = COMPANY_CODES.get(row["company"].strip())
    if company is None:
        raise ValueError(f"알 수 없는 회사: {row['company']} ({row['question_id']})")
    return {
        "id": row["question_id"].strip(),
        "type": "code",
        "company": company,
        "asker": company,
        "query": row["question"].strip(),
        "gold_docs": [row["expected_document_id"].strip()],
        "relevant_docs": None,
        "gold_location": row["locator"].strip() or None,
        "expected_answer": row["expected_answer"].strip() or None,
        "forbidden_companies": None,
        "source": f"{CSV_NAME} {row['question_id'].strip()}",
        "status": "seed",
        "regression": [],
    }


def _same_query(text: str) -> str:
    return re.sub(r"\s+", "", text)


def merge_cases(cases: list[dict], new_cases: list[dict]) -> tuple[list[dict], int, int]:
    """질문 문장이 같은 기존 질문은 예상 답변·근거 위치·출처를 채우고, 나머지는 뒤에 붙인다.

    돌려주는 값: (합친 목록, 새로 넣은 수, 채운 수). 같은 질문인데 정답 문서가 다르면 ValueError."""
    merged = [dict(case) for case in cases]
    by_query = {_same_query(case["query"]): case for case in merged}
    ids = {case["id"] for case in merged}
    added = enriched = 0
    for new in new_cases:
        old = by_query.get(_same_query(new["query"]))
        if old is not None:
            if [g.upper() for g in old.get("gold_docs") or []] != [g.upper() for g in new["gold_docs"]]:
                raise ValueError(f"같은 질문인데 정답 문서가 다릅니다: {old['id']} {old.get('gold_docs')} / {new['id']} {new['gold_docs']}")
            old["gold_location"] = new["gold_location"]
            old["expected_answer"] = new["expected_answer"]
            if new["source"] not in old["source"]:
                old["source"] = f"{old['source']}; {new['source']}"
            enriched += 1
        elif new["id"] not in ids:
            merged.append(new)
            by_query[_same_query(new["query"])] = new
            ids.add(new["id"])
            added += 1
    return merged, added, enriched


def read_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main(argv=None) -> int:
    from .run_eval import known_document_keys  # 기존 QA 코퍼스(25건)의 문서 코드

    parser = argparse.ArgumentParser(description="검색테스트_정답300.csv를 정답셋에 합친다")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--all", action="store_true", help="QA 코퍼스에 없는 문서의 질문도 넣는다")
    parser.add_argument("--dry-run", action="store_true", help="파일을 바꾸지 않고 결과만 출력")
    args = parser.parse_args(argv)

    new_cases = [row_to_case(row) for row in read_rows(args.csv_path)]
    known = known_document_keys()
    if not args.all and known is not None:
        new_cases = [case for case in new_cases if case["gold_docs"][0] in known]

    data = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
    data["cases"], added, enriched = merge_cases(data["cases"], new_cases)
    print(f"CSV 질문 {len(new_cases)}건 → 새로 추가 {added}건, 기존 질문 보강 {enriched}건, 전체 {len(data['cases'])}건")
    if not args.dry_run:
        DEFAULT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"저장: {DEFAULT_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
