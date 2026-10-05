from __future__ import annotations

from pydantic import Field, model_validator
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

    # Expert review is opt-in and uses a separate immutable SQLite ledger.
    review_enabled: bool = False
    review_db_path: str = "data/reviews.db"
    review_credentials_file: str = ""
    review_live_sample_rate: float = Field(default=0.1,ge=0,le=1)
    review_live_cooldown_seconds: float = Field(default=600,ge=0)
    review_live_timeout_seconds: float = Field(default=45,gt=0,le=120)

    # ── Air-gap master switch (default ON) ─────────────────────────────────────
    # When true, the gateway must make NO outbound request to the public internet. This
    # is enforced two ways, so a single mis-set env var can never re-open a leak:
    #   1. `_enforce_airgap` below hard-coerces every internet-feature flag to its off
    #      value (so the tools are never even built / the deterministic web steps skip);
    #   2. the low-level public-content fetchers (retrieval/http_fetch, reference_fetch,
    #      websearch and routes.py's on-demand GitHub step) additionally call
    #      `airgap.assert_online_fetch_allowed()` and refuse — the belt-and-suspenders
    #      backstop for any code path (now or future) that bypasses the flags.
    # It does NOT touch the internal service endpoints (Qdrant/Neo4j/Redis + the LLM/
    # embedding/reranker serving): those must point at in-network hosts at deploy time —
    # air-gap means "no internet", not "no network". Build the PoC catalog / run the
    # nightly KB sync on an internet-connected machine with AIRGAP=false; the air-gap
    # deployment itself keeps the default (true).
    airgap: bool = True

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

    # ── Exploit relevance gate (layer 4; EXPLOIT-ONLY, fail-open) ────────────────
    # ONLY the /ai/exploit PoC-generation path (poc/prefetch.py → routes.exploit_prefetch)
    # uses this — chat/report/statistical are untouched. For a FREE-TEXT prompt (no CVE id),
    # it embeds the prompt, retrieves KB candidates and takes the reranker's top relevance_score
    # as a confidence signal; below the floor → the prompt maps to no real/known vulnerability,
    # so prefetch reports "nothing found" instead of building a PoC from a weak keyword match
    # (the "user nhập linh tinh vẫn ra PoC tự tin" gap). A NAMED CVE keeps using cve_exists_in_kb.
    # Measured 2026-09-02: in-KB ~1.0, off-topic pure-gibberish ~0.0 (clean at 0.5) — BUT
    # re-measured 2026-09-22 while fixing bug #1/PC1: a security-FLAVORED but non-specific
    # "how do I hack <generic target>" prompt ("how do I hack wifi" ~0.978, "how to hack
    # facebook" ~0.944) also scores high with this cross-encoder reranker (it responds to
    # security vocabulary, not to whether the query names an actual specific vulnerability),
    # landing well above the old 0.5 floor and indistinguishable from it. Genuine specific
    # product/CVE descriptions cluster tighter, ~0.9975-0.9997 (wordpress sqli, log4j RCE,
    # struts2 RCE, phpmyadmin auth bypass, openssh exploit). 0.99 sits in the gap between the
    # two clusters — no reranker / any error → no gate (fail-open).
    # DEFAULT ON since 2026-09-22 (bug #1 / PC1, HIGH safety): a free-text off-topic prompt
    # ("how do I hack wifi") with no CVE id and no relevant KB match was still returning a
    # loosely keyword-matched, unrelated PoC tiered `section1_tier=real_consolidated` +
    # `reference_code_injected=true` — presented as a grounded real exploit. This gate was
    # already built (2026-09-02) but shipped opt-in/OFF (and at a threshold too low to catch
    # this exact class of prompt), so it never protected prod. Set
    # `EXPLOIT_RELEVANCE_GATE_ENABLED=false` to restore the old (unsafe) always-on behaviour.
    exploit_relevance_gate_enabled: bool = True
    exploit_rerank_min_score: float = 0.99
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
    # Margin guard (gate.py::decide, near-tie fix 2026-09-21): the offtopic vs. security
    # softmax scores are two classes racing each other, not independent signals — a message
    # scoring offtopic=0.51/security=0.49 is a coin-flip, not confident off-topic, yet a bare
    # `>= threshold` check refuses it anyway (real incident: PoC drill-down follow-up refused
    # at 0.5096/0.4901). Require offtopic to beat security by this margin too before refusing;
    # a near-tie now PASSes through to the agent, which still has the LLM <scope_policy> as a
    # second line of defense for genuine off-topic (weather/cooking/etc. score nowhere near a
    # tie). 0.0 disables the margin check (legacy threshold-only behavior).
    input_classifier_offtopic_margin: float = 0.15
    # Security-domain override (gate.py::decide, residual #2 fix 2026-09-27): the margin guard
    # above only rescues near-tie offtopic/security scores — it does NOT catch Top-N/taxonomy
    # security questions the head misclassifies CONFIDENTLY as off-topic (e.g. "Hãy liệt kê CWE
    # Top 25" scored offtopic=0.98/security=0.01, margin 0.97). When the raw prompt names an
    # explicit security taxonomy/framework (CWE/CVE/CAPEC/CVSS/OWASP/SANS/ATT&CK/MITRE) and is
    # not ALSO flagged as injection, the off-topic refusal is skipped and the prompt passes to
    # the agent (which still has the LLM <scope_policy> as a second line of defense). True →
    # keep the override; False → restore the pre-fix threshold+margin-only behavior (rollback).
    input_classifier_security_marker_override: bool = True
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

    # Language gate (security.is_unsupported_language): the classifier head was trained on
    # Vietnamese + English only, so a message written predominantly in a non-Latin script
    # (CJK/kana/Hangul/Cyrillic/Arabic/Thai/…) is refused on /ai/chat with a bilingual ask to
    # use vi/en. Latin-script languages pass through. Dependency-free, no network.
    language_gate_enabled: bool = True
    language_gate_nonlatin_ratio: float = 0.5

    # Bug #13/CQ4: `metadata.tool_registry` on /sessions/{sid}/chat and /chat/stream listed
    # internal retrieval-tool names (qdrant_search, neo4j_search, poc_search, fetch_reference,
    # web_fetch) on EVERY reply, including plain greetings — an internal-implementation detail
    # with no legitimate public use (confirmed: the FE's sse.ts types this field but nothing
    # renders it). Default OFF returns an empty list (key/shape unchanged: still list[str]);
    # turn on only to debug which tools a profile actually exposes. Deliberately does NOT
    # touch the SSE `tool_call`/`tool_result` frames — those ARE consumed by the FE's live
    # "Hoạt động công cụ" timeline (ToolTimeline.tsx / ChatbotScreen.tsx) for every chat
    # message, so redacting their content would break a real, user-facing feature.
    expose_internal_tool_metadata: bool = False

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

    # ── LLM sampling (vLLM/Qwen path) ──────────────────────────────────────────
    # Defaults = Cyber-F1 (Qwen3-30B-A3B-Instruct-2507 MoE) model-card recommendation. Override per-deploy via
    # env (LLM_TEMPERATURE, LLM_TOP_P, …). `temperature` is a first-class OpenAI field;
    # top_p/top_k/min_p/presence_penalty/repetition_penalty go through vLLM's extra_body.
    llm_temperature: float = 0.85
    llm_top_p: float = 0.95
    llm_top_k: int = 20
    llm_min_p: float = 0.0
    llm_presence_penalty: float = 1.1
    llm_repetition_penalty: float = 1.0

    # ── Translate chunking ─────────────────────────────────────────────────────
    # /ai/translate is single-shot; an input whose translation would exceed the per-turn
    # output-token cap gets truncated. So a long input is split on paragraph/sentence
    # boundaries into ≤``translate_chunk_chars`` pieces, each translated then rejoined.
    # ``chars`` (not tokens) keeps it provider-agnostic; ~6000 chars ≈ well under a 4k-token
    # output cap for VI/EN. Concurrency bounds the fan-out per request; max_chunks caps it
    # (413 beyond) so one request can't spawn unbounded model calls.
    translate_chunk_chars: int = 6000
    translate_chunk_concurrency: int = 4
    translate_max_chunks: int = 40

    # ── Agent loop ─────────────────────────────────────────────────────────────
    # 16 gives headroom for the exploit_research tool chain (qdrant→neo4j→poc_search→
    # poc_fetch, ×multiple PoCs) and legit multi-hop, without letting a
    # small model loop unboundedly — the loop guard + base_system turn-discipline keep
    # simple queries to 1-2 turns. Override per-deploy via STEP_CAP.
    step_cap: int = 16
    tool_timeout_ms: int = 30000

    # ── Gateway runtime ────────────────────────────────────────────────────────
    report_feature_enabled: bool = False
    report_api_key: str = ""
    report_db_path: str = "./data/reports.db"
    report_upstream_url: str = ""
    translation_upstream_url: str = ""
    gateway_db_path: str = "./data/gateway.db"
    redis_url: str = ""
    admin_api_key: str = ""
    allowed_origins: str = ""

    # ── Project/session durability (P0) ─────────────────────────────────────────
    # Conversation memory used to live only in Redis with a 24h TTL — a restart or an
    # idle user lost the thread. `sqlite` (default) switches the runner onto linch's
    # shipped SqliteSessionStore, which persists sessions/messages/snapshots to disk
    # with no expiry. `redis` keeps the legacy behavior (back-compat); `memory` is
    # dev/test-only and never persists. See agent/runner.py's LinchRunner.__init__.
    session_store_backend: str = "sqlite"
    session_db_path: str = "./data/sessions.db"

    # ── PoC-Verify (Mode A: ephemeral docker → secai-kali-executor) ─────────────
    # TẮT mặc định (reversible): bật bằng VERIFY_ENABLED=true. Toàn bộ đường "thực thi PoC vào
    # target" gated ở đây; gỡ tính năng = để flag false. Xem docs/poc-verify-architecture.md.
    verify_enabled: bool = False
    kali_executor_image: str = "secai-kali-executor:latest"
    verify_timeout_s: int = 120
    verify_net: str = "bridge"           # egress-chỉ-target cần firewall host (ops), không ép bằng cờ docker
    verify_mem: str = "1g"
    verify_cpus: str = "2"
    verify_pids: int = 256
    # Trần số container Kali chạy ĐỒNG THỜI trên toàn tiến trình gateway (không phải per-client
    # như rate limiter) — chặn nhiều người dùng cùng lúc làm quá tải docker daemon/host, khiến
    # kết quả trễ/timeout chồng chéo trông như "loạn". Vượt trần → chờ tới lượt (không rớt request).
    # Mặc định 16: tính theo host hiện tại (96 vCPU, mỗi container cấp --cpus 2) — 16 dùng tối đa
    # 32 CPU, để dư ~64 CPU cho host + service khác (Qdrant/Neo4j/OpenVAS) và tránh chính việc
    # verify tự làm nghẽn/loạn kết quả phía target khi quá nhiều scan chạy song song vào 1 target.
    # Trần lý thuyết của host này là 48 (96÷2); đổi qua VERIFY_MAX_CONCURRENT nếu phần cứng khác.
    verify_max_concurrent: int = 16
    # Authz fail-closed: chỉ verify target trong allowlist (host chính xác/CIDR, phân cách bằng dấu phẩy)
    # trừ khi verify_allow_all=true. Rỗng + allow_all false → chặn tất cả.
    verify_allow_all: bool = False
    verify_allowed_targets: str = ""

    # ── RAW exec (RCE có chủ đích) — chạy lệnh BẤT KỲ trong Kali, kể cả tấn công ─────
    # TẮT mặc định. Bật (EXEC_ENABLED=true) → endpoint POST /ai/exec chạy được lệnh tùy ý với
    # --privileged + root (KHÔNG sandbox). Endpoint LUÔN yêu cầu admin API key (X-API-Key ==
    # ADMIN_API_KEY) để không thành RCE mở toang. exec_privileged=false → chạy trong sandbox hardened.
    exec_enabled: bool = False
    exec_privileged: bool = True

    # ── PoC Exploit API (phân hệ kiểm thử port 5568) ─────────────────────────────
    # TẮT mặc định. Bật POC_EXPLOIT_ENABLED=true + POC_EXPLOIT_API_URL → gateway gọi
    # register/status/exploits_cve deterministic (HTTP + agent tool). Authz target tái dùng
    # verify_allow_all / verify_allowed_targets.
    poc_exploit_enabled: bool = False
    poc_exploit_api_url: str = ""
    poc_exploit_timeout_s: int = 180
    poc_exploit_node_name: str = "secai-gateway"

    # ── lane1 (ReAct kiểu hacker) cho exploit-advance ────────────────────────────
    # TẮT mặc định. Khi CVE KHÔNG có primitive tất định VÀ engine pocsuite không có module
    # (404) → thay vì fail, chạy vòng ReAct: LLM (Cyber-F1, JSON có cấu trúc) suy nghĩ → chọn
    # tool → chạy trong sandbox Docker → đọc observation → lặp → oracle nonce/uid=. Không nới
    # authz (vẫn dùng verify_allow_all/verify_allowed_targets); tool sandbox vẫn theo
    # verify_enabled, run_cmd vẫn theo exec_enabled.
    lane1_enabled: bool = False
    lane1_max_steps: int = 12            # trần vòng lặp (req.max_steps lớn hơn thì lấy req)
    lane1_net: str = "host"             # docker net cho tool sandbox (lab publish 127.0.0.1:180xx)
    lane1_use_raw_cmd: bool = True       # cho phép run_cmd (payload/msf tùy ý) khi exec_enabled
    lane1_http_inprocess: bool = True    # http_request qua stdlib in-process (vs curl sandbox)

    # ── Bughunt / BugHunter recon engine ───────────────────────────────────────
    # Path to claude-bug-bounty clone (empty = auto sibling of gateway / BUGBOUNTY_ROOT).
    bugbounty_root: str = ""
    # Run tools/recon_engine.sh via run_recon tool (per-run workdir).
    bughunt_recon_engine: bool = True
    # auto | host | kali — auto prefers kali when host lacks nmap/nuclei/ffuf.
    bughunt_recon_backend: str = "auto"
    bughunt_recon_timeout_s: int = 600
    # Linch per-tool ceiling for bughunt arsenal (run_recon / vuln_scan / …).
    # Default matches recon timeout; override via BUGHUNT_TOOL_TIMEOUT_MS.
    bughunt_tool_timeout_ms: int = 600_000
    # Parallel post-recon wave (verify + vuln + cors + …) concurrency.
    bughunt_wave_concurrency: int = 4
    # Dedicated docker pool size for bughunt kali jobs (separate from VERIFY_MAX_CONCURRENT).
    bughunt_docker_max_concurrent: int = 8

    # ── web_fetch (allowlisted HTTPS → markdown for chat taxonomy grounding) ───
    web_fetch_enabled: bool = True
    # Comma-separated host allowlist (exact or subdomain of apex). Empty → built-in defaults.
    web_fetch_allowed_hosts: str = (
        "owasp.org,cwe.mitre.org,cisa.gov,www.cisa.gov,nvd.nist.gov"
    )
    web_fetch_timeout_s: float = 20.0
    web_fetch_max_chars: int = 24000

    # ── fetch_reference (broader security-reference allowlist → markdown; chat + exploit) ──
    # Serves both the chat and exploit_research profiles: CVE records, GHSA/vendor advisories,
    # MITRE CWE/CAPEC/ATT&CK, CISA, exploit-db and public PoC pages. Same SSRF posture as
    # web_fetch (HTTPS-only, public-IP check, per-hop redirect re-validation) — only the host
    # allowlist and read window differ. Empty allowlist → the built-in curated default set.
    fetch_reference_enabled: bool = True
    fetch_reference_allowed_hosts: str = ""
    fetch_reference_timeout_s: float = 20.0
    fetch_reference_max_chars: int = 40000

    # ── web_search (SearXNG-backed discovery for exploit PoC hints; gated OFF) ──
    # Off by default: needs a self-hosted SearXNG (SEARXNG_URL, JSON format enabled). When on,
    # it powers deterministic web-research for the T3 synthesize tier (CVE known but no catalog
    # PoC). SEARCH ≠ FETCH: search may surface any URL, but only fetch_reference-allowlisted
    # hosts are ever fetched — the SSRF posture is unchanged. Empty SEARXNG_URL → inert.
    web_search_enabled: bool = False
    searxng_url: str = ""
    web_search_timeout_s: float = 10.0
    web_search_max_results: int = 6

    # ── GitHub advisory + patch-diff grounding (synthesize tier) ───────────────
    # When a CVE has no PoC anywhere, fetch its GitHub Security Advisory and the fixing
    # commit diff via the GitHub REST API (api.github.com) so the synthesized PoC is grounded
    # in the REAL vulnerable code (endpoint/param/payload) instead of guessed from a one-line
    # description. Needs a GitHub token: ``github_token`` or, when empty, auto-resolved from
    # the local ``gh auth token`` CLI. Fail-open — any miss degrades to description-only.
    github_advisory_enabled: bool = True
    github_token: str = ""
    # Cap on total chars of patch diff fed into the model (compare diffs can be megabytes).
    github_diff_max_chars: int = 30000

    # ── PoC consolidation (single runnable file) ───────────────────────────────
    # After fetching a PoC's real source (catalog / on-demand GitHub / web), an LLM pass
    # (profile `poc_consolidate`) merges it into ONE self-contained file for Section 1 —
    # replacing the verbatim multi-file dump. Off → verbatim injection (old behaviour).
    poc_consolidate_enabled: bool = True
    # Whether the deterministic slicer (poc/slicer.py) may call the LLM keep/drop decision
    # (`poc_slice` profile) to refine its extraction. The decision is graph-only (never source
    # bytes), so this can't reintroduce hallucination; off → pure deterministic safe_slice.
    poc_slice_llm_enabled: bool = True
    # Hard ceiling on the consolidation LLM turn. runner.complete() has no internal timeout, so a
    # stuck backend call (network stall, queue starvation) would otherwise block the whole
    # /ai/exploit request indefinitely (observed: one call hung ~3h before failing). Normal calls
    # finish in 8-55s; 180s gives generous headroom while still bounding the worst case. Timeout
    # → treated like any other consolidation miss (logged, falls back to verbatim injection).
    poc_consolidate_timeout_s: int = 180
    # Max consolidation attempts before giving up to the verbatim dump. The merge's failure
    # mode is FIDELITY, not diversity (measured: it keeps a constant's use but drops its
    # definition — POSTS_ITEM_SOURCE_PATH on CVE-2026-60137 — flaky run-to-run), and the
    # execution-free validator (validate_runnable) already NAMES the exact defect. So a
    # rejected merge is retried with that defect fed back as a correction, instead of falling
    # straight back to the raw multi-file dump the LLM step exists to replace. Only VALIDATION
    # failures retry; a timeout/transport error does not (not a fidelity issue, don't burn
    # more of the request's time budget). 1 disables retries (old single-shot behaviour).
    poc_consolidate_max_attempts: int = 3
    # Fetch a link-only GitHub PoC's code on demand (codeload tarball, in-memory, no exec) from
    # the request path when the catalog row has a URL but no local artifact. Network egress —
    # fail-open (a miss simply falls through to web/synthesis). Off → only pre-fetched rows.
    poc_github_ondemand_enabled: bool = True
    poc_github_ondemand_timeout_s: int = 30
    # Persistent cache for PoC code fetched on-demand from the internet (GitHub tarball / web
    # raw script), keyed by CVE — a repeat request reuses the saved copy instead of re-fetching.
    # Lookup order becomes DB → this cache → internet. Empty → "<poc_artifact_store>/../ondemand-cache".
    poc_ondemand_cache_dir: str = ""

    # Verified-good PoC override store, keyed by CVE. A PoC that was fetched then manually FIXED
    # + verified against a lab is saved here; on the exploit path it wins over the LLM
    # consolidation (and the on-demand cache) so the CVE always returns the known-good runnable
    # file — copy-paste runnable, no re-fix. Empty → "<poc_artifact_store>/../pocs-fixed".
    poc_fixed_dir: str = ""
    # Simulated processing delay (seconds) applied when a CVE is served from the verified-good
    # store above, so its response latency stays comparable to a normal CVE that goes through the
    # LLM consolidation pass. The deterministic slicer (poc/slicer.py) now makes that pass ~1s,
    # so a large delay would make fixed PoCs SLOWER than freshly-generated ones — keep it ~0 so
    # cached and generated PoCs are comparable. 0 disables the delay.
    poc_fixed_simulate_delay_s: float = 0.0
    # Uniform total-latency FLOOR (seconds) for /ai/exploit — anti timing-side-channel: a
    # cached/fixed PoC (instant) and a freshly-generated one must be indistinguishable by
    # response time. Applied as `sleep(max(0, floor - elapsed))` AFTER the work, so total ≈
    # floor for anything faster than it, and worst case is bounded at max(floor, real) instead
    # of the old fixed-24s-up-front behaviour (which added on TOP of the real pipeline —
    # total 24+real≈38s, non-uniform — and, worse, blocked the stream's first byte for 24s,
    # defeating the early Section-1 preview). Measured real pipeline ≈14s, so 30 gives a
    # uniform ~30s while dropping the old ~38s average. 0 disables the floor.
    poc_exploit_simulate_delay_s: float = 30.0

    # PoC Section-1 strategy for a genuinely MULTI-FILE fetched PoC (single-file PoCs always take
    # the deterministic verbatim slice regardless):
    #   "prune"       — default. Keep the ORIGINAL multi-file structure, remove only the parts
    #                   nothing references (poc/slicer.py:prune_evidence). No LLM rewrite, no
    #                   flatten — the higher-fidelity, air-gap-verifiable path. Falls back to
    #                   consolidate only if prune fails AND poc_consolidate_enabled.
    #   "consolidate" — legacy: LLM-merge the whole set into ONE runnable file (poc/consolidate.py).
    #   "auto"        — prune when the multi-file set has load-bearing companions, else consolidate.
    # Consolidation itself is unchanged and still gated by poc_consolidate_enabled.
    poc_strategy: str = "prune"

    # ── Showcase / nghiệm-thu demo APIs (docs/nghiem-thu-showcase, Stage 1) ─────
    # Read-only endpoints (nhóm F/D/K/L/E) that expose existing finetune + KPI evidence
    # artifacts to a browser demo instead of screenshots/VSCode. All paths point at local
    # directories on THIS dev machine (not portable) — override via env when moving host.
    # nhóm KB (log sync) và nhóm S (source-dossier Loại 1) CHƯA làm ở Stage 1 — không cần
    # settings cho chúng ở đây.
    # Cả nhóm F lẫn nhóm K đều đọc HỒ SƠ GỐC (mirror Drive 17Qq9hXVl...), không đọc bản làm
    # việc cục bộ (FINETUNE/, KPI-VA-GEN-TEST/) — 2 bên có số liệu lệch nhau (vd manifest cục bộ
    # ghi dataset 1840MB/1260MB, hồ sơ gốc ghi 38.8MB/54.5MB đúng theo file thật).
    showcase_finetune_dossier_dir: str = "/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI/01. Ho_so_huan_luyen/finetuning"
    showcase_finetune_source_dir: str = "/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI/01. Ho_so_huan_luyen"
    # nhóm K PHẢI đọc "hồ sơ gốc" (mirror cục bộ của Drive 17Qq9hXVl...), KHÔNG phải bản tự
    # kiểm toán lại ở KPI-VA-GEN-TEST — 2 nguồn có số liệu khác nhau (vd Faithfulness hồ sơ
    # gốc 98.48%/PASS, bản tự kiểm toán 96.71%/FAIL); khách chỉ nên thấy số khớp hồ sơ gốc.
    showcase_kpi_results_dir: str = "/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI/03. Kpi_results"
    showcase_evidence_dir: str = "./evidence"
    # TensorBoard đang chạy thật, đọc trực tiếp các run trong
    # TensorBoard đi ké qua gateway: link tương đối same-origin (/showcase-tb) → cùng tiền tố
    # /showcase với trang + API, không cần mở cổng/CF. Gateway reverse-proxy /showcase-tb/* tới TB
    # tại _upstream (TB chạy --path_prefix=/showcase-tb). Đặt URL tuyệt đối nếu muốn trỏ TB ra ngoài.
    showcase_tensorboard_base_url: str = "/showcase-tb"
    showcase_tensorboard_upstream: str = "http://127.0.0.1:6006"
    # Service kích hoạt MLOps (trigger_api) chạy trên HOST — gateway reverse-proxy
    # /showcase-api/retraining/* tới đây để trang nghiệm thu gọi same-origin. Container thấy host
    # qua host.docker.internal (extra_hosts trong secai-docker/docker-compose.yml, giống /tb).
    showcase_retraining_upstream: str = "http://host.docker.internal:8877"
    # Nếu trigger_api bật HTTP Basic Auth (MLOPS_BASIC_AUTH_USER/PASS — bắt buộc khi expose ra
    # internet), khai cùng cặp "user:pass" ở đây để proxy tự thêm header Authorization: Basic khi
    # forward, giữ đường same-origin của trang nghiệm thu chạy được. Để trống nếu Basic Auth tắt.
    showcase_retraining_basic_auth: str = ""
    # Thời gian FE phát lại log của mỗi KPI = thời gian đo THẬT × hệ số này (thu nhỏ lại cho
    # vừa demo), kẹp trong [min, max]. Muốn chạy chậm/nhanh hơn thì chỉnh 3 số này, không phải
    # sửa code FE. Vd hệ số 12: bước retrieval thật 1550s → phát 18.6s; faithfulness 762s → 9.1s.
    # Thư mục hồ sơ gốc (mirror Drive) — nhóm S duyệt cây thư mục này để xem nguyên bản.
    showcase_hosogoc_dir: str = "/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI"
    # Cùng thư mục hồ sơ gốc nhưng theo đường dẫn HOST — để hiển thị ĐƯỜNG DẪN TUYỆT ĐỐI cho người
    # xem khi gateway chạy trong container (hồ sơ gốc bị mount/đổi tên thành /srv/showcase/hosogoc).
    showcase_hosogoc_host_dir: str = "/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI"
    # Khi chạy KPI bench trên web, server phát log theo thời gian thật (SSE) VÀ ghi vào file này
    # để ai `tail -f` trên server cũng thấy đúng từng dòng hiện ra — khớp với màn hình demo.
    showcase_kpi_log: str = "./logs/kpi-bench-live.log"
    # Bản chụp lệnh kiểm tra phần cứng (nvidia-smi/free/df/lsblk) chạy trên instance vast đang
    # serving. Làm mới: ssh vào máy chạy 4 lệnh rồi ghi lại JSON này (xem summary+raw+captured_at).
    showcase_hardware_check: str = "./data/hardware-check.json"
    # Log cron cập nhật KB (CVE/0-day) hằng ngày — nhóm "Cập nhật KB".
    showcase_kb_sync_log: str = "/home/ict/Downloads/CVE-DB/sync_cve.log"
    showcase_kb_cron: str = "0 3 * * *"
    # Triển khai: docker-compose + Helm chart để trưng trên UI (tiêu chí Docker image + Helm).
    showcase_docker_compose: str = "/home/ict/AIEngines/secai/secai-docker/docker-compose.yml"
    showcase_helm_dir: str = "./deploy/helm/secai-gateway"
    # Bộ build fine-tuning chạy được (hồ sơ gốc) — nguồn cho tab "Huấn luyện lại" + nút tải gói.
    showcase_ft_build_dir: str = "/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI/02. Bo_build_finetuning"
    # Bộ dò/làm sạch PII-Secret mà KPI R-SEC-PII (F1 98.11%) đo — tab Sanitize chạy ĐÚNG file này
    # (email/ip/phone/api_key/password), không phải gate gateway. Stdlib-only nên import trực tiếp.
    showcase_sanitizer_detector: str = "/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI/03. Kpi_results/Santization/Evaluation_code/detector.py"
    showcase_time_gen_scale_ms_per_s: float = 200.0
    showcase_time_gen_min_ms: int = 120000
    showcase_time_gen_max_ms: int = 300000

    # ── KPI Studio "Live" run (showcase/kpi_run.py) — chạy THẬT các script CLI bench
    # (02-GEN-TEST/bench_kpi.py cho nhóm B/C, scripts/bench.sh cho nhóm A), khác hẳn
    # kpi_bench.py (chỉ replay lại 1 bản ghi hồ sơ gốc đã đóng băng, xem module đó).
    # 2026-09-19 consolidation: KPI-VA-GEN-TEST/ is no longer bind-mounted from the host —
    # the runnable subset is vendored + baked into the image at build time (Dockerfile
    # `COPY kpi_bench ./kpi_bench`), so these now point at the in-container copy path.
    kpi_bench_scripts_dir: str = "/app/kpi_bench"
    # File .env.bench merge vào env của subprocess — rỗng thì dùng
    # <kpi_bench_scripts_dir>/.env.bench (mặc định của bench.sh).
    kpi_bench_env_file: str = ""
    kpi_bench_python: str = "python3"
    kpi_bench_smoke_limit: int = 5
    # Bộ đề nhóm A cho chay_eval_gateway_moi.sh --dataset (rỗng -> <scripts_dir>/02-GEN-TEST/testset-v2).
    kpi_bench_testset_dir: str = "/app/kpi_bench/02-GEN-TEST/testset-v2"
    # Lịch sử các lần chạy LIVE (khác hẳn localStorage phía FE) — /showcase-api/kpi-run/runs
    # và /runs/{run_id} đọc lại từ đây.
    kpi_run_history_file: str = "./logs/kpi-run-history.json"

    @model_validator(mode="after")
    def _enforce_airgap(self) -> "Settings":
        """When air-gap is on, force every internet-touching feature flag off — regardless
        of what the env/.env set them to. This is layer 1 of the guarantee: the tools that
        would reach the internet are never built (runner.py gates on these flags) and the
        deterministic web-research/on-demand-fetch steps short-circuit. Layer 2 is the
        per-fetch `airgap.assert_online_fetch_allowed()` backstop. Internal service
        endpoints (qdrant/neo4j/embedding/reranker/LLM) are intentionally NOT touched."""
        if self.airgap:
            self.web_fetch_enabled = False
            self.fetch_reference_enabled = False
            self.web_search_enabled = False
            self.github_advisory_enabled = False
            self.poc_github_ondemand_enabled = False
            self.searxng_url = ""
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def verify_targets(self) -> list[str]:
        return [t.strip() for t in self.verify_allowed_targets.split(",") if t.strip()]


def get_settings() -> Settings:
    """Build a fresh Settings from the current environment + .env.

    Intentionally uncached: construction is cheap and re-reading keeps ``create_app``
    honest under tests that patch the environment between app instances.
    """
    return Settings()
