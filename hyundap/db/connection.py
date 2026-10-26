"""Shared MySQL connection factory. Credentials are loaded locally, never stored in Git."""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError


def create_db_engine() -> Engine:
    """Create a lazy MySQL engine from the process environment or project .env."""
    load_dotenv(Path(__file__).resolve().parents[2]/'.env',override=False)
    value=os.environ.get('DATABASE_URL','').strip()
    if not value:
        raise ValueError('Set DATABASE_URL in your local .env or process environment')
    try:
        url=make_url(value)
        if (url.drivername not in ('mysql','mysql+pymysql') or not url.host
                or not url.username or not url.password or not url.database
                or (url.port is not None and not 0 < url.port < 65536)):
            raise ValueError
    except (ArgumentError,ValueError,TypeError):
        raise ValueError('DATABASE_URL must be a valid MySQL URL; check URL-encoded credentials') from None
    return create_engine(url.set(drivername='mysql+pymysql'),pool_pre_ping=True,
                         pool_recycle=1800,hide_parameters=True,isolation_level='READ COMMITTED',
                         connect_args={'connect_timeout':10,'read_timeout':30,'write_timeout':30,
                                       'charset':'utf8mb4',
                                       'init_command':"SET SESSION sql_mode=CONCAT_WS(',',@@sql_mode,'STRICT_TRANS_TABLES')"})


def main() -> int:
    """Read-only connection check. Do not echo connection URLs or exception messages."""
    engine=None
    try:
        engine=create_db_engine()
        with engine.connect() as connection:
            version=connection.execute(text('SELECT version FROM schema_migrations')).scalar_one()
            if version!=3:
                print('DB_CHECK_FAILED: schema version mismatch')
                return 1
            count=connection.execute(text('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()')).scalar_one()
        print(f'DB_CONNECTION_OK: schema_version={version}, tables={count}')
        return 0
    except Exception as error:
        print('DB_CHECK_FAILED:',type(error).__name__,'Check DATABASE_URL, account permissions and network access.')
        return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':
    raise SystemExit(main())
