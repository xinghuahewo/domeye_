import type { CountryOutagePrincipal } from '../server/contracts.js'
import type { P1ConversationBinding } from './contracts.js'
import {
  P1ReadModelError,
  type P1RuntimeV2ReadProvider,
} from './general-read-model-provider.js'

type JsonObject = Record<string, any>

export const P1_RUNTIME_V2_SINGLE_TURN_SCHEMA =
  'country_outage_p1_single_turn_v2' as const

export interface P1RuntimeV2SingleTurnRequest {
  event_reference: string
  publication_id: string
  revision: number
  controlled_goal: 'event_summary'
}

export interface P1RuntimeV2Evidence {
  evidence_ref: string
  source:
    | 'resolution'
    | 'overview'
    | 'series'
    | 'asns'
    | 'paths'
    | 'audit'
    | 'derived'
  field_path: string
  value: string | number | boolean | null
  unit: string | null
  observed_at_utc: string | null
  incident_id: string
  publication_id: string
  revision: number
  collector_id: 'rrc25'
}

export interface P1RuntimeV2SingleTurnAnswer {
  schema_version: typeof P1_RUNTIME_V2_SINGLE_TURN_SCHEMA
  answerability: 'partial'
  goal: {
    goal_id: 'goal-1'
    requested_goal: 'event_summary'
    capability_ids: ['CAP-002', 'CAP-003', 'CAP-004']
  }
  binding: P1ConversationBinding
  answer_text: string
  evidence: P1RuntimeV2Evidence[]
  limitations: string[]
  unknowns: string[]
  execution_trace: {
    nodes: Array<{
      node_id: string
      execution_unit: 'TOOL-01' | 'TOOL-02'
      capability_ids: string[]
      status: 'passed'
      evidence_refs: string[]
    }>
    authorization: {
      original_scope: string
      effective_permission: 'country_outage:read'
      basis:
        | 'canonical_read'
        | 'event_read_global'
        | 'event_read_country'
      country_code: string
    }
    model_generated_fact_count: 0
    state_commit: 'none'
  }
  validation: {
    passed: true
    checked_identity_fields: string[]
    checked_evidence_refs: string[]
    errors: []
  }
  runtime_identity: {
    implementation: 'p1-runtime-v2-single-turn'
    contract_revision: 'p1-runtime-v2-s0-20260809-r2'
    language_layer: 'controlled-s1-entry'
    collector: 'rrc25'
  }
  completed_at: string
}

export class P1RuntimeV2SingleTurnError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly retryable = false,
  ) {
    super(message)
    this.name = 'P1RuntimeV2SingleTurnError'
  }
}

function object(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new P1RuntimeV2SingleTurnError('invalid_data', `${label} 不是对象`)
  }
  return value as JsonObject
}

function number(value: unknown, label: string): number {
  if (
    typeof value !== 'number'
    || !Number.isSafeInteger(value)
    || value < 0
  ) {
    throw new P1RuntimeV2SingleTurnError('invalid_data', `${label} 缺失或无效`)
  }
  return value
}

function string(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new P1RuntimeV2SingleTurnError('invalid_data', `${label} 缺失或无效`)
  }
  return value
}

function normalizeReference(value: string): string {
  return value.trim().replaceAll('+', ' ')
}

export interface P1RuntimeV2ReadPermissionCandidate {
  originalScope: string
  scopes: Set<string>
}

type EffectiveReadPermission =
  P1RuntimeV2SingleTurnAnswer['execution_trace']['authorization']

export function readP1RuntimeV2PermissionCandidate(
  principal: CountryOutagePrincipal,
): P1RuntimeV2ReadPermissionCandidate {
  const originalScope = principal.authorizationScope
  const scopes = new Set(
    originalScope.split(',').map((value) => value.trim()).filter(Boolean),
  )
  const recognized = [...scopes].some((scope) =>
    scope === 'country_outage:read'
    || scope === 'country_outage_event_read'
    || /^country_outage_event_read:[A-Z]{2}$/.test(scope)
  )
  if (!recognized) {
    throw new P1RuntimeV2SingleTurnError(
      'permission_denied',
      '当前主体没有 country_outage:read 权限',
    )
  }
  return { originalScope, scopes }
}

export function authorizeP1RuntimeV2Country(
  candidate: P1RuntimeV2ReadPermissionCandidate,
  countryCode: string,
): EffectiveReadPermission {
  const normalizedCountry = countryCode.trim().toUpperCase()
  let basis: EffectiveReadPermission['basis'] | null = null
  if (candidate.scopes.has('country_outage:read')) {
    basis = 'canonical_read'
  } else if (candidate.scopes.has('country_outage_event_read')) {
    basis = 'event_read_global'
  } else if (
    candidate.scopes.has(`country_outage_event_read:${normalizedCountry}`)
  ) {
    basis = 'event_read_country'
  }
  if (!basis) {
    throw new P1RuntimeV2SingleTurnError(
      'permission_denied',
      `当前主体无权读取 ${normalizedCountry} country_outage 事件`,
    )
  }
  return {
    original_scope: candidate.originalScope,
    effective_permission: 'country_outage:read',
    basis,
    country_code: normalizedCountry,
  }
}

export function throwIfP1RuntimeV2Cancelled(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw new P1RuntimeV2SingleTurnError(
      'cancelled',
      '本轮读取已取消，未发布回答',
    )
  }
}

function evidence(
  binding: P1ConversationBinding,
  source: P1RuntimeV2Evidence['source'],
  fieldPath: string,
  value: P1RuntimeV2Evidence['value'],
  unit: string | null,
  observedAtUtc: string | null = null,
): P1RuntimeV2Evidence {
  return {
    evidence_ref: `${source}.${fieldPath}`,
    source,
    field_path: fieldPath,
    value,
    unit,
    observed_at_utc: observedAtUtc,
    incident_id: binding.incident_id,
    publication_id: binding.publication_id,
    revision: binding.revision,
    collector_id: 'rrc25',
  }
}

export class P1RuntimeV2SingleTurnService {
  constructor(
    private readonly provider: P1RuntimeV2ReadProvider,
    private readonly now: () => Date = () => new Date(),
  ) {}

  async answer(
    principal: CountryOutagePrincipal,
    request: P1RuntimeV2SingleTurnRequest,
    signal?: AbortSignal,
  ): Promise<P1RuntimeV2SingleTurnAnswer> {
    const permissionCandidate = readP1RuntimeV2PermissionCandidate(principal)
    if (request.controlled_goal !== 'event_summary') {
      throw new P1RuntimeV2SingleTurnError(
        'unsupported_goal',
        'S1 垂直切片只接受 event_summary 受控入口',
      )
    }
    if (!request.event_reference.trim()) {
      throw new P1RuntimeV2SingleTurnError('invalid_reference', '事件引用不能为空')
    }
    try {
      throwIfP1RuntimeV2Cancelled(signal)
      const binding = await this.provider.resolve(request.event_reference, signal)
      throwIfP1RuntimeV2Cancelled(signal)
      return await this.answerResolved(
        principal,
        request,
        binding,
        signal,
        permissionCandidate,
      )
    } catch (error) {
      if (error instanceof P1RuntimeV2SingleTurnError) throw error
      if (error instanceof P1ReadModelError) {
        throw new P1RuntimeV2SingleTurnError(
          error.code,
          error.message,
          error.retryable,
        )
      }
      throw error
    }
  }

  /**
   * 供语义运行时在完成一次 TOOL-01 绑定预检后复用 S1 的确定性事实路径。
   * 该入口仍会重新核对主体权限与完整绑定，但不会再次调用 resolver。
   */
  async answerResolved(
    principal: CountryOutagePrincipal,
    request: P1RuntimeV2SingleTurnRequest,
    binding: P1ConversationBinding,
    signal?: AbortSignal,
    existingPermissionCandidate?: P1RuntimeV2ReadPermissionCandidate,
  ): Promise<P1RuntimeV2SingleTurnAnswer> {
    const permissionCandidate = existingPermissionCandidate
      ?? readP1RuntimeV2PermissionCandidate(principal)
    if (request.controlled_goal !== 'event_summary') {
      throw new P1RuntimeV2SingleTurnError(
        'unsupported_goal',
        'S1 垂直切片只接受 event_summary 受控入口',
      )
    }
    if (!request.event_reference.trim()) {
      throw new P1RuntimeV2SingleTurnError('invalid_reference', '事件引用不能为空')
    }
    try {
      throwIfP1RuntimeV2Cancelled(signal)
      if (
        normalizeReference(binding.legacy_reference)
          !== normalizeReference(request.event_reference)
        || binding.publication_id !== request.publication_id
        || binding.revision !== request.revision
      ) {
        throw new P1RuntimeV2SingleTurnError(
          'binding_conflict',
          '请求事件、publication 或 revision 与解析结果不一致',
        )
      }
      if (
        binding.event_type !== 'country_outage'
        || binding.collector_id !== 'rrc25'
      ) {
        throw new P1RuntimeV2SingleTurnError(
          'unsupported_event',
          'P1 只接受 RRC25 country_outage 事件',
        )
      }
      const authorization = authorizeP1RuntimeV2Country(
        permissionCandidate,
        binding.country_code,
      )
      if (binding.capabilities.overview !== 'available') {
        throw new P1RuntimeV2SingleTurnError(
          'capability_unavailable',
          '当前事件未协商 overview=available',
        )
      }
      throwIfP1RuntimeV2Cancelled(signal)
      const overview = await this.provider.readOverview(binding, signal)
      throwIfP1RuntimeV2Cancelled(signal)
      const event = object(overview.event, 'overview.event')
      const cohort = object(overview.cohort, 'overview.cohort')
      const current = object(overview.current, 'overview.current')
      const peaks = object(overview.peaks, 'overview.peaks')
      const interruptedPeak = object(
        peaks.interrupted_prefix_count,
        'overview.peaks.interrupted_prefix_count',
      )
      const detectedAtUtc = string(event.detected_at_utc, 'event.detected_at_utc')
      if (
        binding.detected_at_utc !== null
        && detectedAtUtc !== binding.detected_at_utc
      ) {
        throw new P1RuntimeV2SingleTurnError(
          'publication_identity_conflict',
          'overview 检测时间与绑定身份不一致',
        )
      }
      if (event.event_end_at_utc !== null) {
        throw new P1RuntimeV2SingleTurnError(
          'lifecycle_identity_conflict',
          '事件结束字段与 event_end_unknown 身份不一致',
        )
      }
      if (binding.lifecycle_state !== 'event_end_unknown') {
        throw new P1RuntimeV2SingleTurnError(
          'lifecycle_identity_conflict',
          'event_end_at_utc=null 但绑定生命周期不是 event_end_unknown',
        )
      }
      const fixedPrefixCount = number(
        cohort.fixed_prefix_count,
        'cohort.fixed_prefix_count',
      )
      const currentInterruptedPrefixCount = number(
        current.interrupted_prefix_count,
        'current.interrupted_prefix_count',
      )
      const peakInterruptedPrefixCount = number(
        interruptedPeak.value,
        'peaks.interrupted_prefix_count.value',
      )
      const peakInterruptedPrefixAtUtc = string(
        interruptedPeak.state_point_utc,
        'peaks.interrupted_prefix_count.state_point_utc',
      )
      const affectedAsCount = number(
        overview.affected_as_count,
        'overview.affected_as_count',
      )
      const completeBinding: P1ConversationBinding = {
        ...binding,
        detected_at_utc: detectedAtUtc,
      }
      const values = [
        evidence(completeBinding, 'overview', 'event.detected_at_utc', detectedAtUtc, 'UTC', detectedAtUtc),
        evidence(completeBinding, 'resolution', 'window_start_utc', binding.window_start_utc, 'UTC', binding.window_start_utc),
        evidence(completeBinding, 'resolution', 'window_end_utc', binding.window_end_utc, 'UTC', binding.window_end_utc),
        evidence(completeBinding, 'resolution', 'data_through', binding.data_through, 'UTC', binding.data_through),
        evidence(completeBinding, 'resolution', 'lifecycle_state', binding.lifecycle_state, null),
        evidence(completeBinding, 'resolution', 'is_final_in_data_range', binding.is_final_in_data_range, null),
        evidence(completeBinding, 'overview', 'cohort.fixed_prefix_count', fixedPrefixCount, 'prefix'),
        evidence(completeBinding, 'overview', 'current.interrupted_prefix_count', currentInterruptedPrefixCount, 'prefix', binding.data_through),
        evidence(completeBinding, 'overview', 'peaks.interrupted_prefix_count.value', peakInterruptedPrefixCount, 'prefix', peakInterruptedPrefixAtUtc),
        evidence(completeBinding, 'overview', 'peaks.interrupted_prefix_count.state_point_utc', peakInterruptedPrefixAtUtc, 'UTC', peakInterruptedPrefixAtUtc),
        evidence(completeBinding, 'overview', 'affected_as_count', affectedAsCount, 'asn'),
        evidence(completeBinding, 'overview', 'event.event_end_at_utc', null, 'UTC'),
      ]
      const overviewRefs = values
        .filter((item) => item.source === 'overview')
        .map((item) => item.evidence_ref)
      throwIfP1RuntimeV2Cancelled(signal)
      return {
        schema_version: P1_RUNTIME_V2_SINGLE_TURN_SCHEMA,
        answerability: 'partial',
        goal: {
          goal_id: 'goal-1',
          requested_goal: 'event_summary',
          capability_ids: ['CAP-002', 'CAP-003', 'CAP-004'],
        },
        binding: completeBinding,
        answer_text: [
          `当前回答绑定 ${binding.country_code} 的 RRC25 publication ${binding.publication_id}（revision ${binding.revision}）。`,
          `固定 cohort 共 ${fixedPrefixCount.toLocaleString('zh-CN')} 个前缀；窗口内中断前缀峰值为 ${peakInterruptedPrefixCount.toLocaleString('zh-CN')}，发生在 ${peakInterruptedPrefixAtUtc}；数据截止时仍有 ${currentInterruptedPrefixCount.toLocaleString('zh-CN')} 个。窗口内共有 ${affectedAsCount.toLocaleString('zh-CN')} 个不同 AS 曾进入受影响集合。`,
          `数据截至 ${binding.data_through ?? '未知'}。事件结束时间未知；窗口结束和数据完整均不能写成事件已经恢复或结束。`,
        ].join('\n'),
        evidence: values,
        limitations: [
          '仅反映 RRC25 BGP 控制面观测，不等同于用户连通性、流量、全国范围或原因。',
          `受影响 AS ${affectedAsCount.toLocaleString('zh-CN')} 是窗口内不同 AS 人口，不是同时峰值。`,
        ],
        unknowns: ['event_end_at_utc', '窗口外状态', '真实原因与用户影响'],
        execution_trace: {
          nodes: [
            {
              node_id: 'node-1',
              execution_unit: 'TOOL-01',
              capability_ids: ['CAP-001'],
              status: 'passed',
              evidence_refs: values
                .filter((item) => item.source === 'resolution')
                .map((item) => item.evidence_ref),
            },
            {
              node_id: 'node-2',
              execution_unit: 'TOOL-02',
              capability_ids: ['CAP-002', 'CAP-003', 'CAP-004'],
              status: 'passed',
              evidence_refs: overviewRefs,
            },
          ],
          authorization,
          model_generated_fact_count: 0,
          state_commit: 'none',
        },
        validation: {
          passed: true,
          checked_identity_fields: [
            'event_type', 'incident_id', 'publication_id', 'revision',
            'collector_id', 'cohort_id', 'window_start_utc', 'window_end_utc',
            'data_through', 'is_final_in_data_range', 'lifecycle_state',
          ],
          checked_evidence_refs: values.map((item) => item.evidence_ref),
          errors: [],
        },
        runtime_identity: {
          implementation: 'p1-runtime-v2-single-turn',
          contract_revision: 'p1-runtime-v2-s0-20260809-r2',
          language_layer: 'controlled-s1-entry',
          collector: 'rrc25',
        },
        completed_at: this.now().toISOString(),
      }
    } catch (error) {
      if (error instanceof P1RuntimeV2SingleTurnError) throw error
      if (error instanceof P1ReadModelError) {
        throw new P1RuntimeV2SingleTurnError(
          error.code,
          error.message,
          error.retryable,
        )
      }
      throw error
    }
  }
}
