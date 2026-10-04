import { useEffect, useRef, useState } from "react";
import Markdown from "../../components/Markdown";
import { DECISIONS, ERROR_TAGS } from "../../reviewTypes";
import type { Assignment, Decision, ErrorTag, JudgmentInput } from "../../reviewTypes";
export default function ReviewPair({assignment, closed, saving, error, onSubmit, focusHeading = true}: {assignment: Assignment; closed: boolean; saving: boolean; error: string; onSubmit: (input: JudgmentInput) => void; focusHeading?: boolean}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (focusHeading) heading.current?.focus(); }, [assignment.id, focusHeading]);
  const [decision, setDecision] = useState<Decision | "">(assignment.judgment?.decision || "");
  const [tags, setTags] = useState<{left: ErrorTag[]; right: ErrorTag[]}>(assignment.judgment?.error_tags || {left: [], right: []});
  const [comment, setComment] = useState(assignment.judgment?.comment || "");
  const locked = closed || !!assignment.judgment;
  const reasonRequired = decision === "both_bad" || decision === "insufficient_context" || tags.left.includes("other") || tags.right.includes("other");
  function toggle(side: "left" | "right", tag: ErrorTag) { setTags(prev => ({...prev, [side]: prev[side].includes(tag) ? prev[side].filter(t => t !== tag) : [...prev[side], tag]})); }
  return <section className="review-pair">
    <h3 ref={heading} tabIndex={-1}>Mẫu {assignment.external_id}</h3>
    <div className="review-note">{assignment.rubric || "Ưu tiên đúng kỹ thuật và có bằng chứng; sau đó đủ ý và phù hợp câu hỏi. Không chọn chỉ vì câu trả lời dài. Phân biệt thiếu bằng chứng với đã xác minh sai."}</div>
    <section className="iascard review-card"><h4>Ngữ cảnh</h4>{assignment.prompt_messages.map((message, i) => <div key={i}><strong>{message.role === "user" ? "Người dùng" : "Trợ lý"}</strong><Markdown>{message.content}</Markdown></div>)}
    {assignment.evidence && <><h4>Bằng chứng được cung cấp</h4><Markdown>{assignment.evidence}</Markdown></>}</section>
    <form onSubmit={e => {e.preventDefault(); if (decision) onSubmit({decision, error_tags: tags, comment});}}>
      <div className="review-columns">{(["left", "right"] as const).map((side, i) => <section key={side} className="iascard review-card"><h4>Câu trả lời {i ? "B" : "A"}</h4><div className="review-answer"><Markdown>{assignment[side].text}</Markdown><details><summary>Xem văn bản gốc</summary><pre className="review-metadata">{assignment[side].text}</pre></details></div><fieldset disabled={locked || saving}><legend>Nhãn lỗi câu trả lời {i ? "B" : "A"}</legend>{ERROR_TAGS.map(([tag, label]) => <label className="review-check" key={tag}><input type="checkbox" checked={tags[side].includes(tag)} onChange={() => toggle(side, tag)}/>{label}</label>)}</fieldset></section>)}</div>
      <fieldset disabled={locked || saving} className="review-decisions"><legend>Kết quả đánh giá</legend>{DECISIONS.map(([value, label]) => <label className="review-check" key={value}><input type="radio" name="decision" value={value} checked={decision === value} onChange={() => setDecision(value)} required/>{label}</label>)}</fieldset>
      <label className="review-field">Nhận xét / lý do {reasonRequired ? "(bắt buộc)" : "(tùy chọn)"}<textarea className="iasin" value={comment} maxLength={4000} required={reasonRequired} disabled={locked || saving} onChange={e => setComment(e.target.value)} rows={4}/></label>
      {error && <div role="alert" className="review-error">{error} Nội dung phiếu vẫn được giữ; có thể nộp lại.</div>}
      {assignment.judgment ? <p role="status">Đã nộp phiếu. Phiếu đã nộp không thể chỉnh sửa.</p> : closed ? <p>Đợt đánh giá đã đóng.</p> : <button className="iasbtn review-primary" disabled={saving || !decision || (reasonRequired && !comment.trim())}>{saving ? "Đang nộp…" : "Nộp phiếu đánh giá"}</button>}
    </form>
  </section>;
}
