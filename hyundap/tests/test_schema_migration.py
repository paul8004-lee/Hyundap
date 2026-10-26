"""격리 MySQL 전용 검증. 공유 DB URL을 받지 않으며 기존 테이블이 있으면 중단한다."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from db import connection, schema


BASELINE = '''
CREATE TABLE schema_migrations (version INT NOT NULL);
INSERT INTO schema_migrations VALUES (3);
CREATE TABLE companies (
 id VARCHAR(36) PRIMARY KEY, name VARCHAR(100) NOT NULL, is_active BOOLEAN NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE users (
 id VARCHAR(36) PRIMARY KEY, company_id VARCHAR(36), email VARCHAR(254),
 role VARCHAR(32) NOT NULL, is_active BOOLEAN NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE documents (
 id VARCHAR(36) PRIMARY KEY, company_id VARCHAR(36) NOT NULL, legacy_note VARCHAR(100),
 deleted_at DATETIME(6) NULL,
 CONSTRAINT legacy_document_company FOREIGN KEY (company_id) REFERENCES companies(id),
 UNIQUE KEY legacy_document_identity (id,company_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE sessions (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL) ENGINE=InnoDB;
CREATE TABLE index_jobs (id VARCHAR(36) PRIMARY KEY) ENGINE=InnoDB;
CREATE TABLE oauth_states (id VARCHAR(36) PRIMARY KEY) ENGINE=InnoDB;
CREATE TABLE audit_logs (id VARCHAR(36) PRIMARY KEY) ENGINE=InnoDB;
CREATE TABLE login_attempts (id VARCHAR(36) PRIMARY KEY) ENGINE=InnoDB;
INSERT INTO companies (id,name) VALUES ('company-a','Synthetic A'),('company-b','Synthetic B');
INSERT INTO users (id,company_id,email,role) VALUES ('user-a','company-a','Example@Test.invalid','EMPLOYEE');
INSERT INTO documents (id,company_id,legacy_note) VALUES
 ('document-a','company-a','preserve me'),('document-b','company-b','preserve me too');
INSERT INTO sessions (id,user_id) VALUES ('old-session','user-a');
'''


def execute_script(database, script):
    """테스트 파일에 한해 단순 SQL 문장을 실행한다. 운영 마이그레이션 실행기가 아니다."""
    lines = [line for line in script.splitlines() if not line.lstrip().startswith('--')]
    for statement in '\n'.join(lines).split(';'):
        if statement.strip():
            database.execute(text(statement))


class SchemaMigrationTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv('DB_MIGRATION_TEST_URL'), 'isolated MySQL URL required')
    def test_additive_migration_and_database_guards(self):
        url = make_url(os.environ['DB_MIGRATION_TEST_URL'])
        self.assertEqual(url.host, '127.0.0.1')
        self.assertEqual(url.database, 'hyundap_schema_test')
        engine = create_engine(url)
        self.addCleanup(engine.dispose)
        with engine.begin() as database:
            self.assertEqual(inspect(database).get_table_names(), [], 'Refuse an existing database')
            execute_script(database, BASELINE)
        with tempfile.TemporaryDirectory() as directory:
            output_file = Path(directory) / '004_local.sql'
            output = io.StringIO()
            with patch.object(schema, 'create_db_engine', return_value=engine), \
                    patch('sys.argv', ['schema', '--prepare', str(output_file)]), redirect_stdout(output):
                self.assertEqual(schema.main(), 0)
            self.assertIn('no database writes executed', output.getvalue())
            self.assertTrue(output_file.is_file())
        with engine.connect() as database:
            original = schema.snapshot(database)
            migration = schema.prepare_sql(original)
            self.assertNotIn('ADD COLUMN `deleted_at`', migration)
            self.assertNotIn('ADD CONSTRAINT `uq_v4_document_company`', migration)
            self.assertNotIn('ADD CONSTRAINT `fk_v4_document_company`', migration)
            self.assertIn('utf8mb4_unicode_ci', migration)
            self.assertNotIn('DROP ', migration.upper())
            self.assertNotIn('DELETE ', migration.upper().replace('ON DELETE RESTRICT', ''))
            self.assertNotIn('UPDATE schema_migrations', migration)
            self.assertTrue(schema.validate_v4(original))
        with engine.begin() as database:
            execute_script(database, migration)
        with engine.connect() as database:
            layout = schema.snapshot(database)
            self.assertEqual(layout['version'], 3, 'DDL must not advance version marker')
            self.assertEqual(schema.validate_v4(layout), [])
            self.assertTrue(all(row['violations'] == 0 for row in schema.audit_counts(database)))
            self.assertEqual(database.execute(text('SELECT legacy_note FROM documents WHERE id=\'document-a\'')).scalar_one(), 'preserve me')
            self.assertEqual(database.execute(text('SELECT publication_status FROM documents WHERE id=\'document-a\'')).scalar_one(), 'draft')
            self.assertEqual(database.execute(text('SELECT auth_version FROM sessions WHERE id=\'old-session\'')).scalar_one(), 0)
            with self.assertRaisesRegex(ValueError, 'Existing v4 tables'):
                schema.prepare_sql(layout)
        version_insert = '''INSERT INTO document_versions
            (id,document_id,company_id,version_no,file_name,storage_path,sha256,file_size)
            VALUES (:id,:document,:company,:number,'test.txt','test/test.txt',:hash,1)'''
        def add_version(identifier, document, company, number, digest):
            with engine.begin() as database:
                database.execute(text(version_insert), {'id': identifier, 'document': document,
                    'company': company, 'number': number, 'hash': digest})
        add_version('version-a', 'document-a', 'company-a', 1, 'a' * 64)
        add_version('version-b', 'document-b', 'company-b', 1, 'a' * 64)
        failures = [
            ('wrong-company', 'document-a', 'company-b', 2, 'b' * 64),
            ('duplicate-number', 'document-a', 'company-a', 1, 'b' * 64),
            ('duplicate-hash', 'document-a', 'company-a', 2, 'a' * 64),
        ]
        for values in failures:
            with self.subTest(values=values), self.assertRaises((IntegrityError, OperationalError)):
                add_version(*values)
        with engine.begin() as database:
            database.execute(text('''INSERT INTO document_chunks VALUES
                ('version-a:00000','version-a',0,:hash)'''), {'hash': 'b' * 64})
        invalid_statements = [
            "UPDATE documents SET active_version_id='version-b' WHERE id='document-a'",
            "INSERT INTO document_chunks VALUES ('bad-id','version-a',1,REPEAT('b',64))",
            "INSERT INTO document_chunks VALUES ('version-a:00000','version-a',0,REPEAT('b',64))",
            "UPDATE documents SET publication_status='published' WHERE id='document-a'",
            "UPDATE document_versions SET version_status='ready' WHERE id='version-a'",
            "INSERT INTO users (id,email,role) VALUES ('user-duplicate',' example@test.invalid ','EMPLOYEE')",
        ]
        for statement in invalid_statements:
            with self.subTest(statement=statement), self.assertRaises((IntegrityError, OperationalError)):
                with engine.begin() as database:
                    database.execute(text(statement))
        with engine.begin() as database:
            database.execute(text("UPDATE document_versions SET expected_chunk_count=2,version_status='ready' WHERE id='version-a'"))
        with engine.connect() as database:
            counts = schema.audit_counts(database)
            self.assertEqual(next(row['violations'] for row in counts if row['check_name']=='ready_manifest'), 1)
        with engine.begin() as database:
            database.execute(text("UPDATE document_versions SET expected_chunk_count=1 WHERE id='version-a'"))
            database.execute(text("UPDATE documents SET active_version_id='version-a',publication_status='published' WHERE id='document-a'"))
            self.assertTrue(all(row['violations']==0 for row in schema.audit_counts(database)))
            database.execute(text('UPDATE schema_migrations SET version=4 WHERE version=3'))
        output = io.StringIO()
        with patch.object(connection, 'create_db_engine', return_value=engine), redirect_stdout(output):
            self.assertEqual(connection.main(), 0)
        self.assertIn('version_storage_ready=True', output.getvalue())
        with engine.begin() as database:
            database.execute(text('ALTER TABLE document_chunks DROP CHECK ck_v4_chunk_index'))
        with engine.connect() as database:
            self.assertTrue(schema.validate_v4(schema.snapshot(database)))


if __name__ == '__main__':
    unittest.main()
