"""chatbot/doc_classifier.py 테스트. OpenAI·DB·Chroma를 부르지 않고 대역으로 실행한다.

실행(저장소 맨 위 폴더에서):  python -m unittest hyundap.tests.test_chatbot_doc_classifier -v
"""
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from openai.types.decision import Decision

from hyundap.chatbot import classify_document, doc_classifier
from hyundap.chatbot.constants import CLASSIFY_MODEL, CLASSIFY_SAMPLE_CHARS
from hyundap.chatbot.models import ChatbotError, DocClassification
from hyundap.embedding.models import VectorHit
from hyundap.search.models import ChunkHit


def vhit(i, text=None, version='v1', doc_type='manual'):
    return VectorHit(f'c{i}', text if text is not None else f'A사 점검 절차 {i}단계', {
        'document_id': 'd1', 'version_id': version, 'company_id': 'co', 'company_name': 'A사', 'doc_type': doc_type,
        'file_name': '점검절차.docx', 'section_no': None, 'location_label': f'p.{i + 1}', 'chunk_index': i})


def chunk_hit(i, text):
    return ChunkHit(chunk_id=f'c{i}', document_id='d1', version_id='v1', version_no=None, company_id='co',
                    company_name='A사', doc_type='manual', file_name='점검절차.docx', section_no=None,
                    location_label=f'p.{i + 1}', chunk_index=i, text=text, score=None)


# Decisions API 응답을 SDK의 실제 응답 형식(Decision)으로 만든다
def decision(*answers):
    return Decision.model_validate({
        'answers': list(answers), 'model': 'gpt-6-luna',
        'usage': {'input_tokens': 400, 'output_tokens': 0, 'total_tokens': 400,
                  'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
                  'output_tokens_details': {'reasoning_tokens': 0}},
    })


def choice(value, confidence=0.95):
    return {'type': 'choice', 'name': 'doc_type', 'choice': value, 'confidence': confidence,
            'probabilities': [{'value': value, 'probability': confidence}]}


def predicate(name, probability):
    return {'type': 'predicate', 'name': name, 'probability': probability}


GOOD = decision(choice('policy', 0.93), predicate('company_named', 0.98), predicate('company_match', 0.96))


class FakeClient:
    """client.decisions.create 대역. reply가 예외면 던지고 아니면 그대로 돌려준다."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.decisions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class Base(unittest.TestCase):
    def setUp(self):
        doc_classifier._RESULTS.clear()
        self.addCleanup(doc_classifier._RESULTS.clear)

    def run_classify(self, reply, hits=None, access_error=None, chunks_error=None):
        client = FakeClient(reply)
        with ExitStack() as stack:
            if chunks_error:
                stack.enter_context(patch('hyundap.embedding.vector_store.get_document_chunks', side_effect=chunks_error))
            else:
                stack.enter_context(patch('hyundap.embedding.vector_store.get_document_chunks',
                                          return_value=hits if hits is not None else [vhit(0), vhit(1)]))
            access = stack.enter_context(patch('hyundap.auth.permissions.verify_documents_access',
                                               side_effect=access_error))
            stack.enter_context(patch.object(doc_classifier, 'get_classify_client', return_value=client))
            result = classify_document('d1', {'user_id': 'u1'})
        return result, client, access


# 입력·질문 만들기와 응답 읽기 (호출 없음)
class Pieces(unittest.TestCase):
    def test_input_uses_ordered_sample_within_limit(self):
        text = doc_classifier.build_classify_input('점검절차.docx', ['첫째' * 10, '  ', '둘째' * 10, '가' * CLASSIFY_SAMPLE_CHARS])
        self.assertTrue(text.startswith('파일명: 점검절차.docx'))
        self.assertLess(text.index('첫째'), text.index('둘째'))
        self.assertEqual(len(text.split('본문:\n')[1].replace('\n', '')), CLASSIFY_SAMPLE_CHARS)
        # 청크 경로는 chunk_index 순서로 본문을 넘긴다
        client = FakeClient(GOOD)
        with patch.object(doc_classifier, 'get_classify_client', return_value=client):
            doc_classifier.classify_hits([chunk_hit(1, '둘째'), chunk_hit(0, '첫째')])
        sent = client.calls[0]['input']
        self.assertLess(sent.index('첫째'), sent.index('둘째'))

    def test_questions(self):
        questions = doc_classifier.build_classify_questions('A사')
        self.assertEqual([q['name'] for q in questions], ['doc_type', 'company_named', 'company_match'])
        self.assertEqual([c['value'] for c in questions[0]['choices']], ['manual', 'policy', 'guide', 'other'])
        self.assertIn("'A사'", questions[2]['instructions'])
        self.assertEqual([q['name'] for q in doc_classifier.build_classify_questions('  ')], ['doc_type'])

    def test_read_answers_company_rules(self):
        cases = [((0.98, 0.96), 'yes'), ((0.98, 0.05), 'no'), ((0.98, 0.5), ''), ((0.1, 0.99), '')]
        for (named, match), expected in cases:
            with self.subTest(named=named, match=match):
                answer = doc_classifier.read_classify_answers(decision(
                    choice('guide', 0.8), predicate('company_named', named), predicate('company_match', match)))
                self.assertEqual(answer, ('guide', 0.8, expected))

    def test_read_answers_unknown_type_and_refusal(self):
        self.assertEqual(doc_classifier.read_classify_answers(decision(choice('contract', 0.9))), ('', 0.0, ''))
        with self.assertRaises(ValueError):
            doc_classifier.read_classify_answers(decision({'type': 'refusal', 'name': 'doc_type'}))
        # 회사 질문만 거부되면 유형은 남기고 회사 일치는 비운다
        answer = doc_classifier.read_classify_answers(decision(
            choice('manual'), {'type': 'refusal', 'name': 'company_match'}, predicate('company_named', 0.9)))
        self.assertEqual(answer, ('manual', 0.95, ''))


# classify_document 흐름: 조회 → 접근 확인 → 분류 → 보관
class Flow(Base):
    def test_success_compares_with_admin_type(self):
        result, client, access = self.run_classify(GOOD)
        self.assertEqual(result, DocClassification(
            document_id='d1', version_id='v1', admin_doc_type='manual', auto_doc_type='policy', confidence=0.93,
            matches_admin_type=False, company_match='yes', status='success', detail=''))
        call = client.calls[0]
        self.assertEqual(call['model'], CLASSIFY_MODEL)
        self.assertIn('A사 점검 절차 0단계', call['input'])
        self.assertEqual(len(call['questions']), 3)
        access.assert_called_once_with({'user_id': 'u1'}, [('d1', 'v1')])

    def test_same_type_marks_match(self):
        reply = decision(choice('manual', 0.9))
        result, _, _ = self.run_classify(reply)
        self.assertIs(result.matches_admin_type, True)

    def test_access_checked_before_api_and_errors_raise(self):
        access_error = type('AccessError', (Exception,), {'code': 'ACCESS_DENIED'})()
        client = FakeClient(GOOD)
        with patch('hyundap.embedding.vector_store.get_document_chunks', return_value=[vhit(0)]), \
                patch('hyundap.auth.permissions.verify_documents_access', side_effect=access_error), \
                patch.object(doc_classifier, 'get_classify_client', return_value=client):
            with self.assertRaises(Exception) as caught:
                classify_document('d1', {'user_id': 'u1'})
        self.assertIs(caught.exception, access_error)
        self.assertEqual(client.calls, [])   # 권한이 없으면 API를 부르지 않는다

    def test_empty_document_and_retrieval_failure(self):
        with self.assertRaises(ChatbotError) as caught:
            self.run_classify(GOOD, hits=[])
        self.assertEqual(caught.exception.code, 'NO_EVIDENCE')
        with self.assertRaises(ChatbotError) as caught:
            self.run_classify(GOOD, chunks_error=RuntimeError('chroma down'))
        self.assertEqual(caught.exception.code, 'RETRIEVAL_FAILED')

    def test_api_failure_returns_failed_without_secret(self):
        for reply in (RuntimeError('boom sk-test-key'), TimeoutError('slow'),
                      decision({'type': 'refusal', 'name': 'doc_type'})):
            with self.subTest(reply=type(reply).__name__):
                result, _, _ = self.run_classify(reply)
                self.assertEqual((result.status, result.auto_doc_type, result.matches_admin_type), ('failed', '', None))
                self.assertNotIn('sk-test', result.detail)
                self.assertEqual(doc_classifier._RESULTS, {})   # 실패는 보관하지 않는다

    def test_missing_key_is_failed_not_raised(self):
        with patch('hyundap.common.config.get_settings') as settings:
            settings.return_value.openai_api_key = ''
            with self.assertRaises(ChatbotError) as caught:
                doc_classifier.get_classify_client()
        self.assertEqual(caught.exception.code, 'CONFIG_MISSING')
        # classify_hits는 키 없음도 예외 대신 failed로 돌려준다
        with patch.object(doc_classifier, 'get_classify_client', side_effect=ChatbotError('CONFIG_MISSING')):
            result = doc_classifier.classify_hits([chunk_hit(0, '본문')])
        self.assertEqual((result.status, result.detail), ('failed', 'ChatbotError'))

    def test_same_version_uses_kept_result_but_rechecks_access(self):
        first, client, _ = self.run_classify(GOOD)
        second, client2, access = self.run_classify(GOOD)
        self.assertEqual((len(client.calls), len(client2.calls)), (1, 0))   # 두 번째는 API를 부르지 않는다
        self.assertEqual(first, second)
        access.assert_called_once()   # 보관한 결과를 돌려줄 때도 접근 확인은 한다
        # 새 버전이면 다시 분류한다
        _, client3, _ = self.run_classify(GOOD, hits=[vhit(0, version='v2')])
        self.assertEqual(len(client3.calls), 1)

    def test_common_document_after_normalize(self):
        # 업로드 중 정규화된 문서: 접근 확인 없이 바로 분류하고, 관리자 유형과 비교한다
        from hyundap.common.schemas import CommonDocument, Section
        doc = CommonDocument(header={'document_id': 'd9', 'version_id': 'v9', 'company_id': 'co', 'company_name': 'A사',
                                     'doc_type': 'policy', 'file_name': '규정.docx'},
                             sections=[Section(1, 'p.1', 'A사 사내 규정 제1조'), Section(2, 'p.2', '제2조 책임')])
        client = FakeClient(GOOD)
        with patch.object(doc_classifier, 'get_classify_client', return_value=client), \
                patch('hyundap.auth.permissions.verify_documents_access') as access:
            result = doc_classifier.classify_common_document(doc)
        self.assertEqual((result.document_id, result.version_id, result.auto_doc_type, result.matches_admin_type,
                          result.company_match, result.status), ('d9', 'v9', 'policy', True, 'yes', 'success'))
        self.assertIn('파일명: 규정.docx', client.calls[0]['input'])
        self.assertIn("'A사'", client.calls[0]['questions'][2]['instructions'])
        access.assert_not_called()
        self.assertEqual(doc_classifier._RESULTS, {})   # 업로드 경로는 메모리에 보관하지 않는다
        # 오류는 올리지 않는다
        with patch.object(doc_classifier, 'get_classify_client', side_effect=RuntimeError('down')):
            self.assertEqual(doc_classifier.classify_common_document(doc).status, 'failed')
        with patch.object(doc_classifier, 'get_classify_client', return_value=client) as get_client:
            self.assertEqual(doc_classifier.classify_common_document(object()).detail, 'EMPTY_DOCUMENT')
        get_client.assert_not_called()   # 본문이 없으면 API를 부르지 않는다

    def test_cache_size_limit(self):
        with patch.object(doc_classifier, 'CLASSIFY_CACHE_SIZE', 2):
            for version in ('v1', 'v2', 'v3'):
                doc_classifier.remember(DocClassification('d1', version, 'manual'))
        self.assertEqual(list(doc_classifier._RESULTS), ['v2', 'v3'])

    def test_real_client_settings(self):
        doc_classifier._CLIENTS.clear()
        self.addCleanup(doc_classifier._CLIENTS.clear)
        with patch('hyundap.common.config.get_settings') as settings:
            settings.return_value.openai_api_key = 'sk-test-key'
            client = doc_classifier.get_classify_client()
            self.assertIs(client, doc_classifier.get_classify_client())
        self.assertEqual((client.timeout, client.max_retries), (10, 0))
        self.assertTrue(hasattr(client.decisions, 'create'))


# 업로드 연결: ingest 정규화 직후 분류 → 수집 보고서 → 작업 기록(jobs_repo). API는 부르지 않는다
class UploadPath(Base):
    def make_docx(self, text):
        import tempfile
        from pathlib import Path
        from docx import Document
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / '규정.docx'
        document = Document()
        document.add_paragraph(text)
        document.save(path)
        return str(path)

    def ingest(self, **kwargs):
        from hyundap.ingest.pipeline import ingest_document
        info = {'document_id': 'd1', 'version_id': 'v1', 'company_id': 'co', 'company_name': 'A사',
                'doc_type': 'manual', 'file_name': '규정.docx'}
        return ingest_document(self.make_docx('A사 사내 규정 제1조 이 규정은 연구윤리 기준을 정한다.'), info, **kwargs)

    def test_ingest_classifies_only_when_asked(self):
        good = DocClassification('d1', 'v1', 'manual', 'policy', 0.93, False, 'yes', 'success')
        with patch.object(doc_classifier, 'classify_common_document', return_value=good) as classify:
            plain = self.ingest()
            classified = self.ingest(classify=True)
        self.assertEqual(classify.call_count, 1)            # 기본(classify=False)은 부르지 않는다
        self.assertNotIn('auto_doc_type', plain.report)
        doc = classify.call_args.args[0]
        self.assertEqual((doc.header['doc_type'], len(doc.sections) > 0), ('manual', True))   # 정규화된 문서를 받는다
        self.assertEqual({key: classified.report[key] for key in
                          ('auto_doc_type', 'doc_type_confidence', 'company_match', 'classify_status')},
                         {'auto_doc_type': 'policy', 'doc_type_confidence': 0.93, 'company_match': 'yes',
                          'classify_status': 'success'})
        # 청크와 관리자 doc_type은 그대로다
        self.assertEqual([c.text for c in classified.chunks], [c.text for c in plain.chunks])
        self.assertEqual({c.doc_type for c in classified.chunks}, {'manual'})

    def test_ingest_continues_when_classifier_breaks(self):
        with patch.object(doc_classifier, 'classify_common_document', side_effect=RuntimeError('boom')):
            result = self.ingest(classify=True)
        self.assertEqual((result.status, result.report['classify_status']), ('success', 'failed'))
        self.assertNotIn('auto_doc_type', result.report)

    def test_upload_turns_on_classify_only_for_supporting_ingest(self):
        from hyundap.db import upload_service, version_service
        seen = []

        def capture(function, *args, **kwargs):
            seen.append(kwargs)
            return {'status': 'failed', 'error_code': 'EMPTY_DOCUMENT'}

        def new_ingest(path, info, find_by_hash=None, file_hash=None, classify=False):
            pass

        def old_ingest(path, info, find_by_hash=None):
            pass

        ids = {'document_id': 'd1', 'version_id': 'v1', 'job_id': 'j1'}
        with patch.object(version_service, 'call_external', side_effect=capture):
            for function in (new_ingest, old_ingest):
                upload_service.ingest_and_save_chunks('a.docx', {'company_id': 'co'}, ids, function)
        self.assertIs(seen[0].get('classify'), True)
        self.assertNotIn('classify', seen[1])               # 옛 수집기 모양이면 넘기지 않는다

    def test_job_report_keeps_ingest_classification(self):
        from hyundap.db import jobs_repo
        from hyundap.db.errors import DbError
        base = dict(stage='ingest', status='success', parser='python-docx', chunk_count=1)
        clean = jobs_repo.normalize_report(dict(base, auto_doc_type='policy', doc_type_confidence=0.93,
                                                company_match='yes', classify_status='success'))
        self.assertEqual({key: clean[key] for key in jobs_repo.CLASSIFY_FIELDS},
                         {'auto_doc_type': 'policy', 'doc_type_confidence': 0.93, 'company_match': 'yes',
                          'classify_status': 'success'})
        self.assertEqual(jobs_repo.normalize_report(dict(base, classify_status='failed'))['classify_status'], 'failed')
        # 범위 밖 값은 수집 기록을 거부하지 않고 분류 값만 빼고 failed로 남긴다 (업로드는 성공)
        good = dict(auto_doc_type='policy', doc_type_confidence=0.93, company_match='yes', classify_status='success')
        bad = [dict(auto_doc_type='contract'), dict(auto_doc_type=None), dict(doc_type_confidence=1.5),
               dict(doc_type_confidence=True), dict(doc_type_confidence=float('nan')), dict(doc_type_confidence='0.9'),
               dict(company_match='maybe'), dict(classify_status='skipped'), dict(classify_status=None)]
        for changes in bad:
            with self.subTest(changes=changes):
                clean = jobs_repo.normalize_report(dict(base, **dict(good, **changes)))
                self.assertEqual({key: clean[key] for key in jobs_repo.CLASSIFY_FIELDS if key in clean},
                                 {'classify_status': 'failed'})
                self.assertEqual((clean['stage'], clean['status'], clean['chunk_count']), ('ingest', 'success', 1))
        # 상태 없이 값만 오거나 상태만 이상해도 failed
        self.assertEqual(jobs_repo.normalize_report(dict(base, auto_doc_type='policy'))['classify_status'], 'failed')
        # 분류 값이 없으면 아무것도 붙이지 않는다
        self.assertNotIn('classify_status', jobs_repo.normalize_report(base))
        with self.assertRaises(DbError):   # 다른 단계에는 섞일 수 없다
            jobs_repo.normalize_report(dict(stage='embedding', status='success', auto_doc_type='policy'))


if __name__ == '__main__':
    unittest.main()
