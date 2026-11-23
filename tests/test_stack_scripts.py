import subprocess
from pathlib import Path

STACK = Path(__file__).resolve().parents[1] / "stack"


def run(*a, **kw):
    return subprocess.run(a, text=True, capture_output=True, **kw)


def test_pool_string_24_slots():
    r = run("bash", str(STACK / "lib/pool.sh"), "12")
    assert r.returncode == 0
    out = r.stdout.strip()
    items = out.split(",")
    assert len(items) == 24 and not out.endswith(",")
    assert items[0] == "http://vm-a:5101|/ias-pool-home-a/slot1"
    assert items[11] == "http://vm-a:5112|/ias-pool-home-a/slot12"
    assert items[12] == "http://vm-b:5201|/ias-pool-home-b/slot1"
    assert items[23] == "http://vm-b:5212|/ias-pool-home-b/slot12"


def test_pool_rejects_bad_slots():
    for bad in ("0", "-1", "abc", "1.5", "", "08", "09", "100", "007"):
        r = run("bash", str(STACK / "lib/pool.sh"), bad)
        assert r.returncode != 0 and "slots" in r.stderr and not r.stdout, bad
    assert run("bash", str(STACK / "lib/pool.sh")).returncode != 0


def test_pool_accepts_1_to_99():
    for ok, n in (("1", 2), ("9", 18), ("10", 20), ("99", 198)):
        r = run("bash", str(STACK / "lib/pool.sh"), ok)
        assert r.returncode == 0 and len(r.stdout.strip().split(",")) == n, ok


# ---- bootstrap.sh / .env.example (Task 6) ----
import json
import os
import re
import shutil
import stat
import tempfile

GENERATED = (
    "NEO4J_PASSWORD", "ADMIN_API_KEY", "LOGS_UI_PASSWORD", "LOGS_UI_SESSION_SECRET",
    "REPORT_API_KEY", "IAS_SERVER_PASSWORD", "TRANSLATE_V2_CONVERTER_TOKEN",
)


def _stack(tmp_path):
    d = Path(tempfile.mkdtemp(dir=tmp_path)) / "stack"    # mỗi lần gọi một thư mục riêng
    shutil.copytree(STACK, d, ignore=shutil.ignore_patterns(".env", "secrets", "tokenizer", "acceptance", "images", "__pycache__"))
    return d


def _env(d, **extra):
    return {**os.environ, "STACK_DIR": str(d), **extra}


def _bs(d, cmd, **extra):
    return run("bash", str(d / "bootstrap.sh"), cmd, env=_env(d, **extra))


def _get(d, key):
    vals = [l.split("=", 1)[1] for l in (d / ".env").read_text().splitlines() if l.startswith(key + "=")]
    return vals[-1] if vals else None


def _set(d, **kv):
    lines = (d / ".env").read_text().splitlines()
    for k, v in kv.items():
        lines = [f"{k}={v}" if l.startswith(k + "=") else l for l in lines]
        if not any(l.startswith(k + "=") for l in lines):
            lines.append(f"{k}={v}")
    (d / ".env").write_text("\n".join(lines) + "\n")


def _ready(tmp_path, profiles="exploit,translation,report,review"):
    """Stack đã init + cấu hình hợp lệ đầy đủ; mỗi test phá một chỗ."""
    d = _stack(tmp_path)
    assert _bs(d, "init").returncode == 0
    tmp_path = d.parent
    root = tmp_path / "data"
    for s in ("exploitdb", "poc", "poc/store", "poc-sources", "exploitdb-mcp-server"):
        (root / s).mkdir(parents=True)
    (root / "poc").chmod(0o775)          # nhóm của thư mục = gid hiện tại = VM_SHARED_GID (đặt bên dưới)
    (root / "poc/store").chmod(0o775)
    tok = tmp_path / "tok"
    tok.mkdir()
    (tok / "tokenizer.json").write_text("{}")
    (tok / "tokenizer_config.json").write_text("{}")
    mm = tmp_path / "max_map_count"
    mm.write_text("262144\n")
    _set(d, COMPOSE_PROFILES=profiles, DATA_ROOT=root, VM_SHARED_GID=os.getgid(), TOKENIZER_DIR=tok,
         QWEN_BASE_URL="http://llm/v1", QWEN_API_KEY="k", QWEN_MODEL="m",
         EMBEDDING_API_URL="http://e", EMBEDDING_API_KEY="k", EMBEDDING_MODEL="m",
         RERANKER_API_URL="http://r", RERANKER_API_KEY="k", RERANKER_MODEL="m",
         GATEWAY_IMAGE="secai-agent-gateway:main-abc12345", VM_IMAGE="secai-pentestcode-vm:abc",
         CONVERTER_IMAGE="secai-doc-convert:abc", REVIEW_UI_IMAGE="secai-review-ui:abc",
         TRANSLATE_V2_OCR_BASE_URL="http://o", TRANSLATE_V2_OCR_MODEL="m",
         TRANSLATE_V2_SPECIALIST_BASE_URL="http://s", TRANSLATE_V2_SPECIALIST_MODEL="m",
         MULTILANG_SPECIALIST_BASE_URL="http://s", MULTILANG_SPECIALIST_MODEL="m")
    return d, {"MAX_MAP_COUNT_FILE": str(mm)}


def test_init_generates_secrets_once(tmp_path):
    d = _stack(tmp_path)
    r = _bs(d, "init")
    assert r.returncode == 0, r.stderr
    first = (d / ".env").read_text()
    assert stat.S_IMODE((d / ".env").stat().st_mode) == 0o600
    for k in GENERATED:
        assert re.fullmatch(r"[0-9a-f]{64}", _get(d, k) or ""), k
    assert len({_get(d, k) for k in GENERATED}) == len(GENERATED)
    assert _get(d, "IAS_SERVER_POOL").count(",") == 23
    assert _get(d, "REVIEW_ENABLED") == "true"
    assert _get(d, "TRANSLATION_UPSTREAM_URL") == "http://translation-gateway:18185"
    assert (d / "tokenizer").is_dir()
    r2 = _bs(d, "init")
    assert r2.returncode == 0, r2.stderr
    assert (d / ".env").read_text() == first            # lần 2 không ghi đè


def test_init_fills_only_empty_secrets(tmp_path):
    d = _stack(tmp_path)
    shutil.copy(d / ".env.example", d / ".env")
    _set(d, ADMIN_API_KEY="giu-nguyen", QWEN_API_KEY="tay")
    assert _bs(d, "init").returncode == 0
    assert _get(d, "ADMIN_API_KEY") == "giu-nguyen" and _get(d, "QWEN_API_KEY") == "tay"
    assert len(_get(d, "NEO4J_PASSWORD")) == 64
    assert stat.S_IMODE((d / ".env").stat().st_mode) == 0o600


def test_init_review_credentials_once_and_token_not_stored(tmp_path):
    d = _stack(tmp_path)
    r = _bs(d, "init")
    cred = d / "secrets/review-credentials.json"
    assert stat.S_IMODE(cred.stat().st_mode) == 0o644 and stat.S_IMODE(cred.parent.stat().st_mode) == 0o755
    data = json.loads(cred.read_text())
    assert set(data) == {"credentials"} and len(data["credentials"]) == 1
    rec = data["credentials"][0]
    assert rec["role"] == "admin" and rec["actor_id"] and re.fullmatch(r"[0-9a-f]{64}", rec["token_sha256"])
    raw = re.findall(r"\b[0-9a-f]{64}\b", r.stdout)
    assert len(raw) == 1 and hashlib_sha(raw[0]) == rec["token_sha256"]
    first = cred.read_text()
    r2 = _bs(d, "init")
    assert cred.read_text() == first and not re.search(r"\b[0-9a-f]{64}\b", r2.stdout)
    for f in d.rglob("*"):
        if f.is_file():
            assert raw[0] not in f.read_text(), f
    # schema thật của gateway chấp nhận tệp
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from secai_gateway.api.review_auth import load_credentials
    assert load_credentials(cred)[rec["token_sha256"]].role == "admin"


def hashlib_sha(s):
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


def test_init_refuses_credentials_directory(tmp_path):
    d = _stack(tmp_path)
    (d / "secrets/review-credentials.json").mkdir(parents=True)
    r = _bs(d, "init")
    assert r.returncode != 0 and "review-credentials.json" in r.stderr


def test_preflight_names_missing_vars(tmp_path):
    d = _stack(tmp_path)
    _bs(d, "init")
    r = _bs(d, "preflight")
    assert r.returncode != 0
    for k in ("QWEN_BASE_URL", "QWEN_API_KEY", "QWEN_MODEL", "EMBEDDING_API_URL", "EMBEDDING_API_KEY", "EMBEDDING_MODEL",
              "RERANKER_API_URL", "RERANKER_API_KEY", "RERANKER_MODEL", "GATEWAY_IMAGE", "DATA_ROOT",
              "VM_IMAGE", "CONVERTER_IMAGE", "REVIEW_UI_IMAGE", "TRANSLATE_V2_OCR_BASE_URL", "TRANSLATE_V2_SPECIALIST_MODEL",
              "MULTILANG_SPECIALIST_BASE_URL"):
        assert k in r.stderr, k
    assert not re.search(r"\b[0-9a-f]{64}\b", r.stderr + r.stdout)     # không in secret


def test_preflight_ok_when_configured(tmp_path):
    d, extra = _ready(tmp_path)
    r = _bs(d, "preflight", **extra)
    assert r.returncode == 0, r.stderr


def _fails(tmp_path, needle, mutate=None, profiles="exploit,translation,report,review", **kv):
    d, extra = _ready(tmp_path, profiles)
    if kv:
        _set(d, **kv)
    if mutate:
        mutate(d)
    r = _bs(d, "preflight", **extra)
    assert r.returncode != 0 and needle in r.stderr, r.stderr
    return r


def test_preflight_rejects_placeholder_and_latest_images(tmp_path):
    _fails(tmp_path, "VM_IMAGE", VM_IMAGE="secai-pentestcode-vm:MISSING-set-VM_IMAGE")
    _fails(tmp_path, "CONVERTER_IMAGE", CONVERTER_IMAGE="secai-doc-convert:MISSING-set-CONVERTER_IMAGE")
    _fails(tmp_path, "GATEWAY_IMAGE", GATEWAY_IMAGE="secai-agent-gateway:latest")
    _fails(tmp_path, "GATEWAY_IMAGE", GATEWAY_IMAGE="secai-agent-gateway")
    _fails(tmp_path, "VM_IMAGE", VM_IMAGE="vm:latest")


def test_preflight_profile_gating(tmp_path):
    # profile tắt thì không đòi biến của profile đó
    d, extra = _ready(tmp_path, "report")
    _set(d, VM_IMAGE="", CONVERTER_IMAGE="", REVIEW_UI_IMAGE="", TRANSLATE_V2_OCR_BASE_URL="")
    assert _bs(d, "preflight", **extra).returncode == 0


def test_preflight_empty_report_key(tmp_path):
    r = _fails(tmp_path, "REPORT_API_KEY", REPORT_API_KEY="")
    assert "404" in r.stderr


def test_preflight_empty_converter_token(tmp_path):
    _fails(tmp_path, "TRANSLATE_V2_CONVERTER_TOKEN", TRANSLATE_V2_CONVERTER_TOKEN="")


def test_preflight_credentials_path_is_directory(tmp_path):
    def mutate(d):
        (d / "secrets/review-credentials.json").unlink()
        (d / "secrets/review-credentials.json").mkdir()
    _fails(tmp_path, "review-credentials.json", mutate)


def test_preflight_missing_mcp_server_dir(tmp_path):
    def mutate(d):
        shutil.rmtree(Path(_get(d, "DATA_ROOT")) / "exploitdb-mcp-server")
    r = _fails(tmp_path, "exploitdb-mcp-server", mutate)
    assert "data package" in r.stderr


def test_preflight_data_root_problems(tmp_path):
    _fails(tmp_path, "DATA_ROOT", DATA_ROOT=str(tmp_path / "khong-co"))
    _fails(tmp_path, "poc-sources", lambda d: shutil.rmtree(Path(_get(d, "DATA_ROOT")) / "poc-sources"))


def test_preflight_poc_not_writable(tmp_path):
    r = _fails(tmp_path, "poc", lambda d: (Path(_get(d, "DATA_ROOT")) / "poc").chmod(0o755))
    assert "10001" in r.stderr


def test_preflight_tokenizer_dir(tmp_path):
    _fails(tmp_path, "TOKENIZER_DIR", lambda d: (Path(_get(d, "TOKENIZER_DIR")) / "tokenizer.json").unlink())


def test_preflight_pool_and_map_count(tmp_path):
    _fails(tmp_path, "IAS_SERVER_POOL", IAS_SERVER_POOL="")
    d, extra = _ready(tmp_path)
    Path(extra["MAX_MAP_COUNT_FILE"]).write_text("65530\n")
    r = _bs(d, "preflight", **extra)
    assert r.returncode != 0 and "vm.max_map_count" in r.stderr and "sysctl -w vm.max_map_count=262144" in r.stderr


def test_env_example_has_no_secret_values_or_model_names():
    sections = 0
    seen = set()
    for line in (STACK / ".env.example").read_text().splitlines():
        if line.startswith("# ──"):
            sections += 1
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            seen.add(k)
            if k.endswith(("_KEY", "_PASSWORD", "_SECRET", "_TOKEN")):   # không bắt *_TOKENS (số)
                assert v == "", k
            if k.endswith("_MODEL"):
                assert v == "", k                                       # check.sh bắt lệch tên model
    assert sections == 5
    exp = {"IAS_POOL_GPU_COUNT": "0", "COMPOSE_PROFILES": "exploit,translation,report,review", "VM_SLOTS": "12",
           "QDRANT_COLLECTION": "cve_kb_hybrid", "AIRGAP": "true", "VERIFY_ENABLED": "false", "EXEC_ENABLED": "false",
           "RATE_LIMIT_ENABLED": "false", "QWEN_CONTEXT_WINDOW": "131072", "INPUT_CLASSIFIER_ENABLED": "true",
           "INPUT_CLASSIFIER_BLOCK_OFFTOPIC": "false", "WEB_SEARCH_ENABLED": "false",
           "AGENT_POC_SOURCES_PATH": "/srv/secai-data/poc-sources"}
    got = {l.split("=", 1)[0]: l.split("=", 1)[1] for l in (STACK / ".env.example").read_text().splitlines()
           if "=" in l and not l.startswith("#")}
    for k, v in exp.items():
        assert got.get(k) == v, k


def test_env_example_covers_compose_variables():
    compose = (STACK / "compose.yml").read_text()
    names = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose))
    declared = {l.split("=", 1)[0] for l in (STACK / ".env.example").read_text().splitlines()
                if "=" in l and not l.startswith("#")}
    internal = {"COMPOSE_PROJECT_NAME"}   # có thể bỏ trống: compose có mặc định
    assert names - declared - internal == set()


def _poc_dirs(d):
    root = Path(_get(d, "DATA_ROOT"))
    return root / "poc", root / "poc/store"


def test_preflight_poc_store_required_and_checked(tmp_path):
    r = _fails(tmp_path, "poc/store", lambda d: shutil.rmtree(_poc_dirs(d)[1]))
    assert "poc/store" in r.stderr
    _fails(tmp_path, "poc/store", lambda d: _poc_dirs(d)[1].chmod(0o755))


def test_preflight_group_branch(tmp_path):
    # nhóm ghi được chỉ khi gid là VM_SHARED_GID (hoặc 999): gid hiện tại khác thì lỗi, trùng thì qua
    _fails(tmp_path, "poc", VM_SHARED_GID=os.getgid() + 12345)
    d, extra = _ready(tmp_path)
    assert _bs(d, "preflight", **extra).returncode == 0


def test_preflight_other_writable_is_warning_only(tmp_path):
    d, extra = _ready(tmp_path)
    _set(d, VM_SHARED_GID=os.getgid() + 12345)
    for p in _poc_dirs(d):
        p.chmod(0o757)
    r = _bs(d, "preflight", **extra)
    assert r.returncode == 0, r.stderr
    assert "WARNING" in r.stderr and "chown -R 10001:" in r.stderr


def test_preflight_unwritable_hint(tmp_path):
    r = _fails(tmp_path, "poc/store", lambda d: _poc_dirs(d)[1].chmod(0o755))
    assert "chown -R 10001:" in r.stderr and "o+w" in r.stderr


def test_preflight_requires_ias_server_password(tmp_path):
    _fails(tmp_path, "IAS_SERVER_PASSWORD", IAS_SERVER_PASSWORD="")
    d, extra = _ready(tmp_path, "report")
    _set(d, IAS_SERVER_PASSWORD="")
    assert _bs(d, "preflight", **extra).returncode == 0


def test_crlf_env_is_normalised_and_empty_detected(tmp_path):
    d = _stack(tmp_path)
    (d / ".env").write_bytes((d / ".env.example").read_bytes().replace(b"\n", b"\r\n"))
    assert _bs(d, "init").returncode == 0
    raw = (d / ".env").read_bytes()
    assert b"\r" not in raw and re.fullmatch(r"[0-9a-f]{64}", _get(d, "NEO4J_PASSWORD"))
    # CRLF chưa chuẩn hoá: KEY=\r vẫn là trống khi preflight
    d2, extra = _ready(tmp_path)
    (d2 / ".env").write_bytes((d2 / ".env").read_bytes().replace(b"REPORT_API_KEY=" + _get(d2, "REPORT_API_KEY").encode(), b"REPORT_API_KEY=").replace(b"\n", b"\r\n"))
    r = _bs(d2, "preflight", **extra)
    assert r.returncode != 0 and "REPORT_API_KEY" in r.stderr


def test_profiles_with_spaces_and_unknown_warning(tmp_path):
    d = _stack(tmp_path)
    shutil.copy(d / ".env.example", d / ".env")
    _set(d, COMPOSE_PROFILES="exploit, translation ,report, review, bogus")
    r = _bs(d, "init")
    assert r.returncode == 0
    assert _get(d, "IAS_SERVER_POOL").count(",") == 23 and _get(d, "REVIEW_ENABLED") == "true"
    assert _get(d, "TRANSLATION_UPSTREAM_URL") and "bogus" in r.stderr and "WARNING" in r.stderr
    p = _bs(d, "preflight")
    assert "bogus" in p.stderr and "VM_IMAGE" in p.stderr and "REVIEW_UI_IMAGE" in p.stderr
