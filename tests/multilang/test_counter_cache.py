"""Verified token counters are memoized by file identity without weakening verification."""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from secai_gateway.multilang import config as cfg
from secai_gateway.multilang.config import (HYMT_GGUF_TEMPLATE_SHA256, HYMT_TOKENIZER_CONFIG_SHA256,
                                            HYMT_TOKENIZER_REVISION, HYMT_TOKENIZER_SHA256, HyMtCounter,
                                            MultilangSettings, clear_counter_cache,
                                            verified_specialist_counter)


@pytest.fixture(autouse=True)
def fresh_cache():
    clear_counter_cache()
    yield
    clear_counter_cache()


def write_tokenizer(path: Path, vocab: dict[str, int]) -> str:
    tokenizer = Tokenizer(WordLevel(vocab=vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.save(str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def builds(monkeypatch):
    calls = []
    real = cfg._build_pinned_counter

    def counting(path, digest):
        calls.append(str(path))
        return real(path, digest)

    monkeypatch.setattr(cfg, "_build_pinned_counter", counting)
    return calls


def test_pinned_counter_is_built_once_and_shared(tmp_path, builds):
    path = tmp_path / "tokenizer.json"
    digest = write_tokenizer(path, {"[UNK]": 0, "hello": 1, "world": 2})
    first = cfg._pinned_counter(str(path), digest)
    second = cfg._pinned_counter(str(path), digest.upper())  # digest compare stays case-insensitive
    assert first is second and first("hello world") == 2
    assert len(builds) == 1


def test_replaced_file_forces_full_reverification(tmp_path, builds):
    path = tmp_path / "tokenizer.json"
    digest = write_tokenizer(path, {"[UNK]": 0, "hello": 1})
    assert cfg._pinned_counter(str(path), digest) is not None
    changed = write_tokenizer(path, {"[UNK]": 0, "hello": 1, "extra": 2})  # same path, new content
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 7_000_000_000))  # defeat coarse mtimes
    assert changed != digest
    assert cfg._pinned_counter(str(path), digest) is None  # old pin no longer matches the file
    assert cfg._pinned_counter(str(path), changed) is not None
    assert len(builds) == 3  # first, stale-digest re-check, new-digest build


def test_failures_are_never_cached_and_heal_when_fixed(tmp_path, builds):
    path = tmp_path / "tokenizer.json"
    digest = write_tokenizer(path, {"[UNK]": 0, "a": 1})
    assert cfg._pinned_counter(str(path), "0" * 64) is None
    assert cfg._pinned_counter(str(path), "0" * 64) is None
    assert len(builds) == 2  # a failed verification is re-run, not remembered
    assert cfg._pinned_counter(str(path), digest) is not None  # correct pin works immediately


def test_missing_or_empty_inputs_still_fail_closed(tmp_path):
    assert cfg._pinned_counter(str(tmp_path / "absent.json"), "a" * 64) is None
    assert cfg._pinned_counter("", "a" * 64) is None
    assert cfg._pinned_counter(str(tmp_path / "t.json"), "") is None
    directory = tmp_path / "dir"
    directory.mkdir()
    assert cfg._pinned_counter(str(directory), "a" * 64) is None


def test_concurrent_first_requests_build_exactly_once(tmp_path, monkeypatch):
    path = tmp_path / "tokenizer.json"
    digest = write_tokenizer(path, {"[UNK]": 0, "hello": 1})
    real = cfg._build_pinned_counter
    count = 0

    def slow(path_, digest_):
        nonlocal count
        count += 1
        time.sleep(0.2)  # widen the race window
        return real(path_, digest_)

    monkeypatch.setattr(cfg, "_build_pinned_counter", slow)
    start = threading.Barrier(16)
    results = []

    def worker():
        start.wait()
        results.append(cfg._pinned_counter(str(path), digest))

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert count == 1 and len({id(r) for r in results}) == 1 and results[0] is not None


def test_cache_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "_COUNTER_CACHE_MAX", 3)
    for index in range(7):
        path = tmp_path / f"t{index}.json"
        assert cfg._pinned_counter(str(path), write_tokenizer(path, {"[UNK]": 0, f"w{index}": 1})) is not None
    assert len(cfg._COUNTER_CACHE) <= 3


# --- the real pinned Hy-MT tokenizer (staged for the translation container) ---------------------
_CANDIDATES = (Path("/home/ict/secai-translation-docker-release/tokenizer"),
               Path("/home/ict/secai-workspaces/secai-multilang-cpu-runtime/hymt2-7b-tokenizer"))
_REAL = next((d for d in _CANDIDATES if (d / "tokenizer.json").is_file()
              and (d / "tokenizer_config.json").is_file()), None)


def hymt_settings(directory: Path, **changes):
    values = dict(enabled=True, enable_zh=True, backend="specialist", specialist_profile="hymt2_7b_gguf",
                  specialist_model="tencent/Hy-MT2-7B",
                  specialist_tokenizer_json=str(directory / "tokenizer.json"),
                  specialist_tokenizer_sha256=HYMT_TOKENIZER_SHA256,
                  specialist_tokenizer_config_json=str(directory / "tokenizer_config.json"),
                  specialist_tokenizer_config_sha256=HYMT_TOKENIZER_CONFIG_SHA256,
                  specialist_tokenizer_revision=HYMT_TOKENIZER_REVISION,
                  specialist_chat_template_sha256=HYMT_GGUF_TEMPLATE_SHA256,
                  specialist_context_tokens=4096, specialist_chunk_input_tokens=896,
                  specialist_max_output_tokens=512)
    values.update(changes)
    return MultilangSettings(_env_file=None, **values)


needs_real = pytest.mark.skipif(_REAL is None, reason="pinned public Hy-MT tokenizer not staged on this host")


@needs_real
def test_real_hymt_counter_is_reused_and_much_faster_than_the_first_build():
    settings = hymt_settings(_REAL)
    started = time.perf_counter()
    first = verified_specialist_counter(settings)
    cold = time.perf_counter() - started
    assert isinstance(first, HyMtCounter)
    started = time.perf_counter()
    for _ in range(200):
        assert verified_specialist_counter(settings) is first
    warm = (time.perf_counter() - started) / 200
    assert warm < 0.005 and warm < cold / 20, (cold, warm)
    assert first.prompt_tokens("zh-Hans", "Xin chào") == 30  # same recipe as the uncached path


@needs_real
@pytest.mark.parametrize("changes", [
    {"specialist_context_tokens": 2048},  # the CPU profile pins 4096
    {"specialist_max_output_tokens": 256},
    {"specialist_model": "someone/else"},
    {"specialist_tokenizer_sha256": "0" * 64},
])
def test_settings_guard_still_runs_even_when_the_counter_is_cached(changes):
    assert verified_specialist_counter(hymt_settings(_REAL)) is not None  # warm the cache
    assert verified_specialist_counter(hymt_settings(_REAL, **changes)) is None


@needs_real
def test_tampered_hymt_tokenizer_is_rejected_after_being_cached(tmp_path):
    for name in ("tokenizer.json", "tokenizer_config.json"):
        (tmp_path / name).write_bytes((_REAL / name).read_bytes())
    settings = hymt_settings(tmp_path)
    assert verified_specialist_counter(settings) is not None
    config = tmp_path / "tokenizer_config.json"
    config.write_bytes(config.read_bytes() + b" ")  # one byte of drift
    assert verified_specialist_counter(settings) is None
