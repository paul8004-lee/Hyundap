"""백엔드 준비 점검이 외부 호출 없이 안전한 상태만 반환하는지 검사한다.

실행: python -m unittest hyundap.tests.test_ui_backend_check -v
실제 설치 환경과 .env 대신 임시 폴더·환경변수·공개 함수 대역을 사용한다.
"""
import contextlib
import io
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hyundap.ui import backend_check as check


class BackendCheckTests(unittest.TestCase):
    """CLI의 버전 판정·비밀값 비노출·외부 호출 금지·종료 코드를 검증한다."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        root_patch = patch.object(check, "PROJECT_ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def write_requirements(self, text):
        """실제 저장소 설치 목록을 읽지 않도록 임시 설치 목록을 만든다."""
        (self.root / "requirements.txt").write_text(text, encoding="utf-8")

    def test_package_versions_distinguish_exact_mismatch_and_missing(self):
        """extras가 붙은 패키지도 조회하며 미설치와 버전 불일치를 구분한다."""
        self.write_requirements(
            "gradio==6.30.0 # 화면\nSQLAlchemy==2.0.55\nPyMySQL[rsa]==1.1.2\n"
        )

        def installed_version(package):
            if package == "PyMySQL":
                raise check.metadata.PackageNotFoundError(package)
            return {"gradio": "6.30.0", "SQLAlchemy": "2.1.6"}[package]

        with patch.object(check.metadata, "version", side_effect=installed_version) as version:
            rows = check._package_status()
        self.assertEqual(
            {row["name"]: row["status"] for row in rows},
            {"package:gradio": "ok", "package:SQLAlchemy": "mismatch", "package:PyMySQL": "missing"},
        )
        version.assert_any_call("PyMySQL")

    def test_invalid_package_metadata_does_not_expose_raw_values(self):
        """패키지 메타정보와 조회 오류에 섞인 원문을 결과에 포함하지 않는다."""
        self.write_requirements("invalid==1.0\nbroken==1.0\n")
        secret = "fixture-private/path"
        with patch.object(check.metadata, "version", side_effect=[secret, RuntimeError(secret)]):
            rows = check._package_status()
        self.assertEqual([row["status"] for row in rows], ["error", "error"])
        self.assertNotIn(secret, str(rows))

    def test_missing_empty_or_unpinned_requirements_report_errors(self):
        """설치 기준이 없거나 고정 버전이 아니면 준비 완료로 처리하지 않는다."""
        rows = check._package_status()
        self.assertEqual(rows[0]["status"], "error")
        for content in ("# 비어 있는 목록\n", "package>=1.0\n"):
            with self.subTest(content=content):
                self.write_requirements(content)
                with patch.object(check.metadata, "version") as version:
                    rows = check._package_status()
                self.assertEqual(rows[0]["status"], "error")
                version.assert_not_called()

    def test_environment_precedence_and_values_remain_private(self):
        """빈 프로세스 값도 .env보다 우선하며 점검이 환경이나 파일을 바꾸지 않는다."""
        content = (
            "DATABASE_URL=fixture-db-secret\n"
            "OPENAI_API_KEY=fixture-file-key\n"
            "OPENAI_CHAT_MODEL=fixture-model\n"
        )
        env_path = self.root / ".env"
        env_path.write_text(content, encoding="utf-8")
        environment = {"DATABASE_URL": "", "OPENAI_API_KEY": "fixture-process-key"}
        with patch.dict(os.environ, environment, clear=True):
            before = dict(os.environ)
            rows = check._environment_status()
            self.assertEqual(dict(os.environ), before)
        statuses = {row["name"]: row["status"] for row in rows}
        self.assertEqual(statuses["DATABASE_URL"], "missing")
        self.assertEqual(statuses["OPENAI_API_KEY"], "ok")
        self.assertEqual(statuses["OPENAI_CHAT_MODEL"], "ok")
        self.assertEqual(statuses["CHROMA_DB_PATH"], "default")
        for secret in ("fixture-db-secret", "fixture-file-key", "fixture-process-key", "fixture-model"):
            self.assertNotIn(secret, str(rows))
        self.assertEqual(env_path.read_text(encoding="utf-8"), content)

    def test_env_parser_failure_hides_output_and_keeps_process_settings(self):
        """환경 파일 파싱이 실패해도 원문을 숨기고 프로세스 설정으로 점검한다."""
        (self.root / ".env").write_text("invalid fixture", encoding="utf-8")
        secret = "fixture-parser-secret"

        def fail_parse(*args, **kwargs):
            print(secret)
            print(secret, file=check.sys.stderr)
            raise ValueError(secret)

        output, errors = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-process-key"}, clear=True),
            patch("dotenv.dotenv_values", side_effect=fail_parse),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            rows = check._environment_status()
        statuses = {row["name"]: row["status"] for row in rows}
        self.assertEqual(statuses[".env"], "error")
        self.assertEqual(statuses["OPENAI_API_KEY"], "ok")
        self.assertEqual(statuses["DATABASE_URL"], "missing")
        self.assertNotIn(secret, str(rows) + output.getvalue() + errors.getvalue())

    def test_function_import_errors_are_safe_and_restore_bytecode_setting(self):
        """모듈 누락·비함수·import 오류를 구분하고 전역 bytecode 설정을 복원한다."""
        targets = (
            ("누락", "fixture.missing", "run"),
            ("비함수", "fixture.stub", "run"),
            ("오류", "fixture.error", "run"),
        )
        secret = "fixture-import-secret"
        for prior in (False, True):
            with self.subTest(prior=prior):
                output, errors = io.StringIO(), io.StringIO()
                results = [ImportError(secret), SimpleNamespace(run=None), RuntimeError(secret)]
                with (
                    patch.object(check, "PUBLIC_FUNCTIONS", targets),
                    patch.object(check.sys, "dont_write_bytecode", prior),
                    patch.object(check.importlib, "import_module", side_effect=results),
                    contextlib.redirect_stdout(output),
                    contextlib.redirect_stderr(errors),
                ):
                    rows = check._function_status()
                    self.assertEqual(check.sys.dont_write_bytecode, prior)
                self.assertEqual([row["status"] for row in rows], ["missing", "missing", "error"])
                self.assertNotIn(secret, str(rows) + output.getvalue() + errors.getvalue())

    def test_collect_checks_imports_without_calling_business_functions_or_network(self):
        """공개 점검은 함수 존재만 확인하고 DB·LLM·업로드·네트워크를 호출하지 않는다."""
        self.write_requirements("fixture-package==1.0\n")
        modules, functions = {}, []
        for _, module_name, function_name in check.PUBLIC_FUNCTIONS:
            module = modules.setdefault(module_name, SimpleNamespace())
            function = Mock(side_effect=AssertionError("업무 함수 호출 금지"))
            setattr(module, function_name, function)
            functions.append(function)

        def import_only(module_name):
            self.assertTrue(check.sys.dont_write_bytecode)
            return modules[module_name]

        with (
            patch.dict(os.environ, {"DATABASE_URL": "fixture-db", "OPENAI_API_KEY": "fixture-key"}, clear=True),
            patch.object(check.metadata, "version", return_value="1.0"),
            patch.object(check.importlib, "import_module", side_effect=import_only) as importer,
            patch.object(check.sys, "dont_write_bytecode", False),
            patch.object(socket, "create_connection", side_effect=AssertionError("외부 접속 금지")) as connect,
            patch.object(socket.socket, "connect", side_effect=AssertionError("외부 접속 금지")) as socket_connect,
        ):
            before = dict(os.environ)
            rows = check.collect_backend_status()
            self.assertFalse(check.sys.dont_write_bytecode)
            self.assertEqual(dict(os.environ), before)
        statuses = {row["name"]: row["status"] for row in rows}
        for label, _, _ in check.PUBLIC_FUNCTIONS:
            self.assertEqual(statuses[label], "ok")
        self.assertEqual(statuses["인증"], "pending")
        self.assertEqual(statuses["원본 다운로드"], "pending")
        self.assertEqual(importer.call_count, len(check.PUBLIC_FUNCTIONS))
        for function in functions:
            function.assert_not_called()
        connect.assert_not_called()
        socket_connect.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [self.root / "requirements.txt"])

    def test_cli_exit_code_reports_unready_states(self):
        """CLI가 준비되지 않은 항목을 성공 종료 코드로 숨기지 않는지 확인한다."""
        for status in ("ok", "default", "pending", "missing", "mismatch", "error"):
            with self.subTest(status=status):
                output = io.StringIO()
                rows = [{"name": "점검 대상", "status": status, "message": "안전한 안내"}]
                with patch.object(check, "collect_backend_status", return_value=rows), contextlib.redirect_stdout(output):
                    result = check.print_backend_status()
                self.assertEqual(result, 0 if status in {"ok", "default"} else 1)
                self.assertIn(f"[{status.upper()}]", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
