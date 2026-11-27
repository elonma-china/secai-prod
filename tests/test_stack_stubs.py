"""Stub Hy-MT2/OCR CHỈ ĐỂ TEST (stack/acceptance/stubs): kiểm bằng chính client của gateway, nên hợp đồng không lệch."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from secai_gateway.multilang.config import MultilangSettings
from secai_gateway.multilang.provider import GenerationRequest, SpecialistLlm
from secai_gateway.translation.ocr_client import recognize_image
from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.specialist import TranslationAgent
from secai_gateway.translation.types import PreparedImage, TranslationError

STUBS = Path(__file__).resolve().parents[1] / "stack/acceptance/stubs"
PNG = Path(__file__).resolve().parents[1] / "stack/acceptance/fixtures/sample-en.png"
VI = "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def stub():
    procs = []

    def start(name, model):
        port = _free_port()
        p = subprocess.Popen([sys.executable, "-I", str(STUBS / name)], cwd=STUBS, stderr=subprocess.DEVNULL,
                             env={"PATH": os.environ["PATH"], "STUB_API_KEYS": "k1,k2", "STUB_MODEL": model, "STUB_PORT": str(port)})
        procs.append(p)
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                return f"http://127.0.0.1:{port}"
            except OSError:
                time.sleep(0.1)
        raise AssertionError(f"{name} không lên")
    yield start
    for p in procs:
        p.terminate()
        p.wait(5)


def test_specialist_stub_satisfies_file_translation_client(stub):
    url = stub("specialist_stub.py", "tencent/Hy-MT2-7B")
    agent = TranslationAgent(TranslationSettings(specialist_base_url=url, specialist_api_key="k2",
                                                 specialist_model="tencent/Hy-MT2-7B"))
    out = asyncio.run(agent.translate("A remote code execution vulnerability in Apache Log4j2 on the server ⟦T0123456789abcdef01234567:0⟧.", "en", "vi"))
    assert out.startswith("[stub] ") and "lỗ hổng" in out and "máy chủ" in out
    assert "Apache Log4j2" in out and "⟦T0123456789abcdef01234567:0⟧" in out   # token kỹ thuật/placeholder giữ nguyên
    bad = TranslationAgent(TranslationSettings(specialist_base_url=url, specialist_api_key="sai", specialist_model="tencent/Hy-MT2-7B"))
    with pytest.raises(TranslationError):
        asyncio.run(bad.translate("x", "en", "vi"))


def test_specialist_stub_satisfies_live_multilang_client(stub):
    url = stub("specialist_stub.py", "tencent/Hy-MT2-7B")
    llm = SpecialistLlm(None, MultilangSettings(specialist_base_url=url, specialist_api_key="k1",
                                                specialist_model="tencent/Hy-MT2-7B"))
    r = asyncio.run(llm.generate(GenerationRequest(system="", user="The attacker", max_output_tokens=64,
                                                   source_lang="en", target_lang="vi")))
    assert r.text == "[stub] The kẻ tấn công" and r.model == "tencent/Hy-MT2-7B"


def test_ocr_stub_satisfies_ocr_client(stub, tmp_path):
    url = stub("ocr_stub.py", "Qwen/Qwen3-VL-2B-Instruct")
    s = TranslationSettings(ocr_enabled=True, ocr_base_url=url, ocr_api_key="k1", ocr_model="Qwen/Qwen3-VL-2B-Instruct")
    res = asyncio.run(recognize_image(PreparedImage(path=PNG, media_type="image/png", width=1250, height=330), settings=s))
    assert len(res.blocks) == 4 and "vulnerability in Apache Log4j2" in res.raw_text
    s2 = TranslationSettings(ocr_enabled=True, ocr_base_url=url, ocr_api_key="k1", ocr_model="khac")
    with pytest.raises(TranslationError):
        asyncio.run(recognize_image(PreparedImage(path=PNG, media_type="image/png", width=1250, height=330), settings=s2))


def test_stub_refuses_to_run_without_keys():
    r = subprocess.run([sys.executable, "-I", str(STUBS / "specialist_stub.py")], capture_output=True, text=True, timeout=10,
                       env={"PATH": os.environ["PATH"], "STUB_MODEL": "m", "STUB_PORT": str(_free_port())})
    assert r.returncode != 0 and "STUB_API_KEYS" in r.stderr


def test_stub_override_renders_with_base_compose(tmp_path):
    import shutil
    if not shutil.which("docker") or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        pytest.skip("docker compose not available")
    base = STUBS.parents[1] / "compose.yml"
    env = {"PATH": os.environ["PATH"], "GATEWAY_IMAGE": "x:1", "DATA_ROOT": str(tmp_path), "NEO4J_PASSWORD": "x",
           "TRANSLATE_V2_SPECIALIST_API_KEY": "real-secret", "TRANSLATE_V2_OCR_API_KEY": "real-secret",
           "TRANSLATE_V2_SPECIALIST_MODEL": "tencent/Hy-MT2-7B", "TRANSLATE_V2_OCR_MODEL": "ocr-m"}
    r = subprocess.run(["docker", "compose", "-f", str(base), "-f", str(STUBS / "compose.stubs.yml"), "--profile", "translation",
                        "config", "--format", "json"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    svcs = json.loads(r.stdout)["services"]
    for n, port in (("stub-specialist", "18868"), ("stub-ocr", "18083")):
        s = svcs[n]
        assert s["environment"]["STUB_PORT"] == port and "ports" not in s and s["cap_drop"] == ["ALL"]
        assert s["volumes"][0]["source"] == str(STUBS) and s["volumes"][0]["read_only"] is True
        assert s["environment"]["STUB_API_KEYS"] == "stub-test-key-not-secret"   # M10: không lấy khoá thật từ .env
    assert "CHỈ ĐỂ TEST" in (STUBS / "compose.stubs.yml").read_text()
    assert "_API_KEY:" not in (STUBS / "compose.stubs.yml").read_text().split("services:", 1)[1]
