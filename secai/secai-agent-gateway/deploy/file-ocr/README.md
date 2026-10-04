# File import Docker candidate — 2026-10-04

Status: candidate passed; production deployment **not completed**. A concurrent
review deployment replaced the gateway during preparation. Compose returned a
container-name conflict on cutover and attempted rollback. The running review
image remained healthy. Do not run the old snapshot against that deployment.

Candidate source: `c53fdfe`; based on original live image
`sha256:1607dd8eadc9445294a7a05234ed95e1a15ffb242151451686c286b25e02dc8c`.
Candidate image ID: `sha256:1bf615727cbfd465e373d42ec3e6dca5b18416f10fe4f17b54c97ea47082d645`.
Candidate tag: `secai-agent-gateway:file-ocr-c53fdfe-20261004`.
Original rollback tag: `secai-agent-gateway:rollback-file-ocr-20261004`.

Private deployment snapshots, image recipe, original data copy and scripts are in
`/home/ict/secai-docker-rollbacks/file-ocr-20261004` (directory 0700;
configuration JSON 0600). They contain credentials; never commit or publish them.
The data copy was taken while the original gateway was running and is only a
precautionary copy, not a transactionally coordinated database backup. Rollback
uses the existing volume and does not restore this copy or delete new v2 data.

The release adds an independent translation worker using memory sessions and no
Redis, shares only its dedicated jobs/artifact paths under `/app/data/translate-v2`,
and preserves the API's existing command, environment and mounts. No GPU service,
database dependency, Docker socket, or model endpoint was changed.

Candidate verification on isolated port 18131 and volume:

- Health HTTP 200; legacy chat/translate/session paths present.
- TXT and DOCX upload jobs completed and results matched source in identity mode.
- Real TXT en→vi completed: “Xin chào thế giới. Đây là một bài kiểm tra dịch thuật.”
- PDF rejected with HTTP 415.
- Image upload returned HTTP 503 `DEPENDENCY_UNAVAILABLE`: no OCR configured.
- Candidate rollback to original image returned health 200 and removed v2 paths.

OCR image translation and legacy `.doc` conversion remain unavailable without
separately configured dependencies. These checks do not certify OCR quality.
Candidate containers and the failed replacement container were removed; the
candidate volume and immutable images remain available for inspection.

The private executable commands, once a fresh coordinated snapshot is prepared:

```sh
python3 /home/ict/secai-docker-rollbacks/file-ocr-20261004/release.py deploy
python3 /home/ict/secai-docker-rollbacks/file-ocr-20261004/release.py rollback
```

The current snapshot intentionally refuses both commands because production now
belongs to another release. Deployment checks the snapshotted original container
ID and image before any write. Readiness verifies exact image and compose-file
ownership, API contract, worker running, then runs `smoke.py`; failure invokes
rollback only if production still belongs to this release. Manual rollback has
the same ownership guard. Other services and volumes are never removed.

Before continuing: coordinate deployment ownership, combine the review and OCR
changes (`app.py` and `prompts/profiles.yaml` overlap), rebuild, repeat candidate
checks, and snapshot the current release as the new rollback target.

Verified guard outputs (both exit 1, no Docker mutation):

```text
RuntimeError: Live gateway changed since snapshot; deployment refused. Coordinate and prepare a fresh release.
RuntimeError: Live gateway belongs to another deployment; rollback refused to preserve it.
```
