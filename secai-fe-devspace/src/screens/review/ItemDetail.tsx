import {useEffect,useState} from 'react';
import type {RefObject} from 'react';
import Markdown from '../../components/Markdown';
import type {ReviewApi} from '../../reviewApi';
import {reviewError} from '../../reviewApi';
import type {OverviewDetail} from '../../reviewTypes';
import {AbAlert,AbDialog,AbLoading} from './AbParts';
import {ChoiceChip,dateLabel} from './ReviewOverview';

/** Nội dung chi tiết một mẫu (câu hỏi, hai đáp án render markdown, chi tiết phiếu). onWinner/onComment vắng = chỉ xem. */
export function ItemDetailBody({detail,winner='',onWinner,comment='',onComment}:{detail:OverviewDetail;winner?:string;onWinner?:(id:string)=>void;comment?:string;onComment?:(v:string)=>void}){
 const letter=(id:string|null)=>id?String.fromCharCode(65+detail.item.candidates.findIndex(c=>c.id===id)):'';
 const pick=(j:OverviewDetail['judgments'][number])=>{const l=j.winning_candidate_id?letter(j.winning_candidate_id):j.decision==='left'?'A':j.decision==='right'?'B':'';return l?<span className="chip accent ab-pick-chip">Chọn {l}</span>:<ChoiceChip value={j.decision}/>;};
 return <>
  <div className="iascard ab-question-box"><span className="ab-label">Câu hỏi</span><div className="ab-full-question"><Markdown>{detail.question}</Markdown></div></div>
  <div className="iascard ab-votes" role="group" aria-label="Lựa chọn của user"><div className="ab-votes-head"><h4>Lựa chọn của user</h4><span className="ab-hint">{detail.judgments.length?`${detail.judgments.length} phiếu`:'Chưa có phiếu'}</span></div>
   {detail.judgments.length>0&&<ul className="ab-votes-list">{detail.judgments.map(j=><li key={j.id}><span className="mono">{j.reviewer_id}</span>{pick(j)}{j.submitted_at&&<time>{dateLabel(j.submitted_at)}</time>}{j.comment&&<span className="ab-vote-comment">{j.comment}</span>}</li>)}</ul>}</div>
  <div className="ab-answers">{detail.item.candidates.map((c,i)=><div key={c.id} className={'iascard review-card ab-answer'+(winner===c.id?' selected':'')}>
   <div className="ab-answer-head"><h4>Câu trả lời {String.fromCharCode(65+i)}</h4>{detail.user_winner_id===c.id&&<span className="chip accent">User đã chọn</span>}</div>
   <div className="ab-answer-text"><Markdown>{c.text}</Markdown></div>
   {onWinner&&<label className="ab-pick"><input type="radio" name="admin-winner" checked={winner===c.id} onChange={()=>onWinner(c.id)}/> Chọn đáp án này</label>}</div>)}</div>
  {onComment&&<label className="ab-field">Nhận xét (tùy chọn)<textarea className="iasin" rows={3} maxLength={4000} value={comment} onChange={e=>onComment(e.target.value)}/></label>}
  <details className="iascard ab-fold"><summary>Chi tiết</summary><div className="ab-fold-body"><p className="ab-text">Nguồn: {detail.source} · {detail.vote_count} phiếu · {detail.campaign_state==='open'?'Đợt đang mở':'Đợt đã đóng'}</p>{detail.adjudication&&<p className="ab-text">Admin: {detail.adjudication.reason}</p>}</div></details>
 </>;
}

/** Dialog chỉ xem cho mẫu đã duyệt (hàng chờ Fine-tune). */
export function ReadOnlyItemDialog({api,id,onClose,restoreRef}:{api:ReviewApi;id:string;onClose:()=>void;restoreRef?:RefObject<HTMLElement|null>}){
 const [detail,setDetail]=useState<OverviewDetail|null>(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 useEffect(()=>{let alive=true;setError('');api.overviewDetail(id).then(d=>{if(alive)setDetail(d);}).catch(e=>{if(alive)setError(reviewError(e));});return()=>{alive=false;};},[api,id,attempt]);
 return <AbDialog label="Chi tiết phản hồi" title="Xem mẫu đã duyệt" restoreRef={restoreRef} onClose={onClose}>
  {error&&<AbAlert onRetry={()=>setAttempt(v=>v+1)}>{error}</AbAlert>}
  {detail?<ItemDetailBody detail={detail}/>:!error&&<AbLoading>Đang tải mẫu…</AbLoading>}
 </AbDialog>;
}
