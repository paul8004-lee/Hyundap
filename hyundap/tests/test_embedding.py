"""embedding 폴더 테스트. (import는 모두 hyundap. 전체 경로) OpenAI를 부르지 않고 가짜 모델과 임시 Chroma 폴더로 실행한다.

실행(저장소 맨 위 폴더에서, 이슈 #131 기준):  python -m unittest hyundap.tests.test_embedding -v
"""
import hashlib
import os
import tempfile
import unittest
from unittest.mock import patch

from hyundap.embedding import embedder, models, vector_store
from hyundap.embedding import (
    EmbeddingError, VectorStoreError, activate_version, build_chunk_metadata, build_embedding_text,
    build_where_filter, check_embedding_health, check_vector_store_health, collection_name_for_model,
    delete_vectors, embed_and_store, embed_batch_with_retry, embed_chunks, embed_query, filter_new_chunks,
    fit_to_token_limit, get_collection, get_document_chunks, get_version_chunk_ids, query_vectors,
    upsert_chunks, validate_embeddings,
)

try:  # 이성민의 hyundap.common이 있으면 그 설정·Chunk도 함께 검증한다.
    from hyundap.common import config as team_config
    from hyundap.common.schemas import Chunk, make_chunk_id
except Exception:  # noqa: BLE001 - common이 없거나 python-dotenv가 없는 환경
    team_config = None
    Chunk = make_chunk_id = None

DIM = 8
REAL_GET_MODEL = embedder.get_embedding_model  # 가짜로 바꾸기 전의 진짜 함수


# --- 가짜 부품 -------------------------------------------------------------

class RateLimitError(Exception):
    status_code = 429


class AuthenticationError(Exception):
    status_code = 401


class APIConnectionError(Exception):
    pass


def fake_vector(text: str) -> list[float]:
    """같은 글이면 같은 벡터, 다른 글이면 다른 벡터(0이 아님)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [(digest[i] - 127.5) / 127.5 + 0.01 for i in range(DIM)]


class FakeModel:
    """embed_documents를 흉내 낸다. plan에 예외를 넣으면 호출 순서대로 던진다."""

    def __init__(self, plan=None, override=None):
        self.plan = list(plan or [])
        self.override = override  # 결과를 바꿔치기하는 함수
        self.calls: list[list[str]] = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.plan:
            step = self.plan.pop(0)
            if isinstance(step, Exception):
                raise step
        vectors = [fake_vector(t) for t in texts]
        return self.override(vectors) if self.override else vectors


def make_chunk(i, *, doc="D1", ver="V1", company="C1", text=None, doc_type="manual", chunk_id=None):
    return {
        "chunk_id": chunk_id or f"{doc}:{ver}:{i:05d}",
        "document_id": doc, "version_id": ver, "company_id": company, "company_name": f"회사{company}",
        "doc_type": doc_type, "file_name": f"{doc}.pdf", "section_no": i + 1, "location_label": f"p.{i + 1}",
        "chunk_index": i, "text": text if text is not None else f"{doc} {ver} 본문 {i}번 조각",
    }


class EmbeddingTestCase(unittest.TestCase):
    """임시 폴더·가짜 모델·가짜 sleep을 준비한다."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, {
            "CHROMA_DB_PATH": os.path.join(self._tmp.name, "chroma"),
            "OPENAI_API_KEY": "sk-test-key-123456",
            "OPENAI_EMBEDDING_MODEL": "fake-model",
        })
        env.start()
        self.addCleanup(env.stop)
        if team_config is not None:
            # get_settings()는 한 번 읽은 값을 저장해 두므로, 테스트 환경변수가 반영되도록 비우고
            # 진짜 .env를 읽지 않게 한다(실제 data/chroma_db를 건드리지 않기 위해 꼭 필요).
            no_dotenv = patch.object(team_config, "load_dotenv", lambda *a, **k: None)
            no_dotenv.start()
            self.addCleanup(no_dotenv.stop)
            team_config.get_settings.cache_clear()
            self.addCleanup(team_config.get_settings.cache_clear)
        self.model = FakeModel()
        for target, value in (
            (embedder, patch.object(embedder, "get_embedding_model", lambda *a, **k: self.model)),
            (embedder, patch.object(embedder.time, "sleep", lambda s: self.sleeps.append(s))),
            (models, patch.object(models, "_dotenv_loaded", True)),
        ):
            value.start()
            self.addCleanup(value.stop)
        self.sleeps: list[float] = []
        vector_store._clients.clear()
        embedder._health_cache.update(until=0.0, model="", result=None)
        self.addCleanup(vector_store._clients.clear)

    def store(self, chunks, activate=False):
        report = embed_and_store(chunks)
        self.assertEqual(report.status, "success", report.failures)
        if activate:
            activate_version(chunks[0]["document_id"], chunks[0]["version_id"])
        return report


# --- 설정·이름 --------------------------------------------------------------

class SettingsAndNames(EmbeddingTestCase):
    def test_settings_from_env_and_relative_path_resolved_against_repo_root(self):
        with patch.dict(os.environ, {"CHROMA_DB_PATH": "data/chroma_db"}):
            s = models.load_embedding_settings()
        self.assertEqual(s.model_name, "fake-model")
        self.assertEqual(s.openai_api_key, "sk-test-key-123456")
        self.assertTrue(os.path.isabs(s.chroma_path))
        self.assertTrue(s.chroma_path.replace("\\", "/").endswith("data/chroma_db"))

    def test_settings_defaults(self):
        keys = ("OPENAI_EMBEDDING_MODEL", "CHROMA_DB_PATH", "OPENAI_API_KEY")
        with patch.dict(os.environ, {}, clear=False):
            for k in keys:
                os.environ.pop(k, None)
            s = models.load_embedding_settings()
        self.assertEqual(s.model_name, "text-embedding-3-small")
        self.assertEqual(s.openai_api_key, "")

    def test_team_settings_object_is_used_when_available(self):
        class Team:
            OPENAI_EMBEDDING_MODEL = "team-model"

        fake_config = type("m", (), {"get_settings": staticmethod(lambda: Team())})
        fake_modules = {"hyundap.common": type("p", (), {"config": fake_config}), "hyundap.common.config": fake_config}
        with patch.dict("sys.modules", fake_modules):
            self.assertEqual(models.load_embedding_settings().model_name, "team-model")

    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(EmbeddingError) as caught:
                REAL_GET_MODEL()
        self.assertEqual(caught.exception.code, "NO_API_KEY")
        self.assertFalse(caught.exception.retryable)

    def test_falls_back_to_env_with_a_warning_when_common_config_cannot_be_imported(self):
        """import가 실패해도 멈추지는 않지만, 조용히 넘어가지 않고 경고를 한 번 남긴다."""
        with patch.dict("sys.modules", {"hyundap.common.config": None}), patch.object(models, "_warned_fallback", False):
            with self.assertLogs(models.__name__, level="WARNING") as logs:
                first = models.load_embedding_settings()
                second = models.load_embedding_settings()
        self.assertEqual(first.model_name, "fake-model")  # 환경변수 값
        self.assertEqual(second.model_name, "fake-model")
        self.assertEqual(len(logs.records), 1)  # 반복해서 남기지 않는다
        self.assertIn("저장소 맨 위", logs.output[0])

    def test_collection_name(self):
        self.assertEqual(collection_name_for_model("text-embedding-3-small"), "chunks__text-embedding-3-small")
        self.assertEqual(collection_name_for_model("my model/v2"), "chunks__my-model-v2")
        self.assertEqual(collection_name_for_model("  "), "chunks__default")

    def test_collections_are_separate_per_model(self):
        a, b = get_collection("model-a"), get_collection("model-b")
        a.add(ids=["x"], embeddings=[[1.0] * DIM])
        self.assertEqual((a.count(), b.count()), (1, 0))


# --- 입력 구성·길이·메타데이터 ----------------------------------------------

class TextAndMetadata(EmbeddingTestCase):
    def test_text_is_stripped_and_has_no_company_info(self):
        chunk = make_chunk(0, text="  매출은 100억이다.\n ")
        text = build_embedding_text(chunk)
        self.assertEqual(text, "매출은 100억이다.")
        self.assertNotIn("회사C1", text)
        self.assertNotIn("manual", text)

    def test_object_chunk_works_like_dict(self):
        class Obj:
            text = " 본문 "

        self.assertEqual(build_embedding_text(Obj()), "본문")

    def test_empty_text_fails(self):
        for bad in ("", "   \n", None):
            with self.assertRaises(EmbeddingError) as caught:
                build_embedding_text(make_chunk(0, text=bad) if bad is not None else {"text": None})
            self.assertEqual(caught.exception.code, "EMPTY_TEXT")

    def test_short_text_not_truncated_even_without_tiktoken(self):
        with patch.object(embedder, "_get_encoding", lambda name: None):
            self.assertEqual(fit_to_token_limit("짧은 글"), ("짧은 글", False))

    def test_fallback_truncates_long_text(self):
        with patch.object(embedder, "_get_encoding", lambda name: None):
            text, truncated = fit_to_token_limit("가" * 10000, max_tokens=8191)
        self.assertTrue(truncated)
        self.assertEqual(len(text), 8191 // 2)

    def test_tiktoken_branch_truncates_by_tokens(self):
        class Enc:
            def encode(self, text, disallowed_special=()):
                return list(range(len(text)))

            def decode(self, tokens):
                return "x" * len(tokens)

        with patch.object(embedder, "_get_encoding", lambda name: Enc()):
            self.assertEqual(fit_to_token_limit("a" * 10, max_tokens=10), ("a" * 10, False))
            self.assertEqual(fit_to_token_limit("a" * 11, max_tokens=10), ("x" * 10, True))

    def test_metadata_fields_and_types(self):
        meta = build_chunk_metadata(make_chunk(3), model_name="fake-model", truncated=True)
        self.assertEqual(set(meta), set(vector_store.METADATA_KEYS))
        self.assertIs(meta["is_active"], False)
        self.assertIs(meta["truncated"], True)
        self.assertEqual((meta["company_id"], meta["doc_type"], meta["location_label"]), ("C1", "manual", "p.4"))
        self.assertEqual((meta["section_no"], meta["chunk_index"]), (4, 3))
        self.assertTrue(all(isinstance(v, (str, int, float, bool)) for v in meta.values()))

    def test_metadata_none_becomes_empty_string_and_required_fields_checked(self):
        chunk = make_chunk(0)
        chunk["file_name"] = None
        chunk["section_no"] = None
        meta = build_chunk_metadata(chunk, model_name="m")
        self.assertEqual((meta["file_name"], meta["section_no"]), ("", ""))
        for key in ("company_id", "doc_type", "document_id", "version_id", "chunk_id"):
            bad = make_chunk(0)
            bad[key] = None
            with self.assertRaises(EmbeddingError) as caught:
                build_chunk_metadata(bad, model_name="m")
            self.assertEqual(caught.exception.code, "MISSING_METADATA", key)


# --- 재시도 -----------------------------------------------------------------

class Retry(EmbeddingTestCase):
    def test_transient_errors_are_retried_with_backoff(self):
        self.model.plan = [RateLimitError("slow down"), APIConnectionError("reset")]
        vectors = embed_batch_with_retry(["a", "b"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(self.model.calls), 3)
        self.assertEqual(self.sleeps, [1.0, 2.0])

    def test_gives_up_after_three_retries_as_retryable(self):
        self.model.plan = [RateLimitError("x")] * 4
        with self.assertRaises(EmbeddingError) as caught:
            embed_batch_with_retry(["a"])
        self.assertEqual(caught.exception.code, "RATE_LIMIT")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0])
        self.assertEqual(len(self.model.calls), 4)

    def test_permanent_error_fails_immediately_and_masks_key(self):
        self.model.plan = [AuthenticationError("Incorrect API key provided: sk-proj-ABCDEF1234567890")]
        with self.assertRaises(EmbeddingError) as caught:
            embed_batch_with_retry(["a"])
        self.assertEqual(caught.exception.code, "AUTH_ERROR")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(self.sleeps, [])
        self.assertNotIn("ABCDEF1234567890", caught.exception.message)
        self.assertNotIn("ABCDEF1234567890", str(caught.exception))

    def test_quota_exceeded_is_not_retried(self):
        self.model.plan = [RateLimitError("You exceeded your current quota")]
        with self.assertRaises(EmbeddingError) as caught:
            embed_batch_with_retry(["a"])
        self.assertEqual(caught.exception.code, "QUOTA_EXCEEDED")
        self.assertEqual(self.sleeps, [])

    def test_wrong_result_count_is_an_error(self):
        self.model.override = lambda vectors: vectors[:-1]
        with self.assertRaises(EmbeddingError) as caught:
            embed_batch_with_retry(["a", "b"])
        self.assertEqual(caught.exception.code, "BAD_RESPONSE")

    def test_empty_input_returns_empty_without_call(self):
        self.assertEqual(embed_batch_with_retry([]), [])
        self.assertEqual(self.model.calls, [])


# --- 청크 임베딩·검증 -------------------------------------------------------

class ChunkEmbedding(EmbeddingTestCase):
    def test_130_chunks_use_batches_of_64(self):
        batch = embed_chunks([make_chunk(i) for i in range(130)])
        self.assertEqual([len(c) for c in self.model.calls], [64, 64, 2])
        self.assertEqual((len(batch.items), len(batch.failures)), (130, 0))
        self.assertEqual(len(batch.items[0].vector), DIM)

    def test_failed_batch_only_fails_its_chunks(self):
        self.model.plan = [None, AuthenticationError("bad key")]  # 첫 묶음 성공, 둘째 묶음 실패
        batch = embed_chunks([make_chunk(i) for i in range(70)])
        self.assertEqual(len(batch.items), 64)
        self.assertEqual(len(batch.failures), 6)
        self.assertEqual({f.code for f in batch.failures}, {"AUTH_ERROR"})

    def test_bad_chunks_become_failures_and_others_continue(self):
        chunks = [make_chunk(0), make_chunk(1, text=" "), make_chunk(2)]
        chunks[2]["company_id"] = ""
        batch = embed_chunks(chunks)
        self.assertEqual(len(batch.items), 1)
        self.assertEqual({f.chunk_id: f.code for f in batch.failures},
                         {"D1:V1:00001": "EMPTY_TEXT", "D1:V1:00002": "MISSING_METADATA"})

    def test_metadata_carries_company_and_model(self):
        item = embed_chunks([make_chunk(0)]).items[0]
        self.assertEqual((item.metadata["company_id"], item.metadata["embedding_model"]), ("C1", "fake-model"))
        self.assertFalse(item.truncated)

    def test_validate_embeddings(self):
        good = [1.0] * DIM
        self.assertEqual(validate_embeddings([]), [])
        self.assertEqual(validate_embeddings([good, good]), [])
        self.assertEqual(validate_embeddings([good, [1.0] * 3]), [1])
        self.assertEqual(validate_embeddings([good, [0.0] * DIM]), [1])
        self.assertEqual(validate_embeddings([good, [float("nan")] + [1.0] * (DIM - 1)]), [1])
        self.assertEqual(validate_embeddings([good, [float("inf")] + [1.0] * (DIM - 1)]), [1])
        self.assertEqual(validate_embeddings([good, good], expected_dim=5), [0, 1])
        self.assertEqual(validate_embeddings([[]]), [0])


# --- 저장·공개·조회 ---------------------------------------------------------

class StoreAndQuery(EmbeddingTestCase):
    def test_store_is_private_until_activated_then_searchable(self):
        chunks = [make_chunk(i) for i in range(5)]
        report = self.store(chunks)
        self.assertEqual((report.total, report.stored, report.embedded, report.skipped_existing), (5, 5, 5, 0))
        self.assertEqual(report.model_name, "fake-model")
        self.assertEqual((report.document_id, report.version_id, report.company_id), ("D1", "V1", "C1"))

        qv = embed_query(chunks[2]["text"])
        self.assertEqual(query_vectors(qv, build_where_filter(["C1"])), [])  # 비공개: 안 보임
        self.assertEqual(len(query_vectors(qv, build_where_filter(["C1"], active_only=False))), 5)

        self.assertEqual(activate_version("D1", "V1"), 5)
        hits = query_vectors(qv, build_where_filter(["C1"]), top_k=3)
        self.assertEqual(len(hits), 3)
        self.assertEqual(hits[0].chunk_id, "D1:V1:00002")
        self.assertAlmostEqual(hits[0].score, 1.0, places=5)
        self.assertEqual(hits[0].text, chunks[2]["text"])
        self.assertEqual(hits[0].metadata["location_label"], "p.3")
        self.assertTrue(all(0.0 <= h.score <= 1.0 for h in hits))
        self.assertEqual([h.score for h in hits], sorted((h.score for h in hits), reverse=True))

    def test_second_run_is_skipped_not_duplicated(self):
        chunks = [make_chunk(i) for i in range(4)]
        self.store(chunks)
        calls_before = len(self.model.calls)
        report = embed_and_store(chunks)
        self.assertEqual((report.status, report.skipped_existing, report.stored, report.embedded), ("success", 4, 0, 0))
        self.assertEqual(len(self.model.calls), calls_before)  # API를 다시 부르지 않는다
        self.assertEqual(get_collection().count(), 4)

    def test_partial_rerun_embeds_only_new_chunks(self):
        self.store([make_chunk(i) for i in range(3)])
        report = embed_and_store([make_chunk(i) for i in range(5)])
        self.assertEqual((report.skipped_existing, report.stored), (3, 2))
        self.assertEqual(get_collection().count(), 5)

    def test_same_id_other_company_is_rejected(self):
        self.store([make_chunk(0, company="C1")])
        with self.assertRaises(VectorStoreError) as caught:
            embed_and_store([make_chunk(0, company="C2")])
        self.assertEqual(caught.exception.code, "COMPANY_MISMATCH")

    def test_same_id_other_document_is_rejected(self):
        self.store([make_chunk(0, doc="D1", chunk_id="same-id")])
        with self.assertRaises(VectorStoreError) as caught:
            embed_and_store([make_chunk(0, doc="D2", chunk_id="same-id")])
        self.assertEqual(caught.exception.code, "CHUNK_ID_CONFLICT")
        self.assertEqual(get_collection().count(), 1)  # 기존 청크가 덮어써지지 않았다

    def test_input_scope_checks(self):
        cases = {
            "EMPTY_CHUNKS": [],
            "MIXED_SCOPE": [make_chunk(0), make_chunk(1, ver="V2")],
            "DUPLICATE_CHUNK_ID": [make_chunk(0), make_chunk(0)],
        }
        for code, chunks in cases.items():
            with self.subTest(code=code), self.assertRaises(EmbeddingError) as caught:
                embed_and_store(chunks)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.model.calls, [])
        mixed_company = [make_chunk(0), make_chunk(1, company="C2")]
        with self.assertRaises(EmbeddingError) as caught:
            embed_and_store(mixed_company)
        self.assertEqual(caught.exception.code, "MIXED_SCOPE")

    def test_status_partial_when_some_fail(self):
        report = embed_and_store([make_chunk(0), make_chunk(1, text=""), make_chunk(2)])
        self.assertEqual((report.status, report.stored, len(report.failures)), ("partial", 2, 1))
        self.assertEqual(report.failures[0].code, "EMPTY_TEXT")

    def test_retry_after_partial_completes_the_chunk_set(self):
        """DB 계약: partial은 게시 실패이고, 같은 후보로 다시 처리할 때 skipped_existing을 포함한 ID 집합을 검증한다."""
        chunks = [make_chunk(i) for i in range(4)]
        broken = [dict(c) for c in chunks]
        broken[2]["text"] = ""  # 첫 처리에서 한 청크가 실패한다
        first = embed_and_store(broken)
        self.assertEqual((first.status, first.stored), ("partial", 3))
        self.assertEqual(len(get_version_chunk_ids("D1", "V1")), 3)  # 집합이 불완전하므로 DB가 게시를 막는다

        second = embed_and_store(chunks)  # 고친 청크로 같은 버전을 다시 처리
        self.assertEqual((second.status, second.skipped_existing, second.stored), ("success", 3, 1))
        self.assertEqual(get_version_chunk_ids("D1", "V1"), [c["chunk_id"] for c in chunks])

    def test_report_can_be_saved_as_json(self):
        """DB가 처리 보고서 원형을 JSON으로 보관하므로(record_stage_result) 직렬화되어야 하고 비밀 값이 없어야 한다."""
        import dataclasses
        import json
        self.model.plan = [AuthenticationError("bad key sk-proj-SECRETSECRET1234")]
        report = embed_and_store([make_chunk(0), make_chunk(1)])
        saved = json.dumps(dataclasses.asdict(report), ensure_ascii=False)
        loaded = json.loads(saved)
        self.assertEqual(set(loaded), {
            "document_id", "version_id", "company_id", "model_name", "total", "skipped_existing", "embedded",
            "stored", "truncated_count", "failures", "status", "elapsed_sec",
        })
        self.assertEqual(loaded["status"], "failed")
        self.assertEqual(set(loaded["failures"][0]), {"chunk_id", "code", "message", "retryable"})
        self.assertNotIn("SECRETSECRET1234", saved)

    def test_stored_text_is_the_original_chunk_text(self):
        """DB가 청크 text의 SHA-256을 보관하므로, 임베딩 입력만 다듬고 저장되는 글은 원문 그대로여야 한다."""
        chunk = make_chunk(0, text="  앞뒤 공백이 있는 본문\n")
        self.store([chunk])
        stored = get_collection().get(ids=[chunk["chunk_id"]], include=["documents"])["documents"][0]
        self.assertEqual(stored, chunk["text"])
        self.assertEqual(self.model.calls[-1], ["앞뒤 공백이 있는 본문"])  # 임베딩에는 공백을 뺀 글이 들어간다

    def test_status_failed_when_nothing_stored(self):
        self.model.plan = [AuthenticationError("bad")]
        report = embed_and_store([make_chunk(0), make_chunk(1)])
        self.assertEqual((report.status, report.stored, len(report.failures)), ("failed", 0, 2))
        self.assertEqual(get_collection().count(), 0)

    def test_invalid_vector_is_not_stored(self):
        def zero_second(vectors):
            vectors[1] = [0.0] * DIM
            return vectors

        self.model.override = zero_second
        report = embed_and_store([make_chunk(i) for i in range(3)])
        self.assertEqual((report.status, report.stored), ("partial", 2))
        self.assertEqual([f.code for f in report.failures], ["INVALID_VECTOR"])
        self.assertEqual(sorted(get_version_chunk_ids("D1", "V1")), ["D1:V1:00000", "D1:V1:00002"])

    def test_truncated_count_in_report(self):
        with patch.object(embedder, "_get_encoding", lambda name: None):
            report = embed_and_store([make_chunk(0, text="가" * 9000), make_chunk(1)])
        self.assertEqual((report.status, report.truncated_count), ("success", 1))
        meta = get_collection().get(ids=["D1:V1:00000"], include=["metadatas"])["metadatas"][0]
        self.assertTrue(meta["truncated"])

    def test_upsert_length_mismatch(self):
        with self.assertRaises(VectorStoreError) as caught:
            upsert_chunks([make_chunk(0)], [], [])
        self.assertEqual(caught.exception.code, "LENGTH_MISMATCH")
        self.assertEqual(upsert_chunks([], [], []), 0)

    def test_upsert_overwrites_same_id(self):
        chunk = make_chunk(0)
        meta = build_chunk_metadata(chunk, model_name="m")
        upsert_chunks([chunk], [fake_vector("a")], [meta])
        upsert_chunks([{**chunk, "text": "새 글"}], [fake_vector("b")], [meta])
        got = get_collection().get(ids=[chunk["chunk_id"]], include=["documents"])
        self.assertEqual((get_collection().count(), got["documents"]), (1, ["새 글"]))

    def test_filter_new_chunks_keeps_order(self):
        self.store([make_chunk(1)])
        kept = filter_new_chunks([make_chunk(2), make_chunk(1), make_chunk(0)])
        self.assertEqual([c["chunk_index"] for c in kept], [2, 0])
        self.assertEqual(filter_new_chunks([]), [])


class WhereFilterAndScope(EmbeddingTestCase):
    def test_where_shapes(self):
        self.assertEqual(build_where_filter(["C1"], active_only=False), {"company_id": "C1"})
        self.assertEqual(build_where_filter(["C1"]), {"$and": [{"company_id": "C1"}, {"is_active": True}]})
        full = build_where_filter(["C1", "C2", "C1"], doc_types=["manual"], document_ids=["D1", "D2"])
        self.assertEqual(full, {"$and": [
            {"company_id": {"$in": ["C1", "C2"]}}, {"doc_type": "manual"},
            {"document_id": {"$in": ["D1", "D2"]}}, {"is_active": True},
        ]})

    def test_empty_company_scope_is_refused(self):
        for companies in ([], [""], ["  "], None):
            with self.subTest(companies=companies), self.assertRaises(VectorStoreError) as caught:
                build_where_filter(companies)
            self.assertEqual(caught.exception.code, "NO_COMPANY_SCOPE")

    def test_query_refuses_where_without_company(self):
        bad_wheres = [
            {}, {"is_active": True}, {"doc_type": "manual"},
            {"company_id": {"$in": []}}, {"company_id": ""},
            {"$or": [{"company_id": "C1"}, {"doc_type": "x"}]},  # $or는 모든 가지에 회사 조건이 있어야 한다
            {"$and": [{"is_active": True}, {"doc_type": "x"}]},
        ]
        for where in bad_wheres:
            with self.subTest(where=where), self.assertRaises(VectorStoreError) as caught:
                query_vectors(fake_vector("q"), where)
            self.assertEqual(caught.exception.code, "NO_COMPANY_SCOPE")

    def test_query_accepts_or_when_every_branch_has_company(self):
        self.store([make_chunk(0, company="C1", doc="D1")], activate=True)
        self.store([make_chunk(0, company="C2", doc="D2")], activate=True)
        where = {"$or": [{"company_id": "C1"}, {"company_id": "C2"}]}
        self.assertEqual(len(query_vectors(fake_vector("q"), where, top_k=5)), 2)

    def test_query_rejects_bad_arguments(self):
        where = build_where_filter(["C1"])
        for kwargs in ({"top_k": 0}, {"top_k": -1}, {"top_k": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(VectorStoreError) as caught:
                query_vectors(fake_vector("q"), where, **kwargs)
            self.assertEqual(caught.exception.code, "QUERY_FAILED")
        with self.assertRaises(VectorStoreError):
            query_vectors([], where)

    def test_wrong_dimension_query_fails_cleanly(self):
        self.store([make_chunk(0)], activate=True)
        with self.assertRaises(VectorStoreError) as caught:
            query_vectors([0.1, 0.2], build_where_filter(["C1"]))
        self.assertEqual(caught.exception.code, "QUERY_FAILED")

    def test_company_and_doc_type_isolation(self):
        self.store([make_chunk(0, company="C1", doc="D1", doc_type="manual")], activate=True)
        self.store([make_chunk(0, company="C2", doc="D2", doc_type="manual")], activate=True)
        self.store([make_chunk(0, company="C1", doc="D3", doc_type="guide")], activate=True)
        q = fake_vector("anything")
        only_c1 = query_vectors(q, build_where_filter(["C1"]), top_k=10)
        self.assertEqual({h.metadata["company_id"] for h in only_c1}, {"C1"})
        self.assertEqual(len(only_c1), 2)
        typed = query_vectors(q, build_where_filter(["C1", "C2"], doc_types=["guide"]), top_k=10)
        self.assertEqual([h.metadata["document_id"] for h in typed], ["D3"])
        one_doc = query_vectors(q, build_where_filter(["C1", "C2"], document_ids=["D2"]), top_k=10)
        self.assertEqual([h.metadata["company_id"] for h in one_doc], ["C2"])

    def test_top_k_larger_than_data_and_empty_store(self):
        self.assertEqual(query_vectors(fake_vector("q"), build_where_filter(["C1"])), [])
        self.store([make_chunk(0)], activate=True)
        self.assertEqual(len(query_vectors(fake_vector("q"), build_where_filter(["C1"]), top_k=50)), 1)


class ActivateDeleteList(EmbeddingTestCase):
    def test_new_version_replaces_old_one(self):
        self.store([make_chunk(i, ver="V1") for i in range(3)], activate=True)
        self.store([make_chunk(i, ver="V2") for i in range(4)])
        where = build_where_filter(["C1"])
        q = fake_vector("q")
        self.assertEqual({h.metadata["version_id"] for h in query_vectors(q, where, top_k=20)}, {"V1"})  # 새 버전 준비 중

        self.assertEqual(activate_version("D1", "V2"), 4)
        hits = query_vectors(q, where, top_k=20)
        self.assertEqual({h.metadata["version_id"] for h in hits}, {"V2"})
        self.assertEqual(len(hits), 4)
        self.assertEqual(get_collection().count(), 7)  # 이전 버전은 지워지지 않고 비공개로만 바뀐다

    def test_activate_keeps_other_documents_untouched(self):
        self.store([make_chunk(0, doc="D1")], activate=True)
        self.store([make_chunk(0, doc="D2")], activate=True)
        self.store([make_chunk(0, doc="D1", ver="V2")])
        activate_version("D1", "V2")
        active = {h.metadata["document_id"] + h.metadata["version_id"]
                  for h in query_vectors(fake_vector("q"), build_where_filter(["C1"]), top_k=10)}
        self.assertEqual(active, {"D2V1", "D1V2"})

    def test_previous_version_can_be_reactivated_for_rollback(self):
        """DB 계약: 게시 실패 시 이전 포인터로 복구하면서 activate_version으로 이전 버전을 다시 공개한다."""
        self.store([make_chunk(i, ver="V1") for i in range(2)], activate=True)
        self.store([make_chunk(i, ver="V2") for i in range(3)])
        activate_version("D1", "V2")
        self.assertEqual({h.metadata["version_id"] for h in get_document_chunks("D1")}, {"V2"})
        self.assertEqual(activate_version("D1", "V1"), 2)  # 롤백
        hits = query_vectors(fake_vector("q"), build_where_filter(["C1"]), top_k=10)
        self.assertEqual({h.metadata["version_id"] for h in hits}, {"V1"})
        self.assertEqual(len(hits), 2)
        self.assertEqual(get_collection().count(), 5)  # 아무것도 지워지지 않는다

    def test_activate_is_idempotent_and_missing_version(self):
        self.store([make_chunk(0)])
        self.assertEqual(activate_version("D1", "V1"), 1)
        self.assertEqual(activate_version("D1", "V1"), 1)
        for doc, ver in (("D1", "V9"), ("D9", "V1")):
            with self.subTest(doc=doc, ver=ver), self.assertRaises(VectorStoreError) as caught:
                activate_version(doc, ver)
            self.assertEqual(caught.exception.code, "VERSION_NOT_FOUND")

    def test_activate_preserves_other_metadata(self):
        self.store([make_chunk(0)], activate=True)
        meta = get_collection().get(ids=["D1:V1:00000"], include=["metadatas"])["metadatas"][0]
        self.assertEqual((meta["company_id"], meta["location_label"], meta["embedding_model"], meta["is_active"]),
                         ("C1", "p.1", "fake-model", True))

    def test_delete_by_version_and_by_document(self):
        self.store([make_chunk(i, ver="V1") for i in range(3)])
        self.store([make_chunk(i, ver="V2") for i in range(2)])
        self.store([make_chunk(0, doc="D2")])
        self.assertEqual(delete_vectors("D1", "V1"), 3)
        self.assertEqual(get_collection().count(), 3)
        self.assertEqual(delete_vectors("D1"), 2)
        self.assertEqual(get_collection().count(), 1)
        self.assertEqual(delete_vectors("D1"), 0)  # 지울 것이 없어도 오류 없이 0
        self.assertEqual(get_version_chunk_ids("D2", "V1"), ["D2:V1:00000"])

    def test_chunk_ids_sorted_numerically(self):
        chunks = [make_chunk(i) for i in range(12)]
        self.store(list(reversed(chunks)))
        self.assertEqual(get_version_chunk_ids("D1", "V1"), [c["chunk_id"] for c in chunks])
        self.assertEqual(get_version_chunk_ids("D1", "V9"), [])

    def test_document_chunks_in_order_with_text_and_no_score(self):
        chunks = [make_chunk(i) for i in range(11)]
        self.store(list(reversed(chunks)), activate=True)
        got = get_document_chunks("D1")
        self.assertEqual([h.metadata["chunk_index"] for h in got], list(range(11)))
        self.assertEqual(got[3].text, chunks[3]["text"])
        self.assertTrue(all(h.score is None for h in got))

    def test_document_chunks_active_only_rules(self):
        self.store([make_chunk(i, ver="V1") for i in range(2)], activate=True)
        self.store([make_chunk(i, ver="V2") for i in range(3)])
        self.assertEqual({h.metadata["version_id"] for h in get_document_chunks("D1")}, {"V1"})  # 공개된 버전만
        self.assertEqual(get_document_chunks("D1", "V2"), [])  # 공개 전 버전은 기본으로 안 나온다
        self.assertEqual(len(get_document_chunks("D1", "V2", active_only=False)), 3)
        self.assertEqual(len(get_document_chunks("D1", active_only=False)), 5)
        self.assertEqual(get_document_chunks("NOPE"), [])


# --- 질문 임베딩·상태 점검 --------------------------------------------------

class QueryAndHealth(EmbeddingTestCase):
    def test_embed_query_validation_and_same_model(self):
        for bad, code in (("", "EMPTY_QUERY"), ("   ", "EMPTY_QUERY"), (None, "EMPTY_QUERY"), ("가" * 2001, "QUERY_TOO_LONG")):
            with self.subTest(bad=bad), self.assertRaises(EmbeddingError) as caught:
                embed_query(bad)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.model.calls, [])
        self.assertEqual(embed_query("  질문  "), fake_vector("질문"))
        self.assertEqual(len(embed_query("가" * 2000)), DIM)

    def test_embed_query_retries_transient_errors(self):
        self.model.plan = [RateLimitError("x")]
        self.assertEqual(len(embed_query("q")), DIM)
        self.assertEqual(self.sleeps, [1.0])

    def test_embed_query_rejects_bad_vector(self):
        self.model.override = lambda v: [[0.0] * DIM]
        with self.assertRaises(EmbeddingError) as caught:
            embed_query("q")
        self.assertEqual(caught.exception.code, "INVALID_VECTOR")

    def test_embedding_health_ok_and_cached(self):
        first = check_embedding_health()
        self.assertEqual((first["ok"], first["model"], first["dim"]), (True, "fake-model", DIM))
        second = check_embedding_health()
        self.assertTrue(second["ok"])
        self.assertEqual(len(self.model.calls), 1)  # 두 번째는 캐시
        check_embedding_health(cache_seconds=0)
        self.assertEqual(len(self.model.calls), 2)

    def test_embedding_health_reports_failure_without_secret(self):
        self.model.plan = [AuthenticationError("bad key sk-proj-SECRETSECRET1234")]
        result = check_embedding_health()
        self.assertFalse(result["ok"])
        self.assertIn("AUTH_ERROR", result["detail"])
        self.assertNotIn("SECRETSECRET1234", result["detail"])
        self.model.plan = []
        self.assertTrue(check_embedding_health()["ok"])  # 실패는 캐시하지 않는다

    def test_embedding_health_timeout(self):
        import threading
        release = threading.Event()

        class Slow:
            def embed_documents(self, texts):
                release.wait(2)
                return [fake_vector(t) for t in texts]

        self.model = Slow()
        try:
            result = check_embedding_health(timeout=0.05)
        finally:
            release.set()
        self.assertFalse(result["ok"])
        self.assertIn("응답이 없습니다", result["detail"])

    def test_embedding_health_missing_key(self):
        with patch.object(embedder, "get_embedding_model", REAL_GET_MODEL), patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            result = check_embedding_health(cache_seconds=0)
        self.assertEqual((result["ok"], result["detail"].split(":")[0]), (False, "NO_API_KEY"))

    def test_vector_store_health_counts(self):
        empty = check_vector_store_health()
        self.assertEqual((empty["ok"], empty["total_vectors"], empty["active_vectors"]), (True, 0, 0))
        self.assertEqual(empty["collection"], "chunks__fake-model")
        self.store([make_chunk(i) for i in range(3)])
        self.store([make_chunk(0, doc="D2")], activate=True)
        counted = check_vector_store_health()
        self.assertEqual((counted["total_vectors"], counted["active_vectors"]), (4, 1))

    def test_vector_store_health_when_store_unavailable(self):
        with patch.object(vector_store, "get_collection", side_effect=VectorStoreError("STORE_UNAVAILABLE", "열 수 없음")):
            result = check_vector_store_health()
        self.assertFalse(result["ok"])
        self.assertIn("STORE_UNAVAILABLE", result["detail"])


@unittest.skipIf(team_config is None, "common(이성민)이 없는 환경")
class TeamCommonIntegration(EmbeddingTestCase):
    """이성민의 common.config·common.schemas와 맞물려 동작하는지 확인한다."""

    def real_chunk(self, i, *, doc="D1", ver="V1", company="C1", text=None):
        return Chunk(
            chunk_id=make_chunk_id(ver, i), document_id=doc, version_id=ver, company_id=company,
            company_name=f"회사{company}", doc_type="manual", file_name=f"{doc}.pdf",
            section_no=i + 1, location_label=f"p.{i + 1}", chunk_index=i,
            text=text if text is not None else f"{doc} {ver} 본문 {i}",
        )

    def test_settings_come_from_common_config(self):
        settings = models.load_embedding_settings()
        self.assertEqual(settings.model_name, "fake-model")
        self.assertEqual(settings.openai_api_key, "sk-test-key-123456")
        self.assertEqual(settings.chroma_path, os.path.join(self._tmp.name, "chroma"))
        self.assertIs(team_config.get_settings().chroma_db_path.is_absolute(), True)

    def test_values_really_come_from_hyundap_common_config_not_the_env_fallback(self):
        """import 경로가 틀리면 조용히 환경변수로 대체되어 알아채기 어렵다. 환경변수와 다른 값을 줘서 구분한다."""
        from pathlib import Path
        from types import SimpleNamespace

        fake = SimpleNamespace(openai_api_key="sk-from-team-config", openai_embedding_model="team-config-model",
                               chroma_db_path=Path(self._tmp.name) / "team-chroma")
        with patch.object(team_config, "get_settings", lambda: fake):
            settings = models.load_embedding_settings()
        self.assertEqual(settings.model_name, "team-config-model")        # 환경변수(fake-model)가 아님
        self.assertEqual(settings.openai_api_key, "sk-from-team-config")  # 환경변수(sk-test-key-...)가 아님
        self.assertEqual(settings.chroma_path, str(Path(self._tmp.name) / "team-chroma"))

    def test_only_one_copy_of_config_module_is_used(self):
        """common.config와 hyundap.common.config가 둘 다 로드되면 설정 저장소가 둘로 갈라진다(이슈 #131의 원인)."""
        import sys
        models.load_embedding_settings()
        self.assertIn("hyundap.common.config", sys.modules)
        self.assertNotIn("common.config", sys.modules)

    def test_missing_api_key_in_common_config_raises_no_api_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            team_config.get_settings.cache_clear()
            self.assertIsNone(team_config.get_settings().openai_api_key)
            with self.assertRaises(EmbeddingError) as caught:
                REAL_GET_MODEL()
        self.assertEqual(caught.exception.code, "NO_API_KEY")

    def test_real_chunk_dataclass_flows_through_embed_store_and_search(self):
        chunks = [self.real_chunk(i) for i in range(4)]
        report = embed_and_store(chunks)
        self.assertEqual((report.status, report.stored), ("success", 4))
        activate_version("D1", "V1")
        hit = query_vectors(embed_query(chunks[1].text), build_where_filter(["C1"], doc_types=["manual"]))[0]
        self.assertEqual(hit.chunk_id, "V1:00001")
        self.assertEqual((hit.metadata["file_name"], hit.metadata["location_label"], hit.metadata["section_no"]),
                         ("D1.pdf", "p.2", 2))
        self.assertEqual(get_version_chunk_ids("D1", "V1"), [c.chunk_id for c in chunks])

    def test_tests_never_touch_the_real_data_folder(self):
        self.assertNotIn(str(team_config.PROJECT_ROOT / "data"), models.load_embedding_settings().chroma_path)

    def test_uuid_version_ids_make_chunk_ids_unique_across_documents(self):
        """DB 계약(DB_contract.md): version_id는 UUID이므로 `{version_id}:{index:05d}` 청크 ID는 전체에서 유일하다."""
        import uuid
        (d1, v1), (d2, v2) = [(str(uuid.uuid4()), str(uuid.uuid4())) for _ in range(2)]
        first = [self.real_chunk(i, doc=d1, ver=v1) for i in range(3)]
        second = [self.real_chunk(i, doc=d2, ver=v2) for i in range(3)]
        self.assertTrue(all(len(c.chunk_id) <= 64 for c in first + second))  # DB 청크 ID 길이 상한
        self.store(first)
        self.store(second)
        self.assertEqual(get_collection().count(), 6)

    def test_same_version_id_string_is_rejected_as_a_safety_net(self):
        """version_id가 겹치는 잘못된 입력이 와도 남의 청크를 덮어쓰지 않고 CHUNK_ID_CONFLICT로 멈춘다."""
        first = [self.real_chunk(0, doc="D1", ver="v1")]
        second = [self.real_chunk(0, doc="D2", ver="v1")]
        self.assertEqual(first[0].chunk_id, second[0].chunk_id)
        self.assertEqual(embed_and_store(first).status, "success")
        with self.assertRaises(VectorStoreError) as caught:
            embed_and_store(second)
        self.assertEqual(caught.exception.code, "CHUNK_ID_CONFLICT")
        self.assertEqual(get_collection().get(ids=[first[0].chunk_id])["metadatas"][0]["document_id"], "D1")


class ChatbotAndSearchContract(EmbeddingTestCase):
    """chatbot(소용우, PR #134)과 search(이준영)가 내 결과를 읽는 방식과 맞는지 확인한다."""

    # chatbot/summary.py가 hit.metadata[...]로 꼭 읽는 키 8개와 .get()으로 읽는 키 1개
    CHATBOT_REQUIRED_KEYS = ("document_id", "version_id", "company_id", "company_name",
                             "doc_type", "file_name", "location_label", "chunk_index")
    CHATBOT_OPTIONAL_KEYS = ("section_no",)

    def test_import_path_used_by_chatbot_summary(self):
        """chatbot은 함수 안에서 `from hyundap.embedding.vector_store import get_document_chunks`로 부른다(#133)."""
        from hyundap.embedding.vector_store import get_document_chunks as via_module
        import hyundap.embedding as package
        self.assertIs(via_module, package.get_document_chunks)

    def test_summary_path_gets_every_key_chatbot_reads(self):
        self.store([make_chunk(i) for i in range(5)], activate=True)
        hits = get_document_chunks("D1")  # chatbot은 document_id만 넘긴다(공개된 버전만)
        self.assertEqual(len(hits), 5)
        for hit in hits:
            for key in self.CHATBOT_REQUIRED_KEYS + self.CHATBOT_OPTIONAL_KEYS:
                self.assertIn(key, hit.metadata, key)
            self.assertIsInstance(hit.metadata["chunk_index"], int)
            self.assertIsInstance(hit.metadata["section_no"], int)
            for key in ("document_id", "version_id", "company_id", "doc_type", "file_name", "location_label"):
                self.assertIsInstance(hit.metadata[key], str)
                self.assertNotEqual(hit.metadata[key], "", key)  # 빈 version_id 등이 저장되지 않는다
            self.assertTrue(hit.chunk_id)
            self.assertTrue(hit.text)
            self.assertIsNone(hit.score)  # 요약 경로는 점수를 쓰지 않는다

    def test_summary_order_is_chunk_index_even_beyond_64_chunks(self):
        """chatbot은 앞 64개를 자르고 포함 범위를 계산할 때 순서를 직접 정렬하지 않고 이 함수의 순서를 믿는다."""
        chunks = [make_chunk(i) for i in range(130)]
        self.store(list(reversed(chunks)), activate=True)
        got = [h.metadata["chunk_index"] for h in get_document_chunks("D1")]
        self.assertEqual(got, list(range(130)))

    def test_search_path_gets_the_keys_search_uses_for_citations(self):
        """search는 hit.metadata의 section_no·location_label과 chunk_id·text·score를 읽는다."""
        self.store([make_chunk(i) for i in range(3)], activate=True)
        hit = query_vectors(embed_query("조각"), build_where_filter(["C1"]), top_k=3)[0]
        for key in ("section_no", "location_label", "document_id", "version_id", "company_id", "doc_type"):
            self.assertIn(key, hit.metadata, key)
        self.assertTrue(hit.chunk_id and hit.text)
        self.assertTrue(0.0 <= hit.score <= 1.0)

    def test_error_classes_are_recognised_by_chatbot_error_mapping(self):
        """chatbot.map_dependency_error는 예외를 import하지 않고 클래스 이름 문자열과 retryable 속성으로 판단한다."""
        self.assertEqual(EmbeddingError.__name__, "EmbeddingError")
        self.assertEqual(VectorStoreError.__name__, "VectorStoreError")
        self.assertIs(EmbeddingError("RATE_LIMIT", "m", retryable=True).retryable, True)
        self.assertIs(getattr(VectorStoreError("QUERY_FAILED", "m"), "retryable", False), False)


class PublicApi(unittest.TestCase):
    def test_public_names_match_spec(self):
        import hyundap.embedding as embedding
        functions = {
            "get_embedding_model", "build_embedding_text", "fit_to_token_limit", "build_chunk_metadata",
            "embed_batch_with_retry", "embed_chunks", "validate_embeddings", "embed_and_store", "embed_query",
            "check_embedding_health", "collection_name_for_model", "get_collection", "filter_new_chunks",
            "upsert_chunks", "build_where_filter", "query_vectors", "activate_version", "delete_vectors",
            "get_version_chunk_ids", "get_document_chunks", "check_vector_store_health",
        }
        classes = {"EmbeddedChunk", "EmbeddingFailure", "EmbeddingBatch", "EmbedStoreReport", "VectorHit",
                   "EmbeddingError", "VectorStoreError"}
        self.assertEqual(len(functions), 21)
        self.assertEqual(set(embedding.__all__), functions | classes)
        for name in functions:
            self.assertTrue(callable(getattr(embedding, name)), name)

    def test_error_classes_carry_code_and_retryable(self):
        err = EmbeddingError("X", "msg", retryable=True)
        self.assertEqual((err.code, err.message, err.retryable), ("X", "msg", True))
        self.assertFalse(EmbeddingError("X", "m").retryable)
        self.assertEqual(VectorStoreError("Y", "m").code, "Y")


if __name__ == "__main__":
    unittest.main()
