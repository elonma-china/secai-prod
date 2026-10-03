# Assessment static analysis checkpoint

2026-10-04, Semgrep OSS1.179.0, exact assessmentbf06bc4311a8c2487abce8bf7612c04025eb9d56. Seven Python source files only, hashes in scope.json. Same isolated tool/metrics-off runner and Pro unavailable status as runtime scan; scope announced before execution, run-all. No app dependency/source edits, no live/GPU/model calls.

Six units ran: secrets/security-audit/TrailofBits/Python completed; elttam andApiiro partial due third-party rule compilation errors. All7files scanned,0findings,0failed/skipped/emptycoverage/oversized. **Partial scan; not a blanket security PASS.** Exact error IDs and thirdparty revisions retained; raw checksums and empty mergedSARIF recorded. Full raw results outside repo under isolated tool directory. Owned rule clones removed after recording revisions. Qualified tester provenance, replay safety and cryptographic trust-boundary correctness require independent code review; a no-finding SAST result cannot replace it.
