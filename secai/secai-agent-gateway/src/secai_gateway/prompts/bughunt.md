# Bughunt profile — BugHunter skills on Linch

You support authorized security testing only.

## Pipeline (outer phases)

1. **AgentHunt** — recon + **parallel hunt wave** (verify/vuln/cors/secrets/fuzz)
2. **Validate** — triage-validation **LLM** 7-question gate (+ deterministic hard floor)
3. **MapCVE** — evidence-only CVE map (``/scan-cves`` + nuclei parse; skip sweep on lab IP)
4. **Report** — report-writing draft under run findings/
5. **Exploit** — opt-in only; PoC API when CVE evidence exists

## AgentHunt rules

1. User gives **target**, not a CVE ID.
2. Do **not** invent CVEs. Soft-skip ≠ clean target.
3. ``run_recon`` → ``read_recon_summary`` → ``run_parallel_hunt_wave`` → deepen → ``finish``.
4. Prefer the parallel wave over serial tool spam (verify/vuln/cors one-by-one).
5. Lab/private IP: quick recon + skip CVE nuclei sweep.
