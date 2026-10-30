"""실행: python -m unittest hyundap.ui.tests.test_upload_reliability -v"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import gradio as gr
from gradio.state_holder import SessionState
from starlette.requests import Request

from hyundap.ui import backend_documents as documents
from hyundap.ui.admin_page import render_upload_result, with_upload_input_state
from hyundap.ui.backend_bindings import create_bindings
from hyundap.ui.controller import UIController
from hyundap.ui.mock_services import MockServices
from hyundap.ui.services import BackendBindings, BackendServices, UIServiceError


def published(**extra):
    return dict(document_id="d", version_id="v", job_id="j", status="success",
                error_code=None, recovery_required=False, **extra)


class UploadResultTests(unittest.TestCase):
    def test_published_ids_only_keeps_success_and_does_not_invent_type(self):
        result = documents.normalize_upload_result(published())
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["searchable"])
        self.assertNotIn("doc_type", result)
        self.assertTrue(result["classification_incomplete"])
        self.assertIn("등록은 완료", render_upload_result(result))

    def test_classifier_exception_keeps_valid_fallback_without_ai_fields(self):
        result = documents.normalize_upload_result(published(
            doc_type="other", doc_type_fallback=True,
            doc_type_fallback_reason="CLASSIFY_FAILED", classify_status="failed"))
        self.assertEqual(result["doc_type"], "other")
        self.assertEqual(result["status"], "succeeded")
        self.assertNotIn("classification_incomplete", result)
        self.assertNotIn("doc_type_confidence", result)

    def test_invalid_ai_metadata_does_not_override_valid_stored_type(self):
        result = documents.normalize_upload_result(published(
            doc_type="policy", doc_type_fallback=False, doc_type_fallback_reason=None,
            auto_doc_type="manual", doc_type_confidence=float("nan"), company_match="yes", classify_status="success"))
        self.assertEqual(result["doc_type"], "policy")
        self.assertNotIn("auto_doc_type", result)
        self.assertTrue(result["classification_incomplete"])

    def test_untrusted_final_type_is_omitted_without_changing_saved_status(self):
        result = documents.normalize_upload_result(published(doc_type="<b>invalid-type</b>",
            doc_type_fallback=False, doc_type_fallback_reason=None))
        self.assertEqual(result["status"], "succeeded")
        self.assertNotIn("doc_type", result)
        self.assertNotIn("<b>", render_upload_result(result))

    def test_core_success_contract_stays_strict(self):
        for updates in ({"status": "queued"}, {"job_id": None}, {"recovery_required": True}, {"error_code": "TIMEOUT"}):
            with self.subTest(updates=updates), self.assertRaises(UIServiceError):
                documents.normalize_upload_result({**published(), **updates})

    def test_failure_preserves_identifiers_and_safe_error(self):
        result = documents.normalize_upload_result({**published(), "status": "failed", "error_code": "TIMEOUT", "message": "private raw traceback"})
        self.assertEqual((result["status"], result["version_id"], result["error_code"]), ("failed", "v", "TIMEOUT"))
        self.assertNotIn("private", repr(result))

    def test_retry_calls_existing_version_only(self):
        with patch("hyundap.db.upload_service.retry_upload", return_value=published()) as retry:
            result = documents.retry_upload_document(user={"session_id": "s"}, version_id="v")
        retry.assert_called_once_with(actor={"session_id": "s"}, version_id="v")
        self.assertEqual(result["status"], "succeeded")

    def test_retry_rejects_other_version_response(self):
        with patch("hyundap.db.upload_service.retry_upload", return_value={**published(), "version_id": "other"}), self.assertRaises(UIServiceError):
            documents.retry_upload_document(user={}, version_id="v")

    def test_automatic_upload_uses_new_metadata_without_forcing_other(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.pdf"
            path.write_bytes(b"synthetic validation input")
            with patch("hyundap.db.upload_service.upload_document", return_value=published()) as upload, patch.object(documents, "auto_classify_enabled", return_value=True):
                result = documents.upload_document(user={}, file_path=str(path), company_id="c", auto_classify=True)
            self.assertEqual(upload.call_args.kwargs["metadata"], {"company_id": "c", "data_category": "operational", "auto_classify": True})
            self.assertEqual(result["status"], "succeeded")

    def test_real_bindings_include_retry(self):
        self.assertIs(create_bindings().retry_upload, documents.retry_upload_document)


class RetryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_revalidates_session_and_maps_actor(self):
        checked = {"token": "t", "user_id": "u", "role": "company_admin", "company_id": "c", "allowed_company_ids": ["c"]}
        validate = AsyncMock(return_value=checked)
        retry = AsyncMock(return_value={"status": "succeeded", "version_id": "v"})
        service = BackendServices(BackendBindings(validate_session=validate, retry_upload=retry, user_factory=lambda value: {"user_id": value["user_id"]}))
        await service.retry_upload({"token": "t"}, "v")
        validate.assert_awaited_once()
        retry.assert_awaited_once_with(user={"user_id": "u"}, version_id="v")

    async def test_retry_expired_session_never_calls_backend(self):
        retry = AsyncMock()
        service = BackendServices(BackendBindings(retry_upload=retry))
        with self.assertRaises(UIServiceError):
            await service.retry_upload({}, "v")
        retry.assert_not_awaited()


class Keys(dict):
    def __init__(self, prefix):
        super().__init__()
        self.prefix = prefix

    def __missing__(self, key):
        self[key] = f"{self.prefix}.{key}"
        return self[key]


class UploadStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = MockServices()
        self.parts = [Keys(prefix) for prefix in ("login", "search", "admin", "shell")]
        self.parts[2]["retry_file"]
        self.parts[2]["retry"]
        self.controller = UIController(self.service, *self.parts)
        self.views = {}
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.paths = []
        for name in ("first.pdf", "second.pdf"):
            path = Path(self.folder.name) / name
            path.write_bytes(name.encode())
            self.paths.append(str(path))
        await self.action("login", "admin", "demo1234", "관리자")
        await self.action("upload_input", self.paths)

    async def action(self, action, *args, key="one"):
        patches = []
        async for patch in self.controller.run(action, key, *args):
            self.views.setdefault(key, {}).update(patch)
            patches.append(patch)
        return patches

    def delayed_upload(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def upload(session, path, company, doc_type):
            calls.append(Path(path).name)
            if len(calls) == 1:
                started.set()
                await release.wait()
            return {"status": "succeeded", "file_name": Path(path).name}
        self.service.upload = upload
        return started, release, calls

    async def test_menu_navigation_preserves_batch_and_restores_finished_result(self):
        started, release, calls = self.delayed_upload()
        task = asyncio.create_task(self.action("upload", self.paths, "hri", "manual"))
        await started.wait()
        await self.action("nav", "search")
        await self.action("nav", "upload")
        self.assertFalse(self.views["one"]["admin.submit"]["interactive"])
        release.set()
        await task
        await self.action("nav", "search")
        await self.action("nav", "upload")
        self.assertEqual(calls, ["first.pdf", "second.pdf"])
        self.assertIn("2/2건 처리", self.views["one"]["admin.result"])
        self.assertNotIn("hd-spinner", self.views["one"]["admin.feedback"])
        self.assertTrue(self.views["one"]["admin.submit"]["interactive"])

    async def test_single_upload_survives_filter_change(self):
        started, release, calls = self.delayed_upload()
        task = asyncio.create_task(self.action("upload", self.paths[:1], "hri", "manual"))
        await started.wait()
        await self.action("filter", "gte", "manual")
        release.set()
        await task
        self.assertEqual(calls, ["first.pdf"])
        self.assertIn("first.pdf", self.views["one"]["admin.result"])

    async def test_duplicate_click_does_not_submit_again(self):
        started, release, calls = self.delayed_upload()
        task = asyncio.create_task(self.action("upload", self.paths, "hri", "manual"))
        await started.wait()
        await self.action("upload", self.paths, "hri", "manual")
        self.assertEqual(calls, ["first.pdf"])
        release.set()
        await task
        self.assertEqual(len(calls), 2)

    async def test_queued_input_event_does_not_cancel_active_batch(self):
        started, release, calls = self.delayed_upload()
        task = asyncio.create_task(self.action("upload", self.paths, "hri", "manual"))
        await started.wait()
        self.assertEqual(await self.action("upload_input", []), [])
        release.set()
        await task
        self.assertEqual(len(calls), 2)
        await self.action("upload_input", [])
        self.assertEqual(self.controller.state("one").upload_results, [])

    async def test_logout_stops_remaining_calls_and_hides_late_result(self):
        started, release, calls = self.delayed_upload()
        task = asyncio.create_task(self.action("upload", self.paths, "hri", "manual"))
        await started.wait()
        await self.action("logout")
        release.set()
        await task
        self.assertEqual(calls, ["first.pdf"])
        self.assertEqual(self.views["one"]["admin.result"], "")
        self.assertEqual(self.controller.state("one").upload_results, [])

    async def test_old_upload_cannot_overwrite_a_new_login(self):
        started, release, calls = self.delayed_upload()
        task = asyncio.create_task(self.action("upload", self.paths, "hri", "manual"))
        await started.wait()
        await self.action("logout")
        await self.action("login", "manager", "demo1234", "관리자")
        release.set()
        await task
        self.assertEqual(calls, ["first.pdf"])
        self.assertEqual(self.controller.state("one").upload_results, [])
        self.assertEqual(self.views["one"]["admin.feedback"], "")

    async def test_interrupted_request_does_not_leave_loading_state(self):
        started, release, calls = self.delayed_upload()
        task = asyncio.create_task(self.action("upload", self.paths, "hri", "manual"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        ctx = self.controller.state("one")
        self.assertFalse(ctx.upload_busy)
        self.assertIn("일부 파일", ctx.upload_feedback)
        self.assertNotIn("hd-spinner", ctx.upload_feedback)
        self.assertEqual(calls, ["first.pdf"])

    async def test_another_session_upload_is_independent(self):
        started, release, calls = self.delayed_upload()
        task = asyncio.create_task(self.action("upload", self.paths[:1], "hri", "manual"))
        await started.wait()
        await self.action("login", "admin", "demo1234", "관리자", key="two")
        await self.action("upload", self.paths[1:], "hri", "manual", key="two")
        self.assertIn("second.pdf", self.views["two"]["admin.result"])
        release.set()
        await task
        self.assertNotIn("second.pdf", self.views["one"]["admin.result"])

    def failed_candidate(self, **extra):
        result = dict(status="failed", version_id="failed-v", document_id="d", job_id="j",
                      file_name="first.pdf", company_id="hri", error_code="TIMEOUT", **extra)
        ctx = self.controller.state("one")
        ctx.upload_results = [result]
        ctx.upload_total = 1
        return ctx

    async def test_retry_updates_same_card_and_uses_existing_version(self):
        self.failed_candidate()
        self.service.retry_upload = AsyncMock(return_value={"status": "succeeded", "version_id": "failed-v", "classification_incomplete": True})
        self.service.upload = AsyncMock()
        await self.action("retry_upload", "failed-v")
        self.service.retry_upload.assert_awaited_once()
        self.assertEqual(self.service.retry_upload.call_args.args[1], "failed-v")
        self.service.upload.assert_not_awaited()
        self.assertEqual(len(self.controller.state("one").upload_results), 1)
        self.assertIn("first.pdf", self.views["one"]["admin.result"])
        self.assertFalse(self.views["one"]["admin.retry"]["visible"])

    async def test_retry_rejects_unowned_version(self):
        self.failed_candidate()
        self.service.retry_upload = AsyncMock()
        await self.action("retry_upload", "another-version")
        self.service.retry_upload.assert_not_awaited()
        self.assertEqual(self.controller.state("one").upload_results[0]["version_id"], "failed-v")

    async def test_recovery_candidate_cannot_use_normal_retry(self):
        self.failed_candidate(recovery_required=True)
        self.service.retry_upload = AsyncMock()
        await self.action("retry_upload", "failed-v")
        self.service.retry_upload.assert_not_awaited()
        self.assertFalse(self.views["one"]["admin.retry"]["visible"])

    async def test_retry_transient_error_preserves_candidate_for_next_try(self):
        self.failed_candidate()
        self.service.retry_upload = AsyncMock(side_effect=UIServiceError("TIMEOUT", "잠시 후 다시 시도해 주세요."))
        await self.action("retry_upload", "failed-v")
        self.assertEqual(self.controller.state("one").upload_results[0]["version_id"], "failed-v")
        self.assertTrue(self.views["one"]["admin.retry"]["interactive"])

    async def test_session_expiry_clears_retry_and_does_not_call_next_file(self):
        self.service.upload = AsyncMock(side_effect=UIServiceError("SESSION_EXPIRED", "만료", "session_expired"))
        await self.action("upload", self.paths, "hri", "manual")
        self.service.upload.assert_awaited_once()
        self.assertIsNone(self.controller.state("one").user)
        self.assertFalse(self.views["one"]["admin.retry"]["visible"])
        self.assertIn("일부 파일", self.views["one"]["login.feedback"])

    async def test_real_input_updates_preserve_values_and_company_role(self):
        with gr.Blocks(analytics_enabled=False):
            refs = {"file": gr.File(), "company": gr.Dropdown(), "doc_type": gr.Dropdown()}
        ctx = self.controller.state("one")
        ctx.upload_busy = True
        result = with_upload_input_state(refs, ctx, {})
        self.assertTrue(all(not update["interactive"] for update in result.values()))
        self.assertTrue(all("value" not in update for update in result.values()))
        ctx.upload_busy = False
        ctx.user = {"role": "company_admin"}
        result = with_upload_input_state(refs, ctx, {refs["file"]: None})
        self.assertIsNone(result[refs["file"]]["value"])
        self.assertTrue(result[refs["file"]]["interactive"])
        self.assertFalse(result[refs["company"]]["interactive"])


class GradioUploadEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_preprocess_upload_and_retry_events_preserve_form(self):
        """실제 Gradio 파일 전처리·입출력 등록·재시도 클릭을 검사한다. DB·AI 대역 사용."""
        from hyundap.app import build_app

        service = MockServices()
        service.upload = AsyncMock(return_value={"status": "failed", "document_id": "d", "version_id": "v", "job_id": "j", "error_code": "TIMEOUT"})
        service.retry_upload = AsyncMock(return_value={"status": "succeeded", "document_id": "d", "version_id": "v", "job_id": "j"})
        screen = build_app(service=service)
        self.addCleanup(screen.close)
        state = SessionState(screen)
        controller = screen.hyundap_controller
        self.addCleanup(controller.discard, "event-test")
        request = gr.Request(request=Request({"type": "http", "headers": [], "query_string": b"",
            "path": "/", "method": "POST", "scheme": "http", "server": ("127.0.0.1", 7861)}))
        view = {}
        async def call(name, values):
            index, block = next((i, fn) for i, fn in screen.fns.items() if getattr(fn.fn, "__name__", "") == name)
            state[block.inputs[0]._id] = "event-test"
            iterator = None
            frames = []
            for _ in range(20):
                response = await screen.process_api(index, [None, *values] if iterator is None else [],
                    state=state, request=request, session_hash="event-test", event_id=name,
                    iterator=iterator, simple_format=True)
                frame = {component._id: update for component, update in zip(block.outputs, response["data"])
                         if update != {"__type__": "update"}}
                view.update(frame)
                frames.append(frame)
                if not response["is_generating"]:
                    return frames
                iterator = response["iterator"]
            self.fail("UI event did not finish")
        await call("on_login", ["admin", "demo1234", "관리자"])
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "event.pdf"
            source.write_bytes(b"synthetic form upload")
            cached = await screen.async_move_resource_to_block_cache(str(source))
            file_data = [{"path": cached, "orig_name": source.name, "meta": {"_type": "gradio.FileData"}}]
            await call("on_upload_input", [file_data])
            frames = await call("on_upload", [file_data, "hri", "manual"])
        file_id = controller.admin["file"]._id
        self.assertTrue(any(frame.get(file_id, {}).get("interactive") is False for frame in frames))
        self.assertTrue(view[file_id]["interactive"])
        self.assertNotIn("value", view[file_id])
        self.assertTrue(view[controller.admin["retry"]._id]["visible"])
        await call("on_retry_upload", ["v"])
        service.upload.assert_awaited_once()
        service.retry_upload.assert_awaited_once()
        self.assertFalse(view[controller.admin["retry"]._id]["visible"])


if __name__ == "__main__":
    unittest.main()
