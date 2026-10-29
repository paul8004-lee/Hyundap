"""search/quality 시험 (이슈 #237). 실제 DB·OpenAI 없이 가짜 검색 결과로 지표 계산과 정답셋 형식을 확인한다.

실행: python -m unittest hyundap.tests.test_search_quality -v
"""
import json
import unittest
from pathlib import Path

from hyundap.search.quality import dataset, metrics

CORPUS_FILE = Path(__file__).resolve().parent / "qa" / "corpus.json"


def result(file_name, score=0.8):
    """검색 결과 문서 1건 (file_name, score만 쓴다)."""
    return {"file_name": file_name, "score": score}


def ranked_results(*keys):
    return [result(f"{key}_가상.docx", 0.9 - 0.05 * i) for i, key in enumerate(keys)]


def case(**overrides):
    base = {"id": "T1", "type": "natural", "company": "hri", "asker": "hri", "query": "질문",
            "gold_docs": ["HRI-SYN-MAN-001"], "relevant_docs": None, "status": "seed", "source": "test"}
    base.update(overrides)
    return base


class MetricTests(unittest.TestCase):
    def test_doc_key_reads_two_code_shapes(self):
        self.assertEqual(metrics.doc_key("HRI-SYN-MAN-001_가상_매뉴얼.docx"), "HRI-SYN-MAN-001")
        self.assertEqual(metrics.doc_key("mvx-pub-026_제품.pdf"), "MVX-PUB-026")
        self.assertIsNone(metrics.doc_key("report.pdf"))
        self.assertIsNone(metrics.doc_key(None))

    def test_first_rank_and_hits(self):
        ranked = ["A-BB-CC-001", "A-BB-CC-002", "A-BB-CC-003"]
        self.assertEqual(metrics.first_rank(ranked, ["a-bb-cc-002"]), 2)
        self.assertIsNone(metrics.first_rank(ranked, ["A-BB-CC-009"]))
        self.assertTrue(metrics.hit_at_k(2, 3))
        self.assertFalse(metrics.hit_at_k(4, 3))
        self.assertFalse(metrics.hit_at_k(None, 3))
        self.assertEqual(metrics.reciprocal_rank(4), 0.25)
        self.assertEqual(metrics.reciprocal_rank(None), 0.0)

    def test_recall_needs_a_relevant_set(self):
        ranked = ["A-BB-CC-001", "A-BB-CC-002", "A-BB-CC-003", "A-BB-CC-004"]
        self.assertIsNone(metrics.recall_at_k(ranked, None, 3))
        self.assertIsNone(metrics.recall_at_k(ranked, [], 3))
        self.assertEqual(metrics.recall_at_k(ranked, ["A-BB-CC-001", "A-BB-CC-004"], 3), 0.5)
        self.assertEqual(metrics.recall_at_k(ranked, ["A-BB-CC-001", "A-BB-CC-004"], 5), 1.0)

    def test_find_leaks_by_company_prefix(self):
        ranked = ["HRI-SYN-MAN-001", "MVX-SYN-MAN-001", metrics.UNKNOWN]
        self.assertEqual(metrics.find_leaks(ranked, "hri"), ["MVX-SYN-MAN-001"])
        self.assertEqual(metrics.find_leaks(["HRI-SYN-MAN-001"], "hri"), [])


class EvaluateCaseTests(unittest.TestCase):
    def test_ranked_case_pass_at_rank_3(self):
        out = metrics.evaluate_case(case(), ranked_results("HRI-SYN-EDU-004", "HRI-SYN-MAN-002", "HRI-SYN-MAN-001"))
        self.assertEqual((out["rank"], out["verdict"]), (3, "PASS"))

    def test_ranked_case_fail_when_gold_is_below_3_or_missing(self):
        late = ranked_results("HRI-SYN-EDU-004", "HRI-SYN-MAN-002", "HRI-SYN-MAN-005", "HRI-SYN-MAN-001")
        self.assertEqual(metrics.evaluate_case(case(), late)["verdict"], "FAIL")
        self.assertIsNone(metrics.evaluate_case(case(), ranked_results("HRI-SYN-MAN-002"))["rank"])
        self.assertEqual(metrics.evaluate_case(case(), [])["verdict"], "FAIL")

    def test_leak_fails_even_if_rank_is_good(self):
        out = metrics.evaluate_case(case(), ranked_results("HRI-SYN-MAN-001", "MVX-SYN-MAN-001"))
        self.assertEqual(out["leaks"], ["MVX-SYN-MAN-001"])
        self.assertEqual(out["verdict"], "FAIL")

    def test_recall_is_only_computed_with_relevant_docs(self):
        plain = metrics.evaluate_case(case(), ranked_results("HRI-SYN-MAN-001"))
        self.assertTrue(all(v is None for v in plain["recall"].values()))
        both = case(relevant_docs=["HRI-SYN-MAN-001", "HRI-SYN-MAN-002"])
        out = metrics.evaluate_case(both, ranked_results("HRI-SYN-MAN-001", "HRI-SYN-EDU-004"))
        self.assertEqual(out["recall"][3], 0.5)

    def test_cross_company_passes_without_foreign_docs(self):
        asked = case(type="cross_company", asker="mvx", company="hri", gold_docs=[], forbidden_companies=["hri"])
        self.assertEqual(metrics.evaluate_case(asked, ranked_results("MVX-SYN-MAN-001"))["verdict"], "PASS")
        self.assertEqual(metrics.evaluate_case(asked, [])["verdict"], "PASS")
        self.assertEqual(metrics.evaluate_case(asked, ranked_results("HRI-SYN-MAN-001"))["verdict"], "FAIL")

    def test_no_evidence_is_info_when_results_exist(self):
        asked = case(type="no_evidence", gold_docs=[])
        self.assertEqual(metrics.evaluate_case(asked, [])["verdict"], "PASS")
        info = metrics.evaluate_case(asked, [result("HRI-SYN-MAN-001_x.docx", 0.31)])
        self.assertEqual((info["verdict"], info["top_score"]), ("INFO", 0.31))

    def test_unreadable_file_name_is_not_a_leak(self):
        out = metrics.evaluate_case(case(), [result("report.pdf"), result("HRI-SYN-MAN-001_x.docx")])
        self.assertEqual((out["leaks"], out["unknown_docs"], out["rank"]), ([], 1, 2))


class SummaryTests(unittest.TestCase):
    def outcomes(self, ranks, qtype="natural"):
        found = []
        for i, rank in enumerate(ranks):
            keys = ["HRI-SYN-EDU-004"] * ((rank or 6) - 1) + (["HRI-SYN-MAN-001"] if rank else [])
            found.append(metrics.evaluate_case(case(id=f"T{i}", type=qtype), ranked_results(*keys)))
        return found

    def test_small_samples_are_marked_not_judged(self):
        summary = metrics.summarize(self.outcomes([1, 1, 5]))
        entry = summary["by_type"]["natural"]
        self.assertEqual((entry["n"], entry["sufficient"]), (3, False))
        verdict = metrics.regression_verdict(summary)
        self.assertEqual(verdict["types"]["natural"], "표본 부족")
        self.assertEqual(verdict["overall"], "표본 부족")

    def test_enough_samples_are_judged_by_hit3(self):
        good = metrics.summarize(self.outcomes([1] * 9 + [5]))   # Hit@3 = 0.9
        self.assertEqual(good["by_type"]["natural"]["hit3"], 0.9)
        self.assertEqual(metrics.regression_verdict(good)["overall"], "PASS")
        bad = metrics.summarize(self.outcomes([1] * 8 + [5, 5]))  # Hit@3 = 0.8
        self.assertEqual(metrics.regression_verdict(bad)["overall"], "FAIL")

    def test_one_leak_fails_the_regression(self):
        outcomes = self.outcomes([1] * 10)
        leaked = case(id="L", type="cross_company", asker="mvx", company="hri", gold_docs=[])
        outcomes.append(metrics.evaluate_case(leaked, ranked_results("HRI-SYN-MAN-001")))
        verdict = metrics.regression_verdict(metrics.summarize(outcomes))
        self.assertEqual((verdict["leaks"], verdict["overall"]), ("FAIL", "FAIL"))

    def test_recall_average_counts_only_questions_with_a_set(self):
        with_set = case(id="R1", relevant_docs=["HRI-SYN-MAN-001", "HRI-SYN-MAN-002"])
        outcomes = [metrics.evaluate_case(with_set, ranked_results("HRI-SYN-MAN-001")),
                    metrics.evaluate_case(case(id="R2"), ranked_results("HRI-SYN-MAN-001"))]
        recall = metrics.summarize(outcomes)["by_type"]["natural"]["recall"][3]
        self.assertEqual((recall["value"], recall["n"]), (0.5, 1))

    def test_errors_are_counted_but_not_measured(self):
        outcomes = self.outcomes([1]) + [{"id": "E", "type": "natural", "verdict": "ERROR", "note": "x"}]
        summary = metrics.summarize(outcomes)
        self.assertEqual((summary["errors"], summary["by_type"]["natural"]["n"]), (1, 1))


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.cases = dataset.load_cases()
        documents = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))["documents"]
        self.known = {d["key"] for d in documents}

    def test_golden_set_is_valid_against_the_corpus(self):
        self.assertEqual(dataset.validate_cases(self.cases, self.known), {})

    def test_golden_set_covers_every_type_the_issue_asks_for(self):
        types = set(dataset.count_by_type(self.cases))
        self.assertTrue({"code", "natural", "exclusion", "cross_company", "no_evidence"} <= types)

    def test_regression_cases_are_linked(self):
        linked = {c["id"] for c in self.cases if c.get("regression")}
        self.assertTrue({"P1-S10", "P1-S11", "Q085"} <= linked)

    def test_validation_catches_broken_cases(self):
        self.assertIn("순위 질문인데 gold_docs 없음", dataset.validate_case(case(gold_docs=[])))
        unknown = dataset.validate_case(case(gold_docs=["HRI-SYN-MAN-999"]), self.known)
        self.assertTrue(any("코퍼스에 없는 문서" in p for p in unknown))
        self.assertIn("알 수 없는 유형 weird", dataset.validate_case(case(type="weird")))
        self.assertIn("relevant_docs에 gold_docs가 모두 들어 있어야 함",
                      dataset.validate_case(case(relevant_docs=["HRI-SYN-MAN-002"])))
        self.assertIn("cross_company인데 forbidden_companies 없음",
                      dataset.validate_case(case(type="cross_company", gold_docs=[])))
        self.assertIn("id 중복", dataset.validate_cases([case(), case()])["T1"])


if __name__ == "__main__":
    unittest.main()


class ImportAnswersTests(unittest.TestCase):
    """검색테스트_정답300.csv 행을 정답셋으로 바꾸고 합치는 규칙."""

    def row(self, **overrides):
        base = {"question_id": "HRI-SYN-MAN-002-Q1", "company": "현대경제연구원",
                "question": "가상 문서 HRI-SYN-MAN-002의 첫 검토 기한은?", "expected_answer": "접수 후 6 업무시간",
                "expected_document_id": "HRI-SYN-MAN-002", "locator": "완료와 보존", "is_synthetic": "True"}
        base.update(overrides)
        return base

    def test_row_becomes_a_code_case(self):
        from hyundap.search.quality.import_answers import row_to_case
        converted = row_to_case(self.row())
        self.assertEqual((converted["type"], converted["company"], converted["gold_docs"]),
                         ("code", "hri", ["HRI-SYN-MAN-002"]))
        self.assertEqual((converted["gold_location"], converted["expected_answer"]), ("완료와 보존", "접수 후 6 업무시간"))
        self.assertEqual(dataset.validate_case(converted), [])

    def test_unknown_company_stops(self):
        from hyundap.search.quality.import_answers import row_to_case
        with self.assertRaises(ValueError):
            row_to_case(self.row(company="다른회사"))

    def test_same_question_enriches_instead_of_duplicating(self):
        from hyundap.search.quality.import_answers import merge_cases, row_to_case
        existing = case(id="P1-S10", query="가상 문서  HRI-SYN-MAN-002의 첫 검토 기한은?",
                        gold_docs=["HRI-SYN-MAN-002"], expected_answer="6 업무시간")
        merged, added, enriched = merge_cases([existing], [row_to_case(self.row())])
        self.assertEqual((len(merged), added, enriched), (1, 0, 1))
        self.assertEqual(merged[0]["id"], "P1-S10")
        self.assertEqual(merged[0]["gold_location"], "완료와 보존")
        self.assertIn("검색테스트_정답300.csv", merged[0]["source"])
        self.assertEqual(existing["expected_answer"], "6 업무시간")  # 원본 목록은 바꾸지 않는다

    def test_new_question_is_appended_once(self):
        from hyundap.search.quality.import_answers import merge_cases, row_to_case
        new = row_to_case(self.row())
        merged, added, _ = merge_cases([case()], [new, new])
        self.assertEqual((len(merged), added), (2, 1))

    def test_conflicting_gold_stops(self):
        from hyundap.search.quality.import_answers import merge_cases, row_to_case
        existing = case(query="가상 문서 HRI-SYN-MAN-002의 첫 검토 기한은?", gold_docs=["HRI-SYN-MAN-001"])
        with self.assertRaises(ValueError):
            merge_cases([existing], [row_to_case(self.row())])
