import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from hyundap.ui.controller import UIController
from hyundap.ui.mock_services import MockServices
from hyundap.ui.services import UIServiceError


class ComponentKeys(dict):
    def __init__(self, prefix):
        super().__init__()
        self.prefix = prefix

    def __missing__(self, key):
        self[key] = f'{self.prefix}.{key}'
        return self[key]


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = MockServices()
        self.parts = [ComponentKeys(prefix) for prefix in ('login', 'search', 'chat', 'admin', 'shell')]
        self.controller = UIController(self.service, *self.parts)
        self.view = {}

    async def run_action(self, action, *args, key='one'):
        patches = []
        async for patch in self.controller.run(action, key, *args):
            patches.append(patch)
            self.view.update(patch)
        return patches

    async def login(self, username='admin', role='관리자'):
        await self.run_action('login', username, 'demo1234', role)
        self.assertIsNotNone(self.controller.state('one').user)

    async def search(self):
        await self.run_action('search', '점검', '__all__', '__all__')
        self.assertIsNotNone(self.controller.state('one').selected)

    async def test_search_selection_question_summary_source_logout(self):
        await self.login()
        await self.search()
        ctx = self.controller.state('one')
        self.assertGreater(len(ctx.results), 1)
        first_id = ctx.selected['document_id']
        await self.run_action('ask', '점검', '선택 문서')
        self.assertIn('[1]', self.view['chat.answer'])
        self.assertEqual(ctx.citations[0]['document_id'], first_id)
        await self.run_action('summarize')
        self.assertTrue(self.view['search.summary'])
        await self.run_action('source')
        self.assertTrue(self.view['shell.modal']['visible'])
        self.assertIn('점검', self.view['shell.modal_body'])
        await self.run_action('close')
        self.assertFalse(self.view['shell.modal']['visible'])
        await self.run_action('open_citation', '0')
        self.assertEqual(ctx.selected['document_id'], first_id)
        self.assertEqual(len(ctx.results), 1)
        self.assertEqual(self.view['chat.answer'], '')
        await self.run_action('search', '점검', '__all__', '__all__')
        await self.run_action('ask', '점검', '선택 문서')
        await self.run_action('select', 1)
        self.assertNotEqual(ctx.selected['document_id'], first_id)
        self.assertEqual(ctx.citations, [])
        self.assertEqual(self.view['chat.answer'], '')
        await self.run_action('logout')
        self.assertIsNone(ctx.user)
        self.assertTrue(self.view['login.container']['visible'])
        self.assertFalse(self.view['shell.container']['visible'])

    async def test_employee_direct_upload_denied_input_preserved(self):
        await self.login('employee', '일반 사용자')
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'example.pdf'
            file.write_bytes(b'example')
            patches = await self.run_action('upload', str(file), 'hri', 'manual')
        self.assertIn('관리자', self.view['admin.feedback'])
        self.assertTrue(self.view['admin.submit']['interactive'])
        self.assertFalse(any('admin.file' in patch for patch in patches))

    async def test_filter_change_drops_delayed_answer(self):
        await self.login()
        await self.search()
        started, release = asyncio.Event(), asyncio.Event()
        original = self.service.ask
        async def delayed(*args, **kwargs):
            started.set()
            await release.wait()
            return await original(*args, **kwargs)
        self.service.ask = delayed
        pending = asyncio.create_task(self.run_action('ask', '점검', '선택 문서'))
        await started.wait()
        await self.run_action('filter', 'gte', '__all__')
        release.set()
        patches = await pending
        self.assertEqual(len(patches), 1)
        self.assertEqual(self.view['chat.answer'], '')
        ctx = self.controller.state('one')
        self.assertEqual(ctx.company, 'gte')
        self.assertIsNone(ctx.selected)
        self.assertEqual(ctx.citations, [])
        self.assertTrue(self.view['chat.submit']['interactive'])

    async def test_session_expiry_clears_page(self):
        now = [0]
        self.service = MockServices(10, lambda: now[0])
        self.controller.service = self.service
        await self.login()
        await self.search()
        now[0] = 10
        await self.run_action('summarize')
        self.assertIsNone(self.controller.state('one').user)
        self.assertEqual(self.controller.state('one').results, [])
        self.assertTrue(self.view['login.container']['visible'])
        self.assertFalse(self.view['shell.container']['visible'])
        self.assertEqual(self.view['admin.result'], '')
        self.assertEqual(self.view['chat.answer'], '')

    async def test_second_stage_detail_auth_error_resets_session(self):
        await self.login()
        async def detail_failure(*args, **kwargs):
            raise UIServiceError('AUTH_REQUIRED', '인증이 필요합니다.')
        self.service.detail = detail_failure
        await self.run_action('search', '점검', '__all__', '__all__')
        self.assertIsNone(self.controller.state('one').user)
        self.assertTrue(self.view['login.container']['visible'])
        self.assertFalse(self.view['shell.container']['visible'])

    async def test_second_upload_failure_clears_previous_success(self):
        await self.login()
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'example.pdf'
            file.write_bytes(b'example')
            await self.run_action('upload', str(file), 'hri', 'manual')
            self.assertIn('입력 확인 완료', self.view['admin.result'])
            await self.run_action('upload', str(file), 'hri', 'manual')
        self.assertIn('제출한 파일', self.view['admin.feedback'])
        self.assertEqual(self.view['admin.result'], '')
        self.assertTrue(self.view['admin.submit']['interactive'])

    async def test_failed_download_clears_previous_link(self):
        await self.login()
        await self.search()
        await self.run_action('download')
        old_file = self.view['search.download_file']['value']
        self.assertTrue(Path(old_file).exists())
        async def failure(*args, **kwargs):
            raise UIServiceError('DOCUMENT_CHANGED', '문서가 변경되었습니다.')
        self.service.download = failure
        try:
            await self.run_action('download')
            self.assertIsNone(self.view['search.download_file']['value'])
            self.assertFalse(self.view['search.download_file']['visible'])
            self.assertIn('변경', self.view['search.detail_feedback'])
        finally:
            Path(old_file).unlink(missing_ok=True)

    async def test_failed_summary_does_not_keep_old_success(self):
        await self.login()
        await self.search()
        await self.run_action('summarize')
        prior_summary = self.view['search.summary']
        self.assertTrue(prior_summary)
        async def failure(*args, **kwargs):
            raise UIServiceError('SUMMARY_FAILED', '요약 생성에 실패했습니다.')
        self.service.summarize = failure
        await self.run_action('summarize')
        self.assertNotEqual(self.view['search.summary'], prior_summary)
        self.assertEqual(self.view['search.coverage'], '')
        self.assertIn('실패', self.view['search.detail_feedback'])
        self.assertTrue(self.view['search.summarize']['interactive'])

    async def test_detail_failure_keeps_results_without_selected_document(self):
        await self.login()
        async def failure(*args, **kwargs):
            raise UIServiceError('DOCUMENT_UNAVAILABLE', '선택 문서를 찾을 수 없습니다.')
        self.service.detail = failure
        await self.run_action('search', '점검', '__all__', '__all__')
        ctx = self.controller.state('one')
        self.assertTrue(ctx.results)
        self.assertIsNone(ctx.selected)
        self.assertTrue(self.view['search.results_area']['visible'])
        self.assertFalse(self.view['search.summarize']['interactive'])
        self.assertFalse(self.view['search.download']['interactive'])
        self.assertIn('찾을 수', self.view['search.detail_feedback'])

    async def test_browser_sessions_are_independent(self):
        await self.login()
        await self.search()
        await self.run_action('login', 'employee', 'demo1234', '일반 사용자', key='two')
        self.assertEqual(self.controller.state('one').user['role'], 'platform_admin')
        self.assertEqual(self.controller.state('two').user['role'], 'employee')
        self.assertIsNotNone(self.controller.state('one').selected)
        self.assertIsNone(self.controller.state('two').selected)
        await self.run_action('logout', key='two')
        self.assertIsNotNone(self.controller.state('one').user)


if __name__ == '__main__':
    unittest.main(verbosity=2)
