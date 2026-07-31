import type {
  AgentToolResult,
  ToolDefinition,
} from '@earendil-works/pi-coding-agent'
import { Type } from 'typebox'

import type {
  CountryOutageAsnPage,
  DerivedNumericFact,
  CountryOutageFactSet,
  CountryOutageResolution,
  ObservationBatch,
  SnapshotIdentity,
} from '../domain/contracts.js'
import type {
  AsnQuery,
  DomeyeCountryOutageClient,
} from '../domain/domeye-client.js'
import { assembleCountryOutageFacts } from '../domain/observation-assembler.js'
import { FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS } from '../formal-runtime-limits.js'
import type { ReportEvidenceBundle } from '../report/contracts.js'

export const COUNTRY_OUTAGE_TOOL_NAMES = [
  'country_outage_resolve',
  'country_outage_get_observation',
  'country_outage_get_asns',
] as const

export type CountryOutageToolName =
  (typeof COUNTRY_OUTAGE_TOOL_NAMES)[number]

const COUNTRY_OUTAGE_REFERENCE =
  /^country_outage\/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}\/[A-Z]{2}\/[1-9]\d*\/[A-Za-z0-9_-]+$/

type CountryOutageReadClient = Pick<
  DomeyeCountryOutageClient,
  'getObservationBatch' | 'getAsns'
>

type CompactFactSet = Omit<
  CountryOutageFactSet,
  'series' | 'resourceSeries' | 'derivedFacts'
> & {
  derivedFacts: Array<Omit<DerivedNumericFact, 'operands'>>
}

export interface CountryOutageToolResolution {
  schemaVersion: 'country_outage_tool_resolution_v1'
  reference: string
  eventType: 'country_outage'
  incidentId: string
  publicationId: string
  revision: number
  dataThrough: string | null
  isFinal: boolean
  collectorId: 'rrc25'
  countryCode: string
  countryName: string
  reportEligible: boolean
  eligibilityReasons: string[]
  capabilities: CountryOutageFactSet['capabilities']
}

export interface CountryOutageObservationToolResult {
  schemaVersion: 'country_outage_tool_observation_v1'
  reference: string
  facts: CompactFactSet
  omittedSeriesSlotCount: number
  omittedResourceSlotCount: number
}

export interface CountryOutageAsnToolResult {
  schemaVersion: 'country_outage_tool_asns_v1'
  reference: string
  snapshot: SnapshotIdentity
  page: CountryOutageAsnPage
}

export interface CountryOutageToolBindingOptions {
  reference: string
  client: CountryOutageReadClient
  /**
   * 报告叙述阶段传入编译器已经固定的证据，避免工具重新解析到更新的 revision。
   * 省略时，三个工具会共享一次惰性加载的观测批次。
   */
  pinnedEvidence?: ReportEvidenceBundle
  executionBudget?: CountryOutageToolExecutionBudget
}

interface PinnedObservation {
  facts: CountryOutageFactSet
  resolution?: CountryOutageResolution
}

export type CountryOutageToolCapacityRejectionCode =
  | 'tool_execution_limit_exceeded'
  | 'tool_result_limit_exceeded'

export class CountryOutageToolCapacityError extends Error {
  constructor(readonly code: CountryOutageToolCapacityRejectionCode) {
    super(
      code === 'tool_execution_limit_exceeded'
        ? '国家中断只读工具执行次数超过冻结上限'
        : '国家中断只读工具结果超过冻结字节上限',
    )
    this.name = 'CountryOutageToolCapacityError'
  }
}

/**
 * 每个正式报告运行独占一个预算实例。计数发生在工具读取数据之前，
 * 结果字节数按真正放入模型上下文的 UTF-8 JSON 文本计算。
 */
export class CountryOutageToolExecutionBudget {
  #executionCount = 0
  #cumulativeResultBytes = 0
  readonly #executionCountByName = new Map<CountryOutageToolName, number>()
  #violationCode: CountryOutageToolCapacityRejectionCode | undefined
  #frozen = false

  get executionCount(): number {
    return this.#executionCount
  }

  get cumulativeResultBytes(): number {
    return this.#cumulativeResultBytes
  }

  rejectResultLimit(): never {
    this.#violationCode = 'tool_result_limit_exceeded'
    throw new CountryOutageToolCapacityError(this.#violationCode)
  }

  /**
   * 进入同会话无工具阶段后冻结预算。任何后续工具尝试都会在读取数据前失败，
   * 并复用既有的冻结执行上限拒绝码。
   */
  freeze(): void {
    this.#frozen = true
  }

  get violationCode():
    | CountryOutageToolCapacityRejectionCode
    | undefined {
    return this.#violationCode
  }

  begin(name: CountryOutageToolName): void {
    if (this.#frozen) {
      this.#violationCode = 'tool_execution_limit_exceeded'
      throw new CountryOutageToolCapacityError(this.#violationCode)
    }
    const perNameCount =
      (this.#executionCountByName.get(name) ?? 0) + 1
    const perNameLimit =
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
        .maximumToolExecutionsByName[name]
    if (
      this.#executionCount + 1 >
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumToolExecutions ||
      perNameCount > perNameLimit
    ) {
      this.#violationCode = 'tool_execution_limit_exceeded'
      throw new CountryOutageToolCapacityError(this.#violationCode)
    }
    this.#executionCount += 1
    this.#executionCountByName.set(name, perNameCount)
  }

  result<T>(
    _name: CountryOutageToolName,
    details: T,
  ): AgentToolResult<T> {
    const text = JSON.stringify(details)
    // Pi/OpenAI adapter 会把工具 JSON 文本再次作为消息字符串编码。
    // 这里按真正进入 provider payload 的字符串编码后字节计数，而不是
    // 只计算内层 JSON，以覆盖引号和反斜线转义带来的膨胀。
    const resultBytes = Buffer.byteLength(
      JSON.stringify(text),
      'utf8',
    )
    if (
      resultBytes >
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumToolResultBytes ||
      this.#cumulativeResultBytes + resultBytes >
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
          .maximumCumulativeToolResultBytes
    ) {
      this.#violationCode = 'tool_result_limit_exceeded'
      throw new CountryOutageToolCapacityError(this.#violationCode)
    }
    this.#cumulativeResultBytes += resultBytes
    return {
      content: [{ type: 'text', text }],
      details,
    }
  }
}

function assertReference(reference: string): string {
  const normalized = reference.trim()
  if (!COUNTRY_OUTAGE_REFERENCE.test(normalized)) {
    throw new TypeError('Pi 国家中断工具只接受已有事件的 country_outage 引用')
  }
  return normalized
}

function assertPinnedEvidence(
  reference: string,
  evidence: ReportEvidenceBundle,
): void {
  if (
    evidence.facts.event.event_type !== 'country_outage' ||
    evidence.facts.event.legacy_reference !== reference
  ) {
    throw new Error('固定证据与绑定的 country_outage 引用不一致')
  }
  if (
    evidence.facts.snapshot.collectorId !== 'rrc25' ||
    evidence.facts.scope.collector_id !== 'rrc25' ||
    evidence.facts.scope.collector_count !== 1 ||
    evidence.facts.scope.collector_ids.length !== 1 ||
    evidence.facts.scope.collector_ids[0] !== 'rrc25'
  ) {
    throw new Error('Pi 国家中断工具只允许 RRC25 单一观测源')
  }
}

function compactFacts(facts: CountryOutageFactSet): CompactFactSet {
  const {
    series: _series,
    resourceSeries: _resourceSeries,
    derivedFacts,
    ...compact
  } = facts
  return {
    ...compact,
    derivedFacts: derivedFacts.map(({ operands: _operands, ...fact }) => fact),
  }
}

function isBoundedPinnedAsnPage(
  page: CountryOutageAsnPage,
  snapshot: SnapshotIdentity,
): boolean {
  return (
    page.incident_id === snapshot.incidentId &&
    page.publication_id === snapshot.publicationId &&
    page.revision === snapshot.revision &&
    page.data_through === snapshot.dataThrough &&
    page.is_final === snapshot.isFinal &&
    page.window_start_utc === snapshot.windowStartUtc &&
    page.window_end_utc === snapshot.windowEndUtc &&
    page.cohort_id === snapshot.cohortId &&
    page.page === 1 &&
    Number.isSafeInteger(page.page_size) &&
    page.page_size >= 1 &&
    page.page_size <= 10 &&
    page.items.length <= page.page_size
  )
}

async function waitWithAbort<T>(
  promise: Promise<T>,
  signal: AbortSignal | undefined,
): Promise<T> {
  if (!signal) return promise
  signal.throwIfAborted()
  return await new Promise<T>((resolve, reject) => {
    const onAbort = (): void => reject(signal.reason)
    signal.addEventListener('abort', onAbort, { once: true })
    void promise.then(resolve, reject).finally(() => {
      signal.removeEventListener('abort', onAbort)
    })
  })
}

function toolResolution(
  reference: string,
  facts: CountryOutageFactSet,
): CountryOutageToolResolution {
  return {
    schemaVersion: 'country_outage_tool_resolution_v1',
    reference,
    eventType: 'country_outage',
    incidentId: facts.snapshot.incidentId,
    publicationId: facts.snapshot.publicationId,
    revision: facts.snapshot.revision,
    dataThrough: facts.snapshot.dataThrough,
    isFinal: facts.snapshot.isFinal,
    collectorId: 'rrc25',
    countryCode: facts.event.country_code,
    countryName: facts.event.country_name,
    reportEligible: facts.eligibility.eligible,
    eligibilityReasons: facts.eligibility.reasons,
    capabilities: facts.capabilities,
  }
}

export function createCountryOutageTools(
  options: CountryOutageToolBindingOptions,
): ToolDefinition[] {
  const reference = assertReference(options.reference)
  const executionBudget =
    options.executionBudget ?? new CountryOutageToolExecutionBudget()
  if (options.pinnedEvidence) {
    assertPinnedEvidence(reference, options.pinnedEvidence)
  }

  let pinnedPromise: Promise<PinnedObservation> | undefined
  const getPinned = async (
    signal: AbortSignal | undefined,
  ): Promise<PinnedObservation> => {
    if (options.pinnedEvidence) {
      signal?.throwIfAborted()
      return { facts: options.pinnedEvidence.facts }
    }
    pinnedPromise ??= options.client
      .getObservationBatch(reference, signal)
      .then((batch: ObservationBatch) => ({
        facts: assembleCountryOutageFacts(batch),
        resolution: batch.resolution,
      }))
    return await waitWithAbort(pinnedPromise, signal)
  }

  const resolveTool: ToolDefinition = {
    name: 'country_outage_resolve',
    label: '确认国家中断事件与固定快照',
    description:
      '确认宿主已经绑定的 country_outage 事件、RRC25、publication、revision、能力和正式报告资格。不接受 URL 或其他事件参数。',
    promptSnippet: '确认当前绑定事件及固定报告快照',
    parameters: Type.Object({}, { additionalProperties: false }),
    executionMode: 'sequential',
    async execute(_toolCallId, _params, signal) {
      executionBudget.begin('country_outage_resolve')
      const pinned = await getPinned(signal)
      return executionBudget.result(
        'country_outage_resolve',
        toolResolution(reference, pinned.facts),
      )
    },
  }

  const observationTool: ToolDefinition = {
    name: 'country_outage_get_observation',
    label: '读取固定国家中断观测事实',
    description:
      '读取当前绑定 reference 的 RRC25 固定快照事实、确定性计算、能力状态和证据引用。原始完整时间序列不会进入模型上下文。',
    promptSnippet: '读取当前固定快照的观测事实合同',
    parameters: Type.Object({}, { additionalProperties: false }),
    executionMode: 'sequential',
    async execute(_toolCallId, _params, signal) {
      executionBudget.begin('country_outage_get_observation')
      const { facts } = await getPinned(signal)
      return executionBudget.result<CountryOutageObservationToolResult>(
        'country_outage_get_observation',
        {
          schemaVersion: 'country_outage_tool_observation_v1',
          reference,
          facts: compactFacts(facts),
          omittedSeriesSlotCount: facts.series.length,
          omittedResourceSlotCount: facts.resourceSeries.length,
        },
      )
    },
  }

  const asnParameters = Type.Object(
    {},
    { additionalProperties: false },
  )
  const asnTool: ToolDefinition<typeof asnParameters> = {
    name: 'country_outage_get_asns',
    label: '读取固定快照前十项 ASN 明细',
    description:
      '读取报告编译器已经固定的、按最长连续全不可见时间排序的第一页最多十项 ASN 明细。不能传入参数切换事件、快照、分页或筛选。',
    promptSnippet: '读取当前固定快照的前十项 ASN 明细',
    parameters: asnParameters,
    executionMode: 'sequential',
    async execute(_toolCallId, _params, signal) {
      executionBudget.begin('country_outage_get_asns')
      const { facts } = await getPinned(signal)
      signal?.throwIfAborted()
      const pinnedPage = options.pinnedEvidence?.asnPages[0]
      const page =
        pinnedPage ??
        (await waitWithAbort(
          options.client.getAsns(
            facts.snapshot,
            {
              page: 1,
              pageSize: 10,
              sort: 'longest_fully_invisible_desc',
            } satisfies AsnQuery,
            signal,
          ),
          signal,
        ))
      if (!isBoundedPinnedAsnPage(page, facts.snapshot)) {
        executionBudget.rejectResultLimit()
      }
      return executionBudget.result<CountryOutageAsnToolResult>(
        'country_outage_get_asns',
        {
          schemaVersion: 'country_outage_tool_asns_v1',
          reference,
          snapshot: facts.snapshot,
          page,
        },
      )
    },
  }

  return [resolveTool, observationTool, asnTool]
}
