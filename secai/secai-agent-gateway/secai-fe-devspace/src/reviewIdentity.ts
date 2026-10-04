import { useSyncExternalStore } from "react";
import type { Actor } from "./reviewTypes";
interface ReviewIdentity { token: string; actor: Actor; liveEnabled: boolean }
let identity: ReviewIdentity | null = null;
const listeners = new Set<() => void>();
function notify() { listeners.forEach(listener => listener()); }
export function getReviewIdentity() { return identity; }
export function setReviewIdentity(token: string, actor: Actor) { identity = {token, actor, liveEnabled: actor.role === "reviewer"}; notify(); }
export function clearReviewIdentity() { identity = null; notify(); }
export function setLiveReviewEnabled(enabled: boolean) { if (identity) { identity = {...identity, liveEnabled: enabled}; notify(); } }
export function useReviewIdentity() { return useSyncExternalStore(listener => { listeners.add(listener); return () => listeners.delete(listener); }, getReviewIdentity, () => null); }
