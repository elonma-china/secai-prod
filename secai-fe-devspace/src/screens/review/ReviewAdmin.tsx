import { useEffect, useState } from "react";
import Markdown from "../../components/Markdown";
import RewardTraining from './RewardTraining';
import { reviewError } from "../../reviewApi";
import type { ReviewApi } from "../../reviewApi";
import { DECISIONS, ERROR_TAGS } from "../../reviewTypes";
import type { Actor, Campaign, ItemJudgments, ReviewItem, ReviewStats } from "../../reviewTypes";
export default function ReviewAdmin({api, campaign, onChange}: {api: ReviewApi; campaign: Campaign; onChange: (campaign: Campaign) => void}) {
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
  const [filter, setFilter] = useState("pending");
  const closed = campaign.state === "closed";
  async function load() {
    setLoading(true); setError("");
    try { const [rows, summary] = await Promise.all([api.items(campaign.id, false), api.stats(campaign.id)]); setItems(rows); setStats(summary); }
    catch (e) { setStats(null); setError(reviewError(e)); } finally { setLoading(false); }
  }
  useEffect(() => { let alive = true; api.reviewers().then(rows => {if (alive) setReviewers(rows);}).catch(e => {if (alive) setError(reviewError(e));}); return () => {alive = false;}; }, [api]);
  useEffect(() => { void load(); }, [api, campaign.id, disagreement]);
  async function inspect(item: ReviewItem) {
    setSelected(item); setJudgments(null); setAssignees([]); setOutcome(""); setReason(""); setError(""); setBusy(true);
    try { setJudgments(await api.judgments(item.id)); } catch (e) { setError(reviewError(e)); } finally {setBusy(false);}
  }
  async function action(work: () => Promise<unknown>, message: string, refresh = true) {
    setBusy(true); setError(""); setNotice("");
    try { await work(); if (message) setNotice(message); if (refresh) {await load(); if (selected) {setJudgments(await api.judgments(selected.id));}} }
    catch (e) {setError(reviewError(e));} finally {setBusy(false);}
  }
  const current = selected ? items.find(i => i.id === selected.id) || selected : null;
  const adjudication = judgments?.adjudication || current?.adjudication;
  const canAdjudicate = current && current.assignment_count > 0 && current.assignment_count === current.submitted_count && !adjudication && !closed;
  const question = (item: ReviewItem) => [...item.prompt_messages].reverse().find(m => m.role === "user")?.content || "Câu hỏi chưa có nội dung";
  const answerLabel = (id: string | null | undefined) => current?.candidates.findIndex(c => c.id === id) === 0 ? "Câu trả lời A" : current?.candidates.findIndex(c => c.id === id) === 1 ? "Câu trả lời B" : "Không chọn đáp án";
  const approvedPairs = items.filter(i => i.adjudication?.outcome === "winner" && i.adjudication.winning_candidate_id && i.candidates.some(c => c.id === i.adjudication?.winning_candidate_id)).length;
  const visibleItems = items.filter(i => (!disagreement || i.disagreement) && (filter === "all" || (filter === "pending" ? !i.adjudication && i.submitted_count > 0 : filter === "approved" ? i.adjudication?.outcome === "winner" : !!i.adjudication && i.adjudication.outcome !== "winner")));
  return <section>
    <div className="review-toolbar"><h3>Phản hồi · {campaign.title}</h3><button className="iasbtn" disabled={busy || loading} onClick={() => void load()}>Làm mới</button></div>
    <p className="review-muted">Chọn câu hỏi, xem phản hồi, rồi duyệt đáp án tốt hơn hoặc loại mẫu.</p>
    {error && <div className="review-error" role="alert">{error} <button className="iasbtn" onClick={() => void load()} disabled={busy || loading}>Thử tải lại</button></div>}
    {notice && <p className="review-note" role="status">{notice}</p>}
    {loading && <p role="status">Đang tải phản hồi…</p>}
    {stats && <div className="review-stats"><span>Câu hỏi: <strong>{stats.items}</strong></span><span>Chờ duyệt: <strong>{stats.pending_adjudication}</strong></span><strong>Cặp đã duyệt: {approvedPairs}</strong></div>}
    <RewardTraining key={campaign.id} api={api} campaignId={campaign.id} approvedPairs={approvedPairs}/>
    <div className="review-toolbar" role="group" aria-label="Lọc phản hồi">{[["pending","Chờ duyệt"],["approved","Đã duyệt"],["excluded","Đã loại"],["all","Tất cả"]].map(([value,label]) => <button className={"iasbtn" + (filter === value ? " review-primary" : "")} aria-pressed={filter === value} key={value} onClick={() => setFilter(value)}>{label}</button>)}</div>
    <div className="review-admin-grid">
      <div className="review-list" aria-label="Danh sách câu hỏi">{!loading && visibleItems.length === 0 && <p>Không có phản hồi trong nhóm này.</p>}{visibleItems.map(item => <button className={"review-list-item" + (current?.id === item.id ? " selected" : "")} key={item.id} onClick={() => void inspect(item)} disabled={busy}><strong>{question(item)}</strong><span>{item.adjudication ? item.adjudication.outcome === "winner" ? "Đã duyệt" : "Đã loại" : item.submitted_count ? "Chờ duyệt" : "Chưa có phản hồi"}</span></button>)}</div>
      {current ? <div className="iascard review-card">
        <h3>Câu hỏi</h3><Markdown>{question(current)}</Markdown>
        <div className="review-columns">{current.candidates.map((candidate,index) => <section className="review-answer" key={candidate.id}><h4>Câu trả lời {index === 0 ? "A" : "B"}</h4><Markdown>{candidate.text}</Markdown></section>)}</div>
        <h4>Phản hồi đã lưu</h4>
        {!judgments && <p>{busy ? "Đang tải phản hồi…" : "Không tải được phản hồi."}</p>}
        {judgments?.judgments.length === 0 && <p>Chưa có phản hồi.</p>}
        {judgments?.judgments.map(judgment => <article className="review-vote" key={judgment.id}><strong>{judgment.winning_candidate_id ? `Người đánh giá chọn: ${answerLabel(judgment.winning_candidate_id)}` : DECISIONS.find(([value]) => value === judgment.decision)?.[1]}</strong><p>{judgment.comment || "Không có nhận xét."}</p>{Object.entries(judgment.candidate_error_tags).map(([id,tags]) => tags.length > 0 && <p key={id}>{answerLabel(id)}: {tags.map(tag => ERROR_TAGS.find(([value]) => value === tag)?.[1] || tag).join(", ")}</p>)}</article>)}
        {adjudication ? <div className="review-note"><strong>{adjudication.outcome === "winner" ? `Đã duyệt: ${answerLabel(adjudication.winning_candidate_id)}` : "Đã loại khỏi dữ liệu huấn luyện"}</strong><p>{adjudication.reason}</p></div> : <form onSubmit={e => {e.preventDefault(); void action(() => api.adjudicate(current.id, "winner", outcome.slice(7), reason), "Đã duyệt cặp câu trả lời.");}}>
          <fieldset disabled={busy || !canAdjudicate}><legend>Duyệt phản hồi</legend><div className="review-decisions">{current.candidates.map((candidate,index) => <label className="review-check" key={candidate.id}><input type="radio" name="approved-answer" checked={outcome === "winner:" + candidate.id} onChange={() => setOutcome("winner:" + candidate.id)}/>Dùng câu trả lời {index === 0 ? "A" : "B"}</label>)}</div><label className="review-field">Lý do duyệt hoặc loại<textarea className="iasin" rows={2} required maxLength={4000} value={reason} onChange={e => setReason(e.target.value)}/></label><div className="review-toolbar"><button className="iasbtn review-primary" disabled={!outcome.startsWith("winner:") || !reason.trim()}>Duyệt dùng huấn luyện</button><button className="iasbtn" type="button" disabled={!reason.trim()} onClick={() => void action(() => api.adjudicate(current.id, "skip", null, reason), "Đã loại mẫu khỏi dữ liệu huấn luyện.")}>Loại mẫu</button></div></fieldset>
          {!canAdjudicate && <p className="review-muted">{closed ? "Đợt này đã đóng. Mở lại trong công cụ nâng cao để duyệt." : "Cần chờ toàn bộ người được phân công gửi phản hồi trước khi duyệt."}</p>}
        </form>}
        <details><summary>Ngữ cảnh và phân công</summary>{current.prompt_messages.slice(0,-1).map((message,i) => <Markdown key={i}>{message.content}</Markdown>)}{current.evidence && <Markdown>{current.evidence}</Markdown>}
        <fieldset disabled={busy || closed || !!adjudication}><legend>Phân công chuyên gia</legend>{reviewers.map(person => <label className="review-check" key={person.actor_id}><input type="checkbox" checked={assignees.includes(person.actor_id)} onChange={e => setAssignees(prev => e.target.checked ? [...prev, person.actor_id] : prev.filter(id => id !== person.actor_id))}/>{person.actor_id}</label>)}{reviewers.length === 0 && <p>Chưa có chuyên gia được cấu hình.</p>}<button className="iasbtn" disabled={!assignees.length} onClick={() => void action(() => api.assign(current.id, assignees), "Đã phân công mẫu.")}>Phân công</button></fieldset>
        {current.assignments?.length ? <section><h4>Phân công hiện tại</h4>{current.assignments.map(assignment => <div className="review-toolbar" key={assignment.id}><span>{assignment.reviewer_id} · {assignment.status === "submitted" ? "Đã nộp" : "Chưa nộp"}</span>{assignment.status !== "submitted" && !adjudication && <button className="iasbtn" disabled={busy || closed} onClick={() => void action(() => api.removeAssignment(assignment.id), "Đã bỏ phân công chưa nộp.")}>Bỏ phân công</button>}</div>)}</section> : null}
        </details>
      </div> : <div className="iascard review-card"><h3>Xem và duyệt phản hồi</h3><p>Chọn một câu hỏi trong danh sách để xem hai đáp án và lựa chọn đã lưu.</p></div>}
    </div>
    <details className="review-advanced"><summary>Công cụ nâng cao</summary><div className="review-toolbar"><button className="iasbtn" disabled={busy} onClick={() => void action(async () => {const updated = await api.campaignState(campaign.id, closed ? "open" : "closed"); onChange(updated);}, closed ? "Đã mở lại đợt đánh giá." : "Đã đóng đợt đánh giá.")}>{closed ? "Mở lại đợt" : "Đóng đợt"}</button></div>
    <form className="iascard review-card review-toolbar" onSubmit={e => {e.preventDefault(); if (file) void action(async () => {const result = await api.importFile(campaign.id, file); setFile(null); setFileKey(k => k + 1); return setNotice(`Đã nhập ${result.imported}, bỏ qua ${result.skipped} mẫu trùng.`);}, "");}}>
      <label className="review-field">Nhập cặp câu trả lời (JSONL, tối đa 10 MiB / 500 mẫu)<input key={fileKey} type="file" accept=".jsonl,.ndjson,application/x-ndjson" disabled={busy || closed} onChange={e => setFile(e.target.files?.[0] || null)}/></label>
      <button className="iasbtn" disabled={busy || closed || !file || file.size > 10 * 1024 * 1024}>Nhập dữ liệu</button>{file && file.size > 10 * 1024 * 1024 && <span role="alert" className="review-error">File vượt quá 10 MiB.</span>}
    </form>
    <div className="review-toolbar"><label className="review-check"><input type="checkbox" checked={disagreement} onChange={e => setDisagreement(e.target.checked)}/>Chỉ mẫu có bất đồng</label><button className="iasbtn" disabled={busy} onClick={() => void action(() => api.export(campaign.id, "raw"), "Đã tải ZIP dữ liệu raw và manifest.", false)}>Xuất raw</button><button className="iasbtn" disabled={busy} onClick={() => void action(() => api.export(campaign.id, "preference"), "Đã tải ZIP preference và manifest.", false)}>Xuất preference</button></div>
    <p className="review-muted">Preference chỉ gồm mẫu đã duyệt có người thắng rõ ràng. Raw giữ tất cả loại phản hồi.</p>
    </details>
  </section>;
}
