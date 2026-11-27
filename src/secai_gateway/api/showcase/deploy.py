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
# Không phải secret: đường dẫn/tên file (cùng quy tắc NOT_SECRET_NAME_RE của scripts/ops-check.py), và các
# từ chỉ chứa chuỗi con trên mà không mang secret (bỏ đi rồi mới xét phần còn lại của tên).
_PATH_NAME = re.compile(r"(?:_FILE|_PATH|_DIR|_FILES)$", re.I)
_BENIGN_WORD = re.compile(r"(?:MAX|NUM)_?TOKENS?|KEYWORDS?|AUTHORS?(?![A-Za-z])", re.I)
# ${VAR:-x} ${VAR-x} ${VAR:=x} ${VAR:+x} (x là literal nằm thẳng trong file) và ${VAR:?msg} (chỉ thông báo).
_REF = re.compile(r"\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)(?P<op>:?[-=+?])(?P<arg>[^{}]*)\}")
# Mọi tham chiếu: ${VAR…}, $VAR (tên viết hoa kiểu biến môi trường), template Helm {{ … }}.
_ANY_REF = re.compile(r"\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:?[-=+?])(?P<arg>[^{}]*))?\}"
                      r"|\$[A-Z_][A-Z0-9_]*\b|\{\{.*?\}\}")
_TPL = re.compile(r"\{\{.*?\}\}")
# Mặc định literal trong template Helm: `{{ .Values.x.password | default "lit" }}`.
_TPL_DEFAULT = re.compile(r"(?P<pre>\bdefault\s+)(?P<val>\"(?:[^\"\\]|\\.)*\"|`[^`]*`)")
_LIT, _REFMARK = "\0", "\1"  # ký hiệu tạm khi xét giá trị chỉ gồm tham chiếu
# `KEY: value` (YAML, kể cả phần tử list `- KEY: value`), giá trị rỗng hoặc bắt đầu bằng khoảng trắng.
_MAP_LINE = re.compile(r"^(?P<pre>\s*(?:-\s+)?)(?P<q>[\"']?)(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)(?P=q)(?P<sep>\s*:)(?P<val>(?:\s.*)?)$")
# `KEY=value`, `- KEY=value`, `- "KEY=value"`, `export KEY=value`.
_ENV_LINE = re.compile(r"^(?P<pre>\s*(?:-\s+)?(?:export\s+)?)(?P<q>[\"']?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<val>.*)$")
# `KEY=value` nằm giữa dòng (`command: sh -c "API_KEY=x run"`, `environment: [API_TOKEN=x]`, `?token=x`).
_INLINE_ENV = re.compile(r"(?<![A-Za-z0-9_$.-])(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<val>[^\s\"',\]}]+)")
# Chuỗi trong ngoặc kép; template `{{ … }}` bên trong có thể chứa ngoặc kép.
_DQ = r'"(?:\{\{.*?\}\}|\\.|[^"\\])*"'
# `{ API_KEY: x }` trong flow mapping.
_FLOW_MAP = re.compile(r"(?P<pre>[{,]\s*[\"']?)(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)(?P<mid>[\"']?\s*:\s+)"
                       r"(?P<val>" + _DQ + r"|'[^']*'|[^,}\s][^,}]*)")
# Biến môi trường Kubernetes dạng flow: `{ name: API_KEY, value: x }`.
_FLOW_NAME_VALUE = re.compile(r"(?P<pre>\bname:\s*[\"']?(?P<name>[A-Za-z_][A-Za-z0-9_]*)[\"']?\s*,\s*value:\s*)"
                              r"(?P<val>" + _DQ + r"|'[^']*'|[^,}\s]+)")
# Mật khẩu trong URL: scheme://user:pass@host.
_URL_CRED = re.compile(r"(?P<pre>\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/@:'\"]*:)(?P<pw>[^\s/@'\"]+)(?=@)")
# Cờ argv: --api-key x, --password=x, ["--token", "x"].
_FLAG = re.compile(r"(?P<flag>(?<![A-Za-z0-9_])--?[A-Za-z0-9_-]*(?:pass|secret|token|key|auth|credential)[A-Za-z0-9_-]*)"
                   r"(?P<sep>=|[ \t]+|[\"']\s*,\s*[\"'])(?P<val>(?:\$\{[^{}\s]*\}|[^\s\"',\]}])+)", re.I)
_FLAG_ONLY_ITEM = re.compile(r"^\s*-\s+[\"']?(--?[A-Za-z0-9_-]+)[\"']?\s*$")
_NAME_ITEM = re.compile(r"^\s*-?\s*name:\s*[\"']?(?P<name>[A-Za-z_][A-Za-z0-9_]*)[\"']?\s*$")
_VALUE_LINE = re.compile(r"^(?P<pre>\s*value:)(?P<val>.*)$")
_LIST_ITEM = re.compile(r"^(?P<pre>\s*-)(?P<val>\s.*)$")
_BLOCK_SCALAR = re.compile(r"[|>][0-9+-]*")
_KEEP = {"", "~", "null", "Null", "NULL", "true", "false", "True", "False", "yes", "no", "on", "off"}


def _secret_name(name: str) -> bool:
    return not _PATH_NAME.search(name) and bool(_SECRET_NAME.search(_BENIGN_WORD.sub("", name)))


def _only_refs(v: str, prefix_ok: bool = False) -> bool:
    """`v` chỉ gồm tham chiếu, nối bằng `:`/`/`/`@`; `prefix_ok` cho thêm tiền tố user kiểu `neo4j/`.
    Mặc định/giá trị thay thế của `${VAR:-x}` phải rỗng, là tham chiếu hoặc đã che. `$$` là `$` literal
    của compose; `Pa$w0rd` còn phần literal `Pa` nên không tính."""
    def ref(m: re.Match) -> str:
        arg = m["arg"]
        if arg and "?" not in m["op"] and arg.strip() and arg != REDACTED and not _only_refs(arg.replace(_LIT, "$$")):
            return _LIT
        return _REFMARK
    t = _ANY_REF.sub(ref, v.strip().replace("$$", _LIT))
    pre = r"(?:[A-Za-z0-9_.-]{1,32}/)?" if prefix_ok else ""
    return not t or re.fullmatch(pre + _REFMARK + "(?:[:/@]?" + _REFMARK + ")*", t) is not None


def _mask_ref(m: re.Match, force: bool = False) -> str:
    """Che phần literal của `${VAR:-x}`; giữ `${VAR:?msg}` và mặc định rỗng/tham chiếu."""
    op, arg = m["op"], m["arg"]
    if "?" in op or not arg.strip() or arg == REDACTED or _only_refs(arg) or not (force or _secret_name(m["var"])):
        return m.group(0)
    return f"${{{m['var']}{op}{REDACTED}}}"


def _mask_tpl(m: re.Match, force: bool = False) -> str:
    """Che chuỗi literal sau `default` trong template Helm có nhắc tới tên secret."""
    t = m.group(0)
    if not (force or _secret_name(t)):
        return t
    def lit(d: re.Match) -> str:
        v = d["val"]
        return d["pre"] + (v if len(v) == 2 else v[0] + REDACTED + v[-1])
    return _TPL_DEFAULT.sub(lit, t)


def _mask_inner(v: str, plain: bool = True) -> str:
    """Giá trị (đã bỏ ngoặc) của khoá secret. `plain`: scalar YAML không ngoặc dạng `KEY: value`, nơi `*`,
    `&`, `{`, `[` là alias/anchor/flow; trong ngoặc hay dạng `KEY=value` chúng là ký tự literal."""
    if v.strip() in _KEEP:  # rỗng/null/bool
        return v
    if plain:
        if v.startswith("*"):  # alias YAML trỏ tới anchor (tự bị che ở đó)
            return v
        if re.fullmatch(r"&\S+", v.strip()):  # chỉ có anchor, giá trị ở các dòng sau
            return v
        anchor = re.match(r"&\S+\s+", v)
        if anchor:
            return anchor.group(0) + _mask_inner(v[anchor.end():])
        if v.startswith("{") and not v.startswith("{{") and re.search(r":\s", v):
            return v  # flow mapping (`secretRef: { name: x }`): khoá secret bên trong do _FLOW_MAP che
        if v.startswith("[") and v.endswith("]"):  # flow sequence: che từng phần tử
            return "[" + ",".join(_mask_scalar(x) for x in v[1:-1].split(",")) + "]"
    masked = _TPL.sub(lambda m: _mask_tpl(m, True), _REF.sub(lambda m: _mask_ref(m, True), v))
    # Chỉ giữ khi giá trị chỉ còn tham chiếu (có thể kèm tiền tố `user/`); còn literal thì che cả giá trị.
    return masked if _only_refs(masked, prefix_ok=True) else REDACTED


def _close(s: str, q: str, i: int) -> int:
    """Vị trí ngoặc đóng `q` từ `i`, bỏ qua `\\"` (ngoặc kép), `''` (ngoặc đơn) và ngoặc nằm trong template
    `{{ … }}`. -1 nếu chưa đóng."""
    while i < len(s):
        if s.startswith("{{", i) and (j := s.find("}}", i + 2)) >= 0:
            i = j + 2
        elif q == '"' and s[i] == "\\":
            i += 2
        elif s[i] == q and not (q == "'" and s[i + 1:i + 2] == "'"):
            return i
        else:
            i += 2 if s[i] == q else 1
    return -1


def _mask_scalar(raw: str, quote: str = "", plain: bool = True) -> str:
    """`raw` là phần sau `:`/`=` của khoá secret, có thể kèm ngoặc và comment. Giữ khoảng trắng,
    ngoặc, comment; chỉ thay nội dung giá trị. `plain=False` cho dạng `KEY=value`."""
    lead = raw[: len(raw) - len(raw.lstrip())]
    s = raw[len(lead):]
    if quote:  # ngoặc mở nằm trước tên khoá (`- "KEY=value"`): giá trị kết thúc ở ngoặc đóng
        end = _close(s, quote, 0)
        return lead + (_mask_inner(s, False) if end < 0 else _mask_inner(s[:end], False) + s[end:])
    if s[:1] in ("\"", "'"):
        q = s[0]
        end = _close(s, q, 1)
        if end < 0:
            return lead + q + _mask_inner(s[1:], False)
        return lead + q + _mask_inner(s[1:end], False) + s[end:]
    c = re.search(r"\s#", s)
    val, tail = (s[: c.start()], s[c.start():]) if c else (s, "")
    core = val.rstrip()
    return lead + _mask_inner(core, plain) + val[len(core):] + tail


def _after(raw: str, base: int, quote: str = "", yaml_key: bool = True) -> tuple | None:
    """Trạng thái cho các dòng sau giá trị `raw` của khoá secret có cột `base`:
    ('block', base) block scalar `|`/`>`; ('nested', base, list_cùng_cột) giá trị rỗng (list, plain scalar
    xuống dòng hay mapping lồng) hoặc giá trị không ngoặc có thể nối dòng; ('quote', base, q) ngoặc chưa
    đóng."""
    s = raw.strip()
    if quote or s[:1] in ("\"", "'"):
        q = quote or s[0]
        return ("quote", base, q) if _close(s, q, 0 if quote else 1) < 0 else None
    core = re.split(r"\s#", " " + s, maxsplit=1)[0].strip()
    core = re.sub(r"^(?:[&!]\S*(?:\s+|$))+", "", core)  # bỏ anchor `&x`, tag `!!binary`
    if yaml_key and _BLOCK_SCALAR.fullmatch(core):
        return ("block", base)
    if yaml_key and not core:
        return ("nested", base, True)
    # Có giá trị trên dòng (kể cả flow `[`/`{` mở nhiều dòng): dòng sâu hơn là phần nối tiếp của nó.
    return ("nested", base, False) if core else None


def _inline(line: str) -> str:
    """Các dạng secret có thể nằm ở bất kỳ đâu trong dòng."""
    line = _REF.sub(_mask_ref, line)
    line = _TPL.sub(_mask_tpl, line)
    line = _URL_CRED.sub(lambda m: m.group(0) if _only_refs(m["pw"]) else m["pre"] + REDACTED, line)
    line = _FLAG.sub(lambda m: m.group(0) if m["val"].startswith("{{") or _only_refs(m["val"])
                     else m["flag"] + m["sep"] + REDACTED, line)
    line = _FLOW_NAME_VALUE.sub(
        lambda m: m["pre"] + _mask_scalar(m["val"]) if _secret_name(m["name"]) else m.group(0), line)
    line = _FLOW_MAP.sub(
        lambda m: m["pre"] + m["key"] + m["mid"] + _mask_scalar(m["val"]) if _secret_name(m["key"]) else m.group(0), line)
    return _INLINE_ENV.sub(
        lambda m: m["key"] + "=" + _mask_inner(m["val"], False) if _secret_name(m["key"]) else m.group(0), line)


def _line(body: str) -> tuple[str, tuple | None]:
    """Che một dòng. Trả (dòng mới, trạng thái cho dòng kế, xem `_after`; thêm ('value',) và ('item',))."""
    comment = re.match(r"^(\s*#+\s?)(.*)$", body)
    if comment:  # cấu hình bị comment vẫn có thể chứa secret thật
        return comment.group(1) + _line(comment.group(2))[0], None
    nxt = None
    m = _MAP_LINE.match(body)
    e = None if m else _ENV_LINE.match(body)
    n = _NAME_ITEM.match(body) if m and m["key"] == "name" else None
    if m and _secret_name(m["key"]):
        nxt = _after(m["val"], len(m["pre"]))
        if nxt and nxt[2:] == (True,) and "-" in m["pre"]:
            nxt = ("nested", nxt[1], False)  # `- key:` rồi `- x` cùng cột là phần tử anh em, không phải giá trị
        if not nxt or nxt[0] != "block":
            body = m["pre"] + m["q"] + m["key"] + m["q"] + m["sep"] + _mask_scalar(m["val"])
    elif n and _secret_name(n["name"]):
        nxt = ("value",)
    elif e and _secret_name(e["key"]):
        body = e["pre"] + e["q"] + e["key"] + "=" + _mask_scalar(e["val"], e["q"], plain=False)
        nxt = _after(e["val"], len(body) - len(body.lstrip()), e["q"], yaml_key=False)
    else:
        f = _FLAG_ONLY_ITEM.match(body)
        if f and _SECRET_NAME.search(f.group(1)):
            nxt = ("item",)
    return _inline(body), nxt


def redact_secrets(text: str) -> str:
    """Che giá trị secret trong compose/Helm/env trước khi trả qua endpoint public.

    Che: giá trị của khoá có tên chứa PASS/PASSWORD/SECRET/TOKEN/KEY/AUTH/CREDENTIAL (YAML
    `KEY: value`, `KEY=value`, phần tử list `- KEY=value`, block scalar, giá trị ở dòng sau khoá (list,
    plain scalar xuống dòng, ngoặc chưa đóng), `- name: KEY` + `value:`), phần mặc định của `${VAR:-x}` khi
    VAR là tên secret (hoặc nằm trong giá trị của khoá secret), mặc định `default "x"` trong template Helm,
    mật khẩu trong URL `scheme://user:pass@`, giá trị sau cờ `--api-key`/`--password`...
    Giá trị của khoá secret còn literal ngoài tham chiếu (`Pa$$w0rd`, `sk-x${SUFFIX}`) bị che cả giá trị.
    Giữ nguyên: giá trị chỉ gồm tham chiếu `${VAR}`, `${VAR:?msg}`, `$VAR`, template `{{ }}` (có thể kèm
    tiền tố `user/`), giá trị rỗng/null/bool, tên khoá, thụt lề, ngoặc, comment."""
    out: list[str] = []
    st: tuple | None = None
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        eol = raw[len(body):]
        stripped = body.strip()
        indent = len(body) - len(body.lstrip())
        kind = st[0] if st else None
        if kind == "quote":  # giá trị trong ngoặc chưa đóng: che tới ngoặc đóng
            end = _close(body, st[2], 0)
            if not stripped:
                out.append(raw)
            elif end < 0:
                out.append(body[:indent] + REDACTED + eol)
            else:
                out.append(body[:indent] + REDACTED + body[end:] + eol)
                st = None
            continue
        if kind in ("block", "nested"):
            if not stripped:
                out.append(raw)
                continue
            item = _LIST_ITEM.match(body)
            if not (indent > st[1] or (kind == "nested" and st[2] and item and indent == st[1])):
                st = None
            elif kind == "block":
                out.append(body[:indent] + REDACTED + eol)
                continue
            elif stripped.startswith("#"):
                out.append(_line(body)[0] + eol)
                continue
            elif _MAP_LINE.match(body):  # mapping lồng: từng khoá bên trong tự xét theo tên
                st = None
            elif item:  # phần tử list của khoá secret
                out.append(_inline(item["pre"] + _mask_scalar(item["val"])) + eol)
                q = _after(item["val"], indent)
                if q and q[0] == "quote":
                    st = q
                continue
            else:  # plain scalar xuống dòng (dòng chỉ có tham chiếu/template `{{- … }}` thì giữ)
                out.append(body[:indent] + _mask_inner(stripped, False) + eol)
                continue
        if kind == "value":
            v = _VALUE_LINE.match(body)
            if v:
                st = _after(v["val"], indent)
                keep = st and st[0] == "block"
                out.append(_inline(v["pre"] + (v["val"] if keep else _mask_scalar(v["val"]))) + eol)
                continue
        if kind == "item":
            it = _LIST_ITEM.match(body)
            if it:
                out.append(_inline(it["pre"] + _mask_scalar(it["val"])) + eol)
                st = None
                continue
        if stripped:
            st = None
        new, nxt = _line(body)
        if nxt:
            st = nxt
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
