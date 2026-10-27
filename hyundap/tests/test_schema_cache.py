"""테이블 구조의 Engine별 캐시·다른 연결 재사용·DDL 후 명시적 무효화 검증."""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text

from hyundap.db import runtime


class SchemaCacheTests(unittest.TestCase):
    def setUp(self):
        self.first = create_engine('sqlite://')
        self.second = create_engine('sqlite://')
        self.addCleanup(self.first.dispose)
        self.addCleanup(self.second.dispose)
        self.addCleanup(runtime.clear_schema_cache)
        with self.first.begin() as database:
            database.execute(text('CREATE TABLE example (id TEXT)'))
        with self.second.begin() as database:
            database.execute(text('CREATE TABLE example (other_id TEXT)'))

    def test_same_engine_reuses_reflection_across_connections(self):
        with patch.object(runtime, 'inspect', wraps=runtime.inspect) as reader:
            with self.first.connect() as database:
                initial = runtime.get_table_columns(database, 'example')
            with self.first.connect() as database:
                repeated = runtime.get_table_columns(database, 'example')
            self.assertEqual(reader.call_count, 1)
            self.assertEqual([column['name'] for column in initial], ['id'])
            self.assertEqual(initial, repeated)

    def test_engines_do_not_share_same_table_name(self):
        with self.first.connect() as first, self.second.connect() as second:
            self.assertEqual([c['name'] for c in runtime.get_table_columns(first, 'example')], ['id'])
            self.assertEqual([c['name'] for c in runtime.get_table_columns(second, 'example')], ['other_id'])

    def test_targeted_invalidation_after_ddl_keeps_other_engine_cache(self):
        with self.first.begin() as first, self.second.connect() as second:
            runtime.get_table_columns(first, 'example')
            runtime.get_table_columns(second, 'example')
            first.execute(text('ALTER TABLE example ADD COLUMN added TEXT'))
            self.assertEqual(len(runtime.get_table_columns(first, 'example')), 1)
            runtime.clear_schema_cache(self.first)
            with patch.object(runtime, 'inspect', wraps=runtime.inspect) as reader:
                self.assertEqual(len(runtime.get_table_columns(first, 'example')), 2)
                runtime.get_table_columns(second, 'example')
                self.assertEqual(reader.call_count, 1)

    def test_failed_reflection_is_not_cached_and_global_reset_reflects_again(self):
        with self.first.begin() as database:
            with patch.object(runtime, 'inspect', side_effect=RuntimeError('synthetic lookup failure')):
                with self.assertRaises(RuntimeError):
                    runtime.get_table_columns(database, 'example')
            self.assertEqual(len(runtime.get_table_columns(database, 'example')), 1)
            runtime.clear_schema_cache()
            with patch.object(runtime, 'inspect', wraps=runtime.inspect) as reader:
                runtime.get_table_columns(database, 'example')
                self.assertEqual(reader.call_count, 1)
