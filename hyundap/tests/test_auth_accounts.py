"""SPEC-AUTH-001 M3 — 초기 계정 등록 함수와 운영자 CLI 검증.

[offline] 입력 검증·CLI 비밀번호 경로는 DB 없이 확인한다. [DB] 클래스는
DB_AUTH_TEST_URL(127.0.0.1/hyundap_auth_test, 빈 DB)일 때만 실행한다. 합성 데이터만 쓴다.
"""

import ast
import io
import logging
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text

from hyundap.auth import accounts, create_account as cli, session_service
from hyundap.auth.passwords import verify_password
from hyundap.db import runtime
from hyundap.db.errors import DbError
from hyundap.tests.test_auth_session import auth_test_engine, make_company

PASSWORD = 'synthetic-Passw0rd!'
EMAIL = 'cli.admin@test.invalid'


class OfflineCreateAccountInputTests(unittest.TestCase):
    """AC-012 offline 절반 — 잘못된 입력은 DB 접속 전에 INVALID_INPUT이다."""

    def test_invalid_inputs_fail_before_connection(self):
        """역할·짧은 비밀번호·@ 없는 이메일·회사 규칙 위반은 쓰기 전에 거부한다."""
        company = str(uuid4())
        cases = (('user@test.invalid', 'admin', company, PASSWORD),
                 ('user@test.invalid', 'employee', company, 'x' * 11),
                 ('user@test.invalid', 'employee', company, 'x' * 1025),
                 ('user.test.invalid', 'employee', company, PASSWORD),
                 ('a@b@test.invalid', 'employee', company, PASSWORD),
                 ('   ', 'employee', company, PASSWORD),
                 ('a' * 250 + '@t.io', 'employee', company, PASSWORD),
                 (None, 'employee', company, PASSWORD),
                 ('user@test.invalid', 'platform_admin', company, PASSWORD),
                 ('user@test.invalid', 'employee', None, PASSWORD),
                 ('user@test.invalid', 'company_admin', '', PASSWORD),
                 ('user@test.invalid', 'employee', company, None))
        with patch.object(runtime, 'get_engine') as engine:
            for email, role, company_id, password in cases:
                with self.subTest(email=email, role=role, company_id=company_id), \
                        self.assertRaises(DbError) as caught:
                    accounts.create_account(email, role, company_id, password)
                self.assertEqual(caught.exception.code, 'INVALID_INPUT')
            engine.assert_not_called()


class OfflineCliTests(unittest.TestCase):
    """AC-013 — 비밀번호는 명령 인자로 받지 않고 getpass(2회) 또는 환경변수 1회 읽기다."""

    def setUp(self):
        environ = patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop('HYUNDAP_ACCOUNT_PASSWORD', None)
        self.stream = io.StringIO()
        handler = logging.StreamHandler(self.stream)
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, old_level)
        self.addCleanup(root.removeHandler, handler)

    def run_main(self, argv):
        """main을 실행하고 (종료 코드, stdout, stderr)를 반환한다."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def assert_no_secret(self, *texts):
        """출력·로그에 비밀번호와 해시 형식 문자열이 없다."""
        for value in texts + (self.stream.getvalue(),):
            self.assertNotIn(PASSWORD, value)
            self.assertNotIn('scrypt$', value)

    def test_parser_has_no_password_option(self):
        """어떤 옵션도 비밀번호를 받지 않는다."""
        for action in cli.build_parser()._actions:
            names = ' '.join(action.option_strings + [action.dest]).lower()
            self.assertNotIn('password', names)
            self.assertNotIn('passwd', names)
        self.assertEqual({action.dest for action in cli.build_parser()._actions},
                         {'help', 'email', 'role', 'company_id'})

    def test_getpass_twice_by_default(self):
        """환경변수가 없으면 getpass를 두 번 부르고 비밀번호를 create_account에 넘긴다."""
        with patch.object(cli.getpass, 'getpass', side_effect=[PASSWORD, PASSWORD]) as prompt, \
                patch.object(cli, 'create_account', return_value='new-user-id') as create:
            code, out, err = self.run_main(['--email', EMAIL, '--role', 'platform_admin'])
        self.assertEqual(code, 0)
        self.assertEqual(prompt.call_count, 2)
        create.assert_called_once_with(EMAIL, 'platform_admin', None, PASSWORD)
        self.assertEqual(out, 'created new-user-id platform_admin\n')
        self.assertEqual(err, '')
        self.assert_no_secret(out, err)

    def test_getpass_mismatch_exits_2(self):
        """두 입력이 다르면 종료 코드 2이며 create_account를 부르지 않는다."""
        with patch.object(cli.getpass, 'getpass', side_effect=[PASSWORD, PASSWORD + 'x']), \
                patch.object(cli, 'create_account') as create:
            code, out, err = self.run_main(['--email', EMAIL, '--role', 'platform_admin'])
        self.assertEqual(code, 2)
        create.assert_not_called()
        self.assert_no_secret(out, err)

    def test_environment_password_is_read_once_and_removed(self):
        """환경변수가 있으면 getpass 없이 그 값을 쓰고 main 종료 후 환경변수는 없다."""
        os.environ['HYUNDAP_ACCOUNT_PASSWORD'] = PASSWORD
        with patch.object(cli.getpass, 'getpass') as prompt, \
                patch.object(cli, 'create_account', return_value='env-user-id') as create:
            code, out, err = self.run_main(['--email', EMAIL, '--role', 'employee',
                                            '--company-id', 'company-a'])
        self.assertEqual(code, 0)
        prompt.assert_not_called()
        create.assert_called_once_with(EMAIL, 'employee', 'company-a', PASSWORD)
        self.assertNotIn('HYUNDAP_ACCOUNT_PASSWORD', os.environ)
        self.assertEqual(out, 'created env-user-id employee\n')
        self.assert_no_secret(out, err)

    def test_db_error_prints_only_fixed_message(self):
        """등록 실패는 고정 안내만 stderr에 출력하고 종료 코드 1이다."""
        os.environ['HYUNDAP_ACCOUNT_PASSWORD'] = PASSWORD
        with patch.object(cli, 'create_account', side_effect=DbError('CONFLICT')):
            code, out, err = self.run_main(['--email', EMAIL, '--role', 'platform_admin'])
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertEqual(err, DbError.MESSAGES['CONFLICT'] + '\n')
        self.assertNotIn('HYUNDAP_ACCOUNT_PASSWORD', os.environ)
        self.assert_no_secret(out, err)


REPO_ROOT = Path(__file__).resolve().parents[2]
NEW_MODULES = ('passwords', 'session_service', 'session_columns', 'accounts', 'create_account')
FORBIDDEN_TABLES = ('login_attempts', 'oauth_states', 'audit_logs')


def imported_modules(source, package):
    """소스의 import 대상 모듈 이름 집합을 절대 이름으로 반환한다(from X import y는 X.y도 포함)."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            if node.level:
                parent = package.rsplit('.', node.level - 1)[0] if node.level > 1 else package
                base = parent + ('.' + base if base else '')
            names.add(base)
            names.update(f'{base}.{alias.name}' for alias in node.names)
    return names


class BoundaryTests(unittest.TestCase):
    """AC-014 + N6 — import 방향·허용 목록·금지 테이블·변경 파일 범위."""

    def test_db_modules_never_import_new_auth_modules(self):
        """hyundap/db/*.py는 새 auth 모듈을 import하지 않는다(검출기 자체도 합성 소스로 확인)."""
        banned = {f'hyundap.auth.{name}' for name in NEW_MODULES}
        self.assertTrue(banned & imported_modules('from hyundap.auth import session_service', 'hyundap.db'))
        self.assertTrue(banned & imported_modules('from ..auth.passwords import x', 'hyundap.db'))
        files = sorted((REPO_ROOT / 'hyundap' / 'db').glob('*.py'))
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.name):
                found = imported_modules(path.read_text(encoding='utf-8'), 'hyundap.db')
                self.assertFalse(found & banned)

    def test_new_modules_import_allowlist_and_tables(self):
        """새 모듈은 표준 라이브러리·SQLAlchemy·hyundap.db·config·auth만 쓰고 금지 테이블을 언급하지 않는다."""
        for name in NEW_MODULES:
            source = (REPO_ROOT / 'hyundap' / 'auth' / f'{name}.py').read_text(encoding='utf-8')
            with self.subTest(module=name):
                for module in imported_modules(source, 'hyundap.auth'):
                    top = module.split('.')[0]
                    allowed = (top in sys.stdlib_module_names or top == 'sqlalchemy'
                               or module.startswith(('hyundap.db', 'hyundap.auth', 'hyundap.common.config')))
                    self.assertTrue(allowed, module)
                for table in FORBIDDEN_TABLES:
                    self.assertNotIn(table, source.lower())

    def test_diff_does_not_touch_forbidden_paths(self):
        """origin/develop 기준 변경 파일에 의존성·스키마·UI·DB 패키지·기존 DB 테스트가 없다."""
        result = subprocess.run(['git', 'diff', '--name-only', 'origin/develop...HEAD'], cwd=REPO_ROOT,
                                capture_output=True, text=True, check=False)
        if result.returncode != 0:
            self.skipTest('origin/develop을 확인할 수 없음')
        changed = result.stdout.split()
        forbidden = ('requirements.txt', 'hyundap/db/', 'docs/db-design/', 'hyundap/ui/',
                     'hyundap/tests/test_db_runtime.py', 'hyundap/tests/test_schema_migration.py')
        for path in changed:
            self.assertFalse(path.startswith(forbidden), path)


@unittest.skipUnless(os.getenv('DB_AUTH_TEST_URL'), '격리 MySQL URL 필요 (DB_AUTH_TEST_URL)')
class DatabaseCreateAccountTests(unittest.TestCase):
    """AC-011, AC-012 DB 절반 — 실제 users 행과 고유 키·회사 상태를 검증한다."""

    @classmethod
    def setUpClass(cls):
        cls.engine = auth_test_engine()

    def setUp(self):
        patcher = patch.object(runtime, 'get_engine', return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

    def user_count(self):
        """users 행 수를 반환한다."""
        with self.engine.connect() as database:
            return database.execute(text('SELECT COUNT(*) FROM users')).scalar_one()

    def test_ac011_create_account_and_login(self):
        """새 company_admin 행의 값과 해시를 확인하고 같은 비밀번호로 로그인한다."""
        company, _ = make_company(self.engine)
        user_id = accounts.create_account('New@Test.invalid', 'company_admin', company, PASSWORD)
        with self.engine.connect() as database:
            row = database.execute(text('''
                SELECT role,auth_version,is_active,login_identifier,password_hash,company_id
                FROM users WHERE id=:id
            '''), {'id': user_id}).mappings().one()
        self.assertEqual((row['role'], row['auth_version'], row['is_active'], row['login_identifier'],
                          row['company_id']), ('company_admin', 1, 1, 'new@test.invalid', company))
        self.assertTrue(verify_password(PASSWORD, row['password_hash']))
        session = session_service.login(username='New@Test.invalid', password=PASSWORD)
        self.assertEqual(session['user_id'], user_id)

    def test_ac012_conflict_and_company_state(self):
        """정규화한 이메일 중복은 CONFLICT, 비활성·없는 회사는 INVALID_INPUT이며 행이 늘지 않는다."""
        before = self.user_count()
        with self.assertRaises(DbError) as caught:
            accounts.create_account(' example@test.invalid ', 'employee', 'company-a', PASSWORD)
        self.assertEqual(caught.exception.code, 'CONFLICT')
        inactive, _ = make_company(self.engine, active=False)
        for company_id in (inactive, str(uuid4())):
            with self.subTest(company_id=company_id), self.assertRaises(DbError) as caught:
                accounts.create_account(f'u.{uuid4().hex[:8]}@test.invalid', 'employee', company_id, PASSWORD)
            self.assertEqual(caught.exception.code, 'INVALID_INPUT')
        self.assertEqual(self.user_count(), before)


if __name__ == '__main__':
    unittest.main()
