import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import {
  chmodSync,
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import test, { after } from 'node:test'

import type {
  CreateAgentSessionOptions,
  ModelRuntime,
  SessionStats,
  ToolDefinition,
} from '@earendil-works/pi-coding-agent'

import { assembleCountryOutageFacts } from '../src/domain/observation-assembler.js'
import {
  buildDeterministicCountryOutageDraft,
  DeterministicAcceptanceNarrator,
} from '../src/report/deterministic-narrator.js'
import {
  buildCountryOutageModelLanguagePlan,
  COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
  type CountryOutageLanguageSlotId,
} from '../src/report/model-language-plan.js'
import type {
  CountryOutageReportDraft,
  ReportEvidenceBundle,
} from '../src/report/contracts.js'
import { CountryOutageReportCompiler } from '../src/report/report-compiler.js'
import {
  createA4CertificationScenarioClient,
  type A4CertificationScenarioId,
} from '../src/pi/model-certification-scenarios.js'
import {
  COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_RELATIVE_PATH,
  COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
  COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_RELATIVE_PATH,
  initializeA4CandidateActivityLedger,
  loadCountryOutageDependencyRiskException,
  loadPiModelCandidate,
  FormalPiRunError,
  openCandidateActivityLedger,
  parsePiModelCertificationManifest,
  PiModelCertificationError,
  reconcileA4PreLedgerFailure,
  reconcileA4PreLedgerHistoricalBilledAmount,
  reconcileA4PreLedgerHistoricalUsage,
  runA4ModelCandidateCertification,
  writeCurrentProviderPriceAttestation,
  type CandidateActivityBudgetPolicy,
  type PiSessionFactory,
} from '../src/pi/index.js'
import {
  COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
  initializeCandidateActivityLedgerWithPreLedgerFailure,
  initializeCleanCandidateActivityLedger,
} from '../src/pi/candidate-activity-ledger.js'
import {
  A4_DATA_THROUGH,
  A4_INCIDENT_ID,
  A4_PUBLICATION_ID,
  A4_REFERENCE,
  a4AsnPage,
  a4ObservationBatch,
} from './helpers/a4-country-outage-fixture.js'

type TestSessionAgent = Awaited<
  ReturnType<PiSessionFactory>
>['session']['agent']

function inertProviderAgent(): TestSessionAgent {
  return {
    streamFunction(model, _context, options) {
      const stream = providerMessageStream(
        model,
        [{ type: 'text', text: 'unused-provider-result' }],
        'stop',
      )
      if (options?.onPayload === undefined) return stream
      const payload = {
        model: model.id,
        messages: [],
        stream: true,
      }
      let prepared:
        | Promise<unknown>
        | undefined
      const prepare = (): Promise<unknown> => {
        prepared ??= Promise.resolve(
          options.onPayload?.(payload, model),
        )
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
    usage: assistantUsage(1, 1),
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
  model: TestProviderModel = fakeCatalogModel(),
  context: Parameters<TestSessionAgent['streamFunction']>[1] = {
    messages: [],
  },
): Promise<void> {
  for (let index = 0; index < count; index += 1) {
    const stream = await agent.streamFunction(model, context)
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

const TEST_DIRECTORY = mkdtempSync(
  join(tmpdir(), 'domeye-a4-model-runner-test-'),
)

after(() => {
  rmSync(TEST_DIRECTORY, { recursive: true, force: true })
})

async function testDirectory(label: string): Promise<{
  root: string
  authPath: string
  registryPath: string
}> {
  const root = join(TEST_DIRECTORY, label)
  mkdirSync(root, { mode: 0o700 })
  const authPath = join(root, 'deepseek-auth.json')
  writeFileSync(
    authPath,
    JSON.stringify({
      deepseek: {
        type: 'api_key',
        key: 'integration-test-key-never-persisted',
      },
    }),
    { encoding: 'utf8', mode: 0o600 },
  )
  chmodSync(authPath, 0o600)
  const registryPath = join(root, 'registry.json')
  writeFileSync(
    registryPath,
    `${JSON.stringify(
      {
        schemaVersion: 'country_outage_pi_certified_models_v1',
        registryVersion: `${label}-registry-v1`,
        status: 'frozen',
        profiles: [],
      },
      null,
      2,
    )}\n`,
    'utf8',
  )
  const loadedCandidate = await loadPiModelCandidate()
  writeCurrentProviderPriceAttestation({
    repositoryRoot: root,
    candidate: loadedCandidate,
    observedAt: '2026-07-29T04:00:00.000Z',
    evidenceSha256: 'e'.repeat(64),
    priceUsdPerMillionTokens: {
      input: '0.14',
      output: '0.28',
      cacheRead: '0.0028',
      cacheWrite: '0',
    },
    now: new Date('2026-07-29T04:00:00.000Z'),
  })
  const activityLedger =
    initializeCleanCandidateActivityLedger({
      repositoryRoot: root,
      policy: activityPolicy(loadedCandidate.resourceSha256),
      recordedAt: new Date('2026-07-29T04:00:00Z'),
    })
  activityLedger.close()
  return { root, authPath, registryPath }
}

function fakeCatalogModel(): NonNullable<
  CreateAgentSessionOptions['model']
> {
  return {
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
    contextWindow: 1_000_000,
    maxTokens: 384_000,
  } as NonNullable<CreateAgentSessionOptions['model']>
}

function fakeRuntime(): ModelRuntime {
  const model = fakeCatalogModel()
  return {
    getError() {
      return undefined
    },
    getModel(provider: string, modelId: string) {
      return provider === 'deepseek' &&
        modelId === 'deepseek-v4-flash'
        ? model
        : undefined
    },
    getProviderAuthStatus() {
      return { configured: true, source: 'stored' }
    },
    async getAvailable() {
      return [model]
    },
  } as unknown as ModelRuntime
}

function compatibleAdapterInspection() {
  return {
    sameNamePreserved: true,
    sourceSha256: 'a'.repeat(64),
  }
}

function activityPolicy(
  candidateResourceSha256: string,
): CandidateActivityBudgetPolicy {
  const maximumSingleReportCostCny = 0.5419008
  return {
    candidateId: 'deepseek-v4-flash-pi-0.82.1-v1',
    candidateResourceSha256,
    provider: 'deepseek',
    model: 'deepseek-v4-flash',
    budgetLimitCny: 20,
    maximumSingleReportCostCny,
    maximumCertificationCostCny:
      maximumSingleReportCostCny * 2,
    conservativeCnyPerUsd: 8,
    priceUsdPerMillionTokens: {
      input: 0.14,
      output: 0.28,
      cacheRead: 0.0028,
      cacheWrite: 0,
    },
  }
}

function fakeStats(): SessionStats {
  return {
    sessionFile: undefined,
    sessionId: 'not-persisted',
    userMessages: 1,
    assistantMessages: 2,
    toolCalls: 2,
    toolResults: 2,
    totalMessages: 5,
    tokens: {
      input: 10_000,
      output: 2_000,
      cacheRead: 0,
      cacheWrite: 0,
      total: 12_000,
    },
    cost: 0.00196,
  }
}

async function executeTool(tool: ToolDefinition): Promise<void> {
  await tool.execute(
    'integration-call',
    {} as never,
    undefined,
    undefined,
    undefined as never,
  )
}

async function validDraftText(): Promise<string> {
  const facts = assembleCountryOutageFacts(a4ObservationBatch())
  const evidence: ReportEvidenceBundle = {
    facts,
    asnPages: [a4AsnPage()],
  }
  return languageBundleText(
    buildDeterministicCountryOutageDraft(evidence),
  )
}

async function semanticallyInvalidDraftText(): Promise<string> {
  const bundle = JSON.parse(await validDraftText()) as {
    slots: Array<{ id: string; text: string }>
  }
  bundle.slots[0]!.text =
    'Prefix×VP 当前下降了 987654321 条，并导致全国用户业务中断。'
  return JSON.stringify(bundle)
}

const VALID_LANGUAGE_SLOT_TEXT: Readonly<
  Record<CountryOutageLanguageSlotId, string>
> = Object.freeze({
  'scope.denominator_explanation':
    'Prefix×VP 描述前缀与固定观测点之间的可见关系；它并非唯一前缀，也不能换算为用户或业务数量。',
  'assessment.evidence_boundary':
    '本报告只支持 BGP 控制面可见性描述，不能据此判断全国数据面状态，也无法认定用户或业务影响、事件原因和责任主体。',
  'address_families.impact_boundary':
    '地址族指标属于路由控制面观测，不能直接换算为用户、业务或实际流量影响。',
  'updates.causality_boundary':
    '相关 UPDATE 活动与可见性变化只构成时间对应；现有证据不足以据此证明因果关系。',
  'resources.resource_boundary':
    '等价资源表示规范化、去重后的路由资源覆盖，并非实际在线 IP 地址，也不能换算成用户或业务数量。',
})

function languageBundleText(
  draft: CountryOutageReportDraft,
): string {
  const plan = buildCountryOutageModelLanguagePlan(draft)
  return JSON.stringify({
    schemaVersion: COUNTRY_OUTAGE_LANGUAGE_SLOT_CONTRACT_VERSION,
    slots: plan.map((item) => ({
      id: item.id,
      text: VALID_LANGUAGE_SLOT_TEXT[item.id],
    })),
  })
}

function sessionFactory(
  draftText: string | readonly string[],
  options: {
    failSecondRun?: boolean
    repairDraftText?: string
    sessions: object[]
    calls: { value: number }
  },
): PiSessionFactory {
  return async (createOptions) => {
    options.calls.value += 1
    const runNumber = options.calls.value
    const activeDraftText =
      typeof draftText === 'string'
        ? draftText
        : draftText[runNumber - 1]
    assert.ok(activeDraftText)
    const finalMessage: Record<string, unknown> = {
      role: 'assistant',
      provider: 'deepseek',
      model: 'deepseek-v4-flash',
      responseModel: 'deepseek-v4-flash',
      stopReason: 'stop',
      usage: assistantUsage(5_000, 1_000),
      content: [{ type: 'text', text: activeDraftText }],
    }
    if (options.failSecondRun && runNumber === 2) {
      delete finalMessage.responseModel
    }
    const messages = [
      {
        role: 'assistant',
        provider: 'deepseek',
        model: 'deepseek-v4-flash',
        responseModel: 'deepseek-v4-flash',
        stopReason: 'toolUse',
        usage: assistantUsage(5_000, 1_000),
        content: [
          {
            type: 'toolCall',
            name: 'country_outage_resolve',
            arguments: {},
          },
          {
            type: 'toolCall',
            name: 'country_outage_get_observation',
            arguments: {},
          },
        ],
      },
      {
        role: 'toolResult',
        toolName: 'country_outage_resolve',
        content: [{ type: 'text', text: 'not-persisted' }],
      },
      {
        role: 'toolResult',
        toolName: 'country_outage_get_observation',
        content: [{ type: 'text', text: 'not-persisted' }],
      },
      finalMessage,
    ]
    const agent = inertProviderAgent()
    let activeTools = [...(createOptions.tools ?? [])]
    let promptCalls = 0
    const session = {
      agent,
      messages,
      getActiveToolNames() {
        return [...activeTools]
      },
      setActiveToolsByName(toolNames: string[]) {
        activeTools = [...toolNames]
      },
      async prompt() {
        promptCalls += 1
        if (promptCalls === 1) {
          await forwardProviderRequests(agent, 1)
          const resolveTool = createOptions.customTools?.find(
            (tool) => tool.name === 'country_outage_resolve',
          )
          const observationTool = createOptions.customTools?.find(
            (tool) => tool.name === 'country_outage_get_observation',
          )
          assert.ok(resolveTool)
          assert.ok(observationTool)
          await executeTool(resolveTool)
          await executeTool(observationTool)
          await forwardProviderRequests(
            agent,
            1,
            fakeCatalogModel(),
            { messages: messages as never[] },
          )
          return
        }
        assert.deepEqual(activeTools, [])
        messages.push({
          role: 'assistant',
          provider: 'deepseek',
          model: 'deepseek-v4-flash',
          responseModel: 'deepseek-v4-flash',
          stopReason: 'stop',
          usage: assistantUsage(5_000, 1_000),
          content: [
            {
              type: 'text',
              text:
                options.repairDraftText ?? activeDraftText,
            },
          ],
        })
        await forwardProviderRequests(
          agent,
          1,
          fakeCatalogModel(),
          { messages: messages as never[] },
        )
      },
      async abort() {},
      getSessionStats() {
        if (promptCalls === 1) return fakeStats()
        return {
          ...fakeStats(),
          userMessages: 2,
          assistantMessages: 3,
          totalMessages: 7,
          tokens: {
            input: 15_000,
            output: 3_000,
            cacheRead: 0,
            cacheWrite: 0,
            total: 18_000,
          },
          cost: 0.00294,
        }
      },
      dispose() {},
    }
    options.sessions.push(session)
    return { session }
  }
}

async function scenarioDraftText(
  scenarioId: A4CertificationScenarioId,
): Promise<string> {
  const baseClient = {
    async getObservationBatch(reference: string) {
      assert.equal(reference, A4_REFERENCE)
      return structuredClone(a4ObservationBatch())
    },
    async getAsns() {
      return structuredClone(a4AsnPage())
    },
  }
  const document = await new CountryOutageReportCompiler({
    client: createA4CertificationScenarioClient(
      baseClient,
      scenarioId,
    ),
    narrator: new DeterministicAcceptanceNarrator(),
    now: () => new Date('2026-07-29T10:00:00Z'),
  }).compile(A4_REFERENCE)
  return languageBundleText(document.draft)
}

function rejectedAfterTwoProviderRoundsSessionFactory(
  code: 'model_attempt_timeout' | 'aborted',
): PiSessionFactory {
  return async () => {
    const agent = inertProviderAgent()
    const session = {
      agent,
      messages: [
        {
          role: 'assistant',
          provider: 'deepseek',
          model: 'deepseek-v4-flash',
          responseModel: 'deepseek-v4-flash',
          stopReason: 'toolUse',
          usage: assistantUsage(5_000, 1_000),
          content: [
            {
              type: 'toolCall',
              name: 'country_outage_resolve',
              arguments: {},
            },
          ],
        },
        {
          role: 'toolResult',
          toolName: 'country_outage_resolve',
          content: [{ type: 'text', text: 'not-persisted' }],
        },
        {
          role: 'assistant',
          provider: 'deepseek',
          model: 'deepseek-v4-flash',
          responseModel: 'deepseek-v4-flash',
          stopReason: 'toolUse',
          usage: assistantUsage(5_000, 1_000),
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
          content: [{ type: 'text', text: 'not-persisted' }],
        },
      ],
      async prompt() {
        await forwardProviderRequests(agent, 2)
        throw new FormalPiRunError(code)
      },
      async abort() {},
      getSessionStats() {
        return fakeStats()
      },
      dispose() {},
    }
    return { session }
  }
}

function sha256(content: Buffer): string {
  return createHash('sha256').update(content).digest('hex')
}

test('价格证明缺失、过期、未来、上调或候选漂移均在 auth、ModelRuntime 与 Domeye 前失败关闭', async (context) => {
  for (const scenario of [
    {
      label: 'missing',
      expectedCode: 'candidate_price_attestation_missing',
      now: '2026-07-29T05:00:00.000Z',
      async mutate(root: string) {
        rmSync(
          resolve(
            root,
            COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_RELATIVE_PATH,
          ),
        )
      },
    },
    {
      label: 'expired',
      expectedCode: 'candidate_price_attestation_expired',
      now: '2026-07-30T04:00:00.000Z',
      async mutate() {},
    },
    {
      label: 'future',
      expectedCode:
        'candidate_price_attestation_future_observation',
      now: '2026-07-29T03:59:59.999Z',
      async mutate() {},
    },
    {
      label: 'insufficient-runway',
      expectedCode:
        'candidate_price_attestation_insufficient_runway',
      now: '2026-07-30T03:50:00.000Z',
      async mutate() {},
    },
    {
      label: 'price-increase',
      expectedCode: 'candidate_price_rebudget_required',
      now: '2026-07-29T05:00:00.000Z',
      async mutate(root: string) {
        const loaded = await loadPiModelCandidate()
        writeCurrentProviderPriceAttestation({
          repositoryRoot: root,
          candidate: {
            ...loaded,
            candidate: {
              ...loaded.candidate,
              catalog: {
                ...loaded.candidate.catalog,
                priceUsdPerMillionTokens: {
                  ...loaded.candidate.catalog
                    .priceUsdPerMillionTokens,
                  input: 1.14,
                },
              },
            },
          },
          observedAt: '2026-07-29T04:00:00.000Z',
          evidenceSha256: 'f'.repeat(64),
          priceUsdPerMillionTokens: {
            input: '1.14',
            output: '0.28',
            cacheRead: '0.0028',
            cacheWrite: '0',
          },
          now: new Date('2026-07-29T04:00:00.000Z'),
        })
      },
    },
    {
      label: 'candidate-drift',
      expectedCode:
        'candidate_price_attestation_candidate_drift',
      now: '2026-07-29T05:00:00.000Z',
      async mutate(root: string) {
        const loaded = await loadPiModelCandidate()
        writeCurrentProviderPriceAttestation({
          repositoryRoot: root,
          candidate: {
            ...loaded,
            resourceSha256: 'b'.repeat(64),
          },
          observedAt: '2026-07-29T04:00:00.000Z',
          evidenceSha256: 'c'.repeat(64),
          priceUsdPerMillionTokens: {
            input: '0.14',
            output: '0.28',
            cacheRead: '0.0028',
            cacheWrite: '0',
          },
          now: new Date('2026-07-29T04:00:00.000Z'),
        })
      },
    },
  ] as const) {
    await context.test(scenario.label, async () => {
      const paths = await testDirectory(
        `price-gate-${scenario.label}`,
      )
      await scenario.mutate(paths.root)
      const ledgerPath = resolve(
        paths.root,
        COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
      )
      const anchorPath = resolve(
        paths.root,
        COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_RELATIVE_PATH,
      )
      const ledgerBefore = readFileSync(ledgerPath)
      const anchorBefore = readFileSync(anchorPath)
      const counters = {
        runtime: 0,
        client: 0,
        session: 0,
        pdf: 0,
      }
      await assert.rejects(
        runA4ModelCandidateCertification({
          authPath: '/definitely/not/read/deepseek-auth.json',
          domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
          pythonExecutable: '/not/used/python',
          fontPath: '/not/used/font.ttf',
          dependencies: {
            registryPath: paths.registryPath,
            repositoryRoot: paths.root,
            now: () => new Date(scenario.now),
            responseModelAdapterInspector:
              compatibleAdapterInspection,
            runtimeFactory: async () => {
              counters.runtime += 1
              return fakeRuntime()
            },
            clientFactory() {
              counters.client += 1
              throw new Error('价格门禁后不得创建 Domeye client')
            },
            sessionFactory: async () => {
              counters.session += 1
              throw new Error('价格门禁后不得创建 Pi session')
            },
            pdfRendererFactory() {
              counters.pdf += 1
              throw new Error('价格门禁后不得创建 PDF renderer')
            },
          },
        }),
        (error: unknown) =>
          error instanceof PiModelCertificationError &&
          error.code === scenario.expectedCode,
      )
      assert.deepEqual(counters, {
        runtime: 0,
        client: 0,
        session: 0,
        pdf: 0,
      })
      assert.deepEqual(readFileSync(ledgerPath), ledgerBefore)
      assert.deepEqual(readFileSync(anchorPath), anchorBefore)
      assert.equal(
        existsSync(
          resolve(
            dirname(ledgerPath),
            '.deepseek-v4-flash-pi-0.82.1-v1-activity-v1.lock',
          ),
        ),
        false,
      )
    })
  }
})

test('启动检查后时间逼近到期时，在读取 auth 和创建 ModelRuntime 前二次失败关闭', async () => {
  const paths = await testDirectory(
    'price-gate-second-check-before-auth',
  )
  let nowCalls = 0
  let runtimeCalls = 0
  let clientCalls = 0
  await assert.rejects(
    runA4ModelCandidateCertification({
      authPath: '/definitely/not/read/deepseek-auth.json',
      domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
      pythonExecutable: '/not/used/python',
      fontPath: '/not/used/font.ttf',
      dependencies: {
        registryPath: paths.registryPath,
        repositoryRoot: paths.root,
        now: () => {
          nowCalls += 1
          return new Date(
            nowCalls === 1
              ? '2026-07-29T05:00:00.000Z'
              : '2026-07-30T03:50:00.000Z',
          )
        },
        responseModelAdapterInspector:
          compatibleAdapterInspection,
        runtimeFactory: async () => {
          runtimeCalls += 1
          return fakeRuntime()
        },
        clientFactory() {
          clientCalls += 1
          throw new Error('价格二次门禁后不得创建 Domeye client')
        },
      },
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code ===
        'candidate_price_attestation_insufficient_runway',
  )
  assert.equal(nowCalls, 2)
  assert.equal(runtimeCalls, 0)
  assert.equal(clientCalls, 0)
})

test('候选预检失败时 Domeye、Pi 会话、PDF 和证据目录均为零触达', async (context) => {
  for (const scenario of [
    {
      label: 'missing-auth',
      expectedCode: 'candidate_auth_required',
      authPath: '',
      compatibleAdapter: true,
    },
    {
      label: 'unapproved-adapter',
      expectedCode:
        'candidate_response_model_adapter_unsupported',
      authPath: 'configured',
      compatibleAdapter: false,
    },
  ] as const) {
    await context.test(scenario.label, async () => {
      const paths = await testDirectory(`preflight-${scenario.label}`)
      const counters = {
        runtime: 0,
        client: 0,
        session: 0,
        pdf: 0,
      }
      const artifactRoot = join(paths.root, 'artifacts')
      await assert.rejects(
        runA4ModelCandidateCertification({
          authPath:
            scenario.authPath === 'configured'
              ? paths.authPath
              : scenario.authPath,
          domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
          pythonExecutable: '/not/used/python',
          fontPath: '/not/used/font.ttf',
          dependencies: {
            registryPath: paths.registryPath,
            repositoryRoot: paths.root,
            now: () => new Date('2026-07-29T05:00:00.000Z'),
            ...(scenario.compatibleAdapter
              ? {
                  responseModelAdapterInspector:
                    compatibleAdapterInspection,
                }
              : {
                  responseModelAdapterInspector: () => ({
                    sameNamePreserved: false,
                    sourceSha256:
                      '0d50250fe2931e66e2078279a397814202e1ecddee58faf4b8bc04c278da177a',
                  }),
                }),
            runtimeFactory: async () => {
              counters.runtime += 1
              return fakeRuntime()
            },
            clientFactory() {
              counters.client += 1
              throw new Error('预检失败后不得创建 Domeye client')
            },
            sessionFactory: async () => {
              counters.session += 1
              throw new Error('预检失败后不得创建 Pi session')
            },
            pdfRendererFactory() {
              counters.pdf += 1
              throw new Error('预检失败后不得创建 PDF renderer')
            },
          },
        }),
        (error: unknown) =>
          error instanceof PiModelCertificationError &&
          error.code === scenario.expectedCode,
      )
      assert.deepEqual(counters, {
        runtime: 0,
        client: 0,
        session: 0,
        pdf: 0,
      })
      assert.equal(existsSync(artifactRoot), false)
    })
  }
})

test('正式认证在 ledger 或 anchor 任一及两者缺失时均先于 ModelRuntime 失败关闭', async (context) => {
  for (const scenario of [
    {
      label: 'ledger-missing',
      removeLedger: true,
      removeAnchor: false,
    },
    {
      label: 'anchor-missing',
      removeLedger: false,
      removeAnchor: true,
    },
    {
      label: 'ledger-and-anchor-missing',
      removeLedger: true,
      removeAnchor: true,
    },
  ] as const) {
    await context.test(scenario.label, async () => {
      const paths = await testDirectory(
        `activity-state-${scenario.label}`,
      )
      const ledgerPath = resolve(
        paths.root,
        COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
      )
      const anchorPath = resolve(
        paths.root,
        COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_RELATIVE_PATH,
      )
      if (scenario.removeLedger) rmSync(ledgerPath)
      if (scenario.removeAnchor) rmSync(anchorPath)
      const registryBefore = readFileSync(
        paths.registryPath,
        'utf8',
      )
      const counters = {
        runtime: 0,
        client: 0,
        session: 0,
        pdf: 0,
      }

      await assert.rejects(
        runA4ModelCandidateCertification({
          authPath: paths.authPath,
          domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
          pythonExecutable: '/not/used/python',
          fontPath: '/not/used/font.ttf',
          dependencies: {
            registryPath: paths.registryPath,
            repositoryRoot: paths.root,
            now: () => new Date('2026-07-29T05:00:00.000Z'),
            responseModelAdapterInspector:
              compatibleAdapterInspection,
            runtimeFactory: async () => {
              counters.runtime += 1
              return fakeRuntime()
            },
            clientFactory() {
              counters.client += 1
              throw new Error('活动账本门禁后不得创建 Domeye client')
            },
            sessionFactory: async () => {
              counters.session += 1
              throw new Error('活动账本门禁后不得创建 Pi session')
            },
            pdfRendererFactory() {
              counters.pdf += 1
              throw new Error('活动账本门禁后不得创建 PDF renderer')
            },
          },
        }),
        (error: unknown) =>
          error instanceof PiModelCertificationError &&
          error.code === 'candidate_activity_audit_failed',
      )
      assert.deepEqual(counters, {
        runtime: 0,
        client: 0,
        session: 0,
        pdf: 0,
      })
      assert.equal(
        readFileSync(paths.registryPath, 'utf8'),
        registryBefore,
      )
      assert.equal(
        existsSync(join(paths.root, 'artifacts')),
        false,
      )
      assert.equal(
        existsSync(ledgerPath),
        !scenario.removeLedger,
      )
      assert.equal(
        existsSync(anchorPath),
        !scenario.removeAnchor,
      )
    })
  }
})

test('产品 clean initializer 只建立一次零成本 resolved genesis', async () => {
  const repositoryRoot = join(
    TEST_DIRECTORY,
    'product-clean-initializer',
  )
  mkdirSync(repositoryRoot, { mode: 0o700 })
  const snapshot = await initializeA4CandidateActivityLedger({
    repositoryRoot,
    now: () => new Date('2026-07-29T04:09:00Z'),
  })
  assert.equal(snapshot.recordCount, 1)
  assert.equal(snapshot.committedCostCny, 0)
  assert.equal(snapshot.historicalUsageStatus, 'resolved')

  for (const operation of [
    () =>
      initializeA4CandidateActivityLedger({
        repositoryRoot,
        now: () => new Date('2026-07-29T04:09:01Z'),
      }),
    () =>
      reconcileA4PreLedgerFailure({
        repositoryRoot,
        now: () => new Date('2026-07-29T04:09:02Z'),
      }),
  ]) {
    await assert.rejects(
      operation(),
      (error: unknown) =>
        error instanceof PiModelCertificationError &&
        error.code === 'candidate_activity_audit_failed',
    )
  }
})

test('显式历史失败迁移只记录旧保守值并保持 unresolved', async () => {
  const paths = await testDirectory('explicit-reconcile-genesis')
  const ledgerPath = resolve(
    paths.root,
    COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
  )
  const anchorPath = resolve(
    paths.root,
    COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_RELATIVE_PATH,
  )
  const registryBefore = readFileSync(paths.registryPath, 'utf8')
  rmSync(ledgerPath)
  rmSync(anchorPath)

  const snapshot = await reconcileA4PreLedgerFailure({
    repositoryRoot: paths.root,
    now: () => new Date('2026-07-29T04:10:00Z'),
  })
  assert.equal(snapshot.recordCount, 1)
  assert.equal(
    snapshot.committedCostCny,
    COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
  )
  assert.equal(snapshot.historicalUsageStatus, 'unresolved')
  assert.equal(existsSync(ledgerPath), true)
  assert.equal(existsSync(anchorPath), true)
  assert.equal(
    readFileSync(paths.registryPath, 'utf8'),
    registryBefore,
  )
  assert.equal(existsSync(join(paths.root, 'artifacts')), false)

  const counters = {
    runtime: 0,
    client: 0,
    session: 0,
    pdf: 0,
  }
  await assert.rejects(
    runA4ModelCandidateCertification({
      authPath: paths.authPath,
      domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
      pythonExecutable: '/not/used/python',
      fontPath: '/not/used/font.ttf',
      dependencies: {
        registryPath: paths.registryPath,
        repositoryRoot: paths.root,
        now: () => new Date('2026-07-29T05:00:00.000Z'),
        responseModelAdapterInspector:
          compatibleAdapterInspection,
        runtimeFactory: async () => {
          counters.runtime += 1
          return fakeRuntime()
        },
        clientFactory() {
          counters.client += 1
          throw new Error('历史用量结清前不得创建 Domeye client')
        },
        sessionFactory: async () => {
          counters.session += 1
          throw new Error('历史用量结清前不得创建 Pi session')
        },
        pdfRendererFactory() {
          counters.pdf += 1
          throw new Error('历史用量结清前不得创建 PDF renderer')
        },
      },
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code ===
        'candidate_historical_usage_unresolved',
  )
  assert.deepEqual(counters, {
    runtime: 0,
    client: 0,
    session: 0,
    pdf: 0,
  })

  await assert.rejects(
    reconcileA4PreLedgerFailure({
      repositoryRoot: paths.root,
      now: () => new Date('2026-07-29T04:11:00Z'),
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_activity_audit_failed',
  )
  const records = readFileSync(ledgerPath, 'utf8')
    .trim()
    .split('\n')
  assert.equal(records.length, 1)

  const reconciled = await reconcileA4PreLedgerHistoricalUsage({
    repositoryRoot: paths.root,
    evidenceSha256: 'e'.repeat(64),
    usage: {
      providerRequestCount: 1,
      inputTokens: 64_000,
      outputTokens: 16_384,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    },
    now: () => new Date('2026-07-29T04:12:00Z'),
  })
  assert.equal(reconciled.historicalUsageStatus, 'resolved')
  assert.equal(reconciled.recordCount, 2)
  assert.equal(
    reconciled.committedCostCny,
    COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
  )
})

test('固定 A4 最终账单金额入口不读取认证、不联网且只追加历史结清记录', async () => {
  const paths = await testDirectory(
    'historical-billed-amount-wrapper',
  )
  const ledgerPath = resolve(
    paths.root,
    COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
  )
  const anchorPath = resolve(
    paths.root,
    COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_RELATIVE_PATH,
  )
  rmSync(ledgerPath)
  rmSync(anchorPath)
  await reconcileA4PreLedgerFailure({
    repositoryRoot: paths.root,
    now: () => new Date('2026-07-29T04:10:00Z'),
  })
  const authBefore = readFileSync(paths.authPath)
  const registryBefore = readFileSync(paths.registryPath)

  const reconciled =
    await reconcileA4PreLedgerHistoricalBilledAmount({
      repositoryRoot: paths.root,
      billedAmount: {
        evidenceSha256: 'f'.repeat(64),
        evidenceWindowStartUtc:
          '2026-07-29T03:18:48.000Z',
        evidenceWindowEndUtc: '2026-07-29T03:19:25.000Z',
        evidenceTimezone: 'Asia/Shanghai',
        evidenceAcquiredAt: '2026-07-29T04:11:00.000Z',
        billingFinality: 'settled_final',
        billingScope: 'single_attempt_exact_charge',
        billedAmountDecimal: '0.05',
        billedCurrency: 'CNY',
      },
      now: () => new Date('2026-07-29T04:12:00Z'),
    })

  assert.equal(reconciled.historicalUsageStatus, 'resolved')
  assert.equal(reconciled.recordCount, 2)
  assert.equal(
    reconciled.committedCostCny,
    COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
  )
  assert.deepEqual(readFileSync(paths.authPath), authBefore)
  assert.deepEqual(readFileSync(paths.registryPath), registryBefore)
  assert.equal(existsSync(join(paths.root, 'artifacts')), false)
  const record = JSON.parse(
    readFileSync(ledgerPath, 'utf8').trim().split('\n')[1]!,
  ) as Record<string, unknown>
  assert.equal(record.provider, 'deepseek')
  assert.equal(record.model, 'deepseek-v4-flash')
  assert.equal(
    record.historicalAttemptStartedAtUtc,
    '2026-07-29T03:18:48.543Z',
  )
  assert.equal(
    record.historicalAttemptEndedAtUtc,
    '2026-07-29T03:19:24.681Z',
  )
  assert.equal(record.chargedCostCnyE8, 10_838_016)
})

test('固定 A4 样本经两个独立 Pi 会话生成两份完整报告并原子落盘', async () => {
  const paths = await testDirectory('success')
  const loadedCandidate = await loadPiModelCandidate()
  writeCurrentProviderPriceAttestation({
    repositoryRoot: paths.root,
    candidate: loadedCandidate,
    observedAt: '2026-07-29T04:00:00.000Z',
    evidenceSha256: 'd'.repeat(64),
    priceUsdPerMillionTokens: {
      input: '0.01',
      output: '0.02',
      cacheRead: '0.001',
      cacheWrite: '0',
    },
    now: new Date('2026-07-29T04:00:00.000Z'),
  })
  const registryBefore = readFileSync(paths.registryPath, 'utf8')
  const draftText = await validDraftText()
  const sessions: object[] = []
  const sessionCalls = { value: 0 }
  const clientRuns: number[] = []
  const pdfRuns: number[] = []

  const result = await runA4ModelCandidateCertification({
    authPath: paths.authPath,
    domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
    pythonExecutable: '/not/used/python',
    fontPath: '/not/used/font.ttf',
    dependencies: {
      registryPath: paths.registryPath,
      repositoryRoot: paths.root,
      runtimeFactory: async () => fakeRuntime(),
      responseModelAdapterInspector: compatibleAdapterInspection,
      dependencyRiskException:
        loadCountryOutageDependencyRiskException({
          now: new Date('2026-07-29T10:00:00Z'),
        }),
      now: () => new Date('2026-07-29T10:00:00Z'),
      sessionFactory: sessionFactory(draftText, {
        sessions,
        calls: sessionCalls,
      }),
      clientFactory({ runNumber }) {
        clientRuns.push(runNumber)
        return {
          async getObservationBatch(reference: string) {
            assert.equal(reference, A4_REFERENCE)
            return structuredClone(a4ObservationBatch())
          },
          async getAsns() {
            return structuredClone(a4AsnPage())
          },
        }
      },
      pdfRendererFactory({ runNumber }) {
        pdfRuns.push(runNumber)
        return {
          async render() {
            return Buffer.from(
              `%PDF-1.4\nA4 integration run ${runNumber}\n%%EOF\n`,
              'utf8',
            )
          },
        }
      },
    },
  })

  assert.equal(sessionCalls.value, 2)
  assert.equal(sessions.length, 2)
  assert.notEqual(sessions[0], sessions[1])
  assert.deepEqual(clientRuns, [1, 2])
  assert.deepEqual(pdfRuns, [1, 2])
  assert.deepEqual(result.manifest.provenance, {
    runnerIdentity:
      'country-outage-full-report-integration-test-v1',
    promotable: false,
    certificationFixtureId:
      'a4-iran-country-outage-rrc25-v1',
  })
  assert.equal(
    result.manifest.certificationStartedAt,
    '2026-07-29T10:00:00.000Z',
  )
  const priceAttestation =
    result.manifest.policy.priceAttestation
  assert.ok(priceAttestation)
  assert.equal(
    priceAttestation.candidateResourceSha256,
    result.manifest.candidateResourceSha256,
  )
  assert.equal(priceAttestation.evidenceSha256, 'd'.repeat(64))
  assert.equal(priceAttestation.observedAt, '2026-07-29T04:00:00.000Z')
  assert.equal(priceAttestation.expiresAt, '2026-07-30T04:00:00.000Z')
  assert.deepEqual(priceAttestation.priceUsdPerMillionTokens, {
    input: '0.01',
    output: '0.02',
    cacheRead: '0.001',
    cacheWrite: '0',
  })
  assert.equal(
    parsePiModelCertificationManifest(
      result.manifest,
      loadedCandidate,
    ),
    result.manifest,
  )
  const tamperedPriceDigest = structuredClone(result.manifest)
  assert.ok(tamperedPriceDigest.policy.priceAttestation)
  tamperedPriceDigest.policy.priceAttestation.resourceSha256 =
    '9'.repeat(64)
  assert.throws(
    () =>
      parsePiModelCertificationManifest(
        tamperedPriceDigest,
        loadedCandidate,
      ),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'certification_manifest_invalid',
  )
  assert.deepEqual(
    result.manifest.runs.map(
      (run) => run.checks.providerRequestCount,
    ),
    [2, 2],
  )
  assert.deepEqual(
    result.manifest.runs.map(
      (run) => run.checks.providerRetryAttempts,
    ),
    [0, 0],
  )
  assert.equal(
    readFileSync(paths.registryPath, 'utf8'),
    registryBefore,
  )

  const evidenceDirectory = resolve(
    paths.root,
    result.artifactDirectory,
  )
  assert.equal(existsSync(evidenceDirectory), true)
  const manifestText = readFileSync(
    join(evidenceDirectory, 'manifest.json'),
    'utf8',
  )
  assert.deepEqual(JSON.parse(manifestText), result.manifest)
  const reportAuditManifests: Array<Record<string, unknown>> = []
  const piRunAudits: Array<Record<string, unknown>> = []
  const documents = [1, 2].map((runNumber) => {
    const runDirectory = join(
      evidenceDirectory,
      `run-${runNumber}`,
    )
    const documentBytes = readFileSync(
      join(runDirectory, 'report-document.json'),
    )
    const reportAuditBytes = readFileSync(
      join(runDirectory, 'audit-manifest.json'),
    )
    const piRunAuditBytes = readFileSync(
      join(runDirectory, 'pi-run-audit.json'),
    )
    const markdown = readFileSync(join(runDirectory, 'report.md'))
    const pdf = readFileSync(join(runDirectory, 'report.pdf'))
    const evidence = result.manifest.runs[runNumber - 1]!
    assert.equal(
      sha256(documentBytes),
      evidence.artifacts.reportDocumentSha256,
    )
    assert.equal(
      sha256(reportAuditBytes),
      evidence.artifacts.reportAuditManifestSha256,
    )
    assert.equal(
      sha256(piRunAuditBytes),
      evidence.artifacts.piRunAuditSha256,
    )
    assert.equal(
      sha256(markdown),
      evidence.artifacts.markdownSha256,
    )
    assert.equal(sha256(pdf), evidence.artifacts.pdfSha256)
    assert.equal(pdf.subarray(0, 5).toString('utf8'), '%PDF-')
    const reportAudit = JSON.parse(
      reportAuditBytes.toString('utf8'),
    ) as Record<string, unknown>
    const piRunAudit = JSON.parse(
      piRunAuditBytes.toString('utf8'),
    ) as Record<string, unknown>
    reportAuditManifests.push(reportAudit)
    piRunAudits.push(piRunAudit)
    assert.equal(
      reportAudit.schemaVersion,
      'country_outage_report_audit_manifest_v1',
    )
    assert.equal(
      (reportAudit.factSetIdentity as { factSetId: string })
        .factSetId,
      evidence.factSetId,
    )
    assert.equal(piRunAudit.runtimeIdentity, 'candidate')
    assert.equal(
      piRunAudit.schemaVersion,
      'country_outage_pi_run_audit_v3',
    )
    assert.deepEqual(piRunAudit.narration, {
      acceptedSlotCount: 5,
      baseV5: 'passed',
      finalV5: 'passed',
      mergeInvariant: 'passed',
      mode: 'deterministic-base-with-language-slots-v1',
      modelOutputApplied: true,
      requestedSlotCount: 5,
      slotContractVersion: 'country_outage_language_slots_v1',
    })
    assert.equal(
      (piRunAudit.usage as { assistantMessages: number })
        .assistantMessages,
      evidence.checks.providerRequestCount,
    )
    assert.deepEqual(
      (piRunAudit.tools as { executedNames: string[] })
        .executedNames,
      [
        'country_outage_resolve',
        'country_outage_get_observation',
      ],
    )
    assert.deepEqual(
      piRunAudit.runtimeSecurity,
      {
        dependencyRiskException: {
          exceptionId:
            'country-outage-pi-ghsa-mh99-v99m-4gvg-20260812-v2',
          expiresAt: '2026-08-12T16:00:00Z',
          status: 'active',
        },
        explicitModel: true,
        modelCatalogNetworkRefreshEnabled: false,
        modelResolverEnabled: false,
        modelsJsonEnabled: false,
        packageManagerResolutionEnabled: false,
        providerRetryAttempts: 0,
        forwardedProviderRequestCount: 2,
        structuredOutput: {
          applicability: 'required',
          mechanism:
            'deepseek-json-object-after-required-tools-v1',
          payloadPreparedCount: 1,
        },
        resourceLoaderId:
          'country-outage-static-resource-loader-v1',
        skillBundleSha256: (
          piRunAudit.runtimeSecurity as {
            skillBundleSha256: string
          }
        ).skillBundleSha256,
      },
    )
    return JSON.parse(documentBytes.toString('utf8')) as {
      event: {
        incident_id: string
        country_code: string
        legacy_reference: string
      }
      snapshot: {
        publicationId: string
        revision: number
        dataThrough: string
        collectorId: string
      }
      model: { runtimeIdentity: string }
      factSetId: string
    }
  })
  for (const document of documents) {
    assert.equal(document.event.incident_id, A4_INCIDENT_ID)
    assert.equal(document.event.country_code, 'IR')
    assert.equal(document.event.legacy_reference, A4_REFERENCE)
    assert.equal(
      document.snapshot.publicationId,
      A4_PUBLICATION_ID,
    )
    assert.equal(document.snapshot.revision, 1)
    assert.equal(document.snapshot.dataThrough, A4_DATA_THROUGH)
    assert.equal(document.snapshot.collectorId, 'rrc25')
    assert.equal(document.model.runtimeIdentity, 'candidate')
  }
  assert.equal(documents[0]?.factSetId, documents[1]?.factSetId)
  assert.equal(
    result.manifest.runs[0].evidenceInputSha256,
    result.manifest.runs[1].evidenceInputSha256,
  )
  assert.equal(
    result.manifest.factEquivalence.evidenceInputSha256,
    result.manifest.runs[0].evidenceInputSha256,
  )
  assert.equal(reportAuditManifests.length, 2)
  assert.equal(piRunAudits.length, 2)

  const persistedText = [
    manifestText,
    ...[1, 2].flatMap((runNumber) => {
      const runDirectory = join(
        evidenceDirectory,
        `run-${runNumber}`,
      )
      return [
        readFileSync(
          join(runDirectory, 'report-document.json'),
          'utf8',
        ),
        readFileSync(
          join(runDirectory, 'audit-manifest.json'),
          'utf8',
        ),
        readFileSync(
          join(runDirectory, 'pi-run-audit.json'),
          'utf8',
        ),
        readFileSync(join(runDirectory, 'report.md'), 'utf8'),
      ]
    }),
  ].join('\n')
  assert.doesNotMatch(
    persistedText,
    /integration-test-key-never-persisted/,
  )
  assert.doesNotMatch(persistedText, /deepseek-auth\.json/)
  assert.doesNotMatch(
    persistedText,
    /toolArguments|rawAnswer|sessionId|not-persisted|integration-call/,
  )
  const activityRecords = readFileSync(
    resolve(
      paths.root,
      COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
    ),
    'utf8',
  )
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line) as Record<string, unknown>)
  assert.equal(activityRecords.length, 5)
  for (const settlement of [
    activityRecords[2],
    activityRecords[4],
  ]) {
    assert.equal(settlement?.recordType, 'settlement')
    assert.equal(settlement?.outcome, 'completed')
    assert.equal(settlement?.costBasis, 'actual_usage')
    assert.ok(
      // 结算继续使用冻结候选价，而不是更低的供应商证明价。
      Math.abs(Number(settlement?.chargedCostCny) - 0.01568) <
        1e-12,
    )
    assert.deepEqual(settlement?.usage, {
      providerRequestCount: 2,
      inputTokens: 10_000,
      outputTokens: 2_000,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    })
  }

  const cliSource = readFileSync(
    resolve(
      process.cwd(),
      'src/cli/certify-a4-model-candidate.ts',
    ),
    'utf8',
  )
  assert.doesNotMatch(cliSource, /node:http|\.listen\s*\(/)
})

test('A4 五报告场景套件在 64K×5 次预算门内生成可核验别名证书与隔离制品', async () => {
  const paths = await testDirectory('scenario-suite')
  const loadedCandidate = await loadPiModelCandidate()
  const representative = await validDraftText()
  const scenarioIds = [
    'capability-degraded-final',
    'direction-end-above-start-final',
    'non-final-snapshot',
  ] as const
  const drafts = [
    representative,
    representative,
    ...(await Promise.all(
      scenarioIds.map(async (scenarioId) =>
        await scenarioDraftText(scenarioId),
      ),
    )),
  ]
  const sessions: object[] = []
  const sessionCalls = { value: 0 }
  const clientRuns: number[] = []
  const pdfRuns: number[] = []

  const result = await runA4ModelCandidateCertification({
    authPath: paths.authPath,
    domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
    pythonExecutable: '/not/used/python',
    fontPath: '/not/used/font.ttf',
    dependencies: {
      executeScenarioSuite: true,
      registryPath: paths.registryPath,
      repositoryRoot: paths.root,
      runtimeFactory: async () => fakeRuntime(),
      responseModelAdapterInspector:
        compatibleAdapterInspection,
      dependencyRiskException:
        loadCountryOutageDependencyRiskException({
          now: new Date('2026-07-29T10:00:00Z'),
        }),
      now: () => new Date('2026-07-29T10:00:00Z'),
      sessionFactory: sessionFactory(drafts, {
        sessions,
        calls: sessionCalls,
      }),
      clientFactory({ runNumber }) {
        clientRuns.push(runNumber)
        return {
          async getObservationBatch(reference: string) {
            assert.equal(reference, A4_REFERENCE)
            return structuredClone(a4ObservationBatch())
          },
          async getAsns() {
            return structuredClone(a4AsnPage())
          },
        }
      },
      pdfRendererFactory({ runNumber }) {
        pdfRuns.push(runNumber)
        return {
          async render() {
            return Buffer.from(
              `%PDF-1.4\nA4 scenario suite run ${runNumber}\n%%EOF\n`,
              'utf8',
            )
          },
        }
      },
    },
  })

  assert.equal(sessionCalls.value, 5)
  assert.equal(sessions.length, 5)
  assert.deepEqual(clientRuns, [1, 2, 3, 4, 5])
  assert.deepEqual(pdfRuns, [1, 2, 3, 4, 5])
  assert.equal(
    result.manifest.budget.maximumCertificationCostCny,
    2.709504,
  )
  assert.ok(
    Math.abs(
      result.manifest.budget.actualCertificationCostCny -
        0.0784,
    ) < 1e-12,
  )
  assert.equal(
    parsePiModelCertificationManifest(
      result.manifest,
      loadedCandidate,
    ),
    result.manifest,
  )
  const tamperedScenario = structuredClone(
    result.manifest,
  ) as unknown as {
    scenarioCoverage: {
      scenarios: Array<{ synthetic: boolean }>
    }
  }
  tamperedScenario.scenarioCoverage.scenarios[0]!.synthetic =
    false
  assert.throws(
    () =>
      parsePiModelCertificationManifest(
        tamperedScenario,
        loadedCandidate,
      ),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'certification_manifest_invalid',
  )
  const tamperedAliasValidity = structuredClone(
    result.manifest,
  ) as unknown as {
    certificationProfile: {
      certificationValidUntil: string
    }
  }
  tamperedAliasValidity.certificationProfile.certificationValidUntil =
    '2026-08-06T10:00:00.000Z'
  assert.throws(
    () =>
      parsePiModelCertificationManifest(
        tamperedAliasValidity,
        loadedCandidate,
      ),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'certification_manifest_invalid',
  )
  assert.deepEqual(
    result.manifest.scenarioCoverage?.scenarios.map(
      (scenario) => scenario.scenarioId,
    ),
    scenarioIds,
  )
  assert.equal(
    result.manifest.scenarioCoverage
      ?.boundaryQuestionEngine,
    'deterministic-country-outage-question-engine-v1',
  )
  assert.deepEqual(result.manifest.certificationProfile, {
    modelRevisionKind: 'mutable_alias',
    immutableRevisionAvailable: false,
    limitation:
      '供应方未提供不可变权重 revision；deepseek-v4-flash 是可变别名，可能无痕变化。',
    certificationValidUntil: '2026-08-05T10:00:00.000Z',
    certifiedScenarioSetId:
      'country-outage-rrc25-legal-scenarios-v2',
    certifiedInputScope: 'legal_country_outage_rrc25_v1',
  })

  const evidenceDirectory = resolve(
    paths.root,
    result.artifactDirectory,
  )
  for (const scenarioId of scenarioIds) {
    const scenarioDirectory = join(
      evidenceDirectory,
      `scenario-${scenarioId}`,
    )
    assert.equal(existsSync(scenarioDirectory), true)
    assert.equal(
      readFileSync(
        join(scenarioDirectory, 'CERTIFICATION-ONLY.txt'),
        'utf8',
      ),
      '认证专用合成场景，不是 Domeye 事件事实，不得作为观测报告对外发布。\n',
    )
    for (const filename of [
      'report-document.json',
      'audit-manifest.json',
      'pi-run-audit.json',
      'report.md',
      'report.pdf',
    ]) {
      assert.equal(
        existsSync(join(scenarioDirectory, filename)),
        true,
      )
    }
  }
  const activityRecords = readFileSync(
    resolve(
      paths.root,
      COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
    ),
    'utf8',
  )
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line) as Record<string, unknown>)
  assert.equal(activityRecords.length, 11)
  assert.deepEqual(
    activityRecords
      .filter((record) => record.recordType === 'settlement')
      .map((record) => record.runNumber),
    [1, 2, 3, 4, 5],
  )
})

test('两份报告均经一次受控整份修订后仍可通过完整 A4 runner 并留存两次尝试审计', async () => {
  const paths = await testDirectory(
    'success-after-controlled-repair',
  )
  const registryBefore = readFileSync(paths.registryPath, 'utf8')
  const validDraft = await validDraftText()
  const invalidDraft = await semanticallyInvalidDraftText()
  const sessions: object[] = []
  const sessionCalls = { value: 0 }

  const result = await runA4ModelCandidateCertification({
    authPath: paths.authPath,
    domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
    pythonExecutable: '/not/used/python',
    fontPath: '/not/used/font.ttf',
    dependencies: {
      registryPath: paths.registryPath,
      repositoryRoot: paths.root,
      runtimeFactory: async () => fakeRuntime(),
      responseModelAdapterInspector: compatibleAdapterInspection,
      dependencyRiskException:
        loadCountryOutageDependencyRiskException({
          now: new Date('2026-07-29T10:20:00Z'),
        }),
      now: () => new Date('2026-07-29T10:20:00Z'),
      sessionFactory: sessionFactory(invalidDraft, {
        repairDraftText: validDraft,
        sessions,
        calls: sessionCalls,
      }),
      clientFactory() {
        return {
          async getObservationBatch(reference: string) {
            assert.equal(reference, A4_REFERENCE)
            return structuredClone(a4ObservationBatch())
          },
          async getAsns() {
            return structuredClone(a4AsnPage())
          },
        }
      },
      pdfRendererFactory({ runNumber }) {
        return {
          async render() {
            return Buffer.from(
              `%PDF-1.4\nA4 repaired integration run ${runNumber}\n%%EOF\n`,
              'utf8',
            )
          },
        }
      },
    },
  })

  assert.equal(sessionCalls.value, 2)
  assert.equal(sessions.length, 2)
  assert.deepEqual(
    result.manifest.runs.map(
      (run) => run.checks.providerRequestCount,
    ),
    [3, 3],
  )
  assert.deepEqual(
    result.manifest.runs.map(
      (run) => run.checks.providerRetryAttempts,
    ),
    [0, 0],
  )
  assert.equal(
    readFileSync(paths.registryPath, 'utf8'),
    registryBefore,
  )

  const evidenceDirectory = resolve(
    paths.root,
    result.artifactDirectory,
  )
  const modelAttempts = [1, 2].map((runNumber) => {
    const audit = JSON.parse(
      readFileSync(
        join(
          evidenceDirectory,
          `run-${runNumber}`,
          'pi-run-audit.json',
        ),
        'utf8',
      ),
    ) as {
      modelAttempt: {
        timeoutMs: number
        maximumAttempts: number
        executedAttempts: number
      }
      runtimeSecurity: {
        forwardedProviderRequestCount: number
        structuredOutput: {
          applicability: string
          mechanism: string
          payloadPreparedCount: number
        }
      }
    }
    assert.equal(
      audit.runtimeSecurity.forwardedProviderRequestCount,
      3,
    )
    assert.deepEqual(
      audit.runtimeSecurity.structuredOutput,
      {
        applicability: 'required',
        mechanism:
          'deepseek-json-object-after-required-tools-v1',
        payloadPreparedCount: 2,
      },
    )
    return audit.modelAttempt
  })
  assert.deepEqual(modelAttempts, [
    {
      timeoutMs: 75_000,
      maximumAttempts: 2,
      executedAttempts: 2,
    },
    {
      timeoutMs: 75_000,
      maximumAttempts: 2,
      executedAttempts: 2,
    },
  ])
})

test('并发方已经持有同一 evidence lock 时失败方不得删除该锁', async () => {
  const paths = await testDirectory('lock-owner')
  const draftText = await validDraftText()
  const certify = async (repositoryRoot: string) => {
    const sessions: object[] = []
    const calls = { value: 0 }
    return await runA4ModelCandidateCertification({
      authPath: paths.authPath,
      domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
      pythonExecutable: '/not/used/python',
      fontPath: '/not/used/font.ttf',
      dependencies: {
        registryPath: paths.registryPath,
        repositoryRoot,
        runtimeFactory: async () => fakeRuntime(),
        responseModelAdapterInspector:
          compatibleAdapterInspection,
        dependencyRiskException:
          loadCountryOutageDependencyRiskException({
            now: new Date('2026-07-29T10:30:00Z'),
          }),
        now: () => new Date('2026-07-29T10:30:00Z'),
        sessionFactory: sessionFactory(draftText, {
          sessions,
          calls,
        }),
        clientFactory() {
          return {
            async getObservationBatch() {
              return structuredClone(a4ObservationBatch())
            },
            async getAsns() {
              return structuredClone(a4AsnPage())
            },
          }
        },
        pdfRendererFactory({ runNumber }) {
          return {
            async render() {
              return Buffer.from(
                `%PDF-1.4\nlock run ${runNumber}\n%%EOF\n`,
              )
            },
          }
        },
      },
    })
  }

  const first = await certify(paths.root)
  const contendedRoot = join(TEST_DIRECTORY, 'lock-contended-root')
  mkdirSync(contendedRoot, { mode: 0o700 })
  const loadedCandidate = await loadPiModelCandidate()
  writeCurrentProviderPriceAttestation({
    repositoryRoot: contendedRoot,
    candidate: loadedCandidate,
    observedAt: '2026-07-29T04:00:00.000Z',
    evidenceSha256: 'e'.repeat(64),
    priceUsdPerMillionTokens: {
      input: '0.14',
      output: '0.28',
      cacheRead: '0.0028',
      cacheWrite: '0',
    },
    now: new Date('2026-07-29T04:00:00.000Z'),
  })
  const contendedActivityLedger =
    initializeCleanCandidateActivityLedger({
      repositoryRoot: contendedRoot,
      policy: activityPolicy(loadedCandidate.resourceSha256),
      recordedAt: new Date('2026-07-29T04:00:00Z'),
    })
  contendedActivityLedger.close()
  const evidenceParent = join(
    contendedRoot,
    'artifacts',
    'country-outage-agent',
    'a4-model-certification',
  )
  mkdirSync(evidenceParent, { recursive: true, mode: 0o700 })
  const lockPath = join(
    evidenceParent,
    `.${first.evidenceId}.lock`,
  )
  writeFileSync(lockPath, 'concurrent-owner', {
    encoding: 'utf8',
    mode: 0o600,
  })

  await assert.rejects(
    certify(contendedRoot),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_artifact_write_failed',
  )
  assert.equal(readFileSync(lockPath, 'utf8'), 'concurrent-owner')
  assert.equal(
    existsSync(join(evidenceParent, first.evidenceId)),
    false,
  )
})

test('修订后报告语义校验失败时在 Pi accepted 审计前以固定安全码关闭', async () => {
  const paths = await testDirectory('report-validation-failure')
  const registryBefore = readFileSync(paths.registryPath)
  const modelBodyMarker =
    'raw-model-validation-body-never-persisted'
  const bundle = JSON.parse(await validDraftText()) as {
    slots: Array<{ id: string; text: string }>
  }
  bundle.slots[0]!.text =
    `Prefix×VP 的普通数字和事件结论 ${modelBodyMarker} 不得进入报告。`
  const sessions: object[] = []
  const sessionCalls = { value: 0 }
  let pdfCalls = 0

  await assert.rejects(
    runA4ModelCandidateCertification({
      authPath: paths.authPath,
      domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
      pythonExecutable: '/not/used/python',
      fontPath: '/not/used/font.ttf',
      dependencies: {
        registryPath: paths.registryPath,
        repositoryRoot: paths.root,
        runtimeFactory: async () => fakeRuntime(),
        responseModelAdapterInspector:
          compatibleAdapterInspection,
        dependencyRiskException:
          loadCountryOutageDependencyRiskException({
            now: new Date('2026-07-29T11:30:00Z'),
          }),
        now: () => new Date('2026-07-29T11:30:00Z'),
        sessionFactory: sessionFactory(JSON.stringify(bundle), {
          sessions,
          calls: sessionCalls,
        }),
        clientFactory() {
          return {
            async getObservationBatch() {
              return structuredClone(a4ObservationBatch())
            },
            async getAsns() {
              return structuredClone(a4AsnPage())
            },
          }
        },
        pdfRendererFactory() {
          pdfCalls += 1
          throw new Error('报告语义校验失败后不得创建 PDF renderer')
        },
      },
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_runner_failed' &&
      error.message === 'DeepSeek 候选完整报告运行失败',
  )

  assert.equal(sessionCalls.value, 1)
  assert.equal(sessions.length, 1)
  assert.equal(pdfCalls, 0)
  assert.deepEqual(readFileSync(paths.registryPath), registryBefore)
  assert.equal(existsSync(join(paths.root, 'artifacts')), false)

  const ledgerText = readFileSync(
    resolve(
      paths.root,
      COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
    ),
    'utf8',
  )
  const records = ledgerText
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line) as Record<string, unknown>)
  assert.equal(records.length, 3)
  assert.equal(records[1]?.recordType, 'reservation')
  assert.equal(records[2]?.recordType, 'settlement')
  assert.equal(records[2]?.outcome, 'rejected')
  assert.equal(
    records[2]?.formalRejectionCode,
    'report_payload_invalid',
  )
  assert.equal(records[2]?.candidateRejectionCode, null)
  assert.equal(records[2]?.costBasis, 'worst_case_reservation')
  assert.ok(
    Math.abs(Number(records[2]?.chargedCostCny) - 0.5419008) <
      1e-12,
  )
  assert.equal(records[2]?.usage, null)
  const loadedCandidate = await loadPiModelCandidate()
  const reopenedLedger = openCandidateActivityLedger({
    repositoryRoot: paths.root,
    policy: activityPolicy(loadedCandidate.resourceSha256),
  })
  assert.equal(reopenedLedger.snapshot().recordCount, 3)
  reopenedLedger.close()

  for (const marker of [
    modelBodyMarker,
    paths.authPath,
    'integration-test-key-never-persisted',
    'not-persisted',
  ]) {
    assert.equal(ledgerText.includes(marker), false)
  }
  assert.doesNotMatch(
    ledgerText,
    /errorMessage|"errors"|"cause"|prompt|toolArguments|toolResult|authPath|apiKey|modelBody/i,
  )
})

test('第二次完整报告失败时第一轮内存结果不得产生任何证据目录', async () => {
  const paths = await testDirectory('second-run-failure')
  const draftText = await validDraftText()
  const sessions: object[] = []
  const sessionCalls = { value: 0 }
  let pdfCalls = 0

  await assert.rejects(
    runA4ModelCandidateCertification({
      authPath: paths.authPath,
      domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
      pythonExecutable: '/not/used/python',
      fontPath: '/not/used/font.ttf',
      dependencies: {
        registryPath: paths.registryPath,
        repositoryRoot: paths.root,
        runtimeFactory: async () => fakeRuntime(),
        responseModelAdapterInspector:
          compatibleAdapterInspection,
        dependencyRiskException:
          loadCountryOutageDependencyRiskException({
            now: new Date('2026-07-29T11:00:00Z'),
          }),
        now: () => new Date('2026-07-29T11:00:00Z'),
        sessionFactory: sessionFactory(draftText, {
          failSecondRun: true,
          sessions,
          calls: sessionCalls,
        }),
        clientFactory() {
          return {
            async getObservationBatch() {
              return structuredClone(a4ObservationBatch())
            },
            async getAsns() {
              return structuredClone(a4AsnPage())
            },
          }
        },
        pdfRendererFactory() {
          return {
            async render() {
              pdfCalls += 1
              return Buffer.from('%PDF-1.4\nfirst-run\n%%EOF\n')
            },
          }
        },
      },
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_runner_failed',
  )
  assert.equal(sessionCalls.value, 2)
  assert.equal(pdfCalls, 1)
  assert.equal(existsSync(join(paths.root, 'artifacts')), false)
})

test('相同 snapshot 与 factSet 下 ASN 分页证据漂移时认证失败且零写入', async () => {
  const paths = await testDirectory('asn-evidence-drift')
  const draftText = await validDraftText()
  const sessions: object[] = []
  const sessionCalls = { value: 0 }
  let pdfCalls = 0

  await assert.rejects(
    runA4ModelCandidateCertification({
      authPath: paths.authPath,
      domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
      pythonExecutable: '/not/used/python',
      fontPath: '/not/used/font.ttf',
      dependencies: {
        registryPath: paths.registryPath,
        repositoryRoot: paths.root,
        runtimeFactory: async () => fakeRuntime(),
        responseModelAdapterInspector:
          compatibleAdapterInspection,
        dependencyRiskException:
          loadCountryOutageDependencyRiskException({
            now: new Date('2026-07-29T12:00:00Z'),
          }),
        now: () => new Date('2026-07-29T12:00:00Z'),
        sessionFactory: sessionFactory(draftText, {
          sessions,
          calls: sessionCalls,
        }),
        clientFactory({ runNumber }) {
          return {
            async getObservationBatch() {
              return structuredClone(a4ObservationBatch())
            },
            async getAsns() {
              const page = structuredClone(a4AsnPage())
              if (runNumber === 2) {
                page.items[0]!.baseline_prefix_vp_count = 11
              }
              return page
            },
          }
        },
        pdfRendererFactory() {
          return {
            async render() {
              pdfCalls += 1
              return Buffer.from('%PDF-1.4\nasn-drift\n%%EOF\n')
            },
          }
        },
      },
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_fact_equivalence_failed',
  )
  assert.equal(sessionCalls.value, 2)
  assert.equal(pdfCalls, 2)
  assert.equal(existsSync(join(paths.root, 'artifacts')), false)
})

test('真实候选多轮后最后一次 provider 失败按整份预留结算且不采信部分 usage', async () => {
  const paths = await testDirectory('provider-failure-activity-audit')
  const registryBefore = readFileSync(paths.registryPath, 'utf8')
  const secretMarkers = {
    key: 'sk-test-secret-never-persisted',
    authPath: paths.authPath,
    prompt: 'raw-prompt-never-persisted',
    answer: 'raw-model-answer-never-persisted',
    toolArguments: 'raw-tool-arguments-never-persisted',
    toolResult: 'raw-tool-result-never-persisted',
    sessionId: 'raw-session-id-never-persisted',
  }
  let pdfCalls = 0

  await assert.rejects(
    runA4ModelCandidateCertification({
      authPath: paths.authPath,
      domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
      pythonExecutable: '/not/used/python',
      fontPath: '/not/used/font.ttf',
      dependencies: {
        registryPath: paths.registryPath,
        repositoryRoot: paths.root,
        runtimeFactory: async () => fakeRuntime(),
        responseModelAdapterInspector:
          compatibleAdapterInspection,
        dependencyRiskException:
          loadCountryOutageDependencyRiskException({
            now: new Date('2026-07-29T13:00:00Z'),
          }),
        now: () => new Date('2026-07-29T13:00:00Z'),
        sessionFactory: async (createOptions) => {
          let providerCalls = 0
          const agent: TestSessionAgent = {
            streamFunction(model) {
              providerCalls += 1
              if (providerCalls === 3) {
                throw new Error(
                  `${secretMarkers.key}:${secretMarkers.answer}`,
                )
              }
              return providerMessageStream(
                model,
                [
                  {
                    type: 'toolCall',
                    name:
                      providerCalls === 1
                        ? 'country_outage_resolve'
                        : 'country_outage_get_observation',
                    arguments: {},
                  },
                ],
                'toolUse',
              )
            },
          }
          const messages = [
            {
              role: 'user',
              content: [
                {
                  type: 'text',
                  text: secretMarkers.prompt,
                },
              ],
            },
            {
              role: 'assistant',
              provider: 'deepseek',
              model: 'deepseek-v4-flash',
              responseModel: 'deepseek-v4-flash',
              stopReason: 'toolUse',
              usage: assistantUsage(500, 100, 25),
              content: [
                {
                  type: 'text',
                  text: secretMarkers.answer,
                },
                {
                  type: 'toolCall',
                  name: 'country_outage_resolve',
                  arguments: {
                    hidden: secretMarkers.toolArguments,
                  },
                },
              ],
            },
            {
              role: 'toolResult',
              toolName: 'country_outage_resolve',
              content: [
                {
                  type: 'text',
                  text: secretMarkers.toolResult,
                },
              ],
            },
            {
              role: 'assistant',
              provider: 'deepseek',
              model: 'deepseek-v4-flash',
              responseModel: 'deepseek-v4-flash',
              stopReason: 'toolUse',
              usage: assistantUsage(500, 100, 25),
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
              content: [
                {
                  type: 'text',
                  text: secretMarkers.toolResult,
                },
              ],
            },
          ]
          const session = {
            agent,
            messages,
            async prompt() {
              const resolveTool = createOptions.customTools?.find(
                (tool) => tool.name === 'country_outage_resolve',
              )
              const observationTool =
                createOptions.customTools?.find(
                  (tool) =>
                    tool.name ===
                    'country_outage_get_observation',
                )
              assert.ok(resolveTool)
              assert.ok(observationTool)
              for (const tool of [resolveTool, observationTool]) {
                const stream = await session.agent.streamFunction(
                  fakeCatalogModel(),
                  { messages: [] },
                )
                for await (const _event of stream) {
                  // 模拟 Pi 已完成的前两轮 provider 回执。
                }
                await executeTool(tool)
              }
              await session.agent.streamFunction(
                fakeCatalogModel(),
                { messages: [] },
              )
            },
            async abort() {},
            getSessionStats() {
              return {
                sessionFile: undefined,
                sessionId: secretMarkers.sessionId,
                userMessages: 1,
                assistantMessages: 2,
                toolCalls: 2,
                toolResults: 2,
                totalMessages: 5,
                tokens: {
                  input: 1_000,
                  output: 200,
                  cacheRead: 50,
                  cacheWrite: 0,
                  total: 1_250,
                },
                cost: 0.000203,
              }
            },
            dispose() {},
          }
          return { session }
        },
        clientFactory() {
          return {
            async getObservationBatch() {
              return structuredClone(a4ObservationBatch())
            },
            async getAsns() {
              return structuredClone(a4AsnPage())
            },
          }
        },
        pdfRendererFactory() {
          pdfCalls += 1
          throw new Error('失败调用后不得创建 PDF renderer')
        },
      },
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_runner_failed',
  )

  assert.equal(pdfCalls, 0)
  assert.equal(readFileSync(paths.registryPath, 'utf8'), registryBefore)
  assert.equal(
    existsSync(
      join(
        paths.root,
        'artifacts',
        'country-outage-agent',
        'a4-model-certification',
      ),
    ),
    false,
  )
  const ledgerText = readFileSync(
    resolve(
      paths.root,
      COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
    ),
    'utf8',
  )
  for (const marker of Object.values(secretMarkers)) {
    assert.equal(ledgerText.includes(marker), false)
  }
  assert.doesNotMatch(
    ledgerText,
    /prompt|answer|toolArguments|toolResult|authPath|apiKey|sessionId/i,
  )
  const records = ledgerText
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line) as Record<string, unknown>)
  assert.equal(records.length, 3)
  assert.equal(records[1]?.recordType, 'reservation')
  assert.equal(records[2]?.recordType, 'settlement')
  assert.equal(records[2]?.outcome, 'rejected')
  assert.equal(records[2]?.costBasis, 'worst_case_reservation')
  assert.equal(records[2]?.chargedCostCny, 0.5419008)
  assert.equal(records[2]?.formalRejectionCode, 'provider_call_failed')
  assert.equal(records[2]?.candidateRejectionCode, null)
  assert.equal(records[2]?.usage, null)
})

test('多轮后的 timeout 与用户取消审计均按整份预留结算', async (context) => {
  for (const scenario of [
    {
      label: 'timeout',
      code: 'model_attempt_timeout',
    },
    {
      label: 'user-abort',
      code: 'aborted',
    },
  ] as const) {
    await context.test(scenario.label, async () => {
      const paths = await testDirectory(
        `partial-${scenario.label}-activity-audit`,
      )
      await assert.rejects(
        runA4ModelCandidateCertification({
          authPath: paths.authPath,
          domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
          pythonExecutable: '/not/used/python',
          fontPath: '/not/used/font.ttf',
          dependencies: {
            registryPath: paths.registryPath,
            repositoryRoot: paths.root,
            runtimeFactory: async () => fakeRuntime(),
            responseModelAdapterInspector:
              compatibleAdapterInspection,
            dependencyRiskException:
              loadCountryOutageDependencyRiskException({
                now: new Date('2026-07-29T13:02:00Z'),
              }),
            now: () => new Date('2026-07-29T13:02:00Z'),
            sessionFactory:
              rejectedAfterTwoProviderRoundsSessionFactory(
                scenario.code,
              ),
            clientFactory() {
              return {
                async getObservationBatch() {
                  return structuredClone(a4ObservationBatch())
                },
                async getAsns() {
                  return structuredClone(a4AsnPage())
                },
              }
            },
            pdfRendererFactory() {
              throw new Error('被拒绝的 Pi 运行不得创建 PDF')
            },
          },
        }),
        (error: unknown) =>
          error instanceof PiModelCertificationError &&
          error.code === 'candidate_runner_failed',
      )

      const records = readFileSync(
        resolve(
          paths.root,
          COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
        ),
        'utf8',
      )
        .trim()
        .split('\n')
        .map(
          (line) =>
            JSON.parse(line) as Record<string, unknown>,
        )
      assert.equal(records.length, 3)
      assert.equal(records[2]?.outcome, 'rejected')
      assert.equal(
        records[2]?.costBasis,
        'worst_case_reservation',
      )
      assert.equal(records[2]?.chargedCostCny, 0.5419008)
      assert.equal(records[2]?.formalRejectionCode, scenario.code)
      assert.equal(records[2]?.candidateRejectionCode, null)
      assert.equal(records[2]?.usage, null)
    })
  }
})

test('模型已接受但 PDF 后处理失败时记录 allowlist candidate code', async () => {
  const paths = await testDirectory('postprocess-failure-code')
  const draftText = await validDraftText()
  const sessions: object[] = []
  const calls = { value: 0 }
  await assert.rejects(
    runA4ModelCandidateCertification({
      authPath: paths.authPath,
      domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
      pythonExecutable: '/not/used/python',
      fontPath: '/not/used/font.ttf',
      dependencies: {
        registryPath: paths.registryPath,
        repositoryRoot: paths.root,
        runtimeFactory: async () => fakeRuntime(),
        responseModelAdapterInspector:
          compatibleAdapterInspection,
        dependencyRiskException:
          loadCountryOutageDependencyRiskException({
            now: new Date('2026-07-29T13:05:00Z'),
          }),
        now: () => new Date('2026-07-29T13:05:00Z'),
        sessionFactory: sessionFactory(draftText, {
          sessions,
          calls,
        }),
        clientFactory() {
          return {
            async getObservationBatch() {
              return structuredClone(a4ObservationBatch())
            },
            async getAsns() {
              return structuredClone(a4AsnPage())
            },
          }
        },
        pdfRendererFactory() {
          return {
            async render() {
              throw new Error('untrusted-pdf-error-body')
            },
          }
        },
      },
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_run_evidence_invalid',
  )
  const records = readFileSync(
    resolve(
      paths.root,
      COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
    ),
    'utf8',
  )
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line) as Record<string, unknown>)
  assert.equal(records.length, 3)
  assert.equal(records[2]?.formalRejectionCode, null)
  assert.equal(
    records[2]?.candidateRejectionCode,
    'candidate_run_evidence_invalid',
  )
  assert.equal(records[2]?.costBasis, 'actual_usage')
  assert.ok(
    Math.abs(Number(records[2]?.chargedCostCny) - 0.01568) <
      1e-12,
  )
  assert.deepEqual(records[2]?.usage, {
    providerRequestCount: 2,
    inputTokens: 10_000,
    outputTokens: 2_000,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
  })
  assert.equal(
    JSON.stringify(records).includes('untrusted-pdf-error-body'),
    false,
  )
})

test('历史失败实际成本参与 20 CNY 预检并在任何新运行时对象前失败关闭', async () => {
  const paths = await testDirectory('historical-activity-budget')
  const loadedCandidate = await loadPiModelCandidate()
  const ledger = openCandidateActivityLedger({
    repositoryRoot: paths.root,
    policy: activityPolicy(loadedCandidate.resourceSha256),
  })
  const reservation = ledger.reserve(
    1,
    new Date('2026-07-29T13:10:00Z'),
  )
  ledger.settle(reservation, {
    outcome: 'rejected',
    recordedAt: new Date('2026-07-29T13:11:00Z'),
    candidateRejectionCode: 'candidate_runner_failed',
    usage: {
      providerRequestCount: 1,
      inputTokens: 0,
      outputTokens: 9_000_000,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    },
  })
  ledger.close()
  const counters = {
    runtime: 0,
    client: 0,
    session: 0,
    pdf: 0,
  }

  await assert.rejects(
    runA4ModelCandidateCertification({
      authPath: paths.authPath,
      domeyeApiBaseUrl: 'http://127.0.0.1:1/api/v2/',
      pythonExecutable: '/not/used/python',
      fontPath: '/not/used/font.ttf',
      dependencies: {
        registryPath: paths.registryPath,
        repositoryRoot: paths.root,
        now: () => new Date('2026-07-29T05:00:00.000Z'),
        responseModelAdapterInspector:
          compatibleAdapterInspection,
        runtimeFactory: async () => {
          counters.runtime += 1
          return fakeRuntime()
        },
        clientFactory() {
          counters.client += 1
          throw new Error('预算预检后不得创建 Domeye client')
        },
        sessionFactory: async () => {
          counters.session += 1
          throw new Error('预算预检后不得创建 Pi session')
        },
        pdfRendererFactory() {
          counters.pdf += 1
          throw new Error('预算预检后不得创建 PDF renderer')
        },
      },
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_budget_preflight_failed',
  )
  assert.deepEqual(counters, {
    runtime: 0,
    client: 0,
    session: 0,
    pdf: 0,
  })
})
