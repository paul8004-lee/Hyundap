"""DB 호출자가 원시 SQL·접속 정보 없이 구분할 수 있는 오류 코드."""


class DbError(Exception):
    """안전한 코드와 고정 안내만 전달하는 DB 오류. 원시 예외는 포함하지 않는다."""

    MESSAGES = {
        'PROVISION_FAILED': '회사 데이터베이스를 준비하지 못했습니다.',
        'CONFIG_MISSING': '필수 데이터베이스 설정이 없습니다.',
        'DB_UNAVAILABLE': '데이터 저장소를 사용할 수 없습니다.',
        'DUPLICATE_FILE': '같은 회사에 동일한 파일이 있습니다.',
        'CONFLICT': '다른 처리 결과와 충돌했습니다.',
        'INVALID_STATE': '현재 상태에서는 처리할 수 없습니다.',
        'INVALID_INPUT': '입력값이 올바르지 않습니다.',
        'INVALID_DOC_TYPE': '문서 유형이 올바르지 않습니다.',
        'FILE_UNAVAILABLE': '업로드한 파일을 읽을 수 없습니다.',
        'INTEGRATION_UNAVAILABLE': '수집·벡터 연동 기능이 아직 준비되지 않았습니다.',
        'PROCESSING_FAILED': '문서 처리가 실패했습니다.',
        'INCOMPLETE_INDEX': '저장된 청크가 기준 목록과 일치하지 않습니다.',
        'ACTIVATION_FAILED': '벡터 활성화가 실패했습니다.',
        'RECOVERY_REQUIRED': '문서 상태 복구를 다시 실행해야 합니다.',
    }

    def __init__(self, code):
        """정의된 code를 보관하고 사용자 데이터가 없는 안내를 예외 문자열로 설정한다."""
        self.code = code
        super().__init__(self.MESSAGES[code])
