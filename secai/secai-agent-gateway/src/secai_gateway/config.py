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

    # ── Bughunt / BugHunter recon engine ───────────────────────────────────────
    # Path to claude-bug-bounty clone (empty = auto sibling of gateway / BUGBOUNTY_ROOT).
    bugbounty_root: str = ""
    # Run tools/recon_engine.sh via run_recon tool (per-run workdir).
    bughunt_recon_engine: bool = True
    # auto | host | kali — auto prefers kali when host lacks nmap/nuclei/ffuf.
    bughunt_recon_backend: str = "auto"
    bughunt_recon_timeout_s: int = 600

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
