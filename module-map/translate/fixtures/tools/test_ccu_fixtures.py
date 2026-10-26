"""Kiểm dataset CCU sinh thật, không gọi dịch vụ dịch/OCR."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("gen_ccu_fixtures.py")


class CcuFixturesTest(unittest.TestCase):
    def generate(self, root, formats, sizes="short", copies=1, profile="standard"):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--out", str(root), "--formats", formats,
             "--sizes", sizes, "--copies", str(copies), "--profile", profile], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((root / "manifest.json").read_text())

    def test_text_lengths_unique_content_and_literal_gold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            manifest = self.generate(root, "txt,text", "short,medium,long", 2)
            self.assertEqual(len(manifest["items"]), 12)
            hashes = set()
            for item in manifest["items"]:
                data = (root / item["path"]).read_bytes()
                text = data.decode("utf-8")
                self.assertEqual(len(text.split()), {"short": 100, "medium": 500, "long": 2000}[item["size_class"]])
                self.assertEqual(item["chars"], len(text))
                self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())
                for literal in item["preserve_literals"]:
                    self.assertIn(literal, text)
                self.assertEqual(text, (root / item["gold_path"]).read_text())
                hashes.add(item["sha256"])
            self.assertEqual(len(hashes), 12)

    def test_pdf_scan_has_no_text_layer_and_pdf_pages_match_manifest(self):
        from pypdf import PdfReader
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            manifest = self.generate(root, "pdf,pdf-scan", "medium")
            self.assertEqual(len(manifest["items"]), 3)
            for item in manifest["items"]:
                reader = PdfReader(root / item["path"])
                self.assertEqual(len(reader.pages), 5)
                self.assertEqual(item["pages"], 5)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                if item["format"] == "pdf-scan":
                    self.assertFalse(text.strip())
                    self.assertTrue(all(page.images for page in reader.pages))
                elif item["format"] == "pdf":
                    for literal in item["preserve_literals"]:
                        self.assertIn(literal, text)
                else:
                    self.assertEqual(item["format"], "pdf-mixed")
                    self.assertEqual(sum(bool(page.extract_text().strip()) for page in reader.pages), 2)
                    self.assertEqual(sum(bool(page.images) for page in reader.pages), 3)

    def test_xlsx_keeps_exact_data_rows_formulas_and_literals(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            item = self.generate(root, "xlsx", "medium")["items"][0]
            book = openpyxl.load_workbook(root / item["path"])
            sheet = book["Data"]
            self.assertEqual(sheet.max_row, 301)
            self.assertEqual(item["rows"], 300)
            self.assertEqual(len({sheet.cell(i, 2).value for i in range(2, 302)}), 300)
            self.assertEqual(book["Summary"]["B1"].value, "=SUM(Data!D2:D301)")
            values = [str(cell.value) for row in book["Info"] for cell in row if cell.value is not None]
            for literal in item["preserve_literals"]:
                self.assertIn(literal, values)

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            self.generate(root, "txt")
            before = (root / "manifest.json").read_bytes()
            result = subprocess.run([sys.executable, str(SCRIPT), "--out", str(root)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / "manifest.json").read_bytes(), before)

    def test_dense_images_include_full_800_word_source(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            manifest = self.generate(root, "png,jpg", "long")
            self.assertEqual(len(manifest["items"]), 2)
            for item in manifest["items"]:
                self.assertEqual(item["words"], 800)
                with Image.open(root / item["path"]) as img:
                    self.assertEqual(img.size, (1240, 1754))

    def test_docx_gold_includes_tables_and_all_paragraphs(self):
        from docx import Document
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            item = self.generate(root, "docx")["items"][0]
            gold = (root / item["gold_path"]).read_text()
            doc = Document(root / item["path"])
            for paragraph in doc.paragraphs:
                if paragraph.text.strip():
                    self.assertIn(paragraph.text, gold)
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        self.assertIn(cell.text, gold)

    def test_large_profile_text_lengths_and_character_limit_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            items = self.generate(root, "text,txt", "short,medium,long", profile="large")["items"]
            self.assertEqual(len(items), 6)
            for item in items:
                self.assertEqual(item["words"], {"short": 2000, "medium": 5000, "long": 10000}[item["size_class"]])
                if item["format"] == "text" and item["size_class"] == "long":
                    self.assertEqual(item["benchmark_role"], "boundary")
                    self.assertIn("TEXT_CHAR_LIMIT", item["limit_notes"])
                if item["format"] == "txt":
                    self.assertEqual(item["benchmark_role"], "load")

    def test_large_dense_image_keeps_readable_font_and_all_words(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            item = self.generate(root, "png", "long", profile="large")["items"][0]
            self.assertEqual(item["words"], 1500)
            self.assertGreaterEqual(item["font_size_px"], 24)
            with Image.open(root / item["path"]) as img:
                self.assertGreater(img.height, 1754)
                self.assertLessEqual(img.width * img.height, 20_000_000)

    def test_large_pdf_and_mixed_scan_minimum_ten_pages(self):
        from pypdf import PdfReader
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            items = self.generate(root, "pdf,pdf-scan", "short,medium", profile="large")["items"]
            for item in items:
                reader = PdfReader(root / item["path"])
                self.assertEqual(len(reader.pages), {"short": 10, "medium": 30}[item["size_class"]])
                if item["format"] == "pdf-mixed":
                    self.assertEqual(item["text_pages"], 15)
                    self.assertEqual(item["scan_pages"], 15)
                    self.assertEqual(sum(bool((p.extract_text() or '').strip()) for p in reader.pages), 15)

    def test_large_xlsx_five_thousand_rows_with_formula(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            item = self.generate(root, "xlsx", "long", profile="large")["items"][0]
            with (root / item["path"]).open("rb") as handle:
                book = openpyxl.load_workbook(handle)
                self.assertEqual(item["rows"], 5000)
                self.assertEqual(book["Data"].max_row, 5001)
                self.assertEqual(book["Summary"]["B1"].value, "=SUM(Data!D2:D5001)")
                book.close()

    def test_within_limits_profile_text_pairs_and_xlsx_cell_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            items = self.generate(root, "text,txt,xlsx", "short,medium,long", copies=2,
                                  profile="within-limits")["items"]
            for item in items:
                if item["format"] == "text":
                    self.assertLessEqual(item["chars"], 24000)
                    self.assertEqual(item["words"], {"short":200, "medium":350, "long":450}[item["size_class"]])
                    self.assertIn((item["source_lang"],item["target_lang"]),[("vi","fr"),("fr","vi")])
                    self.assertEqual(item["benchmark_role"],"load")
                elif item["format"] == "txt":
                    self.assertEqual(item["words"], {"short":2000, "medium":5000, "long":10000}[item["size_class"]])
                else:
                    self.assertLessEqual(item["cells"],20000)
                    self.assertEqual(item["rows"], {"short":1000, "medium":3000, "long":4900}[item["size_class"]])


if __name__ == "__main__":
    unittest.main()
