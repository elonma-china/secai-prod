import { useEffect, useRef, useState } from "react";
import Markdown from "../components/Markdown";
import {
  reportMvpApi,
  ReportApiError,
  type GeneratedReport,
  type ReportRevision,
  type ReportTemplate,
  type ReportTemplateInput,
  type ReportExportFormat,
} from "../reportMvpApi";
import "./reportMvp.css";

const EMPTY_DRAFT: ReportTemplateInput = {
  name: "",
  description: "",
  sections: [],
  instructions: "",
  example: "",
};

function describeError(error: unknown): string {
  if (error instanceof ReportApiError) {
    if (error.status === 401) return "Kết nối kho báo cáo của workspace chưa được cấu hình đúng trên máy chủ.";
    if (error.status === 404) return `Không tìm thấy tài nguyên hoặc tính năng báo cáo chưa bật. ${error.message}`;
    if (error.status === 409) return "Dữ liệu đã được cập nhật ở nơi khác. Tải lại mẫu hoặc báo cáo rồi thử lại.";
    if (error.status === 502) return "Dịch vụ tạo báo cáo đang gián đoạn. Vui lòng thử lại sau.";
    if (error.status === 503) return "Kết nối kho báo cáo của workspace chưa được cấu hình trên máy chủ.";
    return `Lỗi ${error.status}: ${error.message}`;
  }
  return error instanceof Error ? error.message : "Không thể kết nối máy chủ.";
}

function timeLabel(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("vi-VN");
}

export default function ReportMvpPanel() {
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [draft, setDraft] = useState<ReportTemplateInput>(EMPTY_DRAFT);
  const [sectionsText, setSectionsText] = useState("");
  const [templateEditorOpen, setTemplateEditorOpen] = useState(false);
  const [editing, setEditing] = useState<ReportTemplate | null>(null);
  const [sourceData, setSourceData] = useState("");
  const [report, setReport] = useState<GeneratedReport | null>(null);
  const [reportIdInput, setReportIdInput] = useState("");
  const [instruction, setInstruction] = useState("");
  const [revisions, setRevisions] = useState<ReportRevision[]>([]);
  const [previewRevision, setPreviewRevision] = useState<number | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const busyRef = useRef(false);

  const selectedTemplate = templates.find((item) => item.id === selectedTemplateId);
  const preview = previewRevision === null
    ? report?.content
    : revisions.find((item) => item.revision === previewRevision)?.content;

  async function run(action: string, work: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(action);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      busyRef.current = false;
      setBusy("");
    }
  }

  useEffect(() => {
    void run("load", async () => reloadTemplates());
  }, []);

  async function reloadTemplates(preferredId?: string) {
    const next = await reportMvpApi.listTemplates("");
    setTemplates(next);
    setSelectedTemplateId((current) => {
      const id = preferredId || current;
      return next.some((item) => item.id === id) ? id : (next[0]?.id || "");
    });
  }

  function setDraftFromTemplate(value: ReportTemplateInput) {
    setTemplateEditorOpen(true);
    setDraft({
      name: value.name || "",
      description: value.description || "",
      sections: value.sections || [],
      instructions: value.instructions || "",
      example: value.example || "",
    });
    setSectionsText((value.sections || []).join("\n"));
  }

  function resetDraft() {
    setEditing(null);
    setDraftFromTemplate(EMPTY_DRAFT);
    setNotice("");
  }

  async function importFile(file?: File) {
    if (!file) return;
    if (!/\.(docx|md|txt)$/i.test(file.name)) {
      setError("Chỉ nhận tệp .docx, .md hoặc .txt.");
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      setError("Tệp vượt quá 2 MiB.");
      return;
    }
    await run("import", async () => {
      const imported = await reportMvpApi.importTemplate("", file);
      setEditing(null);
      setDraftFromTemplate(imported);
      setNotice("Đã trích xuất bản nháp. Kiểm tra và chỉnh nội dung trước khi lưu mẫu.");
    });
  }

  async function saveTemplate() {
    const fields = { ...draft, name: draft.name.trim(), sections: sectionsText.split(/\r?\n/).map((part) => part.trim()).filter(Boolean) };
    if (!fields.name || fields.sections.length === 0) {
      setError("Nhập tên mẫu và ít nhất một mục báo cáo.");
      return;
    }
    await run("save", async () => {
      const saved = editing
        ? await reportMvpApi.updateTemplate("", editing.id, fields, editing.version)
        : await reportMvpApi.createTemplate("", fields);
      await reloadTemplates(saved.id);
      setEditing(saved);
      setDraftFromTemplate(saved);
      setTemplateEditorOpen(false);
      setNotice(`Đã lưu mẫu “${saved.name}” phiên bản ${saved.version}.`);
    });
  }

  async function showReport(value: GeneratedReport) {
    setReport(value);
    setReportIdInput(value.id);
    setPreviewRevision(null);
    setRevisions(await reportMvpApi.listRevisions("", value.id));
  }

  async function generate() {
    if (!selectedTemplateId || !sourceData.trim()) {
      setError("Chọn mẫu và nhập dữ liệu nguồn.");
      return;
    }
    await run("generate", async () => {
      const value = await reportMvpApi.generate("", selectedTemplateId, sourceData.trim());
      await showReport(value);
      setNotice("Báo cáo đã được tạo và lưu.");
    });
  }

  async function openReport() {
    if (!reportIdInput.trim()) return;
    await run("open", async () => {
      await showReport(await reportMvpApi.getReport("", reportIdInput.trim()));
    });
  }

  async function editReport() {
    if (!report || !instruction.trim()) {
      setError("Mở báo cáo và nhập yêu cầu chỉnh sửa.");
      return;
    }
    await run("edit", async () => {
      const value = await reportMvpApi.editReport("", report.id, instruction.trim(), report.revision);
      await showReport(value);
      setInstruction("");
      setNotice(`Đã lưu bản sửa thứ ${value.revision}.`);
    });
  }

  async function exportReport(format: ReportExportFormat) {
    if (!report) return;
    const revision = previewRevision ?? report.revision;
    await run(`export-${format}`, async () => {
      const { blob, filename } = await reportMvpApi.exportReport("", report.id, format, revision);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      try { link.click(); } finally {
        link.remove();
        // Give the browser time to start consuming the attachment before releasing it.
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      setNotice(`Đã tải báo cáo bản ${revision} (${format.toUpperCase()}).`);
    });
  }

  return (
    <div className="report-mvp">
      <div className="iascard report-mvp-card report-mvp-connection">
        <h4>Kho báo cáo</h4>
        <div className="report-mvp-row">
          <button className="iasbtn ghost" type="button" disabled={!!busy}
            onClick={() => run("load", async () => reloadTemplates())}>
            {busy === "load" ? "Đang tải…" : "Tải lại danh sách mẫu"}
          </button>
        </div>
      </div>

      {error && <div className="error-box" role="alert">{error}</div>}
      {notice && <div className="report-mvp-notice" role="status">{notice}</div>}

      <div className="report-mvp-workspace">
      <div className="report-mvp-controls">
      <div className="iascard report-mvp-card">
        <h4>Mẫu báo cáo</h4>
        <div className="report-mvp-row">
          <label className="report-mvp-field report-mvp-grow">
            <span>Mẫu đã lưu trong DB</span>
            <select className="iasin" value={selectedTemplateId} disabled={!!busy || !templates.length}
              onChange={(event) => setSelectedTemplateId(event.target.value)}>
              {!templates.length && <option value="">Chưa có mẫu</option>}
              {templates.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}
            </select>
          </label>
          <button className="iasbtn ghost" type="button" disabled={!!busy || !selectedTemplate}
            onClick={() => { if (selectedTemplate) { setEditing(selectedTemplate); setDraftFromTemplate(selectedTemplate); setNotice("Đang chỉnh mẫu đã lưu."); } }}>
            Chỉnh mẫu này
          </button>
          <button className="iasbtn ghost" type="button" disabled={!!busy} onClick={resetDraft}>Mẫu mới</button>
        </div>
        <details className="report-template-editor" open={templateEditorOpen}
          onToggle={(event) => setTemplateEditorOpen(event.currentTarget.open)}>
          <summary>{editing ? "Chỉnh nội dung mẫu" : "Tạo mẫu mới / nhập từ tệp"}</summary>
        <div className="report-mvp-form">
          <label className="report-mvp-field"><span>Tên mẫu *</span><input className="iasin" value={draft.name} maxLength={160} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
          <label className="report-mvp-field"><span>Mô tả</span><input className="iasin" value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
          <label className="report-mvp-field"><span>Các mục báo cáo — mỗi dòng một mục *</span><textarea className="iasin" value={sectionsText} rows={5} onChange={(event) => setSectionsText(event.target.value)} /></label>
          <label className="report-mvp-field"><span>Chỉ dẫn văn phong/nội dung</span><textarea className="iasin" value={draft.instructions} rows={4} onChange={(event) => setDraft({ ...draft, instructions: event.target.value })} /></label>
          <label className="report-mvp-field"><span>Ví dụ tham khảo</span><textarea className="iasin" value={draft.example} rows={3} onChange={(event) => setDraft({ ...draft, example: event.target.value })} /></label>
        </div>
        <div className="report-mvp-row">
          <label className="report-mvp-upload">
            Nhập Word / Markdown / TXT
            <input type="file" accept=".docx,.md,.txt" disabled={!!busy}
              onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ""; void importFile(file); }} />
          </label>
          <button className="iasbtn" type="button" disabled={!!busy} onClick={saveTemplate}>
            {busy === "save" ? "Đang lưu…" : editing ? `Lưu phiên bản mới (v${editing.version + 1})` : "Lưu mẫu mới"}
          </button>
        </div>
        <p className="report-mvp-muted">File nhập chỉ điền bản nháp; hãy soát lại trước khi lưu. Chỉnh mẫu đã lưu sẽ tạo phiên bản mới.</p>
        </details>
      </div>

      <div className="iascard report-mvp-card">
        <h4>Sinh báo cáo theo mẫu</h4>
        <p className="report-mvp-muted">Mẫu đang chọn: {selectedTemplate ? `${selectedTemplate.name} (v${selectedTemplate.version})` : "Chưa chọn mẫu"}</p>
        <label className="report-mvp-field"><span>Dữ liệu nguồn *</span>
          <textarea className="iasin" rows={8} value={sourceData} onChange={(event) => setSourceData(event.target.value)}
            placeholder="Dán dữ liệu, log hoặc kết quả quét dùng làm căn cứ cho báo cáo…" />
        </label>
        <button className="iasbtn" type="button" disabled={!selectedTemplateId || !sourceData.trim() || !!busy} onClick={generate}>
          {busy === "generate" ? "Đang tạo báo cáo…" : "Tạo báo cáo"}
        </button>
      </div>

      </div>
      <div className="iascard report-mvp-card report-mvp-document">
        <h4>Bản báo cáo</h4>
        <div className="report-mvp-row">
          <label className="report-mvp-field report-mvp-grow"><span>ID báo cáo đã lưu</span>
            <input className="iasin" value={reportIdInput} onChange={(event) => setReportIdInput(event.target.value)} placeholder="Dán ID để mở lại báo cáo" />
          </label>
          <button className="iasbtn ghost" type="button" disabled={!reportIdInput.trim() || !!busy} onClick={openReport}>
            {busy === "open" ? "Đang mở…" : "Mở báo cáo"}
          </button>
        </div>
        {report && <>
          <p className="report-mvp-muted">ID: <code>{report.id}</code> · mẫu v{report.template_version} · bản sửa {report.revision} · {timeLabel(report.created_at)}</p>
          <div className="report-mvp-revisions" aria-label="Lịch sử bản sửa">
            <button className={previewRevision === null ? "on" : ""} type="button" onClick={() => setPreviewRevision(null)}>Bản hiện tại · v{report.revision}</button>
            {revisions.filter((item) => item.revision !== report.revision).map((item) =>
              <button className={previewRevision === item.revision ? "on" : ""} key={item.revision} type="button"
                onClick={() => setPreviewRevision(item.revision)}>Bản {item.revision} · {timeLabel(item.created_at)}</button>)}
          </div>
          {previewRevision !== null && <p className="report-mvp-muted">Đang xem bản cũ. Yêu cầu chỉnh sửa luôn áp dụng trên bản hiện tại.</p>}
          <div className="report-mvp-row" aria-label="Xuất báo cáo">
            {([['txt', 'Tải TXT'], ['docx', 'Tải Word'], ['pdf', 'Tải PDF']] as const).map(([format, label]) =>
              <button className="iasbtn ghost" type="button" key={format} disabled={!!busy}
                onClick={() => void exportReport(format)}>
                {busy === `export-${format}` ? "Đang xuất…" : label}
              </button>)}
          </div>
          <div className="report-mvp-preview"><Markdown>{preview || "Không có dữ liệu."}</Markdown></div>
          <label className="report-mvp-field"><span>Yêu cầu chỉnh sửa bằng prompt</span>
            <textarea className="iasin" rows={3} value={instruction} onChange={(event) => setInstruction(event.target.value)}
              placeholder="Ví dụ: rút ngắn phần tổng quan, giữ nguyên mọi CVE/IP/số liệu từ dữ liệu nguồn…" />
          </label>
          <button className="iasbtn" type="button" disabled={!instruction.trim() || !!busy} onClick={editReport}>
            {busy === "edit" ? "Đang chỉnh…" : "Tạo bản sửa mới"}
          </button>
        </>}
        {!report && <div className="report-mvp-empty"><h4>Không gian báo cáo của bạn</h4><p>Chọn mẫu và nhập dữ liệu ở bên trái để tạo báo cáo, hoặc mở một báo cáo đã lưu bằng ID.</p><span>Nội dung và lịch sử chỉnh sửa được lưu tự động.</span></div>}
      </div>
      </div>
    </div>
  );
}
