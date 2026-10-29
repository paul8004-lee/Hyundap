"""브라우저에 묶인 일회성 원문 전송. DB 세션 토큰과 원본 경로를 URL에 넣지 않는다."""

from copy import deepcopy
from secrets import token_urlsafe, compare_digest
from threading import RLock
from time import monotonic
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from .services import UIServiceError

COOKIE = 'hyundap_download_browser'
PREFIX = '/hyundap-download/'
TTL = 60
_tickets = {}
_lock = RLock()


def issue_download(session, document, browser, service):
    """권한 확인된 UI 요청에서만 발급한다. 최대 1분·1회·동일 브라우저용이다."""
    if not browser:
        raise UIServiceError('DOWNLOAD_UNAVAILABLE', '앱 실행 경로를 확인한 뒤 다시 로그인해 주세요.')
    now = monotonic()
    with _lock:
        # 반복 한 번마다 만료된 티켓 하나만 제거한다. 살아 있는 티켓은 보존한다.
        for key in list(_tickets):
            if _tickets[key]['expires'] <= now:
                del _tickets[key]
        if len(_tickets) >= 1024:
            raise UIServiceError('DOWNLOAD_UNAVAILABLE', '잠시 후 다시 시도해 주세요.')
        ticket = token_urlsafe(32)
        _tickets[ticket] = dict(session=deepcopy(session), document=deepcopy(document),
                                browser=browser, service=service, expires=now + TTL)
    return PREFIX + ticket


def take_download(ticket, browser):
    """다른 브라우저는 티켓을 소비할 수도 없다. 정상 요청만 한 번 꺼낸다."""
    with _lock:
        entry = _tickets.get(ticket)
        if entry is None:
            return None
        if entry['expires'] <= monotonic():
            del _tickets[ticket]
            return None
        if not browser or not browser.isascii() or not compare_digest(entry['browser'], browser):
            return None
        return _tickets.pop(ticket)


async def file_blocks(stream):
    """파일 전체를 메모리에 적재하지 않는다. 파일 핸들 소유자는 HTTP 전송자다."""
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            break
        yield block


class DownloadMiddleware:
    """ASGI 전송 어댑터. 업무 로직은 함수에 두고 Gradio 서버의 요청만 감싼다."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        if request.url.path.startswith(PREFIX):
            entry = take_download(request.url.path[len(PREFIX):], request.cookies.get(COOKIE))
            if request.method != 'GET' or entry is None:
                return await Response(status_code=403)(scope, receive, send)
            prepared = None
            try:
                # 발급 이후 로그아웃·권한 변경·버전 변경도 service와 DB가 다시 검사한다.
                document = entry['document']
                prepared = await entry['service'].download(entry['session'], document['document_id'], document['version_id'])
                filename = str(prepared['file_name']).replace('\\', '/').split('/')[-1]
                # 전송 중 연결이 끊겨도 파일 핸들을 먼저 닫고 finally에서 사본을 지운다.
                with prepared['path'].open('rb') as stream:
                    response = StreamingResponse(file_blocks(stream), media_type='application/octet-stream',
                        headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename, safe=''),
                                 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
                    await response(scope, receive, send)
            except UIServiceError:
                await Response('다운로드 권한 또는 파일 상태를 확인해 주세요.', status_code=403)(scope, receive, send)
            finally:
                if prepared is not None:
                    prepared['temporary'].cleanup()
            return

        async def send_with_cookie(message):
            # 로그인 전 첫 화면에서만 비밀 없는 브라우저 식별 쿠키를 만든다.
            if message['type'] == 'http.response.start' and request.url.path == '/' and not request.cookies.get(COOKIE):
                cookie = Response()
                cookie.set_cookie(COOKIE, token_urlsafe(32), httponly=True, samesite='strict',
                                  secure=request.url.scheme == 'https', path='/')
                message = dict(message, headers=list(message.get('headers', [])) +
                               [item for item in cookie.raw_headers if item[0] == b'set-cookie'])
            await send(message)
        await self.app(scope, receive, send_with_cookie)
