"""ingest/loaders/pptx_loader.py — PPTX 슬라이드 텍스트·표를 공통 섹션으로 변환.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

구현 예정 함수:
    FUN-210 load_pptx — PPTX 슬라이드 텍스트·표를 공통 문서로 읽는다."""


from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from .base import build_location_label, table_to_text
from hyundap.common.schemas import CommonDocument, Section
from ..models import CORRUPTED_FILE, IngestError


def collect_shape_texts(shapes):
    """도형들을 위→아래, 왼→오른쪽 순서로 훑어 글자와 표 조각을 모은다."""
    texts = []
    ordered = sorted(shapes, key=lambda shape: (shape.top or 0, shape.left or 0))
    for shape in ordered:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            texts.extend(collect_shape_texts(shape.shapes))
        elif shape.has_table:
            rows = []
            for row in shape.table.rows:
                cells = []
                for cell in row.cells:
                    cells.append(cell.text)
                rows.append(cells)
            texts.extend(table_to_text(rows))
        elif shape.has_text_frame:
            text = shape.text_frame.text.strip()
            if text != "":
                texts.append(text)
    return texts


def load_pptx(file_path, upload_info):
    """PPTX 슬라이드를 차례로 읽어 슬라이드마다 섹션 1개로 만든다."""
    try:
        presentation = Presentation(file_path)
    except Exception:
        raise IngestError(CORRUPTED_FILE)

    doc = CommonDocument(header=dict(upload_info))
    for number, slide in enumerate(presentation.slides, start=1):
        texts = collect_shape_texts(slide.shapes)
        if len(texts) == 0:
            doc.warnings.append(f"슬라이드 {number}: 글자가 없어 건너뜀")
            continue
        doc.sections.append(
            Section(
                section_no=number,
                location_label=build_location_label("slide", number),
                text="\n\n".join(texts),
            )
        )
    return doc