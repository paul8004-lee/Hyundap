"""search/quality/run_eval.py — 검색 순위 평가를 실행하고 결과를 기록한다. (이슈 #237, 담당: 이준영)

저장소 맨 위에서 실행한다.

    python -m hyundap.search.quality.run_eval --check   # 정답셋 형식·표본 수 확인 (API·DB 불필요)
    python -m hyundap.search.quality.run_eval --run     # 실제 검색 실행 (실제 DB·OpenAI 호출)

--run은 tests/qa가 쓰는 계정 파일(data/qa_accounts.json)과 올려 둔 문서 25건(tests/qa/upload_corpus.py)이 필요하고,
질문 1건마다 임베딩 호출이 1번 든다. 결과는 data/quality/eval-<시각>.json에 남긴다 (data/는 gitignore).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .dataset import DEFAULT_PATH, count_by_type, load_cases, validate_cases
from .metrics import HIT3_MIN, LEAKS_MAX, MIN_SAMPLES, RANKED_TYPES, regression_verdict, summarize, evaluate_case

ROOT = Path(__file__).resolve().parents[3]
CORPUS_FILE = ROOT / "hyundap" / "tests" / "qa" / "corpus.json"
OUT_DIR = ROOT / "data" / "quality"


def known_document_keys() -> set[str] | None:
    """기존 QA 코퍼스(corpus.json)의 문서 코드. 파일이 없으면 None."""
    if not CORPUS_FILE.exists():
        return None
    return {doc["key"] for doc in json.loads(CORPUS_FILE.read_text(encoding="utf-8"))["documents"]}


def check(cases: list[dict]) -> int:
    """정답셋 형식과 유형별 표본 수를 출력한다. 문제가 있으면 1을 돌려준다."""
    problems = validate_cases(cases, known_document_keys())
    print(f"질문 {len(cases)}건 (기준 표본 수: 유형별 {MIN_SAMPLES}건 이상이어야 판정)")
    for qtype, row in count_by_type(cases).items():
        if qtype not in RANKED_TYPES:
            flag = "  (순위 판정 대상 아님: 노출·거부만 확인)"
        else:
            flag = "" if row["n"] >= MIN_SAMPLES else "  ← 표본 부족"
        print(f"  {qtype:14} {row['n']:3}건 (seed {row['seed']}, draft {row['draft']}){flag}")
    with_set = sum(1 for case in cases if case.get("relevant_docs"))
    print(f"관련 문서 정답 집합이 있는 질문: {with_set}건 (Recall@k는 이 질문에서만 계산)")
    for case_id, items in problems.items():
        print(f"  [문제] {case_id}: {', '.join(items)}")
    print("정답셋 형식 이상 없음" if not problems else f"문제 {len(problems)}건")
    return 1 if problems else 0


def git_state() -> dict:
    """기록용 커밋 해시와 수정 중인 파일 유무. git이 없으면 None."""
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        except Exception:
            return None
    status = run("status", "--porcelain")
    return {"commit": run("rev-parse", "HEAD"), "dirty": None if status is None else bool(status)}


def search_settings() -> dict:
    """기록용 검색·모델 설정. 읽지 못하면 None으로 둔다."""
    try:
        from hyundap.common.config import get_settings
        s = get_settings()
        return {"min_score": s.search_min_score, "top_k_max": s.search_top_k_max,
                "embedding_model": s.openai_embedding_model}
    except Exception:
        return {"min_score": None, "top_k_max": None, "embedding_model": None}


async def run_real(cases: list[dict]) -> list[dict]:
    """실제 백엔드(화면과 같은 BackendServices)로 질문을 검색하고 채점한다."""
    from hyundap.tests.qa.qa_common import COMPANY_ACCOUNTS, Client  # tests/qa 도구 재사용. 지연 import
    from hyundap.ui.services import UIServiceError

    client = Client()
    outcomes = []
    try:
        for case in cases:
            asker = case.get("asker") or case["company"]
            account = COMPANY_ACCOUNTS[asker][1]
            if account not in client.accounts:
                outcomes.append({"id": case["id"], "type": case["type"], "verdict": "ERROR",
                                 "note": f"계정 {account}가 data/qa_accounts.json에 없음"})
                continue
            try:
                response = await client.service.search(await client.session(account), case["query"])
                outcomes.append(evaluate_case(case, response["results"]))
            except UIServiceError as error:
                outcomes.append({"id": case["id"], "type": case["type"], "verdict": "ERROR",
                                 "note": f"{error.code}: {error}"})
            print(f"{case['id']:16} {outcomes[-1]['verdict']:5} {outcomes[-1].get('note', '')}"[:200], flush=True)
    finally:
        await client.close()
    return outcomes


def build_record(cases: list[dict], outcomes: list[dict], mode: str = "real") -> dict:
    """결과와 함께 남길 기록: 커밋·설정·표본 수·호출 수·판정·재현 명령."""
    summary = summarize(outcomes)
    return {
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,  # real = 실제 DB·OpenAI 호출. Mock 시험은 hyundap/tests/test_search_quality.py (결과를 기록하지 않음)
        "git": git_state(),
        "settings": search_settings(),
        "dataset": {"file": str(DEFAULT_PATH.relative_to(ROOT)), "cases": len(cases),
                    "by_type": count_by_type(cases)},
        "calls": {"search": len(cases), "embedding": len(cases),
                  "cost": "계산하지 않음 (질문 1건당 임베딩 1회, 답변 모델 호출 없음)"},
        "thresholds": {"min_samples": MIN_SAMPLES, "hit3_min": HIT3_MIN, "leaks_max": LEAKS_MAX,
                       "status": "제안값, 팀 합의 전"},
        "summary": summary,
        "verdict": regression_verdict(summary),
        "results": outcomes,
        "command": "python -m hyundap.search.quality.run_eval --run",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="검색 순위 평가")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="정답셋 형식·표본 수만 확인")
    mode.add_argument("--run", action="store_true", help="실제 검색 실행 (DB·OpenAI 호출)")
    parser.add_argument("--only-status", choices=("seed", "draft"), help="이 status의 질문만 실행")
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.check:
        return check(cases)
    if check(cases):
        return 1
    if args.only_status:
        cases = [case for case in cases if case["status"] == args.only_status]
    outcomes = asyncio.run(run_real(cases))
    record = build_record(cases, outcomes)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"eval-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"판정 {record['verdict']['overall']}, 타사 노출 {record['summary']['leaks_total']}건, 저장 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
