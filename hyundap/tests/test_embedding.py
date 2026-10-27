"""embedding 폴더 테스트 (수업 범위판). OpenAI를 부르지 않고 가짜 모델과 임시 Chroma 폴더로 실행한다.

실행(저장소 맨 위 폴더에서):  python -m unittest hyundap.tests.test_embedding -v
"""
# hashlib: 글마다 항상 같은 가짜 벡터를 만들기 위해 사용(해시값을 숫자로 바꾼다)
import hashlib
import os
# tempfile: 테스트마다 임시 폴더를 만들어 실제 Chroma 저장 폴더를 건드리지 않게 한다
import tempfile
import unittest
# patch: 테스트하는 동안만 환경변수·함수를 임시로 바꿔 끼운다(끝나면 자동 복구)
from unittest.mock import patch

# 팀 공통 설정 모듈. get_settings()가 값을 저장해 두므로 테스트마다 비워야 한다
from hyundap.common import config as team_config
from hyundap.embedding import embedder, vector_store
from hyundap.embedding import (
    EmbeddingError, VectorStoreError, activate_version, build_chunk_metadata, build_embedding_text,
    build_where_filter, check_embedding_health, check_vector_store_health, collection_name_for_model,
    delete_vectors, embed_and_store, embed_chunks, embed_query, embed_texts, filter_new_chunks,
    fit_to_char_limit, get_document_chunks, get_version_chunk_ids, query_vectors, validate_embeddings,
)

# 가짜 벡터의 길이. 실제는 1536이지만 테스트는 8이면 충분하다
DIM = 8
# setUp에서 가짜로 바꾸기 전의 진짜 함수를 보관해 두었다가 NO_API_KEY 시험에 쓴다
REAL_GET_MODEL = embedder.get_embedding_model  # 가짜로 바꾸기 전의 진짜 함수


# 같은 글이면 같은 벡터, 다른 글이면 다른 벡터를 돌려주는 가짜 임베딩(OpenAI를 부르지 않는다). 0 벡터가 되지 않도록 0.01을 더한다
def fake_vector(text):
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [(digest[i] - 127.5) / 127.5 + 0.01 for i in range(DIM)]


# OpenAIEmbeddings를 흉내 내는 가짜 모델. fail=True면 오류를 던지고, override로 결과를 바꿔치기할 수 있다
class FakeModel:
    """embed_documents / embed_query를 흉내 낸다. fail이 True면 예외를 던진다."""

    def __init__(self, fail=False, override=None):
        self.fail = fail
        self.override = override
        self.calls = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            # 오류 문구에 API 키를 섞어 두었다. embedder가 이 키를 가리는지 확인하기 위함
            raise RuntimeError("boom sk-test-key-123456")
        vectors = [fake_vector(t) for t in texts]
        return self.override(vectors) if self.override else vectors

    def embed_query(self, text):
        return self.embed_documents([text])[0]


# 테스트용 청크(dict) 1개를 만든다. 문서·버전·회사·유형을 바꿔 가며 여러 상황을 만든다
def make_chunk(i, *, doc="D1", ver="V1", company="C1", text=None, doc_type="manual"):
    return {
        "chunk_id": f"{doc}:{ver}:{i:05d}", "document_id": doc, "version_id": ver, "company_id": company,
        "company_name": f"회사{company}", "doc_type": doc_type, "file_name": f"{doc}.pdf",
        "section_no": i + 1, "location_label": f"p.{i + 1}", "chunk_index": i,
        "text": text if text is not None else f"{doc} {ver} 본문 {i}번 조각",
    }


# 모든 테스트의 공통 준비. 테스트마다 임시 폴더·가짜 환경변수·가짜 모델을 세팅하고 끝나면 정리한다
class Base(unittest.TestCase):
    # 각 테스트가 시작되기 전에 자동으로 실행된다
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {
            # Chroma 저장 폴더를 임시 폴더로 바꾼다(실제 data/chroma_db 보호)
            "CHROMA_DB_PATH": os.path.join(tmp.name, "chroma"),
            "OPENAI_API_KEY": "sk-test-key-123456",
            "OPENAI_EMBEDDING_MODEL": "fake-model",
        })
        env.start()
        self.addCleanup(env.stop)
        # 진짜 .env 파일을 읽지 않게 막는다(내 PC의 실제 키·설정이 테스트에 섞이지 않도록)
        no_dotenv = patch.object(team_config, "load_dotenv", lambda *a, **k: None)
        no_dotenv.start()
        self.addCleanup(no_dotenv.stop)
        # get_settings()가 저장해 둔 값을 비워 위에서 바꾼 환경변수가 반영되게 한다
        team_config.get_settings.cache_clear()
        self.addCleanup(team_config.get_settings.cache_clear)
        self.model = FakeModel()
        # 진짜 OpenAI 모델 대신 가짜 모델을 쓰도록 바꿔 끼운다
        fake = patch.object(embedder, "get_embedding_model", lambda *a, **k: self.model)
        fake.start()
        self.addCleanup(fake.stop)
        # 이전 테스트에서 열어 둔 Chroma 저장소를 비워 테스트끼리 영향이 없게 한다
        vector_store._stores.clear()
        self.addCleanup(vector_store._stores.clear)

    # 청크를 저장하고 성공을 확인하는 도우미. activate=True면 공개(검색 가능)까지 한다
    def store(self, chunks, activate=False):
        report = embed_and_store(chunks)
        self.assertEqual(report.status, "success", report.failures)
        if activate:
            activate_version(chunks[0]["document_id"], chunks[0]["version_id"])
        return report


# 작은 함수 하나하나를 시험하는 그룹(저장소 없이 가능한 것들)
class Pieces(Base):
    # 컬렉션 이름 규칙: 모델 이름 앞에 chunks__, 허용되지 않는 문자는 -, 빈 이름은 default
    def test_collection_name(self):
        self.assertEqual(collection_name_for_model("text-embedding-3-small"), "chunks__text-embedding-3-small")
        self.assertEqual(collection_name_for_model("a b/c"), "chunks__a-b-c")
        self.assertEqual(collection_name_for_model(""), "chunks__default")

    # 본문 앞뒤 공백 제거, 빈 본문은 EMPTY_TEXT, 4000자 넘으면 잘림 표시
    def test_text_and_limit(self):
        self.assertEqual(build_embedding_text(make_chunk(0, text="  hi  ")), "hi")
        with self.assertRaises(EmbeddingError) as ctx:
            build_embedding_text(make_chunk(0, text="   "))
        self.assertEqual(ctx.exception.code, "EMPTY_TEXT")
        self.assertEqual(fit_to_char_limit("abc", 5), ("abc", False))
        self.assertEqual(fit_to_char_limit("abcdef", 5), ("abcde", True))

    # 메타데이터: 처음에는 비공개(is_active=False), 필수 값이 비면 MISSING_METADATA
    def test_metadata(self):
        meta = build_chunk_metadata(make_chunk(0), model_name="m")
        self.assertIs(meta["is_active"], False)
        self.assertEqual(meta["embedding_model"], "m")
        with self.assertRaises(EmbeddingError) as ctx:
            build_chunk_metadata(make_chunk(0, company=""), model_name="m")
        self.assertEqual(ctx.exception.code, "MISSING_METADATA")

    # 벡터 검증: 전부 0·길이 다름·NaN인 벡터의 위치를 찾아낸다
    def test_validate_embeddings(self):
        good = [1.0, 2.0]
        self.assertEqual(validate_embeddings([good, good]), [])
        self.assertEqual(validate_embeddings([good, [0.0, 0.0], [1.0], [float("nan"), 1.0]]), [1, 2, 3])
        self.assertEqual(validate_embeddings([]), [])

    # API 호출 실패 시 EMBED_FAILED이고, 오류 문구에 API 키가 노출되지 않는다
    def test_embed_texts_failure_masks_key(self):
        self.model.fail = True
        with self.assertRaises(EmbeddingError) as ctx:
            embed_texts(["a"])
        self.assertEqual(ctx.exception.code, "EMBED_FAILED")
        self.assertNotIn("sk-test-key-123456", ctx.exception.message)

    # 질문 임베딩: 정상은 벡터 길이 확인, 빈 질문은 EMPTY_QUERY, 2000자 초과는 QUERY_TOO_LONG
    def test_embed_query(self):
        self.assertEqual(len(embed_query(" 질문 ")), DIM)
        for bad, code in (("  ", "EMPTY_QUERY"), ("가" * 2001, "QUERY_TOO_LONG")):
            with self.assertRaises(EmbeddingError) as ctx:
                embed_query(bad)
            self.assertEqual(ctx.exception.code, code)

    # 한 청크가 실패(빈 본문)해도 나머지는 계속 처리되고 실패 목록에 담긴다
    def test_embed_chunks_collects_failures(self):
        batch = embed_chunks([make_chunk(0), make_chunk(1, text=" ")], batch_size=1)
        self.assertEqual(len(batch.items), 1)
        self.assertEqual(batch.failures[0].code, "EMPTY_TEXT")

    # 임베딩 상태 점검: 정상이면 ok=True, 모델이 실패하면 ok=False
    def test_health(self):
        self.assertTrue(check_embedding_health()["ok"])
        self.model.fail = True
        self.assertFalse(check_embedding_health()["ok"])


# 저장·공개·조회·삭제 흐름을 임시 Chroma로 시험하는 그룹
class StoreFlow(Base):
    # 핵심 흐름: 저장 → (공개 전에는 검색 안 됨) → 공개 → 같은 글로 검색하면 그 청크가 1등, 점수 1.0
    def test_store_activate_query(self):
        chunks = [make_chunk(i) for i in range(3)]
        report = self.store(chunks)
        self.assertEqual((report.stored, report.embedded, report.skipped_existing), (3, 3, 0))
        where = build_where_filter(["C1"])
        self.assertEqual(query_vectors(fake_vector("x"), where), [])  # 공개 전에는 안 보인다
        self.assertEqual(activate_version("D1", "V1"), 3)
        hits = query_vectors(fake_vector(chunks[1]["text"]), where, top_k=3)
        self.assertEqual(hits[0].chunk_id, chunks[1]["chunk_id"])
        self.assertAlmostEqual(hits[0].score, 1.0, places=3)
        self.assertTrue(all(0.0 <= h.score <= 1.0 for h in hits))

    # 같은 청크를 두 번 저장하면 두 번째는 건너뛴다(중복 저장·재임베딩 방지)
    def test_second_store_skips_existing(self):
        chunks = [make_chunk(i) for i in range(2)]
        self.store(chunks)
        report = embed_and_store(chunks)
        self.assertEqual((report.skipped_existing, report.stored, report.status), (2, 0, "success"))

    # 회사 필터: 허용된 회사의 청크만 나오고, 문서 유형이 안 맞으면 결과가 없다
    def test_company_filter_blocks_other_company(self):
        self.store([make_chunk(0, doc="D1", company="C1")], activate=True)
        self.store([make_chunk(0, doc="D2", company="C2")], activate=True)
        hits = query_vectors(fake_vector("D1 V1 본문 0번 조각"), build_where_filter(["C2"]), top_k=5)
        self.assertEqual({h.metadata["company_id"] for h in hits}, {"C2"})
        hits = query_vectors(fake_vector("x"), build_where_filter(["C1", "C2"], doc_types=["policy"]), top_k=5)
        self.assertEqual(hits, [])

    # 회사 조건이 없으면 NO_COMPANY_SCOPE로 거부(전체 검색 차단)
    def test_no_company_scope(self):
        with self.assertRaises(VectorStoreError) as ctx:
            build_where_filter([])
        self.assertEqual(ctx.exception.code, "NO_COMPANY_SCOPE")
        for bad in ({}, {"is_active": True}, {"$or": [{"company_id": "C1"}, {"is_active": True}]}):
            with self.assertRaises(VectorStoreError):
                query_vectors([1.0], bad)

    # 새 버전을 공개하면 이전 버전은 검색에서 빠지고 새 버전만 나온다
    def test_new_version_replaces_old(self):
        self.store([make_chunk(0, ver="V1")], activate=True)
        self.store([make_chunk(0, ver="V2")], activate=True)
        ids = {h.chunk_id for h in query_vectors(fake_vector("x"), build_where_filter(["C1"]), top_k=5)}
        self.assertEqual(ids, {"D1:V2:00000"})

    # 문서 청크 조회(챗봇 요약용): 저장 순서와 무관하게 chunk_index 순으로 나오고 score는 None
    def test_document_chunks_in_order(self):
        self.store([make_chunk(i) for i in (2, 0, 1)], activate=True)
        hits = get_document_chunks("D1")
        self.assertEqual([h.metadata["chunk_index"] for h in hits], [0, 1, 2])
        self.assertTrue(all(h.score is None for h in hits))
        self.assertEqual(len(get_version_chunk_ids("D1", "V1")), 3)

    # 공개 전 청크는 기본 조회에서 안 보이고, 버전을 지정하고 active_only=False를 주면 보인다
    def test_unpublished_hidden_unless_asked(self):
        self.store([make_chunk(0)])
        self.assertEqual(get_document_chunks("D1"), [])
        self.assertEqual(len(get_document_chunks("D1", "V1", active_only=False)), 1)

    # 삭제: 지운 개수를 돌려주고, 더 지울 것이 없으면 0
    def test_delete(self):
        self.store([make_chunk(i) for i in range(2)], activate=True)
        self.assertEqual(delete_vectors("D1", "V1"), 2)
        self.assertEqual(delete_vectors("D1"), 0)

    # 같은 chunk_id가 다른 회사로 있으면 COMPANY_MISMATCH, 같은 회사의 다른 문서·버전이면 CHUNK_ID_CONFLICT
    def test_conflicts(self):
        self.store([make_chunk(0)])
        clash = make_chunk(0, company="C2")
        clash["chunk_id"] = "D1:V1:00000"
        with self.assertRaises(VectorStoreError) as ctx:
            filter_new_chunks([clash])
        self.assertEqual(ctx.exception.code, "COMPANY_MISMATCH")
        other = make_chunk(0, doc="D9")
        other["chunk_id"] = "D1:V1:00000"
        with self.assertRaises(VectorStoreError) as ctx:
            filter_new_chunks([other])
        self.assertEqual(ctx.exception.code, "CHUNK_ID_CONFLICT")

    # 한 번에 넘긴 청크가 비었거나, 문서가 섞였거나, chunk_id가 겹치면 거부
    def test_scope_errors(self):
        for chunks, code in (
            ([], "EMPTY_CHUNKS"),
            ([make_chunk(0), make_chunk(1, doc="D2")], "MIXED_SCOPE"),
            ([make_chunk(0), make_chunk(0)], "DUPLICATE_CHUNK_ID"),
        ):
            with self.assertRaises(EmbeddingError) as ctx:
                embed_and_store(chunks)
            self.assertEqual(ctx.exception.code, code)

    # 일부만 실패하면 status=partial(이미 저장된 1개는 건너뜀, 새 저장 0)
    def test_partial_when_embedding_fails_for_some(self):
        self.store([make_chunk(0)])
        report = embed_and_store([make_chunk(0), make_chunk(1, text=" ")])
        self.assertEqual((report.status, report.skipped_existing, report.stored), ("partial", 1, 0))

    # API가 완전히 실패하면 status=failed, 저장 0
    def test_failed_when_api_down(self):
        self.model.fail = True
        report = embed_and_store([make_chunk(0)])
        self.assertEqual((report.status, report.stored), ("failed", 0))

    # 전부 0인 비정상 벡터는 INVALID_VECTOR로 기록하고 저장하지 않는다(나머지는 저장)
    def test_invalid_vector_not_stored(self):
        self.model.override = lambda vs: [[0.0] * DIM] + vs[1:]
        report = embed_and_store([make_chunk(0), make_chunk(1)])
        self.assertEqual((report.status, report.stored), ("partial", 1))
        self.assertEqual(report.failures[0].code, "INVALID_VECTOR")

    # 5000자 본문은 잘리고 보고서의 truncated_count가 1이 된다
    def test_long_text_truncated_count(self):
        report = embed_and_store([make_chunk(0, text="가" * 5000)])
        self.assertEqual(report.truncated_count, 1)

    # 저장소 상태 점검: 전체·공개 벡터 수와 컬렉션 이름 확인
    def test_health_store(self):
        self.store([make_chunk(0)], activate=True)
        health = check_vector_store_health()
        self.assertEqual((health["ok"], health["total_vectors"], health["active_vectors"]), (True, 1, 1))
        self.assertEqual(health["collection"], "chunks__fake-model")

    # API 키가 없으면 진짜 get_embedding_model이 NO_API_KEY로 멈춘다(가짜가 아닌 원래 함수로 확인)
    def test_no_api_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            team_config.get_settings.cache_clear()
            with self.assertRaises(EmbeddingError) as ctx:
                REAL_GET_MODEL()
        self.assertEqual(ctx.exception.code, "NO_API_KEY")


# ===========================================================================
# 추가 테스트: 공개 이름·팀 코드와의 약속(계약)·경계 상황
# ===========================================================================


class PublicContract(Base):
    """다른 폴더(db·search·chatbot·ui)가 기대하는 모양이 유지되는지 확인하는 그룹"""

    def test_public_names_exist_and_old_names_gone(self):
        # __all__에 적힌 이름이 모두 실제로 import 되고, 삭제한 옛 이름은 더 이상 없다
        import hyundap.embedding as package
        for name in package.__all__:
            self.assertTrue(hasattr(package, name), name)
        for old in ("embed_batch_with_retry", "fit_to_token_limit", "get_collection"):
            self.assertFalse(hasattr(package, old), old)

    def test_results_are_dataclasses(self):
        # db 코드는 is_dataclass/asdict로 보고서를 읽는다. dataclass가 아니면 업로드가 INVALID_INPUT으로 막힌다
        from dataclasses import asdict, is_dataclass
        report = embed_and_store([make_chunk(0)])
        self.assertTrue(is_dataclass(report))
        data = asdict(report)
        for key in ("document_id", "version_id", "company_id", "model_name", "total", "skipped_existing",
                    "embedded", "stored", "truncated_count", "failures", "status", "elapsed_sec"):
            self.assertIn(key, data)  # db가 읽는 12개 항목

    def test_vector_hit_positional_args(self):
        # 팀 테스트는 VectorHit("id", "text", {...}, 0.5)처럼 위치 인자로 만든다
        from hyundap.embedding import VectorHit
        hit = VectorHit("c1", "본문", {"company_id": "C1"}, 0.5)
        self.assertEqual((hit.chunk_id, hit.text, hit.score), ("c1", "본문", 0.5))
        self.assertIsNone(VectorHit("c1", "본문", {}).score)

    def test_error_fields(self):
        # search는 code·retryable을, 업로드 화면은 code를 읽는다
        from hyundap.embedding import EmbeddingError, VectorStoreError
        error = EmbeddingError("EMBED_FAILED", "x", retryable=True)
        self.assertEqual((error.code, error.message, error.retryable), ("EMBED_FAILED", "x", True))
        self.assertEqual(str(error), "EMBED_FAILED: x")
        self.assertEqual(VectorStoreError("QUERY_FAILED", "y").code, "QUERY_FAILED")

    def test_chatbot_import_path(self):
        # chatbot은 함수 안에서 from hyundap.embedding.vector_store import get_document_chunks 로 부른다
        from hyundap.embedding.vector_store import get_document_chunks as via_module
        import hyundap.embedding as package
        self.assertIs(via_module, package.get_document_chunks)

    def test_chunk_object_and_dict_both_work(self):
        # 청크가 dict여도 Chunk 객체(dataclass)여도 같은 결과여야 한다
        from hyundap.common.schemas import Chunk
        chunk_dict = make_chunk(0)
        chunk_obj = Chunk(**{k: v for k, v in chunk_dict.items()})
        self.assertEqual(build_embedding_text(chunk_obj), build_embedding_text(chunk_dict))
        meta_a = build_chunk_metadata(chunk_obj, model_name="m")
        meta_b = build_chunk_metadata(chunk_dict, model_name="m")
        meta_a.pop("embedded_at"), meta_b.pop("embedded_at")
        self.assertEqual(meta_a, meta_b)
        self.assertEqual(embed_and_store([chunk_obj]).status, "success")

    def test_settings_come_from_get_settings(self):
        # 모델 이름·키·저장 폴더는 팀 공통 설정(get_settings)에서만 읽는다(이슈 #141)
        from hyundap.embedding import models
        self.assertEqual(models.get_model_name(), "fake-model")
        self.assertEqual(models.get_api_key(), "sk-test-key-123456")
        self.assertTrue(models.get_chroma_path().endswith("chroma"))
        self.assertEqual(models.get_model_name("other"), "other")  # 인자가 있으면 인자 우선


class EdgeCases(Base):
    """경계 상황: 묶음 나누기, 여러 모델, 재시작, 잘못된 입력"""

    def test_batches_are_split(self):
        # 5개를 batch_size=2로 보내면 API가 2+2+1로 3번 불린다
        embed_and_store([make_chunk(i) for i in range(5)], batch_size=2)
        self.assertEqual([len(c) for c in self.model.calls], [2, 2, 1])

    def test_only_new_chunks_are_sent_to_api(self):
        # 이미 저장된 청크는 API로 다시 보내지 않는다(비용 절약)
        self.store([make_chunk(i) for i in range(2)])
        self.model.calls.clear()
        embed_and_store([make_chunk(i) for i in range(4)])
        self.assertEqual(sum(len(c) for c in self.model.calls), 2)

    def test_data_survives_reopen(self):
        # 프로그램을 다시 켠 것처럼 저장소 보관함을 비워도 저장된 데이터가 남아 있다
        self.store([make_chunk(0)], activate=True)
        vector_store._stores.clear()
        self.assertEqual(len(get_document_chunks("D1")), 1)

    def test_models_use_separate_collections(self):
        # 모델이 다르면 컬렉션이 달라 서로의 벡터가 섞이지 않는다
        self.store([make_chunk(0)])
        a = vector_store.get_vector_store("model-a")
        b = vector_store.get_vector_store("model-b")
        self.assertNotEqual(a._collection.name, b._collection.name)
        self.assertEqual(a._collection.count(), 0)

    def test_collection_uses_cosine(self):
        # 컬렉션은 cosine 거리 설정이어야 점수(1 − 거리)가 맞다
        store = vector_store.get_vector_store()
        self.assertEqual(store._collection.metadata.get("hnsw:space"), "cosine")

    def test_reactivate_is_idempotent(self):
        # 같은 버전을 두 번 공개해도 결과가 같다(재실행 안전)
        self.store([make_chunk(0), make_chunk(1)], activate=True)
        self.assertEqual(activate_version("D1", "V1"), 2)
        self.assertEqual(len(get_document_chunks("D1")), 2)

    def test_old_version_kept_but_hidden(self):
        # 새 버전을 공개해도 이전 버전은 지워지지 않고 비공개로만 바뀐다
        self.store([make_chunk(0, ver="V1")], activate=True)
        self.store([make_chunk(0, ver="V2")], activate=True)
        self.assertEqual(len(get_document_chunks("D1", "V1", active_only=False)), 1)
        self.assertEqual(get_document_chunks("D1", "V1"), [])

    def test_where_filter_shapes(self):
        # 조건 모양: 1개면 그대로, 여러 개면 $and, 값이 여러 개면 $in, 중복·빈 값은 제거
        self.assertEqual(build_where_filter(["C1"], active_only=False), {"company_id": "C1"})
        where = build_where_filter([" C1 ", "C1", "", "C2"], doc_types=["manual"], document_ids=["D1", "D2"])
        self.assertEqual(where["$and"][0], {"company_id": {"$in": ["C1", "C2"]}})
        self.assertEqual(where["$and"][1], {"doc_type": "manual"})
        self.assertEqual(where["$and"][2], {"document_id": {"$in": ["D1", "D2"]}})
        self.assertEqual(where["$and"][-1], {"is_active": True})

    def test_query_input_checks(self):
        # top_k는 1 이상의 정수, 질문 벡터는 비어 있으면 안 된다
        where = build_where_filter(["C1"])
        for bad_k in (0, -1, True, "3"):
            with self.assertRaises(VectorStoreError):
                query_vectors([1.0] * DIM, where, top_k=bad_k)
        with self.assertRaises(VectorStoreError):
            query_vectors([], where)

    def test_upsert_length_mismatch(self):
        # 청크·벡터·메타데이터 개수가 다르면 저장하지 않고 LENGTH_MISMATCH
        from hyundap.embedding import upsert_chunks
        with self.assertRaises(VectorStoreError) as ctx:
            upsert_chunks([make_chunk(0)], [], [])
        self.assertEqual(ctx.exception.code, "LENGTH_MISMATCH")
        self.assertEqual(upsert_chunks([], [], []), 0)

    def test_activate_unknown_version(self):
        # 없는 버전을 공개하려 하면 VERSION_NOT_FOUND
        with self.assertRaises(VectorStoreError) as ctx:
            activate_version("NOPE", "V0")
        self.assertEqual(ctx.exception.code, "VERSION_NOT_FOUND")

    def test_stored_text_and_metadata(self):
        # 저장된 본문은 원문 그대로(공백 포함, 임베딩에만 공백을 지운 글을 쓴다), 메타데이터에는 회사·유형·모델·비공개 상태가 들어 있다
        self.store([make_chunk(0, text="  원문  ")])
        hit = get_document_chunks("D1", "V1", active_only=False)[0]
        self.assertEqual(hit.text, "  원문  ")
        self.assertEqual(self.model.calls[0], ["원문"])
        for key in vector_store.METADATA_KEYS:
            self.assertIn(key, hit.metadata)
        self.assertEqual((hit.metadata["company_id"], hit.metadata["doc_type"], hit.metadata["is_active"]),
                         ("C1", "manual", False))

    def test_wrong_length_vector_rejected(self):
        # 다른 벡터와 길이가 다른 벡터는 INVALID_VECTOR로 걸러진다
        self.model.override = lambda vs: [vs[0][:4]] + vs[1:]
        report = embed_and_store([make_chunk(0), make_chunk(1), make_chunk(2)])
        self.assertEqual([f.code for f in report.failures], ["INVALID_VECTOR"])
        self.assertEqual((report.stored, report.status), (2, "partial"))

    def test_query_invalid_vector(self):
        # 질문 임베딩 결과가 전부 0이면 INVALID_VECTOR
        self.model.override = lambda vs: [[0.0] * DIM for _ in vs]
        with self.assertRaises(EmbeddingError) as ctx:
            embed_query("질문")
        self.assertEqual(ctx.exception.code, "INVALID_VECTOR")

    def test_no_api_key_does_not_break_read_paths(self):
        # 키가 없어도 저장된 문서 조회·삭제 같은 읽기 기능은 동작한다(임베딩이 필요한 기능만 막힌다)
        self.store([make_chunk(0)], activate=True)
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            team_config.get_settings.cache_clear()
            self.assertEqual(len(get_document_chunks("D1")), 1)
            self.assertEqual(delete_vectors("D1"), 1)

    def test_secret_not_in_failure_message(self):
        # 실패 기록(EmbeddingFailure)에도 API 키가 남지 않는다
        self.model.fail = True
        report = embed_and_store([make_chunk(0)])
        self.assertNotIn("sk-test-key-123456", report.failures[0].message)
        self.assertTrue(report.failures[0].retryable)

    def test_embedding_text_has_no_company_info(self):
        # 임베딩에 들어가는 글에는 회사명·문서 유형을 섞지 않는다(본문 그대로)
        embed_and_store([make_chunk(0, text="휴가 규정")])
        self.assertEqual(self.model.calls[0], ["휴가 규정"])


if __name__ == "__main__":
    unittest.main()
