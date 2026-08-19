import { canonicalJsonSha256 } from '../shared/deterministic-json.js'
import { createHash } from 'node:crypto'

import type {
  DomeyeAnswerContext,
  DomeyeDataIdentity,
  DomeyeGoalState,
  DomeyeSemanticGoal,
  DomeyeTypedFinding,
} from './contracts.js'
import {
  DomeyeCapabilityGateway,
  type CountryOutageSeriesReadModel,
} from './capability-execution.js'
import {
  buildCountryOutageAnswerContext,
  buildCountryOutageSeriesExtremaFinding,
  composeCountryOutageAnswer,
  type DomeyeAcceptedAnswer,
  type DomeyeAnswerRenderer,
  type DomeyeFallbackAnswer,
} from './finding-answer.js'
import {
  PiAnswerRenderer,
} from './pi-answer-renderer.js'
import {
  PiInteractiveAgentLoop,
  DomeyePiInteractiveAgentLoopError,
  type DomeyePiModelBinding,
  type DomeyePiModelIdentity,
  type PiInteractiveAgentLoopFailureEvidence,
  type PiInteractiveAgentLoopResult,
} from './pi-interactive-agent-loop.js'
import {
  DomeyeTurnProviderAccounting,
  type DomeyeProviderUsageAudit,
} from './pi-runtime-boundary.js'
import {
  DomeyeTrustKernel,
  type DomeyePolicySnapshotView,
  type DomeyePrincipalView,
  type DomeyeRegistrySnapshotView,
  type DomeyeRevocationView,
} from './trust-kernel.js'
import type {
  DomeyeDataIdentityVerifier,
  DomeyeVerifiedIdentityReceipt,
} from './country-outage-read-model.js'

export const DOMEYE_FIRST_SLICE_QUESTION =
  '在这次冻结 publication 的观测窗口内，RRC25 看到的固定前缀可见 IPv4 地址量最低是多少，首次在什么观测时刻出现？首值、末值、最大值和极差分别是多少？' as const

export interface DomeyeFirstSliceCandidateBinding {
  readonly candidate_id: string
  readonly contract_version: 'domeye.first-vertical-slice/v1.0'
  readonly contract_digest: string
  readonly data_identity: DomeyeDataIdentity
  readonly series_response_sha256: string
  readonly model_identity: DomeyePiModelIdentity
  readonly budget_policy: {
    readonly model_api_attempt_limit: 10
    readonly approved_action_limit: 2
    readonly cost_policy: 'audit_only'
    readonly monetary_limit_usd: null
  }
  readonly policy: DomeyePolicySnapshotView
  readonly registry: DomeyeRegistrySnapshotView
}

export interface DomeyeFirstSliceRunRequest {
  readonly reference: string
  readonly publication_id: string
  readonly revision: number
  readonly question: string
  readonly principal: DomeyePrincipalView
  readonly identity_receipt?: DomeyeVerifiedIdentityReceipt
  readonly signal?: AbortSignal
}

interface ResultCommon {
  readonly schema_version: 'domeye_first_vertical_slice_run_v1'
  readonly candidate_id: string
  readonly identity_receipt: DomeyeVerifiedIdentityReceipt
  readonly semantic_goal: DomeyeSemanticGoal
  readonly goal_state: DomeyeGoalState
  readonly loop: PiInteractiveAgentLoopResult
  readonly usage: DomeyeProviderUsageAudit
}

export type DomeyeFirstSliceRunResult =
  | Readonly<ResultCommon & {
      outcome: 'completed'
      finding: DomeyeTypedFinding
      answer_context: DomeyeAnswerContext
      answer: DomeyeAcceptedAnswer
    }>
  | Readonly<ResultCommon & {
      outcome: 'clarification_required' | 'stopped'
      finding: null
      answer_context: null
      answer: null
    }>

interface FailureEvidenceCommon {
  schema_version: 'domeye_first_vertical_slice_failure_evidence_v1'
  readonly candidate_id: string
  readonly identity_receipt: DomeyeVerifiedIdentityReceipt
  readonly semantic_goal: DomeyeSemanticGoal
  readonly goal_state: DomeyeGoalState
  readonly usage: DomeyeProviderUsageAudit
}

export type DomeyeFirstSliceRunFailureEvidence =
  | Readonly<FailureEvidenceCommon & {
      failure_stage: 'loop'
      loop_failure: PiInteractiveAgentLoopFailureEvidence
      loop: null
      finding: null
      answer_context: null
      answer: null
    }>
  | Readonly<FailureEvidenceCommon & {
      failure_stage: 'decision'
      loop_failure: null
      loop: PiInteractiveAgentLoopResult
      finding: null
      answer_context: null
      answer: null
    }>
  | Readonly<FailureEvidenceCommon & {
      failure_stage: 'answer'
      loop_failure: null
      loop: PiInteractiveAgentLoopResult
      finding: DomeyeTypedFinding
      answer_context: DomeyeAnswerContext
      answer: DomeyeFallbackAnswer
    }>

export class DomeyeFirstSliceRunError extends Error {
  constructor(
    readonly code: string,
    readonly evidence: DomeyeFirstSliceRunFailureEvidence,
  ) {
    super(code)
    this.name = 'DomeyeFirstSliceRunError'
  }
}
export interface DomeyeFirstSliceRuntimeOptions {
  readonly candidate: DomeyeFirstSliceCandidateBinding
  readonly model_binding: DomeyePiModelBinding
  readonly identity_verifier: DomeyeDataIdentityVerifier
  readonly series_read_model: CountryOutageSeriesReadModel
  readonly revocation: () => DomeyeRevocationView
  readonly cognition_session_factory?: ConstructorParameters<
    typeof PiInteractiveAgentLoop
  >[0]['session_factory']
  readonly renderer_factory?: (
    accounting: DomeyeTurnProviderAccounting,
  ) => DomeyeAnswerRenderer
  readonly renderer_session_factory?: ConstructorParameters<
    typeof PiAnswerRenderer
  >[0]['session_factory']
  readonly runtime_cwd?: string
  readonly now?: () => Date
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

function deepFreeze<T>(value: T): Readonly<T> {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value as Record<string, unknown>)) {
      deepFreeze(child)
    }
    Object.freeze(value)
  }
  return value
}

function stoppedGoalState(
  state: DomeyeGoalState,
  updatedAtUtc: string,
  findingIds: readonly string[] = state.finding_ids,
): DomeyeGoalState {
  return deepFreeze({
    ...state,
    state_revision: state.state_revision + 1,
    status: 'stopped',
    finding_ids: [...findingIds],
    updated_at_utc: updatedAtUtc,
  }) as DomeyeGoalState
}

function hasRejectedDecision(loop: PiInteractiveAgentLoopResult): boolean {
  return loop.decision_protocol_rejections.length > 0
    || loop.admission_receipts.some((receipt) =>
      receipt.decision !== 'admitted'
    )
    || loop.action_receipts.some((receipt) => receipt.status !== 'succeeded')
    || loop.observations.some((observation) =>
      observation.status !== 'succeeded'
    )
}

export class DomeyeFirstSliceRuntime {
  readonly #options: DomeyeFirstSliceRuntimeOptions
  readonly #now: () => Date

  constructor(options: DomeyeFirstSliceRuntimeOptions) {
    if (
      !options.candidate.candidate_id.trim()
      || options.candidate.contract_version
        !== 'domeye.first-vertical-slice/v1.0'
      || !/^sha256:[a-f0-9]{64}$/.test(options.candidate.contract_digest)
      || !/^sha256:[a-f0-9]{64}$/.test(
        options.candidate.series_response_sha256,
      )
      || canonicalJsonSha256(options.candidate.model_identity)
        !== canonicalJsonSha256(options.model_binding.identity)
      || options.candidate.budget_policy.model_api_attempt_limit !== 10
      || options.candidate.budget_policy.approved_action_limit !== 2
      || options.candidate.budget_policy.cost_policy !== 'audit_only'
      || options.candidate.budget_policy.monetary_limit_usd !== null
    ) throw new Error('first_slice_candidate_binding_invalid')
    this.#options = options
    this.#now = options.now ?? (() => new Date())
  }

  async run(
    request: DomeyeFirstSliceRunRequest,
  ): Promise<DomeyeFirstSliceRunResult> {
    request.signal?.throwIfAborted()
    if (request.question.trim() !== DOMEYE_FIRST_SLICE_QUESTION) {
      throw new Error('goal_outside_first_slice_contract')
    }
    const candidate = this.#options.candidate
    const identityReceipt = request.identity_receipt
      ?? await this.#options.identity_verifier.verify({
        reference: request.reference,
        publication_id: request.publication_id,
        revision: request.revision,
        candidate_id: candidate.candidate_id,
      }, request.signal)
    const referenceSha256 = createHash('sha256')
      .update(request.reference, 'utf8')
      .digest('hex')
    if (
      identityReceipt.candidate_id !== candidate.candidate_id
      || !identityReceipt.immutable
      || identityReceipt.reference_sha256 !== referenceSha256
      || identityReceipt.data_identity.publication_id !== request.publication_id
      || identityReceipt.data_identity.revision !== request.revision
      || !sameIdentity(identityReceipt.data_identity, candidate.data_identity)
    ) throw new Error('verified_identity_outside_candidate')

    const createdAt = this.#now().toISOString()
    const semanticGoal: DomeyeSemanticGoal = deepFreeze({
      schema_version: 'domeye_agent_semantic_goal_v1',
      goal_id: `goal-sha256:${canonicalJsonSha256({
        candidate_id: candidate.candidate_id,
        question: request.question,
        data_identity: identityReceipt.data_identity,
      })}`,
      requested_text: request.question,
      objective: 'find_fixed_visible_ipv4_series_extrema',
      metric: 'fixed_visible_ipv4_address_count',
      data_identity: identityReceipt.data_identity,
      created_at_utc: createdAt,
    })
    const initialState: DomeyeGoalState = deepFreeze({
      schema_version: 'domeye_agent_goal_state_v1',
      goal_id: semanticGoal.goal_id,
      state_revision: 1,
      status: 'active',
      completed_capability_ids: [],
      artifact_ids: [],
      finding_ids: [],
      last_observation_id: null,
      updated_at_utc: createdAt,
    })
    const accounting = new DomeyeTurnProviderAccounting()
    const loop = new PiInteractiveAgentLoop({
      model_binding: this.#options.model_binding,
      candidate_id: candidate.candidate_id,
      principal: request.principal,
      policy: candidate.policy,
      registry: candidate.registry,
      revocation: this.#options.revocation,
      trust_kernel: new DomeyeTrustKernel(),
      capability_gateway: new DomeyeCapabilityGateway({
        series_read_model: this.#options.series_read_model,
        expected_series_response_sha256: candidate.series_response_sha256,
        now: this.#now,
      }),
      ...(this.#options.cognition_session_factory
        ? { session_factory: this.#options.cognition_session_factory }
        : {}),
      ...(this.#options.runtime_cwd
        ? { runtime_cwd: this.#options.runtime_cwd }
        : {}),
      now: this.#now,
    })
    let loopResult: PiInteractiveAgentLoopResult
    try {
      loopResult = await loop.run(
        semanticGoal,
        initialState,
        request.signal,
        accounting,
      )
    } catch (caught) {
      if (!(caught instanceof DomeyePiInteractiveAgentLoopError)) throw caught
      throw new DomeyeFirstSliceRunError(
        caught.code,
        deepFreeze({
          schema_version: 'domeye_first_vertical_slice_failure_evidence_v1',
          candidate_id: candidate.candidate_id,
          identity_receipt: identityReceipt,
          semantic_goal: semanticGoal,
          goal_state: caught.evidence.goal_state,
          failure_stage: 'loop',
          loop_failure: caught.evidence,
          loop: null,
          finding: null,
          answer_context: null,
          answer: null,
          usage: caught.evidence.usage,
        }),
      )
    }
    if (hasRejectedDecision(loopResult)) {
      const failureGoalState = stoppedGoalState(
        loopResult.goal_state,
        this.#now().toISOString(),
      )
      throw new DomeyeFirstSliceRunError(
        'decision_rejected',
        deepFreeze({
          schema_version: 'domeye_first_vertical_slice_failure_evidence_v1',
          candidate_id: candidate.candidate_id,
          identity_receipt: identityReceipt,
          semantic_goal: semanticGoal,
          goal_state: failureGoalState,
          failure_stage: 'decision',
          loop_failure: null,
          loop: loopResult,
          finding: null,
          answer_context: null,
          answer: null,
          usage: accounting.audit(),
        }),
      )
    }
    if (loopResult.disposition.disposition !== 'goal_satisfied') {
      return deepFreeze({
        schema_version: 'domeye_first_vertical_slice_run_v1',
        outcome: loopResult.disposition.disposition === 'clarification_required'
          ? 'clarification_required'
          : 'stopped',
        candidate_id: candidate.candidate_id,
        identity_receipt: identityReceipt,
        semantic_goal: semanticGoal,
        goal_state: loopResult.goal_state,
        loop: loopResult,
        finding: null,
        answer_context: null,
        answer: null,
        usage: accounting.audit(),
      })
    }

    const seriesArtifact = loopResult.artifacts.find(
      (artifact) => artifact.artifact_kind === 'metric_series',
    )
    const extremaArtifact = loopResult.artifacts.find(
      (artifact) => artifact.artifact_kind === 'series_extrema',
    )
    const seriesReceipt = loopResult.action_receipts.find(
      (receipt) => receipt.capability_id === 'CAP-006',
    )
    const extremaReceipt = loopResult.action_receipts.find(
      (receipt) => receipt.capability_id === 'CAP-016',
    )
    if (
      !seriesArtifact
      || seriesArtifact.artifact_kind !== 'metric_series'
      || seriesArtifact.payload.source_response_sha256
        !== candidate.series_response_sha256
      || !extremaArtifact
      || !seriesReceipt
      || !extremaReceipt
    ) throw new Error('first_slice_evidence_chain_incomplete')

    const finding = buildCountryOutageSeriesExtremaFinding({
      series_artifact: seriesArtifact,
      series_receipt: seriesReceipt,
      extrema_artifact: extremaArtifact,
      extrema_receipt: extremaReceipt,
    })
    const answerContext = buildCountryOutageAnswerContext(
      finding,
      candidate.contract_digest,
    )
    const renderer = this.#options.renderer_factory?.(accounting)
      ?? new PiAnswerRenderer({
        model_binding: this.#options.model_binding,
        accounting,
        ...(this.#options.renderer_session_factory
          ? { session_factory: this.#options.renderer_session_factory }
          : {}),
        ...(this.#options.runtime_cwd
          ? { runtime_cwd: this.#options.runtime_cwd }
          : {}),
      })
    const answer = await composeCountryOutageAnswer(answerContext, renderer)
    if (answer.source === 'deterministic_fallback') {
      const failureGoalState = stoppedGoalState(
        loopResult.goal_state,
        this.#now().toISOString(),
        [finding.finding_id],
      )
      throw new DomeyeFirstSliceRunError(
        'answer_not_accepted',
        deepFreeze({
          schema_version: 'domeye_first_vertical_slice_failure_evidence_v1',
          candidate_id: candidate.candidate_id,
          identity_receipt: identityReceipt,
          semantic_goal: semanticGoal,
          goal_state: failureGoalState,
          failure_stage: 'answer',
          loop_failure: null,
          loop: loopResult,
          finding,
          answer_context: answerContext,
          answer,
          usage: accounting.audit(),
        }),
      )
    }
    const finalGoalState: DomeyeGoalState = deepFreeze({
      ...loopResult.goal_state,
      state_revision: loopResult.goal_state.state_revision + 1,
      status: 'satisfied',
      finding_ids: [finding.finding_id],
      updated_at_utc: this.#now().toISOString(),
    })
    return deepFreeze({
      schema_version: 'domeye_first_vertical_slice_run_v1',
      outcome: 'completed',
      candidate_id: candidate.candidate_id,
      identity_receipt: identityReceipt,
      semantic_goal: semanticGoal,
      goal_state: finalGoalState,
      loop: loopResult,
      finding,
      answer_context: answerContext,
      answer,
      usage: accounting.audit(),
    })
  }
}
