"""Dedicated resource and dependency settings for anonymous translation v2."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TranslationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRANSLATE_V2_", env_file=".env",
                                      env_file_encoding="utf-8", extra="ignore")

    enabled: bool = False
    ocr_enabled: bool = False
    ocr_base_url: str = ""
    ocr_api_key: str = Field(default="", repr=False)
    ocr_model: str = ""
    ocr_timeout_seconds: float = 120
    ocr_max_retries: int = 1

    db_path: str = "./data/translate-v2/jobs.sqlite3"
    artifact_dir: str = "./data/translate-v2/artifacts"
    worker_db_path: str = "./data/translate-v2/worker-gateway.sqlite3"
    converter_image: str = "secai-doc-converter:1"
    max_upload_bytes: int = 10 * 1024 * 1024
    max_request_bytes: int = 11 * 1024 * 1024
    max_multipart_parts: int = 8
    max_text_field_bytes: int = 4096
    max_image_pixels: int = 20_000_000
    max_image_edge: int = 10_000
    max_text_codepoints: int = 200_000
    max_blocks: int = 10_000
    max_chunks: int = 300
    max_docx_entries: int = 2_000
    max_docx_inflated_bytes: int = 50 * 1024 * 1024
    max_docx_ratio: int = 100
    max_chunk_codepoints: int = 6_000
    max_running_jobs: int = 2
    max_llm_calls: int = 4
    max_ocr_calls: int = 1
    max_queued_jobs: int = 20
    max_active_per_ip: int = 2
    upload_rate_per_minute: int = 5
    lookup_rate_per_minute: int = 60
    converter_timeout_seconds: float = 60
    llm_timeout_seconds: float = 120
    llm_max_retries: int = 1
    job_deadline_seconds: int = 30 * 60
    storage_quota_bytes: int = 5 * 1024 * 1024 * 1024
    artifact_cleanup_seconds: int = 5 * 60
    result_ttl_seconds: int = 24 * 60 * 60
    tombstone_ttl_seconds: int = 24 * 60 * 60
    reaper_interval_seconds: int = 5 * 60
    lease_seconds: int = 60
    heartbeat_seconds: int = 15
    max_reclaims: int = 1
