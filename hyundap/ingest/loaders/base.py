"""ingest/loaders/base.py — 로더 공통 표 변환·출처 위치 표시.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-211 table_to_text — 표를 머리글 문맥이 유지되는 텍스트로 바꾼다.
    FUN-212 build_location_label — 페이지·섹션·슬라이드 출처 위치를 표시한다."""


def build_location_label(kind, section_no, title=None):
    """출처 표시용 위치 문구를 만든다. 예: 'p.3' / '슬라이드 5' / '섹션 2 (제목)'"""
    if kind == "page":
        return f"p.{section_no}"
    if kind == "slide":
        return f"슬라이드 {section_no}"
    if kind == "section":
        if title:
            return f"섹션 {section_no} ({title})"
        return f"섹션 {section_no}"
    raise ValueError(f"알 수 없는 kind: {kind}")


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

    lines = []
    for row in rows:
        cells = []
        for i in range(column_count):
            if i < len(row):
                cell = "" if row[i] is None else str(row[i])
            else:
                cell = ""
            cell = cell.replace("|", "\\|").replace("\n", " ")
            cells.append(cell.strip())
        lines.append("| " + " | ".join(cells) + " |")

    header = lines[0]
    separator = "| " + " | ".join(["---"] * column_count) + " |"
    body = lines[1:]

    if len(body) == 0:
        return [header + "\n" + separator]

    # 한 행이 혼자서도 max_chars를 넘으면 가장 긴 칸을 나눠 여러 행으로 만든다 (다른 칸은 반복해 문맥 유지)
    fixed = len(header) + len(separator) + 2
    expanded = []
    for row, line in zip(rows[1:], body):
        if fixed + len(line) <= max_chars:
            expanded.append(line)
            continue
        cells = [("" if c is None else str(c)).replace("|", "\\|").replace("\n", " ").strip() for c in row]
        cells += [""] * (column_count - len(cells))
        longest = max(range(column_count), key=lambda i: len(cells[i]))
        room = max(50, max_chars - fixed - (len(line) - len(cells[longest])))
        text = cells[longest]
        for start in range(0, len(text), room):
            part = cells[:]
            part[longest] = text[start:start + room]
            expanded.append("| " + " | ".join(part) + " |")
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