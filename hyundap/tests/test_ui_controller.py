"""tests/test_ui_controller.py — UI 화면 컨트롤러 검사 (hyundap/ui/tests에서 이동).

실행: 저장소 맨 위에서 python -m unittest hyundap.tests.test_ui_controller -v"""
import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

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
        self.parts = [ComponentKeys(prefix) for prefix in ('login', 'search', 'admin', 'shell')]
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

    @asynccontextmanager
    async def pending_inline_answer(self, error=None):
        """첫 검색의 답변만 지연시켜 후속 화면 이벤트와 경합시킨다."""
        started, release = asyncio.Event(), asyncio.Event()
        original = self.service.ask
        hold_next = True

        async def delayed(*args, **kwargs):
            nonlocal hold_next
            if not hold_next:
                return await original(*args, **kwargs)
            hold_next = False
            # 로그아웃 뒤에도 도착할 수 있는, 이미 생성된 응답을 재현한다.
            response = await original(*args, **kwargs)
            started.set()
            await asyncio.wait_for(release.wait(), 2)
            if error is not None:
                raise error
            return response

        self.service.ask = delayed
        pending = asyncio.create_task(self.run_action('search', '점검', '__all__', '__all__'))
        try:
            await asyncio.wait_for(started.wait(), 2)
            self.assertTrue(self.view['search.results_area']['visible'])
            self.assertIsNotNone(self.controller.state('one').selected)
            yield
        finally:
            release.set()
            try:
                await asyncio.wait_for(pending, 2)
            finally:
                self.service.ask = original

    def assert_signed_out(self):
        ctx = self.controller.state('one')
        self.assertIsNone(ctx.user)
        self.assertEqual(ctx.results, [])
        self.assertIsNone(ctx.selected)
        self.assertEqual(ctx.answer_citations, [])
        self.assertTrue(self.view['login.container']['visible'])
        self.assertFalse(self.view['shell.container']['visible'])
        self.assertFalse(self.view['search.answer']['visible'])
        self.assertEqual(self.view['search.answer']['value'], '')
        self.assertFalse(self.view['shell.modal']['visible'])
        self.assertEqual(self.view['shell.modal_body'], '')

    async def test_search_inline_answer_selection_summary_source_logout(self):
        await self.login()
        ask = self.service.ask = AsyncMock(wraps=self.service.ask)
        await self.search()
        ctx = self.controller.state('one')
        self.assertGreater(len(ctx.results), 1)
        first_id = ctx.selected['document_id']
        result_ids = [doc['document_id'] for doc in ctx.results]
        ask.assert_awaited_once_with(ctx.user, '점검', None, None, result_ids)
        answer = deepcopy(self.view['search.answer'])
        citations = deepcopy(ctx.answer_citations)
        self.assertTrue(answer['visible'])
        self.assertIn('AI 답변', answer['value'])
        self.assertNotIn('[1]', answer['value'])
        self.assertEqual(citations[0]['document_id'], first_id)
        await self.run_action('summarize')
        self.assertTrue(self.view['search.summary'])
        await self.run_action('source')
        self.assertTrue(self.view['shell.modal']['visible'])
        self.assertIn(ctx.selected['file_name'], self.view['shell.modal_body'])
        self.assertEqual([doc['document_id'] for doc in ctx.results], result_ids)
        await self.run_action('close')
        self.assertFalse(self.view['shell.modal']['visible'])
        self.assertEqual(self.view['shell.modal_body'], '')
        await self.run_action('select', 1)
        self.assertNotEqual(ctx.selected['document_id'], first_id)
        self.assertEqual(ctx.answer_citations, citations)
        self.assertEqual(self.view['search.answer'], answer)
        self.assertIn(citations[1]['excerpt'], self.view['search.evidence'])
        self.assertEqual(ask.await_count, 1)
        await self.run_action('source')
        self.assertIn(ctx.selected['file_name'], self.view['shell.modal_body'])
        await self.run_action('logout')
        self.assert_signed_out()

    async def test_employee_direct_upload_denied_input_preserved(self):
        await self.login('employee', '일반 사용자')
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'example.pdf'
            file.write_bytes(b'example')
            await self.run_action('upload_input', str(file))
            self.assertTrue(self.controller.state('one').upload_ready)
            patches = await self.run_action('upload', str(file), 'hri', 'manual')
        self.assertIn('관리자', self.view['admin.feedback'])
        self.assertFalse(self.view['shell.upload_nav']['visible'])
        self.assertEqual(self.view['admin.result'], '')
        self.assertTrue(self.view['admin.submit']['interactive'])
        self.assertFalse(any('admin.file' in patch for patch in patches))

    async def test_filter_change_drops_delayed_answer(self):
        for error in (None, UIServiceError('ANSWER_FAILED', '이전 답변 오류')):
            with self.subTest(error=error):
                await self.login()
                async with self.pending_inline_answer(error):
                    await self.run_action('filter', 'gte', '__all__')
                    expected = deepcopy(self.view)
                self.assertEqual(self.view, expected)
                self.assertFalse(self.view['search.answer']['visible'])
                self.assertEqual(self.view['search.answer']['value'], '')
                ctx = self.controller.state('one')
                self.assertEqual(ctx.company, 'gte')
                self.assertIsNone(ctx.selected)
                self.assertEqual(ctx.answer_citations, [])
                self.assertTrue(self.view['search.submit']['interactive'])

    async def test_new_search_keeps_new_answer_after_old_answer_finishes(self):
        await self.login()
        async with self.pending_inline_answer():
            await self.run_action('search', '정보보안', 'hri', 'policy')
            ctx = self.controller.state('one')
            self.assertEqual(ctx.query, '정보보안')
            self.assertTrue(ctx.answer_citations)
            expected_citations = deepcopy(ctx.answer_citations)
            expected_document = deepcopy(ctx.selected)
            expected = deepcopy(self.view)
        self.assertEqual(self.view, expected)
        self.assertEqual(ctx.selected, expected_document)
        self.assertEqual(ctx.answer_citations, expected_citations)

    async def test_reset_drops_delayed_answer(self):
        await self.login()
        async with self.pending_inline_answer():
            await self.run_action('reset')
            expected = deepcopy(self.view)
        ctx = self.controller.state('one')
        self.assertEqual(self.view, expected)
        self.assertEqual((ctx.company, ctx.doc_type, ctx.query), ('__all__', '__all__', ''))
        self.assertEqual(ctx.results, [])
        self.assertIsNone(ctx.selected)
        self.assertEqual(ctx.answer_citations, [])
        self.assertFalse(self.view['search.answer']['visible'])
        self.assertEqual(self.view['search.keyword'], '')

    async def test_menu_navigation_drops_delayed_answer(self):
        await self.login()
        async with self.pending_inline_answer():
            await self.run_action('nav', 'upload')
            expected = deepcopy(self.view)
        self.assertEqual(self.view, expected)
        self.assertTrue(self.view['admin.container']['visible'])
        self.assertFalse(self.view['search.container']['visible'])
        self.assertFalse(self.view['search.answer']['visible'])
        self.assertEqual(self.controller.state('one').answer_citations, [])

    async def test_logout_drops_delayed_answer(self):
        await self.login()
        async with self.pending_inline_answer():
            await self.run_action('logout')
            expected = deepcopy(self.view)
        self.assertEqual(self.view, expected)
        self.assert_signed_out()

    async def test_old_inline_auth_error_does_not_sign_out_new_login(self):
        await self.login()
        async with self.pending_inline_answer(UIServiceError('SESSION_EXPIRED', '이전 세션 만료')):
            await self.run_action('logout')
            await self.login('employee', '일반 사용자')
            expected = deepcopy(self.view)
            expected_user = deepcopy(self.controller.state('one').user)
        self.assertEqual(self.view, expected)
        self.assertEqual(self.controller.state('one').user, expected_user)
        self.assertFalse(self.view['login.container']['visible'])
        self.assertEqual(self.controller.state('one').answer_citations, [])

    async def test_selection_keeps_pending_answer_and_updates_selected_evidence(self):
        await self.login()
        async with self.pending_inline_answer():
            await self.run_action('select', 1)
            selected_id = self.controller.state('one').selected['document_id']
        ctx = self.controller.state('one')
        self.assertEqual(ctx.selected['document_id'], selected_id)
        self.assertTrue(self.view['search.answer']['visible'])
        self.assertIn('AI 답변', self.view['search.answer']['value'])
        self.assertGreater(len(ctx.answer_citations), 1)
        matching = [item for item in ctx.answer_citations if item['document_id'] == selected_id]
        self.assertTrue(matching)
        self.assertIn(matching[0]['excerpt'], self.view['search.evidence'])
        await self.run_action('source')
        self.assertIn(matching[0]['file_name'], self.view['shell.modal_body'])

    async def test_session_expiry_clears_page(self):
        now = [0]
        self.service = MockServices(10, lambda: now[0])
        self.controller.service = self.service
        await self.login()
        await self.search()
        await self.run_action('source')
        now[0] = 10
        await self.run_action('summarize')
        self.assert_signed_out()
        self.assertEqual(self.view['admin.result'], '')

    async def test_session_expiry_drops_already_pending_inline_answer(self):
        now = [0]
        self.service = MockServices(10, lambda: now[0])
        self.controller.service = self.service
        await self.login()
        async with self.pending_inline_answer():
            now[0] = 10
            await self.run_action('summarize')
            expected = deepcopy(self.view)
        self.assertEqual(self.view, expected)
        self.assert_signed_out()

    async def test_inline_auth_error_resets_session(self):
        await self.login()
        self.service.ask = AsyncMock(side_effect=UIServiceError('SESSION_EXPIRED', '인증이 만료되었습니다.'))
        await self.run_action('search', '점검', '__all__', '__all__')
        self.service.ask.assert_awaited_once()
        self.assert_signed_out()

    async def test_second_stage_detail_auth_error_resets_session(self):
        await self.login()
        async def detail_failure(*args, **kwargs):
            raise UIServiceError('AUTH_REQUIRED', '인증이 필요합니다.')
        self.service.detail = detail_failure
        self.service.ask = AsyncMock(wraps=self.service.ask)
        await self.run_action('search', '점검', '__all__', '__all__')
        self.service.ask.assert_not_awaited()
        self.assert_signed_out()

    async def test_second_upload_failure_clears_previous_success(self):
        await self.login()
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'example.pdf'
            file.write_bytes(b'example')
            await self.run_action('upload_input', str(file))
            self.assertTrue(self.controller.state('one').upload_ready)
            await self.run_action('upload', str(file), 'hri', 'manual')
            self.assertIn('입력 확인 완료', self.view['admin.result'])
            patches = await self.run_action('upload', str(file), 'hri', 'manual')
        self.assertIn('제출한 파일', self.view['admin.feedback'])
        self.assertEqual(self.view['admin.result'], '')
        self.assertTrue(self.view['admin.submit']['interactive'])
        self.assertFalse(any('admin.file' in patch for patch in patches))

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
        self.service.ask = AsyncMock(wraps=self.service.ask)
        await self.run_action('search', '점검', '__all__', '__all__')
        ctx = self.controller.state('one')
        self.assertTrue(ctx.results)
        self.assertIsNone(ctx.selected)
        self.assertTrue(self.view['search.results_area']['visible'])
        self.assertFalse(self.view['search.summarize']['interactive'])
        self.assertFalse(self.view['search.download']['interactive'])
        self.assertIn('찾을 수', self.view['search.detail_feedback'])
        self.service.ask.assert_not_awaited()
        self.assertEqual(ctx.answer_citations, [])
        self.assertFalse(self.view['search.answer']['visible'])

    async def test_browser_sessions_are_independent(self):
        await self.login()
        await self.search()
        original_citations = deepcopy(self.controller.state('one').answer_citations)
        await self.run_action('login', 'employee', 'demo1234', '일반 사용자', key='two')
        self.assertEqual(self.controller.state('one').user['role'], 'platform_admin')
        self.assertEqual(self.controller.state('two').user['role'], 'employee')
        self.assertIsNotNone(self.controller.state('one').selected)
        self.assertIsNone(self.controller.state('two').selected)
        await self.run_action('search', '정보보안', 'hri', 'policy', key='two')
        self.assertTrue(self.controller.state('two').answer_citations)
        self.assertNotEqual(self.controller.state('two').answer_citations, original_citations)
        self.assertEqual(self.controller.state('one').answer_citations, original_citations)
        await self.run_action('logout', key='two')
        self.assertIsNotNone(self.controller.state('one').user)
        self.assertEqual(self.controller.state('one').answer_citations, original_citations)
        self.assertEqual(self.controller.state('two').answer_citations, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
