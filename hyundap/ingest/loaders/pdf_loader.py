"""ingest/loaders/pdf_loader.py — PDF 페이지 텍스트를 공통 섹션으로 변환.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-208 load_pdf — PDF 페이지 텍스트를 공통 문서로 읽는다."""

import zlib

from langchain_community.document_loaders import PyPDFLoader
from pypdf.errors import PyPdfError

from . import figures
from .base import build_location_label
from .. import ocr
from hyundap.common.config import get_settings
from hyundap.common.schemas import CommonDocument, Section
from ..models import CORRUPTED_FILE, IngestError

# 깨진 PDF를 읽을 때 pypdf가 내는 예외들 (Ascii85/UTF-16 해석 실패, 미지원 필터, 압축 오류 포함).
# 이 목록은 load() 호출 한 줄에만 적용하므로 패키지 미설치(ImportError)·코드 실수는 가리지 않는다.
PDF_READ_ERRORS = (PyPdfError, ValueError, UnicodeDecodeError, NotImplementedError, zlib.error, KeyError, IndexError)

OCR_DPI = 200          # 쪽을 이미지로 바꿀 해상도
OCR_MAX_SIDE_PX = 4000  # 아주 큰 쪽(포스터 등)은 긴 변이 이 값을 넘지 않게 줄인다
# 문서 하나에서 OCR로 읽는 쪽 수 상한은 설정 OCR_MAX_PAGES(기본 50)다. 쪽당 수 초가 걸려 업로드가 길어지는 것을 막는다.

# 글자 레이어가 있는 쪽 안의 그림(그래프·표 이미지)은 그림 영역만 잘라 OCR한다. 실측(그래프 2쪽)에서
# 쪽 전체를 읽을 때보다 영역을 300dpi로 잘라 읽을 때 눈금·범례를 더 많이 읽었다.
FIGURE_DPI = 300
FIGURE_MAX_SIDE_PX = 8000   # 쪽을 그릴 때 긴 변 상한(아주 큰 쪽의 메모리 폭증 방지)


def render_page(pdf, index):
    """pypdfium2로 PDF의 index번째 쪽(0부터)을 BGR 배열 이미지로 그린다."""
    page = pdf[index]
    width, height = page.get_size()
    scale = min(OCR_DPI / 72, OCR_MAX_SIDE_PX / max(width, height))
    return page.render(scale=scale).to_numpy()


def figure_crops(pdf, index):
    """index번째 쪽(0부터) 안의 그림을 잘라 BGR 배열 목록으로 돌려준다.

    너무 작은 그림(로고)과 쪽 전체를 덮는 그림(스캔 배경·표지)은 뺀다."""
    import pypdfium2.raw as pdfium_raw

    page = pdf[index]
    width, height = page.get_size()
    boxes = []
    for obj in page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE]):
        left, bottom, right, top = obj.get_bounds()
        if figures.is_figure_size(right - left, top - bottom, width * height):
            boxes.append((left, bottom, right, top))
    if not boxes:
        return []
    scale = min(FIGURE_DPI / 72, FIGURE_MAX_SIDE_PX / max(width, height))
    image = page.render(scale=scale).to_numpy()
    return [image[max(int((height - top) * scale), 0):int((height - bottom) * scale),
                  max(int(left * scale), 0):int(right * scale)]
            for left, bottom, right, top in boxes]


def read_pages(file_path, upload_info, opened):
    """PDF를 페이지별로 읽어 페이지마다 섹션 1개를 가진 공통 문서 형식으로 바꾼다.

    PaddleOCR가 설치되어 있으면 OCR로 두 가지를 더 읽는다.
    - 글자 레이어가 없는 쪽(스캔 이미지): 쪽 전체를 읽는다.
    - 글자 레이어가 있는 쪽 안의 그림(그래프·표 이미지): 그림 영역만 읽어 본문 뒤에 붙인다."""
    try:
        pages = PyPDFLoader(file_path).load()
    except PDF_READ_ERRORS:
        raise IngestError(CORRUPTED_FILE)

    doc = CommonDocument(header=dict(upload_info))
    max_pages = get_settings().ocr_max_pages
    page_ocr = ocr.is_available() and max_pages > 0   # 글자 없는 쪽 전체 OCR
    reader = figures.FigureReader()                   # 쪽 안의 그림 OCR (OCR_MAX_FIGURES가 0이면 꺼짐)
    pdf = None        # OCR할 쪽이 처음 나올 때만 연다
    ocr_count = 0
    over_limit = []   # 쪽 수 상한 때문에 OCR하지 않은 쪽 번호
    figure_pages = 0  # 그림 속 글자를 본문에 붙인 쪽 수
    for page in pages:
        section_no = page.metadata["page"] + 1
        text = page.page_content
        if pdf is None and ((text.strip() == "" and page_ocr) or (text.strip() != "" and reader.enabled)):
            import pypdfium2
            pdf = pypdfium2.PdfDocument(file_path)
            opened.append(pdf)
        if text.strip() == "" and page_ocr:
            if ocr_count >= max_pages:
                over_limit.append(section_no)   # 경고는 문서 끝에서 한 줄로 모은다(쪽마다 남기면 보고서가 길어진다)
                continue
            ocr_count += 1
            text = ocr.ocr_image(render_page(pdf, section_no - 1))
            if text.strip() == "":
                doc.warnings.append(f"p.{section_no}: OCR로도 글자를 읽지 못해 건너뜀")
                continue
            doc.warnings.append(f"p.{section_no}: 글자가 없어 OCR로 읽음")
        elif text.strip() == "":
            doc.warnings.append(f"p.{section_no}: 글자가 없어 건너뜀")
            continue
        elif reader.enabled:
            try:
                crops = figure_crops(pdf, section_no - 1)
            except pypdfium2.PdfiumError:
                doc.warnings.append(f"p.{section_no}: 그림 속 글자 읽기를 건너뜀 (그림을 읽지 못함)")
                crops = []
            figure_text = "\n".join(line for line in (reader.read_array(crop, text) for crop in crops) if line)
            if figure_text != "":
                text = text.rstrip() + "\n\n" + figure_text
                figure_pages += 1
        doc.sections.append(
            Section(
                section_no=section_no,
                location_label=build_location_label("page", section_no),
                text=text,
            )
        )
    if len(over_limit) == 1:
        doc.warnings.append(f"p.{over_limit[0]}: 글자가 없어 건너뜀 (OCR은 문서당 {max_pages}쪽까지)")
    elif over_limit:
        doc.warnings.append(f"{len(over_limit)}쪽(p.{over_limit[0]}~p.{over_limit[-1]})은 글자가 없어 건너뜀 (OCR은 문서당 {max_pages}쪽까지)")
    doc.warnings.extend(reader.warnings(f"{figure_pages}쪽, "))
    return doc


def load_pdf(file_path, upload_info):
    """read_pages로 PDF를 읽고, 쪽을 그리려고 연 pypdfium2 문서는 끝나면 닫는다.
    닫지 않으면 Windows에서 파일이 잠겨 업로드 뒤 임시 파일을 지우거나 옮기지 못한다."""
    opened = []
    try:
        return read_pages(file_path, upload_info, opened)
    finally:
        for pdf in opened:
            pdf.close()
