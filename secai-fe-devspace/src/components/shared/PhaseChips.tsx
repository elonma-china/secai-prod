import { Fragment } from "react";
import { Check, ChevronRight } from "lucide-react";

export interface PhaseItem {
  key: string;
  label: string;
  status: "done" | "active" | "pending";
}

/** recon → map-CVE → khuyến nghị vá → verify … progress chips, built by folding SSE `phase`
 * events (WorkflowEvent) in arrival order — see ChatbotScreen's `foldPhase`. */
export default function PhaseChips({ items }: { items: PhaseItem[] }) {
  if (!items.length) return null;
  return (
    <div className="phase-row">
      {items.map((p, i) => (
        <Fragment key={p.key}>
          {i > 0 && <ChevronRight size={12} strokeWidth={1.5} className="phase-sep" />}
          <span className={`phase-chip ${p.status}`}>
            {p.status === "done" && <Check size={11} strokeWidth={1.5} />}
            {p.status === "active" && (
              <span className="blink" style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--teal)" }} />
            )}
            {p.label}
          </span>
        </Fragment>
      ))}
    </div>
  );
}
