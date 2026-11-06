# Preflight output incident

During initial read-only Vast metadata collection, an agent printed an unsanitized metadata response containing credential fields (API key and notebook token) into tool output. Root instructed the agent to stop raw output and use allowlisted metadata only; the user was notified. Values are intentionally omitted here and were not copied into project reports or committed configuration.

Credential rotation has **not** been performed: the incumbent shared services and other agent workload depend on their current configuration. This record is an unresolved operational follow-up, not a claim that terminal/transcript history was purged or credentials invalidated. Do not print the values again. Coordinate credential replacement with the serving owner before the next production handoff. Subsequent metadata reports use sanitized fields; no project .env was committed.
