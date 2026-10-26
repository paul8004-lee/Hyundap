"""UI·검색·챗봇이 구분해서 처리하는 인증·접근 오류."""


class AccessError(Exception):
    """계정 존재 여부·토큰·비밀번호를 노출하지 않는 인증 오류."""

    MESSAGES = {
        'INVALID_CREDENTIALS': '로그인 정보를 확인해 주세요.',
        'SESSION_EXPIRED': '다시 로그인해 주세요.',
        'ACCESS_DENIED': '접근 권한이 없습니다.',
        'STATE_CHANGED': '문서 상태가 변경되었습니다. 다시 요청해 주세요.',
    }

    def __init__(self, code):
        """정의된 code와 고정 안내만 저장한다. 원시 DB·검증 예외는 전달하지 않는다."""
        self.code = code
        super().__init__(self.MESSAGES[code])
