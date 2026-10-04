import { useEffect, useRef, useState } from "react";
import { createReviewApi, reviewError } from "../../reviewApi";
import { getReviewIdentity, useReviewIdentity } from "../../reviewIdentity";
import type { Assignment, JudgmentInput, PromptMessage } from "../../reviewTypes";
import ReviewPair from "./ReviewPair";
export interface LiveReviewTurn { id: string; prompt_messages: PromptMessage[]; answer: string; source_group: string }
export default function LiveReviewWidget({turn}: {turn: LiveReviewTurn | null}) {
  const identity = useReviewIdentity();
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const attempted = useRef(new Set<string>());
  const requestVersion = useRef(0);
  useEffect(() => { requestVersion.current++; setAssignment(null); setLoading(false); setSaving(false); setError(""); }, [identity?.token]);
  useEffect(() => {
    const version = ++requestVersion.current;
    setAssignment(null); setError(""); setLoading(false); setSaving(false);
    if (!turn || attempted.current.has(turn.id)) return;
    attempted.current.add(turn.id);
    const auth = getReviewIdentity();
    if (!auth?.liveEnabled) return;
    setLoading(true);
    createReviewApi(auth.token).livePair(turn.prompt_messages, turn.answer, turn.source_group).then(result => {
      if (requestVersion.current === version && getReviewIdentity()?.token === auth.token && result.offered) setAssignment(result.assignment);
    }).catch(() => {
      // Optional feedback must never disrupt a normal chat response.
    }).finally(() => { if (requestVersion.current === version) setLoading(false); });
    return () => {requestVersion.current++;};
  }, [turn]);
  async function submit(input: JudgmentInput) {
    const auth = getReviewIdentity();
    if (!auth || !assignment) return;
    const version = requestVersion.current;
    setSaving(true); setError("");
    try { const judgment = await createReviewApi(auth.token).submit(assignment.id, input); if (requestVersion.current === version && getReviewIdentity()?.token === auth.token) setAssignment({...assignment, judgment, status: "submitted"}); }
    catch(e) {if (requestVersion.current === version) setError(reviewError(e));}
    finally {if (requestVersion.current === version) setSaving(false);}
  }
  if (!identity?.liveEnabled || (!loading && !assignment)) return null;
  return <aside className="review-screen review-live" aria-label="Phản hồi A/B trong chat">{loading ? <p className="review-muted" role="status">Đang kiểm tra lời mời phản hồi tùy chọn…</p> : assignment && <details open><summary><strong>Đánh giá hai câu trả lời · tùy chọn</strong></summary><p className="review-muted">Cặp này gồm câu trả lời chat và một phương án mới. Nhãn A/B được ẩn nguồn. Phản hồi lưu riêng để quản trị duyệt; không thêm vào lịch sử chat.</p><ReviewPair key={assignment.id} assignment={assignment} focusHeading={false} closed={false} saving={saving} error={error} onSubmit={input => void submit(input)}/></details>}</aside>;
}
