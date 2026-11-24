import hashlib
from pathlib import Path

import pytest

from secai_gateway.retrieval import bm25

SHA = "649e2341238138974f7fc014ba2c3655dc334605136791a9d1918a41fca86143"


@pytest.fixture
def recorder(monkeypatch):
    calls = []

    class Rec:
        def __init__(self, **kw):
            calls.append(kw)

    import fastembed

    monkeypatch.setattr(fastembed, "SparseTextEmbedding", Rec)
    monkeypatch.setitem(bm25._bm25_state, "sparse_model", None)
    monkeypatch.delenv("BM25_LOCAL_MODEL_DIR", raising=False)
    return calls


def test_default_uses_bundled_dir(recorder):
    bm25._load_sparse_model()
    bundled = Path(bm25.__file__).parent / "bm25_model"
    assert recorder[0]["specific_model_path"] == str(bundled)
    assert hashlib.sha256((bundled / "english.txt").read_bytes()).hexdigest() == SHA


def test_env_overrides_dir(recorder, monkeypatch, tmp_path):
    (tmp_path / "english.txt").write_text("a\n")
    monkeypatch.setenv("BM25_LOCAL_MODEL_DIR", str(tmp_path))
    bm25._load_sparse_model()
    assert recorder[0]["specific_model_path"] == str(tmp_path)


def test_missing_dir_falls_back_to_download(recorder, monkeypatch, tmp_path):
    monkeypatch.setenv("BM25_LOCAL_MODEL_DIR", str(tmp_path / "nope"))
    bm25._load_sparse_model()
    assert "specific_model_path" not in recorder[0]
