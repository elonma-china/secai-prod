"""Ephemeral Ed25519 fixture keys; never checked in or reused for real evidence."""

import base64
import hashlib
import subprocess

from evals.multilang.evidence import canonical_bytes


def configure_signer(tmp_path, monkeypatch):
    private = tmp_path / "tester-private.pem"
    public = tmp_path / "tester-public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)],
                   check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
                   check=True, capture_output=True)
    public.chmod(0o644)
    monkeypatch.setenv("MULTILANG_TESTER_PUBKEY_PATH", str(public))
    monkeypatch.setenv("MULTILANG_TESTER_PUBKEY_SHA256", hashlib.sha256(public.read_bytes()).hexdigest())
    ledger = tmp_path / "tester-ledger"
    ledger.mkdir(mode=0o700)
    monkeypatch.setenv("MULTILANG_TESTER_LEDGER_DIR", str(ledger))
    return private, ledger


def sign(tmp_path, private, kind, **fields):
    payload = {"schema": "multilang-evidence-v1", "kind": kind, **fields}
    message = tmp_path / "payload-to-sign.json"
    signature = tmp_path / "payload-signature.bin"
    message.write_bytes(canonical_bytes(payload))
    subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(private),
                    "-rawin", "-in", str(message), "-out", str(signature)],
                   check=True, capture_output=True)
    return {"payload": payload, "signature_b64": base64.b64encode(signature.read_bytes()).decode("ascii")}
