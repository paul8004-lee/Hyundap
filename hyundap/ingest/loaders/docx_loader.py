"""ingest/loaders/docx_loader.py — DOCX 문단·표를 공통 섹션으로 변환.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-209 load_docx — DOCX 문단·표를 공통 문서로 읽는다."""


import zlib
from zipfile import BadZipFile

from docx import Document as DocxDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError

from .base import build_location_label, table_to_text
from hyundap.common.schemas import CommonDocument, Section
from ..models import CORRUPTED_FILE, IngestError

# 글자를 읽지 않는 그림·도형·차트·텍스트 상자(w:drawing, 구형 w:pict). mc:Fallback 안의 중복 표현은 뺀다.
UNREAD_OBJECTS_XPATH = ".//w:drawing | .//w:pict[not(ancestor::*[local-name()='Fallback'])]"


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


def iter_blocks(element, parent):
    """본문 요소를 순서대로 Paragraph·Table로 돌려준다. 콘텐츠 컨트롤(w:sdt) 안까지 들어간다.

    python-docx의 iter_inner_content는 w:sdt 안의 문단·표를 건너뛰어 본문이 말없이 사라진다."""
    for child in element.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)
        elif child.tag == qn("w:sdt"):
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                yield from iter_blocks(content, parent)


def cell_text(cell):
    """칸의 글자를 읽는다. 칸 안의 표(중첩 표)가 있으면 그 글자도 이어 붙인다."""
    parts = [cell.text]
    for inner in cell.tables:
        for row in inner.rows:
            parts.extend(c.text for c in row.cells)
    return " ".join(p for p in parts if p.strip())


def read_table_rows(table):
    """표를 행·열 글자 목록으로 읽는다. 병합된 칸은 첫 칸에만 값을 두고 나머지는 빈 칸으로 한다.

    (행·열 목록, 빈 칸으로 바꾼 병합 칸 수, 중첩 표가 든 칸 수)를 돌려준다."""
    rows = []
    seen = set()
    merged_count = 0
    nested_count = 0
    for row in table.rows:
        cells = []
        for cell in row.cells:
            if cell._tc in seen:
                cells.append("")
                merged_count += 1
            else:
                seen.add(cell._tc)
                cells.append(cell_text(cell))
                if cell.tables:
                    nested_count += 1
        rows.append(cells)
    return rows, merged_count, nested_count


def load_docx(file_path, upload_info):
    """DOCX 본문을 순서대로 읽어 제목 스타일마다 새 섹션으로 나눈다."""
    try:
        docx_file = DocxDocument(file_path)
    except (PackageNotFoundError, BadZipFile, KeyError, XMLSyntaxError, zlib.error, AttributeError):
        # AttributeError: [Content_Types].xml만 있고 본문이 없는 zip을 python-docx가 읽다가 내는 예외 (#198)
        raise IngestError(CORRUPTED_FILE)

    doc = CommonDocument(header=dict(upload_info))
    title = None
    lines = []
    table_no = 0
    style_names = {}

    for block in iter_blocks(docx_file.element.body, docx_file._body):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            # [병목 해결을 위해, 수업에서 다루지 않은 block._p.style 사용]
            # block.style.name은 스타일을 직접 지정하지 않은 문단마다 기본 스타일을 처음부터 다시 찾아서 느리다.
            # 그래서 스타일 번호(block._p.style)만 먼저 읽고, 처음 보는 번호일 때만 이름을 읽어 style_names에 저장해 둔다.
            style_id = block._p.style
            if style_id not in style_names:
                style_names[style_id] = block.style.name
            if style_names[style_id].startswith("Heading"):
                add_docx_section(doc, title, lines)
                title = text
                lines = []
            elif text != "":
                lines.append(text)
        elif isinstance(block, Table):
            table_no += 1
            rows, merged_count, nested_count = read_table_rows(block)
            if merged_count > 0:
                doc.warnings.append(f"표 {table_no}: 병합 셀 {merged_count}칸은 빈 칸으로 처리함")
            if nested_count > 0:
                doc.warnings.append(f"표 {table_no}: 중첩 표가 든 {nested_count}칸은 안쪽 글자를 한 칸에 이어 붙임")
            lines.extend(table_to_text(rows))

    add_docx_section(doc, title, lines)
    unread = len(docx_file.element.body.xpath(UNREAD_OBJECTS_XPATH))
    if unread > 0:
        doc.warnings.append(f"그림·도형·차트·텍스트 상자 {unread}개는 글자를 읽지 않음")
    return doc