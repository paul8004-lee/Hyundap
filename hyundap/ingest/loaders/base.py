"""ingest/loaders/base.py — 로더 공통 표 변환·출처 위치 표시.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-211 table_to_text — 표를 머리글 문맥이 유지되는 텍스트로 바꾼다.
    FUN-212 build_location_label — 페이지·섹션·슬라이드 출처 위치를 표시한다."""


def build_location_label(kind, section_no, title=None):
    """출처 표시용 위치 문구를 만든다. 예: 'p.3' / '슬라이드 5' / '섹션 2 (제목)' / '시트 2 (시트 이름)'"""
    if kind == "page":
        return f"p.{section_no}"
    if kind == "slide":
        return f"슬라이드 {section_no}"
    if kind == "sheet":
        if title:
            return f"시트 {section_no} ({title})"
        return f"시트 {section_no}"
    if kind == "section":
        if title:
            return f"섹션 {section_no} ({title})"
        return f"섹션 {section_no}"
    raise ValueError(f"알 수 없는 kind: {kind}")


def clean_cell(value):
    """칸 값을 한 줄 글자로 정리한다. 이스케이프는 하지 않는다."""
    return ("" if value is None else str(value)).replace("\n", " ").strip()


def escape_cell(text):
    """마크다운 표에서 열 구분으로 읽히지 않도록 `|`를 `\\|`로 바꾼다."""
    return text.replace("|", "\\|")


def row_line(cells):
    """정리된 칸 원문 목록을 마크다운 표의 한 줄로 만든다."""
    return "| " + " | ".join(escape_cell(cell) for cell in cells) + " |"


def split_by_escaped_width(text, width):
    """칸 원문을 나눈다. 조각마다 이스케이프한 길이가 width를 넘지 않고 `\\|`는 갈라지지 않는다."""
    parts = []
    current = []
    size = 0
    for char in text:
        step = 2 if char == "|" else 1   # `|`는 이스케이프하면 2글자가 된다
        if current and size + step > width:
            parts.append("".join(current))
            current = []
            size = 0
        current.append(char)
        size += step
    parts.append("".join(current))
    return parts


def table_to_text(rows, max_chars=800):
    """표(행·열 목록)를 마크다운 표 조각 목록으로 바꾼다."""
    if len(rows) == 0:
        return []
    if not any(str(cell or "").strip() for row in rows for cell in row):
        return []   # 모든 칸이 비어 있는 표는 `| | |` 같은 쓰레기 조각만 만들므로 건너뛴다

    column_count = 0
    for row in rows:
        if len(row) > column_count:
            column_count = len(row)

    # 칸 원문(이스케이프 전)을 먼저 정리해 두고, 행으로 만들 때마다 이스케이프한다.
    # 긴 칸을 나눌 때 이미 이스케이프된 글자를 자르면 `\|`가 갈라져 열이 늘어난다.
    raw_rows = []
    for row in rows:
        raw_rows.append([clean_cell(row[i]) if i < len(row) else "" for i in range(column_count)])
    lines = [row_line(cells) for cells in raw_rows]

    header = lines[0]
    separator = "| " + " | ".join(["---"] * column_count) + " |"
    body = lines[1:]

    if len(body) == 0:
        return [header + "\n" + separator]

    # 한 행이 혼자서도 max_chars를 넘으면 가장 긴 칸을 나눠 여러 행으로 만든다 (다른 칸은 반복해 문맥 유지)
    fixed = len(header) + len(separator) + 2
    expanded = []
    for cells, line in zip(raw_rows[1:], body):
        if fixed + len(line) <= max_chars:
            expanded.append(line)
            continue
        longest = max(range(column_count), key=lambda i: len(escape_cell(cells[i])))
        room = max(50, max_chars - fixed - (len(line) - len(escape_cell(cells[longest]))))
        for text in split_by_escaped_width(cells[longest], room):
            part = cells[:]
            part[longest] = text
            expanded.append(row_line(part))
    body = expanded

    pieces = []
    current = []
    for line in body:
        candidate = "\n".join([header, separator] + current + [line])
        if len(current) > 0 and len(candidate) > max_chars:
            pieces.append("\n".join([header, separator] + current))
            current = [line]
        else:
            current.append(line)
    pieces.append("\n".join([header, separator] + current))
    return pieces