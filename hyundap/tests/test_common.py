"""tests/test_common.py — 공통 데이터 형식과 설정의 기본 동작 검증.

테스트 실행 명령은 저장소 맨 위에서 python -m unittest hyundap.tests.test_common -v이다."""
import unittest
from pathlib import Path
from unittest.mock import patch

from hyundap.common import config
from hyundap.common.schemas import CommonDocument, Section, make_chunk_id

HEADER = {'document_id': 'd1', 'version_id': 'v1', 'company_id': 'c1',
          'company_name': '현대', 'doc_type': 'report', 'file_name': 'a.pdf'}


class SchemaTests(unittest.TestCase):
    """공통 문서·섹션·청크 ID가 다른 모듈과의 약속을 지키는지 확인한다."""
    def test_common_document_accepts_full_header(self):
        """필수 헤더와 섹션을 가진 문서가 생성되고 경고 기본값이 빈 목록인지 확인한다."""
        doc = CommonDocument(header=HEADER, sections=[Section(1, 'p.1', 'hello')])
        self.assertEqual(doc.sections[0].location_label, 'p.1')
        self.assertEqual(doc.warnings, [])

    def test_common_document_rejects_missing_header_keys(self):
        """필수 헤더가 빠지면 누락 키가 포함된 ValueError가 발생하는지 확인한다."""
        with self.assertRaises(ValueError) as caught:
            CommonDocument(header={'document_id': 'd1'})
        self.assertIn('version_id', str(caught.exception))

    def test_default_lists_are_not_shared(self):
        """문서 하나의 경고를 바꿔도 다른 문서의 기본 목록이 변하지 않는지 확인한다."""
        first, second = CommonDocument(header=HEADER), CommonDocument(header=HEADER)
        first.warnings.append('empty page')
        self.assertEqual(second.warnings, [])

    def test_chunk_id_format(self):
        """순번 3이 버전 ID 뒤의 5자리 청크 번호 00003으로 표시되는지 확인한다."""
        self.assertEqual(make_chunk_id('v1', 3), 'v1:00003')


class SettingsTests(unittest.TestCase):
    """환경변수·기본값·읽기 전용 속성에 대한 공통 설정 계약을 검증한다."""
    def load(self, env):
        """주어진 테스트 환경 딕셔너리만 사용해 Settings를 읽는다.

        실제 .env 로딩을 막고 호출 전후 캐시를 비워 테스트가 서로 영향을 주지 않게 한다."""
        config.get_settings.cache_clear()
        with patch.dict('os.environ', env, clear=True), patch.object(config, 'load_dotenv'):
            settings = config.get_settings()
        config.get_settings.cache_clear()
        return settings

    def test_defaults_when_env_empty(self):
        """환경변수가 없으면 비밀값 없이 문서화된 기본 설정을 반환하는지 확인한다."""
        settings = self.load({})
        self.assertIsNone(settings.openai_api_key)
        self.assertEqual(settings.openai_embedding_model, 'text-embedding-3-small')
        self.assertEqual(settings.openai_chat_model, 'gpt-5')
        self.assertEqual(settings.search_min_score, 0.3)
        self.assertEqual(settings.search_top_k_max, 80)
        self.assertEqual(settings.chroma_db_path, config.PROJECT_ROOT / 'data/chroma_db')

    def test_reads_env_values(self):
        """테스트 환경변수의 키·숫자·경로가 설정에 반영되는지 확인한다."""
        settings = self.load({'OPENAI_API_KEY': 'sk-test', 'SEARCH_MIN_SCORE': '0.5',
                              'SEARCH_TOP_K_MAX': '40', 'CHROMA_DB_PATH': str(Path('/tmp/chroma').resolve())})
        self.assertEqual(settings.openai_api_key, 'sk-test')
        self.assertEqual(settings.search_min_score, 0.5)
        self.assertEqual(settings.search_top_k_max, 40)
        self.assertEqual(settings.chroma_db_path, Path('/tmp/chroma').resolve())

    def test_invalid_numbers_fall_back_to_defaults(self):
        """잘못된 숫자나 범위 밖 점수에 기본값이 적용되는지 확인한다."""
        settings = self.load({'SEARCH_MIN_SCORE': '1.5', 'SEARCH_TOP_K_MAX': 'abc'})
        self.assertEqual(settings.search_min_score, 0.3)
        self.assertEqual(settings.search_top_k_max, 80)

    def test_settings_are_read_only(self):
        """고정된 Settings 필드를 직접 바꾸면 오류가 발생하는지 확인한다."""
        with self.assertRaises(AttributeError):
            self.load({}).search_min_score = 0.9


if __name__ == '__main__':
    unittest.main()
