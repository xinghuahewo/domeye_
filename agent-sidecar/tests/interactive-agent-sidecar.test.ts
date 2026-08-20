import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import type { IncomingMessage, RequestListener, Server } from 'node:http'
import { resolve } from 'node:path'
import test from 'node:test'

import type {
  DomeyeFirstSliceCandidateManifestPayload,
  LoadedDomeyeFirstSliceCandidateManifest,
} from '../src/agent/candidate-manifest.js'
import type { DomeyeDataIdentity } from '../src/agent/contracts.js'
import type { DomeyeFirstSliceCandidateBinding } from '../src/agent/first-slice-runtime.js'
import { DomeyeInteractiveConversationService } from '../src/agent/interactive-conversation-service.js'
import type { DomeyePiModelBinding } from '../src/agent/pi-interactive-agent-loop.js'
import {
  createDomeyeInteractiveAgentSidecar,
  startDomeyeInteractiveAgentSidecar,
  type DomeyeInteractiveAgentEnvironment,
} from '../src/cli/interactive-agent-sidecar.js'
import {
  createCountryOutageVerifierAuthenticator,
} from '../src/cli/sidecar-security.js'

const sha = (character: string): `sha256:${string}` =>
  `sha256:${character.repeat(64)}`

const candidateId = `manifest:sha256:${'c'.repeat(64)}`

const dataIdentity: DomeyeDataIdentity = {
  event_type: 'country_outage',
  incident_id: 'incident-first-slice-cli',
  publication_id: 'publication-first-slice-cli',
  revision: 1,
  collector_id: 'rrc25',
  cohort_id: 'cohort-first-slice-cli',
  country_code: 'IR',
  window_start_utc: '2026-02-27T00:10:00Z',
  window_end_utc: '2026-03-11T00:00:00Z',
  data_through: '2026-03-11T00:00:00Z',
  is_final_in_data_range: false,
  lifecycle_state: 'event_end_unknown',
}

const modelIdentity = {
  candidate_id: 'model-candidate-first-slice-cli',
  resource_sha256: sha('a'),
  provider: 'provider-first-slice-cli',
  model: 'model-first-slice-cli',
  model_version: 'model-first-slice-cli-20260819',
  expected_response_model: 'model-first-slice-cli',
  api: 'openai-completions' as const,
  base_url: 'https://provider.invalid/v1',
  maximum_output_tokens: 4_096,
  thinking_level: 'off' as const,
  pi_version: '0.84.1' as const,
}

const readerBinding = {
  execution_unit_id: 'TOOL-03' as const,
  execution_unit_name: 'read_metric_series' as const,
  execution_unit_version: '1.0.0' as const,
  contract_digest: sha('5'),
  implementation_digest: sha('6'),
  semantic_digest: sha('7'),
}

const extremaBinding = {
  execution_unit_id: 'OP-01' as const,
  execution_unit_name: 'series_extrema' as const,
  execution_unit_version: '1.0.0' as const,
  contract_digest: sha('8'),
  implementation_digest: sha('9'),
  semantic_digest: sha('b'),
}

const manifestPayload: DomeyeFirstSliceCandidateManifestPayload = {
  schema_version: 'domeye_first_slice_candidate_manifest_v2',
  base_commit: 'a'.repeat(40),
  contract: {
    version: 'domeye.first-vertical-slice/v1.0',
    digest: sha('1'),
  },
  answer_presentation_contract: {
    version: 'domeye.first-vertical-slice.answer-presentation/v1.0',
    digest: sha('d'),
  },
  data_identity: dataIdentity,
  series_response_sha256: sha('2'),
  model: modelIdentity,
  budget_policy: {
    model_api_attempt_limit: 10,
    approved_action_limit: 2,
    cost_policy: 'audit_only',
    monetary_limit_usd: null,
  },
  policy: {
    policy_id: 'first-slice-policy-cli',
    policy_digest: sha('3'),
    state: 'active',
    allowed_capability_ids: ['CAP-006', 'CAP-016'],
  },
  registry: {
    registry_snapshot_id: 'first-slice-registry-cli',
    registry_digest: sha('4'),
    state: 'active',
    capabilities: [
      {
        capability_id: 'CAP-006',
        state: 'active',
        execution_binding: readerBinding,
      },
      {
        capability_id: 'CAP-016',
        state: 'active',
        execution_binding: extremaBinding,
      },
    ],
  },
  attestation_policy: {
    schema_version: 'domeye_first_slice_attestation_policy_v1',
    algorithm: 'ed25519',
    canonicalization: 'domeye_unicode_codepoint_canonical_json_v1',
    signature_domains: {
      execution_evidence:
        'domeye.first-slice.evaluation-attestation/execution/v1',
      independent_review:
        'domeye.first-slice.evaluation-attestation/independent-review/v1',
    },
    release_eligible: true,
    execution_evidence: {
      role: 'execution_evidence',
      actor_id: 'domeye-first-slice-real-runtime-attestor-v1',
      key_id:
        'ed25519-spki-sha256:77b6cf4878e19aa98161ed06d4076bbeeffd47f086d02f65e91961dc000fc53d',
      public_key_spki_der_base64:
        'MCowBQYDK2VwAyEAamxMc7yzmYMTNH7iCH0jrcgOod/9/Wj5xsCjjLsMzQM=',
    },
    independent_review: {
      role: 'independent_review',
      actor_id: 'domeye-first-slice-independent-reviewer-v1',
      key_id:
        'ed25519-spki-sha256:785c71f709a61cd74a4801b0ce163c8614859ec367f9657b813caa458d2ebbdf',
      public_key_spki_der_base64:
        'MCowBQYDK2VwAyEAzzCysI/7F/LIc5UcVtawEwEN1yjkzvgrPvSDRUW8Qls=',
    },
  },
  source_files: [
    { path: 'src/reader.ts', sha256: readerBinding.implementation_digest },
    { path: 'src/extrema.ts', sha256: extremaBinding.implementation_digest },
  ],
  activation: {
    scope: 'local_evaluation_only',
    production_deployed: false,
  },
}

const candidate: DomeyeFirstSliceCandidateBinding = {
  candidate_id: candidateId,
  contract_version: manifestPayload.contract.version,
  contract_digest: manifestPayload.contract.digest,
  answer_presentation_contract_version:
    manifestPayload.answer_presentation_contract.version,
  answer_presentation_contract_digest:
    manifestPayload.answer_presentation_contract.digest,
  data_identity: dataIdentity,
  series_response_sha256: manifestPayload.series_response_sha256,
  model_identity: modelIdentity,
  budget_policy: manifestPayload.budget_policy,
  policy: manifestPayload.policy,
  registry: manifestPayload.registry,
}

const loadedCandidate: LoadedDomeyeFirstSliceCandidateManifest = {
  candidate,
  model_identity: modelIdentity,
  manifest: {
    candidate_id: candidateId,
    payload: manifestPayload,
  },
}

const modelBinding = {
  identity: modelIdentity,
  model: {},
  model_runtime: {},
  thinking_level: 'off',
} as unknown as DomeyePiModelBinding

function validEnvironment(): DomeyeInteractiveAgentEnvironment {
  return {
    COUNTRY_OUTAGE_AGENT_HOST: '127.0.0.1',
    COUNTRY_OUTAGE_AGENT_SHARED_TOKEN:
      'interactive-agent-sidecar-token',
    COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN:
      'interactive-agent-verifier-token',
    COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT: process.cwd(),
    COUNTRY_OUTAGE_FIRST_SLICE_CANDIDATE_MANIFEST:
      'contracts/agent/domeye-first-vertical-slice/candidate.json',
    COUNTRY_OUTAGE_PI_AUTH_PATH: '/run/domeye/interactive-agent-auth.json',
    DOMEYE_API_BASE_URL: 'http://127.0.0.1:28471',
    COUNTRY_OUTAGE_INTERACTIVE_AGENT_API_TIMEOUT_MS: '15000',
    COUNTRY_OUTAGE_INTERACTIVE_AGENT_CONVERSATION_TTL_MS: '1800000',
  }
}

function serverDouble(order: string[]): Server {
  const server = {
    requestTimeout: 0,
    headersTimeout: 0,
    keepAliveTimeout: 0,
    once(event: string) {
      order.push(`once:${event}`)
      return server
    },
    off(event: string) {
      order.push(`off:${event}`)
      return server
    },
    listen(port: number, host: string, callback: () => void) {
      order.push(`listen:${host}:${port}`)
      callback()
      return server
    },
  } as unknown as Server
  return server
}

test('唯一交互式 Agent CLI 精确装配 Candidate、模型、服务与 readiness', async () => {
  const order: string[] = []
  const manifestCalls: unknown[] = []
  const modelCalls: unknown[] = []
  let listener: RequestListener | undefined
  const server = serverDouble(order)

  const sidecar = await startDomeyeInteractiveAgentSidecar(
    validEnvironment(),
    {
      manifest_loader: async (options) => {
        order.push('manifest')
        manifestCalls.push(options)
        return loadedCandidate
      },
      model_binding_factory: async (options) => {
        order.push('model')
        modelCalls.push(options)
        return modelBinding
      },
      http_server_factory: (requestListener) => {
        order.push('server')
        listener = requestListener
        return server
      },
      now: () => new Date('2026-08-19T07:00:00.000Z'),
    },
  )

  assert.deepEqual(order, [
    'manifest',
    'model',
    'server',
    'once:error',
    'listen:127.0.0.1:28476',
    'off:error',
  ])
  assert.deepEqual(manifestCalls, [{
    project_root: resolve(process.cwd()),
    manifest_path:
      'contracts/agent/domeye-first-vertical-slice/candidate.json',
  }])
  assert.deepEqual(modelCalls, [{
    identity: modelIdentity,
    auth_path: '/run/domeye/interactive-agent-auth.json',
  }])
  assert.equal(typeof listener, 'function')
  assert.equal(sidecar.service instanceof DomeyeInteractiveConversationService, true)
  assert.equal(sidecar.server, server)
  assert.equal(server.requestTimeout, 125_000)
  assert.equal(server.headersTimeout, 10_000)
  assert.equal(server.keepAliveTimeout, 20_000)
  assert.equal(sidecar.loaded_candidate, loadedCandidate)
  assert.equal(sidecar.model_binding, modelBinding)
  assert.equal(Object.isFrozen(sidecar.readiness), true)
  assert.deepEqual(sidecar.readiness, {
    schema_version: 'domeye_interactive_agent_readiness_v1',
    ready: true,
    candidate_id: candidateId,
    activation_scope: 'local_evaluation_only',
    production_deployed: false,
    contract: manifestPayload.contract,
    answer_presentation_contract:
      manifestPayload.answer_presentation_contract,
    data_identity: dataIdentity,
    model_identity: modelIdentity,
    budget_policy: {
      model_api_attempt_limit: 10,
      approved_action_limit: 2,
      cost_policy: 'audit_only',
      monetary_limit_usd: null,
    },
    policy_id: 'first-slice-policy-cli',
    policy_digest: sha('3'),
    registry_snapshot_id: 'first-slice-registry-cli',
    registry_digest: sha('4'),
    capabilities: [
      {
        capability_id: 'CAP-006',
        execution_binding: readerBinding,
      },
      {
        capability_id: 'CAP-016',
        execution_binding: extremaBinding,
      },
    ],
    persistence: 'ephemeral',
    report_capability: 'disabled',
    external_evidence: 'disabled',
  })
})

test('非本机 host 与必需配置缺失时在任何工厂或 Server 前失败', async () => {
  let dependencyCallCount = 0
  const dependencies = {
    manifest_loader: async () => {
      dependencyCallCount += 1
      return loadedCandidate
    },
    model_binding_factory: async () => {
      dependencyCallCount += 1
      return modelBinding
    },
    http_server_factory: () => {
      dependencyCallCount += 1
      return serverDouble([])
    },
  }

  await assert.rejects(
    createDomeyeInteractiveAgentSidecar({
      ...validEnvironment(),
      COUNTRY_OUTAGE_AGENT_HOST: '0.0.0.0',
    }, dependencies),
    /只允许监听本机地址/,
  )
  assert.equal(dependencyCallCount, 0)

  const requiredConfiguration = [
    'COUNTRY_OUTAGE_AGENT_SHARED_TOKEN',
    'COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN',
    'COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT',
    'COUNTRY_OUTAGE_FIRST_SLICE_CANDIDATE_MANIFEST',
    'COUNTRY_OUTAGE_PI_AUTH_PATH',
    'DOMEYE_API_BASE_URL',
  ] as const
  for (const name of requiredConfiguration) {
    const env = { ...validEnvironment() }
    delete env[name]
    await assert.rejects(
      createDomeyeInteractiveAgentSidecar(env, dependencies),
      new RegExp(`缺少必需环境变量 ${name}`),
      name,
    )
    assert.equal(dependencyCallCount, 0, name)
  }

  await assert.rejects(
    createDomeyeInteractiveAgentSidecar({
      ...validEnvironment(),
      COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN:
        'interactive-agent-sidecar-token',
    }, dependencies),
    /必须与普通访问 Token 分离/,
  )
  assert.equal(dependencyCallCount, 0)
})

test('验证器 Token 同时绑定独立凭据与 loopback 来源', () => {
  const authenticate = createCountryOutageVerifierAuthenticator(
    'interactive-agent-verifier-token',
  )
  const request = (
    authorization: string,
    remoteAddress: string,
  ): IncomingMessage => ({
    headers: { authorization },
    socket: { remoteAddress },
  }) as unknown as IncomingMessage

  assert.equal(authenticate(request(
    'Bearer interactive-agent-verifier-token',
    '127.0.0.1',
  )), true)
  assert.equal(authenticate(request(
    'Bearer interactive-agent-sidecar-token',
    '127.0.0.1',
  )), false)
  assert.equal(authenticate(request(
    'Bearer interactive-agent-verifier-token',
    '10.0.0.8',
  )), false)
})

test('唯一 CLI 源码不依赖旧 chat，不包含路由选择器或 fallback', () => {
  const source = readFileSync(
    resolve(
      import.meta.dirname,
      '../../src/cli/interactive-agent-sidecar.ts',
    ),
    'utf8',
  )
  assert.doesNotMatch(source, /from ['"]\.\.\/chat\//)
  assert.doesNotMatch(
    source,
    /runtime-v2|page-capability-executor|p2-registry-runtime/,
  )
  assert.doesNotMatch(
    source,
    /(?:legacy|old)[_-]?route|route[_-]?(?:selector|fallback)|fallback(?:To|_to_)/i,
  )
})
