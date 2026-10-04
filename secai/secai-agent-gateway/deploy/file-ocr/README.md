# File import Docker release — 2026-10-04

Deployed successfully to primary gateway `http://127.0.0.1:8131` at 19:02 ICT.
Both `secai-docker-gateway` and added `secai-file-ocr-worker` are running healthy.
Product source is commit `52d3512`: reviewed file/OCR source `c53fdfe` plus the
exact previously deployed review source and its six test modules/fixture.
No main merge or push was performed by the deployment task.

## Images and rollback

New tag: `secai-agent-gateway:file-ocr-review-final-20261004`

New immutable image:
`sha256:fb9bcf48496c0aea8d3f1113251fc0287893154c135c1b54c1f8199acddfcd53`

Rollback tag: `secai-agent-gateway:rollback-before-file-ocr-final-20261004`

Previous review image:
`sha256:fcbd6014319ea3e42972f7406a5e15b31637bdc31dffbedb455c48171a4ccb44`

To return to the previous release, including its review API:

```sh
python3 /home/ict/secai-docker-rollbacks/file-ocr-final-20261004/release.py rollback
```

The command stops/removes only the added file translation worker, recreates the
gateway with the old immutable image and saved environment/command/mounts, and
checks health, exact image ownership and the old API contract. Existing volumes,
review data and new translation job data remain intact. It does not restore a
stale database copy. Existing session/review database schemas were not migrated.

Private snapshots and scripts are in
`/home/ict/secai-docker-rollbacks/file-ocr-final-20261004` (directory 0700;
configuration JSON 0600). They contain credentials; never commit or publish them.
`deploy.log` records the successful rollout and live smoke. The build recipe and
snapshotted live/merged source are retained there. The rollout command was:

```sh
python3 /home/ict/secai-docker-rollbacks/file-ocr-final-20261004/release.py deploy
```

Deployment compares the original container ID and image before any mutation.
A local file lock serializes use of this script. Readiness checks exact image and
compose-file ownership, gateway health, worker health and API paths, then runs
`smoke.py`. Failure invokes rollback only if the current gateway still belongs to
this release. Manual rollback has the same ownership guard. A future deployment
needs a fresh snapshot; rerunning this one is deliberately refused after drift.

## Verification and scope

The isolated candidate and deployed API both passed:

- Health HTTP 200; all original OpenAPI route methods, parameters, and request/
  response definitions preserved, including review and legacy report routes.
  Multi-method operation IDs are ignored because FastAPI derives them from set
  iteration order; the HTTP method contracts remain compared exactly.
- Review endpoint without credentials remains HTTP 401.
- TXT and DOCX upload jobs completed; identity results matched fixture text.
- Real TXT en→vi completed: “Xin chào thế giới. Đây là một bài kiểm tra dịch thuật.”
- PDF rejected with HTTP 415.
- Image upload returned HTTP 503 `DEPENDENCY_UNAVAILABLE`: OCR is unconfigured.
- Legacy `.doc` upload returned HTTP 503 `DEPENDENCY_UNAVAILABLE` on production:
  the external converter is unavailable. No Docker socket privilege was added.

Candidate rollback rehearsal restored the exact previous review image, returned
health 200, retained review routes and removed v2 routes. Production was not
bounced solely to rehearse rollback. Script syntax and git diff whitespace checks
passed. The separate full backend suite is reported by the integration reviewer;
these smoke results are not an assertion that the whole suite or OCR quality passed.

The API retains its original command, security settings, credentials mount,
volumes, network, extra host entry and port. The worker uses memory sessions,
disables Redis, and shares its dedicated jobs/artifacts subtree under
`/app/data/translate-v2`. Only gateway and the added worker were started with
`--no-deps`; review frontend, report backend, separate translation stack, model
services and database dependencies were left running unchanged.

Candidate containers were removed after verification. Its isolated volume and
immutable images remain available. No GPU/model runtime was deployed.

## Earlier attempt

The initial candidate based on old image `1607dd8eadc9` passed smoke but was not
deployed: another session replaced production with the review release while
preparation ran. Compose returned a name conflict. Guards were added and verified
to refuse both deployment and rollback against that other release. After the user
assigned this session the final deployment, a fresh snapshot used the review
image above and its actual Compose project `secai-review-release`, avoiding the
old project mismatch. The old snapshot under `file-ocr-20261004` is historical and
must not be used to roll back this release.
