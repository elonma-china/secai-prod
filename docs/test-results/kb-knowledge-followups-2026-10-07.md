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

## 6. Finding to act on: GHSA batch is in prod

`cve_kb_hybrid` holds 308 points of batch `knowledge-ghsa-2026-10-07` (source `ghsa`). They arrived from the ingest
that was interrupted at 12:18, which had already written them. They sit outside entity scope; they can appear in
concept/remediation answers. Legal sign-off for CC-BY-4.0 is pending. Remove with
`kb_knowledge.py rollback --collection cve_kb_hybrid --batch knowledge-ghsa-2026-10-07 --allow-prod`, or keep.
