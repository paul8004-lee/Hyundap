"""빈 로컬 MySQL을 임시로 실행하여 전체 테스트의 DB 건너뜀을 없앤다.

MYSQL_BASEDIR에 MySQL 8 설치 경로를 지정한 뒤 저장소 루트에서 실행한다.
이 프로그램이 만든 임시 데이터 디렉터리와 로컬 서버만 사용한다.
명령 뒤에 unittest 모듈·클래스·메서드 이름을 주면 해당 검사만 실행한다.
이름을 생략하면 전체 검사를 실행하며, 건너뜀이 하나라도 있으면 실패로 반환한다.
"""

import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import pymysql
from sqlalchemy.engine import URL


def main(test_names=None):
    basedir = Path(os.environ['MYSQL_BASEDIR']).resolve()
    executable = basedir / 'bin' / ('mysqld.exe' if os.name == 'nt' else 'mysqld')
    if not executable.is_file():
        raise SystemExit('MYSQL_BASEDIR에서 mysqld를 찾을 수 없습니다.')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with tempfile.TemporaryDirectory(prefix='hyundap-test-mysql-') as folder:
        datadir = Path(folder) / 'data'
        datadir.mkdir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        common = ['--no-defaults', f'--basedir={basedir}', f'--datadir={datadir}']
        subprocess.run([str(executable), *common, '--initialize-insecure'], check=True,
                       creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server = subprocess.Popen([str(executable), *common, f'--port={port}',
                                   '--bind-address=127.0.0.1', '--mysqlx=OFF', '--skip-log-bin',
                                   f'--log-error={Path(folder) / "mysql.log"}'],
                                  creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    admin = pymysql.connect(host='127.0.0.1', port=port, user='root', password='', autocommit=True)
                    break
                except pymysql.MySQLError:
                    if server.poll() is not None:
                        raise RuntimeError('격리 MySQL이 시작되지 않았습니다.') from None
                    time.sleep(0.2)
            else:
                raise RuntimeError('격리 MySQL 시작 시간이 초과됐습니다.')
            password = secrets.token_urlsafe(24)
            groups = ('auth', 'contract', 'workflow', 'runtime', 'schema', 'split')
            try:
                with admin.cursor() as cursor:
                    cursor.execute("CREATE USER 'hyundap_test'@'127.0.0.1' IDENTIFIED BY %s", (password,))
                    cursor.execute("GRANT ALL PRIVILEGES ON *.* TO 'hyundap_test'@'127.0.0.1' WITH GRANT OPTION")
                    for group in groups:
                        name = 'hyundap_' + group + '_test'
                        cursor.execute('CREATE DATABASE `' + name + '`')
                        os.environ['DB_' + group.upper() + '_TEST_URL'] = URL.create(
                            'mysql+pymysql', username='hyundap_test', password=password,
                            host='127.0.0.1', port=port, database=name).render_as_string(hide_password=False)
            finally:
                admin.close()
            if test_names:
                suite = unittest.defaultTestLoader.loadTestsFromNames(test_names)
            else:
                suite = unittest.defaultTestLoader.discover('hyundap/tests', top_level_dir='.')
            result = unittest.TextTestRunner(verbosity=1).run(suite)
            for test, reason in result.skipped:
                print('SKIPPED:', test.id(), reason)
            return 0 if result.wasSuccessful() and not result.skipped else 1
        finally:
            # Windows의 mysqld는 자식 프로세스가 파일을 열 수 있으므로 SQL로 정상 종료한다.
            try:
                shutdown = pymysql.connect(host='127.0.0.1', port=port, user='root', password='',
                                           connect_timeout=5, autocommit=True)
                try:
                    with shutdown.cursor() as cursor:
                        cursor.execute('SHUTDOWN')
                finally:
                    shutdown.close()
            except pymysql.MySQLError:
                if server.poll() is None:
                    server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
