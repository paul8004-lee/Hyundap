"""tests/test_ui_batch_stream.py — 다중 파일 업로드와 요약 스트리밍 검사 (PR #215 회귀 테스트).

실행: 저장소 맨 위에서 python -m unittest hyundap.tests.test_ui_batch_stream -v"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from hyundap.tests.test_ui_controller import ComponentKeys
from hyundap.ui.controller import UIController
from hyundap.ui.mock_services import MockServices
from hyundap.ui.services import BackendBindings, BackendServices, UIServiceError


class BatchUploadTests(unittest.IsolatedAsyncioTestCase):
    """관리자가 여러 파일을 고르면 파일별로 차례로 등록하고 결과를 따로 보여 주는지 확인한다."""

    async def asyncSetUp(self):
        self.service = MockServices()
        self.parts = [ComponentKeys(prefix) for prefix in ('login', 'search', 'admin', 'shell')]
        self.controller = UIController(self.service, *self.parts)
        self.view = {}
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        await self.run_action('login', 'admin', 'demo1234', '관리자')

    async def run_action(self, action, *args):
        patches = []
        async for patch in self.controller.run(action, 'one', *args):
            patches.append(patch)
            self.view.update(patch)
        return patches

    def make_file(self, name, content):
        path = Path(self.folder.name) / name
        path.write_bytes(content)
        return str(path)

    async def test_each_file_result_is_reported(self):
        """서로 다른 파일 2개를 올리면 2건 모두 처리되고 진행 건수가 표시된다."""
        files = [self.make_file('a.pdf', b'first'), self.make_file('b.docx', b'second')]
        upload = self.service.upload = AsyncMock(wraps=self.service.upload)
        patches = await self.run_action('upload', files, 'hri', 'manual')
        self.assertEqual(upload.await_count, 2)
        self.assertIn('2/2건 처리', self.view['admin.result'])
        progress = [patch['admin.feedback'] for patch in patches if '(1/2)' in str(patch.get('admin.feedback', ''))]
        self.assertTrue(progress, '첫 파일 처리 중 진행 건수가 표시되어야 한다')
        self.assertTrue(self.view['admin.submit']['interactive'])

    async def test_failed_file_does_not_stop_the_rest(self):
        """같은 내용의 파일이 중간에 실패해도 나머지 파일을 계속 처리하고 실패 건수를 보여 준다."""
        files = [self.make_file('a.pdf', b'same'), self.make_file('copy.pdf', b'same'),
                 self.make_file('c.pptx', b'third')]
        upload = self.service.upload = AsyncMock(wraps=self.service.upload)
        await self.run_action('upload', files, 'hri', 'manual')
        self.assertEqual(upload.await_count, 3)
        self.assertIn('3/3건 처리', self.view['admin.result'])
        self.assertIn('실패 1건', self.view['admin.result'])

    async def test_invalid_file_blocks_the_whole_batch(self):
        """지원하지 않는 파일이 하나라도 섞여 있으면 등록을 시작하지 않고 그 파일 이름을 안내한다."""
        files = [self.make_file('a.pdf', b'first'), self.make_file('notes.txt', b'text')]
        upload = self.service.upload = AsyncMock(wraps=self.service.upload)
        await self.run_action('upload', files, 'hri', 'manual')
        upload.assert_not_awaited()
        self.assertIn('notes.txt', self.view['admin.feedback'])

    async def test_session_expiry_stops_remaining_files(self):
        """처리 중 세션이 만료되면 남은 파일을 올리지 않고 로그아웃 안내에 처리 확인을 덧붙인다."""
        files = [self.make_file('a.pdf', b'first'), self.make_file('b.pdf', b'second')]

        async def expired(*args, **kwargs):
            raise UIServiceError('SESSION_EXPIRED', '로그인 시간이 만료되었습니다.', 'session_expired')

        upload = self.service.upload = AsyncMock(side_effect=expired)
        patches = await self.run_action('upload', files, 'hri', 'manual')
        self.assertEqual(upload.await_count, 1)
        self.assertIsNone(self.controller.state('one').user)
        self.assertTrue(any('일부 파일' in str(value) for patch in patches for value in patch.values()))


class SummaryStreamControllerTests(unittest.IsolatedAsyncioTestCase):
    """요약 이벤트(status·delta·reset·done)가 화면에 순서대로 반영되고, 중단 시 스트림이 닫히는지 확인한다."""

    async def asyncSetUp(self):
        self.service = MockServices()
        self.parts = [ComponentKeys(prefix) for prefix in ('login', 'search', 'admin', 'shell')]
        self.controller = UIController(self.service, *self.parts)
        self.view = {}
        await self.run_action('login', 'admin', 'demo1234', '관리자')
        await self.run_action('search', '점검', '__all__', '__all__')
        self.assertIsNotNone(self.controller.state('one').selected)

    async def run_action(self, action, *args):
        patches = []
        async for patch in self.controller.run(action, 'one', *args):
            patches.append(patch)
            self.view.update(patch)
        return patches

    async def test_delta_accumulates_and_reset_clears(self):
        """delta는 이어 붙이고, reset을 받으면 지금까지 표시한 부분 요약을 지운 뒤 done의 요약으로 끝낸다."""
        selected = self.controller.state('one').selected

        async def stream(session, document_id, version_id=None):
            yield {'type': 'status', 'message': '구간 1/1 요약 중'}
            yield {'type': 'delta', 'text': '첫 줄\n'}
            yield {'type': 'reset', 'reason': '근거 확인에 실패해 다시 생성합니다.'}
            yield {'type': 'delta', 'text': '새 줄\n'}
            yield {'type': 'done', 'response': {'document_id': document_id, 'version_id': version_id,
                                                'summary': '최종 요약 [1]', 'citations': []}}

        self.service.summarize_stream = stream
        patches = await self.run_action('summarize')
        shown = [patch['search.summary'] for patch in patches if 'search.summary' in patch]
        # 첫 항목은 요약 시작 시 이전 요약을 지우는 초기 화면 갱신이다.
        self.assertEqual(shown, ['', '첫 줄\n', '', '새 줄\n', '최종 요약 [1]'])
        self.assertEqual(self.view['search.summarize']['value'], '다시 요약')
        self.assertEqual(selected['document_id'], self.controller.state('one').selected['document_id'])

    async def test_stream_is_closed_when_user_selects_another_document(self):
        """요약 도중 다른 문서를 고르면 이전 스트림을 닫고, 이전 요약 내용을 화면에 쓰지 않는다."""
        started, release = asyncio.Event(), asyncio.Event()
        closed = []

        async def stream(session, document_id, version_id=None):
            try:
                yield {'type': 'status', 'message': '요약 중'}
                started.set()
                await release.wait()
                yield {'type': 'delta', 'text': '이전 문서 요약\n'}
            finally:
                closed.append(document_id)

        self.service.summarize_stream = stream
        pending = asyncio.create_task(self.run_action('summarize'))
        await asyncio.wait_for(started.wait(), 2)
        await self.run_action('select', 1)
        release.set()
        await asyncio.wait_for(pending, 2)
        self.assertEqual(len(closed), 1)
        self.assertNotEqual(self.view.get('search.summary'), '이전 문서 요약\n')

    async def test_stream_error_clears_partial_summary(self):
        """스트림 도중 오류가 나면 부분 요약을 지우고 오류 안내를 보여 준다."""
        async def stream(session, document_id, version_id=None):
            yield {'type': 'delta', 'text': '부분 요약\n'}
            raise UIServiceError('LLM_FAILED', '다른 요약이 진행 중입니다. 잠시 후 다시 시도해 주세요.')

        self.service.summarize_stream = stream
        await self.run_action('summarize')
        self.assertEqual(self.view['search.summary'], '')
        self.assertIn('진행 중', self.view['search.detail_feedback'])
        self.assertTrue(self.view['search.summarize']['interactive'])


class SummaryStreamServiceTests(unittest.IsolatedAsyncioTestCase):
    """BackendServices.summarize_stream이 동기 백엔드 스트림을 전달하고 끝나거나 중단될 때 닫는지 확인한다."""

    def make_service(self, stream=None, summarize=None):
        bindings = BackendBindings(summarize_document_stream=stream, summarize_document=summarize)
        service = BackendServices(bindings)

        async def user(session):
            return {'user_id': 'u1'}

        service._user = user
        return service

    @staticmethod
    def backend_stream(closed, version_id='v1'):
        """chatbot.summary.summarize_document_stream과 같은 모양의 동기 제너레이터."""
        def stream(document_id, user):
            try:
                yield {'type': 'status', 'message': '요약 중'}
                yield {'type': 'delta', 'text': '한 줄\n'}
                yield {'type': 'done', 'response': {'document_id': document_id, 'version_id': version_id,
                                                    'summary': '요약'}}
            finally:
                closed.append(document_id)
        return stream

    async def test_events_are_forwarded_and_stream_closed(self):
        closed = []
        service = self.make_service(self.backend_stream(closed))
        events = [event['type'] async for event in service.summarize_stream({}, 'doc', 'v1')]
        self.assertEqual(events, ['status', 'delta', 'done'])
        self.assertEqual(closed, ['doc'])

    async def test_abandoned_stream_is_closed(self):
        """첫 이벤트만 받고 그만두어도 백엔드 제너레이터의 finally가 실행된다 (요약 자리 반환과 같은 경로)."""
        closed = []
        service = self.make_service(self.backend_stream(closed))
        events = service.summarize_stream({}, 'doc', 'v1')
        self.assertEqual((await anext(events))['type'], 'status')
        await events.aclose()
        self.assertEqual(closed, ['doc'])

    async def test_version_change_is_rejected(self):
        closed = []
        service = self.make_service(self.backend_stream(closed, version_id='v2'))
        with self.assertRaises(UIServiceError) as caught:
            async for _ in service.summarize_stream({}, 'doc', 'v1'):
                pass
        self.assertEqual(caught.exception.code, 'DOCUMENT_CHANGED')
        self.assertEqual(closed, ['doc'])

    async def test_falls_back_to_summary_without_stream_binding(self):
        """스트리밍 함수가 연결되지 않았으면 기존 완료형 요약 결과를 done 이벤트로 전달한다."""
        def summarize(document_id, user):
            return {'document_id': document_id, 'version_id': 'v1', 'summary': '완료형 요약'}

        service = self.make_service(summarize=summarize)
        events = [event async for event in service.summarize_stream({}, 'doc', 'v1')]
        self.assertEqual([event['type'] for event in events], ['status', 'done'])
        self.assertEqual(events[-1]['response']['summary'], '완료형 요약')


if __name__ == '__main__':
    unittest.main()
