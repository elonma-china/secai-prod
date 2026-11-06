export * as KnowledgeSchema from "./schema"

import { Schema } from "effect"

export const ToolEffectiveness = Schema.Struct({
  tool: Schema.String,
  technique: Schema.String,
  service_fingerprint: Schema.String,
  attempts: Schema.Number,
  successes: Schema.Number,
  last_used: Schema.String,
  notes: Schema.optional(Schema.String),
}).annotate({ identifier: "Knowledge.ToolEffectiveness" })
export type ToolEffectiveness = typeof ToolEffectiveness.Type

export const FPSignature = Schema.Struct({
  id: Schema.String,
  vuln_pattern: Schema.String,
  service_pattern: Schema.optional(Schema.String),
  reporting_tool: Schema.String,
  reason: Schema.String,
  occurrences: Schema.Number,
  last_seen: Schema.String,
}).annotate({ identifier: "Knowledge.FPSignature" })
export type FPSignature = typeof FPSignature.Type

export const KnowledgeState = Schema.Struct({
  version: Schema.Number,
  updated_at: Schema.String,
  tool_effectiveness: Schema.Array(ToolEffectiveness),
  fp_signatures: Schema.Array(FPSignature),
  engagements_reflected: Schema.Number,
}).annotate({ identifier: "Knowledge.KnowledgeState" })
export type KnowledgeState = typeof KnowledgeState.Type

export const TOOL_EFFECTIVENESS_MAX = 500
export const FP_SIGNATURES_MAX = 200
