"""실제 검색·챗봇·요약 진입점과 UI 연결 규격 검사.

외부 DB·벡터 조회·LLM만 대체하며 실제 데이터 저장이나 AI 호출은 하지 않는다.
실행: 저장소 맨 위에서 python -m unittest hyundap.tests.test_ui_backend -v
"""
import unittest
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import patch
from types import SimpleNamespace

from hyundap.ui.backend_bindings import create_bindings, validated_session_to_actor
from hyundap.ui.services import BackendServices, UIServiceError, company_choices
from hyundap.embedding.models import VectorHit
from hyundap.auth.errors import AccessError
from hyundap.tests.test_ui_controller import ComponentKeys
from hyundap.ui.controller import UIController

SESSION = {
    "user_id": "fixture-user", "role": "company_admin", "company_id": "fixture-company",
    "display_name": "Fixture", "company_name": "Fixture Company",
    "allowed_company_ids": ["fixture-company"],
    "token": "fixture-only-" + "x" * 43,
    "session_id": "fixture-session",
}
DOCUMENT = {
    "document_id": "fixture-doc", "company_id": "fixture-company",
    "company_name": "Fixture Company", "doc_type": "policy",
    "publication_status": "published", "deleted_at": None,
    "active_version_id": "fixture-version", "version_no": 1,
    "version_status": "ready", "data_category": "operational", "file_name": "rules.pdf",
}
HIT = VectorHit("fixture-chunk", "Employees can take annual leave.", {
    "chunk_id": "fixture-chunk", "document_id": "fixture-doc",
    "version_id": "fixture-version", "company_id": "fixture-company",
    "company_name": "Fixture Company", "doc_type": "policy", "file_name": "rules.pdf",
    "is_active": True, "chunk_index": 0, "section_no": 1, "location_label": "p.1",
}, .91)

class FixtureAuth:
    """SPEC-AUTH-001의 공개 로그인·세션 결과만 반환하는 인증 대역."""

    async def login(self, username, password):
        return deepcopy(SESSION)
    async def validate_session(self, session):
        return deepcopy(SESSION)
    async def logout(self, session):
        pass

class BindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = BackendServices(create_bindings(authentication=FixtureAuth()))
        self.session = await self.service.login("fixture", "unused")

    def search_io(self, *, allowed_scope=None):
        """실제 검색의 외부 호출만 대체하고 등록한 대역은 검사 종료 시 해제한다."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch(
            "hyundap.search.filters._load_allowed_company_ids",
            **({"new": allowed_scope} if allowed_scope is not None else {"return_value": {"fixture-company"}}),
        ))
        stack.enter_context(patch("hyundap.search.search._embed_query", return_value=[.1, .2]))
        stack.enter_context(patch("hyundap.search.search._query_vectors", return_value=[deepcopy(HIT)]))
        stack.enter_context(patch("hyundap.search.search._load_document_rows", return_value=[deepcopy(DOCUMENT)]))
        return stack

    async def test_temporary_login_stays_temporary_for_all_roles(self):
        service = BackendServices(create_bindings())
        self.assertTrue(service.temporary_auth)
        self.assertFalse(service.demo)
        for account, role in [("employee", "employee"), ("manager", "company_admin"), ("admin", "platform_admin")]:
            session = await service.login(account, "demo1234")
            self.assertEqual(session["role"], role)
            self.assertNotIn("session_id", session)
            self.assertNotIn("_session_token", session)
            with patch("hyundap.ui.backend_bindings.require_function", side_effect=AssertionError("임시 계정이 실제 백엔드를 호출함")):
                with self.assertRaises(UIServiceError) as caught:
                    await service.search(session, "leave")
            self.assertEqual(caught.exception.code, "AUTH_BACKEND_PENDING")
            self.assertEqual((await service.validate_session(session))["user_id"], session["user_id"])
            await service.logout(session)

    async def test_actual_search_entrypoint_returns_ui_document(self):
        def allowed_scope(actor):
            self.assertEqual(actor, {
                "user_id": SESSION["user_id"], "company_id": SESSION["company_id"],
                "role": SESSION["role"], "session_id": SESSION["session_id"],
                "_session_token": SESSION["token"],
            })
            return {"fixture-company"}

        with self.search_io(allowed_scope=allowed_scope):
            response = await self.service.search(self.session, "leave", ["fixture-company"], ["policy"])
        self.assertEqual(response["results"][0]["document_id"], "fixture-doc")
        self.assertEqual(response["results"][0]["excerpt"], HIT.text)
        self.assertEqual(response["results"][0]["location_label"], "p.1")
        self.assertNotIn("_session_token", repr(response))
        self.assertNotIn(SESSION["token"], repr(response))

    async def test_actual_ask_entrypoint_renders_citations(self):
        with self.search_io(), patch("hyundap.chatbot.ask.generate_answer", return_value="Annual leave is available. [E1]"), patch("hyundap.auth.permissions.verify_documents_access") as verify:
            response = await self.service.ask(self.session, "Can I take leave?", ["fixture-company"], ["policy"])
        self.assertEqual(response["status"], "ANSWERED")
        self.assertEqual(response["answer"], "Annual leave is available. [1]")
        self.assertEqual(response["citations"][0]["version_id"], "fixture-version")
        self.assertEqual(response["document_ids"], ["fixture-doc"])
        self.assertIn("Fixture Company", response["citations_by_company"])
        verify.assert_called_once()
        self.assertEqual(verify.call_args.args[0]["session_id"], SESSION["session_id"])

    async def test_actual_summary_entrypoint_preserves_coverage(self):
        chain = SimpleNamespace(invoke=lambda values: "Annual leave is available. [E1]")
        with patch("hyundap.embedding.vector_store.get_document_chunks", return_value=[deepcopy(HIT)]), patch("hyundap.chatbot.summary.build_summary_chain", return_value=chain), patch("hyundap.auth.permissions.verify_documents_access") as verify:
            response = await self.service.summarize(self.session, "fixture-doc", "fixture-version")
        self.assertEqual(response["summary"], "Annual leave is available. [1]")
        self.assertTrue(response["is_full"])
        self.assertEqual(response["used_chunks"], 1)
        self.assertIn("p.1", response["included_range"])
        self.assertEqual(verify.call_count, 2)

    async def test_actual_search_rejects_forbidden_company(self):
        with patch("hyundap.search.filters._load_allowed_company_ids", return_value={"fixture-company"}), patch("hyundap.search.search._embed_query") as embed:
            with self.assertRaises(UIServiceError) as caught:
                await self.service.search(self.session, "leave", ["another-company"])
        self.assertEqual(caught.exception.code, "COMPANY_FORBIDDEN")
        embed.assert_not_called()

    async def test_inline_answer_wrapped_session_expiry_returns_login(self):
        parts = [ComponentKeys(prefix) for prefix in ("login", "search", "admin", "shell")]
        controller = UIController(self.service, *parts)
        ctx = controller.state("test")
        ctx.user = deepcopy(self.session)
        ctx.query, ctx.company, ctx.doc_type = "leave?", "fixture-company", "policy"
        ctx.results = [{"document_id": "fixture-doc", "version_id": "fixture-version"}]
        ctx.selected = deepcopy(ctx.results[0])
        final = {}
        # 실제 검색·챗봇의 래핑된 만료 오류가 자동 인라인 답변에서도
        # 로그인 화면 복귀 및 문서·답변·출처 초기화로 이어져야 한다.
        with patch("hyundap.search.filters._load_allowed_company_ids", side_effect=AccessError("SESSION_EXPIRED")):
            async for changes in controller.run_inline_answer(ctx, ctx.inline_generation):
                final.update(changes)
        self.assertIsNone(ctx.user)
        self.assertEqual(ctx.results, [])
        self.assertIsNone(ctx.selected)
        self.assertEqual(ctx.answer_citations, [])
        self.assertTrue(final["login.container"]["visible"])
        self.assertFalse(final["shell.container"]["visible"])
        self.assertFalse(final["search.answer"]["visible"])
        self.assertEqual(final["search.answer"]["value"], "")
        self.assertFalse(final["shell.modal"]["visible"])

    async def test_actor_accepts_public_auth_contract_without_private_fields(self):
        self.assertNotIn("auth_version", self.session)
        self.assertNotIn("_session_token", self.session)
        actor = validated_session_to_actor(self.session)
        self.assertEqual(set(actor), {"user_id", "role", "company_id", "session_id", "_session_token"})
        self.assertEqual(actor["_session_token"], SESSION["token"])
        self.assertEqual(actor["session_id"], SESSION["session_id"])

    async def test_actor_requires_nonempty_session_id_and_token(self):
        for field in ("session_id", "token"):
            for value in (None, ""):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(UIServiceError) as caught:
                        validated_session_to_actor({**SESSION, field: value})
                    self.assertEqual(caught.exception.code, "AUTH_BACKEND_PENDING")
            with self.subTest(field=field, value="필드 없음"):
                session = {key: value for key, value in SESSION.items() if key != field}
                with self.assertRaises(UIServiceError) as caught:
                    validated_session_to_actor(session)
                self.assertEqual(caught.exception.code, "AUTH_BACKEND_PENDING")

    async def test_actor_rejects_missing_identity_fields(self):
        for field in ("user_id", "company_id", "role"):
            with self.subTest(field=field):
                session = {key: value for key, value in SESSION.items() if key != field}
                with self.assertRaises(UIServiceError) as caught:
                    validated_session_to_actor(session)
                self.assertEqual(caught.exception.code, "INVALID_SESSION")

    async def test_actor_uses_public_token_over_legacy_private_fields(self):
        session = {**SESSION, "_session_token": "obsolete-private-token", "auth_version": 999}
        actor = validated_session_to_actor(session)
        self.assertEqual(actor["_session_token"], SESSION["token"])
        self.assertNotIn("auth_version", actor)
        self.assertNotIn("token", actor)
        self.assertNotIn("display_name", actor)
        self.assertEqual(session["_session_token"], "obsolete-private-token")

    async def test_backend_receives_token_from_latest_session_validation(self):
        refreshed = {**SESSION, "token": "refreshed-fixture-" + "y" * 43}

        async def validate_session(session):
            return deepcopy(refreshed)

        def allowed_scope(actor):
            self.assertEqual(actor["_session_token"], refreshed["token"])
            self.assertNotEqual(actor["_session_token"], self.session["token"])
            return {"fixture-company"}

        self.service.bindings.validate_session = validate_session
        with self.search_io(allowed_scope=allowed_scope):
            response = await self.service.search(self.session, "leave")
        self.assertTrue(response["results"])
        self.assertNotIn(refreshed["token"], repr(response))

    async def test_real_company_choices_do_not_use_demo_ids(self):
        self.assertEqual(company_choices(self.session), [("Fixture Company", "fixture-company")])
        user = {**self.session, "allowed_company_ids": ["fixture-company", "new-company"], "companies": [{"company_id":"new-company", "company_name":"New"}, {"company_id":"outside", "company_name":"Forbidden"}]}
        self.assertEqual(company_choices(user), [("Fixture Company", "fixture-company"), ("New", "new-company")])

    async def test_download_calls_authorized_original_service(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'original.bin'
        path.write_bytes(b'original')
        prepared = dict(path=path, file_name='original.txt', temporary=temporary)
        with patch('hyundap.db.download_service.prepare_original_download', return_value=prepared) as prepare:
            result = await self.service.download(self.session, 'fixture-doc', 'fixture-version')
        self.assertEqual(result['path'], prepared['path'])
        self.assertIs(result['temporary'], temporary)
        self.assertEqual(prepare.call_args.args[0]['session_id'], self.session['session_id'])

if __name__ == "__main__":
    unittest.main(verbosity=2)
