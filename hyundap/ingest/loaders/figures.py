"""ingest/loaders/figures.py — 문서 안의 그림(그래프·표 이미지) OCR 공통 처리.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    is_figure_size — 읽을 만한 크기의 그림인지 판단한다.
    drop_duplicate_lines — 그림에서 읽은 줄 중 본문에 이미 있는 줄을 뺀다.
    picture_to_array — 문서에 들어 있는 그림 파일(바이트)을 OCR용 배열로 바꾼다.
자료형:
    FigureReader — 문서 하나의 그림 OCR 수를 세며 그림을 한 장씩 읽는다.

PDF·DOCX·PPTX 로더가 같은 크기 기준·상한·중복 제거를 쓰도록 모아 둔 곳이다."""

import io

from .. import ocr
from hyundap.common.config import get_settings

MIN_FIGURE_SIZE_PT = (60, 40)   # (너비, 높이) 이보다 작은 그림(로고·아이콘)은 읽지 않는다
MIN_FIGURE_AREA = 0.01  # 쪽(슬라이드) 면적 대비 그림 면적 비율 하한
MAX_FIGURE_AREA = 0.85  # 이 이상이면 쪽 전체 배경(스캔본·표지)으로 보고 읽지 않는다
MIN_FIGURE_SCORE = 0.5  # OCR 신뢰도가 이보다 낮은 줄은 쓰레기 글자로 보고 버린다
# 문서 하나에서 OCR로 읽는 그림 수 상한은 설정 OCR_MAX_FIGURES(기본 100)다. 0이면 그림 OCR을 하지 않는다.
DUPLICATE_MIN_CHARS = 8  # 이 글자 수 이상인 줄만 본문과 겹치는지 따진다(짧은 눈금 숫자·범례는 항상 남긴다)
DUPLICATE_COVERAGE = 0.8  # 줄의 3글자 조각 중 본문에 있는 비율이 이 값 이상이면 같은 줄로 본다(OCR 오류·잘림 허용)

# 문서에 들어 있는 그림은 해상도가 낮은 경우가 많다. 실측(그래프 2쪽)에서 72dpi 그림은 3배 확대하면 정답 토큰이
# 47/78에서 67/78로, 150dpi 그림은 71/78에서 76/78로 늘었다. 그래서 긴 변이 이 값이 되도록(최대 3배) 확대해서 읽는다.
MIN_PICTURE_PX = 40         # 이보다 작은 그림(점·선)은 읽지 않는다
UPSCALE_TARGET_PX = 1000
MAX_UPSCALE = 3
MAX_PICTURE_PX = 8000       # 긴 변이 이보다 크면 줄여서 읽는다(메모리 폭증 방지)


class UnreadablePicture(Exception):
    """그림 파일을 읽지 못함(EMF·WMF 같은 벡터 형식, 손상 등)."""


def is_figure_size(width_pt, height_pt, page_area_pt=None):
    """그림이 읽을 만한 크기인지 판단한다. page_area_pt(쪽 면적, pt²)를 주면 쪽 대비 비율도 본다.

    너무 작은 그림(로고)과 쪽 전체를 덮는 그림(스캔 배경·표지)은 False."""
    if width_pt < MIN_FIGURE_SIZE_PT[0] or height_pt < MIN_FIGURE_SIZE_PT[1]:
        return False
    if page_area_pt:
        return MIN_FIGURE_AREA <= width_pt * height_pt / page_area_pt < MAX_FIGURE_AREA
    return True


def drop_duplicate_lines(figure_text, page_text):
    """그림에서 읽은 줄 중 본문에 이미 있는 줄을 뺀다.

    스캔본은 쪽이 여러 조각 이미지로 나뉘고 그 글자가 스캐너가 넣은 글자 레이어에도 있어서, 그대로 두면
    같은 문장이 OCR 오류가 낀 채 한 번 더 붙는다. 3글자 조각이 본문에 대부분 있으면 같은 줄로 본다."""
    page_flat = "".join(page_text.split())
    page_grams = {page_flat[i:i + 3] for i in range(len(page_flat) - 2)}
    kept = []
    for line in figure_text.splitlines():
        flat = "".join(line.split())
        if len(flat) >= DUPLICATE_MIN_CHARS:
            grams = [flat[i:i + 3] for i in range(len(flat) - 2)]
            if sum(g in page_grams for g in grams) / len(grams) >= DUPLICATE_COVERAGE:
                continue
        kept.append(line)
    return "\n".join(kept)


def picture_to_array(blob):
    """그림 파일(바이트)을 OCR용 BGR 배열로 바꾼다. 너무 작은 그림이면 None, 읽지 못하면 UnreadablePicture.

    투명한 배경은 흰색으로 채우고(검정이 되면 글자가 안 보인다), 해상도가 낮으면 확대한다."""
    import numpy
    from PIL import Image

    try:
        picture = Image.open(io.BytesIO(blob))
        picture.load()
    except (OSError, ValueError, Image.DecompressionBombError) as error:   # UnidentifiedImageError는 OSError
        raise UnreadablePicture(str(error))
    if min(picture.size) < MIN_PICTURE_PX:
        return None
    picture = picture.convert("RGBA")
    white = Image.new("RGBA", picture.size, (255, 255, 255, 255))
    picture = Image.alpha_composite(white, picture).convert("RGB")
    longest = max(picture.size)
    if longest > MAX_PICTURE_PX:
        factor = MAX_PICTURE_PX / longest
    elif longest < UPSCALE_TARGET_PX:
        factor = min(MAX_UPSCALE, UPSCALE_TARGET_PX / longest)
    else:
        factor = 1
    if factor != 1:
        picture = picture.resize((round(picture.width * factor), round(picture.height * factor)), Image.LANCZOS)
    return numpy.ascontiguousarray(numpy.asarray(picture)[:, :, ::-1])


class FigureReader:
    """문서 하나의 그림 OCR을 맡는다. OCR한 그림 수를 세고 상한(설정 OCR_MAX_FIGURES)을 지킨다.

    읽은 글자 중 본문(page_text)과 겹치는 줄은 빼고 돌려준다. page_text는 문자열이거나 필요할 때만
    본문을 만들어 돌려주는 함수다(긴 본문을 그림마다 이어 붙이지 않으려는 용도).
    PaddleOCR가 설치되어 있지 않으면 enabled가 False이고 읽는 함수는 모두 빈 문자열을 돌려준다."""

    def __init__(self):
        self.max_count = get_settings().ocr_max_figures
        self.enabled = ocr.is_available() and self.max_count > 0
        self.count = 0          # OCR한 그림 수(상한 대상)
        self.appended = 0       # 글자를 읽어 본문에 붙인 그림 수
        self.unreadable = 0     # 형식을 읽지 못해 건너뛴 그림 수(EMF·WMF 등)
        self.capped = False     # 상한 때문에 읽지 않은 그림이 있는지

    def read_array(self, array, page_text=""):
        """OCR용 배열 한 장에서 읽은 글자(본문과 겹치는 줄 제외)를 돌려준다. 없거나 상한을 넘으면 빈 문자열."""
        if not self.enabled:
            return ""
        if self.count >= self.max_count:
            self.capped = True
            return ""
        self.count += 1
        text = ocr.ocr_image(array, min_score=MIN_FIGURE_SCORE)
        text = drop_duplicate_lines(text, page_text() if callable(page_text) else page_text).strip()
        if text:
            self.appended += 1
        return text

    def read_picture(self, blob, width_pt=None, height_pt=None, page_area_pt=None, page_text=""):
        """문서에 들어 있는 그림 파일 한 장에서 읽은 글자를 돌려준다. 너무 작거나 읽지 못하면 빈 문자열.

        width_pt·height_pt(문서에 표시된 크기)를 모르면 크기 검사는 그림 픽셀 수로만 한다."""
        if not self.enabled:
            return ""
        if width_pt is not None and height_pt is not None and not is_figure_size(width_pt, height_pt, page_area_pt):
            return ""
        try:
            array = picture_to_array(blob)
        except UnreadablePicture:
            self.unreadable += 1
            return ""
        if array is None:
            return ""
        return self.read_array(array, page_text)

    def warnings(self, extra=""):
        """문서 경고에 남길 문구 목록. extra는 요약 줄 앞에 붙일 말(예: '20쪽, ')."""
        messages = []
        if self.appended:
            messages.append(f"그림 속 글자를 OCR로 읽어 본문에 붙임: {extra}그림 {self.appended}개")
        if self.unreadable:
            messages.append(f"읽을 수 없는 형식의 그림 {self.unreadable}개는 건너뜀 (EMF·WMF 등)")
        if self.capped:
            messages.append(f"그림 OCR은 문서당 {self.max_count}개까지라 나머지 그림은 읽지 않음")
        return messages
