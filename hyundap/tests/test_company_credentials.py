"""로컬 회사 DB 접속 설정의 검증 및 보존 계약."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hyundap.db.company_credentials import read_credential, save_credential
from hyundap.db.errors import DbError


class CompanyCredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / '.env.company-databases.json'

    def test_store_and_read_multiple_companies(self):
        save_credential('company-a', 'tenant_a', 'password-a-long-123', path=self.path)
        save_credential('company-b', 'tenant_b', 'password-b-long-456', path=self.path)
        self.assertEqual(read_credential('company-a', path=self.path), ('tenant_a', 'password-a-long-123'))
        self.assertEqual(read_credential('company-b', path=self.path), ('tenant_b', 'password-b-long-456'))

    def test_rejects_conflict_and_malformed_file_without_overwriting(self):
        save_credential('company-a', 'tenant_a', 'password-a-long-123', path=self.path)
        with self.assertRaises(DbError):
            save_credential('company-a', 'tenant_a', 'other-password-12345', path=self.path)
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(DbError):
            save_credential('company-b', 'tenant_b', 'password-b-long-456', path=self.path)
        self.assertEqual(self.path.read_text(encoding='utf-8'), '{broken')

    def test_missing_and_invalid_records_are_rejected(self):
        with self.assertRaises(DbError):
            read_credential('company-a', path=self.path)
        self.path.write_text(json.dumps({'company-a': {'username': 'bad user', 'password': 'secret'}}), encoding='utf-8')
        with self.assertRaises(DbError):
            read_credential('company-a', path=self.path)

    def test_identical_retry_and_failed_replace_preserve_original_file(self):
        save_credential('company-a', 'tenant_a', 'password-a-long-123', path=self.path)
        original = self.path.read_bytes()
        with patch('hyundap.db.company_credentials.os.replace') as replace:
            save_credential('company-a', 'tenant_a', 'password-a-long-123', path=self.path)
            replace.assert_not_called()
        with patch('hyundap.db.company_credentials.os.replace', side_effect=OSError), self.assertRaises(DbError):
            save_credential('company-b', 'tenant_b', 'password-b-long-456', path=self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_hosting_secret_is_read_without_falling_back_to_local_file(self):
        save_credential('company-a', 'tenant_local', 'local-password-123', path=self.path)
        secret = json.dumps({'company-a': {'username': 'tenant_host', 'password': 'hosting-password-123'}})
        with patch.dict(os.environ, {'HYUNDAP_COMPANY_CREDENTIALS_SOURCE': 'environment',
                                     'HYUNDAP_COMPANY_DATABASE_CREDENTIALS': secret}):
            self.assertEqual(read_credential('company-a', path=self.path), ('tenant_host', 'hosting-password-123'))
            with self.assertRaises(DbError):
                save_credential('company-b', 'tenant_b', 'another-password-123', path=self.path)
        with patch.dict(os.environ, {'HYUNDAP_COMPANY_CREDENTIALS_SOURCE': 'environment',
                                     'HYUNDAP_COMPANY_DATABASE_CREDENTIALS': '{}'}):
            with self.assertRaises(DbError):
                read_credential('company-a', path=self.path)


if __name__ == '__main__':
    unittest.main()
