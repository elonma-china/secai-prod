"""Offline safety checks for the isolated Q4 GPU diagnostic launcher."""

import json

import pytest

from evals.multilang import gpu_specialist_server as gpu


def snapshot(*, free=8772, added=None, utilization=0):
    processes = {3203145: 65316, 3257875: 1892, 3276685: 5050}
    processes.update(added or {})
    return {"uuid": gpu.GPU_UUID, "name": gpu.GPU_NAME,
            "driver": gpu.DRIVER, "total_mib": 81920,
            "free_mib": free, "utilization_percent": utilization,
            "processes_mib": processes}


def test_static_preflight_requires_original_incumbents_and_free_reserve():
    assert gpu.validate_snapshot(snapshot(), before_load=True) == (0, None)
    with pytest.raises(RuntimeError, match="gpu_process_set_changed"):
        gpu.validate_snapshot(snapshot(added={999: 1}), before_load=True)
    with pytest.raises(RuntimeError, match="gpu_free_reserve_breached"):
        gpu.validate_snapshot(snapshot(free=8191), before_load=True)
    with pytest.raises(RuntimeError, match="gpu_busy_before_load"):
        gpu.validate_snapshot(snapshot(utilization=1), before_load=True)


def test_host_gpu_pid_discovered_without_assuming_container_pid():
    host_pid = 918273
    assert gpu.validate_snapshot(snapshot(added={host_pid: 4500}, free=3900)) == (4500, host_pid)
    assert gpu.validate_snapshot(snapshot(added={host_pid: 4700}, free=3500),
                                 owner_host_pid=host_pid) == (4700, host_pid)
    with pytest.raises(RuntimeError, match="owned_gpu_host_pid_disappeared"):
        gpu.validate_snapshot(snapshot(free=8772), owner_host_pid=host_pid)
    with pytest.raises(RuntimeError, match="gpu_process_set_changed"):
        gpu.validate_snapshot(snapshot(added={host_pid: 4500, 77: 1}, free=3900),
                              owner_host_pid=host_pid)


def test_owned_cap_and_dynamic_free_reserve():
    with pytest.raises(RuntimeError, match="owned_vram_cap_exceeded"):
        gpu.validate_snapshot(snapshot(added={99: 6145}, free=2627))
    with pytest.raises(RuntimeError, match="gpu_free_reserve_breached"):
        gpu.validate_snapshot(snapshot(added={99: 4000}, free=4191))
    assert gpu.validate_snapshot(snapshot(added={99: 4000}, free=4192)) == (4000, 99)


def test_pinned_server_command_forces_full_offload_and_correct_eog():
    command = gpu.server_command()
    assert command[command.index("--n-gpu-layers") + 1] == "99"
    assert command[command.index("--fit") + 1] == "off"
    assert command[command.index("--parallel") + 1] == "1"
    assert command[command.index("--ctx-size") + 1] == "2048"
    assert command[command.index("--verbosity") + 1] == "4"
    assert gpu.EOG_OVERRIDE in command
    assert "tokenizer.ggml.eos_token_id=int:127960" in gpu.EOG_OVERRIDE
    assert "tokenizer.ggml.eot_token_id=int:127967" in gpu.EOG_OVERRIDE


def test_separate_file_context_changes_only_ctx_and_never_relaxes_gpu_guard():
    raw = gpu.server_command()
    file_command = gpu.server_command(4096)
    assert raw[raw.index("--ctx-size") + 1] == "2048"
    assert file_command[file_command.index("--ctx-size") + 1] == "4096"
    assert ["4096" if item == "2048" else item for item in raw] == file_command
    assert gpu.OWNED_CAP_MIB == 6144
    assert gpu.FREE_RESERVE_MIB == 2048
    with pytest.raises(ValueError, match="unsupported_context_size"):
        gpu.server_command(8192)


def test_full_offload_log_is_required(tmp_path):
    log = tmp_path / "server.log"
    log.write_text("offloaded 32/33 layers to GPU\n")
    assert not gpu._full_offload(log)
    log.write_text("offloaded 33/33 layers to GPU\n")
    assert gpu._full_offload(log)


def test_listener_pid_proves_container_child_owns_loopback_port(monkeypatch):
    monkeypatch.setattr(gpu, "_lines", lambda _args: [
        'LISTEN 0 128 127.0.0.1:18767 0.0.0.0:* users:(("llama-server",pid=123,fd=4))'])
    assert gpu.listener_pid() == 123
    monkeypatch.setattr(gpu, "_lines", lambda _args: [])
    assert gpu.listener_pid() is None
    monkeypatch.setattr(gpu, "_lines", lambda _args: [
        'LISTEN 0 128 0.0.0.0:18767 0.0.0.0:* users:(("llama-server",pid=123,fd=4))'])
    with pytest.raises(RuntimeError, match="unexpected_server_listener"):
        gpu.listener_pid()


def test_cleanup_failure_poisons_even_if_gpu_sample_fails(tmp_path, monkeypatch):
    class Process:
        pid = 123

        def poll(self):
            return None

    run_dir = tmp_path / "evidence" / "gpu-run-test"
    run_dir.mkdir(parents=True)
    poisoned = tmp_path / "poisoned-cleanup.json"
    monkeypatch.setattr(gpu, "POISONED", poisoned)
    monkeypatch.setattr(gpu, "_stop_child", lambda _p: (_ for _ in ()).throw(OSError("stop failed")))
    monkeypatch.setattr(gpu, "gpu_snapshot", lambda: (_ for _ in ()).throw(OSError("sample failed")))
    monkeypatch.setattr(gpu.time, "sleep", lambda _seconds: None)
    summary = gpu.cleanup_child(Process(), run_dir, "safety_stop", 5190, 1291011)
    assert not summary["clean_exit_attested"]
    assert summary["final_gpu"] is None
    assert json.loads(poisoned.read_text())["clean_exit_attested"] is False
    assert json.loads((run_dir / "summary.json").read_text())["clean_exit_attested"] is False


def test_incomplete_prior_run_blocks_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu, "BASE", tmp_path)
    monkeypatch.setattr(gpu, "POISONED", tmp_path / "poisoned-cleanup.json")
    (tmp_path / "evidence" / "gpu-run-crashed").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="prior_gpu_cleanup_not_proven"):
        gpu.check_only()
