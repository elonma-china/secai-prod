// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';

const mocks=vi.hoisted(()=>({submit:vi.fn()}));
vi.mock('../../reviewApi',()=>({createReviewApi:()=>mocks,reviewError:()=>"Request failed"}));
import LiveReviewWidget from './LiveReviewWidget';
import ReviewOverview from './ReviewOverview';
import {clearReviewIdentity,setReviewIdentity} from '../../reviewIdentity';

const promptMessages=[{role:'user' as const,content:'Question'}];
const assignment={id:'assignment-1',campaign_id:'campaign-1',campaign_title:'Live chat',status:'pending' as const,
  external_id:'turn-1',prompt_messages:promptMessages,evidence:'',
  left:{text:'Answer A'},right:{text:'Answer B'},rubric:'',rubric_version:'v1',judgment:null};
const row={id:'item-1',question:'Old question',user_choice:'A',submitted_at:'2026-10-06',complete:true,campaign_state:'open',source:'live'};
const detail={id:'item-1',question:'Old question',source:'live',vote_count:1,complete:true,campaign_state:'open',
  user_winner_id:null,item:{candidates:[{id:'c1',text:'Old answer A'},{id:'c2',text:'Old answer B'}]},judgments:[],adjudication:null};

function deferred<T>() {
  let resolve!:(value:T)=>void;
  const promise=new Promise<T>(r=>{resolve=r;});
  return {promise,resolve};
}

function api(overrides:Record<string,unknown>={}) {
  return {overview:vi.fn().mockResolvedValue({items:[row],total:21}),
    overviewDetail:vi.fn().mockResolvedValue(detail),
    bulkPreview:vi.fn().mockResolvedValue({eligible_count:1,skipped_count:0,snapshot_version:'v1'}),
    bulkApprove:vi.fn().mockResolvedValue({approved:1,skipped:0}),
    adjudicate:vi.fn().mockResolvedValue({}),
    liveSettings:vi.fn().mockResolvedValue({sample_percent:10,cooldown_seconds:600}),
    eligibility:vi.fn().mockResolvedValue({valid_new_count:0,minimum_pairs:1000,eligible:false,blocked_reasons:['Cần 1000 cặp.']}),
    batches:vi.fn().mockResolvedValue({items:[],total:0}),...overrides};
}

afterEach(()=>{cleanup();clearReviewIdentity();vi.restoreAllMocks();vi.clearAllMocks();});

// LiveReviewWidget no longer fetches /live-pairs itself — ChatbotScreen now runs
// /live-pairs/decide + /live-pairs/alternative in parallel with its own chat request (so B
// doesn't wait for A to finish) and hands this widget the resulting state. These tests cover
// only what the widget itself still owns: rendering that state, and submitting a judgment.

test('no state renders nothing',()=>{
  const {container}=render(<LiveReviewWidget state={undefined}/>);
  expect(container.firstChild).toBeNull();
});

test('pending state shows a waiting message, not the pair yet',()=>{
  render(<LiveReviewWidget state={{status:'pending'}}/>);
  expect(screen.getByRole('status').textContent).toMatch(/Đang tạo hai phương án/);
  expect(screen.queryByText('Answer A')).toBeNull();
});

test('error state shows an alert with a retry button that calls back into the caller',()=>{
  const retry=vi.fn();
  render(<LiveReviewWidget state={{status:'error',retry}}/>);
  expect(screen.getByRole('alert')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'Thử lại A/B'}));
  expect(retry).toHaveBeenCalledTimes(1);
});

test('ready state renders both candidates for voting',()=>{
  setReviewIdentity('devspace:reviewer',{actor_id:'reviewer-1',role:'reviewer'});
  render(<LiveReviewWidget state={{status:'ready',assignment}}/>);
  expect((screen.getAllByText('Answer A')).length).toBeGreaterThan(0);
  expect((screen.getAllByText('Answer B')).length).toBeGreaterThan(0);
});

test('submitting a judgment from the ready state uses the current identity token',async()=>{
  setReviewIdentity('devspace:reviewer',{actor_id:'reviewer-1',role:'reviewer'});
  mocks.submit.mockResolvedValue({decision:'left',error_tags:{left:[],right:[]},comment:''});
  render(<LiveReviewWidget state={{status:'ready',assignment}}/>);
  fireEvent.click(screen.getByLabelText('A tốt hơn'));
  fireEvent.click(screen.getByRole('button',{name:'Nộp phiếu đánh giá'}));
  await waitFor(()=>expect(mocks.submit).toHaveBeenCalledWith('assignment-1',expect.objectContaining({decision:'left'})));
  expect((await screen.findByRole('status')).textContent).toMatch(/Đã nộp phiếu/);
});

test('a detail response opened under an old source filter is ignored',async()=>{
  const pending=deferred<typeof detail>();
  const a=api({overviewDetail:vi.fn(()=>pending.promise)});
  render(<ReviewOverview api={a as never}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Xem và duyệt'}));
  const source=screen.getByRole('combobox',{name:'Nguồn'});
  fireEvent.change(source,{target:{value:'qa'}});
  await waitFor(()=>expect(a.overview).toHaveBeenCalledWith('pending','qa','',0));

  await act(async()=>{pending.resolve(detail);});

  expect(screen.queryByRole('dialog',{name:'Chi tiết phản hồi'})).toBeNull();
  expect(screen.queryByText('Old answer A')).toBeNull();
});

const staleDetailActions=[
  ['status',async()=>fireEvent.click(screen.getByRole('button',{name:/Đã duyệt/}))],
  ['search',async()=>{fireEvent.change(screen.getByRole('textbox',{name:'Tìm câu hỏi'}),{target:{value:'new'}});fireEvent.click(screen.getByRole('button',{name:'Tìm'}));}],
  ['page',async()=>fireEvent.click(screen.getByRole('button',{name:'Sau'}))],
  ['tab',async()=>{fireEvent.click(screen.getByRole('tab',{name:'Fine-tune'}));fireEvent.click(screen.getByRole('tab',{name:'Phản hồi'}));}],
  ['unmount',async(view:{unmount:()=>void})=>view.unmount()],
] as const;

test.each(staleDetailActions)('a stale detail response is ignored after %s',async(_name,change)=>{
  const pending=deferred<typeof detail>();
  const a=api({overviewDetail:vi.fn(()=>pending.promise)});
  const view=render(<ReviewOverview api={a as never}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Xem và duyệt'}));
  await change(view);
  await act(async()=>{pending.resolve(detail);});

  expect(screen.queryByRole('dialog',{name:'Chi tiết phản hồi'})).toBeNull();
  expect(screen.queryByText('Old answer A')).toBeNull();
});

const staleBulkActions=[
  ['source',async()=>fireEvent.change(screen.getByRole('combobox',{name:'Nguồn'}),{target:{value:'qa'}})],
  ['status',async()=>fireEvent.click(screen.getByRole('button',{name:/Đã duyệt/}))],
  ['search',async()=>{fireEvent.change(screen.getByRole('textbox',{name:'Tìm câu hỏi'}),{target:{value:'new'}});fireEvent.click(screen.getByRole('button',{name:'Tìm'}));}],
  ['page',async()=>fireEvent.click(screen.getByRole('button',{name:'Sau'}))],
  ['tab',async()=>{fireEvent.click(screen.getByRole('tab',{name:'Fine-tune'}));fireEvent.click(screen.getByRole('tab',{name:'Phản hồi'}));}],
  ['unmount',async(view:{unmount:()=>void})=>view.unmount()],
] as const;

test.each(staleBulkActions)('a stale bulk preview is ignored after %s',async(_name,change)=>{
  const pending=deferred<{eligible_count:number;skipped_count:number;snapshot_version:string}>();
  const a=api({bulkPreview:vi.fn(()=>pending.promise)});
  const view=render(<ReviewOverview api={a as never}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Duyệt toàn bộ'}));
  await waitFor(()=>expect(a.bulkPreview).toHaveBeenCalledTimes(1));
  await change(view);
  await act(async()=>{pending.resolve({eligible_count:1,skipped_count:0,snapshot_version:'stale'});});

  expect(screen.queryByRole('dialog',{name:/Xác nhận duyệt/})).toBeNull();
});

test('dirty review detail asks before discard and preserves the draft when declined',async()=>{
  const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
  const a=api();
  render(<ReviewOverview api={a as never}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Xem và duyệt'}));
  const dialog=await screen.findByRole('dialog',{name:'Chi tiết phản hồi'});
  const winner=within(dialog).getAllByLabelText('Chọn đáp án này')[0];
  fireEvent.click(winner);
  fireEvent.change(within(dialog).getByLabelText('Nhận xét (tùy chọn)'),{target:{value:'Keep this draft'}});

  fireEvent.keyDown(document,{key:'Escape'});
  expect(confirm).toHaveBeenLastCalledWith('Bỏ các thay đổi chưa lưu?');
  expect(screen.getByRole('dialog',{name:'Chi tiết phản hồi'})).toBeTruthy();
  expect((within(dialog).getByLabelText('Nhận xét (tùy chọn)') as HTMLTextAreaElement).value).toBe('Keep this draft');
  expect((winner as HTMLInputElement).checked).toBe(true);

  fireEvent.click(within(dialog).getByRole('button',{name:'Đóng'}));
  expect(confirm).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('dialog',{name:'Chi tiết phản hồi'})).toBeTruthy();

  confirm.mockReturnValue(true);
  fireEvent.click(within(dialog).getByRole('button',{name:'Đóng'}));
  await waitFor(()=>expect(screen.queryByRole('dialog',{name:'Chi tiết phản hồi'})).toBeNull());
});

test('busy dirty review detail ignores Escape and close without prompting',async()=>{
  const confirm=vi.spyOn(window,'confirm').mockReturnValue(true);
  const saving=deferred<unknown>();
  const a=api({adjudicate:vi.fn(()=>saving.promise)});
  render(<ReviewOverview api={a as never}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Xem và duyệt'}));
  const dialog=await screen.findByRole('dialog',{name:'Chi tiết phản hồi'});
  const winner=within(dialog).getAllByLabelText('Chọn đáp án này')[0];
  fireEvent.click(winner);
  fireEvent.change(within(dialog).getByLabelText('Nhận xét (tùy chọn)'),{target:{value:'Keep while saving'}});
  fireEvent.click(within(dialog).getByRole('button',{name:'Duyệt và chuyển sang Fine-tune'}));
  await waitFor(()=>expect(a.adjudicate).toHaveBeenCalledTimes(1));

  fireEvent.keyDown(document,{key:'Escape'});
  fireEvent.click(within(dialog).getByRole('button',{name:'Đóng'}));

  expect(confirm).not.toHaveBeenCalled();
  expect(screen.getByRole('dialog',{name:'Chi tiết phản hồi'})).toBeTruthy();
  expect((within(dialog).getByLabelText('Nhận xét (tùy chọn)') as HTMLTextAreaElement).value).toBe('Keep while saving');
  await act(async()=>{saving.resolve({});});
});
