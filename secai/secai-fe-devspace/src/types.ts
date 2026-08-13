// Contract khớp src/secai_gateway/api/{routes,sse,schemas}.py

export type Tab = "chat" | "poc" | "translate" | "bughunt";

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

/** SSE ``event: phase`` from Linch WorkflowEvent (bughunt AgentHunt/Validate). */
export interface PhaseEvent {
  kind?: string;
  title?: string;
  call_key?: string;
  occurrence?: number;
  subagent_type?: string;
  result_text?: string | null;
  structured_output?: Record<string, unknown> | null;
  structured_error?: string | null;
}

export interface StreamHandlers {
  onToken?: (delta: string) => void;
  onToolCall?: (t: ToolCall) => void;
  onToolResult?: (t: ToolCall) => void;
  onRegenerate?: (r: { hook?: string; reason?: string }) => void;
  // Mục 1 (mã PoC) đẩy sớm ngay khi backend fetch xong — song song với model đang gõ Mục
  // 2-4 (xem /ai/exploit/stream). `preview=true`: có thể bị `final` thay thế/chính xác hoá.
  onSection1?: (body: string, preview: boolean) => void;
  onPhase?: (p: PhaseEvent) => void;
  /** Live terminal line from recon_engine / scanners. */
  onLog?: (p: { stream?: string; line?: string; run_id?: string }) => void;
  onError?: (msg: string) => void;
  onFinal?: (payload: Record<string, unknown>) => void;
}
