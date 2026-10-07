import {useEffect, useRef, useState} from "react";
import {createReviewApi, reviewError} from "../../reviewApi";
import {getReviewIdentity, useReviewIdentity} from "../../reviewIdentity";
import type {Assignment, JudgmentInput} from "../../reviewTypes";
import ReviewPair from "./ReviewPair";
import {AbAlert, AbLoading} from "./AbParts";
export default function DemoFeedback() {
  const identity = useReviewIdentity();
  const dialog = useRef<HTMLDialogElement>(null);
  const version = useRef(0);
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [closed, setClosed] = useState(false);
  useEffect(() => () => {version.current++; if (dialog.current?.open) dialog.current.close();}, []);
  function close() {version.current++; dialog.current?.close(); setAssignment(null); setBusy(false);}
  async function open() {
    const auth = getReviewIdentity();
    if (!auth || auth.actor.role !== "reviewer") return;
    const request = ++version.current;
    dialog.current?.showModal(); setBusy(true); setError(""); setAssignment(null);
    try {
      const api = createReviewApi(auth.token);
      const campaign = (await api.campaigns()).find(c => c.title.startsWith("Demo 2 vai trò"));
      if (!campaign) throw new Error("no-demo");
      const rows = await api.queue("pending", campaign.id);
      if (!rows.length) {if (request === version.current) setError("Bạn đã đánh giá hết mẫu demo. Admin có thể xem các phản hồi trong tab A/B."); return;}
      const found = await api.assignment(rows[0].id);
      if (request === version.current && getReviewIdentity()?.token === auth.token) {setAssignment(found); setClosed(campaign.state === "closed");}
    } catch(e) {if (request === version.current) setError(e instanceof Error && e.message === "no-demo" ? "Chưa có dữ liệu demo hai vai trò. Hãy tạo mẫu kiểm thử trước." : reviewError(e));}
    finally {if (request === version.current) setBusy(false);}
  }
  async function submit(input: JudgmentInput) {
    const auth = getReviewIdentity();
    if (!assignment || !auth || auth.actor.role !== "reviewer") return;
    const request = version.current;
    setBusy(true); setError("");
    try {
      const saved = await createReviewApi(auth.token).submit(assignment.id,input);
      if (request === version.current && getReviewIdentity()?.token === auth.token) setAssignment({...assignment,judgment:saved,status:"submitted"});
    } catch(e) {if (request === version.current) setError(reviewError(e));}
    finally {if (request === version.current) setBusy(false);}
  }
  return <><button className="iasbtn" disabled={!identity || busy} onClick={() => void open()}>Thử đánh giá A/B</button><dialog ref={dialog} className="demo-dialog review-screen" aria-labelledby="demo-feedback-title" onCancel={close}><div className="review-toolbar"><h2 id="demo-feedback-title">Thử đánh giá A/B</h2><button type="button" className="iasbtn ghost sm" onClick={close}>Đóng</button></div>{busy && !assignment && <AbLoading>Đang tải ví dụ…</AbLoading>}{error && !assignment && <AbAlert>{error}</AbAlert>}{assignment && <><div className="iascard ab-question-box"><span className="ab-label">Câu hỏi</span><p className="ab-full-question">{assignment.prompt_messages.at(-1)?.content}</p></div><ReviewPair assignment={assignment} showContext={false} focusHeading={false} closed={closed} saving={busy} error={error} onSubmit={input => void submit(input)}/>{assignment.status === "submitted" && <p className="ab-notice" role="status">Đã lưu phản hồi. Chuyển sang Admin và mở tab A/B để phê duyệt.</p>}</>}</dialog></>;
}
