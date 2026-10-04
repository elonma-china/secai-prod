import type { Actor, Assignment, Campaign, ItemJudgments, Judgment, JudgmentInput, Page, QueueItem, ReviewItem, ReviewStats, PromptMessage } from "./reviewTypes";
const BASE = ((import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") || "") + "/review-api";
export class ReviewApiError extends Error { constructor(public status: number, message: string) { super(message); } }
export function reviewError(error: unknown): string { return error instanceof ReviewApiError ? `Lỗi ${error.status}: ${error.message}` : "Không kết nối được máy chủ. Kiểm tra kết nối và thử lại."; }
export function createReviewApi(token: string) {
  const authHeaders: Record<string, string> = token === "devspace:admin" || token === "devspace:reviewer"
    ? {"X-Review-Role": token.slice("devspace:".length)}
    : {Authorization: `Bearer ${token}`};
  async function request<T>(path: string, method = "GET", body?: unknown, contentType = "application/json"): Promise<T> {
    const response = await fetch(BASE + path, {method, headers: {...authHeaders, ...(body === undefined ? {} : {"Content-Type": contentType})}, body: body === undefined ? undefined : contentType === "application/json" ? JSON.stringify(body) : body as BodyInit});
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const detail = data.detail;
      throw new ReviewApiError(response.status, typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : response.statusText);
    }
    return response.status === 204 ? undefined as T : response.json();
  }
  async function all<T>(path: string): Promise<T[]> {
    const items: T[] = [];
    for (let offset = 0; ; offset += 100) {
      const page = await request<Page<T>>(`${path}${path.includes("?") ? "&" : "?"}offset=${offset}&limit=100`);
      items.push(...page.items);
      if (!page.items.length || items.length >= page.total) return items;
    }
  }
  return {
    reviseTemplate: (template: string, instruction: string) => request<{draft: string}>("/report-templates/revise", "POST", {template, instruction, input_clean_confirmed: true}),
    renderTemplate: (template: string, logs: string) => request<{report: string}>("/report-templates/render", "POST", {template, logs, input_clean_confirmed: true}),
    livePair: (prompt_messages: PromptMessage[], answer: string, source_group: string) => request<{offered: false} | {offered: true; assignment: Assignment}>("/live-pairs", "POST", {prompt_messages, answer, source_group}),
    me: () => request<Actor>("/me"),
    reviewers: () => all<Actor>("/reviewers"),
    campaigns: () => all<Campaign>("/campaigns"),
    createCampaign: (title: string) => request<Campaign>("/campaigns", "POST", {title}),
    campaignState: (id: string, state: "open" | "closed") => request<Campaign>(`/campaigns/${encodeURIComponent(id)}`, "PATCH", {state}),
    importFile: (id: string, file: File) => request<{imported: number; skipped: number}>(`/campaigns/${encodeURIComponent(id)}/imports`, "POST", file, "application/x-ndjson"),
    items: (id: string, disagreement: boolean) => all<ReviewItem>(`/campaigns/${encodeURIComponent(id)}/items?disagreement=${disagreement}`),
    assign: (id: string, reviewer_ids: string[]) => request(`/items/${encodeURIComponent(id)}/assignments`, "POST", {reviewer_ids}),
    removeAssignment: (id: string) => request<void>(`/assignments/${encodeURIComponent(id)}`, "DELETE"),
    queue: (status: string, campaignId: string) => all<QueueItem>(`/assignments?status=${status}&campaign_id=${encodeURIComponent(campaignId)}`),
    assignment: (id: string) => request<Assignment>(`/assignments/${encodeURIComponent(id)}`),
    submit: (id: string, body: JudgmentInput) => request<Judgment>(`/assignments/${encodeURIComponent(id)}/judgment`, "PUT", body),
    judgments: (id: string) => request<ItemJudgments>(`/items/${encodeURIComponent(id)}/judgments`),
    adjudicate: (id: string, outcome: string, winning_candidate_id: string | null, reason: string) => request(`/items/${encodeURIComponent(id)}/adjudication`, "PUT", {outcome, winning_candidate_id, reason}),
    stats: (id: string) => request<ReviewStats>(`/campaigns/${encodeURIComponent(id)}/stats`),
    async export(id: string, format: "raw" | "preference") {
      const response = await fetch(`${BASE}/campaigns/${encodeURIComponent(id)}/export?format=${format}`, {headers: authHeaders});
      if (!response.ok) { const data = await response.json().catch(() => ({})); throw new ReviewApiError(response.status, typeof data.detail === "string" ? data.detail : response.statusText); }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = `review-${id}-${format}.zip`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  };
}
export type ReviewApi = ReturnType<typeof createReviewApi>;
