"""MT service song song: cùng hợp đồng /v1/translate (Bearer, 3 trường, EOS) nhưng nhiều request đồng thời
tới 1 llama-server resident nhiều slot. Dịch/prompt/kiểm EOS dùng nguyên TranslationRuntime đã duyệt."""
import asyncio, os, sys, time
from pathlib import Path
sys.path.insert(0, "/workspace/secai-specialist-services/source")
import httpx, uvicorn
from deploy.mt_cpu_service.service import ServiceFailure, TranslationRuntime, create_app
from deploy.gpu_stage.mt_diagnostic import GuardedBackend, diagnostic_settings

PAR = int(os.environ.get("FASTMT_PARALLEL", "12"))
tf = Path("/workspace/secai-specialist-services/mt.token")
settings = diagnostic_settings(token=tf.read_text().strip(), token_file=tf)


class ParallelRuntime:
    """Cân tải giữa R replica llama-server (mỗi replica 1 TranslationRuntime + GuardedBackend riêng)."""
    poisoned = False

    def __init__(self, settings, ports, total_slots):
        self.runtimes = []
        for port in ports:
            client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False, follow_redirects=False,
                                       limits=httpx.Limits(max_connections=256, max_keepalive_connections=64))
            self.runtimes.append(TranslationRuntime(settings, GuardedBackend(client)))
        self.inflight = [0] * len(ports)
        self.sem = asyncio.Semaphore(total_slots)

    async def translate(self, text, source_lang, target_lang, *, deadline, disconnected=None):
        async with self.sem:
            i = min(range(len(self.runtimes)), key=self.inflight.__getitem__)
            rt = self.runtimes[i]
            self.inflight[i] += 1
            try:
                if time.monotonic() >= deadline:
                    raise ServiceFailure(504, "TIMEOUT")
                if not rt.backend.live:
                    await rt.backend.start(deadline=deadline)
                return await asyncio.wait_for(rt._translate_inner(text, target_lang, deadline),
                                              timeout=max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                raise ServiceFailure(504, "TIMEOUT") from None
            except (httpx.HTTPError, OSError):
                rt.backend.live = False
                raise ServiceFailure(503, "UNAVAILABLE") from None
            except ServiceFailure as exc:
                if exc.code == "UNAVAILABLE":
                    rt.backend.live = False
                raise
            finally:
                self.inflight[i] -= 1


R = int(os.environ.get("FASTMT_REPLICAS", "3"))
PORTS = [18767 + i for i in range(R)]
app = create_app(settings, ParallelRuntime(settings, PORTS, PAR * R))
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18868, log_level="warning", backlog=2048)
