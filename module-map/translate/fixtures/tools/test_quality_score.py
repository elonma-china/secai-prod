import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import quality_score as q


class ChrF(unittest.TestCase):
    def test_identical(self):
        self.assertAlmostEqual(q.chrf("Xin chào thế giới", "Xin chào thế giới"), 100.0)

    def test_disjoint(self):
        self.assertEqual(q.chrf("abc", "xyz"), 0.0)

    def test_empty(self):
        self.assertEqual(q.chrf("", "abc"), 0.0)

    def test_known_value(self):
        # hyp="ab", ref="abc": P=(1+1)/2=1, R=(2/3+1/2)/2=7/12; F2=5PR/(4P+R)=0.63636
        self.assertAlmostEqual(q.chrf("ab", "abc"), 63.64, places=2)

    def test_whitespace_ignored(self):
        self.assertAlmostEqual(q.chrf("a b c", "abc"), 100.0)


class Gates(unittest.TestCase):
    S = {"id": "T", "kind": "file", "src": "en", "tgt": "vi", "literals": ["CVE-2024-1"], "gold": "Lỗi CVE-2024-1 trên máy chủ này.\nHãy vá ngay hôm nay."}

    def test_perfect(self):
        r = q.score_sample(self.S, self.S["gold"])
        self.assertEqual(r["state"], "ĐẠT")

    def test_missing_literal(self):
        r = q.score_sample(self.S, "Lỗi trên máy chủ này.\nHãy vá ngay hôm nay.")
        self.assertEqual(r["state"], "TRƯỢT"); self.assertFalse(r["gates"]["G5_literal"])

    def test_foreign_script(self):
        r = q.score_sample(self.S, "这是中文 CVE-2024-1 这是中文这是中文\n这是中文这是中文")
        self.assertFalse(r["gates"]["G3_script"])

    def test_dropped_lines(self):
        s = dict(self.S, gold="\n".join(f"Dòng số {i} của tài liệu" for i in range(10)))
        r = q.score_sample(s, "\n".join(f"Dòng số {i} của tài liệu" for i in range(5)))
        self.assertFalse(r["gates"]["G4_lines"])

    def test_image_wrapped_lines_ok(self):
        # ảnh: câu 1 dòng bị bố cục ảnh ngắt thành 3 dòng vẫn là 1 khối -> không báo bỏ đoạn
        s = {"id": "I", "kind": "image", "src": "zh", "tgt": "vi", "literals": [], "gold": "Khi phát hiện bất thường, đội phải cô lập máy chủ khỏi mạng nội bộ."}
        r = q.score_sample(s, "Khi phát hiện bất thường,\nđội phải cô lập\nmáy chủ khỏi mạng nội bộ.")
        self.assertTrue(r["gates"]["G4_lines"])

    def test_no_gold_not_graded(self):
        self.assertEqual(q.score_sample(dict(self.S, gold=None), "x")["state"], "CHƯA CHẤM")

    def test_gold_selfscore_all_samples(self):
        # kiểm cấu hình: dùng chính gold làm đầu ra thì mọi mẫu phải ĐẠT (cổng không tự mâu thuẫn với gold)
        for s in q.load_samples({"file", "image", "text"}):
            if s["gold"]:
                self.assertEqual(q.score_sample(s, s["gold"])["state"], "ĐẠT", s["id"])


if __name__ == "__main__":
    unittest.main()
