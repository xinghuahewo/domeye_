import assert from 'node:assert/strict'
import {
  chmodSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test, { after } from 'node:test'

import type {
  CreateAgentSessionOptions,
  CreateModelRuntimeOptions,
  ModelRuntime,
} from '@earendil-works/pi-coding-agent'

import {
  createCandidatePiModelBinding,
  DEEPSEEK_V4_FLASH_CANDIDATE_ID,
  loadPiModelCandidate,
  parsePiModelCandidate,
  parsePiModelCertificationManifest,
  PiModelCertificationError,
  promotePiModelCandidate,
  runPiModelCandidateCertification,
  type CandidateCertificationRunnerResult,
  type LoadedPiModelCandidate,
} from '../src/pi/index.js'

const TEST_DIRECTORY = mkdtempSync(
  join(tmpdir(), 'domeye-model-certification-test-'),
)
const AUTH_PATH = join(TEST_DIRECTORY, 'deepseek-auth.json')
const FAKE_KEY = 'not-a-real-deepseek-key'
writeFileSync(
  AUTH_PATH,
  JSON.stringify({
    deepseek: { type: 'api_key', key: FAKE_KEY },
  }),
  { encoding: 'utf8', mode: 0o600 },
)
chmodSync(AUTH_PATH, 0o600)

after(() => {
  rmSync(TEST_DIRECTORY, { recursive: true, force: true })
})

function compatibleAdapterInspection() {
  return {
    sameNamePreserved: true,
    sourceSha256: 'a'.repeat(64),
  }
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

function fakeRuntime(
  mutateModel?: (
    model: NonNullable<CreateAgentSessionOptions['model']>,
  ) => NonNullable<CreateAgentSessionOptions['model']>,
): ModelRuntime {
  const model = mutateModel?.(fakeCatalogModel()) ?? fakeCatalogModel()
  return {
    getError() {
      return undefined
    },
    getModel(provider: string, modelId: string) {
      return provider === 'deepseek' && modelId === 'deepseek-v4-flash'
        ? model
        : undefined
    },
    getProviderAuthStatus(provider: string) {
      assert.equal(provider, 'deepseek')
      return { configured: true, source: 'stored' }
    },
    async getAvailable(provider?: string) {
      assert.equal(provider, 'deepseek')
      return [model]
    },
  } as unknown as ModelRuntime
}

function emptyRegistryPath(label: string): string {
  const path = join(TEST_DIRECTORY, `${label}-registry.json`)
  writeFileSync(
    path,
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
  return path
}

function successfulRun(
  runNumber: 1 | 2,
): CandidateCertificationRunnerResult {
  return {
    completedAt: `2026-07-29T0${runNumber}:00:00Z`,
    observedProvider: 'deepseek',
    observedModel: 'deepseek-v4-flash',
    responseModel: 'deepseek-v4-flash',
    // resolve + observation 的工具循环会产生多个正常请求轮次。
    providerRequestCount: 2,
    providerRetryAttempts: 0,
    structuredOutput: {
      mechanism:
        'deepseek-json-object-after-required-tools-v1',
      payloadPreparedCount: 1,
    },
    artifactId: `report-run-${runNumber}`,
    reportContentSha256: String(runNumber).repeat(64),
    reportDocumentSha256: 'e'.repeat(64),
    reportAuditManifestSha256: 'f'.repeat(64),
    piRunAuditSha256: '0'.repeat(64),
    factSetId: 'facts-fixed-snapshot',
    snapshotSha256: 'b'.repeat(64),
    evidenceInputSha256: '1'.repeat(64),
    validatorPassed: true,
    reportComplete: true,
    markdown: {
      ready: true,
      sha256: 'c'.repeat(64),
    },
    pdf: {
      ready: true,
      sha256: 'd'.repeat(64),
    },
    usage: {
      inputTokens: 10_000,
      outputTokens: 2_000,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    },
  }
}

async function loadedCandidate(): Promise<LoadedPiModelCandidate> {
  return await loadPiModelCandidate()
}

test('版本化 DeepSeek 候选资源固定身份、目录、价格和预算边界', async () => {
  const loaded = await loadedCandidate()
  assert.equal(
    loaded.candidate.candidateId,
    DEEPSEEK_V4_FLASH_CANDIDATE_ID,
  )
  assert.equal(loaded.candidate.status, 'candidate')
  assert.equal(loaded.candidate.thinkingLevel, 'off')
  assert.equal(loaded.candidate.piVersion, '0.82.1')
  assert.equal(loaded.candidate.execution.maximumOutputTokens, 16_384)
  assert.equal(
    loaded.candidate.execution.maximumProviderRequestCount,
    5,
  )
  assert.equal(loaded.candidate.execution.providerRetryAttempts, 0)
  assert.equal(
    loaded.candidate.certification.maximumIndependentReportRuns,
    2,
  )
  assert.equal(loaded.candidate.certification.budgetLimitCny, 20)
  assert.equal(
    loaded.candidate.certification.conservativeCnyPerUsd,
    8,
  )
  assert.deepEqual(
    loaded.candidate.adapterRequirement
      .approvedSameNameSourceSha256,
    [
      '5805cc08566c4d9437280f68d996ef0fb452c15e2becb67b94c967b7ace2023b',
    ],
  )
  assert.match(loaded.resourceSha256, /^[a-f0-9]{64}$/)

  assert.throws(
    () =>
      parsePiModelCandidate({
        ...loaded.candidate,
        status: 'certified',
      }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_invalid',
  )
})

test('缺少安全 auth path 时不创建模型运行时且正式注册表不变', async () => {
  const registryPath = emptyRegistryPath('missing-auth')
  const before = readFileSync(registryPath, 'utf8')
  let runtimeFactoryCalled = false

  await assert.rejects(
    createCandidatePiModelBinding({
      loadedCandidate: await loadedCandidate(),
      authPath: '',
      runtimeFactory: async () => {
        runtimeFactoryCalled = true
        return fakeRuntime()
      },
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_auth_required',
  )
  assert.equal(runtimeFactoryCalled, false)
  assert.equal(readFileSync(registryPath, 'utf8'), before)
})

test('Pi 0.82.1 受控补丁适配器通过固定摘要预检', async () => {
  let runtimeFactoryCalled = false
  const binding = await createCandidatePiModelBinding({
    loadedCandidate: await loadedCandidate(),
    authPath: AUTH_PATH,
    runtimeFactory: async () => {
      runtimeFactoryCalled = true
      return fakeRuntime()
    },
  })
  assert.equal(runtimeFactoryCalled, true)
  assert.equal(
    binding.preflight.responseModelAdapter.sourceSha256,
    '5805cc08566c4d9437280f68d996ef0fb452c15e2becb67b94c967b7ace2023b',
  )
})

test('未补丁适配器仍在创建模型运行时前失败关闭', async () => {
  let runtimeFactoryCalled = false
  await assert.rejects(
    createCandidatePiModelBinding({
      loadedCandidate: await loadedCandidate(),
      authPath: AUTH_PATH,
      responseModelAdapterInspector: () => ({
        sameNamePreserved: false,
        sourceSha256:
          '0d50250fe2931e66e2078279a397814202e1ecddee58faf4b8bc04c278da177a',
      }),
      runtimeFactory: async () => {
        runtimeFactoryCalled = true
        return fakeRuntime()
      },
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code ===
        'candidate_response_model_adapter_unsupported',
  )
  assert.equal(runtimeFactoryCalled, false)
})

test('候选预检复用冻结 CredentialStore，关闭 models.json/目录联网并实际裁剪输出上限', async () => {
  let captured: CreateModelRuntimeOptions | undefined
  const binding = await createCandidatePiModelBinding({
    loadedCandidate: await loadedCandidate(),
    authPath: AUTH_PATH,
    responseModelAdapterInspector: compatibleAdapterInspection,
    runtimeFactory: async (options) => {
      captured = options
      return fakeRuntime()
    },
  })

  assert.equal(captured?.modelsPath, null)
  assert.equal(captured?.allowModelNetwork, false)
  assert.ok(captured?.credentials)
  assert.equal('authPath' in (captured ?? {}), false)
  assert.deepEqual(await captured.credentials.list(), [
    { providerId: 'deepseek', type: 'api_key' },
  ])
  await assert.rejects(
    captured.credentials.modify(
      'deepseek',
      async () => ({
        type: 'api_key',
        key: 'replacement-not-real',
      }),
    ),
  )
  assert.equal(binding.model.maxTokens, 16_384)
  assert.equal(binding.preflight.runtimeIdentity, 'candidate')
  assert.equal(binding.preflight.auth.source, 'stored')
  assert.equal(
    binding.preflight.responseModelAdapter.sameNamePreserved,
    true,
  )
  assert.equal(binding.preflight.providerRetryAttempts, 0)
  assert.equal(binding.preflight.maximumProviderRequestCount, 5)
  assert.equal(
    binding.preflight.maximumCertificationCostCny,
    1.0838016,
  )
})

test('候选预检对价格、上下文和目录最大输出任一漂移失败关闭', async (context) => {
  const cases = [
    {
      name: '价格漂移',
      mutate: (
        model: NonNullable<CreateAgentSessionOptions['model']>,
      ) => ({
        ...model,
        cost: { ...model.cost, output: 0.29 },
      }),
    },
    {
      name: 'cacheRead 价格漂移',
      mutate: (
        model: NonNullable<CreateAgentSessionOptions['model']>,
      ) => ({
        ...model,
        cost: { ...model.cost, cacheRead: 0.0029 },
      }),
    },
    {
      name: 'cacheWrite 价格漂移',
      mutate: (
        model: NonNullable<CreateAgentSessionOptions['model']>,
      ) => ({
        ...model,
        cost: { ...model.cost, cacheWrite: 0.0001 },
      }),
    },
    {
      name: '上下文漂移',
      mutate: (
        model: NonNullable<CreateAgentSessionOptions['model']>,
      ) => ({ ...model, contextWindow: 999_999 }),
    },
    {
      name: '目录最大输出漂移',
      mutate: (
        model: NonNullable<CreateAgentSessionOptions['model']>,
      ) => ({ ...model, maxTokens: 383_999 }),
    },
  ]

  for (const item of cases) {
    await context.test(item.name, async () => {
      await assert.rejects(
        createCandidatePiModelBinding({
          loadedCandidate: await loadedCandidate(),
          authPath: AUTH_PATH,
          responseModelAdapterInspector:
            compatibleAdapterInspection,
          runtimeFactory: async () => fakeRuntime(item.mutate),
        }),
        (error: unknown) =>
          error instanceof PiModelCertificationError &&
          error.code === 'candidate_model_catalog_mismatch',
      )
    })
  }
})

test('两次独立完整报告均通过后只生成 candidate 清单，不自动写正式 registry', async () => {
  const registryPath = emptyRegistryPath('two-pass')
  const registryBefore = readFileSync(registryPath, 'utf8')
  const audits: unknown[] = []
  const runnerModels: number[] = []
  const loaded = await loadedCandidate()

  const manifest = await runPiModelCandidateCertification({
    loadedCandidate: loaded,
    authPath: AUTH_PATH,
    registryPath,
    responseModelAdapterInspector: compatibleAdapterInspection,
    runtimeFactory: async () => fakeRuntime(),
    runner: async ({ runNumber, binding }) => {
      runnerModels.push(binding.model.maxTokens)
      return successfulRun(runNumber)
    },
    auditSink(audit) {
      audits.push(audit)
    },
    now: () => new Date('2026-07-29T03:00:00Z'),
  })

  assert.deepEqual(runnerModels, [16_384, 16_384])
  assert.equal(manifest.runtimeIdentity, 'candidate')
  assert.equal(manifest.status, 'passed')
  assert.deepEqual(manifest.provenance, {
    runnerIdentity: 'candidate-framework-test-runner-v1',
    promotable: false,
    certificationFixtureId: null,
  })
  assert.equal(manifest.runs.length, 2)
  assert.equal(manifest.factEquivalence.passed, true)
  assert.equal(manifest.policy.priceAttestation, null)
  assert.equal(
    manifest.policy.responseModelAdapterSourceSha256,
    'a'.repeat(64),
  )
  assert.ok(manifest.budget.actualCertificationCostCny < 20)
  assert.equal(readFileSync(registryPath, 'utf8'), registryBefore)
  assert.equal(audits.length, 2)
  const serialized = JSON.stringify(audits)
  assert.match(serialized, /"runtimeIdentity":"candidate"/)
  assert.doesNotMatch(serialized, /certified/)
  assert.doesNotMatch(serialized, new RegExp(FAKE_KEY))
  assert.doesNotMatch(serialized, new RegExp(AUTH_PATH))
  assert.doesNotMatch(serialized, /prompt|toolResult|toolArguments/)
  assert.doesNotThrow(() =>
    parsePiModelCertificationManifest(manifest, loaded),
  )
  const forgedRealProvenance = structuredClone(manifest)
  forgedRealProvenance.provenance = {
    runnerIdentity: 'country-outage-full-report-runner-v1',
    promotable: true,
    certificationFixtureId:
      'a4-iran-country-outage-rrc25-v1',
  }
  assert.throws(
    () =>
      parsePiModelCertificationManifest(
        forgedRealProvenance,
        loaded,
      ),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'certification_manifest_invalid',
  )
})

test('同名 responseModel 缺失不得用 observedModel 补齐，失败时隔离 registry', async () => {
  const registryPath = emptyRegistryPath('response-model-missing')
  const before = readFileSync(registryPath, 'utf8')
  let runs = 0

  await assert.rejects(
    runPiModelCandidateCertification({
      loadedCandidate: await loadedCandidate(),
      authPath: AUTH_PATH,
      registryPath,
      responseModelAdapterInspector: compatibleAdapterInspection,
      runtimeFactory: async () => fakeRuntime(),
      runner: async ({ runNumber }) => {
        runs += 1
        const { responseModel: _removed, ...withoutResponseModel } =
          successfulRun(runNumber)
        return withoutResponseModel
      },
      now: () => new Date('2026-07-29T04:00:00Z'),
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_response_model_missing',
  )
  assert.equal(runs, 1)
  assert.equal(readFileSync(registryPath, 'utf8'), before)
})

test('真实工具循环的两次 provider 请求可通过且不计作 retry', async () => {
  const registryPath = emptyRegistryPath('two-provider-requests')
  const manifest = await runPiModelCandidateCertification({
    loadedCandidate: await loadedCandidate(),
    authPath: AUTH_PATH,
    registryPath,
    responseModelAdapterInspector: compatibleAdapterInspection,
    runtimeFactory: async () => fakeRuntime(),
    runner: async ({ runNumber }) => successfulRun(runNumber),
    now: () => new Date('2026-07-29T04:15:00Z'),
  })
  assert.deepEqual(
    manifest.runs.map((run) => run.checks.providerRequestCount),
    [2, 2],
  )
  assert.deepEqual(
    manifest.runs.map((run) => run.checks.providerRetryAttempts),
    [0, 0],
  )
})

test('候选完整报告超过五个请求轮次、发生 transport retry、聚合输入或输出超限时拒绝且 registry 零写入', async (context) => {
  const cases = [
    {
      id: 'provider-zero-requests',
      name: 'provider 请求轮次为零',
      mutate: (result: CandidateCertificationRunnerResult) => ({
        ...result,
        providerRequestCount: 0,
      }),
    },
    {
      id: 'provider-six-requests',
      name: 'provider 请求轮次达到六次',
      mutate: (result: CandidateCertificationRunnerResult) => ({
        ...result,
        providerRequestCount: 6,
      }),
    },
    {
      id: 'provider-retry',
      name: 'transport/provider retry 大于零',
      mutate: (result: CandidateCertificationRunnerResult) => ({
        ...result,
        providerRetryAttempts: 1,
      }),
    },
    {
      id: 'aggregate-input-over-limit',
      name: 'input-like 聚合 token 超过请求轮数乘冻结上限',
      mutate: (result: CandidateCertificationRunnerResult) => ({
        ...result,
        usage: {
          ...result.usage,
          inputTokens: 128_001,
          cacheReadTokens: 0,
        },
      }),
    },
    {
      id: 'output-over-limit',
      name: '输出 token 聚合超过请求轮数乘冻结上限',
      mutate: (result: CandidateCertificationRunnerResult) => ({
        ...result,
        usage: { ...result.usage, outputTokens: 32_769 },
      }),
    },
  ]
  for (const item of cases) {
    await context.test(item.name, async () => {
      const registryPath = emptyRegistryPath(
        `runner-boundary-${item.id}`,
      )
      const before = readFileSync(registryPath, 'utf8')
      await assert.rejects(
        runPiModelCandidateCertification({
          loadedCandidate: await loadedCandidate(),
          authPath: AUTH_PATH,
          registryPath,
          responseModelAdapterInspector:
            compatibleAdapterInspection,
          runtimeFactory: async () => fakeRuntime(),
          runner: async ({ runNumber }) =>
            item.mutate(successfulRun(runNumber)),
          now: () => new Date('2026-07-29T04:30:00Z'),
        }),
        (error: unknown) =>
          error instanceof PiModelCertificationError &&
          error.code === 'candidate_run_evidence_invalid',
      )
      assert.equal(readFileSync(registryPath, 'utf8'), before)
    })
  }
})

test('两次报告事实快照不等价时不产生可晋级清单', async () => {
  const registryPath = emptyRegistryPath('fact-mismatch')
  await assert.rejects(
    runPiModelCandidateCertification({
      loadedCandidate: await loadedCandidate(),
      authPath: AUTH_PATH,
      registryPath,
      responseModelAdapterInspector: compatibleAdapterInspection,
      runtimeFactory: async () => fakeRuntime(),
      runner: async ({ runNumber }) => ({
        ...successfulRun(runNumber),
        factSetId:
          runNumber === 1 ? 'facts-snapshot-a' : 'facts-snapshot-b',
      }),
      now: () => new Date('2026-07-29T05:00:00Z'),
    }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'candidate_fact_equivalence_failed',
  )
})

test('generic fake runner 的两次 pass 清单不可机械晋级', async () => {
  const registryPath = emptyRegistryPath('promotion')
  const before = readFileSync(registryPath, 'utf8')
  const loaded = await loadedCandidate()
  const manifest = await runPiModelCandidateCertification({
    loadedCandidate: loaded,
    authPath: AUTH_PATH,
    registryPath,
    responseModelAdapterInspector: compatibleAdapterInspection,
    runtimeFactory: async () => fakeRuntime(),
    runner: async ({ runNumber }) => successfulRun(runNumber),
    now: () => new Date('2026-07-29T06:00:00Z'),
  })
  assert.throws(
    () =>
      promotePiModelCandidate({
        loadedCandidate: loaded,
        manifest,
        newRegistryVersion: 'deepseek-certification-test-v2',
        registryPath,
        responseModelAdapterInspector:
          compatibleAdapterInspection,
      }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'certification_provenance_untrusted',
  )
  assert.equal(readFileSync(registryPath, 'utf8'), before)
  assert.equal((await loadedCandidate()).candidate.status, 'candidate')
})

test('清单证据被删改或 registry 已变化时 promotion 零写入', async () => {
  const registryPath = emptyRegistryPath('promotion-isolation')
  const loaded = await loadedCandidate()
  const manifest = await runPiModelCandidateCertification({
    loadedCandidate: loaded,
    authPath: AUTH_PATH,
    registryPath,
    responseModelAdapterInspector: compatibleAdapterInspection,
    runtimeFactory: async () => fakeRuntime(),
    runner: async ({ runNumber }) => successfulRun(runNumber),
    now: () => new Date('2026-07-29T07:00:00Z'),
  })
  const tampered = structuredClone(manifest) as unknown as {
    runs: Array<{ checks: { pdf: boolean } }>
  }
  tampered.runs[1]!.checks.pdf = false
  const before = readFileSync(registryPath, 'utf8')
  assert.throws(
    () =>
      promotePiModelCandidate({
        loadedCandidate: loaded,
        manifest: tampered,
        newRegistryVersion: 'must-not-write-v2',
        registryPath,
        responseModelAdapterInspector:
          compatibleAdapterInspection,
      }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'certification_manifest_invalid',
  )
  assert.equal(readFileSync(registryPath, 'utf8'), before)

  for (const mutate of [
    (
      value: typeof manifest,
    ): void => {
      ;(value.runs[0].artifacts as {
        reportAuditManifestSha256: string
      }).reportAuditManifestSha256 = '2'.repeat(64)
    },
    (
      value: typeof manifest,
    ): void => {
      ;(value.runs[0].artifacts as {
        piRunAuditSha256: string
      }).piRunAuditSha256 = '3'.repeat(64)
    },
    (
      value: typeof manifest,
    ): void => {
      ;(value.runs[1] as {
        evidenceInputSha256: string
      }).evidenceInputSha256 = '4'.repeat(64)
    },
  ]) {
    const changedEvidence = structuredClone(manifest)
    mutate(changedEvidence)
    assert.throws(
      () =>
        parsePiModelCertificationManifest(
          changedEvidence,
          loaded,
        ),
      (error: unknown) =>
        error instanceof PiModelCertificationError &&
        error.code === 'certification_manifest_invalid',
    )
  }

  writeFileSync(
    registryPath,
    before.replace(
      'promotion-isolation-registry-v1',
      'promotion-isolation-registry-changed',
    ),
    'utf8',
  )
  const changed = readFileSync(registryPath, 'utf8')
  assert.throws(
    () =>
      promotePiModelCandidate({
        loadedCandidate: loaded,
        manifest,
        newRegistryVersion: 'must-not-write-v3',
        registryPath,
        responseModelAdapterInspector:
          compatibleAdapterInspection,
      }),
    (error: unknown) =>
      error instanceof PiModelCertificationError &&
      error.code === 'certification_provenance_untrusted',
  )
  assert.equal(readFileSync(registryPath, 'utf8'), changed)
})
