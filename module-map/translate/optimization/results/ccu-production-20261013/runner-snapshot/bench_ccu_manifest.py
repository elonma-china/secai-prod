#!/usr/bin/env python3
"""Bench manifest qua gateway; không sửa cấu hình/rate limit hoặc giả IP.

--preflight kiểm từng sample trước tải. Production cần --allow-production.
Terminal có tiến độ; --out chứa JSON thô và report.html tự chứa.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import html
import hashlib
import json
import os
from pathlib import Path
import random
import re
import signal
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

from ccu_translate import multipart, pct


def load_manifest(path):
    path = Path(path).resolve()
    root = path.parent
    data = json.loads(path.read_text())
    items, excluded, ids = [], [], set()
    for item in data["items"]:
        if item["id"] in ids:
            raise ValueError("ID sample bị trùng")
        ids.add(item["id"])
        source = (root / item["path"]).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError(f"Đường dẫn sample không hợp lệ: {item['id']}")
        if item.get("sha256") and hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Sample thay đổi sau khi lập manifest: {item['id']}")
        if item.get("benchmark_role") == "boundary":
            excluded.append({"id": item["id"], "reason": item.get("limit_notes", [])})
        else:
            items.append(item)
    return root, items, excluded


def request(method, url, body=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=max(.01, timeout)) as response:
            return response.status, json.loads(response.read() or b"{}"), dict(response.headers)
    except urllib.error.HTTPError as error:
        try:
            data = json.loads(error.read() or b"{}")
        except ValueError:
            data = {}
        return error.code, data, dict(error.headers)
    except (OSError, ValueError) as error:
        return 0, {"error": {"code": type(error).__name__}}, {}


def retry_delay(headers, default):
    try:
        return max(.01, float(headers.get("Retry-After", default)))
    except (ValueError, TypeError):
        return default


def execute_sample(base, root, sample, deadline, poll=2, on_job=None, cancel_event=None):
    start = time.monotonic()
    row = {"sample_id": sample["id"], "group": sample["group"], "format": sample["format"],
           "size": sample["size_class"], "pages": sample.get("pages"), "words": sample.get("words"),
           "post": None, "status": None, "error": None, "integrity_ok": False,
           "missing_literals": [], "poll_429": 0, "result_429": 0, "queue_s": None,
           "t_start": datetime.now(timezone.utc).isoformat()}
    job = None
    terminal = None

    def call(method, path, body=None, headers=None):
        return request(method, base.rstrip("/") + path, body, headers,
                       min(125 if sample["group"] == "text" else 60, max(.01, deadline - time.monotonic())))

    def pause(delay):
        time.sleep(min(delay, max(0, deadline - time.monotonic())))

    try:
        data = (root / sample["path"]).read_bytes()
        if sample["group"] == "text":
            body = json.dumps({"text": data.decode("utf-8"), "source_lang": sample["source_lang"],
                               "target_lang": sample["target_lang"]}, ensure_ascii=False).encode()
            status, result, headers = call("POST", sample["route"], body, {"Content-Type": "application/json"})
            row["post"] = status
            if status != 200:
                row.update(status="rejected", error=(result.get("error") or {}).get("code") or f"HTTP{status}")
                return row
        else:
            body, content_type = multipart({"source_lang": sample["source_lang"], "target_lang": sample["target_lang"]},
                                           Path(sample["path"]).name, data)
            status, result, headers = call("POST", sample["route"], body,
                                         {"Content-Type": content_type, "Idempotency-Key": uuid.uuid4().hex})
            row["post"] = status
            if status != 202:
                row.update(status="rejected", error=(result.get("error") or {}).get("code") or f"HTTP{status}")
                return row
            job = result.get("job_id")
            if not job:
                row.update(status="poll_error", error="MISSING_JOB_ID")
                return row
            row["job_id"] = job
            if on_job:
                on_job({"event": "accepted", "job_id": job, "sample_id": sample["id"]})
            path = f"/ai/v2/translate/jobs/{job}"
            delay = poll
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    row.update(status="interrupted", error="BENCH_INTERRUPTED")
                    return row
                pause(delay)
                status, result, headers = call("GET", path)
                if status == 429:
                    row["poll_429"] += 1
                    delay = retry_delay(headers, min(30, max(poll, delay * 1.5)))
                    continue
                if status != 200:
                    row.update(status="poll_error", error=(result.get("error") or {}).get("code") or f"HTTP{status}")
                    return row
                delay = poll
                state = result.get("status")
                if row["queue_s"] is None and state not in ("queued", None):
                    row["queue_s"] = round(time.monotonic() - start, 3)
                if state in ("failed", "cancelled"):
                    terminal = state
                    row.update(status=state, error=(result.get("error") or {}).get("code"))
                    return row
                if state == "succeeded":
                    terminal = state
                    break
            else:
                row.update(status="timeout", error="BENCH_DEADLINE")
                return row
            while time.monotonic() < deadline:
                status, result, headers = call("GET", path + "/result")
                if status == 429:
                    row["result_429"] += 1
                    pause(retry_delay(headers, poll))
                    continue
                if status != 200:
                    row.update(status="result_error", error=(result.get("error") or {}).get("code") or f"HTTP{status}")
                    return row
                break
            else:
                row.update(status="timeout", error="RESULT_DEADLINE")
                return row
        text = result.get("translated_text")
        if not isinstance(text, str) or not text.strip():
            row.update(status="result_error", error="EMPTY_RESULT")
            return row
        row["missing_literals"] = [value for value in sample.get("preserve_literals", []) if value not in text]
        row["unexpected_marker"] = bool(re.search(r"⟦(?:ML-|L\d)|\[L\d+\]|\[REDACTED|TOKEN_INTEGRITY_FALLBACK", text))
        row["integrity_ok"] = not row["missing_literals"] and not row["unexpected_marker"]
        row.update(status="succeeded", translated_text=text, output_chars=len(text),
                   warnings=result.get("warnings", []))
        return row
    except Exception as error:
        row.update(status="client_error", error=type(error).__name__)
        return row
    finally:
        if job and terminal is None:
            status, _, _ = request("DELETE", base.rstrip("/") + f"/ai/v2/translate/jobs/{job}", timeout=5)
            row["cancel_requested"] = status
            code, current, _ = request("GET", base.rstrip("/") + f"/ai/v2/translate/jobs/{job}", timeout=5)
            if code == 200 and current.get("status") in ("succeeded", "failed", "cancelled"):
                terminal = current["status"]
        if job and terminal and on_job:
            on_job({"event": "terminal", "job_id": job, "status": terminal})
        row["total_s"] = round(time.monotonic() - start, 3)


def summarize(rows, wall):
    accepted = [r for r in rows if r["post"] in (200, 202)]
    succeeded = [r for r in rows if r["status"] == "succeeded"]
    failed = [r for r in rows if r["status"] != "succeeded"]
    infra = [r for r in rows if (r["post"] or 0) >= 500 or r["post"] == 0 or r["status"] in
             ("timeout", "poll_error", "result_error", "client_error", "interrupted") or
             (r["status"] == "failed" and any(token in (r["error"] or "") for token in
              ("UNAVAILABLE", "TIMEOUT", "LEASE", "STORAGE")))]
    cells = {}
    for key in sorted({(r["group"], r["format"], r["size"]) for r in rows}):
        selected = [r for r in succeeded if (r["group"], r["format"], r["size"]) == key]
        total = [r["total_s"] for r in selected]
        queue = [r["queue_s"] for r in selected if r["queue_s"] is not None]
        cells["/".join(key)] = {"n": len(selected), "unique_samples": len({r["sample_id"] for r in selected}),
                              "p50": pct(total, 50), "p95": pct(total, 95), "queue_p95": pct(queue, 95),
                              "integrity_ok": sum(r["integrity_ok"] for r in selected),
                              "p95_reliable_n20": len(selected) >= 20}
    times = [r["total_s"] for r in succeeded]
    rejected = [r for r in rows if r["post"] not in (200, 202)]
    reasons = []
    if infra:
        reasons.append("infrastructure_error")
    if rejected:
        reasons.append("rejected")
    if accepted and len(succeeded) / len(accepted) < .99:
        reasons.append("success_below_99pct")
    page_rows = [r for r in succeeded if r.get("pages")]
    page_p95 = pct([r["total_s"] / r["pages"] for r in page_rows], 95)
    return {"attempted": len(rows), "accepted": len(accepted), "succeeded": len(succeeded),
            "rejected": len(rejected), "failed": len(failed), "infra": len(infra),
            "integrity_ok": sum(r["integrity_ok"] for r in succeeded),
            "errors": dict(Counter(r["error"] or r["status"] for r in failed)),
            "poll_429": sum(r["poll_429"] + r["result_429"] for r in rows),
            "p50": pct(times, 50), "p95": pct(times, 95), "page_p95_s": page_p95,
            "page_target_3s": page_p95 is not None and page_p95 <= 3,
            "docs_per_min": round(len(succeeded) * 60 / max(wall, .001), 2),
            "pages_per_min": round(sum(r["pages"] for r in page_rows) * 60 / max(wall, .001), 2),
            "wall_s": round(wall, 2), "cells": cells, "reasons": reasons,
            "verdict": "NO_DATA" if not rows else "FAIL" if reasons else "PASS",
            "quality_verdict": "NO_DATA" if not succeeded else "PASS" if all(r["integrity_ok"] for r in succeeded) else "FAIL"}


def annotate_coverage(summary, expected, ready):
    summary["coverage"] = {"total": len(expected), "ready": len(ready),
                           "excluded_ids": sorted({item["id"] for item in expected} - {item["id"] for item in ready})}
    for item in expected:
        key = "/".join((item["group"], item["format"], item["size_class"]))
        summary["cells"].setdefault(key, {"n": 0, "unique_samples": 0, "p50": None, "p95": None,
                                        "queue_p95": None, "integrity_ok": 0, "p95_reliable_n20": False})
    if len(ready) != len(expected):
        summary["reasons"].append("preflight_coverage_partial")
        if summary["verdict"] == "PASS":
            summary["verdict"] = "PARTIAL"


class OwnedJobs:
    def __init__(self, path, base):
        self.path, self.base = Path(path), base.rstrip("/")
        self.active, self.lock = set(), threading.Lock()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                entry = json.loads(line)
                if entry["event"] == "accepted":
                    self.active.add(entry["job_id"])
                else:
                    self.active.discard(entry["job_id"])

    def event(self, entry):
        with self.lock:
            if entry["event"] == "accepted":
                self.active.add(entry["job_id"])
            else:
                self.active.discard(entry["job_id"])
            with self.path.open("a") as handle:
                handle.write(json.dumps(entry) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def cleanup(self):
        for job in list(self.active):
            path = self.base + f"/ai/v2/translate/jobs/{job}"
            code, current, _ = request("GET", path, timeout=5)
            if code == 200 and current.get("status") in ("succeeded", "failed", "cancelled"):
                self.event({"event": "terminal", "job_id": job, "status": current["status"]})
                continue
            request("DELETE", path, timeout=5)
            end = time.monotonic() + 20
            while time.monotonic() < end:
                code, current, _ = request("GET", path, timeout=5)
                if code in (404, 410) or (code == 200 and current.get("status") in ("succeeded", "failed", "cancelled")):
                    self.event({"event": "terminal", "job_id": job, "status": current.get("status", "expired")})
                    break
                time.sleep(2)


def check_resume(data, base, manifest, digest, config):
    if any((data["base"] != base, data["manifest"] != manifest,
            data.get("manifest_sha256") != digest, data["config"] != config)):
        raise ValueError("resume khác gateway/manifest hash/cấu hình; dùng output mới")


def report(data, out):
    out.mkdir(parents=True, exist_ok=True)
    temporary = out / "results.json.tmp"
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    temporary.replace(out / "results.json")
    def escape(value):
        return html.escape(str(value))
    summaries = []
    shown_levels = data.get("levels", []) + ([data["current_level"]] if data.get("current_level") else [])
    for level in shown_levels:
        s = level["summary"]
        summaries.append(f"<tr><td>{escape(level['scenario'])}</td><td>{level['users']}</td><td>{escape(s['verdict'])}</td>"
                         f"<td>{s['succeeded']}/{s['accepted']}</td><td>{s['rejected']}</td><td>{s['p50']}</td><td>{s['p95']}</td>"
                         f"<td>{s['docs_per_min']}</td><td>{s['integrity_ok']}/{s['succeeded']}</td><td>{s['page_p95_s']}</td>"
                         f"<td>{s['infra']}</td><td>{s['poll_429']}</td><td>{s.get('coverage',{}).get('ready')}/{s.get('coverage',{}).get('total')}</td><td>{escape(s['errors'])}</td></tr>")
    cell_rows = []
    for level in shown_levels:
        for cell, s in level["summary"]["cells"].items():
            cell_rows.append(f"<tr><td>{escape(level['scenario'])}/{level['users']}</td><td>{escape(cell)}</td>"
                             f"<td>{s['n']}</td><td>{s['unique_samples']}</td><td>{s['p50']}</td><td>{s['p95']}</td>"
                             f"<td>{s['queue_p95']}</td><td>{s['integrity_ok']}</td><td>{'đủ n≥20' if s['p95_reliable_n20'] else 'ít mẫu'}</td></tr>")
    preflight_rows = []
    for row in data.get("preflight", []):
        preflight_rows.append(f"<tr><td>{escape(row['sample_id'])}</td><td>{escape(row['status'])}</td>"
                              f"<td>{row['total_s']}</td><td>{'đạt' if row['integrity_ok'] else 'chưa đạt'}</td>"
                              f"<td>{escape(row['error'])}</td></tr>")
    charts = []
    for scenario in sorted({level["scenario"] for level in data.get("levels", [])}):
        levels = [level for level in data["levels"] if level["scenario"] == scenario and level["summary"]["p95"] is not None]
        if not levels:
            continue
        ymax = max(1, max(level["summary"]["p95"] for level in levels)) * 1.15
        xmax = max(1, max(level["users"] for level in levels))
        points = " ".join(f"{50 + level['users'] / xmax * 480:.1f},{230 - level['summary']['p95']/ymax*190:.1f}" for level in levels)
        labels = "".join(f"<text x='{50+level['users']/xmax*480:.1f}' y='250'>{level['users']} VU</text>"
                         f"<text x='{50+level['users']/xmax*480:.1f}' y='{220-level['summary']['p95']/ymax*190:.1f}'>{level['summary']['p95']}s</text>" for level in levels)
        charts.append(f"<h3>{escape(scenario)}: p95 tổng thời gian</h3><svg viewBox='0 0 580 270' role='img' aria-label='p95 theo CCU'>"
                      f"<path d='M50 25 V230 H550' fill='none' stroke='#aaa'/><polyline points='{points}' fill='none' stroke='#1673ba' stroke-width='3'/>{labels}</svg>")
    ready = len(data.get("ready_ids", []))
    preflight_ok = sum(row["status"] == "succeeded" for row in data.get("preflight", []))
    preflight_literal = sum(row["integrity_ok"] for row in data.get("preflight", []))
    peaks = [{"scenario": level["scenario"], "VU": level["users"],
              "peak_client_requests": [run.get("peak_in_flight") for run in level["runs"]]} for level in shown_levels]
    content = f"""<!doctype html><html lang='vi'><meta charset='utf-8'><title>CCU dịch — dataset lớn</title>
<style>body{{font:15px system-ui;margin:28px;color:#17212b}}table{{border-collapse:collapse;width:100%;margin-bottom:24px}}td,th{{border:1px solid #d7dce1;padding:7px;text-align:left}}th{{background:#eef3f7}}svg{{max-width:650px;width:100%}}text{{font:12px system-ui}}</style>
<h1>CCU dịch — text, tệp, ảnh, PDF scan</h1><p>Gateway: {escape(data['base'])}; thời điểm: {escape(data['started_at'])}.</p>
<p>Trạng thái: {escape(data.get('phase'))}. Preflight: xử lý {preflight_ok}/{len(data.get('preflight', []))}; literal đạt {preflight_literal}; sẵn sàng tải {ready}/{data.get('dataset_count')}. PARTIAL nghĩa là chỉ đo tập mẫu đã sẵn sàng, chưa chứng minh toàn bộ phạm vi.</p>
<details><summary>Cấu hình, concurrency thực quan sát và môi trường</summary><pre>{escape(json.dumps({'config':data['config'],'peak_client_requests':peaks,'runtime':data.get('runtime')},ensure_ascii=False,indent=2))}</pre></details>
<p>PASS xử lý = không lỗi hạ tầng/từ chối và ≥99% job nhận thành công. Chất lượng giữ literal báo riêng, chưa chấm nghĩa dịch. p95 từng ô có n&lt;20 là tham khảo. Thời gian gồm upload/poll/result; queue là thời gian quan sát đến lần đầu thấy job chạy, có sai số polling.</p>
<p>Không nới rate limit hoặc giả IP. Dữ liệu tổng hợp, không đại diện đầy đủ tài liệu khách hàng. {escape(data.get('notes', ''))}</p>
<h2>Kết quả tải</h2><table><tr><th>Nhóm</th><th>VU</th><th>Xử lý</th><th>Thành công/nhận</th><th>Từ chối</th><th>p50 s</th><th>p95 s</th><th>Tài liệu/phút</th><th>Literal đạt</th><th>p95 s/trang</th><th>Lỗi hạ tầng</th><th>Poll/result 429</th><th>Coverage</th><th>Mã lỗi</th></tr>{''.join(summaries)}</table>
{''.join(charts)}<h2>Định dạng × độ dài</h2><table><tr><th>Nhóm/VU</th><th>Ô mẫu</th><th>n thành công</th><th>Sample duy nhất</th><th>p50 s</th><th>p95 s</th><th>Queue p95 s</th><th>Literal đạt</th><th>Độ đủ mẫu</th></tr>{''.join(cell_rows)}</table>
<h2>Kiểm tra trước tải</h2><table><tr><th>Sample</th><th>Trạng thái</th><th>Giây</th><th>Literal</th><th>Lỗi</th></tr>{''.join(preflight_rows)}</table>
<h2>Phần chưa chạy / loại khỏi tải</h2><pre>{escape(json.dumps(data.get('excluded', []), ensure_ascii=False, indent=2))}</pre></html>"""
    (out / "report.html").write_text(content, encoding="utf-8")


def preflight(a, root, items, data):
    ready = []
    last_text = 0
    existing = {row["sample_id"]: row for row in data.get("preflight", [])}
    for index, sample in enumerate(items, 1):
        if sample["id"] in existing:
            row = existing[sample["id"]]
        else:
            if sample["group"] == "text":
                time.sleep(max(0, 6.2 - (time.monotonic() - last_text)))
                last_text = time.monotonic()
            print(f"PREFLIGHT {index}/{len(items)} {sample['id']}", flush=True)
            row = execute_sample(a.base, root, sample, time.monotonic() + a.preflight_timeout, a.poll,
                                 a.registry.event, a.cancel_event)
            save_output(row, a.out)
            data["preflight"].append(row)
            report(data, a.out)
        print(f"  {row['status']} {row['total_s']}s literal={row['integrity_ok']} error={row['error']}", flush=True)
        if row["status"] == "succeeded":
            ready.append(sample)
        else:
            data["excluded"].append({"id": sample["id"], "reason": "preflight_failed", "error": row["error"]})
    data["ready_ids"] = [item["id"] for item in ready]
    report(data, a.out)
    return ready


def save_output(row, out):
    if "translated_text" in row:
        directory = out / "outputs"
        directory.mkdir(exist_ok=True)
        path = directory / f"{row['sample_id']}-{uuid.uuid4().hex[:8]}.txt"
        path.write_text(row.pop("translated_text"), encoding="utf-8")
        row["output_path"] = str(path.relative_to(out))


def make_picker(items, seed):
    buckets = defaultdict(list)
    for item in items:
        buckets[(item["group"], item["format"], item["size_class"])].append(item)
    groups = sorted({key[0] for key in buckets})
    keys = {group: sorted(key for key in buckets if key[0] == group) for group in groups}
    rng = random.Random(seed)
    for group in groups:
        rng.shuffle(keys[group])
    for choices in buckets.values():
        rng.shuffle(choices)
    def pick(index):
        group = groups[index % len(groups)]
        slot = index // len(groups)
        key = keys[group][slot % len(keys[group])]
        choices = buckets[key]
        return choices[(slot // len(keys[group])) % len(choices)]
    return pick


def run_once(a, root, items, users, seed):
    pick = make_picker(items, seed)
    rows, lock, counter, inflight = [], threading.Lock(), [0], [0, 0]
    started = time.monotonic()
    stop = started + a.duration
    deadline = stop + a.drain
    def vu(index):
        rng = random.Random(seed * 1000 + index)
        time.sleep(a.ramp * index / users)
        while time.monotonic() < stop and not a.cancel_event.is_set():
            with lock:
                k = counter[0]
                counter[0] += 1
                inflight[0] += 1
                inflight[1] = max(inflight[1], inflight[0])
            sample = pick(k)
            row = execute_sample(a.base, root, sample, deadline, a.poll, a.registry.event, a.cancel_event)
            save_output(row, a.out)
            with lock:
                rows.append(row)
                inflight[0] -= 1
            print(f"  DONE {sample['id']} {row['status']} {row['total_s']}s literal={row['integrity_ok']} error={row['error']}", flush=True)
            if time.monotonic() < stop:
                a.cancel_event.wait(min(rng.uniform(5, 20), max(0, stop - time.monotonic())))
    threads = [threading.Thread(target=vu, args=(i,)) for i in range(users)]
    for thread in threads:
        thread.start()
    last_print = started
    interrupted = False
    try:
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(.2)
            now = time.monotonic()
            if now - last_print >= 15:
                with lock:
                    count, attempts = len(rows), counter[0]
                print(f"  PROGRESS {now-started:.0f}s completed={count} in_flight={attempts-count}", flush=True)
                last_print = now
    except KeyboardInterrupt:
        interrupted = True
        a.cancel_event.set()
    finally:
        for thread in threads:
            thread.join()
    wall = time.monotonic() - started
    return {"seed": seed, "rows": rows, "summary": summarize(rows, wall),
            "peak_in_flight": inflight[1], "interrupted": interrupted}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--scenarios", default="files,images,scan,mixed")
    parser.add_argument("--levels", default="5,10,20,25,30")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--duration", type=float, default=180)
    parser.add_argument("--ramp", type=float, default=30)
    parser.add_argument("--drain", type=float, default=600)
    parser.add_argument("--poll", type=float, default=2)
    parser.add_argument("--cooldown", type=float, default=40)
    parser.add_argument("--preflight-timeout", type=float, default=600)
    parser.add_argument("--seed", type=int, default=20261013)
    a = parser.parse_args()
    if urlsplit(a.base).port in (8131, 18185) and not a.allow_production:
        parser.error("production cần --allow-production")
    levels = [int(v) for v in a.levels.split(",")]
    scenarios = a.scenarios.split(",")
    if levels != sorted(set(levels)) or any(n < 1 for n in levels) or a.runs < 1 or a.duration <= 0 or not 0 <= a.ramp < a.duration or a.poll <= 0 or a.drain <= 0 or a.cooldown < 0:
        parser.error("tham số tải/thời gian không hợp lệ")
    if set(scenarios) - {"text", "files", "images", "scan", "mixed", "difficult"}:
        parser.error("scenario không hợp lệ")
    root, items, excluded = load_manifest(a.manifest)
    a.out.mkdir(parents=True, exist_ok=True)
    previous = a.out / "results.json"
    if previous.exists() and not a.resume:
        parser.error("output đã có; dùng thư mục mới hoặc --resume")
    config = {"levels": levels, "scenarios": scenarios, "runs": a.runs, "duration": a.duration,
              "ramp": a.ramp, "drain": a.drain, "poll": a.poll, "cooldown": a.cooldown,
              "seed": a.seed, "preflight_timeout": a.preflight_timeout,
              "sampler_version": 2,
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    digest = hashlib.sha256(a.manifest.read_bytes()).hexdigest()
    data = json.loads(previous.read_text()) if previous.exists() else {
        "base": a.base, "manifest": str(a.manifest.resolve()), "started_at": datetime.now(timezone.utc).isoformat(),
        "config": config, "manifest_sha256": digest, "dataset_count": len(items),
        "preflight": [], "levels": [], "excluded": excluded,
        "notes": "CCU qua gateway production và giới hạn tiếp nhận hiện có. Ô p95 ít mẫu không đủ để kết luận riêng định dạng."}
    try:
        check_resume(data, a.base, str(a.manifest.resolve()), digest, config)
    except ValueError as error:
        parser.error(str(error))
    runtime = a.out / "runtime.json"
    if runtime.exists():
        data["runtime"] = json.loads(runtime.read_text())
    a.cancel_event = threading.Event()
    a.registry = OwnedJobs(a.out / "owned-jobs.jsonl", a.base)
    a.registry.cleanup()
    if a.registry.active:
        parser.error("job benchmark cũ chưa kết thúc; không tạo tải mới")
    data["runner_pid"] = os.getpid()
    def stop_signal(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_signal)
    try:
        if a.preflight:
            data["phase"] = "preflight"
            report(data, a.out)
            preflight(a, root, items, data)
            data["phase"] = "preflight_complete"
            return
        if "ready_ids" not in data:
            parser.error("phải chạy --preflight trước tải")
        ready_ids = set(data["ready_ids"])
        ready = [item for item in items if item["id"] in ready_ids]
        completed = {(level["scenario"], level["users"]) for level in data["levels"]}
        stopped = {entry["scenario"] for entry in data["excluded"] if entry.get("reason") == "stop_after_infrastructure_errors"}
        data["phase"] = "measuring"
        report(data, a.out)
        for scenario in scenarios:
            if scenario in stopped:
                print(f"SKIP {scenario}: đã dừng tăng tải do lỗi hạ tầng", flush=True)
                continue
            def in_scope(item):
                return item["group"] == scenario or (scenario == "mixed" and item["group"] in ("text", "files", "images", "scan"))
            expected = [item for item in items if in_scope(item)]
            selected = [item for item in ready if in_scope(item)]
            if not selected:
                data["excluded"].append({"scenario": scenario, "reason": "no_ready_samples"})
                report(data, a.out)
                continue
            for users in levels:
                if (scenario, users) in completed:
                    continue
                partial = data.get("current_level")
                if partial and (partial["scenario"], partial["users"]) != (scenario, users):
                    parser.error("level đang dở không khớp; không tạo tải chồng")
                runs = partial["runs"] if partial else []
                for repetition in range(len(runs), a.runs):
                    print(f"MEASURE scenario={scenario} VU={users} run={repetition+1}/{a.runs}", flush=True)
                    run = run_once(a, root, selected, users, a.seed + repetition)
                    annotate_coverage(run["summary"], expected, selected)
                    if run["interrupted"]:
                        data.setdefault("aborted_runs", []).append({"scenario": scenario, "users": users, "run": run})
                        raise KeyboardInterrupt
                    runs.append(run)
                    print("SUMMARY " + json.dumps(run["summary"], ensure_ascii=False), flush=True)
                    summary = summarize([row for run in runs for row in run["rows"]], sum(run["summary"]["wall_s"] for run in runs))
                    annotate_coverage(summary, expected, selected)
                    data["current_level"] = {"scenario": scenario, "users": users, "runs": runs, "summary": summary}
                    report(data, a.out)
                    a.registry.cleanup()
                    if a.registry.active:
                        raise RuntimeError("job benchmark chưa kết thúc sau drain")
                    if repetition < a.runs - 1:
                        time.sleep(a.cooldown)
                data["levels"].append(data.pop("current_level"))
                report(data, a.out)
                if data["levels"][-1]["summary"]["infra"] > 0:
                    data["excluded"].append({"scenario": scenario, "levels": [n for n in levels if n > users],
                                             "reason": "stop_after_infrastructure_errors"})
                    report(data, a.out)
                    break
                time.sleep(a.cooldown)
        data["phase"] = "complete"
    except KeyboardInterrupt:
        a.cancel_event.set()
        data["phase"] = "interrupted"
        print("INTERRUPTED: dừng tải mới và thu hồi job benchmark", flush=True)
    except Exception as error:
        data["phase"] = "failed"
        data["execution_error"] = str(error)
        raise
    finally:
        a.cancel_event.set()
        a.registry.cleanup()
        data["unresolved_owned_jobs"] = len(a.registry.active)
        report(data, a.out)
    print(f"REPORT {a.out / 'report.html'}", flush=True)


if __name__ == "__main__":
    main()
