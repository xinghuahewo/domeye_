import { createServer } from 'node:http'

import { DomeyeCountryOutageClient } from '../domain/index.js'
import {
  HttpP1GeneralReadModelProvider,
  P1CodexCliSemanticModel,
  P1ModelUserGoalPlanner,
  P1RuntimeV2ConversationService,
} from '../chat/index.js'
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
  const narratorMode = requiredEnvironmentValue(
    process.env,
    'COUNTRY_OUTAGE_AGENT_NARRATOR',
  )
  if (narratorMode !== 'deterministic-acceptance') {
    throw new Error(
      '此入口仅用于 A3 确定性验收；正式 Pi 模型必须使用已认证的生产入口',
    )
  }

  const client = new DomeyeCountryOutageClient({
    baseUrl: requiredEnvironmentValue(process.env, 'DOMEYE_API_BASE_URL'),
    timeoutMs: positiveIntegerEnvironmentValue(
      process.env,
      'DOMEYE_API_TIMEOUT_MS',
      10_000,
    ),
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
  const p1ChatEnabled = process.env.COUNTRY_OUTAGE_P1_CHAT_ENABLED === 'true'
  const p1TurnTimeoutMs = positiveIntegerEnvironmentValue(
    process.env,
    'COUNTRY_OUTAGE_P1_TURN_TIMEOUT_MS',
    200_000,
  )
  const chatService = p1ChatEnabled
    ? new P1RuntimeV2ConversationService({
        provider: new HttpP1GeneralReadModelProvider(
          requiredEnvironmentValue(
            process.env,
            'COUNTRY_OUTAGE_P1_API_BASE_URL',
          ),
          positiveIntegerEnvironmentValue(
            process.env,
            'COUNTRY_OUTAGE_P1_API_TIMEOUT_MS',
            15_000,
          ),
        ),
        planner: new P1ModelUserGoalPlanner(
          new P1CodexCliSemanticModel({
            executable: requiredEnvironmentValue(
              process.env,
              'COUNTRY_OUTAGE_P1_CODEX_EXECUTABLE',
            ),
            model: process.env.COUNTRY_OUTAGE_P1_MODEL?.trim()
              || 'gpt-5.6-sol',
            timeoutMs: positiveIntegerEnvironmentValue(
              process.env,
              'COUNTRY_OUTAGE_P1_MODEL_TIMEOUT_MS',
              180_000,
            ),
          }),
        ),
        ttlMs: 30 * 60 * 1000,
        turnTimeoutMs: p1TurnTimeoutMs,
      })
    : undefined
  const server = createServer(
    createCountryOutageAgentHttpHandler({
      application: orchestrator,
      ...(chatService ? { chatService } : {}),
      authenticate: createCountryOutageInternalAuthenticator(sharedToken),
    }),
  )
  server.requestTimeout = p1ChatEnabled
    ? Math.max(125_000, p1TurnTimeoutMs + 5_000)
    : 125_000
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
      piVersion: '0.84.1',
      externalEvidence: 'disabled',
      p1Chat: p1ChatEnabled ? 'enabled_candidate' : 'disabled',
    })}\n`,
  )
}

void main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error)
  process.stderr.write(`国家中断 Agent Sidecar 启动失败：${message}\n`)
  process.exitCode = 1
})
