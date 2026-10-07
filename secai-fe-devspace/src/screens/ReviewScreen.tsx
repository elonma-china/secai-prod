import ReviewOverview from './review/ReviewOverview';
import { AbAlert, AbLoading } from './review/AbParts';
import { useEffect, useMemo, useState } from "react";
import { createReviewApi, reviewError } from "../reviewApi";
import type { Assignment, Campaign, JudgmentInput, QueueItem } from "../reviewTypes";
import { useReviewIdentity } from "../reviewIdentity";
import ReviewPair from "./review/ReviewPair";
import ReviewSamplingSettings from "./review/ReviewSamplingSettings";
import ReviewAdmin from "./review/ReviewAdmin";
export default function ReviewScreen({connecting = false, connectionError = "", onReconnect = () => {}}: {connecting?: boolean; connectionError?: string; onReconnect?: () => void} = {}) {
  const identity = useReviewIdentity();
  const token = identity?.token || "";
  const actor = identity?.actor || null;
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [campaignId, setCampaignId] = useState("");
  const [title, setTitle] = useState("");
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [status, setStatus] = useState("pending");
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [notice, setNotice] = useState("");
  const api = useMemo(() => createReviewApi(token), [token]);
  const campaign = campaigns.find(c => c.id === campaignId);
  async function loadCampaigns() { const rows = await api.campaigns(); setCampaigns(rows); setCampaignId(prev => rows.some(c => c.id === prev) ? prev : rows.find(c => c.title === "Đánh giá từ chat")?.id || rows.find(c => c.title.startsWith("Demo 2 vai trò"))?.id || rows[0]?.id || ""); }
  useEffect(() => { if (!actor) return; let alive = true; setBusy(true); api.campaigns().then(rows => {if (alive) {setCampaigns(rows); setCampaignId(prev => rows.some(c => c.id === prev) ? prev : rows.find(c => c.title === "Đánh giá từ chat")?.id || rows.find(c => c.title.startsWith("Demo 2 vai trò"))?.id || rows[0]?.id || "");}}).catch(e => alive && setError(reviewError(e))).finally(() => alive && setBusy(false)); return () => {alive = false;}; }, [api, actor]);
  async function loadQueue() {setBusy(true); setError(""); try {setQueue(await api.queue(status, campaignId));} catch(e) {setError(reviewError(e));} finally {setBusy(false);}}
  useEffect(() => {
    if (!actor || actor.role !== "reviewer" || !campaignId) return;
    let alive = true; setQueue([]); setAssignment(null); setBusy(true); setError(""); setSubmitError("");
    api.queue(status, campaignId).then(rows => alive && setQueue(rows)).catch(e => alive && setError(reviewError(e))).finally(() => alive && setBusy(false));
    return () => {alive = false;};
  }, [api, actor, campaignId, status]);
  async function openAssignment(id: string) {setBusy(true); setError(""); setSubmitError(""); try {setAssignment(await api.assignment(id));} catch(e) {setError(reviewError(e));} finally {setBusy(false);}}
  async function submit(input: JudgmentInput) {
    if (!assignment) return; setBusy(true); setSubmitError(""); setNotice("");
    try {
      const judgment = await api.submit(assignment.id, input);
      setAssignment({...assignment, judgment, status: "submitted"}); setNotice("Đã nộp phiếu thành công.");
      try {
        const rows = await api.queue(status, campaignId); setQueue(rows);
        const next = rows.find(row => row.status === "pending" && row.id !== assignment.id);
        if (next) setAssignment(await api.assignment(next.id));
      } catch (e) {setError("Phiếu đã lưu. " + reviewError(e));}
    } catch(e) {setSubmitError(reviewError(e));} finally {setBusy(false);}
  }
  if (actor && ['admin'].includes(actor.role)) return <ReviewOverview key={actor.actor_id} api={api}/>;
  if (!actor) return <section className="ab-screen"><header className="ab-head"><h3>Đánh giá A/B</h3></header><div className="ab-body">{connectionError && !connecting ? <AbAlert onRetry={onReconnect}>{connectionError}</AbAlert> : <AbLoading>Đang kết nối công cụ đánh giá…</AbLoading>}</div></section>;
  return <div className="review-screen"><div className="review-toolbar review-header"><h2>Phản hồi A/B</h2></div>
    <div className="review-body">
      {!actor ? <div className="iascard review-card review-login" role="status"><h3>{connecting ? "Đang kết nối công cụ đánh giá…" : "Chưa kết nối được công cụ đánh giá"}</h3>{connectionError && <p className="review-error" role="alert">{connectionError}</p>}<button className="iasbtn" type="button" disabled={connecting} onClick={onReconnect}>Thử lại</button></div> : <>
        <p className="review-muted">Xem lựa chọn của user → xác nhận mẫu → quyết định fine-tune. Bản demo dùng tài khoản chung theo vai trò.</p>
        {error && <div className="review-error" role="alert">{error}<button className="iasbtn" disabled={busy} onClick={() => void (actor.role === "reviewer" && campaignId ? loadQueue() : loadCampaigns().catch(e => setError(reviewError(e))))}>Thử tải lại</button></div>}
        {notice && <p className="review-note" role="status">{notice}</p>}
        {actor.role === "admin" && <ReviewSamplingSettings api={api}/>}
        <details className="review-campaign-settings"><summary>Nâng cao · chọn hoặc tạo đợt đánh giá</summary><div className="review-toolbar"><label className="review-field">Đợt đánh giá<select className="iasin" value={campaignId} disabled={busy} onChange={e => {setCampaignId(e.target.value); setNotice("");}}><option value="">Chọn đợt đánh giá</option>{campaigns.map(c => <option key={c.id} value={c.id}>{c.title} · {c.state === "open" ? "Đang mở" : "Đã đóng"}</option>)}</select></label>{actor.role === "reviewer" && <label className="review-field">Trạng thái phiếu<select className="iasin" value={status} disabled={busy} onChange={e => setStatus(e.target.value)}><option value="pending">Chưa nộp</option><option value="submitted">Đã nộp</option></select></label>}</div>
        {actor.role === "admin" && <details><summary>Quản lý đợt đánh giá</summary><form className="review-toolbar" onSubmit={async e => {e.preventDefault(); setBusy(true); setError(""); try {const c = await api.createCampaign(title.trim()); setTitle(""); await loadCampaigns(); setCampaignId(c.id); setNotice("Đã tạo đợt đánh giá.");} catch(e) {setError(reviewError(e));} finally {setBusy(false);}}}><label className="review-field">Tên đợt mới<input className="iasin" required maxLength={200} value={title} disabled={busy} onChange={e => setTitle(e.target.value)}/></label><button className="iasbtn" disabled={busy || !title.trim()}>Tạo đợt</button></form></details>}
        </details>
        {busy && <p role="status">Đang xử lý…</p>}
        {!busy && !campaigns.length && <p>Chưa có đợt đánh giá{actor.role === "reviewer" ? " được phân công cho bạn" : ""}.</p>}
        {campaign && (actor.role === "admin" ? <ReviewAdmin key={campaign.id} api={api} campaign={campaign} onChange={updated => setCampaigns(rows => rows.map(row => row.id === updated.id ? updated : row))}/> : <div>
          <div className="review-toolbar"><h3>Hàng đợi · {queue.length} mẫu</h3><button className="iasbtn" disabled={busy} onClick={() => void loadQueue()}>Làm mới hàng đợi</button>{campaign.state === "closed" && <span className="review-note">Đợt đã đóng; chỉ được xem.</span>}</div>
          <div className="review-reviewer-grid"><div className="review-list" aria-label="Hàng đợi được phân công">{!busy && !queue.length && <p>Không có mẫu {status === "pending" ? "chưa nộp" : "đã nộp"}.</p>}{queue.map(row => <button key={row.id} className={"review-list-item" + (assignment?.id === row.id ? " selected" : "")} disabled={busy} onClick={() => void openAssignment(row.id)}>{row.external_id}<span>{row.status === "submitted" ? "Đã nộp" : "Chưa nộp"}</span></button>)}</div>{assignment ? <ReviewPair key={assignment.id} assignment={assignment} closed={campaign.state === "closed"} saving={busy} error={submitError} onSubmit={input => void submit(input)}/> : <p>Chọn một mẫu trong hàng đợi để bắt đầu đánh giá.</p>}</div>
        </div>)}
      </>}
    </div>
  </div>;
}
