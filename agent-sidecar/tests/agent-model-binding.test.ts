import assert from 'node:assert/strict'
import {
  chmodSync,
  mkdtempSync,
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

import type {
  DomeyeFirstSliceModelBindingPayload,
} from '../src/agent/candidate-manifest.js'
import {
  createDomeyePiModelBinding,
  DomeyeModelBindingError,
} from '../src/agent/model-binding.js'

const PROVIDER = 'provider-first-slice'
const MODEL_ID = 'model-first-slice'
const BASE_URL = 'https://provider.invalid/v1'
const directory = mkdtempSync(join(tmpdir(), 'domeye-model-binding-test-'))
const authPath = join(directory, 'auth.json')
writeFileSync(authPath, JSON.stringify({
  [PROVIDER]: { type: 'api_key', key: 'fixture-only-key' },
}), { mode: 0o600 })
chmodSync(authPath, 0o600)
after(() => rmSync(directory, { recursive: true, force: true }))

const IDENTITY: DomeyeFirstSliceModelBindingPayload = {
  candidate_id: 'model-candidate-first-slice',
  resource_sha256: `sha256:${'a'.repeat(64)}`,
  provider: PROVIDER,
  model: MODEL_ID,
  model_version: 'model-first-slice-20260819',
  expected_response_model: MODEL_ID,
  api: 'openai-completions',
  base_url: BASE_URL,
  maximum_output_tokens: 4_096,
  thinking_level: 'off',
  pi_version: '0.84.1',
}

function catalogModel(
  overrides: Partial<NonNullable<CreateAgentSessionOptions['model']>> = {},
): NonNullable<CreateAgentSessionOptions['model']> {
  return {
    provider: PROVIDER,
    id: MODEL_ID,
    name: '首片模型测试项',
    api: 'openai-completions',
    baseUrl: BASE_URL,
    reasoning: false,
    input: ['text'],
    cost: {
      input: Number.MAX_SAFE_INTEGER,
      output: Number.MAX_SAFE_INTEGER,
      cacheRead: Number.MAX_SAFE_INTEGER,
      cacheWrite: Number.MAX_SAFE_INTEGER,
    },
    contextWindow: 128_000,
    maxTokens: IDENTITY.maximum_output_tokens,
    ...overrides,
  } as NonNullable<CreateAgentSessionOptions['model']>
}

interface RuntimeDoubleOptions {
  readonly model?: NonNullable<CreateAgentSessionOptions['model']> | null
  readonly auth?: { readonly configured: boolean, readonly source?: string }
  readonly available?: readonly NonNullable<CreateAgentSessionOptions['model']>[]
}

function runtimeDouble(options: RuntimeDoubleOptions = {}): ModelRuntime {
  const model = options.model === undefined ? catalogModel() : options.model
  return {
    getError() { return undefined },
    getModel(provider: string, modelId: string) {
      assert.equal(provider, PROVIDER)
      assert.equal(modelId, MODEL_ID)
      return model ?? undefined
    },
    getProviderAuthStatus(provider: string) {
      assert.equal(provider, PROVIDER)
      return options.auth ?? { configured: true, source: 'stored' }
    },
    async getAvailable(provider?: string) {
      assert.equal(provider, PROVIDER)
      return options.available ?? (model ? [model] : [])
    },
  } as unknown as ModelRuntime
}

function hasCode(code: DomeyeModelBindingError['code']) {
  return (error: unknown): boolean =>
    error instanceof DomeyeModelBindingError && error.code === code
}

test('绑定清单模型身份、冻结凭据源且高费用元数据不触发拒绝', async () => {
  let captured: CreateModelRuntimeOptions | undefined
  const model = catalogModel()
  const runtime = runtimeDouble({ model })
  const binding = await createDomeyePiModelBinding({
    identity: IDENTITY,
    auth_path: authPath,
    runtime_factory: async (options) => {
      captured = options
      return runtime
    },
  })

  assert.deepEqual(binding.identity, IDENTITY)
  assert.equal(binding.model, model)
  assert.equal(binding.model_runtime, runtime)
  assert.equal(binding.thinking_level, 'off')
  assert.equal(binding.model.cost.input, Number.MAX_SAFE_INTEGER)
  assert.equal(binding.model.cost.output, Number.MAX_SAFE_INTEGER)
  assert.equal(Object.isFrozen(binding), true)
  assert.equal(Object.isFrozen(binding.identity), true)
  assert.equal(captured?.modelsPath, null)
  assert.equal(captured?.allowModelNetwork, false)

  const credentials = captured?.credentials
  assert.ok(credentials)
  assert.deepEqual(await credentials.read(PROVIDER), {
    type: 'api_key',
    key: 'fixture-only-key',
  })
  assert.equal(await credentials.read('another-provider'), undefined)
  assert.deepEqual(await credentials.list(), [
    { providerId: PROVIDER, type: 'api_key' },
  ])
  assert.equal(Object.isFrozen(credentials), true)
  await assert.rejects(
    () => (credentials as unknown as { modify(): Promise<void> }).modify(),
    (error: unknown) => error instanceof Error
      && (error as Error & { code?: unknown }).code
        === 'credential_mutation_forbidden',
  )
  await assert.rejects(
    () => (credentials as unknown as { delete(): Promise<void> }).delete(),
    (error: unknown) => error instanceof Error
      && (error as Error & { code?: unknown }).code
        === 'credential_mutation_forbidden',
  )
})

const driftCases: readonly {
  readonly name: string
  readonly runtime: RuntimeDoubleOptions
  readonly expected: DomeyeModelBindingError['code']
}[] = [
  {
    name: 'catalog model ID 漂移',
    runtime: { model: catalogModel({ id: 'another-model' }) },
    expected: 'model_catalog_mismatch',
  },
  {
    name: 'provider 漂移',
    runtime: { model: catalogModel({ provider: 'another-provider' }) },
    expected: 'model_catalog_mismatch',
  },
  {
    name: 'base URL 漂移',
    runtime: { model: catalogModel({ baseUrl: 'https://other.invalid/v1' }) },
    expected: 'model_catalog_mismatch',
  },
  {
    name: '最大输出 token 漂移',
    runtime: { model: catalogModel({ maxTokens: 8_192 }) },
    expected: 'model_catalog_mismatch',
  },
  {
    name: '固定凭据认证不可用',
    runtime: { auth: { configured: false } },
    expected: 'provider_auth_unavailable',
  },
  {
    name: '模型不在可用快照',
    runtime: { available: [] },
    expected: 'model_not_available',
  },
]

for (const scenario of driftCases) {
  test(`${scenario.name}时模型绑定失败关闭`, async () => {
    await assert.rejects(
      () => createDomeyePiModelBinding({
        identity: IDENTITY,
        auth_path: authPath,
        runtime_factory: async () => runtimeDouble(scenario.runtime),
      }),
      hasCode(scenario.expected),
    )
  })
}
