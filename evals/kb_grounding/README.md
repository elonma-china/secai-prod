# Live KB/model smoke evaluation

Run from the gateway root with the existing `.env` and services:

```sh
.venv/bin/python evals/kb_grounding/run_live_eval.py --private-raw
```

The evaluator uses the current source's FastAPI app in-process and its real
`LinchRunner`/configured provider. It reads the real Qdrant collections and Neo4j
graph. It requires usable exact context for `CVE-2021-41773` and successful empty
lookups for `CVE-2099-99999`; use `--cve` or `--absent-cve` to select other records.
A KB outage produces `blocked`, never a passing absent-CVE result.

Case B requests a grounded PoC research report through JSON and SSE with an empty
temporary catalog. Case C checks both paths return `not_generated` without invoking
the runner/model. Checks include synthesis provenance/status, raw user prompt,
ephemeral context delivery, displayed session history, and joined SSE tokens equal
to `final.content`. This is a structural smoke evaluation, not a score for factual
quality or exploit capability. Artifact preservation and deterministic retry cases
A/D remain in the offline regression suite.

The process isolates catalog, artifacts, ledger, project/session state and caches
in a temporary directory. It enables air-gap and disables web retrieval, GitHub
on-demand, consolidation, verification and exploit execution. Existing `.env`
files, services, production catalogs and sessions remain untouched. It never runs
generated code, subprocesses, Docker or `evals/exploit_lab/run_lab_eval.py`.

Defaults allow **two actual inference calls in total**, including any agent retry.
If the model uses tools/retries and consumes the budget, remaining requests are
reported `blocked`. To authorize a larger bounded run explicitly:

```sh
.venv/bin/python evals/kb_grounding/run_live_eval.py --timeout 180 --max-provider-calls 6
```

The summary defaults to a private directory outside the repo under `/tmp`; set
`--output /tmp/my-kb-eval/summary.json` for a chosen path. `--private-raw` additionally
stores redacted responses and displayed history there for independent review. Files
use mode `0600`. Console/summary contain hashes, counts, model identity, statuses
and hook summaries, without credentials, full KB context or generated code.
Exit code is `0` only when every check passes, otherwise `2`.

Exact CVE prefetch uses Qdrant scroll and a Neo4j read transaction, without
embedding/reranking. If the model invokes broader retrieval tools, the configured
embedding/reranker must also work. A `/health` response alone is insufficient.

By default, the evaluator selects `cve_kb_knowledge_lab` as a supplemental collection.
The runtime feature itself is opt-in through `QDRANT_KB_SUPPLEMENTAL_COLLECTIONS`
(comma-separated; empty by default). The primary collection remains configured by
`QDRANT_COLLECTION`. Supplemental reads require `source=ghsa` and an exact, single-CVE
`cve_refs` match; they cannot establish CVE existence or replace missing primary evidence.
