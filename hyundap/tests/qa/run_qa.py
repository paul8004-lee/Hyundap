"""기능 점검 시나리오(TEST_SCENARIOS.md)를 실행하고 결과를 results/에 기록한다.

실행(저장소 맨 위, upload_corpus.py 실행 후): python hyundap/tests/qa/run_qa.py [분류...]
분류: format filter permission exception accuracy (생략하면 전부)
"""
import asyncio
import json
import re
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from sqlalchemy import text

from qa_common import (CORPUS, COMPANY_ACCOUNTS, DATA, HERE, STATE_FILE, Client, UIServiceError,
                       company_id, load_json)
from hyundap.db.runtime import transaction

STATE = load_json(STATE_FILE, {'corpus': {}})
DOCS = {doc['key']: doc for doc in CORPUS['documents']}
SAMPLES = DATA / 'qa_samples'
LOCATION = {'.pdf': r'^p\.\d+', '.docx': r'^섹션 \d+', '.pptx': r'^슬라이드 \d+'}
SCENARIOS = []


def scenario(sid, category, title):
    """시나리오 함수를 등록한다. 함수는 (판정, 관찰 내용)을 돌려준다."""
    def register(function):
        SCENARIOS.append({'id': sid, 'category': category, 'title': title, 'run': function})
        return function
    return register


def doc_id(key):
    return STATE['corpus'][key]['document_id']


def key_of(document_id):
    found = [k for k, v in STATE['corpus'].items() if v.get('document_id') == document_id]
    return found[0] if found else str(document_id)[:8]


async def expect_error(call, *codes):
    """호출이 지정한 오류 코드 중 하나로 거부되면 PASS."""
    try:
        result = await call
    except UIServiceError as error:
        return ('PASS' if error.code in codes else 'FAIL'), f'{error.code}: {error}'
    return 'FAIL', '거부되지 않음: ' + json.dumps(result, ensure_ascii=False, default=str)[:200]


def companies_of(results):
    return sorted({key_of(r['document_id']).split('-')[0] for r in results})


# ---------- #81 형식별 업로드·내용 추출 ----------

def ingest_report(version_id):
    with transaction() as database:
        row = database.execute(text('SELECT status, stage, report FROM index_jobs WHERE version_id=:v'),
                               {'v': version_id}).mappings().one_or_none()
    report = row['report'] if row else None
    return row, json.loads(report) if isinstance(report, str) else report or {}


def make_format_scenario(suffix, label):
    keys = [k for k, d in DOCS.items() if d['path'].endswith(suffix)]
    searchable = [k for k in keys if not DOCS[k].get('expect_error')]

    @scenario('U-' + suffix[1:].upper(), 'format', f'{label} 업로드·추출 ({len(keys)}건)')
    async def run(client):
        problems, lines = [], []
        for key in keys:
            info = STATE['corpus'].get(key, {})
            expected = DOCS[key].get('expect_error')
            if expected:
                ok = info.get('status') == 'failed' and info.get('error_code') == expected
                (lines if ok else problems).append(f"{key} 기대한 거부 {expected} → {info.get('status')}/{info.get('error_code')}")
                continue
            if info.get('status') != 'succeeded':
                problems.append(f"{key} 업로드 {info.get('status')}/{info.get('error_code')}")
                continue
            row, report = ingest_report(info['version_id'])
            ingest = report.get('ingest', report)
            sections, chunks = ingest.get('section_count'), ingest.get('chunk_count')
            lines.append(f"{key} 섹션 {sections}·청크 {chunks}·{info.get('seconds')}초")
            if not row or not sections or not chunks:
                problems.append(f'{key} 보고서 없음 또는 섹션 {sections}, 청크 {chunks}')
        # 위치 문구 형식: 문서 제목으로 검색해 그 문서 출처의 위치 문구를 확인한다.
        admin = await client.session('platform_admin')
        for key in searchable[:3]:
            title = Path(DOCS[key]['path']).stem.split('_', 1)[-1].replace('가상_', '')
            found = [r for r in (await client.service.search(admin, title))['results'] if key_of(r['document_id']) == key]
            labels = [c.get('location_label', '') for r in found for c in r['citations']]
            if not labels or not all(re.match(LOCATION[suffix], value) for value in labels):
                problems.append(f'{key} 위치 문구 {labels[:3] or "검색 안 됨"}')
            else:
                lines.append(f'{key} 위치 {labels[:2]}')
        return ('FAIL' if problems else 'PASS'), '; '.join(problems or lines)
    return run


for _suffix, _label in (('.pdf', 'PDF'), ('.docx', 'DOCX'), ('.pptx', 'PPTX')):
    make_format_scenario(_suffix, _label)


# ---------- #82 필터 검색 ----------

async def filtered(client, query, companies=None, doc_types=None, account='platform_admin'):
    ids = [company_id(c) for c in companies] if companies else None
    return (await client.service.search(await client.session(account), query, ids, doc_types))['results']


def check_filter(results, companies=None, doc_types=None):
    seen_companies = companies_of(results)
    seen_types = sorted({str(r.get('doc_type')) for r in results})
    wrong_company = companies and set(seen_companies) - {c.upper() for c in companies}
    wrong_type = doc_types and set(seen_types) - set(doc_types)
    verdict = 'PASS' if results and not wrong_company and not wrong_type else 'FAIL'
    return verdict, f'{len(results)}건, 회사 {seen_companies}, 유형 {seen_types}'


@scenario('F1', 'filter', '회사 1곳(hri) 필터')
async def f1(client):
    return check_filter(await filtered(client, '신규 담당자 실습', ['hri']), ['hri'])


@scenario('F2', 'filter', '회사 2곳(mvx·hel) 필터')
async def f2(client):
    results = await filtered(client, '접수와 등록 매뉴얼', ['mvx', 'hel'])
    verdict, note = check_filter(results, ['mvx', 'hel'])
    return ('FAIL' if verdict == 'PASS' and len(companies_of(results)) < 2 else verdict), note


@scenario('F3', 'filter', '문서 유형 manual 필터')
async def f3(client):
    return check_filter(await filtered(client, '신규 담당자 실습', doc_types=['manual']), doc_types=['manual'])


@scenario('F4', 'filter', '문서 유형 guide 필터')
async def f4(client):
    return check_filter(await filtered(client, '접수와 등록', doc_types=['guide']), doc_types=['guide'])


@scenario('F5', 'filter', '회사 hri + 유형 guide 조합')
async def f5(client):
    return check_filter(await filtered(client, '실습', ['hri'], ['guide']), ['hri'], ['guide'])


@scenario('F6', 'filter', '문서가 없는 유형(policy) 필터')
async def f6(client):
    results = await filtered(client, '접수와 등록', doc_types=['policy'])
    return ('PASS' if not results else 'FAIL'), f'{len(results)}건 (오류 없이 0건 기대)'


@scenario('F7', 'filter', '회사 관리자(hri) 필터 없이 검색')
async def f7(client):
    return check_filter(await filtered(client, '접수와 등록', account='hri_admin'), ['hri'])


@scenario('F8', 'filter', '질문 범위를 문서 1개로 한정')
async def f8(client):
    target = doc_id('HRI-SYN-MAN-001')
    answer = await client.service.ask(await client.session('hri_employee'), '경제지표 원천을 처음 등록할 때 첫 검토 기한은?',
                                      document_ids=[target])
    cited = {c.get('document_id') for c in answer.get('citations') or []}
    verdict = 'PASS' if str(answer.get('status')).lower() == 'answered' and cited == {target} else 'FAIL'
    reply = (answer.get('answer') or '')[:80]
    return verdict, f"상태 {answer.get('status')}, 출처 문서 {[key_of(c) for c in cited]}, 답 {reply}"


# ---------- #84 권한 ----------

def hri_leaks(items, field='document_id'):
    return [key_of(item.get(field, '')) for item in items if key_of(item.get(field, '')).startswith('HRI')]


@scenario('P1', 'permission', '타사 직원(mvx)이 hri 문서 주제로 검색')
async def p1(client):
    results = await filtered(client, '경제지표 원천 등록 접수와 등록', account='mvx_employee')
    leaked = hri_leaks(results)
    return ('FAIL' if leaked else 'PASS'), f'{len(results)}건, hri 노출 {leaked}'


@scenario('P2', 'permission', '타사 직원(mvx)이 hri 회사 필터를 직접 지정')
async def p2(client):
    try:
        results = await filtered(client, '경제지표 원천 등록', ['hri'], account='mvx_employee')
    except UIServiceError as error:
        return 'PASS', f'거부 {error.code}: {error}'
    leaked = hri_leaks(results)
    return ('FAIL' if leaked else 'PASS'), f'{len(results)}건, hri 노출 {leaked}'


@scenario('P3', 'permission', '타사 직원(mvx)이 hri 문서 내용을 질문')
async def p3(client):
    answer = await client.service.ask(await client.session('mvx_employee'), '경제지표 원천 등록의 첫 검토 기한은?')
    leaked = hri_leaks(answer.get('citations') or [])
    return ('FAIL' if leaked else 'PASS'), f"상태 {answer.get('status')}, hri 출처 {leaked}"


@scenario('P4', 'permission', '타사 직원(mvx)이 hri 문서 ID로 요약 요청')
async def p4(client):
    call = client.service.summarize(await client.session('mvx_employee'), doc_id('HRI-SYN-MAN-001'))
    return await expect_error(call, 'ACCESS_DENIED', 'NOT_FOUND', 'DOCUMENT_NOT_FOUND', 'STATE_CHANGED')


@scenario('P5', 'permission', '타사 직원(mvx)이 hri 문서 ID로 상세 조회')
async def p5(client):
    call = client.service.detail(await client.session('mvx_employee'), doc_id('HRI-SYN-MAN-001'))
    return await expect_error(call, 'ACCESS_DENIED', 'NOT_FOUND', 'DOCUMENT_NOT_FOUND', 'STATE_CHANGED')


@scenario('P6', 'permission', '타사 직원(mvx)이 hri 문서를 질문 범위로 지정')
async def p6(client):
    try:
        answer = await client.service.ask(await client.session('mvx_employee'), '첫 검토 기한은?',
                                          document_ids=[doc_id('HRI-SYN-MAN-001')])
    except UIServiceError as error:
        return 'PASS', f'거부 {error.code}: {error}'
    leaked = hri_leaks(answer.get('citations') or [])
    return ('FAIL' if leaked else 'PASS'), f"상태 {answer.get('status')}, hri 출처 {leaked}"


@scenario('P7', 'permission', '직원(hri) 계정이 문서 업로드')
async def p7(client):
    sample = Path(CORPUS['source_root']) / DOCS['HRI-SYN-MAN-005']['path']
    call = client.service.upload(await client.session('hri_employee'), str(sample), company_id('hri'), 'manual')
    return await expect_error(call, 'ACCESS_DENIED', 'FORBIDDEN', 'PERMISSION_DENIED')


@scenario('P8', 'permission', '회사 관리자(hri)가 다른 회사(mvx)로 업로드')
async def p8(client):
    sample = Path(CORPUS['source_root']) / DOCS['HRI-SYN-MAN-005']['path']
    call = client.service.upload(await client.session('hri_admin'), str(sample), company_id('mvx'), 'manual')
    return await expect_error(call, 'ACCESS_DENIED', 'FORBIDDEN', 'PERMISSION_DENIED')


@scenario('P9', 'permission', '플랫폼 관리자 전체 회사 검색')
async def p9(client):
    results = await filtered(client, '신규 담당자 실습 교육자료')
    seen = companies_of(results)
    return ('PASS' if len(seen) >= 2 else 'FAIL'), f'{len(results)}건, 회사 {seen}'


@scenario('P10', 'permission', '로그아웃한 세션으로 검색')
async def p10(client):
    info = client.accounts['hri_employee']
    session = await client.service.login(info['username'], info['password'])
    await client.service.logout(session)
    return await expect_error(client.service.search(session, '경제지표'), 'SESSION_EXPIRED')


@scenario('P11', 'permission', '세션의 역할·회사를 위조한 hri 직원')
async def p11(client):
    forged = dict(await client.session('hri_employee'), company_id=company_id('mvx'),
                  allowed_company_ids=[company_id('mvx')], role='platform_admin')
    results = (await client.service.search(forged, 'WMS 입고 데이터 접수와 등록'))['results']
    leaked = [key_of(r['document_id']) for r in results if not key_of(r['document_id']).startswith('HRI')]
    return ('FAIL' if leaked else 'PASS'), f'{len(results)}건, hri 외 노출 {leaked}'


# ---------- #85 예외 상황 ----------

MOCK = '02_가상자료/확장자별_테스트용_목업/'
EXCEPTIONS = [
    ('E1', 'truncated.pdf', '중간이 잘린 PDF', {'CORRUPTED_FILE'}),
    ('E2', 'encrypted.pdf', '암호 PDF', {'ENCRYPTED_FILE'}),
    ('E3', MOCK + '[블루파트너스자산운용]_사내보안_및_자산운용_가이드(암호화).pptx', '암호 PPTX', {'ENCRYPTED_FILE'}),
    ('E4', MOCK + '[현대경제연구원]_거시경제_동향분석_정례브리핑(손상).pptx', '손상된 PPTX', {'CORRUPTED_FILE'}),
    ('E5', MOCK + '[현대경제연구원]_2026_글로벌_산업동향_보고서(스캔본).pdf', '글자 없는 스캔 PDF', {'EMPTY_DOCUMENT'}),
    ('E6', 'empty.docx', '0바이트 DOCX', {'CORRUPTED_FILE', 'EMPTY_FILE'}),
    ('E7', 'notes.txt', '지원하지 않는 형식(.txt)', {'UNSUPPORTED_FORMAT'}),
    ('E8', 'legacy.docx', '구형 .doc를 .docx로 바꾼 파일', {'LEGACY_FORMAT', 'UNSUPPORTED_FORMAT'}),
    ('E9', 'disguised.docx', 'PDF를 .docx로 바꾼 파일', {'UNSUPPORTED_FORMAT', 'CORRUPTED_FILE'}),
    ('E10', 'no_body.docx', '본문 XML이 없는 DOCX', {'CORRUPTED_FILE'}),
    ('E11', DOCS['HRI-SYN-MAN-001']['path'], '이미 올린 파일 다시 업로드', {'DUPLICATE_FILE'}),
]


def make_samples():
    """예외 점검용 파일을 data/qa_samples에 만든다. 원본 목업 파일은 바꾸지 않는다."""
    SAMPLES.mkdir(parents=True, exist_ok=True)
    pdf = Path(CORPUS['source_root']) / DOCS['MVX-PUB-026']['path']
    (SAMPLES / 'truncated.pdf').write_bytes(pdf.read_bytes()[:2048])
    writer = PdfWriter(clone_from=PdfReader(pdf))
    writer.encrypt('qa-secret')
    with open(SAMPLES / 'encrypted.pdf', 'wb') as handle:
        writer.write(handle)
    (SAMPLES / 'empty.docx').write_bytes(b'')
    (SAMPLES / 'notes.txt').write_text('지원하지 않는 형식', encoding='utf-8')
    (SAMPLES / 'legacy.docx').write_bytes(bytes.fromhex('D0CF11E0A1B11AE1') + bytes(4096))
    (SAMPLES / 'disguised.docx').write_bytes(pdf.read_bytes())
    with zipfile.ZipFile(SAMPLES / 'no_body.docx', 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')


def sample_path(name):
    return SAMPLES / name if '/' not in name else Path(CORPUS['source_root']) / name


# 실패한 업로드도 중복 검사 대상이므로, 같은 파일을 다시 올리면 기대한 오류 대신 DUPLICATE_FILE이 난다.
# 실행마다 파일 끝에 고유 표시를 붙인 복사본을 올려 해시를 바꾼다. 원본 목업 파일은 바꾸지 않는다.
RUN_MARK = datetime.now().strftime('%Y%m%d-%H%M%S')


def unique_sample(name):
    source = sample_path(name)
    data = source.read_bytes()
    if not data:
        return source  # 0바이트 파일은 비어 있어야 하므로 그대로 쓴다
    target = SAMPLES / 'runs' / RUN_MARK / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data + f'\n%qa-run {RUN_MARK}\n'.encode('ascii'))
    return target


def make_exception_scenario(sid, name, title, codes):
    @scenario(sid, 'exception', title)
    async def run(client):
        try:
            result = await client.service.upload(await client.session('hri_admin'), str(sample_path(name) if sid == 'E11' else unique_sample(name)),
                                                  company_id('hri'), 'manual')
        except UIServiceError as error:
            code, message = error.code, str(error)
        else:
            code = result.get('error_code') if result.get('status') == 'failed' else 'SUCCEEDED'
            message = result.get('message')
        return ('PASS' if code in codes else 'FAIL'), f'{code}: {message} (기대 {sorted(codes)})'
    return run


for _exception in EXCEPTIONS:
    make_exception_scenario(*_exception)


# ---------- #83 검색 정확도 ----------

def make_accuracy_scenario(question):
    @scenario('A-' + question['id'], 'accuracy', f"{question['id']} 문서 Hit@3 ({question['company']})")
    async def run(client):
        employee = COMPANY_ACCOUNTS[question['company']][1]
        results = (await client.service.search(await client.session(employee), question['query']))['results']
        ranked = [key_of(r['document_id']) for r in results]
        rank = next((i + 1 for i, key in enumerate(ranked) if key in question['gold']), None)
        verdict = 'PASS' if rank and rank <= 3 else 'FAIL'
        return verdict, f"정답 {question['gold'][0]} 순위 {rank or '없음'}, 상위 3 {ranked[:3]}"
    return run


for _question in CORPUS['accuracy_questions']:
    make_accuracy_scenario(_question)


# ---------- 실행·기록 ----------

async def main(categories):
    make_samples()
    client = Client()
    results = []
    try:
        for item in SCENARIOS:
            if categories and item['category'] not in categories:
                continue
            started = time.monotonic()
            try:
                verdict, note = await item['run'](client)
            except Exception as error:  # 시나리오 하나의 실패가 나머지 실행을 막지 않게 한다
                verdict, note = 'ERROR', f"{type(error).__name__} {getattr(error, 'code', '')}: {error}"
            results.append({'id': item['id'], 'category': item['category'], 'title': item['title'],
                            'verdict': verdict, 'note': note, 'seconds': round(time.monotonic() - started, 1)})
            print(f"{item['id']:7} {verdict:5} {note}"[:400], flush=True)
    finally:
        await client.close()
    out = HERE / 'results'
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    payload = {'run_at': stamp, 'categories': categories or 'all', 'results': results}
    (out / f'run-{stamp}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'saved results/run-{stamp}.json')


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1:]))
