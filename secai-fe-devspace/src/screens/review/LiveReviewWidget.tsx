import { useState } from "react";
import { createReviewApi, reviewError } from "../../reviewApi";
import { getReviewIdentity, useReviewIdentity } from "../../reviewIdentity";
import type { Assignment, JudgmentInput } from "../../reviewTypes";
import ReviewPair from "./ReviewPair";

// A and B are now generated in parallel with the caller's own chat request (see
// ChatbotScreen's send()): "pending" covers the window after sampling picked this turn but
// before both answers + the persisted assignment are ready; "ready" carries the assignment;
// "error" covers any failure along that chain (sampling/generation/persist), with a `retry`
// that redoes it from scratch — same guarantee the old self-fetching widget gave: once a
// turn starts, it always ends in "ready" or "error", never silently disappears.
// This widget itself no longer calls /live-pairs — it only renders what the caller found.
export type LiveReviewState =
  | { status: "pending" }
  | { status: "ready"; assignment: Assignment }
  | { status: "error"; retry: () => void };

export default function LiveReviewWidget({ state }: { state: LiveReviewState | undefined }) {
  useReviewIdentity(); // re-render on role/token change so submit() always uses the current one
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  if (!state) return null;
  const current = assignment ?? (state.status === "ready" ? state.assignment : null);
  async function submit(input: JudgmentInput) {
    const auth = getReviewIdentity();
    if (!auth?.liveEnabled || !current) return;
    setSaving(true);
    setError("");
    try {
      const judgment = await createReviewApi(auth.token).submit(current.id, input);
      setAssignment({ ...current, judgment, status: "submitted" });
    } catch (e) {
      setError(reviewError(e));
    } finally {
      setSaving(false);
    }
  }
  return (
    <aside className="review-screen review-live" aria-label="Phản hồi A/B trong chat">
      {current ? (
        <ReviewPair
          key={current.id}
          assignment={current}
          focusHeading={false}
          showContext={false}
          closed={false}
          saving={saving}
          error={error}
          onSubmit={(input) => void submit(input)}
        />
      ) : state.status === "error" ? (
        <>
          <p role="alert">Chưa tạo được hai phương án A/B. Vui lòng thử lại.</p>
          <button type="button" className="iasbtn ghost sm" onClick={state.retry}>
            Thử lại A/B
          </button>
        </>
      ) : (
        <p className="review-muted" role="status">
          Đang tạo hai phương án A/B…
        </p>
      )}
    </aside>
  );
}
