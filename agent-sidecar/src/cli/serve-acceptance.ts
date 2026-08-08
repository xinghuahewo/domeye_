import { createServer } from 'node:http'

import { DomeyeCountryOutageClient } from '../domain/index.js'
import {
  CountryOutageAgentOrchestrator,
  DisabledAnnexComposer,
  DisabledExternalEvidenceProvider,
} from '../application/index.js'
import {
  adaptCountryOutageReportService,
  CountryOutageCoreSessionManager,
} from '../core/index.js'
import { DeterministicAcceptanceNarrator } from '../report/deterministic-narrator.js'
import { CountryOutageArtifactBuilder } from '../report/artifact-builder.js'
import { CountryOutagePdfRenderer } from '../report/pdf-renderer.js'
import {
  RuntimeCountryOutageQuestionService,
  RuntimeCountryOutageReportService,
} from '../runtime/index.js'
import {
  createCountryOutageAgentHttpHandler,
} from '../server/index.js'
import {
  HttpP1GeneralReadModelProvider,
  P1ConversationManager,
} from '../chat/index.js'
import {
  assertCountryOutageLoopbackHost,
  countryOutageScopeAllowsEvent,
  createCountryOutageInternalAuthenticator,
  positiveIntegerEnvironmentValue,
  requiredEnvironmentValue,
} from './sidecar-security.js'

async function main(): Promise<void> {
  const host = process.env.COUNTRY_OUTAGE_AGENT_HOST?.trim() || '127.0.0.1'
  assertCountryOutageLoopbackHost(host)
  const port = positiveIntegerEnvironmentValue(
    process.env,
    'COUNTRY_OUTAGE_AGENT_PORT',
    28474,
  )
  if (port > 65_535) throw new Error('COUNTRY_OUTAGE_AGENT_PORT 无效')

  const sharedToken = requiredEnvironmentValue(
    process.env,
    'COUNTRY_OUTAGE_AGENT_SHARED_TOKEN',
  )
  if (sharedToken.length < 24) {
    throw new Error('COUNTRY_OUTAGE_AGENT_SHARED_TOKEN 至少需要 24 字符')
  }
  if (process.env.COUNTRY_OUTAGE_AGENT_P1_CHAT_ONLY === 'true') {
    const apiBaseUrl = requiredEnvironmentValue(process.env, 'DOMEYE_API_BASE_URL')
    const apiTimeoutMs = positiveIntegerEnvironmentValue(
      process.env,
      'DOMEYE_API_TIMEOUT_MS',
      10_000,
    )
    const chat = new P1ConversationManager({
      provider: new HttpP1GeneralReadModelProvider(apiBaseUrl, apiTimeoutMs),
    })
    const server = createServer(createCountryOutageAgentHttpHandler({
      // chat-only 只用于 P1 浏览器/机器验收；报告路径保持未配置，不能冒充正式报告入口。
      application: {} as CountryOutageAgentOrchestrator,
      chat,
      authenticate: createCountryOutageInternalAuthenticator(sharedToken),
    }))
    server.requestTimeout = 125_000
    server.headersTimeout = 10_000
    server.keepAliveTimeout = 20_000
    const shutdown = (): void => {
      server.close(() => process.exit(0))
    }
    process.once('SIGINT', shutdown)
    process.once('SIGTERM', shutdown)
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(port, host, () => resolve())
    })
    process.stdout.write(`${JSON.stringify({
      event: 'country_outage_p1_chat_acceptance_ready',
      host,
      port,
      collector: 'rrc25',
      persistence: 'ephemeral',
      reportCapability: 'not_configured',
      p1Chat: 'event-bound-deterministic',
    })}\n`)
    return
  }
  const narratorMode = requiredEnvironmentValue(
    process.env,
    'COUNTRY_OUTAGE_AGENT_NARRATOR',
  )
  if (narratorMode !== 'deterministic-acceptance') {
    throw new Error(
      '此入口仅用于 A3 确定性验收；正式 Pi 模型必须使用已认证的生产入口',
    )
  }

  const apiBaseUrl = requiredEnvironmentValue(process.env, 'DOMEYE_API_BASE_URL')
  const apiTimeoutMs = positiveIntegerEnvironmentValue(
      process.env,
      'DOMEYE_API_TIMEOUT_MS',
      10_000,
    )
  const client = new DomeyeCountryOutageClient({
    baseUrl: apiBaseUrl,
    timeoutMs: apiTimeoutMs,
    maximumSnapshotBatchRetries: 2,
  })
  const artifactBuilder = new CountryOutageArtifactBuilder(
    new CountryOutagePdfRenderer({
      pythonExecutable: requiredEnvironmentValue(
        process.env,
        'DOMEYE_REPORT_PYTHON_EXECUTABLE',
      ),
      fontPath: requiredEnvironmentValue(
        process.env,
        'DOMEYE_REPORT_FONT_PATH',
      ),
      timeoutMs: positiveIntegerEnvironmentValue(
        process.env,
        'DOMEYE_REPORT_PDF_TIMEOUT_MS',
        45_000,
      ),
    }),
  )
  const core = new CountryOutageCoreSessionManager({
    reportGenerator: adaptCountryOutageReportService(
      new RuntimeCountryOutageReportService({
        client,
        narrator: new DeterministicAcceptanceNarrator(),
        artifactBuilder,
        asnPageSize: 10,
      }),
    ),
    questionService: new RuntimeCountryOutageQuestionService(),
    authorize: async (principal, reference) => {
      if (!countryOutageScopeAllowsEvent(principal, reference)) return false
      try {
        const resolution = await client.resolve(reference)
        return (
          resolution.event_type === 'country_outage' &&
          resolution.legacy_reference.replace(' ', '+') ===
            reference.replace(' ', '+')
        )
      } catch {
        return false
      }
    },
  })
  const orchestrator = new CountryOutageAgentOrchestrator({
    core,
    externalEvidenceProvider:
      new DisabledExternalEvidenceProvider(),
    annexComposer: new DisabledAnnexComposer(),
  })
  const chat = new P1ConversationManager({
    provider: new HttpP1GeneralReadModelProvider(apiBaseUrl, apiTimeoutMs),
  })
  const server = createServer(
    createCountryOutageAgentHttpHandler({
      application: orchestrator,
      chat,
      authenticate: createCountryOutageInternalAuthenticator(sharedToken),
    }),
  )
  server.requestTimeout = 125_000
  server.headersTimeout = 10_000
  server.keepAliveTimeout = 20_000

  const shutdown = (): void => {
    server.close(() => process.exit(0))
  }
  process.once('SIGINT', shutdown)
  process.once('SIGTERM', shutdown)

  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(port, host, () => resolve())
  })
  process.stdout.write(
    `${JSON.stringify({
      event: 'country_outage_agent_sidecar_ready',
      host,
      port,
      collector: 'rrc25',
      narrator: narratorMode,
      persistence: 'ephemeral',
      piVersion: '0.82.1',
      externalEvidence: 'disabled',
      p1Chat: 'event-bound-deterministic',
    })}\n`,
  )
}

void main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error)
  process.stderr.write(`国家中断 Agent Sidecar 启动失败：${message}\n`)
  process.exitCode = 1
})
