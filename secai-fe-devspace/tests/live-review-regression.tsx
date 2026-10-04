// Run via Vite: /tests/live-review-regression.html. No API/model requests are sent.
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import LiveReviewWidget from "../src/screens/review/LiveReviewWidget";
import { clearReviewIdentity, setReviewIdentity } from "../src/reviewIdentity";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const host = document.getElementById("test-root")!;
const result = document.getElementById("result")!;
const root = createRoot(host);
const originalFetch = window.fetch;
let resolveSubmit: (response: Response) => void;
let pairCount = 0;
function check(condition: unknown, message: string) { if (!condition) throw new Error(message); }
const json = (body: unknown) => new Response(JSON.stringify(body), {headers: {"Content-Type": "application/json"}});
window.fetch = async (url, init) => {
  if (String(url).endsWith("/live-pairs")) {
    const id = String(++pairCount);
    return json({offered: true, assignment: {id, external_id: id, item_id: id,
      campaign_id: "test", campaign_title: "Test", status: "pending", judgment: null,
      prompt_messages: [{role: "user", content: "Question"}], evidence: "", rubric: "Test",
      left: {text: "Answer A"}, right: {text: "Answer B"}}});
  }
  if (String(url).endsWith("/judgment") && init?.method === "PUT") {
    return new Promise<Response>(resolve => {resolveSubmit = resolve;});
  }
  throw new Error(`Unexpected fetch: ${url}`);
};
async function settle() { await act(async () => {await new Promise(resolve => setTimeout(resolve, 50));}); }
const turn = (id: string) => ({id, prompt_messages: [{role: "user", content: "Question"}], answer: "Answer A", source_group: "test"});
try {
  await act(async () => {
    setReviewIdentity("test-token-not-a-credential", {actor_id: "test", role: "reviewer"});
    root.render(<LiveReviewWidget turn={null}/>);
  });
  await act(async () => {root.render(<LiveReviewWidget turn={turn("one")}/>);});
  await settle();
  check(host.querySelector("form"), "First pair did not render");
  await act(async () => {(host.querySelector('input[value="left"]') as HTMLInputElement).click();});
  await act(async () => {host.querySelector("form")!.dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));});
  check(host.textContent?.includes("Đang nộp"), "Submission was not held pending");
  // Sending another chat clears the previous turn while its submission is pending.
  await act(async () => {root.render(<LiveReviewWidget turn={null}/>);});
  await act(async () => {root.render(<LiveReviewWidget turn={turn("two")}/>);});
  await act(async () => {resolveSubmit!(json({id: "old", decision: "left", error_tags: {left: [], right: []}, comment: ""}));});
  await settle();
  const radio = host.querySelector('input[value="left"]') as HTMLInputElement;
  check(!radio.matches(":disabled"), "New pair is locked by previous saving state");
  await act(async () => {radio.click();});
  check(!(host.querySelector('form button') as HTMLButtonElement).disabled, "New pair cannot be submitted");
  check(!host.textContent?.includes("Đã nộp phiếu"), "Late response replaced the new pair");
  result.textContent = "PASS: pending submission + turn change; new pair enabled; stale response ignored.";
} catch (error) {
  result.textContent = `FAIL: ${String(error)}`;
  throw error;
} finally {
  await act(async () => {root.unmount(); clearReviewIdentity();});
  window.fetch = originalFetch;
}
