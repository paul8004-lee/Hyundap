"""tests/test_ingest.py — ingest(검증·읽기·정리·청킹)의 오류 처리와 보존 동작 검증.

파일은 테스트 안에서 만들어 쓰므로 외부 샘플이 필요 없다.
테스트 실행 명령은 저장소 맨 위에서 python -m unittest hyundap.tests.test_ingest -v이다."""
import base64
import contextlib
import io
import os
import pickle
import re
import sys
import tempfile
import threading
import types
import unittest
import zipfile
import time
import zlib
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import numpy
from docx import Document as DocxDocument
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from langchain_core.documents import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

from hyundap.common.config import get_settings
from hyundap.ingest import ocr
from hyundap.ingest.loaders import charts, figures, pdf_loader
from hyundap.ingest.loaders.base import table_to_text
from hyundap.ingest.loaders.docx_loader import load_docx
from hyundap.ingest.loaders.pdf_loader import load_pdf
from hyundap.ingest.loaders.pptx_loader import load_pptx
from hyundap.ingest import validator
from hyundap.ingest.models import (CORRUPTED_FILE, ENCRYPTED_FILE, LEGACY_FORMAT, UNSUPPORTED_FORMAT, IngestError)
from hyundap.ingest.normalizer import normalize_text
from hyundap.ingest.pipeline import ingest_document

# 1x1 PNG. 그림이 든 DOCX·PPTX를 만들 때 쓴다.
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')

def png_bytes(width=300, height=200, color='white'):
    """지정한 크기의 단색 PNG 바이트를 만든다. color가 4값이면 투명 PNG가 된다."""
    buffer = io.BytesIO()
    Image.new('RGBA' if isinstance(color, tuple) else 'RGB', (width, height), color).save(buffer, 'PNG')
    return buffer.getvalue()


@contextlib.contextmanager
def ocr_settings(**env):
    """OCR_* 환경변수를 바꿔 get_settings를 다시 읽게 한다. 끝나면 원래 설정으로 되돌린다."""
    get_settings.cache_clear()
    try:
        with patch.dict(os.environ, {name: str(value) for name, value in env.items()}), \
                patch('hyundap.common.config.load_dotenv'):
            get_settings.cache_clear()
            yield
    finally:
        get_settings.cache_clear()


def chart_blob(kind=XL_CHART_TYPE.LINE_MARKERS, title='소매판매액지수 증가율', axes=None, data=None):
    """python-pptx로 차트를 만들어 차트 XML(바이트)을 돌려준다. 기본은 항목 3개·계열 2개짜리 꺾은선 차트다."""
    if data is None:
        data = CategoryChartData()
        data.categories = ['24.Q1', 'Q2', 'Q3']
        data.add_series('전기비', (-2.2, 0.30000000000000004, 0.7))
        data.add_series('전년동기비', (-1.6, -3.1, None))
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    chart = slide.shapes.add_chart(kind, Inches(1), Inches(2), Inches(5), Inches(3), data).chart
    if title:
        chart.has_title = True
        chart.chart_title.text_frame.text = title
    if axes:
        chart.category_axis.has_title = True
        chart.category_axis.axis_title.text_frame.text = axes[0]
        chart.value_axis.has_title = True
        chart.value_axis.axis_title.text_frame.text = axes[1]
    return chart.part.blob


LINE_CHART_TEXT = ('[차트] 소매판매액지수 증가율 (꺾은선)\n| 구분 | 전기비 | 전년동기비 |\n| --- | --- | --- |\n'
                   '| 24.Q1 | -2.2 | -1.6 |\n| Q2 | 0.3 | -3.1 |\n| Q3 | 0.7 |  |')


def add_docx_chart(document, paragraph, blob):
    """python-docx에는 차트를 넣는 기능이 없어 차트 파트를 문서에 직접 연결하고 paragraph 끝에 인라인 차트 요소를 붙인다."""
    number = sum(1 for rel in document.part.rels.values() if rel.reltype == RELATIONSHIP_TYPE.CHART) + 1
    part = Part(PackURI(f'/word/charts/chart{number}.xml'),
                'application/vnd.openxmlformats-officedocument.drawingml.chart+xml', blob, document.part.package)
    rid = document.part.relate_to(part, RELATIONSHIP_TYPE.CHART)
    drawing = parse_xml(
        f'<w:drawing {nsdecls("w", "wp", "a", "c", "r")}><wp:inline><wp:extent cx="4572000" cy="2743200"/>'
        f'<wp:docPr id="{number}" name="Chart {number}"/><a:graphic>'
        f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart r:id="{rid}"/>'
        f'</a:graphicData></a:graphic></wp:inline></w:drawing>')
    paragraph.add_run()._r.append(drawing)


# PDF 쪽을 그리는 pypdfium2가 설치되어 있어야 하는 테스트에 붙인다(OCR 의존성은 선택 설치라 없으면 건너뛴다).
PDFIUM = unittest.skipUnless(find_spec('pypdfium2'), 'pypdfium2가 설치되어 있어야 함')


INFO = {'document_id': 'd1', 'version_id': 'v1', 'company_id': 'c1',
        'company_name': '현대', 'doc_type': 'manual', 'file_name': 'x'}


def minimal_pdf(text):
    """글자 한 줄이 든 가장 작은 PDF 바이트를 만든다."""
    stream = f'BT /F1 12 Tf 20 100 Td ({text}) Tj ET'.encode()
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R '
        b'/Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    return assemble_pdf(objects)


def pdf_with_images(text, sizes):
    """글자 한 줄과 그림이 든 400x400pt PDF 바이트를 만든다. sizes는 그림마다 (너비, 높이) pt이며 아래에서 위로 쌓는다."""
    draw, xobjects, images, y = b'', b'', [], 10
    for n, (w, h) in enumerate(sizes):
        draw += b'q %d 0 0 %d 10 %d cm /Im%d Do Q\n' % (w, h, y, n)
        xobjects += b'/Im%d %d 0 R ' % (n, 6 + n)
        images.append(b'<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB '
                      b'/BitsPerComponent 8 /Length 12 >>\nstream\n' + bytes([200, 100, 50]) * 4 + b'\nendstream')
        y += h + 10
    stream = f'BT /F1 12 Tf 20 380 Td ({text}) Tj ET\n'.encode() + draw
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] /Contents 4 0 R '
        b'/Resources << /Font << /F1 5 0 R >> /XObject << ' + xobjects + b'>> >> >>',
        b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ] + images
    return assemble_pdf(objects)


def assemble_pdf(objects):
    """객체 목록(1번부터)을 xref와 함께 PDF 바이트로 묶는다."""
    out = b'%PDF-1.4\n'
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    for offset in offsets:
        out += b'%010d 00000 n \n' % offset
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref)
    return out


class IngestTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def path(self, name):
        return str(self.dir / name)

    def ingest(self, name, **kwargs):
        return ingest_document(self.path(name), dict(INFO), **kwargs)


class CorruptedFileTests(IngestTestCase):
    """깨진 파일은 예외가 아니라 CORRUPTED_FILE 결과로 돌아와야 한다 (#169)."""

    def test_zip_data_error_in_docx_is_corrupted(self):
        """압축 데이터가 깨져 zlib.error가 나도 CORRUPTED_FILE 결과가 되는지 확인한다."""
        DocxDocument().save(self.path('a.docx'))
        with patch.object(zipfile.ZipFile, 'testzip', side_effect=zlib.error('invalid distance')):
            result = self.ingest('a.docx')
        self.assertEqual((result.status, result.error_code), ('failed', CORRUPTED_FILE))

    def test_zip_data_error_in_pptx_is_corrupted(self):
        """PPTX도 같은 오류가 CORRUPTED_FILE 결과가 되는지 확인한다."""
        Presentation().save(self.path('a.pptx'))
        with patch.object(zipfile.ZipFile, 'testzip', side_effect=zlib.error('invalid distance')):
            result = self.ingest('a.pptx')
        self.assertEqual((result.status, result.error_code), ('failed', CORRUPTED_FILE))

    def test_loaders_turn_bodyless_package_into_corrupted(self):
        """[Content_Types].xml만 있는 zip을 로더에 직접 넣어도 AttributeError 대신 CORRUPTED_FILE이 되는지 확인한다 (#198)."""
        for name, loader in (('no_body.docx', load_docx), ('no_body.pptx', load_pptx)):
            with zipfile.ZipFile(self.path(name), 'w') as archive:
                archive.writestr('[Content_Types].xml', '<Types/>')
            with self.subTest(name=name):
                with self.assertRaises(IngestError) as caught:
                    loader(self.path(name), dict(INFO))
                self.assertEqual(caught.exception.code, CORRUPTED_FILE)

    def test_pdf_parse_errors_are_corrupted(self):
        """pypdf가 깨진 PDF에서 내는 예외들이 CORRUPTED_FILE이 되는지 확인한다."""
        Path(self.path('a.pdf')).write_bytes(minimal_pdf('hello'))
        for error in (ValueError('Ascii85 overflow'),
                      UnicodeDecodeError('utf-16-be', b'x', 0, 1, 'truncated'),
                      NotImplementedError('Unsupported filter')):
            with self.subTest(error=type(error).__name__):
                with patch('hyundap.ingest.loaders.pdf_loader.PyPDFLoader') as loader:
                    loader.return_value.load.side_effect = error
                    with self.assertRaises(IngestError) as caught:
                        load_pdf(self.path('a.pdf'), dict(INFO))
                self.assertEqual(caught.exception.code, CORRUPTED_FILE)

    def test_missing_package_is_not_hidden(self):
        """패키지 미설치(ImportError) 같은 개발자 실수는 CORRUPTED_FILE로 덮지 않는지 확인한다."""
        Path(self.path('a.pdf')).write_bytes(minimal_pdf('hello'))
        with patch('hyundap.ingest.loaders.pdf_loader.PyPDFLoader') as loader:
            loader.return_value.load.side_effect = ImportError('pypdf')
            with self.assertRaises(ImportError):
                load_pdf(self.path('a.pdf'), dict(INFO))

    def test_normal_pdf_still_succeeds(self):
        """정상 PDF가 그대로 청크가 되는지 확인한다."""
        Path(self.path('a.pdf')).write_bytes(minimal_pdf('Hello Elevator'))
        result = self.ingest('a.pdf')
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.chunks[0].location_label, 'p.1')
        self.assertIn('Hello Elevator', result.chunks[0].text)


class OcrTests(IngestTestCase):
    """글자 레이어가 없는 PDF 쪽(스캔 이미지)은 OCR로 읽고, 글자가 있는 쪽은 OCR을 건드리지 않아야 한다."""

    def setUp(self):
        super().setUp()
        Path(self.path('scan.pdf')).write_bytes(minimal_pdf(''))   # 글자 없는 쪽 1장

    @PDFIUM
    def test_page_without_text_is_read_by_ocr(self):
        """글자 없는 쪽이 OCR 결과로 섹션이 되고, 경고에 OCR로 읽었다고 남는지 확인한다."""
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image', return_value='스캔 본문') as read:
            result = self.ingest('scan.pdf')
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.chunks[0].location_label, 'p.1')
        self.assertIn('스캔 본문', result.chunks[0].text)
        self.assertEqual(result.report['warnings'], ['p.1: 글자가 없어 OCR로 읽음'])
        image = read.call_args.args[0]   # 200x200pt 쪽이 200dpi로 그려진 BGR 배열
        self.assertEqual(image.shape, (556, 556, 3))

    def test_page_with_text_does_not_call_ocr(self):
        """글자가 있는 PDF는 OCR 엔진을 부르지 않는지 확인한다."""
        Path(self.path('a.pdf')).write_bytes(minimal_pdf('Hello Elevator'))
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image') as read:
            result = self.ingest('a.pdf')
        self.assertEqual(result.status, 'success')
        read.assert_not_called()

    def test_without_ocr_engine_page_is_skipped_as_before(self):
        """PaddleOCR가 없으면 예전처럼 글자 없는 쪽을 건너뛰고 EMPTY_DOCUMENT가 되는지 확인한다."""
        with patch('hyundap.ingest.ocr.is_available', return_value=False), \
                patch('hyundap.ingest.ocr.ocr_image') as read:
            result = self.ingest('scan.pdf')
        self.assertEqual((result.status, result.error_code), ('failed', 'EMPTY_DOCUMENT'))
        read.assert_not_called()

    @PDFIUM
    def test_ocr_reading_nothing_is_empty_document(self):
        """OCR로도 글자가 없으면 EMPTY_DOCUMENT가 되고 경고가 남는지 확인한다."""
        doc = None
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image', return_value='  \n'):
            doc = load_pdf(self.path('scan.pdf'), dict(INFO))
            result = self.ingest('scan.pdf')
        self.assertEqual(doc.warnings, ['p.1: OCR로도 글자를 읽지 못해 건너뜀'])
        self.assertEqual((result.status, result.error_code), ('failed', 'EMPTY_DOCUMENT'))

    @PDFIUM
    def test_ocr_stops_at_page_limit(self):
        """OCR 쪽 수 상한을 넘은 쪽은 OCR하지 않고 경고만 남기는지 확인한다."""
        pages = [Document(page_content='', metadata={'page': n}) for n in range(3)]
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image', return_value='본문') as read, \
                patch('hyundap.ingest.loaders.pdf_loader.render_page'), \
                ocr_settings(OCR_MAX_PAGES=1), \
                patch('hyundap.ingest.loaders.pdf_loader.PyPDFLoader') as loader:
            loader.return_value.load.return_value = pages
            doc = load_pdf(self.path('scan.pdf'), dict(INFO))
        self.assertEqual(read.call_count, 1)
        self.assertEqual([s.section_no for s in doc.sections], [1])
        self.assertEqual(doc.warnings[1:], ['2쪽(p.2~p.3)은 글자가 없어 건너뜀 (OCR은 문서당 1쪽까지)'])   # 쪽마다가 아니라 한 줄로 모은다

    @PDFIUM
    def test_pdfium_document_is_closed_after_reading(self):
        """쪽을 그리려고 연 PDF 문서를 읽은 뒤 닫는지 확인한다(안 닫으면 Windows에서 파일이 잠긴다)."""
        pages = [Document(page_content='', metadata={'page': 0})]
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image', return_value='본문'), \
                patch('hyundap.ingest.loaders.pdf_loader.render_page'), \
                patch('pypdfium2.PdfDocument') as opened, \
                patch('hyundap.ingest.loaders.pdf_loader.PyPDFLoader') as loader:
            loader.return_value.load.return_value = pages
            load_pdf(self.path('scan.pdf'), dict(INFO))
        opened.return_value.close.assert_called_once()

    def test_pdf_without_pdfium_skips_ocr_like_before(self):
        """pypdfium2가 없으면 PDF OCR만 꺼지고 글자 없는 쪽은 예전처럼 건너뛰는지 확인한다."""
        pages = [Document(page_content='', metadata={'page': 0}), Document(page_content='본문', metadata={'page': 1})]
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image') as read, \
                patch('hyundap.ingest.loaders.pdf_loader.pdfium_available', return_value=False), \
                patch('hyundap.ingest.loaders.pdf_loader.PyPDFLoader') as loader:
            loader.return_value.load.return_value = pages
            doc = load_pdf(self.path('scan.pdf'), dict(INFO))
        read.assert_not_called()
        self.assertEqual([s.section_no for s in doc.sections], [2])
        self.assertEqual(doc.warnings, ['p.1: 글자가 없어 건너뜀', 'OCR이 설치되어 있지 않아 이미지·스캔 쪽 1개의 글자를 읽지 않음'])


class FigureOcrTests(IngestTestCase):
    """글자 레이어가 있는 쪽 안의 그림(그래프 등)은 그림 영역만 OCR해 본문 뒤에 붙여야 한다."""

    def read_figures(self, sizes, ocr_text='24.Q1 Q2 Q3\n전기비', text='Hello Elevator', page_extra=b''):
        """page_extra는 쪽 사전에 덧붙일 항목(예: b'/Rotate 90 /CropBox [5 5 395 395]')이다."""
        Path(self.path('mixed.pdf')).write_bytes(pdf_with_images(text, sizes).replace(b'/MediaBox', page_extra + b' /MediaBox', 1))
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image', return_value=ocr_text) as read:
            doc = load_pdf(self.path('mixed.pdf'), dict(INFO))
        return doc, read

    @PDFIUM
    def test_figure_text_is_appended_after_page_text(self):
        """그림에서 읽은 글자가 본문 뒤에 붙고, 그림 영역만 300dpi로 잘려 OCR에 전달되는지 확인한다."""
        doc, read = self.read_figures([(200, 150)])
        self.assertEqual(doc.sections[0].text, 'Hello Elevator\n\n24.Q1 Q2 Q3\n전기비')
        self.assertEqual(doc.warnings, ['그림 속 글자를 OCR로 읽어 본문에 붙임: 1쪽, 그림 1개'])
        crop = read.call_args.args[0]
        self.assertEqual(read.call_args.kwargs['min_score'], 0.5)
        self.assertLessEqual(abs(crop.shape[0] - 625), 2)   # 150pt x 300dpi
        self.assertLessEqual(abs(crop.shape[1] - 834), 2)   # 200pt x 300dpi
        self.assertEqual(crop.shape[2], 3)

    @PDFIUM
    def test_rotated_and_cropped_pages_cut_the_same_figure_area(self):
        """쪽이 회전(/Rotate)돼 있거나 보이는 영역(CropBox)이 원점에서 떨어져 있어도 그림 영역을 맞게 잘라 내는지 확인한다.

        예전에는 90·180·270도 쪽에서 잘못된 영역이 잘려 그림 속 글자를 경고 없이 놓쳤다."""
        for extra in (b'/Rotate 90', b'/Rotate 180', b'/Rotate 270', b'/CropBox [5 5 395 395]', b'/Rotate 90 /CropBox [5 5 395 395]'):
            with self.subTest(extra=extra):
                doc, read = self.read_figures([(200, 150)], page_extra=extra)
                crop = read.call_args.args[0]
                self.assertLessEqual(abs(crop.shape[0] - 625), 2)   # 150pt x 300dpi
                self.assertLessEqual(abs(crop.shape[1] - 834), 2)   # 200pt x 300dpi
                self.assertEqual(float(crop.mean()) < 255 and crop.min() < 255, True)   # 흰 화면(영역이 어긋남)이 아니다
                self.assertEqual(doc.warnings, ['그림 속 글자를 OCR로 읽어 본문에 붙임: 1쪽, 그림 1개'])

    def test_small_and_full_page_images_are_skipped(self):
        """로고 크기의 작은 그림과 쪽 전체를 덮는 그림은 OCR하지 않는지 확인한다."""
        for sizes in ([(30, 20)], [(380, 380)]):
            with self.subTest(sizes=sizes):
                doc, read = self.read_figures(sizes)
                read.assert_not_called()
                self.assertEqual(doc.sections[0].text.strip(), 'Hello Elevator')
                self.assertEqual(doc.warnings, [])

    @PDFIUM
    def test_lines_already_in_page_text_are_dropped(self):
        """본문에 이미 있는 긴 줄은 빼고 짧은 눈금·범례는 남기는지 확인한다."""
        doc, _ = self.read_figures([(200, 150)], ocr_text='Hello Elevator\n24.Q1 Q2 Q3\nQ4')
        self.assertEqual(doc.sections[0].text, 'Hello Elevator\n\n24.Q1 Q2 Q3\nQ4')

    def test_figure_with_only_duplicates_adds_nothing(self):
        """그림에서 읽은 줄이 모두 본문과 같으면 본문이 그대로이고 경고도 없는지 확인한다."""
        doc, _ = self.read_figures([(200, 150)], ocr_text='Hello Elevator')
        self.assertEqual(doc.sections[0].text.strip(), 'Hello Elevator')
        self.assertEqual(doc.warnings, [])

    def test_near_duplicate_with_ocr_errors_is_dropped(self):
        """OCR 오류가 끼거나 잘린 줄도 본문과 같은 줄로 보고 빼는지 확인한다."""
        page = 'The impact of the regularization term on the generalization error'
        for near in ('The impact of the reguIarization term on the generalization error',
                     'impact of the regularization term on the genera'):
            with self.subTest(near=near):
                doc, _ = self.read_figures([(200, 150)], ocr_text=near, text=page)
                self.assertEqual(doc.sections[0].text.strip(), page)

    @PDFIUM
    def test_pdfium_error_keeps_page_text(self):
        """그림을 읽지 못해도 본문은 그대로 두고 경고만 남기는지 확인한다."""
        self.enterContext(self.assertLogs('hyundap.ingest.loaders.pdf_loader', 'ERROR'))   # 일부러 낸 오류의 로그가 테스트 출력에 섞이지 않게 한다
        import pypdfium2
        Path(self.path('mixed.pdf')).write_bytes(pdf_with_images('Hello Elevator', [(200, 150)]))
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image') as read, \
                patch('hyundap.ingest.loaders.pdf_loader.figure_crops', side_effect=pypdfium2.PdfiumError('bad')):
            doc = load_pdf(self.path('mixed.pdf'), dict(INFO))
        read.assert_not_called()
        self.assertEqual(doc.sections[0].text.strip(), 'Hello Elevator')
        self.assertEqual(doc.warnings, ['p.1: 그림 속 글자 읽기를 건너뜀 (그림을 읽지 못함)'])

    @PDFIUM
    def test_figure_count_is_capped_per_document(self):
        """문서당 그림 OCR 수 상한을 넘는 그림은 읽지 않고 경고를 남기는지 확인한다."""
        with ocr_settings(OCR_MAX_FIGURES=1):
            doc, read = self.read_figures([(150, 100), (150, 100)])
        self.assertEqual(read.call_count, 1)
        self.assertEqual(doc.warnings, ['그림 속 글자를 OCR로 읽어 본문에 붙임: 1쪽, 그림 1개',
                                        '그림 OCR은 문서당 1개까지라 나머지 그림은 읽지 않음'])

    def test_without_ocr_engine_figures_are_ignored(self):
        """PaddleOCR가 없으면 그림을 건드리지 않고 본문만 읽는지 확인한다."""
        Path(self.path('mixed.pdf')).write_bytes(pdf_with_images('Hello Elevator', [(200, 150)]))
        with patch('hyundap.ingest.ocr.is_available', return_value=False), \
                patch('hyundap.ingest.ocr.ocr_image') as read:
            doc = load_pdf(self.path('mixed.pdf'), dict(INFO))
        read.assert_not_called()
        self.assertEqual(doc.sections[0].text.strip(), 'Hello Elevator')


class FigurePictureTests(unittest.TestCase):
    """문서에 들어 있는 그림 파일을 OCR용 배열로 바꾸는 규칙을 확인한다."""

    def test_small_picture_is_skipped(self):
        """한 변이 40px보다 작은 그림(점·선)은 None인지 확인한다."""
        self.assertIsNone(figures.picture_to_array(png_bytes(300, 30)))

    def test_low_resolution_picture_is_upscaled_up_to_three_times(self):
        """긴 변이 1000px보다 작으면 최대 3배까지 확대하고 BGR 3채널인지 확인한다."""
        self.assertEqual(figures.picture_to_array(png_bytes(300, 200)).shape, (600, 900, 3))
        self.assertEqual(figures.picture_to_array(png_bytes(500, 100)).shape, (200, 1000, 3))

    def test_large_picture_is_kept_or_shrunk(self):
        """긴 변이 1000~4000px이면 그대로, 4000px을 넘으면 4000px로 줄이는지 확인한다."""
        self.assertEqual(figures.picture_to_array(png_bytes(1200, 100)).shape, (100, 1200, 3))
        self.assertEqual(figures.picture_to_array(png_bytes(9000, 100)).shape, (44, 4000, 3))

    def test_picture_with_too_many_pixels_is_rejected_before_loading(self):
        """화소 수가 상한을 넘는 그림은 화소를 풀기 전에 읽을 수 없는 그림으로 처리하는지 확인한다(메모리 폭증 방지)."""
        blob = png_bytes(100, 100)   # 그림을 만들 때도 load가 불리므로 patch 밖에서 만든다
        with patch.object(figures, 'MAX_PICTURE_PIXELS', 5000), patch('PIL.Image.Image.load') as load:
            with self.assertRaises(figures.UnreadablePicture):
                figures.picture_to_array(blob)
        load.assert_not_called()

    def test_transparent_background_becomes_white(self):
        """투명 배경이 검정이 아니라 흰색으로 채워지는지 확인한다(검정이면 글자가 안 보인다)."""
        array = figures.picture_to_array(png_bytes(300, 200, (0, 0, 0, 0)))
        self.assertEqual(int(array.min()), 255)

    def test_unreadable_format_raises(self):
        """그림이 아닌 바이트는 UnreadablePicture인지 확인한다."""
        with self.assertRaises(figures.UnreadablePicture):
            figures.picture_to_array(b'not an image')

    def test_figure_size_rules(self):
        """작은 그림과 쪽 면적의 85% 이상인 그림은 제외하는지 확인한다."""
        self.assertTrue(figures.is_figure_size(200, 150))
        self.assertFalse(figures.is_figure_size(59, 150))
        self.assertFalse(figures.is_figure_size(200, 39))
        self.assertTrue(figures.is_figure_size(200, 150, 400 * 400))
        self.assertFalse(figures.is_figure_size(380, 380, 400 * 400))
        self.assertFalse(figures.is_figure_size(61, 41, 1000 * 1000))   # 쪽 면적의 1% 미만


class DocxFigureOcrTests(IngestTestCase):
    """DOCX 안의 그림(그래프·표 속 그림)은 OCR로 읽어 그림이 있는 자리에 넣어야 한다."""

    def read(self, document, available=True, **ocr_result):
        """document를 저장해 load_docx로 읽는다. ocr_result는 OCR 함수 목의 return_value·side_effect다."""
        document.save(self.path('fig.docx'))
        with patch('hyundap.ingest.ocr.is_available', return_value=available), \
                patch('hyundap.ingest.ocr.ocr_image', **(ocr_result or {'return_value': 'Q1 Q2\n전기비'})) as read:
            doc = load_docx(self.path('fig.docx'), dict(INFO))
        return doc, read

    def test_picture_text_is_inserted_where_the_picture_is(self):
        """그림에서 읽은 글자가 앞뒤 문단 사이에 들어가고 경고에 요약이 남는지 확인한다."""
        document = DocxDocument()
        document.add_paragraph('앞 문단')
        document.add_picture(io.BytesIO(png_bytes()), width=Inches(3))
        document.add_paragraph('뒤 문단')
        doc, read = self.read(document)
        self.assertEqual(doc.sections[0].text, '앞 문단\n\nQ1 Q2\n전기비\n\n뒤 문단')
        self.assertEqual(doc.warnings, ['그림 속 글자를 OCR로 읽어 본문에 붙임: 그림 1개'])
        self.assertEqual(read.call_args.kwargs['min_score'], 0.5)
        self.assertEqual(read.call_args.args[0].shape, (600, 900, 3))   # 300x200px을 3배 확대

    def test_picture_without_text_stays_in_unread_count(self):
        """글자를 읽지 못한 그림은 '글자를 읽지 않음' 개수에 남고, 읽은 그림만 빠지는지 확인한다."""
        document = DocxDocument()
        document.add_picture(io.BytesIO(png_bytes()), width=Inches(3))
        document.add_picture(io.BytesIO(png_bytes()), width=Inches(3))
        doc, _ = self.read(document, side_effect=['라벨 글자', ''])
        self.assertEqual(doc.warnings, ['그림·도형·차트·텍스트 상자 1개는 글자를 읽지 않음',
                                        '그림 속 글자를 OCR로 읽어 본문에 붙임: 그림 1개'])

    def test_small_picture_is_not_read(self):
        """문서에 작게 표시된 그림(로고)은 OCR하지 않고 읽지 않은 개체로 남는지 확인한다."""
        document = DocxDocument()
        document.add_paragraph('본문')
        document.add_picture(io.BytesIO(png_bytes()), width=Inches(0.5))
        doc, read = self.read(document)
        read.assert_not_called()
        self.assertEqual(doc.warnings, ['그림·도형·차트·텍스트 상자 1개는 글자를 읽지 않음'])

    def test_picture_in_table_cell_is_read_after_the_table(self):
        """표 칸 안의 그림에서 읽은 글자가 표 글자 뒤에 들어가는지 확인한다."""
        document = DocxDocument()
        table = document.add_table(rows=1, cols=1)
        table.cell(0, 0).text = '표내용'
        table.cell(0, 0).paragraphs[0].add_run().add_picture(io.BytesIO(png_bytes()), width=Inches(3))
        doc, _ = self.read(document, return_value='막대 라벨')
        text = doc.sections[0].text
        self.assertLess(text.index('표내용'), text.index('막대 라벨'))

    def test_line_already_in_section_is_dropped(self):
        """그림에서 읽은 줄이 같은 구간 본문과 같으면 빼고 짧은 눈금은 남기는지 확인한다."""
        document = DocxDocument()
        document.add_paragraph('Hello Elevator world')
        document.add_picture(io.BytesIO(png_bytes()), width=Inches(3))
        doc, _ = self.read(document, return_value='Hello Elevator world\nQ4')
        self.assertEqual(doc.sections[0].text, 'Hello Elevator world\n\nQ4')

    def test_unreadable_picture_format_is_warned_not_fatal(self):
        """EMF처럼 읽을 수 없는 그림은 경고만 남기고 나머지 문서를 읽는지 확인한다."""
        document = DocxDocument()
        document.add_paragraph('본문')
        document.add_picture(io.BytesIO(png_bytes()), width=Inches(3))
        document.save(self.path('fig.docx'))
        reopened = DocxDocument(self.path('fig.docx'))
        for part in reopened.part.related_parts.values():
            if str(part.partname).startswith('/word/media/'):
                part._blob = b'not an image'
        doc, read = self.read(reopened)
        read.assert_not_called()
        self.assertEqual(doc.sections[0].text, '본문')
        self.assertEqual(doc.warnings, ['그림·도형·차트·텍스트 상자 1개는 글자를 읽지 않음',
                                        '읽을 수 없는 형식의 그림 1개는 건너뜀 (EMF 등)'])

    def test_without_ocr_engine_pictures_are_not_touched(self):
        """PaddleOCR가 없으면 그림을 읽지 않고 예전처럼 개수만 경고하는지 확인한다."""
        document = DocxDocument()
        document.add_picture(io.BytesIO(png_bytes()), width=Inches(3))
        doc, read = self.read(document, available=False)
        read.assert_not_called()
        self.assertEqual(doc.warnings, ['그림·도형·차트·텍스트 상자 1개는 글자를 읽지 않음', 'OCR이 설치되어 있지 않아 이미지·스캔 쪽 1개의 글자를 읽지 않음'])

    def test_figure_count_is_capped_per_document(self):
        """문서당 그림 OCR 수 상한을 넘으면 읽지 않고 경고하는지 확인한다."""
        document = DocxDocument()
        for _ in range(2):
            document.add_picture(io.BytesIO(png_bytes()), width=Inches(3))
        with ocr_settings(OCR_MAX_FIGURES=1):
            doc, read = self.read(document)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(doc.warnings[-1], '그림 OCR은 문서당 1개까지라 나머지 그림은 읽지 않음')


class PptxFigureOcrTests(IngestTestCase):
    """PPTX 슬라이드의 그림은 읽기 순서대로 OCR해 넣고, 그림만 있는 슬라이드도 섹션이 되어야 한다."""

    def read(self, presentation, ocr_text='Q1 Q2', available=True):
        presentation.save(self.path('fig.pptx'))
        with patch('hyundap.ingest.ocr.is_available', return_value=available), \
                patch('hyundap.ingest.ocr.ocr_image', return_value=ocr_text) as read:
            doc = load_pptx(self.path('fig.pptx'), dict(INFO))
        return doc, read

    def test_picture_text_follows_title_and_picture_only_slide_is_kept(self):
        """제목 뒤에 그림 글자가 붙고, 그림만 있는 슬라이드도 섹션이 되며 요약 경고가 남는지 확인한다."""
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = '제목'
        slide.shapes.add_picture(io.BytesIO(png_bytes()), Inches(1), Inches(2), width=Inches(3))
        blank = presentation.slides.add_slide(presentation.slide_layouts[6])
        blank.shapes.add_picture(io.BytesIO(png_bytes()), Inches(1), Inches(1), width=Inches(3))
        doc, _ = self.read(presentation)
        self.assertEqual([s.text for s in doc.sections], ['제목\n\nQ1 Q2', 'Q1 Q2'])
        self.assertEqual(doc.warnings, ['그림 속 글자를 OCR로 읽어 본문에 붙임: 슬라이드 2장, 그림 2개'])

    def test_group_picture_is_read(self):
        """그룹 도형 안의 그림도 읽는지 확인한다."""
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = '제목'
        group = slide.shapes.add_group_shape()
        group.shapes.add_picture(io.BytesIO(png_bytes()), Inches(1), Inches(2), width=Inches(3))
        doc, _ = self.read(presentation, ocr_text='그룹 그림 글자')
        self.assertEqual(doc.sections[0].text, '제목\n\n그룹 그림 글자')

    def test_full_slide_background_picture_is_not_read(self):
        """슬라이드 전체를 덮는 배경 그림은 OCR하지 않고 읽지 않은 개체로 남는지 확인한다."""
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = '제목'
        slide.shapes.add_picture(io.BytesIO(png_bytes()), 0, 0, presentation.slide_width, presentation.slide_height)
        doc, read = self.read(presentation)
        read.assert_not_called()
        self.assertEqual(doc.warnings, ['이미지·차트 1개는 글자를 읽지 않음 (슬라이드 1)'])

    def test_picture_without_embedded_file_is_skipped(self):
        """파일이 들어 있지 않은(외부 연결) 그림은 건너뛰고 나머지를 읽는지 확인한다."""
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = '제목'
        slide.shapes.add_picture(io.BytesIO(png_bytes()), Inches(1), Inches(2), width=Inches(3))
        with patch('pptx.shapes.picture.Picture.image', new_callable=PropertyMock, side_effect=KeyError('rId9')):
            doc, read = self.read(presentation)
        read.assert_not_called()
        self.assertEqual(doc.sections[0].text, '제목')
        self.assertEqual(doc.warnings, ['이미지·차트 1개는 글자를 읽지 않음 (슬라이드 1)'])

    def test_without_ocr_engine_pictures_are_not_touched(self):
        """PaddleOCR가 없으면 그림을 읽지 않고 예전처럼 개수만 경고하는지 확인한다."""
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = '제목'
        slide.shapes.add_picture(io.BytesIO(png_bytes()), Inches(1), Inches(2), width=Inches(3))
        doc, read = self.read(presentation, available=False)
        read.assert_not_called()
        self.assertEqual(doc.warnings, ['이미지·차트 1개는 글자를 읽지 않음 (슬라이드 1)', 'OCR이 설치되어 있지 않아 이미지·스캔 쪽 1개의 글자를 읽지 않음'])


class OcrLimitTests(IngestTestCase):
    """OCR 상한 설정이 0이면 해당 OCR을 끄고 예전처럼 건너뛰어야 한다."""

    def test_page_ocr_is_off_when_page_limit_is_zero(self):
        """OCR_MAX_PAGES=0이면 글자 없는 쪽을 OCR하지 않고 예전 경고로 건너뛰는지 확인한다."""
        Path(self.path('scan.pdf')).write_bytes(minimal_pdf(''))
        with ocr_settings(OCR_MAX_PAGES=0), patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image') as read:
            doc = load_pdf(self.path('scan.pdf'), dict(INFO))
        read.assert_not_called()
        self.assertEqual(doc.warnings, ['p.1: 글자가 없어 건너뜀'])

    def test_figure_ocr_is_off_when_figure_limit_is_zero(self):
        """OCR_MAX_FIGURES=0이면 글자 있는 쪽의 그림을 OCR하지 않는지 확인한다."""
        Path(self.path('mixed.pdf')).write_bytes(pdf_with_images('Hello Elevator', [(200, 150)]))
        with ocr_settings(OCR_MAX_FIGURES=0), patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image') as read:
            doc = load_pdf(self.path('mixed.pdf'), dict(INFO))
        read.assert_not_called()
        self.assertEqual(doc.sections[0].text.strip(), 'Hello Elevator')
        self.assertEqual(doc.warnings, [])

    @PDFIUM
    def test_page_limit_warning_names_the_configured_limit(self):
        """상한을 넘는 쪽의 경고에 설정한 상한이 적히고 그 뒤 쪽은 OCR하지 않는지 확인한다."""
        pages = [Document(page_content='', metadata={'page': n}) for n in range(3)]
        Path(self.path('scan.pdf')).write_bytes(minimal_pdf(''))
        with ocr_settings(OCR_MAX_PAGES=2), patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image', return_value='본문') as read, \
                patch('hyundap.ingest.loaders.pdf_loader.render_page'), \
                patch('hyundap.ingest.loaders.pdf_loader.PyPDFLoader') as loader:
            loader.return_value.load.return_value = pages
            doc = load_pdf(self.path('scan.pdf'), dict(INFO))
        self.assertEqual(read.call_count, 2)
        self.assertEqual(doc.warnings[-1], 'p.3: 글자가 없어 건너뜀 (OCR은 문서당 2쪽까지)')


class FakeEngine:
    """PaddleOCR 대신 쓰는 가짜 엔진. 만들어질 때 받은 인자와 predict에 들어온 이미지를 기록한다."""
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        FakeEngine.instances.append(self)

    def predict(self, image):
        self.calls.append(image)
        return [{'rec_texts': ['안녕', '저신뢰'], 'rec_scores': [0.9, 0.2]}]


class OcrEngineTests(unittest.TestCase):
    """OCR 엔진 생성(스레드 수·한 번만 생성)과 서버 시작 때의 예열을 확인한다."""

    def setUp(self):
        FakeEngine.instances = []
        module = types.ModuleType('paddleocr')
        module.PaddleOCR = FakeEngine
        for patcher in (patch.dict(sys.modules, {'paddleocr': module}), patch.object(ocr, '_engine', None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_engine_uses_configured_thread_count_and_is_created_once(self):
        """OCR_THREADS가 엔진의 cpu_threads로 전달되고, 여러 번 불러도 엔진은 하나만 만들어지는지 확인한다."""
        with ocr_settings(OCR_THREADS=2):
            ocr.ocr_image('a.png')
            ocr.ocr_image('b.png')
        self.assertEqual(len(FakeEngine.instances), 1)
        kwargs = FakeEngine.instances[0].kwargs
        self.assertEqual(kwargs['cpu_threads'], 2)
        self.assertEqual((kwargs['text_detection_model_name'], kwargs['text_recognition_model_name']),
                         (ocr.DETECTION_MODEL, ocr.RECOGNITION_MODEL))

    def test_ocr_image_drops_lines_below_min_score(self):
        """신뢰도 기준 미만 줄은 버리고, 기준이 없으면 모든 줄을 돌려주는지 확인한다."""
        self.assertEqual(ocr.ocr_image('a.png'), '안녕\n저신뢰')
        self.assertEqual(ocr.ocr_image('a.png', min_score=0.5), '안녕')

    def test_warm_up_runs_engine_once_on_an_image_with_text(self):
        """예열이 엔진을 만들고 글자가 그려진 이미지로 한 번 돌리는지 확인한다."""
        with patch('hyundap.ingest.ocr.is_available', return_value=True):
            self.assertTrue(ocr.warm_up())
        image = FakeEngine.instances[0].calls[0]
        self.assertEqual(image.shape, (320, 960, 3))
        self.assertLess(int(image.min()), 128)   # 흰 바탕 위에 검은 글자가 있다

    def test_warm_up_failure_does_not_raise(self):
        """예열이 실패해도 예외를 내지 않고 False를 돌려주며, 이후 실제 OCR은 다시 시도하는지 확인한다."""
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch.object(FakeEngine, 'predict', side_effect=RuntimeError('모델 없음')), \
                self.assertLogs('hyundap.ingest.ocr', level='ERROR'):
            self.assertFalse(ocr.warm_up())

    def test_warm_up_does_nothing_without_ocr_engine(self):
        """PaddleOCR가 없으면 예열이 엔진을 만들지 않고 False인지 확인한다."""
        with patch('hyundap.ingest.ocr.is_available', return_value=False):
            self.assertFalse(ocr.warm_up())
        self.assertEqual(FakeEngine.instances, [])

    def test_start_warm_up_runs_in_background_when_enabled(self):
        """OCR_WARMUP이 켜져 있으면 백그라운드 스레드에서 예열하고, 꺼져 있으면 시작하지 않는지 확인한다."""
        with patch('hyundap.ingest.ocr.is_available', return_value=True):
            with ocr_settings(OCR_WARMUP='false'):
                self.assertIsNone(ocr.start_warm_up())
            with ocr_settings(OCR_WARMUP='true'):
                thread = ocr.start_warm_up()
                self.assertIsInstance(thread, threading.Thread)
                thread.join(timeout=10)
        self.assertEqual(len(FakeEngine.instances), 1)
        self.assertEqual(len(FakeEngine.instances[0].calls), 1)

    def test_start_warm_up_skips_when_ocr_engine_is_missing(self):
        """PaddleOCR가 없으면 켜져 있어도 스레드를 시작하지 않는지 확인한다."""
        with patch('hyundap.ingest.ocr.is_available', return_value=False), ocr_settings(OCR_WARMUP='true'):
            self.assertIsNone(ocr.start_warm_up())


class ChartTextTests(unittest.TestCase):
    """Office 차트 XML의 제목·항목·계열·값을 제목 머리글이 붙은 표로 읽는 규칙을 확인한다."""

    def test_category_chart_becomes_titled_markdown_table(self):
        """꺾은선 차트가 제목·종류 머리글과 항목×계열 표가 되고 부동소수점 잡음·빈 값이 정리되는지 확인한다."""
        result = charts.chart_to_text(chart_blob())
        self.assertEqual(result.pieces, [LINE_CHART_TEXT])
        self.assertFalse(result.truncated)

    def test_percent_number_format_is_shown_as_percent(self):
        """서식 코드에 %가 있으면 0.052가 5.2%로 표시되고 원형 차트로 읽히는지 확인한다."""
        data = CategoryChartData(number_format='0.0%')
        data.categories = ['A', 'B']
        data.add_series('점유율', (0.052, 0.4))
        text = charts.chart_to_text(chart_blob(XL_CHART_TYPE.PIE, '점유율', data=data)).pieces[0]
        self.assertEqual(text, '[차트] 점유율 (원형)\n| 구분 | 점유율 |\n| --- | --- |\n| A | 5.2% |\n| B | 40% |')

    def test_axis_titles_are_kept(self):
        """축 제목(단위 등)이 머리글 아래 줄에 남는지 확인한다."""
        data = CategoryChartData()
        data.categories = ['24.Q1']
        data.add_series('매출', (100,))
        text = charts.chart_to_text(chart_blob(XL_CHART_TYPE.COLUMN_CLUSTERED, '분기 매출', ('분기', '억원'), data)).pieces[0]
        self.assertTrue(text.startswith('[차트] 분기 매출 (막대)\n(축: 분기, 억원)\n| 구분 | 매출 |'))

    def test_multi_level_categories_are_joined_from_outer_level(self):
        """다단계 항목이 바깥 단계부터 이어 붙은 글자(2024 Q1)로 읽히는지 확인한다."""
        data = CategoryChartData()
        year = data.add_category('2024')
        year.add_sub_category('Q1')
        year.add_sub_category('Q2')
        data.add_category('2025').add_sub_category('Q1')
        data.add_series('S', (1, 2, 3))
        text = charts.chart_to_text(chart_blob(XL_CHART_TYPE.COLUMN_CLUSTERED, None, data=data)).pieces[0]
        self.assertTrue(text.startswith('[차트] (막대)\n'))
        for label in ('| 2024 Q1 | 1 |', '| 2024 Q2 | 2 |', '| 2025 Q1 | 3 |'):
            self.assertIn(label, text)

    def test_scatter_chart_lists_points_per_series(self):
        """분산형 차트가 계열·X·Y 표로 읽히는지 확인한다."""
        data = XyChartData()
        series = data.add_series('A')
        series.add_data_point(1, 2)
        series.add_data_point(3, 4.5)
        text = charts.chart_to_text(chart_blob(XL_CHART_TYPE.XY_SCATTER, '산점도', data=data)).pieces[0]
        self.assertEqual(text, '[차트] 산점도 (분산형)\n| 계열 | X | Y |\n| --- | --- | --- |\n| A | 1 | 2 |\n| A | 3 | 4.5 |')

    def test_rows_and_series_over_the_limit_are_cut_with_a_note(self):
        """항목·계열이 상한을 넘으면 앞부분만 넣고 표 끝에 잘렸다고 적는지 확인한다."""
        with patch('hyundap.ingest.loaders.charts.MAX_CHART_ROWS', 2), \
                patch('hyundap.ingest.loaders.charts.MAX_CHART_SERIES', 1):
            result = charts.chart_to_text(chart_blob())
        self.assertTrue(result.truncated)
        self.assertEqual(result.pieces[-1], '[차트] 소매판매액지수 증가율 (꺾은선)\n| 구분 | 전기비 |\n| --- | --- |\n'
                                           '| 24.Q1 | -2.2 |\n| Q2 | 0.3 |\n※ 항목 3개 중 앞 2개만 넣음, 계열 2개 중 앞 1개만 넣음')

    def test_default_limits_keep_sixty_rows_and_twelve_series(self):
        """기본 상한(항목 60·계열 12)에서 70×14 차트가 7개 이하 조각으로 잘리고 표 끝에 안내가 붙는지 확인한다."""
        data = CategoryChartData()
        data.categories = [f'm{i}' for i in range(1, 71)]
        for number in range(14):
            data.add_series(f's{number}', [float(i) for i in range(1, 71)])
        result = charts.chart_to_text(chart_blob(XL_CHART_TYPE.LINE, '큰 차트', data=data))
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.pieces), 8)
        self.assertTrue(result.pieces[-1].endswith('※ 항목 70개 중 앞 60개만 넣음, 계열 14개 중 앞 12개만 넣음'))
        joined = '\n'.join(result.pieces)
        self.assertIn('| m60 |', joined)
        self.assertNotIn('| m61 |', joined)
        self.assertNotIn('s12', joined)

    def test_chart_without_series_gives_no_pieces(self):
        """계열이 하나도 없는 차트는 오류 없이 빈 목록인지 확인한다."""
        xml = f'<c:chartSpace xmlns:c="{charts.C_NS}"><c:chart><c:plotArea/></c:chart></c:chartSpace>'.encode()
        self.assertEqual(charts.chart_to_text(xml).pieces, [])

    def test_not_a_chart_raises(self):
        """깨진 XML과 차트가 아닌 XML은 UnreadableChart인지 확인한다."""
        for source in (b'not xml', b'<a/>', f'<c:chartSpace xmlns:c="{charts.C_NS}"/>'.encode()):
            with self.subTest(source=source), self.assertRaises(charts.UnreadableChart):
                charts.chart_to_text(source)

    def test_external_entities_are_not_resolved(self):
        """XML 외부 개체(XXE)로 서버 파일 내용이 차트 글자에 섞여 들어오지 않는지 확인한다."""
        with tempfile.TemporaryDirectory() as folder:
            secret = Path(folder) / 'secret.txt'
            secret.write_text('SECRET_MARKER')
            xml = (f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "{secret.as_uri()}">]>'
                   f'<c:chartSpace xmlns:c="{charts.C_NS}"><c:chart><c:plotArea><c:barChart><c:ser>'
                   '<c:tx><c:v>&e;</c:v></c:tx><c:cat><c:strRef><c:strCache><c:ptCount val="1"/>'
                   '<c:pt idx="0"><c:v>a</c:v></c:pt></c:strCache></c:strRef></c:cat><c:val><c:numRef><c:numCache>'
                   '<c:ptCount val="1"/><c:pt idx="0"><c:v>1</c:v></c:pt></c:numCache></c:numRef></c:val>'
                   '</c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>').encode()
            result = charts.chart_to_text(xml)
        self.assertNotIn('SECRET_MARKER', '\n'.join(result.pieces))

    def test_reader_counts_results_and_caps_per_document(self):
        """ChartReader가 읽은·잘린·읽지 못한 차트를 세고 문서당 상한을 넘으면 읽지 않는지 확인한다."""
        reader = charts.ChartReader()
        self.assertEqual(reader.read(chart_blob()), [LINE_CHART_TEXT])
        self.assertEqual(reader.read(b'not xml'), [])
        self.assertEqual((reader.appended, reader.unreadable, reader.truncated), (1, 1, 0))
        self.assertEqual(reader.warnings(), ['차트 1개의 제목·항목·값을 읽어 본문에 넣음', '읽을 수 없는 차트 1개는 건너뜀'])
        with patch('hyundap.ingest.loaders.charts.MAX_CHARTS', 2):
            self.assertEqual(reader.read(chart_blob()), [])
            self.assertTrue(reader.capped)
            self.assertEqual(reader.warnings()[-1], '차트는 문서당 2개까지라 나머지 차트는 읽지 않음')


class PptxChartTests(IngestTestCase):
    """PPTX 차트 도형은 OCR 없이 차트 XML에서 읽어 읽기 순서대로 그 자리에 넣어야 한다."""

    def slide_with_chart(self, **chart_args):
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = '제목'
        data = CategoryChartData()
        data.categories = ['24.Q1', 'Q2', 'Q3']
        data.add_series('전기비', (-2.2, 0.30000000000000004, 0.7))
        data.add_series('전년동기비', (-1.6, -3.1, None))
        chart = slide.shapes.add_chart(XL_CHART_TYPE.LINE_MARKERS, Inches(1), Inches(2), Inches(5), Inches(3), data).chart
        chart.has_title = True
        chart.chart_title.text_frame.text = '소매판매액지수 증가율'
        return presentation

    def read(self, presentation, available=False):
        presentation.save(self.path('chart.pptx'))
        with patch('hyundap.ingest.ocr.is_available', return_value=available):
            return load_pptx(self.path('chart.pptx'), dict(INFO))

    def test_chart_is_read_after_the_title_without_ocr(self):
        """OCR 엔진이 없어도 차트 값이 제목 뒤에 들어가고 '글자를 읽지 않음' 경고에서 빠지는지 확인한다."""
        doc = self.read(self.slide_with_chart())
        self.assertEqual(doc.sections[0].text, '제목\n\n' + LINE_CHART_TEXT)
        self.assertEqual(doc.warnings, ['차트 1개의 제목·항목·값을 읽어 본문에 넣음'])

    def test_chart_only_slide_becomes_a_section(self):
        """차트만 있는 슬라이드도 섹션이 되는지 확인한다."""
        presentation = self.slide_with_chart()
        presentation.slides[0].shapes.title.text = ''
        doc = self.read(presentation)
        self.assertEqual(len(doc.sections), 1)
        self.assertIn(LINE_CHART_TEXT, doc.sections[0].text)

    def test_chart_only_slide_is_not_counted_as_picture_slide(self):
        """그림 1개와 차트 1개가 다른 슬라이드에 있으면 그림 요약은 슬라이드 1장·그림 1개로만 세는지 확인한다."""
        presentation = self.slide_with_chart()
        slide = presentation.slides[0]
        slide.shapes.add_picture(io.BytesIO(png_bytes()), Inches(1), Inches(5), width=Inches(2))
        presentation.slides.add_slide(presentation.slide_layouts[5]).shapes.title.text = '둘째'
        chart_only = presentation.slides.add_slide(presentation.slide_layouts[6])
        data = CategoryChartData()
        data.categories = ['A', 'B']
        data.add_series('S', (1, 2))
        chart_only.shapes.add_chart(XL_CHART_TYPE.PIE, Inches(1), Inches(1), Inches(4), Inches(3), data)
        presentation.save(self.path('chart.pptx'))
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image', return_value='그림 글자'):
            doc = load_pptx(self.path('chart.pptx'), dict(INFO))
        self.assertIn('그림 속 글자를 OCR로 읽어 본문에 붙임: 슬라이드 1장, 그림 1개', doc.warnings)

    def test_unreadable_chart_stays_in_unread_count(self):
        """차트 XML을 읽지 못하면 읽지 않은 개체로 남고 경고가 붙는지 확인한다."""
        with patch('hyundap.ingest.loaders.charts.parse_chart', side_effect=charts.UnreadableChart('깨짐')):
            doc = self.read(self.slide_with_chart())
        self.assertEqual(doc.sections[0].text, '제목')
        self.assertEqual(doc.warnings, ['이미지·차트 1개는 글자를 읽지 않음 (슬라이드 1)', '읽을 수 없는 차트 1개는 건너뜀'])


class DocxChartTests(IngestTestCase):
    """DOCX 차트 개체는 차트 XML에서 읽어 차트가 있는 자리(문단·표 칸 뒤)에 넣어야 한다."""

    def read(self, document):
        document.save(self.path('chart.docx'))
        with patch('hyundap.ingest.ocr.is_available', return_value=False):
            return load_docx(self.path('chart.docx'), dict(INFO))

    def test_chart_is_inserted_between_paragraphs_without_ocr(self):
        """차트 값이 앞뒤 문단 사이에 들어가고 '글자를 읽지 않음' 경고에서 빠지는지 확인한다."""
        document = DocxDocument()
        document.add_paragraph('앞 문단')
        add_docx_chart(document, document.add_paragraph(), chart_blob())
        document.add_paragraph('뒤 문단')
        doc = self.read(document)
        self.assertEqual(doc.sections[0].text, '앞 문단\n\n' + LINE_CHART_TEXT + '\n\n뒤 문단')
        self.assertEqual(doc.warnings, ['차트 1개의 제목·항목·값을 읽어 본문에 넣음'])

    def test_chart_in_table_cell_is_read_after_the_table(self):
        """표 칸 안의 차트가 표 글자 뒤에 들어가는지 확인한다."""
        document = DocxDocument()
        table = document.add_table(rows=1, cols=1)
        table.cell(0, 0).text = '표내용'
        add_docx_chart(document, table.cell(0, 0).paragraphs[0], chart_blob())
        doc = self.read(document)
        text = doc.sections[0].text
        self.assertLess(text.index('표내용'), text.index('[차트]'))

    def test_unreadable_chart_stays_in_unread_count(self):
        """차트 XML이 깨졌으면 읽지 않은 개체로 남고 경고가 붙으며 나머지 문서는 읽히는지 확인한다."""
        document = DocxDocument()
        document.add_paragraph('본문')
        add_docx_chart(document, document.add_paragraph(), b'not xml')
        doc = self.read(document)
        self.assertEqual(doc.sections[0].text, '본문')
        self.assertEqual(doc.warnings, ['그림·도형·차트·텍스트 상자 1개는 글자를 읽지 않음', '읽을 수 없는 차트 1개는 건너뜀'])


class ReviewFixTests(IngestTestCase):
    """PR 리뷰에서 나온 문제: 이상한 차트·그림 하나 때문에 업로드 전체가 실패하거나 오래 걸리면 안 된다."""

    @staticmethod
    def mutate(xml, change):
        """차트 XML(바이트)을 파싱해 change(root)로 고친 뒤 다시 바이트로 돌려준다."""
        root = charts.etree.fromstring(xml)
        change(root)
        return charts.etree.tostring(root)

    def test_chart_point_without_valid_index_is_skipped(self):
        """idx가 없거나 정수가 아닌 점은 건너뛰고 나머지를 읽는지 확인한다(예외로 업로드가 실패하면 안 된다)."""
        def change(root):
            points = root.findall('.//c:ser/c:val//c:pt', charts.NS)
            del points[0].attrib['idx']
            points[1].set('idx', 'zero')
        result = charts.chart_to_text(self.mutate(chart_blob(), change))
        self.assertTrue(result.pieces)

    def test_xml_comment_inside_plot_area_is_ignored(self):
        """plotArea 안의 XML 주석 때문에 차트를 못 읽는 일이 없는지 확인한다."""
        def change(root):
            root.find('.//c:plotArea', charts.NS).insert(0, charts.etree.Comment('메모'))
        self.assertEqual(charts.chart_to_text(self.mutate(chart_blob(), change)).pieces, [LINE_CHART_TEXT])

    def test_huge_point_index_does_not_make_reading_slow(self):
        """분산형 차트의 점 하나에 idx=2000000000을 넣어도 걸리는 시간이 파일 크기에만 달라지는지 확인한다."""
        data = XyChartData()
        series = data.add_series('계열')
        for x in range(3):
            series.add_data_point(x, x * 2)
        def change(root):
            root.find('.//c:yVal//c:pt', charts.NS).set('idx', '2000000000')
        started = time.perf_counter()
        result = charts.chart_to_text(self.mutate(chart_blob(XL_CHART_TYPE.XY_SCATTER, data=data), change))
        self.assertLess(time.perf_counter() - started, 2)
        self.assertTrue(result.pieces)

    def test_huge_declared_point_count_does_not_make_reading_slow(self):
        """다단계 항목 차트에 ptCount=5000000을 선언해도 실제 점 개수만큼만 읽는지 확인한다."""
        source = (f'<c:chartSpace xmlns:c="{charts.C_NS}"><c:chart><c:plotArea><c:barChart><c:ser><c:cat><c:multiLvlStrRef>'
                  '<c:multiLvlStrCache><c:ptCount val="5000000"/><c:lvl><c:pt idx="0"><c:v>1월</c:v></c:pt></c:lvl>'
                  '<c:lvl><c:pt idx="0"><c:v>2025</c:v></c:pt></c:lvl></c:multiLvlStrCache></c:multiLvlStrRef></c:cat>'
                  '<c:val><c:numRef><c:numCache><c:ptCount val="5000000"/><c:pt idx="0"><c:v>7</c:v></c:pt></c:numCache>'
                  '</c:numRef></c:val></c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>').encode()
        started = time.perf_counter()
        result = charts.chart_to_text(source)
        self.assertLess(time.perf_counter() - started, 2)
        self.assertIn('| 2025 1월 | 7 |', result.pieces[0])
        self.assertFalse(result.truncated)

    def test_unexpected_chart_error_counts_as_unreadable(self):
        """차트를 읽다 예상 밖의 예외가 나도 건너뛴 차트로 세고 업로드를 계속하는지 확인한다."""
        reader = charts.ChartReader()
        with patch.object(charts, 'chart_to_text', side_effect=TypeError('x')):
            self.assertEqual(reader.read(chart_blob()), [])
        self.assertEqual(reader.unreadable, 1)

    def test_docx_extent_without_size_is_read_as_unknown_size(self):
        """wp:extent에 cx·cy가 없는 DOCX가 OCR을 끈 상태에서도 읽히고, 켜면 크기를 모르는 그림으로 읽는지 확인한다."""
        document = DocxDocument()
        document.add_paragraph('본문')
        document.add_picture(io.BytesIO(png_bytes()), width=Inches(3))
        for extent in document.element.body.iter('{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent'):
            del extent.attrib['cx']
        document.save(self.path('extent.docx'))
        for available in (False, True):
            with self.subTest(ocr=available), patch('hyundap.ingest.ocr.is_available', return_value=available), \
                    patch('hyundap.ingest.ocr.ocr_image', return_value='그림 글자') as read:
                doc = load_docx(self.path('extent.docx'), dict(INFO))
            self.assertEqual(read.call_count, 1 if available else 0)
            self.assertIn('본문', doc.sections[0].text)

    def test_pptx_skipped_slide_warnings_are_grouped_by_reason(self):
        """같은 이유로 건너뛴 슬라이드가 여럿이면 경고를 이유마다 한 줄로 모으고, 한 장이면 예전 문구 그대로인지 확인한다."""
        presentation = Presentation()
        for _ in range(12):
            presentation.slides.add_slide(presentation.slide_layouts[6])   # 글자 없는 슬라이드
        notes_slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        notes_slide.notes_slide.notes_text_frame.text = '노트에만 글자'
        presentation.save(self.path('blank.pptx'))
        with patch('hyundap.ingest.ocr.is_available', return_value=False):
            doc = load_pptx(self.path('blank.pptx'), dict(INFO))
        self.assertEqual(doc.warnings, [
            '슬라이드 12장(슬라이드 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 외): 글자가 없어 건너뜀',
            '슬라이드 13: 본문 글자가 없어 건너뜀(발표자 노트에만 글자가 있음, 노트는 제외)'])

    def test_docx_object_with_two_pictures_counts_once_in_unread_total(self):
        """그림이 두 장 든 개체를 읽어도 읽은 개체는 1개로 세어, 읽지 않은 다른 개체가 가려지지 않는지 확인한다."""
        document = DocxDocument()
        document.add_picture(io.BytesIO(png_bytes()), width=Inches(3))
        blip = document.element.body.xpath('.//a:blip')[0]
        blip.getparent().append(parse_xml(blip.xml))   # 같은 개체 안에 그림 한 장 더(두 번째 a:blip)
        document.add_picture(io.BytesIO(png_bytes()), width=Inches(3))   # 글자를 읽지 못할 두 번째 개체
        document.save(self.path('two.docx'))
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image', side_effect=['첫째', '둘째', '']):
            doc = load_docx(self.path('two.docx'), dict(INFO))
        self.assertIn('그림·도형·차트·텍스트 상자 1개는 글자를 읽지 않음', doc.warnings)

    def test_pptx_without_slide_size_is_read(self):
        """p:sldSz가 없는 PPTX(슬라이드 크기 None)도 읽히고 그림은 쪽 대비 비율 검사 없이 읽는지 확인한다."""
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = '제목'
        slide.shapes.add_picture(io.BytesIO(png_bytes()), Inches(1), Inches(2), width=Inches(3))
        presentation._element.remove(presentation._element.sldSz)
        presentation.save(self.path('nosize.pptx'))
        for available in (False, True):
            with self.subTest(ocr=available), patch('hyundap.ingest.ocr.is_available', return_value=available), \
                    patch('hyundap.ingest.ocr.ocr_image', return_value='그림 글자'):
                doc = load_pptx(self.path('nosize.pptx'), dict(INFO))
            self.assertEqual(doc.sections[0].text, '제목\n\n그림 글자' if available else '제목')

    def test_pptx_broken_picture_is_skipped_and_not_touched_when_ocr_is_off(self):
        """그림 관계가 이미지가 아닌 부분을 가리켜도 건너뛰고, OCR이 꺼져 있으면 그림 파일을 꺼내지도 않는지 확인한다."""
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = '제목'
        slide.shapes.add_picture(io.BytesIO(png_bytes()), Inches(1), Inches(2), width=Inches(3))
        presentation.save(self.path('broken.pptx'))
        with patch('pptx.shapes.picture.Picture.image', new_callable=PropertyMock, side_effect=AttributeError('x')) as image:
            with patch('hyundap.ingest.ocr.is_available', return_value=False):
                load_pptx(self.path('broken.pptx'), dict(INFO))
            image.assert_not_called()
            with patch('hyundap.ingest.ocr.is_available', return_value=True):
                doc = load_pptx(self.path('broken.pptx'), dict(INFO))
        self.assertIn('읽을 수 없는 형식의 그림 1개는 건너뜀 (EMF 등)', doc.warnings)
        self.assertEqual(doc.sections[0].text, '제목')

    def test_figure_reader_stops_after_engine_error(self):
        """OCR 엔진이 오류를 내면 예외 없이 빈 문자열을 돌려주고 이 문서의 나머지 그림은 읽지 않으며 경고를 남기는지 확인한다."""
        self.enterContext(self.assertLogs('hyundap.ingest.loaders.figures', 'ERROR'))   # 일부러 낸 오류의 로그가 테스트 출력에 섞이지 않게 한다
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image', side_effect=RuntimeError('모델 내려받기 실패')) as read:
            reader = figures.FigureReader()
            self.assertEqual(reader.read_array(numpy.zeros((60, 60, 3))), '')
            self.assertEqual(reader.read_array(numpy.zeros((60, 60, 3))), '')   # 두 번째는 엔진을 부르지 않는다
        self.assertEqual(read.call_count, 1)
        self.assertEqual((reader.failed, reader.enabled), (1, False))
        self.assertEqual(reader.warnings(), ['OCR 엔진 오류로 이 문서의 나머지 그림은 읽지 않음 (로그 확인)'])

    @PDFIUM
    def test_scan_page_engine_error_is_skipped_with_warning(self):
        """스캔 쪽 OCR에서 엔진 오류가 나도 예외 없이 건너뛰고 경고를 남기는지 확인한다(예전의 건너뛰기 동작)."""
        self.enterContext(self.assertLogs('hyundap.ingest.loaders.pdf_loader', 'ERROR'))   # 일부러 낸 오류의 로그가 테스트 출력에 섞이지 않게 한다
        Path(self.path('scan.pdf')).write_bytes(minimal_pdf(''))
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('hyundap.ingest.ocr.ocr_image', side_effect=RuntimeError('모델 내려받기 실패')):
            doc = load_pdf(self.path('scan.pdf'), dict(INFO))
            result = self.ingest('scan.pdf')
        self.assertEqual((doc.sections, doc.warnings), ([], ['p.1: OCR 중 오류가 나 건너뜀 (이 문서의 OCR을 멈춤, 로그 확인)']))
        self.assertEqual((result.status, result.error_code), ('failed', 'EMPTY_DOCUMENT'))   # 예전처럼 글자 없는 문서로 거부

    @PDFIUM
    def test_pdf_that_cannot_be_opened_for_ocr_is_skipped_with_warning(self):
        """OCR용으로 PDF를 열지 못해도 예외 없이 OCR만 건너뛰는지 확인한다."""
        self.enterContext(self.assertLogs('hyundap.ingest.loaders.pdf_loader', 'ERROR'))   # 일부러 낸 오류의 로그가 테스트 출력에 섞이지 않게 한다
        Path(self.path('scan.pdf')).write_bytes(minimal_pdf(''))
        with patch('hyundap.ingest.ocr.is_available', return_value=True), \
                patch('pypdfium2.PdfDocument', side_effect=RuntimeError('열 수 없음')):
            doc = load_pdf(self.path('scan.pdf'), dict(INFO))
        self.assertEqual(doc.sections, [])
        self.assertIn('OCR용으로 PDF를 열지 못해 OCR을 건너뜀', doc.warnings)

    @PDFIUM
    def test_figure_crops_are_not_rendered_after_the_figure_limit(self):
        """그림 상한까지 읽은 뒤에는 쪽을 300dpi로 그리지 않고 읽지 않은 그림이 있다고만 표시하는지 확인한다."""
        class Image:
            def get_bounds(self):
                return (10, 10, 190, 150)

        class Page:
            def set_rotation(self, degrees):
                pass

            def get_cropbox(self):
                return (0, 0, 200, 200)

            def get_size(self):
                return (200, 200)

            def get_objects(self, filter):
                return [Image()]

            def render(self, scale):
                raise AssertionError('상한 뒤에는 쪽을 그리면 안 된다')

        with patch('hyundap.ingest.ocr.is_available', return_value=True):
            reader = figures.FigureReader()
        reader.count = reader.max_count
        self.assertEqual(pdf_loader.figure_crops([Page()], 0, reader), [])
        self.assertTrue(reader.capped)


class PdfTextCleanupTests(IngestTestCase):
    """PDF 쪽 글자에서 반복 머리말을 빼고 끊긴 단어를 잇는지 확인한다."""

    def load(self, texts):
        """쪽별 글자 목록을 가짜 PyPDFLoader 결과로 넣고 load_pdf의 문서를 돌려준다. OCR용 쪽 그리기는 끈다."""
        pages = []
        for number, text in enumerate(texts):
            pages.append(SimpleNamespace(page_content=text, metadata={'page': number}))
        with patch('hyundap.ingest.loaders.pdf_loader.PyPDFLoader') as loader, \
                patch('hyundap.ingest.loaders.pdf_loader.pdfium_available', return_value=False):
            loader.return_value.load.return_value = pages
            return load_pdf(self.path('a.pdf'), dict(INFO))

    def test_repeated_header_and_page_number_are_removed_from_every_page(self):
        """3쪽 이상 반복되는 머리말과 그 옆 쪽 번호가 모든 쪽에서 빠지는지 확인한다."""
        texts = [f'보고서 제목\n{n}\n{n}쪽 본문입니다' for n in range(1, 5)]
        doc = self.load(texts)
        for number, section in enumerate(doc.sections, start=1):
            self.assertEqual(section.text, f'{number}쪽 본문입니다')
        self.assertIn('반복 머리말 1줄 제거', doc.warnings)

    def test_two_pages_are_not_enough_to_be_a_header(self):
        """2쪽에만 같은 첫 줄이 있으면 머리말로 보지 않고 그대로 두는지 확인한다."""
        doc = self.load(['같은 제목\n첫 쪽 본문', '같은 제목\n둘째 쪽 본문'])
        self.assertIn('같은 제목', doc.sections[0].text)
        self.assertEqual(doc.warnings, [])

    def test_repeated_line_below_the_top_is_kept(self):
        """맨 위 3줄 밖에서 반복되는 줄(그래프 출처 등)은 지우지 않는지 확인한다."""
        texts = [f'내용 {n}\n설명 {n}\n보충 {n}\n자료: 통계청.' for n in range(4)]
        doc = self.load(texts)
        for section in doc.sections:
            self.assertIn('자료: 통계청.', section.text)

    def test_page_number_alone_is_kept_without_a_header(self):
        """머리말이 없는 쪽의 숫자 줄은 쪽 번호로 보고 지우지 않는지 확인한다."""
        doc = self.load(['1\n본문 하나', '2\n본문 둘'])
        self.assertTrue(doc.sections[0].text.startswith('1\n'))

    def test_header_only_page_is_skipped_like_an_empty_page(self):
        """머리말뿐인 쪽은 빈 쪽처럼 섹션을 만들지 않고, 다른 쪽 번호는 그대로인지 확인한다."""
        texts = ['보고서 제목\n1\n첫 쪽 본문', '보고서 제목\n2', '보고서 제목\n3\n셋째 쪽 본문', '보고서 제목\n4\n넷째 쪽 본문']
        doc = self.load(texts)
        self.assertEqual([section.location_label for section in doc.sections], ['p.1', 'p.3', 'p.4'])
        self.assertIn('p.2: 글자가 없어 건너뜀', doc.warnings)

    def test_word_split_at_line_end_is_joined(self):
        """줄 끝에서 단어 중간이 끊긴 곳(수 + 준에)이 이어 붙는지 확인한다."""
        doc = self.load(['한국 경제가 평균적인 수\n준에 도달하지 못했다.'])
        self.assertEqual(doc.sections[0].text, '한국 경제가 평균적인 수준에 도달하지 못했다.')

    def test_line_break_between_words_is_kept(self):
        """줄 끝에 공백이 있는(단어 사이에서 바뀐) 줄바꿈은 그대로 두는지 확인한다."""
        text = '한국 경제가 평균적인 \n수준에 도달하지 못했다.'
        doc = self.load([text])
        self.assertEqual(doc.sections[0].text, text)

    def test_short_heading_is_not_joined_to_next_line(self):
        """짧은 제목 줄이 다음 줄과 붙어 '시사점첫째'가 되지 않는지 확인한다."""
        text = '4. 시사점\n첫째, 경기 국면 전환기에 필연적으로 나타나는 혼란을 완화시킬 수 있는 정책 운용이 필요하다.'
        doc = self.load([text])
        self.assertEqual(doc.sections[0].text, text)


class FormatTests(IngestTestCase):
    """확장자만 바꾼 Office 파일과 매크로 파일은 형식 오류로 안내해야 한다 (#171)."""

    def test_renamed_office_files_are_unsupported_format(self):
        """pptx→.docx, xlsx 구조→.docx, docx→.pptx 모두 UNSUPPORTED_FORMAT인지 확인한다."""
        Presentation().save(self.path('src.pptx'))
        DocxDocument().save(self.path('src.docx'))
        Path(self.path('pptx_as.docx')).write_bytes(Path(self.path('src.pptx')).read_bytes())
        Path(self.path('docx_as.pptx')).write_bytes(Path(self.path('src.docx')).read_bytes())
        with zipfile.ZipFile(self.path('xlsx_as.docx'), 'w') as z:
            z.writestr('xl/workbook.xml', '<workbook/>')
        for name in ('pptx_as.docx', 'docx_as.pptx', 'xlsx_as.docx'):
            with self.subTest(name=name):
                result = self.ingest(name)
                self.assertEqual((result.status, result.error_code), ('failed', UNSUPPORTED_FORMAT))

    def ole2_file(self, name, *stream_names, padding=4096):
        """OLE2 시작값 뒤에 스트림 이름(UTF-16LE)이 든 가짜 파일을 만든다. 실제 구조까지 흉내 내지는 않는다."""
        body = bytes.fromhex('D0CF11E0A1B11AE1') + bytes(padding)
        for stream in stream_names:
            body += stream.encode('utf-16-le') + bytes(64)
        Path(self.path(name)).write_bytes(body)

    def test_ole2_office_files_are_told_apart(self):
        """OLE2 파일은 암호 문서·구형 Word/PowerPoint·그 밖으로 구분되는지 확인한다 (#145)."""
        cases = (
            ('enc.docx', ('EncryptionInfo', 'EncryptedPackage'), ENCRYPTED_FILE),
            ('enc.pptx', ('EncryptedPackage',), ENCRYPTED_FILE),
            ('old.docx', ('1Table', 'WordDocument'), LEGACY_FORMAT),
            ('old.pptx', ('PowerPoint Document',), LEGACY_FORMAT),
            ('other.docx', ('Workbook',), UNSUPPORTED_FORMAT),
            ('blank.docx', (), UNSUPPORTED_FORMAT),
        )
        for name, streams, code in cases:
            with self.subTest(name=name):
                self.ole2_file(name, *streams)
                result = self.ingest(name)
                self.assertEqual((result.status, result.error_code), ('failed', code))

    def test_ole2_stream_name_across_read_block_is_found(self):
        """읽기 블록 경계에 걸쳐 있는 스트림 이름도 찾는지 확인한다."""
        raw = 'EncryptedPackage'.encode('utf-16-le')
        for cut in (1, len(raw) // 2, len(raw) - 1):
            with self.subTest(cut=cut):
                Path(self.path('enc.docx')).write_bytes(bytes(100 - cut) + raw + bytes(100))
                found = validator.find_ole2_stream_names(self.path('enc.docx'), ('EncryptedPackage',), block_size=100)
                self.assertEqual(found, {'EncryptedPackage'})

    def test_macro_file_is_rejected(self):
        """vbaProject.bin이 든 파일이 거부되는지 확인한다."""
        DocxDocument().save(self.path('src.docx'))
        with zipfile.ZipFile(self.path('src.docx')) as source, \
                zipfile.ZipFile(self.path('macro.docx'), 'w') as target:
            for name in source.namelist():
                target.writestr(name, source.read(name))
            target.writestr('word/vbaProject.bin', b'macro')
        result = self.ingest('macro.docx')
        self.assertEqual((result.status, result.error_code), ('failed', UNSUPPORTED_FORMAT))

    def test_truncated_zip_stays_corrupted(self):
        """절단된 DOCX는 형식 오류가 아니라 CORRUPTED_FILE로 남는지 확인한다."""
        DocxDocument().save(self.path('src.docx'))
        data = Path(self.path('src.docx')).read_bytes()
        Path(self.path('cut.docx')).write_bytes(data[:len(data) // 2])
        self.assertEqual(self.ingest('cut.docx').error_code, CORRUPTED_FILE)


class DocxContentTests(IngestTestCase):
    """DOCX 본문이 조용히 사라지지 않아야 한다 (#170, #172)."""

    def test_content_control_text_is_kept(self):
        """콘텐츠 컨트롤(w:sdt) 안의 문단이 청크에 들어가는지 확인한다."""
        document = DocxDocument()
        document.add_paragraph('앞 문단')
        body = document.element.body
        sdt = OxmlElement('w:sdt')
        content = OxmlElement('w:sdtContent')
        paragraph = OxmlElement('w:p')
        run = OxmlElement('w:r')
        text = OxmlElement('w:t')
        text.text = '컨트롤 안의 본문'
        run.append(text)
        paragraph.append(run)
        content.append(paragraph)
        sdt.append(content)
        body.insert(list(body).index(body.find(qn('w:sectPr'))), sdt)
        document.save(self.path('a.docx'))
        result = self.ingest('a.docx')
        self.assertEqual(result.status, 'success')
        joined = '\n'.join(chunk.text for chunk in result.chunks)
        self.assertIn('앞 문단', joined)
        self.assertIn('컨트롤 안의 본문', joined)

    def test_nested_table_text_is_kept_with_warning(self):
        """중첩 표의 글자가 남고 경고가 기록되는지 확인한다."""
        document = DocxDocument()
        outer = document.add_table(rows=1, cols=1)
        inner = outer.cell(0, 0).add_table(rows=1, cols=2)
        inner.cell(0, 0).text = '중첩A'
        inner.cell(0, 1).text = '중첩B'
        document.save(self.path('a.docx'))
        result = self.ingest('a.docx')
        self.assertEqual(result.status, 'success')
        joined = '\n'.join(chunk.text for chunk in result.chunks)
        self.assertIn('중첩A', joined)
        self.assertIn('중첩B', joined)
        self.assertTrue(any('중첩 표' in warning for warning in result.report['warnings']))

    def test_heading_sections_keep_labels(self):
        """제목 스타일마다 섹션이 나뉘고 위치 문구가 유지되는지 확인한다."""
        document = DocxDocument()
        document.add_heading('개요', 1)
        document.add_paragraph('본문 하나')
        document.add_heading('점검', 1)
        document.add_paragraph('본문 둘')
        document.save(self.path('a.docx'))
        labels = [chunk.location_label for chunk in self.ingest('a.docx').chunks]
        self.assertEqual(labels, ['섹션 1 (개요)', '섹션 2 (점검)'])


class TableTests(unittest.TestCase):
    """표 조각이 쓰레기 청크가 되지 않고 머리글을 유지해야 한다 (#172)."""

    def test_empty_table_makes_no_piece(self):
        """모든 칸이 빈 표는 조각이 없는지 확인한다."""
        self.assertEqual(table_to_text([['', ''], ['', '']]), [])
        self.assertEqual(table_to_text([[None, None]]), [])

    def test_none_cell_is_blank(self):
        """None 셀이 문자열 'None'이 되지 않는지 확인한다."""
        self.assertNotIn('None', table_to_text([['a', None], ['b', 'c']])[0])

    def test_long_row_is_split_with_header_on_every_piece(self):
        """한 행이 max_chars를 넘어도 모든 조각이 머리글로 시작하고 길이를 넘지 않는지 확인한다."""
        pieces = table_to_text([['항목', '설명'], ['A', '긴설명 ' * 400], ['B', '짧음']], max_chars=800)
        self.assertGreater(len(pieces), 2)
        for piece in pieces:
            self.assertTrue(piece.startswith('| 항목 | 설명 |\n| --- | --- |'))
            self.assertLessEqual(len(piece), 800)
        self.assertIn('| B | 짧음 |', pieces[-1])

    @staticmethod
    def data_cells(pieces, key='a'):
        """조각의 데이터 행마다 (이스케이프 안 된 `|` 개수, 내용 칸 원문)을 돌려준다."""
        found = []
        for piece in pieces:
            for line in piece.split('\n')[2:]:
                bare = len(re.findall(r'(?<!\\)\|', line))
                match = re.match(r'^\| %s \| (.*) \|$' % key, line)
                found.append((bare, match.group(1).replace('\\|', '|') if match else None))
        return found

    def test_split_never_breaks_an_escaped_pipe(self):
        """분할 지점이 `\\|` 사이에 놓여도 행마다 열이 늘지 않고 내용이 보존되는지 확인한다. (리뷰 지적 사례)"""
        text = 'x' * 765 + '|' + 'y' * 200
        pieces = table_to_text([['항목', '내용'], ['a', text]])
        self.assertGreater(len(pieces), 1)
        cells = self.data_cells(pieces)
        self.assertTrue(all(bare == 3 for bare, _ in cells), cells)   # 2열 표의 한 줄은 `|`가 3개
        self.assertEqual(''.join(content for _, content in cells), text)

    def test_split_keeps_columns_for_every_pipe_position(self):
        """`|`의 위치를 분할 경계 주변에서 바꿔 가며 모든 조각의 열 개수와 내용이 유지되는지 확인한다."""
        for lead in range(730, 800):
            for pipes in (1, 2, 3):
                text = 'x' * lead + '|' * pipes + 'y' * 200
                with self.subTest(lead=lead, pipes=pipes):
                    cells = self.data_cells(table_to_text([['항목', '내용'], ['a', text]]))
                    self.assertTrue(all(bare == 3 for bare, _ in cells))
                    self.assertEqual(''.join(content for _, content in cells), text)


class PptxTests(IngestTestCase):
    def test_notes_only_slide_warning_is_distinct(self):
        """노트에만 글자가 있는 슬라이드의 경고가 일반 빈 슬라이드와 구분되는지 확인한다."""
        presentation = Presentation()
        presentation.slides.add_slide(presentation.slide_layouts[6]).notes_slide.notes_text_frame.text = '노트 글'
        presentation.slides.add_slide(presentation.slide_layouts[6])
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = '본문 슬라이드'
        presentation.save(self.path('a.pptx'))
        warnings = self.ingest('a.pptx').report['warnings']
        self.assertIn('발표자 노트', warnings[0])
        self.assertNotIn('발표자 노트', warnings[1])


class PptxReadingOrderTests(IngestTestCase):
    """나란히 놓은 도형은 위치가 조금 어긋나도 왼쪽부터 읽어야 한다 (#150)."""

    def read_slide(self, boxes):
        """(글, 위, 왼쪽) 목록으로 슬라이드를 만들어 청크 본문을 돌려준다. 단위는 EMU다."""
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        for text, top, left in boxes:
            box = slide.shapes.add_textbox(left, top, Inches(2), Inches(0.5))
            box.text_frame.text = text
        presentation.save(self.path('a.pptx'))
        return self.ingest('a.pptx').chunks[0].text

    def test_slightly_misaligned_boxes_read_left_to_right(self):
        """오른쪽 도형이 조금 더 위에 있어도 같은 줄이면 왼쪽 도형을 먼저 읽는지 확인한다."""
        text = self.read_slide([('오른쪽', Inches(1), Inches(5)),
                                ('왼쪽', Inches(1) + 5000, Inches(1))])
        self.assertLess(text.index('왼쪽'), text.index('오른쪽'))

    def test_rows_are_still_read_top_to_bottom(self):
        """줄이 다르면 위쪽 줄을 먼저 읽는 기존 순서가 유지되는지 확인한다."""
        text = self.read_slide([('아래', Inches(3), Inches(1)),
                                ('위 오른쪽', Inches(1), Inches(5)),
                                ('위 왼쪽', Inches(1) + 5000, Inches(1))])
        self.assertEqual([text.index(word) for word in ('위 왼쪽', '위 오른쪽', '아래')],
                         sorted(text.index(word) for word in ('위 왼쪽', '위 오른쪽', '아래')))

    def test_tolerance_does_not_chain_across_rows(self):
        """조금씩 내려가는 도형이 한 줄로 이어지지 않고 위에서 아래로 읽히는지 확인한다."""
        step = 70000   # 허용 오차(91440)보다 작은 간격으로 계단식 배치
        boxes = [(f'칸{i}', Inches(1) + step * i, Inches(5) - Inches(1) * i) for i in range(4)]
        text = self.read_slide(boxes)
        order = [text.index(f'칸{i}') for i in range(4)]
        # 줄 기준은 그 줄 첫 도형이다: 칸0·칸1이 윗줄, 칸2·칸3이 아랫줄(각 줄은 왼쪽부터).
        # 오차가 이어져 한 줄로 합쳐졌다면 가장 왼쪽인 칸3이 맨 먼저 나왔을 것이다.
        self.assertLess(order[0], order[3])
        self.assertLess(order[1], order[2])
        self.assertLess(order[1], order[0])   # 윗줄 안에서는 왼쪽(칸1)이 먼저
        self.assertLess(order[3], order[2])   # 아랫줄 안에서도 왼쪽(칸3)이 먼저


class UnreadObjectTests(IngestTestCase):
    """글자를 읽지 않은 그림·차트는 개수를 경고로 남겨야 한다 (#170)."""

    def test_docx_pictures_are_counted(self):
        """DOCX 그림 개수가 경고에 남고, 그림이 없으면 경고가 없는지 확인한다."""
        document = DocxDocument()
        document.add_paragraph('본문')
        document.add_picture(io.BytesIO(PNG))
        document.add_picture(io.BytesIO(PNG))
        document.save(self.path('pic.docx'))
        warnings = self.ingest('pic.docx').report['warnings']
        self.assertEqual(len(warnings), 1)
        self.assertIn('2개', warnings[0])
        text_only = DocxDocument()
        text_only.add_paragraph('본문만')
        text_only.save(self.path('text.docx'))
        self.assertEqual(self.ingest('text.docx').report['warnings'], [])

    def test_pptx_pictures_including_group_are_counted_once_per_document(self):
        """PPTX 그림(그룹 안 포함) 개수가 문서당 경고 1개로 모이는지 확인한다."""
        presentation = Presentation()
        for number in range(12):
            slide = presentation.slides.add_slide(presentation.slide_layouts[5])
            slide.shapes.title.text = f'제목{number}'
            slide.shapes.add_picture(io.BytesIO(PNG), Inches(1), Inches(2))
        group = presentation.slides[0].shapes.add_group_shape()
        group.shapes.add_picture(io.BytesIO(PNG), Inches(3), Inches(3))
        presentation.save(self.path('pic.pptx'))
        warnings = self.ingest('pic.pptx').report['warnings']
        self.assertEqual(len(warnings), 1)
        self.assertIn('13개', warnings[0])
        self.assertTrue(warnings[0].endswith('외)'))


class NormalizerTests(unittest.TestCase):
    def test_lone_surrogate_is_removed(self):
        """짝 없는 서로게이트가 제거되어 UTF-8로 저장할 수 있는지 확인한다."""
        cleaned = normalize_text('a\ud800b')
        self.assertEqual(cleaned, 'ab')
        cleaned.encode('utf-8')

    def test_symbols_and_digits_are_kept(self):
        """기호·숫자는 바꾸지 않는 기존 규칙이 유지되는지 확인한다."""
        self.assertEqual(normalize_text('① 50%  ABC-1234'), '① 50% ABC-1234')


class IngestErrorTests(unittest.TestCase):
    """IngestError가 코드와 message를 부모 예외에도 남겨야 한다 (#150)."""

    def test_default_message_is_passed_to_parent(self):
        """message를 주지 않아도 기본 문구가 args·문자열 표현에 남는지 확인한다."""
        error = IngestError(CORRUPTED_FILE)
        self.assertEqual(error.args, (CORRUPTED_FILE, error.message))
        self.assertIn(CORRUPTED_FILE, str(error))
        self.assertIn(error.message, str(error))

    def test_custom_message_is_kept(self):
        """직접 준 message가 속성과 문자열 표현에 모두 남는지 확인한다."""
        error = IngestError(UNSUPPORTED_FORMAT, '직접 쓴 문구')
        self.assertEqual((error.code, error.message), (UNSUPPORTED_FORMAT, '직접 쓴 문구'))
        self.assertIn('직접 쓴 문구', str(error))

    def test_error_survives_pickle(self):
        """프로세스 사이를 오갈 때(pickle) 코드와 message가 그대로인지 확인한다."""
        restored = pickle.loads(pickle.dumps(IngestError(CORRUPTED_FILE, '문구')))
        self.assertEqual((restored.code, restored.message), (CORRUPTED_FILE, '문구'))


if __name__ == '__main__':
    unittest.main()
