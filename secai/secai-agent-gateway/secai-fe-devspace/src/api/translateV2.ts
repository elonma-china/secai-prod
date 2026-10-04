/** Browser client for the durable translation v2 file/image jobs. */
export type V2Lang = "vi" | "en" | "zh" | "ja" | "ko" | "fr" | "de" | "es" | "ru" | "ar";
export type V2Source = V2Lang | "auto";
export type V2JobStatus = "queued" | "running" | "cancelling" | "cancelled" | "succeeded" | "failed";
export type V2Kind = "file" | "image";

export interface V2Warning { code: string; message: string; block_id: string | null }
export interface V2Job {
  job_id: string;
  kind: V2Kind;
  status: V2JobStatus;
  stage: string;
  progress: { completed: number; total: number | null };
  source_lang: V2Source;
  target_lang: V2Lang;
  warnings: V2Warning[];
  error: { code: string; message: string; request_id: string } | null;
  expires_at?: string | null;
}
export interface V2Result {
  job_id: string;
  kind: V2Kind;
  status: "succeeded";
  source_lang: V2Source;
  target_lang: V2Lang;
  translated_text: string;
  blocks: Array<{ id: string; kind: string; order: number; source_text: string; translated_text: string; metadata: Record<string, unknown> }>;
  warnings: V2Warning[];
  ocr: null | { raw_text: string; corrected_text: string; edits: Array<Record<string, unknown>>; needs_review: boolean };
}

export class TranslateV2Error extends Error {
  constructor(readonly status: number, readonly code: string | null, readonly retryAfter: number | null) {
    super(code ?? (status ? `HTTP ${status}` : "NETWORK_ERROR"));
    this.name = "TranslateV2Error";
  }
}

const BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") || "";
const REQUEST_TIMEOUT_MS = 20_000;

async function request<T>(path: string, init: RequestInit, expectedStatus: number): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${BASE}${path}`, { ...init, signal: controller.signal });
    const retryValue = response.headers.get("Retry-After");
    const parsedRetry = retryValue === null ? NaN : Number(retryValue);
    const retryAfter = Number.isInteger(parsedRetry) && parsedRetry >= 1 ? parsedRetry : null;
    const value: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const envelope = value && typeof value === "object" && "error" in value ? value.error : null;
      const code = envelope && typeof envelope === "object" && "code" in envelope && typeof envelope.code === "string" ? envelope.code : null;
      throw new TranslateV2Error(response.status, code, retryAfter);
    }
    if (response.status !== expectedStatus) throw new TranslateV2Error(0, "INVALID_RESPONSE", null);
    if (!value || typeof value !== "object") throw new TranslateV2Error(0, "INVALID_RESPONSE", null);
    return value as T;
  } catch (error) {
    if (error instanceof TranslateV2Error) throw error;
    throw new TranslateV2Error(0, "NETWORK_ERROR", null);
  } finally {
    clearTimeout(timer);
  }
}

export function submitTranslationFile(file: File, source: V2Source, target: V2Lang, key: string): Promise<V2Job> {
  const form = new FormData();
  form.append("file", file);
  form.append("source_lang", source);
  form.append("target_lang", target);
  const image = /\.(png|jpe?g)$/i.test(file.name);
  return request<V2Job>(`/ai/v2/translate/${image ? "images" : "files"}`, { method: "POST", headers: { "Idempotency-Key": key }, body: form }, 202);
}

export function getTranslationJob(id: string): Promise<V2Job> {
  return request<V2Job>(`/ai/v2/translate/jobs/${id}`, { method: "GET" }, 200);
}

export function getTranslationResult(id: string): Promise<V2Result> {
  return request<V2Result>(`/ai/v2/translate/jobs/${id}/result`, { method: "GET" }, 200);
}

export function cancelTranslationJob(id: string): Promise<V2Job> {
  return request<V2Job>(`/ai/v2/translate/jobs/${id}`, { method: "DELETE" }, 202);
}
