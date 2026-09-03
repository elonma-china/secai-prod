from __future__ import annotations

from pydantic import model_validator
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
    # Defaults = Cyber-F1 (Qwen3.5-MoE) model-card recommendation. Override per-deploy via
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
    gateway_db_path: str = "./data/gateway.db"
    redis_url: str = ""
    admin_api_key: str = ""
    allowed_origins: str = ""

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
