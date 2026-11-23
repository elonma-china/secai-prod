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
