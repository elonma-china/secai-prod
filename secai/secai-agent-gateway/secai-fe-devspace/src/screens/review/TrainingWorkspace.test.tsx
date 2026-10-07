// @vitest-environment jsdom
// Round 3: queue and history are shown side by side (no 'Hàng chờ'/'Lịch sử' switch), so the test no longer clicks 'Hàng chờ' to reload.
import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';import {afterEach,expect,test,vi} from 'vitest';
import TrainingWorkspace from './TrainingWorkspace';afterEach(cleanup);
test('reconciled lost response clears request key before next dataset',async()=>{
 let oldKey='';const a={batches:vi.fn(async()=>({items:oldKey?[{id:'old-batch',request_key:oldKey,status:'failed',created_at:'2026-10-06',sample_count:1000,runs:[]}]:[],total:oldKey?1:0})),eligibility:vi.fn().mockResolvedValue({valid_new_count:1000,minimum_pairs:1000,eligible:true,blocked_reasons:[],snapshot_version:'snapshot'}),overview:vi.fn().mockResolvedValue({items:[],total:0}),freezeBatch:vi.fn(async(key:string)=>{oldKey=key;throw new Error('lost response');})};
 render(<TrainingWorkspace api={a as never}/>);await screen.findByText('1.000 / 1.000 cặp sẵn sàng');fireEvent.click(screen.getByRole('button',{name:'Fine-tune'}));fireEvent.click(screen.getByRole('button',{name:'Xác nhận fine-tune'}));await screen.findByText('old-batc');await waitFor(()=>expect(a.batches).toHaveBeenCalledTimes(2));
 await screen.findByText('1.000 / 1.000 cặp sẵn sàng');await waitFor(()=>expect((screen.getByRole('button',{name:'Fine-tune'}) as HTMLButtonElement).disabled).toBe(false));fireEvent.click(screen.getByRole('button',{name:'Fine-tune'}));fireEvent.click(screen.getByRole('button',{name:'Xác nhận fine-tune'}));await waitFor(()=>expect(a.freezeBatch).toHaveBeenCalledTimes(2));expect(a.freezeBatch.mock.calls[0][0]).not.toBe(a.freezeBatch.mock.calls[1][0]);
});
