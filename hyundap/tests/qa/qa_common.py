"""기능 점검(WBS 4.1.x) 공통 도구. 계정·상태 파일을 읽고 화면과 같은 BackendServices로 백엔드를 부른다.

계정 파일(data/qa_accounts.json)과 업로드 상태 파일(data/qa_state.json)은 저장소 밖(data/는 gitignore)에 둔다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import hyundap.auth.session_service as real_auth  # noqa: E402
from hyundap.ui.backend_bindings import create_bindings  # noqa: E402
from hyundap.ui.services import BackendServices, UIServiceError  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = ROOT / 'data'
ACCOUNTS_FILE = DATA / 'qa_accounts.json'
STATE_FILE = DATA / 'qa_state.json'
CORPUS = json.loads((HERE / 'corpus.json').read_text(encoding='utf-8'))

# 회사 약칭 → 그 회사의 관리자·직원 계정 키
COMPANY_ACCOUNTS = {'mvx': ('mvx_admin', 'mvx_employee'), 'hel': ('hel_admin', 'hel_employee'),
                    'hri': ('hri_admin', 'hri_employee')}


def load_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def company_id(key):
    """회사 약칭(mvx/hel/hri)을 실제 회사 ID로 바꾼다."""
    return load_json(ACCOUNTS_FILE, {})[COMPANY_ACCOUNTS[key][0]]['company_id']


class Client:
    """계정별 로그인 세션을 한 번만 만들어 재사용한다."""

    def __init__(self):
        self.service = BackendServices(create_bindings(authentication=real_auth))
        self.accounts = load_json(ACCOUNTS_FILE, {})
        self.sessions = {}

    async def session(self, account):
        if account not in self.sessions:
            info = self.accounts[account]
            self.sessions[account] = await self.service.login(info['username'], info['password'])
        return self.sessions[account]

    async def close(self):
        for session in self.sessions.values():
            try:
                await self.service.logout(session)
            except UIServiceError:
                pass
        self.sessions.clear()
