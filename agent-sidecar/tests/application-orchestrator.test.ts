import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import test from 'node:test'

import {
  CountryOutageAgentOrchestrator,
  DisabledAnnexComposer,
  DisabledExternalEvidenceProvider,
  ExternalEvidenceProviderUnavailableError,
  UndeployedManagedEgressExternalEvidenceProvider,
} from '../src/application/index.js'
import type { CountryOutageCore } from '../src/core/contracts.js'
import {
  CountryOutageCoreSessionManager,
} from '../src/core/country-outage-core-session-manager.js'
import {
  createCountryOutageAgentHttpHandler,
  CountryOutageHttpError,
} from '../src/server/index.js'

const PRINCIPAL = {
  userId: 'application-user',
  authorizationScope: 'country_outage_event_read:IR',
}

function fakeCore(overrides: Partial<CountryOutageCore> = {}) {
  const calls = {
    questions: 0,
    coreEntrypoints: 0,
  }
  const core: CountryOutageCore = {
    limits: {
      sessionTtlMs: 1_800_000,
      expiryReminderMs: 300_000,
      reportRunTimeoutMs: 120_000,
      questionRunTimeoutMs: 60_000,
      maximumQuestions: 30,
      maximumActiveAnswers: 1,
      maximumActiveReportRunsPerUser: 1,
      maximumActiveReportRunsGlobal: 8,
      maximumQueueDepth: 32,
      maximumQuestionsPerMinute: 6,
      maximumReportRunsPerUserPerHour: 3,
      maximumAnswerCharacters: 4_000,
      maximumQuestionCharacters: 4_000,
      completedDownloadGraceMs: 120_000,
      tombstoneTtlMs: 300_000,
    },
    async createReport() {
      calls.coreEntrypoints += 1
      throw new Error('本测试不应创建报告')
    },
    async createQuestion() {
      calls.questions += 1
      calls.coreEntrypoints += 1
      return {
        schema_version: 'country_outage_agent_http_v1',
        report_id: 'report-1',
        question_id: 'question-1',
        number: 1,
        run_id: 'run-question-1',
        state: 'running',
        phase: 'answering',
        session: {
          expires_at: '2026-07-30T10:30:00.000Z',
          reminder_at: '2026-07-30T10:25:00.000Z',
        },
        deduplicated: false,
      }
    },
    async abortRun() {
      calls.coreEntrypoints += 1
      throw new Error('本测试不应取消运行')
    },
    async subscribe() {
      calls.coreEntrypoints += 1
      throw new Error('本测试不应订阅')
    },
    async getArtifact() {
      calls.coreEntrypoints += 1
      throw new Error('本测试不应下载')
    },
    sweep() {},
    ...overrides,
  }
  return { core, calls }
}

test('Disabled 与未部署 managed-egress 都以可读 readiness 失败关闭且 fetch 不产生结果', async () => {
  const disabled = new DisabledExternalEvidenceProvider(
    () => new Date('2026-07-30T10:00:00.000Z'),
  )
  assert.deepEqual(disabled.readiness(), {
    schema_version:
      'country_outage_external_evidence_capability_v1',
    capability: 'external_evidence',
    state: 'not_configured',
    provider: 'disabled',
    checked_at: '2026-07-30T10:00:00.000Z',
    policy: null,
    reason_code: 'external_evidence_not_configured',
  })
  await assert.rejects(
    disabled.fetch({} as never),
    (error: unknown) =>
      error instanceof ExternalEvidenceProviderUnavailableError &&
      error.code === 'external_evidence_not_configured',
  )

  const managed =
    new UndeployedManagedEgressExternalEvidenceProvider(
      () => new Date('2026-07-30T10:00:00.000Z'),
    )
  const managedReadiness = managed.readiness()
  assert.equal(managedReadiness.state, 'self_check_failed')
  assert.equal(
    managedReadiness.reason_code,
    'managed_egress_not_deployed',
  )
  await assert.rejects(
    managed.fetch({} as never),
    (error: unknown) =>
      error instanceof ExternalEvidenceProviderUnavailableError &&
      error.code === 'managed_egress_not_deployed',
  )
})

test('Orchestrator 在进入 Core 前拒绝旧 external question，Domeye-only 正常委派', async () => {
  const { core, calls } = fakeCore()
  const orchestrator = new CountryOutageAgentOrchestrator({
    core,
    externalEvidenceProvider:
      new DisabledExternalEvidenceProvider(),
    annexComposer: new DisabledAnnexComposer(),
  })

  await assert.rejects(
    orchestrator.createQuestion(PRINCIPAL, 'report-1', {
      question: '请读取公开来源',
      evidence_mode: 'domeye_plus_external',
      external_authorization: {
        authorized: true,
        authorized_at: new Date().toISOString(),
      },
      external_urls: ['https://bgp.he.net/country/IR'],
      idempotency_key: 'external-disabled-1',
    }),
    (error: unknown) =>
      error instanceof CountryOutageHttpError &&
      error.status === 409 &&
      error.code === 'external_evidence_not_configured',
  )
  assert.equal(calls.questions, 0)
  assert.equal(calls.coreEntrypoints, 0)

  const result = await orchestrator.createQuestion(
    PRINCIPAL,
    'report-1',
    {
      question: '最低点是什么？',
      evidence_mode: 'domeye_only',
      idempotency_key: 'domeye-question-1',
    },
  )
  assert.equal(result.question_id, 'question-1')
  assert.equal(calls.questions, 1)
  assert.equal(calls.coreEntrypoints, 1)
})

test('Core 门面运行时拒绝 external question，且不会调用兼容 SessionManager 的问答服务', async () => {
  let answerCalls = 0
  const core = new CountryOutageCoreSessionManager({
    reportService: {
      async generate() {
        throw new Error('本测试不应生成报告')
      },
    },
    questionService: {
      async answer() {
        answerCalls += 1
        throw new Error('不应调用问答服务')
      },
    },
    authorize: () => true,
    timersEnabled: false,
  })

  await assert.rejects(
    core.createQuestion(
      PRINCIPAL,
      'report-1',
      {
        evidence_mode: 'domeye_plus_external',
      } as never,
    ),
    (error: unknown) =>
      error instanceof CountryOutageHttpError &&
      error.code === 'core_evidence_mode_not_allowed',
  )
  assert.equal(answerCalls, 0)
})

test('readiness、禁用 Provider 与外部附录均在模型承载 Core 前失败关闭', async (t) => {
  const { core, calls } = fakeCore()
  const orchestrator = new CountryOutageAgentOrchestrator({ core })
  const server = createServer(
    createCountryOutageAgentHttpHandler({
      application: orchestrator,
      authenticate: () => PRINCIPAL,
    }),
  )
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  t.after(() => server.close())
  const address = server.address() as AddressInfo
  const url =
    `http://127.0.0.1:${address.port}` +
    '/country-outage/capabilities/external-evidence'

  const response = await fetch(url)
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('cache-control'), 'no-store')
  const body = await response.json() as {
    state: string
    provider: string
    reason_code: string
  }
  assert.equal(body.state, 'not_configured')
  assert.equal(body.provider, 'disabled')
  assert.equal(
    body.reason_code,
    'external_evidence_not_configured',
  )
  assert.equal(calls.coreEntrypoints, 0)

  const methodError = await fetch(url, { method: 'POST' })
  assert.equal(methodError.status, 405)
  assert.equal(calls.coreEntrypoints, 0)

  const disabledQuestion = await fetch(
    `http://127.0.0.1:${address.port}` +
      '/country-outage/reports/report-1/questions',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: '请读取公开来源',
        evidence_mode: 'domeye_plus_external',
        external_authorization: {
          authorized: true,
          authorized_at: '2026-07-30T10:00:00.000Z',
        },
        external_urls: ['https://bgp.he.net/country/IR'],
        idempotency_key: 'external-disabled-http-1',
      }),
    },
  )
  assert.equal(disabledQuestion.status, 409)
  assert.equal(
    ((await disabledQuestion.json()) as {
      error: { code: string }
    }).error.code,
    'external_evidence_not_configured',
  )
  assert.equal(calls.questions, 0)
  assert.equal(calls.coreEntrypoints, 0)

  const disabledAnnex = await fetch(
    `http://127.0.0.1:${address.port}` +
      '/country-outage/reports/report-1/questions/question-1/' +
      'artifacts/external-appendix',
  )
  assert.equal(disabledAnnex.status, 409)
  assert.equal(
    ((await disabledAnnex.json()) as {
      error: { code: string }
    }).error.code,
    'external_evidence_not_configured',
  )
  assert.equal(calls.coreEntrypoints, 0)
})
