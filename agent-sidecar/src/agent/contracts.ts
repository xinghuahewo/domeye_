import { Type, type Static } from 'typebox'

const Sha256 = Type.String({ pattern: '^sha256:[a-f0-9]{64}$' })
const Identifier = Type.String({ minLength: 1, maxLength: 256 })
const Timestamp = Type.String({
  pattern: '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,3})?Z$',
})

export const DomeyeDataIdentitySchema = Type.Object({
  event_type: Type.Literal('country_outage'),
  incident_id: Identifier,
  publication_id: Identifier,
  revision: Type.Integer({ minimum: 1 }),
  collector_id: Type.Literal('rrc25'),
  cohort_id: Identifier,
  country_code: Type.String({ pattern: '^[A-Z]{2}$' }),
  window_start_utc: Timestamp,
  window_end_utc: Timestamp,
  data_through: Timestamp,
  is_final_in_data_range: Type.Boolean(),
  lifecycle_state: Type.Literal('event_end_unknown'),
}, { additionalProperties: false })

export type DomeyeDataIdentity = Static<typeof DomeyeDataIdentitySchema>

export const DomeyeSemanticGoalSchema = Type.Object({
  schema_version: Type.Literal('domeye_agent_semantic_goal_v1'),
  goal_id: Identifier,
  requested_text: Type.String({ minLength: 1, maxLength: 2_000 }),
  objective: Type.Literal('find_fixed_visible_ipv4_series_extrema'),
  metric: Type.Literal('fixed_visible_ipv4_address_count'),
  data_identity: DomeyeDataIdentitySchema,
  created_at_utc: Timestamp,
}, { additionalProperties: false })

export type DomeyeSemanticGoal = Static<typeof DomeyeSemanticGoalSchema>

export const DomeyeGoalStateSchema = Type.Object({
  schema_version: Type.Literal('domeye_agent_goal_state_v1'),
  goal_id: Identifier,
  state_revision: Type.Integer({ minimum: 1 }),
  status: Type.Union([
    Type.Literal('active'),
    Type.Literal('answer_pending'),
    Type.Literal('satisfied'),
    Type.Literal('clarification_required'),
    Type.Literal('stopped'),
  ]),
  completed_capability_ids: Type.Array(Type.Union([
    Type.Literal('CAP-006'),
    Type.Literal('CAP-016'),
  ]), { uniqueItems: true, maxItems: 2 }),
  artifact_ids: Type.Array(Identifier, { uniqueItems: true, maxItems: 2 }),
  finding_ids: Type.Array(Identifier, { uniqueItems: true, maxItems: 1 }),
  last_observation_id: Type.Union([Identifier, Type.Null()]),
  updated_at_utc: Timestamp,
}, { additionalProperties: false })

export type DomeyeGoalState = Static<typeof DomeyeGoalStateSchema>

const ProposalCommon = {
  schema_version: Type.Literal('domeye_agent_capability_proposal_v1'),
  goal_id: Identifier,
  goal_state_revision: Type.Integer({ minimum: 1 }),
  rationale: Type.String({ minLength: 1, maxLength: 500 }),
}

export const DomeyeReadSeriesProposalSchema = Type.Object({
  ...ProposalCommon,
  capability_id: Type.Literal('CAP-006'),
  input: Type.Object({
    metric: Type.Literal('fixed_visible_ipv4_address_count'),
  }, { additionalProperties: false }),
}, { additionalProperties: false })

export const DomeyeSeriesExtremaProposalSchema = Type.Object({
  ...ProposalCommon,
  capability_id: Type.Literal('CAP-016'),
  input: Type.Object({
    metric: Type.Literal('fixed_visible_ipv4_address_count'),
    source_artifact_id: Identifier,
    tie_policy: Type.Literal('first_observed_occurrence'),
  }, { additionalProperties: false }),
}, { additionalProperties: false })

export const DomeyeCapabilityProposalSchema = Type.Union([
  DomeyeReadSeriesProposalSchema,
  DomeyeSeriesExtremaProposalSchema,
])

/**
 * 供应方 Tool API 要求参数 Schema 顶层明确为 object。该捕获 Schema
 * 只负责传输；DomeyeCapabilityProposalSchema 仍在工具执行前做严格配对校验。
 */
export const DomeyeCapabilityProposalCaptureSchema = Type.Object({
  ...ProposalCommon,
  capability_id: Type.Union([
    Type.Literal('CAP-006'),
    Type.Literal('CAP-016'),
  ]),
  input: Type.Union([
    DomeyeReadSeriesProposalSchema.properties.input,
    DomeyeSeriesExtremaProposalSchema.properties.input,
  ]),
}, { additionalProperties: false })

export type DomeyeCapabilityProposal = Static<
  typeof DomeyeCapabilityProposalSchema
>

export const DomeyeExecutionBindingSchema = Type.Object({
  execution_unit_id: Type.Union([
    Type.Literal('TOOL-03'),
    Type.Literal('OP-01'),
  ]),
  execution_unit_name: Type.Union([
    Type.Literal('read_metric_series'),
    Type.Literal('series_extrema'),
  ]),
  execution_unit_version: Type.Literal('1.0.0'),
  contract_digest: Sha256,
  implementation_digest: Sha256,
  semantic_digest: Sha256,
}, { additionalProperties: false })

export type DomeyeExecutionBinding = Static<
  typeof DomeyeExecutionBindingSchema
>

export const DomeyeTrustBindingSchema = Type.Object({
  principal: Type.Object({
    principal_id: Identifier,
    authorization_scopes: Type.Array(Identifier, { uniqueItems: true }),
  }, { additionalProperties: false }),
  tenant_id: Type.Literal('domeye'),
  data_identity: DomeyeDataIdentitySchema,
  goal_state: Type.Object({
    goal_id: Identifier,
    state_revision: Type.Integer({ minimum: 1 }),
    state_digest: Sha256,
  }, { additionalProperties: false }),
  policy: Type.Object({
    policy_id: Identifier,
    policy_digest: Sha256,
  }, { additionalProperties: false }),
  registry: Type.Object({
    registry_snapshot_id: Identifier,
    registry_digest: Sha256,
  }, { additionalProperties: false }),
  budget: Type.Object({
    model_api_attempt_limit: Type.Literal(10),
    model_api_attempts_used: Type.Integer({ minimum: 0, maximum: 10 }),
    approved_action_limit: Type.Literal(2),
    approved_actions_used: Type.Integer({ minimum: 1, maximum: 2 }),
    cost_policy: Type.Literal('audit_only'),
    monetary_limit_usd: Type.Null(),
  }, { additionalProperties: false }),
  revocation: Type.Object({
    state: Type.Literal('not_revoked'),
    checked_at_utc: Timestamp,
  }, { additionalProperties: false }),
  occurred_action_ids: Type.Array(Identifier, { uniqueItems: true, maxItems: 1 }),
  action_history_digest: Sha256,
}, { additionalProperties: false })

export type DomeyeTrustBinding = Static<typeof DomeyeTrustBindingSchema>

const ActionCommon = {
  schema_version: Type.Literal('domeye_agent_interactive_action_v1'),
  action_id: Identifier,
  proposal_id: Identifier,
  proposal_sequence: Type.Integer({ minimum: 1 }),
  candidate_id: Identifier,
  trust_binding: DomeyeTrustBindingSchema,
  execution_binding: DomeyeExecutionBindingSchema,
  admitted_at_utc: Timestamp,
}

export const DomeyeInteractiveActionSchema = Type.Union([
  Type.Object({
    ...ActionCommon,
    capability_id: Type.Literal('CAP-006'),
    input: DomeyeReadSeriesProposalSchema.properties.input,
  }, { additionalProperties: false }),
  Type.Object({
    ...ActionCommon,
    capability_id: Type.Literal('CAP-016'),
    input: DomeyeSeriesExtremaProposalSchema.properties.input,
  }, { additionalProperties: false }),
])

export type DomeyeInteractiveAction = Static<
  typeof DomeyeInteractiveActionSchema
>

export const DomeyeMetricSeriesPayloadSchema = Type.Object({
  schema_version: Type.Literal('domeye_metric_series_artifact_v1'),
  metric: Type.Literal('fixed_visible_ipv4_address_count'),
  unit: Type.Literal('unique_ipv4_address'),
  population_definition: Type.Literal(
    'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union',
  ),
  timestamps_utc: Type.Array(Timestamp, { minItems: 1 }),
  values: Type.Array(Type.Union([
    Type.Integer({ minimum: 0 }),
    Type.Null(),
  ]), { minItems: 1 }),
  time_slot_count: Type.Integer({ minimum: 1 }),
  observed_point_count: Type.Integer({ minimum: 0 }),
  null_point_count: Type.Integer({ minimum: 0 }),
  completeness: Type.Object({
    state: Type.Union([
      Type.Literal('complete'),
      Type.Literal('incomplete'),
    ]),
    missing_slot_count: Type.Integer({ minimum: 0 }),
  }, { additionalProperties: false }),
  definition: Type.String({ minLength: 1 }),
  source_response_sha256: Sha256,
  evidence_refs: Type.Array(Identifier, { minItems: 1, uniqueItems: true }),
}, { additionalProperties: false })

export type DomeyeMetricSeriesPayload = Static<
  typeof DomeyeMetricSeriesPayloadSchema
>

const ExtremaKnown = Type.Object({
  schema_version: Type.Literal('domeye_series_extrema_artifact_v1'),
  result_state: Type.Literal('known'),
  metric: Type.Literal('fixed_visible_ipv4_address_count'),
  unit: Type.Literal('unique_ipv4_address'),
  tie_policy: Type.Literal('first_observed_occurrence'),
  time_slot_count: Type.Integer({ minimum: 1 }),
  observed_point_count: Type.Integer({ minimum: 1 }),
  null_point_count: Type.Integer({ minimum: 0 }),
  first: Type.Integer({ minimum: 0 }),
  first_at_utc: Timestamp,
  last: Type.Integer({ minimum: 0 }),
  last_at_utc: Timestamp,
  minimum: Type.Integer({ minimum: 0 }),
  minimum_at_utc: Timestamp,
  maximum: Type.Integer({ minimum: 0 }),
  maximum_at_utc: Timestamp,
  difference: Type.Integer({ minimum: 0 }),
  net_change: Type.Integer(),
  source_artifact_id: Identifier,
  evidence_refs: Type.Array(Identifier, { minItems: 1, uniqueItems: true }),
}, { additionalProperties: false })

const ExtremaEmpty = Type.Object({
  schema_version: Type.Literal('domeye_series_extrema_artifact_v1'),
  result_state: Type.Literal('empty_observed_set'),
  metric: Type.Literal('fixed_visible_ipv4_address_count'),
  unit: Type.Literal('unique_ipv4_address'),
  tie_policy: Type.Literal('first_observed_occurrence'),
  time_slot_count: Type.Integer({ minimum: 1 }),
  observed_point_count: Type.Literal(0),
  null_point_count: Type.Integer({ minimum: 1 }),
  first: Type.Null(),
  first_at_utc: Type.Null(),
  last: Type.Null(),
  last_at_utc: Type.Null(),
  minimum: Type.Null(),
  minimum_at_utc: Type.Null(),
  maximum: Type.Null(),
  maximum_at_utc: Type.Null(),
  difference: Type.Null(),
  net_change: Type.Null(),
  source_artifact_id: Identifier,
  evidence_refs: Type.Array(Identifier, { minItems: 1, uniqueItems: true }),
}, { additionalProperties: false })

export const DomeyeSeriesExtremaPayloadSchema = Type.Union([
  ExtremaKnown,
  ExtremaEmpty,
])

export type DomeyeSeriesExtremaPayload = Static<
  typeof DomeyeSeriesExtremaPayloadSchema
>

const ArtifactCommon = {
  schema_version: Type.Literal('domeye_agent_artifact_envelope_v1'),
  artifact_id: Identifier,
  candidate_id: Identifier,
  tenant_id: Type.Literal('domeye'),
  data_identity: DomeyeDataIdentitySchema,
  producer_action_id: Identifier,
  execution_binding: DomeyeExecutionBindingSchema,
  immutable: Type.Literal(true),
  content_digest: Sha256,
  created_at_utc: Timestamp,
}

export const DomeyeArtifactEnvelopeSchema = Type.Union([
  Type.Object({
    ...ArtifactCommon,
    artifact_kind: Type.Literal('metric_series'),
    payload: DomeyeMetricSeriesPayloadSchema,
  }, { additionalProperties: false }),
  Type.Object({
    ...ArtifactCommon,
    artifact_kind: Type.Literal('series_extrema'),
    payload: DomeyeSeriesExtremaPayloadSchema,
  }, { additionalProperties: false }),
])

export type DomeyeArtifactEnvelope = Static<
  typeof DomeyeArtifactEnvelopeSchema
>

export const DomeyeActionReceiptSchema = Type.Object({
  schema_version: Type.Literal('domeye_agent_action_receipt_v1'),
  receipt_id: Identifier,
  admission_receipt_id: Identifier,
  action_id: Identifier,
  proposal_id: Identifier,
  capability_id: Type.Union([
    Type.Literal('CAP-006'),
    Type.Literal('CAP-016'),
  ]),
  candidate_id: Identifier,
  tenant_id: Type.Literal('domeye'),
  data_identity: DomeyeDataIdentitySchema,
  execution_binding: DomeyeExecutionBindingSchema,
  status: Type.Union([
    Type.Literal('succeeded'),
    Type.Literal('failed'),
  ]),
  artifact_ids: Type.Array(Identifier, { uniqueItems: true, maxItems: 1 }),
  failure_code: Type.Union([Identifier, Type.Null()]),
  started_at_utc: Timestamp,
  completed_at_utc: Timestamp,
  receipt_digest: Sha256,
}, { additionalProperties: false })

export type DomeyeActionReceipt = Static<typeof DomeyeActionReceiptSchema>

export const DomeyeObservationFindingInputSchema = Type.Object({
  state: Type.Literal('ready'),
  source_artifact_ref: Identifier,
  extrema_artifact_ref: Identifier,
  extrema_result_state: Type.Literal('known'),
  next_owner: Type.Literal('domeye_typed_finding_builder'),
}, { additionalProperties: false })

const ObservationSeriesAvailableSummary = Type.Object({
  metric: Type.Literal('fixed_visible_ipv4_address_count'),
  unit: Type.Literal('unique_ipv4_address'),
  result_state: Type.Literal('series_available'),
  observed_point_count: Type.Integer({ minimum: 0 }),
  finding_input: Type.Null(),
}, { additionalProperties: false })

const ObservationKnownExtremaSummary = Type.Object({
  metric: Type.Literal('fixed_visible_ipv4_address_count'),
  unit: Type.Literal('unique_ipv4_address'),
  result_state: Type.Literal('known'),
  observed_point_count: Type.Integer({ minimum: 1 }),
  finding_input: DomeyeObservationFindingInputSchema,
}, { additionalProperties: false })

const ObservationEmptyExtremaSummary = Type.Object({
  metric: Type.Literal('fixed_visible_ipv4_address_count'),
  unit: Type.Literal('unique_ipv4_address'),
  result_state: Type.Literal('empty_observed_set'),
  observed_point_count: Type.Literal(0),
  finding_input: Type.Null(),
}, { additionalProperties: false })

const ObservationUnavailableSummary = Type.Object({
  metric: Type.Literal('fixed_visible_ipv4_address_count'),
  unit: Type.Null(),
  result_state: Type.Literal('unavailable'),
  observed_point_count: Type.Null(),
  finding_input: Type.Null(),
}, { additionalProperties: false })

const ObservationCommon = {
  schema_version: Type.Literal('domeye_agent_capability_observation_v1'),
  observation_id: Identifier,
  data_identity: DomeyeDataIdentitySchema,
  created_at_utc: Timestamp,
}

export const DomeyeCapabilityObservationSchema = Type.Union([
  Type.Object({
    ...ObservationCommon,
    action_id: Identifier,
    capability_id: Type.Literal('CAP-006'),
    status: Type.Literal('succeeded'),
    reason_code: Type.Null(),
    artifact_ref: Identifier,
    safe_summary: ObservationSeriesAvailableSummary,
  }, { additionalProperties: false }),
  Type.Object({
    ...ObservationCommon,
    action_id: Identifier,
    capability_id: Type.Literal('CAP-016'),
    status: Type.Literal('succeeded'),
    reason_code: Type.Null(),
    artifact_ref: Identifier,
    safe_summary: ObservationKnownExtremaSummary,
  }, { additionalProperties: false }),
  Type.Object({
    ...ObservationCommon,
    action_id: Identifier,
    capability_id: Type.Literal('CAP-016'),
    status: Type.Literal('succeeded'),
    reason_code: Type.Null(),
    artifact_ref: Identifier,
    safe_summary: ObservationEmptyExtremaSummary,
  }, { additionalProperties: false }),
  Type.Object({
    ...ObservationCommon,
    action_id: Identifier,
    capability_id: Type.Union([
      Type.Literal('CAP-006'),
      Type.Literal('CAP-016'),
    ]),
    status: Type.Literal('failed'),
    reason_code: Identifier,
    artifact_ref: Type.Null(),
    safe_summary: ObservationUnavailableSummary,
  }, { additionalProperties: false }),
  Type.Object({
    ...ObservationCommon,
    action_id: Type.Null(),
    capability_id: Type.Union([
      Type.Literal('CAP-006'),
      Type.Literal('CAP-016'),
    ]),
    status: Type.Literal('rejected'),
    reason_code: Identifier,
    artifact_ref: Type.Null(),
    safe_summary: ObservationUnavailableSummary,
  }, { additionalProperties: false }),
])

export type DomeyeCapabilityObservation = Static<
  typeof DomeyeCapabilityObservationSchema
>

export const DomeyeTypedFindingSchema = Type.Object({
  schema_version: Type.Literal('domeye_agent_typed_finding_v1'),
  finding_id: Identifier,
  finding_type: Type.Literal('fixed_visible_ipv4_series_extrema'),
  value_state: Type.Union([
    Type.Literal('known'),
    Type.Literal('empty'),
    Type.Literal('incomplete'),
    Type.Literal('not_computable'),
  ]),
  candidate_id: Identifier,
  tenant_id: Type.Literal('domeye'),
  data_identity: DomeyeDataIdentitySchema,
  metric: Type.Literal('fixed_visible_ipv4_address_count'),
  unit: Type.Literal('unique_ipv4_address'),
  population_definition: Type.Literal(
    'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union',
  ),
  values: Type.Object({
    first: Type.Union([Type.Integer({ minimum: 0 }), Type.Null()]),
    first_at_utc: Type.Union([Timestamp, Type.Null()]),
    last: Type.Union([Type.Integer({ minimum: 0 }), Type.Null()]),
    last_at_utc: Type.Union([Timestamp, Type.Null()]),
    minimum: Type.Union([Type.Integer({ minimum: 0 }), Type.Null()]),
    minimum_at_utc: Type.Union([Timestamp, Type.Null()]),
    maximum: Type.Union([Type.Integer({ minimum: 0 }), Type.Null()]),
    maximum_at_utc: Type.Union([Timestamp, Type.Null()]),
    difference: Type.Union([Type.Integer({ minimum: 0 }), Type.Null()]),
    net_change: Type.Union([Type.Integer(), Type.Null()]),
  }, { additionalProperties: false }),
  time_slot_count: Type.Integer({ minimum: 1 }),
  observed_point_count: Type.Integer({ minimum: 0 }),
  null_point_count: Type.Integer({ minimum: 0 }),
  completeness_state: Type.Union([
    Type.Literal('complete'),
    Type.Literal('incomplete'),
  ]),
  limitation_codes: Type.Array(Identifier, { minItems: 1, uniqueItems: true }),
  tool_version: Type.Literal('1.0.0'),
  operator_version: Type.Literal('1.0.0'),
  artifact_refs: Type.Array(Identifier, { minItems: 2, maxItems: 2, uniqueItems: true }),
  receipt_refs: Type.Array(Identifier, { minItems: 2, maxItems: 2, uniqueItems: true }),
  evidence_refs: Type.Array(Identifier, { minItems: 1, uniqueItems: true }),
  result_digest: Sha256,
}, { additionalProperties: false })

export type DomeyeTypedFinding = Static<typeof DomeyeTypedFindingSchema>

export const DomeyeAnswerContextSchema = Type.Object({
  schema_version: Type.Literal('domeye_agent_answer_context_v1'),
  context_id: Identifier,
  candidate_id: Identifier,
  contract_version: Type.Literal('domeye.first-vertical-slice/v1.0'),
  contract_digest: Sha256,
  data_identity: DomeyeDataIdentitySchema,
  finding: DomeyeTypedFindingSchema,
  observer_scope_zh: Type.Literal('RRC25 单一观察点的 BGP 控制面观测'),
  mandatory_limitations_zh: Type.Array(Type.String({ minLength: 1 }), {
    minItems: 4,
    uniqueItems: true,
  }),
  forbidden_conclusions: Type.Array(Type.Union([
    Type.Literal('national_outage'),
    Type.Literal('real_user_impact'),
    Type.Literal('cause'),
    Type.Literal('responsibility'),
    Type.Literal('real_recovery'),
  ]), { minItems: 5, maxItems: 5, uniqueItems: true }),
  evidence_refs: Type.Array(Identifier, { minItems: 1, uniqueItems: true }),
  context_digest: Sha256,
}, { additionalProperties: false })

export type DomeyeAnswerContext = Static<typeof DomeyeAnswerContextSchema>

export const DomeyeRendererDraftSchema = Type.Object({
  schema_version: Type.Literal('domeye_agent_renderer_draft_v1'),
  context_id: Identifier,
  finding_id: Identifier,
  candidate_id: Identifier,
  publication_id: Identifier,
  revision: Type.Integer({ minimum: 1 }),
  collector_id: Type.Literal('rrc25'),
  window_start_utc: Timestamp,
  window_end_utc: Timestamp,
  metric: Type.Literal('fixed_visible_ipv4_address_count'),
  unit: Type.Literal('unique_ipv4_address'),
  values: DomeyeTypedFindingSchema.properties.values,
  observer_scope_zh: Type.String({ minLength: 1 }),
  limitations_zh: Type.Array(Type.String({ minLength: 1 }), { minItems: 1 }),
  evidence_refs: Type.Array(Identifier, { minItems: 1, uniqueItems: true }),
  text: Type.String({ minLength: 1, maxLength: 4_000 }),
}, { additionalProperties: false })

export type DomeyeRendererDraft = Static<typeof DomeyeRendererDraftSchema>

export const DomeyeResponseGuardDecisionSchema = Type.Union([
  Type.Object({
    schema_version: Type.Literal('domeye_agent_response_guard_v1'),
    decision: Type.Literal('pass'),
    reason_codes: Type.Array(Identifier, { maxItems: 0 }),
  }, { additionalProperties: false }),
  Type.Object({
    schema_version: Type.Literal('domeye_agent_response_guard_v1'),
    decision: Type.Literal('block'),
    reason_codes: Type.Array(Identifier, { minItems: 1, uniqueItems: true }),
  }, { additionalProperties: false }),
])

export type DomeyeResponseGuardDecision = Static<
  typeof DomeyeResponseGuardDecisionSchema
>

/**
 * Goal Disposition 专用工具的完整输入合同。顶层固定为 object，且拒绝额外字段；
 * 运行时只接受通过该合同的工具调用，不从 assistant 文本猜测 JSON。
 */
export const DomeyeGoalDispositionSchema = Type.Object({
  schema_version: Type.Literal('domeye_agent_goal_disposition_v1'),
  goal_id: Identifier,
  goal_state_revision: Type.Integer({ minimum: 1 }),
  disposition: Type.Union([
    Type.Literal('goal_satisfied'),
    Type.Literal('clarification_required'),
    Type.Literal('stopped'),
  ]),
  reason_code: Type.String({
    pattern: '^[a-z][a-z0-9_]{0,63}$',
  }),
}, { additionalProperties: false })

export type DomeyeGoalDisposition = Static<
  typeof DomeyeGoalDispositionSchema
>
