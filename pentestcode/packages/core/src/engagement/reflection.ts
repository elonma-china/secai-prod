export * as ReflectionSchema from "./reflection"

import { Schema } from "effect"

export const TechniqueOutcome = Schema.Struct({
  technique: Schema.String,
  target_service: Schema.optional(Schema.String),
  tool_used: Schema.optional(Schema.String),
  outcome: Schema.Literals(["success", "partial", "fail"]),
  notes: Schema.optional(Schema.String),
}).annotate({ identifier: "Engagement.TechniqueOutcome" })
export type TechniqueOutcome = typeof TechniqueOutcome.Type

export const FPPattern = Schema.Struct({
  vuln_title_pattern: Schema.String,
  service_fingerprint: Schema.optional(Schema.String),
  reporting_tool: Schema.String,
  reason: Schema.String,
  confidence: Schema.optional(Schema.Number),
}).annotate({ identifier: "Engagement.FPPattern" })
export type FPPattern = typeof FPPattern.Type

export const LearnedTechnique = Schema.Struct({
  name: Schema.String,
  description: Schema.String,
  applicable_when: Schema.Array(Schema.String),
  steps: Schema.Array(Schema.String),
  tools_required: Schema.optional(Schema.Array(Schema.String)),
  tags: Schema.optional(Schema.Array(Schema.String)),
  source_engagement: Schema.String,
}).annotate({ identifier: "Engagement.LearnedTechnique" })
export type LearnedTechnique = typeof LearnedTechnique.Type

export const ReflectionStats = Schema.Struct({
  hosts_total: Schema.Number,
  hosts_compromised: Schema.Number,
  vulns_found: Schema.Number,
  vulns_confirmed: Schema.Number,
  creds_found: Schema.Number,
  flags_captured: Schema.Number,
  objectives_completed: Schema.Number,
}).annotate({ identifier: "Engagement.ReflectionStats" })
export type ReflectionStats = typeof ReflectionStats.Type

export const EngagementReflection = Schema.Struct({
  id: Schema.String,
  engagement_name: Schema.String,
  engagement_id: Schema.String,
  timestamp: Schema.String,
  phases_reached: Schema.Array(Schema.String),
  stats: ReflectionStats,
  successful_techniques: Schema.Array(TechniqueOutcome),
  failed_techniques: Schema.Array(TechniqueOutcome),
  false_positive_patterns: Schema.Array(FPPattern),
  learned_techniques: Schema.Array(LearnedTechnique),
  strategic_observations: Schema.Array(Schema.String),
  generated_skills: Schema.optional(Schema.Array(Schema.String)),
}).annotate({ identifier: "Engagement.EngagementReflection" })
export type EngagementReflection = typeof EngagementReflection.Type

export const REFLECTIONS_MAX = 50
