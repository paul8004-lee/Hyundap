"""최종 v4 직접 생성·구조 훼손 탐지·실패 안전성을 격리 MySQL에서 검증한다."""

import io
import os
from pathlib import Path
from uuid import uuid4
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from hyundap.db import clock, connection, runtime, schema


class SchemaOfflineTests(unittest.TestCase):
    def test_expression_preserves_literals_and_non_ascii_charset(self):
        cases = (
            (None, None),
            (42, '42'),
            ('', ''),
            (" `File_Name` = _utf8mb4'ABC' ", "file_name='ABC'"),
            ("`Name` = _ascii'O''Reilly'", "name='O''Reilly'"),
            ("`Name` = _utf8mb4'한글ABC'", "name=_utf8mb4'한글ABC'"),
            ("(`State` IN ('Ready', 'FAILED'))", "(statein('Ready','FAILED'))"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(schema._expression(source), expected)

    def test_unix_seconds_cannot_be_changed_by_legacy_environment(self):
        with patch.dict(os.environ, {'DB_LEGACY_TIME_UNIT': 'milliseconds'}), \
                patch.object(clock.time, 'time_ns', return_value=123456789000):
            self.assertEqual(clock.epoch_now(), 123)

    def test_non_mysql_creator_does_not_open_connection(self):
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with patch.object(engine, 'connect') as connect, self.assertRaises(ValueError):
            schema.create_v4_schema(engine)
        connect.assert_not_called()


@unittest.skipUnless(os.getenv('DB_SCHEMA_TEST_URL'), '격리 MySQL URL 필요')
class SchemaV4Tests(unittest.TestCase):
    def setUp(self):
        url = make_url(os.environ['DB_SCHEMA_TEST_URL'])
        if url.host != '127.0.0.1' or url.database != 'hyundap_schema_test':
            raise RuntimeError('로컬 전용 스키마 DB만 허용한다.')
        admin = create_engine(url, isolation_level='AUTOCOMMIT')
        self.addCleanup(admin.dispose)
        name = 'hyundap_v4_' + uuid4().hex
        with admin.connect() as database:
            database.execute(text('CREATE DATABASE ' + name))
        self.engine = create_engine(url.set(database=name), hide_parameters=True,
            connect_args={'init_command': "SET time_zone='+00:00'"})
        self.addCleanup(self.engine.dispose)

    def test_direct_creation_structure_version_and_readonly_cli(self):
        schema.create_v4_schema(self.engine)
        with self.engine.connect() as database:
            layout = schema.snapshot(database)
            self.assertEqual(len(layout['tables']), 11)
            self.assertEqual(layout['version'], 4)
            self.assertEqual(schema.validate_v4(layout), [])
            self.assertTrue(all(row['violations'] == 0 for row in schema.audit_counts(database)))
            self.assertNotIn('filename', layout['tables']['documents'])
            self.assertNotIn('attempt_no', layout['tables']['index_jobs'])
        output = io.StringIO()
        with patch.object(connection, 'create_db_engine', return_value=self.engine), redirect_stdout(output):
            self.assertEqual(connection.main(), 0)

    def test_existing_objects_are_rejected_without_writes(self):
        with self.engine.begin() as database:
            database.execute(text('CREATE TABLE existing (id INT PRIMARY KEY)'))
            database.execute(text('INSERT INTO existing VALUES (7)'))
        statements = []
        def capture(conn, cursor, sql, parameters, context, many):
            statements.append(sql)
        event.listen(self.engine, 'before_cursor_execute', capture)
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            schema.create_v4_schema(self.engine)
        self.assertFalse(any(sql.lstrip().upper().startswith(('CREATE', 'ALTER', 'INSERT', 'DROP', 'UPDATE'))
                             for sql in statements))
        with self.engine.connect() as database:
            self.assertEqual(inspect(database).get_table_names(), ['existing'])
            self.assertEqual(database.scalar(text('SELECT id FROM existing')), 7)

    def test_partial_ddl_failure_never_records_success_or_drops_objects(self):
        def fail(conn, cursor, sql, parameters, context, many):
            if sql.lstrip().startswith('CREATE TABLE document_versions'):
                raise RuntimeError('synthetic DDL failure')
        event.listen(self.engine, 'before_cursor_execute', fail)
        with self.assertRaises(RuntimeError):
            schema.create_v4_schema(self.engine)
        event.remove(self.engine, 'before_cursor_execute', fail)
        with self.engine.connect() as database:
            tables = inspect(database).get_table_names()
            self.assertIn('documents', tables)
            self.assertNotIn('schema_migrations', tables)
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            schema.create_v4_schema(self.engine)

    def test_validation_failure_leaves_empty_version_table(self):
        with patch.object(schema, 'validate_v4', return_value=['synthetic mismatch']), self.assertRaises(ValueError):
            schema.create_v4_schema(self.engine)
        with self.engine.connect() as database:
            self.assertEqual(database.scalar(text('SELECT COUNT(*) FROM schema_migrations')), 0)
            with self.assertRaises(ValueError):
                schema.snapshot(database)

    def test_concurrent_initializers_cannot_interleave_ddl(self):
        entered, release = Event(), Event()
        def pause(conn, cursor, sql, parameters, context, many):
            if sql.lstrip().startswith('CREATE TABLE companies'):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError('initializer did not resume')
        event.listen(self.engine, 'before_cursor_execute', pause)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(schema.create_v4_schema, self.engine)
                try:
                    self.assertTrue(entered.wait(10))
                    with self.assertRaisesRegex(ValueError, 'already running'):
                        schema.create_v4_schema(self.engine)
                finally:
                    release.set()
                first.result(timeout=20)
        finally:
            event.remove(self.engine, 'before_cursor_execute', pause)
        with self.engine.connect() as database:
            self.assertEqual(schema.validate_v4(schema.snapshot(database)), [])

    def test_every_column_and_constraint_property_is_checked(self):
        schema.create_v4_schema(self.engine)
        alterations = (
            ('ALTER TABLE users MODIFY password_hash VARCHAR(254) NULL',
             'ALTER TABLE users MODIFY password_hash VARCHAR(255) NULL'),
            ('ALTER TABLE companies MODIFY name VARCHAR(100) NULL',
             'ALTER TABLE companies MODIFY name VARCHAR(100) NOT NULL'),
            ('ALTER TABLE companies ALTER is_active SET DEFAULT 0',
             'ALTER TABLE companies ALTER is_active SET DEFAULT 1'),
            ("ALTER TABLE document_versions ALTER version_status SET DEFAULT 'Processing'",
             "ALTER TABLE document_versions ALTER version_status SET DEFAULT 'processing'"),
            ('ALTER TABLE index_jobs DROP CHECK ck_job_attempts',
             'ALTER TABLE index_jobs ADD CONSTRAINT ck_job_attempts CHECK (attempts >= 1)'),
            ('ALTER TABLE sessions ALTER CHECK sessions_ck_3 NOT ENFORCED',
             'ALTER TABLE sessions ALTER CHECK sessions_ck_3 ENFORCED'),
        )
        for sql, restore in alterations:
            with self.subTest(sql=sql), self.engine.begin() as database:
                database.execute(text(sql))
                self.assertTrue(schema.validate_v4(schema.snapshot(database)))
                database.execute(text(restore))
                self.assertEqual(schema.validate_v4(schema.snapshot(database)), [])
        with self.engine.begin() as database:
            database.execute(text('ALTER TABLE index_jobs DROP CHECK ck_job_attempts'))
            database.execute(text('ALTER TABLE index_jobs ADD CONSTRAINT ck_job_attempts CHECK (attempts >= 0)'))
            self.assertIn('index_jobs: incompatible checks', schema.validate_v4(schema.snapshot(database)))

    def test_final_constraints_reject_cross_scope_and_duplicate_jobs(self):
        schema.create_v4_schema(self.engine)
        with self.engine.begin() as database:
            database.execute(text("INSERT INTO companies (id,name) VALUES ('c','Company'),('other','Other')"))
            database.execute(text("INSERT INTO users (id,email,role,company_id) VALUES ('u','test@example.invalid','company_admin','c')"))
            database.execute(text("INSERT INTO documents (id,company_id,uploaded_by) VALUES ('d','c','u'),('other','other','u')"))
            database.execute(text("INSERT INTO document_versions (id,document_id,company_id,version_no,file_name,storage_path,sha256,file_size) VALUES ('v','d','c',1,'a.txt','a',REPEAT('a',64),1),('v2','d','c',2,'b.txt','b',REPEAT('b',64),1)"))
            database.execute(text("INSERT INTO index_jobs (id,version_id,document_id,company_id) VALUES ('j','v','d','c')"))
        invalid = (
            "INSERT INTO index_jobs (id,version_id,document_id,company_id) VALUES ('j2','v','d','c')",
            "INSERT INTO index_jobs (id,version_id,document_id,company_id) VALUES ('j2','v2','d','c')",
            "INSERT INTO index_jobs (id,version_id,document_id,company_id) VALUES ('j2','v2','other','other')",
            "UPDATE documents SET active_version_id='v' WHERE id='other'",
            "UPDATE document_versions SET version_status='ready' WHERE id='v'",
            "INSERT INTO document_chunks VALUES ('bad','v',0,REPEAT('a',64))",
            "UPDATE index_jobs SET status='FAILED' WHERE id='j'",
            "INSERT INTO document_versions (id,document_id,company_id,version_no,file_name,storage_path,sha256,file_size) VALUES ('bad','d','other',3,'c','c',REPEAT('c',64),1)",
            "INSERT INTO document_versions (id,document_id,company_id,version_no,file_name,storage_path,sha256,file_size) VALUES ('bad','d','c',1,'c','c',REPEAT('c',64),1)",
            "INSERT INTO document_versions (id,document_id,company_id,version_no,file_name,storage_path,sha256,file_size) VALUES ('bad','d','c',3,'c','c',REPEAT('a',64),1)",
            "INSERT INTO document_versions (id,document_id,company_id,version_no,file_name,storage_path,sha256,file_size) VALUES ('bad','d','c',3,'c','c',REPEAT('c',64),0)",
            "INSERT INTO users (id,email,role) VALUES ('dup',' TEST@EXAMPLE.INVALID ','platform_admin')",
            "INSERT INTO sessions (id,user_id,token_hash,auth_version,created_at,last_seen_at,idle_expires_at,absolute_expires_at,expires_at) VALUES ('s','u',REPEAT('a',64),1,1,1,2,3,'2099-01-01')",
        )
        for sql in invalid:
            with self.subTest(sql=sql), self.assertRaises((IntegrityError, OperationalError)):
                with self.engine.begin() as database:
                    database.execute(text(sql))

    def test_generated_keys_and_fk_index_changes_are_detected(self):
        schema.create_v4_schema(self.engine)
        with self.engine.connect() as database:
            original = schema.snapshot(database)
        for table, field in (('users', 'unique'), ('index_jobs', 'indexes'),
                             ('index_jobs', 'foreign'), ('documents', 'primary')):
            with self.subTest(table=table, field=field):
                changed = deepcopy(original)
                changed['keys'][table][field].clear()
                self.assertTrue(schema.validate_v4(changed))
        for table, column in (('users', 'login_identifier'), ('index_jobs', 'active_document_id')):
            with self.subTest(table=table, column=column):
                changed = deepcopy(original)
                changed['tables'][table][column]['GENERATION_EXPRESSION'] = 'NULL'
                self.assertIn(table + ': incompatible columns', schema.validate_v4(changed))

    def test_business_engine_refuses_old_structure_before_use(self):
        with self.engine.begin() as database:
            database.execute(text('CREATE TABLE schema_migrations (version INT)'))
            database.execute(text('INSERT INTO schema_migrations VALUES (3)'))
        runtime.get_engine.cache_clear()
        self.addCleanup(runtime.get_engine.cache_clear)
        with patch.object(runtime, 'create_db_engine', return_value=self.engine):
            from hyundap.db.errors import DbError
            with self.assertRaises(DbError):
                runtime.get_engine()
