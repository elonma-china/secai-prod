// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import ReviewSamplingSettings from './ReviewSamplingSettings';
import type {ReviewApi} from '../../reviewApi';
afterEach(cleanup);
it('loads global percent and saves only an admin-selected valid percentage',async()=>{
 const api={liveSettings:vi.fn().mockResolvedValue({sample_percent:10,cooldown_seconds:600}),setLiveSettings:vi.fn().mockResolvedValue({sample_percent:50,cooldown_seconds:600})} as unknown as ReviewApi;
 render(<ReviewSamplingSettings api={api}/>);
 await screen.findByText('Tỷ lệ mời A/B: 10%');
 fireEvent.click(screen.getByText('Tỷ lệ mời A/B: 10%'));
 const input=screen.getByRole('spinbutton',{name:'Tỷ lệ mời đánh giá (%)'});
 fireEvent.change(input,{target:{value:'101'}});
 expect(screen.getByRole('button',{name:'Lưu tỷ lệ'}).hasAttribute('disabled')).toBe(true);
 fireEvent.change(input,{target:{value:'50'}});
 fireEvent.click(screen.getByRole('button',{name:'Lưu tỷ lệ'}));
 await waitFor(()=>expect(api.setLiveSettings).toHaveBeenCalledWith(50));
 await screen.findByText('Tỷ lệ mời A/B: 50%');
 expect(screen.getByText(/10 phút/)).toBeTruthy();
});
it('does not claim a new setting was saved after API failure',async()=>{
 const api={liveSettings:vi.fn().mockResolvedValue({sample_percent:10,cooldown_seconds:600}),setLiveSettings:vi.fn().mockRejectedValue(new Error('offline'))} as unknown as ReviewApi;
 render(<ReviewSamplingSettings api={api}/>);
 fireEvent.click(await screen.findByText('Tỷ lệ mời A/B: 10%'));
 fireEvent.change(screen.getByRole('spinbutton'),{target:{value:'100'}});
 fireEvent.click(screen.getByRole('button',{name:'Lưu tỷ lệ'}));
 await screen.findByRole('alert');
 expect(screen.getByText('Tỷ lệ mời A/B: 10%')).toBeTruthy();
 expect(screen.queryByText(/Đã áp dụng/)).toBeNull();
});
