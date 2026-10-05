export type Decision = "left" | "right" | "tie" | "both_bad" | "insufficient_context" | "skip";
export type ErrorTag = "incorrect_fact" | "unsupported_claim" | "incomplete" | "irrelevant" | "unsafe" | "other";
export interface Actor { actor_id: string; role: "admin" | "reviewer" }
export interface Page<T> { items: T[]; total: number; offset: number; limit: number }
export interface Campaign { id: string; title: string; state: "open" | "closed"; rubric_version: string }
export interface PromptMessage { role: string; content: string }
export interface JudgmentInput { decision: Decision; error_tags: { left: ErrorTag[]; right: ErrorTag[] }; comment: string }
export interface Judgment extends JudgmentInput { id: string; submitted_at: string }
export interface QueueItem { id: string; campaign_id: string; campaign_title: string; external_id: string; status: "pending" | "submitted" }
export interface Assignment extends QueueItem { prompt_messages: PromptMessage[]; evidence: string; left: {text: string}; right: {text: string}; rubric: string; rubric_version: string; judgment: Judgment | null }
export interface Adjudication { id: string; outcome: string; winning_candidate_id: string | null; reason: string }
export interface ReviewItem { id: string; external_id: string; prompt_messages: PromptMessage[]; candidates: {id: string; text: string; provenance: Record<string, unknown>}[]; evidence: string; assignments?: {id: string; reviewer_id: string; status: "pending" | "submitted"}[]; assignment_count: number; submitted_count: number; adjudication: Adjudication | null; disagreement: boolean }
export interface AdminJudgment { id: string; assignment_id: string; reviewer_id: string; decision: Decision; winning_candidate_id: string | null; candidate_error_tags: Record<string, ErrorTag[]>; comment: string }
export interface ItemJudgments { item_id: string; judgments: AdminJudgment[]; adjudication: Adjudication | null }
export interface ReviewStats { items: number; assignments: number; submitted: number; pending_adjudication: number; adjudicated: number; disagreements: number; multi_reviewed_items: number }
export const DECISIONS: [Decision, string][] = [["left", "A tốt hơn"], ["right", "B tốt hơn"], ["tie", "Hai câu trả lời ngang nhau"], ["both_bad", "Cả hai đều không đạt"], ["insufficient_context", "Thiếu ngữ cảnh để đánh giá"], ["skip", "Bỏ qua"]];
export const ERROR_TAGS: [ErrorTag, string][] = [["incorrect_fact", "Sai kỹ thuật / dữ kiện"], ["unsupported_claim", "Khẳng định thiếu bằng chứng"], ["incomplete", "Thiếu ý"], ["irrelevant", "Không phù hợp câu hỏi"], ["unsafe", "Không an toàn"], ["other", "Lỗi khác"]];
