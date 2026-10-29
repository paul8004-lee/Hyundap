"""로컬 회사 DB 접속 설정의 검증 및 보존 계약."""

import json
import os
import multiprocessing
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hyundap.db.company_credentials import read_credential, save_credential, credential_file_lock
from hyundap.db.errors import DbError


def store_in_process(path, company, start, result, password='synthetic-password-123'):
    """독립 프로세스가 같은 시점에 실제 파일 저장을 시도한다."""
    start.wait(10)
    try:
        save_credential(company, 'tenant_' + company, password, path=path)
        result.put('ok')
    except DbError as error:
        result.put(error.code)


def hold_file_lock(path, ready):
    with credential_file_lock(path):
        ready.set()
        time.sleep(60)


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
        # OS별 잠금 구현에 따라 해제 뒤 잠금 파일은 남거나 제거될 수 있다.
        self.assertLessEqual(set(self.path.parent.iterdir()), {self.path, Path(str(self.path) + '.lock')})

    def test_process_writers_preserve_every_company_and_restart_read(self):
        context = multiprocessing.get_context('spawn')
        start, result = context.Event(), context.Queue()
        processes = []
        for company in ('a', 'b', 'c', 'd'):
            process = context.Process(target=store_in_process, args=(str(self.path), company, start, result))
            process.start()
            processes.append(process)
        start.set()
        for process in processes:
            process.join(20)
            if process.is_alive():
                process.kill()
                process.join()
            self.assertEqual(process.exitcode, 0)
        self.assertEqual([result.get(timeout=2) for _ in processes], ['ok'] * 4)
        self.assertEqual(set(json.loads(self.path.read_text(encoding='utf-8'))), {'a', 'b', 'c', 'd'})
        for company in ('a', 'b', 'c', 'd'):
            self.assertEqual(read_credential(company, path=self.path)[0], 'tenant_' + company)
        result.close()

    def test_lock_timeout_and_process_death_preserve_existing_file(self):
        save_credential('a', 'tenant_a', 'synthetic-password-123', path=self.path)
        original = self.path.read_bytes()
        context = multiprocessing.get_context('spawn')
        ready = context.Event()
        process = context.Process(target=hold_file_lock, args=(str(self.path), ready))
        process.start()
        try:
            self.assertTrue(ready.wait(10))
            with patch('hyundap.db.company_credentials.LOCK_TIMEOUT', 0.1):
                with self.assertRaises(DbError) as caught:
                    save_credential('b', 'tenant_b', 'synthetic-password-123', path=self.path)
            self.assertEqual(caught.exception.code, 'DB_UNAVAILABLE')
            self.assertEqual(self.path.read_bytes(), original)
        finally:
            process.kill()
            process.join(10)
        save_credential('b', 'tenant_b', 'synthetic-password-123', path=self.path)
        self.assertEqual(set(json.loads(self.path.read_text(encoding='utf-8'))), {'a', 'b'})

    def test_same_company_process_writers_reject_different_value(self):
        context = multiprocessing.get_context('spawn')
        start, result = context.Event(), context.Queue()
        processes = []
        for password in ('synthetic-password-111', 'synthetic-password-222'):
            process = context.Process(target=store_in_process,
                                      args=(str(self.path), 'a', start, result, password))
            process.start()
            processes.append(process)
        start.set()
        for process in processes:
            process.join(20)
            if process.is_alive():
                process.kill()
                process.join()
            self.assertEqual(process.exitcode, 0)
        self.assertCountEqual([result.get(timeout=2) for _ in processes], ['ok', 'CONFLICT'])
        self.assertIn(read_credential('a', path=self.path)[1],
                      ('synthetic-password-111', 'synthetic-password-222'))
        result.close()

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
