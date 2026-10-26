"""HTTP giả cục bộ để kiểm runner, không gọi production."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RUNNER = Path(__file__).with_name("bench_ccu_manifest.py")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, body, retry=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if retry is not None:
            self.send_header("Retry-After", str(retry))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.endswith("v3/translate"):
            self.reply(200, {"translated_text": "Traduction Pw0&*()_+"})
        else:
            self.reply(202, {"job_id": "owned-test-job", "status": "queued"})

    def do_DELETE(self):
        self.server.cancelled = True
        self.reply(202, {"status": "cancelled"})

    def do_GET(self):
        if self.server.mode == "poll429":
            self.reply(429, {"error": {"code": "RATE_LIMITED"}}, .01)
        elif self.path.endswith("/result"):
            self.server.result_calls += 1
            if self.server.result_calls == 1:
                self.reply(429, {"error": {"code": "RATE_LIMITED"}}, .01)
            else:
                self.reply(200, {"translated_text": "Traduction Pw0&*()_+" if self.server.mode != "missing" else "Traduction"})
        else:
            state = "running" if self.server.mode == "wait" and not self.server.cancelled else "cancelled" if self.server.cancelled else "failed" if self.server.mode == "failed" else "succeeded"
            self.reply(200, {"status": state,
                             "stage": "done", "error": {"code": "OCR_UNAVAILABLE"} if self.server.mode == "failed" else None})


class ManifestRunnerTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(RUNNER.exists(), "manifest runner is not implemented")
        spec = importlib.util.spec_from_file_location("bench_ccu_manifest", RUNNER)
        self.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runner)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "sample.txt").write_text("source Pw0&*()_+")
        self.sample = {"id": "sample", "path": "sample.txt", "group": "files", "format": "txt",
                       "size_class": "short", "source_lang": "en", "target_lang": "vi", "pages": None,
                       "route": "/ai/v2/translate/files", "preserve_literals": ["Pw0&*()_+"]}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.mode = "success"
        self.server.result_calls = 0
        self.server.cancelled = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
            self.tmp.cleanup()

    def execute(self, seconds=2):
        return self.runner.execute_sample(self.base, self.root, self.sample, time.monotonic() + seconds, .01)

    def test_result_429_retries_before_checking_literals(self):
        row = self.execute()
        self.assertEqual(row["status"], "succeeded")
        self.assertTrue(row["integrity_ok"])
        self.assertEqual(row["result_429"], 1)
        self.assertEqual(row["missing_literals"], [])

    def test_repeated_poll_429_obeys_deadline_and_cancels_only_owned_job(self):
        self.server.mode = "poll429"
        row = self.execute(.12)
        self.assertEqual(row["status"], "timeout")
        self.assertTrue(self.server.cancelled)
        self.assertGreater(row["poll_429"], 0)
        self.assertLess(row["total_s"], 1)

    def test_poll_sleep_reaching_deadline_does_not_start_another_request(self):
        clock, calls, events = [100.0], [], []

        def sleep(seconds):
            clock[0] += seconds

        def request(method, url, *args, **kwargs):
            calls.append(method)
            if method == "POST":
                return 202, {"job_id": "owned-deadline-job"}, {}
            if method == "DELETE":
                return 202, {}, {}
            if calls[-2] == "DELETE":
                return 200, {"status": "cancelled"}, {}
            return 0, {"error": {"code": "TimeoutError"}}, {}

        with patch.object(self.runner.time, "monotonic", lambda: clock[0]), \
                patch.object(self.runner.time, "sleep", sleep), \
                patch.object(self.runner, "request", request):
            row = self.runner.execute_sample(self.base, self.root, self.sample,
                                             deadline=101.0, poll=2, on_job=events.append)
        self.assertEqual(row["status"], "timeout")
        self.assertEqual(row["error"], "BENCH_DEADLINE")
        self.assertEqual(calls, ["POST", "DELETE", "GET"])
        self.assertEqual(row["cancel_requested"], 202)
        self.assertEqual(events[-1], {"event": "terminal", "job_id": "owned-deadline-job",
                                     "status": "cancelled"})

    def test_success_with_missing_literal_does_not_pass_quality(self):
        self.server.mode = "missing"
        row = self.execute()
        self.assertEqual(row["status"], "succeeded")
        self.assertFalse(row["integrity_ok"])
        self.assertEqual(row["missing_literals"], ["Pw0&*()_+"])

    def test_failed_job_keeps_actual_error_without_fetching_result(self):
        self.server.mode = "failed"
        row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error"], "OCR_UNAVAILABLE")
        self.assertEqual(self.server.result_calls, 0)
        summary = self.runner.summarize([row] + [dict(row, status="succeeded", error=None, integrity_ok=True)] * 30, 10)
        self.assertEqual(summary["infra"], 1)
        self.assertEqual(summary["verdict"], "FAIL")

    def test_text_route_accepts_json_result_and_counts_integrity(self):
        self.sample.update(group="text", format="text", route="/ai/v3/translate")
        row = self.execute()
        self.assertEqual(row["post"], 200)
        self.assertEqual(row["status"], "succeeded")
        self.assertTrue(row["integrity_ok"])

    def test_empty_measurement_never_passes(self):
        summary = self.runner.summarize([], 10)
        self.assertEqual(summary["verdict"], "NO_DATA")

    def test_mixed_schedule_gives_equal_weight_to_four_groups(self):
        from collections import Counter
        items = [dict(self.sample, id=f"file-{i}", format=f"format-{i}") for i in range(6)]
        items += [dict(self.sample, id=group, group=group) for group in ("text","images","scan")]
        pick = self.runner.make_picker(items, 7)
        self.assertEqual(Counter(pick(i)["group"] for i in range(16)),
                         {"text":4,"files":4,"images":4,"scan":4})

    def test_first_cycle_varies_sample_copy_across_run_seeds(self):
        items = [dict(self.sample, id=f"sample-{i}") for i in range(5)]
        first = {self.runner.make_picker(items, seed)(0)["id"] for seed in range(100)}
        self.assertEqual(first, {"sample-0","sample-1","sample-2","sample-3","sample-4"})

    def test_excluded_preflight_samples_make_successful_subset_partial(self):
        row = self.execute()
        summary = self.runner.summarize([row], 10)
        expected = [self.sample, dict(self.sample, id="not-ready", format="pdf-scan", group="scan")]
        self.runner.annotate_coverage(summary, expected, [self.sample])
        self.assertEqual(summary["verdict"], "PARTIAL")
        self.assertEqual(summary["coverage"]["ready"], 1)
        self.assertEqual(summary["coverage"]["total"], 2)
        self.assertEqual(summary["cells"]["scan/pdf-scan/short"]["n"], 0)

    def test_owned_registry_persists_and_cleans_only_registered_jobs(self):
        self.server.mode = "wait"
        registry = self.runner.OwnedJobs(self.root / "owned-jobs.jsonl", self.base)
        registry.event({"event": "accepted", "job_id": "owned-test-job", "sample_id": "sample"})
        restored = self.runner.OwnedJobs(self.root / "owned-jobs.jsonl", self.base)
        self.assertEqual(restored.active, {"owned-test-job"})
        restored.cleanup()
        self.assertTrue(self.server.cancelled)
        self.assertEqual(restored.active, set())

    def test_resume_rejects_changed_config_or_manifest_hash(self):
        data = {"base": self.base, "manifest": "manifest", "manifest_sha256": "hash", "config": {"duration":180}}
        self.runner.check_resume(data, self.base, "manifest", "hash", {"duration":180})
        with self.assertRaises(ValueError):
            self.runner.check_resume(data, self.base, "manifest", "different", {"duration":180})
        with self.assertRaises(ValueError):
            self.runner.check_resume(data, self.base, "manifest", "hash", {"duration":10})

    def test_manifest_rejects_escape_paths_and_filters_boundary_cases(self):
        path = self.root / "manifest.json"
        boundary = dict(self.sample, id="boundary", benchmark_role="boundary")
        path.write_text(json.dumps({"items": [self.sample, boundary]}))
        root, items, excluded = self.runner.load_manifest(path)
        self.assertEqual(len(items), 1)
        self.assertEqual(excluded[0]["id"], "boundary")
        path.write_text(json.dumps({"items": [dict(self.sample, path="../outside.txt")]}))
        with self.assertRaises(ValueError):
            self.runner.load_manifest(path)


if __name__ == "__main__":
    unittest.main()
