// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';

const mocks=vi.hoisted(()=>({
  chatStream:vi.fn(),createStandaloneSession:vi.fn(),listProjects:vi.fn(),listUserSessions:vi.fn(),
  liveDecide:vi.fn(),liveAlternative:vi.fn(),liveCreatePair:vi.fn(),
}));
vi.mock('../api',()=>({
  chatStream:mocks.chatStream,createStandaloneSession:mocks.createStandaloneSession,
  listProjects:mocks.listProjects,listUserSessions:mocks.listUserSessions,
}));
vi.mock('./chat/ChatSidebar',()=>({default:()=>null}));
vi.mock('./chat/ChatThread',()=>({default:({onInputChange,onSend,reviewWidget,items,busy}:{onInputChange:(value:string)=>void;onSend:()=>void;reviewWidget:unknown;items:{id:string;text?:string}[];busy:boolean})=><div>
  <textarea aria-label="Chat message" onChange={event=>onInputChange(event.target.value)}/>
  <button type="button" onClick={onSend}>Send chat</button>
  {items.map(item=><span key={item.id}>{item.text}</span>)}
  {busy&&<span role="status" aria-label="Sending chat"/>}{typeof reviewWidget === 'function' ? items.map(item=><div key={item.id}>{(reviewWidget as (id:string)=>never)(item.id)}</div>) : reviewWidget as never}
</div>}));
vi.mock('../reviewApi',()=>({createReviewApi:()=>mocks,reviewError:()=>"Request failed"}));
import ChatbotScreen from './ChatbotScreen';
import {clearReviewIdentity,setLiveReviewEnabled,setReviewIdentity} from '../reviewIdentity';

function deferred<T>() {
  let resolve!:(value:T)=>void;
  const promise=new Promise<T>(r=>{resolve=r;});
  return {promise,resolve};
}

afterEach(()=>{cleanup();clearReviewIdentity();vi.restoreAllMocks();vi.clearAllMocks();});

test('each opted-in chat keeps its own two choices when the next message is sent',async()=>{
  mocks.listProjects.mockResolvedValue([]);
  mocks.listUserSessions.mockResolvedValue([]);
  mocks.createStandaloneSession.mockResolvedValue({session_id:'session-1',title:''});
  mocks.chatStream.mockImplementation(async(_sid:string,message:string,_userId:string,handlers:{onFinal:(payload:{content:string})=>void})=>handlers.onFinal({content:`Completed ${message}`}));
  mocks.liveDecide.mockResolvedValue({sampled:true});
  mocks.liveAlternative.mockImplementation(async(messages:{content:string}[])=>{
    if (messages.length > 30) throw new Error('live context exceeds 30 messages');
    return {text:`B ${messages.at(-1)!.content}`};
  });
  mocks.liveCreatePair.mockImplementation(async(messages:{content:string}[],answer:string,alternative:string)=>({offered:true,assignment:{
    id:messages.at(-1)!.content,campaign_id:'c',campaign_title:'t',status:'pending',external_id:'turn',prompt_messages:messages,evidence:'',
    left:{text:answer},right:{text:alternative},rubric:'',rubric_version:'v1',judgment:null,
  }}));
  setReviewIdentity('devspace:reviewer',{actor_id:'reviewer-1',role:'reviewer'});
  render(<ChatbotScreen userId="ab-every-turn" health={null}/>);
  for (const message of ['First','Second',...Array.from({length:14},(_,i)=>`Question ${i+3}`)]) {
    fireEvent.change(screen.getByRole('textbox',{name:'Chat message'}),{target:{value:message}});
    fireEvent.click(screen.getByRole('button',{name:'Send chat'}));
    expect((await screen.findAllByText(`Completed ${message}`)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(`B ${message}`)).length).toBeGreaterThan(0);
    await waitFor(()=>expect(screen.queryByRole('status',{name:'Sending chat'})).toBeNull());
  }
  expect(screen.getAllByText('Completed First').length).toBeGreaterThan(0);
  expect(screen.getAllByText('B First').length).toBeGreaterThan(0);
});

test('a completed chat turn is retained when reviewer identity arrives after streaming',async()=>{
  const streamFinished=deferred<void>();
  mocks.listProjects.mockResolvedValue([]);
  mocks.listUserSessions.mockResolvedValue([]);
  mocks.createStandaloneSession.mockResolvedValue({session_id:'session-1',title:'',created_at:'now',updated_at:'now'});
  mocks.chatStream.mockImplementation(async(_sid:string,_message:string,_userId:string,handlers:{onToken:(delta:string)=>void;onFinal:(payload:{content:string})=>void})=>{
    handlers.onToken('Completed answer');
    handlers.onFinal({content:'Completed answer'});
    await streamFinished.promise;
  });
  mocks.liveDecide.mockResolvedValue({sampled:true});
  mocks.liveAlternative.mockResolvedValue({text:'Reviewer answer B'});
  mocks.liveCreatePair.mockResolvedValue({offered:true,assignment:{
    id:'assignment-1',campaign_id:'c',campaign_title:'t',status:'pending',external_id:'turn-1',prompt_messages:[{role:'user',content:'Question'}],evidence:'',
    left:{text:'Reviewer answer A'},right:{text:'Reviewer answer B'},rubric:'',rubric_version:'v1',judgment:null,
  }});
  clearReviewIdentity();
  render(<ChatbotScreen userId="user-1" health={null}/>);
  fireEvent.change(screen.getByRole('textbox',{name:'Chat message'}),{target:{value:'Question'}});
  fireEvent.click(screen.getByRole('button',{name:'Send chat'}));
  await waitFor(()=>expect(mocks.chatStream).toHaveBeenCalledTimes(1));
  expect(await screen.findByRole('status',{name:'Sending chat'})).toBeTruthy();
  await act(async()=>{streamFinished.resolve();});
  await waitFor(()=>expect(screen.queryByRole('status',{name:'Sending chat'})).toBeNull());
  expect(screen.getByText('Completed answer')).toBeTruthy();

  // Identity wasn't ready while A was streaming, so the parallel path never started — this
  // falls back to firing decide+alternative sequentially once identity does arrive, instead
  // of permanently missing the turn.
  expect(mocks.liveDecide).not.toHaveBeenCalled();
  await act(async()=>{setReviewIdentity('devspace:reviewer',{actor_id:'reviewer-1',role:'reviewer'});});

  await waitFor(()=>expect(mocks.liveCreatePair).toHaveBeenCalledTimes(1));
  expect((await screen.findAllByText('Reviewer answer A')).length).toBeGreaterThan(0);
});

test('an explicit reviewer opt-out skips the live pair for that chat turn',async()=>{
  const streamFinished=deferred<void>();
  mocks.listProjects.mockResolvedValue([]);
  mocks.listUserSessions.mockResolvedValue([]);
  mocks.createStandaloneSession.mockResolvedValue({session_id:'session-1',title:'',created_at:'now',updated_at:'now'});
  mocks.chatStream.mockImplementation(async(_sid:string,_message:string,_userId:string,handlers:{onToken:(delta:string)=>void;onFinal:(payload:{content:string})=>void})=>{
    handlers.onToken('Completed answer');
    handlers.onFinal({content:'Completed answer'});
    await streamFinished.promise;
  });
  setReviewIdentity('devspace:reviewer',{actor_id:'reviewer-1',role:'reviewer'});
  setLiveReviewEnabled(false);
  render(<ChatbotScreen userId="user-1" health={null}/>);
  fireEvent.change(screen.getByRole('textbox',{name:'Chat message'}),{target:{value:'Question'}});
  fireEvent.click(screen.getByRole('button',{name:'Send chat'}));
  await waitFor(()=>expect(mocks.chatStream).toHaveBeenCalledTimes(1));
  expect(await screen.findByRole('status',{name:'Sending chat'})).toBeTruthy();
  await act(async()=>{streamFinished.resolve();});
  await waitFor(()=>expect(screen.queryByRole('status',{name:'Sending chat'})).toBeNull());

  expect(mocks.liveDecide).not.toHaveBeenCalled();
  expect(screen.queryByRole('complementary',{name:'Phản hồi A/B trong chat'})).toBeNull();
});
