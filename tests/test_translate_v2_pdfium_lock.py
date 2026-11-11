"""PDFium is not thread-safe: page rendering from worker threads must be serialized."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from secai_gateway.translation import documents


def test_render_pdf_page_never_runs_pdfium_concurrently(monkeypatch, tmp_path):
    state = {"active": 0, "peak": 0}
    guard = threading.Lock()

    class Bitmap:
        def to_pil(self):
            from PIL import Image
            return Image.new("RGB", (4, 4), "white")

    class Page:
        def render(self, scale):
            with guard:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.05)
            with guard:
                state["active"] -= 1
            return Bitmap()

    class Doc:
        def __init__(self, _path):
            pass

        def __len__(self):
            return 8

        def __getitem__(self, _index):
            return Page()

        def close(self):
            pass

    monkeypatch.setattr(documents.pypdfium2, "PdfDocument", Doc)
    with ThreadPoolExecutor(8) as pool:
        paths = list(pool.map(lambda n: documents.render_pdf_page(tmp_path / "x.pdf", n, tmp_path), range(1, 9)))
    assert all(path.exists() for path in paths)
    assert state["peak"] == 1
