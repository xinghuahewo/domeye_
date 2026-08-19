import type {
  DomeyeActionReceipt,
  DomeyeArtifactEnvelope,
  DomeyeCapabilityObservation,
  DomeyeDataIdentity,
  DomeyeInteractiveAction,
  DomeyeMetricSeriesPayload,
  DomeyeSeriesExtremaPayload,
} from './contracts.js'
import {
  DomeyeActionReceiptSchema,
  DomeyeArtifactEnvelopeSchema,
  DomeyeCapabilityObservationSchema,
  DomeyeInteractiveActionSchema,
} from './contracts.js'
import type { DomeyeAdmittedDecision } from './trust-kernel.js'
import { canonicalJsonSha256 } from '../shared/deterministic-json.js'
import { Check } from 'typebox/value'

const METRIC = 'fixed_visible_ipv4_address_count' as const
const UNIT = 'unique_ipv4_address' as const
const POPULATION_DEFINITION =
  'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union' as const
const TIE_POLICY = 'first_observed_occurrence' as const
const SERIES_INTERVAL_MS = 5 * 60 * 1_000

export type DomeyeCapabilityExecutionErrorCode =
  | 'admission_required'
  | 'action_binding_conflict'
  | 'capability_not_registered'
  | 'read_model_failure'
  | 'identity_conflict'
  | 'metric_mismatch'
  | 'unit_mismatch'
  | 'source_response_digest_mismatch'
  | 'incomplete_series'
  | 'invalid_series_shape'
  | 'invalid_timestamp'
  | 'invalid_metric_value'
  | 'empty_observed_set'
  | 'source_artifact_missing'
  | 'source_artifact_conflict'

export class DomeyeCapabilityExecutionError extends Error {
  constructor(
    readonly code: DomeyeCapabilityExecutionErrorCode,
    message: string,
    readonly retryable = false,
  ) {
    super(message)
    this.name = 'DomeyeCapabilityExecutionError'
  }
}

export interface CountryOutageMetricSeriesReadRequest {
  readonly data_identity: DomeyeDataIdentity
  readonly metric: typeof METRIC
}

/** API 适配器只投影数据，不在这里绑定任何具体网络客户端。 */
export interface CountryOutageMetricSeriesRead {
  readonly data_identity: DomeyeDataIdentity
  readonly metric: typeof METRIC
  readonly unit: string
  readonly population_definition: typeof POPULATION_DEFINITION
  readonly timestamps_utc: readonly string[]
  readonly values: readonly (number | null)[]
  readonly definition: string
  readonly source_response_sha256: string
  readonly completeness: {
    readonly state: 'complete' | 'incomplete'
    readonly missing_slot_count: number
  }
  readonly evidence_refs: readonly string[]
}

export interface CountryOutageSeriesReadModel {
  readMetricSeries(
    request: CountryOutageMetricSeriesReadRequest,
    signal?: AbortSignal,
  ): Promise<CountryOutageMetricSeriesRead>
}

export type DomeyeSeriesExtremaStatistics =
  | Readonly<{
      result_state: 'known'
      time_slot_count: number
      observed_point_count: number
      null_point_count: number
      first: number
      first_at_utc: string
      last: number
      last_at_utc: string
      minimum: number
      minimum_at_utc: string
      maximum: number
      maximum_at_utc: string
      difference: number
      net_change: number
    }>
  | Readonly<{
      result_state: 'empty_observed_set'
      time_slot_count: number
      observed_point_count: 0
      null_point_count: number
      first: null
      first_at_utc: null
      last: null
      last_at_utc: null
      minimum: null
      minimum_at_utc: null
      maximum: null
      maximum_at_utc: null
      difference: null
      net_change: null
    }>

export type DomeyeCapabilityExecutionResult =
  | Readonly<{
      status: 'succeeded'
      artifact: DomeyeArtifactEnvelope
      receipt: DomeyeActionReceipt
      observation: DomeyeCapabilityObservation
    }>
  | Readonly<{
      status: 'failed'
      artifact: null
      receipt: DomeyeActionReceipt
      observation: DomeyeCapabilityObservation
    }>

function deepFreeze<T>(value: T): Readonly<T> {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value as Record<string, unknown>)) {
      deepFreeze(child)
    }
    Object.freeze(value)
  }
  return value
}

function digest(value: unknown): string {
  return `sha256:${canonicalJsonSha256(value)}`
}

function sameIdentity(
  left: DomeyeDataIdentity,
  right: DomeyeDataIdentity,
): boolean {
  return canonicalJsonSha256(left) === canonicalJsonSha256(right)
}

function validateTimestamp(value: string): void {
  if (!value || !Number.isFinite(Date.parse(value))) {
    throw new DomeyeCapabilityExecutionError(
      'invalid_timestamp',
      '时序包含无效 UTC 时间戳',
    )
  }
}

function validateSeriesShape(
  timestamps: readonly string[],
  values: readonly (number | null)[],
): void {
  if (timestamps.length === 0 || timestamps.length !== values.length) {
    throw new DomeyeCapabilityExecutionError(
      'invalid_series_shape',
      'timestamps 与 values 必须非空且同长',
    )
  }
  let previous = Number.NEGATIVE_INFINITY
  for (const timestamp of timestamps) {
    validateTimestamp(timestamp)
    const current = Date.parse(timestamp)
    if (current <= previous) {
      throw new DomeyeCapabilityExecutionError(
        'invalid_timestamp',
        'timestamps 必须严格递增',
      )
    }
    previous = current
  }
  for (const value of values) {
    if (value !== null && (!Number.isSafeInteger(value) || value < 0)) {
      throw new DomeyeCapabilityExecutionError(
        'invalid_metric_value',
        'IPv4 地址量只接受非负安全整数或 null',
      )
    }
  }
}

function validateCompleteFiveMinuteCoverage(
  timestamps: readonly string[],
  identity: DomeyeDataIdentity,
): void {
  const start = Date.parse(identity.window_start_utc)
  const end = Date.parse(identity.window_end_utc)
  const span = end - start
  const expectedSlotCount = span / SERIES_INTERVAL_MS + 1
  if (
    !Number.isSafeInteger(expectedSlotCount)
    || expectedSlotCount < 1
    || timestamps.length !== expectedSlotCount
    || timestamps.some((timestamp, index) =>
      Date.parse(timestamp) !== start + index * SERIES_INTERVAL_MS
    )
  ) {
    throw new DomeyeCapabilityExecutionError(
      'incomplete_series',
      'TOOL-03 时间轴必须精确覆盖冻结窗口内连续的五分钟槽',
    )
  }
}

function isSafeEvidenceReference(value: string): boolean {
  return value.trim().length > 0
    && !/(?:https?|wss?):\/\/|(?:^|:)\/api\/|\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b/iu.test(
      value,
    )
}

/** OP-01：null 不参与计算，并列极值保留第一次观测。 */
export function calculateFirstObservedSeriesExtrema(
  timestamps: readonly string[],
  values: readonly (number | null)[],
): DomeyeSeriesExtremaStatistics {
  validateSeriesShape(timestamps, values)
  const observed = values.flatMap((value, index) =>
    value === null ? [] : [{ value, index }],
  )
  if (observed.length === 0) {
    return deepFreeze({
      result_state: 'empty_observed_set',
      time_slot_count: values.length,
      observed_point_count: 0 as const,
      null_point_count: values.length,
      first: null,
      first_at_utc: null,
      last: null,
      last_at_utc: null,
      minimum: null,
      minimum_at_utc: null,
      maximum: null,
      maximum_at_utc: null,
      difference: null,
      net_change: null,
    })
  }
  const first = observed[0]!
  const last = observed[observed.length - 1]!
  let minimum = first
  let maximum = first
  for (const item of observed.slice(1)) {
    if (item.value < minimum.value) minimum = item
    if (item.value > maximum.value) maximum = item
  }
  return deepFreeze({
    result_state: 'known',
    time_slot_count: values.length,
    observed_point_count: observed.length,
    null_point_count: values.length - observed.length,
    first: first.value,
    first_at_utc: timestamps[first.index]!,
    last: last.value,
    last_at_utc: timestamps[last.index]!,
    minimum: minimum.value,
    minimum_at_utc: timestamps[minimum.index]!,
    maximum: maximum.value,
    maximum_at_utc: timestamps[maximum.index]!,
    difference: maximum.value - minimum.value,
    net_change: last.value - first.value,
  })
}

export class DomeyeCapabilityResolver {
  resolve(decision: DomeyeAdmittedDecision): DomeyeInteractiveAction {
    const receipt = decision?.receipt
    const receiptBody = receipt
      ? (({ receipt_digest: _ignored, ...body }) => body)(receipt)
      : null
    if (
      !decision
      || decision.status !== 'admitted'
      || !decision.action
      || !Check(DomeyeInteractiveActionSchema, decision.action)
      || decision.receipt.decision !== 'admitted'
      || receiptBody === null
      || decision.receipt.receipt_digest !== digest(receiptBody)
      || decision.receipt.proposal_id !== decision.action.proposal_id
      || decision.receipt.proposal_sequence
        !== decision.action.proposal_sequence
      || decision.receipt.capability_id !== decision.action.capability_id
      || decision.receipt.input_digest !== digest(decision.action.input)
      || decision.receipt.candidate_id !== decision.action.candidate_id
      || decision.receipt.tenant_id
        !== decision.action.trust_binding.tenant_id
      || !sameIdentity(
        decision.receipt.data_identity,
        decision.action.trust_binding.data_identity,
      )
      || decision.receipt.goal_state.goal_id
        !== decision.action.trust_binding.goal_state.goal_id
      || decision.receipt.goal_state.state_revision
        !== decision.action.trust_binding.goal_state.state_revision
      || decision.receipt.goal_state.state_digest
        !== decision.action.trust_binding.goal_state.state_digest
      || canonicalJsonSha256(decision.receipt.policy)
        !== canonicalJsonSha256(decision.action.trust_binding.policy)
      || canonicalJsonSha256(decision.receipt.registry)
        !== canonicalJsonSha256(decision.action.trust_binding.registry)
      || canonicalJsonSha256(decision.receipt.revocation)
        !== canonicalJsonSha256(decision.action.trust_binding.revocation)
      || decision.receipt.action_history_digest
        !== decision.action.trust_binding.action_history_digest
      || decision.receipt.execution_binding === null
      || canonicalJsonSha256(decision.receipt.execution_binding)
        !== canonicalJsonSha256(decision.action.execution_binding)
    ) {
      throw new DomeyeCapabilityExecutionError(
        'admission_required',
        'Gateway 只接受同一准入决定签发的 Action',
      )
    }
    const action = decision.action
    const expectedActionId = `action-sha256:${canonicalJsonSha256({
      proposal_id: action.proposal_id,
      candidate_id: action.candidate_id,
      principal_id: action.trust_binding.principal.principal_id,
      tenant_id: action.trust_binding.tenant_id,
      data_identity: action.trust_binding.data_identity,
      goal_state: action.trust_binding.goal_state,
      policy_digest: action.trust_binding.policy.policy_digest,
      registry_digest: action.trust_binding.registry.registry_digest,
      action_history_digest: action.trust_binding.action_history_digest,
    })}`
    if (action.action_id !== expectedActionId) {
      throw new DomeyeCapabilityExecutionError(
        'admission_required',
        'Action 身份与准入绑定不一致',
      )
    }
    const expected = action.capability_id === 'CAP-006'
      ? { id: 'TOOL-03', name: 'read_metric_series' }
      : { id: 'OP-01', name: 'series_extrema' }
    if (
      action.execution_binding.execution_unit_id !== expected.id
      || action.execution_binding.execution_unit_name !== expected.name
    ) {
      throw new DomeyeCapabilityExecutionError(
        'action_binding_conflict',
        'Capability 与准入执行绑定不一致',
      )
    }
    return action
  }
}

function safeError(error: unknown): DomeyeCapabilityExecutionError {
  if (error instanceof DomeyeCapabilityExecutionError) return error
  return new DomeyeCapabilityExecutionError(
    'read_model_failure',
    error instanceof Error ? error.message : 'read model 调用失败',
    true,
  )
}

function validateReadResult(
  action: DomeyeInteractiveAction,
  result: CountryOutageMetricSeriesRead,
  expectedSourceResponseSha256: string,
): void {
  if (!sameIdentity(result.data_identity, action.trust_binding.data_identity)) {
    throw new DomeyeCapabilityExecutionError(
      'identity_conflict',
      'TOOL-03 结果身份与准入身份不一致',
    )
  }
  if (result.metric !== METRIC) {
    throw new DomeyeCapabilityExecutionError(
      'metric_mismatch',
      'TOOL-03 返回了未准入指标',
    )
  }
  if (result.unit !== UNIT) {
    throw new DomeyeCapabilityExecutionError(
      'unit_mismatch',
      'TOOL-03 指标单位不一致',
    )
  }
  if (result.population_definition !== POPULATION_DEFINITION) {
    throw new DomeyeCapabilityExecutionError(
      'metric_mismatch',
      'TOOL-03 人口定义不一致',
    )
  }
  if (
    result.completeness.state !== 'complete'
    || result.completeness.missing_slot_count !== 0
  ) {
    throw new DomeyeCapabilityExecutionError(
      'incomplete_series',
      'TOOL-03 时序不完整，不能进入下游计算',
    )
  }
  if (
    !result.definition.trim()
    || !/^sha256:[a-f0-9]{64}$/.test(result.source_response_sha256)
    || result.evidence_refs.length === 0
    || new Set(result.evidence_refs).size !== result.evidence_refs.length
    || !result.evidence_refs.every(isSafeEvidenceReference)
  ) {
    throw new DomeyeCapabilityExecutionError(
      'invalid_series_shape',
      'TOOL-03 缺少定义、响应摘要或 Evidence 引用',
    )
  }
  if (result.source_response_sha256 !== expectedSourceResponseSha256) {
    throw new DomeyeCapabilityExecutionError(
      'source_response_digest_mismatch',
      'TOOL-03 原始响应摘要不属于当前冻结 Candidate',
    )
  }
  validateSeriesShape(result.timestamps_utc, result.values)
  if (
    result.timestamps_utc[0]
      !== action.trust_binding.data_identity.window_start_utc
    || result.timestamps_utc[result.timestamps_utc.length - 1]
      !== action.trust_binding.data_identity.window_end_utc
  ) {
    throw new DomeyeCapabilityExecutionError(
      'identity_conflict',
      'TOOL-03 时间轴未覆盖准入窗口',
    )
  }
  validateCompleteFiveMinuteCoverage(
    result.timestamps_utc,
    action.trust_binding.data_identity,
  )
}

function makeArtifact(
  action: DomeyeInteractiveAction,
  artifactKind: 'metric_series' | 'series_extrema',
  payload: DomeyeMetricSeriesPayload | DomeyeSeriesExtremaPayload,
  createdAtUtc: string,
): DomeyeArtifactEnvelope {
  const contentDigest = digest(payload)
  const artifactId = `artifact-sha256:${canonicalJsonSha256({
    artifact_kind: artifactKind,
    candidate_id: action.candidate_id,
    tenant_id: action.trust_binding.tenant_id,
    data_identity: action.trust_binding.data_identity,
    producer_action_id: action.action_id,
    execution_binding: action.execution_binding,
    content_digest: contentDigest,
  })}`
  const artifact = deepFreeze({
    schema_version: 'domeye_agent_artifact_envelope_v1',
    artifact_id: artifactId,
    artifact_kind: artifactKind,
    candidate_id: action.candidate_id,
    tenant_id: action.trust_binding.tenant_id,
    data_identity: action.trust_binding.data_identity,
    producer_action_id: action.action_id,
    execution_binding: action.execution_binding,
    immutable: true,
    content_digest: contentDigest,
    created_at_utc: createdAtUtc,
    payload,
  })
  if (!Check(DomeyeArtifactEnvelopeSchema, artifact)) {
    throw new DomeyeCapabilityExecutionError(
      'invalid_series_shape',
      'Artifact 不符合冻结机器合同',
    )
  }
  return artifact as DomeyeArtifactEnvelope
}

function makeReceipt(
  decision: DomeyeAdmittedDecision,
  status: 'succeeded' | 'failed',
  startedAtUtc: string,
  completedAtUtc: string,
  artifact: DomeyeArtifactEnvelope | null,
  error: DomeyeCapabilityExecutionError | null,
): DomeyeActionReceipt {
  const action = decision.action
  const body = {
    schema_version: 'domeye_agent_action_receipt_v1' as const,
    admission_receipt_id: decision.receipt.receipt_id,
    action_id: action.action_id,
    proposal_id: action.proposal_id,
    capability_id: action.capability_id,
    candidate_id: action.candidate_id,
    tenant_id: action.trust_binding.tenant_id,
    data_identity: action.trust_binding.data_identity,
    execution_binding: action.execution_binding,
    status,
    artifact_ids: artifact ? [artifact.artifact_id] : [],
    failure_code: error?.code ?? null,
    started_at_utc: startedAtUtc,
    completed_at_utc: completedAtUtc,
  }
  const receiptId = `action-receipt-sha256:${canonicalJsonSha256(body)}`
  const receipt = deepFreeze({
    ...body,
    receipt_id: receiptId,
    receipt_digest: digest({ ...body, receipt_id: receiptId }),
  })
  if (!Check(DomeyeActionReceiptSchema, receipt)) {
    throw new Error('action_receipt_contract_violation')
  }
  return receipt as DomeyeActionReceipt
}

function makeObservation(
  action: DomeyeInteractiveAction,
  artifact: DomeyeArtifactEnvelope | null,
  error: DomeyeCapabilityExecutionError | null,
  createdAtUtc: string,
): DomeyeCapabilityObservation {
  let safeSummary: DomeyeCapabilityObservation['safe_summary']
  if (!artifact) {
    safeSummary = {
      metric: METRIC,
      unit: null,
      result_state: 'unavailable',
      observed_point_count: null,
      finding_input: null,
    }
  } else if (artifact.artifact_kind === 'metric_series') {
    safeSummary = {
      metric: METRIC,
      unit: UNIT,
      result_state: 'series_available',
      observed_point_count: artifact.payload.observed_point_count,
      finding_input: null,
    }
  } else if (artifact.payload.result_state === 'known') {
    safeSummary = {
      metric: METRIC,
      unit: UNIT,
      result_state: 'known',
      observed_point_count: artifact.payload.observed_point_count,
      finding_input: {
        state: 'ready',
        source_artifact_ref: artifact.payload.source_artifact_id,
        extrema_artifact_ref: artifact.artifact_id,
        extrema_result_state: artifact.payload.result_state,
        next_owner: 'domeye_typed_finding_builder',
      },
    }
  } else {
    safeSummary = {
      metric: METRIC,
      unit: UNIT,
      result_state: 'empty_observed_set',
      observed_point_count: 0,
      finding_input: null,
    }
  }
  const body = {
    schema_version: 'domeye_agent_capability_observation_v1' as const,
    action_id: action.action_id,
    capability_id: action.capability_id,
    status: artifact ? 'succeeded' as const : 'failed' as const,
    reason_code: error?.code ?? null,
    artifact_ref: artifact?.artifact_id ?? null,
    data_identity: action.trust_binding.data_identity,
    safe_summary: safeSummary,
    created_at_utc: createdAtUtc,
  }
  const observation = deepFreeze({
    ...body,
    observation_id: `observation-sha256:${canonicalJsonSha256(body)}`,
  })
  if (!Check(DomeyeCapabilityObservationSchema, observation)) {
    throw new Error('capability_observation_contract_violation')
  }
  return observation
}

function validateSourceArtifact(
  action: Extract<DomeyeInteractiveAction, { capability_id: 'CAP-016' }>,
  artifacts: readonly DomeyeArtifactEnvelope[],
  expectedSourceResponseSha256: string,
): Extract<DomeyeArtifactEnvelope, { artifact_kind: 'metric_series' }> {
  const source = artifacts.find((artifact) =>
    artifact.artifact_id === action.input.source_artifact_id,
  )
  if (!source) {
    throw new DomeyeCapabilityExecutionError(
      'source_artifact_missing',
      'OP-01 缺少准入时绑定的源 Artifact',
    )
  }
  if (
    !Check(DomeyeArtifactEnvelopeSchema, source)
    || source.artifact_kind !== 'metric_series'
    || source.candidate_id !== action.candidate_id
    || source.tenant_id !== action.trust_binding.tenant_id
    || !sameIdentity(source.data_identity, action.trust_binding.data_identity)
    || !source.immutable
    || !action.trust_binding.occurred_action_ids.includes(
      source.producer_action_id,
    )
    || source.payload.metric !== action.input.metric
    || source.payload.unit !== UNIT
    || source.payload.source_response_sha256 !== expectedSourceResponseSha256
    || source.payload.completeness.state !== 'complete'
    || source.payload.completeness.missing_slot_count !== 0
    || digest(source.payload) !== source.content_digest
    || source.execution_binding.execution_unit_id !== 'TOOL-03'
    || source.execution_binding.execution_unit_name !== 'read_metric_series'
  ) {
    throw new DomeyeCapabilityExecutionError(
      'source_artifact_conflict',
      'OP-01 源 Artifact 与准入绑定不一致',
    )
  }
  validateSeriesShape(source.payload.timestamps_utc, source.payload.values)
  validateCompleteFiveMinuteCoverage(
    source.payload.timestamps_utc,
    action.trust_binding.data_identity,
  )
  return source
}

export class DomeyeCapabilityGateway {
  readonly #resolver: DomeyeCapabilityResolver
  readonly #seriesReadModel: CountryOutageSeriesReadModel
  readonly #expectedSourceResponseSha256: string
  readonly #now: () => Date

  constructor(options: {
    readonly resolver?: DomeyeCapabilityResolver
    readonly series_read_model: CountryOutageSeriesReadModel
    readonly expected_series_response_sha256: string
    readonly now?: () => Date
  }) {
    if (!/^sha256:[a-f0-9]{64}$/.test(
      options.expected_series_response_sha256,
    )) throw new Error('expected_series_response_sha256_invalid')
    this.#resolver = options.resolver ?? new DomeyeCapabilityResolver()
    this.#seriesReadModel = options.series_read_model
    this.#expectedSourceResponseSha256 =
      options.expected_series_response_sha256
    this.#now = options.now ?? (() => new Date())
  }

  async execute(
    decision: DomeyeAdmittedDecision,
    artifacts: readonly DomeyeArtifactEnvelope[],
    signal?: AbortSignal,
  ): Promise<DomeyeCapabilityExecutionResult> {
    const action = this.#resolver.resolve(decision)
    const startedAtUtc = this.#now().toISOString()
    try {
      signal?.throwIfAborted()
      let artifact: DomeyeArtifactEnvelope
      if (action.capability_id === 'CAP-006') {
        const read = await this.#seriesReadModel.readMetricSeries({
          data_identity: action.trust_binding.data_identity,
          metric: METRIC,
        }, signal)
        validateReadResult(
          action,
          read,
          this.#expectedSourceResponseSha256,
        )
        const payload: DomeyeMetricSeriesPayload = {
          schema_version: 'domeye_metric_series_artifact_v1',
          metric: METRIC,
          unit: UNIT,
          population_definition: POPULATION_DEFINITION,
          timestamps_utc: [...read.timestamps_utc],
          values: [...read.values],
          time_slot_count: read.timestamps_utc.length,
          observed_point_count:
            read.values.filter((value) => value !== null).length,
          null_point_count:
            read.values.filter((value) => value === null).length,
          completeness: read.completeness,
          definition: read.definition,
          source_response_sha256: read.source_response_sha256,
          evidence_refs: [...read.evidence_refs],
        }
        artifact = makeArtifact(
          action,
          'metric_series',
          payload,
          this.#now().toISOString(),
        )
      } else if (action.capability_id === 'CAP-016') {
        const source = validateSourceArtifact(
          action,
          artifacts,
          this.#expectedSourceResponseSha256,
        )
        const statistics = calculateFirstObservedSeriesExtrema(
          source.payload.timestamps_utc,
          source.payload.values,
        )
        const evidenceRefs = [...new Set([
          ...source.payload.evidence_refs,
          `derived:/operators/series_extrema/${METRIC}`,
        ])]
        const payload: DomeyeSeriesExtremaPayload = {
          schema_version: 'domeye_series_extrema_artifact_v1',
          metric: METRIC,
          unit: UNIT,
          tie_policy: TIE_POLICY,
          source_artifact_id: source.artifact_id,
          evidence_refs: evidenceRefs,
          ...statistics,
        }
        artifact = makeArtifact(
          action,
          'series_extrema',
          payload,
          this.#now().toISOString(),
        )
      } else {
        throw new DomeyeCapabilityExecutionError(
          'capability_not_registered',
          '首片只登记 CAP-006 与 CAP-016',
        )
      }
      const completedAtUtc = this.#now().toISOString()
      const receipt = makeReceipt(
        decision,
        'succeeded',
        startedAtUtc,
        completedAtUtc,
        artifact,
        null,
      )
      return deepFreeze({
        status: 'succeeded',
        artifact,
        receipt,
        observation: makeObservation(action, artifact, null, completedAtUtc),
      })
    } catch (caught) {
      const error = safeError(caught)
      const completedAtUtc = this.#now().toISOString()
      const receipt = makeReceipt(
        decision,
        'failed',
        startedAtUtc,
        completedAtUtc,
        null,
        error,
      )
      return deepFreeze({
        status: 'failed',
        artifact: null,
        receipt,
        observation: makeObservation(action, null, error, completedAtUtc),
      })
    }
  }
}

export const DOMEYE_FIRST_SLICE_EXECUTION_CONTRACT = deepFreeze({
  metric: METRIC,
  unit: UNIT,
  population_definition: POPULATION_DEFINITION,
  tie_policy: TIE_POLICY,
  interval_seconds: SERIES_INTERVAL_MS / 1_000,
  capability_bindings: {
    'CAP-006': 'TOOL-03',
    'CAP-016': 'OP-01',
  },
})
