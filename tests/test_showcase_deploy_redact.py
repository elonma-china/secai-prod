"""GET /showcase-api/deploy/manifests nằm trên :8131 public: compose/Helm trả ra phải che secret.

Mọi giá trị dưới đây là giả, chỉ dùng để kiểm chúng không lọt ra output."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secai_gateway.api.showcase import deploy

FAKE = {
    "auth": "fake-neo4j-pass-111",
    "quoted": "sk-fake-quoted-222",
    "list": "sk-fake-list-333",
    "list_q": "fake list pw 444",
    "default": "fake-default-555",
    "url": "fake-redis-666",
    "argv": "fake-argv-777",
    "block": "FAKEBLOCKKEY888",
    "dsn": "fake-dsn-999",
    "comment": "fake-commented-000",
    "k8s": "fake-k8s-aaa",
    "flow": "fake-flow-bbb",
    "envline": "fake-envline-ccc",
    "seq": "fake-seq-ddd",
}

COMPOSE = f"""\
name: secai-docker
services:
  neo4j:
    image: neo4j:5.20-community
    environment:
      NEO4J_AUTH: neo4j/{FAKE["auth"]}
      NEO4J_REF_AUTH: neo4j/${{NEO4J_PASSWORD:?set NEO4J_PASSWORD}}
  gateway:
    image: secai-agent-gateway:main-0123abcd-20261127
    command: ["uvicorn", "app", "--api-key", "{FAKE["argv"]}"]
    env_file:
      - .env
    environment:
      LOG_LEVEL: info
      QDRANT_URL: http://qdrant:6333
      OPENAI_API_KEY: "{FAKE["quoted"]}"
      NEO4J_PASSWORD: ${{NEO4J_PASSWORD:-{FAKE["default"]}}}
      ADMIN_TOKEN: ${{ADMIN_TOKEN}}
      REVIEW_SECRET: ""
      SHOWCASE_BASIC_AUTH: ${{SHOWCASE_BASIC_AUTH:-}}
      REDIS_URL: redis://:{FAKE["url"]}@redis:6379/0
      DSN: "postgres://u@db/x?pw=${{DB_PASS:-{FAKE["dsn"]}}}"
      # OLD_API_KEY: {FAKE["comment"]}
      TLS_PRIVATE_KEY: |
        -----BEGIN FAKE-----
        {FAKE["block"]}
      AFTER_BLOCK: kept
  worker:
    image: secai-agent-gateway:main-0123abcd-20261127
    environment:
      - TRANSLATION_API_KEY={FAKE["list"]}
      - "DB_PASSWORD={FAKE["list_q"]}"
      - WORKERS=2
    secrets_list:
      ALLOWED_TOKENS: [{FAKE["seq"]}, other]
"""

HELM_VALUES = f"""\
neo4j:
  image: neo4j:5.20-community
  password: {FAKE["auth"]}
  emptyPassword: ""
"""

HELM_TEMPLATE = f"""\
spec:
  containers:
    - name: gateway
      envFrom:
        - secretRef: {{ name: secai-gateway-env }}   # serving endpoints + API keys
      env:
        - {{ name: NEO4J_AUTH, value: "neo4j/{{{{ .Values.neo4j.password }}}}" }}
        - {{ name: SECRET_KEY, value: "{FAKE["flow"]}" }}
        - name: API_TOKEN
          value: {FAKE["k8s"]}
        - name: LOG_LEVEL
          value: debug
"""


def _keys(x, pre=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield f"{pre}/{k}"
            yield from _keys(v, f"{pre}/{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _keys(v, f"{pre}[{i}]")


def test_redact_hides_every_fake_secret_and_keeps_structure() -> None:
    out = deploy.redact_secrets(COMPOSE)
    leaked = [k for k, v in FAKE.items() if v in out]
    assert not leaked, leaked
    assert len(out.splitlines()) == len(COMPOSE.splitlines())
    assert list(_keys(yaml.safe_load(out))) == list(_keys(yaml.safe_load(COMPOSE)))
    lines = out.splitlines()
    # tham chiếu, giá trị rỗng và cấu hình thường giữ nguyên
    for keep in ("      NEO4J_REF_AUTH: neo4j/${NEO4J_PASSWORD:?set NEO4J_PASSWORD}",
                 "      ADMIN_TOKEN: ${ADMIN_TOKEN}", '      REVIEW_SECRET: ""',
                 "      SHOWCASE_BASIC_AUTH: ${SHOWCASE_BASIC_AUTH:-}", "      LOG_LEVEL: info",
                 "      QDRANT_URL: http://qdrant:6333", "      AFTER_BLOCK: kept", "      - WORKERS=2",
                 "    image: secai-agent-gateway:main-0123abcd-20261127"):
        assert keep in lines, keep
    for want in ("      NEO4J_AUTH: <redacted>", '      OPENAI_API_KEY: "<redacted>"',
                 "      NEO4J_PASSWORD: ${NEO4J_PASSWORD:-<redacted>}",
                 "      REDIS_URL: redis://:<redacted>@redis:6379/0",
                 '      DSN: "postgres://u@db/x?pw=${DB_PASS:-<redacted>}"',
                 "      # OLD_API_KEY: <redacted>", "      TLS_PRIVATE_KEY: |", "        <redacted>",
                 "      - TRANSLATION_API_KEY=<redacted>", '      - "DB_PASSWORD=<redacted>"',
                 '    command: ["uvicorn", "app", "--api-key", "<redacted>"]',
                 "      ALLOWED_TOKENS: [<redacted>, <redacted>]"):
        assert want in lines, want


def test_redact_helm_values_and_templates() -> None:
    vals = deploy.redact_secrets(HELM_VALUES)
    assert FAKE["auth"] not in vals
    assert yaml.safe_load(vals)["neo4j"] == {"image": "neo4j:5.20-community", "password": "<redacted>", "emptyPassword": ""}
    tpl = deploy.redact_secrets(HELM_TEMPLATE)
    assert FAKE["flow"] not in tpl and FAKE["k8s"] not in tpl, tpl
    lines = tpl.splitlines()
    assert "        - secretRef: { name: secai-gateway-env }   # serving endpoints + API keys" in lines
    assert '        - { name: NEO4J_AUTH, value: "neo4j/{{ .Values.neo4j.password }}" }' in lines
    assert '        - { name: SECRET_KEY, value: "<redacted>" }' in lines
    assert "          value: <redacted>" in lines and "          value: debug" in lines


def test_redact_is_idempotent() -> None:
    once = deploy.redact_secrets(COMPOSE + HELM_TEMPLATE)
    assert deploy.redact_secrets(once) == once


# (input, giá trị giả phải biến mất, các dòng output mong đợi). Mỗi dạng là YAML/compose hợp lệ.
LEAK_CASES = [
    # `$` literal: compose viết `$$`; `Pa$w0rd` còn phần literal `Pa`; literal ghép tham chiếu
    ("NEO4J_AUTH: neo4j/Pa$$w0rdFake01\n", "w0rdFake01", ["NEO4J_AUTH: <redacted>"]),
    ("NEO4J_PASSWORD: Pa$$w0rdFake02\n", "w0rdFake02", ["NEO4J_PASSWORD: <redacted>"]),
    ("env:\n  - NEO4J_PASSWORD=Pa$$w0rdFake03\n", "w0rdFake03", ["  - NEO4J_PASSWORD=<redacted>"]),
    ('API_KEY: "Pa$w0rdFake04"\n', "w0rdFake04", ['API_KEY: "<redacted>"']),
    ("API_KEY: sk-Fake05${SUFFIX}\n", "Fake05", ["API_KEY: <redacted>"]),
    ("NEO4J_PASSWORD: ${NEO4J_PASSWORD:-Pa$$Fake06}\n", "Fake06", ["NEO4J_PASSWORD: ${NEO4J_PASSWORD:-<redacted>}"]),
    # `*`/`{` trong ngoặc hay dạng KEY=value là literal, không phải alias/flow mapping
    ('API_KEY: "*abcFake07"\n', "abcFake07", ['API_KEY: "<redacted>"']),
    ("API_KEY: '*abcFake08'\n", "abcFake08", ["API_KEY: '<redacted>'"]),
    ("env:\n  - DB_PASSWORD=*abcFake09\n", "abcFake09", ["  - DB_PASSWORD=<redacted>"]),
    ('env:\n  - "API_KEY=*abcFake10"\n', "abcFake10", ['  - "API_KEY=<redacted>"']),
    ('API_KEY: "{abcFake11}"\n', "abcFake11", ['API_KEY: "<redacted>"']),
    ("env:\n  - API_KEY={abcFake12\n", "abcFake12", ["  - API_KEY=<redacted>"]),
    # giá trị nằm ở dòng sau khoá: plain scalar xuống dòng, list, `value: |` sau `- name:`
    ("env:\n  NEO4J_AUTH:\n    neo4j/Fake13\n  OTHER: x\n", "Fake13", ["  NEO4J_AUTH:", "    <redacted>", "  OTHER: x"]),
    ("env:\n  NEO4J_AUTH: neo4j/Fake14a\n    Fake14b\n  OTHER: x\n", "Fake14",
     ["  NEO4J_AUTH: <redacted>", "    <redacted>", "  OTHER: x"]),
    ("apiKeys:\n  - Fake15a\n  - Fake15b\nother: x\n", "Fake15", ["  - <redacted>", "other: x"]),
    ("apiKeys:\n- Fake16\nother: x\n", "Fake16", ["- <redacted>", "other: x"]),
    ("- name: API_TOKEN\n  value: |\n    Fake17\n- name: LOG_LEVEL\n  value: debug\n", "Fake17",
     ["  value: |", "    <redacted>", "  value: debug"]),
    ('API_KEY: "abc\n  Fake18"\nOTHER: x\n', "Fake18", ['API_KEY: "<redacted>', '  <redacted>"', "OTHER: x"]),
    ('API_KEY: "ab\\"Fake19"\n', "Fake19", ['API_KEY: "<redacted>"']),
    ("data:\n  TLS_KEY: !!binary |\n    RmFrZTI0\n  other: x\n", "RmFrZTI0", ["  TLS_KEY: !!binary |", "    <redacted>", "  other: x"]),
    # mặc định literal trong template Helm, cờ argv và URL có `$$`
    ('password: {{ .Values.neo4j.password | default "Fake20" }}\n', "Fake20",
     ['password: {{ .Values.neo4j.password | default "<redacted>" }}']),
    ('env:\n  - name: NEO4J_PASSWORD\n    value: {{ .Values.neo4j.password | default "Fake21" | quote }}\n', "Fake21",
     ['    value: {{ .Values.neo4j.password | default "<redacted>" | quote }}']),
    ('command: ["run", "--password", "$$Fake22"]\n', "Fake22", ['command: ["run", "--password", "<redacted>"]']),
    ("REDIS_URL: redis://:$$Fake23@redis:6379/0\n", "Fake23", ["REDIS_URL: redis://:<redacted>@redis:6379/0"]),
]


@pytest.mark.parametrize("src,fake,want", LEAK_CASES, ids=[f"case{i:02d}" for i in range(len(LEAK_CASES))])
def test_redact_literal_forms(src: str, fake: str, want: list[str]) -> None:
    out = deploy.redact_secrets(src)
    assert fake not in out, out
    lines = out.splitlines()
    for w in want:
        assert w in lines, (w, out)
    assert len(lines) == len(src.splitlines())
    assert deploy.redact_secrets(out) == out
    if "{{" not in out:  # template Helm chưa render không phải YAML
        yaml.safe_load(out)


def test_redact_keeps_references_and_non_secret_names() -> None:
    src = ("services:\n  gw:\n    environment:\n"
           "      REVIEW_CREDENTIALS_FILE: /srv/review/credentials.json\n"
           "      LLM_MAX_TOKENS: 4096\n      KEYWORD_INDEX: cve-keywords\n      AUTHOR: secai\n"
           "      NEO4J_AUTH: neo4j/$NEO4J_PASSWORD\n      BASIC_AUTH: ${BASIC_USER}:${BASIC_PASS}\n"
           "      ADMIN_TOKEN: ${ADMIN_TOKEN:?set ADMIN_TOKEN}\n"
           "x-auth: &auth\n  NEO4J_USER: neo4j\n"
           "auth:\n  enabled: true\n  username: neo4j\n  password: fake-nested-pw\n"
           '- { name: NEO4J_AUTH, value: "neo4j/{{ required "neo4j.password is required" .Values.neo4j.password }}" }\n'
           "imagePullSecrets:\n  {{- toYaml .Values.imagePullSecrets | nindent 2 }}\n")
    out = deploy.redact_secrets(src)
    assert "fake-nested-pw" not in out
    assert out == src.replace("  password: fake-nested-pw", "  password: <redacted>"), out


def _client(monkeypatch: pytest.MonkeyPatch, compose: Path, helm: Path) -> TestClient:
    monkeypatch.setattr(deploy, "get_settings", lambda: SimpleNamespace(
        showcase_docker_compose=str(compose), showcase_helm_dir=str(helm)))
    app = FastAPI()
    app.include_router(deploy.router)
    return TestClient(app)


def test_manifests_endpoint_returns_redacted_compose_and_helm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(COMPOSE)
    helm = tmp_path / "helm"
    (helm / "templates").mkdir(parents=True)
    (helm / "Chart.yaml").write_text("apiVersion: v2\nname: secai-gateway\nversion: 0.1.0\n")
    (helm / "values.yaml").write_text(HELM_VALUES)
    (helm / "templates" / "gateway.yaml").write_text(HELM_TEMPLATE)
    r = _client(monkeypatch, compose, helm).get("/showcase-api/deploy/manifests")
    assert r.status_code == 200
    body = r.text
    assert not [k for k, v in FAKE.items() if v in body]
    data = r.json()
    assert data["docker"]["compose"]["name"] == "docker-compose.yml"
    assert "NEO4J_AUTH: <redacted>" in data["docker"]["compose"]["content"]
    assert [f["path"] for f in data["helm"]["files"]] == ["Chart.yaml", "templates/gateway.yaml", "values.yaml"]
    assert "password: <redacted>" in next(f["content"] for f in data["helm"]["files"] if f["path"] == "values.yaml")


@pytest.mark.skipif(os.geteuid() == 0, reason="root đọc được file mode 000")
def test_unreadable_compose_is_reported_not_500(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(COMPOSE)
    compose.chmod(0)
    try:
        r = _client(monkeypatch, compose, tmp_path / "no-helm").get("/showcase-api/deploy/manifests")
    finally:
        compose.chmod(0o600)
    assert r.status_code == 200
    assert "không đọc được" in r.json()["docker"]["compose"]["content"]
