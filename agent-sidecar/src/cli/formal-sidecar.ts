import {
  createServer,
  type RequestListener,
  type Server,
} from 'node:http'

import { DomeyeCountryOutageClient } from '../domain/index.js'
import {
  HttpP1GeneralReadModelProvider,
  P1ModelUserGoalPlanner,
  P1PiSemanticModel,
  P1RuntimeV2ConversationService,
  type P1PiSemanticModelAuditRecord,
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
import {
  createFormalPiModelBindingFromEnvironment,
  loadCountryOutageDependencySecurityAttestation,
  PiReportNarrator,
  type VerifiedCountryOutageDependencySecurityAttestation,
  type FormalPiAuditSink,
  type FormalPiModelBinding,
  type FormalPiProductionEnvironment,
  type FormalPiRunAuditRecord,
  type PiSessionFactory,
} from '../pi/index.js'
import { COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION } from '../pi/country-outage-skill-bundle.js'
import { CountryOutageArtifactBuilder } from '../report/artifact-builder.js'
import { CountryOutagePdfRenderer } from '../report/pdf-renderer.js'
import {
  RuntimeCountryOutageQuestionService,
  RuntimeCountryOutageReportService,
} from '../runtime/index.js'
import {
  createCountryOutageAgentHttpHandler,
  type CountryOutageReportServiceIdentity,
} from '../server/index.js'
import {
  frozenAcceptanceEnvironmentInteger,
  loadFormalCountryOutageAcceptanceRuntime,
  type FormalCountryOutageAcceptanceRuntime,
} from '../formal-acceptance-runtime.js'
import {
  assertCountryOutageLoopbackHost,
  countryOutageScopeAllowsEvent,
  createCountryOutageInternalAuthenticator,
  positiveIntegerEnvironmentValue,
  requiredEnvironmentValue,
  type SidecarEnvironment,
} from './sidecar-security.js'
import {
  createFormalPiAuditLog,
  FORMAL_PI_AUDIT_RETENTION_DAYS,
} from './formal-pi-audit-log.js'

export const FORMAL_COUNTRY_OUTAGE_NARRATOR =
  'pi-sdk-certified' as const

export const FORMAL_EXTERNAL_EVIDENCE_CAPABILITY = Object.freeze({
  state: 'not_configured' as const,
  provider: 'disabled' as const,
})

export type FormalSidecarEnvironment =
  FormalPiProductionEnvironment & SidecarEnvironment

export type FormalPiBindingFactory = (options: {
  env: FormalPiProductionEnvironment
}) => Promise<FormalPiModelBinding>

export type FormalHttpServerFactory = (
  requestListener: RequestListener,
) => Server

export type FormalAuditLineWriter = (
  line: string,
) => void | Promise<void>

export interface FormalCountryOutageSidecarDependencies {
  bindingFactory?: FormalPiBindingFactory
  httpServerFactory?: FormalHttpServerFactory
  auditWriter?: FormalAuditLineWriter
  auditLogNow?: () => Date
  securityAttestationPath?: string
  /** @deprecated 依赖证明不按运行时日期过期；仅保留测试调用兼容。 */
  securityAttestationNow?: () => Date
  p1SessionFactory?: PiSessionFactory
}

export interface FormalCountryOutageSidecar {
  host: string
  port: number
  server: Server
  core: CountryOutageCoreSessionManager
  orchestrator: CountryOutageAgentOrchestrator
  /** @deprecated 使用 orchestrator；仅保留既有调用兼容。 */
  manager: CountryOutageAgentOrchestrator
  narrator: PiReportNarrator
  binding: FormalPiModelBinding
  dependencySecurityAttestation: VerifiedCountryOutageDependencySecurityAttestation['audit']
  externalEvidenceCapability:
    typeof FORMAL_EXTERNAL_EVIDENCE_CAPABILITY
  acceptanceRuntime: FormalCountryOutageAcceptanceRuntime
  baseReportCacheTtlMs: number
  reportServiceIdentity: CountryOutageReportServiceIdentity
  auditLog: {
    directory: string
    retentionDays: typeof FORMAL_PI_AUDIT_RETENTION_DAYS
  }
  p1Chat: {
    enabled: boolean
    modelIdentity: string | null
    apiBaseUrl: string | null
    apiTimeoutMs: number | null
    modelTimeoutMs: number | null
    turnTimeoutMs: number | null
    reportCapabilityExposed: false
  }
}

function sanitizedRuntimeSecurity(
  record: FormalPiRunAuditRecord,
): FormalPiRunAuditRecord['runtimeSecurity'] {
  return {
    resourceLoaderId: record.runtimeSecurity.resourceLoaderId,
    skillBundleSha256: record.runtimeSecurity.skillBundleSha256,
    packageManagerResolutionEnabled:
      record.runtimeSecurity.packageManagerResolutionEnabled,
    modelResolverEnabled: record.runtimeSecurity.modelResolverEnabled,
    modelsJsonEnabled: record.runtimeSecurity.modelsJsonEnabled,
    modelCatalogNetworkRefreshEnabled:
      record.runtimeSecurity.modelCatalogNetworkRefreshEnabled,
    explicitModel: record.runtimeSecurity.explicitModel,
    providerRetryAttempts:
      record.runtimeSecurity.providerRetryAttempts,
    forwardedProviderRequestCount:
      record.runtimeSecurity.forwardedProviderRequestCount,
    structuredOutput:
      record.runtimeSecurity.structuredOutput.applicability ===
      'required'
        ? {
            applicability: 'required',
            mechanism:
              record.runtimeSecurity.structuredOutput.mechanism,
            payloadPreparedCount:
              record.runtimeSecurity.structuredOutput
                .payloadPreparedCount,
          }
        : {
            applicability: 'not_applicable',
            mechanism: null,
            payloadPreparedCount: 0,
          },
    dependencySecurityAttestation: {
      attestationId:
        record.runtimeSecurity.dependencySecurityAttestation.attestationId,
      verifiedAt:
        record.runtimeSecurity.dependencySecurityAttestation.verifiedAt,
      lockfileSha256:
        record.runtimeSecurity.dependencySecurityAttestation.lockfileSha256,
      status: record.runtimeSecurity.dependencySecurityAttestation.status,
    },
  }
}

/**
 * 只复制审核合同内的白名单字段。即使调用方在运行时附带额外属性，
 * 也不会把提示词、回答正文、工具参数/结果或认证内容写入审核输出。
 */
export function sanitizeFormalPiRunAuditRecord(
  record: FormalPiRunAuditRecord,
): FormalPiRunAuditRecord {
  return {
    schemaVersion: record.schemaVersion,
    recordedAt: record.recordedAt,
    outcome: record.outcome,
    runtimeIdentity: record.runtimeIdentity,
    ...(record.runtimeIdentity === 'formal'
      ? {
          registryVersion: record.registryVersion!,
          certificationEvidenceId:
            record.certificationEvidenceId!,
        }
      : {
          candidateId: record.candidateId!,
          candidateResourceSha256:
            record.candidateResourceSha256!,
        }),
    profileId: record.profileId,
    provider: record.provider,
    model: record.model,
    modelVersion: record.modelVersion,
    expectedResponseModel: record.expectedResponseModel,
    piVersion: record.piVersion,
    input: {
      eventReferenceSha256: record.input.eventReferenceSha256,
      incidentId: record.input.incidentId,
      publicationId: record.input.publicationId,
      revision: record.input.revision,
      dataThrough: record.input.dataThrough,
      factSetId: record.input.factSetId,
      collectorId: record.input.collectorId,
      reportSpecificationVersion:
        record.input.reportSpecificationVersion,
      projectKnowledgeVersion: record.input.projectKnowledgeVersion,
      validatorRulesVersion: record.input.validatorRulesVersion,
    },
    narration: {
      mode: record.narration.mode,
      slotContractVersion: record.narration.slotContractVersion,
      requestedSlotCount: record.narration.requestedSlotCount,
      acceptedSlotCount: record.narration.acceptedSlotCount,
      baseV5: record.narration.baseV5,
      mergeInvariant: record.narration.mergeInvariant,
      finalV5: record.narration.finalV5,
      modelOutputApplied: record.narration.modelOutputApplied,
    },
    runtimeSecurity: sanitizedRuntimeSecurity(record),
    modelAttempt: {
      timeoutMs: record.modelAttempt.timeoutMs,
      maximumAttempts: record.modelAttempt.maximumAttempts,
      executedAttempts: record.modelAttempt.executedAttempts,
    },
    ...(record.observed
      ? {
          observed: {
            provider: record.observed.provider,
            model: record.observed.model,
            responseModel: record.observed.responseModel,
            stopReason: record.observed.stopReason,
          },
        }
      : {}),
    tools: {
      executedNames: [...record.tools.executedNames],
      executionCount: record.tools.executionCount,
      unauthorizedAttemptCount: record.tools.unauthorizedAttemptCount,
    },
    ...(record.usage
      ? {
          usage: {
            assistantMessages: record.usage.assistantMessages,
            toolCalls: record.usage.toolCalls,
            toolResults: record.usage.toolResults,
            totalMessages: record.usage.totalMessages,
            tokens: {
              input: record.usage.tokens.input,
              output: record.usage.tokens.output,
              cacheRead: record.usage.tokens.cacheRead,
              cacheWrite: record.usage.tokens.cacheWrite,
              total: record.usage.tokens.total,
            },
            estimatedCostUsd: record.usage.estimatedCostUsd,
          },
        }
      : {}),
    ...(record.rejectionCode
      ? { rejectionCode: record.rejectionCode }
      : {}),
  }
}

export function createSafeFormalPiAuditSink(
  writer: FormalAuditLineWriter = (line) => {
    process.stdout.write(line)
  },
): FormalPiAuditSink {
  return async (record) => {
    const safeRecord = sanitizeFormalPiRunAuditRecord(record)
    await writer(
      `${JSON.stringify({
        event: 'country_outage_pi_run_audit',
        audit: safeRecord,
      })}\n`,
    )
  }
}

function validateFormalSidecarConfiguration(
  env: FormalSidecarEnvironment,
): {
  host: string
  port: number
  sharedToken: string
  apiBaseUrl: string
  apiTimeoutMs: number
  pythonExecutable: string
  fontPath: string
  pdfTimeoutMs: number
  baseReportCacheTtlMs: number
  auditDirectory: string
  p1ChatEnabled: boolean
  p1ApiBaseUrl: string
  p1ApiTimeoutMs: number
  p1ModelTimeoutMs: number
  p1TurnTimeoutMs: number
  acceptanceRuntime: FormalCountryOutageAcceptanceRuntime
} {
  const acceptanceRuntime =
    loadFormalCountryOutageAcceptanceRuntime()
  const host = env.COUNTRY_OUTAGE_AGENT_HOST?.trim() || '127.0.0.1'
  assertCountryOutageLoopbackHost(host)
  const port = positiveIntegerEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_AGENT_PORT',
    28474,
  )
  if (port > 65_535) {
    throw new Error('COUNTRY_OUTAGE_AGENT_PORT 无效')
  }
  const sharedToken = requiredEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_AGENT_SHARED_TOKEN',
  )
  if (sharedToken.length < 24) {
    throw new Error('COUNTRY_OUTAGE_AGENT_SHARED_TOKEN 至少需要 24 字符')
  }
  const narrator = requiredEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_AGENT_NARRATOR',
  )
  if (narrator !== FORMAL_COUNTRY_OUTAGE_NARRATOR) {
    throw new Error(
      '正式入口只允许 pi-sdk-certified，禁止 deterministic-acceptance 身份',
    )
  }
  const p1ChatEnabled = env.COUNTRY_OUTAGE_P1_CHAT_ENABLED === 'true'
  const p1ApiTimeoutMs = positiveIntegerEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_P1_API_TIMEOUT_MS',
    15_000,
  )
  const p1ModelTimeoutMs = positiveIntegerEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_P1_MODEL_TIMEOUT_MS',
    75_000,
  )
  const p1TurnTimeoutMs = positiveIntegerEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_P1_TURN_TIMEOUT_MS',
    110_000,
  )
  if (
    p1ApiTimeoutMs > 30_000 ||
    p1ModelTimeoutMs > 120_000 ||
    p1TurnTimeoutMs > 180_000 ||
    p1TurnTimeoutMs <= p1ModelTimeoutMs
  ) {
    throw new Error('P1 正式超时配置无效')
  }
  const apiBaseUrl = requiredEnvironmentValue(env, 'DOMEYE_API_BASE_URL')
  return {
    host,
    port,
    sharedToken,
    apiBaseUrl,
    apiTimeoutMs: frozenAcceptanceEnvironmentInteger(
      env,
      'DOMEYE_API_TIMEOUT_MS',
      acceptanceRuntime.formal.domeyeApiTimeoutMs,
    ),
    pythonExecutable: requiredEnvironmentValue(
      env,
      'DOMEYE_REPORT_PYTHON_EXECUTABLE',
    ),
    fontPath: requiredEnvironmentValue(env, 'DOMEYE_REPORT_FONT_PATH'),
    pdfTimeoutMs: frozenAcceptanceEnvironmentInteger(
      env,
      'DOMEYE_REPORT_PDF_TIMEOUT_MS',
      acceptanceRuntime.timeouts.pdfRenderMs,
    ),
    baseReportCacheTtlMs: frozenAcceptanceEnvironmentInteger(
      env,
      'COUNTRY_OUTAGE_AGENT_REPORT_CACHE_TTL_MS',
      acceptanceRuntime.formal.baseReportCacheTtlMs,
    ),
    auditDirectory: requiredEnvironmentValue(
      env,
      'COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY',
    ),
    p1ChatEnabled,
    p1ApiBaseUrl:
      env.COUNTRY_OUTAGE_P1_API_BASE_URL?.trim() || apiBaseUrl,
    p1ApiTimeoutMs,
    p1ModelTimeoutMs,
    p1TurnTimeoutMs,
    acceptanceRuntime,
  }
}

export async function createFormalCountryOutageSidecar(
  env: FormalSidecarEnvironment = process.env,
  dependencies: FormalCountryOutageSidecarDependencies = {},
): Promise<FormalCountryOutageSidecar> {
  const config = validateFormalSidecarConfiguration(env)

  // 持久化审计目录、权限和 30 日清理必须在模型与 Server 前失败关闭。
  const auditLog = createFormalPiAuditLog({
    directory: config.auditDirectory,
    ...(dependencies.auditLogNow
      ? { now: dependencies.auditLogNow }
      : {}),
  })
  const persistentAuditWriter: FormalAuditLineWriter = async (line) => {
    await auditLog.writeLine(line)
    await dependencies.auditWriter?.(line)
  }

  // 依赖安全证明必须先于模型、认证和 HTTP Server 完成失败关闭预检。
  const dependencySecurityAttestation =
    loadCountryOutageDependencySecurityAttestation({
      ...(dependencies.securityAttestationPath
        ? { path: dependencies.securityAttestationPath }
        : {}),
      ...(dependencies.securityAttestationNow
        ? { now: dependencies.securityAttestationNow() }
        : {}),
    })

  // 模型、认证和认证注册表必须在 HTTP Server 创建前完成预检。
  const binding = await (
    dependencies.bindingFactory ??
    createFormalPiModelBindingFromEnvironment
  )({ env })

  const client = new DomeyeCountryOutageClient({
    baseUrl: config.apiBaseUrl,
    timeoutMs: config.apiTimeoutMs,
    maximumSnapshotBatchRetries:
      config.acceptanceRuntime.capacity
        .maximumSnapshotBatchRetries,
  })
  const artifactBuilder = new CountryOutageArtifactBuilder(
    new CountryOutagePdfRenderer({
      pythonExecutable: config.pythonExecutable,
      fontPath: config.fontPath,
      timeoutMs: config.pdfTimeoutMs,
    }),
  )
  const narrator = new PiReportNarrator({
    client,
    model: binding.model,
    modelRuntime: binding.modelRuntime,
    modelSelection: binding.runSelection,
    dependencySecurityAttestation,
    auditSink: createSafeFormalPiAuditSink(persistentAuditWriter),
  })
  if (narrator.identity.adapter !== 'pi-sdk') {
    throw new Error('正式入口禁止使用验收叙述器身份')
  }

  const p1SemanticModel = config.p1ChatEnabled
    ? new P1PiSemanticModel({
        binding,
        timeoutMs: config.p1ModelTimeoutMs,
        auditSink: async (record: P1PiSemanticModelAuditRecord) => {
          await persistentAuditWriter(
            `${JSON.stringify({
              event: 'country_outage_p1_pi_semantic_run_audit',
              audit: record,
            })}\n`,
          )
        },
        ...(dependencies.p1SessionFactory
          ? { sessionFactory: dependencies.p1SessionFactory }
          : {}),
      })
    : undefined
  const chatService = p1SemanticModel
    ? new P1RuntimeV2ConversationService({
        provider: new HttpP1GeneralReadModelProvider(
          config.p1ApiBaseUrl,
          config.p1ApiTimeoutMs,
        ),
        planner: new P1ModelUserGoalPlanner(p1SemanticModel),
        ttlMs: 30 * 60 * 1000,
        turnTimeoutMs: config.p1TurnTimeoutMs,
      })
    : undefined

  const reportServiceIdentity: CountryOutageReportServiceIdentity = {
    reportSpecificationVersion: 'country_outage_report_spec_v1',
    projectKnowledgeVersion:
      COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
    validatorRulesVersion: narrator.validatorRulesVersion,
    skillBundleSha256: narrator.skillBundleSha256,
    model: narrator.identity,
  }
  const core = new CountryOutageCoreSessionManager({
    reportGenerator: adaptCountryOutageReportService(
      new RuntimeCountryOutageReportService({
        client,
        narrator,
        artifactBuilder,
        asnPageSize: 10,
      }),
    ),
    questionService: new RuntimeCountryOutageQuestionService(),
    baseReportCache: {
      ttlMs: config.baseReportCacheTtlMs,
      reportServiceIdentity,
    },
    limits: config.acceptanceRuntime.formal.sessionLimits,
    authorize: async (principal, reference) => {
      if (!countryOutageScopeAllowsEvent(principal, reference)) {
        return false
      }
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
  const requestListener = createCountryOutageAgentHttpHandler({
    application: orchestrator,
    ...(chatService ? { chatService } : {}),
    authenticate: createCountryOutageInternalAuthenticator(
      config.sharedToken,
    ),
  })
  const server = (
    dependencies.httpServerFactory ?? createServer
  )(requestListener)
  server.requestTimeout = chatService
    ? Math.max(125_000, config.p1TurnTimeoutMs + 5_000)
    : 125_000
  server.headersTimeout = 10_000
  server.keepAliveTimeout = 20_000

  return {
    host: config.host,
    port: config.port,
    server,
    core,
    orchestrator,
    manager: orchestrator,
    narrator,
    binding,
    dependencySecurityAttestation: dependencySecurityAttestation.audit,
    externalEvidenceCapability: FORMAL_EXTERNAL_EVIDENCE_CAPABILITY,
    acceptanceRuntime: config.acceptanceRuntime,
    baseReportCacheTtlMs: config.baseReportCacheTtlMs,
    reportServiceIdentity,
    auditLog: {
      directory: auditLog.directory,
      retentionDays: auditLog.retentionDays,
    },
    p1Chat: {
      enabled: Boolean(chatService),
      modelIdentity: p1SemanticModel?.identity ?? null,
      apiBaseUrl: chatService ? config.p1ApiBaseUrl : null,
      apiTimeoutMs: chatService ? config.p1ApiTimeoutMs : null,
      modelTimeoutMs: chatService ? config.p1ModelTimeoutMs : null,
      turnTimeoutMs: chatService ? config.p1TurnTimeoutMs : null,
      reportCapabilityExposed: false,
    },
  }
}

export async function startFormalCountryOutageSidecar(
  env: FormalSidecarEnvironment = process.env,
  dependencies: FormalCountryOutageSidecarDependencies = {},
): Promise<FormalCountryOutageSidecar> {
  const sidecar = await createFormalCountryOutageSidecar(
    env,
    dependencies,
  )
  await new Promise<void>((resolve, reject) => {
    sidecar.server.once('error', reject)
    sidecar.server.listen(sidecar.port, sidecar.host, () => resolve())
  })
  return sidecar
}
