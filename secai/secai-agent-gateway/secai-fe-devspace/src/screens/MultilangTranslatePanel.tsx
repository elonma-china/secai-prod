import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ArrowLeftRight, Copy, Languages, X } from "lucide-react";
import {
  MultilangApiError,
  streamMultilang,
} from "../api/multilang";
import type {
  MultilangCapabilities,
  MultilangLanguageCode,
  MultilangPair,
  MultilangTranslationResponse,
} from "../api/multilang";
import "../styles/multilang.css";

const TECHNICAL_SPAN = /https?:\/\/[^\s<>"'،]+|[A-Za-z0-9][A-Za-z0-9_./:@+#-]*/g;

function isolatedText(value: string): ReactNode[] {
  const pieces: ReactNode[] = [];
  let cursor = 0;
  for (const match of value.matchAll(TECHNICAL_SPAN)) {
    const start = match.index;
    if (start > cursor) pieces.push(value.slice(cursor, start));
    pieces.push(<bdi dir="ltr" key={start}>{match[0]}</bdi>);
    cursor = start + match[0].length;
  }
  if (cursor < value.length) pieces.push(value.slice(cursor));
  return pieces;
}

function errorText(error: unknown): string {
  if (!(error instanceof MultilangApiError)) return "Không kết nối được dịch vụ dịch. Kiểm tra kết nối rồi thử lại.";
  switch (error.code) {
    case "INVALID_REQUEST": return "Nội dung yêu cầu không hợp lệ.";
    case "UNSUPPORTED_LANGUAGE": return "Ngôn ngữ đã chọn không được hỗ trợ.";
    case "UNSUPPORTED_PAIR": return "Chỉ hỗ trợ dịch giữa tiếng Việt và một ngôn ngữ khác.";
    case "LANGUAGE_DISABLED": return "Ngôn ngữ này chưa được bật.";
    case "TEXT_TOO_LONG": return "Văn bản vượt giới hạn cho phép.";
    case "TOKEN_LIMIT_EXCEEDED": return "Văn bản vượt giới hạn xử lý. Hãy rút ngắn nội dung.";
    case "SOURCE_LANGUAGE_MISMATCH": return "Ngôn ngữ nguồn có thể không đúng với văn bản.";
    case "UNSAFE_UNICODE": return "Văn bản chứa ký tự điều khiển hướng chữ không an toàn.";
    case "RATE_LIMITED": return `Hệ thống đang giới hạn yêu cầu.${error.retryAfter ? ` Thử lại sau ${error.retryAfter} giây.` : " Vui lòng thử lại sau."}`;
    case "EMPTY_TRANSLATION":
    case "OUTPUT_VALIDATION_FAILED": return "Bản dịch chưa đạt kiểm tra an toàn. Vui lòng thử lại sau.";
    case "MODEL_UNAVAILABLE": return "Dịch vụ dịch tạm thời không khả dụng.";
    case "TRANSLATION_TIMEOUT": return "Yêu cầu dịch đã hết thời gian.";
    case "STREAM_INCOMPLETE": return "Luồng dịch bị gián đoạn; bản dịch chưa hoàn tất. Vui lòng thử lại.";
    default: return `Không thể dịch văn bản (HTTP ${error.status}).`;
  }
}

function warningText(warning: MultilangTranslationResponse["warnings"][number]): string {
  return warning === "SOURCE_LANGUAGE_UNCERTAIN"
    ? "Ngôn ngữ nguồn do người dùng chọn; hệ thống chưa xác minh tự động."
    : "Hệ thống chưa xác minh tự động ngôn ngữ của kết quả dịch.";
}

export default function MultilangTranslatePanel({ capabilities }: { capabilities: MultilangCapabilities }) {
  const languages = capabilities.languages;
  const firstForeign = (capabilities.pairs.some((pair) => pair.source_lang === "vi" && pair.target_lang === "en" && pair.enabled) ? "en" : undefined)
    ?? languages.find((language) => language.code !== "vi" && capabilities.pairs.some((pair) => pair.source_lang === "vi" && pair.target_lang === language.code && pair.enabled))?.code
    ?? languages.find((language) => language.code !== "vi")?.code ?? "zh-Hans";
  const [source, setSource] = useState<MultilangLanguageCode>("vi");
  const [target, setTarget] = useState<MultilangLanguageCode>(firstForeign);
  const [text, setText] = useState("");
  const [result, setResult] = useState("");
  const [resultTarget, setResultTarget] = useState<MultilangLanguageCode>(firstForeign);
  const [warnings, setWarnings] = useState<MultilangTranslationResponse["warnings"]>([]);
  const [metadata, setMetadata] = useState<MultilangTranslationResponse["metadata"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestVersion = useRef(0);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => {
    requestVersion.current += 1;
    controller.current?.abort();
  }, []);

  const pair: MultilangPair | undefined = capabilities.pairs.find((item) => item.source_lang === source && item.target_lang === target);
  const codePoints = Array.from(text).length;
  const tooLong = codePoints > capabilities.limits.max_text_chars;
  const canTranslate = !!pair?.enabled && !busy && !!text.trim() && !tooLong;

  function invalidate(message: string | null = null) {
    requestVersion.current += 1;
    controller.current?.abort();
    controller.current = null;
    setBusy(false);
    setResult("");
    setWarnings([]);
    setMetadata(null);
    setError(null);
    setNotice(message);
  }

  function chooseSource(next: MultilangLanguageCode) {
    invalidate();
    setSource(next);
    setTarget(next === "vi" ? (target === "vi" ? firstForeign : target) : "vi");
  }

  function chooseTarget(next: MultilangLanguageCode) {
    invalidate();
    setTarget(next);
  }

  function swap() {
    invalidate();
    setSource(target);
    setTarget(source);
    if (result) setText(result);
  }

  async function submit() {
    if (!canTranslate) return;
    invalidate();
    const version = requestVersion.current;
    const active = new AbortController();
    controller.current = active;
    setBusy(true);
    setResultTarget(target);
    try {
      const response = await streamMultilang({ text, source_lang: source, target_lang: target }, (delta) => {
        if (version === requestVersion.current && !active.signal.aborted) setResult((current) => current + delta);
      }, active.signal);
      if (version !== requestVersion.current) return;
      setResult(response.translated_text);
      setResultTarget(response.target_lang);
      setWarnings(response.warnings);
      setMetadata(response.metadata);
    } catch (failure) {
      if (version !== requestVersion.current || active.signal.aborted) return;
      setResult("");
      setError(errorText(failure));
    } finally {
      if (version === requestVersion.current) {
        controller.current = null;
        setBusy(false);
      }
    }
  }

  async function copyResult() {
    try {
      await navigator.clipboard.writeText(result);
      setNotice("Đã sao chép bản dịch.");
    } catch {
      setNotice("Không thể sao chép; hãy chọn và sao chép văn bản thủ công.");
    }
  }

  const targetOptions = source === "vi" ? languages.filter((language) => language.code !== "vi") : languages.filter((language) => language.code === "vi");
  const pairLabel = !pair?.enabled ? "Chưa bật" : pair.status === "beta" ? "Beta" : "Sẵn sàng";

  return (
    <form className="multilang-panel" onSubmit={(event) => { event.preventDefault(); void submit(); }} aria-busy={busy}>
      <p className="multilang-intro">Dịch văn bản giữa tiếng Việt và một ngôn ngữ khác. Chọn rõ ngôn ngữ nguồn và đích.</p>
      <div className="multilang-controls">
        <label>
          Ngôn ngữ nguồn
          <select value={source} onChange={(event) => chooseSource(event.target.value as MultilangLanguageCode)}>
            {languages.map((language) => <option value={language.code} key={language.code}>{language.name} · {language.native_name}</option>)}
          </select>
        </label>
        <button type="button" className="iasbtn ghost multilang-swap" disabled={busy} onClick={swap} aria-label="Hoán đổi hướng dịch" title="Hoán đổi ngôn ngữ nguồn và đích">
          <ArrowLeftRight size={17} aria-hidden="true" />
        </button>
        <label>
          Ngôn ngữ đích
          <select value={target} onChange={(event) => chooseTarget(event.target.value as MultilangLanguageCode)}>
            {targetOptions.map((language) => <option value={language.code} key={language.code}>{language.name} · {language.native_name}</option>)}
          </select>
        </label>
        <span className={`multilang-pair-status ${pair?.status ?? "disabled"}`} aria-label={`Trạng thái hướng dịch: ${pairLabel}`}>{pairLabel}</span>
      </div>
      <div className="multilang-grid">
        <section className="iascard multilang-card">
          <div className="iascard-head">Văn bản nguồn</div>
          <textarea
            className="iasin multilang-input"
            aria-label="Văn bản nguồn"
            dir={source === "ar" ? "rtl" : "ltr"}
            value={text}
            onChange={(event) => { invalidate(); setText(event.target.value); }}
            placeholder="Nhập văn bản cần dịch…"
          />
          <div className="multilang-card-foot">
            <span className={tooLong ? "multilang-limit" : ""}>{codePoints} / {capabilities.limits.max_text_chars} ký tự</span>
            {tooLong && <span role="alert">Văn bản vượt giới hạn cho phép.</span>}
          </div>
        </section>
        <section className="iascard multilang-card">
          <div className="iascard-head multilang-result-head">
            <span>Bản dịch</span>
            <button type="button" className="iasbtn ghost" onClick={() => void copyResult()} disabled={!result || busy} aria-label="Sao chép bản dịch">
              <Copy size={14} aria-hidden="true" /> Sao chép
            </button>
          </div>
          {busy && <p role="status">Đang dịch trực tiếp…</p>}
          <div className="multilang-result" role="status" aria-label="Kết quả dịch" aria-live="polite" dir={resultTarget === "ar" ? "rtl" : "ltr"}>
            {result ? isolatedText(result) : busy ? "Đang dịch…" : "Kết quả dịch sẽ hiển thị tại đây"}
          </div>
          {metadata && <>
            <p role="status">Dịch hoàn tất.</p>
            {warnings.length > 0 && <p className="multilang-warnings">Hệ thống chưa có bộ kiểm tra ngôn ngữ tự động đủ tin cậy; bạn có thể kiểm tra lại bản dịch.</p>}
            <details className="multilang-metadata">
              <summary>Chi tiết bản dịch</summary>
              {warnings.map((warning) => <p key={warning}>{warningText(warning)}</p>)}
              <p>Mã yêu cầu: {metadata.request_id} · {metadata.chunks} đoạn · {metadata.attempts} lượt tạo</p>
            </details>
          </>}
        </section>
      </div>
      <div className="multilang-actions">
        <button type="submit" className="iasbtn" disabled={!canTranslate}><Languages size={17} aria-hidden="true" /> {busy ? "Đang dịch…" : "Dịch trực tiếp"}</button>
        {busy && <button type="button" className="iasbtn ghost" onClick={() => invalidate("Đã hủy yêu cầu dịch.")} aria-label="Hủy dịch"><X size={16} aria-hidden="true" /> Hủy</button>}
      </div>
      {error && <div className="error-box" role="alert">{error}</div>}
      {notice && <div className="multilang-notice" role="status">{notice}</div>}
    </form>
  );
}
