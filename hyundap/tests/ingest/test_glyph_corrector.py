"""깨진 글자 사전 교정 테스트. 실행: python -m unittest hyundap.tests.ingest.test_glyph_corrector"""

import unittest

from hyundap.common.schemas import CommonDocument, Section
from hyundap.ingest.glyph_corrector import correct_document, correct_text

HEADER = {k: "x" for k in ("document_id", "version_id", "company_id", "company_name", "doc_type", "file_name")}


class CorrectTextTest(unittest.TestCase):
    def test_private_use_percent(self):
        text, corrected, unknown = correct_text("점유율 35")
        self.assertEqual(text, "점유율 35%")
        self.assertEqual(sum(corrected.values()), 1)
        self.assertFalse(unknown)

    def test_ocr_percent(self):
        for raw in ("35o/o", "35 o/o", "35°/o"):
            self.assertEqual(correct_text(f"성장률 {raw} 증가")[0], "성장률 35% 증가")

    def test_ocr_percent_does_not_touch_dates_or_words(self):
        for raw in ("10/0", "2024/05/01", "and/or", "w/o", "5o/oa"):
            self.assertEqual(correct_text(raw)[0], raw)

    def test_percent_tail(self):
        self.assertEqual(correct_text("35%o 증가")[0], "35% 증가")
        self.assertEqual(correct_text("35%05")[0], "35%05")

    def test_unknown_left_as_is_and_counted(self):
        text, _, unknown = correct_text("값 (cid:12) � ")
        self.assertIn("(cid:12)", text)
        self.assertEqual(unknown["(cid:12)"], 1)
        self.assertEqual(unknown["U+FFFD"], 1)
        self.assertEqual(unknown["U+F0A1"], 1)

    def test_clean_text_unchanged(self):
        text = "정상 문장 100% ℃ ㎡"
        self.assertEqual(correct_text(text), (text, {}, {}))


class CorrectDocumentTest(unittest.TestCase):
    def test_warnings(self):
        doc = CommonDocument(
            header=HEADER,
            sections=[Section(1, "p.1", "35"), Section(2, "p.2", "(cid:7)"), Section(3, "p.3", "정상")],
            warnings=["기존 경고"],
        )
        out = correct_document(doc)
        self.assertEqual([s.text for s in out.sections], ["35%", "(cid:7)", "정상"])
        self.assertEqual(out.warnings[0], "기존 경고")
        self.assertIn("1건을 사전으로 교정", out.warnings[1])
        self.assertIn("unknown issue", out.warnings[2])
        self.assertIn("p.2", out.warnings[2])
        self.assertEqual(doc.warnings, ["기존 경고"])  # 원본 문서는 바뀌지 않는다


if __name__ == "__main__":
    unittest.main()
