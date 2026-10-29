"""HTTP 다운로드의 브라우저 바인딩·만료·재검증·사본 정리를 검증한다."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.testclient import TestClient

from hyundap.ui import downloads
from hyundap.ui.services import UIServiceError


class DownloadFixture:
    def __init__(self):
        self.revoked = False
        self.directory = None

    async def download(self, session, document_id, version_id):
        if self.revoked:
            raise UIServiceError('SESSION_EXPIRED', 'expired')
        self.directory = TemporaryDirectory()
        path = Path(self.directory.name) / 'snapshot'
        path.write_bytes(b'verified original')
        return dict(path=path, file_name='시험 원본.txt', temporary=self.directory)


class DownloadHttpTests(TestCase):
    def setUp(self):
        self.app = Starlette(middleware=[Middleware(downloads.DownloadMiddleware)])
        self.client = TestClient(self.app)
        self.client.get('/')
        self.browser = self.client.cookies.get(downloads.COOKIE)
        self.service = DownloadFixture()
        self.addCleanup(self.client.close)

    def issue(self):
        return downloads.issue_download({'token': 'server-only'},
            {'document_id': 'document', 'version_id': 'version'}, self.browser, self.service)

    def test_cookie_is_http_only_and_ticket_is_browser_bound_single_use(self):
        self.assertTrue(self.browser)
        url = self.issue()
        with TestClient(self.app) as other:
            self.assertEqual(other.get(url).status_code, 403)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'verified original')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertFalse(Path(self.service.directory.name).exists())
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_revoked_session_and_expired_ticket_are_rejected_at_transfer(self):
        url = self.issue()
        self.service.revoked = True
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertIsNone(self.service.directory)
        url = self.issue()
        with patch.object(downloads, 'monotonic', return_value=10**15):
            self.assertEqual(self.client.get(url).status_code, 403)
