"""ingest/loaders/charts.py — Office 차트 개체(DOCX·PPTX)의 제목·항목·값을 글로 읽는다.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    chart_to_text — 차트 XML 하나를 제목 머리글이 붙은 표 조각 목록으로 바꾼다.
자료형:
    ChartReader — 문서 하나의 차트 읽기 수·잘림·실패 수를 세며 차트를 한 개씩 읽는다.

Office 차트는 그림이 아니라 차트 XML(제목·항목·계열 이름·값의 캐시)로 저장되므로 OCR 없이 정확한 값을 읽을 수 있다.
읽은 내용은 table_to_text로 마크다운 표 조각이 되고, 조각마다 차트 제목 머리글을 붙여 어느 차트의 값인지 남긴다.
차트 그림이 아니라 이미지로 붙여 넣은 그래프는 figures.py(OCR)가 읽는다."""

from lxml import etree

from .base import table_to_text

# 상한은 표가 청크로 나뉘어도 검색 품질이 유지되는 범위로 정했다. 항목 60개는 월별 5년치, 계열 12개는 범례를 읽을 수 있는
# 한계다. 일별 시계열처럼 더 긴 차트는 앞부분만 넣고 잘렸다는 사실을 표 끝과 문서 경고에 남긴다. 표는 800자 조각으로 나뉘므로
# 60행×12열이어도 조각 몇 개로 끝난다.
MAX_CHART_ROWS = 60
MAX_CHART_SERIES = 12
MAX_CHARTS = 200   # 문서 하나에서 읽는 차트 수 상한

C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS = {"c": C_NS, "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}

# 차트 종류 요소 이름 → 표시 이름. 이름이 Chart로 끝나는 요소가 계열 묶음이다(bar3DChart·ofPieChart 포함).
KIND_LABELS = (
    ("bar", "막대"), ("line", "꺾은선"), ("pie", "원형"), ("ofPie", "원형"), ("doughnut", "도넛"),
    ("area", "영역"), ("scatter", "분산형"), ("radar", "방사형"), ("bubble", "거품형"),
    ("stock", "주식형"), ("surface", "표면"),
)
XY_KINDS = ("scatter", "bubble")
AXIS_KINDS = ("catAx", "dateAx", "valAx", "serAx")


class UnreadableChart(Exception):
    """차트 XML을 읽지 못함(손상, 차트가 아닌 형식 등)."""


class ChartText:
    """차트 하나에서 읽은 결과. pieces는 표 조각 목록, truncated는 상한 때문에 잘렸는지다."""

    def __init__(self, pieces, truncated):
        self.pieces = pieces
        self.truncated = truncated


def to_index(value):
    """c:pt의 idx 같은 정수 글자를 읽는다. 없거나 정수가 아니거나 음수면 None(그 점은 건너뛴다)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def child_elements(parent):
    """XML 주석·처리 명령을 빼고 요소 자식만 돌려준다(주석의 tag는 문자열이 아니라 QName이 실패한다)."""
    return [child for child in parent if isinstance(child.tag, str)]


def parse_chart(source):
    """차트 XML(바이트)을 파싱해 chartSpace 요소를 돌려준다. 외부 개체는 읽지 않는다."""
    try:
        root = etree.fromstring(source, etree.XMLParser(resolve_entities=False, no_network=True))
    except (etree.XMLSyntaxError, ValueError):
        raise UnreadableChart("XML을 읽지 못함")
    if root.tag != f"{{{C_NS}}}chartSpace":
        raise UnreadableChart("차트 XML이 아님")
    return root


def kind_of(group):
    """계열 묶음 요소의 종류 이름(예: 'bar')과 표시 이름을 돌려준다."""
    name = etree.QName(group).localname[:-len("Chart")]
    name = name[:-2] if name.endswith("3D") else name
    for prefix, label in KIND_LABELS:
        if name == prefix:
            return name, label
    return name, "차트"


def read_points(ref):
    """c:cat·c:val·c:tx 같은 요소 안의 캐시를 ({위치: 글자}, 위치 목록, 서식 코드)로 읽는다. 없으면 빈 값.

    위치 목록은 실제로 점이 있는 위치(작은 것부터)다. ptCount나 idx 같은 파일 안의 숫자만큼 반복하지 않으려고
    선언된 개수는 쓰지 않는다. idx가 없거나 정수가 아닌 점은 건너뛴다.
    다단계 항목(c:multiLvlStrCache)은 바깥 단계부터 이어 붙인 글자로 만들며, 글자는 앞쪽 MAX_CHART_ROWS개 위치만 만든다."""
    if ref is None:
        return {}, [], ""
    multi = ref.find(".//c:multiLvlStrCache", NS)
    if multi is not None:
        levels = []   # 첫 단계가 가장 안쪽이다
        for lvl in multi.findall("c:lvl", NS):
            level = {}
            for pt in lvl.findall("c:pt", NS):
                index = to_index(pt.get("idx"))
                if index is not None:
                    level[index] = pt.findtext("c:v", default="", namespaces=NS)
            levels.append(level)
        indices = sorted(set().union(*levels)) if levels else []
        points = {}
        for index in indices[:MAX_CHART_ROWS]:
            parts = []
            for level in reversed(levels):
                earlier = [i for i in level if i <= index]   # 바깥 단계는 묶음의 첫 위치에만 값이 있다
                if earlier:
                    parts.append(level[max(earlier)])
            points[index] = " ".join(parts)
        return points, indices, ""
    cache = ref.find(".//c:strCache", NS)
    if cache is None:
        cache = ref.find(".//c:numCache", NS)
    if cache is None:
        return {}, [], ""
    points = {}
    for pt in cache.findall("c:pt", NS):
        index = to_index(pt.get("idx"))
        if index is not None:
            points[index] = pt.findtext("c:v", default="", namespaces=NS)
    return points, sorted(points), cache.findtext("c:formatCode", default="", namespaces=NS)


def format_value(value, number_format):
    """차트 캐시의 값 글자를 읽기 좋게 바꾼다. 부동소수점 잡음(0.30000000000000004)을 없애고 %는 퍼센트로 쓴다."""
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except ValueError:
        return value
    if number != number or number in (float("inf"), float("-inf")):
        return value
    if "%" in number_format:
        return f"{number * 100:.10g}%"
    return str(int(number)) if number.is_integer() else f"{number:.10g}"


def series_name(ser, order):
    """계열 이름. 없으면 '계열 N'."""
    name = ser.xpath("string(c:tx//c:v)", namespaces=NS).strip()
    return name if name else f"계열 {order}"


def category_rows(series):
    """항목-값 차트(막대·꺾은선·원형 등)의 (행 목록, 전체 행 수, 전체 계열 수)를 만든다. 상한을 넘으면 잘라 낸다.

    행은 항목이나 값이 실제로 있는 위치만 만든다(선언된 개수가 아니라 점 개수를 센다)."""
    categories, category_indices = {}, []
    for ser in series:
        points, indices, _ = read_points(ser.find("c:cat", NS))
        if indices:
            categories, category_indices = points, indices
            break
    columns = []
    positions = set(category_indices)
    for order, ser in enumerate(series, start=1):
        points, indices, number_format = read_points(ser.find("c:val", NS))
        columns.append((series_name(ser, order), points, number_format))
        positions.update(indices)
    ordered = sorted(positions)
    kept = columns[:MAX_CHART_SERIES]
    rows = [["구분"] + [name for name, _, _ in kept]]
    for index in ordered[:MAX_CHART_ROWS]:
        label = categories.get(index, str(index + 1))
        rows.append([label] + [format_value(points.get(index), number_format) for _, points, number_format in kept])
    return rows, len(ordered), len(columns)


def xy_rows(series):
    """분산형·거품형 차트의 (행 목록, 전체 점 수, 전체 계열 수)를 만든다. 점 수가 상한을 넘으면 잘라 낸다."""
    has_size = any(ser.find("c:bubbleSize", NS) is not None for ser in series)
    rows = [["계열", "X", "Y"] + (["크기"] if has_size else [])]
    total = 0
    for order, ser in enumerate(series[:MAX_CHART_SERIES], start=1):
        xs, x_indices, x_format = read_points(ser.find("c:xVal", NS))
        ys, y_indices, y_format = read_points(ser.find("c:yVal", NS))
        sizes, _, _ = read_points(ser.find("c:bubbleSize", NS))
        name = series_name(ser, order)
        indices = sorted(set(x_indices) | set(y_indices))
        total += len(indices)
        for index in indices[:max(MAX_CHART_ROWS + 1 - len(rows), 0)]:
            row = [name, format_value(xs.get(index), x_format), format_value(ys.get(index), y_format)]
            rows.append(row + ([format_value(sizes.get(index), "")] if has_size else []))
    return rows, total, len(series)


def chart_to_text(source):
    """차트 XML(바이트) 하나를 ChartText로 바꾼다. 읽을 값이 없으면 pieces가 빈 목록이다. 읽지 못하면 UnreadableChart.

    조각마다 '[차트] 제목 (종류)' 머리글이 붙는다. 항목·계열이 상한을 넘으면 앞부분만 넣고 표 끝에 잘렸다고 적는다."""
    root = parse_chart(source)
    chart = root.find("c:chart", NS)
    plot = chart.find("c:plotArea", NS) if chart is not None else None
    if plot is None:
        raise UnreadableChart("차트 본문이 없음")

    kinds, category_series, xy_series = [], [], []
    for group in child_elements(plot):
        if not etree.QName(group).localname.endswith("Chart"):
            continue
        kind, label = kind_of(group)
        if label not in kinds:
            kinds.append(label)
        (xy_series if kind in XY_KINDS else category_series).extend(group.findall("c:ser", NS))

    title = " ".join(text.strip() for text in chart.xpath("c:title//a:t/text()", namespaces=NS)).strip()
    if not title:
        title = chart.xpath("string(c:title/c:tx/c:strRef/c:strCache/c:pt/c:v)", namespaces=NS).strip()
    axis_titles = []
    for axis in child_elements(plot):
        if etree.QName(axis).localname in AXIS_KINDS:
            text = " ".join(t.strip() for t in axis.xpath("c:title//a:t/text()", namespaces=NS)).strip()
            if text and text not in axis_titles:
                axis_titles.append(text)
    header = "[차트]" + (f" {title}" if title else "") + f" ({'+'.join(kinds) if kinds else '차트'})"
    if axis_titles:
        header += f"\n(축: {', '.join(axis_titles)})"

    pieces, truncated = [], False
    for series, build in ((category_series, category_rows), (xy_series, xy_rows)):
        if not series:
            continue
        rows, total_rows, total_series = build(series)
        notes = []
        if total_rows > MAX_CHART_ROWS:
            notes.append(f"항목 {total_rows}개 중 앞 {MAX_CHART_ROWS}개만 넣음")
        if total_series > MAX_CHART_SERIES:
            notes.append(f"계열 {total_series}개 중 앞 {MAX_CHART_SERIES}개만 넣음")
        truncated = truncated or bool(notes)
        table = table_to_text(rows if len(rows) > 1 else [])   # 값이 하나도 없는 차트는 머리글만 남기지 않는다
        for position, piece in enumerate(table):
            text = f"{header}\n{piece}"
            if notes and position == len(table) - 1:
                text += "\n※ " + ", ".join(notes)
            pieces.append(text)
    return ChartText(pieces, truncated)


class ChartReader:
    """문서 하나의 차트 읽기를 맡는다. 읽은 수·잘림·실패 수를 세고 문서당 상한(MAX_CHARTS)을 지킨다."""

    def __init__(self):
        self.count = 0        # 읽기를 시도한 차트 수(상한 대상)
        self.appended = 0     # 값을 읽어 본문에 넣은 차트 수
        self.truncated = 0    # 항목·계열이 상한을 넘어 잘린 차트 수
        self.unreadable = 0   # 읽지 못해 건너뛴 차트 수
        self.capped = False   # 상한 때문에 읽지 않은 차트가 있는지

    def read(self, source):
        """차트 XML(바이트) 하나에서 읽은 표 조각 목록을 돌려준다. 읽지 못하거나 상한을 넘으면 빈 목록."""
        if self.count >= MAX_CHARTS:
            self.capped = True
            return []
        self.count += 1
        try:
            result = chart_to_text(source)
        except Exception:   # 한 차트를 읽지 못해도 업로드 전체가 실패하지 않게 건너뛴 차트로 센다
            self.unreadable += 1
            return []
        if result.pieces:
            self.appended += 1
        if result.truncated:
            self.truncated += 1
        return result.pieces

    def warnings(self):
        """문서 경고에 남길 문구 목록."""
        messages = []
        if self.appended:
            messages.append(f"차트 {self.appended}개의 제목·항목·값을 읽어 본문에 넣음")
        if self.truncated:
            messages.append(f"차트 {self.truncated}개는 항목 {MAX_CHART_ROWS}개·계열 {MAX_CHART_SERIES}개를 넘는 부분을 잘라 냄")
        if self.unreadable:
            messages.append(f"읽을 수 없는 차트 {self.unreadable}개는 건너뜀")
        if self.capped:
            messages.append(f"차트는 문서당 {MAX_CHARTS}개까지라 나머지 차트는 읽지 않음")
        return messages
