"""tests/test_search.py — search(통합 검색·챗봇용 청크 검색)의 규칙 검증.

OpenAI·Chroma·DB·auth는 search의 창구 함수(_embed_query 등)를 가짜로 바꿔 끼워 실제로 호출하지 않는다.
hyundap.search.search는 패키지가 내보낸 함수 이름과 겹치므로 importlib로 모듈을 잡는다(README 참고).
테스트 실행 명령은 저장소 맨 위에서 python -m unittest hyundap.tests.test_search -v이다."""
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hyundap.auth.errors import AccessError
from hyundap.db.errors import DbError
from hyundap.embedding.models import EmbeddingError, VectorHit, VectorStoreError
from hyundap.search import SearchError, search, search_chunks
from hyundap.search.errors import to_feedback, to_search_error
from hyundap.search.models import ChunkHit, DocumentInfo

search_module = importlib.import_module('hyundap.search.search')
filters_module = importlib.import_module('hyundap.search.filters')

USER = {'session_id': 's1'}
SETTINGS = SimpleNamespace(search_min_score=0.3, search_top_k_max=80)


def make_hit(chunk_id, document_id, version_id='v1', company_id='A', score=0.9, section_no=1, text='본문'):
    """벡터 조회 결과(VectorHit) 1개를 만든다."""
    metadata = {'document_id': document_id, 'version_id': version_id, 'company_id': company_id,
                'section_no': section_no, 'location_label': 'p.1', 'chunk_index': 0}
    return VectorHit(chunk_id=chunk_id, text=text, metadata=metadata, score=score)


def make_row(document_id, version_id='v1', company_id='A', **changes):
    """DB가 돌려주는 11필드 행 1개를 만든다. 기본값은 공개 조건을 모두 만족한다."""
    row = {'document_id': document_id, 'company_id': company_id, 'company_name': 'A사', 'doc_type': 'manual',
           'publication_status': 'published', 'deleted_at': None, 'active_version_id': version_id,
           'version_no': 1, 'version_status': 'ready', 'data_category': 'operational',
           'file_name': f'{document_id}.pdf'}
    row.update(changes)
    return row


class SearchTestCase(unittest.TestCase):
    """창구 함수를 가짜로 바꿔 끼우는 공통 준비. 각 테스트는 hits·rows·allowed만 바꾼다."""

    def setUp(self):
        self.hits = [make_hit('v1:00000', 'd1')]
        self.rows = [make_row('d1')]
        self.allowed = {'A', 'B'}
        self.calls = {}
        fakes = [
            patch.object(search_module, 'get_settings', return_value=SETTINGS),
            patch.object(search_module, '_embed_query', return_value=[1.0]),
            patch.object(search_module, '_query_vectors', side_effect=self._query_vectors),
            patch.object(search_module, '_load_document_rows', side_effect=lambda ids: list(self.rows)),
            patch.object(filters_module, '_load_allowed_company_ids', side_effect=lambda user: set(self.allowed)),
            patch.object(filters_module, '_build_where_filter', side_effect=self._where),
        ]
        for fake in fakes:
            fake.start()
            self.addCleanup(fake.stop)

    def _query_vectors(self, vector, where, top_k):
        self.calls['top_k'] = top_k
        return list(self.hits)

    def _where(self, company_ids, doc_types, document_ids):
        self.calls['where'] = (company_ids, doc_types, document_ids)
        return {'company_id': company_ids}

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(SearchError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception


class InputTests(SearchTestCase):
    """검색어·숫자 인자 검사 (F80)."""

    def test_query_is_stripped(self):
        """앞뒤 공백을 지운 검색어로 검색하고 결과에 그 값을 돌려주는지 확인한다."""
        self.assertEqual(search(USER, '  휴가 규정  ').query, '휴가 규정')

    def test_empty_or_too_long_query(self):
        """빈 검색어·공백뿐·문자열이 아님은 EMPTY_QUERY, 2001자는 QUERY_TOO_LONG인지 확인한다."""
        for query in ('', '   ', None):
            self.assert_code('EMPTY_QUERY', search, USER, query)
        self.assert_code('QUERY_TOO_LONG', search, USER, '가' * 2001)
        self.assertEqual(search(USER, '가' * 2000).query, '가' * 2000)

    def test_counts_must_be_integers_in_range(self):
        """limit·chunks_per_document·top_k는 범위 안의 정수만 허용하는지 확인한다(리뷰 2번)."""
        for limit in (0, 21, 2.5, 10.0, True, '10'):
            self.assert_code('INVALID_LIMIT', search, USER, 'q', limit=limit)
        self.assert_code('INVALID_LIMIT', search, USER, 'q', chunks_per_document=2.5)
        for top_k in (0, 81, 2.5):
            self.assert_code('INVALID_LIMIT', search_chunks, USER, 'q', top_k=top_k)

    def test_min_score_allows_decimals(self):
        """min_score는 0~1의 소수를 허용하고 범위 밖은 INVALID_MIN_SCORE인지 확인한다."""
        self.assertEqual(len(search(USER, 'q', min_score=0.5).results), 1)
        for min_score in (-0.1, 1.1):
            self.assert_code('INVALID_MIN_SCORE', search, USER, 'q', min_score=min_score)

    def test_top_k_follows_limit(self):
        """화면 검색은 limit × 문서당 근거 수만큼, 상한 80 안에서 조회하는지 확인한다."""
        search(USER, 'q', limit=10)
        self.assertEqual(self.calls['top_k'], 40)
        search(USER, 'q', limit=20, chunks_per_document=5)
        self.assertEqual(self.calls['top_k'], 80)


class ScopeTests(SearchTestCase):
    """회사 범위·문서 유형·문서 범위 (F81·F82·F83)."""

    def test_company_scope(self):
        """선택 없음은 허용 전체(정렬), 허용 안 선택은 그대로, 허용 밖 선택은 COMPANY_FORBIDDEN인지 확인한다."""
        self.assertEqual(search(USER, 'q').company_ids, ['A', 'B'])
        self.assertEqual(search(USER, 'q', company_ids=['B', 'B']).company_ids, ['B'])
        self.assert_code('COMPANY_FORBIDDEN', search, USER, 'q', company_ids=['A', 'Z'])

    def test_no_allowed_company(self):
        """허용 회사가 비면 검색하지 않고 NO_COMPANY_SCOPE인지 확인한다."""
        self.allowed = set()
        self.assert_code('NO_COMPANY_SCOPE', search, USER, 'q')
        self.assertNotIn('top_k', self.calls)

    def test_doc_types(self):
        """문서 유형의 공백·중복을 정리하고 허용 밖 값은 INVALID_DOC_TYPE인지 확인한다."""
        self.assertEqual(search(USER, 'q', doc_types=[' policy', 'policy', '']).doc_types, ['policy'])
        self.assertIsNone(search(USER, 'q', doc_types=[]).doc_types)
        self.assert_code('INVALID_DOC_TYPE', search, USER, 'q', doc_types=['contract'])

    def test_empty_document_ids_returns_nothing(self):
        """document_ids=[]는 전체 검색이 아니라 빈 결과이고, None은 제한 없음인지 확인한다(리뷰 3번)."""
        self.assertEqual(search_chunks(USER, 'q', document_ids=[]), [])
        self.assertEqual(search_chunks(USER, 'q', document_ids=['  ']), [])
        self.assertNotIn('where', self.calls)
        self.assertEqual(len(search_chunks(USER, 'q', document_ids=None)), 1)
        self.assertIsNone(self.calls['where'][2])
        search_chunks(USER, 'q', document_ids=[' d1', 'd1'])
        self.assertEqual(self.calls['where'][2], ['d1'])

    def test_empty_document_ids_still_checks_session(self):
        """문서를 고르지 않아도 로그인 만료는 먼저 알려 주는지 확인한다."""
        with patch.object(filters_module, '_load_allowed_company_ids', side_effect=AccessError('SESSION_EXPIRED')):
            self.assert_code('SESSION_EXPIRED', search_chunks, USER, 'q', document_ids=[])


class VisibilityTests(SearchTestCase):
    """최소 점수와 DB 기준 공개 조건 (F85·F86)."""

    def test_low_score_is_removed(self):
        """최소 점수 0.3보다 낮거나 점수가 없는 결과는 빠지는지 확인한다."""
        self.hits = [make_hit('a', 'd1', score=0.2), make_hit('b', 'd1', score=None)]
        self.assertEqual(search_chunks(USER, 'q'), [])

    def test_each_visibility_condition(self):
        """공개 조건이 하나라도 틀리면 결과에서 빠지는지 조건마다 확인한다."""
        cases = {
            'DB에 없는 문서': [],
            '게시 전': [make_row('d1', publication_status='draft')],
            '삭제됨': [make_row('d1', deleted_at='2026-10-01')],
            '옛 버전': [make_row('d1', version_id='v2')],
            '처리 중 버전': [make_row('d1', version_status='processing')],
            '평가 자료': [make_row('d1', data_category='evaluation')],
            '허용 밖 회사': [make_row('d1', company_id='Z')],
            '청크와 DB 회사 불일치': [make_row('d1', company_id='B')],
        }
        for name, rows in cases.items():
            with self.subTest(name):
                self.rows = rows
                self.assertEqual(search_chunks(USER, 'q'), [])

    def test_visible_hit_uses_db_values(self):
        """회사명·파일명·버전은 DB 값, 위치 문구는 메타데이터 값, 빈 section_no는 None인지 확인한다."""
        self.hits = [make_hit('v1:00000', 'd1', section_no='')]
        self.rows = [make_row('d1', company_name='현대', file_name='규정.pdf', version_no=3)]
        hit = search_chunks(USER, 'q')[0]
        self.assertIsInstance(hit, ChunkHit)
        self.assertEqual((hit.company_name, hit.file_name, hit.version_no), ('현대', '규정.pdf', 3))
        self.assertEqual(hit.location_label, 'p.1')
        self.assertIsNone(hit.section_no)


class ResultTests(SearchTestCase):
    """문서 단위로 묶기·정렬·발췌 (F88·F89·F90)."""

    def test_group_sort_and_limit(self):
        """문서 점수는 최고 청크 점수, 같은 점수는 파일명순, 문서당 근거·limit이 지켜지는지 확인한다."""
        self.hits = [make_hit(f'b{i}', 'doc-b', score=0.9 - i * 0.01) for i in range(6)]
        self.hits += [make_hit('a0', 'doc-a', score=0.9), make_hit('c0', 'doc-c', score=0.5)]
        self.rows = [make_row('doc-a'), make_row('doc-b'), make_row('doc-c')]
        response = search(USER, 'q', limit=2)
        self.assertEqual([r.document_id for r in response.results], ['doc-a', 'doc-b'])
        self.assertEqual(len(response.results[1].citations), 4)
        self.assertEqual(response.total_hits, 8)

    def test_excerpt_is_200_chars(self):
        """연속 공백을 한 칸으로 줄이고 200자를 넘으면 잘라 말줄임표를 붙이는지 확인한다."""
        self.hits = [make_hit('a', 'd1', text='가  나\n' * 100)]
        excerpt = search(USER, 'q').results[0].citations[0].excerpt
        self.assertNotIn('  ', excerpt)
        self.assertTrue(excerpt.endswith('…'))
        self.assertLessEqual(len(excerpt), 201)

    def test_no_result_is_not_an_error(self):
        """결과가 없으면 오류가 아니라 빈 목록인지 확인한다."""
        self.hits = []
        self.assertEqual(search(USER, 'q').results, [])


class ErrorTests(SearchTestCase):
    """다른 폴더 오류 변환과 화면 안내 형식 (F91)."""

    def test_dependency_errors(self):
        """embedding·벡터 저장소·auth·db 오류가 정해진 검색 오류 코드로 바뀌는지 확인한다."""
        cases = [
            (EmbeddingError('QUERY_TOO_LONG', 'x'), 'QUERY_TOO_LONG', False),
            (EmbeddingError('RATE_LIMIT', 'x', retryable=True), 'EMBEDDING_UNAVAILABLE', True),
            (VectorStoreError('NO_COMPANY_SCOPE', 'x'), 'NO_COMPANY_SCOPE', False),
            (VectorStoreError('QUERY_FAILED', 'x'), 'SEARCH_UNAVAILABLE', True),
            (AccessError('SESSION_EXPIRED'), 'SESSION_EXPIRED', False),
            (AccessError('ACCESS_DENIED'), 'ACCESS_DENIED', False),
            (DbError('DB_UNAVAILABLE'), 'SEARCH_UNAVAILABLE', True),
            (DbError('INVALID_INPUT'), 'SEARCH_UNAVAILABLE', False),
        ]
        for error, code, retryable in cases:
            with self.subTest(code=getattr(error, 'code', None)):
                converted = to_search_error(error)
                self.assertEqual((converted.code, converted.retryable), (code, retryable))

    def test_unexpected_error_message_is_hidden(self):
        """예상하지 못한 오류는 SEARCH_FAILED가 되고 원문을 화면 문구에 넣지 않는지 확인한다."""
        with self.assertLogs('hyundap.search.errors', level='ERROR'):
            converted = to_search_error(RuntimeError('mysql://secret@host'))
        self.assertEqual(converted.code, 'SEARCH_FAILED')
        self.assertNotIn('secret', converted.message)

    def test_errors_during_search(self):
        """검색 도중 auth·embedding·db 오류가 나면 검색 오류로 올라오는지 확인한다."""
        with patch.object(filters_module, '_load_allowed_company_ids', side_effect=AccessError('ACCESS_DENIED')):
            self.assert_code('ACCESS_DENIED', search, USER, 'q')
        with patch.object(search_module, '_embed_query', side_effect=EmbeddingError('NO_API_KEY', 'x')):
            self.assert_code('EMBEDDING_UNAVAILABLE', search, USER, 'q')
        with patch.object(search_module, '_load_document_rows', side_effect=DbError('DB_UNAVAILABLE')):
            self.assert_code('SEARCH_UNAVAILABLE', search, USER, 'q')

    def test_bug_in_db_lookup_is_not_hidden(self):
        """DB 조회 함수의 코드 버그는 검색 오류로 숨기지 않고 그대로 올라오는지 확인한다(DB 계약 2절)."""
        with patch.object(search_module, '_load_document_rows', side_effect=KeyError('bug')):
            with self.assertRaises(KeyError):
                search(USER, 'q')

    def test_to_feedback_for_ui(self):
        """SearchError를 UI 상태 문자열로 바꾸고, 다른 오류는 None을 돌려주는지 확인한다."""
        expected = {'EMPTY_QUERY': 'input_error', 'COMPANY_FORBIDDEN': 'denied',
                    'SESSION_EXPIRED': 'session_expired', 'SEARCH_UNAVAILABLE': 'failed'}
        for code, status in expected.items():
            with self.subTest(code=code):
                feedback = to_feedback(SearchError(code))
                self.assertEqual((feedback['status'], feedback['error_code']), (status, code))
                self.assertTrue(feedback['message'])
        self.assertIsNone(to_feedback(ValueError('x')))


class ContractTests(unittest.TestCase):
    """다른 폴더가 기대하는 형식 (chatbot·db 계약)."""

    def test_chunk_hit_fields_for_chatbot(self):
        """chatbot이 이름으로 읽고 직접 만드는 ChunkHit 13개 필드가 그대로인지 확인한다."""
        fields = ['chunk_id', 'document_id', 'version_id', 'version_no', 'company_id', 'company_name',
                  'doc_type', 'file_name', 'section_no', 'location_label', 'chunk_index', 'text', 'score']
        self.assertEqual(list(ChunkHit.__dataclass_fields__), fields)

    def test_document_info_matches_db_rows(self):
        """DocumentInfo 필드가 DB 계약의 11필드와 같은지 확인한다."""
        self.assertEqual(list(DocumentInfo.__dataclass_fields__), list(make_row('d1')))


if __name__ == '__main__':
    unittest.main()
