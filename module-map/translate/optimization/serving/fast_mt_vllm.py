"""MT service dùng vLLM BF16 (continuous batching lớn) sau cùng hợp đồng /v1/translate.
Giữ nguyên TranslationRuntime đã duyệt (prompt, giới hạn token, kiểm EOS); chỉ thay backend:
  /apply-template, /tokenize  -> tokenizer HF cục bộ (cùng chat template của model)
  /completion                 -> vLLM /v1/completions (prompt = token ids, greedy, stop_token_ids)."""
import asyncio, os, sys, time
from pathlib import Path
sys.path.insert(0, "/workspace/secai-specialist-services/source")
import httpx, uvicorn
from transformers import AutoTokenizer
from deploy.mt_cpu_service.service import ServiceFailure, TranslationRuntime, create_app
from deploy.gpu_stage.mt_diagnostic import Q4_CANDIDATE, diagnostic_settings

MODEL_DIR = os.environ.get("VLLM_MODEL_DIR", "/workspace/hymt2-bf16")
VLLM = os.environ.get("VLLM_URL", "http://127.0.0.1:18770")
NSEQ = int(os.environ.get("FASTMT_CONCURRENCY", "128"))
TERMINAL = sorted(Q4_CANDIDATE.terminal_token_ids)
tf = Path("/workspace/secai-specialist-services/mt.token")
settings = diagnostic_settings(token=tf.read_text().strip(), token_file=tf)
tok = AutoTokenizer.from_pretrained(MODEL_DIR)
MODEL_NAME = f"{Q4_CANDIDATE.model}@{Q4_CANDIDATE.revision}"


class VllmBackend:
    def __init__(self):
        self.live = False
        self.client = httpx.AsyncClient(base_url=VLLM, trust_env=False, follow_redirects=False, timeout=120,
                                        limits=httpx.Limits(max_connections=512, max_keepalive_connections=256))

    async def start(self, deadline=None):
        r = await self.client.get("/health", timeout=5)
        if r.status_code != 200:
            raise ServiceFailure(503, "UNAVAILABLE")
        self.live = True

    async def post_json(self, path, body, deadline):
        if not self.live:
            raise ServiceFailure(503, "UNAVAILABLE", uncertain=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ServiceFailure(504, "TIMEOUT", uncertain=True)
        if path == "/apply-template":
            prompt = tok.apply_chat_template(body["messages"], tokenize=False,
                                             add_generation_prompt=bool(body.get("add_generation_prompt", True)))
            return {"prompt": prompt}
        if path == "/tokenize":
            ids = tok(body["content"], add_special_tokens=bool(body.get("add_special", False)))["input_ids"]
            return {"tokens": ids}
        if path != "/completion":
            raise ServiceFailure(503, "UNAVAILABLE", uncertain=True)
        req = {"model": "hymt", "prompt": body["prompt"], "max_tokens": body["n_predict"], "temperature": 0,
               "seed": body.get("seed", 42), "stop_token_ids": TERMINAL, "return_token_ids": True}
        r = await self.client.post("/v1/completions", json=req, timeout=remaining)
        if r.status_code != 200:
            raise ServiceFailure(502, "BAD_UPSTREAM", uncertain=True)
        ch = r.json()["choices"][0]
        ids = list(ch.get("token_ids") or [])
        stop = ch.get("finish_reason") == "stop"
        if stop and (not ids or ids[-1] not in TERMINAL):
            sr = ch.get("stop_reason")
            ids.append(sr if sr in TERMINAL else TERMINAL[0])  # vLLM không luôn trả token dừng
        return {"content": ch.get("text", ""), "tokens": ids, "model": MODEL_NAME,
                "stop_type": "eos" if stop else "limit", "truncated": not stop}

    async def reap(self):
        self.live = False
        return False


class ParallelRuntime:
    poisoned = False

    def __init__(self):
        self.rt = TranslationRuntime(settings, VllmBackend())
        self.sem = asyncio.Semaphore(NSEQ)

    async def translate(self, text, source_lang, target_lang, *, deadline, disconnected=None):
        async with self.sem:
            try:
                if time.monotonic() >= deadline:
                    raise ServiceFailure(504, "TIMEOUT")
                if not self.rt.backend.live:
                    await self.rt.backend.start(deadline=deadline)
                return await asyncio.wait_for(self.rt._translate_inner(text, target_lang, deadline),
                                              timeout=max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                raise ServiceFailure(504, "TIMEOUT") from None
            except (httpx.HTTPError, OSError):
                self.rt.backend.live = False
                raise ServiceFailure(503, "UNAVAILABLE") from None
            except ServiceFailure as exc:
                if exc.code == "UNAVAILABLE":
                    self.rt.backend.live = False
                raise


app = create_app(settings, ParallelRuntime())
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18868, log_level="warning", backlog=4096)
