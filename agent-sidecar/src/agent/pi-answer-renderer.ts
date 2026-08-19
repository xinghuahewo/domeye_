import { resolve } from 'node:path'

import {
  createAgentSession,
  SessionManager,
  SettingsManager,
  type CreateAgentSessionOptions,
  type SessionStats,
} from '@earendil-works/pi-coding-agent'
import { Check } from 'typebox/value'

import {
  DomeyeRendererDraftSchema,
  type DomeyeAnswerContext,
  type DomeyeRendererDraft,
} from './contracts.js'
import {
  renderCountryOutageDeterministicFallback,
  type DomeyeAnswerRenderer,
} from './finding-answer.js'
import {
  DomeyeTurnProviderAccounting,
  installDomeyeProviderAttemptBoundary,
  type DomeyePiSessionFactory,
  type DomeyePiSessionHandle,
} from './pi-runtime-boundary.js'
import {
  createStaticCountryOutageEmptyResourceBundle,
} from '../pi/static-resource-loader.js'

const RENDERER_SYSTEM_PROMPT = `你是 Domeye 首个纵向切片的中文 Renderer。
当前会话没有工具，只能读取用户消息中由单个 Answer Context 生成的受控投影；不得使用模型记忆、互联网、文件、旧对话或投影外事实。
用户消息本身就是唯一目标 JSON 对象；原样返回该对象，不要输出 wrapper、Markdown 围栏、解释或任何额外字段。
不得增删、改写或重排目标 JSON 中的任何字段和值，text 也必须逐字保持。
不得推断全国断网、真实用户影响、原因、责任或真实恢复。`

type PiStreamFunction = DomeyePiSessionHandle['agent']['streamFunction']
type PiStreamOptions = Parameters<PiStreamFunction>[2]
type PiPayloadHook = NonNullable<
  NonNullable<PiStreamOptions>['onPayload']
>

export class DomeyeRendererError extends Error {
  constructor(
    readonly code:
      | 'renderer_busy'
      | 'renderer_timeout'
      | 'renderer_attempt_unclosed'
      | 'renderer_output_invalid'
      | 'renderer_provider_failed',
  ) {
    super(code)
    this.name = 'DomeyeRendererError'
  }
}

export interface PiAnswerRendererOptions {
  readonly model_binding: {
    readonly identity: {
      readonly provider: string
      readonly model: string
      readonly model_version: string
      readonly expected_response_model: string
    }
    readonly model: NonNullable<CreateAgentSessionOptions['model']>
    readonly model_runtime: NonNullable<CreateAgentSessionOptions['modelRuntime']>
    readonly thinking_level: NonNullable<CreateAgentSessionOptions['thinkingLevel']>
  }
  readonly accounting: DomeyeTurnProviderAccounting
  readonly session_factory?: DomeyePiSessionFactory
  readonly runtime_cwd?: string
  readonly timeout_ms?: number
}

function defaultSessionFactory(
  options: CreateAgentSessionOptions,
): Promise<{ session: DomeyePiSessionHandle }> {
  return createAgentSession(options) as unknown as Promise<{
    session: DomeyePiSessionHandle
  }>
}

function lastAssistantText(session: DomeyePiSessionHandle): string | undefined {
  const direct = session.getLastAssistantText?.()
  if (direct?.trim()) return direct.trim()
  for (let index = session.messages.length - 1; index >= 0; index -= 1) {
    const value = session.messages[index]
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue
    const record = value as Record<string, unknown>
    if (record.role !== 'assistant' || !Array.isArray(record.content)) continue
    const text = record.content.flatMap((block) => {
      if (!block || typeof block !== 'object' || Array.isArray(block)) return []
      const item = block as Record<string, unknown>
      return item.type === 'text' && typeof item.text === 'string'
        ? [item.text]
        : []
    }).join('').trim()
    if (text) return text
  }
  return undefined
}

function safeStats(session: DomeyePiSessionHandle): SessionStats | undefined {
  try {
    return session.getSessionStats()
  } catch {
    return undefined
  }
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

function rendererJsonObjectPayloadHook(
  existingHook: PiPayloadHook | undefined,
): PiPayloadHook {
  return async (payload, model) => {
    if (!isPlainJsonObject(payload)) {
      throw new Error('renderer_provider_payload_invalid')
    }
    const existingResult = await existingHook?.(payload, model)
    if (
      existingResult !== undefined
      && !isPlainJsonObject(existingResult)
    ) {
      throw new Error('renderer_provider_payload_invalid')
    }
    const source = existingResult ?? payload
    if (
      typeof source.model !== 'string'
      || !Array.isArray(source.messages)
      || source.stream !== true
    ) {
      throw new Error('renderer_provider_payload_invalid')
    }
    return {
      ...source,
      tool_choice: 'none',
      temperature: 0,
      response_format: {
        type: 'json_object',
      },
    }
  }
}

function installRendererJsonObjectPayloadBoundary(
  session: DomeyePiSessionHandle,
): void {
  const original = session.agent.streamFunction
  session.agent.streamFunction = (model, context, options) => {
    if (
      model.provider !== 'deepseek'
      || model.api !== 'openai-completions'
    ) return original(model, context, options)
    return original(model, context, {
      ...(options ?? {}),
      onPayload: rendererJsonObjectPayloadHook(options?.onPayload),
    })
  }
}

function rendererPrompt(context: DomeyeAnswerContext): string {
  const identity = context.data_identity
  const finding = context.finding
  return JSON.stringify({
    schema_version: 'domeye_agent_renderer_draft_v1',
    context_id: context.context_id,
    finding_id: finding.finding_id,
    candidate_id: context.candidate_id,
    publication_id: identity.publication_id,
    revision: identity.revision,
    collector_id: identity.collector_id,
    window_start_utc: identity.window_start_utc,
    window_end_utc: identity.window_end_utc,
    metric: finding.metric,
    unit: finding.unit,
    values: finding.values,
    observer_scope_zh: context.observer_scope_zh,
    limitations_zh: context.mandatory_limitations_zh,
    evidence_refs: context.evidence_refs,
    text: renderCountryOutageDeterministicFallback(context),
  })
}

export class PiAnswerRenderer implements DomeyeAnswerRenderer {
  readonly #options: PiAnswerRendererOptions
  readonly #runtimeCwd: string
  readonly #timeoutMs: number
  readonly #resourceBundle = createStaticCountryOutageEmptyResourceBundle(
    RENDERER_SYSTEM_PROMPT,
  )
  #active = false

  constructor(options: PiAnswerRendererOptions) {
    this.#options = options
    this.#runtimeCwd = resolve(options.runtime_cwd ?? process.cwd())
    this.#timeoutMs = options.timeout_ms ?? 75_000
    if (
      !Number.isSafeInteger(this.#timeoutMs)
      || this.#timeoutMs < 10_000
      || this.#timeoutMs > 120_000
    ) throw new Error('renderer_timeout_invalid')
  }

  async render(context: DomeyeAnswerContext): Promise<DomeyeRendererDraft> {
    if (this.#active) throw new DomeyeRendererError('renderer_busy')
    this.#active = true
    let session: DomeyePiSessionHandle | undefined
    try {
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
        noTools: 'all',
        tools: [],
        customTools: [],
        resourceLoader: this.#resourceBundle.loader,
        sessionManager: SessionManager.inMemory(this.#runtimeCwd),
        settingsManager,
      })
      session = created.session
      installDomeyeProviderAttemptBoundary(
        session,
        this.#options.accounting.budget,
        'renderer',
        this.#options.model_binding.identity,
      )
      installRendererJsonObjectPayloadBoundary(session)
      const attemptOffset = this.#options.accounting.budget.snapshot().length
      let timer: ReturnType<typeof setTimeout> | undefined
      let timedOut = false
      try {
        await Promise.race([
          session.prompt(rendererPrompt(context), {
            expandPromptTemplates: false,
            source: 'rpc',
          }),
          new Promise<never>((_resolve, reject) => {
            timer = setTimeout(() => {
              timedOut = true
              void session?.abort().catch(() => undefined)
              reject(new DomeyeRendererError('renderer_timeout'))
            }, this.#timeoutMs)
            timer.unref()
          }),
        ])
      } finally {
        if (timer) clearTimeout(timer)
      }
      if (timedOut) throw new DomeyeRendererError('renderer_timeout')
      const rendererAttempts = this.#options.accounting.budget
        .snapshot()
        .slice(attemptOffset)
      if (
        rendererAttempts.length !== 1
        || rendererAttempts[0]?.phase !== 'renderer'
        || rendererAttempts[0].outcome !== 'completed'
      ) {
        const failure = rendererAttempts.some(
          (attempt) => attempt.outcome === 'started',
        )
          ? 'renderer_attempt_unclosed'
          : 'renderer_provider_failed'
        throw new DomeyeRendererError(failure)
      }
      const text = lastAssistantText(session)
      let parsed: unknown
      try {
        parsed = text ? JSON.parse(text) : undefined
      } catch {
        throw new DomeyeRendererError('renderer_output_invalid')
      }
      if (!Check(DomeyeRendererDraftSchema, parsed)) {
        throw new DomeyeRendererError('renderer_output_invalid')
      }
      return parsed
    } catch (error) {
      this.#options.accounting.budget.failStarted(error, 'renderer')
      if (error instanceof DomeyeRendererError) throw error
      throw new DomeyeRendererError('renderer_provider_failed')
    } finally {
      this.#options.accounting.budget.failStarted(
        new Error('renderer_attempt_unclosed'),
        'renderer',
      )
      if (session) {
        this.#options.accounting.recordSessionStats(safeStats(session))
        session.dispose()
      }
      this.#active = false
    }
  }
}
