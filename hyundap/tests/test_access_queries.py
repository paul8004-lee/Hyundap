"""DB 조회 계약 회귀. 합성 SQLite 자료만 사용하며 MySQL 잠금 검증과 구분한다."""

import hashlib
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from hyundap.auth.errors import AccessError
from hyundap.auth.permissions import get_allowed_company_ids, verify_documents_access
from hyundap.db import documents_repo, runtime
from hyundap.db.errors import DbError
from hyundap.db.sessions_repo import current_actor


class AccessQueryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        self.patch_engine = patch.object(runtime, 'get_engine', return_value=self.engine)
        self.patch_engine.start()
        self.addCleanup(self.patch_engine.stop)
        self.token = 'synthetic-session-' + 'a' * 36
        self.actor = dict(session_id='session', _session_token=self.token,
                          user_id='forged-user', company_id='company-b', role='platform_admin',
                          auth_version=999)
        with self.engine.begin() as db:
            db.connection.driver_connection.create_function('UTC_TIMESTAMP', 1, lambda _: '2026-10-26 00:00:00')
            for sql in (
                'CREATE TABLE companies (id TEXT PRIMARY KEY,name TEXT,is_active INTEGER)',
                'CREATE TABLE users (id TEXT PRIMARY KEY,company_id TEXT,role TEXT,auth_version INTEGER,is_active INTEGER)',
                'CREATE TABLE sessions (id TEXT PRIMARY KEY,user_id TEXT,token_hash TEXT,auth_version INTEGER,expires_at TEXT,revoked_at TEXT,idle_expires_at INTEGER,absolute_expires_at INTEGER)',
                'CREATE TABLE documents (id TEXT PRIMARY KEY,company_id TEXT,doc_type TEXT,publication_status TEXT,deleted_at TEXT,active_version_id TEXT)',
                'CREATE TABLE document_versions (id TEXT PRIMARY KEY,document_id TEXT,company_id TEXT,version_no INTEGER,version_status TEXT,data_category TEXT,file_name TEXT)',
            ):
                db.execute(text(sql))
            db.execute(text("INSERT INTO companies VALUES ('company-a','Company A',1),('company-b','Company B',1),('inactive','Inactive',0)"))
            db.execute(text("INSERT INTO users VALUES ('user','company-a','company_admin',1,1)"))
            db.execute(text("INSERT INTO sessions VALUES ('session','user',:digest,1,'2099-01-01 00:00:00',NULL,4102444800,4102444800)"),
                       {'digest': hashlib.sha256(self.token.encode()).hexdigest()})
            db.execute(text("INSERT INTO documents VALUES ('document','company-a','manual','published',NULL,'version'),('draft','company-a','other','draft',NULL,NULL)"))
            db.execute(text("INSERT INTO document_versions VALUES ('version','document','company-a',1,'ready','operational','sample.pdf')"))
            db.execute(text('ALTER TABLE document_versions ADD COLUMN file_size INTEGER DEFAULT 123'))

    def change(self, sql):
        with self.engine.begin() as db:
            db.execute(text(sql))

    def test_detail_returns_only_public_fields_for_each_current_role(self):
        expected = {'document_id': 'document', 'company_id': 'company-a',
                    'company_name': 'Company A', 'version_id': 'version',
                    'file_name': 'sample.pdf', 'doc_type': 'manual',
                    'version_no': 1, 'size_bytes': 123, 'indexed_at': None}
        original = dict(self.actor)
        for role in ('employee', 'company_admin', 'platform_admin'):
            with self.subTest(role=role):
                with self.engine.begin() as db:
                    db.execute(text('UPDATE users SET role=:role'), {'role': role})
                    result = documents_repo.get_document_detail(
                        self.actor, 'document', version_id='version', connection=db)
                    self.assertEqual(result, expected)
                    self.assertTrue(db.in_transaction())
        self.assertEqual(self.actor, original)
        self.assertEqual(documents_repo.get_document_detail(self.actor, 'document'), expected)

    def test_detail_rejects_unavailable_and_changed_versions(self):
        cases = (
            ('missing', None), ('draft', None), ('document', 'old-version'),
        )
        for document_id, version_id in cases:
            with self.subTest(document=document_id, version=version_id):
                with self.assertRaises(AccessError) as caught:
                    documents_repo.get_document_detail(self.actor, document_id, version_id=version_id)
                self.assertEqual(caught.exception.code, 'STATE_CHANGED')
        changes = (
            "UPDATE documents SET deleted_at='2026-10-26' WHERE id='document'",
            "UPDATE documents SET publication_status='blocked' WHERE id='document'",
            "UPDATE documents SET active_version_id=NULL WHERE id='document'",
            "UPDATE document_versions SET version_status='processing'",
            "UPDATE document_versions SET data_category='evaluation'",
            "UPDATE document_versions SET company_id='company-b'",
        )
        # 각 변경은 별도 트랜잭션에서 검사하고 롤백하여 다음 사례에 영향을 주지 않는다.
        for sql in changes:
            with self.subTest(change=sql), self.engine.connect() as db:
                tx = db.begin()
                db.execute(text(sql))
                with self.assertRaises(AccessError) as caught:
                    documents_repo.get_document_detail(self.actor, 'document', connection=db)
                self.assertEqual(caught.exception.code, 'STATE_CHANGED')
                tx.rollback()

    def test_detail_rechecks_session_and_company_instead_of_actor_claims(self):
        cases = (
            ("UPDATE users SET company_id='company-b'", 'ACCESS_DENIED'),
            ("UPDATE companies SET is_active=0 WHERE id='company-a'", 'ACCESS_DENIED'),
            ("UPDATE sessions SET expires_at='2000-01-01'", 'SESSION_EXPIRED'),
            ("UPDATE sessions SET revoked_at='2026-10-26'", 'SESSION_EXPIRED'),
            ("UPDATE users SET auth_version=2", 'SESSION_EXPIRED'),
        )
        for sql, code in cases:
            with self.subTest(change=sql), self.engine.connect() as db:
                tx = db.begin()
                db.execute(text(sql))
                with self.assertRaises(AccessError) as caught:
                    documents_repo.get_document_detail(self.actor, 'document', connection=db)
                self.assertEqual(caught.exception.code, code)
                tx.rollback()

    def test_detail_validates_ids_before_connecting_and_hides_database_error(self):
        with patch.object(runtime, 'get_engine') as engine:
            for document_id, version_id in (('', None), (None, None), ('document', ''), ('document', 1)):
                with self.subTest(document=document_id, version=version_id):
                    with self.assertRaises(DbError) as caught:
                        documents_repo.get_document_detail(self.actor, document_id, version_id=version_id)
                    self.assertEqual(caught.exception.code, 'INVALID_INPUT')
            engine.assert_not_called()
        error = OperationalError('query', {}, Exception('synthetic-secret'))
        with patch.object(runtime, 'get_engine', side_effect=error):
            with self.assertRaises(DbError) as caught:
                documents_repo.get_document_detail(self.actor, 'document')
            self.assertEqual(caught.exception.code, 'DB_UNAVAILABLE')
            self.assertNotIn('synthetic-secret', str(caught.exception))

    def test_detail_does_not_replace_post_read_access_recheck(self):
        detail = documents_repo.get_document_detail(self.actor, 'document')
        self.change("UPDATE documents SET active_version_id=NULL WHERE id='document'")
        with self.assertRaises(AccessError) as caught:
            verify_documents_access(self.actor, [(detail['document_id'], detail['version_id'])])
        self.assertEqual(caught.exception.code, 'STATE_CHANGED')

    def test_current_actor_uses_database_scope_instead_of_forged_fields(self):
        before = dict(self.actor)
        with self.engine.connect() as db:
            current = current_actor(self.actor, connection=db)
        self.assertEqual((current['user_id'], current['company_id'], current['role'], current['auth_version']),
                         ('user', 'company-a', 'company_admin', 1))
        self.assertEqual(self.actor, before)
        self.assertEqual(get_allowed_company_ids(self.actor), {'company-a'})

    def test_platform_admin_and_unknown_role_use_current_database_values(self):
        self.change("UPDATE users SET role='platform_admin',company_id=NULL")
        self.assertEqual(get_allowed_company_ids(self.actor), {'company-a', 'company-b'})
        self.change("UPDATE users SET role='unknown'")
        with self.assertRaises(AccessError) as caught:
            get_allowed_company_ids(self.actor)
        self.assertEqual(caught.exception.code, 'ACCESS_DENIED')

    def test_expiry_revocation_and_auth_version_changes_are_read_again(self):
        cases = ("UPDATE sessions SET expires_at='2026-10-26 00:00:00'",
                 "UPDATE sessions SET revoked_at='2026-10-26 00:00:00'",
                 'UPDATE sessions SET auth_version=2',
                 'UPDATE users SET is_active=0')
        for sql in cases:
            with self.subTest(sql=sql):
                self.change(sql)
                with self.assertRaises(AccessError) as caught:
                    get_allowed_company_ids(self.actor)
                self.assertEqual(caught.exception.code, 'SESSION_EXPIRED')
                self.change("UPDATE sessions SET expires_at='2099-01-01 00:00:00',revoked_at=NULL,auth_version=1")
                self.change('UPDATE users SET is_active=1')

    def test_invalid_session_proof_and_inactive_company_are_denied(self):
        for token in (None, 'short', 'x' * 43):
            with self.subTest(token=token), self.assertRaises(AccessError) as caught:
                get_allowed_company_ids(dict(self.actor, _session_token=token))
            self.assertEqual(caught.exception.code, 'SESSION_EXPIRED')
        self.change("UPDATE companies SET is_active=0 WHERE id='company-a'")
        with self.assertRaises(AccessError) as caught:
            get_allowed_company_ids(self.actor)
        self.assertEqual(caught.exception.code, 'ACCESS_DENIED')

    def test_document_lookup_has_exact_fields_and_null_rules(self):
        rows = documents_repo.get_documents_with_active_version(['document', 'document', 'draft', 'missing'])
        self.assertEqual(len(rows), 2)
        expected = {'document_id', 'company_id', 'company_name', 'doc_type', 'publication_status',
                    'deleted_at', 'active_version_id', 'version_no', 'version_status', 'data_category', 'file_name'}
        by_id = {row['document_id']: row for row in rows}
        for row in rows:
            self.assertEqual(set(row), expected)
        self.assertIsInstance(by_id['document']['version_no'], int)
        self.assertEqual(by_id['document']['company_name'], 'Company A')
        self.assertEqual(by_id['document']['file_name'], 'sample.pdf')
        for key in ('active_version_id', 'version_no', 'version_status', 'data_category', 'file_name'):
            self.assertIsNone(by_id['draft'][key])

    def test_access_recheck_rejects_each_document_state_change(self):
        refs = [('document', 'version')]
        self.assertIsNone(verify_documents_access(self.actor, refs + refs))
        cases = (
            ("UPDATE documents SET deleted_at='2026-10-26'", 'UPDATE documents SET deleted_at=NULL'),
            ("UPDATE documents SET publication_status='blocked'", "UPDATE documents SET publication_status='published' WHERE id='document'"),
            ("UPDATE documents SET active_version_id=NULL", "UPDATE documents SET active_version_id='version' WHERE id='document'"),
            ("UPDATE document_versions SET version_status='processing'", "UPDATE document_versions SET version_status='ready'"),
            ("UPDATE document_versions SET data_category='evaluation'", "UPDATE document_versions SET data_category='operational'"),
        )
        for change, restore in cases:
            with self.subTest(change=change):
                self.change(change)
                with self.assertRaises(AccessError) as caught:
                    verify_documents_access(self.actor, refs)
                self.assertEqual(caught.exception.code, 'STATE_CHANGED')
                self.change(restore)
        with self.assertRaises(AccessError) as caught:
            verify_documents_access(self.actor, refs + [('missing', 'missing')])
        self.assertEqual(caught.exception.code, 'STATE_CHANGED')
        self.change("UPDATE users SET company_id='company-b'")
        with self.assertRaises(AccessError) as caught:
            verify_documents_access(self.actor, refs)
        self.assertEqual(caught.exception.code, 'ACCESS_DENIED')

    def test_invalid_references_and_empty_lookup_do_not_connect(self):
        with patch.object(runtime, 'get_engine') as engine:
            self.assertEqual(documents_repo.get_documents_with_active_version([]), [])
            for refs in ([], [('document', '')], [('document', None)], [('document',)], 'bad'):
                with self.subTest(refs=refs), self.assertRaises(DbError) as caught:
                    verify_documents_access(self.actor, refs)
                self.assertEqual(caught.exception.code, 'INVALID_INPUT')
            engine.assert_not_called()

    def test_database_failure_is_safe_and_not_an_empty_result(self):
        error = OperationalError('query', {}, Exception('synthetic-secret'))
        with patch.object(runtime, 'get_engine', side_effect=error):
            for call in (lambda: get_allowed_company_ids(self.actor),
                         lambda: verify_documents_access(self.actor, [('document', 'version')]),
                         lambda: documents_repo.get_documents_with_active_version(['document'])):
                with self.assertRaises(DbError) as caught:
                    call()
                self.assertEqual(caught.exception.code, 'DB_UNAVAILABLE')
                self.assertNotIn('synthetic-secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
