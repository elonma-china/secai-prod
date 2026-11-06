#!/usr/bin/env python3
"""Offline PoC generator+verifier+repairer for /ai/exploit.

Mục tiêu (yêu cầu khách hàng): với mỗi CVE, sinh ra mã KHAI THÁC ĐƯỢC mục tiêu THẬT, KHÔNG lấy
y nguyên từ data — bằng vòng lặp: lấy mã hệ thống đang phục vụ → chạy nhắm lab → nếu fail thì
đưa lỗi runtime + mô tả lab cho LLM tự sửa → chạy lại (bounded) → khi thành công thì lưu vào
store "verified" (`poc/fixed_pocs.py` shape) để request path phục vụ bản đã kiểm chứng.
Tái dùng harness có sẵn (`evals/exploit_lab/run_lab_eval.py`) làm oracle nếu muốn.

Config JSON (một object per CVE):
{
  "CVE-2026-27944": {
    "lang": "python",
    "target": "http://127.0.0.1:9000",
    "args": ["-t","{target}","--exploit","--decrypt","-o","{work}/out"],
    "docker": null,                      // nếu mã chạy trong container: image để chạy
    "success_regex": ["[0-9a-f]{64}", "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"],
    "success_forbid": ["No credentials extracted"],   // nếu thấy thì coi là fail
    "lab_desc": "mô tả lab + thuật toán đúng để LLM sửa",
    "goal": "mục tiêu sửa",
    "code_file": null                    // nếu có: lấy mã từ file thay vì gọi /ai/exploit
  }
}

Ví dụ chạy:
  python scripts/poc_verify_repair.py --config /tmp/vr_cfg.json --fixed-dir /srv/poc/pocs-fixed \
      --repair-url http://<llm>/v1 --repair-model <model> --rounds 4 [--apply-fixed]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPAIR_SYS = (
    "Bạn là kỹ sư khai thác bảo mật. Bạn nhận một PoC CHẠY ĐƯỢC về cú pháp nhưng KHÔNG khai thác "
    "được lab. Hãy sửa ĐÚNG nguyên nhân để chuỗi khai thác chạy thành công trên lab mô tả. "
    "Giữ nguyên CLI/tham số, KHÔNG hardcode giá trị của lab. Chỉ trả về DUY NHẤT MỘT khối mã."
)


def _llm(repair_url: str, model: str, prompt: str, key: str | None, timeout: int = 600) -> str:
    """One chat call. The target is often a REASONING model whose </think> can eat the whole
    max_tokens budget → `content` comes back EMPTY with finish_reason=length. Retry once with a
    much larger budget and an explicit "no reasoning, code only" nudge before giving up."""
    def call(max_tokens: int, extra: str = "") -> str:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "system", "content": REPAIR_SYS + extra},
                         {"role": "user", "content": prompt}],
            "temperature": 0.2, "max_tokens": max_tokens,
        }).encode()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(repair_url.rstrip("/") + "/chat/completions", data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        ch = d["choices"][0]
        return ch["message"].get("content") or ""

    out = call(32000)
    if not out.strip():
        out = call(96000, "\nQUAN TRỌNG: KHÔNG suy luận dài dòng. Chỉ trả về DUY NHẤT một khối mã.")
    return out


def _code_block(txt: str) -> str:
    m = re.search(r"```[a-zA-Z0-9_+.-]*\n(.*?)\n```", txt, re.S)
    return (m.group(1) if m else txt).strip("\n")


def _success(cfg: dict, out: str, err: str) -> bool:
    blob = out + "\n" + err
    if any(p in blob for p in (cfg.get("success_forbid") or [])):
        return False
    pats = cfg.get("success_regex") or []
    return bool(pats) and any(re.search(p, blob) for p in pats)


def _run(cfg: dict, code: str, work: str) -> tuple[int, str, str]:
    lang = cfg.get("lang", "python")
    ext = {"python": "py", "ruby": "rb", "php": "php", "bash": "sh", "sh": "sh"}.get(lang, lang)
    path = os.path.join(work, f"poc.{ext}")
    Path(path).write_text(code, encoding="utf-8")
    args = [a.format(work=work, target=cfg["target"]) for a in cfg.get("args", [])]
    if cfg.get("docker"):
        cmd = ["docker", "run", "--rm", "--network", "host", "-v", f"{work}:/w", "-w", "/w",
               cfg["docker"], "timeout", str(cfg.get("timeout", 60)), lang, f"poc.{ext}", *args]
    else:
        cmd = [sys.executable if lang == "python" else lang, path, *args]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=cfg.get("timeout", 60) + 20)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "", "TIMEOUT"


def verify_repair(cve: str, cfg: dict, code: str, round_fn) -> tuple[str | None, str]:
    work = tempfile.mkdtemp(prefix=f"vrp_{cve}_")
    rounds = cfg.get("rounds", 4)
    for i in range(rounds + 1):
        rc, out, err = _run(cfg, code, work)
        ok = _success(cfg, out, err)
        print(f"[{cve}] round {i}: rc={rc} success={ok}")
        if ok:
            return code, work
        if i == rounds:
            return None, work
        prompt = (f"PoC cho {cve}.\n\n### Lab\n{cfg.get('lab_desc','')}\n\n### Mục tiêu\n{cfg.get('goal','')}\n\n"
                  f"### Kết quả chạy (rc={rc})\nSTDOUT:\n{out[-1500:]}\nSTDERR:\n{err[-1500:]}\n\n"
                  f"### Mã hiện tại\n```{cfg.get('lang','python')}\n{code}\n```\n")
        code = _code_block(round_fn(prompt))
        if not code.strip():
            print(f"[{cve}] round {i}: LLM trả về RỖNG — dừng (không hội tụ)")
            return None, work
        Path(os.path.join(work, f"attempt_{i+1}.txt")).write_text(code, encoding="utf-8")
    return None, work


def save_fixed(fixed_dir: str, cve: str, code: str, cfg: dict) -> bool:
    """Write a verified PoC in the shape `poc/fixed_pocs.py::save_fixed` produces (fixed=True)."""
    d = Path(fixed_dir)
    d.mkdir(parents=True, exist_ok=True)
    ev = {
        "kind": "exploit",
        "title": f"Verified PoC {cve}",
        "cve_ids": [cve],
        "language": cfg.get("lang", "python"),
        "source_code": code,
        "source_files": None,
        "code_truncated": False,
        "sha256": None,
        "url": cfg.get("source_url"),
        "source": "verified-repair",
        "source_ref": cfg.get("source_url"),
        "consolidated": True,
        "fixed": True,
        "targets": [cfg["target"]] if cfg.get("target") else None,
        "static_indicators": {"language": cfg.get("lang", "python"), "fixed": True},
    }
    tmp = d / f"{cve.upper()}.json.tmp"
    tmp.write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / f"{cve.upper()}.json")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--fixed-dir", required=True)
    ap.add_argument("--repair-url")
    ap.add_argument("--repair-model")
    ap.add_argument("--repair-key")
    ap.add_argument("--only")
    ap.add_argument("--apply-fixed", action="store_true", help="ghi bản verify được vào fixed-dir")
    args = ap.parse_args()

    configs = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.only:
        configs = {args.only: configs[args.only]}

    def round_fn(prompt: str) -> str:
        if not args.repair_url:
            raise SystemExit("--repair-url required for repair rounds")
        return _llm(args.repair_url, args.repair_model or "", prompt, args.repair_key)

    results = {}
    for cve, cfg in configs.items():
        code = ""
        if cfg.get("code_file"):
            code = Path(cfg["code_file"]).read_text(encoding="utf-8")
        else:
            print(f"[{cve}] --code_file chưa đặt; cần lấy mã từ /ai/exploit trước")
            results[cve] = "no_code"
            continue
        best, work = verify_repair(cve, cfg, code, round_fn)
        if best:
            results[cve] = "verified"
            outp = Path(f"/tmp/verified_{cve}." + {"python": "py", "ruby": "rb"}.get(cfg.get("lang"), "txt"))
            outp.write_text(best, encoding="utf-8")
            print(f"[{cve}] VERIFIED — lưu {outp}")
            if args.apply_fixed:
                save_fixed(args.fixed_dir, cve, best, cfg)
                print(f"[{cve}] đã ghi fixed-dir {args.fixed_dir}")
        else:
            results[cve] = "not_converged"
            print(f"[{cve}] KHÔNG hội tụ (xem {work})")
    print("SUMMARY:", json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
