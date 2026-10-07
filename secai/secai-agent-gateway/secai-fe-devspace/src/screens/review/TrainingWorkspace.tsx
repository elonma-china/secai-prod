import {useEffect,useRef,useState} from 'react';
import {FlaskConical} from 'lucide-react';
import type {ReviewApi} from '../../reviewApi';
import {reviewError} from '../../reviewApi';
import type {Eligibility,OverviewRow,Page,TrainingBatch} from '../../reviewTypes';
import {dateLabel,Pagination} from './ReviewOverview';
import TrainingBatchDetail,{StatusChip} from './TrainingBatchDetail';
import {AbAlert,AbDialog,AbEmpty,AbLoading} from './AbParts';
import {ReadOnlyItemDialog} from './ItemDetail';
const QUEUE_PAGE=10; // hàng chờ hiển thị 10 dòng/trang (API trả 20 dòng/trang; cắt phía FE)
export default function TrainingWorkspace({api,refreshKey=0}:{api:ReviewApi;refreshKey?:number}){
 const [eligibility,setEligibility]=useState<Eligibility|null>(null),[queue,setQueue]=useState<Page<OverviewRow>|null>(null),[history,setHistory]=useState<Page<TrainingBatch>|null>(null),[selected,setSelected]=useState<string|null>(null),[viewing,setViewing]=useState<string|null>(null),[confirm,setConfirm]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[refresh,setRefresh]=useState(0),[offset,setOffset]=useState(0),[historyOffset,setHistoryOffset]=useState(0);
 const requestKey=useRef<string|null>(null),apiOffset=Math.floor(offset/20)*20;
 useEffect(()=>{let alive=true;setEligibility(null);setQueue(null);Promise.all([api.batches(historyOffset),api.eligibility(),api.overview('approved','all','',apiOffset)]).then(([h,e,q])=>{if(alive){if(requestKey.current&&h.items.some(b=>b.request_key===requestKey.current))requestKey.current=null;setHistory(h);setEligibility(e);setQueue(q);setError('');}}).catch(e=>{if(alive)setError(reviewError(e));});return()=>{alive=false;};},[api,refresh,apiOffset,historyOffset,refreshKey]);
 async function start(){if(!eligibility)return;setBusy(true);setError('');requestKey.current??=crypto.randomUUID();try{const b=await api.freezeBatch(requestKey.current,eligibility.snapshot_version);requestKey.current=null;setSelected(b.id);setConfirm(false);setRefresh(v=>v+1);}catch(e){setError(reviewError(e));setConfirm(false);setRefresh(v=>v+1);}finally{setBusy(false);}}
 const opener=useRef<HTMLElement|null>(null),pct=eligibility?Math.min(100,Math.round(eligibility.valid_new_count/Math.max(1,eligibility.minimum_pairs)*100)):0,shortage=eligibility?Math.max(0,eligibility.minimum_pairs-eligibility.valid_new_count):0,reasons=eligibility?eligibility.blocked_reasons.filter(r=>!(shortage>0&&/^Cần\s+[\d.,]+\s+cặp/i.test(r))):[];
 const retry=()=>setRefresh(v=>v+1);
 return <>
 {error&&<AbAlert onRetry={retry}>{error}</AbAlert>}
 <div className="ab-ft-grid">
  <div className="ab-ft-main">
  {(queue||!error)&&<div className="iascard ab-ft-queue">
   <div className="ab-card-head"><h4>Hàng chờ</h4></div>
   {queue?<>{queue.items.length>0&&<div className="ab-table-wrap"><table className="ab-table"><thead><tr><th>Câu hỏi đã duyệt</th><th>Admin chọn</th><th>Nguồn</th><th>Thao tác</th></tr></thead><tbody>{queue.items.slice(offset-apiOffset,offset-apiOffset+QUEUE_PAGE).map(r=><tr key={r.id}><td data-label="Câu hỏi"><span className="ab-question">{r.question}</span></td><td data-label="Admin chọn">{r.admin_choice?<span className="chip accent">{r.admin_choice}</span>:'—'}</td><td data-label="Nguồn">{r.source==='qa'?'QA / demo':r.source==='live'?'Chat':'Nhập dữ liệu'}</td><td className="ab-actions"><button type="button" className="iasbtn ghost sm" onClick={e=>{opener.current=e.currentTarget;setViewing(r.id);}}>Xem</button></td></tr>)}</tbody></table></div>}{!queue.items.length&&<AbEmpty>Chưa có mẫu đã duyệt đang chờ.</AbEmpty>}<Pagination offset={offset} total={queue.total||0} size={QUEUE_PAGE} onPage={setOffset}/></>:<AbLoading>Đang tải hàng chờ…</AbLoading>}
  </div>}
  </div>
  <div className="ab-ft-side">
 <div className="iascard ab-summary"><div className="ab-summary-main">
  {eligibility?<><h4 tabIndex={-1} data-ab-focus-home>{`${eligibility.valid_new_count.toLocaleString('vi-VN')} / ${eligibility.minimum_pairs.toLocaleString('vi-VN')} cặp sẵn sàng`}<span className={'chip '+(eligibility.eligible?'ok':'warn')}>{eligibility.eligible?'Đủ điều kiện':'Chưa đủ điều kiện'}</span></h4><div className="ab-meter" role="progressbar" aria-label="Tiến độ dữ liệu" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}><span style={{width:pct+'%'}}/></div>{shortage>0&&<p className="ab-hint warn">{`Còn thiếu ${shortage.toLocaleString('vi-VN')} cặp`}</p>}{reasons.map(r=><p key={r} className="ab-hint warn">{r}</p>)}{!eligibility.eligible&&!shortage&&!reasons.length&&<p className="ab-hint warn">Chưa đủ điều kiện fine-tune.</p>}</>:error?<p className="ab-hint warn">Chưa tải được dữ liệu fine-tune.</p>:<AbLoading>Đang kiểm tra dữ liệu…</AbLoading>}
  <p className="ab-hint">Qwen3-1.7B · Chế độ QA. Mẫu từ chat vẫn được lưu, chờ tích hợp danh tính IAS trước khi dùng huấn luyện production.</p></div>
  <button type="button" className="iasbtn" disabled={busy||!eligibility?.eligible} onClick={e=>{opener.current=e.currentTarget;setConfirm(true);}}><FlaskConical size={16} strokeWidth={1.5}/>Fine-tune</button></div>
  {(history||!error)&&<div className="iascard ab-ft-history">
   <div className="ab-card-head"><h4>Lịch sử fine-tune</h4></div>
   {history?<>{history.items.length>0&&<ul className="ab-hist">{history.items.map(b=><li key={b.id}><div className="ab-hist-main"><div className="ab-hist-top"><code className="mono">{b.id.slice(0,8)}</code><StatusChip status={b.status}/>{b.connection_error&&<span className="chip warn">Chưa cập nhật được</span>}</div><div className="ab-hist-sub"><time>{dateLabel(b.created_at)}</time><span>{b.sample_count} mẫu</span></div></div><button type="button" className="iasbtn ghost sm" onClick={e=>{opener.current=e.currentTarget;setSelected(b.id);}}>Xem batch</button></li>)}</ul>}{!history.items.length&&<AbEmpty>Chưa có batch trong lịch sử.</AbEmpty>}<Pagination offset={historyOffset} total={history.total||0} onPage={setHistoryOffset}/></>:<AbLoading>Đang tải lịch sử…</AbLoading>}
  </div>}
  </div>
 </div>
 {confirm&&<AbDialog label="Xác nhận fine-tune" title={`Chốt ${eligibility?.valid_new_count} mẫu thành một batch?`} size="narrow" hideClose restoreRef={opener} onClose={()=>{if(!busy)setConfirm(false);}} footer={<><button type="button" className="iasbtn ghost" disabled={busy} onClick={()=>setConfirm(false)}>Hủy</button><button type="button" className="iasbtn" disabled={busy} onClick={()=>void start()}>{busy?'Đang gửi…':'Xác nhận fine-tune'}</button></>}><p className="ab-text">Huấn luyện Qwen3-1.7B trên Vast.ai. Mẫu duyệt sau bước này thuộc batch tiếp theo. Model mới chưa tự đưa vào chatbot.</p></AbDialog>}
 {viewing&&<ReadOnlyItemDialog api={api} id={viewing} restoreRef={opener} onClose={()=>setViewing(null)}/>}
 {selected&&<TrainingBatchDetail key={selected} api={api} id={selected} restoreRef={opener} onClose={()=>{setSelected(null);setRefresh(v=>v+1);}}/>}
 </>;
}
