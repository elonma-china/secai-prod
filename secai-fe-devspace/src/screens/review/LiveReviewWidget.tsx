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
  const [retryCount, setRetryCount] = useState(0);
  // Retain the current request so StrictMode can subscribe again without sending twice.
  const request = useRef<{key: string; promise: Promise<Assignment | null>; assignment: Assignment | null} | null>(null);
  const requestVersion = useRef(0);
  const actorKey = identity ? JSON.stringify([identity.token, identity.actor.actor_id, identity.actor.role]) : "";
  const contextKey = turn && identity?.liveEnabled ? JSON.stringify([actorKey, turn.id]) : "";
  const currentContext = useRef(contextKey);
  currentContext.current = contextKey;
  function contextIsCurrent() {
    const auth = getReviewIdentity();
    return currentContext.current === contextKey && !!auth?.liveEnabled &&
      JSON.stringify([auth.token, auth.actor.actor_id, auth.actor.role]) === actorKey;
  }
  useEffect(() => {
    const version = ++requestVersion.current;
    setAssignment(null); setError(""); setLoading(false); setSaving(false);
    if (!turn || !identity?.liveEnabled) return;
    if (request.current?.key !== contextKey) {
      request.current = {key: contextKey, assignment: null, promise: createReviewApi(identity.token)
        .livePair(turn.prompt_messages, turn.answer, turn.source_group)
        .then(result => {
          if (!result.offered) throw new Error('No A/B pair');
          return result.assignment;
        })};
    }
    const pending = request.current;
    setLoading(true);
    pending.promise.then(result => {
      if (requestVersion.current === version && contextIsCurrent()) {
        pending.assignment = pending.assignment || result;
        setAssignment(pending.assignment);
      }
    }).catch(() => {
      if (requestVersion.current === version && contextIsCurrent()) setError("Chưa tạo được hai phương án A/B. Vui lòng thử lại.");
    }).finally(() => { if (requestVersion.current === version && contextIsCurrent()) setLoading(false); });
    return () => {requestVersion.current++;};
  }, [contextKey, retryCount]);
  async function submit(input: JudgmentInput) {
    const auth = getReviewIdentity();
    const pending = request.current;
    if (!auth?.liveEnabled || !assignment || !pending || pending.key !== contextKey ||
        JSON.stringify([auth.token, auth.actor.actor_id, auth.actor.role]) !== actorKey ||
        currentContext.current !== contextKey) return;
    const version = requestVersion.current;
    const current = () => requestVersion.current === version && contextIsCurrent();
    setSaving(true); setError("");
    try { const judgment = await createReviewApi(auth.token).submit(assignment.id, input); if (current()) {pending.assignment = {...assignment, judgment, status: "submitted"}; setAssignment(pending.assignment);} }
    catch(e) {if (current()) setError(reviewError(e));}
    finally {if (current()) setSaving(false);}
  }
  if (!contextKey || request.current?.key !== contextKey || (!loading && !assignment && !error)) return null;
  return <aside className="review-screen review-live" aria-label="Phản hồi A/B trong chat">{loading ? <p className="review-muted" role="status">Đang tạo hai phương án A/B…</p> : assignment ? <ReviewPair key={assignment.id} assignment={assignment} focusHeading={false} showContext={false} closed={false} saving={saving} error={error} onSubmit={input => void submit(input)}/> : <><p role="alert">{error}</p><button type="button" className="iasbtn ghost sm" onClick={() => {request.current = null; setRetryCount(count => count + 1);}}>Thử lại A/B</button></>}</aside>;
}
