"""db/connection.py — 로컬 접속 설정으로 MySQL 연결을 만들고 상태를 점검.

접속 비밀값은 프로세스 환경변수 또는 Git에서 제외된 레포 루트 .env로 읽는다.
연결 생성과 읽기 전용 점검을 제공하며 스키마 변경·계정 발급은 수행하지 않는다."""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError


def create_db_engine(database_url=None) -> Engine:
    """로컬 접속 설정을 검사하고 지연 연결 방식의 SQLAlchemy Engine을 만든다.

    프로세스 DATABASE_URL이 레포 루트 .env보다 우선한다.
    MySQL URL의 드라이버·호스트·계정·비밀번호·DB·포트를 검사하고
    PyMySQL, UTF-8, UTC 시간대, 엄격한 SQL 모드로 연결하도록 설정한다.
    설정 누락·잘못된 URL은 비밀값 없는 ValueError로 알린다.
    Engine 생성만으로 DB에 접속하지 않으며 사용 후 호출자가 dispose()해야 한다."""
    # 환경변수와 로컬 설정을 읽고 실제 연결 전에 MySQL URL을 검사한다.
    load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)
    value = database_url
    if not value:
        value = os.environ.get('DATABASE_URL', '').strip()
    if not value:
        raise ValueError('Set DATABASE_URL in your local .env or process environment')
    try:
        # URL의 드라이버, 호스트, 계정, 비밀번호, DB 이름과 포트를 검사한다.
        url = make_url(value)
        # URL 파싱이 성공해도 필요한 값이 비었거나 포트가 범위 밖이면 사용하지 않는다.
        if url.drivername not in ('mysql', 'mysql+pymysql'):
            raise ValueError
        # 연결에 필요한 값 하나씩 확인한다. 비밀값을 오류 메시지에 넣지는 않는다.
        for required_value in (url.host, url.username, url.password, url.database):
            if not required_value:
                raise ValueError
        if url.port is not None and not 0 < url.port < 65536:
            raise ValueError
    except (ArgumentError, ValueError, TypeError):
        raise ValueError('DATABASE_URL must be a valid MySQL URL; check URL-encoded credentials') from None
    # 공개 TCP 경로에서는 운영자가 배포한 CA로 서버 인증서를 확인하고 암호화한다.
    ssl_options = {}
    ca_file = os.environ.get('DB_SSL_CA', '').strip()
    # 명시적으로 다른 서버를 쓰는 격리 테스트/도구에 팀 DB의 CA를 강제하지 않는다.
    if ca_file and database_url:
        configured = os.environ.get('DATABASE_URL', '').strip()
        try:
            configured_url = make_url(configured)
        except (ArgumentError, ValueError, TypeError):
            configured_url = None
        if configured_url is None or (url.host, url.port or 3306) != (configured_url.host, configured_url.port or 3306):
            ca_file = ''
    if ca_file:
        ca_path = Path(ca_file)
        if not ca_path.is_absolute():
            ca_path = Path(__file__).resolve().parents[2] / ca_path
        if not ca_path.is_file():
            raise ValueError('DB_SSL_CA certificate file is missing; check MYSQL_ACCESS.md')
        # MySQL 자체 발급 인증서는 Railway 프록시 도메인을 CN/SAN에 포함하지 않는다.
        # 지정된 CA 서명은 검증하며 TLS 실패 시 평문 연결로 재시도하지 않는다.
        ssl_options = {'ssl_ca': str(ca_path), 'ssl_verify_cert': True, 'ssl_verify_identity': False}
    # 연결 풀, 타임아웃, UTF-8, UTC와 엄격한 SQL 모드를 설정한다.
    return create_engine(
        url.set(drivername='mysql+pymysql'),
        pool_pre_ping=True,
        pool_recycle=1800,
        hide_parameters=True,
        isolation_level='READ COMMITTED',
        connect_args={
            **ssl_options,
            'connect_timeout': 10,
            'read_timeout': 30,
            'write_timeout': 30,
            'charset': 'utf8mb4',
            'init_command': "SET SESSION sql_mode=CONCAT_WS(',',@@sql_mode,'STRICT_TRANS_TABLES'), time_zone='+00:00'",
        },
    )


def main() -> int:
    """현재 DB의 연결과 지원 스키마 구조를 읽기 전용으로 확인한다.

    명령: 저장소 맨 위에서 python -m hyundap.db.connection.
    최종 v4 물리 계약을 검증한 뒤에만 성공으로 판정한다.
    표준 출력에 접속 URL·DB 예외 원문을 노출하지 않고 성공 시 0, 실패 시 1을 반환한다.
    생성한 Engine은 성공·실패와 관계없이 닫으며 스키마 변경은 실행하지 않는다."""
    # 생성한 엔진은 finally에서 정리하고 오류 출력에 접속 정보를 넣지 않는다.
    engine = None
    try:
        engine = create_db_engine()
        with engine.connect() as connection:
            from .schema import snapshot, validate_v4
            from .runtime import split_layout
            layout = snapshot(connection)
            version = layout['version']
            if split_layout():
                from .split_schema import validate_control
                # CLI 출력용으로 읽은 같은 시점의 구조를 검증에도 사용한다.
                errors = validate_control(connection, layout=layout)
            else:
                errors = validate_v4(layout)
            if errors:
                print('DB_CHECK_FAILED: incompatible schema; check the configured DB layout')
                return 1
            count = len(layout['tables'])
        print(f'DB_CONNECTION_OK: schema_version={version}, tables={count}, '
              f'version_storage_ready={version == 4}')
        return 0
    except Exception as error:
        # 드라이버 예외 원문에는 주소·계정·비밀번호가 섞일 수 있어 숫자 코드만 사용한다.
        args = getattr(getattr(error, 'orig', None), 'args', ())
        code = args[0] if args and isinstance(args[0], int) else None
        message = {
            1044: 'DB_ACCESS_DENIED: 발급된 계정의 대상 DB 접근 권한을 확인하세요.',
            1045: 'DB_AUTH_FAILED: DB 계정과 비밀번호를 확인하세요.',
            1049: 'DB_NOT_FOUND: DATABASE_URL의 DB 이름을 확인하세요. DB를 자동 생성하지 않습니다.',
            2003: 'DB_UNREACHABLE: DB 주소·포트와 네트워크를 확인하세요. localhost 주소는 해당 PC의 DB 또는 SSH 터널이 필요합니다.',
            2005: 'DB_HOST_NOT_FOUND: 공개 DB 호스트 이름을 확인하세요. Railway 내부 주소는 외부 PC에서 사용할 수 없습니다.',
        }.get(code, 'DB_CONFIG_OR_CONNECTION_ERROR: .env.example과 MYSQL_ACCESS.md의 설정·연결 절차를 확인하세요.')
        print('DB_CHECK_FAILED:', message)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__=='__main__':
    raise SystemExit(main())
