import type { SessionStats } from '@earendil-works/pi-coding-agent'

import { FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS } from '../formal-runtime-limits.js'
import { COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION } from '../report/model-language-plan.js'
import type { COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION } from './country-outage-skill-bundle.js'
import type { PiModelRunSelection } from './formal-model-runtime.js'

// 正式叙述层只接收宿主冻结的语言计划，不允许模型再调用事实工具。
const ALLOWED_TOOLS = new Set<string>()

export const FORMAL_PI_RUN_REJECTION_CODES = Object.freeze([
  'configured_model_mismatch',
  'dependency_risk_exception_inactive',
  'model_context_window_too_small',
  'model_output_limit_invalid',
  'resource_bundle_mismatch',
  'provider_call_failed',
  'provider_request_limit_exceeded',
  'provider_context_limit_exceeded',
  'model_attempt_timeout',
  'maximum_model_attempts_exceeded',
  'context_input_limit_exceeded',
  'evidence_record_limit_exceeded',
  'tool_execution_limit_exceeded',
  'tool_result_limit_exceeded',
  'assistant_message_missing',
  'assistant_metadata_invalid',
  'provider_mismatch',
  'model_mismatch',
  'response_model_missing',
  'response_model_mismatch',
  'stop_reason_invalid',
  'tool_not_allowed',
  'required_tool_missing',
  'session_stats_invalid',
  'report_json_object_missing',
  'report_json_syntax_invalid',
  'report_draft_schema_invalid',
  'report_payload_invalid',
  'audit_sink_failed',
  'aborted',
] as const)

export type FormalPiRunRejectionCode =
  (typeof FORMAL_PI_RUN_REJECTION_CODES)[number]

const FORMAL_PI_RUN_REJECTION_CODE_SET = new Set<string>(
  FORMAL_PI_RUN_REJECTION_CODES,
)

export function isFormalPiRunRejectionCode(
  value: unknown,
): value is FormalPiRunRejectionCode {
  return (
    typeof value === 'string' &&
    FORMAL_PI_RUN_REJECTION_CODE_SET.has(value)
  )
}

const SAFE_ERROR_MESSAGES: Record<FormalPiRunRejectionCode, string> = {
  configured_model_mismatch: '正式 Pi 模型对象与已认证组合不一致',
  dependency_risk_exception_inactive:
    '正式 Pi 依赖风险例外未生效或已经到期',
  model_context_window_too_small:
    '正式 Pi 模型上下文窗口低于冻结下限',
  model_output_limit_invalid:
    '正式 Pi 模型输出令牌上限未按冻结边界执行',
  resource_bundle_mismatch: '正式 Pi Skill 资源包与启动时固定摘要不一致',
  provider_call_failed: '正式 Pi 模型调用失败',
  provider_request_limit_exceeded:
    '正式 Pi 上游供应商请求次数超过冻结上限',
  provider_context_limit_exceeded:
    '正式 Pi 上游供应商请求上下文超过冻结字节上限',
  model_attempt_timeout: '正式 Pi 单次模型调用超过冻结时限',
  maximum_model_attempts_exceeded:
    '正式 Pi 模型调用次数超过冻结上限',
  context_input_limit_exceeded:
    '正式 Pi 模型实际输入令牌数超过冻结上限',
  evidence_record_limit_exceeded:
    '正式报告证据记录数超过冻结上限',
  tool_execution_limit_exceeded:
    '正式 Pi 叙述层执行了已禁用的工具',
  tool_result_limit_exceeded:
    '正式 Pi 只读工具结果超过冻结字节上限',
  assistant_message_missing: '正式 Pi 模型没有返回最终消息',
  assistant_metadata_invalid: '正式 Pi 模型返回的消息元数据无效',
  provider_mismatch: '正式 Pi 模型实际供应方与已认证组合不一致',
  model_mismatch: '正式 Pi 模型实际模型与已认证组合不一致',
  response_model_missing: '正式 Pi 模型没有返回可核验的响应模型版本',
  response_model_mismatch: '正式 Pi 模型响应版本与已认证组合不一致',
  stop_reason_invalid: '正式 Pi 模型没有正常完成报告生成',
  tool_not_allowed: '正式 Pi 模型尝试使用未授权工具',
  required_tool_missing: '正式 Pi 运行缺少历史合同要求的只读事实读取',
  session_stats_invalid: '正式 Pi 模型会话统计不可核验',
  report_json_object_missing:
    '正式 Pi 报告响应中未找到 JSON 对象',
  report_json_syntax_invalid:
    '正式 Pi 报告响应中的 JSON 语法无效',
  report_draft_schema_invalid:
    '正式 Pi 报告 JSON 不符合草稿结构',
  report_payload_invalid: '正式 Pi 模型输出不符合报告合同',
  audit_sink_failed: '正式 Pi 模型安全审计记录失败',
  aborted: '正式 Pi 模型运行已取消',
}

export class FormalPiRunError extends Error {
  constructor(readonly code: FormalPiRunRejectionCode) {
    super(SAFE_ERROR_MESSAGES[code])
    this.name = 'FormalPiRunError'
  }
}

export interface FormalPiUsageAudit {
  assistantMessages: number
  toolCalls: number
  toolResults: number
  totalMessages: number
  tokens: {
    input: number
    output: number
    cacheRead: number
    cacheWrite: number
    total: number
  }
  estimatedCostUsd: number
}

export interface FormalPiObservedModelAudit {
  provider: string
  model: string
  responseModel: string
  stopReason: 'stop'
}

export interface FormalPiToolAudit {
  executedNames: string[]
  executionCount: number
  unauthorizedAttemptCount: number
}

export interface FormalPiRunInputAudit {
  eventReferenceSha256: string
  incidentId: string
  publicationId: string
  revision: number
  dataThrough: string | null
  factSetId: string
  collectorId: 'rrc25'
  reportSpecificationVersion: 'country_outage_report_spec_v1'
  projectKnowledgeVersion:
    typeof COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION
  validatorRulesVersion: string
}

export interface FormalPiRuntimeSecurityAudit {
  resourceLoaderId: 'country-outage-static-resource-loader-v1'
  skillBundleSha256: string
  packageManagerResolutionEnabled: false
  modelResolverEnabled: false
  modelsJsonEnabled: false
  modelCatalogNetworkRefreshEnabled: false
  explicitModel: true
  providerRetryAttempts: 0
  forwardedProviderRequestCount: number
  structuredOutput:
    | {
        applicability: 'required'
        mechanism: 'deepseek-json-object-no-tools-v2'
        payloadPreparedCount: number
      }
    | {
        applicability: 'not_applicable'
        mechanism: null
        payloadPreparedCount: 0
      }
  dependencyRiskException: {
    exceptionId: string
    expiresAt: string
    status: 'active' | 'not_yet_active' | 'expired'
  }
}

export interface FormalPiModelAttemptAudit {
  timeoutMs: number
  maximumAttempts: number
  executedAttempts: number
}

export const FORMAL_PI_NARRATION_MODE =
  'deterministic-base-with-language-slots-v1' as const

export type FormalPiNarrationCheckStatus =
  | 'not_run'
  | 'passed'
  | 'failed'

/**
 * 只记录模型语言槽在宿主确定性报告流水线中的机器状态。
 * 不得写入槽正文、提示词、报告正文或校验错误详情。
 */
export interface FormalPiNarrationAudit {
  mode: typeof FORMAL_PI_NARRATION_MODE
  slotContractVersion:
    typeof COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION
  requestedSlotCount: number
  acceptedSlotCount: number
  baseV5: FormalPiNarrationCheckStatus
  mergeInvariant: FormalPiNarrationCheckStatus
  finalV5: FormalPiNarrationCheckStatus
  modelOutputApplied: boolean
}

export function createFormalPiNarrationAudit(): FormalPiNarrationAudit {
  return {
    mode: FORMAL_PI_NARRATION_MODE,
    slotContractVersion:
      COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
    requestedSlotCount: 0,
    acceptedSlotCount: 0,
    baseV5: 'not_run',
    mergeInvariant: 'not_run',
    finalV5: 'not_run',
    modelOutputApplied: false,
  }
}

export interface FormalPiRunAuditRecord {
  schemaVersion: 'country_outage_pi_run_audit_v3'
  recordedAt: string
  outcome: 'accepted' | 'rejected'
  runtimeIdentity: 'formal' | 'candidate'
  registryVersion?: string
  candidateId?: string
  candidateResourceSha256?: string
  profileId: string
  provider: string
  model: string
  modelVersion: string
  expectedResponseModel: string
  piVersion: string
  certificationEvidenceId?: string
  input: FormalPiRunInputAudit
  narration: FormalPiNarrationAudit
  runtimeSecurity: FormalPiRuntimeSecurityAudit
  modelAttempt: FormalPiModelAttemptAudit
  observed?: FormalPiObservedModelAudit
  tools: FormalPiToolAudit
  usage?: FormalPiUsageAudit
  rejectionCode?: FormalPiRunRejectionCode
}

export type FormalPiAuditSink = (
  record: FormalPiRunAuditRecord,
) => void | Promise<void>

interface AssistantMetadata {
  provider: string
  model: string
  responseModel: string | undefined
  stopReason: string
  content: unknown[]
  usage: unknown
}

interface AssistantUsageMetadata {
  input: number
  output: number
  cacheRead: number
  cacheWrite: number
  totalTokens: number
}

export interface ValidatedFormalPiSession {
  finalText: string
  observed: FormalPiObservedModelAudit
  tools: FormalPiToolAudit
  usage: FormalPiUsageAudit
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function assistantMetadata(value: unknown): AssistantMetadata | undefined {
  if (!isRecord(value) || value.role !== 'assistant') return undefined
  if (
    typeof value.provider !== 'string' ||
    typeof value.model !== 'string' ||
    typeof value.stopReason !== 'string' ||
    !Array.isArray(value.content)
  ) {
    throw new FormalPiRunError('assistant_metadata_invalid')
  }
  return {
    provider: value.provider,
    model: value.model,
    responseModel:
      typeof value.responseModel === 'string'
        ? value.responseModel
        : undefined,
    stopReason: value.stopReason,
    content: value.content,
    usage: value.usage,
  }
}

function assistantUsageMetadata(
  value: unknown,
): AssistantUsageMetadata | undefined {
  if (!isRecord(value)) return undefined
  const integerValues = [
    value.input,
    value.output,
    value.cacheRead,
    value.cacheWrite,
    value.totalTokens,
  ]
  if (
    integerValues.some(
      (item) =>
        typeof item !== 'number' ||
        !Number.isSafeInteger(item) ||
        item < 0,
    )
  ) {
    return undefined
  }
  const input =
    Number(value.input) +
    Number(value.cacheRead) +
    Number(value.cacheWrite)
  const total =
    input + Number(value.output)
  if (
    !Number.isSafeInteger(input) ||
    !Number.isSafeInteger(total) ||
    input <= 0 ||
    Number(value.output) <= 0 ||
    value.totalTokens !== total ||
    !isRecord(value.cost)
  ) {
    return undefined
  }
  const costValues = [
    value.cost.input,
    value.cost.output,
    value.cost.cacheRead,
    value.cost.cacheWrite,
    value.cost.total,
  ]
  if (
    costValues.some(
      (item) =>
        typeof item !== 'number' ||
        !Number.isFinite(item) ||
        item < 0,
    )
  ) {
    return undefined
  }
  return {
    input: value.input as number,
    output: value.output as number,
    cacheRead: value.cacheRead as number,
    cacheWrite: value.cacheWrite as number,
    totalTokens: value.totalTokens as number,
  }
}

function finalText(content: readonly unknown[]): string {
  return content
    .flatMap((block) => {
      if (!isRecord(block)) return []
      return block.type === 'text' && typeof block.text === 'string'
        ? [block.text]
        : []
    })
    .join('')
    .trim()
}

function safeUsage(stats: SessionStats | undefined): FormalPiUsageAudit | undefined {
  if (!stats) return undefined
  const integerValues = [
    stats.assistantMessages,
    stats.toolCalls,
    stats.toolResults,
    stats.totalMessages,
    stats.tokens.input,
    stats.tokens.output,
    stats.tokens.cacheRead,
    stats.tokens.cacheWrite,
    stats.tokens.total,
  ]
  if (
    integerValues.some(
      (value) =>
        typeof value !== 'number' ||
        !Number.isSafeInteger(value) ||
        value < 0,
    ) ||
    typeof stats.cost !== 'number' ||
    !Number.isFinite(stats.cost) ||
    stats.cost < 0
  ) {
    return undefined
  }
  if (
    stats.tokens.total !==
    stats.tokens.input +
      stats.tokens.output +
      stats.tokens.cacheRead +
      stats.tokens.cacheWrite
  ) {
    return undefined
  }
  return {
    assistantMessages: stats.assistantMessages,
    toolCalls: stats.toolCalls,
    toolResults: stats.toolResults,
    totalMessages: stats.totalMessages,
    tokens: {
      input: stats.tokens.input,
      output: stats.tokens.output,
      cacheRead: stats.tokens.cacheRead,
      cacheWrite: stats.tokens.cacheWrite,
      total: stats.tokens.total,
    },
    estimatedCostUsd: stats.cost,
  }
}

export function collectSafeToolAudit(
  messages: readonly unknown[],
): FormalPiToolAudit {
  const executedNames: string[] = []
  let unauthorizedAttemptCount = 0

  for (const message of messages) {
    if (!isRecord(message)) continue
    if (message.role === 'assistant' && Array.isArray(message.content)) {
      for (const block of message.content) {
        if (!isRecord(block) || block.type !== 'toolCall') continue
        if (
          typeof block.name !== 'string' ||
          !ALLOWED_TOOLS.has(block.name)
        ) {
          unauthorizedAttemptCount += 1
        }
      }
    }
    if (message.role !== 'toolResult') continue
    if (
      typeof message.toolName !== 'string' ||
      !ALLOWED_TOOLS.has(message.toolName)
    ) {
      unauthorizedAttemptCount += 1
      continue
    }
    executedNames.push(message.toolName)
  }

  return {
    executedNames,
    executionCount: executedNames.length,
    unauthorizedAttemptCount,
  }
}

export function collectSafeUsageAudit(
  stats: SessionStats | undefined,
): FormalPiUsageAudit | undefined {
  return safeUsage(stats)
}

export function validateFormalPiSession(
  messages: readonly unknown[],
  stats: SessionStats | undefined,
  selection: PiModelRunSelection,
  forwardedProviderRequestCount: number,
): ValidatedFormalPiSession {
  const assistants: AssistantMetadata[] = []
  for (const message of messages) {
    const metadata = assistantMetadata(message)
    if (metadata) assistants.push(metadata)
  }
  if (assistants.length === 0) {
    throw new FormalPiRunError('assistant_message_missing')
  }

  const { profile } = selection
  for (const assistant of assistants) {
    if (assistant.provider !== profile.provider) {
      throw new FormalPiRunError('provider_mismatch')
    }
    if (assistant.model !== profile.model) {
      throw new FormalPiRunError('model_mismatch')
    }
    if (!assistant.responseModel?.trim()) {
      throw new FormalPiRunError('response_model_missing')
    }
    if (assistant.responseModel !== profile.expectedResponseModel) {
      throw new FormalPiRunError('response_model_mismatch')
    }
    if (!['toolUse', 'stop'].includes(assistant.stopReason)) {
      throw new FormalPiRunError('stop_reason_invalid')
    }
  }

  const final = assistants.at(-1)!
  if (final.stopReason !== 'stop') {
    throw new FormalPiRunError('stop_reason_invalid')
  }
  const text = finalText(final.content)
  if (!text) throw new FormalPiRunError('assistant_message_missing')

  const tools = collectSafeToolAudit(messages)
  if (tools.unauthorizedAttemptCount > 0) {
    throw new FormalPiRunError('tool_not_allowed')
  }
  if (
    tools.executionCount >
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumToolExecutions
  ) {
    throw new FormalPiRunError('tool_execution_limit_exceeded')
  }
  const perToolExecutions = new Map<string, number>()
  for (const toolName of tools.executedNames) {
    const count = (perToolExecutions.get(toolName) ?? 0) + 1
    perToolExecutions.set(toolName, count)
    const maximum =
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
        .maximumToolExecutionsByName[
        toolName as keyof typeof FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumToolExecutionsByName
      ]
    if (count > maximum) {
      throw new FormalPiRunError('tool_execution_limit_exceeded')
    }
  }
  const usage = safeUsage(stats)
  if (!usage) throw new FormalPiRunError('session_stats_invalid')
  if (
    !Number.isSafeInteger(forwardedProviderRequestCount) ||
    forwardedProviderRequestCount < 1 ||
    forwardedProviderRequestCount !== assistants.length ||
    forwardedProviderRequestCount !== usage.assistantMessages
  ) {
    throw new FormalPiRunError('session_stats_invalid')
  }
  const assistantUsages = assistants.map((assistant) =>
    assistantUsageMetadata(assistant.usage),
  )
  if (assistantUsages.some((item) => item === undefined)) {
    throw new FormalPiRunError('session_stats_invalid')
  }
  for (const item of assistantUsages) {
    if (
      item!.input + item!.cacheRead + item!.cacheWrite >
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumContextInputTokens ||
      item!.output >
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelOutputTokens
    ) {
      throw new FormalPiRunError('context_input_limit_exceeded')
    }
  }
  const assistantUsageTotals = assistantUsages.reduce(
    (totals, item) => ({
      input: totals.input + item!.input,
      output: totals.output + item!.output,
      cacheRead: totals.cacheRead + item!.cacheRead,
      cacheWrite: totals.cacheWrite + item!.cacheWrite,
    }),
    { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  )
  if (
    assistantUsageTotals.input !== usage.tokens.input ||
    assistantUsageTotals.output !== usage.tokens.output ||
    assistantUsageTotals.cacheRead !==
      usage.tokens.cacheRead ||
    assistantUsageTotals.cacheWrite !==
      usage.tokens.cacheWrite
  ) {
    throw new FormalPiRunError('session_stats_invalid')
  }
  let messageToolCallCount = 0
  let messageToolResultCount = 0
  for (const message of messages) {
    if (!isRecord(message)) continue
    if (message.role === 'assistant' && Array.isArray(message.content)) {
      messageToolCallCount += message.content.filter(
        (block) => isRecord(block) && block.type === 'toolCall',
      ).length
    }
    if (message.role === 'toolResult') {
      messageToolResultCount += 1
    }
  }
  if (
    usage.assistantMessages !== assistants.length ||
    usage.toolCalls !== messageToolCallCount ||
    usage.toolResults !== messageToolResultCount
  ) {
    throw new FormalPiRunError('session_stats_invalid')
  }
  if (
    usage.tokens.input +
      usage.tokens.cacheRead +
      usage.tokens.cacheWrite >
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumContextInputTokens *
        forwardedProviderRequestCount ||
    usage.tokens.output >
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelOutputTokens *
        forwardedProviderRequestCount
  ) {
    throw new FormalPiRunError('context_input_limit_exceeded')
  }

  return {
    finalText: text,
    observed: {
      provider: final.provider,
      model: final.model,
      responseModel: final.responseModel!,
      stopReason: 'stop',
    },
    tools,
    usage,
  }
}

export function baseFormalPiAuditRecord(
  selection: PiModelRunSelection,
  recordedAt: string,
  input: FormalPiRunInputAudit,
  narration: FormalPiNarrationAudit,
  messages: readonly unknown[],
  stats: SessionStats | undefined,
  runtimeSecurity: FormalPiRuntimeSecurityAudit,
  executedModelAttempts: number,
): Omit<
  FormalPiRunAuditRecord,
  'outcome' | 'observed' | 'rejectionCode'
> {
  const { profile } = selection
  const usage = collectSafeUsageAudit(stats)
  return {
    schemaVersion: 'country_outage_pi_run_audit_v3',
    recordedAt,
    runtimeIdentity: selection.runtimeIdentity,
    ...(selection.runtimeIdentity === 'formal'
      ? {
          registryVersion: selection.registryVersion,
          certificationEvidenceId:
            selection.profile.certificationEvidenceId,
        }
      : {
          candidateId: selection.candidateId,
          candidateResourceSha256:
            selection.candidateResourceSha256,
        }),
    profileId: profile.id,
    provider: profile.provider,
    model: profile.model,
    modelVersion: profile.modelVersion,
    expectedResponseModel: profile.expectedResponseModel,
    piVersion: profile.piVersion,
    input: {
      eventReferenceSha256: input.eventReferenceSha256,
      incidentId: input.incidentId,
      publicationId: input.publicationId,
      revision: input.revision,
      dataThrough: input.dataThrough,
      factSetId: input.factSetId,
      collectorId: input.collectorId,
      reportSpecificationVersion: input.reportSpecificationVersion,
      projectKnowledgeVersion: input.projectKnowledgeVersion,
      validatorRulesVersion: input.validatorRulesVersion,
    },
    narration: {
      mode: narration.mode,
      slotContractVersion: narration.slotContractVersion,
      requestedSlotCount: narration.requestedSlotCount,
      acceptedSlotCount: narration.acceptedSlotCount,
      baseV5: narration.baseV5,
      mergeInvariant: narration.mergeInvariant,
      finalV5: narration.finalV5,
      modelOutputApplied: narration.modelOutputApplied,
    },
    runtimeSecurity,
    modelAttempt: {
      timeoutMs:
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.modelAttemptTimeoutMs,
      maximumAttempts:
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelAttempts,
      executedAttempts: executedModelAttempts,
    },
    tools: collectSafeToolAudit(messages),
    ...(usage ? { usage } : {}),
  }
}
