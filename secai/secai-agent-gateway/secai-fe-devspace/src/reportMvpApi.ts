/** API báo cáo: Devspace dùng kết nối workspace do proxy máy chủ xác thực. */

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

export type ReportExportFormat = "txt" | "docx" | "pdf";

const EXPORT_MIME: Record<ReportExportFormat, string> = {
  txt: "text/plain",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pdf: "application/pdf",
};

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
  if (key) headers.set("X-API-Key", key);
  else headers.set("X-Report-Workspace", "shared");
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
  exportReport: async (key: string, id: string, format: ReportExportFormat, revision: number) => {
    const headers = new Headers();
    if (key) headers.set("X-API-Key", key);
    else headers.set("X-Report-Workspace", "shared");
    const query = new URLSearchParams({ format, revision: String(revision) });
    const response = await fetch(`${BASE}${PREFIX}/${encodeURIComponent(id)}/export?${query}`, { headers });
    if (!response.ok) {
      let body: unknown;
      try { body = await response.json(); } catch { body = null; }
      throw new ReportApiError(response.status, errorMessage(body, response.statusText || "Không thể xuất báo cáo."));
    }
    const mime = response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase();
    if (mime !== EXPORT_MIME[format]) {
      throw new ReportApiError(502, "Máy chủ trả về định dạng tệp không hợp lệ.");
    }
    const blob = await response.blob();
    if (!blob.size) throw new ReportApiError(502, "Máy chủ trả về tệp rỗng.");
    const safeId = id.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 80);
    return { blob, filename: `report-${safeId}-v${revision}.${format}` };
  },
  listTemplates: (key: string = "") => request<ReportTemplate[]>(key, "/templates"),
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
