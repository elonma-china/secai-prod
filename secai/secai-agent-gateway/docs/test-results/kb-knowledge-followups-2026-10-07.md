# Knowledge KB — follow-ups: measurement, CVE discovery, injection red-team (2026-10-07)

Follow-up to `kb-knowledge-grounding-2026-10-07.md`. Everything here ran on throwaway canary containers
(`secai-agent-gateway` image `062b3ecabdff` with the prod environment and the prod Qdrant read-only, or the lab
collection for the red-team). Nothing in this document was deployed.

## 1. Repeated core eval (prod configuration, 3 runs)

Three runs of the 100-question set against the running image, answers shuffled and graded blind by one independent
judge agent (strict about invented identifiers). PASS=1, PARTIAL=0.5, FAIL=0.

| run | score | answers with a suspected fabricated id |
|---|---|---|
| 1 | 88.5 | 10 |
| 2 | 85.5 | 14 |
| 3 | 92.5 | 6 |
| mean ± sd | **88.8 ± 2.9** | |

* The earlier single-run 93.0 came from a different judge and one draw. The two are not comparable; the 80 → 93
  comparison in the first report used one judge on both sides and still holds as a *relative* result.
* Weak or unstable groups (mean of runs, min–max): `cve_alias` 56 (37–75), `cve_discovery` 60 (56–69),
  `followup` 83. All others ≥ 91.
* Artifacts: `kb-knowledge-followups-artifacts/out_*.json` + `labelmap.json` (run ↔ label).

## 2. Monitoring

`evals/core_eval/monitor_chat.py` reads the gateway log (5xx on chat routes, restarts, tracebacks, knowledge
pre-fetch ms, hits=0 rate per scope) and can send synthetic probes for TTFT. Prod since the flag was switched on:
0 chat 5xx, 0 restarts, 0 tracebacks, hits=0 rate 0 %. There was almost no organic traffic (the only chat requests in
8 h were test requests), so the "30–60 min under real load" criterion has no real-user data yet. Probe TTFT p50 3.17 s /
p95 4.96 s was measured while three evals were running against the same LLM and is not a baseline.

## 3. CVE discovery

Root causes found:

1. **Routing.** 19 of 60 independently written discovery questions ("Which 2021 Apple zero-day …", "Is there a CVE
   for …", "… dính CVE SQL injection nào") were routed to `concept` / `concept_kb`, so no CVE search ran before
   the model answered.
2. **Vocabulary.** Citrix Bleed's record says "NetScaler ADC / Gateway … sensitive information disclosure"; nothing in
   it says Citrix session hijack, so a question in the user's words cannot match.
3. **ProxyLogon** has an alias on one of its four CVEs only.

Change (all behind `CHAT_DISCOVERY_ENABLED`, default off; off = previous routing and search):
`_is_cve_discovery` routing, vendor/effect synonyms for the query (`retrieval/discovery.py`), and a KEV-only candidate
pass whose members join the rerank pool without the KEV score boost (with the boost, KEV noise outranked relevant
results: recall@5 fell 0.85 → 0.75 in the first attempt).

Independent set: 60 questions written by a separate agent from sampled records (40 KEV, 20 non-KEV controls), plus the 8
original `cve_discovery` questions.

| measure (n = 68) | flag off | flag on |
|---|---|---|
| routed to a CVE search | 69 % | 87 % |
| retrieval recall@5 / @10 | 0.853 / 0.882 | 0.868 / 0.897 |
| end-to-end: answer names the gold CVE | 61.8 % | **80.9 %** (KEV 57.5 → 85, control 75 → 85) |
| answers containing a CVE id not in the KB | 0 | 0 |
| mean TTFT under load | 3.28 s | 3.79 s |

The end-to-end run is one pass per side, so ±1 question of noise is ~1.5 pts per group. The 8 original questions are
unchanged (50 %): core-023/026/027/028 still miss (the CWE ones accept only a gold list, although any real KEV CVE of
that CWE is a defensible answer). Citrix Bleed needs data, not code: an alias line with the effect for famous CVEs
(extend `evals/rag_eval/alias_enrich.py`). That is a write to the prod collection and was not done.

Routing safety: with the flag on, 0 of the other 339 questions (core 100, knowledge set, routing fixtures) change
route; all 16 changes are in the discovery set.

## 4. Prompt-injection red-team of the knowledge context

Eight payload styles (override, system-prompt exfiltration, closing the untrusted tag, markdown-image exfiltration,
tool abuse, bad security advice, citation hijack, Vietnamese override) were embedded in password-storage chunks in
the **lab** collection (deleted afterwards; prod untouched). The poisoned text was confirmed to be in the model
context for every run.

Result: **0 of 64** runs followed an injected instruction (2 flagged by the detector were the model explicitly
rejecting the bad advice, checked by hand). Two further facts: the 6 chunks that really contain attack strings
("ignore previous instructions") are labelled `contains_attack_example` and are excluded from knowledge search;
the injection detector at ingest was widened (`disregard/forget … rules`, "you are now in developer mode", Vietnamese
"bỏ qua mọi hướng dẫn trước"), changing no existing label. One side effect: in two answers the model mentioned the
poisoned "Redteam" documents to the user, which `chat_knowledge.md` forbids; real chunks do not contain such text.

## 5. Tests

Full suite on the branch: 1896 passed, 7 failed. The same 7 fail on the unmodified branch in this environment
(PoC drill-down needs the real catalog, `test_gateway` exploit stream, `test_bughunt_phase0`): no regression from
this change. New: `tests/test_cve_discovery.py` (16), chunker cases for the wider injection detector.

## 7. Routing precision (P2)

Two independent sets written by separate agents: A (240 questions, used while tuning) and B (150, never tuned on).
Look-alike = ordinary engineering questions that reuse words like ssh, upload, certificate, token.

| | knowledge recall | CVE recall | look-alike false trigger |
|---|---|---|---|
| before (A) | 94 % | 97 % | 28 % |
| after, A (tuned on) | 97 % | 100 % | 10 % |
| after, **B (held out)** | **88 %** | **94 %** | **11 %** |

Changes: plain "how do I / làm sao / hướng dẫn" no longer counts as security intent next to an ambiguous word; `ssh`
moved from always-security to ambiguous; added `sdlc/sast/dast`, "quản lý secrets", string-built SQL snippets, and
"show/list/find … vulnerabilities" / "tìm CVE …" as discovery. 0 of the 339 earlier questions change route. Because a
route only requests a reference block, the user-visible error is a block injected for a look-alike: 25 % before,
10 % after on A+B (105 look-alikes); the dense gate (0.55) was left alone, since raising it to 0.60 costs 7 points of
knowledge recall for 7 points fewer injections. `tests/test_routing_eval_sets.py` pins floors on both sets.
Known B misses: open redirect, TOCTOU, cloud-IAM privilege escalation, billion-laughs — plain knowledge questions
with no vocabulary the router knows; fixing them needs a semantic signal, not more keywords.

## 8. Latency (P3)

Knowledge pre-fetch p50 1.36 s was embed 763 ms + rerank 553 ms; Qdrant and BM25 are ~40 ms. The serving host is
~240 ms away and each search opened new connections (warm: embed 267 ms, rerank 290 ms), and the input classifier
embeds the same prompt that the pre-fetch embeds again. `EMBEDDING_KEEPALIVE_ENABLED` → 0.83 s;
`+ EMBEDDING_CACHE_TTL_S=300` → 0.46 s when the prompt was already embedded (the cache hit is the common case in
chat, where the classifier runs first). Rerank time does not depend on the candidate count (272–315 ms for 4–20
documents), so shrinking the window would not help. Quiet probe, 8 questions: prod p50 3.21 s / p95 5.01 s; canary
with all flags p50 2.24 s / p95 5.74 s.

## 9. All new flags together, blind A/B on the 100-question set

Canary with `CHAT_DISCOVERY_ENABLED`, keep-alive and cache vs the running image, 3 runs each, the six answers of every
question graded blind by one judge per third of the set (strict about invented identifiers).

| | running image | all flags |
|---|---|---|
| mean of 3 runs | 89.2 (sd 2.0) | **93.2** (sd 1.6) |
| answers with a suspected fabricated id (3 runs) | 32 | 15 |
| TTFT median / p90, same load | 3.06 / 3.82 s | **1.93 / 2.86 s** |

Paired per question: 18 better, 12 worse, 70 equal; mean +0.040, bootstrap 95 % CI [0.005, 0.078]. The gain is
concentrated in `cve_alias` (54 → 92: alias questions now reach the CVE search) and `taxonomy`. `cve_discovery`
stays at 54 → 58: core-025 (ProxyLogon chain) got worse (0.33 → 0.00; the model presents ProxyShell CVEs as the
chain, because only one ProxyLogon CVE has the alias in the KB), core-023/026/027/028 are unchanged. The 12 "worse"
questions are mostly one PARTIAL out of three runs (irrelevant ATT&CK/CAPEC ids listed for a CVE, a wrong detail in a
fix snippet); no group fell for a systematic reason, and groups of 3–6 questions move by 8–11 points per question.
A larger sample would be needed to claim any per-group change other than `cve_alias`.

## 10. Finding to act on: GHSA batch is in prod

`cve_kb_hybrid` holds 308 points of batch `knowledge-ghsa-2026-10-07` (source `ghsa`). They arrived from the ingest
that was interrupted at 12:18, which had already written them. They sit outside entity scope; they can appear in
concept/remediation answers. Legal sign-off for CC-BY-4.0 is pending. Remove with
`kb_knowledge.py rollback --collection cve_kb_hybrid --batch knowledge-ghsa-2026-10-07 --allow-prod`, or keep.

## 11. Round 2 (2026-10-08): data fixes, semantic routing, release to prod

Everything below was requested ("update theo khuyến nghị") and **deployed to the prod gateway**; backups and rollbacks are listed at the end.

### 11.1 Data
* **GHSA batch removed** from `cve_kb_hybrid` (308 points, batch `knowledge-ghsa-2026-10-07`; sign-off for CC-BY-4.0 was pending). Backup of the points with vectors: `/home/ict/secai-releases/knowledge-followups-20261007/data/ghsa-backup-points.json`. Knowledge points stay at 8,386. The collection total also grew by ~5.3k CVE points that this work did not write (nightly CVE sync).
* **Alias enrichment** (`evals/rag_eval/alias_enrich_v2.py`): the core chunks of CVE-2023-4966 (Citrix Bleed: the record never says "session", so a question in the user's words found nothing) and of the four ProxyLogon CVEs (only one carried the name) now start with a "Tên gọi phổ biến" line that includes the effect; 7 points re-embedded (dense + bm25). Backup of old text+vectors: `data/alias-v2-backup.json`; `alias_enrich_v2.py restore --backup` puts them back. Retrieval, 68 discovery questions: recall@5 0.868 → 0.882; the 8 original questions 0.50 → 0.625; core-025 (ProxyLogon members) and core-026 (Citrix Bleed) now rank first.

### 11.2 Where the databases disagreed with the keywords
Routing was decided by keywords; the gates that were meant to catch mistakes did not work:
* **The knowledge rerank gate (`knowledge_min_rerank` 0.9) was inert.** Candidates beyond the rerank window (10) carry no rerank score and the rule `rerank_score is None or >= 0.9` let them through, so a block of reference text was handed over whenever the keyword router asked for one (measured: block returned for 390 of 390 questions). With `gate=` the unjudged candidates no longer pass.
* Semantic evidence separates the classes much better than the keywords: best rerank of the knowledge scope vs knowledge questions and look-alikes, AUC 0.968; best rerank among CVE records vs look-alikes, AUC 1.000 (A+B) / 0.98 (C).

`CHAT_SEMANTIC_ROUTING_ENABLED` (default off, needs the knowledge flag): both databases are asked at once (latency = the slower one). Keyword hit → the reranker must confirm it (best ≥ 0.5; strong evidence keeps only chunks ≥ 0.9). No keyword → reference text only if best rerank ≥ 0.99 and cosine ≥ 0.62. A CVE record that answers almost verbatim (rerank ≥ 0.998, cosine ≥ 0.60) and no strong reference text → CVE lookup, with the mitigations appended when the question also asks for them. Also: an explicit "liệt kê CVE / cho xin số CVE / list CVEs" needs no capitalised product name (nginx, runc), and `harden*`, "cách sửa" count as remediation. Thresholds were chosen on sets A+B (390 questions) and checked on **set C: 240 questions written by a separate agent that never saw the code or the other sets**.

### 11.3 Routing, measured through the real chat API (what the model is actually handed)
`evals/core_eval/run_route_e2e.py` reads only the pre-fetch frames of `/ai/chat/stream`. Image running before this release vs this release (all flags):

| set | cve_search | knowledge | look-alike wrongly grounded | both (CVE+mitigation) |
|---|---|---|---|---|
| A (240, tuned on) | 0.70 → **1.00** | 0.94 → 0.95 | 0.25 → **0.02** | – |
| B (150) | 0.66 → **1.00** | 0.82 → **0.94** | 0.29 → **0.07** | – |
| **C (240, held out)** | 0.48 → **0.94** | 0.78 → **0.88–0.93** | 0.36 → **0.09** | 0.15 → 0.30 |

(Set C knowledge was 0.93 on the src-mounted canary and 0.88 on the built image and on prod: the 3 s pre-fetch time-box makes the borderline questions load-dependent.) Known remaining misses on C: "both" questions where only the CVE block arrives (the mitigation text is not always found), a few look-alikes with real security vocabulary (jwt malformed in a Jest test, sts:AssumeRole, mixed content), famous names the CVE store does not tag (Heartbleed with c_rr 0.985).

### 11.4 Answer quality, 100-question core set, 3 runs per side, blind shuffled judges (same procedure as §9)
| | image before | this release |
|---|---|---|
| mean | 92.0 | **93.5** |
| answers with a suspected fabricated id | 24 | 22 |
| TTFT median / p90 | 3.06 / 3.66 s | **1.88 / 2.79 s** |

Paired per question: 13 better, 11 worse, 76 equal; difference +1.5, 95 % CI [-0.8, 4.3] — **no regression, no significant gain on this set**; it is saturated (the earlier 89.2 → 93.2 came mostly from alias questions that this release's data fix now gives both sides). The routing gains of §11.3 are not visible here because the core set has few look-alikes. The one question that got worse (core-015 Zerologon) takes the same route on both sides; the model wrote a wrong CVSS vector in all three new runs (generation variance, not routing). Full suite: 1923 passed, the same 7 environment failures as before.

### 11.5 Release
Overlay image `secai-agent-gateway:followups-20261008` = `sha256:7bf54773d97f…` on top of `062b3ecabdff…` (6 files: `agent/hooks.py`, `api/routes.py`, `config.py`, `retrieval/{qdrant,embedding,discovery}.py`). Applied in two steps with the shared lock, guards and profile comparison: `latency` (keep-alive + vector cache), then `full` (+ discovery + semantic routing). After: 0 chat 5xx, 0 tracebacks, 0 restarts; knowledge pre-fetch p50 392 ms / p95 670 ms (was 1.36 s); probe TTFT p50 2.2 s / p95 2.9 s; set C through prod gives the same numbers as the canary; Citrix Bleed, open-redirect and Spring4Shell questions are now grounded on the right database.
Scripts, profiles and snapshots: `/home/ict/secai-releases/knowledge-followups-20261007/` (`apply-gateway.sh latency|full`, `rollback-gateway.sh flags|all`, `build.sh`, `gen.py`). Rollback: `rollback-gateway.sh flags` (discovery + semantic routing off, keep the image), `rollback-gateway.sh all` (previous image `062b3eca…`). Data: `kb_knowledge.py` GHSA points can be re-upserted from `data/ghsa-backup-points.json` (not recommended before legal sign-off); `alias_enrich_v2.py restore`.
Artifacts: `kb-knowledge-followups-artifacts/p4/` (route results per set, judge outputs, features).
