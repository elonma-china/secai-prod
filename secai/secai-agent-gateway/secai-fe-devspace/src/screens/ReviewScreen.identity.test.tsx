// @vitest-environment jsdom
import {cleanup,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
const api=vi.hoisted(()=>({me:vi.fn().mockResolvedValue({actor_id:'admin',role:'admin'}),campaigns:vi.fn().mockResolvedValue([]),overview:vi.fn().mockResolvedValue({items:[],total:0,offset:0,limit:20}),liveSettings:vi.fn().mockResolvedValue({sample_percent:10,cooldown_seconds:600,scope:"global"})}));
vi.mock('../reviewApi',()=>({createReviewApi:()=>api,reviewError:()=> 'Không kết nối được'}));
import ReviewScreen from './ReviewScreen';
import {clearReviewIdentity,setReviewIdentity} from '../reviewIdentity';
afterEach(()=>{cleanup();clearReviewIdentity();vi.clearAllMocks();});
it('reads the app identity without exposing a second role selector',async()=>{
 setReviewIdentity('devspace:admin',{actor_id:'admin',role:'admin'});
 render(<ReviewScreen/>);
 await screen.findByText('Không có phản hồi trong danh sách này.');
 expect(screen.queryByRole('button',{name:'Chuyên gia',hidden:true})).toBeNull();
 expect(api.me).not.toHaveBeenCalled();
});

it('does not show or load global AB settings for a reviewer',async()=>{
 setReviewIdentity('devspace:reviewer',{actor_id:'user',role:'reviewer'});
 render(<ReviewScreen/>);
 await screen.findByText('Chưa có đợt đánh giá được phân công cho bạn.');
 expect(screen.queryByText(/Tỷ lệ mời A\/B/)).toBeNull();
 expect(api.liveSettings).not.toHaveBeenCalled();
});
