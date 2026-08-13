import {
  p2S1CanonicalJson,
  p2S1Digest,
  type P2S1PublicationIdentity,
} from './p2-s1-trusted-receipt-store.js'

type JsonObject = Record<string, unknown>

const PROPOSAL_SCHEMA = 'country_outage_p2_s1_registry_proposal_v1'
const ADMISSION_SCHEMA = 'country_outage_p2_s1_registry_proposal_admission_v1'
const DIGEST = /^sha256:[a-f0-9]{64}$/
const SNAPSHOT_ID = /^p2-s1-registry-proposal-sha256:[a-f0-9]{64}$/
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
