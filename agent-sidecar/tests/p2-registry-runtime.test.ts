import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { after, test } from 'node:test'
import { fileURLToPath } from 'node:url'

import type { CountryOutagePrincipal } from '../src/server/contracts.js'
import type { P1ConversationBinding } from '../src/chat/contracts.js'
import type {
  P1AsnReadRequest,
  P1PageCapabilityReadProvider,
  P1PathReadRequest,
} from '../src/chat/general-read-model-provider.js'
import {
  P2GovernedRegistryRuntime,
  P2RegistryRuntimeError,
  P2RegistrySnapshotLoader,
  validateP2RegistrySnapshot,
} from '../src/chat/p2-registry-runtime.js'
import {
  P1RuntimeV2Grounder,
  P1RuntimeV2SemanticTurnService,
  type P1SemanticPlan,
  type P1UserGoal,
  type P1UserGoalPlan,
  type P1UserGoalPlanner,
  type P1UserGoalPlannerContext,
} from '../src/chat/runtime-v2-semantic.js'
import { P1TrendAwareGrounder } from '../src/chat/trend-aware-grounder.js'

type JsonObject = Record<string, unknown>

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
const snapshotPath = resolve(
  repoRoot,
  'contracts/agent/country-outage-p2-s0b-runtime/registry-snapshot.json',
)
const temporaryRoot = mkdtempSync(join(tmpdir(), 'p2-s0b-registry-test-'))

after(() => rmSync(temporaryRoot, { recursive: true, force: true }))

const reference = 'country_outage/2026-02-27 09:12:32/IR/1/r'
const publication = 'country_outage_publication_v1_test'
const binding: P1ConversationBinding = {
  event_type: 'country_outage',
  incident_id: 'incident_test',
  legacy_reference: reference,
  publication_id: publication,
  revision: 1,
  collector_id: 'rrc25',
  cohort_id: 'cohort_test',
  country_code: 'IR',
  detected_at_utc: '2026-02-27T01:12:32Z',
  window_start_utc: '2026-02-27T00:10:00Z',
  window_end_utc: '2026-03-11T00:00:00Z',
  data_through: '2026-03-11T00:00:00Z',
  is_final_in_data_range: false,
  lifecycle_state: 'event_end_unknown',
  observation_state: 'evidence_complete',
  quality_state: 'complete',
  missing_slot_count: 0,
  capabilities: {
    overview: 'available',
    event_series: 'available',
    affected_as: 'available',
    path_downstreams: 'available',
    full_path_evidence: 'audit_only',
  },
}

const overview = {
  ...binding,
  event: {
    legacy_reference: reference,
    country_code: 'IR',
    detected_at_utc: binding.detected_at_utc,
    event_end_at_utc: null,
    event_duration_seconds: null,
  },
  cohort: { fixed_prefix_count: 9257, fixed_asn_count: 572 },
  current: {
    interrupted_prefix_count: 1024,
    completely_interrupted_prefix_count: 318,
    invisible_direction_count: 14867,
  },
  peaks: {
    interrupted_prefix_count: {
      value: 3855,
      state_point_utc: '2026-02-27T23:15:00Z',
    },
    completely_interrupted_prefix_count: {
      value: 1553,
      state_point_utc: '2026-02-28T14:35:00Z',
    },
    affected_asn_count: {
      value: 350,
      state_point_utc: '2026-03-02T11:30:00Z',
    },
    route_interrupted_asn_count: {
      value: 94,
      state_point_utc: '2026-02-28T13:50:00Z',
    },
  },
  affected_as_count: 525,
  route_interrupted_as_count: 151,
}

class FixtureProvider implements P1PageCapabilityReadProvider {
  calls: string[] = []

  async resolve(): Promise<P1ConversationBinding> {
    this.calls.push('resolve')
    return structuredClone(binding)
  }

  async readOverview(): Promise<JsonObject> {
    this.calls.push('overview')
    return structuredClone(overview)
  }

  async readSeries(): Promise<JsonObject> {
    this.calls.push('series')
    throw new Error('not_needed_by_this_fixture')
  }

  async readAsns(
    _binding: P1ConversationBinding,
    _request: P1AsnReadRequest,
  ): Promise<JsonObject> {
    this.calls.push('asns')
    throw new Error('not_needed_by_this_fixture')
  }

  async readPaths(
    _binding: P1ConversationBinding,
    _request: P1PathReadRequest,
  ): Promise<JsonObject> {
    this.calls.push('paths')
    throw new Error('not_needed_by_this_fixture')
  }

  async readAudit(): Promise<JsonObject> {
    this.calls.push('audit')
    throw new Error('not_needed_by_this_fixture')
  }
}

function goal(
  requestedGoal: string,
  normalizedKind: string,
  entities: P1UserGoal['entities'] = {},
): P1UserGoal {
  return {
    goal_id: 'goal-1',
    requested_goal: requestedGoal,
    normalized_kind: normalizedKind,
    entities,
    references: [],
    ambiguity: 'none',
    context_dependencies: [],
  }
}

function userPlan(question: string, item: P1UserGoal): P1UserGoalPlan {
  return {
    plan_revision: 'user-goal-plan-v2',
    original_question: question,
    goals: [item],
    state_proposal: { inherit: [], set: {}, clear: [], reason_codes: [] },
    planner_identity: 'p2-s0b-fixture-planner',
    confidence: 1,
  }
}

class FixturePlanner implements P1UserGoalPlanner {
  readonly identity = 'p2-s0b-fixture-planner'

  constructor(private readonly value: P1UserGoalPlan) {}

  async plan(
    _question: string,
    _context: P1UserGoalPlannerContext,
  ): Promise<P1UserGoalPlan> {
    return structuredClone(this.value)
  }
}

function planFor(
  normalizedKind: string,
  entities: P1UserGoal['entities'] = {},
): { userGoalPlan: P1UserGoalPlan; semanticPlan: P1SemanticPlan } {
  const value = userPlan(
    normalizedKind,
    goal(normalizedKind, normalizedKind, entities),
  )
  const grounder = entities.analysis_mode === 'event_window_trend'
    ? new P1TrendAwareGrounder()
    : new P1RuntimeV2Grounder()
  return {
    userGoalPlan: value,
    semanticPlan: grounder.ground(value, binding, reference),
  }
}

function loadSnapshot(): JsonObject {
  return JSON.parse(readFileSync(snapshotPath, 'utf8')) as JsonObject
}

function canonicalNumber(value: number): string {
  if (Object.is(value, -0) || value === 0) return '0'
  const sign = value < 0 ? '-' : ''
  const [coefficientPart = '0', exponentPart = '0'] = Math.abs(value).toString().toLowerCase().split('e')
  const explicitExponent = Number.parseInt(exponentPart, 10)
  const decimalAt = coefficientPart.indexOf('.')
  const fractionalLength = decimalAt === -1 ? 0 : coefficientPart.length - decimalAt - 1
  const leadingTrimmed = coefficientPart.replace('.', '').replace(/^0+/, '')
  const trailingCount = leadingTrimmed.length - leadingTrimmed.replace(/0+$/, '').length
  const digits = leadingTrimmed.replace(/0+$/, '')
  const scientificExponent = explicitExponent - fractionalLength + trailingCount + digits.length - 1
  const coefficient = digits.length === 1 ? digits : `${digits[0]}.${digits.slice(1)}`
  return `${sign}${coefficient}e${scientificExponent}`
}

function canonicalText(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return canonicalNumber(value)
  if (typeof value === 'string') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonicalText).join(',')}]`
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as JsonObject)
      .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${canonicalText(item)}`).join(',')}}`
  }
  throw new TypeError('unsupported canonical value')
}

function digest(value: unknown): string {
  return `sha256:${createHash('sha256')
    .update(canonicalText(value))
    .digest('hex')}`
}

function reseal(snapshot: JsonObject): JsonObject {
  const payload = snapshot.snapshot_payload
  const snapshotDigest = digest(payload)
  snapshot.snapshot_digest = snapshotDigest
  snapshot.registry_snapshot_id =
    `registry-snapshot-sha256:${snapshotDigest.slice('sha256:'.length)}`
  return snapshot
}

function writeSnapshot(name: string, snapshot: JsonObject): string {
  const path = join(temporaryRoot, name)
  writeFileSync(path, `${JSON.stringify(snapshot, null, 2)}\n`, 'utf8')
  return path
}

function expectCode(code: string, action: () => unknown): void {
  assert.throws(action, (error: unknown) =>
    error instanceof P2RegistryRuntimeError && error.code === code
  )
}

test('S0B 快照内容寻址有效且候选保持未部署', () => {
  const snapshot = new P2RegistrySnapshotLoader(snapshotPath).load()
  assert.equal(snapshot.snapshot_payload.capability_registry.entries.length, 18)
  assert.equal(snapshot.snapshot_payload.execution_unit_registry.entries.length, 10)
  assert.equal(snapshot.snapshot_payload.runtime_integration, 'implemented_not_deployed')
  assert.equal(snapshot.production_deployed, false)
})

test('S0B 事实问题按需自动调用并把快照版本摘要写入执行回执', async () => {
  const question = '当前事件概况是什么？'
  const provider = new FixtureProvider()
  const planner = new FixturePlanner(userPlan(
    question,
    goal(question, 'event_summary'),
  ))
  const principal: CountryOutagePrincipal = {
    userId: 'p2-s0b-test',
    authorizationScope: 'country_outage:read',
  }
  const answer = await new P1RuntimeV2SemanticTurnService(
    provider,
    planner,
  ).answer(principal, {
    event_reference: reference,
    publication_id: publication,
    revision: 1,
    question,
  })

  assert.deepEqual(provider.calls, ['resolve', 'overview'])
  assert.equal(answer.execution_trace.registry_admission.status, 'admitted')
  assert.equal(answer.execution_trace.registry_admission.execution_started, false)
  assert.ok(answer.execution_trace.registry_admission.goal_resolutions.some(
    (goalResolution) => goalResolution.call_policy === 'required',
  ))
  assert.ok(answer.execution_trace.nodes.length >= 2)
  assert.ok(answer.execution_trace.nodes.every((node) =>
    node.registry_admission_status === 'admitted'
    && node.registry_snapshot_id === answer.runtime_identity.registry_snapshot_id
    && node.execution_unit_version !== null
    && node.unit_contract_digest?.startsWith('sha256:')
    && node.unit_implementation_digest?.startsWith('sha256:')
    && node.unit_semantic_digest?.startsWith('sha256:')
  ))
})

test('S0B 可执行事实计划缺少必需身份预检时拒绝', () => {
  const { userGoalPlan, semanticPlan } = planFor('event_summary')
  semanticPlan.grounding_plan.nodes = semanticPlan.grounding_plan.nodes.filter(
    (node) => node.execution_unit !== 'TOOL-01',
  )
  expectCode(
    'required_call_missing',
    () => new P2GovernedRegistryRuntime().admitPlan(
      semanticPlan,
      userGoalPlan,
      binding,
    ),
  )
})

test('S0B 越界问题零工具执行但保留禁止调用决策', async () => {
  const question = '这次中断的技术原因是什么？'
  const provider = new FixtureProvider()
  const planner = new FixturePlanner(userPlan(
    question,
    goal(question, 'cause_or_responsibility'),
  ))
  const answer = await new P1RuntimeV2SemanticTurnService(
    provider,
    planner,
  ).answer({
    userId: 'p2-s0b-test',
    authorizationScope: 'country_outage:read',
  }, {
    event_reference: reference,
    publication_id: publication,
    revision: 1,
    question,
  })

  assert.deepEqual(provider.calls, ['resolve'])
  assert.equal(answer.execution_trace.nodes.length, 0)
  assert.equal(
    answer.execution_trace.registry_admission.goal_resolutions[0]?.call_policy,
    'forbidden',
  )
})

test('S0B 非active单元在Executor之前拒绝', () => {
  const snapshot = loadSnapshot()
  const payload = snapshot.snapshot_payload as JsonObject
  const units = (payload.execution_unit_registry as JsonObject).entries as JsonObject[]
  units.find((unit) => unit.unit_id === 'TOOL-02')!.state = 'retired'
  const path = writeSnapshot('inactive.json', reseal(snapshot))
  const runtime = new P2GovernedRegistryRuntime(new P2RegistrySnapshotLoader(path))
  const { userGoalPlan, semanticPlan } = planFor('event_summary')
  expectCode('execution_unit_not_active', () =>
    runtime.admitPlan(semanticPlan, userGoalPlan, binding)
  )
})

test('S0B 快照未重封的篡改失败关闭', () => {
  const snapshot = loadSnapshot()
  const payload = snapshot.snapshot_payload as JsonObject
  payload.registry_revision = Number(payload.registry_revision) + 1
  const path = writeSnapshot('tampered.json', snapshot)
  expectCode('registry_snapshot_digest_mismatch', () =>
    new P2RegistrySnapshotLoader(path).load()
  )
})

test('S0B Capability与Unit摘要冲突在执行前拒绝', () => {
  const snapshot = loadSnapshot()
  const payload = snapshot.snapshot_payload as JsonObject
  const capabilities = (payload.capability_registry as JsonObject)
    .entries as JsonObject[]
  const capability = capabilities.find((item) => item.capability_id === 'CAP-002')!
  const references = capability.execution_units as JsonObject[]
  references[0]!.implementation_digest = `sha256:${'0'.repeat(64)}`
  const path = writeSnapshot('digest-conflict.json', reseal(snapshot))
  const runtime = new P2GovernedRegistryRuntime(new P2RegistrySnapshotLoader(path))
  const { userGoalPlan, semanticPlan } = planFor('event_summary')
  expectCode('capability_unit_digest_mismatch', () =>
    runtime.admitPlan(semanticPlan, userGoalPlan, binding)
  )
})

test('S0B active单元缺少Host Handler时拒绝执行', () => {
  const runtime = new P2GovernedRegistryRuntime(
    new P2RegistrySnapshotLoader(snapshotPath),
    ['TOOL-01'],
  )
  const { userGoalPlan, semanticPlan } = planFor('event_summary')
  expectCode('execution_handler_missing', () =>
    runtime.admitPlan(semanticPlan, userGoalPlan, binding)
  )
})

test('S0B OP-04缺少TOOL-03依赖节点时拒绝', () => {
  const { userGoalPlan, semanticPlan } = planFor('address_family_change', {
    address_family: 'ipv4',
    population: 'fixed_cohort',
    include_new_prefixes: false,
    analysis_mode: 'event_window_trend',
    time_scope: 'current_publication_window',
  })
  const op04 = semanticPlan.grounding_plan.nodes.find(
    (node) => node.execution_unit === 'OP-04',
  )!
  op04.depends_on = []
  const runtime = new P2GovernedRegistryRuntime(
    new P2RegistrySnapshotLoader(snapshotPath),
  )
  expectCode('execution_dependency_missing', () =>
    runtime.admitPlan(semanticPlan, userGoalPlan, binding)
  )
})

test('S0B 同轮保持旧快照，下一轮才读取新快照', () => {
  const first = loadSnapshot()
  const path = writeSnapshot('moving-pointer.json', first)
  const runtime = new P2GovernedRegistryRuntime(new P2RegistrySnapshotLoader(path))
  const { userGoalPlan, semanticPlan } = planFor('event_summary')
  const firstAdmission = runtime.admitPlan(semanticPlan, userGoalPlan, binding)
  const oldId = firstAdmission.receipt.registry_snapshot_id

  const second = structuredClone(first)
  const payload = second.snapshot_payload as JsonObject
  payload.registry_revision = Number(payload.registry_revision) + 1
  payload.candidate_id = 'p2-s0b-1111111111111111'
  writeSnapshot('moving-pointer.json', reseal(second))
  const secondAdmission = runtime.admitPlan(semanticPlan, userGoalPlan, binding)

  assert.notEqual(secondAdmission.receipt.registry_snapshot_id, oldId)
  assert.ok(firstAdmission.plan.grounding_plan.nodes.every((node) =>
    node.registry_binding?.registry_snapshot_id === oldId
  ))
})

test('S0B 缺失与关键身份null均失败关闭', () => {
  expectCode('registry_snapshot_missing', () =>
    new P2RegistrySnapshotLoader(join(temporaryRoot, 'missing.json')).load()
  )
  const snapshot = loadSnapshot()
  const payload = snapshot.snapshot_payload as JsonObject
  payload.candidate_id = null
  expectCode('registry_snapshot_invalid', () =>
    validateP2RegistrySnapshot(reseal(snapshot))
  )
})
