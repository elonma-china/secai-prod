import { useEffect, useRef, useState } from "react";
import {
  cancelTranslationJob, getTranslationJob, getTranslationResult, submitTranslationFile, TranslateV2Error,
} from "../api/translateV2";
import type { V2Job, V2Kind, V2Lang, V2Result, V2Source } from "../api/translateV2";
import { sha256File } from "./fileFingerprint";
import "../styles/translateFileBatch.css";

const STORAGE = "secai.translate.v2.file.batch.v1";
const MAX_FILES = 5;
// Keep older sessions readable so lowering the upload limit does not lose their job IDs.
const MAX_RESTORED_FILES = 20;
const MAX_BYTES = 10 * 1024 * 1024;
const UPLOAD_INTERVAL_MS = 12_500;
const POLL_INTERVAL_MS = 4_000;
const LANGS: Array<{ code: V2Lang; label: string }> = [
  { code: "vi", label: "Tiếng Việt" }, { code: "en", label: "English" }, { code: "zh", label: "中文" },
  { code: "ja", label: "日本語" }, { code: "ko", label: "한국어" }, { code: "fr", label: "Français" },
  { code: "de", label: "Deutsch" }, { code: "es", label: "Español" }, { code: "ru", label: "Русский" },
  { code: "ar", label: "العربية" },
];
const HEX = /^[0-9a-f]{32}$/;
type Phase = "waiting" | "posting" | "retry" | "uncertain" | "needs_file" | "active" | "succeeded" | "failed" | "cancelled" | "unavailable";
type Row = {
  id: string; name: string; size: number; extension: string; hash: string; key: string;
  kind: V2Kind; source: V2Source; target: V2Lang; phase: Phase;
  jobId?: string; job?: V2Job; result?: V2Result; error?: string;
  nextPollAt?: number; retryAt?: number; retries?: number; lookupError?: boolean; cancelRequested?: boolean;
  unknownAcceptance?: boolean;
};

function randomHex(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function supported(file: File, imagesEnabled: boolean): V2Kind | null {
  const ext = file.name.toLowerCase().match(/\.[^.]+$/)?.[0] ?? "";
  if ([".txt", ".docx", ".doc"].includes(ext)) return "file";
  if (imagesEnabled && [".png", ".jpg", ".jpeg"].includes(ext)) return "image";
  return null;
}

function load(): { rows: Row[]; nextUploadAt: number } {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(STORAGE) ?? "null");
    if (!value || typeof value !== "object" || !("rows" in value) || !Array.isArray(value.rows)) return { rows: [], nextUploadAt: 0 };
    const stored = value as { rows: unknown[]; nextUploadAt?: unknown };
    if (stored.rows.length > MAX_RESTORED_FILES) return { rows: [], nextUploadAt: 0 };
    const rows: Row[] = [];
    for (const raw of stored.rows) {
      if (!raw || typeof raw !== "object") continue;
      const row = raw as Row;
      if (!HEX.test(row.id) || !HEX.test(row.key) || !/^[0-9a-f]{64}$/.test(row.hash) ||
          typeof row.name !== "string" || row.name.length > 255 || typeof row.size !== "number" ||
          (row.jobId && !HEX.test(row.jobId)) || !LANGS.some((item) => item.code === row.target)) continue;
      const phase: Phase = row.phase === "posting" || row.phase === "uncertain" ? "uncertain"
        : row.phase === "waiting" || row.phase === "retry" ? "needs_file" : row.phase;
      rows.push({ ...row, phase, result: undefined });
    }
    return { rows, nextUploadAt: typeof stored.nextUploadAt === "number" ? stored.nextUploadAt : 0 };
  } catch {
    return { rows: [], nextUploadAt: 0 };
  }
}

function errorLabel(error: unknown): string {
  if (error instanceof TranslateV2Error) return error.code ?? `HTTP ${error.status}`;
  return "Không kết nối được dịch vụ.";
}

function terminal(phase: string): boolean {
  return phase === "succeeded" || phase === "failed" || phase === "cancelled" || phase === "unavailable";
}

function definitiveMissing(error: unknown): error is TranslateV2Error {
  return error instanceof TranslateV2Error &&
    ((error.status === 410 && error.code === "JOB_EXPIRED") ||
      (error.status === 404 && error.code === "JOB_NOT_FOUND"));
}

function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function TranslateFileBatchPanel({ imagesEnabled }: { imagesEnabled: boolean }) {
  const initial = useRef<ReturnType<typeof load> | null>(null);
  if (!initial.current) initial.current = load();
  const [rows, setRows] = useState<Row[]>(initial.current.rows);
  const rowsRef = useRef(rows);
  const filesRef = useRef(new Map<string, File>());
  const [source, setSource] = useState<V2Lang>("vi");
  const [target, setTarget] = useState<V2Lang>("en");
  const [notice, setNotice] = useState("");
  const [hashing, setHashing] = useState(false);
  const busyRef = useRef(false);
  const mountedRef = useRef(true);
  const nextUploadAt = useRef(initial.current.nextUploadAt);

  function save(next: Row[]): boolean {
    if (!mountedRef.current) return false;
    const persisted = next.map(({ result: _result, ...row }) => row);
    try {
      sessionStorage.setItem(STORAGE, JSON.stringify({ rows: persisted, nextUploadAt: nextUploadAt.current }));
    } catch {
      setNotice("Không lưu được trạng thái phiên. Hãy giải phóng bộ nhớ trình duyệt trước khi gửi.");
      return false;
    }
    rowsRef.current = next;
    setRows(next);
    return true;
  }

  function change(id: string, update: Partial<Row>) {
    save(rowsRef.current.map((row) => row.id === id ? { ...row, ...update } : row));
  }

  function markUnavailable(id: string, error: TranslateV2Error) {
    change(id, { phase: "unavailable", job: undefined, result: undefined,
      cancelRequested: false, lookupError: false, error: error.code ?? "JOB_EXPIRED" });
  }

  async function pump() {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      const now = Date.now();
      const current = rowsRef.current;
      const cancelling = current.find((row) => row.phase === "active" && row.jobId && row.cancelRequested && (row.retryAt ?? 0) <= now);
      if (cancelling?.jobId) {
        try {
          const job = await cancelTranslationJob(cancelling.jobId);
          change(cancelling.id, { job, phase: job.status === "cancelled" ? "cancelled" : "active",
            cancelRequested: false, lookupError: false, nextPollAt: Date.now() + POLL_INTERVAL_MS, retryAt: 0 });
        } catch (error) {
          if (definitiveMissing(error)) {
            markUnavailable(cancelling.id, error);
            return;
          }
          if (error instanceof TranslateV2Error && error.status === 409) {
            change(cancelling.id, { cancelRequested: false, lookupError: true,
              nextPollAt: Date.now(), error: errorLabel(error) });
            return;
          }
          change(cancelling.id, { lookupError: true, error: errorLabel(error),
            retryAt: Date.now() + Math.max(POLL_INTERVAL_MS, (error instanceof TranslateV2Error ? error.retryAfter ?? 0 : 0) * 1000) });
        }
        return;
      }
      const checking = current.find((row) => row.jobId &&
        (row.phase === "active" || (row.phase === "succeeded" && !row.result)) &&
        (row.nextPollAt ?? 0) <= now);
      if (checking?.jobId) {
        try {
          if (checking.phase === "succeeded") {
            const result = await getTranslationResult(checking.jobId);
            if (result.job_id !== checking.jobId || result.kind !== checking.kind ||
                result.source_lang !== checking.source || result.target_lang !== checking.target ||
                result.status !== "succeeded") throw new TranslateV2Error(0, "INVALID_RESPONSE", null);
            change(checking.id, { result, lookupError: false, error: undefined });
          } else {
            const job = await getTranslationJob(checking.jobId);
            if (job.job_id !== checking.jobId || job.kind !== checking.kind ||
                job.source_lang !== checking.source || job.target_lang !== checking.target) throw new TranslateV2Error(0, "INVALID_RESPONSE", null);
            const phase: Phase = job.status === "succeeded" ? "succeeded"
              : job.status === "failed" ? "failed" : job.status === "cancelled" ? "cancelled" : "active";
            change(checking.id, { job, phase, lookupError: false, error: job.error?.code,
              nextPollAt: Date.now() + (phase === "succeeded" ? 0 : POLL_INTERVAL_MS) });
          }
        } catch (error) {
          if (definitiveMissing(error)) {
            markUnavailable(checking.id, error);
            return;
          }
          change(checking.id, { lookupError: true, error: errorLabel(error),
            nextPollAt: Date.now() + Math.max(POLL_INTERVAL_MS, (error instanceof TranslateV2Error ? error.retryAfter ?? 0 : 0) * 1000) });
        }
        return;
      }
      // A disconnected POST may already have committed. Preserve its slot and key until the
      // user supplies the same bytes again; lookup failures also halt new admissions.
      if (current.some((row) => row.phase === "uncertain" || row.lookupError)) return;
      const unresolved = current.filter((row) => ["posting", "retry", "uncertain", "active"].includes(row.phase)).length;
      if (unresolved >= 2 || now < nextUploadAt.current) return;
      const uncertainReplay = current.find((row) => row.unknownAcceptance && !terminal(row.phase));
      if (uncertainReplay && uncertainReplay.phase !== "retry") return;
      const next = uncertainReplay ?? current.find((row) => (row.phase === "waiting" || row.phase === "retry") &&
        (row.retryAt ?? 0) <= now && filesRef.current.has(row.id));
      if (!next || (next.retryAt ?? 0) > now || !filesRef.current.has(next.id)) return;
      const file = filesRef.current.get(next.id)!;
      nextUploadAt.current = now + UPLOAD_INTERVAL_MS;
      if (!save(current.map((row) => row.id === next.id ? { ...row, phase: "posting", error: undefined } : row))) return;
      try {
        const job = await submitTranslationFile(file, next.source, next.target, next.key);
        if (!HEX.test(job.job_id) || job.kind !== next.kind || job.source_lang !== next.source ||
            job.target_lang !== next.target) throw new TranslateV2Error(0, "INVALID_RESPONSE", null);
        filesRef.current.delete(next.id);
        change(next.id, { phase: terminal(job.status) ? job.status as Phase : "active",
          jobId: job.job_id, job, unknownAcceptance: false,
          nextPollAt: Date.now() + (job.status === "succeeded" ? 0 : POLL_INTERVAL_MS) });
      } catch (error) {
        if (error instanceof TranslateV2Error && error.status === 429 && (next.retries ?? 0) < 3) {
          const retryAt = Date.now() + Math.max(UPLOAD_INTERVAL_MS, (error.retryAfter ?? 0) * 1000);
          nextUploadAt.current = Math.max(nextUploadAt.current, retryAt);
          change(next.id, { phase: "retry", retries: (next.retries ?? 0) + 1, retryAt, error: errorLabel(error) });
        } else if (next.unknownAcceptance || !(error instanceof TranslateV2Error) || error.status === 0 || error.status >= 500) {
          change(next.id, { phase: "uncertain", unknownAcceptance: true, error: errorLabel(error) });
        } else {
          filesRef.current.delete(next.id);
          change(next.id, { phase: "failed", error: errorLabel(error) });
        }
      }
    } finally {
      busyRef.current = false;
    }
  }

  useEffect(() => {
    mountedRef.current = true;
    const timer = setInterval(() => { void pump(); }, 1000);
    void pump();
    return () => { mountedRef.current = false; clearInterval(timer); };
  // The interval reads rowsRef, so it is intentionally bound to this component instance.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function addFiles(input: FileList | File[] | null) {
    if (!input?.length || hashing) return;
    const selected = Array.from(input);
    if (rowsRef.current.length + selected.length > MAX_FILES) { setNotice(`Tối đa ${MAX_FILES} tệp mỗi phiên.`); return; }
    setHashing(true);
    try {
      for (const file of selected) {
        const kind = supported(file, imagesEnabled);
        if (!kind || file.size === 0 || file.size > MAX_BYTES) {
          setNotice(`${file.name}: chỉ nhận TXT/DOCX/DOC${imagesEnabled ? "/PNG/JPG" : ""}, mỗi tệp tối đa 10 MiB và không rỗng.`);
          continue;
        }
        const hash = await sha256File(file);
        const row: Row = { id: randomHex(), name: file.name, size: file.size,
          extension: file.name.toLowerCase().match(/\.[^.]+$/)?.[0] ?? "", hash, key: randomHex(),
          kind, source, target, phase: "waiting" };
        if (save([...rowsRef.current, row])) filesRef.current.set(row.id, file);
      }
      void pump();
    } catch {
      setNotice("Không đọc được một tệp đã chọn.");
    } finally {
      setHashing(false);
    }
  }

  async function reselect(id: string, file: File | undefined) {
    if (!file) return;
    const row = rowsRef.current.find((item) => item.id === id);
    if (!row || (row.phase !== "uncertain" && row.phase !== "needs_file")) return;
    try {
      const hash = await sha256File(file);
      if (hash !== row.hash || file.size !== row.size || file.name.toLowerCase().match(/\.[^.]+$/)?.[0] !== row.extension) {
        change(id, { error: "Tệp đã thay đổi; hãy chọn đúng tệp gốc." });
        return;
      }
      filesRef.current.set(id, file);
      change(id, { phase: row.unknownAcceptance || row.phase === "uncertain" ? "retry" : "waiting", error: undefined,
        retryAt: Math.max(row.retryAt ?? 0, Date.now(), nextUploadAt.current) });
      void pump();
    } catch {
      change(id, { error: "Không đọc được tệp đã chọn." });
    }
  }

  function cancel(row: Row) {
    if (row.unknownAcceptance) return;
    if (row.phase === "waiting" || row.phase === "needs_file" || row.phase === "retry") {
      filesRef.current.delete(row.id);
      change(row.id, { phase: "cancelled" });
    } else if (row.phase === "active" && row.jobId) {
      change(row.id, { cancelRequested: true });
      void pump();
    }
  }

  const targets = source === "vi" ? LANGS.filter((item) => item.code !== "vi")
    : LANGS.filter((item) => item.code === "vi");
  const accept = imagesEnabled ? ".txt,.docx,.doc,.png,.jpg,.jpeg" : ".txt,.docx,.doc";
  return (
    <section className="file-batch">
      <p>Dịch tối đa {MAX_FILES} tệp mỗi phiên. Máy chủ xử lý tối đa 2 job đang chờ hoặc chạy; kết quả tệp được giữ trên máy chủ trong 24 giờ.</p>
      <div className="file-batch-controls">
        <label>Ngôn ngữ nguồn của tệp
          <select value={source} onChange={(event) => {
            const value = event.target.value as V2Lang;
            setSource(value);
            setTarget(value === "vi" ? "en" : "vi");
          }}>
            {LANGS.map((lang) => <option key={lang.code} value={lang.code}>{lang.label}</option>)}
          </select>
        </label>
        <label>Ngôn ngữ đích của tệp
          <select value={target} onChange={(event) => setTarget(event.target.value as V2Lang)}>
            {targets.map((lang) => <option key={lang.code} value={lang.code}>{lang.label}</option>)}
          </select>
        </label>
        <label>Chọn nhiều tệp cần dịch
          <input type="file" multiple accept={accept} disabled={hashing || rows.length >= MAX_FILES}
            onChange={(event) => { void addFiles(event.target.files); event.target.value = ""; }} />
        </label>
      </div>
      {notice && <p role="alert">{notice}</p>}
      {rows.some((row) => row.phase === "needs_file") && <p>Trang đã tải lại: chọn lại các tệp chưa gửi. Job đã nhận sẽ tiếp tục được kiểm tra.</p>}
      <ol className="file-batch-list">
        {rows.map((row) => (
          <li className="file-batch-row" key={row.id}>
            <div className="file-batch-row-head">
              <strong>{row.name}</strong>
              <span>{row.source} → {row.target} · {row.kind === "image" ? "Ảnh OCR" : "Tài liệu"}</span>
            </div>
            <p role="status">{row.phase === "uncertain" || (row.phase === "needs_file" && row.unknownAcceptance) ? "Chưa rõ máy chủ đã nhận; chọn lại đúng tệp để kiểm tra bằng khóa cũ." :
              row.phase === "needs_file" ? "Chưa gửi; cần chọn lại tệp." :
              row.job ? `${row.job.status} · ${row.job.stage} · ${row.job.progress.completed}/${row.job.progress.total ?? "?"} đoạn` : row.phase}</p>
            {row.error && <p role="alert">{row.error}</p>}
            {(row.phase === "uncertain" || row.phase === "needs_file") &&
              <label>Chọn lại tệp {row.name}<input type="file" accept={row.extension} onChange={(event) => {
                void reselect(row.id, event.target.files?.[0]); event.target.value = "";
              }} /></label>}
            {!terminal(row.phase) && row.phase !== "uncertain" && row.phase !== "posting" && !row.unknownAcceptance &&
              <button type="button" className="iasbtn ghost" onClick={() => cancel(row)} disabled={!!row.cancelRequested}
                aria-label={`Hủy ${row.name}`}>Hủy</button>}
            {row.job?.warnings.map((warning, index) => <p key={index}>Cảnh báo: {warning.code} — {warning.message}</p>)}
            {row.result && <>
              <pre className="file-batch-result" dir={row.target === "ar" ? "rtl" : "auto"}>{row.result.translated_text}</pre>
              {row.result.warnings.map((warning, index) => <p key={index}>Cảnh báo: {warning.code} — {warning.message}</p>)}
              {row.result.ocr && <details><summary>Văn bản OCR {row.result.ocr.needs_review ? "· cần kiểm tra" : ""}</summary>
                <h4>Bản gốc</h4><pre>{row.result.ocr.raw_text}</pre>
                <h4>Đã sửa</h4><pre>{row.result.ocr.corrected_text}</pre>
              </details>}
              <div className="file-batch-downloads">
                <button type="button" className="iasbtn ghost" onClick={() => download(`${row.name}.translated.txt`, row.result!.translated_text, "text/plain;charset=utf-8")}>Tải TXT</button>
                <button type="button" className="iasbtn ghost" onClick={() => download(`${row.name}.translated.json`, JSON.stringify(row.result, null, 2), "application/json")}>Tải JSON</button>
              </div>
            </>}
          </li>
        ))}
      </ol>
    </section>
  );
}
