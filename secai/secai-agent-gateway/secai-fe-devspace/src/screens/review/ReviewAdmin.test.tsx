// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ReviewAdmin from './ReviewAdmin';
import ReviewPair from './ReviewPair';
import type { ReviewApi } from '../../reviewApi';
import type { Assignment, Campaign } from '../../reviewTypes';

afterEach(cleanup);

it('keeps the confirmed campaign state when refreshing data fails', async () => {
  const initial: Campaign = {id: 'qa', title: 'QA', state: 'open', rubric_version: 'security_qa_v1'};
  const api = {items: vi.fn().mockResolvedValueOnce([]).mockRejectedValue(new Error('429')), stats: vi.fn().mockResolvedValueOnce({items:0,submitted:0,assignments:0,pending_adjudication:0,adjudicated:0,disagreements:0,multi_reviewed_items:0}).mockRejectedValue(new Error('429')),
    trainingJobs: vi.fn().mockResolvedValue({enabled:false,purpose:'demo',jobs:[]}), reviewers: vi.fn().mockResolvedValue([]), campaignState: vi.fn().mockResolvedValue({...initial, state: 'closed'})} as unknown as ReviewApi;
  function Harness() {
    const [campaign, setCampaign] = useState(initial);
    return <ReviewAdmin api={api} campaign={campaign} onChange={setCampaign}/>;
  }
  render(<Harness/>);
  await waitFor(() => expect(document.querySelector('.review-stats')).not.toBeNull());
  fireEvent.click(screen.getByRole('button', {name: 'Đóng đợt'}));
  await screen.findByRole('button', {name: 'Mở lại đợt'});
  await screen.findByRole('alert');
  expect(document.querySelector('.review-stats')).toBeNull();
  await waitFor(() => expect(screen.getByLabelText(/Nhập cặp câu trả lời/).hasAttribute('disabled')).toBe(true));
  expect(api.campaignState).toHaveBeenCalledWith('qa', 'closed');
  expect(api.reviewers).toHaveBeenCalledTimes(1);
});

it('submits Bỏ qua without requiring a comment', () => {
  const submit = vi.fn();
  const assignment = {id:'qa', external_id:'qa', prompt_messages:[{role:'user',content:'Question'}],
    evidence:'', left:{text:'Answer A'}, right:{text:'Answer B'}, rubric:'QA', judgment:null} as Assignment;
  render(<ReviewPair assignment={assignment} closed={false} saving={false} error='' onSubmit={submit}/>);
  fireEvent.click(screen.getByRole('radio', {name:'Bỏ qua'}));
  expect((screen.getByLabelText(/Nhận xét/) as HTMLTextAreaElement).required).toBe(false);
  fireEvent.click(screen.getByRole('button', {name:'Nộp phiếu đánh giá'}));
  expect(submit).toHaveBeenCalledWith({decision:'skip',error_tags:{left:[],right:[]},comment:''});
});
