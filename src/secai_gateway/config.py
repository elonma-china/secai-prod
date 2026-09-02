from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single, typed source of truth for every environment variable the gateway reads.

    ``.env`` (in the working directory) is loaded automatically; real environment
    variables always win over the file. This replaces the ad-hoc ``os.getenv`` calls
    that used to be scattered across the code and the hand-rolled dotenv loader.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False,
    )

    # ── Qdrant + hybrid retrieval ──────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    # Dense+sparse hybrid collection (legacy dense-only: cve_kb).
    qdrant_collection: str = "cve_kb_hybrid"
    qdrant_bm25_vector: str = "bm25"
    bm25_model: str = "Qdrant/bm25"
    embedding_api_url: str = ""
    reranker_api_url: str = ""
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"
    embedding_api_key: str = ""
    reranker_api_key: str = ""

    # ── Exploit relevance gate (layer 4; EXPLOIT-ONLY, opt-in, default OFF, fail-open) ──
    # ONLY the /ai/exploit PoC-generation path (poc/prefetch.py → routes.exploit_prefetch)
    # uses this — chat/report/statistical are untouched. For a FREE-TEXT prompt (no CVE id),
    # it embeds the prompt, retrieves KB candidates and takes the reranker's top relevance_score
    # as a confidence signal; below the floor → the prompt maps to no real/known vulnerability,
    # so prefetch returns no evidence instead of building a PoC from a weak keyword match
    # (the "user nhập linh tinh vẫn ra PoC tự tin" gap). A NAMED CVE is never gated.
    # Measured 2026-09-02: in-KB ~1.0, off-topic ~0.0 (clean at 0.5). No reranker / any error →
    # no gate (fail-open). OFF by default so current behaviour is unchanged until enabled.
    exploit_relevance_gate_enabled: bool = False
    exploit_rerank_min_score: float = 0.5

    # ── Input sanitization classifier (model-based, chat path) ─────────────────
    # A tiny numpy linear head over the sec-embedding vector classifies each chat
    # prompt into a combined taxonomy (intent: security|chitchat|offtopic + risk:
    # injection|pii) BEFORE the agent runs, so off-topic/injection are refused
    # deterministically instead of relying only on the LLM's <scope_policy>. Runtime
    # is numpy-only (no torch/transformers); the head ships as sanitize/assets/head.npz,
    # trained offline by scripts/train_sanitize_head.py against this same embedding
    # model. FAIL-OPEN by construction: a missing/untrained head, an embed-model or
    # dimension mismatch, or any embedding error passes the prompt through unchanged —
    # the classifier can only ADD a block, never take chat down.
    input_classifier_enabled: bool = True
    # Directory holding head.npz/head.json/thresholds.json. Empty → packaged assets.
    input_classifier_weights_path: str = ""
    # Per-action switches (turn a behaviour into shadow/log-only by flipping to false).
    input_classifier_block_injection: bool = True
    input_classifier_block_offtopic: bool = True
    input_classifier_shortcircuit_chitchat: bool = False  # OFF: LLM <interaction_policy> handles greetings
    # Decision thresholds; 0 → use the value shipped in thresholds.json.
    input_classifier_injection_threshold: float = 0.0
    input_classifier_pii_threshold: float = 0.0
    input_classifier_offtopic_threshold: float = 0.0
    # Ceiling on the embedding call the classifier adds to the chat path (fail-open on timeout).
    input_classifier_embed_timeout_s: float = 5.0
    # Ư2 uncertain-band cascade (sanitize/cascade.py): escalate an ambiguous injection score to
    # the LLM judge instead of a binary threshold call. OFF by default — opt in once verified
    # against a live serving endpoint (calibration was fit/verified offline; vast was down when
    # this was built). No-op regardless of this flag when assets/calibration.json is absent
    # (InputVerdict.injection_uncertain then never sets True), so enabling it is inert until a
    # calibrated head is shipped.
    injection_cascade_enabled: bool = False
    injection_cascade_timeout_s: float = 8.0

    # ── Neo4j graph ────────────────────────────────────────────────────────────
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "secai2026"
    neo4j_database: str = "neo4j"
    neo4j_http_port: str = "7474"

    # ── PoC / Exploit-DB ───────────────────────────────────────────────────────
    exploitdb_root: str = "/srv/exploitdb"
    searchsploit_bin: str = "searchsploit"
    # Unified PoC catalog (SQLite). Built by `python -m secai_gateway.poc.build_catalog`
    # from the collectors. See docs/unified-poc-db-design.md.
    poc_catalog_db: str = "/srv/exploitdb/poc-catalog.db"
    # Cloned source repos (metasploit-framework, nuclei-templates, vulhub, advisory-database,
    # PoC-in-GitHub) live under here; each collector reads its own subdir.
    poc_sources_root: str = "/srv/poc-sources"
    # Content-addressed store for artifacts fetched from link-only sources (GitHub PoCs),
    # so serving is fully offline (no runtime internet).
    poc_artifact_store: str = "/srv/poc-store"

    # ── LLM provider ───────────────────────────────────────────────────────────
    llm_provider: str = "vllm"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_context_window: int = 131072
    qwen_model: str = "Qwen/Qwen3-32B"
    qwen_api_key: str = "not-needed"
    qwen_base_url: str = "http://127.0.0.1:8000/v1"
    qwen_context_window: int = 32768

    # ── Agent loop ─────────────────────────────────────────────────────────────
    # 16 gives headroom for the exploit_research tool chain (qdrant→neo4j→poc_search→
    # poc_fetch, ×multiple PoCs) and legit multi-hop, without letting a
    # small model loop unboundedly — the loop guard + base_system turn-discipline keep
    # simple queries to 1-2 turns. Override per-deploy via STEP_CAP.
    step_cap: int = 16
    tool_timeout_ms: int = 30000

    # ── Gateway runtime ────────────────────────────────────────────────────────
    gateway_db_path: str = "./data/gateway.db"
    redis_url: str = ""
    admin_api_key: str = ""
    allowed_origins: str = ""

    # ── Durable multi-conversation chat history (/ai/conversations/*) ──────────
    # Live-memory retention window for linch's RedisSessionStore (both the legacy /ai/chat
    # thread and each /ai/conversations/* conversation). Durable history for the latter is a
    # separate SQLite store (state/conversations.py), independent of this TTL — refreshed on
    # every write. Default = the pre-existing hardcoded 24h so /ai/chat's behavior is unchanged.
    session_ttl_seconds: int = 86400
    # Turns to reload from the durable SQLite transcript into a fresh linch session when its
    # 24h live window has expired and the model would otherwise have no context. 0 = off.
    chat_history_rehydrate_turns: int = 12
    # Surface an additive `compaction` SSE frame/metadata field on /ai/conversations/chat/stream
    # when linch compacts mid-run. Observational only — never changes the old /ai/chat stream.
    conversation_compaction_meta: bool = True

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


def get_settings() -> Settings:
    """Build a fresh Settings from the current environment + .env.

    Intentionally uncached: construction is cheap and re-reading keeps ``create_app``
    honest under tests that patch the environment between app instances.
    """
    return Settings()
