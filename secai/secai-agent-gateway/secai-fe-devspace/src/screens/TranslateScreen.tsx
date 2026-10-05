import { useEffect, useState } from "react";
import { getMultilangCapabilities, MultilangApiError } from "../api/multilang";
import type { MultilangCapabilities } from "../api/multilang";
import MultilangTranslatePanel from "./MultilangTranslatePanel";
import TranslateFileBatchPanel from "./TranslateFileBatchPanel";

export default function TranslateScreen(_props: { userId: string }) {
  const fileBatchEnabled = import.meta.env.VITE_TRANSLATE_V2_FILES_ENABLED === "true";
  const imagesEnabled = fileBatchEnabled && import.meta.env.VITE_TRANSLATE_V2_IMAGES_ENABLED === "true";
  const [capabilities, setCapabilities] = useState<MultilangCapabilities | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "off" | "error">("loading");
  const [reload, setReload] = useState(0);
  const [mode, setMode] = useState<"text" | "files">("text");

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    getMultilangCapabilities(controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setCapabilities(value);
      setState("ready");
    }).catch((failure: unknown) => {
      if (controller.signal.aborted) return;
      setCapabilities(null);
      setState(failure instanceof MultilangApiError && failure.status === 404 ? "off" : "error");
    });
    return () => controller.abort();
  }, [reload]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={{ padding: "16px 26px", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
        <h3 style={{ fontSize: 21 }}>Dịch thuật bảo mật</h3>
        <div className="multilang-mode-switch" role="group" aria-label="Chế độ dịch">
          <button className={"iasbtn ghost" + (mode === "text" ? " active" : "")} type="button" aria-pressed={mode === "text"} onClick={() => setMode("text")}>Dịch trực tiếp</button>
          {fileBatchEnabled && <button className={"iasbtn ghost" + (mode === "files" ? " active" : "")} type="button" aria-pressed={mode === "files"} onClick={() => setMode("files")}>Dịch tệp và ảnh</button>}
        </div>
      </div>
      <div style={{ flex: 1, overflow: "auto" }}>
        {mode === "files" && fileBatchEnabled ? <TranslateFileBatchPanel imagesEnabled={imagesEnabled} />
          : state === "ready" && capabilities ? <MultilangTranslatePanel capabilities={capabilities} />
          : <div className="multilang-mode-note" role="status" style={{ padding: "24px 26px" }}>
            {state === "loading" ? "Đang tải ngôn ngữ dịch…" : state === "off" ? "Dịch trực tiếp chưa được bật trên máy chủ." : "Chưa kết nối được dịch vụ dịch."}
            {state !== "loading" && <button type="button" className="iasbtn ghost" onClick={() => setReload((value) => value + 1)}>Thử lại</button>}
          </div>}
      </div>
    </div>
  );
}
