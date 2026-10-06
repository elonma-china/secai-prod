// @vitest-environment jsdom
import {cleanup,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
const api=vi.hoisted(()=>({me:vi.fn().mockResolvedValue({actor_id:'admin',role:'admin'}),campaigns:vi.fn().mockResolvedValue([])}));
vi.mock('../reviewApi',()=>({createReviewApi:()=>api,reviewError:()=> 'Không kết nối được'}));
import ReviewScreen from './ReviewScreen';
import {clearReviewIdentity,setReviewIdentity} from '../reviewIdentity';
afterEach(()=>{cleanup();clearReviewIdentity();vi.clearAllMocks();});
it('reads the app identity without exposing a second role selector',async()=>{
 setReviewIdentity('devspace:admin',{actor_id:'admin',role:'admin'});
 render(<ReviewScreen/>);
 await screen.findByText('Chưa có đợt đánh giá.');
 expect(screen.queryByRole('button',{name:'Chuyên gia',hidden:true})).toBeNull();
 expect(api.me).not.toHaveBeenCalled();
});
