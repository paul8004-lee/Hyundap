"""UI·검색·챗봇이 구분해서 처리하는 DB 접근 검증 오류."""


class AccessError(Exception):
    """토큰과 내부 DB 정보를 노출하지 않는 접근 검증 오류."""

    MESSAGES = {
        'SESSION_EXPIRED': '다시 로그인해 주세요.',
        'ACCESS_DENIED': '접근 권한이 없습니다.',
        'STATE_CHANGED': '문서 상태가 변경되었습니다. 다시 요청해 주세요.',
        'INVALID_CREDENTIALS': '아이디 또는 비밀번호가 올바르지 않습니다.',
    }

    def __init__(self, code):
        """정의된 code와 고정 안내만 저장한다. 원시 DB·검증 예외는 전달하지 않는다."""
        self.code = code
        super().__init__(self.MESSAGES[code])
