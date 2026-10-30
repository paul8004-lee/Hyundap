"""문서 상세·업로드 연결 검사. 외부 조회·저장 함수는 대체 객체로 검사한다.

실행: 저장소 맨 위에서 python -m unittest hyundap.tests.test_ui_backend_documents -v
"""
import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from hyundap.auth.errors import AccessError
from hyundap.db.errors import DbError
from hyundap.ui import backend_documents as documents
from hyundap.ui.backend_feedback import safe_upload_message
from hyundap.ui.services import UIServiceError


class DocumentAdapterTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.actor = {"user_id": "user", "_session_token": "private-token"}
        self.row = {
            "document_id": "document", "version_id": "version",
            "company_id": "company", "company_name": "Actual company",
            "file_name": "original.pdf", "doc_type": "manual", "version_no": 3, "size_bytes": 123,
        }
        self.hits = [
            {
                "chunk_id": f"version:{index:05d}", "text": f"Actual text {index} " * 150,
                "metadata": {
                    "chunk_id": f"version:{index:05d}", "document_id": "document",
                    "version_id": "version", "company_id": "company", "is_active": True,
                    "location_label": f"p.{index + 1}",
                },
            }
            for index in range(5)
        ]
        self.read = self.stack.enter_context(patch(
            "hyundap.db.documents_repo.get_document_detail",
            autospec=True, return_value=self.row,
        ))
        self.verify = self.stack.enter_context(patch(
            "hyundap.auth.permissions.verify_documents_access", autospec=True,
        ))
        self.chunks = self.stack.enter_context(patch(
            "hyundap.embedding.vector_store.get_document_chunks",
            autospec=True, return_value=self.hits,
        ))

    def get(self, version="version"):
        return documents.get_document(user=self.actor, document_id="document", version_id=version)

    def test_detail_uses_public_functions_and_actual_bounded_excerpts(self):
        ordered = Mock()
        ordered.attach_mock(self.read, "read")
        ordered.attach_mock(self.verify, "verify")
        ordered.attach_mock(self.chunks, "chunks")
        result = self.get()
        self.assertEqual(ordered.mock_calls, [
            call.read(self.actor, "document"),
            call.chunks("document", "version", active_only=True),
            call.verify(self.actor, [("document", "version")]),
        ])
        self.assertEqual(len(result["citations"]), 4)
        self.assertEqual(result["excerpt"], self.hits[0]["text"][:1200])
        self.assertEqual(result["citations"][3]["location_label"], "p.4")
        self.assertEqual(result["company_name"], "Actual company")
        self.assertEqual(result["version_id"], "version")
        self.assertIsNone(result["indexed_at"])
        self.assertEqual(result["size_bytes"], 123)
        self.assertNotIn("private-token", repr(result))

    def test_access_denied_before_body_read(self):
        self.read.side_effect = AccessError("ACCESS_DENIED")
        with self.assertRaises(AccessError):
            self.get()
        self.chunks.assert_not_called()

    def test_access_revoked_after_body_read(self):
        self.verify.side_effect = AccessError("STATE_CHANGED")
        with self.assertRaises(AccessError):
            self.get()
        self.assertEqual(self.verify.call_count, 1)
        self.chunks.assert_called_once()

    def test_old_version_rejected_after_permission_check(self):
        with self.assertRaises(UIServiceError) as caught:
            self.get("old")
        self.assertEqual(caught.exception.code, "DOCUMENT_CHANGED")
        self.read.assert_called_once_with(self.actor, "document")
        self.verify.assert_not_called()
        self.chunks.assert_not_called()

    def test_all_chunks_checked_including_fifth_not_displayed(self):
        for field in ("company_id", "document_id", "version_id"):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.hits)
                changed[4]["metadata"][field] = "other"
                self.chunks.return_value = changed
                with self.assertRaises(UIServiceError) as caught:
                    self.get()
                self.assertEqual(caught.exception.code, "DOCUMENT_CHANGED")

    def test_malformed_metadata_is_rejected(self):
        changed = copy.deepcopy(self.hits)
        changed[4]["metadata"] = "not a mapping"
        self.chunks.return_value = changed
        with self.assertRaises(UIServiceError) as caught:
            self.get()
        self.assertEqual(caught.exception.code, "INVALID_RESPONSE")

    def test_inactive_or_wrong_chunk_identity_is_rejected(self):
        for field, value in (("is_active", False), ("chunk_id", "wrong")):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.hits)
                changed[4]["metadata"][field] = value
                self.chunks.return_value = changed
                with self.assertRaises(UIServiceError) as caught:
                    self.get()
                self.assertEqual(caught.exception.code, "DOCUMENT_CHANGED")

    def test_duplicate_chunk_is_rejected(self):
        self.chunks.return_value = [self.hits[0], self.hits[0]]
        with self.assertRaises(UIServiceError) as caught:
            self.get()
        self.assertEqual(caught.exception.code, "INVALID_RESPONSE")

    def test_empty_body_does_not_invent_excerpt(self):
        self.chunks.return_value = []
        with self.assertRaises(UIServiceError) as caught:
            self.get()
        self.assertEqual(caught.exception.code, "DOCUMENT_UNAVAILABLE")

    def test_missing_document_does_not_read_chunks(self):
        self.read.side_effect = AccessError("STATE_CHANGED")
        with self.assertRaises(UIServiceError) as caught:
            self.get()
        self.assertEqual(caught.exception.code, "DOCUMENT_UNAVAILABLE")
        self.chunks.assert_not_called()


class UploadAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.file = Path(self.temporary.name) / "original.pdf"
        self.file.write_bytes(b"Temporary UI input; parsing is not called in this test.")
        self.patcher = patch("hyundap.db.upload_service.upload_document", autospec=True)
        self.submit = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.result = {
            "document_id": "document", "version_id": "version", "job_id": "job",
            "status": "success", "error_code": None, "recovery_required": False,
        }
        self.submit.return_value = self.result
        self.actor = {"company_id": "other", "company_name": "Wrong company", "_session_token": "secret"}

    def upload(self):
        return documents.upload_document(
            user=self.actor, file_path=str(self.file), company_id="target", doc_type="manual",
        )

    def test_exact_upload_call_and_published_success_mapping(self):
        result = self.upload()
        self.submit.assert_called_once_with(
            actor=self.actor, source_path=str(self.file),
            metadata={"company_id": "target", "doc_type": "manual", "data_category": "operational"},
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertIs(result["searchable"], True)
        self.assertEqual(result["stage"], "complete")
        self.assertEqual(result["file_name"], "original.pdf")
        self.assertNotIn("company_name", result)

    def test_failed_result_keeps_code_and_safe_message(self):
        self.result.update(status="failed", error_code="EMPTY_DOCUMENT")
        result = self.upload()
        self.assertEqual(result["status"], "failed")
        self.assertIs(result["searchable"], False)
        self.assertEqual(result["error_code"], "EMPTY_DOCUMENT")
        self.assertEqual(result["message"], safe_upload_message("EMPTY_DOCUMENT"))
        self.assertNotIn("stage", result)

    def test_recovery_state_preserved_without_secret_backend_fields(self):
        self.result.update(
            status="failed", error_code="RECOVERY_REQUIRED", recovery_required=True,
            message="db://secret", error_message="credential secret", storage_path="private/path",
            _session_token="secret",
        )
        result = self.upload()
        self.assertIs(result["recovery_required"], True)
        self.assertEqual(result["message"], safe_upload_message("RECOVERY_REQUIRED", True))
        self.assertNotIn("secret", repr(result))
        self.assertNotIn("private/path", repr(result))

    def test_known_company_name_only_for_matching_company(self):
        self.actor.update(company_id="target", company_name="Correct company")
        self.assertEqual(self.upload()["company_name"], "Correct company")

    def test_backend_exception_propagated_for_shared_feedback(self):
        self.submit.side_effect = DbError("DUPLICATE_FILE")
        with self.assertRaises(DbError) as caught:
            self.upload()
        self.assertEqual(caught.exception.code, "DUPLICATE_FILE")

    def test_empty_file_rejected_before_backend_mutation(self):
        self.file.write_bytes(b"")
        with self.assertRaises(UIServiceError) as caught:
            self.upload()
        self.assertEqual(caught.exception.code, "CORRUPTED_FILE")
        self.submit.assert_not_called()

    def test_unknown_or_contradictory_status_is_rejected(self):
        for change in (
            {"status": "unknown"}, {"recovery_required": True},
            {"error_code": "PROCESSING_FAILED"}, {"job_id": None},
        ):
            with self.subTest(change=change):
                self.submit.return_value = {**self.result, **change}
                with self.assertRaises(UIServiceError) as caught:
                    self.upload()
                self.assertEqual(caught.exception.code, "INVALID_RESPONSE")


class DependencyTests(unittest.TestCase):
    def test_missing_function_is_not_silently_replaced(self):
        with patch.object(documents.importlib, "import_module", return_value=SimpleNamespace()):
            with self.assertRaises(UIServiceError) as caught:
                documents._function("hyundap.db.documents_repo", "get_documents_with_active_version")
        self.assertEqual(caught.exception.code, "BACKEND_NOT_CONNECTED")

    def test_known_missing_dependency_has_install_guidance(self):
        missing = ModuleNotFoundError("missing", name="sqlalchemy")
        with patch.object(documents.importlib, "import_module", side_effect=missing):
            with self.assertRaises(UIServiceError) as caught:
                documents._function("hyundap.db.documents_repo", "get_documents_with_active_version")
        self.assertEqual(caught.exception.code, "DEPENDENCY_MISSING")

    def test_unrelated_import_error_is_not_hidden_as_optional_dependency(self):
        missing = ModuleNotFoundError("bug", name="unexpected_internal_typo")
        with patch.object(documents.importlib, "import_module", side_effect=missing):
            with self.assertRaises(ModuleNotFoundError):
                documents._function("hyundap.db.documents_repo", "get_documents_with_active_version")


if __name__ == "__main__":
    unittest.main(verbosity=2)
