"""#261: 격리 MySQL의 실제 게시·복구와 주입 수집기의 자동 유형 계약."""
import os
import unittest
from dataclasses import replace
from unittest.mock import patch
from sqlalchemy import text, create_engine, inspect
from sqlalchemy.engine import make_url
from hyundap.db import schema
from hyundap.db import classification, jobs_repo
from hyundap.db.errors import DbError
from . import test_upload_workflow as fixtures


class DecisionTests(unittest.TestCase):
    def test_incomplete_or_inconsistent_decision_is_rejected(self):
        good = dict(doc_type='policy', doc_type_fallback=False, doc_type_fallback_reason=None)
        for change in ({'doc_type': []}, {'doc_type_fallback': 1},
                       {'doc_type_fallback_reason': 'LOW_CONFIDENCE'}, {'doc_type': 'auto'}):
            with self.subTest(change=change), self.assertRaises(DbError):
                classification.decision_fields(dict(good, **change))
        with self.assertRaises(DbError):
            jobs_repo.normalize_report(dict(stage='embedding', status='success', **good))

    def test_auto_request_is_explicit(self):
        for value in (False, 1, None, 'true'):
            with self.subTest(value=value), self.assertRaises(DbError):
                classification.validate_auto_request({'auto_classify': value})
        with self.assertRaises(DbError):
            classification.validate_auto_request({'auto_classify': True, 'doc_type': 'other'})


@unittest.skipUnless(os.getenv('DB_WORKFLOW_TEST_URL'), '격리 MySQL URL 필요')
class AutoWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ['DB_WORKFLOW_TEST_URL'])
        if url.host != '127.0.0.1' or url.database != 'hyundap_workflow_test':
            raise RuntimeError('local isolated database required')
        cls.engine = create_engine(url, isolation_level='READ COMMITTED')
        cls.addClassCleanup(cls.engine.dispose)
        # ?? ?? DB? ??? ?? ????? ?? ???? ????.
        with cls.engine.connect() as connection:
            connection.execute(text('CREATE DATABASE IF NOT EXISTS hyundap_auto_type_test'))
        cls.engine.dispose()
        cls.engine = create_engine(url.set(database='hyundap_auto_type_test'), isolation_level='READ COMMITTED')
        cls.addClassCleanup(cls.engine.dispose)
        if not inspect(cls.engine).get_table_names():
            schema.create_v4_schema(cls.engine)
    setUp = fixtures.UploadWorkflowTests.setUp

    def auto_ingest(self, path, info, *, find_by_hash=None, classify=False,
                    apply_classification=False, classification=None):
        self.assertTrue(apply_classification)
        self.reused = classification
        report = classification or dict(doc_type='policy', doc_type_fallback=False,
            doc_type_fallback_reason=None, auto_doc_type='policy', doc_type_confidence=0.95,
            company_match='yes', classify_status='success')
        result = fixtures.UploadWorkflowTests.ingest(self, path, info, find_by_hash=find_by_hash)
        result['chunks'] = [replace(chunk, doc_type=report['doc_type']) for chunk in result['chunks']]
        result['report'].update(report)
        return result

    def upload_auto(self, **metadata):
        with patch.object(self.upload_service, 'get_settings') as settings:
            settings.return_value.doc_classify = True
            return self.upload_service.upload_document(self.actor, self.source,
                dict(company_id=self.company, auto_classify=True, **metadata),
                ingest_document=self.auto_ingest, embedding=self.embedding, upload_dir=self.upload_dir)

    def current_type(self, document_id):
        with self.engine.connect() as connection:
            return connection.scalar(text('SELECT doc_type FROM documents WHERE id=:id'), {'id': document_id})

    def test_auto_publication_and_stored_report(self):
        result = self.upload_auto()
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['doc_type'], 'policy')
        self.assertEqual(self.current_type(result['document_id']), 'policy')
        self.assertFalse(result['doc_type_fallback'])

    def test_failure_then_retry_reuses_decision_when_disabled(self):
        self.embedding.partial = True
        failed = self.upload_auto()
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(self.current_type(failed['document_id']), 'other')
        self.embedding.partial = False
        with patch.object(self.upload_service, 'get_settings') as settings:
            settings.return_value.doc_classify = False
            result = self.upload_service.retry_upload(self.actor, failed['version_id'],
                ingest_document=self.auto_ingest, embedding=self.embedding)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(self.reused['doc_type'], 'policy')

    def test_new_version_activation_failure_restores_previous_type(self):
        first = fixtures.UploadWorkflowTests.upload(self)
        self.source.write_text('changed source', encoding='utf-8')
        def fail_candidate():
            for row in self.embedding.rows.values():
                version = row['metadata']['version_id']
                if version != first['version_id']:
                    self.embedding.fail_activation.add(version)
        self.embedding.on_activate = fail_candidate
        result = self.upload_auto(document_id=first['document_id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['doc_type'], 'policy')
        self.assertEqual(self.current_type(first['document_id']), 'manual')

    def test_mismatched_chunk_is_rejected_before_manifest(self):
        original = self.auto_ingest
        def mismatch(path, info, *, find_by_hash=None, classify=False,
                     apply_classification=False, classification=None):
            result = original(path, info, find_by_hash=find_by_hash,
                apply_classification=apply_classification, classification=classification)
            result['chunks'][0] = replace(result['chunks'][0], doc_type='manual')
            return result
        self.auto_ingest = mismatch
        result = self.upload_auto()
        self.assertEqual(result['error_code'], 'INVALID_INPUT')
        self.assertNotIn('doc_type', result)
        self.assertEqual(self.current_type(result['document_id']), 'other')

    def test_disabled_auto_request_does_not_create_candidate(self):
        with patch.object(self.upload_service, 'get_settings') as settings:
            settings.return_value.doc_classify = False
            with self.assertRaises(DbError) as caught:
                self.upload_service.upload_document(self.actor, self.source,
                    dict(company_id=self.company, auto_classify=True),
                    ingest_document=self.auto_ingest, embedding=self.embedding)
        self.assertEqual(caught.exception.code, 'INVALID_INPUT')
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text('SELECT COUNT(*) FROM documents WHERE company_id=:id'),
                {'id': self.company}), 0)

    ingest = fixtures.UploadWorkflowTests.ingest
