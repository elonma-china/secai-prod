#!/usr/bin/env python3
"""secret_scan.py: tìm bí mật trước khi đẩy gói dữ liệu / bundle lên GHCR. Chỉ in luật + vị trí, không bao giờ in giá trị.

  python3 -I stack/lib/secret_scan.py [--allow FILE] [--exclude GLOB]... [--summary-json OUT] [--max-print N]
                                      [--qdrant-url URL --qdrant-collection NAME] PATH...

PATH: file hoặc thư mục (không theo symlink, symlink được đếm). Đọc dạng luồng, không giải nén ra đĩa: .tar.zst/.tzst
và .zst (zstd -dcq), .tar/.snapshot, .tar.gz/.tgz, .gz, .zip (<= 256 MiB), tar lồng (theo tên hoặc magic 'ustar').
Vị trí: đường dẫn tương đối với PATH (PATH là file thì lấy basename; nhiều PATH thì thêm PATH làm tiền tố),
member archive nối bằng '!', rồi :<dòng> (text) hoặc @<offset> (có NUL trong 8 KiB đầu). Payload Qdrant: qdrant:<coll>#<id>.
stdout: FINDING<TAB><luật><TAB><vị trí>, tối đa --max-print dòng, phần còn lại gộp thành TRUNCATED<TAB><số>.
--allow FILE: mỗi dòng '<luật|*> <glob>' (glob so với vị trí đã bỏ :dòng/@offset). Khoá Qdrant chỉ lấy từ env QDRANT_API_KEY.
--policy data: luật "mềm" (gán password, gán api_key, private key, AKIA, sk-) trong các cây corpus công khai đã duyệt (DATA_CORPUS,
cố định trong mã) chỉ được đếm (corpus_counts); ngoài các cây đó và với mọi luật cứng thì vẫn là phát hiện.
--known-secrets [?]KIND[:PATH]: nạp giá trị secret THẬT của máy này (env|compose|shell|json|raw:PATH, hoặc gh = `gh auth token`;
'?' = nguồn tuỳ chọn) và tìm khớp chính xác ở mọi nơi, kể cả corpus. Giá trị chỉ nằm trong bộ nhớ; dòng báo là
FINDING<TAB>known-secret<TAB><vị trí><TAB><nguồn#tên khoá>. Bỏ giá trị < 12 ký tự và giá trị hiển nhiên.
Mã thoát: 0 sạch, 1 có phát hiện, 2 lỗi đọc/giải nén/HTTP hoặc sai cú pháp (fail closed), 3 khớp secret thật (ưu tiên nhất).
Env cho test: SECRET_SCAN_CHUNK (byte mỗi lần đọc, tối thiểu 8192), SECRET_SCAN_ZIP_MAX, SECRET_SCAN_MAX_BYTES.
"""
import argparse
import fnmatch
import gzip
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import threading
import urllib.parse
import urllib.request
import zipfile


def _env_int(name, default, lo):
    try:
        return max(int(os.environ.get(name) or default), lo)
    except ValueError:
        return default


CHUNK = _env_int("SECRET_SCAN_CHUNK", 8 << 20, 8192)
ZIP_MAX = _env_int("SECRET_SCAN_ZIP_MAX", 256 << 20, 0)
MAX_BYTES = _env_int("SECRET_SCAN_MAX_BYTES", 64 << 30, 1)  # byte giải nén tối đa cho một PATH con (chặn bom nén)
OVERLAP = 4096  # mỗi match được nhận khi còn ít nhất chừng này byte phía sau nó trong bộ đệm
CTX = 64        # byte giữ lại trước cửa sổ quét cho lookbehind
HEAD = 8192     # có NUL trong 8 KiB đầu => nhị phân, vị trí theo @offset
MAX_DEPTH = 12  # archive lồng sâu hơn => lỗi (zip tự chứa chính nó)

# Giá trị gán: không rỗng, không mở đầu bằng biểu thức shell/template ($ % { < ( [), có thể nằm trong nháy.
_VAL = rb"""(?=["'`]?[ \t]*=[ \t]*["'`]?(?![$%{<(\[])[^\s"'`&;,)])"""
# (luật, regex, quét trên bản chữ thường). Mỗi regex mở đầu bằng chữ cố định để re tìm nhanh (~1 GB/s mỗi luật,
# một regex gộp chỉ ~30 MB/s); lookbehind đặt sau tiền tố nhưng vẫn xét ký tự đứng trước tiền tố.
RULES = (
    ("private-key", rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----", False),
    ("github-token", rb"gh(?<![A-Za-z0-9_]gh)[pousr]_[A-Za-z0-9]{36,}", False),
    ("github-token", rb"github_pat_(?<![A-Za-z0-9_]github_pat_)[A-Za-z0-9_]{22,}", False),
    ("hf-token", rb"hf_(?<![A-Za-z0-9_]hf_)[A-Za-z0-9]{30,}", False),
    ("sk-key", rb"sk-(?<![A-Za-z0-9_-]sk-)[A-Za-z0-9_-]{20,}", False),
    ("aws-access-key", rb"A(?<![A-Za-z0-9]A)[KS]IA[0-9A-Z]{16}(?![0-9A-Z])", False),
    ("password-assign", rb"passw(?<![a-z0-9]passw)(?:or)?d" + _VAL, True),
    ("api-key-assign", rb"api(?<![a-z0-9]api)[_-]?key" + _VAL, True),
)
COMPILED = tuple((r, re.compile(p), ci) for r, p, ci in RULES)
RULE_NAMES = ("env-file",) + tuple(dict.fromkeys(r for r, _, _ in RULES))

# Cây corpus công khai đã chủ dự án duyệt (2026-11-27) cho gói secai-data (--policy data). Vị trí là tiền tố CHÍNH XÁC
# (hoặc khớp đúng cả chuỗi với exact=True), không glob. Trong các cây này luật mềm chỉ được đếm; luật cứng
# (github-token, hf-token, env-file) và known-secret luôn chặn. Đổi danh sách này phải qua review và test.
DATA_CORPUS = (
    ("exploitdb", "exploitdb.tar.zst!exploitdb/", False),
    ("poc-sources", "poc-sources.tar.zst!poc-sources/", False),
    ("poc/store", "poc.tar.zst!poc/store/", False),
    ("poc/catalog.db", "poc.tar.zst!poc/catalog.db", True),
    ("exploitdb-mcp-server/node_modules", "exploitdb-mcp-server.tar.zst!exploitdb-mcp-server/node_modules/", False),
    ("qdrant payload", "qdrant:cve_kb_hybrid#", False),
    ("qdrant snapshot", "qdrant/cve_kb_hybrid.snapshot!", False),
)
SOFT_RULES = frozenset(("password-assign", "api-key-assign", "private-key", "aws-access-key", "sk-key"))
POLICIES = ("strict", "data")

# Tên khoá mang secret; giá trị "hiển nhiên" và giá trị ngắn hơn MIN_SECRET bị bỏ (chủ dự án 2026-11-27).
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASS|PWD|AUTH|CREDENTIAL|PRIVATE", re.I)
MIN_SECRET, MAX_SECRET = 12, 1024
OBVIOUS = frozenset(("true", "false", "yes", "no", "on", "off", "null", "none", "changeme", "placeholder"))
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_URL_CRED = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://([^/@\s]+)@")
KNOWN_KINDS = ("env", "compose", "shell", "json", "raw", "gh")
ENV_OK_SUFFIX = (".example", ".sample", ".template", ".dist")
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class ScanError(Exception):
    """Lỗi đọc/giải nén đã có lời giải thích sẵn."""


class Abort(Exception):
    """Dừng cả PATH con hiện tại (vượt giới hạn byte giải nén)."""


class _Info(tarfile.TarInfo):
    """tarfile mặc định coi header hỏng/thiếu giữa archive là hết archive và im lặng dừng; ở đây báo lỗi."""

    @classmethod
    def fromtarfile(cls, tf):
        try:
            return super().fromtarfile(tf)
        except tarfile.EOFHeaderError:
            raise
        except tarfile.HeaderError as e:
            raise tarfile.ReadError(f"header tar hỏng hoặc thiếu ({e})") from None


class _Reader:
    """Đọc tuần tự: trả phần đầu đã đọc (để dò magic) rồi tới luồng gốc."""

    def __init__(self, head, f):
        self.head, self.pos, self.f = head, 0, f

    def read(self, n=-1):
        if self.pos < len(self.head):
            end = len(self.head) if n is None or n < 0 else self.pos + n
            out = self.head[self.pos:end]
            self.pos += len(out)
            return out
        return self.f.read(n)


def _read_full(f, n):
    parts, got = [], 0
    while got < n:
        b = f.read(n - got)
        if not b:
            break
        parts.append(b)
        got += len(b)
    return parts[0] if len(parts) == 1 else b"".join(parts)


def _drain(f):
    while f.read(1 << 20):
        pass


def _feed(src, dst, errs):
    try:
        while True:
            b = src.read(1 << 20)
            if not b:
                break
            dst.write(b)
    except BrokenPipeError:
        pass  # zstd đã thoát; mã thoát của nó sẽ báo lỗi
    except Exception as e:  # noqa: BLE001 - lỗi đọc luồng ngoài phải về luồng chính
        errs.append(e)
    finally:
        try:
            dst.close()
        except OSError:
            pass


def _is_tar_header(head):
    if len(head) < 512 or head[257:262] != b"ustar":
        return False
    try:
        return tarfile.nti(head[148:156]) in tarfile.calc_chksums(head[:512])
    except (tarfile.HeaderError, ValueError):
        return False


def is_env_file(base):
    return base == ".env" or (base.startswith(".env.") and not base.endswith(ENV_OK_SUFFIX))


def clean(s):
    """Tên file để in: byte không phải UTF-8 thành \\xNN, ký tự điều khiển thành '?'."""
    return _CTRL.sub("?", s.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace"))


def _member_name(name):
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def _errmsg(e):
    if isinstance(e, ScanError):
        return str(e)
    if isinstance(e, OSError) and e.strerror:
        return f"{type(e).__name__}: {e.strerror}"
    return f"{type(e).__name__}: {e}"


def _globs_rx(globs):
    return re.compile("|".join(fnmatch.translate(g) for g in globs)) if globs else None


def load_allow(path):
    """'<luật|*> <glob>' mỗi dòng; '#' và dòng trống bỏ qua. Trả {luật: regex}."""
    by_rule = {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2 or (parts[0] != "*" and parts[0] not in RULE_NAMES):
                raise ScanError(f"{path}:{n}: cần '<luật|*> <glob>', luật là một trong {', '.join(RULE_NAMES)}")
            by_rule.setdefault(parts[0], []).append(parts[1].strip())
    return {r: _globs_rx(g) for r, g in by_rule.items()}


def corpus_of(loc):
    """Tên cây corpus chứa vị trí (đã bỏ :dòng/@offset), hoặc None."""
    for name, prefix, exact in DATA_CORPUS:
        if (loc == prefix) if exact else loc.startswith(prefix):
            return name
    return None


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return re.split(r"\s+#", v, 1)[0].strip()


def _keep(v):
    return MIN_SECRET <= len(v) <= MAX_SECRET and v.lower() not in OBVIOUS and not re.fullmatch(r"[0-9.:,_-]+", v)


def secret_candidates(v, secret_name=True):
    """Giá trị đáng so khớp: URL không credential bị bỏ; URL có credential cho cả URL lẫn mật khẩu."""
    v = _unquote(v)
    m = _URL_CRED.match(v)
    if _URL.match(v):
        if not m:
            return []
        user = m.group(1)
        return [x for x in (v, user.split(":", 1)[-1]) if _keep(x)]
    return [v] if secret_name and _keep(v) else []


def _src_env(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        for line in f:
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.rstrip("\r\n"))
            if m:
                yield m.group(1), secret_candidates(m.group(2), bool(SECRET_NAME.search(m.group(1))))


def _src_compose(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        for line in f:
            t = line.strip()
            if t.startswith("#"):
                continue
            for var, default in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*):?-([^}]*)\}", t):
                if SECRET_NAME.search(var):
                    yield var, secret_candidates(default)
            m = re.match(r"-?\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.+)$", t)
            if m and SECRET_NAME.search(m.group(1)) and "${" not in m.group(2):
                v = _unquote(m.group(2))
                if m.group(1).upper().endswith("AUTH") and "/" in v:   # NEO4J_AUTH: user/password
                    v = v.split("/", 1)[1]
                yield m.group(1), secret_candidates(v)


def _src_shell(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()
    for m in re.finditer(r"(?:^|[\s;])(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|[^\s;]+)", text):
        if SECRET_NAME.search(m.group(1)) and "$" not in m.group(2):
            yield m.group(1), secret_candidates(m.group(2))
    for m in re.finditer(r"(?i)(api[-_]?key|x-api-key|authorization|token)\s*:\s*(?:bearer\s+)?([A-Za-z0-9._~+/=-]{12,})", text):
        yield m.group(1), secret_candidates(m.group(2))


def _src_json(pattern):
    import glob
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue   # tệp hồ sơ hỏng không chứa được secret đọc được
        todo = [doc]
        while todo:
            o = todo.pop()
            if isinstance(o, dict):
                env = o.get("environment")
                items = env.items() if isinstance(env, dict) else \
                    [(x.split("=", 1) + [""])[:2] for x in env if isinstance(x, str)] if isinstance(env, list) else []
                for k, v in items:
                    if isinstance(v, str) and SECRET_NAME.search(str(k)):
                        yield f"{os.path.basename(os.path.dirname(path))}/{os.path.basename(path)}#{k}", secret_candidates(v)
                todo.extend(o.values())
            elif isinstance(o, list):
                todo.extend(o)


def load_known(specs, err=sys.stderr):
    """[(bytes, nhãn)] từ các nguồn; không bao giờ in giá trị. Nguồn bắt buộc thiếu/lỗi => ScanError."""
    found, counts = {}, []
    for spec in specs:
        optional = spec.startswith("?")
        kind, _, path = spec.lstrip("?").partition(":")
        if kind not in KNOWN_KINDS or (kind != "gh" and not path):
            raise ScanError(f"--known-secrets '{spec}': cần [?]KIND:PATH với KIND trong {', '.join(KNOWN_KINDS)} (hoặc gh)")
        label = "gh auth token" if kind == "gh" else path
        try:
            if kind == "gh":
                r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
                if r.returncode != 0 or not r.stdout.strip():
                    raise FileNotFoundError("gh auth token không trả token")
                pairs = [("", secret_candidates(r.stdout.strip(), True))]
            elif kind == "raw":
                with open(path, encoding="utf-8", errors="surrogateescape") as f:
                    pairs = [("", secret_candidates(f.read().strip(), True))]
            elif kind == "json":
                pairs = list(_src_json(path))
            else:
                pairs = list({"env": _src_env, "compose": _src_compose, "shell": _src_shell}[kind](path))
        except FileNotFoundError as e:
            if optional:
                counts.append(f"{label}=không có")
                continue
            raise ScanError(f"nguồn secret {label}: {_errmsg(e)}") from None
        except (OSError, subprocess.SubprocessError, UnicodeError) as e:
            raise ScanError(f"nguồn secret {label}: {_errmsg(e)}") from None
        n = 0
        for key, vals in pairs:
            lab = f"json:{key}" if kind == "json" else (f"{label}#{key}" if key else label)
            for v in vals:
                b = v.encode("utf-8", "surrogateescape")
                if b not in found:
                    found[b] = lab
                    n += 1
        counts.append(f"{label}={n}")
    # Payload Qdrant được quét ở dạng JSON: thêm biến thể đã escape nếu khác.
    for b, lab in list(found.items()):
        esc = json.dumps(b.decode("utf-8", "surrogateescape"), ensure_ascii=False)[1:-1].encode("utf-8", "surrogateescape")
        if esc != b and esc not in found:
            found[esc] = lab
    print("secret_scan: secret thật đã nạp (chỉ đếm): " + (" ".join(counts) or "-"), file=err)
    return list(found.items())


class Scanner:
    def __init__(self, allow=None, excludes=(), max_print=200, out=sys.stdout, err=sys.stderr, chunk=CHUNK,
                 policy="strict", known=()):
        self.policy = policy
        self.known = list(known)
        self.corpus_counts = {}
        self.known_hits = 0
        self.chunk = chunk
        self.allow = allow or {}
        self.excl = _globs_rx(list(excludes))
        self.max_print = max_print
        self.out, self.err = out, err
        self.findings = []
        self.by_rule, self.by_top = {}, {}
        self.allowed = self.files = self.bytes = self.symlinks = self.special = self.points = 0
        self.errors = []
        self.not_archives = []
        self.budget = 0

    # ---------- kết quả ----------
    def found(self, rule, loc, suf="", source=None):
        if rule == "known-secret":   # không bao giờ được phép/hạ cấp
            self.known_hits += 1
            self._record(rule, loc, suf, source)
            return
        if self.policy == "data" and rule in SOFT_RULES:
            tree = corpus_of(loc)
            if tree is not None:
                c = self.corpus_counts.setdefault(tree, {})
                c[rule] = c.get(rule, 0) + 1
                return
        for key in (rule, "*"):
            rx = self.allow.get(key)
            if rx is not None and rx.match(loc):
                self.allowed += 1
                return
        self._record(rule, loc, suf, source)

    def _record(self, rule, loc, suf, source):
        full = loc + suf
        self.findings.append((rule, full, source))
        self.by_rule[rule] = self.by_rule.get(rule, 0) + 1
        top = loc.split("#", 1)[0] if loc.startswith("qdrant:") else re.split(r"[/!]", loc, 1)[0]
        self.by_top[top] = self.by_top.get(top, 0) + 1
        if rule == "known-secret" or len(self.findings) <= self.max_print:
            print(f"FINDING\t{rule}\t{full}" + (f"\t{source}" if source else ""), file=self.out)

    def error(self, loc, e):
        msg = _errmsg(e) if isinstance(e, BaseException) else str(e)
        msg = clean(msg)[:300]
        self.errors.append({"location": loc, "error": msg})
        if len(self.errors) <= 100:
            print(f"secret_scan: LỖI {loc}: {msg}", file=self.err)

    def _excluded(self, s):
        return self.excl is not None and self.excl.match(s) is not None

    def member_excluded(self, name):
        if self.excl is None:
            return False
        path = ""
        for p in name.split("/"):
            if not p or p == ".":
                continue
            path = f"{path}/{p}" if path else p
            if self._excluded(p) or self._excluded(path):
                return True
        return False

    # ---------- nội dung ----------
    def scan_data(self, loc, head, read, eof, suffix=True):
        """Quét theo khúc CHUNK, giữ lại OVERLAP byte cuối để match nằm vắt qua ranh giới chỉ được nhận đúng một lần."""
        self.files += 1
        self.bytes += len(head)
        binary = b"\0" in head[:HEAD]
        buf, base, win = head, 0, 0          # base: offset tuyệt đối của buf[0]; win: đầu cửa sổ nhận match
        last = [0] * len(COMPILED)           # offset tuyệt đối cuối match trước, theo từng regex
        nl = nl_abs = 0                      # nl = số '\n' trước offset tuyệt đối nl_abs
        seen = set()
        while True:
            if not eof:
                data = read(self.chunk)
                if data:
                    self.bytes += len(data)
                    self.budget += len(data)
                    if self.budget > MAX_BYTES:
                        raise Abort(f"vượt {MAX_BYTES} byte sau giải nén (bom nén?)")
                    buf += data
                else:
                    eof = True
            limit = len(buf) if eof else len(buf) - OVERLAP
            if limit > win:
                hits, low = [], None
                for i, (rule, rx, ci) in enumerate(COMPILED):
                    if ci and low is None:
                        low = buf.lower()
                    for m in rx.finditer(low if ci else buf, max(win, last[i] - base)):
                        a = m.start()
                        if a >= limit:
                            break
                        last[i] = base + m.end()
                        hits.append((a, rule))
                for v, lab in self.known:
                    i = buf.find(v, win)
                    while i != -1 and i < limit:
                        hits.append((i, "known-secret", lab))
                        i = buf.find(v, i + 1)
                hits = [h if len(h) == 3 else (h[0], h[1], None) for h in hits]
                hits.sort(key=lambda h: (h[0], h[1]))
                for a, rule, lab in hits:
                    if not suffix:
                        suf = ""
                    elif binary:
                        suf = f"@{base + a}"
                    else:
                        nl += buf.count(b"\n", nl_abs - base, a)
                        nl_abs = base + a
                        suf = f":{nl + 1}"
                    if (rule, suf, lab) not in seen:
                        seen.add((rule, suf, lab))
                        self.found(rule, loc, suf, lab)
                if not eof:
                    cut = limit - CTX
                    if not binary and nl_abs < base + cut:
                        nl += buf.count(b"\n", nl_abs - base, cut)
                        nl_abs = base + cut
                    buf, base, win = buf[cut:], base + cut, CTX
            if eof:
                return

    def sniff(self, loc, f, size, depth):
        n = self.chunk if size is None else min(size, self.chunk)
        head = _read_full(f, n)
        self.budget += len(head)
        if self.budget > MAX_BYTES:
            raise Abort(f"vượt {MAX_BYTES} byte sau giải nén (bom nén?)")
        if _is_tar_header(head):
            self.tar(loc, _Reader(head, f), depth)
            return
        eof = len(head) < n or (size is not None and len(head) >= size)
        self.scan_data(loc, head, f.read, eof)

    def scan_obj(self, loc, name, f, size, depth, path=None):
        """Một file/member: archive (theo tên hoặc magic ustar) thì đi vào, còn lại quét nội dung."""
        if depth > MAX_DEPTH:
            raise ScanError(f"archive lồng quá {MAX_DEPTH} tầng")
        low = name.lower()
        if low.endswith((".tar.zst", ".tzst")):
            self.zstd(loc, f, path, depth, tar=True)
        elif low.endswith(".zst"):
            self.zstd(loc, f, path, depth, tar=False)
        elif low.endswith((".tar", ".snapshot")):
            self.tar(loc, f, depth)
        elif low.endswith((".gz", ".tgz", ".zip")):
            # Cây nguồn bên thứ ba có file text mang đuôi .zip/.gz: không có magic thì quét như file thường.
            # Có magic mà đọc hỏng vẫn là lỗi. .zst/.tar/.snapshot (do pack.sh tạo) luôn nghiêm.
            zipname = low.endswith(".zip")
            head = _read_full(f, 4 if zipname else 2)
            magic_ok = head in ZIP_MAGIC if zipname else head == b"\x1f\x8b"
            if not magic_ok:
                self.not_archives.append(loc)
                print(f"secret_scan: CHÚ Ý {loc}: tên như archive nhưng không đúng định dạng, quét như file thường",
                      file=self.err)
                self.sniff(loc, _Reader(head, f), size, depth)
            elif zipname:
                seekable = path is not None
                if seekable:
                    f.seek(0)
                self.zip(loc, f if seekable else _Reader(head, f), size, depth, seekable=seekable)
            elif low.endswith((".tar.gz", ".tgz")):
                self.tar(loc, gzip.GzipFile(fileobj=_Reader(head, f), mode="rb"), depth)
            else:
                g = gzip.GzipFile(fileobj=_Reader(head, f), mode="rb")
                self.sniff(loc, g, None, depth)
                _drain(g)
        else:
            self.sniff(loc, f, size, depth)

    def member(self, loc, name, opener, size, depth):
        base = name.rsplit("/", 1)[-1]
        if is_env_file(base):
            self.found("env-file", loc)
        try:
            with opener() as f:
                self.scan_obj(loc, base, f, size, depth)
        except Abort:
            raise
        except Exception as e:  # noqa: BLE001 - mọi lỗi đọc là lỗi quét (fail closed)
            self.error(loc, e)

    # ---------- archive ----------
    def tar(self, loc, f, depth):
        tf = tarfile.open(fileobj=f, mode="r|", tarinfo=_Info)
        while True:
            m = tf.next()
            if m is None:
                break
            tf.members.clear()  # 'r|' giữ mọi TarInfo: bỏ đi để 500k member không ngốn RAM
            if m.issym():
                self.symlinks += 1
                continue
            name = _member_name(m.name)
            if not m.isreg() or self.member_excluded(name):
                continue
            self.member(f"{loc}!{clean(name)}", name, lambda m=m: tf.extractfile(m), m.size, depth + 1)
        _drain(f)  # đọc nốt để zstd/gzip kiểm tra được phần đuôi

    def zip(self, loc, f, size, depth, seekable):
        if size is not None and size > ZIP_MAX:
            raise ScanError(f"zip lớn hơn {ZIP_MAX} byte, không mở")
        if not seekable:
            data = _read_full(f, ZIP_MAX + 1)
            if len(data) > ZIP_MAX:
                raise ScanError(f"zip lớn hơn {ZIP_MAX} byte, không mở")
            f = io.BytesIO(data)
        with zipfile.ZipFile(f) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    self.symlinks += 1
                    continue
                name = _member_name(info.filename)
                if self.member_excluded(name):
                    continue
                mloc = f"{loc}!{clean(name)}"
                if info.flag_bits & 0x1:
                    if is_env_file(name.rsplit("/", 1)[-1]):
                        self.found("env-file", mloc)
                    self.error(mloc, "member zip có mật khẩu, không đọc được")
                    continue
                self.member(mloc, name, lambda info=info: zf.open(info), info.file_size, depth + 1)

    def zstd(self, loc, f, path, depth, tar):
        args = ["zstd", "-dcq", "--", path] if path else ["zstd", "-dcq"]
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL if path else subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as e:
            raise ScanError(f"không chạy được zstd ({e.strerror})") from None
        errs, th, ok = [], None, False
        if not path:
            th = threading.Thread(target=_feed, args=(f, proc.stdin, errs), daemon=True)
            th.start()
        try:
            if tar:
                self.tar(loc, proc.stdout, depth)
            else:
                self.sniff(loc, proc.stdout, None, depth)
            _drain(proc.stdout)
            ok = True
        finally:
            if not ok:
                proc.kill()
            proc.stdout.close()
            if th is not None:
                th.join()
            rc = proc.wait()
        if errs:
            raise errs[0]
        if rc != 0:
            raise ScanError(f"zstd -dcq thất bại (rc={rc})")

    # ---------- đĩa ----------
    def scan_file(self, path, loc, name, size):
        if is_env_file(name):
            self.found("env-file", loc)
        self.budget = 0
        try:
            if name.lower().endswith((".zst", ".tzst")):
                self.scan_obj(loc, name, None, size, 0, path=path)
            else:
                with open(path, "rb") as f:
                    self.scan_obj(loc, name, f, size, 0, path=path)
        except Exception as e:  # noqa: BLE001 - kể cả Abort: lỗi của file này, quét tiếp file khác
            self.error(loc, e)

    def scan_path(self, root, multi=False):
        norm = os.path.normpath(root)
        try:
            st = os.stat(root)
        except OSError as e:
            self.error(clean(root), e)
            return
        if not stat.S_ISDIR(st.st_mode):
            name = os.path.basename(norm)
            if not stat.S_ISREG(st.st_mode):
                self.error(clean(root), "không phải file thường hay thư mục")
            elif not self._excluded(name):
                self.scan_file(root, clean(name), name, st.st_size)
            return
        prefix = clean(norm) + "/" if multi else ""
        anchors = tuple(dict.fromkeys(("", os.path.basename(norm) + "/", norm + "/")))
        stack = [""]
        while stack:
            rel = stack.pop()
            d = os.path.join(root, rel) if rel else root
            try:
                with os.scandir(d) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError as e:
                self.error(prefix + clean(rel or "."), e)
                continue
            subdirs = []
            for e in entries:
                r = f"{rel}/{e.name}" if rel else e.name
                if self._excluded(e.name) or any(self._excluded(a + r) for a in anchors):
                    continue
                try:
                    if e.is_symlink():
                        self.symlinks += 1
                    elif e.is_dir(follow_symlinks=False):
                        subdirs.append(r)
                    elif e.is_file(follow_symlinks=False):
                        self.scan_file(e.path, prefix + clean(r), e.name, e.stat(follow_symlinks=False).st_size)
                    else:
                        self.special += 1
                except OSError as ex:
                    self.error(prefix + clean(r), ex)
            stack.extend(reversed(subdirs))

    # ---------- Qdrant ----------
    def scan_qdrant(self, url, coll):
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            self.error("qdrant:" + coll, "--qdrant-url phải là http(s)://")
            return
        endpoint = f"{url.rstrip('/')}/collections/{urllib.parse.quote(coll, safe='')}/points/scroll"
        headers = {"Content-Type": "application/json"}
        key = os.environ.get("QDRANT_API_KEY")
        if key:
            headers["api-key"] = key
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # Qdrant nội bộ: không qua proxy
        offset, pages = None, 0
        while True:
            body = {"limit": 1000, "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            req = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=headers, method="POST")
            try:
                with opener.open(req, timeout=300) as resp:
                    res = json.load(resp)["result"]
                points, nxt = res["points"], res.get("next_page_offset")
            except Exception as e:  # noqa: BLE001 - HTTP/JSON hỏng => exit 2
                self.error(f"qdrant:{coll}", e)
                return
            for p in points:
                data = json.dumps(p.get("payload"), ensure_ascii=False).encode("utf-8", "surrogatepass")
                self.points += 1
                self.scan_data(f"qdrant:{clean(coll)}#{clean(str(p.get('id')))}", data, None, True, suffix=False)
            pages += 1
            if nxt is None:
                return
            if nxt == offset:
                self.error(f"qdrant:{coll}", "next_page_offset lặp lại")
                return
            offset = nxt

    # ---------- tổng kết ----------
    def summary(self):
        return {
            "policy": self.policy,
            "findings": [dict({"rule": r, "location": loc}, **({"source": src} if src else {})) for r, loc, src in self.findings],
            "known_secret_hits": self.known_hits,
            "corpus": [{"tree": n, "prefix": p, "exact": e} for n, p, e in DATA_CORPUS] if self.policy == "data" else [],
            "corpus_counts": {t: dict(sorted(c.items())) for t, c in sorted(self.corpus_counts.items())},
            "counts_by_rule": dict(sorted(self.by_rule.items())),
            "counts_by_top": dict(sorted(self.by_top.items(), key=lambda kv: (-kv[1], kv[0]))),
            "allowed": self.allowed,
            "scanned_files": self.files,
            "scanned_bytes": self.bytes,
            "skipped_symlinks": self.symlinks,
            "skipped_special": self.special,
            "qdrant_points": self.points,
            "not_archives": self.not_archives,
            "errors": self.errors,
        }

    def report(self):
        more = len(self.findings) - self.max_print
        if more > 0:
            print(f"TRUNCATED\t{more}", file=self.out)
        rules = " ".join(f"{r}={n}" for r, n in sorted(self.by_rule.items())) or "-"
        print(f"secret_scan: quét {self.files} nội dung, {self.bytes} byte ({self.bytes / 2**20:.1f} MiB); "
              f"phát hiện {len(self.findings)} ({rules}); được phép {self.allowed}; symlink bỏ qua {self.symlinks}; "
              f"lỗi {len(self.errors)}", file=self.err)
        if self.corpus_counts:
            print("secret_scan: corpus công khai (chỉ đếm, không chặn): " + "; ".join(
                f"{t}: " + " ".join(f"{r}={n}" for r, n in sorted(c.items())) for t, c in sorted(self.corpus_counts.items())),
                file=self.err)
        if self.known_hits:
            print(f"secret_scan: !!! KHỚP SECRET THẬT {self.known_hits} chỗ (xem dòng known-secret): DỪNG, báo người phụ trách",
                  file=self.err)
        if self.by_top:
            tops = sorted(self.by_top.items(), key=lambda kv: (-kv[1], kv[0]))
            extra = f" (+{len(tops) - 20} nhánh khác)" if len(tops) > 20 else ""
            print("secret_scan: theo nhánh: " + " ".join(f"{t}={n}" for t, n in tops[:20]) + extra, file=self.err)


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="secret_scan.py", description="Quét bí mật; chỉ in luật + vị trí, không in giá trị.")
    ap.add_argument("paths", nargs="*", metavar="PATH")
    ap.add_argument("--allow", metavar="FILE")
    ap.add_argument("--exclude", metavar="GLOB", action="append", default=[])
    ap.add_argument("--summary-json", metavar="OUT")
    ap.add_argument("--max-print", metavar="N", type=int, default=200)
    ap.add_argument("--qdrant-url", metavar="URL")
    ap.add_argument("--qdrant-collection", metavar="NAME")
    ap.add_argument("--policy", choices=POLICIES, default="strict")
    ap.add_argument("--known-secrets", metavar="[?]KIND:PATH", action="append", default=[])
    a = ap.parse_args(argv)
    if bool(a.qdrant_url) != bool(a.qdrant_collection):
        ap.error("--qdrant-url và --qdrant-collection phải đi cùng nhau")
    if not a.paths and not a.qdrant_url:
        ap.error("cần ít nhất một PATH hoặc --qdrant-url")
    if a.max_print < 0:
        ap.error("--max-print phải >= 0")
    try:
        allow = load_allow(a.allow) if a.allow else {}
    except (OSError, UnicodeDecodeError, ScanError) as e:
        print(f"secret_scan: --allow: {_errmsg(e)}", file=sys.stderr)
        return 2
    try:
        known = load_known(a.known_secrets) if a.known_secrets else []
    except ScanError as e:
        print(f"secret_scan: --known-secrets: {e}", file=sys.stderr)
        return 2
    s = Scanner(allow, a.exclude, a.max_print, policy=a.policy, known=known)
    for p in a.paths:
        s.scan_path(p, multi=len(a.paths) > 1)
    if a.qdrant_url:
        s.scan_qdrant(a.qdrant_url, a.qdrant_collection)
    s.report()
    if a.summary_json:
        try:
            with open(a.summary_json, "w", encoding="utf-8") as f:
                json.dump(s.summary(), f, ensure_ascii=False, indent=1)
                f.write("\n")
        except OSError as e:
            print(f"secret_scan: không ghi được --summary-json: {_errmsg(e)}", file=sys.stderr)
            return 2
    return 3 if s.known_hits else 2 if s.errors else 1 if s.findings else 0


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        rc = 2
    except Exception as e:  # noqa: BLE001 - lỗi bất ngờ không được trông như "có phát hiện" (1) hay "sạch" (0)
        print(f"secret_scan: lỗi bất ngờ: {_errmsg(e)}", file=sys.stderr)
        rc = 2
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        pass
    sys.exit(rc)
