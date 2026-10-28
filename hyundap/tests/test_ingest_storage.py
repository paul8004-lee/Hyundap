"""청크 입력·수집 보고서의 DB 저장 계약을 외부 API 없이 검증한다."""

from copy import deepcopy
from dataclasses import make_dataclass
import json
import unittest
from unittest.mock import patch

from hyundap.db import chunks_repo, jobs_repo, runtime
from hyundap.db.errors import DbError


class IngestStorageTests(unittest.TestCase):
    def setUp(self):
        self.report = dict(stage='ingest', status='success', parser='pypdf',
                           parser_version='6.21.2', chunk_size=1000, chunk_overlap=150,
                           section_count=2, chunk_count=3, warning_count=1)

    def test_allowed_fields_survive_without_mutation_or_raw_content(self):
        report = dict(self.report, warnings=['synthetic-secret'], text='synthetic-secret',
                      api_key='synthetic-secret', error_message='synthetic-secret')
        original = deepcopy(report)
        clean = jobs_repo.normalize_report(report)
        for key, value in self.report.items():
            self.assertEqual(clean[key], value)
        self.assertNotIn('synthetic-secret', json.dumps(clean))
        self.assertEqual(report, original)

    def test_invalid_ingest_metrics_fail_before_connecting(self):
        cases = [dict(parser=value) for value in (None, [], 'unknown', '')]
        cases += [dict(parser_version=value) for value in (None, '', 'a' * 65, 'bad\nversion')]
        for field in jobs_repo.INGEST_COUNT_FIELDS:
            cases += [{field: value} for value in (True, None, -1, 1.5, '1')]
        cases += [dict(chunk_size=0), dict(chunk_overlap=1000), dict(chunk_overlap=1001)]
        cases += [dict(short_chunk_count=4)]
        with patch.object(runtime, 'get_engine') as engine:
            for changes in cases:
                with self.subTest(changes=changes), self.assertRaises(DbError) as caught:
                    jobs_repo.record_stage_result('job', dict(self.report, **changes))
                self.assertEqual(caught.exception.code, 'INVALID_INPUT')
            engine.assert_not_called()

    def test_optional_fields_zero_counts_and_stage_scope(self):
        self.assertEqual(jobs_repo.normalize_report(dict(status='success'))['stage'], 'ingest')
        clean = jobs_repo.normalize_report(dict(status='failed', warning_count=0, chunk_count=0,
                                                section_count=0, chunk_overlap=0,
                                                parser_version='1.2.0rc1+local'))
        self.assertEqual(clean['chunk_count'], 0)
        self.assertNotIn('parser', clean)
        for stage in ('embedding', 'verify', 'publish'):
            with self.subTest(stage=stage), self.assertRaises(DbError):
                jobs_repo.normalize_report(dict(self.report, stage=stage))

    def test_embedding_dataclass_contract_is_preserved(self):
        Report = make_dataclass('Report', [('status', str), ('model_name', str), ('total', int)])
        clean = jobs_repo.normalize_report(Report('success', 'embedding-model', 3))
        self.assertEqual(clean['stage'], 'embedding')
        self.assertEqual(clean['total'], 3)
        with self.assertRaises(DbError):
            jobs_repo.normalize_report(dict(self.report, error_code='SYNTHETIC_ERROR'))

    def test_embedding_classify_fields_are_stored(self):
        # 임베딩 보고서의 문서 분류 결과(EmbedStoreReport 필드)는 작업 기록에 남는다
        report = dict(stage='embedding', status='success', model_name='embedding-model', total=3,
                      auto_doc_type='policy', doc_type_confidence=0.93, company_match='no',
                      classify_status='success')
        clean = jobs_repo.normalize_report(report)
        self.assertEqual({key: clean[key] for key in jobs_repo.CLASSIFY_FIELDS},
                         dict(auto_doc_type='policy', doc_type_confidence=0.93, company_match='no',
                              classify_status='success'))
        # 분류를 건너뛴 기본값(빈 유형·0·빈 일치)도 그대로 받는다
        skipped = jobs_repo.normalize_report(dict(report, auto_doc_type='', doc_type_confidence=0.0,
                                                  company_match='', classify_status='skipped'))
        self.assertEqual((skipped['auto_doc_type'], skipped['classify_status']), ('', 'skipped'))

    def test_invalid_classify_fields_fail_before_connecting(self):
        base = dict(stage='embedding', status='success', model_name='embedding-model', total=3)
        cases = [dict(auto_doc_type=value) for value in (None, 'contract', 1)]
        cases += [dict(doc_type_confidence=value) for value in (True, None, -0.1, 1.5, float('nan'), '0.9')]
        cases += [dict(company_match=value) for value in (None, 'maybe', True)]
        cases += [dict(classify_status=value) for value in (None, 'done', '')]
        with patch.object(runtime, 'get_engine') as engine:
            for changes in cases:
                with self.subTest(changes=changes), self.assertRaises(DbError) as caught:
                    jobs_repo.record_stage_result('job', dict(base, **changes))
                self.assertEqual(caught.exception.code, 'INVALID_INPUT')
            engine.assert_not_called()
        # 분류 결과는 임베딩 단계에만 들어갈 수 있다
        with self.assertRaises(DbError):
            jobs_repo.normalize_report(dict(self.report, auto_doc_type='manual'))

    def test_stage_merge_preserves_history_and_protects_success(self):
        original = {'_attempts': [{'ingest': {'status': 'failed'}}],
                    'embedding': {'status': 'partial'}}
        before = deepcopy(original)
        clean = jobs_repo.normalize_report(self.report)
        merged = jobs_repo._merge_stage_reports(original, clean)
        self.assertEqual(original, before)
        self.assertEqual(merged['_attempts'], before['_attempts'])
        self.assertEqual(merged['embedding'], before['embedding'])
        self.assertEqual(jobs_repo._merge_stage_reports(merged, clean), merged)
        with self.assertRaises(DbError) as caught:
            jobs_repo._merge_stage_reports(merged, dict(clean, chunk_count=4))
        self.assertEqual(caught.exception.code, 'CONFLICT')

    def test_manifest_order_is_normalized_without_mutation(self):
        rows = [dict(id=f'v:{i:05d}', chunk_index=i, content_hash='a' * 64) for i in (1, 0)]
        before = deepcopy(rows)
        self.assertEqual([r['chunk_index'] for r in chunks_repo._normalize_manifest('v', rows)], [0, 1])
        self.assertEqual(rows, before)

    def test_bad_manifest_boundaries_do_not_connect(self):
        valid = dict(id='v:00000', chunk_index=0, content_hash='a' * 64)
        cases = [[], [valid, valid], [dict(valid, chunk_index=True)],
                 [dict(valid, id='other:00000')], [dict(valid, chunk_index=100000)],
                 [dict(valid, content_hash='A' * 64)], [dict(valid, chunk_index=1, id='v:00001')]]
        with patch.object(runtime, 'get_engine') as engine:
            for rows in cases:
                with self.subTest(rows=rows), self.assertRaises(DbError):
                    chunks_repo.save_manifest('v', rows)
            engine.assert_not_called()


if __name__ == '__main__':
    unittest.main()
