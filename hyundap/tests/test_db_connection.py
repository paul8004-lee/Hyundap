"""tests/test_db_connection.py — DB 접속 설정과 오류 출력의 비밀정보 보호 검증.

테스트 실행 명령은 저장소 맨 위에서 python -m unittest hyundap.tests.test_db_connection -v이다."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from hyundap.db import connection


class DatabaseConnectionTests(unittest.TestCase):
    """실제 DB 접속 없이 연결 설정과 비밀정보 비노출을 검증한다."""
    def test_normalizes_mysql_driver_without_connecting(self):
        """MySQL URL을 PyMySQL 드라이버로 정규화하고 지정 DB를 유지하는지 확인한다."""
        with patch.dict('os.environ',{'DATABASE_URL':'mysql://team:private-password@localhost:3306/railway'},clear=True),patch.object(connection,'load_dotenv'):
            engine=connection.create_db_engine()
            try:
                self.assertEqual(engine.url.drivername,'mysql+pymysql')
                self.assertEqual(engine.url.database,'railway')
            finally:engine.dispose()

    def test_missing_url_has_no_fallback(self):
        """접속 URL이 없을 때 임의 DB로 연결하지 않고 ValueError를 내는지 확인한다."""
        with patch.dict('os.environ',{},clear=True),patch.object(connection,'load_dotenv'),self.assertRaises(ValueError):
            connection.create_db_engine()

    def test_engine_initializes_mysql_session_in_utc(self):
        """앱 Engine은 세션 시간대를 UTC로 설정해 만료 시각 변환을 보호한다."""
        with patch.dict('os.environ', {'DATABASE_URL': 'mysql://team:password@localhost/db'}, clear=True), \
                patch.object(connection, 'load_dotenv'), patch.object(connection, 'create_engine') as create:
            connection.create_db_engine()
        self.assertIn("time_zone='+00:00'", create.call_args.kwargs['connect_args']['init_command'])

    def test_invalid_urls_do_not_expose_credentials(self):
        """드라이버·포트·비밀번호가 잘못된 URL의 오류에 비밀값이 노출되지 않는지 확인한다."""
        for value in ('sqlite:///private-password','mysql://team:private-password@localhost:99999/db','mysql://team@localhost/db'):
            with self.subTest(value=value),patch.dict('os.environ',{'DATABASE_URL':value},clear=True),patch.object(connection,'load_dotenv'):
                with self.assertRaises(ValueError) as caught:connection.create_db_engine()
                self.assertNotIn('private-password',str(caught.exception))

    def test_cli_does_not_print_connection_exception(self):
        """CLI가 접속 예외의 원문을 숨기고 실패 코드 1을 반환하는지 확인한다."""
        output=io.StringIO()
        with patch.object(connection,'create_db_engine',side_effect=RuntimeError('private-password')),redirect_stdout(output):
            self.assertEqual(connection.main(),1)
        self.assertNotIn('private-password',output.getvalue())


if __name__=='__main__':unittest.main()
