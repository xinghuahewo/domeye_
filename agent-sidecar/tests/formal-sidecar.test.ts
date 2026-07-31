import assert from 'node:assert/strict'
import {
  chmodSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import type {
  IncomingMessage,
  RequestListener,
  Server,
} from 'node:http'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test, { after } from 'node:test'

import type {
  CreateAgentSessionOptions,
  ModelRuntime,
} from '@earendil-works/pi-coding-agent'

import {
  createFormalCountryOutageSidecar,
  createSafeFormalPiAuditSink,
  FORMAL_EXTERNAL_EVIDENCE_CAPABILITY,
  startFormalCountryOutageSidecar,
  type FormalSidecarEnvironment,
} from '../src/cli/formal-sidecar.js'
import {
  FormalAcceptanceConfigurationError,
} from '../src/formal-acceptance-runtime.js'
import {
  createFormalPiAuditLog,
  FORMAL_PI_AUDIT_FILE_PREFIX,
} from '../src/cli/formal-pi-audit-log.js'
import {
  countryOutageScopeAllowsEvent,
  createCountryOutageInternalAuthenticator,
} from '../src/cli/sidecar-security.js'
import {
  CountryOutageDependencyRiskExceptionError,
  formalPiModelRunSelection,
  FormalPiRuntimeError,
  MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
  type CertifiedPiModelProfile,
  type FormalPiModelBinding,
  type FormalPiRunAuditRecord,
} from '../src/pi/index.js'

const CERTIFIED_PROFILE: CertifiedPiModelProfile = {
  id: 'formal-sidecar-test-v1',
  status: 'certified',
  provider: 'certified-provider',
  model: 'certified-model-20260728',
  modelVersion: 'certified-model-20260728',
  expectedResponseModel: 'certified-model-20260728',
  thinkingLevel: 'off',
  piVersion: '0.82.1',
  certificationEvidenceId: 'evidence:a4-formal-sidecar-test',
  certifiedAt: '2026-07-28T15:00:00Z',
  modelRevisionKind: 'mutable_alias',
  immutableRevisionAvailable: false,
  limitation: MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
  certificationValidUntil: '2099-01-01T00:00:00Z',
  certifiedScenarioSetId:
    'country-outage-formal-sidecar-scenarios-test-v1',
  certifiedInputScope: 'legal_country_outage_rrc25_test_v1',
}

const FORMAL_TEST_AUDIT_DIRECTORY = realpathSync(
  mkdtempSync(join(tmpdir(), 'domeye-formal-sidecar-audit-')),
)
chmodSync(FORMAL_TEST_AUDIT_DIRECTORY, 0o700)
after(() => {
  rmSync(FORMAL_TEST_AUDIT_DIRECTORY, {
    recursive: true,
    force: true,
  })
})

function fakeModel(): NonNullable<CreateAgentSessionOptions['model']> {
  return {
    provider: CERTIFIED_PROFILE.provider,
    id: CERTIFIED_PROFILE.model,
    name: '正式接线测试模型',
    api: 'openai-responses',
    baseUrl: 'https://example.invalid',
    reasoning: false,
    input: ['text'],
    cost: { input: 1, output: 2, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128_000,
    maxTokens: 16_384,
  } as NonNullable<CreateAgentSessionOptions['model']>
}

function fakeBinding(): FormalPiModelBinding {
  const model = fakeModel()
  const certification = {
    registryVersion: 'formal-sidecar-registry-test-v1',
    profile: CERTIFIED_PROFILE,
  }
  return {
    model,
    modelRuntime: {} as ModelRuntime,
    certification,
    runSelection: formalPiModelRunSelection(certification),
    preflight: {
      schemaVersion: 'country_outage_pi_model_preflight_v1',
      registryVersion: 'formal-sidecar-registry-test-v1',
      profileId: CERTIFIED_PROFILE.id,
      provider: CERTIFIED_PROFILE.provider,
      model: CERTIFIED_PROFILE.model,
      modelVersion: CERTIFIED_PROFILE.modelVersion,
      expectedResponseModel: CERTIFIED_PROFILE.expectedResponseModel,
      thinkingLevel: CERTIFIED_PROFILE.thinkingLevel,
      piVersion: '0.82.1',
      certificationEvidenceId:
        CERTIFIED_PROFILE.certificationEvidenceId,
      modelRevisionKind: CERTIFIED_PROFILE.modelRevisionKind,
      immutableRevisionAvailable:
        CERTIFIED_PROFILE.immutableRevisionAvailable,
      limitation: CERTIFIED_PROFILE.limitation,
      certificationValidUntil:
        CERTIFIED_PROFILE.certificationValidUntil,
      certifiedScenarioSetId:
        CERTIFIED_PROFILE.certifiedScenarioSetId,
      certifiedInputScope:
        CERTIFIED_PROFILE.certifiedInputScope,
      auth: { configured: true, source: 'environment' },
      maximumOutputTokens: 16_384,
      available: true,
    },
  }
}

function validEnvironment(): FormalSidecarEnvironment {
  return {
    COUNTRY_OUTAGE_AGENT_NARRATOR: 'pi-sdk-certified',
    COUNTRY_OUTAGE_AGENT_HOST: '127.0.0.1',
    COUNTRY_OUTAGE_AGENT_PORT: '28474',
    COUNTRY_OUTAGE_AGENT_SHARED_TOKEN:
      'formal-sidecar-internal-token',
    DOMEYE_API_BASE_URL: 'http://127.0.0.1:28471',
    DOMEYE_API_TIMEOUT_MS: '5000',
    DOMEYE_REPORT_PYTHON_EXECUTABLE: '/usr/bin/python3',
    DOMEYE_REPORT_FONT_PATH: '/opt/domeye/fonts/test.otf',
    DOMEYE_REPORT_PDF_TIMEOUT_MS: '45000',
    COUNTRY_OUTAGE_PI_PROFILE: CERTIFIED_PROFILE.id,
    COUNTRY_OUTAGE_PI_AUTH_PATH:
      '/run/domeye-secrets/formal-sidecar-test.json',
    COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY:
      FORMAL_TEST_AUDIT_DIRECTORY,
  }
}

function riskExceptionResource(): Record<string, unknown> {
  return JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        'resources/risk-exceptions/country-outage-pi-ghsa-mh99-v99m-4gvg-v2.json',
      ),
      'utf8',
    ),
  ) as Record<string, unknown>
}

function fakeServer(
  order: string[],
): { server: Server; listeners: { request?: RequestListener } } {
  const listeners: { request?: RequestListener } = {}
  const server = {
    requestTimeout: 0,
    headersTimeout: 0,
    keepAliveTimeout: 0,
    once(event: string) {
      order.push(`once:${event}`)
      return server
    },
    listen(port: number, host: string, callback: () => void) {
      order.push(`listen:${host}:${port}`)
      callback()
      return server
    },
  } as unknown as Server
  return { server, listeners }
}

test('默认空认证注册表在创建 HTTP Server 和监听端口前失败关闭', async () => {
  let serverFactoryCalled = false
  const env = {
    ...validEnvironment(),
    COUNTRY_OUTAGE_PI_PROFILE: 'not-certified',
  }

  await assert.rejects(
    createFormalCountryOutageSidecar(env, {
      riskExceptionNow: () => new Date('2026-08-01T00:00:00Z'),
      httpServerFactory() {
        serverFactoryCalled = true
        throw new Error('不应创建 HTTP Server')
      },
    }),
    (error: unknown) =>
      error instanceof FormalPiRuntimeError &&
      error.code === 'profile_not_certified',
  )
  assert.equal(serverFactoryCalled, false)
})

test('正式 Sidecar 按模型预检、Pi 叙述器、服务和 HTTP 监听顺序完成接线且不调用模型', async () => {
  const order: string[] = []
  let modelCallCount = 0
  const binding = fakeBinding()
  binding.modelRuntime = {
    async complete() {
      modelCallCount += 1
      throw new Error('启动期间禁止调用模型')
    },
  } as unknown as ModelRuntime
  const holder = fakeServer(order)

  const sidecar = await startFormalCountryOutageSidecar(
    validEnvironment(),
    {
      riskExceptionNow: () => new Date('2026-08-01T00:00:00Z'),
      bindingFactory: async () => {
        order.push('model-preflight')
        return binding
      },
      httpServerFactory(listener) {
        order.push('http-server-created')
        holder.listeners.request = listener
        return holder.server
      },
      auditWriter() {
        throw new Error('启动期间不应产生模型运行审计')
      },
    },
  )

  assert.deepEqual(order, [
    'model-preflight',
    'http-server-created',
    'once:error',
    'listen:127.0.0.1:28474',
  ])
  assert.equal(modelCallCount, 0)
  assert.equal(sidecar.narrator.identity.adapter, 'pi-sdk')
  assert.equal(
    sidecar.narrator.identity.model,
    CERTIFIED_PROFILE.model,
  )
  assert.ok(sidecar.manager)
  assert.ok(sidecar.core)
  assert.equal(sidecar.manager, sidecar.orchestrator)
  assert.equal(typeof holder.listeners.request, 'function')
  assert.deepEqual(
    sidecar.externalEvidenceCapability,
    FORMAL_EXTERNAL_EVIDENCE_CAPABILITY,
  )
  assert.equal(
    sidecar.externalEvidenceCapability.state,
    'not_configured',
  )
  assert.equal(
    sidecar.externalEvidenceCapability.provider,
    'disabled',
  )
  assert.equal(sidecar.baseReportCacheTtlMs, 3_600_000)
  assert.equal(
    sidecar.acceptanceRuntime.id,
    'country-outage-agent-core-acceptance-v3',
  )
  assert.equal(sidecar.acceptanceRuntime.scope.collectorId, 'rrc25')
  assert.deepEqual(
    sidecar.manager.limits,
    sidecar.acceptanceRuntime.formal.sessionLimits,
  )
  assert.deepEqual(sidecar.auditLog, {
    directory: FORMAL_TEST_AUDIT_DIRECTORY,
    retentionDays: 30,
  })
  assert.deepEqual(sidecar.dependencyRiskException, {
    exceptionId:
      'country-outage-pi-ghsa-mh99-v99m-4gvg-20260812-v2',
    expiresAt: '2026-08-12T16:00:00Z',
    status: 'active',
  })
  assert.equal(
    sidecar.reportServiceIdentity.validatorRulesVersion,
    'country_outage_report_validator_rules_v5',
  )
  assert.equal(
    sidecar.reportServiceIdentity.skillBundleSha256,
    sidecar.narrator.skillBundleSha256,
  )
})

test('正式入口拒绝 deterministic-acceptance 且不会开始模型预检或创建 Server', async () => {
  let bindingFactoryCalled = false
  let serverFactoryCalled = false
  await assert.rejects(
    createFormalCountryOutageSidecar(
      {
        ...validEnvironment(),
        COUNTRY_OUTAGE_AGENT_NARRATOR: 'deterministic-acceptance',
      },
      {
        riskExceptionNow: () => new Date('2026-08-01T00:00:00Z'),
        bindingFactory: async () => {
          bindingFactoryCalled = true
          return fakeBinding()
        },
        httpServerFactory() {
          serverFactoryCalled = true
          throw new Error('不应创建 HTTP Server')
        },
      },
    ),
    /正式入口只允许 pi-sdk-certified/,
  )
  assert.equal(bindingFactoryCalled, false)
  assert.equal(serverFactoryCalled, false)
})

test('正式入口在模型预检前拒绝 API、PDF 和 cache 环境量化值漂移', async () => {
  const drifts = [
    ['DOMEYE_API_TIMEOUT_MS', '10000'],
    ['DOMEYE_REPORT_PDF_TIMEOUT_MS', '45001'],
    ['COUNTRY_OUTAGE_AGENT_REPORT_CACHE_TTL_MS', '3599999'],
  ] as const

  for (const [name, value] of drifts) {
    let bindingFactoryCalled = false
    let serverFactoryCalled = false
    await assert.rejects(
      createFormalCountryOutageSidecar(
        {
          ...validEnvironment(),
          [name]: value,
        },
        {
          riskExceptionNow: () =>
            new Date('2026-08-01T00:00:00Z'),
          bindingFactory: async () => {
            bindingFactoryCalled = true
            return fakeBinding()
          },
          httpServerFactory() {
            serverFactoryCalled = true
            throw new Error('量化值漂移时不应创建 HTTP Server')
          },
        },
      ),
      (error: unknown) =>
        error instanceof FormalAcceptanceConfigurationError &&
        error.code === 'acceptance_environment_drift',
      name,
    )
    assert.equal(bindingFactoryCalled, false, name)
    assert.equal(serverFactoryCalled, false, name)
  }
})

test('正式入口缺失审计目录配置时在模型预检和 HTTP Server 创建前失败关闭', async () => {
  let bindingFactoryCalled = false
  let serverFactoryCalled = false
  const env = { ...validEnvironment() }
  delete env.COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY

  await assert.rejects(
    createFormalCountryOutageSidecar(env, {
      riskExceptionNow: () => new Date('2026-08-01T00:00:00Z'),
      bindingFactory: async () => {
        bindingFactoryCalled = true
        return fakeBinding()
      },
      httpServerFactory() {
        serverFactoryCalled = true
        throw new Error('审计目录缺失时不应创建 HTTP Server')
      },
    }),
    /缺少必需环境变量 COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY/,
  )
  assert.equal(bindingFactoryCalled, false)
  assert.equal(serverFactoryCalled, false)
})

test('正式入口审计目录权限过宽时在模型预检和 HTTP Server 创建前失败关闭', async () => {
  const temporaryDirectory = realpathSync(
    mkdtempSync(join(tmpdir(), 'domeye-formal-audit-wide-')),
  )
  chmodSync(temporaryDirectory, 0o750)
  try {
    let bindingFactoryCalled = false
    let serverFactoryCalled = false
    await assert.rejects(
      createFormalCountryOutageSidecar(
        {
          ...validEnvironment(),
          COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY: temporaryDirectory,
        },
        {
          riskExceptionNow: () =>
            new Date('2026-08-01T00:00:00Z'),
          bindingFactory: async () => {
            bindingFactoryCalled = true
            return fakeBinding()
          },
          httpServerFactory() {
            serverFactoryCalled = true
            throw new Error('审计目录无效时不应创建 HTTP Server')
          },
        },
      ),
      /正式 Pi 审计目录权限必须是 0700/,
    )
    assert.equal(bindingFactoryCalled, false)
    assert.equal(serverFactoryCalled, false)
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true })
  }
})

test('风险例外到期时在模型预检和 HTTP Server 创建前失败关闭', async () => {
  let bindingFactoryCalled = false
  let serverFactoryCalled = false
  await assert.rejects(
    createFormalCountryOutageSidecar(validEnvironment(), {
      riskExceptionNow: () =>
        new Date('2026-08-12T16:00:00Z'),
      bindingFactory: async () => {
        bindingFactoryCalled = true
        return fakeBinding()
      },
      httpServerFactory() {
        serverFactoryCalled = true
        throw new Error('风险例外到期后不得创建 HTTP Server')
      },
    }),
    (error: unknown) =>
      error instanceof CountryOutageDependencyRiskExceptionError &&
      error.code === 'risk_exception_expired',
  )
  assert.equal(bindingFactoryCalled, false)
  assert.equal(serverFactoryCalled, false)
})

test('风险例外正式路径约束漂移时在模型预检前失败关闭', async () => {
  const temporaryDirectory = realpathSync(
    mkdtempSync(join(tmpdir(), 'domeye-risk-exception-drift-')),
  )
  const riskExceptionPath = join(
    temporaryDirectory,
    'risk-exception.json',
  )
  const resource = riskExceptionResource()
  ;(
    resource.constraints as Record<string, unknown>
  ).externalGlobEnabled = true
  writeFileSync(
    riskExceptionPath,
    `${JSON.stringify(resource, null, 2)}\n`,
  )

  try {
    let bindingFactoryCalled = false
    let serverFactoryCalled = false
    await assert.rejects(
      createFormalCountryOutageSidecar(validEnvironment(), {
        riskExceptionPath,
        riskExceptionNow: () =>
          new Date('2026-08-01T00:00:00Z'),
        bindingFactory: async () => {
          bindingFactoryCalled = true
          return fakeBinding()
        },
        httpServerFactory() {
          serverFactoryCalled = true
          throw new Error('约束漂移后不得创建 HTTP Server')
        },
      }),
      (error: unknown) =>
        error instanceof
          CountryOutageDependencyRiskExceptionError &&
        error.code === 'risk_exception_constraint_mismatch',
    )
    assert.equal(bindingFactoryCalled, false)
    assert.equal(serverFactoryCalled, false)
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true })
  }
})

test('正式审核 sink 仅输出白名单元数据并剥离正文、工具参数和凭据', async (context) => {
  const lines: string[] = []
  const temporaryDirectory = realpathSync(
    mkdtempSync(join(tmpdir(), 'domeye-formal-audit-sanitize-')),
  )
  chmodSync(temporaryDirectory, 0o700)
  context.after(() => {
    rmSync(temporaryDirectory, { recursive: true, force: true })
  })
  const record: FormalPiRunAuditRecord & Record<string, unknown> = {
    schemaVersion: 'country_outage_pi_run_audit_v3',
    recordedAt: '2026-07-28T16:00:00Z',
    outcome: 'rejected',
    runtimeIdentity: 'formal',
    registryVersion: 'registry-v1',
    profileId: CERTIFIED_PROFILE.id,
    provider: CERTIFIED_PROFILE.provider,
    model: CERTIFIED_PROFILE.model,
    modelVersion: CERTIFIED_PROFILE.modelVersion,
    expectedResponseModel: CERTIFIED_PROFILE.expectedResponseModel,
    piVersion: '0.82.1',
    certificationEvidenceId:
      CERTIFIED_PROFILE.certificationEvidenceId,
    input: {
      eventReferenceSha256: 'f'.repeat(64),
      incidentId: 'incident-ir',
      publicationId: 'publication-ir-r7',
      revision: 7,
      dataThrough: '2026-02-28T23:00:00Z',
      factSetId: 'facts-ir-r7',
      collectorId: 'rrc25',
      reportSpecificationVersion: 'country_outage_report_spec_v1',
      projectKnowledgeVersion: 'country_outage_report_skill_v6',
      validatorRulesVersion:
        'country_outage_report_validator_rules_v5',
    },
    narration: {
      mode: 'deterministic-base-with-language-slots-v1',
      slotContractVersion: 'country_outage_language_slots_v1',
      requestedSlotCount: 5,
      acceptedSlotCount: 0,
      baseV5: 'passed',
      mergeInvariant: 'not_run',
      finalV5: 'not_run',
      modelOutputApplied: false,
    },
    runtimeSecurity: {
      resourceLoaderId: 'country-outage-static-resource-loader-v1',
      skillBundleSha256: 'a'.repeat(64),
      packageManagerResolutionEnabled: false,
      modelResolverEnabled: false,
      modelsJsonEnabled: false,
      modelCatalogNetworkRefreshEnabled: false,
      explicitModel: true,
      providerRetryAttempts: 0,
      forwardedProviderRequestCount: 1,
      structuredOutput: {
        applicability: 'required',
        mechanism:
          'deepseek-json-object-after-required-tools-v1',
        payloadPreparedCount: 0,
      },
      dependencyRiskException: {
        exceptionId:
          'country-outage-pi-ghsa-mh99-v99m-4gvg-20260812-v2',
        expiresAt: '2026-08-12T16:00:00Z',
        status: 'active',
      },
    },
    modelAttempt: {
      timeoutMs: 75_000,
      maximumAttempts: 2,
      executedAttempts: 1,
    },
    tools: {
      executedNames: ['country_outage_resolve'],
      executionCount: 1,
      unauthorizedAttemptCount: 0,
    },
    rejectionCode: 'required_tool_missing',
    prompt: '不得落盘的提示词正文',
    answerText: '不得落盘的回答正文',
    credential: 'sk-never-write-this',
  }
  Object.assign(record.tools, {
    arguments: { url: 'https://secret.invalid/?token=credential' },
  })
  Object.assign(record.input, {
    prompt: '嵌套提示词也不得写入',
    credential: 'nested-secret',
  })
  Object.assign(record.narration, {
    slotText: '不得写入的模型语言槽正文',
  })
  Object.assign(record.runtimeSecurity.dependencyRiskException, {
    advisory: '不得写入安全审计的 advisory 正文',
    component: 'brace-expansion@5.0.7',
    approvalText: '不得写入安全审计的批准正文',
  })

  const persistentLog = createFormalPiAuditLog({
    directory: temporaryDirectory,
    now: () => new Date('2026-07-29T00:00:00Z'),
  })
  const sink = createSafeFormalPiAuditSink(async (line) => {
    lines.push(line)
    await persistentLog.writeLine(line)
  })
  await sink(record)

  assert.equal(lines.length, 1)
  const output = lines[0]!
  assert.equal(
    readFileSync(
      join(
        temporaryDirectory,
        `${FORMAL_PI_AUDIT_FILE_PREFIX}2026-07-29.jsonl`,
      ),
      'utf8',
    ),
    output,
  )
  assert.match(output, /country_outage_pi_run_audit/)
  assert.match(output, /required_tool_missing/)
  assert.doesNotMatch(output, /提示词正文/)
  assert.doesNotMatch(output, /回答正文/)
  assert.doesNotMatch(output, /sk-never-write-this/)
  assert.doesNotMatch(output, /secret\.invalid/)
  assert.doesNotMatch(output, /嵌套提示词/)
  assert.doesNotMatch(output, /nested-secret/)
  assert.doesNotMatch(output, /模型语言槽正文/)
  assert.doesNotMatch(output, /advisory 正文/)
  assert.doesNotMatch(output, /brace-expansion/)
  assert.doesNotMatch(output, /批准正文/)
  const parsed = JSON.parse(output) as {
    event: string
    audit: Record<string, unknown>
  }
  assert.equal(parsed.event, 'country_outage_pi_run_audit')
  assert.equal('prompt' in parsed.audit, false)
  assert.equal('answerText' in parsed.audit, false)
  assert.equal('credential' in parsed.audit, false)
  assert.deepEqual(parsed.audit.input, {
    eventReferenceSha256: 'f'.repeat(64),
    incidentId: 'incident-ir',
    publicationId: 'publication-ir-r7',
    revision: 7,
    dataThrough: '2026-02-28T23:00:00Z',
    factSetId: 'facts-ir-r7',
    collectorId: 'rrc25',
    reportSpecificationVersion: 'country_outage_report_spec_v1',
    projectKnowledgeVersion: 'country_outage_report_skill_v6',
    validatorRulesVersion:
      'country_outage_report_validator_rules_v5',
  })
  assert.deepEqual(parsed.audit.narration, {
    mode: 'deterministic-base-with-language-slots-v1',
    slotContractVersion: 'country_outage_language_slots_v1',
    requestedSlotCount: 5,
    acceptedSlotCount: 0,
    baseV5: 'passed',
    mergeInvariant: 'not_run',
    finalV5: 'not_run',
    modelOutputApplied: false,
  })
  assert.deepEqual(
    (
      parsed.audit.runtimeSecurity as {
        dependencyRiskException: unknown
      }
    ).dependencyRiskException,
    {
      exceptionId:
        'country-outage-pi-ghsa-mh99-v99m-4gvg-20260812-v2',
      expiresAt: '2026-08-12T16:00:00Z',
      status: 'active',
    },
  )
})

test('正式与验收入口共享 loopback 内部 Token 和国家事件授权边界', () => {
  const authenticate = createCountryOutageInternalAuthenticator(
    'formal-sidecar-internal-token',
  )
  const request = {
    headers: {
      authorization: 'Bearer formal-sidecar-internal-token',
      'x-domeye-user': 'user@example.test',
      'x-domeye-authorization-scope':
        'country_outage_event_read:IR',
    },
  } as unknown as IncomingMessage
  const principal = authenticate(request)
  assert.ok(principal)
  assert.equal(
    countryOutageScopeAllowsEvent(
      principal,
      'country_outage/2026-02-27+09:12:32/IR/1/r',
    ),
    true,
  )
  assert.equal(
    countryOutageScopeAllowsEvent(
      principal,
      'country_outage/2026-02-27+09:12:32/CN/1/r',
    ),
    false,
  )
  assert.equal(
    countryOutageScopeAllowsEvent(
      principal,
      'country_outage/2026-02-27+09:12:32/IR/1/not-r',
    ),
    false,
  )
})
