# Exact historical baseline invocation

Retrieved verbatim by baseline SOL from its original tool call on 2026-10-04. Historical evidence, not a portable deployment script. For another checkout substitute only its source path, private scratch paths, report names and explicitly document interpreter/dependency differences; never write into the original baseline artifacts.

Cwd: `/home/ict/secai-translate-baseline-wt/secai/secai-agent-gateway`.

```sh
set -o pipefail
env -i PATH=/usr/bin:/bin HOME=/tmp/secai-translate-baseline-ce9cd47 XDG_CACHE_HOME=/tmp/secai-translate-baseline-ce9cd47/cache HF_HOME=/tmp/secai-translate-baseline-ce9cd47/cache/hf HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/secai-translate-baseline-ce9cd47/pytest-plugins:/home/ict/secai-translate-baseline-wt/secai/secai-agent-gateway/src GATEWAY_DB_PATH=/tmp/secai-translate-baseline-ce9cd47/data/gateway.db POC_CATALOG_DB=/tmp/secai-translate-baseline-ce9cd47/data/poc-catalog.db EXPLOITDB_ROOT=/tmp/secai-translate-baseline-ce9cd47/data/exploitdb POC_SOURCES_ROOT=/tmp/secai-translate-baseline-ce9cd47/data/sources POC_ARTIFACT_STORE=/tmp/secai-translate-baseline-ce9cd47/data/artifacts SESSION_DB_PATH=/tmp/secai-translate-baseline-ce9cd47/data/sessions.db SESSION_STORE_BACKEND=memory REDIS_URL= INPUT_CLASSIFIER_WEIGHTS_PATH=/nonexistent/hermetic-sanitize-head HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9 NO_PROXY= RES_OPTIONS='attempts:1 timeout:1' timeout 1200s /home/ict/AIEngines/secai/secai-agent-gateway/.venv/bin/python -m pytest -q --tb=short --timeout=10 --timeout-method=signal --junitxml=docs/test-results/translate-baseline.full-timeout.junit.xml 2>&1 | tee docs/test-results/translate-baseline.full-timeout.stdout.log
code=$?
print -r -- "$code" > docs/test-results/translate-baseline.full-timeout.exitcode
exit "$code"
```

Plugin preparation: `mkdir -p /tmp/secai-translate-baseline-ce9cd47/pytest-plugins` followed by `uv pip install --target /tmp/secai-translate-baseline-ce9cd47/pytest-plugins --no-deps pytest-timeout`, which resolved 2.4.0. Original interpreter was read-only. Completed result:925 collected,905passed,19failed,1skipped,0errors;exit1. No rerun was performed to retrieve this command.
