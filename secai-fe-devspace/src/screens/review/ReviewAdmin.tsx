import { useEffect, useState } from "react";
import Markdown from "../../components/Markdown";
import { reviewError } from "../../reviewApi";
import type { ReviewApi } from "../../reviewApi";
import { DECISIONS } from "../../reviewTypes";
import type { Actor, Campaign, ItemJudgments, ReviewItem, ReviewStats } from "../../reviewTypes";
export default function ReviewAdmin({api, campaign, onChange}: {api: ReviewApi; campaign: Campaign; onChange: () => void}) {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [reviewers, setReviewers] = useState<Actor[]>([]);
  const [stats, setStats] = useState<ReviewStats | null>(null);
  const [disagreement, setDisagreement] = useState(false);
  const [selected, setSelected] = useState<ReviewItem | null>(null);
  const [judgments, setJudgments] = useState<ItemJudgments | null>(null);
  const [assignees, setAssignees] = useState<string[]>([]);
  const [outcome, setOutcome] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);
  const closed = campaign.state === "closed";
  async function load() {
    setLoading(true); setError("");
    try { const [rows, people, summary] = await Promise.all([api.items(campaign.id, disagreement), api.reviewers(), api.stats(campaign.id)]); setItems(rows); setReviewers(people); setStats(summary); }
    catch (e) { setError(reviewError(e)); } finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, [api, campaign.id, disagreement]);
  async function inspect(item: ReviewItem) {
    setSelected(item); setJudgments(null); setAssignees([]); setOutcome(""); setReason(""); setError(""); setBusy(true);
    try { setJudgments(await api.judgments(item.id)); } catch (e) { setError(reviewError(e)); } finally {setBusy(false);}
  }
  async function action(work: () => Promise<unknown>, message: string, refresh = true) {
    setBusy(true); setError(""); setNotice("");
    try { await work(); if (message) setNotice(message); if (refresh) {await load(); if (selected) {setJudgments(await api.judgments(selected.id));} onChange();} }
    catch (e) {setError(reviewError(e));} finally {setBusy(false);}
  }
  const current = selected ? items.find(i => i.id === selected.id) || selected : null;
  const adjudication = judgments?.adjudication || current?.adjudication;
  const canAdjudicate = current && current.assignment_count > 0 && current.assignment_count === current.submitted_count && !adjudication && !closed;
  return <section>
    <div className="review-toolbar"><h3>Quản trị · {campaign.title}</h3><button className="iasbtn" disabled={busy} onClick={() => void action(() => api.campaignState(campaign.id, closed ? "open" : "closed"), closed ? "Đã mở lại đợt đánh giá." : "Đã đóng đợt đánh giá.")}>{closed ? "Mở lại đợt" : "Đóng đợt"}</button><button className="iasbtn" disabled={busy || loading} onClick={() => void load()}>Làm mới</button></div>
    {error && <div className="review-error" role="alert">{error} <button className="iasbtn" onClick={() => void load()} disabled={busy || loading}>Thử tải lại</button></div>}
    {notice && <p className="review-note" role="status">{notice}</p>}
    {loading && <p role="status">Đang tải mẫu và thống kê…</p>}
    {stats && <div className="review-stats"><span>Mẫu: <strong>{stats.items}</strong></span><span>Đã nộp: <strong>{stats.submitted}/{stats.assignments}</strong></span><span>Chờ duyệt: <strong>{stats.pending_adjudication}</strong></span><span>Đã duyệt: <strong>{stats.adjudicated}</strong></span><span>Bất đồng: <strong>{stats.disagreements}/{stats.multi_reviewed_items}</strong> mẫu có ≥ 2 phiếu</span></div>}
    <form className="iascard review-card review-toolbar" onSubmit={e => {e.preventDefault(); if (file) void action(async () => {const result = await api.importFile(campaign.id, file); setFile(null); setFileKey(k => k + 1); return setNotice(`Đã nhập ${result.imported}, bỏ qua ${result.skipped} mẫu trùng.`);}, "");}}>
      <label className="review-field">Nhập cặp câu trả lời (JSONL, tối đa 10 MiB / 500 mẫu)<input key={fileKey} type="file" accept=".jsonl,.ndjson,application/x-ndjson" disabled={busy || closed} onChange={e => setFile(e.target.files?.[0] || null)}/></label>
      <button className="iasbtn" disabled={busy || closed || !file || file.size > 10 * 1024 * 1024}>Nhập dữ liệu</button>{file && file.size > 10 * 1024 * 1024 && <span role="alert" className="review-error">File vượt quá 10 MiB.</span>}
    </form>
    <div className="review-toolbar"><label className="review-check"><input type="checkbox" checked={disagreement} onChange={e => setDisagreement(e.target.checked)}/>Chỉ mẫu có bất đồng</label><button className="iasbtn" disabled={busy} onClick={() => void action(() => api.export(campaign.id, "raw"), "Đã tải ZIP dữ liệu raw và manifest.", false)}>Xuất raw</button><button className="iasbtn" disabled={busy} onClick={() => void action(() => api.export(campaign.id, "preference"), "Đã tải ZIP preference và manifest.", false)}>Xuất preference</button></div>
    <p className="review-muted">Preference chỉ gồm mẫu đã duyệt có người thắng rõ ràng. Raw giữ tất cả loại phản hồi.</p>
    <div className="review-admin-grid">
      <div className="review-list" aria-label="Danh sách mẫu">{!loading && items.length === 0 && <p>Chưa có mẫu phù hợp.</p>}{items.map(item => <button className={"review-list-item" + (selected?.id === item.id ? " selected" : "")} key={item.id} onClick={() => void inspect(item)} disabled={busy}><strong>{item.external_id}</strong><span>{item.submitted_count}/{item.assignment_count} phiếu · {item.adjudication ? "Đã duyệt" : "Chưa duyệt"}{item.disagreement ? " · Bất đồng" : ""}</span></button>)}</div>
      {current && <div className="iascard review-card">
        <h3>Mẫu {current.external_id}</h3>
        <details><summary>Xem ngữ cảnh và candidate</summary>{current.prompt_messages.map((message, i) => <div key={i}><strong>{message.role}</strong><Markdown>{message.content}</Markdown></div>)}{current.evidence && <Markdown>{current.evidence}</Markdown>}{current.candidates.map(candidate => <section key={candidate.id}><h4>Candidate {candidate.id}</h4><pre className="review-metadata">{JSON.stringify(candidate.provenance, null, 2)}</pre><Markdown>{candidate.text}</Markdown></section>)}</details>
        <fieldset disabled={busy || closed || !!adjudication}><legend>Phân công chuyên gia</legend>{reviewers.map(person => <label className="review-check" key={person.actor_id}><input type="checkbox" checked={assignees.includes(person.actor_id)} onChange={e => setAssignees(prev => e.target.checked ? [...prev, person.actor_id] : prev.filter(id => id !== person.actor_id))}/>{person.actor_id}</label>)}{reviewers.length === 0 && <p>Chưa có chuyên gia được cấu hình.</p>}<button className="iasbtn" disabled={!assignees.length} onClick={() => void action(() => api.assign(current.id, assignees), "Đã phân công mẫu.")}>Phân công</button></fieldset>
        {current.assignments?.length ? <section><h4>Phân công hiện tại</h4>{current.assignments.map(assignment => <div className="review-toolbar" key={assignment.id}><span>{assignment.reviewer_id} · {assignment.status === "submitted" ? "Đã nộp" : "Chưa nộp"}</span>{assignment.status !== "submitted" && !adjudication && <button className="iasbtn" disabled={busy || closed} onClick={() => void action(() => api.removeAssignment(assignment.id), "Đã bỏ phân công chưa nộp.")}>Bỏ phân công</button>}</div>)}</section> : null}
        <h4>Phiếu chuyên gia (quy về candidate ID)</h4>
        {!judgments && <p>{busy ? "Đang tải phiếu…" : "Không tải được phiếu."}</p>}
        {judgments?.judgments.length === 0 && <p>Chưa có phiếu.</p>}
        {judgments?.judgments.map(judgment => <article className="review-vote" key={judgment.id}><strong>{judgment.reviewer_id}</strong><p>{judgment.winning_candidate_id ? `Chọn candidate: ${judgment.winning_candidate_id}` : DECISIONS.find(([value]) => value === judgment.decision)?.[1]}</p><pre className="review-metadata">{JSON.stringify(judgment.candidate_error_tags, null, 2)}</pre><p>{judgment.comment || "Không có nhận xét."}</p></article>)}
        {adjudication ? <div className="review-note"><strong>Đã duyệt: {adjudication.winning_candidate_id ? `candidate ${adjudication.winning_candidate_id}` : adjudication.outcome}</strong><p>{adjudication.reason}</p></div> : <form onSubmit={e => {e.preventDefault(); void action(() => api.adjudicate(current.id, outcome.startsWith("winner:") ? "winner" : outcome, outcome.startsWith("winner:") ? outcome.slice(7) : null, reason), "Đã duyệt kết quả. Quyết định được giữ bất biến.");}}>
          <fieldset disabled={busy || !canAdjudicate}><legend>Quyết định cuối của quản trị</legend><label className="review-field">Kết quả<select className="iasin" value={outcome} required onChange={e => setOutcome(e.target.value)}><option value="">Chọn kết quả</option>{current.candidates.map(candidate => <option key={candidate.id} value={"winner:" + candidate.id}>Candidate {candidate.id} thắng</option>)}<option value="tie">Ngang nhau</option><option value="both_bad">Cả hai không đạt</option><option value="insufficient_context">Thiếu ngữ cảnh</option></select></label><label className="review-field">Lý do (bắt buộc)<textarea className="iasin" rows={3} required maxLength={4000} value={reason} onChange={e => setReason(e.target.value)}/></label><button className="iasbtn review-primary" disabled={!outcome || !reason.trim()}>Duyệt kết quả</button></fieldset>
          {!canAdjudicate && <p className="review-muted">Cần ít nhất một phân công và toàn bộ chuyên gia nộp phiếu trước khi duyệt. Đợt phải đang mở.</p>}
        </form>}
      </div>}
    </div>
  </section>;
}
