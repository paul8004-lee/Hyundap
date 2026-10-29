"""DB 중복 처리 축소 후 입력·라우팅·저장 계약을 확인하는 외부 접속 없는 회귀 검사."""

from copy import deepcopy
from contextlib import nullcontext, redirect_stdout
import hashlib
import io
import os
import sys
import unittest
from unittest.mock import Mock, patch

from sqlalchemy.engine import make_url

from hyundap.db import company_admin, documents_repo, jobs_repo, tenancy, upload_service, version_service
from hyundap.db.errors import DbError


class RefactorBoundaryTests(unittest.TestCase):
    def test_failure_code_precedence_preserves_explicit_invalid_values(self):
        from hyundap.db.jobs_repo import normalize_report
        failure = {'code': 'FILE_UNAVAILABLE', 'error_code': 'LEGACY_FORMAT', 'message': 'secret'}
        report = {'stage': 'ingest', 'status': 'failed', 'failures': [failure]}
        clean = normalize_report(report)
        self.assertEqual(clean['failures'], [{'code': 'FILE_UNAVAILABLE'}])
        self.assertEqual(failure['message'], 'secret')
        fallback = normalize_report({'status': 'failed', 'failures': [{'error_code': 'LEGACY_FORMAT'}]})
        self.assertEqual(fallback['failures'], [{'code': 'LEGACY_FORMAT'}])
        for invalid in (None, '', False):
            with self.subTest(invalid=invalid), self.assertRaises(DbError):
                normalize_report({'status': 'failed', 'failures': [{'code': invalid, 'error_code': 'LEGACY_FORMAT'}]})

    def test_metadata_required_fields_precede_value_checks_and_preserve_input(self):
        metadata = {
            'company_id': 'company', 'file_name': 'sample.pdf',
            'storage_path': 'uploads/source.pdf', 'sha256': 'a' * 64,
            'file_size': 10, 'data_category': 'operational',
        }
        original = deepcopy(metadata)
        clean = documents_repo.validate_metadata(metadata)
        self.assertEqual(metadata, original)
        self.assertIsNot(clean, metadata)
        self.assertEqual(clean['company_id'], 'company')
        for field in metadata:
            incomplete = dict(metadata, doc_type='unknown')
            del incomplete[field]
            with self.subTest(missing=field), self.assertRaises(DbError) as caught:
                documents_repo.validate_metadata(incomplete)
            self.assertEqual(caught.exception.code, 'INVALID_INPUT')
        for invalid_id in ('', ' ', 123, 'x' * 37):
            with self.subTest(document_id=invalid_id), self.assertRaises(DbError) as caught:
                documents_repo.validate_metadata(dict(metadata, document_id=invalid_id))
            self.assertEqual(caught.exception.code, 'INVALID_INPUT')
        with self.assertRaises(DbError) as caught:
            documents_repo.validate_metadata(dict(metadata, doc_type='unknown'))
        self.assertEqual(caught.exception.code, 'INVALID_DOC_TYPE')

    def test_routing_checks_each_reference_and_preserves_company_boundaries(self):
        engine = object()
        with patch.object(tenancy, 'company_for_reference', return_value='owner') as reference, \
                patch.object(tenancy, 'get_company_engine', return_value=engine) as select:
            self.assertIsNone(tenancy.routed_engine())
            reference.assert_not_called()
            select.assert_not_called()
            for ids in ({'document_id': 'd'}, {'version_id': 'v'}, {'job_id': 'j'},
                        {'document_id': 'd', 'version_id': 'v', 'job_id': 'j'}):
                with self.subTest(ids=ids):
                    self.assertIs(tenancy.routed_engine(**ids), engine)
                    self.assertEqual(select.call_args.args, ('owner',))
            with self.assertRaises(DbError) as caught:
                tenancy.routed_engine(company_id='other', document_id='d')
            self.assertEqual(caught.exception.code, 'INVALID_STATE')
            token = tenancy._company.set('other')
            try:
                self.assertIs(tenancy.routed_engine(), engine)
                with self.assertRaises(DbError) as caught:
                    tenancy.routed_engine(document_id='d')
                self.assertEqual(caught.exception.code, 'INVALID_STATE')
            finally:
                tenancy._company.reset(token)
        with patch.object(tenancy, 'company_for_reference', side_effect=DbError('INVALID_INPUT')) as reference:
            with self.assertRaises(DbError) as caught:
                tenancy.routed_engine(document_id='')
            self.assertEqual(caught.exception.code, 'INVALID_INPUT')
            self.assertEqual(reference.call_args.kwargs['document_id'], '')

    def test_upload_boundary_validates_before_routing_and_passes_company(self):
        metadata = {'company_id': 'company', 'doc_type': 'manual'}
        result = object()
        with patch.object(upload_service, 'authorize_company') as authorize, \
                patch.object(upload_service, 'company_scope', return_value=nullcontext()), \
                patch.object(upload_service, '_upload_document', return_value=result) as upload:
            for invalid in (None, [], {}, {'company_id': ''}, {'company_id': 123}):
                with self.subTest(metadata=invalid), self.assertRaises(DbError) as caught:
                    upload_service.upload_document({}, 'unused', invalid)
                self.assertEqual(caught.exception.code, 'INVALID_INPUT')
            authorize.assert_not_called()
            upload.assert_not_called()
            self.assertIs(upload_service.upload_document({}, 'unused', metadata), result)
            self.assertEqual(upload.call_args.kwargs['company_id'], 'company')
            self.assertIs(upload.call_args.args[2], metadata)
            self.assertEqual(metadata, {'company_id': 'company', 'doc_type': 'manual'})

    def test_retry_invalid_id_is_rejected_before_authorization_or_work(self):
        with patch.object(upload_service, 'authorize_company') as authorize, \
                patch.object(upload_service, '_retry_upload') as retry:
            for invalid in (None, '', ' ', 123, 'x' * 37):
                with self.subTest(version_id=invalid), self.assertRaises(DbError) as caught:
                    upload_service.retry_upload({}, invalid)
                self.assertEqual(caught.exception.code, 'INVALID_INPUT')
            authorize.assert_not_called()
            retry.assert_not_called()

    def test_stage_target_rejects_invalid_id_without_query_and_missing_job(self):
        database = Mock()
        for invalid in (None, '', ' ', 123, 'x' * 37):
            with self.subTest(job_id=invalid), self.assertRaises(DbError) as caught:
                jobs_repo._read_stage_target(database, invalid)
            self.assertEqual(caught.exception.code, 'INVALID_INPUT')
        database.execute.assert_not_called()
        database.execute.return_value.scalar_one_or_none.return_value = None
        with self.assertRaises(DbError) as caught:
            jobs_repo._read_stage_target(database, 'missing-job')
        self.assertEqual(caught.exception.code, 'INVALID_STATE')

    def test_non_ingest_metrics_return_empty_and_reject_all_ingest_only_fields(self):
        for stage in jobs_repo.STAGES:
            if stage == 'ingest':
                continue
            with self.subTest(stage=stage):
                clean = jobs_repo.normalize_report({'stage': stage, 'status': 'success'})
                self.assertEqual(clean['stage'], stage)
            for field in ('parser', 'parser_version') + jobs_repo.INGEST_COUNT_FIELDS:
                with self.subTest(stage=stage, field=field), self.assertRaises(DbError) as caught:
                    jobs_repo.normalize_report({'stage': stage, 'status': 'success', field: None})
                self.assertEqual(caught.exception.code, 'INVALID_INPUT')
        report = {'stage': 'ingest', 'status': 'success', 'parser': 'pypdf',
                  'parser_version': '6.0', 'chunk_size': 100, 'chunk_overlap': 20,
                  'chunk_count': 3, 'short_chunk_count': 1}
        self.assertEqual(jobs_repo.normalize_report(report)['chunk_count'], 3)
        for change in ({'chunk_size': 0}, {'chunk_overlap': 100},
                       {'short_chunk_count': 4}, {'section_count': True}):
            with self.subTest(change=change), self.assertRaises(DbError) as caught:
                jobs_repo.normalize_report(dict(report, **change))
            self.assertEqual(caught.exception.code, 'INVALID_INPUT')

    def test_chunk_comparison_preserves_duplicate_count_and_rejects_invalid_ids(self):
        identity = {'document_id': 'd', 'version_id': 'v', 'company_id': 'c'}
        version = {'expected_chunk_count': 1, 'version_status': 'processing'}
        manifest = [{'id': 'v:00000', 'chunk_index': 0,
                     'content_hash': hashlib.sha256(b'text').hexdigest()}]
        hits = [{'chunk_id': 'v:00000', 'text': 'text', 'metadata': dict(identity, chunk_index=0)}]
        ids = ['v:00000']
        result = version_service.compare_stored_chunks(identity, version, manifest, 'seal', ids, hits)
        self.assertTrue(result['ok'])
        self.assertEqual(ids, ['v:00000'])
        duplicates = ids + ids
        result = version_service.compare_stored_chunks(identity, version, manifest, 'seal', duplicates, hits)
        self.assertFalse(result['ok'])
        self.assertEqual(result['actual_count'], 2)
        self.assertEqual(duplicates, ['v:00000', 'v:00000'])
        for invalid in ([None], ['v:00000', 1], ('v:00000',)):
            with self.subTest(ids=invalid), self.assertRaises(DbError) as caught:
                version_service.collect_chunk_comparison(identity, manifest, invalid, hits)
            self.assertEqual(caught.exception.code, 'INVALID_INPUT')

    def test_publication_status_and_stage_preserve_all_phase_combinations(self):
        phase_status = {'pending_activation': 'QUEUED', 'restore_pending': 'QUEUED',
                        'restored': 'FAILED', 'superseded': 'FAILED',
                        'activated': None, 'unknown': None, None: None}
        for phase, pending_status in phase_status.items():
            for complete in (False, True):
                for code in (None, 'RECOVERY_REQUIRED'):
                    with self.subTest(phase=phase, complete=complete, code=code), \
                            patch.object(jobs_repo, 'store_job_result') as store:
                        database = object()
                        publication = {'phase': phase}
                        reports = {'ingest': {'status': 'success'}}
                        version_service.write_publication(
                            database, {'id': 'job'}, reports, publication,
                            complete=complete, error_code=code)
                        store.assert_called_once()
                        self.assertEqual(store.call_args.args[:3],
                                         (database, 'job', 'complete' if complete else 'publish'))
                        self.assertEqual(store.call_args.kwargs['status'],
                                         'SUCCEEDED' if complete else pending_status)
                        self.assertEqual(store.call_args.kwargs['error_code'], code)
                        self.assertEqual(store.call_args.kwargs['error_message'],
                                         '게시 상태 복구 필요' if code else None)
                        self.assertIs(reports['_publication'], publication)
                        self.assertEqual(reports['ingest'], {'status': 'success'})

    def test_company_setup_reuses_parsed_url_and_rejects_other_targets(self):
        control_url = make_url('mysql+pymysql://app:synthetic%40pass@127.0.0.1/control')
        targets = (
            ('mysql+pymysql://admin:admin-secret@127.0.0.1/control', 0),
            ('mysql+pymysql://admin:admin-secret@127.0.0.1/other', 1),
            ('mysql+pymysql://admin:admin-secret@127.0.0.1:3307/control', 1),
            ('mysql+pymysql://admin:admin-secret@localhost/control', 1),
        )
        for target, expected in targets:
            control, admin = Mock(url=control_url), Mock(url=make_url(target))
            output = io.StringIO()
            with self.subTest(target=target), \
                    patch.object(sys, 'argv', ['company_admin', '--create-control']), \
                    patch.object(company_admin.runtime, 'split_layout', return_value=True), \
                    patch.dict(os.environ, {'DATABASE_URL': str(control_url), 'DB_PROVISION_URL': target}), \
                    patch.object(company_admin, 'create_db_engine', side_effect=[control, admin]), \
                    patch.object(company_admin, 'create_split_schema') as create, redirect_stdout(output):
                self.assertEqual(company_admin.main(), expected)
                if expected == 0:
                    create.assert_called_once_with(admin)
                else:
                    create.assert_not_called()
            control.dispose.assert_called_once()
            admin.dispose.assert_called_once()
            for secret in ('synthetic@pass', 'admin-secret', target):
                self.assertNotIn(secret, output.getvalue())

    def test_stage_result_status_uses_normalized_success_and_failure_contract(self):
        for state in ('success', 'partial', 'failed'):
            for stage in jobs_repo.STAGES:
                report = {'stage': stage, 'status': state}
                if state != 'success':
                    report['error_code'] = 'PROCESSING_FAILED'
                clean = jobs_repo.normalize_report(report)
                with self.subTest(stage=stage, state=state), \
                        patch.object(jobs_repo, 'store_job_result') as store:
                    reports = {stage: clean}
                    jobs_repo._store_stage_result('database', 'job', clean, reports)
                    store.assert_called_once()
                    expected = None
                    if state in ('partial', 'failed'):
                        expected = 'FAILED'
                    elif stage == 'complete':
                        expected = 'SUCCEEDED'
                    self.assertEqual(store.call_args.kwargs['status'], expected)
                    self.assertEqual(store.call_args.args, ('database', 'job', stage, reports))
                    self.assertEqual(store.call_args.kwargs['error_message'],
                                     None if state == 'success' else '처리 실패: PROCESSING_FAILED')
        with self.assertRaises(DbError) as caught:
            jobs_repo.normalize_report({'stage': 'complete', 'status': 'unknown'})
        self.assertEqual(caught.exception.code, 'INVALID_INPUT')
