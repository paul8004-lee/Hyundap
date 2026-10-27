"""수집 직접 호출의 필수 헤더 검증."""
import unittest

from hyundap.ingest.pipeline import ingest_document


class IngestInputTests(unittest.TestCase):
    def test_invalid_doc_type_and_missing_file_are_failed_results(self):
        header = dict(document_id='d', version_id='v', company_id='c',
                      company_name='회사', doc_type='manual', file_name='a.pdf')
        for invalid in (None, '', 'invalid'):
            result = ingest_document('missing.pdf', dict(header, doc_type=invalid))
            self.assertEqual((result.status, result.error_code), ('failed', 'INVALID_DOC_TYPE'))
            self.assertTrue(result.error_message)
        result = ingest_document('missing.pdf', header)
        self.assertEqual((result.status, result.error_code), ('failed', 'FILE_UNAVAILABLE'))

    def test_invalid_required_header_is_rejected_before_file_access(self):
        header = dict(document_id='d', version_id='v', company_id='c',
                      company_name='회사', doc_type='manual', file_name='a.pdf')
        for key in ('document_id', 'version_id', 'company_id', 'company_name', 'file_name'):
            for invalid in (None, '', '  '):
                with self.subTest(key=key, value=invalid):
                    with self.assertRaises(ValueError):
                        ingest_document('missing.pdf', dict(header, **{key: invalid}))


if __name__ == '__main__':
    unittest.main()
