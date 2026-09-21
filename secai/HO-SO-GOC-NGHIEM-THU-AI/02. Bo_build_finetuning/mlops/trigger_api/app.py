#!/usr/bin/env python3
"""Service kích hoạt + theo dõi pipeline MLOps theo yêu cầu (bổ sung cho lịch tự động ở
setup/install_schedule.sh) — CỐ Ý một tiến trình riêng, cổng riêng, khoá riêng
(`MLOPS_TRIGGER_API_KEY`), KHÔNG chung với gateway public (`secai_gateway`). Lý do: endpoint này
có thể châm ngòi thuê GPU vast.ai + push model lên HF — hành động tốn tiền/ảnh hưởng production
không nên nằm chung tiến trình với API khách hàng dùng hàng ngày. Xem README.md cùng thư mục.

Chạy TRÊN MÁY ĐIỀU PHỐI (host), KHÔNG trong container gateway — pipeline cần venv/GPU/vast CLI/
HF token (~/.config) của host. Có 2 cách hiển thị:
  - UI standalone đi kèm tại /ui (static/index.html).
  - Trang nghiệm thu (:8131/showcase-demo) tab "Retraining tự động" gọi tới đây qua reverse-proxy
    same-origin của gateway (/showcase-api/retraining/* -> host :8877) hoặc trực tiếp (CORS bật).

Endpoints:
- GET  /health                         — liveness
- GET  /schedule                       — cadence/ngày đối soát + trạng thái systemd timer
- GET  /runs                           — liệt kê các lần chạy (đọc runs/<id>/status.json)
- GET  /runs/{run_id}                  — status.json đầy đủ 1 lần chạy (KHÔNG cần key, chỉ đọc)
- GET  /runs/{run_id}/stages/{sid}/log — log THẬT của 1 giai đoạn
- GET  /runs/{run_id}/stream           — SSE: đẩy status + log realtime tới khi xong
- POST /trigger  {mode: dry|execute|deploy}  hoặc {execute,allow_deploy}  — chạy nền (CẦN X-API-Key)
- POST /smoke                                — = /trigger mode=dry (CẦN X-API-Key)
- GET  /status/{run_id}                — (cũ, giữ tương thích) quyết định drift + log tail, CẦN key

Chạy: uvicorn app:app --host 127.0.0.1 --port 8877 (xem configs/pipeline.yml:trigger_api)
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

MLOPS_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = MLOPS_ROOT / "scripts"
RUNS_DIR = MLOPS_ROOT / "runs"
STATIC_DIR = Path(__file__).resolve().parent / "static"
sys.path.insert(0, str(SCRIPTS_DIR))
from _lib import common  # noqa: E402

app = FastAPI(
    title="secai-mlops-trigger",
    description="Kích hoạt + theo dõi pipeline MLOps (CVE mới -> drift -> finetune/DPO -> gate -> deploy).",
)
# Auth theo X-API-Key (không dùng cookie/origin) nên mở CORS mọi origin là an toàn: mutation vẫn
# phải có key; GET chỉ đọc artifact/log. Cho trang nghiệm thu gọi trực tiếp khi chưa dựng proxy.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Lớp NGOÀI (tuỳ chọn): HTTP Basic Auth bao TOÀN BỘ route ────────────────────────────────────
# TẮT mặc định (localhost không đổi). BẬT khi cả MLOPS_BASIC_AUTH_USER + MLOPS_BASIC_AUTH_PASS có
# giá trị — BẮT BUỘC bật trước khi expose service này ra internet (vd Cloudflare Quick Tunnel, vốn
# không có access-control riêng). Đây là lớp NGOÀI; X-API-Key vẫn là lớp TRONG cho mutation như cũ
# (defense-in-depth). OPTIONS (CORS preflight) được cho qua để trình duyệt preflight không vỡ.
_BASIC_USER_ENV, _BASIC_PASS_ENV = "MLOPS_BASIC_AUTH_USER", "MLOPS_BASIC_AUTH_PASS"


@app.middleware("http")
async def _basic_auth(request: Request, call_next):
    user = os.environ.get(_BASIC_USER_ENV)
    pw = os.environ.get(_BASIC_PASS_ENV)
    if user and pw and request.method != "OPTIONS":
        ok = False
        hdr = request.headers.get("authorization", "")
        if hdr.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(hdr[6:]).decode("utf-8", "replace").partition(":")
                ok = secrets.compare_digest(u, user) and secrets.compare_digest(p, pw)
            except (ValueError, TypeError):
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="secai-mlops"'})
    return await call_next(request)

MODE_FLAGS = {"dry": [], "execute": ["--execute"], "deploy": ["--execute", "--allow-deploy"],
              "mock": ["--mock"]}  # mock: chạy đủ 10 stage, stub GPU, không đụng production


def _config() -> dict:
    return common.load_config()


def _env_file_path() -> Path:
    return Path(os.environ.get("MLOPS_ENV_FILE") or (Path.home() / ".config/secai-mlops/mlops.env"))


def _vast_key_path() -> Path:
    # Nơi vastai CLI + secai-serving-vast/lib.sh:ensure_apikey ĐỌC key (cat file này) — không qua
    # env var. Ghi thẳng vào đây khi cấu hình qua UI.
    return Path.home() / ".config/vastai/vast_api_key"


# Các tham số retraining người vận hành cấu hình qua UI. SECRET = che giá trị, chỉ báo có/chưa.
_CONFIG_SECRET_KEYS = ["HF_TOKEN", "JUDGE_API_KEY"]
_CONFIG_PLAIN_KEYS = ["JUDGE_BASE_URL", "JUDGE_MODEL"]  # không phải secret nhưng vẫn ở env file
# KHÔNG cho sửa qua UI (tránh tự khoá mình / phải restart service): MLOPS_TRIGGER_API_KEY,
# MLOPS_BASIC_AUTH_* — set 1 lần lúc cài (setup/mlops.env + install_*.sh).


def _require_api_key(x_api_key: str | None, query_key: str | None = None) -> None:
    # query_key: cho endpoint stream (EventSource KHÔNG gửi được custom header) — chấp nhận
    # ?api_key=... thay cho header X-API-Key.
    key_env = _config()["trigger_api"]["api_key_env"]
    expected = os.environ.get(key_env)
    if not expected:
        raise HTTPException(500, f"server thiếu ${key_env} — không thể xác thực, xem trigger_api/README.md")
    if x_api_key != expected and query_key != expected:
        raise HTTPException(401, "sai hoặc thiếu X-API-Key")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _run_summary(run_dir: Path) -> dict | None:
    status = _read_json(run_dir / "status.json")
    if not status:
        return None
    stages = status.get("stages", [])
    done = sum(1 for s in stages if s.get("state") in ("done", "skipped"))
    return {
        "run_id": status.get("run_id", run_dir.name), "mode": status.get("mode"),
        "overall": status.get("overall"), "started_at": status.get("started_at"),
        "ended_at": status.get("ended_at"), "stages_total": len(stages), "stages_done": done,
    }


def _launch(flags: list[str], mode_label: str) -> dict:
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    args = [str(SCRIPTS_DIR / "run_pipeline.sh"), "--run-id", run_id, *flags]
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    # Đọc LẠI mlops.env lúc phóng để tham số vừa sửa qua UI (/config) áp dụng ngay cho run kế
    # tiếp, KHÔNG cần restart service. (MLOPS_TRIGGER_API_KEY / MLOPS_BASIC_AUTH_* vẫn lấy từ env
    # tiến trình đã nạp lúc systemd khởi động — 2 thứ đó không sửa qua UI nên không cần reload.)
    child_env = dict(os.environ)
    child_env.update(common.read_env_file(_env_file_path()))
    log_path = run_dir / "trigger_api.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        subprocess.Popen(args, stdout=log_file, stderr=subprocess.STDOUT,
                         cwd=str(SCRIPTS_DIR), env=child_env)
    return {"run_id": run_id, "mode": mode_label, "log": str(log_path),
            "status_url": f"/status/{run_id}", "runs_url": f"/runs/{run_id}",
            "stream_url": f"/runs/{run_id}/stream"}


class TriggerRequest(BaseModel):
    # Mới: mode=dry|execute|deploy. Cũ (tương thích ngược cho UI /ui đã có): execute/allow_deploy.
    mode: str | None = None
    execute: bool = False
    allow_deploy: bool = False


@app.post("/trigger")
def trigger(body: TriggerRequest, x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    if body.mode is not None:
        if body.mode not in MODE_FLAGS:
            raise HTTPException(400, f"mode không hợp lệ: {body.mode!r} (dùng dry|execute|deploy)")
        return _launch(MODE_FLAGS[body.mode], body.mode)
    # đường cũ: suy từ execute/allow_deploy
    flags: list[str] = []
    label = "dry"
    if body.execute:
        flags.append("--execute"); label = "execute"
    if body.allow_deploy:
        flags.append("--allow-deploy"); label = "deploy"
    return _launch(flags, label)


@app.post("/smoke")
def smoke(x_api_key: str | None = Header(default=None)) -> dict:
    """Smoke test = dry-run (giai đoạn 1->3: đọc Qdrant + đo model đang serving + gate), KHÔNG
    train/thuê GPU/đụng production. Rẻ, an toàn — bấm để kiểm tra vòng lặp có sống không."""
    _require_api_key(x_api_key)
    return _launch([], "dry")


@app.get("/schedule")
def schedule() -> dict:
    sched = _config().get("schedule", {})
    timer = {"installed": False, "detail": ""}
    try:
        out = subprocess.run(
            ["systemctl", "--user", "list-timers", "secai-mlops-pipeline.timer", "--all", "--no-pager"],
            capture_output=True, text=True, timeout=10)
        if "secai-mlops-pipeline.timer" in out.stdout:
            timer = {"installed": True, "detail": out.stdout.strip()}
    except (OSError, subprocess.SubprocessError):
        pass
    return {"schedule": sched, "timer": timer}


@app.get("/config")
def get_config() -> dict:
    """Xem tham số retraining (KHÔNG cần key — KHÔNG bao giờ trả giá trị secret, chỉ báo có/chưa
    cấu hình). Nguồn secret/plain: mlops.env; config phi-secret: pipeline.yml."""
    cfg = _config()
    env = common.read_env_file(_env_file_path())
    secrets_status = {k: bool(env.get(k) or os.environ.get(k)) for k in _CONFIG_SECRET_KEYS}
    plain = {k: env.get(k, os.environ.get(k, "")) for k in _CONFIG_PLAIN_KEYS}
    vast_cfg = cfg.get("vast_serving", {})
    vast_key = _vast_key_path()
    return {
        "env_file": str(_env_file_path()),
        "secrets": secrets_status,   # {"HF_TOKEN": true/false, "JUDGE_API_KEY": ...} — chỉ boolean
        "judge": plain,              # JUDGE_BASE_URL / JUDGE_MODEL (không phải secret)
        "schedule": cfg.get("schedule", {}),
        "hf_repos": cfg.get("hf", {}).get("production_repos", {}),
        "hf_candidate_repos": cfg.get("hf", {}).get("candidate_repos", {}),
        "min_new_cves_to_run": cfg.get("min_new_cves_to_run"),
        "basic_auth_enabled": bool(os.environ.get(_BASIC_USER_ENV) and os.environ.get(_BASIC_PASS_ENV)),
        "vast": {
            "api_key_set": vast_key.is_file() and vast_key.stat().st_size > 0,
            "embed_model": vast_cfg.get("embed_model_flag_default"),
            "rerank_model": vast_cfg.get("rerank_model_flag_default"),
            "candidate_label": vast_cfg.get("candidate_label"),
            "production_label": vast_cfg.get("production_label"),
        },
    }


@app.get("/vast/status")
def vast_status() -> dict:
    """Trạng thái tài khoản vast.ai (số dư) — best-effort, không cần key của trigger_api (chỉ đọc,
    KHÔNG trả vast API key). Đọc key từ file vastai CLI rồi gọi REST cloud.vast.ai."""
    import urllib.request
    key_path = _vast_key_path()
    if not (key_path.is_file() and key_path.stat().st_size > 0):
        return {"ok": False, "error": "chưa cấu hình vast API key"}
    try:
        key = key_path.read_text(encoding="utf-8").strip()
        req = urllib.request.Request(
            "https://cloud.vast.ai/api/v0/users/current/",
            headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            d = json.loads(resp.read())
        if d.get("error"):
            return {"ok": False, "error": str(d.get("error"))}
        credit = d.get("credit")
        return {"ok": True, "username": d.get("username") or d.get("email"),
                "credit": credit, "balance": d.get("balance"),
                # số tiêu được thực tế: credit prepaid (âm/0 = phải nạp mới thuê được GPU)
                "can_rent": bool(credit and credit > 0)}
    except Exception as exc:  # noqa: BLE001 — best-effort status, đừng để lỗi mạng làm sập UI
        return {"ok": False, "error": repr(exc)[:200]}


@app.get("/vast/ssh")
def vast_ssh() -> dict:
    """Public SSH key của MÁY TRIỂN KHAI — để add vào tài khoản vast.ai (Account → SSH Keys) thì
    máy này mới SSH được vào instance thuê. Public key AN TOÀN để hiển thị (không phải secret).
    KHÔNG bao giờ trả private key."""
    ssh_dir = Path.home() / ".ssh"
    keys = []
    if ssh_dir.is_dir():
        for p in sorted(ssh_dir.glob("*.pub")):
            try:
                keys.append({"name": p.name, "key": p.read_text(encoding="utf-8").strip()})
            except OSError:
                pass
    return {
        "public_keys": keys,
        "note": ("Add public key này vào vast.ai (Account → SSH Keys) TRƯỚC khi thuê instance. "
                 "Sau đó chỉ cần lệnh `ssh -p <port> root@<host>` mà serving-up in ra là máy triển "
                 "khai vào được instance (vast tự cài key đã add vào authorized_keys của instance)."),
    }


class ConfigUpdate(BaseModel):
    HF_TOKEN: str | None = None
    JUDGE_API_KEY: str | None = None
    JUDGE_BASE_URL: str | None = None
    JUDGE_MODEL: str | None = None
    VAST_API_KEY: str | None = None   # ghi vào ~/.config/vastai/vast_api_key (không phải mlops.env)
    schedule_cadence: str | None = None
    schedule_day: int | None = None
    min_new_cves_to_run: int | None = None


@app.post("/config")
def set_config(body: ConfigUpdate, x_api_key: str | None = Header(default=None)) -> dict:
    """Cập nhật tham số retraining (CẦN X-API-Key). Secret/plain -> mlops.env (chỉ ghi key có gửi,
    giữ nguyên dòng khác + backup); schedule/min_cves -> pipeline.yml. Áp dụng ngay cho run KẾ TIẾP
    (không cần restart service — _launch đọc lại mlops.env mỗi lần phóng)."""
    _require_api_key(x_api_key)
    from _lib.update_env_file import set_keys

    env_updates = {}
    for k in _CONFIG_SECRET_KEYS + _CONFIG_PLAIN_KEYS:
        v = getattr(body, k, None)
        if v is not None and v != "":
            env_updates[k] = v
    if env_updates:
        set_keys(_env_file_path(), env_updates)

    # Vast API key ghi vào file vastai CLI đọc (KHÔNG phải mlops.env) + chmod 600.
    vast_set = False
    if body.VAST_API_KEY:
        kp = _vast_key_path()
        kp.parent.mkdir(parents=True, exist_ok=True)
        kp.write_text(body.VAST_API_KEY.strip() + "\n", encoding="utf-8")
        kp.chmod(0o600)
        vast_set = True

    cfg = _config()
    changed_cfg = False
    if body.schedule_cadence is not None:
        cfg.setdefault("schedule", {})["cadence"] = body.schedule_cadence; changed_cfg = True
    if body.schedule_day is not None:
        cfg.setdefault("schedule", {})["day"] = body.schedule_day; changed_cfg = True
    if body.min_new_cves_to_run is not None:
        cfg["min_new_cves_to_run"] = body.min_new_cves_to_run; changed_cfg = True
    if changed_cfg:
        common.CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"updated_env_keys": sorted(env_updates), "vast_api_key_set": vast_set,
            "updated_config": changed_cfg, "config": get_config()}


@app.get("/runs")
def list_runs(limit: int = 30) -> dict:
    if not RUNS_DIR.is_dir():
        return {"runs": []}
    dirs = sorted((d for d in RUNS_DIR.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)
    runs = [s for d in dirs if (s := _run_summary(d))]
    return {"runs": runs[:limit]}


@app.get("/runs/{run_id}")
def run_status(run_id: str) -> dict:
    status = _read_json(RUNS_DIR / run_id / "status.json")
    if not status:
        raise HTTPException(404, f"không thấy status cho run_id={run_id}")
    return status


@app.get("/runs/{run_id}/stages/{stage_id}/log")
def stage_log(run_id: str, stage_id: str, tail: int = 0,
              x_api_key: str | None = Header(default=None), api_key: str | None = None) -> dict:
    # CẦN key: log giai đoạn có thể chứa secret nội bộ (API key serving `sk-secai****`, IP vast,
    # endpoint) — vd log 06b/07 in ra dòng "API KEY : ..." của serving-up. Không để lộ công khai
    # qua gateway proxy (đúng lớp lỗi bundle 2026-09-18). /schedule + /runs (chỉ status) vẫn mở.
    _require_api_key(x_api_key, api_key)
    log_path = RUNS_DIR / run_id / "logs" / f"{stage_id}.log"
    if not log_path.is_file():
        raise HTTPException(404, f"chưa có log giai đoạn {stage_id} của run {run_id}")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if tail > 0:
        text = "\n".join(text.splitlines()[-tail:])
    return {"run_id": run_id, "stage": stage_id, "log": text}


async def _stream_run(run_id: str):
    run_dir = RUNS_DIR / run_id
    status_path = run_dir / "status.json"
    logs_dir = run_dir / "logs"
    sent_offset: dict[str, int] = {}

    def sse(event: dict) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    for _ in range(3600):  # trần ~1h (poll 1s) — không treo stream vĩnh viễn
        status = _read_json(status_path)
        if status is None:
            yield sse({"type": "waiting", "msg": "status.json chưa sẵn sàng"})
            await asyncio.sleep(1)
            continue
        yield sse({"type": "status", "status": status})
        for st in status.get("stages", []):
            sid = st["id"]
            lp = logs_dir / f"{sid}.log"
            if not lp.is_file():
                continue
            try:
                data = lp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            off = sent_offset.get(sid, 0)
            if len(data) > off:
                yield sse({"type": "log", "stage": sid, "chunk": data[off:]})
                sent_offset[sid] = len(data)
        if status.get("overall") in ("done", "failed", "stopped"):
            yield sse({"type": "end", "overall": status["overall"]})
            return
        await asyncio.sleep(1)
    yield sse({"type": "end", "overall": "timeout"})


@app.get("/runs/{run_id}/stream")
async def stream(run_id: str, x_api_key: str | None = Header(default=None),
                 api_key: str | None = None) -> StreamingResponse:
    # CẦN key (header hoặc ?api_key= vì EventSource không gửi header) — stream đẩy nguyên log
    # từng giai đoạn, cùng rủi ro secret như /stages/log ở trên.
    _require_api_key(x_api_key, api_key)
    if not (RUNS_DIR / run_id).is_dir():
        raise HTTPException(404, f"không thấy run_id={run_id}")
    return StreamingResponse(
        _stream_run(run_id), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@app.get("/status/{run_id}")
def legacy_status(run_id: str, x_api_key: str | None = Header(default=None)) -> dict:
    """(Cũ, giữ tương thích cho UI /ui đã có) — quyết định drift + log tail. CẦN key như trước."""
    _require_api_key(x_api_key)
    run_dir = RUNS_DIR / run_id
    if not run_dir.is_dir():
        raise HTTPException(404, f"không thấy run_id={run_id}")
    result: dict = {"run_id": run_id}
    for key, rel in (("production_drift", "report-production/finetune-decision.json"),
                     ("candidate_requalify", "report-candidate/finetune-decision.json")):
        path = run_dir / rel
        if path.is_file():
            result[key] = json.loads(path.read_text(encoding="utf-8"))
    log_path = run_dir / "trigger_api.log"
    if log_path.is_file():
        result["last_log_lines"] = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
    status = _read_json(run_dir / "status.json")
    if status:
        result["status"] = status
    result["done"] = bool(status and status.get("overall") in ("done", "failed", "stopped"))
    return result


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


if STATIC_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")
