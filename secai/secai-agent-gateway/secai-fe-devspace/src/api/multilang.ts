import { ApiError, streamRequest } from "../lib/sse";

export type MultilangLanguageCode = "vi" | "en" | "zh-Hans" | "ja" | "ko" | "fr" | "de" | "es" | "ru" | "ar";
export type MultilangStatus = "disabled" | "beta" | "stable";

export interface MultilangLanguage {
  code: MultilangLanguageCode;
  name: string;
  native_name: string;
  aliases: string[];
  script: "Latn" | "Hans" | "Jpan" | "Kore" | "Cyrl" | "Arab";
  direction: "ltr" | "rtl";
}

export interface MultilangPair {
  source_lang: MultilangLanguageCode;
  target_lang: MultilangLanguageCode;
  enabled: boolean;
  status: MultilangStatus;
}

export interface MultilangCapabilities {
  languages: MultilangLanguage[];
  pairs: MultilangPair[];
  limits: {
    max_text_chars: number;
    max_source_tokens: number;
    max_chunks: number;
    request_timeout_seconds: number;
  };
}

export interface MultilangTranslationRequest {
  text: string;
  source_lang: MultilangLanguageCode;
  target_lang: MultilangLanguageCode;
}

export interface MultilangTranslationResponse {
  translated_text: string;
  source_lang: MultilangLanguageCode;
  target_lang: MultilangLanguageCode;
  warnings: Array<"SOURCE_LANGUAGE_UNCERTAIN" | "TARGET_LANGUAGE_UNCERTAIN">;
  metadata: { request_id: string; chunks: number; attempts: number };
}

export class MultilangApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | null,
    readonly requestId: string | null,
    readonly retryAfter: number | null,
  ) {
    super(code ?? `HTTP ${status}`);
    this.name = "MultilangApiError";
  }
}

const BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") || "";

async function readJson<T>(response: Response): Promise<T> {
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = data && typeof data === "object" && "error" in data ? data.error : null;
    const envelope = error && typeof error === "object" ? error as Record<string, unknown> : {};
    const retryAfter = Number(response.headers.get("Retry-After"));
    throw new MultilangApiError(
      response.status,
      typeof envelope.code === "string" ? envelope.code : null,
      typeof envelope.request_id === "string" ? envelope.request_id : null,
      Number.isInteger(retryAfter) && retryAfter >= 1 ? retryAfter : null,
    );
  }
  return data as T;
}

export async function getMultilangCapabilities(signal?: AbortSignal): Promise<MultilangCapabilities> {
  return readJson<MultilangCapabilities>(await fetch(`${BASE}/ai/v3/translate/languages`, { method: "GET", signal }));
}

export async function translateMultilang(
  request: MultilangTranslationRequest,
  signal?: AbortSignal,
): Promise<MultilangTranslationResponse> {
  return readJson<MultilangTranslationResponse>(await fetch(`${BASE}/ai/v3/translate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  }));
}

export async function streamMultilang(
  request: MultilangTranslationRequest,
  onChunk: (delta: string) => void,
  signal?: AbortSignal,
): Promise<MultilangTranslationResponse> {
  let final: MultilangTranslationResponse | null = null;
  let failure: MultilangApiError | null = null;
  const envelopeError = (body: Record<string, unknown>, status: number, retryAfter: number | null = null) => {
    const error = body.error as Record<string, unknown> | undefined;
    return new MultilangApiError(status, typeof error?.code === "string" ? error.code : null,
      typeof error?.request_id === "string" ? error.request_id : null, retryAfter);
  };
  try {
    await streamRequest(`${BASE}/ai/v3/translate/stream`, { ...request }, {
      onToken: (delta) => { if (!failure) onChunk(delta); },
      onErrorPayload: (payload) => { failure = envelopeError(payload, 200); },
      onFinal: (payload) => { if (!failure) final = payload as unknown as MultilangTranslationResponse; },
    }, signal);
  } catch (error) {
    if (error instanceof ApiError) {
      const body = error.body as Record<string, unknown> | undefined;
      throw envelopeError(body ?? {}, error.status, error.retryAfter);
    }
    throw error;
  }
  if (failure) throw failure;
  if (!final) throw new MultilangApiError(200, "STREAM_INCOMPLETE", null, null);
  return final;
}
