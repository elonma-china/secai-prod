# Docker translation release with rollback

This release is isolated from agents updating `secai-docker-gateway`:

- Compose project: `secai-translation-release`.
- Containers: `secai-translation-gateway`, `secai-translation-worker`, `secai-translation-frontend`.
- Gateway port: 18185; Devspace port: 5179.
- Persistent volume: `secai-translation-data`; rollback does not remove it.
- Model remains on Vast. The existing OCR and on-demand translation services share GPU admission. Gateway and worker run on CPU.

The image adds only the reviewed translation modules to a pinned copy of the currently deployed gateway image. The application composition preserves that image's review integration. Frontend assets enable file/image uploads. The worker runs separately, with one active job and one translation call. Public tokenizer metadata is mounted read-only. Credentials and rendered Compose profiles stay outside Git, mode 0600.

Operator state on this host: `/home/ict/secai-translation-docker-release`. Its `release.json` selects the translation image. `rollback.json` selects the preserved baseline image and prior UI; the translation worker is stopped. Neither profile defines shared gateway, review, report, database or search containers. Container ownership is checked before any switch; a per-release flock prevents simultaneous switches. Failed release readiness triggers rollback.

```sh
python3 release.py check --state-dir /home/ict/secai-translation-docker-release
python3 release.py deploy --state-dir /home/ict/secai-translation-docker-release
python3 release.py rollback --state-dir /home/ict/secai-translation-docker-release
python3 release.py status --state-dir /home/ict/secai-translation-docker-release
```

The host's user service `secai-translation-vast-tunnel.service` reconnects private transport automatically and binds host loopback. These CPU containers use host networking, with gateway port 18185 and frontend port 5179 explicitly checked before deployment. This preserves the translation adapters' requirement that plain HTTP specialist transport be loopback. GPU serving and model loading stay on Vast.

The existing shared Devspace/gateway are not switched by this release. Integrating into those shared endpoints requires combining the other agents' final releases first. Supported and smoke-tested translation inputs are TXT, DOCX and PNG/JPEG OCR. Legacy DOC additionally requires the existing sandboxed converter; this Docker worker does not mount the host Docker socket.
