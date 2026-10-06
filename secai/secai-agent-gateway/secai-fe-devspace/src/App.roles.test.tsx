// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
vi.mock('./api',()=>({health:vi.fn().mockResolvedValue({status:'ok'})}));
vi.mock('./reviewApi',()=>({createReviewApi:(token:string)=>({me:async()=>({actor_id:token,role:token.endsWith('admin')?'admin':'reviewer'})})}));
vi.mock('./screens/ChatbotScreen',()=>({default:()=> <p>Demo chatbot</p>}));
vi.mock('./screens/TranslateScreen',()=>({default:()=>null}));
vi.mock('./screens/ExploitScreen',()=>({default:()=>null}));
vi.mock('./screens/ReportScreen',()=>({default:()=>null}));
vi.mock('./screens/ReviewScreen',()=>({default:()=> <p>Demo admin review</p>}));
import App from './App';
import {clearReviewIdentity,getReviewIdentity} from './reviewIdentity';
beforeEach(()=>{sessionStorage.clear();localStorage.clear();clearReviewIdentity();});
afterEach(cleanup);
it('hides the admin tab for regular users and removes it when switching back',async()=>{
 render(<App/>);
 expect(screen.queryByRole('button',{name:'Đánh giá A/B'})).toBeNull();
 await waitFor(()=>expect(getReviewIdentity()?.actor.role).toBe('reviewer'));
 fireEvent.change(screen.getByLabelText('Vai trò demo'),{target:{value:'admin'}});
 await screen.findByRole('button',{name:'Đánh giá A/B'});
 await waitFor(()=>expect(getReviewIdentity()?.actor.role).toBe('admin'));
 expect(screen.getByText('Demo admin review')).toBeTruthy();
 fireEvent.change(screen.getByLabelText('Vai trò demo'),{target:{value:'user'}});
 expect(screen.queryByRole('button',{name:'Đánh giá A/B'})).toBeNull();
 expect(screen.queryByText('Demo admin review')).toBeNull();
 await waitFor(()=>expect(getReviewIdentity()?.actor.role).toBe('reviewer'));
});
