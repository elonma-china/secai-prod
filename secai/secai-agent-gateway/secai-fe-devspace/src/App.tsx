import { useEffect, useState } from "react";
import {
  Bug,
  CalendarClock,
  ChevronLeft,
  Cpu,
  Crosshair,
  Database,
  Eye,
  FileBarChart,
  LayoutDashboard,
  ScanSearch,
  Search,
  Settings2,
  Sparkles,
  User,
  VenetianMask,
} from "lucide-react";
import { health as fetchHealth } from "./api";
import type { AiScreenId, HealthStatus } from "./types";
import ChatbotScreen from "./screens/ChatbotScreen";
import TranslateScreen from "./screens/TranslateScreen";
import ExploitScreen from "./screens/ExploitScreen";
import ReportScreen from "./screens/ReportScreen";
import ModelsScreen from "./screens/ModelsScreen";
import ReviewScreen from "./screens/ReviewScreen";

/** The functional "Nền tảng AI" screens — the only routable/functional entries in the sidebar. */
const AI_ITEMS: { id: AiScreenId; label: string }[] = [
  { id: "translate", label: "Dịch văn bản" },
  { id: "chat", label: "Trợ lý ảo ATTT" },
  { id: "exploit", label: "Sinh mã khai thác" },
  { id: "report", label: "Báo cáo thông minh" },
  { id: "review", label: "Đánh giá A/B" },
  { id: "models", label: "Quản lý huấn luyện, triển khai" },
];

/** Other IAS product-nav sections shown in the design but out of scope here — visually
 * present, inert ("phân hệ khác"): no click handler, default cursor. */
type LucideIconType = typeof LayoutDashboard;
const INERT_TOP: { icon: LucideIconType; label: string }[] = [
  { icon: LayoutDashboard, label: "Dashboard" },
  { icon: VenetianMask, label: "Hệ thống ẩn danh" },
  { icon: ScanSearch, label: "Quét diện rộng" },
  { icon: Crosshair, label: "Quét chuyên sâu" },
  { icon: Bug, label: "Kiểm thử lỗ hổng" },
  { icon: Cpu, label: "Engine quét" },
];
const INERT_BOTTOM: { icon: LucideIconType; label: string }[] = [
  { icon: Database, label: "Data lake" },
  { icon: Settings2, label: "Thiết lập nguồn thu thập" },
  { icon: CalendarClock, label: "Quản lý lịch" },
  { icon: FileBarChart, label: "Báo cáo" },
];

export default function App() {
  const [screen, setScreen] = useState<AiScreenId>("chat");
  const [userId, setUserId] = useState(() => localStorage.getItem("user_id") || "devspace");
  const [health, setHealth] = useState<HealthStatus | null>(null);

  useEffect(() => localStorage.setItem("user_id", userId), [userId]);

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const h = await fetchHealth();
        if (alive) setHealth(h);
      } catch {
        if (alive) setHealth(null);
      }
    };
    check();
    const t = setInterval(check, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const online = health?.status === "ok";

  return (
    <div className="ias">
      {/* ═══ HEADER ═══ */}
      <header className="ias-header">
        <div className="ias-brand">
          <div className="mark">
            <Eye size={17} strokeWidth={1.5} />
          </div>
          <span className="name">INTELLIGENT AUTOSCAN</span>
        </div>

        <div className="ias-search">
          <input className="iasin" placeholder="Tra cứu ip hoặc tên miền" />
          <button className="iasbtn" aria-label="Tra cứu">
            <Search size={17} strokeWidth={1.5} />
          </button>
        </div>

        <div className="ias-user">
          <div className="ias-status">
            <span className={"dot" + (online ? "" : " bad")} />
            {online ? "Hệ thống ổn định" : "Hệ thống gián đoạn"}
          </div>
          <input
            className="ias-uid"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            title="user_id (owner-scoping cho project/session/chat)"
          />
          <div className="avatar">
            <User size={18} strokeWidth={1.5} />
          </div>
        </div>
      </header>

      {/* ═══ BODY ═══ */}
      <div className="ias-body">
        {/* SIDEBAR */}
        <aside className="ias-sidebar">
          <div className="ias-sidebar-scroll">
            {INERT_TOP.map((it) => (
              <div key={it.label} className="iasnav inert" title="Phân hệ khác — chưa khả dụng trong bản này">
                <it.icon size={17} strokeWidth={1.5} />
                {it.label}
              </div>
            ))}

            <div className="iasnav group">
              <Sparkles size={17} strokeWidth={1.5} color="var(--teal)" />
              Nền tảng AI
            </div>
            <div style={{ padding: "2px 0 6px" }}>
              {AI_ITEMS.map((it) => (
                <div key={it.id} role="button" tabIndex={0} aria-current={screen === it.id ? "page" : undefined} className={"iassub" + (screen === it.id ? " on" : "")} onClick={() => setScreen(it.id)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") {e.preventDefault(); setScreen(it.id);} }}>
                  <span className="dot" />
                  {it.label}
                </div>
              ))}
            </div>

            {INERT_BOTTOM.map((it) => (
              <div key={it.label} className="iasnav inert" title="Phân hệ khác — chưa khả dụng trong bản này">
                <it.icon size={17} strokeWidth={1.5} />
                {it.label}
              </div>
            ))}
          </div>
          <div className="ias-sidebar-foot">
            <ChevronLeft size={17} strokeWidth={1.5} />
            Thu gọn
          </div>
        </aside>

        {/* MAIN */}
        <main className="ias-main">
          {screen === "chat" && <ChatbotScreen userId={userId} health={health} />}
          {screen === "translate" && <TranslateScreen userId={userId} />}
          {screen === "exploit" && <ExploitScreen userId={userId} />}
          {screen === "report" && <ReportScreen userId={userId} />}
          {screen === "review" && <ReviewScreen />}
          {screen === "models" && <ModelsScreen userId={userId} />}
        </main>
      </div>
    </div>
  );
}
