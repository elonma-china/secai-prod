// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {StrictMode} from 'react';
import {afterEach,expect,test,vi} from 'vitest';

const mocks=vi.hoisted(()=>({livePair:vi.fn()}));
vi.mock('../../reviewApi',()=>({createReviewApi:()=>mocks,reviewError:()=>"Request failed"}));
import LiveReviewWidget from './LiveReviewWidget';
import ReviewOverview from './ReviewOverview';
import {clearReviewIdentity,setReviewIdentity} from '../../reviewIdentity';

const turn={id:'turn-1',prompt_messages:[{role:'user' as const,content:'Question'}],answer:'Original',source_group:'chat'};
const assignment={id:'assignment-1',external_id:'turn-1',prompt_messages:turn.prompt_messages,evidence:'',
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

test('a failed opted-in pair shows an error instead of silently disappearing',async()=>{
  setReviewIdentity('devspace:reviewer',{actor_id:'reviewer-1',role:'reviewer'});
  mocks.livePair.mockResolvedValue({offered:false});
  render(<LiveReviewWidget turn={turn}/>);
  expect(await screen.findByRole('alert')).toBeTruthy();
});

test('a failed pair can be retried without sending the chat again',async()=>{
  setReviewIdentity('devspace:reviewer',{actor_id:'reviewer-1',role:'reviewer'});
  mocks.livePair.mockResolvedValueOnce({offered:false}).mockResolvedValueOnce({offered:true,assignment});
  render(<LiveReviewWidget turn={turn}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Thử lại A/B'}));
  expect((await screen.findAllByText('Answer A')).length).toBeGreaterThan(0);
  expect((await screen.findAllByText('Answer B')).length).toBeGreaterThan(0);
});

test('a turn received before reviewer identity is checked once identity arrives',async()=>{
  clearReviewIdentity();
  mocks.livePair.mockResolvedValue({offered:true,assignment});
  render(<LiveReviewWidget turn={turn}/>);
  expect(mocks.livePair).not.toHaveBeenCalled();

  await act(async()=>{setReviewIdentity('devspace:reviewer',{actor_id:'reviewer-1',role:'reviewer'});});

  await waitFor(()=>expect(mocks.livePair).toHaveBeenCalledTimes(1));
  expect((await screen.findAllByText('Answer A')).length).toBeGreaterThan(0);
});

test('StrictMode preserves one offered pair request through effect replay',async()=>{
  setReviewIdentity('devspace:reviewer',{actor_id:'reviewer-1',role:'reviewer'});
  const pending=deferred<{offered:boolean;assignment:typeof assignment}>();
  mocks.livePair.mockReturnValue(pending.promise);
  render(<StrictMode><LiveReviewWidget turn={turn}/></StrictMode>);

  await waitFor(()=>expect(mocks.livePair).toHaveBeenCalledTimes(1));
  await act(async()=>{pending.resolve({offered:true,assignment});});

  expect((await screen.findAllByText('Answer A')).length).toBeGreaterThan(0);
  expect(mocks.livePair).toHaveBeenCalledTimes(1);
});

test('an old identity response cannot install a pair after identity changes',async()=>{
  setReviewIdentity('devspace:old-reviewer',{actor_id:'old-reviewer',role:'reviewer'});
  const oldResponse=deferred<{offered:boolean;assignment:typeof assignment}>();
  const newResponse=deferred<{offered:boolean;assignment:typeof assignment}>();
  mocks.livePair.mockReturnValueOnce(oldResponse.promise).mockReturnValueOnce(newResponse.promise);
  render(<LiveReviewWidget turn={turn}/>);
  await waitFor(()=>expect(mocks.livePair).toHaveBeenCalledTimes(1));

  await act(async()=>{setReviewIdentity('devspace:new-reviewer',{actor_id:'new-reviewer',role:'reviewer'});});
  await waitFor(()=>expect(mocks.livePair).toHaveBeenCalledTimes(2));
  const oldAssignment={...assignment,id:'old-assignment',left:{text:'Old identity answer'}};
  const newAssignment={...assignment,id:'new-assignment',left:{text:'New identity answer'}};
  await act(async()=>{oldResponse.resolve({offered:true,assignment:oldAssignment});});

  expect(screen.queryByText('Old identity answer')).toBeNull();
  expect(screen.queryByText('New identity answer')).toBeNull();

  await act(async()=>{newResponse.resolve({offered:true,assignment:newAssignment});});
  expect((await screen.findAllByText('New identity answer')).length).toBeGreaterThan(0);
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
