"""Shared helpers for the mlops/ orchestration scripts. Stdlib only — every script in this
package must run with the plain `python3` already required elsewhere in this repo's ops
tooling (e.g. `secai/scripts/sync_autoscan_to_kb.py`), no extra pip install for the glue code
itself (only the actual train/quantize/push steps need the heavier requirements files).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

MLOPS_ROOT = Path(__file__).resolve().parents[2]  # .../mlops
# MLOPS_CONFIG env cho phép trỏ config khác (vd pipeline.sandbox.yml khi smoke sandbox-promote —
# đổi đích HF/serving-label/gateway sang bản test, production nguyên vẹn). Mặc định: pipeline.yml.
CONFIG_PATH = Path(os.environ.get("MLOPS_CONFIG") or (MLOPS_ROOT / "configs" / "pipeline.yml"))
STATE_PATH = MLOPS_ROOT / "state" / "last_finetune.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def qdrant_base_url(config: dict) -> str:
    qc = config["qdrant"]
    # `or` (không phải `.get(key, default)`) cố ý: 1 biến môi trường TỒN TẠI nhưng rỗng (vd. dòng
    # `QDRANT_URL=` để trống trong setup/mlops.env.example, đúng cách người dùng hay điền) vẫn
    # phải rơi về default — `.get()` chỉ fallback khi biến KHÔNG tồn tại, không khi nó rỗng, gây
    # lỗi thật "unknown url type" (đã hit live 2026-09-18) khi ghép ra "/collections//...".
    url = (os.environ.get(qc["url_env"]) or qc["default_url"]).rstrip("/")
    collection = os.environ.get(qc["collection_env"]) or qc["default_collection"]
    return f"{url}/collections/{collection}"


def qdrant_post(config: dict, path: str, body: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        qdrant_base_url(config) + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=value .env file (quotes stripped) — same shape as the shell `env_val`
    helper in secai/scripts/autoscan_daily_sync.sh, kept in sync deliberately."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        out[key.strip()] = value
    return out


def resolve_path(relative: str) -> Path:
    return (MLOPS_ROOT / "configs" / relative).resolve()


def http_get_json(url: str, headers: dict | None = None, timeout: int = 15) -> tuple[bool, dict | str]:
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, str(exc)
