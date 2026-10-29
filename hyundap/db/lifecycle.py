"""서버 수명 종료 시 이미 생성된 DB 풀을 정리한다. 종료를 위해 새 DB에 연결하지 않는다."""

from contextlib import asynccontextmanager
from . import runtime, tenancy


def close_service_engines():
    """요청이 끝난 서버 종료 시 호출한다. 회사 풀을 먼저, 공통 풀을 나중에 정리한다."""
    tenancy.close_company_engines()
    if runtime.get_engine.cache_info().currsize:
        engine = runtime.get_engine()
        runtime.get_engine.cache_clear()
        engine.dispose()


@asynccontextmanager
async def service_lifespan(app):
    """FastAPI/Gradio 서버의 정상 종료에 연결한다. 강제 kill은 OS 정리에 의존한다."""
    try:
        yield
    finally:
        close_service_engines()
