"""Extract the exact same text blocks used in the earlier MT benchmark, for a fair
LLM-based comparison. Run with the gateway .venv."""
import sys, types
sys.path.insert(0, "/home/ict/AIEngines/secai/secai-agent-gateway-wt-llm-translate/src")
pkg = types.ModuleType("secai_gateway"); pkg.__path__ = ["/home/ict/AIEngines/secai/secai-agent-gateway-wt-llm-translate/src/secai_gateway"]
sys.modules["secai_gateway"] = pkg
tpkg = types.ModuleType("secai_gateway.translation"); tpkg.__path__ = ["/home/ict/AIEngines/secai/secai-agent-gateway-wt-llm-translate/src/secai_gateway/translation"]
sys.modules["secai_gateway.translation"] = tpkg

from pathlib import Path
from secai_gateway.translation import documents
from secai_gateway.translation.settings import TranslationSettings
import json

settings = TranslationSettings()
cases = {
    "long30p": "/home/ict/AIEngines/secai/secai-agent-gateway-wt-translate-perf/secai-fe-devspace/e2e/fixtures/translate/long-30p.docx",
    "pdf20p": "/tmp/claude-1000/-home-ict-AIEngines-secai-secai-agent-gateway/f5647c66-87a3-4622-8082-3e95b9d130a9/scratchpad/translate-perf/real-20p.pdf",
    "heavy50mb": "/tmp/claude-1000/-home-ict-AIEngines-secai-secai-agent-gateway/f5647c66-87a3-4622-8082-3e95b9d130a9/scratchpad/translate-perf/heavy-50mb.docx",
}
out = {}
for name, path in cases.items():
    p = Path(path)
    if p.suffix == ".docx":
        doc = documents.extract_docx(p, settings)
    else:
        doc, scanned = documents.extract_pdf(p, settings)
    texts = [b.text for b in doc.blocks]
    out[name] = texts
    print(name, "blocks:", len(texts), "avg_chars:", sum(len(t) for t in texts)/len(texts))

with open("/tmp/claude-1000/-home-ict-AIEngines-secai-secai-agent-gateway/f5647c66-87a3-4622-8082-3e95b9d130a9/scratchpad/translate-perf/blocks.json", "w") as f:
    json.dump(out, f, ensure_ascii=False)
print("saved blocks.json")
