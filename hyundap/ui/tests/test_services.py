import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from hyundap.ui.services import BackendBindings, BackendServices, UIServiceError, MAX_UPLOAD_BYTES, validate_upload_file
from hyundap.ui.mock_services import MockServices


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.s = MockServices()
        self.e = await self.s.login('employee', 'demo1234')
        self.m = await self.s.login('manager', 'demo1234')
        self.a = await self.s.login('admin', 'demo1234')

    async def error(self, code, operation):
        with self.assertRaises(UIServiceError) as caught:
            await operation
        self.assertEqual(caught.exception.code, code)

    async def test_login_scope_and_mutation_isolation(self):
        await self.error('INVALID_CREDENTIALS', self.s.login('admin', 'wrong'))
        await self.error('LOGIN_REQUIRED', self.s.login('', ''))
        forged = dict(self.e, role='platform_admin', allowed_company_ids=['gte'])
        await self.error('ACCESS_DENIED', self.s.search(forged, '점검', ['gte']))
        result = await self.s.search(self.e, '점검')
        self.assertTrue(result['results'])
        self.assertTrue(all(d['company_id'] == 'hri' for d in result['results']))
        result['results'][0]['citations'][0]['excerpt'] = 'mutated'
        self.assertNotEqual((await self.s.search(self.e, '점검'))['results'][0]['citations'][0]['excerpt'], 'mutated')
        self.e['allowed_company_ids'].append('bpa')
        self.assertEqual((await self.s.validate_session(self.e))['allowed_company_ids'], ['hri'])

    async def test_session_expiry_logout(self):
        now = [0]
        service = MockServices(10, lambda: now[0])
        session = await service.login('admin', 'demo1234')
        now[0] = 10
        await self.error('SESSION_EXPIRED', service.search(session, '점검'))
        await self.s.logout(self.a)
        await self.error('SESSION_EXPIRED', self.s.detail(self.a, 'demo-hri-manual'))

    async def test_versions_citations_and_filter_conflict(self):
        document = await self.s.detail(self.e, 'demo-hri-manual')
        await self.error('DOCUMENT_CHANGED', self.s.detail(self.e, document['document_id'], 'old'))
        await self.error('ACCESS_DENIED', self.s.detail(self.e, 'demo-gte-manual'))
        answer = await self.s.ask(self.e, '점검', document_ids=[document['document_id']])
        self.assertEqual(answer['status'], 'ANSWERED')
        self.assertIn('[1]', answer['answer'])
        self.assertEqual(answer['citations'][0]['number'], 1)
        self.assertEqual(answer['citations'][0]['version_id'], document['version_id'])
        self.assertFalse((await self.s.summarize(self.e, document['document_id']))['is_full'])
        self.assertEqual((await self.s.ask(self.e, 'xyzunknown'))['status'], 'NO_EVIDENCE')
        await self.error('FILTER_CONFLICT', self.s.ask(self.a, '점검', company_ids=['gte'], document_ids=[document['document_id']]))

    async def test_upload_boundary_duplicates_scope_and_download(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.pdf'
            path.write_bytes(b'example')
            await self.error('ACCESS_DENIED', self.s.upload(self.e, str(path), 'hri', 'manual'))
            result = await self.s.upload(self.m, str(path), 'hri', 'manual')
            self.assertTrue(result['simulated'])
            self.assertFalse(result['searchable'])
            await self.error('DUPLICATE_FILE', self.s.upload(self.m, str(path), 'hri', 'manual'))
            await self.s.upload(self.a, str(path), 'hri', 'manual')
            await self.s.upload(self.a, str(path), 'gte', 'manual')
            path.write_bytes(b'')
            await self.error('EMPTY_DOCUMENT', self.s.upload(self.m, str(path), 'hri', 'manual'))
            wrong = Path(folder) / 'test.txt'
            wrong.write_text('x')
            await self.error('UNSUPPORTED_FORMAT', self.s.upload(self.m, str(wrong), 'hri', 'manual'))
            large = Path(folder) / 'large.pdf'
            with large.open('wb') as stream:
                stream.truncate(MAX_UPLOAD_BYTES)
            self.assertEqual(validate_upload_file(large), large)
            with large.open('ab') as stream:
                stream.truncate(MAX_UPLOAD_BYTES + 1)
            with self.assertRaises(UIServiceError) as caught:
                validate_upload_file(large)
            self.assertEqual(caught.exception.code, 'FILE_TOO_LARGE')
        download = Path(await self.s.download(self.e, 'demo-hri-manual'))
        try:
            self.assertEqual(download.suffix, '.txt')
            self.assertIn('원본 문서가 아닙니다', download.read_text(encoding='utf-8-sig'))
        finally:
            download.unlink()

    async def test_backend_exact_kwargs_and_no_fallback(self):
        import dataclasses
        observed = {}
        @dataclasses.dataclass
        class Result:
            query: str
            results: list
            total_hits: int = 2
        def search(**kwargs):
            observed['search'] = kwargs
            return Result(kwargs['query'], [{'document_id': 'd', 'citations': [{'location_label': 'p. 3', 'excerpt': 'text'}]}])
        async def ask(**kwargs):
            observed['ask'] = kwargs
            return {'status': 'ANSWERED', 'answer': 'a', 'citations': []}
        bindings = BackendBindings(validate_session=lambda session: dict(session, role='employee'), search=search, ask=ask, user_factory=lambda s: ('user', s['user_id']), filters_factory=lambda data: ('filters', data), summarize_document=lambda **kwargs: {'version_id': 'v2'})
        service = BackendServices(bindings)
        result = await service.search(self.e, 'hello', ['hri'])
        self.assertEqual(result['results'][0]['excerpt'], 'text')
        self.assertEqual(observed['search']['actor'], ('user', 'demo-employee'))
        self.assertEqual(observed['search']['limit'], 10)
        await service.ask(self.e, 'q', document_ids=['d'])
        self.assertEqual(observed['ask']['filters'][1]['document_ids'], ['d'])
        await self.error('DOCUMENT_CHANGED', service.summarize(self.e, 'd', 'v1'))
        await self.error('BACKEND_NOT_CONNECTED', service.detail(self.e, 'd'))
        await self.error('BACKEND_NOT_CONNECTED', BackendServices().login('admin', 'demo1234'))

    async def test_safe_errors_and_password_change_gate(self):
        def failure(**kwargs):
            raise RuntimeError('SECRET CONNECTION STRING')
        service = BackendServices(BackendBindings(login=failure))
        with self.assertRaises(UIServiceError) as caught:
            await service.login('a', 'b')
        self.assertNotIn('SECRET', caught.exception.message)
        service = BackendServices(BackendBindings(validate_session=lambda session: {k: v for k, v in session.items() if k != 'must_change_password'}, search=lambda **kwargs: {'results': []}))
        session = dict(self.e, must_change_password=True)
        await self.error('PASSWORD_CHANGE_REQUIRED', service.search(session, 'a'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
