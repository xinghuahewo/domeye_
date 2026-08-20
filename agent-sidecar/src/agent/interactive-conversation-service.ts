import { createHash } from 'node:crypto'
import { Check } from 'typebox/value'

import { canonicalJsonSha256 } from '../shared/deterministic-json.js'

import {
  DomeyeAnswerContextSchema,
  DomeyeActionReceiptSchema,
  DomeyeArtifactEnvelopeSchema,
  DomeyeCapabilityObservationSchema,
  DomeyeGoalDispositionSchema,
  DomeyeGoalStateSchema,
  DomeyeInteractiveActionSchema,
  DomeyeRendererDraftSchema,
  DomeyeResponseGuardDecisionSchema,
  DomeyeSemanticGoalSchema,
  DomeyeTypedFindingSchema,
  type DomeyeDataIdentity,
  type DomeyeTypedFinding,
} from './contracts.js'
import {
  buildCountryOutageAnswerContext,
  buildCountryOutageSeriesExtremaFinding,
  guardCountryOutageResponse,
} from './finding-answer.js'
import { calculateFirstObservedSeriesExtrema } from './capability-execution.js'
import {
  DOMEYE_FIRST_SLICE_QUESTION,
  DomeyeFirstSliceRunError,
  DomeyeFirstSliceRuntime,
  type DomeyeFirstSliceCandidateBinding,
  type DomeyeFirstSliceRunResult,
} from './first-slice-runtime.js'
import type {
  DomeyeDataIdentityVerifier,
  DomeyeVerifiedIdentityReceipt,
} from './country-outage-read-model.js'

export interface DomeyeAuthenticatedPrincipal {
  readonly userId: string
  readonly authorizationScope: string
}

export interface DomeyeConversationBindingRequest {
  readonly event_reference: string
  readonly publication_id: string
  readonly revision: number
  readonly idempotency_key: string
}

export interface DomeyeConversationTurnRequest {
  readonly question: string
  readonly idempotency_key: string
}

export interface DomeyeTurnEvidence {
  readonly evidence_ref: string
  readonly label: string
  readonly value: number | string | null
  readonly unit: string | null
  readonly observed_at_utc: string | null
}

export interface DomeyeAuthorizationDerivation {
  readonly schema_version: 'domeye_authorization_derivation_v1'
  readonly rule_id: 'country_outage_event_read_to_country_outage_read_v1'
  readonly source_scope:
    | 'country_outage_event_read'
    | `country_outage_event_read:${string}`
  readonly source_scope_kind: 'global_event_read' | 'country_event_read'
  readonly source_country_code: string
  readonly derived_scope: 'country_outage:read'
}

interface DomeyeInteractiveTurnTrace {
  readonly goal_id: string
  readonly goal_state_revision: number
  readonly disposition: string
  readonly authorization_derivation: DomeyeAuthorizationDerivation
  readonly admission_receipts: readonly {
    receipt_id: string
    decision: 'admitted' | 'rejected'
    reason_code: string | null
  }[]
  readonly action_receipts: readonly {
    receipt_id: string
    capability_id: 'CAP-006' | 'CAP-016'
    status: 'succeeded' | 'failed'
    failure_code: string | null
  }[]
  readonly artifacts: readonly {
    artifact_id: string
    artifact_kind: 'metric_series' | 'series_extrema'
    content_digest: string
  }[]
  readonly observations: readonly {
    observation_id: string
    capability_id: 'CAP-006' | 'CAP-016'
    status: 'succeeded' | 'rejected' | 'failed'
    reason_code: string | null
  }[]
  readonly response_guard: {
    decision: 'pass' | 'block'
    reason_codes: readonly string[]
  } | null
}

interface DomeyeInteractiveTurnAnswerCommon {
  readonly schema_version: 'domeye_interactive_agent_turn_answer_v1'
  readonly answer_text: string
  readonly candidate_id: string
  readonly data_identity: DomeyeDataIdentity
  readonly evidence: readonly DomeyeTurnEvidence[]
  readonly limitations: readonly string[]
  readonly usage: DomeyeFirstSliceRunResult['usage']
}

export interface DomeyeInteractiveSuccessfulTurnAnswer
  extends DomeyeInteractiveTurnAnswerCommon {
  readonly answerability: 'supported'
  readonly answer_source: 'renderer'
  readonly finding: DomeyeTypedFinding
  readonly trace: Readonly<
    Omit<
      DomeyeInteractiveTurnTrace,
      | 'disposition'
      | 'admission_receipts'
      | 'action_receipts'
      | 'observations'
      | 'response_guard'
    > & {
      disposition: 'goal_satisfied'
      admission_receipts: readonly {
        receipt_id: string
        decision: 'admitted'
        reason_code: null
      }[]
      action_receipts: readonly {
        receipt_id: string
        capability_id: 'CAP-006' | 'CAP-016'
        status: 'succeeded'
        failure_code: null
      }[]
      observations: readonly {
        observation_id: string
        capability_id: 'CAP-006' | 'CAP-016'
        status: 'succeeded'
        reason_code: null
      }[]
      response_guard: {
        decision: 'pass'
        reason_codes: readonly []
      }
    }
  >
}

export interface DomeyeInteractiveNonSuccessfulTurnAnswer
  extends DomeyeInteractiveTurnAnswerCommon {
  readonly answerability: 'clarification_required' | 'stopped'
  readonly answer_source: 'none'
  readonly finding: null
  readonly evidence: readonly []
  readonly limitations: readonly []
  readonly trace: DomeyeInteractiveTurnTrace
}

export type DomeyeInteractiveTurnAnswer =
  | DomeyeInteractiveSuccessfulTurnAnswer
  | DomeyeInteractiveNonSuccessfulTurnAnswer

interface DomeyeConversationTurnCommon {
  readonly turn_id: string
  readonly turn_number: number
  readonly question: string
  readonly created_at: string
}

export type DomeyeConversationTurn =
  | Readonly<DomeyeConversationTurnCommon & {
    state: 'executing'
    answer_success: false
    workflow_completed: false
  }>
  | Readonly<DomeyeConversationTurnCommon & {
    state: 'completed'
    answer_success: true
    workflow_completed: true
    answer: DomeyeInteractiveSuccessfulTurnAnswer
    completed_at: string
  }>
  | Readonly<DomeyeConversationTurnCommon & {
    state: 'clarification_required' | 'stopped'
    answer_success: false
    workflow_completed: false
    answer: DomeyeInteractiveNonSuccessfulTurnAnswer
    completed_at: string
  }>
  | Readonly<DomeyeConversationTurnCommon & {
    state: 'failed' | 'cancelled'
    answer_success: false
    workflow_completed: false
    error: {
    code: string
    message: string
    retryable: boolean
    }
    completed_at: string
  }>

export interface DomeyeInteractiveConversation {
  readonly schema_version: 'domeye_interactive_agent_conversation_v1'
  readonly conversation_id: string
  readonly binding: DomeyeDataIdentity & { event_reference: string }
  readonly identity_receipt_id: string
  readonly candidate_id: string
  readonly turns: readonly DomeyeConversationTurn[]
  readonly expires_at: string
  readonly created_at: string
}

interface StoredConversation {
  descriptor: DomeyeInteractiveConversation
  identityReceipt: DomeyeVerifiedIdentityReceipt
  ownerId: string
  authorizationScope: string
  authorizationDerivation: DomeyeAuthorizationDerivation
  createIdempotencyKey: string
  turnIdempotency: Map<string, { question: string, turnId: string }>
  failureEvidence: Map<string, DomeyeFirstSliceRunError['evidence']>
  active?: { turnId: string, controller: AbortController, promise: Promise<void> }
}

export class DomeyeConversationError extends Error {
  constructor(
    readonly code:
      | 'permission_denied'
      | 'conversation_not_found'
      | 'conversation_expired'
      | 'conversation_busy'
      | 'idempotency_conflict'
      | 'goal_outside_first_slice_contract'
      | 'verified_identity_outside_candidate',
    message: string,
    readonly retryable = false,
  ) {
    super(message)
    this.name = 'DomeyeConversationError'
  }
}

export interface DomeyeInteractiveConversationServiceOptions {
  readonly candidate: DomeyeFirstSliceCandidateBinding
  readonly identity_verifier: DomeyeDataIdentityVerifier
  readonly runtime: DomeyeFirstSliceRuntime
  readonly ttl_ms?: number
  readonly now?: () => Date
}

function sameIdentity(left: DomeyeDataIdentity, right: DomeyeDataIdentity): boolean {
  return canonicalJsonSha256(left) === canonicalJsonSha256(right)
}

function referenceSha256(reference: string): string {
  return createHash('sha256').update(reference, 'utf8').digest('hex')
}

function immutableClone<T>(value: T): T {
  const clone = structuredClone(value)
  const freeze = (item: unknown): void => {
    if (item === null || typeof item !== 'object' || Object.isFrozen(item)) return
    for (const child of Object.values(item as Record<string, unknown>)) {
      freeze(child)
    }
    Object.freeze(item)
  }
  freeze(clone)
  return clone
}

function safeErrorCode(error: unknown): string {
  if (error instanceof DomeyeConversationError) return error.code
  if (
    error instanceof Error
    && /^[a-z][a-z0-9_]{0,63}$/.test(error.message)
  ) return error.message
  return 'interactive_agent_failed'
}

function countryFromReference(reference: string): string | null {
  return reference.match(
    /^country_outage\/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}\/([A-Z]{2})\/[1-9]\d*\/[A-Za-z0-9_-]+$/,
  )?.[1] ?? null
}

function deriveRuntimeAuthorization(
  principal: DomeyeAuthenticatedPrincipal,
  reference: string,
): DomeyeAuthorizationDerivation {
  const country = countryFromReference(reference)
  if (!country) {
    throw new DomeyeConversationError(
      'permission_denied',
      '事件引用不属于可授权的国家中断事件',
    )
  }
  const scopes = new Set(
    principal.authorizationScope
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean),
  )
  const countryScope = `country_outage_event_read:${country}` as const
  const sourceScope = scopes.has(countryScope)
    ? countryScope
    : scopes.has('country_outage_event_read')
      ? 'country_outage_event_read' as const
      : null
  if (!sourceScope) {
    throw new DomeyeConversationError(
      'permission_denied',
      '缺少该事件的已认证读取权限',
    )
  }
  return immutableClone({
    schema_version: 'domeye_authorization_derivation_v1',
    rule_id: 'country_outage_event_read_to_country_outage_read_v1',
    source_scope: sourceScope,
    source_scope_kind: sourceScope === 'country_outage_event_read'
      ? 'global_event_read'
      : 'country_event_read',
    source_country_code: country,
    derived_scope: 'country_outage:read',
  })
}

function runtimePrincipal(
  principal: DomeyeAuthenticatedPrincipal,
  derivation: DomeyeAuthorizationDerivation,
) {
  return {
    principal_id: principal.userId,
    authorization_scopes: [derivation.derived_scope],
  }
}

function evidenceFromFinding(
  finding: DomeyeTypedFinding | null,
): DomeyeTurnEvidence[] {
  if (!finding) return []
  const values = finding.values
  return [
    ['first', '首值', values.first, values.first_at_utc],
    ['last', '末值', values.last, values.last_at_utc],
    ['minimum', '最低值', values.minimum, values.minimum_at_utc],
    ['maximum', '最大值', values.maximum, values.maximum_at_utc],
    ['difference', '极差', values.difference, null],
    ['net_change', '首末净变化', values.net_change, null],
  ].map(([field, label, value, observedAt]) => ({
    evidence_ref: `${finding.finding_id}#/values/${field}`,
    label: String(label),
    value: value as number | null,
    unit: finding.unit,
    observed_at_utc: observedAt as string | null,
  }))
}

function publicTrace(
  result: DomeyeFirstSliceRunResult,
  authorizationDerivation: DomeyeAuthorizationDerivation,
): DomeyeInteractiveTurnTrace {
  return {
    goal_id: result.semantic_goal.goal_id,
    goal_state_revision: result.goal_state.state_revision,
    disposition: result.loop.disposition.disposition,
    authorization_derivation: authorizationDerivation,
    admission_receipts: result.loop.admission_receipts.map((receipt) => ({
      receipt_id: receipt.receipt_id,
      decision: receipt.decision,
      reason_code: receipt.reason_code,
    })),
    action_receipts: result.loop.action_receipts.map((receipt) => ({
      receipt_id: receipt.receipt_id,
      capability_id: receipt.capability_id,
      status: receipt.status,
      failure_code: receipt.failure_code,
    })),
    artifacts: result.loop.artifacts.map((artifact) => ({
      artifact_id: artifact.artifact_id,
      artifact_kind: artifact.artifact_kind,
      content_digest: artifact.content_digest,
    })),
    observations: result.loop.observations.map((observation) => ({
      observation_id: observation.observation_id,
      capability_id: observation.capability_id,
      status: observation.status,
      reason_code: observation.reason_code,
    })),
    response_guard: result.answer
      ? {
          decision: result.answer.guard_result.decision,
          reason_codes: [...result.answer.guard_result.reason_codes],
        }
      : null,
  }
}

function publicSuccessfulAnswer(
  result: Extract<DomeyeFirstSliceRunResult, { outcome: 'completed' }>,
  authorizationDerivation: DomeyeAuthorizationDerivation,
): DomeyeInteractiveSuccessfulTurnAnswer {
  const trace = publicTrace(result, authorizationDerivation)
  return {
    schema_version: 'domeye_interactive_agent_turn_answer_v1',
    answerability: 'supported',
    answer_text: result.answer.answer,
    answer_source: 'renderer',
    candidate_id: result.candidate_id,
    data_identity: result.semantic_goal.data_identity,
    finding: result.finding,
    evidence: evidenceFromFinding(result.finding),
    limitations: result.answer_context.mandatory_limitations_zh,
    trace: {
      ...trace,
      disposition: 'goal_satisfied',
      admission_receipts: result.loop.admission_receipts.map((receipt) => ({
        receipt_id: receipt.receipt_id,
        decision: 'admitted',
        reason_code: null,
      })),
      action_receipts: result.loop.action_receipts.map((receipt) => ({
        receipt_id: receipt.receipt_id,
        capability_id: receipt.capability_id,
        status: 'succeeded',
        failure_code: null,
      })),
      observations: result.loop.observations.map((observation) => ({
        observation_id: observation.observation_id,
        capability_id: observation.capability_id,
        status: 'succeeded',
        reason_code: null,
      })),
      response_guard: {
        decision: 'pass',
        reason_codes: [],
      },
    },
    usage: result.usage,
  }
}

function publicNonSuccessfulAnswer(
  result: DomeyeFirstSliceRunResult,
  authorizationDerivation: DomeyeAuthorizationDerivation,
): DomeyeInteractiveNonSuccessfulTurnAnswer {
  return {
    schema_version: 'domeye_interactive_agent_turn_answer_v1',
    answerability: result.outcome === 'clarification_required'
      ? 'clarification_required'
      : 'stopped',
    answer_text: result.outcome === 'completed'
      ? '未形成满足公开合同的正确完整答案。'
      : result.outcome === 'clarification_required'
        ? '当前目标需要进一步澄清，未执行未获准能力。'
        : '当前调查已安全停止，未形成可发布答案。',
    answer_source: 'none',
    candidate_id: result.candidate_id,
    data_identity: result.semantic_goal.data_identity,
    finding: null,
    evidence: [],
    limitations: [],
    trace: publicTrace(result, authorizationDerivation),
    usage: result.usage,
  }
}

function hasValidFindingAndContext(
  result: Extract<DomeyeFirstSliceRunResult, { outcome: 'completed' }>,
  candidate: DomeyeFirstSliceCandidateBinding,
  identityReceipt: DomeyeVerifiedIdentityReceipt,
): boolean {
  const finding = result.finding
  const context = result.answer_context
  if (
    !Check(DomeyeTypedFindingSchema, finding)
    || !Check(DomeyeAnswerContextSchema, context)
  ) return false
  try {
    const expectedFinding = buildCountryOutageSeriesExtremaFinding({
      series_artifact: result.loop.artifacts[0]!,
      series_receipt: result.loop.action_receipts[0]!,
      extrema_artifact: result.loop.artifacts[1]!,
      extrema_receipt: result.loop.action_receipts[1]!,
    })
    const expectedContext = buildCountryOutageAnswerContext(
      expectedFinding,
      candidate.contract_digest,
    )
    return canonicalJsonSha256(finding)
        === canonicalJsonSha256(expectedFinding)
      && canonicalJsonSha256(context)
        === canonicalJsonSha256(expectedContext)
      && finding.candidate_id === candidate.candidate_id
      && context.candidate_id === candidate.candidate_id
      && result.candidate_id === candidate.candidate_id
      && canonicalJsonSha256(result.identity_receipt)
        === canonicalJsonSha256(identityReceipt)
      && sameIdentity(finding.data_identity, candidate.data_identity)
      && sameIdentity(context.data_identity, candidate.data_identity)
      && sameIdentity(
        result.semantic_goal.data_identity,
        candidate.data_identity,
      )
  } catch {
    return false
  }
}

function isUtcTimestamp(value: unknown): value is string {
  if (
    typeof value !== 'string'
    || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(value)
  ) return false
  const parsed = Date.parse(value)
  if (!Number.isFinite(parsed)) return false
  const normalized = value.includes('.')
    ? value.replace(/\.(\d{1,3})Z$/, (_match, fraction: string) =>
        `.${fraction.padEnd(3, '0')}Z`,
      )
    : value.replace(/Z$/, '.000Z')
  return new Date(parsed).toISOString() === normalized
}

function hasValidIdentityReceipt(
  receipt: DomeyeVerifiedIdentityReceipt,
  candidate: DomeyeFirstSliceCandidateBinding,
): boolean {
  const receiptBody = {
    candidate_id: receipt.candidate_id,
    reference_sha256: receipt.reference_sha256,
    data_identity: receipt.data_identity,
    resolver_response_sha256: receipt.resolver_response_sha256,
    overview_response_sha256: receipt.overview_response_sha256,
    verified_at_utc: receipt.verified_at_utc,
  }
  return receipt.schema_version === 'domeye_verified_data_identity_receipt_v1'
    && receipt.receipt_id
      === `identity-receipt-sha256:${referenceSha256(JSON.stringify(receiptBody))}`
    && receipt.candidate_id === candidate.candidate_id
    && sameIdentity(receipt.data_identity, candidate.data_identity)
    && receipt.immutable === true
    && isUtcTimestamp(receipt.verified_at_utc)
    && /^[a-f0-9]{64}$/.test(receipt.reference_sha256)
    && /^[a-f0-9]{64}$/.test(receipt.resolver_response_sha256)
    && /^[a-f0-9]{64}$/.test(receipt.overview_response_sha256)
    && Array.isArray(receipt.evidence_refs)
    && canonicalJsonSha256(receipt.evidence_refs)
      === canonicalJsonSha256([
        `domeye:evidence:resolver:sha256:${receipt.resolver_response_sha256}`,
        `domeye:evidence:overview:sha256:${receipt.overview_response_sha256}`,
      ])
}

function hasNoProtocolRejections(
  result: Extract<DomeyeFirstSliceRunResult, { outcome: 'completed' }>,
  cognitionAttemptCount: number,
): boolean {
  const rejections = result.loop.decision_protocol_rejections
  return rejections.length === 0
    && cognitionAttemptCount === result.loop.admission_receipts.length + 1
}

function hasValidProviderUsage(
  result: Extract<DomeyeFirstSliceRunResult, { outcome: 'completed' }>,
  candidate: DomeyeFirstSliceCandidateBinding,
): boolean {
  const usage = result.usage
  const attempts = usage.attempts
  const expectedModel = candidate.model_identity
  const nonLimitAttempts = attempts.filter(
    (attempt) => attempt.outcome !== 'limit_rejected',
  )
  const limitAttempts = attempts.filter(
    (attempt) => attempt.outcome === 'limit_rejected',
  )
  const cognitionAttempts = attempts.filter(
    (attempt) => attempt.phase === 'cognition',
  )
  const rendererAttempts = attempts.filter(
    (attempt) => attempt.phase === 'renderer',
  )
  const tokens = usage.tokens
  if (
    usage.maximum_attempt_count !== 10
    || candidate.budget_policy.model_api_attempt_limit !== 10
    || usage.cost_policy !== 'audit_only'
    || usage.attempt_count !== nonLimitAttempts.length
    || usage.attempt_count < 3
    || usage.attempt_count > usage.maximum_attempt_count
    || attempts.length > usage.maximum_attempt_count + 1
    || rendererAttempts.length !== 1
    || attempts.at(-1)?.phase !== 'renderer'
    || cognitionAttempts.length !== attempts.length - 1
    || cognitionAttempts.some((attempt) => attempt.outcome !== 'completed')
    || limitAttempts.length !== 0
    || result.answer.source !== 'renderer'
    || result.answer.render_attempt.status !== 'completed'
    || usage.attempt_count !== cognitionAttempts.length + 1
    || !hasNoProtocolRejections(result, cognitionAttempts.length)
    || result.loop.usage.maximum_attempt_count !== 10
    || result.loop.usage.cost_policy !== 'audit_only'
    || result.loop.usage.attempt_count !== cognitionAttempts.length
    || canonicalJsonSha256(result.loop.usage.attempts)
      !== canonicalJsonSha256(cognitionAttempts)
    || !Number.isSafeInteger(tokens.input)
    || !Number.isSafeInteger(tokens.output)
    || !Number.isSafeInteger(tokens.cache_read)
    || !Number.isSafeInteger(tokens.cache_write)
    || !Number.isSafeInteger(tokens.total)
    || Object.values(tokens).some((value) => value < 0)
    || tokens.total !== tokens.input + tokens.output
      + tokens.cache_read + tokens.cache_write
    || !Number.isFinite(usage.estimated_cost_usd)
    || usage.estimated_cost_usd < 0
  ) return false
  const rendererAttempt = rendererAttempts[0]!
  if (rendererAttempt.outcome !== 'completed') return false
  return !attempts.some((attempt, index) => {
    const endedAt = attempt.ended_at_utc
    const startedMs = Date.parse(attempt.started_at_utc)
    const endedMs = endedAt === null ? Number.NaN : Date.parse(endedAt)
    return attempt.attempt_id !== index + 1
      || attempt.provider !== expectedModel.provider
      || attempt.model !== expectedModel.model
      || attempt.model_version !== expectedModel.model_version
      || attempt.expected_response_model
        !== expectedModel.expected_response_model
      || (
        attempt.response_model !== null
        && attempt.response_model !== expectedModel.expected_response_model
      )
      || !isUtcTimestamp(attempt.started_at_utc)
      || attempt.outcome === 'started'
      || (
        attempt.outcome === 'completed'
        && (
          attempt.response_model !== expectedModel.expected_response_model
          || attempt.failure_code !== null
          || !isUtcTimestamp(endedAt)
          || typeof attempt.latency_ms !== 'number'
          || !Number.isSafeInteger(attempt.latency_ms)
          || attempt.latency_ms < 0
          || endedMs - startedMs !== attempt.latency_ms
        )
      )
      || (
        attempt.outcome === 'failed'
        && (
          typeof attempt.failure_code !== 'string'
          || !/^[a-z][a-z0-9_]{0,63}$/.test(attempt.failure_code)
          || !isUtcTimestamp(endedAt)
          || typeof attempt.latency_ms !== 'number'
          || !Number.isSafeInteger(attempt.latency_ms)
          || attempt.latency_ms < 0
          || endedMs - startedMs !== attempt.latency_ms
        )
      )
      || (
        attempt.outcome === 'limit_rejected'
        && (
          attempt.failure_code !== 'provider_request_limit_exceeded'
          || attempt.response_model !== null
          || endedAt !== attempt.started_at_utc
          || attempt.latency_ms !== 0
        )
      )
      || attempt.failure_code === 'provider_response_identity_mismatch'
      || attempt.failure_code === 'provider_response_identity_missing'
      || attempt.failure_code === 'provider_request_model_mismatch'
  })
}

function hasCompleteExecutionChain(
  result: Extract<DomeyeFirstSliceRunResult, { outcome: 'completed' }>,
  candidate: DomeyeFirstSliceCandidateBinding,
  expectedPrincipalId: string,
): boolean {
  const expectedCapabilities = ['CAP-006', 'CAP-016'] as const
  const admissions = result.loop.admission_receipts
  const actions = result.loop.action_receipts
  const artifacts = result.loop.artifacts
  const observations = result.loop.observations
  const cognitionAttemptCount = result.usage.attempts.filter(
    (attempt) => attempt.phase === 'cognition',
  ).length
  const admissionCycles = admissions.map(
    (admission) => admission.budget.model_api_attempts_used,
  )
  const decisionCycles = [
    ...admissionCycles,
    ...result.loop.decision_protocol_rejections.map(
      (rejection) => rejection.sequence,
    ),
    cognitionAttemptCount,
  ].sort((left, right) => left - right)
  if (
    !Check(DomeyeSemanticGoalSchema, result.semantic_goal)
    || !Check(DomeyeGoalStateSchema, result.loop.goal_state)
    || !Check(DomeyeGoalStateSchema, result.goal_state)
    || !Check(DomeyeGoalDispositionSchema, result.loop.disposition)
    || result.schema_version !== 'domeye_first_vertical_slice_run_v1'
    || result.candidate_id !== candidate.candidate_id
    || result.loop.disposition.disposition !== 'goal_satisfied'
    || result.loop.disposition.reason_code !== 'finding_input_ready'
    || result.goal_state.goal_id !== result.semantic_goal.goal_id
    || result.goal_state.status !== 'satisfied'
    || result.loop.goal_state.status !== 'answer_pending'
    || result.loop.goal_state.state_revision !== 3
    || result.goal_state.state_revision !== 4
    || admissions.length !== 2
    || actions.length !== 2
    || artifacts.length !== 2
    || observations.length !== 2
    || candidate.contract_version !== 'domeye.first-vertical-slice/v1.0'
    || candidate.policy.state !== 'active'
    || candidate.registry.state !== 'active'
    || canonicalJsonSha256(candidate.policy.allowed_capability_ids)
      !== canonicalJsonSha256(expectedCapabilities)
    || candidate.registry.capabilities.length !== 2
    || canonicalJsonSha256(decisionCycles)
      !== canonicalJsonSha256(Array.from(
        { length: cognitionAttemptCount },
        (_value, index) => index + 1,
      ))
    || admissionCycles.some((cycle, index) =>
      !Number.isSafeInteger(cycle)
      || cycle < 1
      || cycle >= cognitionAttemptCount
      || (index > 0 && cycle <= admissionCycles[index - 1]!)
    )
  ) return false
  const expectedGoalId = `goal-sha256:${canonicalJsonSha256({
    candidate_id: candidate.candidate_id,
    question: result.semantic_goal.requested_text,
    data_identity: candidate.data_identity,
  })}`
  if (
    result.semantic_goal.goal_id !== expectedGoalId
    || result.semantic_goal.requested_text !== DOMEYE_FIRST_SLICE_QUESTION
    || !sameIdentity(result.semantic_goal.data_identity, candidate.data_identity)
    || result.loop.disposition.goal_id !== expectedGoalId
    || result.loop.disposition.goal_state_revision
      !== result.loop.goal_state.state_revision
    || result.loop.goal_state.finding_ids.length !== 0
    || Date.parse(result.goal_state.updated_at_utc)
      < Date.parse(result.loop.goal_state.updated_at_utc)
  ) return false
  const initialState = {
    schema_version: 'domeye_agent_goal_state_v1' as const,
    goal_id: expectedGoalId,
    state_revision: 1,
    status: 'active' as const,
    completed_capability_ids: [],
    artifact_ids: [],
    finding_ids: [],
    last_observation_id: null,
    updated_at_utc: result.semantic_goal.created_at_utc,
  }
  for (const [index, capabilityId] of expectedCapabilities.entries()) {
    const admission = admissions[index]
    const action = actions[index]
    const artifact = artifacts[index]
    const observation = observations[index]
    const expectedKind = index === 0 ? 'metric_series' : 'series_extrema'
    if (
      !admission
      || typeof admission !== 'object'
      || !action
      || !artifact
      || !observation
      || !Check(DomeyeActionReceiptSchema, action)
      || !Check(DomeyeArtifactEnvelopeSchema, artifact)
      || !Check(DomeyeCapabilityObservationSchema, observation)
    ) return false
    const expectedArtifactIds = index === 0
      ? []
      : [artifacts[0]!.artifact_id]
    const expectedAdmissionState = index === 0
      ? initialState
      : {
          ...initialState,
          state_revision: 2,
          completed_capability_ids: ['CAP-006'] as const,
          artifact_ids: expectedArtifactIds,
          last_observation_id: observations[0]!.observation_id,
          updated_at_utc: observations[0]!.created_at_utc,
        }
    const {
      receipt_digest: admissionReceiptDigest,
      ...admissionWithId
    } = admission
    const {
      receipt_id: admissionReceiptId,
      ...admissionWithoutId
    } = admissionWithId
    const expectedInput = capabilityId === 'CAP-006'
      ? { metric: 'fixed_visible_ipv4_address_count' }
      : {
          metric: 'fixed_visible_ipv4_address_count',
          source_artifact_id: artifacts[0]!.artifact_id,
          tie_policy: 'first_observed_occurrence',
        }
    const priorActions = actions.slice(0, index)
    const expectedOccurredActionIds = priorActions.map(
      (receipt) => receipt.action_id,
    )
    const expectedRegistryBinding = candidate.registry.capabilities.find(
      (entry) => entry.capability_id === capabilityId,
    )?.execution_binding
    const {
      receipt_digest: actionReceiptDigest,
      receipt_id: actionReceiptId,
      ...actionReceiptBody
    } = action
    const {
      observation_id: observationId,
      ...observationBody
    } = observation
    const expectedArtifactId = `artifact-sha256:${canonicalJsonSha256({
      artifact_kind: artifact.artifact_kind,
      candidate_id: artifact.candidate_id,
      tenant_id: artifact.tenant_id,
      data_identity: artifact.data_identity,
      producer_action_id: artifact.producer_action_id,
      execution_binding: artifact.execution_binding,
      content_digest: artifact.content_digest,
    })}`
    const expectedFindingInput = artifact.artifact_kind === 'series_extrema'
      && artifact.payload.result_state === 'known'
      ? {
          state: 'ready' as const,
          source_artifact_ref: artifact.payload.source_artifact_id,
          extrema_artifact_ref: artifact.artifact_id,
          extrema_result_state: 'known' as const,
          next_owner: 'domeye_typed_finding_builder' as const,
        }
      : null
    const reconstructedAction = {
      schema_version: 'domeye_agent_interactive_action_v1' as const,
      action_id: action.action_id,
      proposal_id: admission.proposal_id,
      proposal_sequence: admission.proposal_sequence,
      capability_id: capabilityId,
      input: expectedInput,
      candidate_id: candidate.candidate_id,
      trust_binding: {
        principal: admission.principal,
        tenant_id: admission.tenant_id,
        data_identity: admission.data_identity,
        goal_state: {
          goal_id: admission.goal_state.goal_id,
          state_revision: admission.goal_state.state_revision,
          state_digest: admission.goal_state.state_digest,
        },
        policy: admission.policy,
        registry: admission.registry,
        budget: admission.budget,
        revocation: admission.revocation,
        occurred_action_ids: admission.occurred_action_ids,
        action_history_digest: admission.action_history_digest,
      },
      execution_binding: admission.execution_binding,
      admitted_at_utc: admission.created_at_utc,
    }
    const expectedActionId = `action-sha256:${canonicalJsonSha256({
      proposal_id: admission.proposal_id,
      candidate_id: candidate.candidate_id,
      principal_id: expectedPrincipalId,
      tenant_id: 'domeye',
      data_identity: candidate.data_identity,
      goal_state: reconstructedAction.trust_binding.goal_state,
      policy_digest: candidate.policy.policy_digest,
      registry_digest: candidate.registry.registry_digest,
      action_history_digest: admission.action_history_digest,
    })}`
    const admissionCreatedMs = Date.parse(admission.created_at_utc)
    const actionStartedMs = Date.parse(action.started_at_utc)
    const actionCompletedMs = Date.parse(action.completed_at_utc)
    const artifactCreatedMs = Date.parse(artifact.created_at_utc)
    if (
      admission.schema_version !== 'domeye_agent_admission_receipt_v1'
      || admissionReceiptId !== `admission-receipt-sha256:${canonicalJsonSha256(admissionWithoutId)}`
      || admissionReceiptDigest
        !== `sha256:${canonicalJsonSha256(admissionWithId)}`
      || admission.proposal_sequence !== index + 1
      || admission.capability_id !== capabilityId
      || !/^proposal-sha256:[a-f0-9]{64}$/.test(admission.proposal_id)
      || admission.decision !== 'admitted'
      || admission.reason_code !== null
      || admission.input_digest
        !== `sha256:${canonicalJsonSha256(expectedInput)}`
      || !/^sha256:[a-f0-9]{64}$/.test(admission.proposal_digest)
      || admission.goal_state.goal_id !== expectedGoalId
      || admission.goal_state.state_revision !== index + 1
      || admission.goal_state.state_digest
        !== `sha256:${canonicalJsonSha256(expectedAdmissionState)}`
      || canonicalJsonSha256(admission.goal_state.artifact_ids)
        !== canonicalJsonSha256(expectedArtifactIds)
      || admission.principal.principal_id !== expectedPrincipalId
      || canonicalJsonSha256(admission.principal.authorization_scopes)
        !== canonicalJsonSha256(['country_outage:read'])
      || admission.tenant_id !== 'domeye'
      || admission.policy.policy_id !== candidate.policy.policy_id
      || admission.policy.policy_digest !== candidate.policy.policy_digest
      || admission.registry.registry_snapshot_id
        !== candidate.registry.registry_snapshot_id
      || admission.registry.registry_digest !== candidate.registry.registry_digest
      || admission.budget.model_api_attempt_limit !== 10
      || admission.budget.approved_action_limit !== 2
      || admission.budget.approved_actions_used !== index + 1
      || admission.budget.cost_policy !== 'audit_only'
      || admission.budget.monetary_limit_usd !== null
      || admission.revocation.state !== 'not_revoked'
      || !isUtcTimestamp(admission.revocation.checked_at_utc)
      || !isUtcTimestamp(admission.created_at_utc)
      || canonicalJsonSha256(admission.occurred_action_ids)
        !== canonicalJsonSha256(expectedOccurredActionIds)
      || admission.action_history_digest
        !== `sha256:${canonicalJsonSha256(priorActions)}`
      || canonicalJsonSha256(admission.execution_binding)
        !== canonicalJsonSha256(action.execution_binding)
      || canonicalJsonSha256(admission.execution_binding)
        !== canonicalJsonSha256(expectedRegistryBinding)
      || admission.candidate_id !== candidate.candidate_id
      || !sameIdentity(admission.data_identity, candidate.data_identity)
      || !Check(DomeyeInteractiveActionSchema, reconstructedAction)
      || action.action_id !== expectedActionId
      || action.capability_id !== capabilityId
      || action.status !== 'succeeded'
      || action.failure_code !== null
      || action.proposal_id !== admission.proposal_id
      || action.admission_receipt_id !== admission.receipt_id
      || action.candidate_id !== candidate.candidate_id
      || action.tenant_id !== 'domeye'
      || !sameIdentity(action.data_identity, candidate.data_identity)
      || canonicalJsonSha256(action.execution_binding)
        !== canonicalJsonSha256(expectedRegistryBinding)
      || !isUtcTimestamp(action.started_at_utc)
      || !isUtcTimestamp(action.completed_at_utc)
      || actionStartedMs < admissionCreatedMs
      || actionCompletedMs < actionStartedMs
      || actionReceiptId
        !== `action-receipt-sha256:${canonicalJsonSha256(actionReceiptBody)}`
      || actionReceiptDigest !== `sha256:${canonicalJsonSha256({
        ...actionReceiptBody,
        receipt_id: actionReceiptId,
      })}`
      || artifact.artifact_kind !== expectedKind
      || artifact.candidate_id !== candidate.candidate_id
      || artifact.tenant_id !== 'domeye'
      || artifact.immutable !== true
      || !sameIdentity(artifact.data_identity, candidate.data_identity)
      || artifact.producer_action_id !== action.action_id
      || artifact.content_digest
        !== `sha256:${canonicalJsonSha256(artifact.payload)}`
      || artifact.artifact_id !== expectedArtifactId
      || !isUtcTimestamp(artifact.created_at_utc)
      || artifactCreatedMs < actionStartedMs
      || artifactCreatedMs > actionCompletedMs
      || canonicalJsonSha256(artifact.execution_binding)
        !== canonicalJsonSha256(action.execution_binding)
      || action.artifact_ids.length !== 1
      || action.artifact_ids[0] !== artifact.artifact_id
      || observation.capability_id !== capabilityId
      || observation.status !== 'succeeded'
      || observation.reason_code !== null
      || observation.action_id !== action.action_id
      || observation.artifact_ref !== artifact.artifact_id
      || !sameIdentity(observation.data_identity, candidate.data_identity)
      || observation.safe_summary.metric
        !== 'fixed_visible_ipv4_address_count'
      || observation.safe_summary.unit !== 'unique_ipv4_address'
      || observation.safe_summary.result_state !== (
        artifact.artifact_kind === 'metric_series'
          ? 'series_available'
          : artifact.payload.result_state
      )
      || observation.safe_summary.observed_point_count
        !== artifact.payload.observed_point_count
      || canonicalJsonSha256(observation.safe_summary.finding_input)
        !== canonicalJsonSha256(expectedFindingInput)
      || observation.created_at_utc !== action.completed_at_utc
      || observationId
        !== `observation-sha256:${canonicalJsonSha256(observationBody)}`
    ) return false
  }
  const seriesArtifact = artifacts[0]
  const extremaArtifact = artifacts[1]
  if (
    seriesArtifact?.artifact_kind !== 'metric_series'
    || extremaArtifact?.artifact_kind !== 'series_extrema'
    || seriesArtifact.payload.source_response_sha256
      !== candidate.series_response_sha256
    || extremaArtifact.payload.source_artifact_id !== seriesArtifact.artifact_id
    || seriesArtifact.payload.timestamps_utc.length
      !== seriesArtifact.payload.values.length
    || seriesArtifact.payload.time_slot_count
      !== seriesArtifact.payload.values.length
    || seriesArtifact.payload.observed_point_count
      !== seriesArtifact.payload.values.filter((value) => value !== null).length
    || seriesArtifact.payload.null_point_count
      !== seriesArtifact.payload.values.filter((value) => value === null).length
    || seriesArtifact.payload.completeness.state !== 'complete'
    || seriesArtifact.payload.completeness.missing_slot_count !== 0
    || seriesArtifact.payload.timestamps_utc.some((timestamp, index) =>
      Date.parse(timestamp)
        !== Date.parse(candidate.data_identity.window_start_utc)
          + index * 5 * 60 * 1_000
    )
    || seriesArtifact.payload.timestamps_utc.at(-1)
      !== candidate.data_identity.window_end_utc
  ) return false
  try {
    const expectedExtrema = calculateFirstObservedSeriesExtrema(
      seriesArtifact.payload.timestamps_utc,
      seriesArtifact.payload.values,
    )
    const {
      schema_version: _schemaVersion,
      metric: _metric,
      unit: _unit,
      tie_policy: _tiePolicy,
      source_artifact_id: _sourceArtifactId,
      evidence_refs: _evidenceRefs,
      ...actualExtrema
    } = extremaArtifact.payload
    if (
      canonicalJsonSha256(actualExtrema)
        !== canonicalJsonSha256(expectedExtrema)
      || canonicalJsonSha256(extremaArtifact.payload.evidence_refs)
        !== canonicalJsonSha256([
          ...seriesArtifact.payload.evidence_refs,
          'derived:/operators/series_extrema/fixed_visible_ipv4_address_count',
        ])
    ) return false
  } catch {
    return false
  }
  return canonicalJsonSha256(result.loop.goal_state.completed_capability_ids)
      === canonicalJsonSha256(expectedCapabilities)
    && canonicalJsonSha256(result.loop.goal_state.artifact_ids)
      === canonicalJsonSha256(artifacts.map((artifact) => artifact.artifact_id))
    && result.loop.goal_state.last_observation_id
      === observations[1]?.observation_id
    && canonicalJsonSha256(result.goal_state.completed_capability_ids)
      === canonicalJsonSha256(expectedCapabilities)
    && canonicalJsonSha256(result.goal_state.artifact_ids)
      === canonicalJsonSha256(artifacts.map((artifact) => artifact.artifact_id))
    && canonicalJsonSha256(result.goal_state.finding_ids)
      === canonicalJsonSha256([result.finding.finding_id])
    && canonicalJsonSha256(result.finding.artifact_refs)
      === canonicalJsonSha256(artifacts.map((artifact) => artifact.artifact_id))
    && canonicalJsonSha256(result.finding.receipt_refs)
      === canonicalJsonSha256(actions.map((action) => action.receipt_id))
    && result.goal_state.last_observation_id
      === observations[1]?.observation_id
}

function hasSuccessfulFinalAnswerUnchecked(
  result: DomeyeFirstSliceRunResult,
  candidate: DomeyeFirstSliceCandidateBinding,
  identityReceipt: DomeyeVerifiedIdentityReceipt,
  expectedPrincipalId: string,
): boolean {
  if (result.outcome !== 'completed') return false
  const finding = result.finding
  const values = finding.values
  if (
    !hasValidIdentityReceipt(identityReceipt, candidate)
    || !hasValidFindingAndContext(result, candidate, identityReceipt)
    || !hasCompleteExecutionChain(result, candidate, expectedPrincipalId)
    || !hasValidProviderUsage(result, candidate)
    || finding.value_state !== 'known'
    || finding.completeness_state !== 'complete'
    || finding.observed_point_count < 1
    || values.first === null
    || values.first_at_utc === null
    || values.last === null
    || values.last_at_utc === null
    || values.minimum === null
    || values.minimum_at_utc === null
    || values.maximum === null
    || values.maximum_at_utc === null
    || values.difference === null
    || values.net_change === null
    || !result.answer.answer.trim()
  ) return false
  if (!Check(DomeyeResponseGuardDecisionSchema, result.answer.guard_result)) {
    return false
  }
  try {
    if (
      result.answer.source !== 'renderer'
      || result.answer.render_attempt.status !== 'completed'
      || !Check(
        DomeyeRendererDraftSchema,
        result.answer.render_attempt.draft,
      )
      || result.answer.answer !== result.answer.render_attempt.draft.text
    ) return false
    const recomputedGuard = guardCountryOutageResponse(
      result.answer_context,
      result.answer.render_attempt.draft,
    )
    return recomputedGuard.decision === 'pass'
      && canonicalJsonSha256(recomputedGuard)
        === canonicalJsonSha256(result.answer.guard_result)
  } catch {
    return false
  }
}

export function hasSuccessfulDomeyePublicFinalAnswer(
  result: DomeyeFirstSliceRunResult,
  candidate: DomeyeFirstSliceCandidateBinding,
  identityReceipt: DomeyeVerifiedIdentityReceipt,
  expectedPrincipalId: string,
): result is Extract<DomeyeFirstSliceRunResult, { outcome: 'completed' }> {
  try {
    return hasSuccessfulFinalAnswerUnchecked(
      result,
      candidate,
      identityReceipt,
      expectedPrincipalId,
    )
  } catch {
    return false
  }
}

export class DomeyeInteractiveConversationService {
  readonly #options: DomeyeInteractiveConversationServiceOptions
  readonly #now: () => Date
  readonly #ttlMs: number
  readonly #conversations = new Map<string, StoredConversation>()
  readonly #createIdempotency = new Map<string, string>()

  constructor(options: DomeyeInteractiveConversationServiceOptions) {
    this.#options = options
    this.#now = options.now ?? (() => new Date())
    this.#ttlMs = options.ttl_ms ?? 30 * 60 * 1_000
    if (!Number.isSafeInteger(this.#ttlMs) || this.#ttlMs < 60_000) {
      throw new Error('conversation_ttl_invalid')
    }
  }

  #assertOwner(
    principal: DomeyeAuthenticatedPrincipal,
    stored: StoredConversation,
  ): void {
    if (
      stored.ownerId !== principal.userId
      || stored.authorizationScope !== principal.authorizationScope
    ) throw new DomeyeConversationError('permission_denied', '无权访问该会话')
    if (Date.parse(stored.descriptor.expires_at) <= this.#now().valueOf()) {
      throw new DomeyeConversationError('conversation_expired', '会话已过期')
    }
  }

  async createConversation(
    principal: DomeyeAuthenticatedPrincipal,
    request: DomeyeConversationBindingRequest,
  ): Promise<{ conversation: DomeyeInteractiveConversation, deduplicated: boolean }> {
    const authorizationDerivation = deriveRuntimeAuthorization(
      principal,
      request.event_reference,
    )
    const idempotencyScope = `${principal.userId}\u0000${request.idempotency_key}`
    const existingId = this.#createIdempotency.get(idempotencyScope)
    if (existingId) {
      const existing = this.#conversations.get(existingId)
      if (!existing) throw new Error('idempotency_index_corrupt')
      if (
        existing.descriptor.binding.event_reference !== request.event_reference
        || existing.descriptor.binding.publication_id !== request.publication_id
        || existing.descriptor.binding.revision !== request.revision
      ) throw new DomeyeConversationError(
        'idempotency_conflict',
        '同一幂等键对应不同会话绑定',
      )
      return { conversation: structuredClone(existing.descriptor), deduplicated: true }
    }
    const verifiedReceipt = await this.#options.identity_verifier.verify({
      reference: request.event_reference,
      publication_id: request.publication_id,
      revision: request.revision,
      candidate_id: this.#options.candidate.candidate_id,
    })
    if (
      verifiedReceipt.candidate_id !== this.#options.candidate.candidate_id
      || verifiedReceipt.immutable !== true
      || verifiedReceipt.reference_sha256 !== referenceSha256(
        request.event_reference,
      )
      || verifiedReceipt.data_identity.publication_id
        !== request.publication_id
      || verifiedReceipt.data_identity.revision !== request.revision
      || !sameIdentity(
        verifiedReceipt.data_identity,
        this.#options.candidate.data_identity,
      )
    ) throw new DomeyeConversationError(
      'verified_identity_outside_candidate',
      '该事件身份不属于当前首片 Candidate',
    )
    const identityReceipt = immutableClone(verifiedReceipt)
    const createdAt = this.#now()
    const conversationId = `conversation_sha256_${canonicalJsonSha256({
      owner_id: principal.userId,
      identity_receipt_id: identityReceipt.receipt_id,
      idempotency_key: request.idempotency_key,
    })}`
    const descriptor: DomeyeInteractiveConversation = {
      schema_version: 'domeye_interactive_agent_conversation_v1',
      conversation_id: conversationId,
      binding: {
        ...identityReceipt.data_identity,
        event_reference: request.event_reference,
      },
      identity_receipt_id: identityReceipt.receipt_id,
      candidate_id: this.#options.candidate.candidate_id,
      turns: [],
      expires_at: new Date(createdAt.valueOf() + this.#ttlMs).toISOString(),
      created_at: createdAt.toISOString(),
    }
    this.#conversations.set(conversationId, {
      descriptor,
      identityReceipt,
      ownerId: principal.userId,
      authorizationScope: principal.authorizationScope,
      authorizationDerivation,
      createIdempotencyKey: request.idempotency_key,
      turnIdempotency: new Map(),
      failureEvidence: new Map(),
    })
    this.#createIdempotency.set(idempotencyScope, conversationId)
    return { conversation: structuredClone(descriptor), deduplicated: false }
  }

  async getConversation(
    principal: DomeyeAuthenticatedPrincipal,
    conversationId: string,
  ): Promise<DomeyeInteractiveConversation> {
    const stored = this.#conversations.get(conversationId)
    if (!stored) throw new DomeyeConversationError(
      'conversation_not_found',
      '会话不存在',
    )
    this.#assertOwner(principal, stored)
    return structuredClone(stored.descriptor)
  }

  async createTurn(
    principal: DomeyeAuthenticatedPrincipal,
    conversationId: string,
    request: DomeyeConversationTurnRequest,
  ): Promise<{ turn: DomeyeConversationTurn, deduplicated: boolean }> {
    const stored = this.#conversations.get(conversationId)
    if (!stored) throw new DomeyeConversationError(
      'conversation_not_found',
      '会话不存在',
    )
    this.#assertOwner(principal, stored)
    if (request.question.trim() !== DOMEYE_FIRST_SLICE_QUESTION) {
      throw new DomeyeConversationError(
        'goal_outside_first_slice_contract',
        '当前 Candidate 只实现首个纵向切片固定问题',
      )
    }
    const prior = stored.turnIdempotency.get(request.idempotency_key)
    if (prior) {
      if (prior.question !== request.question) throw new DomeyeConversationError(
        'idempotency_conflict',
        '同一幂等键对应不同问题',
      )
      const turn = stored.descriptor.turns.find(
        (item) => item.turn_id === prior.turnId,
      )
      if (!turn) throw new Error('turn_idempotency_index_corrupt')
      return { turn: structuredClone(turn), deduplicated: true }
    }
    if (stored.active) throw new DomeyeConversationError(
      'conversation_busy',
      '当前会话已有执行中的 turn',
      true,
    )
    const createdAt = this.#now().toISOString()
    const turnNumber = stored.descriptor.turns.length + 1
    const turnId = `turn_sha256_${canonicalJsonSha256({
      conversation_id: conversationId,
      turn_number: turnNumber,
      question: request.question,
      idempotency_key: request.idempotency_key,
    })}`
    const turn: DomeyeConversationTurn = {
      turn_id: turnId,
      turn_number: turnNumber,
      question: request.question,
      state: 'executing',
      answer_success: false,
      workflow_completed: false,
      created_at: createdAt,
    }
    stored.descriptor = {
      ...stored.descriptor,
      turns: [...stored.descriptor.turns, turn],
    }
    stored.turnIdempotency.set(request.idempotency_key, {
      question: request.question,
      turnId,
    })
    const controller = new AbortController()
    const promise = this.#executeTurn(
      principal,
      stored,
      turnId,
      request.question,
      controller,
    )
    stored.active = { turnId, controller, promise }
    void promise.finally(() => {
      if (stored.active?.turnId === turnId) delete stored.active
    })
    return { turn: structuredClone(turn), deduplicated: false }
  }

  async #executeTurn(
    principal: DomeyeAuthenticatedPrincipal,
    stored: StoredConversation,
    turnId: string,
    question: string,
    controller: AbortController,
  ): Promise<void> {
    try {
      const result = await this.#options.runtime.run({
        reference: stored.descriptor.binding.event_reference,
        publication_id: stored.descriptor.binding.publication_id,
        revision: stored.descriptor.binding.revision,
        question,
        principal: runtimePrincipal(
          principal,
          stored.authorizationDerivation,
        ),
        identity_receipt: stored.identityReceipt,
        signal: controller.signal,
      })
      if (
        controller.signal.aborted
        || !this.#isTurnExecuting(stored, turnId)
      ) {
        if (controller.signal.aborted) {
          this.#markTurnCancelled(stored, turnId)
        }
        return
      }
      const answerSuccess = hasSuccessfulDomeyePublicFinalAnswer(
        result,
        this.#options.candidate,
        stored.identityReceipt,
        stored.ownerId,
      )
      if (answerSuccess) {
        const answer = publicSuccessfulAnswer(
          result,
          stored.authorizationDerivation,
        )
        this.#replaceTurn(stored, turnId, (turn) => ({
          turn_id: turn.turn_id,
          turn_number: turn.turn_number,
          question: turn.question,
          state: 'completed',
          answer_success: true,
          workflow_completed: true,
          answer,
          created_at: turn.created_at,
          completed_at: this.#now().toISOString(),
        }))
      } else {
        const answer = publicNonSuccessfulAnswer(
          result,
          stored.authorizationDerivation,
        )
        const state = result.outcome === 'clarification_required'
          ? 'clarification_required' as const
          : 'stopped' as const
        this.#replaceTurn(stored, turnId, (turn) => ({
          turn_id: turn.turn_id,
          turn_number: turn.turn_number,
          question: turn.question,
          state,
          answer_success: false,
          workflow_completed: false,
          answer,
          created_at: turn.created_at,
          completed_at: this.#now().toISOString(),
        }))
      }
    } catch (error) {
      const cancelled = controller.signal.aborted
      if (cancelled) {
        this.#markTurnCancelled(stored, turnId)
        return
      }
      if (!this.#isTurnExecuting(stored, turnId)) return
      if (error instanceof DomeyeFirstSliceRunError) {
        stored.failureEvidence.set(turnId, immutableClone(error.evidence))
      }
      this.#replaceTurn(stored, turnId, (turn) => ({
        turn_id: turn.turn_id,
        turn_number: turn.turn_number,
        question: turn.question,
        state: 'failed',
        answer_success: false,
        workflow_completed: false,
        error: {
          code: safeErrorCode(error),
          message: '首个纵向切片执行失败，未发布答案',
          retryable: true,
        },
        created_at: turn.created_at,
        completed_at: this.#now().toISOString(),
      }))
    }
  }

  #replaceTurn(
    stored: StoredConversation,
    turnId: string,
    update: (turn: DomeyeConversationTurn) => DomeyeConversationTurn,
  ): void {
    stored.descriptor = {
      ...stored.descriptor,
      turns: stored.descriptor.turns.map((turn) =>
        turn.turn_id === turnId ? update(turn) : turn,
      ),
    }
  }

  #isTurnExecuting(stored: StoredConversation, turnId: string): boolean {
    return stored.descriptor.turns.some(
      (turn) => turn.turn_id === turnId && turn.state === 'executing',
    )
  }

  #markTurnCancelled(stored: StoredConversation, turnId: string): void {
    this.#replaceTurn(stored, turnId, (turn) => {
      if (turn.state !== 'executing') return turn
      return {
        turn_id: turn.turn_id,
        turn_number: turn.turn_number,
        question: turn.question,
        state: 'cancelled',
        answer_success: false,
        workflow_completed: false,
        error: {
          code: 'cancelled',
          message: '本轮已取消，未发布答案',
          retryable: false,
        },
        created_at: turn.created_at,
        completed_at: this.#now().toISOString(),
      }
    })
  }

  async cancelTurn(
    principal: DomeyeAuthenticatedPrincipal,
    conversationId: string,
    turnId: string,
  ): Promise<{ turn_id: string, state: 'cancel_requested' | 'not_active' }> {
    const stored = this.#conversations.get(conversationId)
    if (!stored) throw new DomeyeConversationError(
      'conversation_not_found',
      '会话不存在',
    )
    this.#assertOwner(principal, stored)
    if (
      stored.active?.turnId !== turnId
      || !this.#isTurnExecuting(stored, turnId)
    ) {
      return { turn_id: turnId, state: 'not_active' }
    }
    stored.active.controller.abort(new Error('user_cancelled'))
    this.#markTurnCancelled(stored, turnId)
    return { turn_id: turnId, state: 'cancel_requested' }
  }

  async waitForTurn(conversationId: string, turnId: string): Promise<void> {
    const active = this.#conversations.get(conversationId)?.active
    if (active?.turnId === turnId) await active.promise
  }
}
