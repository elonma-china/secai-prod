// @vitest-environment jsdom
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import ReviewAdmin from './ReviewAdmin';
import type {ReviewApi} from '../../reviewApi';
import type {Campaign, ReviewItem} from '../../reviewTypes';
afterEach(cleanup);
const campaign:Campaign={id:'chat',title:'Đánh giá từ chat',state:'open',rubric_version:'security_qa_v1'};
const pending:ReviewItem={id:'pending',external_id:'internal-uuid',prompt_messages:[{role:'user',content:'MFA bảo vệ tài khoản thế nào?'}],candidates:[{id:'original',text:'Cần thêm yếu tố xác thực.',provenance:{}},{id:'generated',text:'Có thể bỏ mật khẩu.',provenance:{}}],evidence:'',assignment_count:1,submitted_count:1,adjudication:null,disagreement:false};
function fixture(){
 const approved={...pending,id:'approved',external_id:'approved-id',prompt_messages:[{role:'user',content:'Câu đã duyệt'}],adjudication:{id:'decision',outcome:'winner',winning_candidate_id:'original',reason:'Đã đối chiếu'}};
 const excluded={...approved,id:'excluded',adjudication:{...approved.adjudication,outcome:'tie',winning_candidate_id:null}};
 const api={trainingJobs:vi.fn().mockResolvedValue({enabled:false,purpose:'demo',jobs:[]}),items:vi.fn().mockResolvedValue([pending,approved,excluded]),stats:vi.fn().mockResolvedValue({items:3,submitted:3,assignments:3,pending_adjudication:1,adjudicated:2,disagreements:0,multi_reviewed_items:0}),reviewers:vi.fn().mockResolvedValue([]),judgments:vi.fn().mockResolvedValue({item_id:'pending',adjudication:null,judgments:[{id:'vote',reviewer_id:'user',winning_candidate_id:'original',decision:'right',candidate_error_tags:{},comment:'Ngắn và dễ hiểu.'}]}),adjudicate:vi.fn().mockResolvedValue({})} as unknown as ReviewApi;
 render(<ReviewAdmin api={api} campaign={campaign} onChange={()=>{}}/>);return api;
}
it('starts with pending questions and counts only approved winner pairs',async()=>{
 fixture();await screen.findByRole('button',{name:/MFA bảo vệ tài khoản/});
 expect(screen.queryByRole('button',{name:/Câu đã duyệt/})).toBeNull();
 expect(screen.getByText('Cặp đã duyệt: 1')).toBeTruthy();
 expect(screen.getByRole('button',{name:'Fine-tune'}).hasAttribute('disabled')).toBe(true);
 expect(screen.queryByRole('button',{name:'Tất cả'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:/Đã duyệt, chờ huấn luyện/}));
 expect(screen.getAllByRole('button',{name:/Câu đã duyệt/})).toHaveLength(1);
});
it('approves the selected canonical candidate without forcing a comment',async()=>{
 const api=fixture();fireEvent.click(await screen.findByRole('button',{name:/MFA bảo vệ tài khoản/}));
 await screen.findByText('Ngắn và dễ hiểu.');
 expect(screen.getByText('User đã chọn')).toBeTruthy();
 fireEvent.click(screen.getByRole('radio',{name:'Dùng câu trả lời B'}));
 expect(screen.getByRole('button',{name:'Xác nhận dùng huấn luyện'}).hasAttribute('disabled')).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Xác nhận dùng huấn luyện'}));
 await waitFor(()=>expect(api.adjudicate).toHaveBeenCalledWith('pending','winner','generated','Admin xác nhận câu trả lời B để dùng huấn luyện.'));
});
it('excludes a sample with a skip decision rather than a fabricated winner',async()=>{
 const api=fixture();fireEvent.click(await screen.findByRole('button',{name:/MFA bảo vệ tài khoản/}));await screen.findByText('Ngắn và dễ hiểu.');
 fireEvent.change(screen.getByLabelText('Nhận xét (tùy chọn)'),{target:{value:'Không phù hợp để huấn luyện.'}});
 fireEvent.click(screen.getByRole('button',{name:'Loại mẫu'}));
 await waitFor(()=>expect(api.adjudicate).toHaveBeenCalledWith('pending','skip',null,'Không phù hợp để huấn luyện.'));
});

it('keeps technical controls collapsed and offers search',async()=>{
 fixture();await screen.findByRole('button',{name:/MFA bảo vệ tài khoản/});
 expect(screen.getByRole('searchbox',{name:'Tìm câu hỏi'})).toBeTruthy();
 expect(screen.getByText('Nâng cao').closest('details')?.open).toBe(false);
 fireEvent.change(screen.getByRole('searchbox',{name:'Tìm câu hỏi'}),{target:{value:'không tồn tại'}});
 expect(screen.queryByRole('button',{name:/MFA bảo vệ tài khoản/})).toBeNull();
});
it('refreshes the user choice shown in the list after loading updated data',async()=>{
 const api=fixture();
 await screen.findByText('User chọn A');
 vi.mocked(api.judgments).mockResolvedValue({item_id:'pending',adjudication:null,judgments:[{id:'updated',assignment_id:'a',reviewer_id:'user',winning_candidate_id:'generated',decision:'left',candidate_error_tags:{},comment:''}]});
 fireEvent.click(screen.getByRole('button',{name:'Làm mới'}));
 await screen.findByText('User chọn B');
});
it('refreshes the open detail and keeps approval locked until the latest votes load',async()=>{
 const api=fixture();
 fireEvent.click(await screen.findByRole('button',{name:/MFA bảo vệ tài khoản/}));
 await screen.findByText('User đã chọn');
 fireEvent.click(screen.getByRole('radio',{name:'Dùng câu trả lời A'}));
 vi.mocked(api.items).mockResolvedValue([{...pending,assignment_count:2,submitted_count:2}]);
 let finish!: (value: Awaited<ReturnType<ReviewApi['judgments']>>) => void;
 const response = new Promise<Awaited<ReturnType<ReviewApi['judgments']>>>(resolve => {finish=resolve;});
 vi.mocked(api.judgments).mockImplementation(()=>response);
 fireEvent.click(screen.getByRole('button',{name:'Làm mới'}));
 await waitFor(()=>expect((screen.getByRole('radio',{name:'Dùng câu trả lời A'}) as HTMLInputElement).disabled || screen.getByRole('radio',{name:'Dùng câu trả lời A'}).closest('fieldset')?.disabled).toBe(true));
 finish({item_id:'pending',adjudication:null,judgments:[
  {id:'v1',assignment_id:'a1',reviewer_id:'u1',decision:'left',winning_candidate_id:'original',candidate_error_tags:{},comment:''},
  {id:'v2',assignment_id:'a2',reviewer_id:'u2',decision:'right',winning_candidate_id:'generated',candidate_error_tags:{},comment:''}
 ]});
 await waitFor(()=>expect(screen.getAllByText('User đã chọn')).toHaveLength(2));
});
