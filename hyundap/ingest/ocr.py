"""ingest/ocr.py — 이미지 속 글자(스캔 쪽, 쪽 안의 그래프·표 이미지)를 PaddleOCR로 읽는다.

담당: 김혜리
기준 명세: AI_문서검색_통합_기능명세서

함수:
    is_available — PaddleOCR가 설치되어 있는지 확인한다.
    ocr_image — 이미지 한 장(파일 경로 또는 numpy 배열)의 글자를 줄 단위로 읽어 돌려준다(신뢰도 기준 선택).
    warm_up — 엔진을 미리 만들고 글자가 있는 이미지로 한 번 돌려 첫 OCR 지연을 없앤다.
    start_warm_up — 설정이 켜져 있으면 warm_up을 백그라운드 스레드로 시작한다. 서버 시작을 막지 않는다.

PaddleOCR는 설치가 무겁고 첫 실행에 모델을 내려받아서, 처음 부를 때만 불러온다(지연 로딩).
설치되어 있지 않으면 is_available()이 False이고, 로더는 예전처럼 글자 없는 쪽을 건너뛴다.
스레드 수(OCR_THREADS)·예열(OCR_WARMUP)은 .env의 설정(get_settings)에서 읽는다.

설정을 고칠 때 주의:
- 검출 모델 이름만 지정하면 한국어 인식 모델 선택이 풀려 한글이 공백으로 나온다.
  그래서 검출·인식 모델 이름을 항상 같이 지정한다.
- PaddlePaddle 3.3.x는 CPU에서 oneDNN 오류가 나므로 requirements.txt의 3.2.0을 쓴다."""

import logging
import os
import threading
import time
from importlib.util import find_spec

from hyundap.common.config import get_settings

# 모델 저장소 연결 확인을 건너뛰어 첫 호출 지연을 줄인다. PaddleOCR 불러오기 전에 정해야 한다.
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

logger = logging.getLogger(__name__)

DETECTION_MODEL = "PP-OCRv5_mobile_det"
RECOGNITION_MODEL = "korean_PP-OCRv5_mobile_rec"
DETECTION_MAX_SIDE = 1920   # 큰 이미지의 긴 변을 이 값으로 줄여 검출한다(메모리 폭증 방지)
RECOGNITION_BATCH_SIZE = 4

# 엔진 생성과 실행을 한 번에 하나씩만 하게 한다. 서버 시작 때의 예열과 첫 업로드가 겹쳐도 엔진이 두 개 만들어지거나
# 한 엔진이 동시에 실행되지 않는다(예열이 끝날 때까지 업로드가 기다린다).
_lock = threading.RLock()
_engine = None


def is_available():
    """PaddleOCR·PaddlePaddle·pypdfium2(PDF 쪽 렌더링)가 모두 설치되어 있으면 True. 모델은 불러오지 않는다."""
    return all(find_spec(name) is not None for name in ("paddleocr", "paddle", "pypdfium2"))


def _get_engine():
    """OCR 엔진을 한 번만 만든다. 첫 호출 때 모델 파일을 내려받을 수 있다."""
    global _engine
    with _lock:
        if _engine is None:
            from paddleocr import PaddleOCR

            _engine = PaddleOCR(
                text_detection_model_name=DETECTION_MODEL,
                text_recognition_model_name=RECOGNITION_MODEL,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=True,
                text_det_limit_type="max",
                text_det_limit_side_len=DETECTION_MAX_SIDE,
                text_recognition_batch_size=RECOGNITION_BATCH_SIZE,
                cpu_threads=get_settings().ocr_threads,
            )
        return _engine


def ocr_image(image, min_score=0.0):
    """이미지 한 장에서 읽은 글자를 한 줄씩 줄바꿈으로 이어 돌려준다. 글자가 없으면 빈 문자열.

    min_score보다 신뢰도가 낮은 줄은 버린다(그래프·무늬에서 나오는 쓰레기 글자를 거를 때 쓴다)."""
    with _lock:
        result = _get_engine().predict(image)
    if not result:
        return ""
    lines = [text for text, score in zip(result[0]["rec_texts"], result[0]["rec_scores"]) if score >= min_score]
    return "\n".join(lines)


def warmup_image():
    """예열용 이미지(흰 바탕에 큰 글자)를 만든다. 글자가 있어야 검출·인식 경로가 모두 한 번 실행된다."""
    import numpy
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (960, 320), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=56)   # Pillow 10.1 이상은 크기를 정할 수 있다
    except TypeError:
        font = ImageFont.load_default()
    draw.text((30, 40), "OCR warm up 2025", fill="black", font=font)
    draw.text((30, 170), "Hello 123 Q1 Q2 Q3", fill="black", font=font)
    return numpy.asarray(image)[:, :, ::-1].copy()


def warm_up():
    """엔진을 만들고 글자가 있는 이미지로 한 번 돌려 첫 OCR 지연을 없앤다. 성공하면 True.

    예열은 부가 기능이라 어떤 오류도 밖으로 내지 않는다. 실패하면 로그만 남기고, 실제 OCR이 필요할 때 다시 시도한다."""
    if not is_available():
        return False
    started = time.time()
    try:
        with _lock:
            _get_engine().predict(warmup_image())
    except Exception:   # 모델 내려받기·설치 문제 등 무엇이든 서버 시작과 업로드를 막으면 안 된다
        logger.exception("OCR 엔진 예열에 실패했습니다. 첫 OCR 호출 때 다시 시도합니다")
        return False
    logger.info("OCR 엔진 예열 완료 (%.1f초)", time.time() - started)
    return True


def start_warm_up():
    """OCR_WARMUP이 켜져 있고 PaddleOCR가 설치되어 있으면 warm_up을 백그라운드 스레드로 시작한다.

    서버 시작을 막지 않는다. 시작한 스레드를 돌려주고, 꺼져 있거나 설치되어 있지 않으면 None."""
    if not get_settings().ocr_warmup or not is_available():
        return None
    thread = threading.Thread(target=warm_up, name="ocr-warm-up", daemon=True)
    thread.start()
    return thread
