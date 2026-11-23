from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VM = ROOT / "stack/images/pentestcode-vm"
HOST_POC = "/home/ict/AIEngines/secai/secai-data/poc-sources"


def test_dockerfile_self_contained():
    df = (VM / "Dockerfile").read_text()
    assert "FROM oven/bun:1.3.14" in df          # không còn FROM image cục bộ
    assert "secai-pentestcode:1.3.14-tools" not in df
    # chỉ dòng sed/grep của bước viết lại ias.jsonc mới được nhắc đường dẫn host
    assert not [l for l in df.splitlines() if "/home/ict" in l and "sed -i" not in l and "! grep" not in l]
    assert "docker-cli" not in df
    assert "COPY pentestcode/ /opt/pentestcode/" in df
    assert "bun install --frozen-lockfile" in df


def test_engine_paths_rewritten_at_build_not_in_git():
    df = (VM / "Dockerfile").read_text()
    # ias.jsonc trong git giữ nguyên (prod mount live checkout); image stack sed sang đường dẫn nội bộ
    assert "sed" in df and "/srv/secai-data/exploitdb-mcp-server" in df
    assert "/home/ict/AIEngines/secai/exploitdb-mcp-server" in (ROOT / "pentestcode/.ias/ias.jsonc").read_text()


def test_prompt_poc_sources_path_configurable_default_unchanged():
    from secai_gateway.config import Settings
    assert Settings().agent_poc_sources_path == HOST_POC


def test_prompt_uses_configured_poc_sources_path(monkeypatch):
    from secai_gateway.poc.exploit_v2_prompt import build_exploit_v2_prompt
    kw = dict(cve_id="CVE-2026-1234", target="10.0.0.1:80")
    assert HOST_POC in build_exploit_v2_prompt(**kw)  # mặc định: prod không đổi
    monkeypatch.setenv("AGENT_POC_SOURCES_PATH", "/srv/secai-data/poc-sources")
    p = build_exploit_v2_prompt(**kw)
    assert "/srv/secai-data/poc-sources" in p and "/home/ict" not in p


def test_build_script_tags_by_sha():
    sh = (VM / "build.sh").read_text()
    assert "git archive" in sh and "secai-pentestcode-vm:" in sh and "latest" not in sh


def test_pip_installs_are_constrained():
    assert "-c" in (VM / "install-tools.sh").read_text() and "pip-constraints.txt" in (VM / "install-tools.sh").read_text()
    assert (VM / "pip-constraints.txt").read_text().count("==") > 5
