import { useEffect, useState } from "react";
import type { Tab } from "./types";
import { health } from "./api";
import ChatPanel from "./components/ChatPanel";
import PocPanel from "./components/PocPanel";
import TranslatePanel from "./components/TranslatePanel";

const NAV: { id: Tab; ic: string; label: string; sub: string }[] = [
  { id: "chat", ic: "💬", label: "Chatbot", sub: "/ai/chat" },
  { id: "poc", ic: "🛡️", label: "PoC / Khai thác", sub: "/ai/exploit" },
  { id: "translate", ic: "🌐", label: "Dịch", sub: "/ai/translate" },
];

type Health = { state: "ok" | "degraded" | "down"; detail?: Record<string, unknown> };

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [streaming, setStreaming] = useState(true);
  const [userId, setUserId] = useState(() => localStorage.getItem("secai_uid") || "devspace");
  const [hp, setHp] = useState<Health>({ state: "down" });

  useEffect(() => localStorage.setItem("secai_uid", userId), [userId]);

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const h = await health();
        if (alive) setHp({ state: (h.status as string) === "ok" ? "ok" : "degraded", detail: h });
      } catch {
        if (alive) setHp({ state: "down" });
      }
    };
    check();
    const t = setInterval(check, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const dotClass = hp.state === "ok" ? "ok" : hp.state === "degraded" ? "deg" : "bad";
  const statusText = hp.state === "ok" ? "Gateway online" : hp.state === "degraded" ? "Degraded" : "Gateway offline";

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">S</div>
          <div>
            <h1>SecAI</h1>
            <small>DevSpace</small>
          </div>
        </div>
        {NAV.map((n) => (
          <div key={n.id} className={"nav-item" + (tab === n.id ? " active" : "")} onClick={() => setTab(n.id)}>
            <span className="ic">{n.ic}</span>
            <div>
              <div>{n.label}</div>
              <small style={{ color: "var(--muted)", fontFamily: "var(--mono)", fontSize: 10 }}>{n.sub}</small>
            </div>
          </div>
        ))}
        <div className="spacer" />
        <div className="side-foot">
          Agent Gateway · IAS v1<br />
          chat · exploit · translate
        </div>
      </aside>

      <div className="main">
        <div className="topbar">
          <div className="title">{NAV.find((n) => n.id === tab)?.label}</div>
          <div className="grow" />
          <label className="toggle" title="Bật/tắt streaming (SSE)">
            <input type="checkbox" checked={streaming} onChange={(e) => setStreaming(e.target.checked)} />
            Streaming
          </label>
          {(tab === "chat" || tab === "poc") && (
            <div className="field">
              user_id
              <input value={userId} onChange={(e) => setUserId(e.target.value)} style={{ width: 120 }} />
            </div>
          )}
          <div className="status" title={JSON.stringify(hp.detail || {}, null, 2)}>
            <span className={"dot " + dotClass} />
            {statusText}
          </div>
        </div>

        <div className="content">
          {tab === "chat" && <ChatPanel streaming={streaming} userId={userId} />}
          {tab === "poc" && <PocPanel streaming={streaming} userId={userId} />}
          {tab === "translate" && <TranslatePanel streaming={streaming} />}
        </div>
      </div>
    </div>
  );
}
