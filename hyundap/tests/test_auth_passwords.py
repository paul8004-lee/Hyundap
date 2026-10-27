"""SPEC-AUTH-001 M1 — scrypt 비밀번호 해시 형식·경계·실패 계약 검증 (DB 불필요).

hashlib.scrypt를 실제 함수를 감싼 spy로 바꿔 호출 횟수와 인자만 확인한다.
합성 비밀번호만 사용하며 외부 API·DB에는 접속하지 않는다.
"""

import base64
import hashlib
import hmac
import importlib
import re
import sys
import unittest
from unittest.mock import patch

from hyundap.auth.errors import AccessError

REAL_SCRYPT = hashlib.scrypt
PASSWORD = 'synthetic-Passw0rd!'
DEFAULT_MAXMEM = 128 * 8 * (32768 + 2) + 128 * 8 * 1 + 32 * 1024 * 1024
PEAK_MAXMEM = 100_667_392
FORMAT = re.compile(r'^scrypt\$32768\$8\$1\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+$')


def passwords_module():
    """현재 import된 passwords 모듈을 반환한다(재import 테스트 이후에도 최신 모듈)."""
    return importlib.import_module('hyundap.auth.passwords')


def parse(stored):
    """테스트용 단순 파서: (n, r, p, salt bytes, key bytes)."""
    _, n, r, p, salt, key = stored.split('$')
    return int(n), int(r), int(p), base64.b64decode(salt), base64.b64decode(key)


def b64(data):
    """bytes를 표준 base64 문자열로 바꾼다."""
    return base64.b64encode(data).decode('ascii')


def scrypt_spy(side_effect=None):
    """실제 scrypt에 위임하는 spy 패치를 만든다."""
    return patch.object(hashlib, 'scrypt', side_effect=side_effect or REAL_SCRYPT)


class HashFormatTests(unittest.TestCase):
    """AC-001 해시 형식·maxmem·검증 결과."""

    def test_hash_format_length_salt_and_maxmem(self):
        """두 번 만든 해시가 형식·길이를 지키고 서로 다르며 비밀번호를 담지 않는다."""
        passwords = passwords_module()
        with scrypt_spy() as spy:
            first = passwords.hash_password(PASSWORD)
            second = passwords.hash_password(PASSWORD)
        for stored in (first, second):
            self.assertRegex(stored, FORMAT)
            self.assertLessEqual(len(stored), 255)
            self.assertNotIn(PASSWORD, stored)
            n, r, p, salt, key = parse(stored)
            self.assertEqual((n, r, p, len(salt), len(key)), (32768, 8, 1, 16, 32))
        self.assertNotEqual(first, second)
        self.assertEqual(spy.call_count, 2)
        for call in spy.call_args_list:
            self.assertEqual(call.kwargs['maxmem'], DEFAULT_MAXMEM)

    def test_verify_right_and_wrong_password_uses_compare_digest(self):
        """맞는 비밀번호는 True, 틀린 비밀번호는 False이며 비교는 compare_digest를 거친다."""
        passwords = passwords_module()
        stored = passwords.hash_password(PASSWORD)
        with patch.object(hmac, 'compare_digest', wraps=hmac.compare_digest) as compare:
            self.assertIs(passwords.verify_password(PASSWORD, stored), True)
            self.assertIs(passwords.verify_password(PASSWORD + 'x', stored), False)
        self.assertEqual(compare.call_count, 2)


class FailedVerificationTests(unittest.TestCase):
    """AC-001 / REQ-AUTH-008 — 범위 밖·손상 해시는 저장된 인자로 scrypt를 부르지 않는다."""

    def setUp(self):
        passwords = passwords_module()
        self.dummy = parse(passwords._DUMMY_HASH)
        salt, key = b64(b'\x01' * 16), b64(b'\x02' * 32)
        self.rejected = {
            'none': None,
            'malformed': 'not-a-hash',
            'empty': '',
            'wrong-scheme': f'pbkdf2$32768$8$1${salt}${key}',
            'bad-base64': 'scrypt$32768$8$1$***$***',
            'short-salt': f'scrypt$32768$8$1${b64(b"x" * 8)}${key}',
            'short-key': f'scrypt$32768$8$1${salt}${b64(b"y" * 16)}',
            'n=2^17': f'scrypt${2 ** 17}$8$1${salt}${key}',
            'n=2^20': f'scrypt${2 ** 20}$8$1${salt}${key}',
            'n=3*2^14': f'scrypt${3 * 2 ** 14}$8$1${salt}${key}',
            'r=16': f'scrypt$32768$16$1${salt}${key}',
            'p=4': f'scrypt$32768$8$4${salt}${key}',
            'n=2^16,r=1': f'scrypt$65536$1$1${salt}${key}',
        }

    def assert_dummy_call(self, call):
        """scrypt 호출이 _DUMMY_HASH의 salt·인자를 사용했는지 확인한다."""
        n, r, p, salt, _ = self.dummy
        self.assertEqual((call.kwargs['salt'], call.kwargs['n'], call.kwargs['r'], call.kwargs['p']),
                         (salt, n, r, p))

    def test_rejected_hashes_run_exactly_one_dummy_scrypt(self):
        """각 거부 해시는 예외 없이 False, scrypt는 더미 인자로 정확히 1회다."""
        passwords = passwords_module()
        for name, stored in self.rejected.items():
            with self.subTest(name=name), scrypt_spy() as spy:
                self.assertIs(passwords.verify_password(PASSWORD, stored), False)
                self.assertEqual(spy.call_count, 1)
                self.assert_dummy_call(spy.call_args)
                self.assertLessEqual(spy.call_args.kwargs['maxmem'], PEAK_MAXMEM)

    def test_value_error_on_in_bounds_hash_runs_one_dummy_after_failure(self):
        """저장 해시 scrypt가 ValueError를 내면 더미 1회를 더 실행하고 False다(총 2회)."""
        passwords = passwords_module()
        stored = passwords.hash_password(PASSWORD)
        stored_salt = parse(stored)[3]
        calls = []

        def first_call_fails(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise ValueError('synthetic scrypt failure')
            return REAL_SCRYPT(*args, **kwargs)

        with scrypt_spy(side_effect=first_call_fails) as spy:
            self.assertIs(passwords.verify_password(PASSWORD, stored), False)
        self.assertEqual(spy.call_count, 2)
        self.assertEqual(calls[0]['salt'], stored_salt)
        dummy_calls = [call for call in spy.call_args_list if call.kwargs['salt'] == self.dummy[3]]
        self.assertEqual(len(dummy_calls), 1)
        self.assert_dummy_call(dummy_calls[0])

    def test_peak_maxmem_for_bound_maximum_hash(self):
        """경계 최대 해시(n=2^16, r=8, p=2)의 maxmem은 100,667,392바이트다."""
        passwords = passwords_module()
        self.assertEqual(passwords._maxmem(2 ** 16, 8, 2), PEAK_MAXMEM)


class DummyHashTests(unittest.TestCase):
    """AC-005 offline 절반 — import 시 scrypt 0회, 더미 인자 = 기본 인자."""

    def test_import_performs_no_scrypt(self):
        """passwords 모듈을 새로 import해도 scrypt를 호출하지 않는다."""
        sys.modules.pop('hyundap.auth.passwords', None)
        with scrypt_spy() as spy:
            importlib.import_module('hyundap.auth.passwords')
        self.assertEqual(spy.call_count, 0)

    def test_dummy_parameters_equal_hash_password_output(self):
        """_DUMMY_HASH와 새 해시의 n, r, p, salt 길이, key 길이가 같다."""
        passwords = passwords_module()
        dummy = parse(passwords._DUMMY_HASH)
        fresh = parse(passwords.hash_password('x' * 12))
        self.assertEqual(dummy[:3] + (len(dummy[3]), len(dummy[4])),
                         fresh[:3] + (len(fresh[3]), len(fresh[4])))


class InvalidCredentialsErrorTests(unittest.TestCase):
    """REQ-AUTH-003 — 자격 증명 실패의 단일 코드·고정 메시지."""

    def test_invalid_credentials_message(self):
        """INVALID_CREDENTIALS는 고정 안내 하나만 담는다."""
        error = AccessError('INVALID_CREDENTIALS')
        self.assertEqual(error.code, 'INVALID_CREDENTIALS')
        self.assertEqual(str(error), '아이디 또는 비밀번호가 올바르지 않습니다.')


if __name__ == '__main__':
    unittest.main()
