import { resolve } from 'node:path'

import {
  createAgentSession,
  SessionManager,
  SettingsManager,
  type CreateAgentSessionOptions,
  type SessionStats,
  type ToolDefinition,
} from '@earendil-works/pi-coding-agent'
import { Check } from 'typebox/value'

import { canonicalJsonSha256 } from '../shared/deterministic-json.js'

import {
  DomeyeCapabilityObservationSchema,
  DomeyeCapabilityProposalSchema,
  DomeyeCapabilityProposalCaptureSchema,
  DomeyeGoalDispositionSchema,
  type DomeyeActionReceipt,
  type DomeyeArtifactEnvelope,
  type DomeyeCapabilityObservation,
  type DomeyeCapabilityProposal,
  type DomeyeGoalDisposition,
  type DomeyeGoalState,
  type DomeyeSemanticGoal,
} from './contracts.js'
import {
  DomeyeCapabilityGateway,
} from './capability-execution.js'
import {
  DomeyeTrustKernel,
  type DomeyeAdmissionReceipt,
  type DomeyePolicySnapshotView,
  type DomeyePrincipalView,
  type DomeyeRegistrySnapshotView,
  type DomeyeRevocationView,
} from './trust-kernel.js'
import {
  DomeyeTurnProviderAccounting,
  installDomeyeProviderAttemptBoundary,
  type DomeyePiSessionFactory,
  type DomeyePiSessionHandle,
  type DomeyeProviderUsageAudit,
} from './pi-runtime-boundary.js'
import {
  createStaticCountryOutageEmptyResourceBundle,
} from '../pi/static-resource-loader.js'

export const DOMEYE_CAPABILITY_PROPOSAL_TOOL =
  'propose_domeye_capability' as const
export const DOMEYE_GOAL_DISPOSITION_TOOL =
  'submit_domeye_goal_disposition' as const

type DomeyeDecisionToolName =
  | typeof DOMEYE_CAPABILITY_PROPOSAL_TOOL
  | typeof DOMEYE_GOAL_DISPOSITION_TOOL
type PiStreamFunction = DomeyePiSessionHandle['agent']['streamFunction']
type PiStreamOptions = Parameters<PiStreamFunction>[2]
type PiPayloadHook = NonNullable<NonNullable<PiStreamOptions>['onPayload']>

const BUILTIN_AND_FILESYSTEM_TOOLS = [
  'read',
  'bash',
  'edit',
  'write',
  'grep',
  'find',
  'ls',
] as const

const COGNITION_SYSTEM_PROMPT = `你是 Domeye 首个纵向切片的 PiInteractiveAgentLoop。
你只维护当前 Semantic Goal 和 Goal State，每次只决定一个下一步；禁止生成完整计划、DAG 或未来动作清单。
每个模型响应必须恰好调用一个决策工具：需要读取事实或计算时调用一次 propose_domeye_capability；目标满足、需要澄清或必须停止时调用一次 submit_domeye_goal_disposition。禁止同一响应调用两个工具或重复调用。
propose_domeye_capability 只提交 Proposal，不代表已准入或已执行。
首片仅允许 CAP-006 读取 fixed_visible_ipv4_address_count；看到其成功 Observation 和 artifact_ref 后，才可提出 CAP-016，并必须原样引用该 Artifact。
每次 Action 都会由 DomeyeTrustKernel 独立准入。不得声称未出现在 Observation 中的执行结果。你只负责下一步决策，不负责生成或渲染最终答案；Finding、Context 和答案渲染由后续受控组件完成。
Goal Disposition 只能通过专用工具提交；禁止在 assistant 文本、Markdown 或 JSON 文本中表达或补交决策。`

export interface DomeyePiModelIdentity {
  readonly candidate_id: string
  readonly resource_sha256: string
  readonly provider: string
  readonly model: string
  readonly model_version: string
  readonly expected_response_model: string
  readonly api: 'openai-completions'
  readonly base_url: string
  readonly maximum_output_tokens: number
  readonly thinking_level: NonNullable<CreateAgentSessionOptions['thinkingLevel']>
  readonly pi_version: '0.84.1'
}

export interface DomeyePiModelBinding {
  readonly identity: DomeyePiModelIdentity
  readonly model: NonNullable<CreateAgentSessionOptions['model']>
  readonly model_runtime: NonNullable<CreateAgentSessionOptions['modelRuntime']>
  readonly thinking_level: NonNullable<CreateAgentSessionOptions['thinkingLevel']>
}

export interface DomeyeDecisionProtocolRejection {
  readonly sequence: number
  readonly reason_code:
    | 'multiple_decisions_in_single_response'
    | 'decision_missing_or_invalid'
    | 'goal_disposition_not_yet_valid'
  readonly observed_proposal_count: number
  readonly observed_disposition_count: number
}

export type PiInteractiveAgentLoopResult = Readonly<{
  goal_state: DomeyeGoalState
  disposition: DomeyeGoalDisposition
  artifacts: readonly DomeyeArtifactEnvelope[]
  action_receipts: readonly DomeyeActionReceipt[]
  admission_receipts: readonly DomeyeAdmissionReceipt[]
  observations: readonly DomeyeCapabilityObservation[]
  decision_protocol_rejections: readonly DomeyeDecisionProtocolRejection[]
  usage: DomeyeProviderUsageAudit
}>

export type PiInteractiveAgentLoopFailureEvidence = Readonly<{
  schema_version: 'domeye_agent_loop_failure_evidence_v1'
  failure_code: string
  goal_state: DomeyeGoalState
  artifacts: readonly DomeyeArtifactEnvelope[]
  action_receipts: readonly DomeyeActionReceipt[]
  admission_receipts: readonly DomeyeAdmissionReceipt[]
  observations: readonly DomeyeCapabilityObservation[]
  decision_protocol_rejections: readonly DomeyeDecisionProtocolRejection[]
  usage: DomeyeProviderUsageAudit
}>

export class DomeyePiInteractiveAgentLoopError extends Error {
  constructor(
    readonly code: string,
    readonly evidence: PiInteractiveAgentLoopFailureEvidence,
  ) {
    super(code)
    this.name = 'DomeyePiInteractiveAgentLoopError'
  }
}

export interface PiInteractiveAgentLoopOptions {
  readonly model_binding: DomeyePiModelBinding
  readonly candidate_id: string
  readonly principal: DomeyePrincipalView
  readonly policy: DomeyePolicySnapshotView
  readonly registry: DomeyeRegistrySnapshotView
  readonly revocation: () => DomeyeRevocationView
  readonly trust_kernel: DomeyeTrustKernel
  readonly capability_gateway: DomeyeCapabilityGateway
  readonly session_factory?: DomeyePiSessionFactory
  readonly runtime_cwd?: string
  readonly timeout_ms?: number
  readonly now?: () => Date
}

function defaultSessionFactory(
  options: CreateAgentSessionOptions,
): Promise<{ session: DomeyePiSessionHandle }> {
  return createAgentSession(options) as unknown as Promise<{
    session: DomeyePiSessionHandle
  }>
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

function isPlainJsonObject(
  value: unknown,
): value is Record<string, unknown> {
  if (
    value === null
    || typeof value !== 'object'
    || Array.isArray(value)
  ) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function hasNamedProviderTool(
  payload: Record<string, unknown>,
  toolName: DomeyeDecisionToolName,
): boolean {
  return Array.isArray(payload.tools)
    && payload.tools.some((value) => {
      if (!isPlainJsonObject(value) || value.type !== 'function') return false
      const definition = value.function
      return isPlainJsonObject(definition) && definition.name === toolName
    })
}

function cognitionPayloadHook(
  existingHook: PiPayloadHook | undefined,
  requiredToolName: () => DomeyeDecisionToolName,
): PiPayloadHook {
  return async (payload, model) => {
    if (!isPlainJsonObject(payload)) {
      throw new Error('cognition_provider_payload_invalid')
    }
    const existingResult = await existingHook?.(payload, model)
    if (
      existingResult !== undefined
      && !isPlainJsonObject(existingResult)
    ) throw new Error('cognition_provider_payload_invalid')
    const source = existingResult ?? payload
    const toolName = requiredToolName()
    if (
      typeof source.model !== 'string'
      || !Array.isArray(source.messages)
      || source.stream !== true
      || !hasNamedProviderTool(source, toolName)
    ) throw new Error('cognition_provider_payload_invalid')
    return {
      ...source,
      temperature: 0,
      tool_choice: {
        type: 'function',
        function: { name: toolName },
      },
    }
  }
}

function installCognitionPayloadBoundary(
  session: DomeyePiSessionHandle,
  requiredToolName: () => DomeyeDecisionToolName,
): void {
  const original = session.agent.streamFunction
  session.agent.streamFunction = (model, context, options) => {
    if (
      model.provider !== 'deepseek'
      || model.api !== 'openai-completions'
    ) return original(model, context, options)
    return original(model, context, {
      ...(options ?? {}),
      onPayload: cognitionPayloadHook(
        options?.onPayload,
        requiredToolName,
      ),
    })
  }
}

function assistantProviderFailed(session: DomeyePiSessionHandle): boolean {
  for (let index = session.messages.length - 1; index >= 0; index -= 1) {
    const value = session.messages[index]
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue
    const record = value as Record<string, unknown>
    if (record.role !== 'assistant') continue
    return record.stopReason === 'error'
  }
  return false
}

function nextGoalState(
  state: DomeyeGoalState,
  observation: DomeyeCapabilityObservation,
  actionReceipt: DomeyeActionReceipt | undefined,
  artifact: DomeyeArtifactEnvelope | undefined,
): DomeyeGoalState {
  const completed = actionReceipt?.status === 'succeeded'
    ? [...new Set([
        ...state.completed_capability_ids,
        actionReceipt.capability_id,
      ])]
    : [...state.completed_capability_ids]
  return deepFreeze({
    ...state,
    state_revision: state.state_revision + 1,
    completed_capability_ids: completed,
    artifact_ids: artifact
      ? [...new Set([...state.artifact_ids, artifact.artifact_id])]
      : [...state.artifact_ids],
    last_observation_id: observation.observation_id,
    updated_at_utc: observation.created_at_utc,
  }) as DomeyeGoalState
}

function rejectedObservation(
  proposal: DomeyeCapabilityProposal,
  goal: DomeyeSemanticGoal,
  reasonCode: string,
  createdAt: string,
): DomeyeCapabilityObservation {
  const body = {
    schema_version: 'domeye_agent_capability_observation_v1' as const,
    action_id: null,
    capability_id: proposal.capability_id,
    status: 'rejected' as const,
    reason_code: reasonCode,
    artifact_ref: null,
    data_identity: goal.data_identity,
    safe_summary: {
      metric: 'fixed_visible_ipv4_address_count' as const,
      unit: null,
      result_state: 'unavailable' as const,
      observed_point_count: null,
      finding_input: null,
    },
    created_at_utc: createdAt,
  }
  const payload = JSON.stringify(body)
  const observation = deepFreeze({
    ...body,
    observation_id: `observation-protocol-${Buffer.from(payload).toString('base64url').slice(0, 80)}`,
  })
  if (!Check(DomeyeCapabilityObservationSchema, observation)) {
    throw new Error('capability_observation_contract_violation')
  }
  return observation
}

type DomeyeRequiredDecision =
  | Readonly<{
      tool_name: typeof DOMEYE_CAPABILITY_PROPOSAL_TOOL
      capability_proposal: DomeyeCapabilityProposal
      goal_disposition: null
    }>
  | Readonly<{
      tool_name: typeof DOMEYE_GOAL_DISPOSITION_TOOL
      capability_proposal: null
      goal_disposition: DomeyeGoalDisposition
    }>

function requiredDecisionForState(
  goal: DomeyeSemanticGoal,
  state: DomeyeGoalState,
  artifacts: readonly DomeyeArtifactEnvelope[],
): DomeyeRequiredDecision {
  if (findingInputForState(goal, state, artifacts) !== null) {
    return {
      tool_name: DOMEYE_GOAL_DISPOSITION_TOOL,
      capability_proposal: null,
      goal_disposition: {
        schema_version: 'domeye_agent_goal_disposition_v1',
        goal_id: goal.goal_id,
        goal_state_revision: state.state_revision,
        disposition: 'goal_satisfied',
        reason_code: 'finding_input_ready',
      },
    }
  }
  if (
    canonicalJsonSha256(state.completed_capability_ids)
      === canonicalJsonSha256([])
    && artifacts.length === 0
  ) {
    return {
      tool_name: DOMEYE_CAPABILITY_PROPOSAL_TOOL,
      capability_proposal: {
        schema_version: 'domeye_agent_capability_proposal_v1',
        goal_id: goal.goal_id,
        goal_state_revision: state.state_revision,
        rationale: '读取当前冻结身份下的固定可见 IPv4 地址数时序。',
        capability_id: 'CAP-006',
        input: { metric: 'fixed_visible_ipv4_address_count' },
      },
      goal_disposition: null,
    }
  }
  const seriesArtifact = artifacts[0]
  if (
    canonicalJsonSha256(state.completed_capability_ids)
      === canonicalJsonSha256(['CAP-006'])
    && seriesArtifact?.artifact_kind === 'metric_series'
    && canonicalJsonSha256(state.artifact_ids)
      === canonicalJsonSha256([seriesArtifact.artifact_id])
  ) {
    return {
      tool_name: DOMEYE_CAPABILITY_PROPOSAL_TOOL,
      capability_proposal: {
        schema_version: 'domeye_agent_capability_proposal_v1',
        goal_id: goal.goal_id,
        goal_state_revision: state.state_revision,
        rationale: '只计算当前冻结时序 Artifact 的首个并列极值。',
        capability_id: 'CAP-016',
        input: {
          metric: 'fixed_visible_ipv4_address_count',
          source_artifact_id: seriesArtifact.artifact_id,
          tie_policy: 'first_observed_occurrence',
        },
      },
      goal_disposition: null,
    }
  }
  return {
    tool_name: DOMEYE_GOAL_DISPOSITION_TOOL,
    capability_proposal: null,
    goal_disposition: {
      schema_version: 'domeye_agent_goal_disposition_v1',
      goal_id: goal.goal_id,
      goal_state_revision: state.state_revision,
      disposition: 'stopped',
      reason_code: 'required_finding_input_unavailable',
    },
  }
}

function requiredDecisionPromptFields(
  decision: DomeyeRequiredDecision,
): Record<string, unknown> {
  return {
    required_decision_tool: decision.tool_name,
    required_capability_proposal: decision.capability_proposal,
    required_goal_disposition: decision.goal_disposition,
  }
}

function initialPrompt(
  goal: DomeyeSemanticGoal,
  state: DomeyeGoalState,
  decision: DomeyeRequiredDecision,
): string {
  return JSON.stringify({
    instruction: '恰好调用 required_decision_tool，并原样提交对应 required_capability_proposal 或 required_goal_disposition；禁止文本决策、补字段或第二次工具调用。',
    ...requiredDecisionPromptFields(decision),
    semantic_goal: goal,
    goal_state: state,
  })
}

function observationPrompt(
  goal: DomeyeSemanticGoal,
  state: DomeyeGoalState,
  observation: DomeyeCapabilityObservation,
  artifacts: readonly DomeyeArtifactEnvelope[],
  decision: DomeyeRequiredDecision,
): string {
  const expectedFindingInput = findingInputForState(goal, state, artifacts)
  const evidenceReady = expectedFindingInput !== null
    && observation.status === 'succeeded'
    && observation.capability_id === 'CAP-016'
    && observation.artifact_ref !== null
    && observation.safe_summary.result_state === 'known'
    && observation.safe_summary.finding_input !== null
    && canonicalJsonSha256(observation.safe_summary.finding_input)
      === canonicalJsonSha256(expectedFindingInput)
    && canonicalJsonSha256(observation.data_identity)
      === canonicalJsonSha256(goal.data_identity)
    && state.last_observation_id === observation.observation_id
  return JSON.stringify({
    instruction: evidenceReady
      ? '你只负责决策，不负责渲染最终答案。合格的 Typed Finding 输入已就绪；goal_satisfied 只表示该输入可交给受控 Finding/Context 构建器，不表示最终答案已经生成。恰好调用 required_decision_tool 并原样提交 required_goal_disposition，禁止文本决策。'
      : '你只负责决策，不负责渲染最终答案。只根据安全 Observation 更新判断；恰好调用 required_decision_tool，并原样提交对应机器对象，禁止文本决策。',
    evidence_ready_for_finding_context: evidenceReady,
    ...requiredDecisionPromptFields(decision),
    semantic_goal: goal,
    goal_state: state,
    observation,
  })
}

function protocolPrompt(
  goal: DomeyeSemanticGoal,
  state: DomeyeGoalState,
  rejection: DomeyeDecisionProtocolRejection,
  artifacts: readonly DomeyeArtifactEnvelope[],
  decision: DomeyeRequiredDecision,
): string {
  const findingInputReady = findingInputForState(goal, state, artifacts) !== null
  return JSON.stringify({
    instruction: findingInputReady
      ? '上一 Goal Disposition 不符合当前 ready 状态，未被接受。合格的 Typed Finding 输入已就绪；请恰好调用 required_decision_tool 并原样提交 required_goal_disposition。goal_satisfied 不表示最终答案已经生成，禁止文本决策。'
      : '上一响应违反单决策工具合同，未执行任何 Action。请恰好调用 required_decision_tool 并原样提交对应机器对象。',
    evidence_ready_for_finding_context: findingInputReady,
    ...requiredDecisionPromptFields(decision),
    semantic_goal: goal,
    goal_state: state,
    protocol_rejection: rejection,
  })
}

type DomeyeReadyFindingInput = Exclude<
  DomeyeCapabilityObservation['safe_summary']['finding_input'],
  null
>

function findingInputForState(
  goal: DomeyeSemanticGoal,
  state: DomeyeGoalState,
  artifacts: readonly DomeyeArtifactEnvelope[],
): DomeyeReadyFindingInput | null {
  if (
    state.status !== 'active'
    || canonicalJsonSha256(state.completed_capability_ids)
      !== canonicalJsonSha256(['CAP-006', 'CAP-016'])
    || artifacts.length !== 2
  ) return null
  const series = artifacts[0]
  const extrema = artifacts[1]
  if (
    series?.artifact_kind !== 'metric_series'
    || extrema?.artifact_kind !== 'series_extrema'
    || extrema.payload.result_state !== 'known'
    || extrema.payload.source_artifact_id !== series.artifact_id
    || extrema.candidate_id !== series.candidate_id
    || extrema.tenant_id !== series.tenant_id
    || canonicalJsonSha256(series.data_identity)
      !== canonicalJsonSha256(goal.data_identity)
    || canonicalJsonSha256(extrema.data_identity)
      !== canonicalJsonSha256(goal.data_identity)
    || canonicalJsonSha256(state.artifact_ids)
      !== canonicalJsonSha256([series.artifact_id, extrema.artifact_id])
  ) return null
  return {
    state: 'ready',
    source_artifact_ref: series.artifact_id,
    extrema_artifact_ref: extrema.artifact_id,
    extrema_result_state: 'known',
    next_owner: 'domeye_typed_finding_builder',
  }
}

function validDispositionForState(
  disposition: DomeyeGoalDisposition,
  goal: DomeyeSemanticGoal,
  state: DomeyeGoalState,
  artifacts: readonly DomeyeArtifactEnvelope[],
): boolean {
  if (
    disposition.goal_id !== goal.goal_id
    || disposition.goal_state_revision !== state.state_revision
  ) return false
  const findingInputReady = findingInputForState(
    goal,
    state,
    artifacts,
  ) !== null
  if (!findingInputReady) return disposition.disposition !== 'goal_satisfied'
  return disposition.disposition === 'goal_satisfied'
    && disposition.reason_code === 'finding_input_ready'
}

function finalState(
  state: DomeyeGoalState,
  disposition: DomeyeGoalDisposition,
  now: string,
): DomeyeGoalState {
  const status = disposition.disposition === 'goal_satisfied'
    ? 'answer_pending' as const
    : disposition.disposition === 'clarification_required'
      ? 'clarification_required' as const
      : 'stopped' as const
  return deepFreeze({ ...state, status, updated_at_utc: now })
}

function safeStats(session: DomeyePiSessionHandle): SessionStats | undefined {
  try {
    return session.getSessionStats()
  } catch {
    return undefined
  }
}

function safeLoopFailureCode(error: unknown, cancelled: boolean): string {
  if (cancelled) return 'cancelled'
  if (
    error instanceof Error
    && /^[a-z][a-z0-9_]{0,63}$/.test(error.message)
  ) return error.message
  return 'interactive_loop_failed'
}

export class PiInteractiveAgentLoop {
  readonly #options: PiInteractiveAgentLoopOptions
  readonly #runtimeCwd: string
  readonly #timeoutMs: number
  readonly #now: () => Date
  readonly #resourceBundle = createStaticCountryOutageEmptyResourceBundle(
    COGNITION_SYSTEM_PROMPT,
  )
  #active = false

  constructor(options: PiInteractiveAgentLoopOptions) {
    if (!options.candidate_id.trim()) throw new Error('candidate_id_invalid')
    this.#options = options
    this.#runtimeCwd = resolve(options.runtime_cwd ?? process.cwd())
    this.#timeoutMs = options.timeout_ms ?? 110_000
    if (
      !Number.isSafeInteger(this.#timeoutMs)
      || this.#timeoutMs < 10_000
      || this.#timeoutMs > 180_000
    ) throw new Error('interactive_loop_timeout_invalid')
    this.#now = options.now ?? (() => new Date())
  }

  async run(
    goal: DomeyeSemanticGoal,
    initialState: DomeyeGoalState,
    signal?: AbortSignal,
    accounting = new DomeyeTurnProviderAccounting(),
  ): Promise<PiInteractiveAgentLoopResult> {
    if (this.#active) throw new Error('interactive_loop_busy')
    if (
      goal.goal_id !== initialState.goal_id
      || initialState.status !== 'active'
    ) throw new Error('goal_state_conflict')
    signal?.throwIfAborted()
    this.#active = true
    const proposals: DomeyeCapabilityProposal[] = []
    const dispositions: DomeyeGoalDisposition[] = []
    const budget = accounting.budget
    const artifacts: DomeyeArtifactEnvelope[] = []
    const actionReceipts: DomeyeActionReceipt[] = []
    const admissionReceipts: DomeyeAdmissionReceipt[] = []
    const observations: DomeyeCapabilityObservation[] = []
    const protocolRejections: DomeyeDecisionProtocolRejection[] = []
    let state = deepFreeze(structuredClone(initialState)) as DomeyeGoalState
    let session: DomeyePiSessionHandle | undefined
    let stats: SessionStats | undefined
    let statsRecorded = false
    let proposalSequence = 0
    let requiredDecisionTool: DomeyeDecisionToolName =
      DOMEYE_CAPABILITY_PROPOSAL_TOOL
    try {
      const proposalTool: ToolDefinition<
        typeof DomeyeCapabilityProposalCaptureSchema
      > = {
        name: DOMEYE_CAPABILITY_PROPOSAL_TOOL,
        label: '提交下一项 Domeye Capability Proposal',
        description: '只提交当前下一项 CAP-006 或 CAP-016 Proposal；不会准入或执行。每个响应最多调用一次。',
        parameters: DomeyeCapabilityProposalCaptureSchema,
        executionMode: 'sequential',
        async execute(_toolCallId, proposal) {
          if (!Check(DomeyeCapabilityProposalSchema, proposal)) {
            throw new Error('capability_proposal_invalid')
          }
          proposals.push(structuredClone(proposal) as DomeyeCapabilityProposal)
          return {
            content: [{
              type: 'text',
              text: JSON.stringify({
                status: 'proposal_captured_not_executed',
                instruction: '不要再次调用工具；等待准入和真实 Observation。',
              }),
            }],
            details: { captured: true },
            terminate: true,
          }
        },
      }
      const dispositionTool: ToolDefinition<
        typeof DomeyeGoalDispositionSchema
      > = {
        name: DOMEYE_GOAL_DISPOSITION_TOOL,
        label: '提交 Domeye Goal Disposition',
        description: '目标满足、需要澄清或必须停止时提交最终 Goal Disposition；每个响应最多调用一次。',
        parameters: DomeyeGoalDispositionSchema,
        executionMode: 'sequential',
        async execute(_toolCallId, disposition) {
          if (!Check(DomeyeGoalDispositionSchema, disposition)) {
            throw new Error('goal_disposition_invalid')
          }
          dispositions.push(
            structuredClone(disposition) as DomeyeGoalDisposition,
          )
          return {
            content: [{
              type: 'text',
              text: JSON.stringify({
                status: 'goal_disposition_captured',
                instruction: '不要再次调用工具；等待机器合同校验。',
              }),
            }],
            details: { captured: true },
            terminate: true,
          }
        },
      }
      const settingsManager = SettingsManager.inMemory({
        compaction: { enabled: false },
        retry: { enabled: false, provider: { maxRetries: 0 } },
        images: { blockImages: true },
        enableSkillCommands: false,
      })
      const created = await (
        this.#options.session_factory ?? defaultSessionFactory
      )({
        cwd: this.#runtimeCwd,
        agentDir: this.#runtimeCwd,
        model: this.#options.model_binding.model,
        modelRuntime: this.#options.model_binding.model_runtime,
        thinkingLevel: this.#options.model_binding.thinking_level,
        noTools: 'builtin',
        tools: [
          DOMEYE_CAPABILITY_PROPOSAL_TOOL,
          DOMEYE_GOAL_DISPOSITION_TOOL,
        ],
        excludeTools: [...BUILTIN_AND_FILESYSTEM_TOOLS],
        customTools: [proposalTool, dispositionTool],
        resourceLoader: this.#resourceBundle.loader,
        sessionManager: SessionManager.inMemory(this.#runtimeCwd),
        settingsManager,
      })
      session = created.session
      installCognitionPayloadBoundary(
        session,
        () => requiredDecisionTool,
      )
      installDomeyeProviderAttemptBoundary(
        session,
        budget,
        'cognition',
        this.#options.model_binding.identity,
      )
      let requiredDecision = requiredDecisionForState(goal, state, artifacts)
      requiredDecisionTool = requiredDecision.tool_name
      let nextPrompt = initialPrompt(goal, state, requiredDecision)
      for (let cycle = 1; cycle <= 10; cycle += 1) {
        signal?.throwIfAborted()
        const proposalStart = proposals.length
        const dispositionStart = dispositions.length
        const providerAttemptsBeforeCycle = budget.used
        let timeoutHandle: ReturnType<typeof setTimeout> | undefined
        let timedOut = false
        const onAbort = (): void => { void session?.abort().catch(() => undefined) }
        signal?.addEventListener('abort', onAbort, { once: true })
        try {
          await Promise.race([
            session.prompt(nextPrompt, {
              expandPromptTemplates: false,
              source: 'rpc',
            }),
            new Promise<never>((_resolve, reject) => {
              timeoutHandle = setTimeout(() => {
                timedOut = true
                void session?.abort().catch(() => undefined)
                reject(new Error('interactive_loop_timeout'))
              }, this.#timeoutMs)
              timeoutHandle.unref()
            }),
          ])
        } finally {
          if (timeoutHandle) clearTimeout(timeoutHandle)
          signal?.removeEventListener('abort', onAbort)
        }
        if (timedOut) throw new Error('interactive_loop_timeout')
        signal?.throwIfAborted()
        if (budget.used - providerAttemptsBeforeCycle !== 1) {
          throw new Error('decision_cycle_provider_attempt_count_invalid')
        }
        if (assistantProviderFailed(session)) {
          throw new Error('cognition_provider_failed')
        }

        const capturedProposals = proposals.slice(proposalStart)
        const capturedDispositions = dispositions.slice(dispositionStart)
        const capturedDecisionCount =
          capturedProposals.length + capturedDispositions.length
        if (capturedDecisionCount === 1 && capturedDispositions.length === 1) {
          const disposition = capturedDispositions[0]!
          if (validDispositionForState(disposition, goal, state, artifacts)) {
            const completedState = finalState(
              state,
              disposition,
              this.#now().toISOString(),
            )
            stats = safeStats(session)
            accounting.recordSessionStats(stats)
            statsRecorded = true
            return deepFreeze({
              goal_state: completedState,
              disposition,
              artifacts,
              action_receipts: actionReceipts,
              admission_receipts: admissionReceipts,
              observations,
              decision_protocol_rejections: protocolRejections,
              usage: accounting.audit(),
            })
          }
          const rejection: DomeyeDecisionProtocolRejection = deepFreeze({
            sequence: cycle,
            reason_code: 'goal_disposition_not_yet_valid',
            observed_proposal_count: 0,
            observed_disposition_count: 1,
          })
          protocolRejections.push(rejection)
          requiredDecision = requiredDecisionForState(goal, state, artifacts)
          requiredDecisionTool = requiredDecision.tool_name
          nextPrompt = protocolPrompt(
            goal,
            state,
            rejection,
            artifacts,
            requiredDecision,
          )
          continue
        }
        if (capturedDecisionCount !== 1) {
          const rejection: DomeyeDecisionProtocolRejection = deepFreeze({
            sequence: cycle,
            reason_code: capturedDecisionCount > 1
              ? 'multiple_decisions_in_single_response'
              : 'decision_missing_or_invalid',
            observed_proposal_count: capturedProposals.length,
            observed_disposition_count: capturedDispositions.length,
          })
          protocolRejections.push(rejection)
          requiredDecision = requiredDecisionForState(goal, state, artifacts)
          requiredDecisionTool = requiredDecision.tool_name
          nextPrompt = protocolPrompt(
            goal,
            state,
            rejection,
            artifacts,
            requiredDecision,
          )
          continue
        }

        const proposal = capturedProposals[0]!
        proposalSequence += 1
        const decision = this.#options.trust_kernel.admit({
          proposal,
          proposal_sequence: proposalSequence,
          goal_state: state,
          principal: this.#options.principal,
          tenant_id: 'domeye',
          data_identity: goal.data_identity,
          candidate_id: this.#options.candidate_id,
          policy: this.#options.policy,
          registry: this.#options.registry,
          revocation: this.#options.revocation(),
          model_api_attempts_used: budget.used,
          action_history: actionReceipts,
          artifacts,
          admitted_at_utc: this.#now().toISOString(),
        })
        admissionReceipts.push(decision.receipt)
        if (decision.status === 'rejected') {
          const observation = rejectedObservation(
            proposal,
            goal,
            decision.receipt.reason_code ?? 'admission_rejected',
            this.#now().toISOString(),
          )
          observations.push(observation)
          state = nextGoalState(
            state,
            observation,
            undefined,
            undefined,
          )
          requiredDecision = requiredDecisionForState(goal, state, artifacts)
          requiredDecisionTool = requiredDecision.tool_name
          nextPrompt = observationPrompt(
            goal,
            state,
            observation,
            artifacts,
            requiredDecision,
          )
          continue
        }

        const execution = await this.#options.capability_gateway.execute(
          decision,
          artifacts,
          signal,
        )
        actionReceipts.push(execution.receipt)
        observations.push(execution.observation)
        if (execution.artifact) artifacts.push(execution.artifact)
        state = nextGoalState(
          state,
          execution.observation,
          execution.receipt,
          execution.artifact ?? undefined,
        )
        requiredDecision = requiredDecisionForState(goal, state, artifacts)
        requiredDecisionTool = requiredDecision.tool_name
        nextPrompt = observationPrompt(
          goal,
          state,
          execution.observation,
          artifacts,
          requiredDecision,
        )
      }
      throw new Error('interactive_loop_decision_limit_exceeded')
    } catch (caught) {
      const failureCode = safeLoopFailureCode(caught, signal?.aborted === true)
      budget.failStarted(new Error(failureCode))
      stats ??= session ? safeStats(session) : undefined
      if (stats && !statsRecorded) {
        accounting.recordSessionStats(stats)
        statsRecorded = true
      }
      throw new DomeyePiInteractiveAgentLoopError(
        failureCode,
        deepFreeze({
          schema_version: 'domeye_agent_loop_failure_evidence_v1',
          failure_code: failureCode,
          goal_state: state,
          artifacts,
          action_receipts: actionReceipts,
          admission_receipts: admissionReceipts,
          observations,
          decision_protocol_rejections: protocolRejections,
          usage: accounting.audit(),
        }),
      )
    } finally {
      stats ??= session ? safeStats(session) : undefined
      if (stats && !statsRecorded) {
        accounting.recordSessionStats(stats)
        statsRecorded = true
      }
      session?.dispose()
      this.#active = false
    }
  }
}
