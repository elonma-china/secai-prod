"""Nhóm Triển khai — `GET /showcase-api/deploy/manifests`: Docker Compose + Helm chart Kubernetes.

Chứng minh tiêu chí "cung cấp Docker image + Helm chart" (sheet dòng 101). Trả nội dung file
thật: docker-compose.yml (settings.showcase_docker_compose) + toàn bộ file trong Helm chart
(settings.showcase_helm_dir). Chỉ đọc.

Endpoint này nằm trên cổng public :8131 nên mọi file đều qua `redact_secrets` trước khi trả:
giá trị của khoá có tên dạng secret bị thay bằng `<redacted>`, cấu trúc YAML giữ nguyên.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter

from ...config import get_settings

router = APIRouter(prefix="/showcase-api/deploy", tags=["showcase-deploy"])

_MAX = 60_000  # cắt file quá lớn

REDACTED = "<redacted>"
# Tên khoá/biến coi là secret: chứa PASS (PASSWORD/PASSWD), SECRET, TOKEN, KEY, AUTH, CREDENTIAL.
_SECRET_NAME = re.compile(r"pass|secret|token|key|auth|credential", re.I)
# ${VAR:-x} ${VAR-x} ${VAR:=x} ${VAR:+x} (x là literal nằm thẳng trong file) và ${VAR:?msg} (chỉ thông báo).
_REF = re.compile(r"\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)(?P<op>:?[-=+?])(?P<arg>[^{}]*)\}")
# `KEY: value` (YAML, kể cả phần tử list `- KEY: value`), giá trị rỗng hoặc bắt đầu bằng khoảng trắng.
_MAP_LINE = re.compile(r"^(?P<pre>\s*(?:-\s+)?)(?P<q>[\"']?)(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)(?P=q)(?P<sep>\s*:)(?P<val>(?:\s.*)?)$")
# `KEY=value`, `- KEY=value`, `- "KEY=value"`, `export KEY=value`.
_ENV_LINE = re.compile(r"^(?P<pre>\s*(?:-\s+)?(?:export\s+)?)(?P<q>[\"']?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<val>.*)$")
# `KEY=value` nằm giữa dòng (`command: sh -c "API_KEY=x run"`, `environment: [API_TOKEN=x]`, `?token=x`).
_INLINE_ENV = re.compile(r"(?<![A-Za-z0-9_$.-])(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<val>[^\s\"',\]}]+)")
# `{ API_KEY: x }` trong flow mapping.
_FLOW_MAP = re.compile(r"(?P<pre>[{,]\s*[\"']?)(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)(?P<mid>[\"']?\s*:\s+)"
                       r"(?P<val>\"[^\"]*\"|'[^']*'|[^,}\s][^,}]*)")
# Biến môi trường Kubernetes dạng flow: `{ name: API_KEY, value: x }`.
_FLOW_NAME_VALUE = re.compile(r"(?P<pre>\bname:\s*[\"']?(?P<name>[A-Za-z_][A-Za-z0-9_]*)[\"']?\s*,\s*value:\s*)"
                              r"(?P<val>\"[^\"]*\"|'[^']*'|[^,}\s]+)")
# Mật khẩu trong URL: scheme://user:pass@host.
_URL_CRED = re.compile(r"(?P<pre>\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/@:'\"]*:)(?P<pw>[^\s/@'\"]+)(?=@)")
# Cờ argv: --api-key x, --password=x, ["--token", "x"].
_FLAG = re.compile(r"(?P<flag>(?<![A-Za-z0-9_])--?[A-Za-z0-9_-]*(?:pass|secret|token|key|auth|credential)[A-Za-z0-9_-]*)"
                   r"(?P<sep>=|[ \t]+|[\"']\s*,\s*[\"'])(?P<val>[^\s\"',\]}]+)", re.I)
_FLAG_ONLY_ITEM = re.compile(r"^\s*-\s+[\"']?(--?[A-Za-z0-9_-]+)[\"']?\s*$")
_NAME_ITEM = re.compile(r"^\s*-?\s*name:\s*[\"']?(?P<name>[A-Za-z_][A-Za-z0-9_]*)[\"']?\s*$")
_VALUE_LINE = re.compile(r"^(?P<pre>\s*value:)(?P<val>.*)$")
_LIST_ITEM = re.compile(r"^(?P<pre>\s*-)(?P<val>\s.*)$")
_BLOCK_SCALAR = re.compile(r"[|>][0-9+-]*")
_KEEP = {"", "~", "null", "Null", "NULL", "true", "false", "True", "False", "yes", "no", "on", "off"}


def _secret_name(name: str) -> bool:
    return bool(_SECRET_NAME.search(name))


def _mask_ref(m: re.Match, force: bool = False) -> str:
    """Che phần literal của `${VAR:-x}`; giữ `${VAR:?msg}` và mặc định rỗng/tham chiếu."""
    op, arg = m["op"], m["arg"]
    if "?" in op or not arg or "$" in arg or not (force or _secret_name(m["var"])):
        return m.group(0)
    return f"${{{m['var']}{op}{REDACTED}}}"


def _mask_inner(v: str) -> str:
    """Giá trị (đã bỏ ngoặc) của khoá secret."""
    if v.strip() in _KEEP or v.startswith("*"):  # rỗng/null/bool; alias YAML trỏ tới anchor (tự bị che ở đó)
        return v
    anchor = re.match(r"&\S+\s+", v)
    if anchor:
        return anchor.group(0) + _mask_inner(v[anchor.end():])
    if v.startswith("{") and not v.startswith("{{"):  # flow mapping (`secretRef: { name: x }`): khoá secret bên trong do _FLOW_MAP che
        return v
    if v.startswith("[") and v.endswith("]"):  # flow sequence: che từng phần tử
        return "[" + ",".join(_mask_scalar(x) for x in v[1:-1].split(",")) + "]"
    if "$" in v or "{{" in v:  # tham chiếu ${VAR}/$VAR hay template Helm: giữ, chỉ che mặc định literal
        return _REF.sub(lambda m: _mask_ref(m, force=True), v)
    return REDACTED


def _mask_scalar(raw: str, quote: str = "") -> str:
    """`raw` là phần sau `:`/`=` của khoá secret, có thể kèm ngoặc và comment. Giữ khoảng trắng,
    ngoặc, comment; chỉ thay nội dung giá trị."""
    lead = raw[: len(raw) - len(raw.lstrip())]
    s = raw[len(lead):]
    if quote:  # ngoặc mở nằm trước tên khoá (`- "KEY=value"`): giá trị kết thúc ở ngoặc đóng
        end = s.find(quote)
        return lead + (_mask_inner(s) if end < 0 else _mask_inner(s[:end]) + s[end:])
    if s[:1] in ("\"", "'"):
        q = s[0]
        end = s.find(q, 1)
        if end < 0:
            return lead + q + _mask_inner(s[1:])
        return lead + q + _mask_inner(s[1:end]) + s[end:]
    c = re.search(r"\s#", s)
    val, tail = (s[: c.start()], s[c.start():]) if c else (s, "")
    core = val.rstrip()
    return lead + _mask_inner(core) + val[len(core):] + tail


def _inline(line: str) -> str:
    """Các dạng secret có thể nằm ở bất kỳ đâu trong dòng."""
    line = _REF.sub(_mask_ref, line)
    line = _URL_CRED.sub(lambda m: m.group(0) if m["pw"].startswith("$") else m["pre"] + REDACTED, line)
    line = _FLAG.sub(lambda m: m.group(0) if m["val"].startswith(("$", "{{")) else m["flag"] + m["sep"] + REDACTED, line)
    line = _FLOW_NAME_VALUE.sub(
        lambda m: m["pre"] + _mask_scalar(m["val"]) if _secret_name(m["name"]) else m.group(0), line)
    line = _FLOW_MAP.sub(
        lambda m: m["pre"] + m["key"] + m["mid"] + _mask_scalar(m["val"]) if _secret_name(m["key"]) else m.group(0), line)
    return _INLINE_ENV.sub(
        lambda m: m["key"] + "=" + _mask_inner(m["val"]) if _secret_name(m["key"]) else m.group(0), line)


def _line(body: str) -> tuple[str, str | None]:
    """Che một dòng. Trả (dòng mới, trạng thái cho dòng kế: 'block' | 'value' | 'item' | None)."""
    comment = re.match(r"^(\s*#+\s?)(.*)$", body)
    if comment:  # cấu hình bị comment vẫn có thể chứa secret thật
        return comment.group(1) + _line(comment.group(2))[0], None
    nxt = None
    m = _MAP_LINE.match(body)
    e = None if m else _ENV_LINE.match(body)
    n = _NAME_ITEM.match(body) if m and m["key"] == "name" else None
    if m and _secret_name(m["key"]):
        val = m["val"]
        core = re.split(r"\s#", val, maxsplit=1)[0].strip()
        if _BLOCK_SCALAR.fullmatch(core):
            nxt = "block"
        else:
            body = m["pre"] + m["q"] + m["key"] + m["q"] + m["sep"] + _mask_scalar(val)
    elif n and _secret_name(n["name"]):
        nxt = "value"
    elif e and _secret_name(e["key"]):
        body = e["pre"] + e["q"] + e["key"] + "=" + _mask_scalar(e["val"], e["q"])
    else:
        f = _FLAG_ONLY_ITEM.match(body)
        if f and _SECRET_NAME.search(f.group(1)):
            nxt = "item"
    return _inline(body), nxt


def redact_secrets(text: str) -> str:
    """Che giá trị secret trong compose/Helm/env trước khi trả qua endpoint public.

    Che: giá trị của khoá có tên chứa PASS/PASSWORD/SECRET/TOKEN/KEY/AUTH/CREDENTIAL (YAML
    `KEY: value`, `KEY=value`, phần tử list `- KEY=value`, block scalar, `- name: KEY` + `value:`),
    phần mặc định của `${VAR:-x}` khi VAR là tên secret (hoặc nằm trong giá trị của khoá secret),
    mật khẩu trong URL `scheme://user:pass@`, giá trị sau cờ `--api-key`/`--password`...
    Giữ nguyên: tham chiếu `${VAR}`, `${VAR:?msg}`, `$VAR`, template `{{ }}`, giá trị rỗng/null/bool,
    tên khoá, thụt lề, ngoặc, comment."""
    out: list[str] = []
    state: str | None = None
    block_indent = -1
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        eol = raw[len(body):]
        stripped = body.strip()
        indent = len(body) - len(body.lstrip())
        if state == "block":
            if not stripped:
                out.append(raw)
                continue
            if indent > block_indent:
                out.append(body[:indent] + REDACTED + eol)
                continue
            state = None
        if state == "value":
            v = _VALUE_LINE.match(body)
            if v:
                out.append(_inline(v["pre"] + _mask_scalar(v["val"])) + eol)
                state = None
                continue
        if state == "item":
            it = _LIST_ITEM.match(body)
            if it:
                out.append(_inline(it["pre"] + _mask_scalar(it["val"])) + eol)
                state = None
                continue
        if stripped:
            state = None
        new, nxt = _line(body)
        if nxt:
            state, block_indent = nxt, indent
        out.append(new + eol)
    return "".join(out)


def _read(path: Path) -> str:
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # file mount không đọc được (quyền, symlink hỏng): báo, không 500
        return f"(không đọc được {path.name}: {type(exc).__name__})"
    txt = redact_secrets(txt)
    return txt[:_MAX] + ("\n… (đã cắt bớt)" if len(txt) > _MAX else "")


@router.get("/manifests")
def manifests() -> dict:
    s = get_settings()
    out: dict = {
        "docker": {
            "image_sizes": {
                "gateway": "~2 GB",
                "serving_weights": "~24 GB (LLM + embedding + reranker)",
            },
            "compose": None,
        },
        "helm": {"chart": None, "files": []},
    }

    compose = Path(s.showcase_docker_compose)
    if compose.is_file():
        out["docker"]["compose"] = {"name": compose.name, "content": _read(compose)}

    helm_dir = Path(s.showcase_helm_dir)
    if helm_dir.is_dir():
        chart = helm_dir / "Chart.yaml"
        if chart.is_file():
            out["helm"]["chart"] = _read(chart)
        files = []
        for f in sorted(helm_dir.rglob("*")):
            if f.is_file():
                files.append({"path": str(f.relative_to(helm_dir)), "content": _read(f)})
        out["helm"]["files"] = files

    return out
