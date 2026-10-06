// @vitest-environment jsdom
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import RewardTraining from './RewardTraining';
import type {ReviewApi} from '../../reviewApi';
afterEach(cleanup);
it('submits a real job request and displays its saved evaluation without claiming activation',async()=>{
 const job={id:'job-1',campaign_id:'qa',status:'succeeded',purpose:'demo',counts:{train:600,validation:200,test:200},result:{test:{accuracy:0.9,records:200},artifact_sha256:'a'.repeat(64)},error:null};
 const api={trainingJobs:vi.fn().mockResolvedValue({enabled:true,purpose:'demo',jobs:[]}),startTraining:vi.fn().mockResolvedValue(job)} as unknown as ReviewApi;
 render(<RewardTraining api={api} campaignId="qa" approvedPairs={1000}/>);
 const button=await screen.findByRole('button',{name:'Huấn luyện model chấm điểm'});
 await waitFor(()=>expect(button.hasAttribute('disabled')).toBe(false));
 fireEvent.click(button);
 await screen.findByText(/90.0%/);
 expect(screen.getByText(/600.*200.*200/)).toBeTruthy();
 expect(screen.getByText(/chưa đưa vào chatbot/i)).toBeTruthy();
});
it('shows an outage and prevents starting a job when status cannot be loaded',async()=>{
 const api={trainingJobs:vi.fn().mockRejectedValue(new Error('offline'))} as unknown as ReviewApi;
 render(<RewardTraining api={api} campaignId="qa" approvedPairs={1000}/>);
 await screen.findByRole('alert');
 expect(screen.getByRole('button',{name:'Huấn luyện model chấm điểm'}).hasAttribute('disabled')).toBe(true);
});
it('keeps saved results visible while refreshing and prevents submitting during refresh',async()=>{
 const job={id:'saved',campaign_id:'qa',status:'succeeded',purpose:'demo',counts:{train:3,validation:1,test:1},result:{test:{accuracy:0.8,records:1},artifact_sha256:'a'.repeat(64)},error:null};
 const api={trainingJobs:vi.fn().mockResolvedValueOnce({enabled:true,purpose:'demo',jobs:[job]}).mockImplementation(()=>new Promise(()=>{}))} as unknown as ReviewApi;
 render(<RewardTraining api={api} campaignId="qa" approvedPairs={5}/>);
 await screen.findByText('Hoàn tất');
 fireEvent.click(screen.getByRole('button',{name:'Cập nhật trạng thái'}));
 expect(screen.getByText('Hoàn tất')).toBeTruthy();
 expect(screen.getByRole('button',{name:'Huấn luyện model chấm điểm'}).hasAttribute('disabled')).toBe(true);
});
