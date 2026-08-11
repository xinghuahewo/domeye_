import { createHash } from 'node:crypto'
import { dirname, resolve } from 'node:path'

import {
  createAgentSession,
  SessionManager,
  SettingsManager,
  type AgentSession,
  type CreateAgentSessionOptions,
  type ModelRuntime,
  type SessionStats,
} from '@earendil-works/pi-coding-agent'

import type {
  CountryOutageReportDraft,
  NarrationRequest,
  ReportModelIdentity,
  ReportNarrator,
} from '../report/contracts.js'
import { buildDeterministicCountryOutageDraft } from '../report/deterministic-narrator.js'
import {
  assertCountryOutageEvidenceCapacity,
  CountryOutageEvidenceCapacityError,
  FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS,
} from '../formal-runtime-limits.js'
import {
  COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION,
  validateReportDraft,
} from '../report/draft-validator.js'
import {
  buildCountryOutageModelLanguagePlan,
  COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
  CountryOutageModelLanguageError,
  mergeCountryOutageLanguageSlots,
  parseCountryOutageLanguageSlotBundle,
  type CountryOutageLanguageSlotBundle,
  type CountryOutageModelLanguagePlanItem,
} from '../report/model-language-plan.js'
import {
  COUNTRY_OUTAGE_TOOL_NAMES,
} from './country-outage-tools.js'
import type { CountryOutageToolBindingOptions } from './country-outage-tools.js'
import {
  COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
  defaultCountryOutageSkillPath,
} from './country-outage-skill-bundle.js'
import {
  assertFormalPiModelCertificationCurrent,
  capCountryOutageModelOutput,
  formalPiModelRunSelection,
  FORMAL_PI_VERSION,
  type CertifiedPiModelSelection,
  type PiModelRunSelection,
} from './formal-model-runtime.js'
import type {
  VerifiedCountryOutageDependencySecurityAttestation,
} from './dependency-security-attestation.js'
import {
  baseFormalPiAuditRecord,
  createFormalPiNarrationAudit,
  FormalPiRunError,
  validateFormalPiSession,
  type FormalPiAuditSink,
  type FormalPiNarrationAudit,
  type FormalPiRunInputAudit,
  type FormalPiRunAuditRecord,
  type FormalPiRuntimeSecurityAudit,
} from './formal-run-audit.js'
import {
  createStaticCountryOutageResourceBundle,
  STATIC_RESOURCE_LOADER_ID,
} from './static-resource-loader.js'

const TRUSTED_SKILL_NAME = 'country-outage-report'
export const BUILTIN_AND_FILESYSTEM_TOOLS = [
  'read',
  'bash',
  'edit',
  'write',
  'grep',
  'find',
  'ls',
] as const

const TRUSTED_SYSTEM_PROMPT = `你是 Domeye 国家中断观测报告的中文语言编辑器。
当前会话禁用全部工具；只能使用受信任的 country-outage-report Skill 和宿主提供的语言槽计划。
不得使用模型记忆、Codex 记忆、项目 AGENTS、互联网、任意 URL、文件系统或 Shell 补充事件事实。
完整报告结构、事实、数字、方向、引用和未知项已经由宿主锁定；不得输出或改写这些内容。
只输出 country_outage_language_slots_v1 JSON，不输出 Markdown 围栏、过程或思考。`

type FixedModel = NonNullable<CreateAgentSessionOptions['model']>
type PiStreamFunction = AgentSession['agent']['streamFunction']
type PiStreamOptions = Parameters<PiStreamFunction>[2]
type PiPayloadHook = NonNullable<
  NonNullable<PiStreamOptions>['onPayload']
>

type ProviderGateViolationCode =
  | 'provider_request_limit_exceeded'
  | 'provider_context_limit_exceeded'

export interface ProviderRequestGate {
  readonly forwardedRequestCount: number
  readonly structuredOutputPayloadPreparedCount: number
  readonly violationCode: ProviderGateViolationCode | undefined
}

export interface PiSessionHandle {
  readonly agent: {
    streamFunction: PiStreamFunction
  }
  readonly messages: readonly unknown[]
  getActiveToolNames?(): string[]
  setActiveToolsByName?(toolNames: string[]): void
  prompt(
    text: string,
    options?: {
      expandPromptTemplates?: boolean
      source?: 'rpc'
    },
  ): Promise<void>
  abort(): Promise<void>
  getSessionStats(): SessionStats
  dispose(): void
}

function providerGateErrorStream(
  model: Parameters<PiStreamFunction>[0],
  code: ProviderGateViolationCode,
): Awaited<ReturnType<PiStreamFunction>> {
  const message = {
    role: 'assistant' as const,
    content: [],
    api: model.api,
    provider: model.provider,
    model: model.id,
    responseModel: model.id,
    usage: {
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 0,
      cost: {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0,
        total: 0,
      },
    },
    stopReason: 'error' as const,
    errorMessage: code,
    timestamp: Date.now(),
  }
  return {
    async *[Symbol.asyncIterator]() {
      yield {
        type: 'error' as const,
        reason: 'error' as const,
        error: message,
      }
    },
    async result() {
      return message
    },
  } as unknown as Awaited<ReturnType<PiStreamFunction>>
}

function isPlainJsonObject(
  value: unknown,
): value is Record<string, unknown> {
  if (
    value === null ||
    typeof value !== 'object' ||
    Array.isArray(value)
  ) {
    return false
  }
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function deepseekNoToolJsonPayloadHook(
  existingHook: PiPayloadHook | undefined,
  onRequested: () => void,
): PiPayloadHook {
  return async (payload, model) => {
    const existingResult = await existingHook?.(payload, model)
    const source =
      existingResult === undefined ? payload : existingResult
    if (
      !isPlainJsonObject(source) ||
      typeof source.model !== 'string' ||
      !Array.isArray(source.messages) ||
      source.stream !== true
    ) {
      throw new Error('structured_output_payload_invalid')
    }
    const prepared: Record<string, unknown> = {
      ...source,
      tool_choice: 'none',
      response_format: {
        type: 'json_object',
      },
    }
    if (
      prepared.model !== source.model ||
      prepared.messages !== source.messages ||
      prepared.stream !== source.stream
    ) {
      throw new Error('structured_output_payload_invalid')
    }
    onRequested()
    return prepared
  }
}

function providerPayloadLimitHook(
  existingHook: PiPayloadHook | undefined,
  onViolation: () => void,
): PiPayloadHook {
  return async (payload, model) => {
    const existingResult = await existingHook?.(payload, model)
    const finalPayload =
      existingResult === undefined ? payload : existingResult
    let serialized: string | undefined
    try {
      serialized = JSON.stringify(finalPayload)
    } catch {
      onViolation()
      throw new Error('provider_payload_limit_exceeded')
    }
    if (
      serialized === undefined
      || Buffer.byteLength(serialized, 'utf8') >
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
          .maximumProviderPayloadBytes
      || FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
        .maximumProviderPayloadBytes
        + FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
          .providerFramingTokenReserve
        > FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
          .maximumContextInputTokens
    ) {
      onViolation()
      throw new Error('provider_payload_limit_exceeded')
    }
    return finalPayload
  }
}

function requiresDeepseekJsonObject(
  model: Parameters<PiStreamFunction>[0],
): boolean {
  return (
    model.provider === 'deepseek' &&
    model.id === 'deepseek-v4-flash' &&
    model.api === 'openai-completions'
  )
}

const REJECTED_DRAFT_CONTEXT_MARKER =
  '上一份语言槽 JSON 已由本地机器校验拒绝；完整修订要求见下一条用户消息。'

/**
 * 工具轮中的自由文本、thinking 和上一份被机器拒绝的完整草稿都不是
 * 下一轮所需事实。正式路径只保留工具调用骨架、固定工具结果和显式修订
 * 提示，避免供应商一次冗长中间响应把后续 64K 上下文挤爆。
 *
 * 原始 Pi 会话仍保留在内存并参与安全审计；这里只收窄送往下一次
 * provider 请求的派生上下文，且不改写工具结果或最终发布草稿。
 */
function compactCountryOutageProviderContext(
  context: Parameters<PiStreamFunction>[1],
): Parameters<PiStreamFunction>[1] {
  if (!context || !Array.isArray(context.messages)) return context
  const messages = context.messages
  const compactedMessages = messages.map((message, index) => {
    if (
      !message ||
      typeof message !== 'object' ||
      Array.isArray(message)
    ) {
      return message
    }
    const record = message as unknown as Record<string, unknown>
    if (
      record.role !== 'assistant' ||
      !Array.isArray(record.content)
    ) {
      return message
    }
    const toolCalls = record.content.flatMap((block) => {
      if (
        !block ||
        typeof block !== 'object' ||
        Array.isArray(block)
      ) {
        return []
      }
      const item = block as Record<string, unknown>
      if (
        item.type !== 'toolCall' ||
        typeof item.name !== 'string' ||
        !COUNTRY_OUTAGE_TOOL_NAMES.includes(
          item.name as (typeof COUNTRY_OUTAGE_TOOL_NAMES)[number],
        )
      ) {
        return []
      }
      return [
        {
          type: 'toolCall' as const,
          ...(typeof item.id === 'string' ? { id: item.id } : {}),
          name: item.name,
          // 三个正式工具均为无参数工具；不把模型自造参数带入下一轮。
          arguments: {},
        },
      ]
    })
    if (toolCalls.length > 0) {
      return {
        ...record,
        content: toolCalls,
      } as typeof message
    }
    const hasLaterUserMessage = messages
      .slice(index + 1)
      .some(
        (later) =>
          Boolean(later) &&
          typeof later === 'object' &&
          !Array.isArray(later) &&
          (later as unknown as Record<string, unknown>).role ===
            'user',
      )
    if (
      record.stopReason === 'stop' &&
      hasLaterUserMessage
    ) {
      return {
        ...record,
        content: [
          {
            type: 'text' as const,
            text: REJECTED_DRAFT_CONTEXT_MARKER,
          },
        ],
      } as typeof message
    }
    return message
  })
  return {
    ...context,
    messages: compactedMessages,
  }
}

export function installProviderRequestGate(
  session: PiSessionHandle,
  maximumProviderRequests: number =
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumProviderRequestsPerReport,
): ProviderRequestGate {
  if (!Number.isSafeInteger(maximumProviderRequests) || maximumProviderRequests < 1) {
    throw new Error('maximum_provider_requests_invalid')
  }
  const original = session.agent.streamFunction
  let forwardedRequestCount = 0
  let structuredOutputPayloadPreparedCount = 0
  let violationCode: ProviderGateViolationCode | undefined
  session.agent.streamFunction = (model, context, options) => {
    if (
      forwardedRequestCount >=
      maximumProviderRequests
    ) {
      violationCode = 'provider_request_limit_exceeded'
      return providerGateErrorStream(model, violationCode)
    }

    let contextBytes: number
    try {
      contextBytes = Buffer.byteLength(
        JSON.stringify(context),
        'utf8',
      )
    } catch {
      violationCode = 'provider_context_limit_exceeded'
      return providerGateErrorStream(model, violationCode)
    }
    const modelContextWindow =
      Number.isSafeInteger(model.contextWindow) &&
      model.contextWindow > 0
        ? model.contextWindow
        : 0
    const maximumContextBytes = Math.min(
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
        .maximumProviderContextBytes,
      modelContextWindow,
    )
    if (
      maximumContextBytes <= 0 ||
      contextBytes > maximumContextBytes
    ) {
      violationCode = 'provider_context_limit_exceeded'
      return providerGateErrorStream(model, violationCode)
    }

    forwardedRequestCount += 1
    let payloadHook = options?.onPayload
    if (requiresDeepseekJsonObject(model)) {
      payloadHook = deepseekNoToolJsonPayloadHook(
        payloadHook,
        () => {
          structuredOutputPayloadPreparedCount += 1
        },
      )
    }
    const gatedOptions: NonNullable<PiStreamOptions> = {
      ...(options ?? {}),
      onPayload: providerPayloadLimitHook(
        payloadHook,
        () => {
          violationCode = 'provider_context_limit_exceeded'
        },
      ),
    }
    return original(
      model,
      compactCountryOutageProviderContext(context),
      gatedOptions,
    )
  }
  return {
    get forwardedRequestCount() {
      return forwardedRequestCount
    },
    get structuredOutputPayloadPreparedCount() {
      return structuredOutputPayloadPreparedCount
    },
    get violationCode() {
      return violationCode
    },
  }
}

export type PiSessionFactory = (
  options: CreateAgentSessionOptions,
) => Promise<{ session: PiSessionHandle }>

export type PiAttemptTimeoutScheduler = (
  callback: () => void,
  timeoutMs: number,
) => () => void

export interface PiReportNarratorOptions {
  client: CountryOutageToolBindingOptions['client']
  model: FixedModel
  modelRuntime: ModelRuntime
  modelSelection?: PiModelRunSelection
  /** @deprecated 仅供既有正式测试兼容；产品入口应显式传 modelSelection。 */
  certification?: CertifiedPiModelSelection
  dependencySecurityAttestation: VerifiedCountryOutageDependencySecurityAttestation
  auditSink: FormalPiAuditSink
  skillPath?: string
  runtimeCwd?: string
  isolatedAgentDir?: string
  sessionFactory?: PiSessionFactory
  attemptTimeoutScheduler?: PiAttemptTimeoutScheduler
  now?: () => Date
}

export interface PiReportResourceOptions {
  skillPath?: string
  runtimeCwd?: string
  isolatedAgentDir?: string
}

function normalizedResourcePaths(options: PiReportResourceOptions): {
  skillPath: string
  runtimeCwd: string
  isolatedAgentDir: string
} {
  const skillPath = resolve(
    options.skillPath ?? defaultCountryOutageSkillPath(),
  )
  const runtimeCwd = resolve(options.runtimeCwd ?? dirname(skillPath))
  const isolatedAgentDir = resolve(
    options.isolatedAgentDir ?? resolve(runtimeCwd, '.pi-isolated'),
  )
  return { skillPath, runtimeCwd, isolatedAgentDir }
}

function languagePlanPrompt(
  plan: readonly CountryOutageModelLanguagePlanItem[],
): string {
  return plan
    .map(
      (item, index) => `${index + 1}. id=${item.id}
   长度：${item.minLength} 至 ${item.maxLength} 个字符
   必含语义：${item.requiredSemanticIds.join('、')}
   语义基线（只可改写语言，不可扩展事实）：${item.seedText}`,
    )
    .join('\n')
}

function languageReportPrompt(
  reference: string,
  plan: readonly CountryOutageModelLanguagePlanItem[],
): string {
  return `/skill:${TRUSTED_SKILL_NAME}

请为宿主已经生成并通过 v5 校验的确定性报告基稿改写指定说明段落。
绑定 reference：${reference}

不得调用任何工具；事件事实、数字、方向和证据引用已经由宿主冻结，且不进入模型可改写范围。
不得请求其他事件、URL、publication 或 revision。
最终只返回 ${COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION} JSON。

当前语言槽计划：
${languagePlanPrompt(plan)}

强制合同：
1. 根对象只能有 schemaVersion、slots；schemaVersion 必须严格等于 ${COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION}。
2. slots 必须按上述顺序逐项输出，每项只能有 id、text；不得缺失、重复、新增或重排。
3. 每个 text 只能是一段简体中文，满足对应长度和全部必含语义。
4. 只改写事件无关的指标口径或证据边界；不得写国家、运营商、具体 ASN、日期、时间、百分比、计数、普通数字、URL、HTML 或 Markdown。
5. 不得新增下降、上升、增加、减少、回升、持平、高于、低于、峰值、恢复或事件结束等方向判断。
6. 不得肯定全国中断、用户或业务中断、原因、责任、攻击、政策、配置错误或故障。
7. 除 RRC25、IPv4、IPv6、/24、/48 等合同允许的技术词外，不得写数字。
8. 不输出完整报告、title、summary、highlights、sections、unknowns、evidenceRefs、解释、工具过程或思考。`
}

function languageRepairPrompt(
  plan: readonly CountryOutageModelLanguagePlanItem[],
): string {
  return `上一份 ${COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION} JSON 未通过本地机器校验。
请从头重写完整语言槽 JSON；不要复述或修补上一份输出。

当前语言槽计划：
${languagePlanPrompt(plan)}

强制要求：
1. 不得调用任何工具；工具已关闭。
2. 不得搜索、请求 URL、切换事件或补充外部事实。
3. 根对象只能有 schemaVersion、slots；每项只能有 id、text，ID、顺序和数量必须与计划完全一致。
4. text 只写一段简体中文指标口径或证据边界，满足长度和全部必含语义。
5. 不得写普通数字、日期、时间、百分比、事件身份、运营商、具体 ASN、URL、HTML 或 Markdown。
6. 不得新增方向、因果、全国中断、用户业务中断、原因责任、完全恢复或事件结束结论。
7. 不输出完整报告、补丁、diff、解释、工具结果、提示词或思考过程。`
}

function parseLanguageSlotText(
  text: string,
  plan: readonly CountryOutageModelLanguagePlanItem[],
  request: NarrationRequest,
): CountryOutageLanguageSlotBundle {
  const normalized = text.trim()
  if (!normalized.startsWith('{')) {
    throw new FormalPiRunError('report_json_object_missing')
  }
  let value: unknown
  try {
    value = JSON.parse(normalized)
  } catch {
    throw new FormalPiRunError('report_json_syntax_invalid')
  }
  try {
    return parseCountryOutageLanguageSlotBundle(
      value,
      plan,
      request.evidence.facts.event,
    )
  } catch (error) {
    if (!(error instanceof CountryOutageModelLanguageError)) {
      throw error
    }
    if (
      error.code === 'language_bundle_schema_invalid' ||
      error.code === 'language_bundle_slot_mismatch'
    ) {
      throw new FormalPiRunError('report_draft_schema_invalid')
    }
    throw new FormalPiRunError('report_payload_invalid')
  }
}

function assembleLanguageEditedDraft(
  baseDraft: CountryOutageReportDraft,
  plan: readonly CountryOutageModelLanguagePlanItem[],
  bundle: CountryOutageLanguageSlotBundle,
  request: NarrationRequest,
  narrationAudit: FormalPiNarrationAudit,
): CountryOutageReportDraft {
  let draft: CountryOutageReportDraft
  try {
    draft = mergeCountryOutageLanguageSlots(baseDraft, plan, bundle)
  } catch (error) {
    narrationAudit.mergeInvariant = 'failed'
    if (error instanceof CountryOutageModelLanguageError) {
      throw new FormalPiRunError('report_payload_invalid')
    }
    throw error
  }
  narrationAudit.mergeInvariant = 'passed'
  let validation: ReturnType<typeof validateReportDraft>
  try {
    validation = validateReportDraft(draft, request.evidence)
  } catch (error) {
    narrationAudit.finalV5 = 'failed'
    throw error
  }
  if (!validation.passed) {
    narrationAudit.finalV5 = 'failed'
    throw new FormalPiRunError('report_payload_invalid')
  }
  narrationAudit.finalV5 = 'passed'
  narrationAudit.modelOutputApplied = true
  return draft
}

function containsToolActivity(messages: readonly unknown[]): boolean {
  for (const message of messages) {
    if (
      !message ||
      typeof message !== 'object' ||
      Array.isArray(message)
    ) {
      continue
    }
    const record = message as Record<string, unknown>
    if (record.role === 'toolResult') return true
    if (record.role !== 'assistant' || !Array.isArray(record.content)) {
      continue
    }
    if (
      record.content.some(
        (block) =>
          Boolean(block) &&
          typeof block === 'object' &&
          !Array.isArray(block) &&
          (block as Record<string, unknown>).type === 'toolCall',
      )
    ) {
      return true
    }
  }
  return false
}

function runInputAudit(
  request: NarrationRequest,
): FormalPiRunInputAudit {
  const { snapshot } = request.evidence.facts
  return {
    eventReferenceSha256: createHash('sha256')
      .update(request.reference.replace(' ', '+'))
      .digest('hex'),
    incidentId: snapshot.incidentId,
    publicationId: snapshot.publicationId,
    revision: snapshot.revision,
    dataThrough: snapshot.dataThrough,
    factSetId: request.evidence.facts.factSetId,
    collectorId: 'rrc25',
    reportSpecificationVersion: 'country_outage_report_spec_v1',
    projectKnowledgeVersion: COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
    validatorRulesVersion:
      COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION,
  }
}

const defaultSessionFactory: PiSessionFactory = async (options) => {
  return await createAgentSession(options)
}

const defaultAttemptTimeoutScheduler: PiAttemptTimeoutScheduler = (
  callback,
  timeoutMs,
) => {
  const handle = setTimeout(callback, timeoutMs)
  handle.unref()
  return () => clearTimeout(handle)
}

export class PiReportNarrator implements ReportNarrator {
  readonly identity: ReportModelIdentity
  readonly validatorRulesVersion =
    COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION
  readonly skillBundleSha256: string
  readonly #model: FixedModel
  readonly #modelRuntime: ModelRuntime
  readonly #modelSelection: PiModelRunSelection
  readonly #dependencySecurityAttestation:
    VerifiedCountryOutageDependencySecurityAttestation
  readonly #auditSink: FormalPiAuditSink
  readonly #skillPath: string | undefined
  readonly #runtimeCwd: string | undefined
  readonly #isolatedAgentDir: string | undefined
  readonly #sessionFactory: PiSessionFactory
  readonly #attemptTimeoutScheduler: PiAttemptTimeoutScheduler
  readonly #now: () => Date

  constructor(options: PiReportNarratorOptions) {
    const modelSelection =
      options.modelSelection ??
      (options.certification
        ? formalPiModelRunSelection(options.certification)
        : undefined)
    if (!modelSelection) {
      throw new FormalPiRunError('configured_model_mismatch')
    }
    const { profile } = modelSelection
    if (
      options.model.provider !== profile.provider ||
      options.model.id !== profile.model
    ) {
      throw new FormalPiRunError('configured_model_mismatch')
    }
    if (
      !Number.isInteger(options.model.contextWindow) ||
      options.model.contextWindow <
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.minimumModelContextWindowTokens
    ) {
      throw new FormalPiRunError('model_context_window_too_small')
    }
    try {
      this.#model = capCountryOutageModelOutput(options.model)
    } catch {
      throw new FormalPiRunError('model_output_limit_invalid')
    }
    this.#modelRuntime = options.modelRuntime
    this.#modelSelection = modelSelection
    if (options.dependencySecurityAttestation.audit.status !== 'verified') {
      throw new FormalPiRunError('dependency_security_attestation_invalid')
    }
    this.#dependencySecurityAttestation = options.dependencySecurityAttestation
    this.#auditSink = options.auditSink
    const resourcePaths = normalizedResourcePaths(options)
    const resourceBundle = createStaticCountryOutageResourceBundle(
      resourcePaths.skillPath,
      TRUSTED_SYSTEM_PROMPT,
    )
    this.#skillPath = resourcePaths.skillPath
    this.#runtimeCwd = resourcePaths.runtimeCwd
    this.#isolatedAgentDir = resourcePaths.isolatedAgentDir
    this.skillBundleSha256 = resourceBundle.skillBundleSha256
    this.#sessionFactory = options.sessionFactory ?? defaultSessionFactory
    this.#attemptTimeoutScheduler =
      options.attemptTimeoutScheduler ?? defaultAttemptTimeoutScheduler
    this.#now = options.now ?? (() => new Date())
    this.identity = {
      provider: profile.provider,
      model: profile.model,
      modelVersion: profile.modelVersion,
      adapter: 'pi-sdk',
      piVersion: FORMAL_PI_VERSION,
      runtimeIdentity: modelSelection.runtimeIdentity,
      ...(modelSelection.runtimeIdentity === 'formal'
        ? {
            modelRevisionKind:
              modelSelection.profile.modelRevisionKind,
            immutableRevisionAvailable:
              modelSelection.profile.immutableRevisionAvailable,
            limitation: modelSelection.profile.limitation,
            certificationValidUntil:
              modelSelection.profile.certificationValidUntil,
            certifiedScenarioSetId:
              modelSelection.profile.certifiedScenarioSetId,
            certifiedInputScope:
              modelSelection.profile.certifiedInputScope,
          }
        : {}),
    }
  }

  async #writeAudit(record: FormalPiRunAuditRecord): Promise<void> {
    try {
      await this.#auditSink(record)
    } catch {
      throw new FormalPiRunError('audit_sink_failed')
    }
  }

  async generate(
    request: NarrationRequest,
  ): Promise<CountryOutageReportDraft> {
    if (this.#modelSelection.runtimeIdentity === 'formal') {
      assertFormalPiModelCertificationCurrent(
        this.#modelSelection.profile,
        this.#now(),
      )
    }
    request.signal?.throwIfAborted()
    const inputAudit = runInputAudit(request)
    const narrationAudit = createFormalPiNarrationAudit()
    const resourceOptions: PiReportResourceOptions = {
      ...(this.#skillPath === undefined
        ? {}
        : { skillPath: this.#skillPath }),
      ...(this.#runtimeCwd === undefined
        ? {}
        : { runtimeCwd: this.#runtimeCwd }),
      ...(this.#isolatedAgentDir === undefined
        ? {}
        : { isolatedAgentDir: this.#isolatedAgentDir }),
    }
    const { skillPath, runtimeCwd, isolatedAgentDir } =
      normalizedResourcePaths(resourceOptions)
    const settingsManager = SettingsManager.inMemory({
      compaction: { enabled: false },
      retry: {
        enabled: false,
        provider: { maxRetries: 0 },
      },
      images: { blockImages: true },
      enableSkillCommands: true,
    })
    const resourceBundle = createStaticCountryOutageResourceBundle(
      skillPath,
      TRUSTED_SYSTEM_PROMPT,
    )
    const resourceLoader = resourceBundle.loader
    const dependencyCheckedAt = this.#now()
    const runtimeSecurity: FormalPiRuntimeSecurityAudit = {
      resourceLoaderId: STATIC_RESOURCE_LOADER_ID,
      skillBundleSha256: resourceBundle.skillBundleSha256,
      packageManagerResolutionEnabled: false,
      modelResolverEnabled: false,
      modelsJsonEnabled: false,
      modelCatalogNetworkRefreshEnabled: false,
      explicitModel: true,
      providerRetryAttempts: 0,
      forwardedProviderRequestCount: 0,
      structuredOutput: requiresDeepseekJsonObject(this.#model)
        ? {
            applicability: 'required',
            mechanism: 'deepseek-json-object-no-tools-v2',
            payloadPreparedCount: 0,
          }
        : {
            applicability: 'not_applicable',
            mechanism: null,
            payloadPreparedCount: 0,
          },
      dependencySecurityAttestation: {
        attestationId:
          this.#dependencySecurityAttestation.audit.attestationId,
        verifiedAt:
          this.#dependencySecurityAttestation.audit.verifiedAt,
        lockfileSha256:
          this.#dependencySecurityAttestation.audit.lockfileSha256,
        status: 'verified',
      },
    }
    if (!Number.isFinite(dependencyCheckedAt.valueOf())) {
      throw new FormalPiRunError('dependency_security_attestation_invalid')
    }
    if (
      resourceBundle.skillBundleSha256 !== this.skillBundleSha256
    ) {
      await this.#writeAudit({
        ...baseFormalPiAuditRecord(
          this.#modelSelection,
          this.#now().toISOString(),
          inputAudit,
          narrationAudit,
          [],
          undefined,
          runtimeSecurity,
          0,
        ),
        outcome: 'rejected',
        rejectionCode: 'resource_bundle_mismatch',
      })
      throw new FormalPiRunError('resource_bundle_mismatch')
    }
    request.signal?.throwIfAborted()
    try {
      assertCountryOutageEvidenceCapacity(request.evidence)
    } catch (error) {
      if (!(error instanceof CountryOutageEvidenceCapacityError)) {
        throw error
      }
      await this.#writeAudit({
        ...baseFormalPiAuditRecord(
          this.#modelSelection,
          this.#now().toISOString(),
          inputAudit,
          narrationAudit,
          [],
          undefined,
          runtimeSecurity,
          0,
        ),
        outcome: 'rejected',
        rejectionCode: 'evidence_record_limit_exceeded',
      })
      throw new FormalPiRunError('evidence_record_limit_exceeded')
    }

    let baseDraft: CountryOutageReportDraft
    try {
      baseDraft = buildDeterministicCountryOutageDraft(
        request.evidence,
      )
    } catch {
      await this.#writeAudit({
        ...baseFormalPiAuditRecord(
          this.#modelSelection,
          this.#now().toISOString(),
          inputAudit,
          narrationAudit,
          [],
          undefined,
          runtimeSecurity,
          0,
        ),
        outcome: 'rejected',
        rejectionCode: 'report_payload_invalid',
      })
      throw new FormalPiRunError('report_payload_invalid')
    }
    try {
      const baseValidation = validateReportDraft(
        baseDraft,
        request.evidence,
      )
      if (!baseValidation.passed) {
        narrationAudit.baseV5 = 'failed'
        throw new FormalPiRunError('report_payload_invalid')
      }
      narrationAudit.baseV5 = 'passed'
    } catch {
      if (narrationAudit.baseV5 === 'not_run') {
        narrationAudit.baseV5 = 'failed'
      }
      await this.#writeAudit({
        ...baseFormalPiAuditRecord(
          this.#modelSelection,
          this.#now().toISOString(),
          inputAudit,
          narrationAudit,
          [],
          undefined,
          runtimeSecurity,
          0,
        ),
        outcome: 'rejected',
        rejectionCode: 'report_payload_invalid',
      })
      throw new FormalPiRunError('report_payload_invalid')
    }
    let languagePlan: readonly CountryOutageModelLanguagePlanItem[]
    try {
      languagePlan = buildCountryOutageModelLanguagePlan(baseDraft)
      narrationAudit.requestedSlotCount = languagePlan.length
    } catch {
      await this.#writeAudit({
        ...baseFormalPiAuditRecord(
          this.#modelSelection,
          this.#now().toISOString(),
          inputAudit,
          narrationAudit,
          [],
          undefined,
          runtimeSecurity,
          0,
        ),
        outcome: 'rejected',
        rejectionCode: 'report_payload_invalid',
      })
      throw new FormalPiRunError('report_payload_invalid')
    }

    let created: Awaited<ReturnType<PiSessionFactory>>
    try {
      created = await this.#sessionFactory({
        cwd: runtimeCwd,
        agentDir: isolatedAgentDir,
        model: this.#model,
        modelRuntime: this.#modelRuntime,
        thinkingLevel: this.#modelSelection.profile.thinkingLevel,
        noTools: 'all',
        tools: [],
        excludeTools: [...BUILTIN_AND_FILESYSTEM_TOOLS],
        customTools: [],
        resourceLoader,
        sessionManager: SessionManager.inMemory(runtimeCwd),
        settingsManager,
      })
    } catch {
      const aborted = request.signal?.aborted === true
      await this.#writeAudit({
        ...baseFormalPiAuditRecord(
          this.#modelSelection,
          this.#now().toISOString(),
          inputAudit,
          narrationAudit,
          [],
          undefined,
          runtimeSecurity,
          0,
        ),
        outcome: 'rejected',
        rejectionCode: aborted ? 'aborted' : 'provider_call_failed',
      })
      request.signal?.throwIfAborted()
      throw new FormalPiRunError('provider_call_failed')
    }
    const { session } = created
    let providerRequestGate: ProviderRequestGate
    try {
      providerRequestGate = installProviderRequestGate(session)
    } catch {
      await this.#writeAudit({
        ...baseFormalPiAuditRecord(
          this.#modelSelection,
          this.#now().toISOString(),
          inputAudit,
          narrationAudit,
          [],
          undefined,
          runtimeSecurity,
          0,
        ),
        outcome: 'rejected',
        rejectionCode: 'provider_call_failed',
      })
      session.dispose()
      throw new FormalPiRunError('provider_call_failed')
    }
    let auditRecorded = false
    let executedModelAttempts = 0
    let abortCause: 'user' | 'timeout' | undefined
    let sessionAbortPromise: Promise<void> | undefined
    const abortSession = (): Promise<void> => {
      sessionAbortPromise ??= session.abort().catch(() => undefined)
      return sessionAbortPromise
    }
    const onAbort = (): void => {
      abortCause ??= 'user'
      void abortSession()
    }
    const runPromptAttempt = async (prompt: string): Promise<void> => {
      request.signal?.throwIfAborted()
      executedModelAttempts += 1
      if (
        executedModelAttempts >
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelAttempts
      ) {
        throw new FormalPiRunError('maximum_model_attempts_exceeded')
      }
      let cancelAttemptTimeout = (): void => undefined
      const attemptTimeout = new Promise<never>((_resolve, reject) => {
        cancelAttemptTimeout = this.#attemptTimeoutScheduler(() => {
          if (abortCause !== undefined) return
          abortCause = 'timeout'
          reject(new FormalPiRunError('model_attempt_timeout'))
          void abortSession()
        }, FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.modelAttemptTimeoutMs)
      })
      try {
        await Promise.race([
          session.prompt(prompt, {
            expandPromptTemplates: false,
            source: 'rpc',
          }),
          attemptTimeout,
        ])
      } finally {
        cancelAttemptTimeout()
      }
      request.signal?.throwIfAborted()
    }
    const validateCurrentSession = (): {
      stats: SessionStats
      validated: ReturnType<typeof validateFormalPiSession>
    } => {
      runtimeSecurity.forwardedProviderRequestCount =
        providerRequestGate.forwardedRequestCount
      if (
        runtimeSecurity.structuredOutput.applicability === 'required'
      ) {
        runtimeSecurity.structuredOutput.payloadPreparedCount =
          providerRequestGate.structuredOutputPayloadPreparedCount
      }
      if (providerRequestGate.violationCode) {
        throw new FormalPiRunError(
          providerRequestGate.violationCode,
        )
      }
      let stats: SessionStats
      try {
        stats = session.getSessionStats()
      } catch {
        throw new FormalPiRunError('session_stats_invalid')
      }
      return {
        stats,
        validated: validateFormalPiSession(
          session.messages,
          stats,
          this.#modelSelection,
          providerRequestGate.forwardedRequestCount,
        ),
      }
    }
    request.signal?.addEventListener('abort', onAbort, { once: true })
    try {
      await runPromptAttempt(
        languageReportPrompt(request.reference, languagePlan),
      )
      let { stats, validated } = validateCurrentSession()
      let draft: CountryOutageReportDraft | undefined
      let initialLanguageFailure: FormalPiRunError | undefined
      try {
        const bundle = parseLanguageSlotText(
          validated.finalText,
          languagePlan,
          request,
        )
        narrationAudit.acceptedSlotCount = bundle.slots.length
        draft = assembleLanguageEditedDraft(
          baseDraft,
          languagePlan,
          bundle,
          request,
          narrationAudit,
        )
      } catch (error) {
        initialLanguageFailure =
          error instanceof FormalPiRunError
            ? error
            : new FormalPiRunError('report_payload_invalid')
      }
      if (
        draft === undefined &&
        providerRequestGate.forwardedRequestCount <
          FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
            .maximumProviderRequestsPerReport
      ) {
        const messagesBeforeRepair = session.messages.length
        if (
          typeof session.setActiveToolsByName !== 'function' ||
          typeof session.getActiveToolNames !== 'function'
        ) {
          throw new FormalPiRunError('tool_not_allowed')
        }
        try {
          session.setActiveToolsByName([])
          if (session.getActiveToolNames().length !== 0) {
            throw new FormalPiRunError('tool_not_allowed')
          }
        } catch (error) {
          if (error instanceof FormalPiRunError) throw error
          throw new FormalPiRunError('tool_not_allowed')
        }
        await runPromptAttempt(
          languageRepairPrompt(languagePlan),
        )
        if (
          containsToolActivity(
            session.messages.slice(messagesBeforeRepair),
          )
        ) {
          throw new FormalPiRunError('tool_not_allowed')
        }
        const repairedSession = validateCurrentSession()
        stats = repairedSession.stats
        validated = repairedSession.validated
        const repairedBundle = parseLanguageSlotText(
          validated.finalText,
          languagePlan,
          request,
        )
        narrationAudit.acceptedSlotCount =
          repairedBundle.slots.length
        draft = assembleLanguageEditedDraft(
          baseDraft,
          languagePlan,
          repairedBundle,
          request,
          narrationAudit,
        )
      }
      if (draft === undefined) {
        throw (
          initialLanguageFailure ??
          new FormalPiRunError('report_payload_invalid')
        )
      }
      auditRecorded = true
      await this.#writeAudit({
        ...baseFormalPiAuditRecord(
          this.#modelSelection,
          this.#now().toISOString(),
          inputAudit,
          narrationAudit,
          session.messages,
          stats,
          runtimeSecurity,
          executedModelAttempts,
        ),
        outcome: 'accepted',
        observed: validated.observed,
      })
      return draft
    } catch (error) {
      if (auditRecorded) throw error
      const userAborted =
        abortCause === 'user' ||
        request.signal?.aborted === true
      if (abortCause === 'timeout' || userAborted) {
        await abortSession()
      }
      const rejectionCode =
        abortCause === 'timeout'
          ? 'model_attempt_timeout'
          : userAborted
            ? 'aborted'
            : providerRequestGate.violationCode
              ? providerRequestGate.violationCode
              : error instanceof FormalPiRunError
                ? error.code
                : 'provider_call_failed'
      let stats: SessionStats | undefined
      runtimeSecurity.forwardedProviderRequestCount =
        providerRequestGate.forwardedRequestCount
      if (
        runtimeSecurity.structuredOutput.applicability === 'required'
      ) {
        runtimeSecurity.structuredOutput.payloadPreparedCount =
          providerRequestGate.structuredOutputPayloadPreparedCount
      }
      try {
        stats = session.getSessionStats()
      } catch {
        stats = undefined
      }
      auditRecorded = true
      await this.#writeAudit({
        ...baseFormalPiAuditRecord(
          this.#modelSelection,
          this.#now().toISOString(),
          inputAudit,
          narrationAudit,
          session.messages,
          stats,
          runtimeSecurity,
          executedModelAttempts,
        ),
        outcome: 'rejected',
        rejectionCode,
      })
      if (abortCause === 'timeout') {
        throw new FormalPiRunError('model_attempt_timeout')
      }
      if (userAborted) throw error
      if (providerRequestGate.violationCode) {
        throw new FormalPiRunError(
          providerRequestGate.violationCode,
        )
      }
      if (error instanceof FormalPiRunError) throw error
      throw new FormalPiRunError('provider_call_failed')
    } finally {
      request.signal?.removeEventListener('abort', onAbort)
      session.dispose()
    }
  }
}

export const PI_REPORT_SECURITY_PROFILE = Object.freeze({
  piVersion: FORMAL_PI_VERSION,
  noTools: 'all' as const,
  allowedTools: [],
  excludedTools: [...BUILTIN_AND_FILESYSTEM_TOOLS],
  trustedSkillName: TRUSTED_SKILL_NAME,
  resourceLoaderId: STATIC_RESOURCE_LOADER_ID,
  packageManagerResolutionEnabled: false,
  modelResolverEnabled: false,
  modelsJsonEnabled: false,
  modelCatalogNetworkRefreshEnabled: false,
  explicitModel: true,
  dependencySecurityAttestationRequired: true,
  externalGlobEnabled: false,
  extensionsEnabled: false,
  promptTemplatesEnabled: false,
  contextFilesEnabled: false,
  persistentSessionEnabled: false,
  capacityLimits: FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS,
})
