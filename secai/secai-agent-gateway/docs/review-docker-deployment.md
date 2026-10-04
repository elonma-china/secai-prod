# Review MVP Docker deployment

**Current limitation:** the retained original rollback container disappeared during concurrent deployment activity. The identity guard correctly blocks rollback before stopping the live gateway. Original image/configuration and database snapshots remain, but reconstruction and verification are required before rollback is ready. No production rollback was attempted.

Deployed 2026-10-04: gateway port 8131, separate frontend port 5174, image `secai-agent-gateway:review-mvp-20261004`. Existing frontend 5173 remains available. Review uses its own SQLite volume and hashed credential file. Private credentials and deployment snapshots are outside Git in `/home/ict/secai-releases/review-mvp-20261004`.

The image overlays reviewed changes on the exact prior running image, preserving its dependencies and existing endpoint code. The release preserves actual running environment, mounts and network. The frontend static server proxies `/review-api`, including PUT requests.

Verification: feature tests 122 passed; frontend build passed; isolated Docker canary exercised authenticated import, assignment, judgment, adjudication, export hashes, real-model A/B and template revision/render through the frontend proxy. Activation checked health, authenticated review API and frontend HTML. Original-image rollback was rehearsed on the isolated canary. Full repository suite was not completed successfully; external Bughunt/catalog dependencies remain a limitation.

Rollback command:

```sh
/home/ict/secai-releases/review-mvp-20261004/rollback.sh
```

Rollback retains review and shared data. SQLite backups are retained privately but not automatically restored, to avoid discarding post-deployment writes. Production rollback itself was not performed. The script refuses to overwrite a gateway/frontend subsequently deployed by another agent.

Concurrent deployments: consult `/home/ict/secai-releases/DEPLOYMENT-STATE.md`, acquire `/home/ict/secai-releases/gateway-deploy.lock`, verify current container identity and integrate all active features before promotion. Lock coordination is advisory and requires every deploying agent to participate. Do not re-run this release activation or use broad compose up/down against the shared stack.
