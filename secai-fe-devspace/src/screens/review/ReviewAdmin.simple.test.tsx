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
 expect(screen.getByRole('button',{name:'Huấn luyện model chấm điểm'}).hasAttribute('disabled')).toBe(true);
 fireEvent.click(screen.getByRole('button',{name:'Tất cả'}));
 expect(screen.getAllByRole('button',{name:/Câu đã duyệt/})).toHaveLength(2);
});
it('approves the selected canonical candidate and keeps the reason required',async()=>{
 const api=fixture();fireEvent.click(await screen.findByRole('button',{name:/MFA bảo vệ tài khoản/}));
 await screen.findByText('Ngắn và dễ hiểu.');
 expect(screen.getByText('Người đánh giá chọn: Câu trả lời A')).toBeTruthy();
 fireEvent.click(screen.getByRole('radio',{name:'Dùng câu trả lời B'}));
 expect(screen.getByRole('button',{name:'Duyệt dùng huấn luyện'}).hasAttribute('disabled')).toBe(true);
 fireEvent.change(screen.getByLabelText('Lý do duyệt hoặc loại'),{target:{value:'Đã đối chiếu và sửa lựa chọn.'}});
 fireEvent.click(screen.getByRole('button',{name:'Duyệt dùng huấn luyện'}));
 await waitFor(()=>expect(api.adjudicate).toHaveBeenCalledWith('pending','winner','generated','Đã đối chiếu và sửa lựa chọn.'));
});
it('excludes a sample with a skip decision rather than a fabricated winner',async()=>{
 const api=fixture();fireEvent.click(await screen.findByRole('button',{name:/MFA bảo vệ tài khoản/}));await screen.findByText('Ngắn và dễ hiểu.');
 fireEvent.change(screen.getByLabelText('Lý do duyệt hoặc loại'),{target:{value:'Không phù hợp để huấn luyện.'}});
 fireEvent.click(screen.getByRole('button',{name:'Loại mẫu'}));
 await waitFor(()=>expect(api.adjudicate).toHaveBeenCalledWith('pending','skip',null,'Không phù hợp để huấn luyện.'));
});
