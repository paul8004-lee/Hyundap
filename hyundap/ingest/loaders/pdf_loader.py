"""ingest/loaders/pdf_loader.py — PDF 페이지 텍스트를 공통 섹션으로 변환.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    FUN-208 load_pdf — PDF 페이지 텍스트를 공통 문서로 읽는다.
        top_lines — 쪽 맨 위에서 글자가 있는 줄 몇 개를 고른다.
        is_page_number — 줄이 쪽 번호(숫자·로마 숫자)인지 확인한다.
        find_header_lines — 여러 쪽 맨 위에 반복되는 줄(머리말)을 찾는다.
        remove_page_header — 한 쪽에서 머리말과 쪽 번호 줄을 뺀다.
        can_join_lines — 앞 줄과 뒤 줄이 한 단어가 끊긴 것인지 확인한다.
        join_split_words — 단어 중간에서 끊긴 줄을 이어 붙인다."""

import logging
import zlib
from importlib.util import find_spec

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

logger = logging.getLogger(__name__)

OCR_DPI = 200          # 쪽을 이미지로 바꿀 해상도
OCR_MAX_SIDE_PX = 4000  # 아주 큰 쪽(포스터 등)은 긴 변이 이 값을 넘지 않게 줄인다
# 문서 하나에서 OCR로 읽는 쪽 수 상한은 설정 OCR_MAX_PAGES(기본 50)다. 쪽당 수 초가 걸려 업로드가 길어지는 것을 막는다.

# 글자 레이어가 있는 쪽 안의 그림(그래프·표 이미지)은 그림 영역만 잘라 OCR한다. 실측(그래프 2쪽)에서
# 쪽 전체를 읽을 때보다 영역을 300dpi로 잘라 읽을 때 눈금·범례를 더 많이 읽었다.
FIGURE_DPI = 300
FIGURE_MAX_SIDE_PX = 8000   # 쪽을 그릴 때 긴 변 상한(아주 큰 쪽의 메모리 폭증 방지)

# 쪽 맨 위에서 머리말을 찾을 줄 수
HEADER_LINE_COUNT = 3
# 이 쪽 수 이상의 맨 위에 같은 줄이 있으면 머리말로 본다
MIN_HEADER_PAGES = 3
# 줄 길이가 그 쪽에서 가장 긴 줄의 이 비율 이상일 때만 끊긴 단어를 이어 붙인다 (짧은 제목 줄이 붙는 것을 막는다)
JOIN_MIN_RATIO = 0.3
# 머리말 옆에 있으면 같이 지우는 로마 숫자 쪽 번호
ROMAN_PAGE_NUMBERS = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"]


def top_lines(text):
    """쪽 맨 위에서 글자가 있는 줄을 HEADER_LINE_COUNT개까지 (줄 번호, 줄 내용) 목록으로 돌려준다."""
    result = []
    for number, line in enumerate(text.split("\n")):
        if line.strip() == "":
            continue
        result.append((number, line.strip()))
        if len(result) == HEADER_LINE_COUNT:
            break
    return result


def is_page_number(line):
    """숫자만 있거나 로마 숫자(i, ii, iii …)만 있는 줄이면 True."""
    return line.isdigit() or line.lower() in ROMAN_PAGE_NUMBERS


def find_header_lines(page_texts):
    """쪽별 글자 목록에서 MIN_HEADER_PAGES쪽 이상의 맨 위에 반복되는 줄을 찾아 목록으로 돌려준다.

    본문 중간에 반복되는 줄(예: 그래프 출처)은 맨 위가 아니므로 찾지 않는다."""
    counts = {}
    for text in page_texts:
        found = []
        for number, line in top_lines(text):
            if len(line) >= 3 and not is_page_number(line) and line not in found:
                found.append(line)
        for line in found:
            counts[line] = counts.get(line, 0) + 1

    headers = []
    for line, count in counts.items():
        if count >= MIN_HEADER_PAGES:
            headers.append(line)
    return headers


def remove_page_header(text, headers):
    """한 쪽의 맨 위 줄 중 머리말이 있으면, 머리말 줄과 쪽 번호 줄을 함께 뺀 글자를 돌려준다.

    머리말이 없는 쪽은 그대로 돌려준다. 그래서 쪽 번호만 있는 줄은 지우지 않는다."""
    top = top_lines(text)
    has_header = False
    for number, line in top:
        if line in headers:
            has_header = True
    if not has_header:
        return text

    remove_numbers = []
    for number, line in top:
        if line in headers or is_page_number(line):
            remove_numbers.append(number)

    kept = []
    for number, line in enumerate(text.split("\n")):
        if number not in remove_numbers:
            kept.append(line)
    return "\n".join(kept)


def is_hangul(char):
    """완성형 한글 글자이면 True."""
    return "가" <= char <= "힣"


def can_join_lines(before, after, longest):
    """앞 줄 끝과 뒤 줄 시작이 모두 한글이고 앞 줄이 충분히 길면 True.

    줄 끝에 공백이 있으면(단어 사이에서 줄이 바뀐 경우) 끝 글자가 한글이 아니라서 붙이지 않는다."""
    if before == "" or after == "":
        return False
    if not is_hangul(before[-1]) or not is_hangul(after[0]):
        return False
    return len(before) >= longest * JOIN_MIN_RATIO


def join_split_words(text):
    """줄 끝에서 단어가 끊긴 곳(예: '수' + '준에')의 줄바꿈을 지워 이어 붙인다.

    정규화 단계가 줄 끝 공백을 지우기 전의 원문에서 해야 단어 사이 줄바꿈과 구분할 수 있다."""
    lines = text.split("\n")
    longest = 0
    for line in lines:
        if len(line) > longest:
            longest = len(line)

    result = []
    for number, line in enumerate(lines):
        if number > 0 and can_join_lines(lines[number - 1], line, longest):
            result[-1] = result[-1] + line
        else:
            result.append(line)
    return "\n".join(result)


def render_page(pdf, index):
    """pypdfium2로 PDF의 index번째 쪽(0부터)을 BGR 배열 이미지로 그린다."""
    page = pdf[index]
    width, height = page.get_size()
    scale = min(OCR_DPI / 72, OCR_MAX_SIDE_PX / max(width, height))
    return page.render(scale=scale).to_numpy()


def figure_boxes(page):
    """쪽 안의 읽을 만한 그림 영역을 (왼, 아래, 오른, 위) 목록과 쪽 크기(너비, 높이)로 돌려준다. 쪽을 그리지는 않는다.

    너무 작은 그림(로고)과 쪽 전체를 덮는 그림(스캔 배경·표지)은 뺀다. 쪽 밖에 놓여 잘라 낼 영역이 없는 그림도 뺀다."""
    import pypdfium2.raw as pdfium_raw

    page.set_rotation(0)   # 그림 위치(obj.get_bounds)는 회전 전 좌표라서 쪽 회전(/Rotate)을 풀어야 영역이 맞는다. 메모리 속 쪽만 바꾼다.
    width, height = page.get_size()
    origin_left, origin_bottom = page.get_cropbox()[:2]   # 보이는 영역(CropBox)이 좌표 원점에서 떨어져 있을 수 있다
    boxes = []
    for obj in page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE]):
        left, bottom, right, top = obj.get_bounds()
        left, right, bottom, top = left - origin_left, right - origin_left, bottom - origin_bottom, top - origin_bottom
        if figures.is_figure_size(right - left, top - bottom, width * height):
            boxes.append((left, bottom, right, top))
    return boxes, width, height


def count_figures(pdf, index):
    """index번째 쪽(0부터) 안의 읽을 만한 그림 수. OCR이 꺼져 있어도 읽지 못한 그림을 세려고 쓴다(쪽을 그리지 않아 빠르다)."""
    return len(figure_boxes(pdf[index])[0])


def figure_crops(pdf, index, reader=None):
    """index번째 쪽(0부터) 안의 그림을 잘라 BGR 배열 목록으로 돌려준다.

    reader가 이미 상한만큼 읽었으면 쪽을 그리지 않고(300dpi 렌더링은 느리다) 읽지 않은 그림이 있다고만 표시한다."""
    page = pdf[index]
    boxes, width, height = figure_boxes(page)
    if not boxes:
        return []
    if reader is not None and reader.exhausted:
        reader.capped = True
        reader.unread += len(boxes)
        return []
    scale = min(FIGURE_DPI / 72, FIGURE_MAX_SIDE_PX / max(width, height))
    image = page.render(scale=scale).to_numpy()
    crops = [image[max(int((height - top) * scale), 0):int((height - bottom) * scale),
                   max(int(left * scale), 0):int(right * scale)]
             for left, bottom, right, top in boxes]
    return [crop for crop in crops if crop.shape[0] > 0 and crop.shape[1] > 0]


def pdfium_available():
    """PDF 쪽을 이미지로 그리는 pypdfium2가 설치되어 있으면 True. PDF OCR에만 필요하고 DOCX·PPTX 그림 OCR에는 필요 없다."""
    return find_spec("pypdfium2") is not None


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
    # 여러 쪽에 반복되는 머리말·쪽 번호는 모든 쪽에서 똑같이 뺀다. 글자 레이어가 없는 쪽(스캔)은 빈 글자라 세지 않는다.
    page_texts = []
    for page in pages:
        page_texts.append(page.page_content)
    headers = find_header_lines(page_texts)
    if len(headers) > 0:
        doc.warnings.append(f"반복 머리말 {len(headers)}줄 제거")
    max_pages = get_settings().ocr_max_pages
    can_render = pdfium_available()
    page_ocr = ocr.is_available() and can_render and max_pages > 0   # 글자 없는 쪽 전체 OCR
    reader = figures.FigureReader()                   # 쪽 안의 그림 OCR (OCR_MAX_FIGURES가 0이면 꺼짐)
    read_figures = reader.enabled and can_render
    pdf = None        # OCR할 쪽이 처음 나올 때만 연다
    pdf_failed = False   # PDF를 그림으로 열지 못했거나 OCR 엔진이 오류를 냈으면 이 문서의 OCR을 멈춘다
    ocr_count = 0
    pages_read = 0    # OCR로 글자를 읽어 본문에 넣은 쪽 수
    pages_unread = 0  # 글자가 없는데 OCR로 읽지 못한 쪽 수(OCR 꺼짐·상한 초과·OCR로도 글자 없음·엔진 오류)
    over_limit = []   # 쪽 수 상한 때문에 OCR하지 않은 쪽 번호
    figure_pages = 0  # 그림 속 글자를 본문에 붙인 쪽 수
    for page in pages:
        section_no = page.metadata["page"] + 1
        # 머리말을 빼고 끊긴 단어를 이은 글자를 쓴다. 머리말뿐이던 쪽은 빈 글자가 되어 아래의 빈 쪽 처리를 따른다.
        text = join_split_words(remove_page_header(page.page_content, headers))
        # OCR이 꺼져 있어도 PDF를 열어 읽지 못한 그림을 센다(보고서에 OCR 상태와 함께 남기려고).
        if pdf is None and not pdf_failed and can_render and ((text.strip() == "" and page_ocr) or text.strip() != ""):
            try:
                import pypdfium2
                pdf = pypdfium2.PdfDocument(file_path)
                opened.append(pdf)
            except Exception:   # 쪽을 그리지 못하면 OCR 없이 예전처럼 건너뛴다
                logger.exception("PDF를 OCR용으로 열지 못했습니다. 이 문서의 OCR을 건너뜁니다")
                pdf_failed = True
                if page_ocr or read_figures:   # OCR을 쓰려던 문서만 알린다(OCR이 꺼진 문서는 읽지 못한 그림 수만 못 세는 것이다)
                    doc.warnings.append("OCR용으로 PDF를 열지 못해 OCR을 건너뜀")
        if pdf_failed:
            page_ocr = read_figures = False
        if text.strip() == "" and page_ocr:
            if ocr_count >= max_pages:
                over_limit.append(section_no)   # 경고는 문서 끝에서 한 줄로 모은다(쪽마다 남기면 보고서가 길어진다)
                pages_unread += 1
                continue
            ocr_count += 1
            try:
                text = ocr.ocr_image(render_page(pdf, section_no - 1))
            except Exception:   # 쪽을 그리지 못했거나 OCR 엔진 오류(모델 내려받기 실패 등): 업로드 전체가 실패하지 않게 건너뛴다
                logger.exception("p.%d: OCR 중 오류가 났습니다. 이 문서의 OCR을 멈춥니다", section_no)
                pdf_failed = True
                page_ocr = read_figures = False
                doc.warnings.append(f"p.{section_no}: OCR 중 오류가 나 건너뜀 (이 문서의 OCR을 멈춤, 로그 확인)")
                pages_unread += 1
                continue
            if text.strip() == "":
                doc.warnings.append(f"p.{section_no}: OCR로도 글자를 읽지 못해 건너뜀")
                pages_unread += 1
                continue
            doc.warnings.append(f"p.{section_no}: 글자가 없어 OCR로 읽음")
            pages_read += 1
        elif text.strip() == "":
            doc.warnings.append(f"p.{section_no}: 글자가 없어 건너뜀")
            pages_unread += 1
            continue
        elif not (read_figures and reader.enabled):
            # OCR을 쓸 수 없는 상태(미설치·설정 꺼짐·엔진 오류 뒤)에서는 읽지 못한 그림만 센다. 세다가 실패해도 업로드는 계속한다.
            if pdf is not None:
                try:
                    reader.unread += count_figures(pdf, section_no - 1)
                except Exception:
                    logger.exception("p.%d: 그림 수를 세지 못했습니다", section_no)
        elif read_figures and reader.enabled:
            try:
                crops = figure_crops(pdf, section_no - 1, reader)
            except Exception:   # pypdfium2 오류 등: 본문은 그대로 두고 그림만 건너뛴다
                logger.exception("p.%d: 그림 영역을 잘라 내지 못했습니다", section_no)
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
    figures.set_ocr_stats(doc, figures.ocr_status(can_render, uses_pages=True), pages_read + reader.appended, pages_unread + reader.unread)
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
