"""corpus.json의 문서를 각 회사 관리자 계정으로 업로드한다. 이미 올린 문서는 건너뛴다.

실행(저장소 맨 위): python hyundap/tests/qa/upload_corpus.py
결과(문서 ID·버전 ID·소요 시간)는 data/qa_state.json에 쌓는다.
"""
import asyncio
import time
from pathlib import Path

from qa_common import CORPUS, COMPANY_ACCOUNTS, Client, UIServiceError, company_id, load_json, save_state, STATE_FILE


async def main():
    client = Client()
    state = load_json(STATE_FILE, {'corpus': {}})
    root = Path(CORPUS['source_root'])
    try:
        for doc in CORPUS['documents']:
            if state['corpus'].get(doc['key'], {}).get('status') == 'succeeded':
                continue
            admin = await client.session(COMPANY_ACCOUNTS[doc['company']][0])
            started = time.monotonic()
            try:
                result = await client.service.upload(admin, str(root / doc['path']), company_id(doc['company']), doc['doc_type'])
            except UIServiceError as error:
                result = {'status': 'error', 'error_code': error.code, 'message': error.message}
            result['seconds'] = round(time.monotonic() - started, 1)
            state['corpus'][doc['key']] = result
            save_state(state)
            print(doc['key'], result.get('status'), result.get('error_code'), result['seconds'], flush=True)
    finally:
        await client.close()


if __name__ == '__main__':
    asyncio.run(main())
