"""tests/test_ingest.py — ingest(검증·읽기·정리·청킹)의 오류 처리와 보존 동작 검증.

파일은 테스트 안에서 만들어 쓰므로 외부 샘플이 필요 없다.
테스트 실행 명령은 저장소 맨 위에서 python -m unittest hyundap.tests.test_ingest -v이다."""
import base64
import io
import pickle
import re
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

from docx import Document as DocxDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from pptx import Presentation
from pptx.util import Inches

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
