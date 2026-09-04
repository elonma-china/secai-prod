// Contract khớp src/secai_gateway/api/{routes,sse,schemas}.py

export type Tab = "chat" | "poc" | "translate";

export interface ToolCall {
  id: string;
  name: string;
  input?: unknown;
  summary?: string;
  result?: string;
  is_error?: boolean;
  duration_ms?: number;
}

// metadata trả về từ /ai/exploit (một phần từ ledger.exploit_metadata)
export interface ExploitMeta {
  run_id?: string;
  profile?: string;
  candidate_status?: string;
  reference_code_injected?: boolean;
  synthesized?: boolean;
  section1_tier?: string;
  reason?: string;
  tool_registry?: string[];
  refs?: number;
  files?: number;
  sha256?: string[];
  evidence_ids?: string[];
  edb_ids?: string[];
  provenance?: Array<Record<string, unknown>>;
  [k: string]: unknown;
}

export interface StreamHandlers {
  onToken?: (delta: string) => void;
  onToolCall?: (t: ToolCall) => void;
  onToolResult?: (t: ToolCall) => void;
  onRegenerate?: (r: { hook?: string; reason?: string }) => void;
  // Mục 1 (mã PoC) đẩy sớm ngay khi backend fetch xong — song song với model đang gõ Mục
  // 2-4 (xem /ai/exploit/stream). `preview=true`: có thể bị `final` thay thế/chính xác hoá.
  onSection1?: (body: string, preview: boolean) => void;
  onError?: (msg: string) => void;
  onFinal?: (payload: Record<string, unknown>) => void;
}
