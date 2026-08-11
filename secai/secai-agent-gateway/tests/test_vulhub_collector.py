"""VulhubCollector — README capture when a CVE dir has no docker-compose file, so a
code-less catalog hit still carries real reproduction text (see poc/inject.py's
_description_for / synthesize-from-README tier)."""
from __future__ import annotations

from secai_gateway.poc.collectors.vulhub import VulhubCollector


def test_no_compose_file_captures_full_readme_as_description(tmp_path) -> None:
    d = tmp_path / "someapp" / "CVE-2099-0001"
    d.mkdir(parents=True)
    (d / "README.md").write_text(
        "# Some Vuln\nAffected versions: 1.0-2.3.\n"
        "Reproduce: send a crafted request to /admin with header X-Foo: bar.\n",
        encoding="utf-8")
    poc = next(VulhubCollector(tmp_path).collect())
    assert poc.artifact_path is None  # no compose file → nothing to fetch as an artifact
    assert "Reproduce: send a crafted request" in poc.description
    assert poc.description != "Vulhub reproducible lab for CVE-2099-0001 (someapp)."


def test_compose_file_present_keeps_generic_description(tmp_path) -> None:
    # unchanged behavior: when a compose artifact exists, the README is attached
    # separately as a companion doc by backend.py — the generic sentence stays here.
    d = tmp_path / "someapp" / "CVE-2099-0002"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text("services:\n  app:\n    image: someapp:1.0\n", encoding="utf-8")
    (d / "README.md").write_text("# Some Vuln\nRun docker compose up.\n", encoding="utf-8")
    poc = next(VulhubCollector(tmp_path).collect())
    assert poc.artifact_path is not None
    assert poc.description == "Vulhub reproducible lab for CVE-2099-0002 (someapp)."


def test_no_compose_no_readme_falls_back_to_generic_description(tmp_path) -> None:
    d = tmp_path / "someapp" / "CVE-2099-0003"
    d.mkdir(parents=True)
    (d / "notes.txt").write_text("irrelevant", encoding="utf-8")
    poc = next(VulhubCollector(tmp_path).collect())
    assert poc.description == "Vulhub reproducible lab for CVE-2099-0003 (someapp)."
