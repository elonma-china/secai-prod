import {useEffect,useId,useState} from 'react';
import {ChevronDown,Settings2} from 'lucide-react';
import {reviewError} from '../../reviewApi';
import type {ReviewApi} from '../../reviewApi';
import {AbAlert} from './AbParts';
export default function ReviewSamplingSettings({api}:{api:ReviewApi}) {
 const [saved,setSaved]=useState<number|null>(null),[input,setInput]=useState(''),[cooldown,setCooldown]=useState(600),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[attempt,setAttempt]=useState(0),fieldId=useId();
 useEffect(()=>{let alive=true;setError('');api.liveSettings().then(data=>{if(alive){setSaved(data.sample_percent);setInput(String(data.sample_percent));setCooldown(data.cooldown_seconds);}}).catch(e=>{if(alive)setError(reviewError(e));});return()=>{alive=false;};},[api,attempt]);
 const percent=Number(input),valid=input.trim()!==''&&Number.isInteger(percent)&&percent>=0&&percent<=100;
 async function save(){if(!valid||saved===null||busy)return;setBusy(true);setError('');setNotice('');try{const data=await api.setLiveSettings(percent);setSaved(data.sample_percent);setInput(String(data.sample_percent));setCooldown(data.cooldown_seconds);setNotice('Đã áp dụng tỷ lệ mới cho toàn hệ thống.');}catch(e){setError(reviewError(e));}finally{setBusy(false);}}
 const reason=saved===null?(error?'Chưa tải được tỷ lệ hiện tại.':'Đang tải tỷ lệ hiện tại…'):!valid?'Nhập số nguyên từ 0 đến 100.':percent===saved?'Chưa thay đổi so với giá trị hiện tại.':'';
 return <div className="iascard ab-settings"><details className="ab-settings-details"><summary><Settings2 size={16} strokeWidth={1.5}/><span>{saved===null?(error?'Tỷ lệ mời A/B · chưa tải được':'Tỷ lệ mời A/B · đang kết nối'):`Tỷ lệ mời A/B: ${saved}%`}</span><span className="chip">Toàn hệ thống</span><ChevronDown size={16} strokeWidth={1.5} className="ab-chev"/></summary>
  <div className="ab-settings-body"><p className="ab-hint">Áp dụng toàn hệ thống. 0% tắt lời mời; 100% mời ở mỗi lượt đủ điều kiện. Vẫn có thời gian chờ {cooldown/60} phút giữa các lời mời của một người dùng.</p>
  <form className="ab-sampling" onSubmit={e=>{e.preventDefault();void save();}}><label className="ab-field" htmlFor={fieldId}>Tỷ lệ mời đánh giá (%)</label><div className="ab-inline"><input id={fieldId} className="iasin" type="number" min="0" max="100" step="1" required value={input} disabled={busy||saved===null} aria-describedby={reason?fieldId+'-why':undefined} onChange={e=>{setInput(e.target.value);setNotice('');}}/><button type="submit" className="iasbtn" disabled={busy||saved===null||!valid||percent===saved}>{busy?'Đang lưu…':'Lưu tỷ lệ'}</button></div>{reason&&!busy&&<p id={fieldId+'-why'} className="ab-hint">{reason}</p>}</form>
  {notice&&<p role="status" className="ab-hint ok">{notice}</p>}</div></details>
  {error&&<div className="ab-settings-alert"><AbAlert onRetry={saved===null?()=>setAttempt(v=>v+1):undefined}>{error}</AbAlert></div>}
 </div>;
}
