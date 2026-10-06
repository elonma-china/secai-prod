import {useEffect,useState} from 'react';
import {reviewError} from '../../reviewApi';
import type {ReviewApi} from '../../reviewApi';
export default function ReviewSamplingSettings({api}:{api:ReviewApi}) {
 const [saved,setSaved]=useState<number|null>(null),[input,setInput]=useState(''),[cooldown,setCooldown]=useState(600),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 useEffect(()=>{let alive=true;api.liveSettings().then(data=>{if(alive){setSaved(data.sample_percent);setInput(String(data.sample_percent));setCooldown(data.cooldown_seconds);}}).catch(e=>{if(alive)setError(reviewError(e));});return()=>{alive=false;};},[api]);
 const percent=Number(input),valid=input.trim()!==''&&Number.isInteger(percent)&&percent>=0&&percent<=100;
 async function save(){if(!valid||saved===null||busy)return;setBusy(true);setError('');setNotice('');try{const data=await api.setLiveSettings(percent);setSaved(data.sample_percent);setInput(String(data.sample_percent));setCooldown(data.cooldown_seconds);setNotice('Đã áp dụng tỷ lệ mới cho toàn hệ thống.');}catch(e){setError(reviewError(e));}finally{setBusy(false);}}
 return <details className="review-campaign-settings"><summary>{saved===null?'Tỷ lệ mời A/B · đang kết nối':`Tỷ lệ mời A/B: ${saved}%`}</summary>
  <p className="review-muted">Áp dụng toàn hệ thống. 0% tắt lời mời; 100% mời ở mỗi lượt đủ điều kiện. Vẫn có thời gian chờ {cooldown/60} phút giữa các lời mời của một người dùng.</p>
  <form className="review-toolbar" onSubmit={e=>{e.preventDefault();void save();}}><label className="review-field">Tỷ lệ mời đánh giá (%)<input className="iasin" type="number" min="0" max="100" step="1" required value={input} disabled={busy||saved===null} onChange={e=>{setInput(e.target.value);setNotice('');}}/></label><button className="iasbtn" disabled={busy||saved===null||!valid||percent===saved}>{busy?'Đang lưu…':'Lưu tỷ lệ'}</button></form>
  {error&&<p className="review-error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 </details>;
}
