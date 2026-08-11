import {
  createServer,
  type RequestListener,
  type Server,
} from 'node:http'

import {
  HttpP1GeneralReadModelProvider,
  P1ModelUserGoalPlanner,
  P1PiSemanticModel,
  P1RuntimeV2ConversationService,
  P1TrendAwareGrounder,
  P2RegistrySnapshotLoader,
  type P1PiSemanticModelAuditRecord,
} from '../chat/index.js'
import {
  createFormalPiModelBindingFromEnvironment,
  loadCountryOutageDependencySecurityAttestation,
  type FormalPiModelBinding,
  type FormalPiProductionEnvironment,
  type PiSessionFactory,
  type VerifiedCountryOutageDependencySecurityAttestation,
} from '../pi/index.js'
import { createCountryOutageAgentHttpHandler } from '../server/index.js'
import {
  assertCountryOutageLoopbackHost,
  createCountryOutageInternalAuthenticator,
  positiveIntegerEnvironmentValue,
  requiredEnvironmentValue,
  type SidecarEnvironment,
} from './sidecar-security.js'
import {
  createFormalPiAuditLog,
  FORMAL_PI_AUDIT_RETENTION_DAYS,
} from './formal-pi-audit-log.js'

export const FORMAL_P1_CERTIFIED_SCENARIO_SET_ID =
  'country-outage-p1-page-coverage-s2-v1' as const
export const FORMAL_P1_CERTIFIED_INPUT_SCOPE =
  'country_outage_p1_rrc25_event_bound_chat_v1' as const

export type FormalP1SidecarEnvironment =
  FormalPiProductionEnvironment & SidecarEnvironment & {
    COUNTRY_OUTAGE_P2_REGISTRY_MODE?: 'shadow' | 'production'
    COUNTRY_OUTAGE_P2_REGISTRY_SNAPSHOT?: string
  }

export interface FormalP1SidecarDependencies {
  bindingFactory?: (options: {
    env: FormalPiProductionEnvironment
  }) => Promise<FormalPiModelBinding>
  httpServerFactory?: (listener: RequestListener) => Server
  auditWriter?: (line: string) => void | Promise<void>
  auditLogNow?: () => Date
  securityAttestationPath?: string
  p1SessionFactory?: PiSessionFactory
}

export interface FormalP1Sidecar {
  host: string
  port: number
  server: Server
  binding: FormalPiModelBinding
  dependencySecurityAttestation:
    VerifiedCountryOutageDependencySecurityAttestation['audit']
  modelIdentity: string
  chatService: P1RuntimeV2ConversationService
  auditLog: {
    directory: string
    retentionDays: typeof FORMAL_PI_AUDIT_RETENTION_DAYS
  }
  runtime: {
    collector: 'rrc25'
    eventType: 'country_outage'
    apiBaseUrl: string
    apiTimeoutMs: number
    modelTimeoutMs: number
    turnTimeoutMs: number
    maximumProviderRequestCountPerTurn: 1
    businessCostLimit: null
    reportCapability: 'disabled'
    externalEvidence: 'disabled'
    eventWindowTrendOperator: {
      executionUnit: 'OP-04'
      capabilityId: 'CAP-TREND-001'
      operatorId: 'event-window-trend'
      operatorVersion: '1.2.0'
      modelDependency: 'none'
    }
    toolOperatorRegistry: {
      candidateId: string
      registrySnapshotId: string
      registryRevision: number
      activationScope: 'runtime_candidate_shadow_only' | 'production_active'
      runtimeIntegration: 'implemented_not_deployed' | 'deployed'
      productionDeployed: boolean
    }
  }
}

function validateConfiguration(env: FormalP1SidecarEnvironment) {
  const host = env.COUNTRY_OUTAGE_AGENT_HOST?.trim() || '127.0.0.1'
  assertCountryOutageLoopbackHost(host)
  const port = positiveIntegerEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_AGENT_PORT',
    28_475,
  )
  if (port > 65_535) throw new Error('COUNTRY_OUTAGE_AGENT_PORT 无效')
  const sharedToken = requiredEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_AGENT_SHARED_TOKEN',
  )
  if (sharedToken.length < 24) {
    throw new Error('COUNTRY_OUTAGE_AGENT_SHARED_TOKEN 至少需要 24 字符')
  }
  const apiBaseUrl =
    env.COUNTRY_OUTAGE_P1_API_BASE_URL?.trim() ||
    requiredEnvironmentValue(env, 'DOMEYE_API_BASE_URL')
  const apiTimeoutMs = positiveIntegerEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_P1_API_TIMEOUT_MS',
    15_000,
  )
  const modelTimeoutMs = positiveIntegerEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_P1_MODEL_TIMEOUT_MS',
    75_000,
  )
  const turnTimeoutMs = positiveIntegerEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_P1_TURN_TIMEOUT_MS',
    110_000,
  )
  if (
    apiTimeoutMs > 30_000 ||
    modelTimeoutMs > 120_000 ||
    turnTimeoutMs > 180_000 ||
    turnTimeoutMs <= modelTimeoutMs
  ) {
    throw new Error('P1 正式超时配置无效')
  }
  const registryModeValue = env.COUNTRY_OUTAGE_P2_REGISTRY_MODE?.trim() || 'shadow'
  if (registryModeValue !== 'shadow' && registryModeValue !== 'production') {
    throw new Error('P2 Registry 运行模式无效')
  }
  const registryMode: 'shadow' | 'production' = registryModeValue
  const registrySnapshotPath =
    env.COUNTRY_OUTAGE_P2_REGISTRY_SNAPSHOT?.trim() || undefined
  return {
    host,
    port,
    sharedToken,
    apiBaseUrl,
    apiTimeoutMs,
    modelTimeoutMs,
    turnTimeoutMs,
    registryMode,
    registrySnapshotPath,
    auditDirectory: requiredEnvironmentValue(
      env,
      'COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY',
    ),
  }
}

export async function createFormalP1Sidecar(
  env: FormalP1SidecarEnvironment = process.env,
  dependencies: FormalP1SidecarDependencies = {},
): Promise<FormalP1Sidecar> {
  const config = validateConfiguration(env)
  const auditLog = createFormalPiAuditLog({
    directory: config.auditDirectory,
    ...(dependencies.auditLogNow ? { now: dependencies.auditLogNow } : {}),
  })
  const writeAuditLine = async (line: string): Promise<void> => {
    await auditLog.writeLine(line)
    await dependencies.auditWriter?.(line)
  }
  const dependencySecurityAttestation =
    loadCountryOutageDependencySecurityAttestation({
      ...(dependencies.securityAttestationPath
        ? { path: dependencies.securityAttestationPath }
        : {}),
    })
  const binding = await (
    dependencies.bindingFactory ?? createFormalPiModelBindingFromEnvironment
  )({ env })
  const registrySnapshot = new P2RegistrySnapshotLoader(
    config.registrySnapshotPath,
    config.registryMode,
  ).load()
  if (
    binding.preflight.certifiedScenarioSetId !==
      FORMAL_P1_CERTIFIED_SCENARIO_SET_ID ||
    binding.preflight.certifiedInputScope !==
      FORMAL_P1_CERTIFIED_INPUT_SCOPE
  ) {
    throw new Error('正式 Pi 证书未覆盖 P1 事件绑定问答范围')
  }
  const semanticModel = new P1PiSemanticModel({
    binding,
    timeoutMs: config.modelTimeoutMs,
    ...(dependencies.p1SessionFactory
      ? { sessionFactory: dependencies.p1SessionFactory }
      : {}),
    auditSink: async (record: P1PiSemanticModelAuditRecord) => {
      await writeAuditLine(
        `${JSON.stringify({
          event: 'country_outage_p1_pi_semantic_run_audit',
          audit: record,
        })}\n`,
      )
    },
  })
  const chatService = new P1RuntimeV2ConversationService({
    provider: new HttpP1GeneralReadModelProvider(
      config.apiBaseUrl,
      config.apiTimeoutMs,
    ),
    planner: new P1ModelUserGoalPlanner(semanticModel),
    grounder: new P1TrendAwareGrounder(),
    ttlMs: 30 * 60 * 1000,
    turnTimeoutMs: config.turnTimeoutMs,
  })
  const requestListener = createCountryOutageAgentHttpHandler({
    chatService,
    chatReadiness: () => ({
      schema_version: 'country_outage_p1_chat_readiness_v1',
      ready: true,
      event_type: 'country_outage',
      collector_id: 'rrc25',
      model_profile: binding.preflight.profileId,
      registry_version: binding.preflight.registryVersion,
      certification_evidence_id:
        binding.preflight.certificationEvidenceId,
      certified_scenario_set_id:
        binding.preflight.certifiedScenarioSetId,
      certified_input_scope: binding.preflight.certifiedInputScope,
      maximum_provider_request_count_per_turn: 1,
      business_cost_limit: null,
      usage_and_estimated_cost_audit: 'required_per_provider_call',
      report_capability: 'disabled',
      external_evidence: 'disabled',
      event_window_trend_operator: {
        execution_unit: 'OP-04',
        capability_id: 'CAP-TREND-001',
        operator_id: 'event-window-trend',
        operator_version: '1.2.0',
        model_dependency: 'none',
      },
      tool_operator_registry: {
        candidate_id: registrySnapshot.snapshot_payload.candidate_id,
        registry_snapshot_id: registrySnapshot.registry_snapshot_id,
        registry_revision: registrySnapshot.snapshot_payload.registry_revision,
        activation_scope: registrySnapshot.snapshot_payload.activation_scope,
        runtime_integration: registrySnapshot.snapshot_payload.runtime_integration,
        production_deployed: registrySnapshot.production_deployed,
      },
    }),
    authenticate: createCountryOutageInternalAuthenticator(
      config.sharedToken,
    ),
  })
  const server = (
    dependencies.httpServerFactory ?? createServer
  )(requestListener)
  server.requestTimeout = Math.max(125_000, config.turnTimeoutMs + 5_000)
  server.headersTimeout = 10_000
  server.keepAliveTimeout = 20_000
  return {
    host: config.host,
    port: config.port,
    server,
    binding,
    dependencySecurityAttestation:
      dependencySecurityAttestation.audit,
    modelIdentity: semanticModel.identity,
    chatService,
    auditLog: {
      directory: auditLog.directory,
      retentionDays: auditLog.retentionDays,
    },
    runtime: {
      collector: 'rrc25',
      eventType: 'country_outage',
      apiBaseUrl: config.apiBaseUrl,
      apiTimeoutMs: config.apiTimeoutMs,
      modelTimeoutMs: config.modelTimeoutMs,
      turnTimeoutMs: config.turnTimeoutMs,
      maximumProviderRequestCountPerTurn: 1,
      businessCostLimit: null,
      reportCapability: 'disabled',
      externalEvidence: 'disabled',
      eventWindowTrendOperator: {
        executionUnit: 'OP-04',
        capabilityId: 'CAP-TREND-001',
        operatorId: 'event-window-trend',
        operatorVersion: '1.2.0',
        modelDependency: 'none',
      },
      toolOperatorRegistry: {
        candidateId: registrySnapshot.snapshot_payload.candidate_id,
        registrySnapshotId: registrySnapshot.registry_snapshot_id,
        registryRevision: registrySnapshot.snapshot_payload.registry_revision,
        activationScope: registrySnapshot.snapshot_payload.activation_scope,
        runtimeIntegration: registrySnapshot.snapshot_payload.runtime_integration,
        productionDeployed: registrySnapshot.production_deployed,
      },
    },
  }
}

export async function startFormalP1Sidecar(
  env: FormalP1SidecarEnvironment = process.env,
  dependencies: FormalP1SidecarDependencies = {},
): Promise<FormalP1Sidecar> {
  const sidecar = await createFormalP1Sidecar(env, dependencies)
  await new Promise<void>((resolve, reject) => {
    sidecar.server.once('error', reject)
    sidecar.server.listen(sidecar.port, sidecar.host, () => resolve())
  })
  return sidecar
}
