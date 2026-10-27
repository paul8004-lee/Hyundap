"""실제 임베딩·Chroma·MySQL의 소규모 의미 검색 시험. 합성 문서만 외부 API에 전송한다.

SEMANTIC_TEST_URL은 127.0.0.1의 빈 hyundap_semantic_eval_test DB만 허용한다.
실행: python -m hyundap.tests.smoke.semantic_embedding_smoke. API 키는 루트 .env에서 읽는다.
"""
import asyncio
import json
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from hyundap.common.config import get_settings
from hyundap.db import runtime, schema
from .services import DatabaseSmokeServices


SAMPLES = (
    ('maintenance.docx', '승강기 안전 점검은 매월 한 번 실시한다. 점검 항목은 비상정지장치와 출입문 센서다. 이상이 발견되면 운행을 중단하고 정비 담당자에게 보고한다.',
     '엘리베이터 상태를 확인하는 주기는 어떻게 되나요?'),
    ('expenses.docx', '출장자는 업무상 지출한 교통비와 숙박비를 회사에 청구할 수 있다. 비용 정산 신청에는 영수증을 첨부해야 하며 출장 종료 후 7일 이내에 제출한다.',
     '업무 때문에 이동하면서 쓴 돈은 어떻게 돌려받나요?'),
    ('leave.docx', '연차 휴가를 사용하려는 직원은 희망 날짜의 3영업일 전까지 신청서를 제출하고 상급자의 승인을 받아야 한다. 잔여 일수는 인사 시스템에서 확인한다.',
     '며칠 쉬려면 언제까지 신청해야 하나요?'),
)


async def seed_samples(service, directory):
    from docx import Document

    session = await service.login('manager', 'demo1234')
    uploaded = {}
    for name, content, _ in SAMPLES:
        path = directory / name
        doc = Document()
        doc.add_paragraph(content)
        doc.save(path)
        result = await service.upload(session, str(path), 'hri', 'manual')
        if result['status'] != 'succeeded':
            raise RuntimeError('Synthetic upload failed: ' + str(result.get('error_code')))
        uploaded[name] = result['document_id']
    return uploaded


async def exercise(service, directory):
    uploaded = await seed_samples(service, directory)
    session = await service.login('manager', 'demo1234')
    outcomes = []
    for name, _, question in SAMPLES:
        response = await service.search(session, question, company_ids=['hri'])
        results = response['results']
        outcomes.append(dict(question=question, expected=name,
            top_file=results[0]['file_name'] if results else None,
            top_score=results[0]['score'] if results else None,
            top1_correct=bool(results and results[0]['document_id'] == uploaded[name]),
            results=[dict(file=item['file_name'], score=item['score']) for item in results]))
    irrelevant = await service.search(session, '토성의 고리는 어떤 물질로 이루어져 있나요?', company_ids=['hri'])
    return dict(model=get_settings().openai_embedding_model, documents=3,
                natural_questions=outcomes, top1_passed=sum(item['top1_correct'] for item in outcomes),
                unrelated_result_count=len(irrelevant['results']),
                limitation='합성 문서 3개에 대한 표본 시험이며 일반 검색 품질을 보장하지 않는다.')


def main():
    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit('OPENAI_API_KEY is not configured; no API request was sent.')
    url = make_url(os.environ['SEMANTIC_TEST_URL'])
    if url.host != '127.0.0.1' or not re.fullmatch(r'hyundap_semantic_eval_test(?:_[0-9a-f]{8})?', url.database or ''):
        raise SystemExit('Only a dedicated local semantic evaluation DB is allowed.')
    engine = create_engine(url, isolation_level='READ COMMITTED', hide_parameters=True,
                           connect_args={'init_command': "SET time_zone='+00:00'"})
    try:
        schema.create_v4_schema(engine)
        original_get_engine = runtime.get_engine
        runtime.get_engine = lambda: engine
        try:
            # Windows에서 Chroma가 연 파일은 프로세스 종료까지 남을 수 있다.
            with TemporaryDirectory(prefix='hyundap-semantic-eval-', ignore_cleanup_errors=True) as directory:
                root = Path(directory)
                os.environ['CHROMA_DB_PATH'] = str(root / 'chroma')
                get_settings.cache_clear()
                service = DatabaseSmokeServices(engine, root / 'originals')
                result = asyncio.run(exercise(service, root))
                Path('.git/semantic-embedding-result.json').write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(result, ensure_ascii=False, indent=2))
                if result['top1_passed'] != 3:
                    raise SystemExit('Some natural-language queries did not rank the expected document first.')
        finally:
            runtime.get_engine = original_get_engine
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
