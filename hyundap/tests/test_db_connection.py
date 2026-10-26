"""Connection configuration and error reporting must not expose credentials."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from db import connection


class DatabaseConnectionTests(unittest.TestCase):
    def test_normalizes_mysql_driver_without_connecting(self):
        with patch.dict('os.environ',{'DATABASE_URL':'mysql://team:private-password@localhost:3306/railway'},clear=True),patch.object(connection,'load_dotenv'):
            engine=connection.create_db_engine()
            try:
                self.assertEqual(engine.url.drivername,'mysql+pymysql')
                self.assertEqual(engine.url.database,'railway')
            finally:engine.dispose()

    def test_missing_url_has_no_fallback(self):
        with patch.dict('os.environ',{},clear=True),patch.object(connection,'load_dotenv'),self.assertRaises(ValueError):
            connection.create_db_engine()

    def test_invalid_urls_do_not_expose_credentials(self):
        for value in ('sqlite:///private-password','mysql://team:private-password@localhost:99999/db','mysql://team@localhost/db'):
            with self.subTest(value=value),patch.dict('os.environ',{'DATABASE_URL':value},clear=True),patch.object(connection,'load_dotenv'):
                with self.assertRaises(ValueError) as caught:connection.create_db_engine()
                self.assertNotIn('private-password',str(caught.exception))

    def test_cli_does_not_print_connection_exception(self):
        output=io.StringIO()
        with patch.object(connection,'create_db_engine',side_effect=RuntimeError('private-password')),redirect_stdout(output):
            self.assertEqual(connection.main(),1)
        self.assertNotIn('private-password',output.getvalue())


if __name__=='__main__':unittest.main()
