import {useEffect, useState} from 'react';
import {reviewError} from '../../reviewApi';
import type {ReviewApi, TrainingJob} from '../../reviewApi';
const active=new Set(['queued','waiting_gpu','training','evaluating']);
const labels:Record<string,string>={queued:'Đang xếp hàng',waiting_gpu:'Chờ GPU trống',training:'Đang huấn luyện trên Vast.ai',evaluating:'Đang đánh giá',succeeded:'Hoàn tất',failed:'Thất bại',interrupted:'Bị gián đoạn'};
export default function RewardTraining({api,campaignId,approvedPairs}:{api:ReviewApi;campaignId:string;approvedPairs:number}) {
 const [enabled,setEnabled]=useState(false),[job,setJob]=useState<TrainingJob|null>(null),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState(''),[refresh,setRefresh]=useState(0);
 useEffect(()=>{
  let alive=true; let timer:ReturnType<typeof setTimeout>|undefined;
  setLoading(true);setError('');
  async function poll(){
   try {const data=await api.trainingJobs(campaignId);if(!alive)return;setEnabled(data.enabled);setJob(data.jobs[0]||null);setError('');if(data.jobs.some(j=>active.has(j.status)))timer=setTimeout(poll,2000);}
   catch(e){if(alive){setEnabled(false);setError(reviewError(e));}}
   finally{if(alive)setLoading(false);}
  }
  void poll();
  return()=>{alive=false;if(timer)clearTimeout(timer);};
 },[api,campaignId,refresh]);
 async function start(){setBusy(true);setError('');try{const created=await api.startTraining(campaignId);setJob(created);if(active.has(created.status))setRefresh(v=>v+1);}catch(e){setError(reviewError(e));}finally{setBusy(false);}}
 return <div className="review-training"><div><h4>Huấn luyện model chấm điểm</h4>
  <p>{enabled?'Vast.ai · Qwen3-1.7B · LoRA + đầu điểm số. Chế độ thử nghiệm QA; cần ít nhất 3 nhóm câu hỏi độc lập đã duyệt.':loading?'Đang tải trạng thái huấn luyện…':'Chưa kết nối dịch vụ huấn luyện.'}</p>
  <p>Mẫu QA chỉ để kiểm thử; model mới chưa đưa vào chatbot.</p>
  {job&&<div role="status"><strong>{labels[job.status]||job.status}</strong><p>Train {job.counts.train} · Validation {job.counts.validation} · Test {job.counts.test}</p><small>Job: {job.id}</small>
   {job.result&&<><p>Đồng thuận trên tập test QA: {(job.result.test.accuracy*100).toFixed(1)}% ({job.result.test.records} cặp).</p><p>Phiên bản: <code>{job.result.artifact_sha256}</code></p></>}
   {job.error&&<p role="alert">{job.error}</p>}
  </div>}
  {error&&<p role="alert" className="review-error">{error}</p>}
 </div><div><button className="iasbtn" disabled={!enabled||busy||loading||approvedPairs<3||!!job&&active.has(job.status)} onClick={()=>void start()}>{busy?'Đang gửi job…':'Huấn luyện model chấm điểm'}</button><button className="iasbtn" disabled={busy||loading} onClick={()=>setRefresh(v=>v+1)}>Cập nhật trạng thái</button></div></div>;
}
