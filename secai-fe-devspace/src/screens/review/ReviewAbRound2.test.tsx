// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import ReviewOverview,{ChoiceChip} from './ReviewOverview';
import ReviewSamplingSettings from './ReviewSamplingSettings';
import ReviewPair from './ReviewPair';
import TrainingBatchDetail from './TrainingBatchDetail';
import TrainingWorkspace from './TrainingWorkspace';
import {AbDialog} from './AbParts';
import {ReadOnlyItemDialog} from './ItemDetail';
import type {Assignment} from '../../reviewTypes';
afterEach(cleanup);

const row=(id:string,question='Câu hỏi '+id)=>({id,question,user_choice:'A',submitted_at:'2026-10-06',complete:true,campaign_state:'open',source:'live',vote_count:1,user_winner_id:'c1',admin_choice:'A',adjudication:null,batch_id:null});
function overviewApi(){
 const totals:Record<string,number>={pending:7,approved:3,excluded:2,all:12};
 return {overview:vi.fn(async(status:string)=>({items:[row('i1')],total:totals[status]??0})),liveSettings:vi.fn().mockResolvedValue({sample_percent:10,cooldown_seconds:600}),
  eligibility:vi.fn().mockResolvedValue({valid_new_count:980,minimum_pairs:1000,eligible:false,blocked_reasons:['Cần 1000 cặp hợp lệ mới.'],snapshot_version:'s'}),batches:vi.fn().mockResolvedValue({items:[],total:0}),bulkPreview:vi.fn()};
}

test('stat tiles show real totals per status and filter by pressing them (incl. Đã duyệt)',async()=>{
 const a=overviewApi();render(<ReviewOverview api={a as never}/>);
 const group=await screen.findByRole('group',{name:'Lọc theo trạng thái'});
 await waitFor(()=>expect(within(group).getByRole('button',{name:/Đã duyệt/}).textContent).toContain('3'));
 expect(within(group).getByRole('button',{name:/Chờ duyệt/}).textContent).toContain('7');
 expect(within(group).getByRole('button',{name:/Đã loại/}).textContent).toContain('2');
 expect(within(group).queryByRole('button',{name:/Tất cả/})).toBeNull();
 expect(within(group).getAllByRole('button')).toHaveLength(3);
 expect(within(group).getByRole('button',{name:/Chờ duyệt/}).getAttribute('aria-pressed')).toBe('true');
 fireEvent.click(within(group).getByRole('button',{name:/Đã duyệt/}));
 await waitFor(()=>expect(a.overview).toHaveBeenCalledWith('approved','all','',0));
 await waitFor(()=>expect(within(group).getByRole('button',{name:/Đã duyệt/}).getAttribute('aria-pressed')).toBe('true'));
 expect(within(group).getByRole('button',{name:/Chờ duyệt/}).getAttribute('aria-pressed')).toBe('false');
 expect(screen.queryByRole('button',{name:'Duyệt toàn bộ'})).toBeNull();
});

test('Fine-tune tab shows shortage instead of the backend "need 1000" message',async()=>{
 const a=overviewApi();render(<ReviewOverview api={a as never}/>);
 fireEvent.click(screen.getByRole('tab',{name:'Fine-tune'}));
 await screen.findByText('Còn thiếu 20 cặp');
 expect(screen.queryByText(/Cần 1000 cặp/)).toBeNull();
 expect((screen.getByRole('button',{name:'Fine-tune'}) as HTMLButtonElement).disabled).toBe(true);
});

test('queue rows of Fine-tune open a read-only detail dialog',async()=>{
 const a={...overviewApi(),overviewDetail:vi.fn().mockResolvedValue({...row('i1'),item:{candidates:[{id:'c1',text:'Đáp án 1'},{id:'c2',text:'Đáp án 2'}]},judgments:[],adjudication:{outcome:'winner',reason:'ok'}})};
 render(<TrainingWorkspace api={a as never}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Xem'}));
 const dlg=await screen.findByRole('dialog',{name:'Chi tiết phản hồi'});
 await within(dlg).findByText('Đáp án 1');
 expect(within(dlg).queryByRole('radio')).toBeNull();
 expect(within(dlg).queryByRole('button',{name:/Duyệt/})).toBeNull();
});

test('detail dialog renders markdown for question and answers (no raw code fences)',async()=>{
 const detail={...row('i1','x'),question:'Hỏi:\n```python\nprint(1)\n```',item:{candidates:[{id:'c1',text:'A:\n```sh\nls -la\n```'},{id:'c2',text:'**đậm**'}]},judgments:[],adjudication:{outcome:'winner',reason:'ok'}};
 const api={overviewDetail:vi.fn().mockResolvedValue(detail)};
 render(<ReadOnlyItemDialog api={api as never} id="i1" onClose={()=>{}}/>);
 const dlg=await screen.findByRole('dialog');
 await waitFor(()=>expect(within(dlg).getByText('đậm').tagName).toBe('STRONG'));
 expect(dlg.textContent).not.toContain('```');
});

test('"N phiếu" is a neutral chip, never labelled as conflict',()=>{
 render(<><ChoiceChip value="2 phiếu"/><ChoiceChip value="tie"/><ChoiceChip value="A"/></>);
 expect(screen.getByText('2 phiếu')).toBeTruthy();expect(screen.queryByText(/Mâu thuẫn/)).toBeNull();
 expect(screen.getByText('Ngang nhau')).toBeTruthy();
});

test('retry after a failed sample list reloads the list instead of spinning forever',async()=>{
 const item={id:'i1',prompt_messages:[{role:'user',content:'Mẫu 1'}],candidates:[{id:'c1',text:'a'},{id:'c2',text:'b'}],adjudication:{winning_candidate_id:'c1',reason:'ok'},judgments:[]};
 const api={batch:vi.fn().mockResolvedValue({id:'b',status:'succeeded',sample_count:1,snapshot_sha256:'x',runs:[]}),batchItems:vi.fn().mockRejectedValueOnce(new Error('boom')).mockResolvedValue({items:[item],total:1}),runEvents:vi.fn().mockResolvedValue({items:[],next_seq:0})};
 render(<TrainingBatchDetail api={api as never} id="b" onClose={()=>{}}/>);
 const alert=await screen.findByRole('alert');
 fireEvent.click(within(alert).getByRole('button',{name:'Thử lại'}));
 await screen.findByText('Mẫu 1');
 expect(api.batchItems).toHaveBeenCalledTimes(2);
 expect(screen.queryByRole('alert')).toBeNull();
});

test('disabled save explains why (invalid / unchanged)',async()=>{
 const api={liveSettings:vi.fn().mockResolvedValue({sample_percent:10,cooldown_seconds:600}),setLiveSettings:vi.fn()};
 render(<ReviewSamplingSettings api={api as never}/>);
 await screen.findByText('Tỷ lệ mời A/B: 10%');
 expect(screen.getByText('Chưa thay đổi so với giá trị hiện tại.')).toBeTruthy();
 fireEvent.change(screen.getByRole('spinbutton'),{target:{value:'150'}});
 expect(screen.getByText('Nhập số nguyên từ 0 đến 100.')).toBeTruthy();
 expect(screen.getByRole('button',{name:'Lưu tỷ lệ'}).hasAttribute('disabled')).toBe(true);
});

test('settings load failure shows alert with retry outside the closed details',async()=>{
 const api={liveSettings:vi.fn().mockRejectedValueOnce(new Error('x')).mockResolvedValue({sample_percent:20,cooldown_seconds:600})};
 render(<ReviewSamplingSettings api={api as never}/>);
 const alert=await screen.findByRole('alert');
 expect(alert.closest('details')).toBeNull();
 expect(screen.getByText('Tỷ lệ mời A/B · chưa tải được')).toBeTruthy();
 fireEvent.click(within(alert).getByRole('button',{name:'Thử lại'}));
 await screen.findByText('Tỷ lệ mời A/B: 20%');
});

test('submit button of the A/B pair states the reason while disabled',()=>{
 const assignment={id:'a',external_id:'a',prompt_messages:[{role:'user',content:'Q'}],evidence:'',left:{text:'A'},right:{text:'B'},rubric:'',judgment:null} as Assignment;
 render(<ReviewPair assignment={assignment} showContext={false} focusHeading={false} closed={false} saving={false} error='' onSubmit={()=>{}}/>);
 expect(screen.getByText('Chọn kết quả đánh giá để nộp phiếu.')).toBeTruthy();
 fireEvent.click(screen.getByRole('radio',{name:'A tốt hơn'}));
 expect(screen.queryByText('Chọn kết quả đánh giá để nộp phiếu.')).toBeNull();
});

test('dialog locks background scroll, closes on Esc, restores scroll and focus',()=>{
 const onClose=vi.fn();
 function Harness({open}:{open:boolean}){return <div className="ab-body"><button id="opener">mở</button>{open&&<AbDialog label="Thử" title="Thử" onClose={onClose}><button>trong</button></AbDialog>}</div>;}
 const {rerender,container}=render(<Harness open={false}/>);
 const body=container.querySelector('.ab-body') as HTMLElement,opener=screen.getByText('mở');opener.focus();
 rerender(<Harness open={true}/>);
 expect(body.style.overflow).toBe('hidden');
 expect(document.activeElement?.getAttribute('role')).toBe('dialog');
 fireEvent.keyDown(document,{key:'Escape'});expect(onClose).toHaveBeenCalledTimes(1);
 rerender(<Harness open={false}/>);
 expect(body.style.overflow).toBe('');
 expect(document.activeElement).toBe(opener);
});

test('dialog Tab trap skips controls inside closed details',()=>{
 render(<AbDialog label="T" title="T" onClose={()=>{}}><details><summary>s</summary><button>hidden-inside</button></details><button>last</button></AbDialog>);
 const last=screen.getByRole('button',{name:'last'});last.focus();
 fireEvent.keyDown(document,{key:'Tab'});
 expect(document.activeElement).toBe(screen.getByRole('button',{name:'Đóng'}));
});

test('approval notice stays visible after switching to the Fine-tune tab and can be dismissed',async()=>{
 const detail={...row('i1'),user_winner_id:'c1',item:{candidates:[{id:'c1',text:'Đáp án 1'},{id:'c2',text:'Đáp án 2'}]},judgments:[],adjudication:null};
 const a={...overviewApi(),overviewDetail:vi.fn().mockResolvedValue(detail),adjudicate:vi.fn().mockResolvedValue({})};
 render(<ReviewOverview api={a as never}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Xem và duyệt'}));
 const dlg=await screen.findByRole('dialog',{name:'Chi tiết phản hồi'});
 const approve=within(dlg).getByRole('button',{name:'Duyệt và chuyển sang Fine-tune'});
 expect((approve as HTMLButtonElement).disabled).toBe(true);
 expect(within(dlg).getByText('Chọn một đáp án để duyệt.')).toBeTruthy();
 fireEvent.click(within(dlg).getAllByRole('radio')[0]);
 fireEvent.click(approve);
 await screen.findByText('Đã duyệt. Mẫu đã chuyển sang Fine-tune.');
 fireEvent.click(screen.getByRole('tab',{name:'Fine-tune'}));
 expect(screen.getByText('Đã duyệt. Mẫu đã chuyển sang Fine-tune.')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'Đóng thông báo'}));
 expect(screen.queryByText('Đã duyệt. Mẫu đã chuyển sang Fine-tune.')).toBeNull();
});

test('pagination only appears above 20 rows',()=>{
 const a=overviewApi();a.overview.mockImplementation(async()=>({items:[row('i1')],total:15}));
 const {container}=render(<ReviewOverview api={a as never}/>);
 return screen.findByText('Câu hỏi i1').then(()=>expect(container.querySelector('.ab-pagination')).toBeNull());
});

test('Fine-tune shows queue and history together (two columns), compact empty states',async()=>{
 const a={...overviewApi(),overview:vi.fn().mockResolvedValue({items:[],total:0}),batches:vi.fn().mockResolvedValue({items:[{id:'abcdef123456',status:'failed',created_at:'2026-10-06',sample_count:1000,runs:[]}],total:1})};
 render(<TrainingWorkspace api={a as never}/>);
 await screen.findByText('abcdef12');
 expect(screen.getByRole('heading',{name:'Hàng chờ'})).toBeTruthy();
 expect(screen.getByRole('heading',{name:'Lịch sử fine-tune'})).toBeTruthy();
 expect(screen.getByRole('button',{name:'Xem batch'})).toBeTruthy();
 expect(screen.getByText('Chưa có mẫu đã duyệt đang chờ.')).toBeTruthy();
});

test('Fine-tune load error hides the empty cards and keeps one alert with retry',async()=>{
 const a={...overviewApi(),eligibility:vi.fn().mockRejectedValue(new Error('x')),batches:vi.fn().mockRejectedValue(new Error('x'))};
 render(<TrainingWorkspace api={a as never}/>);
 const alert=await screen.findByRole('alert');
 expect(within(alert).getByRole('button',{name:'Thử lại'})).toBeTruthy();
 expect(screen.queryByRole('heading',{name:'Hàng chờ'})).toBeNull();
 expect(screen.queryByRole('heading',{name:'Lịch sử fine-tune'})).toBeNull();
});

test('tablist and status tiles support arrow keys with roving tabindex',async()=>{
 const a=overviewApi();render(<ReviewOverview api={a as never}/>);
 const feedback=await screen.findByRole('tab',{name:'Phản hồi'}),training=screen.getByRole('tab',{name:'Fine-tune'});
 expect(feedback.getAttribute('tabindex')).toBe('0');expect(training.getAttribute('tabindex')).toBe('-1');
 feedback.focus();fireEvent.keyDown(feedback,{key:'ArrowRight'});
 await waitFor(()=>expect(screen.getByRole('tab',{name:'Fine-tune'}).getAttribute('aria-selected')).toBe('true'));
 expect(document.activeElement).toBe(screen.getByRole('tab',{name:'Fine-tune'}));
 fireEvent.keyDown(document.activeElement as HTMLElement,{key:'ArrowLeft'});
 await waitFor(()=>expect(screen.getByRole('tab',{name:'Phản hồi'}).getAttribute('aria-selected')).toBe('true'));
 const group=await screen.findByRole('group',{name:'Lọc theo trạng thái'});
 const pending=within(group).getByRole('button',{name:/Chờ duyệt/});pending.focus();
 fireEvent.keyDown(pending,{key:'ArrowRight'});
 expect(document.activeElement).toBe(within(group).getByRole('button',{name:/Đã duyệt/}));
 expect(within(group).getByRole('button',{name:/Đã duyệt/}).getAttribute('aria-pressed')).toBe('false');
});

test('backdrop click does not close a dialog with unsaved input, Esc still does',()=>{
 const onClose=vi.fn();
 const {container,rerender}=render(<AbDialog label="d" title="d" dirty onClose={onClose}><textarea/></AbDialog>);
 fireEvent.mouseDown(container.querySelector('.ab-overlay') as HTMLElement);
 expect(onClose).not.toHaveBeenCalled();
 rerender(<AbDialog label="d" title="d" onClose={onClose}><textarea/></AbDialog>);
 fireEvent.mouseDown(container.querySelector('.ab-overlay') as HTMLElement);
 expect(onClose).toHaveBeenCalledTimes(1);
});

test('bulk approve is blocked with a reason once preview reports nothing eligible',async()=>{
 const a=overviewApi();a.bulkPreview.mockResolvedValue({eligible_count:0,skipped_count:3,snapshot_version:'v'});
 render(<ReviewOverview api={a as never}/>);
 const btn=await screen.findByRole('button',{name:'Duyệt toàn bộ'});
 fireEvent.click(btn);
 await screen.findByText(/Không có câu nào đủ điều kiện duyệt hàng loạt \(3 câu cần duyệt riêng\)/);
 expect((screen.getByRole('button',{name:'Duyệt toàn bộ'}) as HTMLButtonElement).disabled).toBe(true);
 expect(screen.queryByRole('dialog')).toBeNull();
});

function detailWith(judgments:unknown[],extra:Record<string,unknown>={}){
 return {...row('i1'),item:{candidates:[{id:'c1',text:'Đáp án 1'},{id:'c2',text:'Đáp án 2'}]},judgments,adjudication:{outcome:'winner',reason:'ok'},...extra};
}
async function openVotes(judgments:unknown[],extra:Record<string,unknown>={}){
 const api={overviewDetail:vi.fn().mockResolvedValue(detailWith(judgments,extra))};
 render(<ReadOnlyItemDialog api={api as never} id="i1" onClose={()=>{}}/>);
 const dlg=await screen.findByRole('dialog',{name:'Chi tiết phản hồi'});
 return {dlg,votes:await within(dlg).findByRole('group',{name:'Lựa chọn của user'})};
}

test('detail shows a single vote in the main area (not inside the closed Chi tiết fold)',async()=>{
 const {votes}=await openVotes([{id:'j1',reviewer_id:'canary-user-1',winning_candidate_id:'c2',decision:'right',comment:'B rõ ràng hơn',submitted_at:'2026-10-06T08:00:00Z'}],{user_winner_id:'c2'});
 expect(votes.closest('details')).toBeNull();
 expect(within(votes).getByText('canary-user-1')).toBeTruthy();
 expect(within(votes).getByText('Chọn B')).toBeTruthy();
 expect(within(votes).getByText('B rõ ràng hơn')).toBeTruthy();
 expect(votes.querySelector('time')?.textContent).toContain('2026');
 expect(within(votes).getByText('1 phiếu')).toBeTruthy();
 expect(screen.getAllByText('User đã chọn')).toHaveLength(1);
});

test('detail lists every vote of a multi-vote sample compactly',async()=>{
 const {votes}=await openVotes([{id:'j1',reviewer_id:'u1',winning_candidate_id:'c1',decision:'left',comment:''},{id:'j2',reviewer_id:'u2',winning_candidate_id:null,decision:'tie',comment:'như nhau'},{id:'j3',reviewer_id:'u3',winning_candidate_id:null,decision:'both_bad',comment:''}],{user_winner_id:null});
 expect(within(votes).getAllByRole('listitem')).toHaveLength(3);
 expect(within(votes).getByText('Chọn A')).toBeTruthy();
 expect(within(votes).getByText('Ngang nhau')).toBeTruthy();
 expect(within(votes).getByText('Cả hai kém')).toBeTruthy();
 expect(within(votes).getByText('3 phiếu')).toBeTruthy();
 expect(screen.queryByText('User đã chọn')).toBeNull();
});

test('detail without votes says so; a skip vote shows "Bỏ qua"',async()=>{
 cleanup();
 const none=await openVotes([]);
 expect(within(none.votes).getByText('Chưa có phiếu')).toBeTruthy();
 expect(within(none.votes).queryByRole('listitem')).toBeNull();
 cleanup();
 const skip=await openVotes([{id:'j1',reviewer_id:'u1',winning_candidate_id:null,decision:'skip',comment:''}]);
 expect(within(skip.votes).getByText('Bỏ qua')).toBeTruthy();
});

test('the pending detail dialog of ReviewOverview shows votes up front too',async()=>{
 const detail=detailWith([{id:'j1',reviewer_id:'u1',winning_candidate_id:'c1',decision:'left',comment:''}],{user_winner_id:'c1',adjudication:null});
 const a={...overviewApi(),overviewDetail:vi.fn().mockResolvedValue(detail)};
 render(<ReviewOverview api={a as never}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Xem và duyệt'}));
 const dlg=await screen.findByRole('dialog',{name:'Chi tiết phản hồi'});
 const votes=within(dlg).getByRole('group',{name:'Lựa chọn của user'});
 expect(votes.closest('details')).toBeNull();
 expect(within(votes).getByText('Chọn A')).toBeTruthy();
});
