"""B24~B28: 백엔드팀이 실제로 제공하는 함수만 이곳에 연결한다.

현재 UI에 실제 백엔드 함수는 등록하지 않았다. 기존 백엔드 구현은 보존한다.
담당 팀과 함수명, 입력/반환값, 인증 UserContext/ChatFilters가 확정되면 빈 바인딩을
실제 함수로 채운다. import 경로·함수 이름이 화면 이벤트명과 같을 필요는 없다.
실행 예: python -m hyundap.app --mode backend  (미연결 기능은 오류 안내 표시)
"""
from .services import BackendBindings


def create_bindings() -> BackendBindings:
    # B24: login / logout / validate_session 및 user_factory
    # B25: search / detail / summarize_document / download
    # B26: upload. 삭제·재색인 등 P3는 이 화면 범위에서 제외.
    # B27: ask / filters_factory / to_feedback
    # B28: detail 함수에서 document_id + version_id 재검증
    # 담당자가 제공한 실제 함수로 교체. 미연결을 데모 데이터로 숨기지 않는다.
    return BackendBindings()
