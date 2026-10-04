import { useEffect, useMemo, useState } from "react";
import Markdown from "../../components/Markdown";
import { createReviewApi, reviewError } from "../../reviewApi";
import { getReviewIdentity, useReviewIdentity } from "../../reviewIdentity";
const DEFAULT_TEMPLATE = "# Báo cáo an ninh mạng\n\n## Tóm tắt\n{{summary}}\n\n## Phát hiện\n{{findings}}\n\n## Khuyến nghị\n{{recommendations}}\n";
function downloadMarkdown(text: string, filename: string) {
  const url = URL.createObjectURL(new Blob([text], {type: "text/markdown;charset=utf-8"}));
  const link = document.createElement("a"); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export default function ReportTemplatePanel({logs, onLogsChange}: {logs: string; onLogsChange: (value: string) => void}) {
  const identity = useReviewIdentity();
  const api = useMemo(() => createReviewApi(identity?.token || ""), [identity?.token]);
  const [template, setTemplate] = useState(DEFAULT_TEMPLATE);
  const [instruction, setInstruction] = useState("");
  const [draft, setDraft] = useState("");
  const [draftBase, setDraftBase] = useState("");
  const [report, setReport] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState<"revise" | "render" | "import" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {setConfirmed(false);}, [logs]);
  function editTemplate(text: string) {setTemplate(text); setDraft(""); setConfirmed(false); setNotice("");}
  async function importTemplate(file: File | undefined) {
    if (!file) return;
    setError(""); setBusy("import");
    try {if (file.size > 65536) throw new Error("Template vượt quá 64 KiB."); editTemplate(await file.text()); setNotice("Đã nhập template Markdown. Hãy kiểm tra nội dung trước khi sử dụng.");}
    catch(e) {setError(e instanceof Error ? e.message : "Không đọc được file.");} finally {setBusy(null);}
  }
  async function revise() {
    if (!identity || !confirmed || busy) return;
    const auth = identity.token;
    setBusy("revise"); setError(""); setNotice("");
    try {const result = await api.reviseTemplate(template, instruction); if (getReviewIdentity()?.token === auth) {setDraft(result.draft); setDraftBase(template); setNotice("Bản nháp đã sẵn sàng. Template hiện tại chỉ thay đổi khi bạn bấm Áp dụng bản nháp.");}}
    catch(e) {setError(reviewError(e));} finally {setBusy(null);}
  }
  async function renderReport() {
    if (!identity || !confirmed || busy) return;
    const auth = identity.token;
    setBusy("render"); setError(""); setNotice("");
    try {const result = await api.renderTemplate(template, logs); if (getReviewIdentity()?.token === auth) {setReport(result.report); setNotice("Đã tạo báo cáo theo template. Kiểm tra kết quả trước khi tải hoặc chia sẻ.");}}
    catch(e) {setError(reviewError(e));} finally {setBusy(null);}
  }
  return <section className="review-screen" style={{height: "auto"}}>
    <p className="review-note">Template và bản nháp lưu trong bộ nhớ của màn hình. Có thể tải file .md để giữ lại. Công cụ không cung cấp bộ phát hiện prompt injection riêng; người dùng chịu trách nhiệm kiểm tra đầu vào. Checkbox xác nhận không chứng minh nội dung an toàn.</p>
    {!identity && <p role="status">Mở mục Đánh giá A/B để kết nối Devspace trước khi sửa template bằng prompt.</p>}
    {error && <p className="review-error" role="alert">{error} Template đang dùng và kết quả trước đó được giữ nguyên.</p>}
    {notice && <p className="review-note" role="status">{notice}</p>}
    <section className="iascard review-card">
      <h4>Template đang dùng</h4>
      <label className="review-field">Nội dung Markdown<textarea className="iasin mono" rows={12} value={template} disabled={!!busy} onChange={e => editTemplate(e.target.value)}/></label>
      <div className="review-toolbar"><label className="review-field">Nhập template .md<input type="file" accept=".md,.markdown,text/markdown,text/plain" disabled={!!busy} onChange={e => {void importTemplate(e.target.files?.[0]); e.target.value = "";}}/></label><button className="iasbtn" disabled={!!busy || !template.trim()} onClick={() => downloadMarkdown(template, "report-template.md")}>Xuất template .md</button></div>
      <details><summary>Xem trước template đang dùng</summary><Markdown>{template}</Markdown></details>
    </section>
    <label className="review-field">Yêu cầu sửa template<textarea className="iasin" rows={3} value={instruction} maxLength={8192} disabled={!!busy} placeholder="Ví dụ: Thêm mục đánh giá mức độ ưu tiên, giữ các placeholder hiện có." onChange={e => {setInstruction(e.target.value); setDraft(""); setConfirmed(false);}}/></label>
    <label className="review-field">Dữ liệu / logs cho báo cáo<textarea className="iasin mono" rows={6} value={logs} disabled={!!busy} onChange={e => {onLogsChange(e.target.value); setConfirmed(false);}}/></label>
    <label className="review-check"><input type="checkbox" checked={confirmed} disabled={!!busy} onChange={e => setConfirmed(e.target.checked)}/>Tôi xác nhận đầu vào đã được kiểm tra, không chứa prompt injection</label>
    <div className="review-toolbar"><button className="iasbtn" disabled={!identity || !!busy || !confirmed || !template.trim() || !instruction.trim()} onClick={() => void revise()}>{busy === "revise" ? "Đang đề xuất…" : "Đề xuất bản nháp bằng prompt"}</button><button className="iasbtn review-primary" disabled={!identity || !!busy || !confirmed || !template.trim() || !logs.trim()} onClick={() => void renderReport()}>{busy === "render" ? "Đang tạo…" : "Tạo báo cáo theo template"}</button></div>
    {draft && <section className="iascard review-card"><h4>Bản nháp đề xuất · chưa áp dụng</h4><Markdown>{draft}</Markdown><details><summary>Xem Markdown gốc của bản nháp</summary><pre className="review-metadata">{draft}</pre></details><div className="review-toolbar"><button className="iasbtn review-primary" disabled={!!busy || draftBase !== template} onClick={() => {editTemplate(draft); setNotice("Đã áp dụng bản nháp làm template hiện tại. Kiểm tra lại đầu vào trước khi tạo báo cáo.");}}>Áp dụng bản nháp</button><button className="iasbtn" disabled={!!busy} onClick={() => setDraft("")}>Bỏ bản nháp</button></div></section>}
    {report && <section className="iascard review-card"><h4>Báo cáo theo template</h4><Markdown>{report}</Markdown><button className="iasbtn" onClick={() => downloadMarkdown(report, "report.md")}>Tải báo cáo .md</button></section>}
  </section>;
}
