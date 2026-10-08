"""/health dependency + KB-sync freshness (src/secai_gateway/api/health_deps.py)."""
from datetime import datetime, timezone

import pytest

from secai_gateway.api import health_deps as hd

NOW = datetime(2026, 10, 8, 4, 0, tzinfo=timezone.utc)  # 11:00 +07 on 8 Oct


def _log(tmp_path, body: str) -> str:
    p = tmp_path / "sync_cve.log"
    p.write_text(body, encoding="utf-8")
    return str(p)


OK_RUN = ("Starting sync at Thu Oct  8 03:00:01 AM +07 2026\n"
          "AutoScan CVE→KB sync exit=0 at Thu Oct  8 03:11:26 AM +07 2026\n")


def test_parse_cron_date_handles_short_tz() -> None:
    d = hd._parse_cron_date("Thu Oct  8 03:11:26 AM +07 2026")
    assert d is not None and d.utcoffset().total_seconds() == 7 * 3600


def test_fresh_cron_and_kb(tmp_path) -> None:
    st = hd.kb_sync_status(_log(tmp_path, OK_RUN), now=NOW)
    assert st["cron_ok"] and st["kb_fresh"] and st["last_run_exit"] == 0


def test_cron_firing_but_ingest_failing_is_stale_kb(tmp_path) -> None:
    """The 3-7 Oct 2026 outage: cron ran every night (exit=1) while the KB went stale."""
    body = ("Starting sync at Sun Oct  4 03:00:01 AM +07 2026\n"
            "AutoScan CVE→KB sync exit=0 at Sun Oct  4 03:04:00 AM +07 2026\n"
            "Starting sync at Wed Oct  7 03:00:01 AM +07 2026\n"
            "AutoScan CVE→KB sync exit=1 at Wed Oct  7 03:00:04 AM +07 2026\n")
    now = datetime(2026, 10, 7, 23, 0, tzinfo=timezone.utc)  # 8 Oct 06:00 +07
    st = hd.kb_sync_status(_log(tmp_path, body), now=now)
    assert st["cron_ok"] is True
    assert st["kb_fresh"] is False and st["last_run_exit"] == 1
    probs = hd.problems_from({"serving": {}, "databases": {}, "kb_sync": st})
    assert len(probs) == 1 and probs[0].startswith("kb_sync")


def test_cron_not_firing(tmp_path) -> None:
    now = datetime(2026, 10, 10, 4, 0, tzinfo=timezone.utc)
    st = hd.kb_sync_status(_log(tmp_path, OK_RUN), now=now)
    assert st["cron_ok"] is False
    assert hd.problems_from({"serving": {}, "databases": {}, "kb_sync": st})[0].startswith("cron")


def test_missing_or_empty_log_is_a_problem_not_a_crash(tmp_path) -> None:
    for st in (hd.kb_sync_status(str(tmp_path / "nope.log"), now=NOW),
               hd.kb_sync_status(_log(tmp_path, "garbage\n"), now=NOW)):
        assert st["cron_ok"] is False and st["detail"]


def test_problems_flag_down_dependencies() -> None:
    extra = {"serving": {"embedding": {"ok": False, "detail": "HTTP 404 (model)"},
                         "reranker": {"ok": True}},
             "databases": {"qdrant": {"ok": True}, "neo4j": {"ok": False, "detail": None}},
             "kb_sync": {"cron_ok": True, "kb_fresh": True}}
    assert hd.problems_from(extra) == ["embedding: HTTP 404 (model)", "neo4j: không phản hồi"]


def test_hostport_never_leaks_path_or_credentials() -> None:
    assert hd._hostport("http://user:pw@1.2.3.4:8000/v1?key=x") == "1.2.3.4:8000"
    assert hd._hostport("") is None


@pytest.mark.asyncio
async def test_probe_failure_is_captured_not_raised() -> None:
    class S:
        embedding_api_url = "http://127.0.0.1:1"
        embedding_api_key = "k"
        embedding_model = "m"

    row = await hd._check_embedding(S())
    assert row["ok"] is False and row["endpoint"] == "127.0.0.1:1" and "k" not in str(row.get("detail", "")).split()
