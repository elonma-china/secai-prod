# Assessment final-candidate static analysis

2026-10-04. Exact commit8e53a00a8adf710dd630196de8341a2585e5d97a; seven Python source files exported from Git, hashes in scope.json. Semgrep OSS1.179.0, run-all through skill run-scans.sh, metrics OFF, jobs2,20MB max. Same rules as prior checkpoint, no autofix or application dependency change.

Six units ran: secrets, security-audit, Python and TrailofBits complete; elttam and Apiiro partial due12 and4 third-party rule compilation errors respectively. Every scan covered all7 files;0findings,0failed/skipped/empty-coverage/oversized. Error IDs/revisions/raw checksums/SARIF retained. Pro unavailable; no cross-file Pro/CodeQL claim. This is a partial SAST checkpoint, not blanket security acceptance or proof of trusted benchmark provenance. Independent review4 separately evaluates corrected protocol, lease, gate minima and filesystem race handling.

Raw artifacts /tmp/secai-multilang-semgrep/assessment-final-results. Temporary third-party clones removed after revision recording. No live/model/GPU calls. Prior bf06bc4 scan remains historical.
