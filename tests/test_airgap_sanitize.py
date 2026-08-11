"""Deterministic air-gap sanitizer: strips internet/URL/upstream-path leaks the model may
still emit in Mục 2/3 despite the prompt rules. Regression fixture is the real CVE-2026-39987
run guide a user reported (github link + nuclei path + `go install` all leaked through)."""
from __future__ import annotations

from secai_gateway.poc.inject import sanitize_airgap_runguide

# Verbatim leak lines from the reported CVE-2026-39987 answer.
LEAKY = """## 2. Cách chạy PoC
PoC này là một template Nuclei, cần dùng công cụ Nuclei để thực thi.
Cài đặt Nuclei: Đảm bảo đã cài công cụ trên máy (ví dụ: `go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest`).
Chạy template:
`nuclei -t javascript/cves/2026/CVE-2026-39987.yaml -u http://<target_ip>:<target_port>`
Sử dụng template trực tiếp từ kho GitHub:
`nuclei -t https://raw.githubusercontent.com/projectdiscovery/nuclei-templates/main/javascript/cves/2026/CVE-2026-39987.yaml -u http://<target-ip>:<port>`
Nếu đã lưu template về máy:
`nuclei -t CVE-2026-39987.yaml -u http://<target-ip>:<port>`
Kết quả: phản hồi chứa chuỗi uid=<số>(...) xác nhận RCE.
"""


def test_sanitizer_removes_all_reported_leaks() -> None:
    out = sanitize_airgap_runguide(LEAKY, "CVE-2026-39987")
    # no upstream URLs / hosts / repo template paths anywhere
    assert "github.com" not in out
    assert "githubusercontent" not in out
    assert "https://" not in out
    assert "javascript/cves/" not in out
    assert "nuclei-templates/" not in out
    # no internet installer
    assert "go install" not in out
    assert "air-gap" in out                       # replaced with the neutral note
    # the template is now the LOCAL cve file, lowercase; the uppercase form is gone
    assert "cve-2026-39987.yaml" in out
    assert "CVE-2026-39987.yaml" not in out
    # every `-t` arg (the token right after `-t`) is the local file, never a path/url
    for line in out.splitlines():
        if "-t " in line:
            arg = line.split("-t ", 1)[1].split()[0].strip("`")
            assert arg == "cve-2026-39987.yaml", arg
    # target placeholders are preserved (not a source host / no source ext)
    assert "<target_ip>" in out and "<target-ip>" in out


def test_sanitizer_preserves_curl_wget_poc_actions() -> None:
    # curl/wget can BE the PoC exploit action against the target — must NOT be neutralized.
    text = "Chạy: `curl -X POST http://<target>/api/exploit -d @payload`"
    out = sanitize_airgap_runguide(text, "CVE-2026-1")
    assert "curl -X POST" in out
    assert "air-gap" not in out


def test_sanitizer_rewrites_bare_source_url_outside_nuclei() -> None:
    text = "Tải PoC: https://github.com/acme/poc/blob/main/exploit.py rồi chạy."
    out = sanitize_airgap_runguide(text, "CVE-2026-2222")
    assert "github.com" not in out and "https://" not in out
    assert "cve-2026-2222.py" in out


def test_sanitizer_noop_when_already_clean() -> None:
    clean = "## 2. Cách chạy PoC\n`python3 cve-2026-9256.py 127.0.0.1 8080`\n"
    assert sanitize_airgap_runguide(clean, "CVE-2026-9256") == clean
