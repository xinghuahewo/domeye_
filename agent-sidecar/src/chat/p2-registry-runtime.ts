import { createHash } from 'node:crypto'
import { lstatSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { P1ConversationBinding } from './contracts.js'
import type {
  P1GroundingNode,
  P1SemanticPlan,
  P1UserGoalPlan,
} from './runtime-v2-semantic.js'

type JsonObject = Record<string, unknown>

const SNAPSHOT_SCHEMA = 'country_outage_p2_s0b_registry_snapshot_v1'
const RUNTIME_INTEGRATION = 'implemented_not_deployed'
const ACTIVATION_SCOPE = 'runtime_candidate_shadow_only'
const DIGEST = /^sha256:[a-f0-9]{64}$/
const SNAPSHOT_ID = /^registry-snapshot-sha256:[a-f0-9]{64}$/
const MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024

export const P2_SUPPORTED_EXECUTION_UNITS = [
  'TOOL-01', 'TOOL-02', 'TOOL-03', 'TOOL-04', 'TOOL-05', 'TOOL-06',
  'OP-01', 'OP-02', 'OP-03', 'OP-04',
] as const

export interface P2RegistryNodeBinding {
  registry_snapshot_id: string
  registry_revision: number
  candidate_id: string
  capability_bindings: Array<{
    capability_id: string
    capability_version: string
    capability_contract_digest: string
  }>
  execution_unit_id: string
  execution_unit_version: string
  unit_contract_digest: string
  unit_implementation_digest: string
  unit_semantic_digest: string
  admission_status: 'admitted'
}

export interface P2RegistryAdmissionReceipt {
  schema_version: 'country_outage_p2_s0b_registry_admission_v1'
  status: 'admitted'
  registry_snapshot_id: string
  registry_revision: number
  candidate_id: string
  event_identity: {
    event_type: 'country_outage'
    incident_id: string
    publication_id: string
    revision: number
    collector_id: 'rrc25'
  }
  goal_resolutions: Array<{
    goal_id: string
    normalized_kind: string
    disposition: string
    capability_ids: string[]
    execution_unit_ids: string[]
    call_policy: 'required' | 'conditional' | 'forbidden'
  }>
  admitted_nodes: Array<{
    node_id: string
    goal_id: string
    execution_unit_id: string
    execution_unit_version: string
    capability_ids: string[]
  }>
  execution_started: false
  production_deployed: false
}

export interface P2AdmittedSemanticPlan {
  plan: P1SemanticPlan
  receipt: P2RegistryAdmissionReceipt
}

interface RegistryUnitReference {
  unit_id: string
  version: string
  contract_digest: string
  implementation_digest: string
  semantic_digest: string
}

interface RegistryCapability {
  capability_id: string
  version: string
  state: string
  contract_digest: string
  permission: string
  identity_constraints: {
    event_type: string
    collector_id: string
  }
  execution_units: RegistryUnitReference[]
}

interface RegistryExecutionUnit {
  unit_id: string
  version: string
  state: string
  capability_ids: string[]
  contract_digest: string
  implementation_digest: string
  semantic_digest: string
  permission: string
  dependencies: Array<{
    unit_id: string
    version: string
    relationship: string
  }>
}

interface RegistrySnapshotPayload {
  candidate_id: string
  registry_revision: number
  activation_scope: string
  runtime_integration: string
  capability_registry: { entries: RegistryCapability[] }
  execution_unit_registry: { entries: RegistryExecutionUnit[] }
}

export interface P2RegistrySnapshot {
  schema_version: string
  registry_snapshot_id: string
  snapshot_digest: string
  created_at: string
  production_deployed: false
  snapshot_payload: RegistrySnapshotPayload
}

export class P2RegistryRuntimeError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message)
    this.name = 'P2RegistryRuntimeError'
  }
}

function object(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new P2RegistryRuntimeError(
      'registry_snapshot_invalid',
      `${label} 必须是对象`,
    )
  }
  return value as JsonObject
}

function canonicalNumber(value: number): string {
  if (!Number.isFinite(value)) {
    throw new P2RegistryRuntimeError('registry_snapshot_invalid', '摘要输入包含非有限数字')
  }
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
  throw new P2RegistryRuntimeError('registry_snapshot_invalid', '摘要输入包含不支持的类型')
}

function digestValue(value: unknown): string {
  return `sha256:${createHash('sha256')
    .update(canonicalText(value))
    .digest('hex')}`
}

function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value)
    for (const item of Object.values(value as Record<string, unknown>)) {
      deepFreeze(item)
    }
  }
  return value
}

function defaultSnapshotPath(): string {
  const configured = process.env.COUNTRY_OUTAGE_P2_REGISTRY_SNAPSHOT
  if (configured?.trim()) return resolve(configured.trim())
  const moduleDirectory = dirname(fileURLToPath(import.meta.url))
  return resolve(
    moduleDirectory,
    '../../../../contracts/agent/country-outage-p2-s0b-runtime/registry-snapshot.json',
  )
}

function requireDigest(value: unknown, label: string): string {
  if (typeof value !== 'string' || !DIGEST.test(value)) {
    throw new P2RegistryRuntimeError(
      'registry_snapshot_invalid',
      `${label} 不是规范 SHA-256 摘要`,
    )
  }
  return value
}

function validateEntryIdentities(payload: RegistrySnapshotPayload): void {
  const capabilities = payload.capability_registry.entries
  const units = payload.execution_unit_registry.entries
  const capabilityKeys = new Set<string>()
  const unitKeys = new Set<string>()
  for (const capability of capabilities) {
    const key = `${capability.capability_id}@${capability.version}`
    if (capabilityKeys.has(key)) {
      throw new P2RegistryRuntimeError(
        'registry_snapshot_invalid',
        `Capability 身份重复：${key}`,
      )
    }
    capabilityKeys.add(key)
    requireDigest(capability.contract_digest, `${key}.contract_digest`)
  }
  for (const unit of units) {
    const key = `${unit.unit_id}@${unit.version}`
    if (unitKeys.has(key)) {
      throw new P2RegistryRuntimeError(
        'registry_snapshot_invalid',
        `Execution Unit 身份重复：${key}`,
      )
    }
    unitKeys.add(key)
    requireDigest(unit.contract_digest, `${key}.contract_digest`)
    requireDigest(unit.implementation_digest, `${key}.implementation_digest`)
    requireDigest(unit.semantic_digest, `${key}.semantic_digest`)
  }
}

export function validateP2RegistrySnapshot(
  value: unknown,
): P2RegistrySnapshot {
  const snapshot = object(value, 'registry_snapshot') as unknown as P2RegistrySnapshot
  if (
    snapshot.schema_version !== SNAPSHOT_SCHEMA
    || typeof snapshot.registry_snapshot_id !== 'string'
    || !SNAPSHOT_ID.test(snapshot.registry_snapshot_id)
    || snapshot.production_deployed !== false
  ) {
    throw new P2RegistryRuntimeError(
      'registry_snapshot_invalid',
      'Registry Snapshot 顶层身份或非部署边界无效',
    )
  }
  const payload = object(
    snapshot.snapshot_payload,
    'registry_snapshot.snapshot_payload',
  ) as unknown as RegistrySnapshotPayload
  if (
    !/^p2-s0b-[a-f0-9]{16}$/.test(payload.candidate_id)
    || !Number.isSafeInteger(payload.registry_revision)
    || payload.registry_revision < 1
    || payload.activation_scope !== ACTIVATION_SCOPE
    || payload.runtime_integration !== RUNTIME_INTEGRATION
    || !Array.isArray(payload.capability_registry?.entries)
    || !Array.isArray(payload.execution_unit_registry?.entries)
  ) {
    throw new P2RegistryRuntimeError(
      'registry_snapshot_invalid',
      'Registry Snapshot payload 身份、范围或双 Registry 无效',
    )
  }
  const expectedDigest = digestValue(payload)
  if (
    snapshot.snapshot_digest !== expectedDigest
    || snapshot.registry_snapshot_id
      !== `registry-snapshot-sha256:${expectedDigest.slice('sha256:'.length)}`
  ) {
    throw new P2RegistryRuntimeError(
      'registry_snapshot_digest_mismatch',
      'Registry Snapshot 内容寻址摘要不一致',
    )
  }
  validateEntryIdentities(payload)
  return deepFreeze(structuredClone(snapshot))
}

export class P2RegistrySnapshotLoader {
  constructor(
    private readonly path: string = defaultSnapshotPath(),
  ) {}

  load(): P2RegistrySnapshot {
    let stat
    try {
      stat = lstatSync(this.path)
    } catch {
      throw new P2RegistryRuntimeError(
        'registry_snapshot_missing',
        `无法读取 Registry Snapshot：${this.path}`,
      )
    }
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_SNAPSHOT_BYTES) {
      throw new P2RegistryRuntimeError(
        'registry_snapshot_unsafe',
        'Registry Snapshot 必须是大小受限的普通文件且不得为符号链接',
      )
    }
    let parsed: unknown
    try {
      parsed = JSON.parse(readFileSync(this.path, 'utf8'))
    } catch {
      throw new P2RegistryRuntimeError(
        'registry_snapshot_invalid',
        'Registry Snapshot 不是合法 JSON',
      )
    }
    return validateP2RegistrySnapshot(parsed)
  }
}

function findActiveCapability(
  snapshot: P2RegistrySnapshot,
  capabilityId: string,
): RegistryCapability {
  const matches = snapshot.snapshot_payload.capability_registry.entries
    .filter((entry) =>
      entry.capability_id === capabilityId && entry.state === 'active'
    )
  if (matches.length !== 1) {
    throw new P2RegistryRuntimeError(
      'capability_not_active',
      `Capability ${capabilityId} 没有唯一 active 版本`,
    )
  }
  return matches[0]!
}

function findActiveUnit(
  snapshot: P2RegistrySnapshot,
  unitId: string,
): RegistryExecutionUnit {
  const matches = snapshot.snapshot_payload.execution_unit_registry.entries
    .filter((entry) => entry.unit_id === unitId && entry.state === 'active')
  if (matches.length !== 1) {
    throw new P2RegistryRuntimeError(
      'execution_unit_not_active',
      `Execution Unit ${unitId} 没有唯一 active 版本`,
    )
  }
  return matches[0]!
}

function nodeBinding(
  snapshot: P2RegistrySnapshot,
  node: P1GroundingNode,
  nodeById: Map<string, P1GroundingNode>,
  supportedUnits: ReadonlySet<string>,
): P2RegistryNodeBinding {
  const unit = findActiveUnit(snapshot, node.execution_unit)
  if (!supportedUnits.has(unit.unit_id)) {
    throw new P2RegistryRuntimeError(
      'execution_handler_missing',
      `Execution Unit ${unit.unit_id} 没有已登记 Runtime Handler`,
    )
  }
  if (
    unit.permission !== 'country_outage:read'
    && unit.permission !== 'inherits_source_read_permission'
  ) {
    throw new P2RegistryRuntimeError(
      'registry_permission_denied',
      `Execution Unit ${unit.unit_id} 的权限不允许当前只读 Runtime`,
    )
  }
  const capabilityBindings = node.capability_ids.map((capabilityId) => {
    const capability = findActiveCapability(snapshot, capabilityId)
    if (
      capability.identity_constraints.event_type !== 'country_outage'
      || capability.identity_constraints.collector_id !== 'rrc25'
    ) {
      throw new P2RegistryRuntimeError(
        'registry_boundary_violation',
        `Capability ${capabilityId} 越过 country_outage/RRC25 边界`,
      )
    }
    const reference = capability.execution_units.find((item) =>
      item.unit_id === unit.unit_id
      && item.version === unit.version
    )
    if (
      !reference
      || reference.contract_digest !== unit.contract_digest
      || reference.implementation_digest !== unit.implementation_digest
      || reference.semantic_digest !== unit.semantic_digest
      || !unit.capability_ids.includes(capabilityId)
    ) {
      throw new P2RegistryRuntimeError(
        'capability_unit_digest_mismatch',
        `Capability ${capabilityId} 与 ${unit.unit_id} 的双向映射或摘要不一致`,
      )
    }
    return {
      capability_id: capability.capability_id,
      capability_version: capability.version,
      capability_contract_digest: capability.contract_digest,
    }
  })
  for (const dependency of unit.dependencies) {
    const satisfied = node.depends_on.some((nodeId) => {
      const source = nodeById.get(nodeId)
      if (!source) return false
      const sourceUnit = findActiveUnit(snapshot, source.execution_unit)
      return sourceUnit.unit_id === dependency.unit_id
        && sourceUnit.version === dependency.version
    })
    if (!satisfied) {
      throw new P2RegistryRuntimeError(
        'execution_dependency_missing',
        `${unit.unit_id} 缺少 ${dependency.unit_id}@${dependency.version} 依赖节点`,
      )
    }
  }
  return {
    registry_snapshot_id: snapshot.registry_snapshot_id,
    registry_revision: snapshot.snapshot_payload.registry_revision,
    candidate_id: snapshot.snapshot_payload.candidate_id,
    capability_bindings: capabilityBindings,
    execution_unit_id: unit.unit_id,
    execution_unit_version: unit.version,
    unit_contract_digest: unit.contract_digest,
    unit_implementation_digest: unit.implementation_digest,
    unit_semantic_digest: unit.semantic_digest,
    admission_status: 'admitted',
  }
}

export class P2GovernedRegistryRuntime {
  readonly #supportedUnits: ReadonlySet<string>

  constructor(
    private readonly loader = new P2RegistrySnapshotLoader(),
    supportedUnits: readonly string[] = P2_SUPPORTED_EXECUTION_UNITS,
  ) {
    this.#supportedUnits = new Set(supportedUnits)
  }

  admitPlan(
    plan: P1SemanticPlan,
    userGoalPlan: P1UserGoalPlan,
    binding: P1ConversationBinding,
  ): P2AdmittedSemanticPlan {
    const snapshot = this.loader.load()
    if (
      binding.event_type !== 'country_outage'
      || binding.collector_id !== 'rrc25'
    ) {
      throw new P2RegistryRuntimeError(
        'registry_boundary_violation',
        'Runtime 只允许 country_outage/RRC25 绑定',
      )
    }
    const admitted = structuredClone(plan)
    const nodeById = new Map(
      admitted.grounding_plan.nodes.map((node) => [node.node_id, node]),
    )
    for (const node of admitted.grounding_plan.nodes) {
      node.registry_binding = nodeBinding(
        snapshot,
        node,
        nodeById,
        this.#supportedUnits,
      )
    }
    const goals = new Map(userGoalPlan.goals.map((goal) => [goal.goal_id, goal]))
    const hasExecutableDecision = admitted.grounding_plan.decisions.some(
      (decision) => decision.answerability === 'supported'
        || decision.answerability === 'partial',
    )
    if (
      hasExecutableDecision
      && !admitted.grounding_plan.nodes.some((node) => node.execution_unit === 'TOOL-01')
    ) {
      throw new P2RegistryRuntimeError(
        'required_call_missing',
        '可执行事实计划缺少必需的 TOOL-01 事件身份预检节点',
      )
    }
    const goalResolutions = admitted.grounding_plan.decisions.map((decision) => {
      const goal = goals.get(decision.goal_id)
      if (!goal) {
        throw new P2RegistryRuntimeError(
          'registry_plan_invalid',
          `准入计划缺少 ${decision.goal_id} 用户目标`,
        )
      }
      const nodes = admitted.grounding_plan.nodes.filter(
        (node) => node.goal_id === decision.goal_id,
      )
      const executable = decision.answerability === 'supported'
        || decision.answerability === 'partial'
      if (executable !== (nodes.length > 0)) {
        throw new P2RegistryRuntimeError(
          'registry_plan_invalid',
          `目标 ${decision.goal_id} 的可执行性与节点不一致`,
        )
      }
      return {
        goal_id: decision.goal_id,
        normalized_kind: goal.normalized_kind,
        disposition: decision.answerability,
        capability_ids: [...new Set(nodes.flatMap((node) => node.capability_ids))],
        execution_unit_ids: [...new Set(nodes.map((node) => node.execution_unit))],
        call_policy: executable
          ? nodes.some((node) => node.execution_unit === 'TOOL-01')
            ? 'required' as const
            : 'conditional' as const
          : 'forbidden' as const,
      }
    })
    return {
      plan: admitted,
      receipt: {
        schema_version: 'country_outage_p2_s0b_registry_admission_v1',
        status: 'admitted',
        registry_snapshot_id: snapshot.registry_snapshot_id,
        registry_revision: snapshot.snapshot_payload.registry_revision,
        candidate_id: snapshot.snapshot_payload.candidate_id,
        event_identity: {
          event_type: 'country_outage',
          incident_id: binding.incident_id,
          publication_id: binding.publication_id,
          revision: binding.revision,
          collector_id: 'rrc25',
        },
        goal_resolutions: goalResolutions,
        admitted_nodes: admitted.grounding_plan.nodes.map((node) => ({
          node_id: node.node_id,
          goal_id: node.goal_id,
          execution_unit_id: node.execution_unit,
          execution_unit_version: node.registry_binding!.execution_unit_version,
          capability_ids: [...node.capability_ids],
        })),
        execution_started: false,
        production_deployed: false,
      },
    }
  }
}
