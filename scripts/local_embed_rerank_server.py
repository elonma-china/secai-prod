"""Local OpenAI-compatible embedding + rerank server (single GPU, transformers).

  POST /v1/embeddings  {model, input: str|list[str]}   -> CLS pooling + L2 normalize (bge-m3 style)
  POST /v1/rerank      {model, query, documents, top_n} -> Qwen3-reranker yes/no probability
Usage: python scripts/local_embed_rerank_server.py embed|rerank --port N [--api-key K]
Models come from the local HF cache (offline).
"""

import argparse
import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import torch
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer

EMBED_MODEL = "DuyTa/sec-embedding"
RERANK_MODEL = "DuyTa/sec-rerank"
INSTRUCT = "Given a web search query, retrieve relevant passages that answer the query"
PREFIX = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the "
          'Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def build(kind: str, api_key: str) -> FastAPI:
    app = FastAPI()
    dev = "cuda"

    def auth(authorization: str | None = Header(default=None)) -> None:
        if api_key and authorization != f"Bearer {api_key}":
            raise HTTPException(401, "unauthorized")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "kind": kind}

    if kind == "embed":
        tok = AutoTokenizer.from_pretrained(EMBED_MODEL)
        model = AutoModel.from_pretrained(EMBED_MODEL, torch_dtype=torch.float16).to(dev).eval()

        class Req(BaseModel):
            model: str | None = None
            input: str | list[str]

        @app.post("/v1/embeddings", dependencies=[Depends(auth)])
        def embeddings(req: Req) -> dict:
            texts = [req.input] if isinstance(req.input, str) else req.input
            out: list[list[float]] = []
            with torch.inference_mode():
                for i in range(0, len(texts), 32):
                    b = tok(texts[i:i + 32], padding=True, truncation=True, max_length=512,
                            return_tensors="pt").to(dev)
                    cls = model(**b).last_hidden_state[:, 0]
                    out += torch.nn.functional.normalize(cls.float(), dim=-1).cpu().tolist()
            return {"object": "list", "model": req.model or EMBED_MODEL,
                    "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(out)]}
    else:
        tok = AutoTokenizer.from_pretrained(RERANK_MODEL, padding_side="left")
        model = AutoModelForCausalLM.from_pretrained(RERANK_MODEL, torch_dtype=torch.float16).to(dev).eval()
        yes, no = tok.convert_tokens_to_ids("yes"), tok.convert_tokens_to_ids("no")

        class RReq(BaseModel):
            model: str | None = None
            query: str
            documents: list[str]
            top_n: int | None = None

        @app.post("/v1/rerank", dependencies=[Depends(auth)])
        def rerank(req: RReq) -> dict:
            prompts = [PREFIX + f"<Instruct>: {INSTRUCT}\n<Query>: {req.query}\n<Document>: {d}" + SUFFIX
                       for d in req.documents]
            scores: list[float] = []
            with torch.inference_mode():
                for i in range(0, len(prompts), 8):
                    b = tok(prompts[i:i + 8], padding=True, truncation=True, max_length=4096,
                            return_tensors="pt").to(dev)
                    logits = model(**b).logits[:, -1, :]
                    pair = torch.stack([logits[:, no], logits[:, yes]], dim=1).float()
                    scores += torch.softmax(pair, dim=1)[:, 1].cpu().tolist()
            order = sorted(range(len(scores)), key=lambda i: -scores[i])[: req.top_n or len(scores)]
            return {"model": req.model or RERANK_MODEL,
                    "results": [{"index": i, "relevance_score": scores[i],
                                 "document": {"text": req.documents[i]}} for i in order]}
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["embed", "rerank"])
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="172.17.0.1")
    ap.add_argument("--api-key", default=os.environ.get("LOCAL_SERVING_API_KEY", ""))
    a = ap.parse_args()
    uvicorn.run(build(a.kind, a.api_key), host=a.host, port=a.port, log_level="warning")
