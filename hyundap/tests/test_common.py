"""common/ schemas and settings must match what other folders expect."""
import unittest
from pathlib import Path
from unittest.mock import patch

from common import config
from common.schemas import CommonDocument, Section, make_chunk_id

HEADER = {'document_id': 'd1', 'version_id': 'v1', 'company_id': 'c1',
          'company_name': '현대', 'doc_type': 'report', 'file_name': 'a.pdf'}


class SchemaTests(unittest.TestCase):
    def test_common_document_accepts_full_header(self):
        doc = CommonDocument(header=HEADER, sections=[Section(1, 'p.1', 'hello')])
        self.assertEqual(doc.sections[0].location_label, 'p.1')
        self.assertEqual(doc.warnings, [])

    def test_common_document_rejects_missing_header_keys(self):
        with self.assertRaises(ValueError) as caught:
            CommonDocument(header={'document_id': 'd1'})
        self.assertIn('version_id', str(caught.exception))

    def test_default_lists_are_not_shared(self):
        first, second = CommonDocument(header=HEADER), CommonDocument(header=HEADER)
        first.warnings.append('empty page')
        self.assertEqual(second.warnings, [])

    def test_chunk_id_format(self):
        self.assertEqual(make_chunk_id('v1', 3), 'v1:00003')


class SettingsTests(unittest.TestCase):
    def load(self, env):
        config.get_settings.cache_clear()
        with patch.dict('os.environ', env, clear=True), patch.object(config, 'load_dotenv'):
            settings = config.get_settings()
        config.get_settings.cache_clear()
        return settings

    def test_defaults_when_env_empty(self):
        settings = self.load({})
        self.assertIsNone(settings.openai_api_key)
        self.assertEqual(settings.openai_embedding_model, 'text-embedding-3-small')
        self.assertEqual(settings.search_min_score, 0.3)
        self.assertEqual(settings.search_top_k_max, 80)
        self.assertEqual(settings.chroma_db_path, config.PROJECT_ROOT / 'data/chroma_db')

    def test_reads_env_values(self):
        settings = self.load({'OPENAI_API_KEY': 'sk-test', 'SEARCH_MIN_SCORE': '0.5',
                              'SEARCH_TOP_K_MAX': '40', 'CHROMA_DB_PATH': str(Path('/tmp/chroma').resolve())})
        self.assertEqual(settings.openai_api_key, 'sk-test')
        self.assertEqual(settings.search_min_score, 0.5)
        self.assertEqual(settings.search_top_k_max, 40)
        self.assertEqual(settings.chroma_db_path, Path('/tmp/chroma').resolve())

    def test_invalid_numbers_fall_back_to_defaults(self):
        settings = self.load({'SEARCH_MIN_SCORE': '1.5', 'SEARCH_TOP_K_MAX': 'abc'})
        self.assertEqual(settings.search_min_score, 0.3)
        self.assertEqual(settings.search_top_k_max, 80)

    def test_settings_are_read_only(self):
        with self.assertRaises(AttributeError):
            self.load({}).search_min_score = 0.9


if __name__ == '__main__':
    unittest.main()
