"""ingest/loaders/base.py — 로더 공통 표 변환·출처 위치 표시.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-211 table_to_text — 표를 머리글 문맥이 유지되는 텍스트로 바꾼다.
    FUN-212 build_location_label — 페이지·섹션·슬라이드 출처 위치를 표시한다."""


def build_location_label(kind, section_no, title=None):
    """출처 표시용 위치 문구를 만든다. 예: 'p. 3' / '슬라이드 5' / '섹션 2 (제목)'"""
    if kind == "page":
        return f"p. {section_no}"
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

    column_count = 0
    for row in rows:
        if len(row) > column_count:
            column_count = len(row)

    lines = []
    for row in rows:
        cells = []
        for i in range(column_count):
            if i < len(row):
                cell = str(row[i])
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