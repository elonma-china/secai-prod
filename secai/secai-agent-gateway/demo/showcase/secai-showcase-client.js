// SecAI Showcase — client gọi API nghiệm thu AI. Không phụ thuộc framework.
// Dùng: import { SecaiShowcase } from './secai-showcase-client.js'
//        const api = new SecaiShowcase('http://<gateway>:8131')
// Mọi hàm trả Promise, ném Error(message) khi lỗi.
export class SecaiShowcase {
  constructor(baseUrl = "") {
    this.base = baseUrl.replace(/\/$/, "");
  }

  async _get(path) {
    const res = await fetch(this.base + path, { headers: { Accept: "application/json" } });
    const body = await res.json().catch(() => null);
    if (!res.ok) throw new Error(body?.message || `HTTP ${res.status}`);
    return body;
  }
  async _post(path, payload) {
    const res = await fetch(this.base + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => null);
    if (!res.ok) throw new Error(body?.message || `HTTP ${res.status}`);
    return body;
  }

  // ── Finetuning ────────────────────────────────────────────────
  datasets() { return this._get("/showcase-api/finetune/datasets"); }
  datasetSample(id, limit = 5) { return this._get(`/showcase-api/finetune/datasets/${encodeURIComponent(id)}/sample?limit=${limit}`); }
  runLog(runId, granularity = "step", maxPoints = 300) {
    return this._get(`/showcase-api/finetune/runs/${encodeURIComponent(runId)}/log?granularity=${granularity}&max_points=${maxPoints}`);
  }
  runCharts(runId) { return this._get(`/showcase-api/finetune/runs/${encodeURIComponent(runId)}/charts`); }
  kpiBaseline() { return this._get("/showcase-api/finetune/kpi-baseline"); }
  datasetAppendix() { return this._get("/showcase-api/finetune/dataset-appendix"); }
  dataProcessing() { return this._get("/showcase-api/finetune/data-processing"); }
  // URL ảnh chart để gán thẳng <img src>:
  assetUrl(filename) { return `${this.base}/showcase-api/finetune/assets/${filename}`; }

  // ── Model & phần cứng ─────────────────────────────────────────
  modelInfo() { return this._get("/showcase-api/model-info"); }

  // ── KPI Bench ─────────────────────────────────────────────────
  kpiGroups() { return this._get("/showcase-api/kpi-bench/groups"); }
  kpiTests(groupId) { return this._get(`/showcase-api/kpi-bench/groups/${groupId}/tests`); }
  kpiResult(testId) { return this._get(`/showcase-api/kpi-bench/tests/${testId}/result`); }

  // ── Sanitize demo (POST) ──────────────────────────────────────
  sanitize(text) { return this._post("/showcase-api/sanitize-demo", { text }); }

  // ── Evidence theo dòng sheet ──────────────────────────────────
  evidenceList() { return this._get("/showcase-api/evidence"); }
  evidence(id) { return this._get(`/showcase-api/evidence/${encodeURIComponent(id)}`); }
  evidenceMatch(contractText) { return this._get(`/showcase-api/evidence/match?contract_text=${encodeURIComponent(contractText)}`); }

  // ── Duyệt hồ sơ gốc ───────────────────────────────────────────
  tree(path = "") { return this._get(`/showcase-api/source-dossier/tree?path=${encodeURIComponent(path)}`); }
  file(path) { return this._get(`/showcase-api/source-dossier/file?path=${encodeURIComponent(path)}`); }
  rawUrl(path) { return `${this.base}/showcase-api/source-dossier/raw?path=${encodeURIComponent(path)}`; }
}
