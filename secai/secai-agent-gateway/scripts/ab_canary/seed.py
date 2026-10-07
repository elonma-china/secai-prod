#!/usr/bin/env python3
"""Seed the A/B canary with clearly synthetic review data through the real API/store.

Run with the repo venv:  .venv/bin/python scripts/ab_canary/seed.py [--n 1000] [--pending 20]

Campaigns created (all synthetic, no real chat/user data):
  * "Đánh giá từ chat"                  (live source) ~50 scenario items: clear A/B winner, tie, both bad,
    skip, insufficient context, missing votes, conflicting votes, duplicate prompt/answers, long/markdown/code.
  * "Demo 2 vai trò — canary synthetic" (qa source) pending assignments for canary-user-1, used by the
    demo-user "Thử đánh giá A/B" dialog.
  * "QA canary synthetic bulk"          (qa source) --n valid, voted, admin-approved pairs (training threshold).
Idempotent: re-running skips what already exists. Tokens are read from the 0600 tokens file and never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

CANARY_DIR = Path(os.environ.get("AB_CANARY_DIR", "/tmp/ab-canary"))
ROOT = Path(__file__).resolve().parents[2]
LIVE_CID = "641f3b8e-8bbe-4603-bf12-fbb181e86d20"      # fixed id the live-pair flow uses
LIVE_TITLE = "Đánh giá từ chat"
DEMO_TITLE = "Demo 2 vai trò — canary synthetic"
BULK_TITLE = "QA canary synthetic bulk"
FORBIDDEN_PORTS = {5173, 8131, 8133, 8134, 18183, 18185, 18241, 18197, 18083, 18868}
USERS = {"user1": "canary-user-1", "user2": "canary-user-2", "user3": "canary-user-3"}
TAG = "[synthetic ab-canary]"


# ---------------------------------------------------------------- API client
class Api:
    def __init__(self, base: str, tokens: dict):
        self.client = httpx.Client(base_url=base, timeout=60, trust_env=False,
                                   limits=httpx.Limits(max_connections=16))
        self.tokens = tokens

    def call(self, who: str, method: str, path: str, **kw):
        headers = {"Authorization": "Bearer " + self.tokens[who]}
        if "content" in kw:
            headers["Content-Type"] = "application/x-ndjson"
        for attempt in range(6):
            r = self.client.request(method, path, headers=headers, **kw)
            if r.status_code in (502, 503) or (r.status_code == 500 and attempt < 2):
                time.sleep(0.5 * (attempt + 1)); continue
            break
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} as {who} -> {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else None

    def campaigns(self):
        out, offset = [], 0
        while True:
            page = self.call("admin", "GET", f"/review-api/campaigns?offset={offset}&limit=100")
            out += page["items"]
            offset += 100
            if offset >= page["total"]: return out

    def ensure_campaign(self, title: str) -> str:
        for c in self.campaigns():
            if c["title"] == title: return c["id"]
        return self.call("admin", "POST", "/review-api/campaigns", json={"title": title})["id"]

    def items(self, cid: str) -> dict:
        out, offset = {}, 0
        while True:
            page = self.call("admin", "GET", f"/review-api/campaigns/{cid}/items?offset={offset}&limit=100")
            out.update({i["external_id"]: i for i in page["items"]})
            offset += 100
            if offset >= page["total"]: return out

    def import_items(self, cid: str, records: list[dict]):
        for i in range(0, len(records), 500):
            body = "\n".join(json.dumps(r, ensure_ascii=False) for r in records[i:i + 500]).encode()
            self.call("admin", "POST", f"/review-api/campaigns/{cid}/imports", content=body)


def record(ext, messages, a, b, group, evidence=""):
    return {"external_id": ext, "prompt_messages": messages, "evidence": evidence, "source_group": group,
            "candidates": [{"id": "A", "text": a, "provenance": {"model": "synthetic", "source": "ab-canary"}},
                           {"id": "B", "text": b, "provenance": {"model": "synthetic", "source": "ab-canary"}}],
            "provenance": {"source": "ab-canary-synthetic", "synthetic": True}}


def user(text): return {"role": "user", "content": text}
def asst(text): return {"role": "assistant", "content": text}


# ---------------------------------------------------------------- scenario set
LONG_Q = (TAG + " Hệ thống nội bộ của chúng tôi gồm nhiều lớp. " +
          " ".join(f"Lớp {i}: dịch vụ web phía trước, cơ sở dữ liệu quan hệ, hàng đợi thông điệp, bộ nhớ đệm và cổng API." for i in range(1, 40)) +
          " Hãy liệt kê các rủi ro cấu hình thường gặp và cách rà soát từng lớp.")
MD_A = ("## Rà soát cấu hình\n\n| Hạng mục | Rủi ro | Cách kiểm tra |\n|---|---|---|\n| TLS | Giao thức cũ | Kiểm tra phiên bản tối thiểu |\n"
        "| Header | Thiếu CSP | Đọc phản hồi HTTP |\n\n- Bật log truy cập\n- Hạn chế quyền tài khoản dịch vụ\n\n> Chỉ rà soát trên hệ thống được phép.")
MD_B = "Kiểm tra TLS, header bảo mật và quyền tài khoản dịch vụ. Không có bảng chi tiết."
CODE_A = ("Dùng truy vấn tham số hoá:\n\n```python\ncur.execute(\"SELECT id FROM users WHERE email = ?\", (email,))\n```\n\n"
          "Không nối chuỗi từ dữ liệu người dùng vào câu lệnh SQL.")
CODE_B = ("Có thể escape ký tự đặc biệt:\n\n```python\nq = \"SELECT id FROM users WHERE email='\" + email.replace(\"'\", \"''\") + \"'\"\n```")

TOPICS = [
    ("Khác nhau giữa XSS phản chiếu và XSS lưu trữ là gì?",
     "XSS phản chiếu xuất hiện ngay trong phản hồi của một request; XSS lưu trữ được lưu ở máy chủ và chạy với mọi người xem. Cách phòng chống: mã hoá đầu ra theo ngữ cảnh và CSP.",
     "XSS là lỗi web. Hãy cẩn thận khi nhập dữ liệu."),
    ("CSRF được giảm thiểu bằng cách nào?",
     "Dùng token CSRF theo phiên, thuộc tính SameSite cho cookie và kiểm tra Origin/Referer với các thao tác thay đổi trạng thái.",
     "Chỉ cần dùng HTTPS là đủ để chặn CSRF."),
    ("Nguyên tắc đặc quyền tối thiểu nghĩa là gì?",
     "Mỗi tài khoản, dịch vụ chỉ nhận đúng quyền cần thiết cho nhiệm vụ và trong thời gian cần thiết; định kỳ rà soát và thu hồi quyền thừa.",
     "Là cấp quyền quản trị cho mọi người để họ làm việc nhanh hơn."),
    ("Khi nào nên xoay vòng khoá API?",
     "Theo chu kỳ cố định, ngay khi nghi ngờ lộ khoá, khi nhân sự rời dự án; dùng kho bí mật và không để khoá trong mã nguồn.",
     "Không cần xoay vòng nếu khoá đang chạy ổn định."),
    ("Phân biệt SAST và DAST.",
     "SAST phân tích mã nguồn tĩnh từ sớm trong vòng đời; DAST kiểm tra ứng dụng đang chạy từ bên ngoài. Hai phương pháp bổ sung cho nhau.",
     "SAST và DAST là hai tên gọi của cùng một công cụ."),
    ("Zero trust khác gì mô hình vành đai truyền thống?",
     "Zero trust không mặc định tin mạng nội bộ; mọi yêu cầu được xác thực, uỷ quyền theo ngữ cảnh và ghi log.",
     "Zero trust nghĩa là không dùng mật khẩu nữa."),
    ("What is the difference between hashing and encryption?",
     "Hashing is one-way and used for integrity or password storage with a salt; encryption is reversible with a key and protects confidentiality.",
     "They are the same thing; both turn text into random-looking data."),
    ("How should password storage be implemented?",
     "Use a slow, salted password hashing function such as Argon2id or bcrypt with a unique salt, plus rate limiting and breach checks.",
     "Store passwords with MD5 so that support can recover them."),
]


def scenarios() -> list[dict]:
    """Each entry: key, messages, A, B, votes[(user, 'A'|'B'|decision, comment)], adjudicate, group."""
    S: list[dict] = []
    def add(key, topic, votes, adj=None, a=None, b=None, messages=None, group=None, swap=False):
        q, ta, tb = TOPICS[topic % len(TOPICS)]
        if swap: ta, tb = tb, ta
        S.append(dict(key=key, a=a or ta, b=b or tb, votes=votes, adj=adj, group=group or f"canary-live-g{len(S)}",
                      messages=messages or [user(f"{TAG} {q}")]))
    for i in range(6): add(f"winA-{i}", i, [("user1", "A", "")], swap=False)             # clear winner A
    for i in range(6): add(f"winB-{i}", i, [("user1", "B", "")], swap=True)              # clear winner B (A is the bad one)
    for i in range(3): add(f"agreeA-{i}", i + 1, [("user1", "A", ""), ("user2", "A", "")])
    for i in range(3): add(f"agreeB3-{i}", i + 2, [("user1", "B", ""), ("user2", "B", ""), ("user3", "B", "")], swap=True)
    for i in range(4): add(f"tie-{i}", i, [("user1", "tie", "")], a=TOPICS[i][1], b=TOPICS[i][1] + " (diễn đạt khác, cùng nội dung)")
    for i in range(4): add(f"bothbad-{i}", i, [("user1", "both_bad", "Cả hai đều thiếu ví dụ và không nêu cách phòng chống.")], a=TOPICS[i][2], b="Không có thông tin hữu ích. " + TOPICS[i][2])
    for i in range(3): add(f"skip-{i}", i, [("user1", "skip", "")])
    for i in range(3): add(f"nocontext-{i}", i, [("user1", "insufficient_context", "Câu hỏi thiếu ngữ cảnh hệ thống cụ thể.")])
    for i in range(4): add(f"missing-{i}", i, [("user1", "A", "")], adj="assign:user2")  # 2 assigned, 1 submitted
    for i in range(2): add(f"novote-{i}", i, [], adj="assign:user1+user2")                 # assigned, nothing submitted
    for i in range(4): add(f"conflict-{i}", i, [("user1", "A", ""), ("user2", "B", "")])   # opposite votes
    # Duplicate prompt/answers: same pair, swapped pair order, same prompt different answers.
    add("dup-orig", 0, [("user1", "A", "")], group="canary-dup")
    add("dup-exact", 0, [("user1", "A", "")], group="canary-dup")
    add("dup-swapped", 0, [("user1", "B", "")], a=TOPICS[0][2], b=TOPICS[0][1], group="canary-dup")
    add("dup-prompt-other-answers", 0, [("user1", "B", "")], a="Chỉ cần lọc từ khoá <script>.", b="Mã hoá đầu ra theo ngữ cảnh HTML/JS/URL, thêm CSP và HttpOnly cho cookie.", group="canary-dup")
    # Long / markdown / code / multi-turn.
    add("long-question", 0, [("user1", "A", "")], a="Rà soát từng lớp: cấu hình mặc định, tài khoản dịch vụ, mã hoá đường truyền, ghi log, sao lưu và quản lý bí mật.",
        b="Hãy kiểm tra cấu hình.", messages=[user(LONG_Q)])
    add("markdown", 0, [("user1", "A", "")], a=MD_A, b=MD_B, messages=[user(f"{TAG} Cho tôi checklist rà soát cấu hình web dạng bảng.")])
    add("code", 0, [("user1", "A", "")], a=CODE_A, b=CODE_B, messages=[user(f"{TAG} Cách phòng chống SQL injection trong Python?")])
    add("multiturn", 1, [("user1", "B", ""), ("user2", "B", "")], swap=True, messages=[
        user(f"{TAG} Tôi đang xây dựng form đổi mật khẩu."), asst("Bạn cần bảo vệ form khỏi các tấn công nào?"), user("Chủ yếu là CSRF. Nên làm thế nào?")])
    # Already decided by admin (fills the Approved / Excluded tabs).
    for i in range(5): add(f"approved-{i}", i, [("user1", "A", "")], adj="winner:A")
    for i in range(2): add(f"excluded-tie-{i}", i, [("user1", "tie", "")], adj="tie", a=TOPICS[i][1], b=TOPICS[i][1] + " Nội dung tương đương.")
    add("excluded-skip", 3, [("user1", "skip", "")], adj="skip")
    return S


# ---------------------------------------------------------------- live campaign helper
HELPER = r'''
import json, sys
from secai_gateway.api.review_schemas import ReviewItemInput
from secai_gateway.state.review_store import ReviewStore
spec = json.load(sys.stdin)
store = ReviewStore(spec["db"])
with store.connection() as db:
    existing = {r[0] for r in db.execute("SELECT external_id FROM items WHERE campaign_id=?", (spec["cid"],))}
created = 0
for rec, actor in spec["items"]:
    if rec["external_id"] in existing: continue
    store.create_live_assignment(ReviewItemInput.model_validate(rec), actor); created += 1
print(json.dumps({"created": created}))
'''


def run_helper(spec: dict) -> dict:
    """Run the real ReviewStore in an isolated, env-less interpreter (importing secai_gateway builds an app)."""
    gw = CANARY_DIR / "gw"
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(gw / "home"), "LANG": "C.UTF-8", "AIRGAP": "true",
           "PYTHONDONTWRITEBYTECODE": "1", "GATEWAY_DB_PATH": str(gw / "gateway.db"),
           "SESSION_DB_PATH": str(gw / "sessions.db"), "REPORT_DB_PATH": str(gw / "reports.db"),
           "POC_CATALOG_DB": str(gw / "poc_catalog.db")}
    out = subprocess.run([sys.executable, "-c", HELPER], input=json.dumps(spec), text=True, capture_output=True,
                         cwd=gw, env=env, timeout=300)
    if out.returncode != 0:
        raise SystemExit("live helper failed: " + out.stderr[-500:])
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------- seeding steps
def decision_for(assign: dict, choice: str) -> str:
    """Map desired candidate A/B (or a literal decision) onto this assignment's left/right order."""
    if choice not in ("A", "B"): return choice
    return "left" if assign["left_candidate_id"] == choice else "right"


def vote(api: Api, iid: str, who: str, choice: str, comment: str):
    a = api.call("admin", "POST", f"/review-api/items/{iid}/assignments", json={"reviewer_ids": [USERS[who]]})["items"][0]
    body = {"decision": decision_for(a, choice), "comment": comment}
    api.call(who, "PUT", f"/review-api/assignments/{a['id']}/judgment", json=body)
    return a


def adjudicate(api: Api, iid: str, outcome: str):
    if outcome == "winner":
        body = {"outcome": "winner", "winning_candidate_id": "A", "reason": "Synthetic canary: admin xác nhận đáp án A."}
    else:
        body = {"outcome": outcome, "reason": "Synthetic canary: loại mẫu khỏi huấn luyện."}
    api.call("admin", "PUT", f"/review-api/items/{iid}/adjudication", json=body)


def seed_mixed(api: Api, db_path: Path) -> int:
    S = scenarios()
    recs = {s["key"]: record("ab-canary-live-" + s["key"], s["messages"], s["a"], s["b"], s["group"]) for s in S}
    firsts = []
    for s in S:
        voters = [v[0] for v in s["votes"]]
        adj = s["adj"] or ""
        first = voters[0] if voters else (adj.split(":")[1].split("+")[0] if adj.startswith("assign:") else "user1")
        firsts.append((recs[s["key"]], USERS[first]))
    run_helper({"db": str(db_path), "cid": LIVE_CID, "items": firsts})
    items = api.items(LIVE_CID)

    def work(s):
        item = items["ab-canary-live-" + s["key"]]
        iid = item["id"]
        if item["adjudication"]: return       # already seeded and decided (re-run)
        adj = s["adj"] or ""
        for who, choice, comment in s["votes"]:
            vote(api, iid, who, choice, comment)
        if adj.startswith("assign:"):
            for who in adj.split(":")[1].split("+"):
                api.call("admin", "POST", f"/review-api/items/{iid}/assignments", json={"reviewer_ids": [USERS[who]]})
        elif adj.startswith("winner:"):
            adjudicate(api, iid, "winner")
        elif adj in {"tie", "skip", "both_bad", "insufficient_context"}:
            adjudicate(api, iid, adj)
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(work, S))
    return len(S)


def seed_demo(api: Api) -> int:
    cid = api.ensure_campaign(DEMO_TITLE)
    recs = []
    for i, (q, a, b) in enumerate(TOPICS[:6]):
        recs.append(record(f"ab-canary-demo-{i}", [user(f"{TAG} {q}")], a, b, f"canary-demo-g{i}"))
    api.import_items(cid, recs)
    for it in api.items(cid).values():
        api.call("admin", "POST", f"/review-api/items/{it['id']}/assignments", json={"reviewer_ids": [USERS["user1"]]})
    return len(recs)


def bulk_record(i: int) -> dict:
    topic = TOPICS[i % len(TOPICS)]
    q = f"{TAG} Câu hỏi QA số {i}: {topic[0]}"
    return record(f"ab-canary-bulk-{i:05d}", [user(q)], f"Câu trả lời tham chiếu #{i}. {topic[1]}",
                  f"Câu trả lời yếu #{i}. {topic[2]}", f"canary-bulk-g{i}")


def seed_bulk(api: Api, n: int, pending: int) -> int:
    cid = api.ensure_campaign(BULK_TITLE)
    api.import_items(cid, [bulk_record(i) for i in range(n)])
    items = api.items(cid)
    todo = [items[f"ab-canary-bulk-{i:05d}"]["id"] for i in range(n)]
    keep_pending = set(todo[:max(0, min(pending, n))])

    def work(item):
        iid = item[1]
        if items[f"ab-canary-bulk-{item[0]:05d}"]["adjudication"]: return   # already seeded (re-run)
        rng = random.Random(item[0])
        choice = rng.choice("AB")                 # both A and B appear as "winner" over the set
        a = vote(api, iid, "user1", choice, "")
        if iid not in keep_pending:
            body = {"outcome": "winner", "winning_candidate_id": choice,
                    "reason": "Synthetic canary: admin xác nhận lựa chọn của user."}
            api.call("admin", "PUT", f"/review-api/items/{iid}/adjudication", json=body)
    with ThreadPoolExecutor(8) as pool:
        for k, _ in enumerate(pool.map(work, list(enumerate(todo)))):
            if (k + 1) % 250 == 0: print(f"  bulk {k + 1}/{n}", flush=True)
    return n


def spread_timestamps(db_path: Path, hours: float):
    """Make submitted_at differ visibly: backdate seeded judgments across `hours` (canary DB only)."""
    if hours <= 0: return
    if not str(db_path.resolve()).startswith("/tmp/ab-canary"): raise SystemExit("refusing to edit a non-canary DB")
    con = sqlite3.connect(db_path, timeout=30)
    try:
        rows = con.execute("SELECT j.id FROM judgments j JOIN assignments a ON a.id=j.assignment_id "
                           "JOIN items i ON i.id=a.item_id WHERE i.external_id LIKE 'ab-canary-%' ORDER BY j.submitted_at,j.id").fetchall()
        now = datetime.now(timezone.utc)
        total = max(1, len(rows))
        updates = [((now - timedelta(hours=hours * (1 - k / total)) - timedelta(seconds=k % 59)).isoformat(), r[0])
                   for k, r in enumerate(rows)]
        con.executemany("UPDATE judgments SET submitted_at=? WHERE id=?", updates)
        con.commit()
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:18261")
    ap.add_argument("--n", type=int, default=0, help="valid approved QA pairs for the Fine-tune threshold (e.g. 1000)")
    ap.add_argument("--pending", type=int, default=0, help="of --n, how many keep a user vote but no admin approval")
    ap.add_argument("--spread-hours", type=float, default=72, help="backdate seeded vote times across this window (0=off)")
    ap.add_argument("--skip-mixed", action="store_true")
    ap.add_argument("--skip-demo", action="store_true")
    args = ap.parse_args()

    base = httpx.URL(args.base)
    if base.host not in {"127.0.0.1", "localhost"} or (base.port or 80) in FORBIDDEN_PORTS:
        raise SystemExit("seed.py only targets a loopback canary gateway (never prod/reserved ports)")
    tokens = json.loads((CANARY_DIR / "tokens.json").read_text())
    api = Api(args.base, tokens)
    me = api.call("admin", "GET", "/review-api/me")
    if me["actor_id"] != "canary-admin": raise SystemExit("not a canary gateway")
    db_path = CANARY_DIR / "review.db"

    if not args.skip_mixed: print("mixed scenarios:", seed_mixed(api, db_path))
    if not args.skip_demo: print("demo assignments for canary-user-1:", seed_demo(api))
    if args.n: print("bulk pairs:", seed_bulk(api, args.n, args.pending))
    spread_timestamps(db_path, args.spread_hours)
    elig = api.call("admin", "GET", "/review-api/reward/training/eligibility")
    print("eligibility:", {k: elig[k] for k in ("valid_new_count", "invalid_count", "group_count", "counts", "eligible")})
    ov = {s: api.call("admin", "GET", f"/review-api/overview?status={s}&limit=1")["total"]
          for s in ("pending", "approved", "excluded")}
    print("overview totals:", ov)


if __name__ == "__main__":
    main()
