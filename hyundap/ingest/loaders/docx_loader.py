"""ingest/loaders/docx_loader.py — DOCX 문단·표를 공통 섹션으로 변환.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-209 load_docx — DOCX 문단·표를 공통 문서로 읽는다."""


from zipfile import BadZipFile

from docx import Document as DocxDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError

from .base import build_location_label, table_to_text
from hyundap.common.schemas import CommonDocument, Section
from ..models import CORRUPTED_FILE, IngestError


def add_docx_section(doc, title, lines):
    """제목과 모아 둔 글로 구간 1개를 만들어 문서에 더한다."""
    if title is None and len(lines) == 0:
        return
    section_no = len(doc.sections) + 1
    texts = []
    if title is not None and title != "":
        texts.append(title)
    texts.extend(lines)
    doc.sections.append(
        Section(
            section_no=section_no,
            location_label=build_location_label("section", section_no, title),
            text="\n\n".join(texts),
        )
    )


def read_table_rows(table):
    """표를 행·열 글자 목록으로 읽는다. 병합된 칸은 첫 칸에만 값을 두고 나머지는 빈 칸으로 한다.

    (행·열 목록, 빈 칸으로 바꾼 병합 칸 수)를 돌려준다."""
    rows = []
    seen = set()
    merged_count = 0
    for row in table.rows:
        cells = []
        for cell in row.cells:
            if cell._tc in seen:
                cells.append("")
                merged_count += 1
            else:
                seen.add(cell._tc)
                cells.append(cell.text)
        rows.append(cells)
    return rows, merged_count


def load_docx(file_path, upload_info):
    """DOCX 본문을 순서대로 읽어 제목 스타일마다 새 섹션으로 나눈다."""
    try:
        docx_file = DocxDocument(file_path)
    except (PackageNotFoundError, BadZipFile, KeyError, XMLSyntaxError):
        raise IngestError(CORRUPTED_FILE)

    doc = CommonDocument(header=dict(upload_info))
    title = None
    lines = []
    table_no = 0

    for block in docx_file.iter_inner_content():
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if block.style.name.startswith("Heading"):
                add_docx_section(doc, title, lines)
                title = text
                lines = []
            elif text != "":
                lines.append(text)
        elif isinstance(block, Table):
            table_no += 1
            rows, merged_count = read_table_rows(block)
            if merged_count > 0:
                doc.warnings.append(f"표 {table_no}: 병합 셀 {merged_count}칸은 빈 칸으로 처리함")
            lines.extend(table_to_text(rows))

    add_docx_section(doc, title, lines)
    return doc