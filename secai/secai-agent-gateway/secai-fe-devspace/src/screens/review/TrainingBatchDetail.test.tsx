// @vitest-environment jsdom
import {render,screen,waitFor,fireEvent,cleanup} from '@testing-library/react';import {afterEach,expect,test,vi} from 'vitest';
import TrainingBatchDetail from './TrainingBatchDetail';afterEach(cleanup);
test('terminal history drains events and allows selecting previous attempt',async()=>{
 const run=(id:string,status:string)=>({id,status,created_at:'2026-10-06',remote:null});
 const api={batch:vi.fn().mockResolvedValue({status:'succeeded',sample_count:20,runs:[run('old','failed'),run('latest','succeeded')]}),batchItems:vi.fn().mockResolvedValue({items:[],total:0}),runEvents:vi.fn(async(id:string,cursor:number)=>({items:cursor===0?Array.from({length:100},(_,i)=>({seq:i+1,time:'2026-10-06',stage:'training'})):cursor===100?[{seq:101,time:'2026-10-06',stage:id==='old'?'failed':'succeeded'}]:[],next_seq:cursor===0?100:101}))};
 render(<TrainingBatchDetail api={api as never} id="batch" onClose={()=>{}}/>);
 await waitFor(()=>expect(api.runEvents).toHaveBeenCalledWith('latest',100));
 fireEvent.change(screen.getByRole('combobox',{name:'Lần chạy'}),{target:{value:'old'}});
 await waitFor(()=>expect(api.runEvents).toHaveBeenCalledWith('old',100));
});

test('shows loading while historical events are still being fetched',async()=>{
 let resolve:(value:unknown)=>void=()=>{};const pending=new Promise(r=>{resolve=r;});
 const api={batch:vi.fn().mockResolvedValue({status:'succeeded',sample_count:1,runs:[{id:'r',status:'succeeded',created_at:'2026-10-06',remote:null}]}),batchItems:vi.fn().mockResolvedValue({items:[],total:0}),runEvents:vi.fn().mockReturnValue(pending)};
 render(<TrainingBatchDetail api={api as never} id="batch" onClose={()=>{}}/>);
 await screen.findByText('Đang tải log…');expect(screen.queryByText('Chưa có sự kiện từ worker.')).toBeNull();
 resolve({items:[],next_seq:0});await screen.findByText('Chưa có sự kiện từ worker.');
});
