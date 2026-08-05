import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import {
  mkdirSync,
  mkdtempSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import test from 'node:test'

import type {
  CreateAgentSessionOptions,
  ModelRuntime,
  SessionStats,
  ToolDefinition,
} from '@earendil-works/pi-coding-agent'
import { DefaultPackageManager } from '@earendil-works/pi-coding-agent'
import { join, resolve } from 'node:path'

import type {
  CountryOutageAsnPage,
  CountryOutageFactSet,
  SnapshotIdentity,
} from '../src/domain/contracts.js'
import { FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS } from '../src/formal-runtime-limits.js'
import type { AsnQuery } from '../src/domain/domeye-client.js'
import type { ReportEvidenceBundle } from '../src/report/contracts.js'
import {
  COUNTRY_OUTAGE_TOOL_NAMES,
  CountryOutageToolCapacityError,
  CountryOutageToolExecutionBudget,
  computeCountryOutageSkillBundleSha256,
  type CertifiedPiModelSelection,
  createStaticCountryOutageResourceBundle,
  createCountryOutageTools,
  FormalPiRuntimeError,
  FormalPiRunError,
  loadCountryOutageDependencyRiskException,
  MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
  type FormalPiRunAuditRecord,
  PI_REPORT_SECURITY_PROFILE,
  PiReportNarrator,
  type CountryOutageAsnToolResult,
  type CountryOutageObservationToolResult,
  type CountryOutageToolResolution,
  type PiSessionFactory,
  STATIC_RESOURCE_LOADER_ID,
} from '../src/pi/index.js'

const REFERENCE = 'country_outage/2026-02-27 09:12:32/IR/1/r'

type TestSessionAgent = Awaited<
  ReturnType<PiSessionFactory>
>['session']['agent']

function inertProviderAgent(): TestSessionAgent {
  return {
    streamFunction(model) {
      return providerMessageStream(
        model,
        [{ type: 'text', text: 'unused-provider-result' }],
        'stop',
      )
    },
  }
}

type TestProviderModel = Parameters<
  TestSessionAgent['streamFunction']
>[0]
type TestProviderStream = Awaited<
  ReturnType<TestSessionAgent['streamFunction']>
>

function providerMessageStream(
  model: TestProviderModel,
  content: Array<Record<string, unknown>>,
  stopReason: 'stop' | 'toolUse' = 'toolUse',
): TestProviderStream {
  const message = {
    role: 'assistant' as const,
    content,
    api: model.api,
    provider: model.provider,
    model: model.id,
    responseModel: model.id,
    usage: {
      input: 1,
      output: 1,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 2,
      cost: {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0,
        total: 0,
      },
    },
    stopReason,
    timestamp: Date.now(),
  }
  return {
    async *[Symbol.asyncIterator]() {
      yield {
        type: 'done' as const,
        reason: stopReason,
        message,
      }
    },
    async result() {
      return message
    },
  } as unknown as TestProviderStream
}

async function forwardProviderRequests(
  agent: TestSessionAgent,
  count: number,
  model: TestProviderModel = fakeModel(),
): Promise<void> {
  for (let index = 0; index < count; index += 1) {
    const stream = await agent.streamFunction(model, {
      messages: [],
    })
    for await (const _event of stream) {
      // 与真实 agent loop 一样消费完整 provider stream。
    }
  }
}

function assistantUsage(
  input: number,
  output: number,
  cacheRead = 0,
  cacheWrite = 0,
) {
  return {
    input,
    output,
    cacheRead,
    cacheWrite,
    totalTokens: input + output + cacheRead + cacheWrite,
    cost: {
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      total: 0,
    },
  }
}

function makeFacts(): CountryOutageFactSet {
  const snapshot: SnapshotIdentity = {
    incidentId: 'incident-ir',
    publicationId: 'publication-ir-r7',
    revision: 7,
    dataThrough: '2026-02-28T23:00:00Z',
    isFinal: true,
    cohortId: 'cohort-ir-r7',
    collectorId: 'rrc25',
    windowStartUtc: '2026-02-28T18:05:00Z',
    windowEndUtc: '2026-02-28T23:00:00Z',
  }
  const derivedProvenance = {
    endpoint: 'series' as const,
    schemaVersion: 'country_outage_series_v2',
    pointer: '/series',
    publicationId: snapshot.publicationId,
  }
  return {
    schemaVersion: 'country_outage_report_facts_v1',
    factSetId: 'facts-ir-r7',
    snapshot,
    event: {
      incident_id: snapshot.incidentId,
      legacy_reference: REFERENCE,
      event_type: 'country_outage',
      country_code: 'IR',
      country_name: '伊朗',
      display_name: '伊朗 BGP 路由观测',
    },
    scope: {
      collector_id: 'rrc25',
      collector_ids: ['rrc25'],
      collector_count: 1,
      window_start_utc: snapshot.windowStartUtc,
      window_start_local: '2026-03-01T02:05:00+08:00',
      window_end_utc: snapshot.windowEndUtc,
      window_end_local: '2026-03-01T07:00:00+08:00',
      timezone: 'Asia/Shanghai',
      interval_seconds: 300,
      observation_count: 2,
      expected_observation_count: 2,
      quality_status: 'complete',
      last_observation_at_utc: snapshot.windowEndUtc,
      last_observation_at_local: '2026-03-01T07:00:00+08:00',
    },
    cohort: {
      cohort_id: snapshot.cohortId,
      denominator_policy: 'fixed',
      origin_asn_count: 563,
      prefix_vp_count: 384_767,
    },
    capabilities: {
      visibility: { state: 'available' },
      asn_matrix: { state: 'unavailable' },
    },
    quality: {
      status: 'complete',
      missingSlotCount: 0,
      limitations: ['仅为 RRC25 BGP 控制面观测'],
    },
    eligibility: {
      eligible: true,
      reasons: [],
      missingRequiredFields: [],
      degradedCapabilities: {},
    },
    keyVisibilityPoints: [
      {
        kind: 'start',
        slotIndex: 0,
        observedAtUtc: snapshot.windowStartUtc,
        observedAtLocal: '2026-03-01T02:05:00+08:00',
        visiblePrefixVpCount: 367_215,
        visiblePrefixVpRatio: 0.9544,
        provenance: {
          endpoint: 'series',
          schemaVersion: 'country_outage_series_v2',
          pointer: '/series/0',
          publicationId: snapshot.publicationId,
        },
      },
      {
        kind: 'lowest',
        slotIndex: 1,
        observedAtUtc: snapshot.windowEndUtc,
        observedAtLocal: '2026-03-01T07:00:00+08:00',
        visiblePrefixVpCount: 333_938,
        visiblePrefixVpRatio: 0.8679,
        provenance: {
          endpoint: 'series',
          schemaVersion: 'country_outage_series_v2',
          pointer: '/series/1',
          publicationId: snapshot.publicationId,
        },
      },
      {
        kind: 'end',
        slotIndex: 1,
        observedAtUtc: snapshot.windowEndUtc,
        observedAtLocal: '2026-03-01T07:00:00+08:00',
        visiblePrefixVpCount: 333_938,
        visiblePrefixVpRatio: 0.8679,
        provenance: {
          endpoint: 'series',
          schemaVersion: 'country_outage_series_v2',
          pointer: '/series/1',
          publicationId: snapshot.publicationId,
        },
      },
    ],
    derivedFacts: [
      {
        factId: 'fact-test-start-to-lowest-change',
        metric: 'start_to_lowest_visible_prefix_vp_change',
        label: '起点至最低点可见关系减少量',
        value: 33_277,
        unit: 'Prefix×VP',
        formula: 'start - lowest',
        operands: { start: 367_215, lowest: 333_938 },
        provenance: derivedProvenance,
      },
      {
        factId: 'fact-test-start-to-lowest-loss-ratio',
        metric: 'start_to_lowest_loss_ratio',
        label: '起点至最低点损失占比',
        value: 33_277 / 367_215,
        unit: 'ratio',
        formula: '(start - lowest) / start',
        operands: { start: 367_215, lowest: 333_938 },
        provenance: derivedProvenance,
      },
      {
        factId: 'fact-test-end-gap-from-start',
        metric: 'end_gap_from_start',
        label: '窗口结束相对起点缺口',
        value: 33_277,
        unit: 'Prefix×VP',
        formula: 'start - end',
        operands: { start: 367_215, end: 333_938 },
        provenance: derivedProvenance,
      },
      {
        factId: 'fact-test-recovered-from-lowest',
        metric: 'recovered_from_lowest',
        label: '最低点至结束回升量',
        value: 0,
        unit: 'Prefix×VP',
        formula: 'end - lowest',
        operands: { lowest: 333_938, end: 333_938 },
        provenance: derivedProvenance,
      },
      {
        factId: 'fact-test-recovery-share',
        metric: 'recovery_share_of_prior_loss',
        label: '回升占此前损失比例',
        value: 0,
        unit: 'ratio',
        formula: '(end - lowest) / (start - lowest)',
        operands: {
          start: 367_215,
          lowest: 333_938,
          end: 333_938,
        },
        provenance: derivedProvenance,
      },
    ],
    series: [
      {
        observed_at_utc: snapshot.windowStartUtc,
        observed_at_local: '2026-03-01T02:05:00+08:00',
        slot_state: 'observed',
        visible_prefix_vp_count: 367_215,
        visible_prefix_vp_ratio: 0.9544,
      },
      {
        observed_at_utc: snapshot.windowEndUtc,
        observed_at_local: '2026-03-01T07:00:00+08:00',
        slot_state: 'observed',
        visible_prefix_vp_count: 333_938,
        visible_prefix_vp_ratio: 0.8679,
      },
    ],
    resourceSeries: [],
    metricExtrema: {},
    resourceMetricExtrema: {},
    annotations: [],
    audit: {
      sourceSystem: 'domeye',
      sourceReference: REFERENCE,
      evidenceLevel: 'control_plane',
      algorithmVersion: 'test',
      mappingVersion: 'test',
      verifiedHashes: {},
    },
  }
}

function makeAsnPage(snapshot: SnapshotIdentity): CountryOutageAsnPage {
  return {
    schema_version: 'country_outage_asn_page_v2',
    incident_id: snapshot.incidentId,
    publication_id: snapshot.publicationId,
    revision: snapshot.revision,
    data_through: snapshot.dataThrough,
    is_final: snapshot.isFinal,
    observation_state: 'ready',
    publication_state: 'published',
    window_start_utc: snapshot.windowStartUtc,
    window_end_utc: snapshot.windowEndUtc,
    cohort_id: snapshot.cohortId,
    page: 1,
    page_size: 10,
    page_count: 5,
    total: 42,
    items: [{ asn: 34369, longest_fully_invisible_slots: 60 }],
  }
}

function makeEvidence(): ReportEvidenceBundle {
  return { facts: makeFacts(), asnPages: [] }
}

async function executeTool(
  tool: ToolDefinition,
  parameters: Record<string, unknown> = {},
) {
  return await tool.execute(
    'test-call',
    parameters as never,
    undefined,
    undefined,
    undefined as never,
  )
}

async function executeToolWithSignal(
  tool: ToolDefinition,
  signal: AbortSignal,
  parameters: Record<string, unknown> = {},
) {
  return await tool.execute(
    'test-call',
    parameters as never,
    signal,
    undefined,
    undefined as never,
  )
}

test('只注册三个国家中断只读工具，参数不能切换 reference 或 URL', async () => {
  const evidence = makeEvidence()
  const client = {
    async getObservationBatch() {
      throw new Error('固定证据模式不应重新读取观测批次')
    },
    async getAsns(snapshot: SnapshotIdentity) {
      return makeAsnPage(snapshot)
    },
  }
  const tools = createCountryOutageTools({
    reference: REFERENCE,
    client,
    pinnedEvidence: evidence,
  })

  assert.deepEqual(
    tools.map((tool) => tool.name),
    COUNTRY_OUTAGE_TOOL_NAMES,
  )
  for (const tool of tools) {
    const schema = tool.parameters as unknown as {
      additionalProperties?: boolean
      properties?: Record<string, unknown>
    }
    assert.equal(schema.additionalProperties, false)
    assert.equal('reference' in (schema.properties ?? {}), false)
    assert.equal('url' in (schema.properties ?? {}), false)
    assert.equal('incidentId' in (schema.properties ?? {}), false)
    assert.equal('publicationId' in (schema.properties ?? {}), false)
  }

  const resolution = (await executeTool(tools[0]!, {
    reference: 'country_outage/2099-01-01 00:00:00/US/1/r',
    url: 'https://example.invalid',
  })).details as CountryOutageToolResolution
  assert.equal(resolution.reference, REFERENCE)
  assert.equal(resolution.publicationId, evidence.facts.snapshot.publicationId)
  assert.equal(resolution.revision, evidence.facts.snapshot.revision)
  assert.equal(resolution.collectorId, 'rrc25')

  assert.throws(
    () =>
      createCountryOutageTools({
        reference: 'https://example.invalid/events/detail',
        client,
      }),
    /只接受已有事件的 country_outage 引用/,
  )
})

test('观测工具返回紧凑事实，ASN 工具复用编译器固定的前十项明细', async () => {
  const evidence = makeEvidence()
  evidence.asnPages = [makeAsnPage(evidence.facts.snapshot)]
  evidence.facts.derivedFacts = [
    {
      factId: 'fact_model_visible_value_only',
      metric: 'start_to_lowest_visible_prefix_vp_change',
      label: '起点至最低点可见关系减少量',
      value: 33_277,
      unit: 'Prefix×VP',
      formula: 'start - lowest',
      operands: {
        start: 367_215,
        lowest: 333_938,
      },
      provenance: {
        endpoint: 'series',
        schemaVersion: 'country_outage_series_v2',
        pointer: '/series',
        publicationId: evidence.facts.snapshot.publicationId,
      },
    },
  ]
  const snapshots: SnapshotIdentity[] = []
  const queries: AsnQuery[] = []
  const client = {
    async getObservationBatch() {
      throw new Error('固定证据模式不应重新读取观测批次')
    },
    async getAsns(snapshot: SnapshotIdentity, query: AsnQuery) {
      snapshots.push(snapshot)
      queries.push(query)
      return makeAsnPage(snapshot)
    },
  }
  const tools = createCountryOutageTools({
    reference: REFERENCE,
    client,
    pinnedEvidence: evidence,
  })

  const observation = (await executeTool(tools[1]!))
    .details as CountryOutageObservationToolResult
  assert.equal(observation.reference, REFERENCE)
  assert.equal(observation.omittedSeriesSlotCount, 2)
  assert.equal('series' in observation.facts, false)
  assert.equal('resourceSeries' in observation.facts, false)
  assert.equal(observation.facts.derivedFacts.length, 1)
  assert.equal(
    'operands' in (observation.facts.derivedFacts[0] ?? {}),
    false,
  )
  assert.equal(
    observation.facts.derivedFacts[0]?.value,
    33_277,
  )

  const asns = (await executeTool(tools[2]!))
    .details as CountryOutageAsnToolResult
  assert.deepEqual(snapshots, [])
  assert.deepEqual(asns.snapshot, evidence.facts.snapshot)
  assert.equal(asns.page.publication_id, evidence.facts.snapshot.publicationId)
  assert.equal(asns.page, evidence.asnPages[0])
  assert.deepEqual(queries, [])
})

test('未预取 ASN 明细时工具只读取固定第一页十项', async () => {
  const evidence = makeEvidence()
  const queries: AsnQuery[] = []
  const tools = createCountryOutageTools({
    reference: REFERENCE,
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应重新读取观测批次')
      },
      async getAsns(
        snapshot: SnapshotIdentity,
        query: AsnQuery,
      ) {
        assert.deepEqual(snapshot, evidence.facts.snapshot)
        queries.push(query)
        return makeAsnPage(snapshot)
      },
    },
    pinnedEvidence: evidence,
  })

  await executeTool(tools[2]!)
  assert.deepEqual(queries, [
    {
      page: 1,
      pageSize: 10,
      sort: 'longest_fully_invisible_desc',
    },
  ])
})

test('ASN 服务忽略固定分页并返回超出十项时失败关闭', async () => {
  const evidence = makeEvidence()
  const oversizedPage = makeAsnPage(evidence.facts.snapshot)
  oversizedPage.page_size = 11
  oversizedPage.items = Array.from({ length: 11 }, (_, index) => ({
    asn: 34_369 + index,
    longest_fully_invisible_slots: 60,
  }))
  const executionBudget = new CountryOutageToolExecutionBudget()
  const tools = createCountryOutageTools({
    reference: REFERENCE,
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应重新读取观测批次')
      },
      async getAsns() {
        return oversizedPage
      },
    },
    pinnedEvidence: evidence,
    executionBudget,
  })

  await assert.rejects(
    executeTool(tools[2]!),
    (error: unknown) =>
      error instanceof CountryOutageToolCapacityError &&
      error.code === 'tool_result_limit_exceeded',
  )
  assert.equal(
    executionBudget.violationCode,
    'tool_result_limit_exceeded',
  )
})

test('Pi 工具取消信号传入底层观测批次与 ASN 读取', async () => {
  let batchSignal: AbortSignal | undefined
  let batchReads = 0
  let markBatchStarted!: () => void
  const batchStarted = new Promise<void>((resolve) => {
    markBatchStarted = resolve
  })
  const batchTools = createCountryOutageTools({
    reference: REFERENCE,
    client: {
      async getObservationBatch(
        _reference: string,
        signal?: AbortSignal,
      ) {
        assert.ok(signal)
        batchReads += 1
        batchSignal = signal
        markBatchStarted()
        if (!signal.aborted) {
          await new Promise<void>((resolve) => {
            signal.addEventListener('abort', () => resolve(), {
              once: true,
            })
          })
        }
        signal.throwIfAborted()
        throw new Error('取消后不应返回观测批次')
      },
      async getAsns() {
        throw new Error('本分支不应读取 ASN')
      },
    },
  })
  const batchController = new AbortController()
  const pendingBatch = executeToolWithSignal(
    batchTools[0]!,
    batchController.signal,
  )
  await batchStarted
  batchController.abort()
  await assert.rejects(
    pendingBatch,
    (error: unknown) =>
      error instanceof DOMException && error.name === 'AbortError',
  )
  assert.equal(batchSignal, batchController.signal)
  assert.equal(batchReads, 1)

  let asnSignal: AbortSignal | undefined
  let asnReads = 0
  let markAsnStarted!: () => void
  const asnStarted = new Promise<void>((resolve) => {
    markAsnStarted = resolve
  })
  const asnTools = createCountryOutageTools({
    reference: REFERENCE,
    pinnedEvidence: makeEvidence(),
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应读取观测批次')
      },
      async getAsns(
        _snapshot: SnapshotIdentity,
        _query?: AsnQuery,
        signal?: AbortSignal,
      ) {
        assert.ok(signal)
        asnReads += 1
        asnSignal = signal
        markAsnStarted()
        if (!signal.aborted) {
          await new Promise<void>((resolve) => {
            signal.addEventListener('abort', () => resolve(), {
              once: true,
            })
          })
        }
        signal.throwIfAborted()
        throw new Error('取消后不应返回 ASN 分页')
      },
    },
  })
  const asnController = new AbortController()
  const pendingAsns = executeToolWithSignal(
    asnTools[2]!,
    asnController.signal,
  )
  await asnStarted
  asnController.abort()
  await assert.rejects(
    pendingAsns,
    (error: unknown) =>
      error instanceof DOMException && error.name === 'AbortError',
  )
  assert.equal(asnSignal, asnController.signal)
  assert.equal(asnReads, 1)
})

test('国家中断工具预算硬限制总次数、单工具次数和结果字节数', () => {
  const executionBudget = new CountryOutageToolExecutionBudget()
  executionBudget.begin('country_outage_resolve')
  executionBudget.begin('country_outage_get_observation')
  executionBudget.begin('country_outage_get_asns')
  assert.equal(executionBudget.executionCount, 3)
  assert.throws(
    () => executionBudget.begin('country_outage_get_asns'),
    (error: unknown) =>
      error instanceof CountryOutageToolCapacityError &&
      error.code === 'tool_execution_limit_exceeded',
  )
  assert.equal(
    executionBudget.violationCode,
    'tool_execution_limit_exceeded',
  )

  const perToolBudget = new CountryOutageToolExecutionBudget()
  perToolBudget.begin('country_outage_resolve')
  assert.throws(
    () => perToolBudget.begin('country_outage_resolve'),
    (error: unknown) =>
      error instanceof CountryOutageToolCapacityError &&
      error.code === 'tool_execution_limit_exceeded',
  )

  const resultBudget = new CountryOutageToolExecutionBudget()
  resultBudget.begin('country_outage_get_observation')
  assert.throws(
    () =>
      resultBudget.result('country_outage_get_observation', {
        oversized: 'x'.repeat(24_576),
      }),
    (error: unknown) =>
      error instanceof CountryOutageToolCapacityError &&
      error.code === 'tool_result_limit_exceeded',
  )
  assert.equal(
    resultBudget.violationCode,
    'tool_result_limit_exceeded',
  )

  const cumulativeBudget = new CountryOutageToolExecutionBudget()
  cumulativeBudget.begin('country_outage_resolve')
  cumulativeBudget.result('country_outage_resolve', {
    chunk: 'x'.repeat(18_000),
  })
  cumulativeBudget.begin('country_outage_get_observation')
  assert.throws(
    () =>
      cumulativeBudget.result('country_outage_get_observation', {
        chunk: 'x'.repeat(18_864),
      }),
    (error: unknown) =>
      error instanceof CountryOutageToolCapacityError &&
      error.code === 'tool_result_limit_exceeded',
  )
  assert.equal(cumulativeBudget.cumulativeResultBytes, 18_018)

  const frozenBudget = new CountryOutageToolExecutionBudget()
  frozenBudget.begin('country_outage_resolve')
  frozenBudget.freeze()
  assert.throws(
    () => frozenBudget.begin('country_outage_get_observation'),
    (error: unknown) =>
      error instanceof CountryOutageToolCapacityError &&
      error.code === 'tool_execution_limit_exceeded',
  )
  assert.equal(frozenBudget.executionCount, 1)
})

test('PiReportNarrator 即使模型吞掉工具超限错误也拒绝发布', async () => {
  const audits: FormalPiRunAuditRecord[] = []
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应重新读取')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    sessionFactory: async (options) => {
      const resolveTool = options.customTools?.find(
        (tool) => tool.name === 'country_outage_resolve',
      )
      assert.ok(resolveTool)
      return {
        session: {
          agent: inertProviderAgent(),
          messages: validFormalMessages(),
          async prompt() {
            await executeTool(resolveTool)
            try {
              await executeTool(resolveTool)
            } catch (error) {
              assert.ok(error instanceof CountryOutageToolCapacityError)
            }
          },
          async abort() {},
          getSessionStats() {
            return fakeSessionStats()
          },
          dispose() {},
        },
      }
    },
  })

  await assert.rejects(
    narrator.generate({
      reference: REFERENCE,
      evidence: makeEvidence(),
    }),
    (error: unknown) =>
      error instanceof FormalPiRunError &&
      error.code === 'tool_execution_limit_exceeded',
  )
  assert.equal(
    audits[0]?.rejectionCode,
    'tool_execution_limit_exceeded',
  )
})

test('provider context 字节门在 900000 放行并在 900001 于上游前拒绝', async (context) => {
  const model = {
    ...fakeModel(),
    api: 'openai-completions',
    contextWindow: 1_000_000,
  } as NonNullable<CreateAgentSessionOptions['model']>

  function exactContextBytes(targetBytes: number) {
    const empty = { messages: [], systemPrompt: '' }
    const framingBytes = Buffer.byteLength(
      JSON.stringify(empty),
      'utf8',
    )
    assert.ok(targetBytes >= framingBytes)
    const value = {
      messages: [],
      systemPrompt: 'x'.repeat(targetBytes - framingBytes),
    }
    assert.equal(
      Buffer.byteLength(JSON.stringify(value), 'utf8'),
      targetBytes,
    )
    return value
  }

  for (const item of [
    {
      name: '900000 bytes',
      bytes: 900_000,
      expectedUpstreamCalls: 3,
      expectedRejection: undefined,
    },
    {
      name: '900001 bytes',
      bytes: 900_001,
      expectedUpstreamCalls: 0,
      expectedRejection: 'provider_context_limit_exceeded',
    },
  ] as const) {
    await context.test(item.name, async () => {
      let upstreamCalls = 0
      const agent: TestSessionAgent = {
        streamFunction(streamModel) {
          upstreamCalls += 1
          return providerMessageStream(
            streamModel,
            [{ type: 'text', text: 'unused' }],
            'stop',
          )
        },
      }
      const audits: FormalPiRunAuditRecord[] = []
      const narrator = new PiReportNarrator({
        client: {
          async getObservationBatch() {
            throw new Error('固定证据模式不应重新读取')
          },
          async getAsns(snapshot: SnapshotIdentity) {
            return makeAsnPage(snapshot)
          },
        },
        model,
        modelRuntime: fakeModelRuntime(),
        certification: fakeCertification(),
        dependencyRiskException: fakeDependencyRiskException(),
        auditSink(record) {
          audits.push(record)
        },
        sessionFactory: async () => ({
          session: {
            agent,
            messages: validFormalMessages(),
            async prompt() {
              for (let index = 0; index < 3; index += 1) {
                const stream = await agent.streamFunction(
                  model,
                  exactContextBytes(item.bytes),
                )
                for await (const _event of stream) {
                  // 消费与真实 agent loop 相同的流协议。
                }
              }
            },
            async abort() {},
            getSessionStats: () => fakeSessionStats(),
            dispose() {},
          },
        }),
      })

      if (item.expectedRejection) {
        await assert.rejects(
          narrator.generate({
            reference: REFERENCE,
            evidence: makeEvidence(),
          }),
          (error: unknown) =>
            error instanceof FormalPiRunError &&
            error.code === item.expectedRejection,
        )
        assert.equal(
          audits[0]?.rejectionCode,
          item.expectedRejection,
        )
      } else {
        await narrator.generate({
          reference: REFERENCE,
          evidence: makeEvidence(),
        })
        assert.equal(audits[0]?.outcome, 'accepted')
      }
      assert.equal(upstreamCalls, item.expectedUpstreamCalls)
    })
  }
})

test('最终 provider payload 在既有 hook 后执行 59904-byte 发送前硬门', async (context) => {
  function exactPayloadBytes(targetBytes: number) {
    const empty = {
      model: 'deepseek-v4-flash',
      messages: [],
      stream: true,
      padding: '',
    }
    const framingBytes = Buffer.byteLength(
      JSON.stringify(empty),
      'utf8',
    )
    assert.ok(targetBytes >= framingBytes)
    const payload = {
      ...empty,
      padding: 'x'.repeat(targetBytes - framingBytes),
    }
    assert.equal(
      Buffer.byteLength(JSON.stringify(payload), 'utf8'),
      targetBytes,
    )
    return payload
  }

  assert.equal(
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumProviderPayloadBytes
      + FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.providerFramingTokenReserve,
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumContextInputTokens,
  )

  for (const item of [
    {
      name: '边界 payload 放行',
      payloadBytes:
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumProviderPayloadBytes,
      existingHookExpansionBytes: 0,
      expectedNetworkCalls: 3,
      expectedRejection: undefined,
    },
    {
      name: '边界加一在网络前拒绝',
      payloadBytes:
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumProviderPayloadBytes + 1,
      existingHookExpansionBytes: 0,
      expectedNetworkCalls: 0,
      expectedRejection: 'provider_context_limit_exceeded',
    },
    {
      name: '既有 hook 扩大后的最终 payload 仍在网络前拒绝',
      payloadBytes:
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumProviderPayloadBytes,
      existingHookExpansionBytes: 1,
      expectedNetworkCalls: 0,
      expectedRejection: 'provider_context_limit_exceeded',
    },
  ] as const) {
    await context.test(item.name, async () => {
      let networkCalls = 0
      const payload = exactPayloadBytes(item.payloadBytes)
      const agent: TestSessionAgent = {
        streamFunction(streamModel, _streamContext, streamOptions) {
          return {
            async *[Symbol.asyncIterator]() {
              const finalPayload = await streamOptions?.onPayload?.(
                payload,
                streamModel,
              )
              assert.ok(finalPayload)
              networkCalls += 1
            },
            async result() {
              return {
                stopReason: 'stop',
              }
            },
          } as unknown as TestProviderStream
        },
      }
      const audits: FormalPiRunAuditRecord[] = []
      const narrator = new PiReportNarrator({
        client: {
          async getObservationBatch() {
            throw new Error('固定证据模式不应重新读取')
          },
          async getAsns(snapshot: SnapshotIdentity) {
            return makeAsnPage(snapshot)
          },
        },
        model: fakeModel(),
        modelRuntime: fakeModelRuntime(),
        certification: fakeCertification(),
        dependencyRiskException: fakeDependencyRiskException(),
        auditSink(record) {
          audits.push(record)
        },
        sessionFactory: async () => ({
          session: {
            agent,
            messages: validFormalMessages(),
            async prompt() {
              for (let index = 0; index < 3; index += 1) {
                const stream = await agent.streamFunction(
                  fakeModel(),
                  { messages: [] },
                  item.existingHookExpansionBytes === 0
                    ? undefined
                    : {
                        onPayload(value) {
                          assert.ok(
                            value && typeof value === 'object',
                          )
                          return {
                            ...(value as Record<string, unknown>),
                            padding:
                              `${String(
                                (value as Record<string, unknown>)
                                  .padding ?? '',
                              )}${'x'.repeat(
                                item.existingHookExpansionBytes,
                              )}`,
                          }
                        },
                      },
                )
                for await (const _event of stream) {
                  // 模拟 adapter 在 onPayload 通过后才触达网络。
                }
              }
            },
            async abort() {},
            getSessionStats: () => fakeSessionStats(),
            dispose() {},
          },
        }),
      })

      if (item.expectedRejection) {
        await assert.rejects(
          narrator.generate({
            reference: REFERENCE,
            evidence: makeEvidence(),
          }),
          (error: unknown) =>
            error instanceof FormalPiRunError &&
            error.code === item.expectedRejection,
        )
        assert.equal(
          audits[0]?.rejectionCode,
          item.expectedRejection,
        )
      } else {
        await narrator.generate({
          reference: REFERENCE,
          evidence: makeEvidence(),
        })
        assert.equal(audits[0]?.outcome, 'accepted')
      }
      assert.equal(networkCalls, item.expectedNetworkCalls)
    })
  }
})

test('provider 下一轮只保留工具骨架并移除冗长 thinking 与被拒绝草稿', async () => {
  const sensitiveThinking =
    `SENSITIVE_TOOL_THINKING_${'x'.repeat(20_000)}`
  const sensitiveToolText =
    `SENSITIVE_TOOL_TEXT_${'y'.repeat(10_000)}`
  const sensitiveRejectedDraft =
    `SENSITIVE_REJECTED_DRAFT_${'z'.repeat(10_000)}`
  const verboseContext = {
    messages: [
      {
        role: 'assistant',
        provider: 'acceptance-provider',
        model: 'acceptance-model',
        responseModel: 'fixed-revision',
        stopReason: 'toolUse',
        usage: assistantUsage(400, 100),
        content: [
          {
            type: 'thinking',
            thinking: sensitiveThinking,
          },
          {
            type: 'text',
            text: sensitiveToolText,
          },
          {
            type: 'toolCall',
            id: 'call-resolve',
            name: 'country_outage_resolve',
            arguments: {
              url: 'https://example.invalid/模型自造参数',
            },
          },
        ],
      },
      {
        role: 'toolResult',
        toolCallId: 'call-resolve',
        toolName: 'country_outage_resolve',
        content: [{ type: 'text', text: '固定工具结果必须保留' }],
        isError: false,
      },
      {
        role: 'assistant',
        provider: 'acceptance-provider',
        model: 'acceptance-model',
        responseModel: 'fixed-revision',
        stopReason: 'stop',
        usage: assistantUsage(400, 100),
        content: [
          {
            type: 'text',
            text: sensitiveRejectedDraft,
          },
        ],
      },
      {
        role: 'user',
        content: [
          {
            type: 'text',
            text: '只保留固定修订诊断。',
          },
        ],
      },
    ],
  } as unknown as Parameters<
    TestSessionAgent['streamFunction']
  >[1]
  const capturedContexts: Array<
    Parameters<TestSessionAgent['streamFunction']>[1]
  > = []
  const agent: TestSessionAgent = {
    streamFunction(model, context) {
      capturedContexts.push(context)
      return providerMessageStream(
        model,
        [{ type: 'text', text: 'unused' }],
        'stop',
      )
    },
  }
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应重新读取')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink() {},
    sessionFactory: async () => ({
      session: {
        agent,
        messages: validFormalMessages(),
        async prompt() {
          for (let index = 0; index < 3; index += 1) {
            const stream = await agent.streamFunction(
              fakeModel(),
              verboseContext,
            )
            for await (const _event of stream) {
              // 消费完整本地 provider stream。
            }
          }
        },
        async abort() {},
        getSessionStats() {
          return fakeSessionStats()
        },
        dispose() {},
      },
    }),
  })

  await narrator.generate({
    reference: REFERENCE,
    evidence: makeEvidence(),
  })

  assert.equal(capturedContexts.length, 3)
  for (const context of capturedContexts) {
    const serialized = JSON.stringify(context)
    assert.doesNotMatch(serialized, /SENSITIVE_TOOL_THINKING_/)
    assert.doesNotMatch(serialized, /SENSITIVE_TOOL_TEXT_/)
    assert.doesNotMatch(serialized, /SENSITIVE_REJECTED_DRAFT_/)
    assert.doesNotMatch(serialized, /example\.invalid/)
    assert.match(serialized, /固定工具结果必须保留/)
    assert.match(
      serialized,
      /上一份语言槽 JSON 已由本地机器校验拒绝/,
    )
    const messages = (
      context as unknown as {
        messages: Array<Record<string, unknown>>
      }
    ).messages
    const first = messages[0] as {
      content: Array<Record<string, unknown>>
    }
    assert.deepEqual(first.content, [
      {
        type: 'toolCall',
        id: 'call-resolve',
        name: 'country_outage_resolve',
        arguments: {},
      },
    ])
  }
})

test('真实 Pi agent loop 在工具错误后继续时，第六轮于上游前被 provider gate 截断', async () => {
  const model = {
    provider: 'acceptance-provider',
    id: 'acceptance-model',
    name: 'Acceptance Model',
    api: 'openai-completions',
    baseUrl: 'https://provider.invalid',
    reasoning: false,
    input: ['text'],
    cost: {
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
    },
    contextWindow: 1_000_000,
    maxTokens: 16_384,
  } as NonNullable<CreateAgentSessionOptions['model']>
  let upstreamCalls = 0
  const runtime = {
    hasConfiguredAuth() {
      return true
    },
    async checkAuth() {
      return { configured: true }
    },
    isUsingOAuth() {
      return false
    },
    streamSimple(streamModel: TestProviderModel) {
      upstreamCalls += 1
      return providerMessageStream(streamModel, [
        {
          type: 'toolCall',
          id: `repeated-tool-${upstreamCalls}`,
          name: 'country_outage_resolve',
          arguments: {},
        },
      ])
    },
  } as unknown as ModelRuntime
  const audits: FormalPiRunAuditRecord[] = []
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应重新读取')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model,
    modelRuntime: runtime,
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
  })

  let rejection: unknown
  try {
    await narrator.generate({
      reference: REFERENCE,
      evidence: makeEvidence(),
    })
  } catch (error) {
    rejection = error
  }
  assert.equal(upstreamCalls, 5)
  assert.equal(
    rejection instanceof FormalPiRunError
      ? rejection.code
      : String(rejection),
    'provider_request_limit_exceeded',
  )
  assert.equal(
    audits[0]?.rejectionCode,
    'provider_request_limit_exceeded',
  )
})

const VALID_LANGUAGE_SLOT_TEXT = JSON.stringify({
  schemaVersion: 'country_outage_language_slots_v1',
  slots: [
    {
      id: 'scope.denominator_explanation',
      text:
        'Prefix×VP 描述前缀与固定观测点之间的可见关系；它并非唯一前缀，也不能换算为用户或业务数量。',
    },
    {
      id: 'assessment.evidence_boundary',
      text:
        '本报告只支持 BGP 控制面可见性描述，不能据此判断全国数据面状态，也无法认定用户或业务影响、事件原因和责任主体。',
    },
  ],
})

function semanticFailureDraftText(): string {
  const bundle = JSON.parse(VALID_LANGUAGE_SLOT_TEXT) as {
    schemaVersion: string
    slots: Array<{ id: string; text: string }>
  }
  bundle.slots[0]!.text =
    'Prefix×VP 在本次事件中下降了 987654321 条，并导致全国用户业务中断。'
  return JSON.stringify(bundle)
}

function fakeModel(): NonNullable<CreateAgentSessionOptions['model']> {
  return {
    provider: 'acceptance-provider',
    id: 'acceptance-model',
    contextWindow: 128_000,
    maxTokens: 16_384,
  } as NonNullable<CreateAgentSessionOptions['model']>
}

function fakeModelRuntime(): ModelRuntime {
  return {} as ModelRuntime
}

function fakeCertification(): CertifiedPiModelSelection {
  return {
    registryVersion: 'test-certified-models-v1',
    profile: {
      id: 'primary-v1',
      status: 'certified',
      provider: 'acceptance-provider',
      model: 'acceptance-model',
      modelVersion: 'fixed-revision',
      expectedResponseModel: 'fixed-revision',
      thinkingLevel: 'off',
      piVersion: '0.82.1',
      certificationEvidenceId: 'evidence:test-model-certification',
      certifiedAt: '2026-07-28T15:00:00Z',
      modelRevisionKind: 'mutable_alias',
      immutableRevisionAvailable: false,
      limitation: MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
      certificationValidUntil: '2099-01-01T00:00:00Z',
      certifiedScenarioSetId:
        'country-outage-pi-runtime-scenarios-test-v1',
      certifiedInputScope:
        'legal_country_outage_rrc25_test_v1',
    },
  }
}

function fakeDependencyRiskException() {
  return loadCountryOutageDependencyRiskException({
    now: new Date('2026-08-01T00:00:00Z'),
  })
}

function fakeSessionStats(
  overrides: Partial<SessionStats> = {},
): SessionStats {
  return {
    sessionFile: undefined,
    sessionId: 'session-not-audited',
    userMessages: 1,
    assistantMessages: 3,
    toolCalls: 2,
    toolResults: 2,
    totalMessages: 6,
    tokens: {
      input: 1200,
      output: 300,
      cacheRead: 100,
      cacheWrite: 0,
      total: 1600,
    },
    cost: 0.0042,
    ...overrides,
  }
}

function validFormalMessages(
  draftText = VALID_LANGUAGE_SLOT_TEXT,
): unknown[] {
  return [
    {
      role: 'assistant',
      provider: 'acceptance-provider',
      model: 'acceptance-model',
      responseModel: 'fixed-revision',
      stopReason: 'toolUse',
      usage: assistantUsage(400, 100),
      content: [
        {
          type: 'toolCall',
          name: 'country_outage_resolve',
          arguments: { ignoredByAudit: 'secret-tool-argument' },
        },
      ],
    },
    {
      role: 'toolResult',
      toolName: 'country_outage_resolve',
      content: [{ type: 'text', text: 'secret-tool-result' }],
    },
    {
      role: 'assistant',
      provider: 'acceptance-provider',
      model: 'acceptance-model',
      responseModel: 'fixed-revision',
      stopReason: 'toolUse',
      usage: assistantUsage(400, 100),
      content: [
        {
          type: 'toolCall',
          name: 'country_outage_get_observation',
          arguments: {},
        },
      ],
    },
    {
      role: 'toolResult',
      toolName: 'country_outage_get_observation',
      content: [{ type: 'text', text: 'another-secret-tool-result' }],
    },
    {
      role: 'assistant',
      provider: 'acceptance-provider',
      model: 'acceptance-model',
      responseModel: 'fixed-revision',
      stopReason: 'stop',
      usage: assistantUsage(400, 100, 100),
      content: [{ type: 'text', text: draftText }],
    },
  ]
}

function repairAssistantMessage(draftText: string): unknown {
  return {
    role: 'assistant',
    provider: 'acceptance-provider',
    model: 'acceptance-model',
    responseModel: 'fixed-revision',
    stopReason: 'stop',
    usage: assistantUsage(200, 100, 100),
    content: [{ type: 'text', text: draftText }],
  }
}

function asnAssistantAndResult(callId: string): unknown[] {
  return [
    {
      role: 'assistant',
      provider: 'acceptance-provider',
      model: 'acceptance-model',
      responseModel: 'fixed-revision',
      stopReason: 'toolUse',
      usage: assistantUsage(200, 100, 100),
      content: [
        {
          type: 'toolCall',
          name: 'country_outage_get_asns',
          arguments: { page: callId },
        },
      ],
    },
    {
      role: 'toolResult',
      toolName: 'country_outage_get_asns',
      content: [{ type: 'text', text: 'fixed-asn-result' }],
    },
  ]
}

function formalMessagesWithAsnRequests(
  draftText: string,
  asnRequestCount: 1 | 2,
): unknown[] {
  const messages = validFormalMessages(draftText)
  messages.splice(
    messages.length - 1,
    0,
    ...asnAssistantAndResult('first'),
    ...(asnRequestCount === 2
      ? asnAssistantAndResult('second')
      : []),
  )
  return messages
}

function formalMessagesWithFiveProviderRequests(
  draftText: string,
): unknown[] {
  const messages = formalMessagesWithAsnRequests(draftText, 1)
  messages.splice(messages.length - 1, 0, {
    role: 'assistant',
    provider: 'acceptance-provider',
    model: 'acceptance-model',
    responseModel: 'fixed-revision',
    stopReason: 'toolUse',
    usage: assistantUsage(200, 100, 100),
    content: [],
  })
  return messages
}

function sessionStatsWithAsnRequests(
  asnRequestCount: 1 | 2,
  repaired = false,
): SessionStats {
  const extraAssistantCount = asnRequestCount + (repaired ? 1 : 0)
  const input = 1_200 + asnRequestCount * 200 + (repaired ? 200 : 0)
  const output = 300 + extraAssistantCount * 100
  const cacheRead =
    100 + asnRequestCount * 100 + (repaired ? 100 : 0)
  return fakeSessionStats({
    userMessages: repaired ? 2 : 1,
    assistantMessages: 3 + extraAssistantCount,
    toolCalls: 2 + asnRequestCount,
    toolResults: 2 + asnRequestCount,
    totalMessages:
      6 + asnRequestCount * 2 + (repaired ? 2 : 0),
    tokens: {
      input,
      output,
      cacheRead,
      cacheWrite: 0,
      total: input + output + cacheRead,
    },
  })
}

function sessionStatsWithFiveProviderRequests(): SessionStats {
  const base = sessionStatsWithAsnRequests(1)
  const input = base.tokens.input + 200
  const output = base.tokens.output + 100
  const cacheRead = base.tokens.cacheRead + 100
  return {
    ...base,
    assistantMessages: base.assistantMessages + 1,
    totalMessages: base.totalMessages + 1,
    tokens: {
      input,
      output,
      cacheRead,
      cacheWrite: base.tokens.cacheWrite,
      total: input + output + cacheRead + base.tokens.cacheWrite,
    },
  }
}

test('正式静态 ResourceLoader 不触达 Pi PackageManager.resolve', async () => {
  const originalResolve = DefaultPackageManager.prototype.resolve
  let packageResolveCalls = 0
  DefaultPackageManager.prototype.resolve = async function (..._arguments_) {
    packageResolveCalls += 1
    throw new Error('正式路径不得调用 PackageManager.resolve')
  }
  try {
    const bundle = createStaticCountryOutageResourceBundle(
      resolve(
        process.cwd(),
        'resources/skills/country-outage-report/SKILL.md',
      ),
      '固定系统提示',
    )
    await bundle.loader.reload()
    assert.equal(packageResolveCalls, 0)
    assert.equal(bundle.resourceLoaderId, STATIC_RESOURCE_LOADER_ID)
    assert.deepEqual(
      bundle.loader.getSkills().skills.map((skill) => skill.name),
      ['country-outage-report'],
    )
    assert.equal(bundle.loader.getExtensions().extensions.length, 0)
    assert.equal(bundle.loader.getPrompts().prompts.length, 0)
    assert.equal(bundle.loader.getThemes().themes.length, 0)
    assert.equal(bundle.loader.getAgentsFiles().agentsFiles.length, 0)
    const systemPrompt = bundle.loader.getSystemPrompt() ?? ''
    assert.match(
      systemPrompt,
      /trusted_country_outage_project_knowledge/,
    )
    assert.match(systemPrompt, /country_outage_language_slots_v1/)
    assert.match(systemPrompt, /scope\.denominator_explanation/)
    assert.match(systemPrompt, /assessment\.evidence_boundary/)
    assert.match(systemPrompt, /address_families\.impact_boundary/)
    assert.match(systemPrompt, /updates\.causality_boundary/)
    assert.match(systemPrompt, /resources\.resource_boundary/)
    assert.match(
      systemPrompt,
      /完整 `country_outage_report_draft_v1` 由宿主/,
    )
    assert.match(
      systemPrompt,
      /模型不得输出或改写报告结构、标题、摘要、关键数字、章节、方向判断/,
    )
    assert.match(
      systemPrompt,
      /根对象只能包含 `schemaVersion` 和 `slots`/,
    )
    assert.match(
      systemPrompt,
      /槽 ID、顺序和数量必须与当前请求逐项一致/,
    )
    assert.match(
      systemPrompt,
      /不得部分采用槽、逐槽回退或静默发布/,
    )
    assert.doesNotMatch(
      systemPrompt,
      /"title": "国家或地区 BGP 路由可见性观测报告"/,
    )
    assert.doesNotMatch(
      systemPrompt,
      /367,215|95\.44%|伊朗 BGP 路由可见性观测报告/,
    )
    assert.throws(
      () =>
        bundle.loader.extendResources({
          skillPaths: [
            {
              path: '/tmp/untrusted/SKILL.md',
              metadata: {
                source: 'untrusted',
                scope: 'temporary',
                origin: 'top-level',
              },
            },
          ],
        }),
      /禁止运行时扩展资源/,
    )
  } finally {
    DefaultPackageManager.prototype.resolve = originalResolve
  }
})

test('固定 Skill 三个资源任一内容变化都会改变共同 bundle 摘要', () => {
  const temporaryDirectory = realpathSync(
    mkdtempSync(join(tmpdir(), 'domeye-skill-bundle-digest-')),
  )
  const referencesDirectory = join(temporaryDirectory, 'references')
  mkdirSync(referencesDirectory)
  const skillPath = join(temporaryDirectory, 'SKILL.md')
  const metricsPath = join(
    referencesDirectory,
    'metrics-and-boundaries.md',
  )
  const contractPath = join(
    referencesDirectory,
    'report-output-contract.md',
  )
  const skillText = [
    '---',
    'name: country-outage-report',
    'description: 固定国家中断报告测试 Skill',
    '---',
    '固定 Skill 正文。',
    '',
  ].join('\n')
  const metricsText = '固定指标边界\n'
  const contractText = '固定输出合同\n'
  writeFileSync(skillPath, skillText)
  writeFileSync(metricsPath, metricsText)
  writeFileSync(contractPath, contractText)

  try {
    const baseline =
      computeCountryOutageSkillBundleSha256(skillPath)
    const mutations = [
      [skillPath, `${skillText}变更\n`, skillText],
      [metricsPath, `${metricsText}变更\n`, metricsText],
      [contractPath, `${contractText}变更\n`, contractText],
    ] as const
    for (const [path, changed, original] of mutations) {
      writeFileSync(path, changed)
      assert.notEqual(
        computeCountryOutageSkillBundleSha256(skillPath),
        baseline,
      )
      writeFileSync(path, original)
      assert.equal(
        computeCountryOutageSkillBundleSha256(skillPath),
        baseline,
      )
    }
    assert.equal(
      createStaticCountryOutageResourceBundle(
        skillPath,
        '固定系统提示',
      ).skillBundleSha256,
      baseline,
    )
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true })
  }
})

test('PiReportNarrator 在 Skill 文件变化后按启动时摘要失败关闭且不创建模型会话', async () => {
  const temporaryDirectory = realpathSync(
    mkdtempSync(join(tmpdir(), 'domeye-pi-skill-toctou-')),
  )
  const referencesDirectory = join(temporaryDirectory, 'references')
  mkdirSync(referencesDirectory)
  const skillPath = join(temporaryDirectory, 'SKILL.md')
  const metricsPath = join(
    referencesDirectory,
    'metrics-and-boundaries.md',
  )
  const contractPath = join(
    referencesDirectory,
    'report-output-contract.md',
  )
  writeFileSync(
    skillPath,
    [
      '---',
      'name: country-outage-report',
      'description: 固定国家中断报告测试 Skill',
      '---',
      '只使用受信任的国家中断工具。',
      '',
    ].join('\n'),
  )
  writeFileSync(metricsPath, '固定指标边界 v1\n')
  writeFileSync(contractPath, '固定输出合同 v1\n')

  try {
    let sessionFactoryCalls = 0
    const audits: FormalPiRunAuditRecord[] = []
    const narrator = new PiReportNarrator({
      client: {
        async getObservationBatch() {
          throw new Error('不应读取')
        },
        async getAsns(snapshot: SnapshotIdentity) {
          return makeAsnPage(snapshot)
        },
      },
      model: fakeModel(),
      modelRuntime: fakeModelRuntime(),
      certification: fakeCertification(),
      dependencyRiskException: fakeDependencyRiskException(),
      auditSink(record) {
        audits.push(record)
      },
      skillPath,
      runtimeCwd: temporaryDirectory,
      sessionFactory: async () => {
        sessionFactoryCalls += 1
        throw new Error('Skill 摘要漂移时不应创建会话')
      },
    })
    const startupDigest = narrator.skillBundleSha256
    writeFileSync(metricsPath, '固定指标边界 v2，模拟启动后变更\n')

    await assert.rejects(
      narrator.generate({
        reference: REFERENCE,
        evidence: makeEvidence(),
      }),
      (error: unknown) =>
        error instanceof FormalPiRunError &&
        error.code === 'resource_bundle_mismatch',
    )
    assert.equal(sessionFactoryCalls, 0)
    assert.equal(audits.length, 1)
    assert.equal(audits[0]?.outcome, 'rejected')
    assert.equal(
      audits[0]?.rejectionCode,
      'resource_bundle_mismatch',
    )
    assert.notEqual(
      audits[0]?.runtimeSecurity.skillBundleSha256,
      startupDigest,
    )
    assert.doesNotMatch(
      JSON.stringify(audits[0]),
      /模拟启动后变更/,
    )
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true })
  }
})

test('已启动 Pi 叙述器在模型认证恰好到期时复核并拒绝创建会话', async () => {
  let checkedAt = '2026-07-31T23:59:59.999Z'
  let sessionFactoryCalls = 0
  const baseCertification = fakeCertification()
  const certification: CertifiedPiModelSelection = {
    ...baseCertification,
    profile: {
      ...baseCertification.profile,
      certificationValidUntil: '2026-08-01T00:00:00Z',
    },
  }
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('不应执行')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification,
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink() {},
    now: () => new Date(checkedAt),
    sessionFactory: async () => {
      sessionFactoryCalls += 1
      throw new Error('模型认证到期后不应创建模型会话')
    },
  })
  assert.equal(narrator.identity.modelRevisionKind, 'mutable_alias')
  assert.equal(narrator.identity.immutableRevisionAvailable, false)
  assert.equal(
    narrator.identity.limitation,
    MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
  )
  assert.equal(
    narrator.identity.certificationValidUntil,
    '2026-08-01T00:00:00Z',
  )
  assert.equal(
    narrator.identity.certifiedScenarioSetId,
    certification.profile.certifiedScenarioSetId,
  )
  assert.equal(
    narrator.identity.certifiedInputScope,
    certification.profile.certifiedInputScope,
  )

  checkedAt = '2026-08-01T00:00:00Z'
  await assert.rejects(
    narrator.generate({
      reference: REFERENCE,
      evidence: makeEvidence(),
    }),
    (error: unknown) =>
      error instanceof FormalPiRuntimeError &&
      error.code === 'certification_expired',
  )
  assert.equal(sessionFactoryCalls, 0)
})

test('已启动叙述器在风险例外到期后拒绝创建模型会话和发布报告', async () => {
  let sessionFactoryCalls = 0
  const audits: FormalPiRunAuditRecord[] = []
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('不应执行')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    now: () => new Date('2026-08-12T16:00:00Z'),
    sessionFactory: async () => {
      sessionFactoryCalls += 1
      throw new Error('风险例外到期后不应创建模型会话')
    },
  })

  await assert.rejects(
    narrator.generate({
      reference: REFERENCE,
      evidence: makeEvidence(),
    }),
    (error: unknown) =>
      error instanceof FormalPiRunError &&
      error.code === 'dependency_risk_exception_inactive',
  )
  assert.equal(sessionFactoryCalls, 0)
  assert.equal(audits.length, 1)
  assert.equal(
    audits[0]?.rejectionCode,
    'dependency_risk_exception_inactive',
  )
  assert.deepEqual(
    audits[0]?.runtimeSecurity.dependencyRiskException,
    {
      exceptionId:
        'country-outage-pi-ghsa-mh99-v99m-4gvg-20260812-v2',
      expiresAt: '2026-08-12T16:00:00Z',
      status: 'expired',
    },
  )
  assert.deepEqual(audits[0]?.narration, {
    mode: 'deterministic-base-with-language-slots-v1',
    slotContractVersion: 'country_outage_language_slots_v1',
    requestedSlotCount: 0,
    acceptedSlotCount: 0,
    baseV5: 'not_run',
    mergeInvariant: 'not_run',
    finalV5: 'not_run',
    modelOutputApplied: false,
  })
})

test('PiReportNarrator 固定模型并关闭内置工具、扩展、模板、上下文和持久会话', async () => {
  const evidence = makeEvidence()
  let captured: CreateAgentSessionOptions | undefined
  let prompt = ''
  let disposed = false
  const audits: FormalPiRunAuditRecord[] = []
  const sessionFactory: PiSessionFactory = async (options) => {
    captured = options
    const agent = inertProviderAgent()
    return {
      session: {
        agent,
        messages: validFormalMessages(),
        async prompt(text) {
          prompt = text
          await forwardProviderRequests(agent, 3)
        },
        async abort() {},
        getSessionStats() {
          return fakeSessionStats()
        },
        dispose() {
          disposed = true
        },
      },
    }
  }
  const client = {
    async getObservationBatch() {
      throw new Error('叙述阶段不应重新读取观测批次')
    },
    async getAsns(snapshot: SnapshotIdentity) {
      return makeAsnPage(snapshot)
    },
  }
  const model = fakeModel()
  const modelRuntime = fakeModelRuntime()
  const narrator = new PiReportNarrator({
    client,
    model,
    modelRuntime,
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    sessionFactory,
    now: () => new Date('2026-08-01T00:00:00Z'),
  })

  const draft = await narrator.generate({
    reference: REFERENCE,
    evidence,
  })
  assert.equal(draft.schemaVersion, 'country_outage_report_draft_v1')
  assert.equal(draft.title, '伊朗 BGP 路由可见性观测报告')
  assert.equal(disposed, true)
  assert.match(prompt, /^\/skill:country-outage-report/)
  assert.match(prompt, new RegExp(REFERENCE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  assert.match(prompt, /country_outage_language_slots_v1/)
  assert.match(prompt, /scope\.denominator_explanation/)
  assert.match(prompt, /assessment\.evidence_boundary/)
  assert.match(prompt, /完整报告、title、summary、highlights/)
  assert.match(prompt, /不得新增下降、上升、增加、减少/)

  assert.ok(captured)
  const configured = captured
  assert.equal(configured.model, model)
  assert.equal(configured.modelRuntime, modelRuntime)
  assert.equal(configured.thinkingLevel, 'off')
  assert.equal(configured.noTools, 'builtin')
  assert.deepEqual(configured.tools, COUNTRY_OUTAGE_TOOL_NAMES)
  assert.deepEqual(
    configured.customTools?.map((tool) => tool.name),
    COUNTRY_OUTAGE_TOOL_NAMES,
  )
  assert.deepEqual(configured.excludeTools, [
    'read',
    'bash',
    'edit',
    'write',
    'grep',
    'find',
    'ls',
  ])
  assert.equal(configured.sessionManager?.getSessionFile(), undefined)
  assert.equal(
    configured.resourceLoader?.getExtensions().extensions.length,
    0,
  )
  assert.equal(configured.resourceLoader?.getPrompts().prompts.length, 0)
  assert.equal(
    configured.resourceLoader?.getAgentsFiles().agentsFiles.length,
    0,
  )
  assert.deepEqual(
    configured.resourceLoader?.getSkills().skills.map((skill) => skill.name),
    ['country-outage-report'],
  )
  assert.equal(narrator.identity.adapter, 'pi-sdk')
  assert.equal(narrator.identity.piVersion, '0.82.1')
  assert.equal(audits.length, 1)
  assert.equal(
    audits[0]?.schemaVersion,
    'country_outage_pi_run_audit_v3',
  )
  assert.equal(audits[0]?.outcome, 'accepted')
  assert.deepEqual(audits[0]?.narration, {
    mode: 'deterministic-base-with-language-slots-v1',
    slotContractVersion: 'country_outage_language_slots_v1',
    requestedSlotCount: 2,
    acceptedSlotCount: 2,
    baseV5: 'passed',
    mergeInvariant: 'passed',
    finalV5: 'passed',
    modelOutputApplied: true,
  })
  assert.deepEqual(audits[0]?.tools.executedNames, [
    'country_outage_resolve',
    'country_outage_get_observation',
  ])
  assert.equal(audits[0]?.usage?.estimatedCostUsd, 0.0042)
  assert.equal(audits[0]?.observed?.responseModel, 'fixed-revision')
  assert.deepEqual(audits[0]?.modelAttempt, {
    timeoutMs: 75_000,
    maximumAttempts: 2,
    executedAttempts: 1,
  })
  assert.deepEqual(audits[0]?.input, {
    eventReferenceSha256: createHash('sha256')
      .update(REFERENCE.replace(' ', '+'))
      .digest('hex'),
    incidentId: evidence.facts.snapshot.incidentId,
    publicationId: evidence.facts.snapshot.publicationId,
    revision: evidence.facts.snapshot.revision,
    dataThrough: evidence.facts.snapshot.dataThrough,
    factSetId: evidence.facts.factSetId,
    collectorId: 'rrc25',
    reportSpecificationVersion: 'country_outage_report_spec_v1',
    projectKnowledgeVersion: 'country_outage_report_skill_v6',
    validatorRulesVersion:
      'country_outage_report_validator_rules_v5',
  })
  assert.deepEqual(audits[0]?.runtimeSecurity, {
    resourceLoaderId: STATIC_RESOURCE_LOADER_ID,
    skillBundleSha256: audits[0]?.runtimeSecurity.skillBundleSha256,
    packageManagerResolutionEnabled: false,
    modelResolverEnabled: false,
    modelsJsonEnabled: false,
    modelCatalogNetworkRefreshEnabled: false,
    explicitModel: true,
    providerRetryAttempts: 0,
    forwardedProviderRequestCount: 3,
    structuredOutput: {
      applicability: 'not_applicable',
      mechanism: null,
      payloadPreparedCount: 0,
    },
    dependencyRiskException: {
      exceptionId:
        'country-outage-pi-ghsa-mh99-v99m-4gvg-20260812-v2',
      expiresAt: '2026-08-12T16:00:00Z',
      status: 'active',
    },
  })
  assert.match(
    audits[0]?.runtimeSecurity.skillBundleSha256 ?? '',
    /^[a-f0-9]{64}$/,
  )
  assert.equal(
    narrator.skillBundleSha256,
    audits[0]?.runtimeSecurity.skillBundleSha256,
  )
  const serializedAudit = JSON.stringify(audits[0])
  assert.doesNotMatch(serializedAudit, /secret-tool/)
  assert.doesNotMatch(serializedAudit, /伊朗 BGP 路由可见性观测报告/)
  assert.doesNotMatch(serializedAudit, new RegExp(REFERENCE))
  assert.deepEqual(PI_REPORT_SECURITY_PROFILE.allowedTools, [
    ...COUNTRY_OUTAGE_TOOL_NAMES,
  ])
  assert.equal(
    PI_REPORT_SECURITY_PROFILE.packageManagerResolutionEnabled,
    false,
  )
  assert.equal(PI_REPORT_SECURITY_PROFILE.modelResolverEnabled, false)
})

test('PiReportNarrator 对已解析的语义失败草稿在同会话关闭工具后受控修订', async () => {
  const invalidDraftText = semanticFailureDraftText()
  const messages = formalMessagesWithAsnRequests(
    invalidDraftText,
    1,
  )
  const agent = inertProviderAgent()
  const prompts: string[] = []
  const activeToolTransitions: string[][] = []
  let activeTools = [...COUNTRY_OUTAGE_TOOL_NAMES] as string[]
  let promptCalls = 0
  let sessionFactoryCalls = 0
  const audits: FormalPiRunAuditRecord[] = []
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应重新读取')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    sessionFactory: async () => {
      sessionFactoryCalls += 1
      return {
        session: {
          agent,
          messages,
          getActiveToolNames() {
            return [...activeTools]
          },
          setActiveToolsByName(toolNames) {
            activeTools = [...toolNames]
            activeToolTransitions.push([...toolNames])
          },
          async prompt(text) {
            prompts.push(text)
            promptCalls += 1
            if (promptCalls === 1) {
              await forwardProviderRequests(agent, 4)
              return
            }
            assert.deepEqual(activeTools, [])
            messages.push(
              repairAssistantMessage(VALID_LANGUAGE_SLOT_TEXT),
            )
            await forwardProviderRequests(agent, 1)
          },
          async abort() {},
          getSessionStats() {
            return sessionStatsWithAsnRequests(
              1,
              promptCalls === 2,
            )
          },
          dispose() {},
        },
      }
    },
  })

  const draft = await narrator.generate({
    reference: REFERENCE,
    evidence: makeEvidence(),
  })

  assert.equal(draft.title, '伊朗 BGP 路由可见性观测报告')
  assert.equal(sessionFactoryCalls, 1)
  assert.equal(promptCalls, 2)
  assert.deepEqual(activeToolTransitions, [[]])
  assert.match(
    prompts[1] ?? '',
    /上一份 country_outage_language_slots_v1 JSON 未通过本地机器校验/,
  )
  assert.match(
    prompts[1] ?? '',
    /id=scope\.denominator_explanation/,
  )
  assert.match(
    prompts[1] ?? '',
    /id=assessment\.evidence_boundary/,
  )
  assert.match(
    prompts[1] ?? '',
    /ID、顺序和数量必须与计划完全一致/,
  )
  assert.match(
    prompts[1] ?? '',
    /不得写普通数字、日期、时间、百分比/,
  )
  assert.match(
    prompts[1] ?? '',
    /不得新增方向、因果、全国中断/,
  )
  assert.doesNotMatch(prompts[1] ?? '', /987654321/)
  assert.match(prompts[1] ?? '', /不得调用任何工具；工具已关闭/)
  assert.equal(audits.length, 1)
  assert.equal(audits[0]?.outcome, 'accepted')
  assert.deepEqual(audits[0]?.modelAttempt, {
    timeoutMs: 75_000,
    maximumAttempts: 2,
    executedAttempts: 2,
  })
  assert.equal(
    audits[0]?.runtimeSecurity.forwardedProviderRequestCount,
    5,
  )
  assert.equal(audits[0]?.usage?.assistantMessages, 5)
  assert.equal(audits[0]?.tools.executionCount, 3)
  assert.doesNotMatch(JSON.stringify(audits[0]), /987654321/)
  assert.doesNotMatch(
    JSON.stringify(audits[0]),
    /sensitive-reference-never-forwarded/,
  )
})

test('PiReportNarrator 首轮非结构化输出在剩余请求内关闭工具后整份修订', async () => {
  const invalidPayload = 'not-a-country-outage-report-json'
  const messages = formalMessagesWithAsnRequests(
    invalidPayload,
    1,
  )
  const agent = inertProviderAgent()
  let activeTools = [...COUNTRY_OUTAGE_TOOL_NAMES] as string[]
  let promptCalls = 0
  let activeToolMutationCalls = 0
  const prompts: string[] = []
  const audits: FormalPiRunAuditRecord[] = []
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应重新读取')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    sessionFactory: async () => ({
      session: {
        agent,
        messages,
        getActiveToolNames() {
          return [...activeTools]
        },
        setActiveToolsByName(toolNames) {
          activeTools = [...toolNames]
          activeToolMutationCalls += 1
        },
        async prompt(text) {
          prompts.push(text)
          promptCalls += 1
          if (promptCalls === 1) {
            await forwardProviderRequests(agent, 4)
            return
          }
          assert.deepEqual(activeTools, [])
          messages.push(
            repairAssistantMessage(VALID_LANGUAGE_SLOT_TEXT),
          )
          await forwardProviderRequests(agent, 1)
        },
        async abort() {},
        getSessionStats() {
          return sessionStatsWithAsnRequests(
            1,
            promptCalls === 2,
          )
        },
        dispose() {},
      },
    }),
  })

  const draft = await narrator.generate({
    reference: REFERENCE,
    evidence: makeEvidence(),
  })
  assert.equal(draft.title, '伊朗 BGP 路由可见性观测报告')
  assert.equal(promptCalls, 2)
  assert.equal(activeToolMutationCalls, 1)
  assert.match(
    prompts[1] ?? '',
    /上一份 country_outage_language_slots_v1 JSON 未通过本地机器校验/,
  )
  assert.match(
    prompts[0] ?? '',
    /最终只返回 country_outage_language_slots_v1 JSON/,
  )
  assert.match(
    prompts[0] ?? '',
    /根对象只能有 schemaVersion、slots/,
  )
  assert.match(
    prompts[0] ?? '',
    /每项只能有 id、text/,
  )
  assert.match(
    prompts[0] ?? '',
    /不得缺失、重复、新增或重排/,
  )
  assert.match(
    prompts[0] ?? '',
    /不输出完整报告、title、summary、highlights、sections、unknowns、evidenceRefs/,
  )
  assert.match(
    prompts[1] ?? '',
    /ID、顺序和数量必须与计划完全一致/,
  )
  assert.match(
    prompts[1] ?? '',
    /不得写普通数字、日期、时间、百分比/,
  )
  assert.match(
    prompts[1] ?? '',
    /不输出完整报告、补丁、diff/,
  )
  assert.doesNotMatch(prompts[1] ?? '', new RegExp(invalidPayload))
  assert.equal(audits[0]?.outcome, 'accepted')
  assert.equal(audits[0]?.modelAttempt.executedAttempts, 2)
  assert.equal(
    audits[0]?.runtimeSecurity.forwardedProviderRequestCount,
    5,
  )
})

test('PiReportNarrator 以固定安全码区分整份修订的三类结构失败', async (context) => {
  const cases = [
    {
      name: '缺少 JSON 对象',
      payload: 'SENSITIVE_REPAIR_BODY',
      expectedCode: 'report_json_object_missing',
    },
    {
      name: 'JSON 语法无效',
      payload: '{"secret":"SENSITIVE_REPAIR_FIELD",}',
      expectedCode: 'report_json_syntax_invalid',
    },
    {
      name: '草稿结构无效',
      payload: '{"secret":"SENSITIVE_REPAIR_FIELD"}',
      expectedCode: 'report_draft_schema_invalid',
    },
  ] as const

  for (const item of cases) {
    await context.test(item.name, async () => {
      const messages = formalMessagesWithAsnRequests(
        'initial-invalid-payload',
        1,
      )
      const agent = inertProviderAgent()
      let activeTools = [...COUNTRY_OUTAGE_TOOL_NAMES] as string[]
      let promptCalls = 0
      const audits: FormalPiRunAuditRecord[] = []
      const narrator = new PiReportNarrator({
        client: {
          async getObservationBatch() {
            throw new Error('固定证据模式不应重新读取')
          },
          async getAsns(snapshot: SnapshotIdentity) {
            return makeAsnPage(snapshot)
          },
        },
        model: fakeModel(),
        modelRuntime: fakeModelRuntime(),
        certification: fakeCertification(),
        dependencyRiskException: fakeDependencyRiskException(),
        auditSink(record) {
          audits.push(record)
        },
        sessionFactory: async () => ({
          session: {
            agent,
            messages,
            getActiveToolNames() {
              return [...activeTools]
            },
            setActiveToolsByName(toolNames) {
              activeTools = [...toolNames]
            },
            async prompt() {
              promptCalls += 1
              if (promptCalls === 1) {
                await forwardProviderRequests(agent, 4)
                return
              }
              assert.deepEqual(activeTools, [])
              messages.push(repairAssistantMessage(item.payload))
              await forwardProviderRequests(agent, 1)
            },
            async abort() {},
            getSessionStats() {
              return sessionStatsWithAsnRequests(
                1,
                promptCalls === 2,
              )
            },
            dispose() {},
          },
        }),
      })

      await assert.rejects(
        narrator.generate({
          reference: REFERENCE,
          evidence: makeEvidence(),
        }),
        (error: unknown) =>
          error instanceof FormalPiRunError &&
          error.code === item.expectedCode,
      )
      assert.equal(audits.length, 1)
      assert.equal(audits[0]?.outcome, 'rejected')
      assert.equal(
        audits[0]?.rejectionCode,
        item.expectedCode,
      )
      assert.equal(audits[0]?.modelAttempt.executedAttempts, 2)
      assert.doesNotMatch(
        JSON.stringify(audits[0]),
        /SENSITIVE_REPAIR_BODY|SENSITIVE_REPAIR_FIELD/,
      )
    })
  }
})

test('首轮结构失败且五次 provider 请求已耗尽时保留固定安全诊断且不再修订', async () => {
  const sensitivePayload = 'SENSITIVE_INITIAL_BODY'
  const messages =
    formalMessagesWithFiveProviderRequests(sensitivePayload)
  const agent = inertProviderAgent()
  let promptCalls = 0
  let activeToolMutationCalls = 0
  const audits: FormalPiRunAuditRecord[] = []
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应重新读取')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    sessionFactory: async () => ({
      session: {
        agent,
        messages,
        getActiveToolNames() {
          return [...COUNTRY_OUTAGE_TOOL_NAMES]
        },
        setActiveToolsByName() {
          activeToolMutationCalls += 1
        },
        async prompt() {
          promptCalls += 1
          await forwardProviderRequests(agent, 5)
        },
        async abort() {},
        getSessionStats() {
          return sessionStatsWithFiveProviderRequests()
        },
        dispose() {},
      },
    }),
  })

  await assert.rejects(
    narrator.generate({
      reference: REFERENCE,
      evidence: makeEvidence(),
    }),
    (error: unknown) =>
      error instanceof FormalPiRunError &&
      error.code === 'report_json_object_missing',
  )
  assert.equal(promptCalls, 1)
  assert.equal(activeToolMutationCalls, 0)
  assert.equal(
    audits[0]?.rejectionCode,
    'report_json_object_missing',
  )
  assert.equal(audits[0]?.modelAttempt.executedAttempts, 1)
  assert.equal(
    audits[0]?.runtimeSecurity.forwardedProviderRequestCount,
    5,
  )
  assert.doesNotMatch(JSON.stringify(audits[0]), new RegExp(sensitivePayload))
})

test('DeepSeek 发送前依次强制 resolve、observation 与无工具 JSON 叙述且不修改原对象', async () => {
  const model = {
    provider: 'deepseek',
    id: 'deepseek-v4-flash',
    name: 'DeepSeek V4 Flash',
    api: 'openai-completions',
    baseUrl: 'https://api.deepseek.com',
    reasoning: true,
    input: ['text'],
    cost: {
      input: 0.14,
      output: 0.28,
      cacheRead: 0.0028,
      cacheWrite: 0,
    },
    contextWindow: 128_000,
    maxTokens: 16_384,
  } as NonNullable<CreateAgentSessionOptions['model']>
  const certification: CertifiedPiModelSelection = {
    registryVersion: 'test-deepseek-models-v1',
    profile: {
      id: 'deepseek-v4-flash-v1',
      status: 'certified',
      provider: 'deepseek',
      model: 'deepseek-v4-flash',
      modelVersion: 'deepseek-v4-flash',
      expectedResponseModel: 'deepseek-v4-flash',
      thinkingLevel: 'off',
      piVersion: '0.82.1',
      certificationEvidenceId: 'evidence:test-deepseek',
      certifiedAt: '2026-07-29T00:00:00Z',
      modelRevisionKind: 'mutable_alias',
      immutableRevisionAvailable: false,
      limitation: MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
      certificationValidUntil: '2099-01-01T00:00:00Z',
      certifiedScenarioSetId:
        'country-outage-deepseek-scenarios-test-v1',
      certifiedInputScope:
        'legal_country_outage_rrc25_test_v1',
    },
  }
  const invalidPayload = 'not-a-country-outage-report-json'
  const messages = formalMessagesWithAsnRequests(
    invalidPayload,
    1,
  ).map((message) => {
    if (
      message === null ||
      typeof message !== 'object' ||
      Array.isArray(message) ||
      (message as Record<string, unknown>).role !== 'assistant'
    ) {
      return message
    }
    return {
      ...(message as Record<string, unknown>),
      provider: 'deepseek',
      model: 'deepseek-v4-flash',
      responseModel: 'deepseek-v4-flash',
    }
  })
  const rawPayloads: Array<Record<string, unknown>> = []
  const forwardedPayloads: unknown[] = []
  let existingHookCalls = 0
  const agent: TestSessionAgent = {
    streamFunction(streamModel, _context, options) {
      const stream = providerMessageStream(
        streamModel,
        [{ type: 'text', text: 'unused' }],
        'stop',
      )
      const rawPayload = {
        model: streamModel.id,
        messages: [],
        stream: true,
      }
      rawPayloads.push(rawPayload)
      let prepared: Promise<void> | undefined
      const prepare = (): Promise<void> => {
        prepared ??= (async () => {
          const transformed = await options?.onPayload?.(
            rawPayload,
            streamModel,
          )
          forwardedPayloads.push(transformed ?? rawPayload)
        })()
        return prepared
      }
      return {
        async *[Symbol.asyncIterator]() {
          await prepare()
          for await (const event of stream) yield event
        },
        async result() {
          await prepare()
          return await stream.result()
        },
      } as unknown as TestProviderStream
    },
  }
  const existingPayloadHook = async (
    payload: unknown,
  ): Promise<unknown> => {
    existingHookCalls += 1
    assert.ok(
      payload !== null &&
        typeof payload === 'object' &&
        !Array.isArray(payload),
    )
    return {
      ...(payload as Record<string, unknown>),
      existing_hook_preserved: true,
      response_format: { type: 'legacy-value' },
    }
  }
  let activeTools = [...COUNTRY_OUTAGE_TOOL_NAMES] as string[]
  let promptCalls = 0
  const audits: FormalPiRunAuditRecord[] = []
  const forward = async (
    count: number,
    contextMessages: unknown[],
  ): Promise<void> => {
    for (let index = 0; index < count; index += 1) {
      const stream = await agent.streamFunction(
        model,
        { messages: contextMessages as never[] },
        { onPayload: existingPayloadHook },
      )
      for await (const _event of stream) {
        // 模拟适配器完整消费请求。
      }
    }
  }
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应重新读取')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model,
    modelRuntime: fakeModelRuntime(),
    certification,
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    sessionFactory: async () => ({
      session: {
        agent,
        messages,
        getActiveToolNames() {
          return [...activeTools]
        },
        setActiveToolsByName(toolNames) {
          activeTools = [...toolNames]
        },
        async prompt() {
          promptCalls += 1
          if (promptCalls === 1) {
            await forward(1, [])
            await forward(1, [
              {
                role: 'toolResult',
                toolName: 'country_outage_resolve',
                isError: false,
              },
            ])
            await forward(2, messages)
            return
          }
          assert.deepEqual(activeTools, [])
          messages.push({
            ...(repairAssistantMessage(
              VALID_LANGUAGE_SLOT_TEXT,
            ) as Record<string, unknown>),
            provider: 'deepseek',
            model: 'deepseek-v4-flash',
            responseModel: 'deepseek-v4-flash',
          })
          await forward(1, messages)
        },
        async abort() {},
        getSessionStats() {
          return sessionStatsWithAsnRequests(1, promptCalls === 2)
        },
        dispose() {},
      },
    }),
  })

  const draft = await narrator.generate({
    reference: REFERENCE,
    evidence: makeEvidence(),
  })

  assert.equal(draft.title, '伊朗 BGP 路由可见性观测报告')
  assert.equal(existingHookCalls, 5)
  assert.equal(forwardedPayloads.length, 5)
  assert.deepEqual(
    (forwardedPayloads[0] as Record<string, unknown>).tool_choice,
    {
      type: 'function',
      function: { name: 'country_outage_resolve' },
    },
  )
  assert.deepEqual(
    (forwardedPayloads[1] as Record<string, unknown>).tool_choice,
    {
      type: 'function',
      function: { name: 'country_outage_get_observation' },
    },
  )
  for (const payload of forwardedPayloads.slice(0, 2)) {
    assert.deepEqual(
      (payload as Record<string, unknown>).response_format,
      { type: 'legacy-value' },
    )
  }
  for (const payload of forwardedPayloads.slice(2)) {
    assert.deepEqual(
      (payload as Record<string, unknown>).response_format,
      { type: 'json_object' },
    )
    assert.equal(
      (payload as Record<string, unknown>).tool_choice,
      'none',
    )
  }
  assert.equal(
    (forwardedPayloads[4] as Record<string, unknown>)
      .existing_hook_preserved,
    true,
  )
  assert.equal(
    Object.prototype.hasOwnProperty.call(
      rawPayloads[4],
      'response_format',
    ),
    false,
  )
  assert.equal(
    Object.prototype.hasOwnProperty.call(
      rawPayloads[4],
      'tool_choice',
    ),
    false,
  )
  assert.deepEqual(audits[0]?.runtimeSecurity.structuredOutput, {
    applicability: 'required',
    mechanism:
      'deepseek-json-object-after-required-tools-v1',
    payloadPreparedCount: 3,
  })
})

test('DeepSeek 拒绝既有 payload hook 返回的非普通对象且不误增结构化计数', async () => {
  const model = {
    provider: 'deepseek',
    id: 'deepseek-v4-flash',
    name: 'DeepSeek V4 Flash',
    api: 'openai-completions',
    baseUrl: 'https://api.deepseek.com',
    reasoning: true,
    input: ['text'],
    cost: {
      input: 0.14,
      output: 0.28,
      cacheRead: 0.0028,
      cacheWrite: 0,
    },
    contextWindow: 128_000,
    maxTokens: 16_384,
  } as NonNullable<CreateAgentSessionOptions['model']>
  const certification: CertifiedPiModelSelection = {
    registryVersion: 'test-deepseek-models-v1',
    profile: {
      id: 'deepseek-v4-flash-v1',
      status: 'certified',
      provider: 'deepseek',
      model: 'deepseek-v4-flash',
      modelVersion: 'deepseek-v4-flash',
      expectedResponseModel: 'deepseek-v4-flash',
      thinkingLevel: 'off',
      piVersion: '0.82.1',
      certificationEvidenceId: 'evidence:test-deepseek',
      certifiedAt: '2026-07-29T00:00:00Z',
      modelRevisionKind: 'mutable_alias',
      immutableRevisionAvailable: false,
      limitation: MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
      certificationValidUntil: '2099-01-01T00:00:00Z',
      certifiedScenarioSetId:
        'country-outage-deepseek-scenarios-test-v1',
      certifiedInputScope:
        'legal_country_outage_rrc25_test_v1',
    },
  }
  const messages = formalMessagesWithAsnRequests(
    VALID_LANGUAGE_SLOT_TEXT,
    1,
  ).map((message) => {
    if (
      message === null ||
      typeof message !== 'object' ||
      Array.isArray(message) ||
      (message as Record<string, unknown>).role !== 'assistant'
    ) {
      return message
    }
    return {
      ...(message as Record<string, unknown>),
      provider: 'deepseek',
      model: 'deepseek-v4-flash',
      responseModel: 'deepseek-v4-flash',
    }
  })
  const agent: TestSessionAgent = {
    streamFunction(streamModel, _context, options) {
      const stream = providerMessageStream(
        streamModel,
        [{ type: 'text', text: 'unused' }],
        'stop',
      )
      let prepared: Promise<void> | undefined
      const prepare = (): Promise<void> => {
        prepared ??= (async () => {
          await options?.onPayload?.(
            {
              model: streamModel.id,
              messages: [],
              stream: true,
            },
            streamModel,
          )
        })()
        return prepared
      }
      return {
        async *[Symbol.asyncIterator]() {
          await prepare()
          for await (const event of stream) yield event
        },
        async result() {
          await prepare()
          return await stream.result()
        },
      } as unknown as TestProviderStream
    },
  }
  const audits: FormalPiRunAuditRecord[] = []
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应重新读取')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model,
    modelRuntime: fakeModelRuntime(),
    certification,
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    sessionFactory: async () => ({
      session: {
        agent,
        messages,
        async prompt() {
          const stream = await agent.streamFunction(
            model,
            { messages: messages as never[] },
            {
              async onPayload() {
                return new Date() as unknown as Record<
                  string,
                  unknown
                >
              },
            },
          )
          for await (const _event of stream) {
            // 触发真实 provider hook 消费路径。
          }
        },
        async abort() {},
        getSessionStats() {
          return sessionStatsWithAsnRequests(1)
        },
        dispose() {},
      },
    }),
  })

  await assert.rejects(
    narrator.generate({
      reference: REFERENCE,
      evidence: makeEvidence(),
    }),
    (error: unknown) =>
      error instanceof FormalPiRunError &&
      error.code === 'provider_call_failed',
  )
  assert.equal(audits[0]?.outcome, 'rejected')
  assert.equal(
    audits[0]?.runtimeSecurity.forwardedProviderRequestCount,
    1,
  )
  assert.deepEqual(audits[0]?.runtimeSecurity.structuredOutput, {
    applicability: 'required',
    mechanism:
      'deepseek-json-object-after-required-tools-v1',
    payloadPreparedCount: 0,
  })
})

test('PiReportNarrator 修订仍语义失败时在 accepted 审计前失败关闭', async () => {
  const invalidDraftText = semanticFailureDraftText()
  const messages = formalMessagesWithAsnRequests(
    invalidDraftText,
    1,
  )
  const agent = inertProviderAgent()
  let activeTools = [...COUNTRY_OUTAGE_TOOL_NAMES] as string[]
  let promptCalls = 0
  const audits: FormalPiRunAuditRecord[] = []
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应重新读取')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    sessionFactory: async () => ({
      session: {
        agent,
        messages,
        getActiveToolNames() {
          return [...activeTools]
        },
        setActiveToolsByName(toolNames) {
          activeTools = [...toolNames]
        },
        async prompt() {
          promptCalls += 1
          if (promptCalls === 1) {
            await forwardProviderRequests(agent, 4)
            return
          }
          assert.deepEqual(activeTools, [])
          messages.push(repairAssistantMessage(invalidDraftText))
          await forwardProviderRequests(agent, 1)
        },
        async abort() {},
        getSessionStats() {
          return sessionStatsWithAsnRequests(1, promptCalls === 2)
        },
        dispose() {},
      },
    }),
  })

  await assert.rejects(
    narrator.generate({
      reference: REFERENCE,
      evidence: makeEvidence(),
    }),
    (error: unknown) =>
      error instanceof FormalPiRunError &&
      error.code === 'report_payload_invalid',
  )

  assert.equal(promptCalls, 2)
  assert.equal(audits[0]?.outcome, 'rejected')
  assert.equal(audits[0]?.rejectionCode, 'report_payload_invalid')
  assert.equal(audits[0]?.modelAttempt.executedAttempts, 2)
  assert.deepEqual(audits[0]?.narration, {
    mode: 'deterministic-base-with-language-slots-v1',
    slotContractVersion: 'country_outage_language_slots_v1',
    requestedSlotCount: 2,
    acceptedSlotCount: 0,
    baseV5: 'passed',
    mergeInvariant: 'not_run',
    finalV5: 'not_run',
    modelOutputApplied: false,
  })
})

test('PiReportNarrator 首轮已用满五个 provider request 时不发起修订', async () => {
  const invalidDraftText = semanticFailureDraftText()
  const messages =
    formalMessagesWithFiveProviderRequests(invalidDraftText)
  const agent = inertProviderAgent()
  let promptCalls = 0
  let activeToolMutationCalls = 0
  const audits: FormalPiRunAuditRecord[] = []
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('固定证据模式不应重新读取')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    sessionFactory: async () => ({
      session: {
        agent,
        messages,
        getActiveToolNames() {
          return [...COUNTRY_OUTAGE_TOOL_NAMES]
        },
        setActiveToolsByName() {
          activeToolMutationCalls += 1
        },
        async prompt() {
          promptCalls += 1
          await forwardProviderRequests(agent, 5)
        },
        async abort() {},
        getSessionStats() {
          return sessionStatsWithFiveProviderRequests()
        },
        dispose() {},
      },
    }),
  })

  await assert.rejects(
    narrator.generate({
      reference: REFERENCE,
      evidence: makeEvidence(),
    }),
    (error: unknown) =>
      error instanceof FormalPiRunError &&
      error.code === 'report_payload_invalid',
  )

  assert.equal(promptCalls, 1)
  assert.equal(activeToolMutationCalls, 0)
  assert.equal(audits[0]?.outcome, 'rejected')
  assert.equal(audits[0]?.rejectionCode, 'report_payload_invalid')
  assert.equal(
    audits[0]?.runtimeSecurity.forwardedProviderRequestCount,
    5,
  )
  assert.equal(audits[0]?.modelAttempt.executedAttempts, 1)
})

test('PiReportNarrator 仅在每轮完整 usage 与转发数、SessionStats 三方一致时接受', async (context) => {
  const cases: Array<{
    name: string
    forwardedProviderRequestCount: number
    mutate: (messages: Array<Record<string, unknown>>) => void
  }> = [
    {
      name: '单轮 usage 缺失',
      forwardedProviderRequestCount: 3,
      mutate(messages) {
        delete messages[2]!.usage
      },
    },
    {
      name: '有输入但 completion 输出为零',
      forwardedProviderRequestCount: 3,
      mutate(messages) {
        messages[2]!.usage = assistantUsage(400, 0)
      },
    },
    {
      name: '逐轮 usage 合计与 SessionStats 不一致',
      forwardedProviderRequestCount: 3,
      mutate(messages) {
        messages[2]!.usage = assistantUsage(401, 100)
      },
    },
    {
      name: 'provider 转发轮数与 assistant 轮数不一致',
      forwardedProviderRequestCount: 2,
      mutate() {},
    },
  ]

  for (const item of cases) {
    await context.test(item.name, async () => {
      const messages = structuredClone(
        validFormalMessages(),
      ) as Array<Record<string, unknown>>
      item.mutate(messages)
      const agent = inertProviderAgent()
      const audits: FormalPiRunAuditRecord[] = []
      const narrator = new PiReportNarrator({
        client: {
          async getObservationBatch() {
            throw new Error('固定证据模式不应重新读取')
          },
          async getAsns(snapshot: SnapshotIdentity) {
            return makeAsnPage(snapshot)
          },
        },
        model: fakeModel(),
        modelRuntime: fakeModelRuntime(),
        certification: fakeCertification(),
        dependencyRiskException: fakeDependencyRiskException(),
        auditSink(record) {
          audits.push(record)
        },
        sessionFactory: async () => ({
          session: {
            agent,
            messages,
            async prompt() {
              await forwardProviderRequests(
                agent,
                item.forwardedProviderRequestCount,
              )
            },
            async abort() {},
            getSessionStats: () => fakeSessionStats(),
            dispose() {},
          },
        }),
      })

      await assert.rejects(
        narrator.generate({
          reference: REFERENCE,
          evidence: makeEvidence(),
        }),
        (error: unknown) =>
          error instanceof FormalPiRunError &&
          error.code === 'session_stats_invalid',
      )
      assert.equal(audits.length, 1)
      assert.equal(audits[0]?.outcome, 'rejected')
      assert.equal(
        audits[0]?.rejectionCode,
        'session_stats_invalid',
      )
      assert.equal(
        audits[0]?.runtimeSecurity
          .forwardedProviderRequestCount,
        item.forwardedProviderRequestCount,
      )
    })
  }
})

test('PiReportNarrator 将宿主 AbortSignal 转发到 Pi 会话并拒绝半成品', async () => {
  const evidence = makeEvidence()
  const controller = new AbortController()
  const agent = inertProviderAgent()
  const partialMessages = validFormalMessages().slice(0, 4)
  let signalPromptStarted!: () => void
  const promptStarted = new Promise<void>((resolve) => {
    signalPromptStarted = resolve
  })
  let finishPrompt!: () => void
  let abortCount = 0
  let disposed = false
  const audits: FormalPiRunAuditRecord[] = []
  const sessionFactory: PiSessionFactory = async () => ({
    session: {
      agent,
      messages: partialMessages,
      async prompt() {
        await forwardProviderRequests(agent, 2)
        signalPromptStarted()
        await new Promise<void>((resolve) => {
          finishPrompt = resolve
        })
      },
      async abort() {
        abortCount += 1
        finishPrompt()
      },
      getSessionStats() {
        return fakeSessionStats({
          assistantMessages: 2,
          toolCalls: 2,
          toolResults: 2,
          totalMessages: 5,
          tokens: {
            input: 800,
            output: 200,
            cacheRead: 0,
            cacheWrite: 0,
            total: 1_000,
          },
        })
      },
      dispose() {
        disposed = true
      },
    },
  })
  const client = {
    async getObservationBatch() {
      throw new Error('叙述阶段不应重新读取观测批次')
    },
    async getAsns(snapshot: SnapshotIdentity) {
      return makeAsnPage(snapshot)
    },
  }
  const narrator = new PiReportNarrator({
    client,
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    sessionFactory,
  })

  const generation = narrator.generate({
    reference: REFERENCE,
    evidence,
    signal: controller.signal,
  })
  await promptStarted
  controller.abort()
  await assert.rejects(generation, (error: unknown) => {
    return error instanceof Error && error.name === 'AbortError'
  })
  assert.equal(abortCount, 1)
  assert.equal(disposed, true)
  assert.equal(audits.length, 1)
  assert.equal(audits[0]?.outcome, 'rejected')
  assert.equal(audits[0]?.rejectionCode, 'aborted')
  assert.equal(
    audits[0]?.runtimeSecurity.forwardedProviderRequestCount,
    2,
  )
  assert.equal(audits[0]?.usage?.assistantMessages, 2)
})

test('PiReportNarrator 多轮后 75 秒超时会中止会话且不发布部分用量草稿', async () => {
  let timeoutMs = 0
  let timeoutCancelled = false
  let fireTimeout!: () => void
  let abortCount = 0
  let disposed = false
  let finishPrompt!: () => void
  const agent = inertProviderAgent()
  const partialMessages = validFormalMessages().slice(0, 4)
  const audits: FormalPiRunAuditRecord[] = []
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('不应执行')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    attemptTimeoutScheduler(callback, delay) {
      timeoutMs = delay
      fireTimeout = callback
      return () => {
        timeoutCancelled = true
      }
    },
    sessionFactory: async () => ({
      session: {
        agent,
        messages: partialMessages,
        async prompt() {
          await forwardProviderRequests(agent, 2)
          await new Promise<void>((resolve) => {
            finishPrompt = resolve
            fireTimeout()
          })
        },
        async abort() {
          abortCount += 1
          finishPrompt()
        },
        getSessionStats() {
          return fakeSessionStats({
            assistantMessages: 2,
            toolCalls: 2,
            toolResults: 2,
            totalMessages: 5,
            tokens: {
              input: 800,
              output: 200,
              cacheRead: 0,
              cacheWrite: 0,
              total: 1_000,
            },
          })
        },
        dispose() {
          disposed = true
        },
      },
    }),
  })

  await assert.rejects(
    narrator.generate({
      reference: REFERENCE,
      evidence: makeEvidence(),
    }),
    (error: unknown) =>
      error instanceof FormalPiRunError &&
      error.code === 'model_attempt_timeout',
  )
  assert.equal(timeoutMs, 75_000)
  assert.equal(timeoutCancelled, true)
  assert.equal(abortCount, 1)
  assert.equal(disposed, true)
  assert.equal(audits.length, 1)
  assert.equal(audits[0]?.outcome, 'rejected')
  assert.equal(audits[0]?.rejectionCode, 'model_attempt_timeout')
  assert.equal(
    audits[0]?.runtimeSecurity.forwardedProviderRequestCount,
    2,
  )
  assert.equal(audits[0]?.usage?.assistantMessages, 2)
  assert.deepEqual(audits[0]?.modelAttempt, {
    timeoutMs: 75_000,
    maximumAttempts: 2,
    executedAttempts: 1,
  })
})

test('PiReportNarrator 构造时拒绝模型对象与认证组合不一致', () => {
  const model = {
    ...fakeModel(),
    id: 'unapproved-model',
  } as NonNullable<CreateAgentSessionOptions['model']>
  const client = {
    async getObservationBatch() {
      throw new Error('不应执行')
    },
    async getAsns(snapshot: SnapshotIdentity) {
      return makeAsnPage(snapshot)
    },
  }
  assert.throws(
    () =>
      new PiReportNarrator({
        client,
        model,
        modelRuntime: fakeModelRuntime(),
        certification: fakeCertification(),
        dependencyRiskException: fakeDependencyRiskException(),
        auditSink() {},
      }),
    (error: unknown) =>
      error instanceof FormalPiRunError &&
      error.code === 'configured_model_mismatch',
  )
})

test('PiReportNarrator 构造时拒绝小于 64k 的模型上下文窗口', () => {
  const client = {
    async getObservationBatch() {
      throw new Error('不应执行')
    },
    async getAsns(snapshot: SnapshotIdentity) {
      return makeAsnPage(snapshot)
    },
  }
  assert.throws(
    () =>
      new PiReportNarrator({
        client,
        model: {
          ...fakeModel(),
          contextWindow: 63_999,
        } as NonNullable<CreateAgentSessionOptions['model']>,
        modelRuntime: fakeModelRuntime(),
        certification: fakeCertification(),
        dependencyRiskException: fakeDependencyRiskException(),
        auditSink() {},
      }),
    (error: unknown) =>
      error instanceof FormalPiRunError &&
      error.code === 'model_context_window_too_small',
  )
})

test('供应方 AbortError 在宿主未取消时仍归类为模型调用失败', async () => {
  const audits: FormalPiRunAuditRecord[] = []
  const narrator = new PiReportNarrator({
    client: {
      async getObservationBatch() {
        throw new Error('不应执行')
      },
      async getAsns(snapshot: SnapshotIdentity) {
        return makeAsnPage(snapshot)
      },
    },
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    sessionFactory: async () => ({
      session: {
        agent: inertProviderAgent(),
        messages: [],
        async prompt() {
          const error = new Error('供应方网络中止')
          error.name = 'AbortError'
          throw error
        },
        async abort() {},
        getSessionStats() {
          return fakeSessionStats()
        },
        dispose() {},
      },
    }),
  })

  await assert.rejects(
    narrator.generate({
      reference: REFERENCE,
      evidence: makeEvidence(),
    }),
    (error: unknown) =>
      error instanceof FormalPiRunError &&
      error.code === 'provider_call_failed',
  )
  assert.equal(audits[0]?.rejectionCode, 'provider_call_failed')
})

test('PiReportNarrator 对供应方、模型、响应版本和停止原因逐项失败关闭', async (context) => {
  const cases: Array<{
    name: string
    mutate: (message: Record<string, unknown>) => void
    code: FormalPiRunError['code']
    forbiddenAuditValue: string
  }> = [
    {
      name: '供应方漂移',
      mutate: (message) => {
        message.provider = 'unapproved-provider'
      },
      code: 'provider_mismatch',
      forbiddenAuditValue: 'unapproved-provider',
    },
    {
      name: '请求模型漂移',
      mutate: (message) => {
        message.model = 'unapproved-model'
      },
      code: 'model_mismatch',
      forbiddenAuditValue: 'unapproved-model',
    },
    {
      name: '响应版本缺失',
      mutate: (message) => {
        delete message.responseModel
      },
      code: 'response_model_missing',
      forbiddenAuditValue: '"responseModel":',
    },
    {
      name: '响应版本漂移',
      mutate: (message) => {
        message.responseModel = 'unapproved-response-model'
      },
      code: 'response_model_mismatch',
      forbiddenAuditValue: 'unapproved-response-model',
    },
    {
      name: '非正常停止',
      mutate: (message) => {
        message.stopReason = 'length'
      },
      code: 'stop_reason_invalid',
      forbiddenAuditValue: 'length',
    },
  ]

  for (const item of cases) {
    await context.test(item.name, async () => {
      const messages = structuredClone(validFormalMessages())
      const final = messages.at(-1) as Record<string, unknown>
      item.mutate(final)
      const audits: FormalPiRunAuditRecord[] = []
      const sessionFactory: PiSessionFactory = async () => ({
        session: {
          agent: inertProviderAgent(),
          messages,
          async prompt() {},
          async abort() {},
          getSessionStats() {
            return fakeSessionStats()
          },
          dispose() {},
        },
      })
      const client = {
        async getObservationBatch() {
          throw new Error('叙述阶段不应重新读取观测批次')
        },
        async getAsns(snapshot: SnapshotIdentity) {
          return makeAsnPage(snapshot)
        },
      }
      const narrator = new PiReportNarrator({
        client,
        model: fakeModel(),
        modelRuntime: fakeModelRuntime(),
        certification: fakeCertification(),
        dependencyRiskException: fakeDependencyRiskException(),
        auditSink(record) {
          audits.push(record)
        },
        sessionFactory,
      })

      await assert.rejects(
        narrator.generate({
          reference: REFERENCE,
          evidence: makeEvidence(),
        }),
        (error: unknown) =>
          error instanceof FormalPiRunError && error.code === item.code,
      )
      assert.equal(audits.length, 1)
      assert.equal(audits[0]?.outcome, 'rejected')
      assert.equal(audits[0]?.rejectionCode, item.code)
      assert.equal(audits[0]?.observed, undefined)
      assert.equal(
        JSON.stringify(audits[0]).includes(item.forbiddenAuditValue),
        false,
      )
    })
  }
})

test('PiReportNarrator 不记录未授权工具名、参数、结果或报告正文', async () => {
  const messages = structuredClone(validFormalMessages())
  const firstAssistant = messages[0] as {
    content: Array<Record<string, unknown>>
  }
  firstAssistant.content[0]!.name = 'bash-secret-command'
  const firstResult = messages[1] as Record<string, unknown>
  firstResult.toolName = 'bash-secret-command'
  const audits: FormalPiRunAuditRecord[] = []
  const sessionFactory: PiSessionFactory = async () => ({
    session: {
      agent: inertProviderAgent(),
      messages,
      async prompt() {},
      async abort() {},
      getSessionStats() {
        return fakeSessionStats()
      },
      dispose() {},
    },
  })
  const client = {
    async getObservationBatch() {
      throw new Error('叙述阶段不应重新读取观测批次')
    },
    async getAsns(snapshot: SnapshotIdentity) {
      return makeAsnPage(snapshot)
    },
  }
  const narrator = new PiReportNarrator({
    client,
    model: fakeModel(),
    modelRuntime: fakeModelRuntime(),
    certification: fakeCertification(),
    dependencyRiskException: fakeDependencyRiskException(),
    auditSink(record) {
      audits.push(record)
    },
    sessionFactory,
  })

  await assert.rejects(
    narrator.generate({
      reference: REFERENCE,
      evidence: makeEvidence(),
    }),
    (error: unknown) =>
      error instanceof FormalPiRunError &&
      error.code === 'tool_not_allowed',
  )
  assert.equal(audits[0]?.tools.unauthorizedAttemptCount, 2)
  const serializedAudit = JSON.stringify(audits[0])
  assert.doesNotMatch(serializedAudit, /bash-secret-command/)
  assert.doesNotMatch(serializedAudit, /secret-tool/)
  assert.doesNotMatch(serializedAudit, /伊朗 BGP 路由可见性观测报告/)
})

test('PiReportNarrator 缺少必需工具结果、统计异常或审计写入失败时不发布草稿', async (context) => {
  const client = {
    async getObservationBatch() {
      throw new Error('叙述阶段不应重新读取观测批次')
    },
    async getAsns(snapshot: SnapshotIdentity) {
      return makeAsnPage(snapshot)
    },
  }
  await context.test('缺少必需工具结果', async () => {
    const messages = validFormalMessages().filter(
      (message) =>
        !(
          typeof message === 'object' &&
          message !== null &&
          'toolName' in message &&
          message.toolName === 'country_outage_get_observation'
        ),
    )
    const narrator = new PiReportNarrator({
      client,
      model: fakeModel(),
      modelRuntime: fakeModelRuntime(),
      certification: fakeCertification(),
      dependencyRiskException: fakeDependencyRiskException(),
      auditSink() {},
      sessionFactory: async () => ({
        session: {
          agent: inertProviderAgent(),
          messages,
          async prompt() {},
          async abort() {},
          getSessionStats: () => fakeSessionStats(),
          dispose() {},
        },
      }),
    })
    await assert.rejects(
      narrator.generate({ reference: REFERENCE, evidence: makeEvidence() }),
      (error: unknown) =>
        error instanceof FormalPiRunError &&
        error.code === 'required_tool_missing',
    )
  })

  await context.test('统计异常', async () => {
    const narrator = new PiReportNarrator({
      client,
      model: fakeModel(),
      modelRuntime: fakeModelRuntime(),
      certification: fakeCertification(),
      dependencyRiskException: fakeDependencyRiskException(),
      auditSink() {},
      sessionFactory: async () => ({
        session: {
          agent: inertProviderAgent(),
          messages: validFormalMessages(),
          async prompt() {},
          async abort() {},
          getSessionStats: () =>
            fakeSessionStats({
              cost: Number.NaN,
            }),
          dispose() {},
        },
      }),
    })
    await assert.rejects(
      narrator.generate({ reference: REFERENCE, evidence: makeEvidence() }),
      (error: unknown) =>
        error instanceof FormalPiRunError &&
      error.code === 'session_stats_invalid',
    )
  })

  await context.test('完成后任一轮 input-like 令牌超过 64k', async () => {
    const audits: FormalPiRunAuditRecord[] = []
    const messages = structuredClone(validFormalMessages()) as Array<
      Record<string, unknown>
    >
    messages[0]!.usage = assistantUsage(64_001, 100)
    messages[2]!.usage = assistantUsage(1_000, 100)
    messages[4]!.usage = assistantUsage(1_000, 100)
    const agent = inertProviderAgent()
    const narrator = new PiReportNarrator({
      client,
      model: fakeModel(),
      modelRuntime: fakeModelRuntime(),
      certification: fakeCertification(),
      dependencyRiskException: fakeDependencyRiskException(),
      auditSink(record) {
        audits.push(record)
      },
      sessionFactory: async () => ({
        session: {
          agent,
          messages,
          async prompt() {
            await forwardProviderRequests(agent, 3)
          },
          async abort() {},
          getSessionStats: () =>
            fakeSessionStats({
              tokens: {
                input: 66_001,
                output: 300,
                cacheRead: 0,
                cacheWrite: 0,
                total: 66_301,
              },
            }),
          dispose() {},
        },
      }),
    })
    await assert.rejects(
      narrator.generate({ reference: REFERENCE, evidence: makeEvidence() }),
      (error: unknown) =>
        error instanceof FormalPiRunError &&
        error.code === 'context_input_limit_exceeded',
    )
    assert.equal(
      audits[0]?.rejectionCode,
      'context_input_limit_exceeded',
    )
  })

  await context.test('审计写入失败', async () => {
    const agent = inertProviderAgent()
    const narrator = new PiReportNarrator({
      client,
      model: fakeModel(),
      modelRuntime: fakeModelRuntime(),
      certification: fakeCertification(),
      dependencyRiskException: fakeDependencyRiskException(),
      auditSink() {
        throw new Error('日志存储不可用')
      },
      sessionFactory: async () => ({
        session: {
          agent,
          messages: validFormalMessages(),
          async prompt() {
            await forwardProviderRequests(agent, 3)
          },
          async abort() {},
          getSessionStats: () => fakeSessionStats(),
          dispose() {},
        },
      }),
    })
    await assert.rejects(
      narrator.generate({ reference: REFERENCE, evidence: makeEvidence() }),
      (error: unknown) =>
        error instanceof FormalPiRunError &&
        error.code === 'audit_sink_failed',
    )
  })
})
