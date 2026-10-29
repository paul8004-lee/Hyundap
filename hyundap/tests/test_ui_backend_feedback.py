"""검색·챗봇·DB·수집 오류 변환 검사. 실제 외부 서비스를 호출하지 않는다.

실행: 저장소 맨 위에서 python -m unittest hyundap.tests.test_ui_backend_feedback -v
"""

import unittest
from unittest.mock import patch
from hyundap.ui import backend_feedback as feedback
from hyundap.auth.errors import AccessError
from hyundap.db.errors import DbError
from hyundap.search.errors import build_error
from hyundap.chatbot.models import ChatbotError
from hyundap.ingest.models import IngestError

class FeedbackTests(unittest.TestCase):
    def test_direct_and_wrapped_session_expiry(self):
        for original in [AccessError('SESSION_EXPIRED'), build_error('SESSION_EXPIRED')]:
            wrapped = ChatbotError('RETRIEVAL_FAILED')
            wrapped.__cause__ = original
            for error in [original, wrapped]:
                self.assertEqual(feedback.to_feedback(error)['status'], 'session_expired')
                self.assertEqual(feedback.to_feedback(error)['error_code'], 'SESSION_EXPIRED')
    def test_known_converters(self):
        self.assertEqual(feedback.to_feedback(build_error('INVALID_DOC_TYPE'))['status'], 'input_error')
        self.assertEqual(feedback.to_feedback(ChatbotError('NO_EVIDENCE'))['status'], 'empty')
        self.assertEqual(feedback.to_feedback(ChatbotError('STATE_CHANGED'))['error_code'], 'STATE_CHANGED')
    def test_access_and_db_keep_safe_codes(self):
        self.assertEqual(feedback.to_feedback(AccessError('ACCESS_DENIED'))['status'], 'denied')
        self.assertEqual(feedback.to_feedback(AccessError('STATE_CHANGED'))['error_code'], 'STATE_CHANGED')
        error = DbError('DB_UNAVAILABLE')
        error.args = ('secret-database-url',)
        result = feedback.to_feedback(error)
        self.assertEqual(result['error_code'], 'DB_UNAVAILABLE')
        self.assertNotIn('secret', repr(result))
    def test_ingest_message_is_fixed(self):
        error = IngestError('CORRUPTED_FILE', 'secret-file-path')
        result = feedback.to_feedback(error)
        self.assertEqual(result['error_code'], 'CORRUPTED_FILE')
        self.assertNotIn('secret', repr(result))
    def test_upload_input_codes_are_preserved(self):
        for code in ('INVALID_DOC_TYPE', 'FILE_UNAVAILABLE'):
            result = feedback.to_feedback(DbError(code))
            self.assertEqual((result['status'], result['error_code']), ('input_error', code))
            self.assertEqual(result['message'], feedback.safe_upload_message(code))
    def test_unknown_type_cannot_spoof_session(self):
        class AccessError(Exception):
            code = 'SESSION_EXPIRED'
        result = feedback.to_feedback(AccessError('secret-token'))
        self.assertEqual(result['error_code'], 'BACKEND_ERROR')
        self.assertNotIn('secret', repr(result))
    def test_cycle_and_depth_are_bounded(self):
        error = RuntimeError('secret')
        error.__cause__ = error
        self.assertEqual(feedback.to_feedback(error)['error_code'], 'BACKEND_ERROR')
        head = tail = RuntimeError()
        for _ in range(9):
            child = RuntimeError()
            tail.__cause__ = child
            tail = child
        tail.__cause__ = AccessError('SESSION_EXPIRED')
        self.assertEqual(feedback.to_feedback(head)['error_code'], 'BACKEND_ERROR')
    def test_missing_optional_modules(self):
        with patch.object(feedback, 'import_module', side_effect=ModuleNotFoundError('missing-module-secret')):
            result = feedback.to_feedback(RuntimeError('secret'))
        self.assertEqual(result['error_code'], 'BACKEND_ERROR')
        self.assertNotIn('secret', repr(result))
    def test_upload_fixed_messages_and_recovery(self):
        self.assertNotIn('secret', feedback.safe_upload_message('secret'))
        self.assertNotEqual(feedback.safe_upload_message('CORRUPTED_FILE'), feedback.safe_upload_message('EMPTY_DOCUMENT'))
        self.assertEqual(feedback.safe_upload_message('anything', True), feedback.safe_upload_message('RECOVERY_REQUIRED'))
if __name__ == '__main__':
    unittest.main(verbosity=2)
