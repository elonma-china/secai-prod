"""Lab exploit-v2 handoff lookup for chat CVE grounding."""
from __future__ import annotations

from pathlib import Path

from secai_gateway.poc import lab_handoff as lh


def test_write_and_lookup_lab_handoff(tmp_path, monkeypatch) -> None:
    root = tmp_path / "ias"
    root.mkdir()
    monkeypatch.setattr(lh, "IAS_ROOT", root)
    monkeypatch.setattr(lh, "INDEX_DIR", root / "cve-handoff")

    out = lh.write_lab_handoff(
        cve="CVE-2026-53435",
        session_id="ses_testlab1",
        ok=True,
        stage="3.2",
        reason="Arbitrary file read via config.xml gadget",
        attempted_steps="## Tiến độ\n- Stage đạt: **3.2**",
        run_instructions="python3 exploit.py http://target",
        recommend="Đóng gói PoC",
        code="print('poc')\n" * 5,
    )
    assert out is not None
    assert out.is_file()
    assert (root / "cve-handoff" / "CVE-2026-53435.json").is_file()

    block = lh.lookup_lab_handoff("CVE-2026-53435")
    assert block is not None
    assert "Lab exploit-v2 handoff" in block
    assert "3.2" in block
    assert "Arbitrary file read" in block


def test_scan_fallback_findings_md(tmp_path, monkeypatch) -> None:
    root = tmp_path / "ias"
    sess = root / "ev2_ses_old53435"
    sess.mkdir(parents=True)
    (sess / "findings.md").write_text(
        "# CVE-2026-53435 — CONFIRMED\nSTAGE: 3.2\nGadget: hudson.Plugin$DummyImpl\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lh, "IAS_ROOT", root)
    monkeypatch.setattr(lh, "INDEX_DIR", root / "cve-handoff")

    block = lh.lookup_lab_handoff("cve-2026-53435")
    assert block is not None
    assert "hudson.Plugin" in block
    assert "CONFIRMED" in block


def test_lab_handoff_blocks_dedupes(tmp_path, monkeypatch) -> None:
    root = tmp_path / "ias"
    root.mkdir()
    monkeypatch.setattr(lh, "IAS_ROOT", root)
    monkeypatch.setattr(lh, "INDEX_DIR", root / "cve-handoff")
    lh.write_lab_handoff(
        cve="CVE-2026-53435", session_id="ses_x", ok=True, stage="3.2", reason="ok",
    )
    text = lh.lab_handoff_blocks(["CVE-2026-53435", "CVE-2026-53435"])
    assert text is not None
    assert text.count("Lab exploit-v2 handoff") == 1
