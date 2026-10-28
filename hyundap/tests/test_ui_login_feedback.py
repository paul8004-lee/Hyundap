"""#187 로그인 오류 표시 회귀 검사. 실제 DB·계정·외부 서비스에 접속하지 않는다.

실제 인증 함수와 scrypt 검증을 사용하고 transaction/get_settings만 대체한다.
실행: python -m unittest hyundap.tests.test_ui_login_feedback -v
"""

from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from hyundap.auth import session_service as auth
from hyundap.auth.errors import AccessError
from hyundap.auth.passwords import hash_password
from hyundap.db.errors import DbError
from hyundap.ui.backend_bindings import create_bindings
from hyundap.ui.backend_feedback import to_feedback
from hyundap.ui.components import render_login_error
from hyundap.ui.controller import UIController
from hyundap.ui.services import BackendServices, UIServiceError


CREDENTIALS_MESSAGE = "아이디 또는 비밀번호가 올바르지 않습니다."
TEST_USERNAME = "Synthetic-Manager@example.invalid"
WRONG_PASSWORD = "synthetic-wrong-password"


class ComponentKeys(dict):
    """Gradio를 실행하지 않고 화면에 전달된 변경값을 이름으로 확인한다."""

    def __init__(self, prefix):
        super().__init__()
        self.prefix = prefix

    def __missing__(self, key):
        self[key] = f"{self.prefix}.{key}"
        return self[key]


@contextmanager
def database_user(user):
    database = MagicMock()
    database.execute.return_value.mappings.return_value.one_or_none.return_value = user

    @contextmanager
    def transaction():
        yield database

    with patch.object(auth, "transaction", transaction), patch.object(
        auth, "get_settings", return_value=SimpleNamespace(session_ttl_minutes=60)
    ):
        yield database


class LoginFeedbackTests(unittest.TestCase):
    def test_credentials_code_uses_fixed_input_error_message(self):
        error = AccessError("INVALID_CREDENTIALS")
        error.args = ("synthetic-private-token-and-password",)
        self.assertEqual(to_feedback(error), {
            "status": "input_error",
            "message": CREDENTIALS_MESSAGE,
            "error_code": "INVALID_CREDENTIALS",
        })

    def test_login_renderer_preserves_exact_message_and_field_error_marker(self):
        rendered = render_login_error("<script>private</script>", "INVALID_CREDENTIALS")
        self.assertIn(CREDENTIALS_MESSAGE, rendered)
        self.assertIn('data-login-invalid="credentials"', rendered)
        self.assertIn('role="alert"', rendered)
        self.assertNotIn("private", rendered)
        self.assertNotIn("<script>", rendered)

    def test_other_access_and_server_errors_remain_distinct(self):
        cases = (
            (AccessError("STATE_CHANGED"), "STATE_CHANGED", "failed"),
            (AccessError("ACCESS_DENIED"), "ACCESS_DENIED", "denied"),
            (AccessError("SESSION_EXPIRED"), "SESSION_EXPIRED", "session_expired"),
            (DbError("DB_UNAVAILABLE"), "DB_UNAVAILABLE", "failed"),
            (RuntimeError("private-stack"), "BACKEND_ERROR", "failed"),
        )
        for error, code, status in cases:
            with self.subTest(code=code):
                error.args = ("private-stack",)
                feedback = to_feedback(error)
                self.assertEqual((feedback["error_code"], feedback["status"]), (code, status))
                self.assertNotEqual(feedback["message"], CREDENTIALS_MESSAGE)
                self.assertNotIn("private-stack", repr(feedback))

    def test_unknown_exception_cannot_spoof_credentials_code(self):
        class UnrelatedError(Exception):
            code = "INVALID_CREDENTIALS"

        feedback = to_feedback(UnrelatedError("private-password"))
        self.assertEqual(feedback["error_code"], "BACKEND_ERROR")
        self.assertEqual(feedback["status"], "failed")
        self.assertNotIn("private-password", repr(feedback))

    def test_noncredential_login_error_is_escaped_without_field_error_marker(self):
        rendered = render_login_error("<script>invalid</script>", "DB_UNAVAILABLE")
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("data-login-invalid", rendered)


class RealAuthLoginFeedbackTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 테스트 전용 값이며 실제 계정이나 저장된 비밀번호 해시를 읽지 않는다.
        cls.user = {
            "id": "fixture-user",
            "password_hash": hash_password("synthetic-correct-password"),
            "auth_version": 1,
        }

    def setUp(self):
        self.service = BackendServices(create_bindings(authentication=auth))
        refs = {name: ComponentKeys(name) for name in ("login", "search", "admin", "shell")}
        self.controller = UIController(self.service, **refs)

    async def run_login(self, key):
        view = self.controller.signed_out()
        view["login.username"] = TEST_USERNAME
        view["login.password"] = WRONG_PASSWORD
        patches = []
        async for update in self.controller.run(
            "login", key, TEST_USERNAME, WRONG_PASSWORD, "관리자"
        ):
            patches.append(update)
            view.update(update)
        return view, patches

    async def test_wrong_password_and_unknown_account_have_identical_login_ui(self):
        feedbacks = []
        for key, user in (("wrong-password", self.user), ("unknown-account", None)):
            with self.subTest(case=key), database_user(user) as database:
                with patch.object(auth, "verify_password", wraps=auth.verify_password) as verify:
                    view, patches = await self.run_login(key)
                    verify.assert_called_once()
            # 잘못된 자격 증명은 사용자 조회만 하고 세션 INSERT를 수행하지 않는다.
            self.assertEqual(database.execute.call_count, 1)
            self.assertIsNone(self.controller.state(key).user)
            self.assertTrue(view["login.container"]["visible"])
            self.assertFalse(view["shell.container"]["visible"])
            self.assertFalse(patches[0]["login.submit"]["interactive"])
            self.assertTrue(view["login.submit"]["interactive"])
            self.assertEqual(view["login.username"], TEST_USERNAME)
            self.assertIn(CREDENTIALS_MESSAGE, view["login.feedback"])
            self.assertIn('data-login-invalid="credentials"', view["login.feedback"])
            self.assertNotIn("잠시 후", view["login.feedback"])
            self.assertNotIn(WRONG_PASSWORD, repr(patches))
            self.assertNotIn(self.user["password_hash"], repr(patches))
            feedbacks.append(view["login.feedback"])
        self.assertEqual(feedbacks[0], feedbacks[1])

    async def test_service_keeps_credentials_code_for_both_real_auth_failures(self):
        for user in (self.user, None):
            with self.subTest(existing=user is not None), database_user(user):
                with self.assertRaises(UIServiceError) as caught:
                    await self.service.login(TEST_USERNAME, WRONG_PASSWORD)
            self.assertEqual(
                (caught.exception.code, caught.exception.status, caught.exception.message),
                ("INVALID_CREDENTIALS", "input_error", CREDENTIALS_MESSAGE),
            )

    async def test_database_outage_keeps_login_without_credentials_notice(self):
        with patch.object(auth, "transaction", side_effect=DbError("DB_UNAVAILABLE")), patch.object(
            auth, "get_settings", return_value=SimpleNamespace(session_ttl_minutes=60)
        ):
            with self.assertRaises(UIServiceError) as caught:
                await self.service.login(TEST_USERNAME, WRONG_PASSWORD)
            self.assertEqual(caught.exception.code, "DB_UNAVAILABLE")
            self.assertEqual(caught.exception.status, "failed")
            view, patches = await self.run_login("database-outage")
        self.assertIn("데이터 저장소", view["login.feedback"])
        self.assertNotIn(CREDENTIALS_MESSAGE, view["login.feedback"])
        self.assertNotIn("data-login-invalid", view["login.feedback"])
        self.assertTrue(view["login.container"]["visible"])
        self.assertFalse(view["shell.container"]["visible"])
        self.assertTrue(view["login.submit"]["interactive"])
        self.assertNotIn(WRONG_PASSWORD, repr(patches))

    async def test_invalid_session_keeps_existing_expiry_contract_without_db(self):
        with patch.object(auth, "transaction") as transaction:
            with self.assertRaises(UIServiceError) as caught:
                await self.service.validate_session({"token": "short", "session_id": "invalid"})
            transaction.assert_not_called()
        self.assertEqual(caught.exception.code, "SESSION_EXPIRED")
        self.assertEqual(caught.exception.status, "session_expired")


if __name__ == "__main__":
    unittest.main(verbosity=2)
