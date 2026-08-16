import {
  p2S1CanonicalJson,
  p2S1Digest,
  type P2S1PublicationIdentity,
} from './p2-s1-trusted-receipt-store.js'

type JsonObject = Record<string, unknown>

const PROPOSAL_SCHEMA = 'country_outage_p2_s1_registry_proposal_v1'
const ADMISSION_SCHEMA = 'country_outage_p2_s1_registry_proposal_admission_v1'
const WAVE_HANDLER_MANIFEST_SCHEMA = 'country_outage_p2_s1_wave_handler_manifest_v1'
const WAVE_TEST_RECEIPT_SCHEMA = 'country_outage_p2_s1_unit_test_evidence_v1'
const WAVE_SNAPSHOT_SCHEMA = 'country_outage_p2_s1_registry_wave_snapshot_v1'
const WAVE_ADMISSION_SCHEMA = 'country_outage_p2_s1_registry_wave_admission_v1'
const DIGEST = /^sha256:[a-f0-9]{64}$/
const SNAPSHOT_ID = /^p2-s1-registry-proposal-sha256:[a-f0-9]{64}$/
const WAVE_MANIFEST_ID = /^p2-s1-handler-manifest-sha256:[a-f0-9]{64}$/
const WAVE_SNAPSHOT_ID = /^p2-s1-registry-wave-sha256:[a-f0-9]{64}$/
const CANDIDATE_ID = /^country-outage-p2-s1-s1d-6-[a-f0-9]{24}$/
export const P2S1_FROZEN_DESIGN_CANDIDATE_ID = 'country-outage-p2-s1-s1d-6-04135cee55b39ce5d574f7e4'
export const P2S1_FROZEN_DESIGN_CANDIDATE_DIGEST = 'sha256:d0256d9f1246191df2d48432655ea384acb2e5a6844b15a78f80e4c9f5e55e74'
const P2S1_FROZEN_TOOL_CATALOG_DIGEST = 'sha256:6b6ff25fa6e98d5e8aa897ee08d3fae51db899671aa6f2f0d5a46b91652bf8b3'
const P2S1_FROZEN_OPERATOR_CATALOG_DIGEST = 'sha256:784e8a585e6736bfd372b6f80374fce08fa17a4032bd9e03017413c4225f16bb'
const P2S1_FROZEN_DECOMPOSITION_DIGEST = 'sha256:4236714baa699872f4157543331415770286a00990929290aec02c555e09970a'
const P2S1_FROZEN_EXISTING_REGISTRY = {
  registry_snapshot_id: 'registry-snapshot-sha256:46e2c08b311b7b16e003a8eb56ec4f4fd2865ef4644a8bdbe7709c590c8514c2',
  snapshot_digest: 'sha256:46e2c08b311b7b16e003a8eb56ec4f4fd2865ef4644a8bdbe7709c590c8514c2',
  candidate_id: 'p2-s0b-763eb09a654b8b29',
  registry_revision: 2,
} as const

export const P2S1_V1_TOOL_IDS = [
  'TOOL-07', 'TOOL-08', 'TOOL-09', 'TOOL-10', 'TOOL-11', 'TOOL-12',
] as const

export const P2S1_V1_OPERATOR_IDS = [
  'OP-05', 'OP-06', 'OP-07', 'OP-08', 'OP-09', 'OP-10', 'OP-11', 'OP-12',
  'OP-13', 'OP-14', 'OP-15', 'OP-16', 'OP-17', 'OP-18', 'OP-19', 'OP-20',
  'OP-21', 'OP-22', 'OP-23', 'OP-24', 'OP-25', 'OP-26', 'OP-27', 'OP-28',
  'OP-29', 'OP-30', 'OP-31', 'OP-32', 'OP-33', 'OP-35', 'OP-36', 'OP-37',
  'OP-38', 'OP-39',
] as const

export const P2S1_V1_CONTROL_IDS = [
  'PLAN-CAP-01',
  'GATE-01', 'GATE-02', 'GATE-03', 'GATE-04', 'GATE-05',
  'BOUNDARY-01',
  'RENDERER-01', 'RENDERER-02', 'RENDERER-03', 'DELIVERY-01',
] as const

export const P2S1_DEFERRED_UNIT_IDS = ['PLAN-CAP-02', 'TOOL-13', 'OP-34'] as const

export const P2S1_W1_ACTIVATION_UNIT_IDS = [
  'TOOL-07', 'TOOL-08', 'TOOL-09', 'TOOL-10',
  'OP-05', 'OP-06', 'OP-07', 'OP-08', 'OP-09', 'OP-10', 'OP-11', 'OP-12',
  'OP-13', 'OP-14', 'OP-35', 'OP-36',
] as const

export const P2S1_W2_ACTIVATION_UNIT_IDS = [
  'TOOL-12',
  'OP-15', 'OP-16', 'OP-17', 'OP-18', 'OP-19', 'OP-20', 'OP-21',
  'OP-22', 'OP-23', 'OP-24', 'OP-25', 'OP-26', 'OP-27', 'OP-28',
] as const

export const P2S1_W3_ACTIVATION_UNIT_IDS = ['OP-38', 'OP-39'] as const

export const P2S1_W4_ACTIVATION_UNIT_IDS = [
  'TOOL-11',
  'OP-29', 'OP-30', 'OP-31', 'OP-32', 'OP-33', 'OP-37',
] as const

export const P2S1_W0_PROPOSAL_UNIT_IDS = [
  ...P2S1_V1_TOOL_IDS,
  ...P2S1_V1_OPERATOR_IDS,
  ...P2S1_V1_CONTROL_IDS,
  ...P2S1_DEFERRED_UNIT_IDS,
] as const

const V1_UNITS = new Set<string>([
  ...P2S1_V1_TOOL_IDS,
  ...P2S1_V1_OPERATOR_IDS,
  ...P2S1_V1_CONTROL_IDS,
])
const DEFERRED_UNITS = new Set<string>(P2S1_DEFERRED_UNIT_IDS)
const ALL_UNITS = new Set<string>(P2S1_W0_PROPOSAL_UNIT_IDS)
const EXPECTED_ATOMIC_CAPABILITY_BY_UNIT = new Map<string, string>([
  ['TOOL-07', 'read.fixed_cohort_members'],
  ['TOOL-08', 'read.prefix_states'],
  ['TOOL-09', 'read.as_states'],
  ['TOOL-10', 'read.new_prefix_states'],
  ['TOOL-11', 'read.materialized_route_states_at_time'],
  ['TOOL-12', 'read.window_path_associations'],
  ['TOOL-13', 'read.route_events'],
  ['OP-05', 'rank.as_severity'],
  ['OP-06', 'select.first_state_occurrence'],
  ['OP-07', 'derive.state_intervals'],
  ['OP-08', 'select.last_state_at_cutoff'],
  ['OP-09', 'select.peak_state_observation'],
  ['OP-10', 'compute.as_peak_complete_ratio'],
  ['OP-11', 'select.longest_interval'],
  ['OP-12', 'rank.as_first_threshold_crossing'],
  ['OP-13', 'rank.as_longest_duration'],
  ['OP-14', 'rank.as_peak_complete_ratio'],
  ['OP-15', 'path.locate_asn_positions'],
  ['OP-16', 'path.direct_neighbors'],
  ['OP-17', 'path.ordered_asn_relation'],
  ['OP-18', 'project.path_prefix_set'],
  ['OP-19', 'project.downstream_origin_set'],
  ['OP-20', 'project.canonical_path_set'],
  ['OP-21', 'project.peer_direction_set'],
  ['OP-22', 'count.unique_paths'],
  ['OP-23', 'count.unique_prefixes'],
  ['OP-24', 'count.unique_peer_directions'],
  ['OP-25', 'set.intersection'],
  ['OP-26', 'set.directional_difference'],
  ['OP-27', 'set.directional_coverage'],
  ['OP-28', 'set.jaccard'],
  ['OP-29', 'time.evidence_relation'],
  ['OP-30', 'vp.visibility_consistency'],
  ['OP-31', 'vp.origin_consistency'],
  ['OP-32', 'vp.path_consistency'],
  ['OP-33', 'join.new_prefix_route_state'],
  ['OP-34', 'classify.route_change'],
  ['OP-35', 'select.last_state_occurrence'],
  ['OP-36', 'detect.first_threshold_crossing'],
  ['OP-37', 'classify.evidence_consistency'],
  ['OP-38', 'time.intersect_state_interval_sets'],
  ['OP-39', 'project.fixed_cohort_prefix_set'],
  ['PLAN-CAP-01', 'plan.bind_output_to_argument'],
  ['PLAN-CAP-02', 'plan.expand_member_scoped_subplan'],
  ['GATE-01', 'validate.identity'],
  ['GATE-02', 'validate.evidence_refs'],
  ['GATE-03', 'validate.result_completeness'],
  ['GATE-04', 'validate.control_plane_boundary'],
  ['GATE-05', 'validate.prohibited_conclusions'],
  ['BOUNDARY-01', 'respond.boundary'],
  ['RENDERER-01', 'render.markdown'],
  ['RENDERER-02', 'render.csv'],
  ['RENDERER-03', 'render.json'],
  ['DELIVERY-01', 'deliver.commit_export'],
])

const P2S1_WAVE_UNIT_IDS = new Set<string>([
  ...P2S1_W1_ACTIVATION_UNIT_IDS,
  ...P2S1_W2_ACTIVATION_UNIT_IDS,
  ...P2S1_W3_ACTIVATION_UNIT_IDS,
  ...P2S1_W4_ACTIVATION_UNIT_IDS,
])

const P2S1_EXPECTED_HANDLER_BY_UNIT = new Map<string, string>([
  ['TOOL-07', 'python:backend.services.country_outage_p2_s1_tools.CountryOutageP2S1Tools.query_fixed_cohort_members'],
  ['TOOL-08', 'python:backend.services.country_outage_p2_s1_tools.CountryOutageP2S1Tools.query_prefix_states'],
  ['TOOL-09', 'python:backend.services.country_outage_p2_s1_tools.CountryOutageP2S1Tools.query_as_states'],
  ['TOOL-10', 'python:backend.services.country_outage_p2_s1_tools.CountryOutageP2S1Tools.query_new_prefix_states'],
  ['TOOL-12', 'python:backend.services.country_outage_p2_s1_tools.CountryOutageP2S1Tools.query_window_path_associations'],
  ['TOOL-11', 'python:backend.services.country_outage_p2_s1_tools.CountryOutageP2S1Tools.query_materialized_route_states_at_time'],
  ['OP-05', 'python:backend.services.country_outage_p2_s1_operators.op05_as_severity_rank'],
  ['OP-06', 'python:backend.services.country_outage_p2_s1_operators.op06_select_first_state_occurrence'],
  ['OP-07', 'python:backend.services.country_outage_p2_s1_operators.op07_derive_state_intervals'],
  ['OP-08', 'python:backend.services.country_outage_p2_s1_operators.op08_select_last_state_at_cutoff'],
  ['OP-09', 'python:backend.services.country_outage_p2_s1_operators.op09_select_peak_state_observation'],
  ['OP-10', 'python:backend.services.country_outage_p2_s1_operators.op10_compute_as_peak_complete_ratio'],
  ['OP-11', 'python:backend.services.country_outage_p2_s1_operators.op11_select_longest_interval'],
  ['OP-12', 'python:backend.services.country_outage_p2_s1_operators.op12_rank_as_first_threshold_crossing'],
  ['OP-13', 'python:backend.services.country_outage_p2_s1_operators.op13_rank_as_longest_duration'],
  ['OP-14', 'python:backend.services.country_outage_p2_s1_operators.op14_rank_as_peak_complete_ratio'],
  ['OP-15', 'python:backend.services.country_outage_p2_s1_operators.op15_locate_asn_positions'],
  ['OP-16', 'python:backend.services.country_outage_p2_s1_operators.op16_project_direct_path_neighbors'],
  ['OP-17', 'python:backend.services.country_outage_p2_s1_operators.op17_classify_ordered_asn_path_relation'],
  ['OP-18', 'python:backend.services.country_outage_p2_s1_operators.op18_project_path_prefix_set'],
  ['OP-19', 'python:backend.services.country_outage_p2_s1_operators.op19_project_observed_downstream_origin_set'],
  ['OP-20', 'python:backend.services.country_outage_p2_s1_operators.op20_project_canonical_path_set'],
  ['OP-21', 'python:backend.services.country_outage_p2_s1_operators.op21_project_peer_direction_set'],
  ['OP-22', 'python:backend.services.country_outage_p2_s1_operators.op22_count_unique_paths'],
  ['OP-23', 'python:backend.services.country_outage_p2_s1_operators.op23_count_unique_prefixes'],
  ['OP-24', 'python:backend.services.country_outage_p2_s1_operators.op24_count_unique_peer_directions'],
  ['OP-25', 'python:backend.services.country_outage_p2_s1_operators.op25_set_intersection'],
  ['OP-26', 'python:backend.services.country_outage_p2_s1_operators.op26_set_directional_difference'],
  ['OP-27', 'python:backend.services.country_outage_p2_s1_operators.op27_set_directional_coverage'],
  ['OP-28', 'python:backend.services.country_outage_p2_s1_operators.op28_set_jaccard'],
  ['OP-29', 'python:backend.services.country_outage_p2_s1_operators.op29_classify_temporal_evidence_relation'],
  ['OP-30', 'python:backend.services.country_outage_p2_s1_operators.op30_classify_vp_visibility_consistency'],
  ['OP-31', 'python:backend.services.country_outage_p2_s1_operators.op31_classify_vp_origin_consistency'],
  ['OP-32', 'python:backend.services.country_outage_p2_s1_operators.op32_classify_vp_path_consistency'],
  ['OP-33', 'python:backend.services.country_outage_p2_s1_operators.op33_join_new_prefix_route_state'],
  ['OP-35', 'python:backend.services.country_outage_p2_s1_operators.op35_select_last_state_occurrence'],
  ['OP-36', 'python:backend.services.country_outage_p2_s1_operators.op36_detect_first_threshold_crossing'],
  ['OP-37', 'python:backend.services.country_outage_p2_s1_operators.op37_classify_evidence_consistency'],
  ['OP-38', 'python:backend.services.country_outage_p2_s1_operators.op38_intersect_state_interval_sets'],
  ['OP-39', 'python:backend.services.country_outage_p2_s1_operators.op39_project_fixed_cohort_prefix_set'],
])

export type P2S1RegistryUnitKind = 'tool' | 'operator' | 'plan_capability' | 'control'
export type P2S1RegistryUnitState = 'proposed' | 'inactive' | 'deferred'

export interface P2S1RegistryDependency {
  unit_id: string
  unit_version: string
  source: 'same_proposal' | 'existing_registry'
  contract_digest: string
}

export interface P2S1RegistryProposalUnit {
  unit_id: string
  unit_kind: P2S1RegistryUnitKind
  version: string
  activation_state: P2S1RegistryUnitState
  atomic_capability_id: string
  contract_digest: string
  semantic_digest: string
  implementation_status: 'not_implemented'
  implementation_digest: null
  permission: 'country_outage:read' | 'country_outage:derive' | 'country_outage:plan' | 'country_outage:control'
  identity_constraints: {
    event_type: 'country_outage'
    collector_id: 'rrc25'
    publication_cardinality: 1
  }
  dependencies: P2S1RegistryDependency[]
}

export interface P2S1ExistingRegistryUnitBinding {
  unit_id: string
  version: string
  state: 'active'
  contract_digest: string
  implementation_digest: string
  semantic_digest: string
  permission: string
}

export interface P2S1ExistingRegistryBinding {
  registry_snapshot_id: string
  snapshot_digest: string
  candidate_id: string
  registry_revision: number
  unit_bindings: P2S1ExistingRegistryUnitBinding[]
}

export interface P2S1RegistryProposalPayload {
  candidate_id: string
  design_candidate_digest: string
  registry_revision: number
  activation_scope: 'w0_proposal_only'
  runtime_integration: 'governance_implemented_units_not_implemented'
  production_deployed: false
  permission_mode: 'read_only'
  external_data_allowed: false
  publication_identity: P2S1PublicationIdentity
  existing_registry_binding: P2S1ExistingRegistryBinding
  units: P2S1RegistryProposalUnit[]
}

export interface P2S1RegistryProposalSnapshot {
  schema_version: typeof PROPOSAL_SCHEMA
  registry_snapshot_id: string
  snapshot_digest: string
  created_at_utc: string
  snapshot_payload: P2S1RegistryProposalPayload
}

export interface P2S1RegistryProposalAdmissionReceipt {
  schema_version: typeof ADMISSION_SCHEMA
  receipt_digest: string
  status: 'admitted_as_inactive_proposal'
  registry_snapshot_id: string
  snapshot_digest: string
  registry_revision: number
  candidate_id: string
  design_candidate_digest: string
  publication_identity: P2S1PublicationIdentity
  proposed_unit_ids: string[]
  inactive_unit_ids: string[]
  deferred_denied_unit_ids: string[]
  execution_allowed_unit_ids: []
  execution_started: false
  production_deployed: false
}

export interface P2S1RegistryExpectedContext {
  candidate_id: string
  design_candidate_digest: string
  publication_identity: P2S1PublicationIdentity
  existing_registry_snapshot_id: string
  existing_registry_snapshot_digest: string
}

export type P2S1RegistryWaveId = 'W1' | 'W2' | 'W3' | 'W4'

export interface P2S1RegistryUnitTestEvidence {
  schema_version: typeof WAVE_TEST_RECEIPT_SCHEMA
  receipt_digest: string
  candidate_id: string
  design_candidate_digest: string
  wave_id: P2S1RegistryWaveId
  unit_id: string
  handler_id: string
  implementation_digest: string
  contract_digest: string
  semantic_digest: string
  structural_binding_contract_digest: string
  runner_receipt_digest: string
  runner_receipt_file_digest: string
  runner_receipt_path: string
  test_case_ids: string[]
  test_result: 'passed'
  tested_execution_count: number
}

export interface P2S1RegistryWaveHandlerBinding {
  unit_id: string
  handler_id: string
  implementation_digest: string
  contract_digest: string
  semantic_digest: string
  structural_binding_contract_digest: string
  dependencies: P2S1RegistryDependency[]
  test_evidence: P2S1RegistryUnitTestEvidence
}

export interface P2S1RegistryWaveHandlerManifestPayload {
  candidate_id: string
  design_candidate_digest: string
  wave_id: P2S1RegistryWaveId
  structural_binding_contract_digest: string
  handlers: P2S1RegistryWaveHandlerBinding[]
}

export interface P2S1RegistryWaveHandlerManifest {
  schema_version: typeof WAVE_HANDLER_MANIFEST_SCHEMA
  handler_manifest_id: string
  handler_manifest_digest: string
  manifest_payload: P2S1RegistryWaveHandlerManifestPayload
}

export interface P2S1RegistryWavePreviousSnapshotRef {
  registry_snapshot_id: string
  snapshot_digest: string
  registry_revision: number
}

export interface P2S1RegistryWaveSnapshotPayload {
  candidate_id: string
  design_candidate_digest: string
  registry_revision: number
  wave_id: P2S1RegistryWaveId
  activation_scope: 'complete_atomic_wave_binding_admission'
  permission_mode: 'read_only'
  external_data_allowed: false
  production_deployed: false
  publication_identity: P2S1PublicationIdentity
  proposal_snapshot_ref: P2S1RegistryWavePreviousSnapshotRef
  previous_snapshot_ref: P2S1RegistryWavePreviousSnapshotRef
  handler_manifest: P2S1RegistryWaveHandlerManifest
  admitted_wave_binding_unit_ids: string[]
  admitted_binding_unit_ids: string[]
}

export interface P2S1RegistryWaveSnapshot {
  schema_version: typeof WAVE_SNAPSHOT_SCHEMA
  registry_snapshot_id: string
  snapshot_digest: string
  created_at_utc: string
  snapshot_payload: P2S1RegistryWaveSnapshotPayload
}

export interface P2S1RegistryWaveAdmissionReceipt {
  schema_version: typeof WAVE_ADMISSION_SCHEMA
  receipt_digest: string
  status: 'admitted_complete_atomic_wave_bindings'
  wave_id: P2S1RegistryWaveId
  registry_snapshot_id: string
  snapshot_digest: string
  registry_revision: number
  previous_snapshot_ref: P2S1RegistryWavePreviousSnapshotRef
  candidate_id: string
  design_candidate_digest: string
  publication_identity: P2S1PublicationIdentity
  handler_manifest_id: string
  handler_manifest_digest: string
  structural_binding_contract_digest: string
  admitted_wave_binding_unit_ids: string[]
  admitted_binding_unit_ids: string[]
  execution_allowed_unit_ids: []
  partial_binding_admission: false
  execution_started: false
  production_deployed: false
}

export interface P2S1RegistryWaveBindingAdmissionContext extends P2S1RegistryExpectedContext {
  structural_binding_contract_digest: string
  implementation_digest_by_unit: Record<string, string>
  test_evidence_receipt_digest_by_unit: Record<string, string>
}

export class P2S1RegistryRuntimeError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message)
    this.name = 'P2S1RegistryRuntimeError'
  }
}

function object(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new P2S1RegistryRuntimeError('registry_proposal_invalid', `${label} 必须是对象`)
  }
  return value as JsonObject
}

function assertExactKeys(value: JsonObject, keys: readonly string[], label: string): void {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new P2S1RegistryRuntimeError('registry_proposal_invalid', `${label} 字段集合不符合冻结合同`)
  }
}

function requireDigest(value: unknown, label: string): asserts value is string {
  if (typeof value !== 'string' || !DIGEST.test(value)) {
    throw new P2S1RegistryRuntimeError('registry_proposal_invalid', `${label} 不是规范 SHA-256 摘要`)
  }
}

function requireNonempty(value: unknown, label: string): asserts value is string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new P2S1RegistryRuntimeError('registry_proposal_invalid', `${label} 必须是非空字符串`)
  }
}

function same(left: unknown, right: unknown): boolean {
  return p2S1CanonicalJson(left) === p2S1CanonicalJson(right)
}

function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value)
    for (const item of Object.values(value as Record<string, unknown>)) deepFreeze(item)
  }
  return value
}

function expectedKind(unitId: string): P2S1RegistryUnitKind {
  if (unitId.startsWith('TOOL-')) return 'tool'
  if (unitId.startsWith('OP-')) return 'operator'
  if (unitId.startsWith('PLAN-CAP-')) return 'plan_capability'
  return 'control'
}

function expectedPermission(kind: P2S1RegistryUnitKind): P2S1RegistryProposalUnit['permission'] {
  if (kind === 'tool') return 'country_outage:read'
  if (kind === 'operator') return 'country_outage:derive'
  if (kind === 'plan_capability') return 'country_outage:plan'
  return 'country_outage:control'
}

export function p2S1ExpectedDesignContractDigest(unitId: string): string {
  if (unitId.startsWith('TOOL-')) return P2S1_FROZEN_TOOL_CATALOG_DIGEST
  if (unitId.startsWith('OP-')) return P2S1_FROZEN_OPERATOR_CATALOG_DIGEST
  return P2S1_FROZEN_DECOMPOSITION_DIGEST
}

export function p2S1ExpectedAtomicCapabilityId(unitId: string): string {
  const value = EXPECTED_ATOMIC_CAPABILITY_BY_UNIT.get(unitId)
  if (!value) {
    throw new P2S1RegistryRuntimeError('registry_unit_population_invalid', `未知冻结单元：${unitId}`)
  }
  return value
}

export function p2S1ExpectedUnitSemanticDigest(unitId: string): string {
  const atomicCapabilityId = p2S1ExpectedAtomicCapabilityId(unitId)
  return p2S1Digest({
    design_candidate_digest: P2S1_FROZEN_DESIGN_CANDIDATE_DIGEST,
    unit_id: unitId,
    atomic_capability_id: atomicCapabilityId,
    contract_digest: p2S1ExpectedDesignContractDigest(unitId),
  })
}

function validatePublicationIdentity(value: unknown): P2S1PublicationIdentity {
  const identity = object(value, 'publication_identity')
  assertExactKeys(identity, [
    'event_type', 'incident_id', 'publication_id', 'revision', 'cohort_id',
    'collector_id', 'window_start_utc', 'window_end_utc', 'data_through_utc',
  ], 'publication_identity')
  if (
    identity.event_type !== 'country_outage'
    || identity.collector_id !== 'rrc25'
    || !Number.isSafeInteger(identity.revision)
    || (identity.revision as number) < 1
  ) {
    throw new P2S1RegistryRuntimeError('registry_boundary_violation', 'Registry proposal 越过 country_outage/RRC25 边界')
  }
  for (const field of [
    'incident_id', 'publication_id', 'cohort_id', 'window_start_utc',
    'window_end_utc', 'data_through_utc',
  ] as const) requireNonempty(identity[field], `publication_identity.${field}`)
  return identity as unknown as P2S1PublicationIdentity
}

function validateExistingRegistryBinding(value: unknown): P2S1ExistingRegistryBinding {
  const binding = object(value, 'existing_registry_binding')
  assertExactKeys(binding, [
    'registry_snapshot_id', 'snapshot_digest', 'candidate_id', 'registry_revision', 'unit_bindings',
  ], 'existing_registry_binding')
  requireNonempty(binding.registry_snapshot_id, 'existing_registry_binding.registry_snapshot_id')
  requireDigest(binding.snapshot_digest, 'existing_registry_binding.snapshot_digest')
  requireNonempty(binding.candidate_id, 'existing_registry_binding.candidate_id')
  if (!Number.isSafeInteger(binding.registry_revision) || (binding.registry_revision as number) < 1) {
    throw new P2S1RegistryRuntimeError('registry_proposal_invalid', 'existing Registry revision 无效')
  }
  if (
    binding.registry_snapshot_id !== P2S1_FROZEN_EXISTING_REGISTRY.registry_snapshot_id
    || binding.snapshot_digest !== P2S1_FROZEN_EXISTING_REGISTRY.snapshot_digest
    || binding.candidate_id !== P2S1_FROZEN_EXISTING_REGISTRY.candidate_id
    || binding.registry_revision !== P2S1_FROZEN_EXISTING_REGISTRY.registry_revision
  ) {
    throw new P2S1RegistryRuntimeError('registry_dependency_snapshot_mismatch', '既有 Registry 必须绑定冻结 S0B snapshot')
  }
  if (!Array.isArray(binding.unit_bindings)) {
    throw new P2S1RegistryRuntimeError('registry_proposal_invalid', 'existing Registry unit_bindings 必须是数组')
  }
  const keys = new Set<string>()
  for (const itemValue of binding.unit_bindings) {
    const item = object(itemValue, 'existing_registry_binding.unit')
    assertExactKeys(item, [
      'unit_id', 'version', 'state', 'contract_digest', 'implementation_digest',
      'semantic_digest', 'permission',
    ], 'existing_registry_binding.unit')
    requireNonempty(item.unit_id, 'existing unit_id')
    requireNonempty(item.version, 'existing version')
    if (item.state !== 'active') {
      throw new P2S1RegistryRuntimeError('registry_dependency_not_active', `既有依赖 ${String(item.unit_id)} 未 active`)
    }
    requireDigest(item.contract_digest, 'existing contract_digest')
    requireDigest(item.implementation_digest, 'existing implementation_digest')
    requireDigest(item.semantic_digest, 'existing semantic_digest')
    requireNonempty(item.permission, 'existing permission')
    const key = `${String(item.unit_id)}@${String(item.version)}`
    if (keys.has(key)) {
      throw new P2S1RegistryRuntimeError('registry_proposal_invalid', `既有依赖重复：${key}`)
    }
    keys.add(key)
  }
  if (
    binding.unit_bindings.length !== 1
    || binding.unit_bindings[0]?.unit_id !== 'TOOL-01'
    || binding.unit_bindings[0]?.version !== '1.0.0'
    || binding.unit_bindings[0]?.contract_digest !== 'sha256:fd9169810375f1f8181e9a7c8fbd7c8fdfe24e7715d79dd8f2c0f50a160d0b21'
    || binding.unit_bindings[0]?.implementation_digest !== 'sha256:72fc464bf871a9688c23bd550479440cdcd9c53ce8d724b73deb4bbec17c38aa'
    || binding.unit_bindings[0]?.semantic_digest !== 'sha256:cc510ef729059e8413cfcf1e263845900c92c378cea0b169b773f788010d9216'
    || binding.unit_bindings[0]?.permission !== 'country_outage:read'
  ) {
    throw new P2S1RegistryRuntimeError('registry_dependency_snapshot_mismatch', '既有 Registry TOOL-01 绑定与冻结 snapshot 不一致')
  }
  return binding as unknown as P2S1ExistingRegistryBinding
}

function validateUnits(
  unitsValue: unknown,
  existing: P2S1ExistingRegistryBinding,
): P2S1RegistryProposalUnit[] {
  if (!Array.isArray(unitsValue)) {
    throw new P2S1RegistryRuntimeError('registry_proposal_invalid', 'units 必须是数组')
  }
  const units = unitsValue.map((value) => object(value, 'unit'))
  const byId = new Map<string, JsonObject>()
  for (const unit of units) {
    assertExactKeys(unit, [
      'unit_id', 'unit_kind', 'version', 'activation_state', 'atomic_capability_id',
      'contract_digest', 'semantic_digest', 'implementation_status',
      'implementation_digest', 'permission', 'identity_constraints', 'dependencies',
    ], 'unit')
    requireNonempty(unit.unit_id, 'unit.unit_id')
    const unitId = unit.unit_id
    if (!ALL_UNITS.has(unitId) || byId.has(unitId)) {
      throw new P2S1RegistryRuntimeError('registry_unit_population_invalid', `W0 unit 人口包含未知或重复身份：${unitId}`)
    }
    byId.set(unitId, unit)
    requireNonempty(unit.version, `${unitId}.version`)
    if (unit.version !== '1.0.0-design') {
      throw new P2S1RegistryRuntimeError('registry_unit_contract_drift', `${unitId} 设计版本漂移`)
    }
    if (unit.atomic_capability_id !== EXPECTED_ATOMIC_CAPABILITY_BY_UNIT.get(unitId)) {
      throw new P2S1RegistryRuntimeError('registry_unit_contract_drift', `${unitId} atomic capability 漂移`)
    }
    requireDigest(unit.contract_digest, `${unitId}.contract_digest`)
    requireDigest(unit.semantic_digest, `${unitId}.semantic_digest`)
    if (
      unit.contract_digest !== p2S1ExpectedDesignContractDigest(unitId)
      || unit.semantic_digest !== p2S1ExpectedUnitSemanticDigest(unitId)
    ) {
      throw new P2S1RegistryRuntimeError('registry_unit_contract_drift', `${unitId} 未绑定冻结设计合同与语义`)
    }
    const kind = expectedKind(unitId)
    if (unit.unit_kind !== kind || unit.permission !== expectedPermission(kind)) {
      throw new P2S1RegistryRuntimeError('registry_permission_denied', `${unitId} 的 kind/permission 不符合冻结只读边界`)
    }
    const deferred = DEFERRED_UNITS.has(unitId)
    if (
      (deferred && unit.activation_state !== 'deferred')
      || (!deferred && !['proposed', 'inactive'].includes(String(unit.activation_state)))
    ) {
      throw new P2S1RegistryRuntimeError(
        deferred ? 'p2_1_deferred_forbidden' : 'registry_w0_active_forbidden',
        `${unitId} 在 W0 只能是 proposed/inactive，P2.1 只能是 deferred`,
      )
    }
    if (unit.implementation_status !== 'not_implemented' || unit.implementation_digest !== null) {
      throw new P2S1RegistryRuntimeError('registry_w0_implementation_claim_forbidden', `${unitId} 不得在 W0 伪造实现摘要`)
    }
    const constraints = object(unit.identity_constraints, `${unitId}.identity_constraints`)
    assertExactKeys(constraints, ['event_type', 'collector_id', 'publication_cardinality'], `${unitId}.identity_constraints`)
    if (
      constraints.event_type !== 'country_outage'
      || constraints.collector_id !== 'rrc25'
      || constraints.publication_cardinality !== 1
    ) {
      throw new P2S1RegistryRuntimeError('registry_boundary_violation', `${unitId} 越过单 RRC25 publication 边界`)
    }
    if (!Array.isArray(unit.dependencies)) {
      throw new P2S1RegistryRuntimeError('registry_dependency_invalid', `${unitId}.dependencies 必须是数组`)
    }
  }
  if (byId.size !== ALL_UNITS.size || [...ALL_UNITS].some((id) => !byId.has(id))) {
    throw new P2S1RegistryRuntimeError('registry_unit_population_invalid', 'W0 proposal 未闭合全部 P2-S1 v1 与 P2.1 deferred 人口')
  }

  const existingByKey = new Map(existing.unit_bindings.map((unit) => [`${unit.unit_id}@${unit.version}`, unit]))
  for (const unit of units) {
    const unitId = unit.unit_id as string
    const dependencyKeys = new Set<string>()
    for (const dependencyValue of unit.dependencies as unknown[]) {
      const dependency = object(dependencyValue, `${unitId}.dependency`)
      assertExactKeys(dependency, ['unit_id', 'unit_version', 'source', 'contract_digest'], `${unitId}.dependency`)
      requireNonempty(dependency.unit_id, 'dependency.unit_id')
      requireNonempty(dependency.unit_version, 'dependency.unit_version')
      requireDigest(dependency.contract_digest, 'dependency.contract_digest')
      if (!['same_proposal', 'existing_registry'].includes(String(dependency.source))) {
        throw new P2S1RegistryRuntimeError('registry_dependency_invalid', `${unitId} 的 dependency source 无效`)
      }
      const key = `${String(dependency.unit_id)}@${String(dependency.unit_version)}`
      if (dependencyKeys.has(key)) {
        throw new P2S1RegistryRuntimeError('registry_dependency_invalid', `${unitId} 依赖重复：${key}`)
      }
      dependencyKeys.add(key)
      if (dependency.source === 'same_proposal') {
        const target = byId.get(String(dependency.unit_id))
        if (
          !target
          || target.version !== dependency.unit_version
          || target.contract_digest !== dependency.contract_digest
          || target.activation_state === 'deferred'
        ) {
          throw new P2S1RegistryRuntimeError('registry_dependency_invalid', `${unitId} 的同 proposal 依赖未闭合：${key}`)
        }
      } else {
        const target = existingByKey.get(key)
        if (!target || target.contract_digest !== dependency.contract_digest || target.state !== 'active') {
          throw new P2S1RegistryRuntimeError('registry_dependency_invalid', `${unitId} 的既有 Registry 依赖未闭合：${key}`)
        }
      }
    }
  }
  return units as unknown as P2S1RegistryProposalUnit[]
}

export function createP2S1RegistryProposal(
  createdAtUtc: string,
  payload: P2S1RegistryProposalPayload,
): P2S1RegistryProposalSnapshot {
  const snapshotDigest = p2S1Digest(payload)
  return validateP2S1RegistryProposal({
    schema_version: PROPOSAL_SCHEMA,
    registry_snapshot_id: `p2-s1-registry-proposal-sha256:${snapshotDigest.slice('sha256:'.length)}`,
    snapshot_digest: snapshotDigest,
    created_at_utc: createdAtUtc,
    snapshot_payload: structuredClone(payload),
  })
}

export function validateP2S1RegistryProposal(value: unknown): P2S1RegistryProposalSnapshot {
  const snapshot = object(value, 'registry_proposal')
  assertExactKeys(snapshot, [
    'schema_version', 'registry_snapshot_id', 'snapshot_digest', 'created_at_utc', 'snapshot_payload',
  ], 'registry_proposal')
  if (
    snapshot.schema_version !== PROPOSAL_SCHEMA
    || typeof snapshot.registry_snapshot_id !== 'string'
    || !SNAPSHOT_ID.test(snapshot.registry_snapshot_id)
  ) {
    throw new P2S1RegistryRuntimeError('registry_proposal_invalid', 'Registry proposal 顶层身份无效')
  }
  requireDigest(snapshot.snapshot_digest, 'snapshot_digest')
  requireNonempty(snapshot.created_at_utc, 'created_at_utc')
  const payload = object(snapshot.snapshot_payload, 'snapshot_payload')
  assertExactKeys(payload, [
    'candidate_id', 'design_candidate_digest', 'registry_revision', 'activation_scope',
    'runtime_integration', 'production_deployed', 'permission_mode',
    'external_data_allowed', 'publication_identity', 'existing_registry_binding', 'units',
  ], 'snapshot_payload')
  if (typeof payload.candidate_id !== 'string' || !CANDIDATE_ID.test(payload.candidate_id)) {
    throw new P2S1RegistryRuntimeError('registry_proposal_invalid', 'design candidate 身份无效')
  }
  requireDigest(payload.design_candidate_digest, 'design_candidate_digest')
  if (
    payload.candidate_id !== P2S1_FROZEN_DESIGN_CANDIDATE_ID
    || payload.design_candidate_digest !== P2S1_FROZEN_DESIGN_CANDIDATE_DIGEST
  ) {
    throw new P2S1RegistryRuntimeError('registry_candidate_binding_mismatch', 'Registry proposal 未绑定冻结 P2-S1 设计候选')
  }
  if (!Number.isSafeInteger(payload.registry_revision) || (payload.registry_revision as number) < 1) {
    throw new P2S1RegistryRuntimeError('registry_proposal_invalid', 'registry_revision 无效')
  }
  if (
    payload.activation_scope !== 'w0_proposal_only'
    || payload.runtime_integration !== 'governance_implemented_units_not_implemented'
    || payload.production_deployed !== false
    || payload.permission_mode !== 'read_only'
    || payload.external_data_allowed !== false
  ) {
    throw new P2S1RegistryRuntimeError('registry_w0_boundary_violation', 'Registry proposal 越过 W0 非运行、非部署、无外部数据边界')
  }
  validatePublicationIdentity(payload.publication_identity)
  const existing = validateExistingRegistryBinding(payload.existing_registry_binding)
  if (payload.registry_revision !== existing.registry_revision + 1) {
    throw new P2S1RegistryRuntimeError('registry_revision_chain_invalid', 'W0 proposal revision 必须严格承接冻结 S0B snapshot')
  }
  validateUnits(payload.units, existing)
  const expectedDigest = p2S1Digest(payload)
  if (
    snapshot.snapshot_digest !== expectedDigest
    || snapshot.registry_snapshot_id !== `p2-s1-registry-proposal-sha256:${expectedDigest.slice('sha256:'.length)}`
  ) {
    throw new P2S1RegistryRuntimeError('registry_proposal_digest_mismatch', 'Registry proposal 内容寻址摘要不一致')
  }
  return deepFreeze(structuredClone(snapshot) as unknown as P2S1RegistryProposalSnapshot)
}

function admissionDigestInput(
  receipt: Omit<P2S1RegistryProposalAdmissionReceipt, 'receipt_digest'>,
): JsonObject {
  return structuredClone(receipt) as unknown as JsonObject
}

export class P2S1RegistryProposalResolver {
  constructor(private readonly expected: P2S1RegistryExpectedContext) {}

  admit(value: unknown): P2S1RegistryProposalAdmissionReceipt {
    const snapshot = validateP2S1RegistryProposal(value)
    const payload = snapshot.snapshot_payload
    if (
      payload.candidate_id !== this.expected.candidate_id
      || payload.design_candidate_digest !== this.expected.design_candidate_digest
    ) {
      throw new P2S1RegistryRuntimeError('registry_candidate_binding_mismatch', 'Registry proposal 与冻结设计候选不一致')
    }
    if (!same(payload.publication_identity, this.expected.publication_identity)) {
      throw new P2S1RegistryRuntimeError('registry_publication_replay_denied', 'Registry proposal 被跨 publication 重放')
    }
    if (
      payload.existing_registry_binding.registry_snapshot_id !== this.expected.existing_registry_snapshot_id
      || payload.existing_registry_binding.snapshot_digest !== this.expected.existing_registry_snapshot_digest
    ) {
      throw new P2S1RegistryRuntimeError('registry_dependency_snapshot_mismatch', '既有 Registry snapshot 绑定不一致')
    }
    const proposed = payload.units.filter((unit) => unit.activation_state === 'proposed').map((unit) => unit.unit_id).sort()
    const inactive = payload.units.filter((unit) => unit.activation_state === 'inactive').map((unit) => unit.unit_id).sort()
    const deferred = payload.units.filter((unit) => unit.activation_state === 'deferred').map((unit) => unit.unit_id).sort()
    const withoutDigest: Omit<P2S1RegistryProposalAdmissionReceipt, 'receipt_digest'> = {
      schema_version: ADMISSION_SCHEMA,
      status: 'admitted_as_inactive_proposal',
      registry_snapshot_id: snapshot.registry_snapshot_id,
      snapshot_digest: snapshot.snapshot_digest,
      registry_revision: payload.registry_revision,
      candidate_id: payload.candidate_id,
      design_candidate_digest: payload.design_candidate_digest,
      publication_identity: structuredClone(payload.publication_identity),
      proposed_unit_ids: proposed,
      inactive_unit_ids: inactive,
      deferred_denied_unit_ids: deferred,
      execution_allowed_unit_ids: [],
      execution_started: false,
      production_deployed: false,
    }
    return deepFreeze({
      ...withoutDigest,
      receipt_digest: p2S1Digest(admissionDigestInput(withoutDigest)),
    })
  }

  assertExecutionAuthorized(unitId: string, value: unknown): never {
    if (DEFERRED_UNITS.has(unitId)) {
      throw new P2S1RegistryRuntimeError('p2_1_deferred_forbidden', `${unitId} 属于 P2.1，W0/P2 v1 永不授权`)
    }
    const snapshot = validateP2S1RegistryProposal(value)
    const unit = snapshot.snapshot_payload.units.find((item) => item.unit_id === unitId)
    if (!unit || !V1_UNITS.has(unitId)) {
      throw new P2S1RegistryRuntimeError('execution_unit_unknown', `${unitId} 不属于 P2-S1 v1 proposal`)
    }
    // W0 proposal validator already forbids active/implemented units. Keeping this
    // as an explicit terminal denial makes it impossible for a caller to confuse
    // proposal admission with execution admission.
    throw new P2S1RegistryRuntimeError('execution_unit_not_active', `${unitId} 仅为 ${unit.activation_state}，执行授权为 0`)
  }
}

function waveUnitIds(waveId: P2S1RegistryWaveId): readonly string[] {
  if (waveId === 'W1') return P2S1_W1_ACTIVATION_UNIT_IDS
  if (waveId === 'W2') return P2S1_W2_ACTIVATION_UNIT_IDS
  if (waveId === 'W3') return P2S1_W3_ACTIVATION_UNIT_IDS
  return P2S1_W4_ACTIVATION_UNIT_IDS
}

function fullAdmittedBindingUnitIds(waveId: P2S1RegistryWaveId): string[] {
  const waves: readonly (readonly string[])[] = [
    P2S1_W1_ACTIVATION_UNIT_IDS,
    P2S1_W2_ACTIVATION_UNIT_IDS,
    P2S1_W3_ACTIVATION_UNIT_IDS,
    P2S1_W4_ACTIVATION_UNIT_IDS,
  ]
  const lastIndex = Number(waveId.slice(1)) - 1
  return waves.slice(0, lastIndex + 1).flatMap((ids) => [...ids])
}

export function p2S1ExpectedHandlerId(unitId: string): string {
  const handlerId = P2S1_EXPECTED_HANDLER_BY_UNIT.get(unitId)
  if (!handlerId) {
    throw new P2S1RegistryRuntimeError('registry_wave_unit_forbidden', `${unitId} 不属于 W1/W2/W3/W4 原子 binding 准入人口`)
  }
  return handlerId
}

export function createP2S1RegistryUnitTestEvidence(
  value: Omit<P2S1RegistryUnitTestEvidence, 'schema_version' | 'receipt_digest'>,
): P2S1RegistryUnitTestEvidence {
  const withoutDigest = {
    schema_version: WAVE_TEST_RECEIPT_SCHEMA,
    ...structuredClone(value),
  }
  return validateUnitTestEvidence({
    ...withoutDigest,
    receipt_digest: p2S1Digest(withoutDigest),
  })
}

function validateUnitTestEvidence(value: unknown): P2S1RegistryUnitTestEvidence {
  const receipt = object(value, 'unit_test_evidence')
  assertExactKeys(receipt, [
    'schema_version', 'receipt_digest', 'candidate_id', 'design_candidate_digest',
    'wave_id', 'unit_id', 'handler_id', 'implementation_digest', 'contract_digest',
    'semantic_digest', 'structural_binding_contract_digest', 'runner_receipt_digest',
    'runner_receipt_file_digest', 'runner_receipt_path', 'test_case_ids', 'test_result',
    'tested_execution_count',
  ], 'unit_test_evidence')
  if (receipt.schema_version !== WAVE_TEST_RECEIPT_SCHEMA || !['W1', 'W2', 'W3', 'W4'].includes(String(receipt.wave_id))) {
    throw new P2S1RegistryRuntimeError('registry_test_evidence_invalid', '单元测试证据 schema/wave 无效')
  }
  requireDigest(receipt.receipt_digest, 'unit_test_evidence.receipt_digest')
  requireDigest(receipt.design_candidate_digest, 'unit_test_evidence.design_candidate_digest')
  requireDigest(receipt.implementation_digest, 'unit_test_evidence.implementation_digest')
  requireDigest(receipt.contract_digest, 'unit_test_evidence.contract_digest')
  requireDigest(receipt.semantic_digest, 'unit_test_evidence.semantic_digest')
  requireDigest(receipt.structural_binding_contract_digest, 'unit_test_evidence.structural_binding_contract_digest')
  requireDigest(receipt.runner_receipt_digest, 'unit_test_evidence.runner_receipt_digest')
  requireDigest(receipt.runner_receipt_file_digest, 'unit_test_evidence.runner_receipt_file_digest')
  requireNonempty(receipt.runner_receipt_path, 'unit_test_evidence.runner_receipt_path')
  if (
    !Array.isArray(receipt.test_case_ids)
    || receipt.test_case_ids.length < 1
    || receipt.test_case_ids.some((item) => typeof item !== 'string' || item.length < 1)
    || new Set(receipt.test_case_ids).size !== receipt.test_case_ids.length
  ) {
    throw new P2S1RegistryRuntimeError('registry_test_evidence_invalid', '单元测试证据必须绑定非空且唯一的真实 runner test case IDs')
  }
  requireNonempty(receipt.candidate_id, 'unit_test_evidence.candidate_id')
  requireNonempty(receipt.unit_id, 'unit_test_evidence.unit_id')
  requireNonempty(receipt.handler_id, 'unit_test_evidence.handler_id')
  if (
    receipt.test_result !== 'passed'
    || !Number.isSafeInteger(receipt.tested_execution_count)
    || (receipt.tested_execution_count as number) < 1
  ) {
    throw new P2S1RegistryRuntimeError('registry_test_evidence_invalid', '测试证据必须是至少执行一次的 passed 结果')
  }
  const digestInput = structuredClone(receipt)
  delete digestInput.receipt_digest
  if (receipt.receipt_digest !== p2S1Digest(digestInput)) {
    throw new P2S1RegistryRuntimeError('registry_test_evidence_digest_mismatch', '单元测试证据内容寻址摘要不一致')
  }
  return deepFreeze(structuredClone(receipt) as unknown as P2S1RegistryUnitTestEvidence)
}

export function createP2S1RegistryWaveHandlerManifest(
  payload: P2S1RegistryWaveHandlerManifestPayload,
): P2S1RegistryWaveHandlerManifest {
  const manifestDigest = p2S1Digest(payload)
  return validateP2S1RegistryWaveHandlerManifest({
    schema_version: WAVE_HANDLER_MANIFEST_SCHEMA,
    handler_manifest_id: `p2-s1-handler-manifest-sha256:${manifestDigest.slice('sha256:'.length)}`,
    handler_manifest_digest: manifestDigest,
    manifest_payload: structuredClone(payload),
  })
}

export function validateP2S1RegistryWaveHandlerManifest(
  value: unknown,
): P2S1RegistryWaveHandlerManifest {
  const manifest = object(value, 'wave_handler_manifest')
  assertExactKeys(manifest, [
    'schema_version', 'handler_manifest_id', 'handler_manifest_digest', 'manifest_payload',
  ], 'wave_handler_manifest')
  if (
    manifest.schema_version !== WAVE_HANDLER_MANIFEST_SCHEMA
    || typeof manifest.handler_manifest_id !== 'string'
    || !WAVE_MANIFEST_ID.test(manifest.handler_manifest_id)
  ) {
    throw new P2S1RegistryRuntimeError('registry_handler_manifest_invalid', 'handler manifest 顶层身份无效')
  }
  requireDigest(manifest.handler_manifest_digest, 'handler_manifest_digest')
  const payload = object(manifest.manifest_payload, 'handler_manifest.manifest_payload')
  assertExactKeys(payload, [
    'candidate_id', 'design_candidate_digest', 'wave_id',
    'structural_binding_contract_digest', 'handlers',
  ], 'handler_manifest.manifest_payload')
  if (!['W1', 'W2', 'W3', 'W4'].includes(String(payload.wave_id))) {
    throw new P2S1RegistryRuntimeError('registry_wave_unit_forbidden', '仅 W1/W2/W3/W4 可以生成 binding 准入 manifest')
  }
  requireNonempty(payload.candidate_id, 'handler_manifest.candidate_id')
  requireDigest(payload.design_candidate_digest, 'handler_manifest.design_candidate_digest')
  requireDigest(payload.structural_binding_contract_digest, 'handler_manifest.structural_binding_contract_digest')
  if (!Array.isArray(payload.handlers)) {
    throw new P2S1RegistryRuntimeError('registry_handler_manifest_invalid', 'handlers 必须是数组')
  }
  const expectedIds = waveUnitIds(payload.wave_id as P2S1RegistryWaveId)
  const actualIds: string[] = []
  for (const [index, handlerValue] of payload.handlers.entries()) {
    const handler = object(handlerValue, `handlers[${index}]`)
    assertExactKeys(handler, [
      'unit_id', 'handler_id', 'implementation_digest', 'contract_digest',
      'semantic_digest', 'structural_binding_contract_digest', 'dependencies', 'test_evidence',
    ], `handlers[${index}]`)
    requireNonempty(handler.unit_id, `handlers[${index}].unit_id`)
    requireNonempty(handler.handler_id, `handlers[${index}].handler_id`)
    requireDigest(handler.implementation_digest, `handlers[${index}].implementation_digest`)
    requireDigest(handler.contract_digest, `handlers[${index}].contract_digest`)
    requireDigest(handler.semantic_digest, `handlers[${index}].semantic_digest`)
    requireDigest(handler.structural_binding_contract_digest, `handlers[${index}].structural_binding_contract_digest`)
    actualIds.push(handler.unit_id as string)
    if (handler.handler_id !== P2S1_EXPECTED_HANDLER_BY_UNIT.get(handler.unit_id as string)) {
      throw new P2S1RegistryRuntimeError('registry_handler_binding_mismatch', `${String(handler.unit_id)} handler 身份与实现入口不一致`)
    }
    if (
      handler.contract_digest !== p2S1ExpectedDesignContractDigest(handler.unit_id as string)
      || handler.semantic_digest !== p2S1ExpectedUnitSemanticDigest(handler.unit_id as string)
    ) {
      throw new P2S1RegistryRuntimeError('registry_unit_contract_drift', `${String(handler.unit_id)} 未绑定冻结设计合同/语义`)
    }
    if (handler.structural_binding_contract_digest !== payload.structural_binding_contract_digest) {
      throw new P2S1RegistryRuntimeError('registry_structural_binding_mismatch', `${String(handler.unit_id)} 结构绑定合同漂移`)
    }
    if (!Array.isArray(handler.dependencies)) {
      throw new P2S1RegistryRuntimeError('registry_dependency_invalid', `${String(handler.unit_id)} dependencies 必须是数组`)
    }
    const evidence = validateUnitTestEvidence(handler.test_evidence)
    if (
      evidence.candidate_id !== payload.candidate_id
      || evidence.design_candidate_digest !== payload.design_candidate_digest
      || evidence.wave_id !== payload.wave_id
      || evidence.unit_id !== handler.unit_id
      || evidence.handler_id !== handler.handler_id
      || evidence.implementation_digest !== handler.implementation_digest
      || evidence.contract_digest !== handler.contract_digest
      || evidence.semantic_digest !== handler.semantic_digest
      || evidence.structural_binding_contract_digest !== handler.structural_binding_contract_digest
    ) {
      throw new P2S1RegistryRuntimeError('registry_test_evidence_binding_mismatch', `${String(handler.unit_id)} 测试证据与同候选实现绑定不闭合`)
    }
  }
  if (!same(actualIds, expectedIds)) {
    throw new P2S1RegistryRuntimeError('registry_partial_wave_forbidden', `${String(payload.wave_id)} 必须一次包含精确完整波次，禁止缺项、重复、乱序或跨波次`)
  }
  const expectedDigest = p2S1Digest(payload)
  if (
    manifest.handler_manifest_digest !== expectedDigest
    || manifest.handler_manifest_id !== `p2-s1-handler-manifest-sha256:${expectedDigest.slice('sha256:'.length)}`
  ) {
    throw new P2S1RegistryRuntimeError('registry_handler_manifest_digest_mismatch', 'handler manifest 内容寻址摘要不一致')
  }
  return deepFreeze(structuredClone(manifest) as unknown as P2S1RegistryWaveHandlerManifest)
}

export function createP2S1RegistryWaveSnapshot(
  createdAtUtc: string,
  payload: P2S1RegistryWaveSnapshotPayload,
): P2S1RegistryWaveSnapshot {
  requireNonempty(createdAtUtc, 'wave_snapshot.created_at_utc')
  const snapshotDigest = p2S1Digest(payload)
  return deepFreeze({
    schema_version: WAVE_SNAPSHOT_SCHEMA,
    registry_snapshot_id: `p2-s1-registry-wave-sha256:${snapshotDigest.slice('sha256:'.length)}`,
    snapshot_digest: snapshotDigest,
    created_at_utc: createdAtUtc,
    snapshot_payload: structuredClone(payload),
  })
}

function snapshotRef(
  snapshot: P2S1RegistryProposalSnapshot | P2S1RegistryWaveSnapshot,
): P2S1RegistryWavePreviousSnapshotRef {
  return {
    registry_snapshot_id: snapshot.registry_snapshot_id,
    snapshot_digest: snapshot.snapshot_digest,
    registry_revision: snapshot.snapshot_payload.registry_revision,
  }
}

function validateSnapshotRef(value: unknown, label: string): P2S1RegistryWavePreviousSnapshotRef {
  const ref = object(value, label)
  assertExactKeys(ref, ['registry_snapshot_id', 'snapshot_digest', 'registry_revision'], label)
  requireNonempty(ref.registry_snapshot_id, `${label}.registry_snapshot_id`)
  requireDigest(ref.snapshot_digest, `${label}.snapshot_digest`)
  if (!Number.isSafeInteger(ref.registry_revision) || (ref.registry_revision as number) < 1) {
    throw new P2S1RegistryRuntimeError('registry_revision_chain_invalid', `${label}.registry_revision 无效`)
  }
  return ref as unknown as P2S1RegistryWavePreviousSnapshotRef
}

function validateWaveDependencies(
  manifest: P2S1RegistryWaveHandlerManifest,
  proposal: P2S1RegistryProposalSnapshot,
  admittedBindingUnitIds: readonly string[],
): void {
  const proposalById = new Map(proposal.snapshot_payload.units.map((unit) => [unit.unit_id, unit]))
  const existingById = new Map(
    proposal.snapshot_payload.existing_registry_binding.unit_bindings.map((unit) => [unit.unit_id, unit]),
  )
  const admitted = new Set(admittedBindingUnitIds)
  for (const handler of manifest.manifest_payload.handlers) {
    const proposalUnit = proposalById.get(handler.unit_id)
    if (!proposalUnit || !same(handler.dependencies, proposalUnit.dependencies)) {
      throw new P2S1RegistryRuntimeError('registry_dependency_invalid', `${handler.unit_id} 激活依赖与冻结 proposal 不一致`)
    }
    for (const dependency of handler.dependencies) {
      if (dependency.source === 'existing_registry') {
        const target = existingById.get(dependency.unit_id)
        if (
          !target
          || target.state !== 'active'
          || target.version !== dependency.unit_version
          || target.contract_digest !== dependency.contract_digest
        ) {
          throw new P2S1RegistryRuntimeError('registry_dependency_not_active', `${handler.unit_id} 既有依赖未 active`)
        }
      } else {
        const target = proposalById.get(dependency.unit_id)
        if (
          !target
          || target.version !== dependency.unit_version
          || target.contract_digest !== dependency.contract_digest
          || !admitted.has(dependency.unit_id)
        ) {
          throw new P2S1RegistryRuntimeError('registry_dependency_not_active', `${handler.unit_id} 同 proposal 依赖未被既有 active Registry 或此前完整 binding 波次闭合`)
        }
      }
    }
  }
}

function validateP2S1RegistryWaveSnapshot(
  value: unknown,
  context: P2S1RegistryWaveBindingAdmissionContext,
  proposal: P2S1RegistryProposalSnapshot,
  previousWave: P2S1RegistryWaveSnapshot | null,
): P2S1RegistryWaveSnapshot {
  const snapshot = object(value, 'registry_wave_snapshot')
  assertExactKeys(snapshot, [
    'schema_version', 'registry_snapshot_id', 'snapshot_digest', 'created_at_utc', 'snapshot_payload',
  ], 'registry_wave_snapshot')
  if (
    snapshot.schema_version !== WAVE_SNAPSHOT_SCHEMA
    || typeof snapshot.registry_snapshot_id !== 'string'
    || !WAVE_SNAPSHOT_ID.test(snapshot.registry_snapshot_id)
  ) {
    throw new P2S1RegistryRuntimeError('registry_wave_snapshot_invalid', 'wave snapshot 顶层身份无效')
  }
  requireDigest(snapshot.snapshot_digest, 'wave_snapshot.snapshot_digest')
  requireNonempty(snapshot.created_at_utc, 'wave_snapshot.created_at_utc')
  const payload = object(snapshot.snapshot_payload, 'wave_snapshot.snapshot_payload')
  assertExactKeys(payload, [
    'candidate_id', 'design_candidate_digest', 'registry_revision', 'wave_id',
    'activation_scope', 'permission_mode', 'external_data_allowed', 'production_deployed',
    'publication_identity', 'proposal_snapshot_ref', 'previous_snapshot_ref',
    'handler_manifest', 'admitted_wave_binding_unit_ids', 'admitted_binding_unit_ids',
  ], 'wave_snapshot.snapshot_payload')
  const expectedWave: P2S1RegistryWaveId = previousWave === null
    ? 'W1'
    : previousWave.snapshot_payload.wave_id === 'W1'
      ? 'W2'
      : previousWave.snapshot_payload.wave_id === 'W2'
        ? 'W3'
        : 'W4'
  if (payload.wave_id !== expectedWave) {
    throw new P2S1RegistryRuntimeError('registry_wave_sequence_invalid', `当前 CAS 只能激活 ${expectedWave}`)
  }
  if (
    payload.candidate_id !== context.candidate_id
    || payload.design_candidate_digest !== context.design_candidate_digest
    || payload.candidate_id !== proposal.snapshot_payload.candidate_id
    || payload.design_candidate_digest !== proposal.snapshot_payload.design_candidate_digest
  ) {
    throw new P2S1RegistryRuntimeError('registry_candidate_binding_mismatch', 'wave activation 与冻结候选不一致')
  }
  if (
    payload.activation_scope !== 'complete_atomic_wave_binding_admission'
    || payload.permission_mode !== 'read_only'
    || payload.external_data_allowed !== false
    || payload.production_deployed !== false
  ) {
    throw new P2S1RegistryRuntimeError('registry_wave_boundary_violation', 'wave activation 越过只读、无外部数据、非部署边界')
  }
  const identity = validatePublicationIdentity(payload.publication_identity)
  if (!same(identity, context.publication_identity) || !same(identity, proposal.snapshot_payload.publication_identity)) {
    throw new P2S1RegistryRuntimeError('registry_publication_replay_denied', 'wave activation 被跨 publication 重放')
  }
  const proposalRef = validateSnapshotRef(payload.proposal_snapshot_ref, 'proposal_snapshot_ref')
  if (!same(proposalRef, snapshotRef(proposal))) {
    throw new P2S1RegistryRuntimeError('registry_proposal_snapshot_mismatch', 'wave activation 未绑定 W0 proposal')
  }
  const previous = previousWave ?? proposal
  const previousRef = validateSnapshotRef(payload.previous_snapshot_ref, 'previous_snapshot_ref')
  if (!same(previousRef, snapshotRef(previous))) {
    throw new P2S1RegistryRuntimeError('registry_cas_mismatch', 'wave activation 的 CAS previous snapshot 已过期或被替换')
  }
  if (payload.registry_revision !== previous.snapshot_payload.registry_revision + 1) {
    throw new P2S1RegistryRuntimeError('registry_revision_chain_invalid', 'wave activation revision 必须严格 +1')
  }
  if (!same(payload.admitted_wave_binding_unit_ids, waveUnitIds(expectedWave))) {
    throw new P2S1RegistryRuntimeError('registry_partial_wave_forbidden', `${expectedWave} admitted_wave_binding_unit_ids 不是精确完整波次`)
  }
  const expectedAdmitted = fullAdmittedBindingUnitIds(expectedWave)
  if (!same(payload.admitted_binding_unit_ids, expectedAdmitted)) {
    throw new P2S1RegistryRuntimeError('registry_partial_wave_forbidden', `${expectedWave} admitted_binding_unit_ids 未闭合继承人口`)
  }
  const manifest = validateP2S1RegistryWaveHandlerManifest(payload.handler_manifest)
  if (
    manifest.manifest_payload.wave_id !== expectedWave
    || manifest.manifest_payload.candidate_id !== context.candidate_id
    || manifest.manifest_payload.design_candidate_digest !== context.design_candidate_digest
    || manifest.manifest_payload.structural_binding_contract_digest !== context.structural_binding_contract_digest
  ) {
    throw new P2S1RegistryRuntimeError('registry_handler_manifest_binding_mismatch', 'handler manifest 与激活上下文不一致')
  }
  for (const handler of manifest.manifest_payload.handlers) {
    if (handler.implementation_digest !== context.implementation_digest_by_unit[handler.unit_id]) {
      throw new P2S1RegistryRuntimeError('registry_implementation_digest_mismatch', `${handler.unit_id} 实现摘要不属于受信同候选 manifest`)
    }
    if (handler.test_evidence.receipt_digest !== context.test_evidence_receipt_digest_by_unit[handler.unit_id]) {
      throw new P2S1RegistryRuntimeError('registry_test_evidence_binding_mismatch', `${handler.unit_id} 测试回执不属于受信同候选测试证据集`)
    }
  }
  validateWaveDependencies(manifest, proposal, expectedAdmitted)
  const expectedDigest = p2S1Digest(payload)
  if (
    snapshot.snapshot_digest !== expectedDigest
    || snapshot.registry_snapshot_id !== `p2-s1-registry-wave-sha256:${expectedDigest.slice('sha256:'.length)}`
  ) {
    throw new P2S1RegistryRuntimeError('registry_wave_snapshot_digest_mismatch', 'wave snapshot 内容寻址摘要不一致')
  }
  return deepFreeze(structuredClone(snapshot) as unknown as P2S1RegistryWaveSnapshot)
}

export class P2S1RegistryWaveBindingAdmitter {
  private readonly proposal: P2S1RegistryProposalSnapshot
  private currentWave: P2S1RegistryWaveSnapshot | null = null
  private admittedBindingByUnit = new Map<string, P2S1RegistryWaveHandlerBinding>()

  constructor(
    private readonly context: P2S1RegistryWaveBindingAdmissionContext,
    proposalValue: unknown,
  ) {
    requireDigest(context.structural_binding_contract_digest, 'structural_binding_contract_digest')
    const implementationIds = Object.keys(context.implementation_digest_by_unit).sort()
    const testEvidenceIds = Object.keys(context.test_evidence_receipt_digest_by_unit).sort()
    const expectedIds = [...P2S1_WAVE_UNIT_IDS].sort()
    if (!same(implementationIds, expectedIds) || !same(testEvidenceIds, expectedIds)) {
      throw new P2S1RegistryRuntimeError('registry_trusted_manifest_population_invalid', '受信实现与测试摘要必须精确覆盖 W1/W2/W3/W4 人口')
    }
    for (const unitId of expectedIds) {
      requireDigest(context.implementation_digest_by_unit[unitId], `${unitId}.trusted_implementation_digest`)
      requireDigest(context.test_evidence_receipt_digest_by_unit[unitId], `${unitId}.trusted_test_evidence_digest`)
    }
    this.proposal = validateP2S1RegistryProposal(proposalValue)
    new P2S1RegistryProposalResolver(context).admit(this.proposal)
  }

  currentSnapshotRef(): P2S1RegistryWavePreviousSnapshotRef {
    return deepFreeze(snapshotRef(this.currentWave ?? this.proposal))
  }

  admitBindings(value: unknown): P2S1RegistryWaveAdmissionReceipt {
    if (this.currentWave?.snapshot_payload.wave_id === 'W4') {
      throw new P2S1RegistryRuntimeError('registry_wave_sequence_invalid', 'W1/W2/W3/W4 binding 已完整准入，W5+ 不在本阶段范围')
    }
    const snapshot = validateP2S1RegistryWaveSnapshot(
      value,
      this.context,
      this.proposal,
      this.currentWave,
    )
    // 所有绑定、依赖、测试、完整人口与 CAS 都通过后才进行唯一一次状态替换。
    const nextBindings = new Map(this.admittedBindingByUnit)
    for (const binding of snapshot.snapshot_payload.handler_manifest.manifest_payload.handlers) {
      nextBindings.set(binding.unit_id, binding)
    }
    this.admittedBindingByUnit = nextBindings
    this.currentWave = snapshot
    const payload = snapshot.snapshot_payload
    const manifest = payload.handler_manifest
    const withoutDigest: Omit<P2S1RegistryWaveAdmissionReceipt, 'receipt_digest'> = {
      schema_version: WAVE_ADMISSION_SCHEMA,
      status: 'admitted_complete_atomic_wave_bindings',
      wave_id: payload.wave_id,
      registry_snapshot_id: snapshot.registry_snapshot_id,
      snapshot_digest: snapshot.snapshot_digest,
      registry_revision: payload.registry_revision,
      previous_snapshot_ref: structuredClone(payload.previous_snapshot_ref),
      candidate_id: payload.candidate_id,
      design_candidate_digest: payload.design_candidate_digest,
      publication_identity: structuredClone(payload.publication_identity),
      handler_manifest_id: manifest.handler_manifest_id,
      handler_manifest_digest: manifest.handler_manifest_digest,
      structural_binding_contract_digest: manifest.manifest_payload.structural_binding_contract_digest,
      admitted_wave_binding_unit_ids: [...payload.admitted_wave_binding_unit_ids],
      admitted_binding_unit_ids: [...payload.admitted_binding_unit_ids],
      execution_allowed_unit_ids: [],
      partial_binding_admission: false,
      execution_started: false,
      production_deployed: false,
    }
    return deepFreeze({
      ...withoutDigest,
      receipt_digest: p2S1Digest(withoutDigest),
    })
  }

  resolveAdmittedBinding(
    unitId: string,
    snapshotValue: unknown,
  ): P2S1RegistryWaveHandlerBinding {
    if (DEFERRED_UNITS.has(unitId)) {
      throw new P2S1RegistryRuntimeError('p2_1_deferred_forbidden', `${unitId} 属于 P2.1，不可准入 P2 v1 binding`)
    }
    if (!P2S1_WAVE_UNIT_IDS.has(unitId)) {
      throw new P2S1RegistryRuntimeError('registry_binding_not_admitted', `${unitId} 不属于 W1/W2/W3/W4 binding 准入人口`)
    }
    if (!this.currentWave || !same(snapshotValue, this.currentWave)) {
      throw new P2S1RegistryRuntimeError('registry_binding_snapshot_mismatch', 'binding 解析必须绑定当前已准入 Registry wave snapshot')
    }
    if (!this.currentWave.snapshot_payload.admitted_binding_unit_ids.includes(unitId)) {
      throw new P2S1RegistryRuntimeError('registry_binding_not_admitted', `${unitId} 尚未随完整波次准入 binding`)
    }
    // 后续 wave snapshot 通过内容链继承此前 binding；不可变 binding 只来自成功 CAS 的缓存。
    const binding = this.admittedBindingByUnit.get(unitId)
    if (!binding) {
      throw new P2S1RegistryRuntimeError('registry_handler_binding_mismatch', `${unitId} 缺少已准入 handler binding`)
    }
    return binding
  }

  assertExecutionAuthorized(_unitId: string, _snapshotValue: unknown): never {
    throw new P2S1RegistryRuntimeError(
      'registry_dispatch_not_bound',
      'W1/W2 仅准入不可变 dispatch binding；受信 Python dispatcher 尚未闭合，执行授权保持为空',
    )
  }
}
