import assert from 'node:assert/strict'
import {
  chmodSync,
  existsSync,
  mkdtempSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test, { after } from 'node:test'

import type {
  CreateAgentSessionOptions,
  CreateModelRuntimeOptions,
  ModelRuntime,
} from '@earendil-works/pi-coding-agent'

import {
  assertFormalPiInstalledVersion,
  capCountryOutageModelOutput,
  createFormalPiModelBinding,
  createFormalPiModelBindingFromEnvironment,
  FormalPiRuntimeError,
  loadCertifiedPiModelRegistry,
  MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
  parseCertifiedPiModelRegistry,
  type CertifiedPiModelRegistry,
} from '../src/pi/index.js'

const CERTIFIED_PROFILE = {
  id: 'primary-v1',
  status: 'certified',
  provider: 'certified-provider',
  model: 'certified-model-20260728',
  modelVersion: 'certified-model-20260728',
  expectedResponseModel: 'certified-model-20260728',
  thinkingLevel: 'off',
  piVersion: '0.84.1',
  certificationEvidenceId: 'evidence:a4-model-certification',
  certifiedAt: '2026-07-28T15:00:00Z',
  modelRevisionKind: 'mutable_alias',
  immutableRevisionAvailable: false,
  limitation: MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
  certificationValidUntil: '2099-01-01T00:00:00Z',
  certifiedScenarioSetId: 'country-outage-formal-scenarios-test-v1',
  certifiedInputScope: 'legal_country_outage_rrc25_test_v1',
} as const

const AUTH_DIRECTORY = mkdtempSync(
  join(tmpdir(), 'domeye-formal-pi-auth-test-'),
)
const EMPTY_AUTH_PATH = join(AUTH_DIRECTORY, 'empty-auth.json')
writeFileSync(EMPTY_AUTH_PATH, '{}', { encoding: 'utf8', mode: 0o600 })
chmodSync(EMPTY_AUTH_PATH, 0o600)
after(() => {
  rmSync(AUTH_DIRECTORY, { recursive: true, force: true })
})

function registry(): CertifiedPiModelRegistry {
  return parseCertifiedPiModelRegistry({
    schemaVersion: 'country_outage_pi_certified_models_v1',
    registryVersion: 'country-outage-pi-models-test-v1',
    status: 'frozen',
    profiles: [CERTIFIED_PROFILE],
  })
}

function fakeModel(): NonNullable<CreateAgentSessionOptions['model']> {
  return {
    provider: CERTIFIED_PROFILE.provider,
    id: CERTIFIED_PROFILE.model,
    name: '认证测试模型',
    api: 'openai-responses',
    baseUrl: 'https://example.invalid',
    reasoning: false,
    input: ['text'],
    cost: { input: 1, output: 2, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128_000,
    maxTokens: 16_384,
  } as NonNullable<CreateAgentSessionOptions['model']>
}

interface FakeRuntimeOptions {
  authConfigured?: boolean
  authSource?: string
  modelAvailable?: boolean
  modelFound?: boolean
  runtimeError?: string
  throwAvailable?: boolean
}

function fakeRuntime(options: FakeRuntimeOptions = {}): ModelRuntime {
  const model = fakeModel()
  return {
    getError() {
      return options.runtimeError
    },
    getModel(provider: string, modelId: string) {
      if (options.modelFound === false) return undefined
      return provider === model.provider && modelId === model.id
        ? model
        : undefined
    },
    getProviderAuthStatus(provider: string) {
      assert.equal(provider, CERTIFIED_PROFILE.provider)
      return options.authConfigured === false
        ? { configured: false }
        : {
            configured: true,
            source: options.authSource ?? 'environment',
          }
    },
    async getAvailable(provider?: string) {
      assert.equal(provider, CERTIFIED_PROFILE.provider)
      if (options.throwAvailable) throw new Error('不得透传的认证错误正文')
      return options.modelAvailable === false ? [] : [model]
    },
    async getAuth() {
      throw new Error('预检不得读取或解析凭据正文')
    },
  } as unknown as ModelRuntime
}

test('正式模型预检固定关闭 models.json 与目录联网刷新', async () => {
  let captured: CreateModelRuntimeOptions | undefined
  const binding = await createFormalPiModelBinding({
    registry: registry(),
    profileId: CERTIFIED_PROFILE.id,
    authPath: EMPTY_AUTH_PATH,
    runtimeFactory: async (options) => {
      captured = options
      return fakeRuntime()
    },
  })

  assert.equal(captured?.modelsPath, null)
  assert.equal(captured?.allowModelNetwork, false)
  assert.ok(captured?.credentials)
  assert.equal('authPath' in (captured ?? {}), false)
  assert.deepEqual(await captured.credentials.list(), [])
  assert.equal(binding.model.provider, CERTIFIED_PROFILE.provider)
  assert.equal(binding.model.id, CERTIFIED_PROFILE.model)
  assert.equal(binding.preflight.auth.source, 'environment')
  assert.equal(binding.preflight.available, true)
  assert.equal(
    binding.preflight.certificationEvidenceId,
    CERTIFIED_PROFILE.certificationEvidenceId,
  )
  assert.equal(
    binding.preflight.modelRevisionKind,
    'mutable_alias',
  )
  assert.equal(binding.preflight.immutableRevisionAvailable, false)
  assert.equal(
    binding.preflight.limitation,
    MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
  )
  assert.equal(
    binding.preflight.certificationValidUntil,
    CERTIFIED_PROFILE.certificationValidUntil,
  )
  assert.equal(
    binding.preflight.certifiedScenarioSetId,
    CERTIFIED_PROFILE.certifiedScenarioSetId,
  )
  assert.equal(
    binding.preflight.certifiedInputScope,
    CERTIFIED_PROFILE.certifiedInputScope,
  )
  assert.equal(binding.certification.registryVersion, registry().registryVersion)
})

test('生产环境只允许选择注册表内已经认证的组合', async () => {
  const p1RegistryPath = resolve(
    process.cwd(),
    'resources/certified-models/country-outage-p1-semantic-models-v1.json',
  )
  let factoryCalled = false
  await assert.rejects(
    createFormalPiModelBindingFromEnvironment({
      env: {
        COUNTRY_OUTAGE_PI_PROFILE: 'not-certified',
        COUNTRY_OUTAGE_PI_AUTH_PATH: EMPTY_AUTH_PATH,
        COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH: p1RegistryPath,
      },
      runtimeFactory: async () => {
        factoryCalled = true
        return fakeRuntime()
      },
    }),
    (error: unknown) =>
      error instanceof FormalPiRuntimeError &&
      error.code === 'profile_not_certified',
  )
  assert.equal(factoryCalled, false)

  const defaultRegistry = await loadCertifiedPiModelRegistry(p1RegistryPath)
  assert.equal(defaultRegistry.profiles.length, 1)
  assert.equal(
    defaultRegistry.profiles[0]?.id,
    'deepseek-v4-flash-pi-0.84.1-v1',
  )
  assert.equal(defaultRegistry.profiles[0]?.status, 'certified')
})

test('注册表拒绝重复、未认证、Pi 版本漂移和无证据组合', () => {
  const {
    certificationValidUntil: _missingCertificationValidUntil,
    ...missingCertificationValidUntil
  } = CERTIFIED_PROFILE
  const invalidProfiles = [
    { ...CERTIFIED_PROFILE, status: 'candidate' },
    { ...CERTIFIED_PROFILE, piVersion: '0.82.2' },
    { ...CERTIFIED_PROFILE, certificationEvidenceId: '' },
    missingCertificationValidUntil,
    {
      ...CERTIFIED_PROFILE,
      certificationValidUntil: 'not-an-iso-timestamp',
    },
    {
      ...CERTIFIED_PROFILE,
      certifiedAt: CERTIFIED_PROFILE.certificationValidUntil,
    },
    {
      ...CERTIFIED_PROFILE,
      certifiedAt: '2099-01-02T00:00:00Z',
    },
    {
      ...CERTIFIED_PROFILE,
      modelRevisionKind: 'immutable_revision',
    },
    {
      ...CERTIFIED_PROFILE,
      immutableRevisionAvailable: true,
    },
    {
      ...CERTIFIED_PROFILE,
      limitation: '不完整限制',
    },
    { ...CERTIFIED_PROFILE, certifiedScenarioSetId: '' },
    { ...CERTIFIED_PROFILE, certifiedInputScope: '' },
    { ...CERTIFIED_PROFILE, unexpectedField: true },
  ]
  for (const profile of invalidProfiles) {
    assert.throws(
      () =>
        parseCertifiedPiModelRegistry({
          schemaVersion: 'country_outage_pi_certified_models_v1',
          registryVersion: 'test-v1',
          status: 'frozen',
          profiles: [profile],
        }),
      (error: unknown) =>
        error instanceof FormalPiRuntimeError &&
        error.code === 'registry_invalid',
    )
  }
  assert.throws(
    () =>
      parseCertifiedPiModelRegistry({
        schemaVersion: 'country_outage_pi_certified_models_v1',
        registryVersion: 'test-v1',
        status: 'frozen',
        profiles: [CERTIFIED_PROFILE, CERTIFIED_PROFILE],
      }),
    (error: unknown) =>
      error instanceof FormalPiRuntimeError &&
      error.code === 'registry_invalid',
  )
})

test('认证恰好到期或已过期时在读取 auth 和创建运行时前失败关闭', async (context) => {
  for (const item of [
    {
      name: '恰好到期',
      checkedAt: '2099-01-01T00:00:00Z',
    },
    {
      name: '已经过期',
      checkedAt: '2099-01-01T00:00:00.001Z',
    },
  ] as const) {
    await context.test(item.name, async () => {
      let runtimeFactoryCalled = false
      await assert.rejects(
        createFormalPiModelBinding({
          registry: registry(),
          profileId: CERTIFIED_PROFILE.id,
          authPath: join(
            AUTH_DIRECTORY,
            'expired-certification-must-not-read.json',
          ),
          now: () => new Date(item.checkedAt),
          runtimeFactory: async () => {
            runtimeFactoryCalled = true
            return fakeRuntime()
          },
        }),
        (error: unknown) =>
          error instanceof FormalPiRuntimeError &&
          error.code === 'certification_expired',
      )
      assert.equal(runtimeFactoryCalled, false)
    })
  }
})

test('认证字段缺失、非法或时间区间倒置时在读取 auth 前失败关闭', async (context) => {
  const {
    certificationValidUntil: _missingCertificationValidUntil,
    ...missingCertificationValidUntil
  } = CERTIFIED_PROFILE
  const {
    certifiedInputScope: _missingCertifiedInputScope,
    ...missingCertifiedInputScope
  } = CERTIFIED_PROFILE
  for (const [index, profile] of [
    missingCertificationValidUntil,
    missingCertifiedInputScope,
    {
      ...CERTIFIED_PROFILE,
      certificationValidUntil: 'invalid',
    },
    {
      ...CERTIFIED_PROFILE,
      certifiedAt: CERTIFIED_PROFILE.certificationValidUntil,
    },
    {
      ...CERTIFIED_PROFILE,
      certifiedAt: '2099-01-01T00:00:00.001Z',
    },
  ].entries()) {
    await context.test(`非法认证 profile ${index + 1}`, async () => {
      let runtimeFactoryCalled = false
      await assert.rejects(
        createFormalPiModelBinding({
          registry: {
            schemaVersion:
              'country_outage_pi_certified_models_v1',
            registryVersion: 'invalid-profile-test-v1',
            status: 'frozen',
            profiles: [profile],
          } as unknown as CertifiedPiModelRegistry,
          profileId: CERTIFIED_PROFILE.id,
          authPath: join(
            AUTH_DIRECTORY,
            'invalid-profile-must-not-read.json',
          ),
          runtimeFactory: async () => {
            runtimeFactoryCalled = true
            return fakeRuntime()
          },
        }),
        (error: unknown) =>
          error instanceof FormalPiRuntimeError &&
          error.code === 'registry_invalid',
      )
      assert.equal(runtimeFactoryCalled, false)
    })
  }
})

test('认证有效期结束前一毫秒仍允许进入正式运行时预检', async () => {
  let runtimeFactoryCalled = false
  await createFormalPiModelBinding({
    registry: registry(),
    profileId: CERTIFIED_PROFILE.id,
    authPath: EMPTY_AUTH_PATH,
    now: () => new Date('2098-12-31T23:59:59.999Z'),
    runtimeFactory: async () => {
      runtimeFactoryCalled = true
      return fakeRuntime()
    },
  })
  assert.equal(runtimeFactoryCalled, true)
})

test('生产预检核验真实安装的 Pi package 版本', () => {
  assert.doesNotThrow(() => assertFormalPiInstalledVersion('0.84.1'))
  assert.throws(
    () => assertFormalPiInstalledVersion('0.82.2'),
    (error: unknown) =>
      error instanceof FormalPiRuntimeError &&
      error.code === 'pi_version_mismatch',
  )
})

test('正式模型对象在交给 Pi 会话前将目录输出上限裁剪至 16384', () => {
  const catalogModel = {
    ...fakeModel(),
    maxTokens: 384_000,
  }
  const executionModel = capCountryOutageModelOutput(catalogModel)
  assert.equal(catalogModel.maxTokens, 384_000)
  assert.equal(executionModel.maxTokens, 16_384)
  assert.notStrictEqual(executionModel, catalogModel)
})

test('认证、模型目录、可用性和运行时元数据任一不满足均失败关闭', async (context) => {
  const cases: Array<{
    name: string
    options: FakeRuntimeOptions
    code: FormalPiRuntimeError['code']
  }> = [
    {
      name: '目录无模型',
      options: { modelFound: false },
      code: 'model_not_found',
    },
    {
      name: '认证不可用',
      options: { authConfigured: false },
      code: 'provider_auth_unavailable',
    },
    {
      name: '认证来源未知',
      options: { authSource: 'unknown' },
      code: 'provider_auth_unavailable',
    },
    {
      name: '认证来源声明为 models.json 命令',
      options: { authSource: 'models_json_command' },
      code: 'provider_auth_unavailable',
    },
    {
      name: '模型不在可用集合',
      options: { modelAvailable: false },
      code: 'model_not_available',
    },
    {
      name: '可用性检查失败',
      options: { throwAvailable: true },
      code: 'runtime_metadata_invalid',
    },
    {
      name: '运行时报告错误',
      options: { runtimeError: '不得透传的运行时正文' },
      code: 'runtime_metadata_invalid',
    },
  ]
  for (const item of cases) {
    await context.test(item.name, async () => {
      await assert.rejects(
        createFormalPiModelBinding({
          registry: registry(),
          profileId: CERTIFIED_PROFILE.id,
          authPath: EMPTY_AUTH_PATH,
          runtimeFactory: async () => fakeRuntime(item.options),
        }),
        (error: unknown) =>
          error instanceof FormalPiRuntimeError &&
          error.code === item.code &&
          !error.message.includes('不得透传'),
      )
    })
  }
})

test('认证路径必须由生产入口显式提供绝对路径', async () => {
  for (const authPath of ['', './pi-auth.json']) {
    await assert.rejects(
      createFormalPiModelBinding({
        registry: registry(),
        profileId: CERTIFIED_PROFILE.id,
        authPath,
        runtimeFactory: async () => fakeRuntime(),
      }),
      (error: unknown) =>
        error instanceof FormalPiRuntimeError &&
        error.code === 'auth_path_invalid',
    )
  }
})

test('命令型 auth key 在 ModelRuntime 工厂前失败关闭且不会执行', async () => {
  const markerPath = join(AUTH_DIRECTORY, 'command-must-not-run')
  const commandAuthPath = join(AUTH_DIRECTORY, 'command-auth.json')
  writeFileSync(
    commandAuthPath,
    JSON.stringify({
      [CERTIFIED_PROFILE.provider]: {
        type: 'api_key',
        key: `!touch ${markerPath}`,
      },
    }),
    { encoding: 'utf8', mode: 0o600 },
  )
  chmodSync(commandAuthPath, 0o600)
  let runtimeFactoryCalled = false

  await assert.rejects(
    createFormalPiModelBinding({
      registry: registry(),
      profileId: CERTIFIED_PROFILE.id,
      authPath: commandAuthPath,
      runtimeFactory: async () => {
        runtimeFactoryCalled = true
        return fakeRuntime()
      },
    }),
    (error: unknown) =>
      error instanceof FormalPiRuntimeError &&
      error.code === 'credential_command_forbidden',
  )
  assert.equal(runtimeFactoryCalled, false)
  assert.equal(existsSync(markerPath), false)
})

test('认证文件拒绝宽权限、符号链接与未知凭据结构', async (context) => {
  const cases = [
    {
      name: '宽权限',
      file: 'wide-auth.json',
      body: '{}',
      mode: 0o644,
      path: undefined as string | undefined,
    },
    {
      name: 'OAuth 结构',
      file: 'oauth-auth.json',
      body: JSON.stringify({
        [CERTIFIED_PROFILE.provider]: {
          type: 'oauth',
          access: 'not-a-real-token',
          refresh: 'not-a-real-refresh-token',
          expires: 0,
        },
      }),
      mode: 0o600,
      path: undefined as string | undefined,
    },
    {
      name: '未知字段',
      file: 'unknown-auth.json',
      body: JSON.stringify({
        [CERTIFIED_PROFILE.provider]: {
          type: 'api_key',
          key: 'not-a-real-token',
          command: 'forbidden',
        },
      }),
      mode: 0o600,
      path: undefined as string | undefined,
    },
  ]
  for (const item of cases) {
    item.path = join(AUTH_DIRECTORY, item.file)
    writeFileSync(item.path, item.body, {
      encoding: 'utf8',
      mode: item.mode,
    })
    chmodSync(item.path, item.mode)
    await context.test(item.name, async () => {
      await assert.rejects(
        createFormalPiModelBinding({
          registry: registry(),
          profileId: CERTIFIED_PROFILE.id,
          authPath: item.path!,
          runtimeFactory: async () => fakeRuntime(),
        }),
        (error: unknown) =>
          error instanceof FormalPiRuntimeError &&
          error.code === 'credential_store_invalid',
      )
    })
  }

  await context.test('符号链接', async () => {
    const symlinkPath = join(AUTH_DIRECTORY, 'symlink-auth.json')
    symlinkSync(EMPTY_AUTH_PATH, symlinkPath)
    await assert.rejects(
      createFormalPiModelBinding({
        registry: registry(),
        profileId: CERTIFIED_PROFILE.id,
        authPath: symlinkPath,
        runtimeFactory: async () => fakeRuntime(),
      }),
      (error: unknown) =>
        error instanceof FormalPiRuntimeError &&
        error.code === 'auth_path_invalid',
    )
  })
})
