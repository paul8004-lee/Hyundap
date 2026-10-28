"""embedding/classifier.py 테스트. OpenAI를 부르지 않고 가짜 Decisions 클라이언트로 실행한다.

실행(저장소 맨 위 폴더에서):  python -m unittest hyundap.tests.test_embedding_classifier -v
"""
import os
import unittest
from unittest.mock import patch

from openai.types.decision import Decision

from hyundap.common import config as team_config
from hyundap.embedding import classifier
from hyundap.embedding import (
    ClassifyResult, build_chunk_metadata, classify_document, embed_and_store, get_document_chunks,
)
from hyundap.embedding.classifier import (
    SAMPLE_CHARS, build_classify_input, build_classify_questions, get_classifier_client, read_classify_answers,
)
from hyundap.tests.test_embedding import Base, make_chunk


# Decisions API 응답 하나를 SDK의 실제 응답 형식(Decision)으로 만든다
def decision(*answers):
    return Decision.model_validate({
        "answers": list(answers), "model": "gpt-6-luna",
        "usage": {"input_tokens": 400, "output_tokens": 0, "total_tokens": 400,
                  "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}},
    })


def choice(value, confidence=0.95):
    return {"type": "choice", "name": "doc_type", "choice": value, "confidence": confidence,
            "probabilities": [{"value": value, "probability": confidence}]}


def predicate(name, probability):
    return {"type": "predicate", "name": name, "probability": probability}


GOOD = decision(choice("policy", 0.93), predicate("company_named", 0.98), predicate("company_match", 0.96))


# client.decisions.create를 흉내 낸다. reply가 예외면 던지고, 아니면 그대로 돌려준다
class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []
        self.decisions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


# 분류를 켠 상태(DOC_CLASSIFY=1)에서 시험한다. Base는 분류를 꺼 둔다
class ClassifyBase(Base):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"DOC_CLASSIFY": "1"})
        env.start()
        self.addCleanup(env.stop)
        team_config.get_settings.cache_clear()

    def use_client(self, reply):
        client = FakeClient(reply)
        fake = patch.object(classifier, "get_classifier_client", lambda: client)
        fake.start()
        self.addCleanup(fake.stop)
        return client


# 입력·질문 만들기와 응답 읽기(호출 없이)
class Pieces(ClassifyBase):
    def test_input_uses_ordered_sample_within_limit(self):
        chunks = [make_chunk(1, text="둘째" * 10), make_chunk(0, text="첫째" * 10),
                  make_chunk(2, text="가" * SAMPLE_CHARS)]
        text = build_classify_input(chunks)
        self.assertTrue(text.startswith("파일명: D1.pdf"))
        self.assertLess(text.index("첫째"), text.index("둘째"))
        body = text.split("본문:\n")[1]
        self.assertEqual(len(body.replace("\n", "")), SAMPLE_CHARS)

    def test_questions(self):
        questions = build_classify_questions("회사C1")
        self.assertEqual([q["name"] for q in questions], ["doc_type", "company_named", "company_match"])
        self.assertEqual([c["value"] for c in questions[0]["choices"]], ["manual", "policy", "guide", "other"])
        self.assertIn("'회사C1'", questions[2]["instructions"])
        # 업로드 회사 이름이 없으면 회사 질문을 하지 않는다
        self.assertEqual([q["name"] for q in build_classify_questions("  ")], ["doc_type"])

    def test_read_answers_company_rules(self):
        cases = [
            ((0.98, 0.96), "yes"),   # 회사명 있음, 일치
            ((0.98, 0.05), "no"),    # 회사명 있음, 다른 회사
            ((0.98, 0.5), ""),       # 애매하면 보류
            ((0.1, 0.99), ""),       # 본문에 회사명이 없으면 판단하지 않음(업로드 회사명에 끌려간 답 차단)
        ]
        for (named, match), expected in cases:
            with self.subTest(named=named, match=match):
                result = read_classify_answers(decision(
                    choice("guide", 0.8), predicate("company_named", named), predicate("company_match", match)))
                self.assertEqual((result.auto_doc_type, result.doc_type_confidence, result.company_match),
                                 ("guide", 0.8, expected))

    def test_read_answers_unknown_type_clears_confidence(self):
        result = read_classify_answers(decision(choice("contract", 0.9)))
        self.assertEqual((result.auto_doc_type, result.doc_type_confidence, result.status), ("", 0.0, "success"))

    def test_read_answers_rejects_refusal(self):
        with self.assertRaises(ValueError):
            read_classify_answers(decision({"type": "refusal", "name": "doc_type"}))
        # 회사 질문만 거부되면 유형은 남기고 회사 일치는 비운다
        result = read_classify_answers(decision(choice("manual"), {"type": "refusal", "name": "company_match"},
                                                predicate("company_named", 0.9)))
        self.assertEqual((result.auto_doc_type, result.company_match), ("manual", ""))

    def test_metadata_has_empty_classify_slots(self):
        meta = build_chunk_metadata(make_chunk(0), model_name="m")
        self.assertEqual((meta["auto_doc_type"], meta["doc_type_confidence"], meta["company_match"]), ("", 0.0, ""))


# classify_document: 꺼짐·성공·실패
class Classify(ClassifyBase):
    def test_disabled_or_no_key_skips(self):
        classifier._clients.clear()
        self.addCleanup(classifier._clients.clear)
        for env in ({"DOC_CLASSIFY": "0"}, {"OPENAI_API_KEY": ""}):
            with self.subTest(env=env), patch.dict(os.environ, env):
                team_config.get_settings.cache_clear()
                self.assertIsNone(get_classifier_client())
                self.assertEqual(classify_document([make_chunk(0)]).status, "skipped")
        self.assertEqual(classify_document([]).status, "skipped")

    def test_real_client_settings(self):
        # 진짜 OpenAI 클라이언트에 시간 제한·재시도 횟수가 들어간다(호출은 하지 않는다)
        classifier._clients.clear()
        self.addCleanup(classifier._clients.clear)
        client = get_classifier_client()
        self.assertIs(client, get_classifier_client())
        self.assertEqual((client.timeout, client.max_retries),
                         (classifier.CLASSIFY_TIMEOUT_SEC, classifier.CLASSIFY_MAX_RETRIES))
        self.assertTrue(hasattr(client.decisions, "create"))

    def test_success_sends_model_input_questions(self):
        client = self.use_client(GOOD)
        result = classify_document([make_chunk(0), make_chunk(1)])
        self.assertEqual(result, ClassifyResult("policy", 0.93, "yes", "success", ""))
        call = client.calls[0]
        self.assertEqual(call["model"], "gpt-6-luna")
        self.assertIn("D1 V1 본문 0번 조각", call["input"])
        self.assertEqual(len(call["questions"]), 3)

    def test_failure_returns_failed_without_secret(self):
        for reply in (RuntimeError("boom sk-test-key-123456"), decision({"type": "refusal", "name": "doc_type"})):
            with self.subTest(reply=type(reply).__name__):
                self.use_client(reply)
                result = classify_document([make_chunk(0)])
                self.assertEqual((result.status, result.auto_doc_type), ("failed", ""))
                self.assertNotIn("sk-test", result.detail)


# embed_and_store 연결: 분류 결과가 모든 청크에 붙고, 실패해도 업로드는 성공한다
class StoreFlow(ClassifyBase):
    def test_labels_stored_on_every_chunk(self):
        client = self.use_client(GOOD)
        report = self.store([make_chunk(0), make_chunk(1), make_chunk(2)])
        self.assertEqual((report.auto_doc_type, report.doc_type_confidence, report.company_match,
                          report.classify_status), ("policy", 0.93, "yes", "success"))
        self.assertEqual(len(client.calls), 1)
        hits = get_document_chunks("D1", "V1", active_only=False)
        self.assertEqual(len(hits), 3)
        for hit in hits:
            # 관리자가 고른 doc_type은 그대로 두고 auto_doc_type에 따로 저장한다
            self.assertEqual((hit.metadata["doc_type"], hit.metadata["auto_doc_type"]), ("manual", "policy"))
            self.assertEqual((hit.metadata["doc_type_confidence"], hit.metadata["company_match"]), (0.93, "yes"))

    def test_classify_failure_keeps_upload_success(self):
        self.use_client(TimeoutError("slow"))
        report = self.store([make_chunk(0)])
        self.assertEqual((report.status, report.classify_status, report.auto_doc_type), ("success", "failed", ""))
        self.assertEqual(get_document_chunks("D1", "V1", active_only=False)[0].metadata["auto_doc_type"], "")

    def test_no_new_chunks_skips_classify(self):
        client = self.use_client(GOOD)
        self.store([make_chunk(0)])
        report = embed_and_store([make_chunk(0)])
        self.assertEqual((report.skipped_existing, report.classify_status), (1, "skipped"))
        self.assertEqual(len(client.calls), 1)

    def test_classify_runs_once_per_call_not_per_batch(self):
        client = self.use_client(GOOD)
        report = embed_and_store([make_chunk(i) for i in range(5)], batch_size=2)
        self.assertEqual((report.stored, len(client.calls)), (5, 1))
        self.assertEqual(len(self.model.calls), 3)


if __name__ == "__main__":
    unittest.main()
