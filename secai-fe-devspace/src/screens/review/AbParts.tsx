import {useEffect,useRef} from 'react';
import type {KeyboardEvent as ReactKeyboardEvent,ReactNode,RefObject} from 'react';
import {AlertTriangle,Inbox} from 'lucide-react';
import './reviewAb.css';

/** Khối lỗi thống nhất: role=alert, token danger, nút "Thử lại" khi có onRetry. */
export function AbAlert({children,onRetry,busy}:{children:ReactNode;onRetry?:()=>void;busy?:boolean}){
 return <div role="alert" className="ab-alert"><AlertTriangle size={16} strokeWidth={1.5} className="ab-alert-icon"/><span>{children}</span>{onRetry&&<button type="button" className="iasbtn ghost sm" disabled={busy} onClick={onRetry}>Thử lại</button>}</div>;
}
export function AbLoading({children}:{children:ReactNode}){return <p role="status" className="ab-loading"><span className="spin"/>{children}</p>;}
export function AbEmpty({children}:{children:ReactNode}){return <p className="ab-empty"><Inbox size={20} strokeWidth={1.5}/>{children}</p>;}

/** Phím mũi tên/Home/End cho nhóm nút (roving tabindex). activate=true: chọn luôn mục nhận focus (tablist). */
export function arrowNav(e:KeyboardEvent|ReactKeyboardEvent,selector:string,activate=false){
 if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
 const root=e.currentTarget as HTMLElement,items=Array.from(root.querySelectorAll<HTMLElement>(selector)).filter(x=>!(x as HTMLButtonElement).disabled);
 const at=items.indexOf(document.activeElement as HTMLElement);
 if(!items.length||at<0)return;
 e.preventDefault();
 const next=e.key==='Home'?0:e.key==='End'?items.length-1:(at+(e.key==='ArrowRight'?1:-1)+items.length)%items.length;
 items[next].focus();
 if(activate)items[next].click();
}

const FOCUSABLE='a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex]:not([tabindex="-1"])';
/** Phần tử focus được và đang hiển thị (không nằm trong <details> đóng, không hidden). */
function focusables(root:HTMLElement){
 return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(el=>{
  if(el.hasAttribute('hidden')||el.closest('[hidden]'))return false;
  const fold=el.closest('details:not([open])');
  return !fold||(el.tagName==='SUMMARY'&&el.parentElement===fold);
 });
}
// Ngăn xếp dialog đang mở: chỉ dialog trên cùng xử lý Esc/Tab.
const stack:object[]=[];

/**
 * Dialog modal thật: backdrop, aria-modal, focus vào dialog khi mở, bẫy Tab, Esc/backdrop đóng, khoá cuộn nền,
 * trả focus về opener (restoreRef, hoặc phần tử đang focus lúc mở); nếu opener đã mất thì về vùng cuộn của màn.
 */
export function AbDialog({label,title,onClose,footer,children,size,hideClose,restoreRef,dirty,busy}:{label:string;title:ReactNode;onClose:()=>void;footer?:ReactNode;children:ReactNode;size?:'narrow'|'wide';hideClose?:boolean;restoreRef?:RefObject<HTMLElement|null>;dirty?:boolean;busy?:boolean}){
 const box=useRef<HTMLDivElement>(null),close=useRef(onClose),keep=useRef(!!dirty);
 keep.current=!!dirty;
 close.current=()=>{if(busy)return;if(keep.current&&!window.confirm('Bỏ các thay đổi chưa lưu?'))return;onClose();};
 useEffect(()=>{
  const el=box.current,token={};
  const opener=restoreRef?.current||(document.activeElement instanceof HTMLElement?document.activeElement:null);
  const scroller=el?.closest<HTMLElement>('.ab-body')||null,prevOverflow=scroller?.style.overflow??'';
  if(scroller)scroller.style.overflow='hidden';
  stack.push(token);
  el?.focus();
  function key(e:KeyboardEvent){
   if(stack[stack.length-1]!==token||!el)return;
   if(e.key==='Escape'){e.stopImmediatePropagation();close.current();return;}
   if(e.key!=='Tab')return;
   const items=focusables(el);
   if(!items.length){e.preventDefault();el.focus();return;}
   const first=items[0],last=items[items.length-1],cur=document.activeElement;
   if(!el.contains(cur)||(cur===el&&e.shiftKey)){e.preventDefault();(e.shiftKey?last:first).focus();}
   else if(e.shiftKey&&cur===first){e.preventDefault();last.focus();}
   else if(!e.shiftKey&&cur===last){e.preventDefault();first.focus();}
  }
  document.addEventListener('keydown',key);
  return()=>{
   document.removeEventListener('keydown',key);
   const at=stack.indexOf(token);if(at>=0)stack.splice(at,1);
   if(scroller)scroller.style.overflow=prevOverflow;
   const restore=()=>{if(opener&&opener.isConnected&&!(opener as HTMLButtonElement).disabled){opener.focus();return true;}return false;};
   restore();
   // Danh sách có thể được tải lại ngay sau khi đóng (opener bị thay): nếu focus rơi về <body> thì đưa về nơi neo của màn.
   setTimeout(()=>{const cur=document.activeElement;if(cur&&cur!==document.body&&cur.isConnected)return;if(!restore()){const home=document.querySelector<HTMLElement>('[data-ab-focus-home]')||(scroller?.isConnected?scroller:document.querySelector<HTMLElement>('.ab-body'));home?.focus();}},60);
  };
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[]);
 return <div className="ab-overlay" onMouseDown={e=>{if(e.target===e.currentTarget&&!keep.current)close.current();}}>
  <div ref={box} tabIndex={-1} role="dialog" aria-modal="true" aria-label={label} className={'iascard ab-dialog'+(size?' '+size:'')}>
   <div className="ab-dialog-head"><h3>{title}</h3>{!hideClose&&<button type="button" className="iasbtn ghost sm" disabled={busy} onClick={()=>close.current()}>Đóng</button>}</div>
   <div className="ab-dialog-body">{children}</div>
   {footer&&<div className="ab-dialog-foot">{footer}</div>}
  </div>
 </div>;
}
