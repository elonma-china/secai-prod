import {useEffect,useRef,useState} from 'react';
import {CheckCircle2,RefreshCw,Search,X} from 'lucide-react';
import type {ReviewApi} from '../../reviewApi';
import {reviewError} from '../../reviewApi';
import type {OverviewRow,OverviewDetail,Page} from '../../reviewTypes';
import ReviewSamplingSettings from './ReviewSamplingSettings';
import TrainingWorkspace from './TrainingWorkspace';
import {ItemDetailBody} from './ItemDetail';
import {AbAlert,AbDialog,AbEmpty,AbLoading,arrowNav} from './AbParts';
export const dateLabel=(value:string)=>new Date(value).toLocaleString('vi-VN');
const choices:Record<string,string>={tie:'Ngang nhau',both_bad:'Cả hai kém',skip:'Bỏ qua',insufficient_context:'Thiếu ngữ cảnh'};
export const choiceLabel=(value:string)=>choices[value]||value;
/** Lựa chọn của user dưới dạng chip. Backend trả "N phiếu" khi không có đáp án A/B thống nhất (kể cả các phiếu cùng chọn "ngang nhau"/"cả hai kém"), nên chỉ gọi trung tính, không kết luận "mâu thuẫn". */
export function ChoiceChip({value}:{value:string}){
 if(value==='A'||value==='B')return <span className="chip accent ab-pick-chip">{value}</span>;
 if(value==='both_bad'||value==='insufficient_context')return <span className="chip warn">{choiceLabel(value)}</span>;
 if(/^\d+ phiếu$/.test(value))return <span className="chip warn" title="Chưa có đáp án A/B được chọn thống nhất; mở mẫu để xem từng phiếu">{value}</span>;
 return <span className="chip">{choiceLabel(value)}</span>;
}
const STATUSES:[string,string,string][]=[['pending','Chờ duyệt',''],['approved','Đã duyệt','chờ fine-tune'],['excluded','Đã loại',''],['all','Tất cả','']];
export default function ReviewOverview({api}:{api:ReviewApi}) {
 const [tab,setTab]=useState('feedback'),[page,setPage]=useState<Page<OverviewRow>|null>(null),[offset,setOffset]=useState(0),[search,setSearch]=useState(''),[query,setQuery]=useState(''),[source,setSource]=useState('all'),[status,setStatus]=useState('pending'),[refresh,setRefresh]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[detail,setDetail]=useState<OverviewDetail|null>(null),[winner,setWinner]=useState(''),[comment,setComment]=useState(''),[loading,setLoading]=useState(true);
 const [bulk,setBulk]=useState<{eligible_count:number;skipped_count:number;snapshot_version:string}|null>(null);
 useEffect(()=>{let alive=true;setLoading(true);setPage(null);api.overview(status,source,query,offset).then(p=>{if(alive){setPage(p);setError('');}}).catch(e=>{if(alive)setError(reviewError(e));}).finally(()=>{if(alive)setLoading(false);});return()=>{alive=false;};},[api,status,source,query,offset,refresh]);
 function reload(){setDetail(null);setBulk(null);setRefresh(v=>v+1);}
 async function open(id:string){setBusy(true);setError('');setDetail(null);try{const d=await api.overviewDetail(id);setDetail(d);setWinner('');setComment('');}catch(e){setError(reviewError(e));}finally{setBusy(false);}}
 async function approve(exclude=false){if(!detail)return;setBusy(true);setError('');try{await api.adjudicate(detail.id,exclude?'skip':'winner',exclude?null:winner,comment.trim()||(exclude?'Admin loại mẫu khỏi dữ liệu huấn luyện.':'Admin kiểm tra và xác nhận đáp án dùng huấn luyện.'));setNotice(exclude?'Đã loại mẫu.':'Đã duyệt. Mẫu đã chuyển sang Fine-tune.');reload();}catch(e){setError(reviewError(e));}finally{setBusy(false);}}
 async function preview(){setBusy(true);setError('');try{const r=await api.bulkPreview(source,query);if(r.eligible_count===0)setBulkNone({key:`${source}|${query}|${refresh}`,skipped:r.skipped_count});else setBulk(r);}catch(e){setError(reviewError(e));}finally{setBusy(false);}}
 async function approveAll(){if(!bulk)return;setBusy(true);setError('');try{const r=await api.bulkApprove(source,query,bulk.snapshot_version);setNotice(`Đã duyệt ${r.approved} câu, giữ lại ${r.skipped} câu cần kiểm tra riêng.`);reload();}catch(e){setBulk(null);setError(reviewError(e));}finally{setBusy(false);}}
 const [trainingRefresh,setTrainingRefresh]=useState(0),[bulkNone,setBulkNone]=useState<{key:string;skipped:number}|null>(null),[counts,setCounts]=useState<Record<string,number>>({}),opener=useRef<HTMLElement|null>(null);
 const mark=(e:{currentTarget:HTMLElement})=>{opener.current=e.currentTarget;};
 // Số liệu thật từ API overview (total theo từng trạng thái, cùng bộ lọc nguồn + tìm kiếm).
 useEffect(()=>{let alive=true;Promise.all(STATUSES.map(([s])=>api.overview(s,source,query,0).then(p=>[s,p.total] as const).catch(()=>null))).then(rows=>{if(alive)setCounts(Object.fromEntries(rows.filter((r):r is readonly [string,number]=>r!==null)));});return()=>{alive=false;};},[api,source,query,refresh]);
 useEffect(()=>{if(!notice)return;const t=setTimeout(()=>setNotice(''),8000);return()=>clearTimeout(t);},[notice]);
 const statusLabel=STATUSES.find(s=>s[0]===status)?.[1]||'';
 const noneHere=bulkNone&&bulkNone.key===`${source}|${query}|${refresh}`?bulkNone:null;
 const bulkReason=!loading&&!page?.total?'Không có câu nào để duyệt.':noneHere?`Không có câu nào đủ điều kiện duyệt hàng loạt${noneHere.skipped?` (${noneHere.skipped} câu cần duyệt riêng)`:''}.`:'';
 const detailReason=detail&&!detail.adjudication?(!detail.complete?'Còn phiếu chưa nộp; chưa thể duyệt.':detail.campaign_state!=='open'?'Đợt đánh giá đã đóng; chỉ được xem.':!winner?'Chọn một đáp án để duyệt.':''):'';
 return <section className="ab-screen">
  <header className="ab-head"><h3>Đánh giá A/B</h3>
   <div className="iasseg" role="tablist" aria-label="Quản lý phản hồi" onKeyDown={e=>arrowNav(e,'[role=tab]',true)}><button type="button" role="tab" tabIndex={tab==='feedback'?0:-1} aria-selected={tab==='feedback'} className={'opt'+(tab==='feedback'?' on':'')} onClick={()=>setTab('feedback')}>Phản hồi</button><button type="button" role="tab" tabIndex={tab==='training'?0:-1} aria-selected={tab==='training'} className={'opt'+(tab==='training'?' on':'')} onClick={()=>setTab('training')}>Fine-tune</button></div>
   <div className="ab-head-actions"><button type="button" className="iasbtn ghost" disabled={tab==='feedback'&&(busy||loading)} onClick={()=>{if(tab==='feedback')reload();else setTrainingRefresh(v=>v+1);}}><RefreshCw size={16} strokeWidth={1.5}/>Làm mới</button></div></header>
  <div className="ab-body" tabIndex={-1}>
  {notice&&<div role="status" className="ab-notice"><CheckCircle2 size={16} strokeWidth={1.5}/><span>{notice}</span><button type="button" className="ab-notice-close" aria-label="Đóng thông báo" onClick={()=>setNotice('')}><X size={14} strokeWidth={1.5}/></button></div>}
  {tab==='training'?<TrainingWorkspace api={api} refreshKey={trainingRefresh}/>:<>
   {error&&!detail&&!bulk&&<AbAlert busy={busy||loading} onRetry={reload}>{error}</AbAlert>}
   <ReviewSamplingSettings api={api}/>
   <div className="ab-stats" role="group" aria-label="Lọc theo trạng thái" onKeyDown={e=>arrowNav(e,'button')}>{STATUSES.map(([s,label,sub])=>{const on=status===s,n=counts[s]??(on?page?.total:undefined);return <button key={s} type="button" className={'iascard ab-stat'+(on?' on':'')} aria-pressed={on} tabIndex={on?0:-1} onClick={()=>{setStatus(s);setOffset(0);setDetail(null);}}><span className="ab-stat-num">{n===undefined?'…':n.toLocaleString('vi-VN')}</span><span className="ab-stat-label">{label}</span>{sub&&<span className="ab-stat-sub">{sub}</span>}</button>;})}</div>
   <div className="iascard ab-table-card">
    <div className="ab-card-head"><h4 tabIndex={-1} data-ab-focus-home>{statusLabel}</h4>{bulkReason&&status==='pending'&&<span className="ab-hint ab-spacer">{bulkReason}</span>}{status==='pending'&&<button type="button" className={'iasbtn'+(bulkReason?'':' ab-spacer')} disabled={busy||loading||!page?.total||!!noneHere} onClick={e=>{mark(e);void preview();}}>Duyệt toàn bộ</button>}</div>
    <div className="ab-filters"><label className="ab-field">Nguồn<select className="iasin" value={source} onChange={e=>{setSource(e.target.value);setOffset(0);setDetail(null);}}><option value="all">Tất cả</option><option value="live">Chat</option><option value="qa">QA / demo</option><option value="import">Nhập dữ liệu</option></select></label>
     <form className="ab-search" onSubmit={e=>{e.preventDefault();setQuery(search.trim());setOffset(0);}}><input className="iasin" aria-label="Tìm câu hỏi" placeholder="Tìm câu hỏi…" value={search} onChange={e=>setSearch(e.target.value)}/><button className="iasbtn ghost"><Search size={16} strokeWidth={1.5}/>Tìm</button></form></div>
    {loading?<AbLoading>Đang tải phản hồi…</AbLoading>:error&&!page?null:<><div className="ab-table-wrap"><table className="ab-table"><thead><tr><th>Câu hỏi</th><th>User chọn</th><th>Thời gian</th><th>Thao tác</th></tr></thead><tbody>{page?.items.map(r=><tr key={r.id}><td data-label="Câu hỏi"><span className="ab-question">{r.question}</span></td><td data-label="User chọn"><ChoiceChip value={r.user_choice}/></td><td data-label="Thời gian">{dateLabel(r.submitted_at)}</td><td className="ab-actions"><button type="button" className="iasbtn ghost sm" disabled={busy} onClick={e=>{mark(e);void open(r.id);}}>{status==='pending'?'Xem và duyệt':'Xem'}</button></td></tr>)}</tbody></table></div>{!page?.items.length&&<AbEmpty>Không có phản hồi trong danh sách này.</AbEmpty>}<Pagination offset={offset} total={page?.total||0} onPage={setOffset}/></>}
   </div>
   {bulk&&<AbDialog label="Xác nhận duyệt toàn bộ" title={`Xác nhận duyệt ${bulk.eligible_count} câu`} size="narrow" hideClose restoreRef={opener} onClose={()=>{if(!busy)setBulk(null);}} footer={<>{!bulk.eligible_count&&<span className="ab-hint ab-reason">Không có câu nào đủ điều kiện duyệt hàng loạt.</span>}<button type="button" className="iasbtn ghost" disabled={busy} onClick={()=>setBulk(null)}>Hủy</button><button type="button" className="iasbtn" disabled={busy||!bulk.eligible_count} onClick={()=>void approveAll()}>Xác nhận duyệt</button></>}>
    {error&&<AbAlert>{error}</AbAlert>}<p className="ab-text">Áp dụng cho toàn bộ kết quả lọc hiện tại, gồm các trang chưa hiển thị. Đáp án được duyệt là lựa chọn nhất quán của user.</p><p className="ab-text">{bulk.skipped_count} câu chưa có lựa chọn rõ ràng, còn phiếu chưa nộp hoặc đợt đã đóng sẽ giữ lại để duyệt riêng.</p></AbDialog>}
   {detail&&<AbDialog label="Chi tiết phản hồi" title="Kiểm tra phản hồi" dirty={!detail.adjudication&&(!!winner||!!comment.trim())} restoreRef={opener} onClose={()=>{if(!busy)setDetail(null);}} footer={!detail.adjudication?<>{detailReason&&<span id="ab-detail-reason" className="ab-hint ab-reason">{detailReason}</span>}<button type="button" className="iasbtn ghost" disabled={busy||!detail.complete||detail.campaign_state!=='open'} onClick={()=>void approve(true)}>Loại mẫu</button><button type="button" className="iasbtn" disabled={busy||!winner||!detail.complete||detail.campaign_state!=='open'} aria-describedby={detailReason?'ab-detail-reason':undefined} onClick={()=>void approve()}>Duyệt và chuyển sang Fine-tune</button></>:undefined}>
    {error&&<AbAlert>{error}</AbAlert>}
    <ItemDetailBody detail={detail} winner={winner} onWinner={detail.adjudication?undefined:setWinner} comment={comment} onComment={detail.adjudication?undefined:setComment}/>
   </AbDialog>}
  </>}
  </div>
 </section>;
}
export function Pagination({offset,total,onPage,size=20}:{offset:number;total:number;onPage:(n:number)=>void;size?:number}){return total>size?<div className="ab-pagination"><span>{offset+1}–{Math.min(offset+size,total)} / {total}</span><button type="button" className="iasbtn ghost sm" disabled={!offset} onClick={()=>onPage(Math.max(0,offset-size))}>Trước</button><button type="button" className="iasbtn ghost sm" disabled={offset+size>=total} onClick={()=>onPage(offset+size)}>Sau</button></div>:null;}
