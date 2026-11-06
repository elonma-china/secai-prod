export * as EngagementSchema from "./schema"

import { Schema } from "effect"

export const ID = Schema.String.pipe(Schema.brand("Engagement.ID"))
export type ID = typeof ID.Type

export const Severity = Schema.Literals(["critical", "high", "medium", "low", "info"])
export type Severity = typeof Severity.Type

export const VulnStatus = Schema.Literals(["suspected", "confirmed", "exploited", "false_positive"])
export type VulnStatus = typeof VulnStatus.Type

export const AccessLevel = Schema.Literals(["none", "user", "root", "system"])
export type AccessLevel = typeof AccessLevel.Type

export const PentestPhase = Schema.Literals([
  "recon",
  "enumeration",
  "vuln_assess",
  "exploitation",
  "post_exploit",
  "reporting",
])
export type PentestPhase = typeof PentestPhase.Type

export const PentestMode = Schema.Literals(["auto", "free", "guided"])
export type PentestMode = typeof PentestMode.Type

export const PauseBehavior = Schema.Literals(["never", "always", "checkpoint"])
export type PauseBehavior = typeof PauseBehavior.Type

export const TaskNodeStatus = Schema.Literals(["pending", "in_progress", "done", "abandoned"])
export type TaskNodeStatus = typeof TaskNodeStatus.Type

export const ObjectiveStatus = Schema.Literals(["not_started", "in_progress", "completed", "blocked", "abandoned"])
export type ObjectiveStatus = typeof ObjectiveStatus.Type

export const ObjectivePriority = Schema.Literals(["critical", "high", "medium", "low"])
export type ObjectivePriority = typeof ObjectivePriority.Type

export const ObjectiveCategory = Schema.Literals(["ctf", "pentest", "bounty", "red_team", "custom"])
export type ObjectiveCategory = typeof ObjectiveCategory.Type

export const Service = Schema.Struct({
  port: Schema.Number,
  protocol: Schema.optional(Schema.String),
  service: Schema.optional(Schema.String),
  version: Schema.optional(Schema.String),
  state: Schema.optional(Schema.String),
  banner: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.Service" })
export type Service = typeof Service.Type

export const VerificationStatus = Schema.Literals(["unverified", "verified", "false_positive"])
export type VerificationStatus = typeof VerificationStatus.Type

export const EvidenceItem = Schema.Struct({
  tool: Schema.String,
  command: Schema.optional(Schema.String),
  output: Schema.String,
  timestamp: Schema.optional(Schema.String),
  confidence: Schema.optional(Schema.Number),
  reasoning: Schema.optional(Schema.String),
  source_agent: Schema.optional(Schema.String),
  attempt_number: Schema.optional(Schema.Number),
  verification_status: Schema.optional(VerificationStatus),
}).annotate({ identifier: "Engagement.EvidenceItem" })
export type EvidenceItem = typeof EvidenceItem.Type

// I-3: derive a vulnerability's confidence from real signal — corroboration
// (independent tools agreeing, repeated observation) and exploitation state —
// NOT from severity (severity is impact, not likelihood-of-being-real) and NOT a
// hardcoded literal. A critic/manual verification dominates when present. Keep
// this the single source of a vuln's confidence so the number means something and
// the auto-critic (which gates on confidence) reacts to evidence, not a guess.
export function deriveConfidence(vuln: {
  status?: string
  evidence_items?: ReadonlyArray<{ tool?: string; verification_status?: string }>
}): number {
  const items = vuln.evidence_items ?? []
  if (items.some((e) => e.verification_status === "verified")) return 0.95
  if (items.length > 0 && items.every((e) => e.verification_status === "false_positive")) return 0.1
  let c = 0.5 // one unverified signal
  const tools = new Set(items.map((e) => e.tool).filter(Boolean))
  if (tools.size >= 2) c += 0.2 // independent tools corroborate
  else if (items.length >= 2) c += 0.1 // repeated observation, same tool
  if (vuln.status === "exploited") c += 0.25
  else if (vuln.status === "confirmed") c += 0.1
  else if (vuln.status === "suspected") c -= 0.15
  return Math.round(Math.max(0.1, Math.min(0.95, c)) * 100) / 100
}

export const Vulnerability = Schema.Struct({
  id: Schema.optional(Schema.String),
  title: Schema.String,
  service_port: Schema.optional(Schema.Number),
  severity: Schema.optional(Severity),
  status: Schema.optional(VulnStatus),
  confidence: Schema.optional(Schema.Number),
  description: Schema.optional(Schema.String),
  evidence: Schema.optional(Schema.String),
  evidence_items: Schema.optional(Schema.Array(EvidenceItem)),
  references: Schema.optional(Schema.Array(Schema.String)),
  mitre_attack_id: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.Vulnerability" })
export type Vulnerability = typeof Vulnerability.Type

export const Credential = Schema.Struct({
  id: Schema.String,
  cred_type: Schema.optional(Schema.String),
  username: Schema.optional(Schema.String),
  value: Schema.optional(Schema.String),
  source: Schema.optional(Schema.String),
  valid_for: Schema.optional(Schema.Array(Schema.String)),
  confidence: Schema.optional(Schema.Number),
  domain: Schema.optional(Schema.String),
  ticket_type: Schema.optional(Schema.String),
  service_principal: Schema.optional(Schema.String),
  ticket_expiry: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.Credential" })
export type Credential = typeof Credential.Type

export const Access = Schema.Struct({
  access_type: Schema.String,
  username: Schema.String,
  level: Schema.optional(AccessLevel),
  confidence: Schema.optional(Schema.Number),
  credential_id: Schema.optional(Schema.String),
  details: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.Access" })
export type Access = typeof Access.Type

export const DomainInfo = Schema.Struct({
  domain: Schema.optional(Schema.String),
  is_dc: Schema.optional(Schema.Boolean),
  computer_account: Schema.optional(Schema.String),
  forest: Schema.optional(Schema.String),
  site: Schema.optional(Schema.String),
  functional_level: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.DomainInfo" })
export type DomainInfo = typeof DomainInfo.Type

export const Host = Schema.Struct({
  ip: Schema.String,
  hostname: Schema.optional(Schema.String),
  os: Schema.optional(Schema.String),
  domain_info: Schema.optional(DomainInfo),
  services: Schema.Array(Service),
  vulns: Schema.Array(Vulnerability),
  access: Schema.Array(Access),
  notes: Schema.Array(Schema.String),
}).annotate({ identifier: "Engagement.Host" })
export type Host = typeof Host.Type

export const AttackStep = Schema.Struct({
  timestamp: Schema.String,
  source: Schema.String,
  target: Schema.String,
  technique: Schema.String,
  result: Schema.String,
  success: Schema.optional(Schema.Boolean),
  mitre_attack_id: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.AttackStep" })
export type AttackStep = typeof AttackStep.Type

export const Objective = Schema.Struct({
  id: Schema.String,
  title: Schema.String,
  description: Schema.optional(Schema.String),
  status: ObjectiveStatus,
  priority: Schema.optional(ObjectivePriority),
  category: Schema.optional(ObjectiveCategory),
  target_hosts: Schema.optional(Schema.Array(Schema.String)),
  linked_vulns: Schema.optional(Schema.Array(Schema.String)),
  linked_creds: Schema.optional(Schema.Array(Schema.String)),
  flags: Schema.optional(Schema.Array(Schema.String)),
  evidence: Schema.optional(Schema.String),
  notes: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.Objective" })
export type Objective = typeof Objective.Type

export const Scope = Schema.Struct({
  targets: Schema.Array(Schema.String),
  excludes: Schema.Array(Schema.String),
  discovered_targets: Schema.optional(Schema.Array(Schema.String)),
  notes: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.Scope" })
export type Scope = typeof Scope.Type

export const TaskTreeNode = Schema.Struct({
  id: Schema.String,
  parent_id: Schema.optional(Schema.String),
  description: Schema.String,
  status: Schema.optional(TaskNodeStatus),
  difficulty: Schema.optional(Schema.Number),
  target: Schema.optional(Schema.String),
  technique: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.TaskTreeNode" })
export type TaskTreeNode = typeof TaskTreeNode.Type

export const Trust = Schema.Struct({
  target_domain: Schema.String,
  trust_type: Schema.optional(Schema.String),
  trust_direction: Schema.optional(Schema.String),
  is_transitive: Schema.optional(Schema.Boolean),
}).annotate({ identifier: "Engagement.Trust" })
export type Trust = typeof Trust.Type

export const DomainState = Schema.Struct({
  domain_name: Schema.String,
  forest: Schema.optional(Schema.String),
  domain_sid: Schema.optional(Schema.String),
  trusts: Schema.optional(Schema.Array(Trust)),
  domain_controllers: Schema.optional(Schema.Array(Schema.String)),
  domain_admins: Schema.optional(Schema.Array(Schema.String)),
  gpo_names: Schema.optional(Schema.Array(Schema.String)),
  password_policy: Schema.optional(Schema.Struct({
    min_length: Schema.optional(Schema.Number),
    lockout_threshold: Schema.optional(Schema.Number),
    lockout_duration: Schema.optional(Schema.String),
    complexity_enabled: Schema.optional(Schema.Boolean),
  })),
}).annotate({ identifier: "Engagement.DomainState" })
export type DomainState = typeof DomainState.Type

export const RelationType = Schema.Literals([
  "EXPLOITED_VIA",
  "CREDENTIAL_FROM",
  "REACHABLE_FROM",
  "TRUSTS",
  "MEMBER_OF",
  "ADMIN_OF",
  "PIVOT_TO",
  "AUTHENTICATES_TO",
  "LATERAL_MOVE",
  "CONTROLS",
])
export type RelationType = typeof RelationType.Type

export const EntityType = Schema.Literals(["host", "credential", "vuln", "service", "domain", "user", "group"])
export type EntityType = typeof EntityType.Type

export const Relationship = Schema.Struct({
  source_type: EntityType,
  source_id: Schema.String,
  rel_type: RelationType,
  target_type: EntityType,
  target_id: Schema.String,
  metadata: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.Relationship" })
export type Relationship = typeof Relationship.Type

// --- Decision Memory ---

export const DecisionOutcome = Schema.Literals(["pending", "successful", "failed", "abandoned", "superseded"])
export type DecisionOutcome = typeof DecisionOutcome.Type

export const Decision = Schema.Struct({
  id: Schema.String,
  timestamp: Schema.String,
  phase: PentestPhase,
  decision: Schema.String,
  reasoning: Schema.String,
  alternatives: Schema.optional(Schema.Array(Schema.String)),
  outcome: Schema.optional(DecisionOutcome),
  outcome_notes: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.Decision" })
export type Decision = typeof Decision.Type

export const DECISIONS_MAX_ENTRIES = 100

// --- Alert Queue ---

export const AlertSeverity = Schema.Literals(["critical", "high", "medium", "info"])
export type AlertSeverity = typeof AlertSeverity.Type

export const AlertPriority = Schema.Literals(["normal", "interrupt"])
export type AlertPriority = typeof AlertPriority.Type

export const Alert = Schema.Struct({
  id: Schema.String,
  timestamp: Schema.String,
  severity: AlertSeverity,
  priority: Schema.optional(AlertPriority),
  source_agent: Schema.optional(Schema.String),
  title: Schema.String,
  details: Schema.optional(Schema.String),
  host_ip: Schema.optional(Schema.String),
  acknowledged: Schema.optional(Schema.Boolean),
  ttl_minutes: Schema.optional(Schema.Number),
}).annotate({ identifier: "Engagement.Alert" })
export type Alert = typeof Alert.Type

export const ALERTS_MAX_ACTIVE = 50
export const ALERTS_DEFAULT_TTL_MINUTES = 60

// --- Live Sessions (shells, tunnels, listeners) ---

export const SessionType = Schema.Literals(["shell", "listener", "tunnel", "socks_proxy", "port_forward"])
export type SessionType = typeof SessionType.Type

export const LiveSession = Schema.Struct({
  id: Schema.String,
  session_type: SessionType,
  host_ip: Schema.String,
  port: Schema.optional(Schema.Number),
  username: Schema.optional(Schema.String),
  pid: Schema.optional(Schema.Number),
  established_at: Schema.String,
  last_seen: Schema.optional(Schema.String),
  alive: Schema.optional(Schema.Boolean),
  details: Schema.optional(Schema.String),
  local_port: Schema.optional(Schema.Number),
  remote_target: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.LiveSession" })
export type LiveSession = typeof LiveSession.Type

// --- Artifacts (A-2 minimal): reusable exploit weapons / loot / scripts, made
// first-class so sibling agents INVOKE a recorded weapon (path + how to call it)
// instead of re-deriving the payload. Only metadata lives here; the file stays on
// disk (in-container path / engagement dir). Capped, deletable. ---
export const ArtifactType = Schema.Literals(["exploit", "loot", "script", "payload", "wordlist", "other"])
export type ArtifactType = typeof ArtifactType.Type

export const Artifact = Schema.Struct({
  id: Schema.String,
  name: Schema.String,
  path: Schema.String,
  type: ArtifactType,
  description: Schema.optional(Schema.String),
  host_ip: Schema.optional(Schema.String),
  created_at: Schema.String,
}).annotate({ identifier: "Engagement.Artifact" })
export type Artifact = typeof Artifact.Type
export const ARTIFACTS_MAX = 50

// --- Network Segmentation ---

export const NetworkSegment = Schema.Struct({
  id: Schema.String,
  name: Schema.optional(Schema.String),
  cidr: Schema.String,
  vlan: Schema.optional(Schema.Number),
  gateway: Schema.optional(Schema.String),
  reachable_from: Schema.optional(Schema.Array(Schema.String)),
  pivot_host: Schema.optional(Schema.String),
  notes: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.NetworkSegment" })
export type NetworkSegment = typeof NetworkSegment.Type

// --- Resolved Vectors Ledger (R6 fix: don't re-test dead vectors) ---
// A first-class, cross-agent record of attack vectors that have been probed to a
// conclusion. Both the coordinator and every subagent query this before opening a
// vector and record into it when a vector is settled — so the open-redirect-7x
// loop and cross-agent duplication cannot recur.

export const VectorStatus = Schema.Literals([
  "attempted", // probed, inconclusive — safe to retry only with a new technique
  "confirmed", // vulnerable — a Vulnerability record carries the detail; do not re-confirm
  "resolved", // definitively NOT exploitable / dead end — DO NOT RETEST
  "blocked", // needs a precondition not yet met — revisit only when it changes
])
export type VectorStatus = typeof VectorStatus.Type

// A single sub-attempt WITHIN a vector — the specific technique tried during a grind.
// This is the in-vector memory: a hard exploit (deser gadget chains, payload variations,
// shell-stabilization tricks) burns many attempts before the vector as a whole settles.
// Logging each one means a re-spawn / post-compaction turn does not re-explore blind.
export const VectorAttempt = Schema.Struct({
  technique: Schema.String, // the specific approach, e.g. "gadget CommonsCollections6", "hessian2 base64 encoding"
  outcome: Schema.Literals(["failed", "partial", "success"]),
  detail: Schema.optional(Schema.String), // short reason/result, e.g. "ClassNotFound on target classpath"
  timestamp: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.VectorAttempt" })
export type VectorAttempt = typeof VectorAttempt.Type

export const VECTOR_ATTEMPT_LOG_MAX = 20 // per-vector cap on sub-attempt history

export const ResolvedVector = Schema.Struct({
  id: Schema.String,
  timestamp: Schema.String,
  target: Schema.String, // host:port / URL / endpoint the vector was tried against
  vector: Schema.String, // technique, e.g. "open-redirect on /redirect", "JWT alg-confusion"
  status: VectorStatus,
  tested_by: Schema.optional(Schema.String), // agent type that concluded it
  attempts: Schema.optional(Schema.Number), // how many times it has been probed
  evidence: Schema.optional(Schema.String), // short reason it is settled (why dead / why blocked)
  revisit_when: Schema.optional(Schema.String), // for "blocked": the precondition to wait on
  attempt_log: Schema.optional(Schema.Array(VectorAttempt)), // in-vector memory: per-technique sub-attempts
}).annotate({ identifier: "Engagement.ResolvedVector" })
export type ResolvedVector = typeof ResolvedVector.Type

export const RESOLVED_VECTORS_MAX = 300

export const GoalStatus = Schema.Literals(["active", "achieved", "blocked", "abandoned"])
export type GoalStatus = typeof GoalStatus.Type

export const Goal = Schema.Struct({
  text: Schema.String,
  status: GoalStatus,
  set_at: Schema.String,
  achieved_at: Schema.optional(Schema.String),
  evidence: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.Goal" })
export type Goal = typeof Goal.Type

export const State = Schema.Struct({
  id: ID,
  name: Schema.String,
  created_at: Schema.String,
  updated_at: Schema.String,
  scope: Scope,
  hosts: Schema.Record(Schema.String, Host),
  credentials: Schema.Record(Schema.String, Credential),
  flags: Schema.Array(Schema.String),
  attack_path: Schema.Array(AttackStep),
  task_tree: Schema.Array(TaskTreeNode),
  task_graph: Schema.optional(Schema.Record(Schema.String, Schema.Unknown)),
  objectives: Schema.optional(Schema.Record(Schema.String, Objective)),
  domain: Schema.optional(DomainState),
  relationships: Schema.optional(Schema.Array(Relationship)),
  alerts: Schema.optional(Schema.Array(Alert)),
  live_sessions: Schema.optional(Schema.Array(LiveSession)),
  network_segments: Schema.optional(Schema.Array(NetworkSegment)),
  resolved_vectors: Schema.optional(Schema.Array(ResolvedVector)),
  artifacts: Schema.optional(Schema.Array(Artifact)),
  pause_on_finding: Schema.optional(PauseBehavior),
  goal: Schema.optional(Goal),
  current_phase: PentestPhase,
  mode: PentestMode,
  notes: Schema.Array(Schema.String),
}).annotate({ identifier: "Engagement.State" })
export type State = typeof State.Type

export const ChangelogEntry = Schema.Struct({
  timestamp: Schema.String,
  action: Schema.String,
  entity_type: Schema.String,
  entity_id: Schema.optional(Schema.String),
  summary: Schema.String,
}).annotate({ identifier: "Engagement.ChangelogEntry" })
export type ChangelogEntry = typeof ChangelogEntry.Type

export const CHANGELOG_MAX_ENTRIES = 500

// --- Agent Context Summaries (for context carry across subagent re-spawns) ---

export const AgentContextSummary = Schema.Struct({
  id: Schema.String,
  agent_type: Schema.String,
  timestamp: Schema.String,
  task_description: Schema.String,
  outcome: Schema.Literals(["completed", "error"]),
  key_findings: Schema.Array(Schema.String),
  failed_attempts: Schema.Array(Schema.String),
  recommended_next: Schema.Array(Schema.String),
}).annotate({ identifier: "Engagement.AgentContextSummary" })
export type AgentContextSummary = typeof AgentContextSummary.Type

export const AGENT_CONTEXT_MAX_PER_TYPE = 10

// --- Wordlist Usage Tracking ---

export const WordlistToolType = Schema.Literals([
  "dir_fuzz", "brute", "vhost", "subdomain",
  "user_enum", "param_fuzz", "password_spray",
])
export type WordlistToolType = typeof WordlistToolType.Type

export const WordlistUsage = Schema.Struct({
  host_ip: Schema.String,
  port: Schema.Number,
  tool_type: WordlistToolType,
  wordlist_path: Schema.String,
  timestamp: Schema.String,
  results_count: Schema.optional(Schema.Number),
  agent_type: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.WordlistUsage" })
export type WordlistUsage = typeof WordlistUsage.Type

export const WORDLISTS_MAX_ENTRIES = 1000

export function wordlistSummary(usages: readonly WordlistUsage[], hostIp?: string, port?: number): string {
  let filtered = [...usages]
  if (hostIp) filtered = filtered.filter((u) => u.host_ip === hostIp)
  if (port !== undefined) filtered = filtered.filter((u) => u.port === port)

  if (filtered.length === 0) return "No wordlists used yet."

  const byTarget = new Map<string, WordlistUsage[]>()
  for (const u of filtered) {
    const key = `${u.host_ip}:${u.port}`
    const list = byTarget.get(key) ?? []
    list.push(u)
    byTarget.set(key, list)
  }

  const lines: string[] = []
  for (const [target, entries] of byTarget) {
    const byTool = new Map<string, string[]>()
    for (const e of entries) {
      const list = byTool.get(e.tool_type) ?? []
      list.push(e.wordlist_path)
      byTool.set(e.tool_type, list)
    }
    lines.push(`${target}: ${[...byTool.entries()].map(([t, wl]) => `${t}=[${wl.join(",")}]`).join(" ")}`)
  }
  return lines.join("\n")
}

/**
 * #1 vector-ledger — the coordinator's PRIMARY strategic lens: a compact ranked
 * board of attack vectors so it allocates over a SHORT list (live / confirmed /
 * dead) instead of re-reading a raw multi-host state dump. Per host:
 *   IP (hostname) [OWNED-root | OWNED-user | recon]  ⚡signal (new progress this turn)
 *     ! confirmed/exploited vuln not yet converted → FINISH or ESCALATE (never drop)
 *     ? suspected vuln → candidate
 *     · open service, no access → untried lead
 * plus a DEAD list (resolved_vectors status=resolved) = do NOT retry. `recentChanges`
 * flags which hosts saw new signal, so a still-advancing vector is visibly ALIVE.
 */
export function toVectorLedger(state: State, recentChanges: ChangelogEntry[] = []): string | undefined {
  const hosts = Object.entries(state.hosts)
  if (hosts.length === 0) return undefined
  const isRoot = (lvl?: string) => !!lvl && /root|admin|system|super/i.test(lvl)
  const signalIPs = new Set<string>()
  for (const c of recentChanges) {
    for (const [ip] of hosts) if (c.entity_id === ip || c.summary?.includes(ip)) signalIPs.add(ip)
  }
  const lines: string[] = [
    "<vector-ledger> — your strategic board. Allocate over LIVE/untried vectors; FINISH or ESCALATE a confirmed vuln (never drop one that still shows progress); never retry DEAD.",
  ]
  for (const [ip, h] of hosts.slice(0, 25)) {
    const anyAcc = h.access.length > 0
    const rootAcc = h.access.some((a) => isRoot(a.level))
    const status = rootAcc ? "OWNED-root" : anyAcc ? "OWNED-user" : h.services.length ? "recon" : "seen"
    lines.push(`  ${ip}${h.hostname ? ` (${h.hostname})` : ""} [${status}]${signalIPs.has(ip) ? " ⚡signal" : ""}`)
    for (const v of h.vulns.filter((v) => v.status === "confirmed" || v.status === "exploited").slice(0, 4))
      lines.push(`     ! ${v.status} — ${v.title}${v.severity ? ` [${v.severity}]` : ""} → finish/escalate`)
    for (const v of h.vulns.filter((v) => v.status === "suspected" || !v.status).slice(0, 3))
      lines.push(`     ? suspected — ${v.title}${v.confidence !== undefined ? ` conf:${v.confidence}` : ""}`)
    if (!anyAcc) {
      const svc = h.services
        .filter((s) => !s.state || s.state === "open")
        .map((s) => `${s.port}/${s.service ?? "?"}`)
        .slice(0, 8)
      if (svc.length) lines.push(`     · untried: ${svc.join(", ")}`)
    }
  }
  if (hosts.length > 25) lines.push(`  … +${hosts.length - 25} more hosts (state_query hosts)`)
  const dead = (state.resolved_vectors ?? []).filter((v) => v.status === "resolved")
  if (dead.length)
    lines.push(
      `  DEAD — do NOT retry: ${dead.slice(0, 10).map((v) => `${v.target}:${v.vector}`).join(" | ")}${dead.length > 10 ? ` +${dead.length - 10}` : ""}`,
    )
  lines.push("</vector-ledger>")
  return lines.join("\n")
}

export function toDiffContext(entries: ChangelogEntry[], maxEntries = 20): string | undefined {
  if (entries.length === 0) return undefined
  const recent = entries.slice(-maxEntries)
  const lines: string[] = ["Changes since last turn:"]
  for (const e of recent) {
    const ts = e.timestamp.split("T")[1]?.slice(0, 8) ?? e.timestamp
    lines.push(`  [${ts}] ${e.action} ${e.entity_type}${e.entity_id ? ` (${e.entity_id})` : ""}: ${e.summary}`)
  }
  if (entries.length > maxEntries) {
    lines.push(`  ... and ${entries.length - maxEntries} earlier changes`)
  }
  return lines.join("\n")
}

export function unvalidatedVulns(state: State): Array<{ ip: string; vuln: Vulnerability }> {
  const result: Array<{ ip: string; vuln: Vulnerability }> = []
  for (const [ip, host] of Object.entries(state.hosts)) {
    for (const vuln of host.vulns) {
      if (vuln.status === "suspected" || (!vuln.status && (vuln.confidence === undefined || vuln.confidence < 0.8))) {
        result.push({ ip, vuln })
      }
    }
  }
  return result
}

export function criticHint(state: State): string | undefined {
  const unvalidated = unvalidatedVulns(state)
  if (unvalidated.length === 0) return undefined
  const byHost = new Map<string, string[]>()
  for (const { ip, vuln } of unvalidated) {
    const list = byHost.get(ip) ?? []
    list.push(`${vuln.title} [${(vuln.severity ?? "medium").toUpperCase()}]`)
    byHost.set(ip, list)
  }
  const lines = [`${unvalidated.length} unvalidated finding(s) — spawn "critic" subagent to validate:`]
  for (const [ip, vulns] of byHost) {
    lines.push(`  ${ip}: ${vulns.join(", ")}`)
  }
  return lines.join("\n")
}

export function activeAlerts(state: State): Alert[] {
  const now = Date.now()
  return (state.alerts ?? []).filter((a) => {
    if (a.acknowledged) return false
    const ttl = (a.ttl_minutes ?? ALERTS_DEFAULT_TTL_MINUTES) * 60 * 1000
    const created = new Date(a.timestamp).getTime()
    return now - created < ttl
  })
}

export function aliveSessions(state: State): LiveSession[] {
  return (state.live_sessions ?? []).filter((s) => s.alive !== false)
}

export function toOODAContext(state: State, recentChanges: ChangelogEntry[]): string {
  const s = summary(state)
  const lines: string[] = ["<situation-awareness>"]

  // Changes
  if (recentChanges.length > 0) {
    lines.push(`  Recent changes: ${recentChanges.length} mutations since last turn`)
    const byType = new Map<string, number>()
    for (const e of recentChanges) {
      byType.set(e.entity_type, (byType.get(e.entity_type) ?? 0) + 1)
    }
    lines.push(`    ${[...byType.entries()].map(([k, v]) => `${k}:${v}`).join(" ")}`)
  }

  // Coverage
  const totalHosts = s.hosts_discovered
  const enumerated = Object.values(state.hosts).filter((h) => h.services.length > 0).length
  const assessed = Object.values(state.hosts).filter((h) => h.vulns.length > 0).length
  const accessed = s.hosts_compromised
  lines.push(`  Coverage: ${totalHosts} hosts → ${enumerated} enumerated → ${assessed} assessed → ${accessed} compromised`)
  if (s.unchecked_services > 0) lines.push(`  Gaps: ${s.unchecked_services} services without version info`)

  // Unvalidated findings
  const unval = unvalidatedVulns(state)
  if (unval.length > 0) lines.push(`  Unvalidated: ${unval.length} finding(s) need critic review`)

  // Alerts
  const alerts = activeAlerts(state)
  if (alerts.length > 0) {
    lines.push(`  ALERTS (${alerts.length}):`)
    for (const a of alerts.slice(0, 5)) {
      lines.push(`    [${a.severity.toUpperCase()}] ${a.title}${a.host_ip ? ` on ${a.host_ip}` : ""}${a.source_agent ? ` (from ${a.source_agent})` : ""}`)
    }
    if (alerts.length > 5) lines.push(`    ... and ${alerts.length - 5} more`)
  }

  // Live sessions
  const sessions = aliveSessions(state)
  if (sessions.length > 0) {
    lines.push(`  Live sessions (${sessions.length}):`)
    for (const sess of sessions) {
      const detail = sess.session_type === "tunnel" || sess.session_type === "port_forward"
        ? ` → ${sess.remote_target ?? "?"}${sess.local_port ? ` local:${sess.local_port}` : ""}`
        : sess.username ? ` as ${sess.username}` : ""
      lines.push(`    ${sess.session_type}: ${sess.host_ip}${sess.port ? `:${sess.port}` : ""}${detail}`)
    }
  }

  // Network segments
  const segments = state.network_segments ?? []
  if (segments.length > 0) {
    lines.push(`  Network segments (${segments.length}):`)
    for (const seg of segments) {
      const pivot = seg.pivot_host ? ` via ${seg.pivot_host}` : ""
      const reach = seg.reachable_from?.length ? ` reachable_from:[${seg.reachable_from.join(",")}]` : ""
      lines.push(`    ${seg.id}: ${seg.cidr}${seg.vlan !== undefined ? ` VLAN:${seg.vlan}` : ""}${pivot}${reach}`)
    }
  }

  // Ready tasks
  const pending = state.task_tree.filter((t) => t.status === "pending")
  const inProgress = state.task_tree.filter((t) => t.status === "in_progress")
  if (inProgress.length > 0 || pending.length > 0) {
    lines.push(`  Tasks: ${inProgress.length} in-progress, ${pending.length} pending`)
  }

  // Objectives progress
  if (state.objectives) {
    const objs = Object.values(state.objectives)
    const completed = objs.filter((o) => o.status === "completed").length
    const blocked = objs.filter((o) => o.status === "blocked").length
    if (objs.length > 0) {
      lines.push(`  Objectives: ${completed}/${objs.length} completed${blocked > 0 ? `, ${blocked} blocked` : ""}`)
    }
  }

  lines.push("</situation-awareness>")
  return lines.join("\n")
}

export function summary(state: State) {
  const hostCount = Object.keys(state.hosts).length
  const compromised = Object.values(state.hosts).filter((h) => h.access.length > 0).length
  const vulnCount = Object.values(state.hosts).reduce((sum, h) => sum + h.vulns.length, 0)
  const credCount = Object.keys(state.credentials).length
  const flagCount = state.flags.length
  const uncheckedServices = Object.values(state.hosts).reduce(
    (sum, h) => sum + h.services.filter((s) => !s.version).length,
    0,
  )
  const objectives = state.objectives ? Object.values(state.objectives) : []
  return {
    hosts_discovered: hostCount,
    hosts_compromised: compromised,
    vulnerabilities: vulnCount,
    credentials: credCount,
    flags: flagCount,
    attack_steps: state.attack_path.length,
    unchecked_services: uncheckedServices,
    current_phase: state.current_phase,
    mode: state.mode,
    objectives_total: objectives.length,
    objectives_completed: objectives.filter((o) => o.status === "completed").length,
    objectives_in_progress: objectives.filter((o) => o.status === "in_progress").length,
    objectives_blocked: objectives.filter((o) => o.status === "blocked").length,
  }
}

export function decisionSummary(decisions: Decision[]): {
  total: number
  successful: number
  failed: number
  pending: number
  failedVectors: string[]
} {
  const successful = decisions.filter((d) => d.outcome === "successful").length
  const failed = decisions.filter((d) => d.outcome === "failed")
  const pending = decisions.filter((d) => !d.outcome || d.outcome === "pending").length
  return {
    total: decisions.length,
    successful,
    failed: failed.length,
    pending,
    failedVectors: failed.slice(-5).map((d) => d.decision),
  }
}

// Compact block for both coordinator and subagents. Shows vectors already settled
// so no agent re-opens a dead end. `resolved`/`confirmed` are hard "don't retest";
// `blocked` lists the precondition; `attempted` is advisory (retry only with a new
// technique). Kept small — this rides in every turn's context for every role.
// NEW-1: this block is injected EVERY turn for both roles and was the single
// heaviest volatile block on a real engagement (~3.5k tok, 62 vectors) — the
// verbose per-vector evidence strings dominated. Keep the anti-re-test guarantee
// but bound the cost: (a) drop CONFIRMED vectors — those are successes already
// recorded as vulnerabilities, not dead ends to avoid; (b) clip evidence/revisit
// text; (c) tighter cap. Everything (incl. confirmed + full evidence) stays in
// `state_query resolved_vectors`.
export function toResolvedVectorsContext(state: State, max = 30): string | undefined {
  const all = state.resolved_vectors ?? []
  if (all.length === 0) return undefined
  const vectors = all.filter((v) => v.status !== "confirmed")
  if (vectors.length === 0) return undefined
  const rank: Record<string, number> = { resolved: 0, blocked: 1, attempted: 2 }
  const sorted = [...vectors].sort((a, b) => (rank[a.status] ?? 9) - (rank[b.status] ?? 9))
  const shown = sorted.slice(0, max)
  const clip = (s: string) => (s.length > 90 ? s.slice(0, 90) + "…" : s)
  const lines = [
    "<resolved-vectors>",
    // NEW-2: license informed re-testing. The ledger kills BLIND repetition, not
    // evidence-driven reconsideration — a RESOLVED verdict only holds for the info
    // known when it was made (e.g. tested unauthenticated).
    "Already settled — do not repeat these BLINDLY. RESOLVED = dead given the info known then; re-open one ONLY if you now have NEW leverage that wasn't available at resolution (fresh creds/access, a new technique/exploit, or the target changed). BLOCKED: honor its precondition. ATTEMPTED: retry only with a genuinely new technique.",
  ]
  for (const v of shown) {
    const n = v.attempts && v.attempts > 1 ? ` x${v.attempts}` : ""
    const why =
      v.status === "blocked" && v.revisit_when
        ? ` (revisit: ${clip(v.revisit_when)})`
        : v.evidence
          ? ` — ${clip(v.evidence)}`
          : ""
    lines.push(`  [${v.status.toUpperCase()}] ${v.target} :: ${v.vector}${n}${why}`)
    // For vectors still in progress, surface the techniques already tried so a
    // re-spawn / post-compaction turn does not repeat the same dead-end sub-attempts.
    if (v.status === "attempted" || v.status === "blocked") {
      const failed = (v.attempt_log ?? []).filter((a) => a.outcome === "failed").slice(-6)
      for (const a of failed) {
        lines.push(`      ✗ tried: ${a.technique}${a.detail ? ` — ${a.detail}` : ""} (don't repeat)`)
      }
    }
  }
  const hidden = all.length - shown.length
  if (hidden > 0) lines.push(`  … +${hidden} more (incl. confirmed) — state_query resolved_vectors`)
  lines.push("</resolved-vectors>")
  return lines.join("\n")
}

export interface CompactContextOpts {
  maxVulnsPerHost?: number
  maxServicesPerHost?: number
  maxRelationships?: number
  maxObjectives?: number
  excludeOODAFields?: boolean
}

const SEVERITY_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 }

export function toCompactContext(state: State, maxHosts = 20, opts?: CompactContextOpts): string {
  const maxVulns = opts?.maxVulnsPerHost ?? 10
  const maxServices = opts?.maxServicesPerHost ?? 15
  const maxRels = opts?.maxRelationships ?? 30
  const maxObjs = opts?.maxObjectives ?? 10
  const excludeOODA = opts?.excludeOODAFields ?? true

  const s = summary(state)
  const data: Record<string, unknown> = {
    scope: { targets: state.scope.targets, excludes: state.scope.excludes, ...(state.scope.discovered_targets?.length ? { discovered: state.scope.discovered_targets } : {}) },
    summary: s,
    phase: state.current_phase,
    mode: state.mode,
    ...(state.goal ? { goal: { text: state.goal.text, status: state.goal.status } } : {}),
    hosts: {} as Record<string, unknown>,
  }

  const hostEntries = Object.entries(state.hosts).slice(0, maxHosts)
  for (const [ip, host] of hostEntries) {
    const shownServices = host.services.slice(0, maxServices)
    const h: Record<string, unknown> = {
      services: shownServices.map((svc) => ({
        port: svc.port,
        service: svc.service,
        version: svc.version || undefined,
      })),
    }
    if (host.services.length > maxServices) h.services_omitted = host.services.length - maxServices
    if (host.hostname) h.hostname = host.hostname
    if (host.os) h.os = host.os
    if (host.vulns.length > 0) {
      const sorted = [...host.vulns].sort((a, b) =>
        (SEVERITY_ORDER[a.severity ?? "medium"] ?? 3) - (SEVERITY_ORDER[b.severity ?? "medium"] ?? 3),
      )
      const shown = sorted.slice(0, maxVulns)
      h.vulns = shown.map((v) => ({
        title: v.title,
        severity: v.severity,
        status: v.status,
        ...(v.confidence !== undefined ? { conf: v.confidence } : {}),
        ...(v.evidence_items?.length ? {
          evidence_count: v.evidence_items.length,
          verified_by: [...new Set(v.evidence_items.filter((e) => e.verification_status === "verified").map((e) => e.source_agent).filter(Boolean))],
        } : {}),
      }))
      if (host.vulns.length > maxVulns) h.vulns_omitted = host.vulns.length - maxVulns
    }
    if (host.access.length > 0)
      h.access = host.access.map((a) => ({
        type: a.access_type,
        user: a.username,
        level: a.level,
        ...(a.confidence !== undefined ? { conf: a.confidence } : {}),
      }))
    ;(data.hosts as Record<string, unknown>)[ip] = h
  }
  if (Object.keys(state.hosts).length > maxHosts) data.hosts_omitted = Object.keys(state.hosts).length - maxHosts

  if (state.domain) {
    const dom: Record<string, unknown> = { name: state.domain.domain_name }
    if (state.domain.forest) dom.forest = state.domain.forest
    if (state.domain.domain_controllers?.length) dom.dcs = state.domain.domain_controllers
    if (state.domain.domain_admins?.length) dom.admins = state.domain.domain_admins
    if (state.domain.trusts?.length) dom.trusts = state.domain.trusts.map((t) => t.target_domain)
    data.domain = dom
  }

  if (state.flags.length > 0) data.flags = state.flags
  if (state.task_tree.length > 0) {
    const pending = state.task_tree.filter((t) => t.status === "pending" || t.status === "in_progress")
    if (pending.length > 0) data.pending_tasks = pending.map((t) => ({ id: t.id, desc: t.description, target: t.target }))
  }

  if (state.objectives) {
    const objs = Object.values(state.objectives)
    if (objs.length > 0) {
      const priorityOrder: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 }
      const sorted = [...objs].sort((a, b) => {
        if (a.status === "completed" && b.status !== "completed") return 1
        if (a.status !== "completed" && b.status === "completed") return -1
        return (priorityOrder[a.priority ?? "medium"] ?? 2) - (priorityOrder[b.priority ?? "medium"] ?? 2)
      })
      const shown = sorted.slice(0, maxObjs)
      data.objectives = shown.map((o) => {
        const compact: Record<string, unknown> = { id: o.id, title: o.title, status: o.status }
        if (o.priority) compact.priority = o.priority
        if (o.category) compact.category = o.category
        if (o.target_hosts && o.target_hosts.length > 0) compact.target_hosts = o.target_hosts
        if (o.flags && o.flags.length > 0) compact.flags = o.flags
        if (o.evidence) compact.evidence = o.evidence
        return compact
      })
      const completed = objs.filter((o) => o.status === "completed").length
      data.objectives_progress = `${completed}/${objs.length} completed`
      if (objs.length > maxObjs) data.objectives_omitted = objs.length - maxObjs
    }
  }

  if (state.relationships && state.relationships.length > 0) {
    const shown = state.relationships.slice(-maxRels)
    data.relationships = shown.map((r) => `${r.source_type}:${r.source_id}-[${r.rel_type}]->${r.target_type}:${r.target_id}`)
    if (state.relationships.length > maxRels) data.relationships_omitted = state.relationships.length - maxRels
  }

  if (!excludeOODA) {
    const alerts = activeAlerts(state)
    if (alerts.length > 0) {
      data.alerts = alerts.map((a) => ({
        severity: a.severity,
        title: a.title,
        ...(a.host_ip ? { host: a.host_ip } : {}),
        ...(a.source_agent ? { from: a.source_agent } : {}),
      }))
    }

    const sessions = aliveSessions(state)
    if (sessions.length > 0) {
      data.live_sessions = sessions.map((s) => ({
        type: s.session_type,
        host: s.host_ip,
        ...(s.port ? { port: s.port } : {}),
        ...(s.username ? { user: s.username } : {}),
        ...(s.remote_target ? { target: s.remote_target } : {}),
      }))
    }

    const segments = state.network_segments ?? []
    if (segments.length > 0) {
      data.network_segments = segments.map((seg) => ({
        id: seg.id,
        cidr: seg.cidr,
        ...(seg.vlan !== undefined ? { vlan: seg.vlan } : {}),
        ...(seg.pivot_host ? { pivot: seg.pivot_host } : {}),
        ...(seg.reachable_from?.length ? { reachable_from: seg.reachable_from } : {}),
      }))
    }
  }

  // Minified, not pretty-printed: 2-space indentation added ~20-30% tokens for
  // no comprehension benefit to the model. See redesign QW2.
  return JSON.stringify(data)
}
