import {
  createServer,
  type RequestListener,
  type Server,
} from 'node:http'
import { isAbsolute, resolve } from 'node:path'

import {
  loadDomeyeFirstSliceCandidateManifest,
  type LoadedDomeyeFirstSliceCandidateManifest,
} from '../agent/candidate-manifest.js'
import { HttpCountryOutageReadModel } from '../agent/country-outage-read-model.js'
import { DomeyeFirstSliceRuntime } from '../agent/first-slice-runtime.js'
import {
  createDomeyeInteractiveAgentHttpHandler,
} from '../agent/interactive-http-handler.js'
import {
  DomeyeInteractiveConversationService,
} from '../agent/interactive-conversation-service.js'
import {
  createDomeyePiModelBinding,
} from '../agent/model-binding.js'
import type { DomeyePiModelBinding } from '../agent/pi-interactive-agent-loop.js'
import {
  assertCountryOutageLoopbackHost,
  createCountryOutageInternalAuthenticator,
  createCountryOutageVerifierAuthenticator,
  positiveIntegerEnvironmentValue,
  requiredEnvironmentValue,
  type SidecarEnvironment,
} from './sidecar-security.js'

export type DomeyeInteractiveAgentEnvironment = SidecarEnvironment

export interface DomeyeInteractiveAgentSidecarDependencies {
  readonly manifest_loader?: typeof loadDomeyeFirstSliceCandidateManifest
  readonly model_binding_factory?: typeof createDomeyePiModelBinding
  readonly http_server_factory?: (listener: RequestListener) => Server
  readonly now?: () => Date
}

export interface DomeyeInteractiveAgentSidecar {
  readonly host: string
  readonly port: number
  readonly server: Server
  readonly loaded_candidate: LoadedDomeyeFirstSliceCandidateManifest
  readonly model_binding: DomeyePiModelBinding
  readonly service: DomeyeInteractiveConversationService
  readonly readiness: Readonly<Record<string, unknown>>
}

function configuration(env: DomeyeInteractiveAgentEnvironment) {
  const host = env.COUNTRY_OUTAGE_AGENT_HOST?.trim() || '127.0.0.1'
  assertCountryOutageLoopbackHost(host)
  const port = positiveIntegerEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_AGENT_PORT',
    28_476,
  )
  if (port > 65_535) throw new Error('COUNTRY_OUTAGE_AGENT_PORT 无效')
  const sharedToken = requiredEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_AGENT_SHARED_TOKEN',
  )
  if (sharedToken.length < 24) {
    throw new Error('COUNTRY_OUTAGE_AGENT_SHARED_TOKEN 至少需要 24 字符')
  }
  const verifierToken = requiredEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN',
  )
  if (verifierToken.length < 24) {
    throw new Error('COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN 至少需要 24 字符')
  }
  if (verifierToken === sharedToken) {
    throw new Error('验证器 Token 必须与普通访问 Token 分离')
  }
  const projectRoot = resolve(requiredEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT',
  ))
  if (!isAbsolute(projectRoot)) {
    throw new Error('COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT 必须是绝对路径')
  }
  const apiTimeoutMs = positiveIntegerEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_INTERACTIVE_AGENT_API_TIMEOUT_MS',
    15_000,
  )
  if (apiTimeoutMs > 30_000) {
    throw new Error('交互式 Agent 数据 API 超时配置无效')
  }
  const ttlMs = positiveIntegerEnvironmentValue(
    env,
    'COUNTRY_OUTAGE_INTERACTIVE_AGENT_CONVERSATION_TTL_MS',
    30 * 60 * 1_000,
  )
  if (ttlMs < 60_000 || ttlMs > 24 * 60 * 60 * 1_000) {
    throw new Error('交互式 Agent 会话 TTL 配置无效')
  }
  return {
    host,
    port,
    sharedToken,
    verifierToken,
    projectRoot,
    manifestPath: requiredEnvironmentValue(
      env,
      'COUNTRY_OUTAGE_FIRST_SLICE_CANDIDATE_MANIFEST',
    ),
    modelAuthPath: requiredEnvironmentValue(
      env,
      'COUNTRY_OUTAGE_PI_AUTH_PATH',
    ),
    apiBaseUrl: requiredEnvironmentValue(env, 'DOMEYE_API_BASE_URL'),
    apiTimeoutMs,
    ttlMs,
  }
}

export async function createDomeyeInteractiveAgentSidecar(
  env: DomeyeInteractiveAgentEnvironment = process.env,
  dependencies: DomeyeInteractiveAgentSidecarDependencies = {},
): Promise<DomeyeInteractiveAgentSidecar> {
  const config = configuration(env)
  const now = dependencies.now ?? (() => new Date())
  const loadedCandidate = await (
    dependencies.manifest_loader ?? loadDomeyeFirstSliceCandidateManifest
  )({
    project_root: config.projectRoot,
    manifest_path: config.manifestPath,
  })
  const modelBinding = await (
    dependencies.model_binding_factory ?? createDomeyePiModelBinding
  )({
    identity: loadedCandidate.model_identity,
    auth_path: config.modelAuthPath,
  })
  const readModel = new HttpCountryOutageReadModel(config.apiBaseUrl, {
    timeout_ms: config.apiTimeoutMs,
    now,
  })
  const runtime = new DomeyeFirstSliceRuntime({
    candidate: loadedCandidate.candidate,
    model_binding: modelBinding,
    identity_verifier: readModel,
    series_read_model: readModel,
    revocation: () => ({
      state: 'not_revoked',
      checked_at_utc: now().toISOString(),
      reason_code: null,
    }),
    runtime_cwd: config.projectRoot,
    now,
  })
  const service = new DomeyeInteractiveConversationService({
    candidate: loadedCandidate.candidate,
    identity_verifier: readModel,
    runtime,
    ttl_ms: config.ttlMs,
    now,
  })
  const manifest = loadedCandidate.manifest
  const readiness = Object.freeze({
    schema_version: 'domeye_interactive_agent_readiness_v1',
    ready: true,
    candidate_id: manifest.candidate_id,
    activation_scope: manifest.payload.activation.scope,
    production_deployed: manifest.payload.activation.production_deployed,
    contract: manifest.payload.contract,
    answer_presentation_contract:
      manifest.payload.answer_presentation_contract,
    data_identity: manifest.payload.data_identity,
    model_identity: manifest.payload.model,
    budget_policy: manifest.payload.budget_policy,
    policy_id: manifest.payload.policy.policy_id,
    policy_digest: manifest.payload.policy.policy_digest,
    registry_snapshot_id: manifest.payload.registry.registry_snapshot_id,
    registry_digest: manifest.payload.registry.registry_digest,
    capabilities: manifest.payload.registry.capabilities.map((item) => ({
      capability_id: item.capability_id,
      execution_binding: item.execution_binding,
    })),
    persistence: 'ephemeral',
    report_capability: 'disabled',
    external_evidence: 'disabled',
  })
  const listener = createDomeyeInteractiveAgentHttpHandler({
    service,
    authenticate: createCountryOutageInternalAuthenticator(config.sharedToken),
    authenticate_verifier: createCountryOutageVerifierAuthenticator(
      config.verifierToken,
    ),
    readiness: () => readiness,
  })
  const server = (
    dependencies.http_server_factory ?? createServer
  )(listener)
  server.requestTimeout = 125_000
  server.headersTimeout = 10_000
  server.keepAliveTimeout = 20_000
  return Object.freeze({
    host: config.host,
    port: config.port,
    server,
    loaded_candidate: loadedCandidate,
    model_binding: modelBinding,
    service,
    readiness,
  })
}

export async function startDomeyeInteractiveAgentSidecar(
  env: DomeyeInteractiveAgentEnvironment = process.env,
  dependencies: DomeyeInteractiveAgentSidecarDependencies = {},
): Promise<DomeyeInteractiveAgentSidecar> {
  const sidecar = await createDomeyeInteractiveAgentSidecar(env, dependencies)
  await new Promise<void>((resolveListen, reject) => {
    sidecar.server.once('error', reject)
    sidecar.server.listen(sidecar.port, sidecar.host, () => {
      sidecar.server.off('error', reject)
      resolveListen()
    })
  })
  return sidecar
}
