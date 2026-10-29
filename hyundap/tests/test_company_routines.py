"""회사별 프로시저의 입력 경계와 안전한 오류 계약을 확인한다."""

import unittest
from unittest.mock import Mock

from pymysql.err import OperationalError as MySQLError
from sqlalchemy.exc import OperationalError

from hyundap.auth.errors import AccessError
from hyundap.db.company_routines import authenticate_company_actor, procedure_statements, register_company_route
from hyundap.db.errors import DbError


class CompanyRoutineContractTests(unittest.TestCase):
    def test_statements_are_scoped_to_trusted_company_and_account(self):
        statements = procedure_statements('control_db', 'company-a', 'tenant_abc')
        self.assertEqual(len(statements), 5)
        self.assertTrue(all('SQL SECURITY DEFINER' in sql for sql in statements))
        self.assertIn("'company-a'", statements[0])
        self.assertIn("'tenant_abc'", statements[0])
        self.assertIn('platform_admin', statements[0])
        self.assertIn('document_versions', statements[1])
        self.assertIn('index_jobs', statements[1])

    def test_rejects_untrusted_identifiers(self):
        for control, company, account in [
            ('db;DROP', 'company-a', 'tenant_abc'),
            ('control', "x' OR 1=1", 'tenant_abc'),
            ('control', 'company-a', "x'@'%'"),
        ]:
            with self.subTest(control=control, company=company, account=account):
                with self.assertRaises(DbError):
                    procedure_statements(control, company, account)

    def test_route_registration_rejects_invalid_ids_before_database_access(self):
        with self.assertRaises(DbError) as error:
            register_company_route(None, '', 'version', 'job')
        self.assertEqual(error.exception.code, 'INVALID_INPUT')

    def test_database_errors_are_sanitized_and_classified(self):
        class FailingDatabase:
            def __init__(self, code, message):
                self.code, self.message = code, message

            def execute(self, statement, values):
                raise OperationalError('secret SQL', {}, MySQLError(self.code, self.message))

        with self.assertRaises(DbError) as error:
            register_company_route(FailingDatabase(1644, 'INVALID_STATE'), 'd', 'v', 'j')
        self.assertEqual(error.exception.code, 'INVALID_STATE')
        with self.assertRaises(DbError) as error:
            register_company_route(FailingDatabase(2003, 'secret password'), 'd', 'v', 'j')
        self.assertEqual(error.exception.code, 'DB_UNAVAILABLE')
        self.assertNotIn('secret', str(error.exception))
        actor = {'session_id': 's', '_session_token': 'a' * 43}
        with self.assertRaises(AccessError) as error:
            authenticate_company_actor(FailingDatabase(1644, 'SESSION_EXPIRED'), actor)
        self.assertEqual(error.exception.code, 'SESSION_EXPIRED')

    def test_driver_error_shapes_do_not_expose_details(self):
        cases = [
            ((), DbError, 'DB_UNAVAILABLE'),
            ((1644,), DbError, 'DB_UNAVAILABLE'),
            ((1644, 'ACCESS_DENIED'), AccessError, 'ACCESS_DENIED'),
            ((1644, 'secret password'), DbError, 'DB_UNAVAILABLE'),
            ((1062, 'secret duplicate value'), DbError, 'CONFLICT'),
        ]
        for arguments, expected_type, expected_code in cases:
            with self.subTest(arguments=arguments):
                database = Mock()
                database.execute.side_effect = OperationalError('secret SQL', {}, MySQLError(*arguments))
                with self.assertRaises(expected_type) as caught:
                    register_company_route(database, 'd', 'v', 'j')
                self.assertEqual(caught.exception.code, expected_code)
                self.assertNotIn('secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
