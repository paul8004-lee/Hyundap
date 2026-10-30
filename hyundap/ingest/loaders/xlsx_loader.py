"""ingest/loaders/xlsx_loader.py — XLSX 시트의 값을 공통 섹션으로 변환.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    load_xlsx — XLSX 시트의 값을 표(마크다운)로 바꿔 공통 문서로 읽는다. 시트 1개 = 섹션 1개.

수식은 파일에 저장된 계산 결과만 읽는다(엑셀에서 저장한 파일은 결과가 들어 있다). 숨겨진 시트는 읽지 않는다.
그림·차트는 글자를 읽지 않고 개수만 경고에 남긴다."""

import datetime
import re
import zlib
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation
from functools import lru_cache
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from lxml.etree import XMLSyntaxError
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .base import build_location_label, table_to_text
from hyundap.common.schemas import CommonDocument, Section
from ..models import CORRUPTED_FILE, IngestError

# 읽는 양의 상한. 업로드가 한 요청 안에서 끝나야 하고, 청크가 많을수록 뒤의 임베딩 호출(64개씩)과 비용이 늘기 때문에 둔다.
# 행 수만 제한하면 열이 많은 시트가 문제다: 행 100,000 × 열 50(500만 칸)은 실측 159초·메모리 924MiB·청크 199,490개였다.
# 그래서 칸 수로 문서 전체를 제한한다(실측: 칸 1만 개 ≈ 청크 400개 ≈ 0.15초). 서식만 칠해진 빈 행도 읽는 양에 세므로 빈 행이 많아도 늘지 않는다.
MAX_ROWS_PER_SHEET = 20000
MAX_TOTAL_ROWS = 100000   # 문서 하나에서 읽는 행 수(시트 합계)
MAX_TOTAL_CELLS = 500000  # 문서 하나에서 읽는 칸 수(시트 합계, 행 수 × 열 수). 꽉 찬 파일 실측: 청크 19,898개·35초·메모리 301MiB
MAX_COLUMNS = 50          # 시트 하나에서 읽는 열 수. 이보다 넓은 시트는 왼쪽 열만 읽는다
MAX_TITLE_ROWS = 3        # 표 위의 제목 줄(칸이 하나뿐인 줄)을 몇 줄까지 제목으로 볼지
MAX_PREFIX_CHARS = 150    # 조각마다 붙는 '[시트] 이름 / 제목'의 길이 상한(표 조각 800자와 합쳐도 청크 크기 1000자 안)
MAX_LISTED_SHEETS = 10

# openpyxl이 깨진 파일을 읽다가 내는 예외들. 읽기 모드는 시트를 쓸 때 읽으므로 반복문의 읽는 줄에도 적용한다.
# 이 목록은 openpyxl을 부르는 줄에만 적용해 코드 실수는 가리지 않는다.
XLSX_READ_ERRORS = (BadZipFile, zlib.error, KeyError, ValueError, IndexError, TypeError, AttributeError,
                    InvalidFileException, XMLSyntaxError, ParseError)

CHART_PART = re.compile(r"^xl/charts/chart\d+\.xml$")


# 화면처럼 바꿀 수 있는 단순한 숫자 서식: [통화 기호] #,##0 또는 0 [.소수] [%] [단위 글자]. 예: 0% / 0.0% / #,##0 / "$"#,##0.00 / #,##0"원"
# 구역(;)이 있거나 [$₩-412]처럼 복잡한 서식은 해석하지 않고 원래 값을 쓴다(값이 틀리게 나오는 것보다 서식만 빠지는 편이 안전하다).
SIMPLE_NUMBER_FORMAT = re.compile(
    r'^(?:"(?P<prefix>[^"]*)"|(?P<symbol>[$₩¥€£]))?(?P<comma>#,##0|0)(?:\.(?P<decimals>0+))?(?P<percent>%)?(?:"(?P<suffix>[^"]*)")?$')


# 자릿수 계산용. 기본 문맥(28자리)이면 1e30 같은 큰 수를 소수점 자리에 맞추다가 InvalidOperation으로 업로드 전체가 실패한다.
FORMAT_CONTEXT = Context(prec=80)


@lru_cache(maxsize=256)
def parse_number_format(number_format):
    """숫자 서식 문자열을 (앞 글자, 쉼표 여부, 소수 자릿수, 퍼센트 여부, 뒤 글자)로 바꾼다. 단순하지 않은 서식이면 None."""
    match = SIMPLE_NUMBER_FORMAT.match(number_format or "")
    if match is None:
        return None
    prefix = match.group("prefix") or match.group("symbol") or ""
    decimals = len(match.group("decimals") or "")
    return prefix, match.group("comma") == "#,##0", decimals, match.group("percent") is not None, match.group("suffix") or ""


def format_number(value, number_format):
    """숫자 칸을 엑셀 화면처럼 쓴다(퍼센트, 천 단위 쉼표, 소수 자릿수, 통화 기호). 단순하지 않은 서식이면 None."""
    parsed = parse_number_format(number_format)
    if parsed is None:
        return None
    prefix, comma, decimals, percent, suffix = parsed
    number = Decimal(format(value, ".15g"))
    if percent:
        number *= 100
    try:
        number = number.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP, context=FORMAT_CONTEXT)   # 엑셀처럼 반올림은 0.5에서 올린다
    except InvalidOperation:   # 80자리를 넘는 수(1e300 등)는 서식을 적용하지 않고 원래 값을 쓴다
        return None
    sign = "-" if number < 0 else ""
    body = format(abs(number), f",.{decimals}f" if comma else f".{decimals}f")
    return f"{sign}{prefix}{body}{'%' if percent else ''}{suffix}"


def cell_to_text(value, number_format=None):
    """칸 값을 화면에서 보이는 모양에 가깝게 글자로 바꾼다. 빈 칸은 빈 글자.

    number_format은 숫자 칸의 서식이다. 단순한 서식만 반영하고, 날짜는 서식과 관계없이 ISO 형식으로 쓴다."""
    if value is None:
        return ""
    if isinstance(value, bool):   # bool은 int의 한 종류라 먼저 본다
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and number_format not in (None, "General"):
        shown = format_number(value, number_format)
        if shown is not None:
            return shown
    if isinstance(value, float):
        return format(value, ".15g")   # 0.30000000000000004 → 0.3, 1500000.0 → 1500000
    if isinstance(value, datetime.datetime):
        if value.time() == datetime.time(0):
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):   # [h]:mm 서식의 경과 시간. str()은 '1 day, 2:00:00'처럼 영어로 나온다
        seconds = int(value.total_seconds())
        hours, rest = divmod(abs(seconds), 3600)
        return f"{'-' if seconds < 0 else ''}{hours}:{rest // 60:02d}:{rest % 60:02d}"
    return str(value)


def filled_count(row):
    """줄에서 값이 있는 칸의 수."""
    return sum(1 for cell in row if cell != "")


def sheet_columns(sheet):
    """시트에서 읽을 열 수. 파일에 적힌 범위(dimension)가 없거나 터무니없이 커도 MAX_COLUMNS를 넘지 않는다."""
    return min(MAX_COLUMNS, sheet.max_column or MAX_COLUMNS)


def read_sheet_rows(sheet, limit):
    """시트의 값을 앞에서 limit+1줄까지 읽는다(마지막 한 줄은 더 있는지 확인용).

    칸은 글자로 바꿔서 줄 목록으로 돌려주고, MAX_COLUMNS 오른쪽에 값이 실제로 있었는지(wide)도 함께 돌려준다."""
    columns = sheet_columns(sheet)
    try:
        # 서식은 숫자 칸에서만 읽는다(날짜·글자 칸까지 읽으면 느려진다). 반복 중 깨진 서식 정보도 이 줄에서 잡힌다.
        # 열은 하나 더 읽어 그 칸에 값이 있는지로 '열이 잘렸는지'를 판단한다(파일에 적힌 범위는 서식만으로도 커진다).
        raw_rows = [[(cell.value, cell.number_format if isinstance(cell.value, (int, float)) else None) for cell in row]
                    for row in sheet.iter_rows(max_row=limit + 1, max_col=columns + 1)]
    except XLSX_READ_ERRORS:
        raise IngestError(CORRUPTED_FILE)
    wide = any(len(row) > columns and row[columns][0] is not None for row in raw_rows)
    rows = [[cell_to_text(value, number_format) for value, number_format in row[:columns]] for row in raw_rows]
    return rows, wide


def split_titles(rows):
    """표 위에 칸이 하나뿐인 줄(문서·표 제목)이 있으면 떼어 (제목 글자 목록, 나머지 줄)로 돌려준다.

    표가 한 칸짜리뿐이면 제목이 아니라 표 자체이므로 떼지 않는다."""
    if not any(filled_count(row) >= 2 for row in rows):
        return [], rows
    titles = []
    start = 0
    while start < len(rows) and len(titles) < MAX_TITLE_ROWS and filled_count(rows[start]) == 1:
        titles.append(next(cell for cell in rows[start] if cell != ""))
        start += 1
    return titles, rows[start:]


def build_prefix(sheet_name, titles):
    """표 조각마다 맨 위에 붙일 한 줄. 시트 이름이 달라도 머리글이 같은 시트(월별 시트 등)를 구분하게 해 준다."""
    prefix = f"[시트] {sheet_name}"
    if titles:
        prefix += " / " + " / ".join(titles)
    if len(prefix) > MAX_PREFIX_CHARS:
        prefix = prefix[:MAX_PREFIX_CHARS - 1] + "…"
    return prefix


def count_unread_objects(file_path):
    """글자를 읽지 않는 그림과 차트의 수를 압축 파일 목록에서 센다. 파일을 열지 못하면 (0, 0)."""
    try:
        with ZipFile(file_path) as archive:
            names = archive.namelist()
    except (BadZipFile, zlib.error, OSError):
        return 0, 0
    pictures = sum(1 for name in names if name.startswith("xl/media/"))
    charts = sum(1 for name in names if CHART_PART.match(name))
    return pictures, charts


def list_names(names):
    """경고에 적을 시트 이름 목록. 많으면 앞쪽만 쓰고 '외'를 붙인다."""
    listed = ", ".join(f"'{name}'" for name in names[:MAX_LISTED_SHEETS])
    return listed + (" 외" if len(names) > MAX_LISTED_SHEETS else "")


def load_xlsx(file_path, upload_info):
    """XLSX를 시트별로 읽어 시트마다 섹션 1개(표 조각들)로 만든다."""
    # 경로를 넘기면 openpyxl이 깨진 파일에서 예외를 낼 때 파일을 닫지 못한다(Windows에서는 올린 파일을 지울 수 없게 된다).
    # 그래서 파일을 직접 열어 두고, 읽기가 끝나면 어떤 경우에도 닫는다.
    with open(file_path, "rb") as handle:
        return read_workbook(handle, file_path, upload_info)


def read_workbook(handle, file_path, upload_info):
    """열어 둔 XLSX 파일에서 시트를 읽어 공통 문서로 만든다."""
    try:
        workbook = load_workbook(handle, read_only=True, data_only=True)
    except XLSX_READ_ERRORS:
        raise IngestError(CORRUPTED_FILE)

    doc = CommonDocument(header=dict(upload_info))
    hidden = []          # 숨겨진 시트 이름
    empty = []           # 값이 없는 시트 이름
    truncated = []       # (시트 이름, 읽은 행 수) 시트당 상한에 걸린 시트
    wide = []            # 열 상한에 걸려 오른쪽 값을 읽지 않은 시트 이름
    unread_sheets = []   # 문서당 상한 때문에 읽지 않은 시트 이름
    rows_left = MAX_TOTAL_ROWS
    cells_left = MAX_TOTAL_CELLS
    try:
        for number, sheet in enumerate(workbook.worksheets, start=1):
            if sheet.sheet_state != "visible":
                hidden.append(sheet.title)
                continue
            columns = sheet_columns(sheet)
            limit = min(MAX_ROWS_PER_SHEET, rows_left, cells_left // columns)
            if limit < 1:
                unread_sheets.append(sheet.title)
                continue
            rows, is_wide = read_sheet_rows(sheet, limit)
            more = len(rows) > limit and filled_count(rows[limit]) > 0   # 상한 바로 다음 줄에도 값이 있으면 잘린 것이다
            rows = rows[:limit]
            rows_left -= len(rows)
            cells_left -= len(rows) * columns
            rows = [row for row in rows if filled_count(row) > 0]   # 빈 줄은 건너뛴다
            if is_wide:
                wide.append(sheet.title)
            if len(rows) == 0:
                empty.append(sheet.title)
                continue
            if more:
                truncated.append((sheet.title, limit))
            width = max(max(i for i, cell in enumerate(row) if cell != "") for row in rows) + 1   # 오른쪽 끝의 빈 열은 뺀다
            rows = [row[:width] + [""] * (width - len(row[:width])) for row in rows]
            titles, table_rows = split_titles(rows)
            prefix = build_prefix(sheet.title, titles)
            pieces = [f"{prefix}\n{piece}" for piece in table_to_text(table_rows)]
            if len(pieces) == 0:   # 제목 줄만 있는 시트
                pieces = [prefix]
            doc.sections.append(
                Section(
                    section_no=number,
                    location_label=build_location_label("sheet", number, sheet.title),
                    text="\n\n".join(pieces),
                )
            )
    finally:
        workbook.close()

    if hidden:
        doc.warnings.append(f"숨겨진 시트 {len(hidden)}개는 읽지 않음 ({list_names(hidden)})")
    if empty:
        doc.warnings.append(f"값이 없는 시트 {len(empty)}개는 건너뜀 ({list_names(empty)})")
    for name, limit in truncated:
        doc.warnings.append(f"시트 '{name}': 앞쪽 {limit}행까지만 읽음 (상한: 시트당 {MAX_ROWS_PER_SHEET}행, 문서당 {MAX_TOTAL_ROWS}행·{MAX_TOTAL_CELLS}칸)")
    if unread_sheets:
        doc.warnings.append(f"문서당 읽는 양 상한(행 {MAX_TOTAL_ROWS}개·칸 {MAX_TOTAL_CELLS}개)에 걸려 시트 {len(unread_sheets)}개는 읽지 않음 ({list_names(unread_sheets)})")
    if wide:
        doc.warnings.append(f"{MAX_COLUMNS}열 오른쪽에 값이 있는 시트 {len(wide)}개는 왼쪽 {MAX_COLUMNS}열까지만 읽음 ({list_names(wide)})")
    pictures, charts = count_unread_objects(file_path)
    if pictures or charts:
        doc.warnings.append(f"그림 {pictures}개·차트 {charts}개는 글자를 읽지 않음")
    return doc
