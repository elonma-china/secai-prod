/** API mới cho báo cáo theo mẫu. Khóa chỉ được giữ trong state của màn hình. */

const BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") || "";
const PREFIX = "/ai/v2/reports";

export interface ReportTemplateInput {
  name: string;
  description: string;
  sections: string[];
  instructions: string;
  example: string;
}

export interface ReportTemplate extends ReportTemplateInput {
  id: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface GeneratedReport {
  id: string;
  template_id: string;
  template_version: number;
  revision: number;
  content: string;
  created_at: string;
}

export interface ReportRevision {
  revision: number;
  content: string;
  created_at: string;
}

export class ReportApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ReportApiError";
  }
}

function errorMessage(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      const row = item as { loc?: unknown[]; msg?: string };
      return `${Array.isArray(row.loc) ? row.loc.slice(1).join(".") : "Trường"}: ${row.msg || "không hợp lệ"}`;
    }).join("; ");
  }
  return fallback;
}

async function request<T>(key: string, path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("X-API-Key", key);
  const response = await fetch(`${BASE}${PREFIX}${path}`, { ...options, headers });
  const raw = await response.text();
  let body: unknown;
  try {
    body = raw ? JSON.parse(raw) : null;
  } catch {
    body = null;
  }
  if (!response.ok) {
    throw new ReportApiError(response.status, errorMessage(body, response.statusText || "Yêu cầu thất bại"));
  }
  return body as T;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const reportMvpApi = {
  listTemplates: (key: string) => request<ReportTemplate[]>(key, "/templates"),
  createTemplate: (key: string, body: ReportTemplateInput) =>
    request<ReportTemplate>(key, "/templates", json("POST", body)),
  importTemplate: (key: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<ReportTemplateInput>(key, "/templates/import", { method: "POST", body });
  },
  updateTemplate: (key: string, id: string, body: ReportTemplateInput, expectedVersion: number) =>
    request<ReportTemplate>(key, `/templates/${encodeURIComponent(id)}`, json("PATCH", { ...body, expected_version: expectedVersion })),
  generate: (key: string, templateId: string, data: string) =>
    request<GeneratedReport>(key, "/generate", json("POST", { template_id: templateId, data })),
  getReport: (key: string, id: string) =>
    request<GeneratedReport>(key, `/${encodeURIComponent(id)}`),
  editReport: (key: string, id: string, instruction: string, expectedRevision: number) =>
    request<GeneratedReport>(key, `/${encodeURIComponent(id)}/edit`, json("POST", { instruction, expected_revision: expectedRevision })),
  listRevisions: (key: string, id: string) =>
    request<ReportRevision[]>(key, `/${encodeURIComponent(id)}/revisions`),
};
