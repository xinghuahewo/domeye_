import {
  closeSync,
  constants,
  existsSync,
  fstatSync,
  lstatSync,
  openSync,
  readFileSync,
} from 'node:fs'
import { readFile } from 'node:fs/promises'
import { isAbsolute, resolve } from 'node:path'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  ModelRuntime,
  VERSION as INSTALLED_PI_VERSION,
  type CreateAgentSessionOptions,
  type CreateModelRuntimeOptions,
} from '@earendil-works/pi-coding-agent'

import { FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS } from '../formal-runtime-limits.js'

export const FORMAL_PI_VERSION = '0.82.1' as const
export const FORMAL_PI_REGISTRY_SCHEMA =
  'country_outage_pi_certified_models_v1' as const
export const MUTABLE_MODEL_ALIAS_LIMITATION_ZH =
  '供应方未提供不可变权重 revision；deepseek-v4-flash 是可变别名，可能无痕变化。' as const

const SAFE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/
const SAFE_PROFILE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const SAFE_EVIDENCE_ID = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/
const RESERVED_OBJECT_KEYS = new Set(['__proto__', 'constructor', 'prototype'])
const MAX_AUTH_FILE_BYTES = 65_536
const MAX_API_KEY_BYTES = 8_192
const THINKING_LEVELS = [
  'off',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
  'max',
] as const

type PiThinkingLevel = NonNullable<
  CreateAgentSessionOptions['thinkingLevel']
>

export interface CertifiedPiModelProfile {
  id: string
  status: 'certified'
  provider: string
  model: string
  modelVersion: string
  expectedResponseModel: string
  thinkingLevel: PiThinkingLevel
  piVersion: typeof FORMAL_PI_VERSION
  certificationEvidenceId: string
  certifiedAt: string
  modelRevisionKind: 'mutable_alias'
  immutableRevisionAvailable: false
  limitation: typeof MUTABLE_MODEL_ALIAS_LIMITATION_ZH
  certificationValidUntil: string
  certifiedScenarioSetId: string
  certifiedInputScope: string
}

export interface CertifiedPiModelRegistry {
  schemaVersion: typeof FORMAL_PI_REGISTRY_SCHEMA
  registryVersion: string
  status: 'frozen'
  profiles: readonly CertifiedPiModelProfile[]
}

export interface CertifiedPiModelSelection {
  registryVersion: string
  profile: CertifiedPiModelProfile
}

export interface CandidatePiModelRunProfile {
  id: string
  status: 'candidate'
  provider: string
  model: string
  modelVersion: string
  expectedResponseModel: string
  thinkingLevel: PiThinkingLevel
  piVersion: typeof FORMAL_PI_VERSION
}

export type PiModelRunSelection =
  | {
      runtimeIdentity: 'formal'
      registryVersion: string
      profile: CertifiedPiModelProfile
    }
  | {
      runtimeIdentity: 'candidate'
      candidateId: string
      candidateResourceSha256: string
      profile: CandidatePiModelRunProfile
    }

export function formalPiModelRunSelection(
  selection: CertifiedPiModelSelection,
): PiModelRunSelection {
  return Object.freeze({
    runtimeIdentity: 'formal',
    registryVersion: selection.registryVersion,
    profile: selection.profile,
  })
}

export interface FormalPiModelPreflight {
  schemaVersion: 'country_outage_pi_model_preflight_v1'
  registryVersion: string
  profileId: string
  provider: string
  model: string
  modelVersion: string
  expectedResponseModel: string
  thinkingLevel: PiThinkingLevel
  piVersion: typeof FORMAL_PI_VERSION
  certificationEvidenceId: string
  modelRevisionKind: 'mutable_alias'
  immutableRevisionAvailable: false
  limitation: typeof MUTABLE_MODEL_ALIAS_LIMITATION_ZH
  certificationValidUntil: string
  certifiedScenarioSetId: string
  certifiedInputScope: string
  maximumOutputTokens: number
  auth: {
    configured: true
    source:
      | 'stored'
      | 'runtime'
      | 'environment'
      | 'fallback'
      | 'models_json_key'
      | 'models_json_command'
      | 'unknown'
  }
  available: true
}

export interface FormalPiModelBinding {
  modelRuntime: ModelRuntime
  model: NonNullable<CreateAgentSessionOptions['model']>
  certification: CertifiedPiModelSelection
  runSelection: PiModelRunSelection
  preflight: FormalPiModelPreflight
}

export type FormalPiModelRuntimeFactory = (
  options: CreateModelRuntimeOptions,
) => Promise<ModelRuntime>

export type FormalPiRuntimeErrorCode =
  | 'registry_invalid'
  | 'profile_not_selected'
  | 'profile_not_certified'
  | 'certification_expired'
  | 'pi_version_mismatch'
  | 'auth_path_invalid'
  | 'credential_store_invalid'
  | 'credential_command_forbidden'
  | 'credential_mutation_forbidden'
  | 'runtime_initialization_failed'
  | 'runtime_metadata_invalid'
  | 'model_not_found'
  | 'provider_auth_unavailable'
  | 'model_not_available'

const SAFE_ERROR_MESSAGES: Record<FormalPiRuntimeErrorCode, string> = {
  registry_invalid: '正式 Pi 模型认证注册表无效',
  profile_not_selected: '未选择已认证的正式 Pi 模型组合',
  profile_not_certified: '指定的 Pi 模型组合尚未通过认证',
  certification_expired: '正式 Pi 模型认证已到期，必须重新认证后才能运行',
  pi_version_mismatch: '实际安装的 Pi 版本与正式认证版本不一致',
  auth_path_invalid: '正式 Pi 认证存储路径无效',
  credential_store_invalid: '正式 Pi 认证存储内容或权限无效',
  credential_command_forbidden: '正式 Pi 认证存储禁止命令型密钥',
  credential_mutation_forbidden: '正式 Pi 运行时禁止修改认证存储',
  runtime_initialization_failed: '正式 Pi 模型运行时初始化失败',
  runtime_metadata_invalid: '正式 Pi 模型运行时元数据不可用',
  model_not_found: '已认证的正式 Pi 模型不在固定模型目录中',
  provider_auth_unavailable: '正式 Pi 模型供应方没有可用认证',
  model_not_available: '已认证的正式 Pi 模型当前不可用',
}

export class FormalPiRuntimeError extends Error {
  constructor(readonly code: FormalPiRuntimeErrorCode) {
    super(SAFE_ERROR_MESSAGES[code])
    this.name = 'FormalPiRuntimeError'
  }
}

export function assertFormalPiInstalledVersion(
  installedVersion: string = INSTALLED_PI_VERSION,
): void {
  if (installedVersion !== FORMAL_PI_VERSION) {
    throw new FormalPiRuntimeError('pi_version_mismatch')
  }
}

type FormalCredentialStore = NonNullable<
  CreateModelRuntimeOptions['credentials']
>
type FormalCredential = Awaited<ReturnType<FormalCredentialStore['read']>>

function readDedicatedAuthFile(authPath: string): unknown {
  const normalized = resolve(authPath)
  if (!isAbsolute(authPath)) {
    throw new FormalPiRuntimeError('auth_path_invalid')
  }
  try {
    if (lstatSync(normalized).isSymbolicLink()) {
      throw new FormalPiRuntimeError('auth_path_invalid')
    }
  } catch (error) {
    if (error instanceof FormalPiRuntimeError) throw error
    throw new FormalPiRuntimeError('auth_path_invalid')
  }

  let descriptor: number | undefined
  try {
    descriptor = openSync(
      normalized,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    )
    const before = fstatSync(descriptor)
    const getUid = process.getuid
    if (
      !before.isFile() ||
      before.size <= 0 ||
      before.size > MAX_AUTH_FILE_BYTES ||
      (before.mode & 0o077) !== 0 ||
      (before.mode & 0o400) === 0 ||
      (typeof getUid === 'function' && before.uid !== getUid())
    ) {
      throw new FormalPiRuntimeError('credential_store_invalid')
    }
    const text = readFileSync(descriptor, 'utf8')
    const after = fstatSync(descriptor)
    if (
      before.dev !== after.dev ||
      before.ino !== after.ino ||
      before.size !== after.size ||
      before.mtimeMs !== after.mtimeMs
    ) {
      throw new FormalPiRuntimeError('credential_store_invalid')
    }
    return JSON.parse(text) as unknown
  } catch (error) {
    if (error instanceof FormalPiRuntimeError) throw error
    throw new FormalPiRuntimeError('credential_store_invalid')
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
  }
}

function parseSelectedCredential(
  authDocument: unknown,
  provider: string,
): FormalCredential {
  if (!isRecord(authDocument)) {
    throw new FormalPiRuntimeError('credential_store_invalid')
  }
  const raw = Object.prototype.hasOwnProperty.call(authDocument, provider)
    ? authDocument[provider]
    : undefined
  if (raw === undefined) return undefined
  if (!isRecord(raw) || raw.type !== 'api_key') {
    throw new FormalPiRuntimeError('credential_store_invalid')
  }
  if (
    Object.keys(raw).some((key) => !['type', 'key'].includes(key)) ||
    typeof raw.key !== 'string' ||
    raw.key.length === 0 ||
    Buffer.byteLength(raw.key, 'utf8') > MAX_API_KEY_BYTES ||
    raw.key !== raw.key.trim() ||
    /[\u0000-\u001f\u007f]/.test(raw.key)
  ) {
    throw new FormalPiRuntimeError('credential_store_invalid')
  }
  if (raw.key.startsWith('!')) {
    throw new FormalPiRuntimeError('credential_command_forbidden')
  }
  if (raw.key.startsWith('$')) {
    throw new FormalPiRuntimeError('credential_store_invalid')
  }
  return Object.freeze({ type: 'api_key', key: raw.key })
}

export function createFrozenFormalCredentialStore(
  authPath: string,
  provider: string,
): FormalCredentialStore {
  const credential = parseSelectedCredential(
    readDedicatedAuthFile(authPath),
    provider,
  )
  const store: FormalCredentialStore = {
    async read(requestedProvider) {
      return requestedProvider === provider ? credential : undefined
    },
    async list() {
      return credential
        ? [{ providerId: provider, type: credential.type }]
        : []
    },
    async modify() {
      throw new FormalPiRuntimeError('credential_mutation_forbidden')
    },
    async delete() {
      throw new FormalPiRuntimeError('credential_mutation_forbidden')
    },
  }
  return Object.freeze(store)
}

export function capCountryOutageModelOutput(
  model: NonNullable<CreateAgentSessionOptions['model']>,
): NonNullable<CreateAgentSessionOptions['model']> {
  const configuredMaxTokens = (
    model as NonNullable<CreateAgentSessionOptions['model']> & {
      maxTokens?: unknown
    }
  ).maxTokens
  // 兼容只实现最小 Model 接口的宿主测试替身；真实 ModelRuntime 目录
  // 仍在 createFormalPiModelBinding 中强制要求有效 maxTokens。
  if (configuredMaxTokens === undefined) {
    return Object.freeze({
      ...model,
      maxTokens:
        FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelOutputTokens,
    })
  }
  if (
    !Number.isInteger(configuredMaxTokens) ||
    (configuredMaxTokens as number) <= 0
  ) {
    throw new FormalPiRuntimeError('runtime_metadata_invalid')
  }
  if (
    (configuredMaxTokens as number) <=
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelOutputTokens
  ) {
    return Object.freeze(model)
  }
  return Object.freeze({
    ...model,
    maxTokens: Math.min(
      configuredMaxTokens as number,
      FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelOutputTokens,
    ),
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function exactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return JSON.stringify(actual) === JSON.stringify(expected)
}

function requiredSafeString(
  value: unknown,
  pattern: RegExp,
): string | undefined {
  if (typeof value !== 'string') return undefined
  const normalized = value.trim()
  return normalized && pattern.test(normalized) ? normalized : undefined
}

function isIsoTimestamp(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(value) &&
    Number.isFinite(Date.parse(value))
  )
}

function parseProfile(value: unknown): CertifiedPiModelProfile {
  if (!isRecord(value)) throw new FormalPiRuntimeError('registry_invalid')
  const id = requiredSafeString(value.id, SAFE_PROFILE_ID)
  const provider = requiredSafeString(value.provider, SAFE_IDENTIFIER)
  const model = requiredSafeString(value.model, SAFE_IDENTIFIER)
  const modelVersion = requiredSafeString(
    value.modelVersion,
    SAFE_IDENTIFIER,
  )
  const expectedResponseModel = requiredSafeString(
    value.expectedResponseModel,
    SAFE_IDENTIFIER,
  )
  const certificationEvidenceId = requiredSafeString(
    value.certificationEvidenceId,
    SAFE_EVIDENCE_ID,
  )
  const certifiedScenarioSetId = requiredSafeString(
    value.certifiedScenarioSetId,
    SAFE_IDENTIFIER,
  )
  const certifiedInputScope = requiredSafeString(
    value.certifiedInputScope,
    SAFE_IDENTIFIER,
  )
  if (
    !exactKeys(value, [
      'id',
      'status',
      'provider',
      'model',
      'modelVersion',
      'expectedResponseModel',
      'thinkingLevel',
      'piVersion',
      'certificationEvidenceId',
      'certifiedAt',
      'modelRevisionKind',
      'immutableRevisionAvailable',
      'limitation',
      'certificationValidUntil',
      'certifiedScenarioSetId',
      'certifiedInputScope',
    ]) ||
    !id ||
    value.status !== 'certified' ||
    !provider ||
    RESERVED_OBJECT_KEYS.has(provider) ||
    !model ||
    !modelVersion ||
    !expectedResponseModel ||
    !THINKING_LEVELS.includes(value.thinkingLevel as PiThinkingLevel) ||
    value.piVersion !== FORMAL_PI_VERSION ||
    !certificationEvidenceId ||
    !isIsoTimestamp(value.certifiedAt) ||
    value.modelRevisionKind !== 'mutable_alias' ||
    value.immutableRevisionAvailable !== false ||
    value.limitation !== MUTABLE_MODEL_ALIAS_LIMITATION_ZH ||
    !isIsoTimestamp(value.certificationValidUntil) ||
    Date.parse(value.certifiedAt) >=
      Date.parse(value.certificationValidUntil) ||
    !certifiedScenarioSetId ||
    !certifiedInputScope
  ) {
    throw new FormalPiRuntimeError('registry_invalid')
  }
  return Object.freeze({
    id,
    status: 'certified',
    provider,
    model,
    modelVersion,
    expectedResponseModel,
    thinkingLevel: value.thinkingLevel as PiThinkingLevel,
    piVersion: FORMAL_PI_VERSION,
    certificationEvidenceId,
    certifiedAt: value.certifiedAt,
    modelRevisionKind: 'mutable_alias',
    immutableRevisionAvailable: false,
    limitation: MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
    certificationValidUntil: value.certificationValidUntil,
    certifiedScenarioSetId,
    certifiedInputScope,
  })
}

export function parseCertifiedPiModelRegistry(
  value: unknown,
): CertifiedPiModelRegistry {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'schemaVersion',
      'registryVersion',
      'status',
      'profiles',
    ]) ||
    value.schemaVersion !== FORMAL_PI_REGISTRY_SCHEMA ||
    value.status !== 'frozen' ||
    !Array.isArray(value.profiles)
  ) {
    throw new FormalPiRuntimeError('registry_invalid')
  }
  const registryVersion = requiredSafeString(
    value.registryVersion,
    SAFE_IDENTIFIER,
  )
  if (!registryVersion) {
    throw new FormalPiRuntimeError('registry_invalid')
  }
  const profiles = value.profiles.map(parseProfile)
  if (new Set(profiles.map((profile) => profile.id)).size !== profiles.length) {
    throw new FormalPiRuntimeError('registry_invalid')
  }
  return Object.freeze({
    schemaVersion: FORMAL_PI_REGISTRY_SCHEMA,
    registryVersion,
    status: 'frozen',
    profiles: Object.freeze([...profiles]),
  })
}

export function assertFormalPiModelCertificationCurrent(
  profile: CertifiedPiModelProfile,
  checkedAt: Date = new Date(),
): void {
  const parsed = parseProfile(profile)
  const checkedAtMs = checkedAt.valueOf()
  if (
    !Number.isFinite(checkedAtMs) ||
    checkedAtMs >= Date.parse(parsed.certificationValidUntil)
  ) {
    throw new FormalPiRuntimeError('certification_expired')
  }
}

function defaultRegistryPath(): string {
  const moduleDirectory = dirname(fileURLToPath(import.meta.url))
  const candidates = [
    resolve(
      moduleDirectory,
      '../../resources/certified-models/country-outage-pi-models-v1.json',
    ),
    resolve(
      moduleDirectory,
      '../../../resources/certified-models/country-outage-pi-models-v1.json',
    ),
  ]
  return (
    candidates.find((candidate) => existsSync(candidate)) ?? candidates[0]!
  )
}

export async function loadCertifiedPiModelRegistry(
  path: string = defaultRegistryPath(),
): Promise<CertifiedPiModelRegistry> {
  try {
    const text = await readFile(resolve(path), 'utf8')
    return parseCertifiedPiModelRegistry(JSON.parse(text) as unknown)
  } catch (error) {
    if (error instanceof FormalPiRuntimeError) throw error
    throw new FormalPiRuntimeError('registry_invalid')
  }
}

function selectedProfile(
  registry: CertifiedPiModelRegistry,
  profileId: string,
  checkedAt: Date,
): CertifiedPiModelSelection {
  const normalized = requiredSafeString(profileId, SAFE_PROFILE_ID)
  if (!normalized) {
    throw new FormalPiRuntimeError('profile_not_selected')
  }
  const profile = registry.profiles.find(
    (candidate) => candidate.id === normalized,
  )
  if (!profile || profile.status !== 'certified') {
    throw new FormalPiRuntimeError('profile_not_certified')
  }
  assertFormalPiModelCertificationCurrent(profile, checkedAt)
  return Object.freeze({
    registryVersion: registry.registryVersion,
    profile,
  })
}

function safeAuthSource(
  source: string | undefined,
): FormalPiModelPreflight['auth']['source'] {
  switch (source) {
    case 'stored':
    case 'runtime':
    case 'environment':
    case 'fallback':
    case 'models_json_key':
    case 'models_json_command':
      return source
    default:
      return 'unknown'
  }
}

const defaultRuntimeFactory: FormalPiModelRuntimeFactory = async (options) =>
  await ModelRuntime.create(options)

export interface CreateFormalPiModelBindingOptions {
  registry: CertifiedPiModelRegistry
  profileId: string
  authPath: string
  runtimeFactory?: FormalPiModelRuntimeFactory
  now?: () => Date
}

export async function createFormalPiModelBinding(
  options: CreateFormalPiModelBindingOptions,
): Promise<FormalPiModelBinding> {
  assertFormalPiInstalledVersion()
  const registry = parseCertifiedPiModelRegistry(options.registry)
  const certification = selectedProfile(
    registry,
    options.profileId,
    (options.now ?? (() => new Date()))(),
  )
  const authPath = options.authPath.trim()
  if (!authPath || !isAbsolute(authPath)) {
    throw new FormalPiRuntimeError('auth_path_invalid')
  }
  const credentials = createFrozenFormalCredentialStore(
    authPath,
    certification.profile.provider,
  )

  let modelRuntime: ModelRuntime
  try {
    modelRuntime = await (options.runtimeFactory ?? defaultRuntimeFactory)({
      credentials,
      modelsPath: null,
      allowModelNetwork: false,
    })
  } catch {
    throw new FormalPiRuntimeError('runtime_initialization_failed')
  }

  if (modelRuntime.getError()) {
    throw new FormalPiRuntimeError('runtime_metadata_invalid')
  }

  const { profile } = certification
  const catalogModel = modelRuntime.getModel(
    profile.provider,
    profile.model,
  )
  if (!catalogModel) throw new FormalPiRuntimeError('model_not_found')
  if (
    !Number.isInteger(catalogModel.maxTokens) ||
    catalogModel.maxTokens <= 0
  ) {
    throw new FormalPiRuntimeError('runtime_metadata_invalid')
  }

  const authStatus = modelRuntime.getProviderAuthStatus(profile.provider)
  if (!authStatus.configured) {
    throw new FormalPiRuntimeError('provider_auth_unavailable')
  }
  const authSource = safeAuthSource(authStatus.source)
  if (
    authSource === 'unknown' ||
    authSource === 'models_json_command' ||
    authSource === 'models_json_key'
  ) {
    throw new FormalPiRuntimeError('provider_auth_unavailable')
  }

  let available: readonly NonNullable<CreateAgentSessionOptions['model']>[]
  try {
    available = await modelRuntime.getAvailable(profile.provider)
  } catch {
    throw new FormalPiRuntimeError('runtime_metadata_invalid')
  }
  if (
    !available.some(
      (candidate) =>
        candidate.provider === profile.provider &&
        candidate.id === profile.model,
    )
  ) {
    throw new FormalPiRuntimeError('model_not_available')
  }
  if (modelRuntime.getError()) {
    throw new FormalPiRuntimeError('runtime_metadata_invalid')
  }

  const model = capCountryOutageModelOutput(catalogModel)

  return {
    modelRuntime,
    model,
    certification,
    runSelection: formalPiModelRunSelection(certification),
    preflight: {
      schemaVersion: 'country_outage_pi_model_preflight_v1',
      registryVersion: certification.registryVersion,
      profileId: profile.id,
      provider: profile.provider,
      model: profile.model,
      modelVersion: profile.modelVersion,
      expectedResponseModel: profile.expectedResponseModel,
      thinkingLevel: profile.thinkingLevel,
      piVersion: FORMAL_PI_VERSION,
      certificationEvidenceId: profile.certificationEvidenceId,
      modelRevisionKind: profile.modelRevisionKind,
      immutableRevisionAvailable:
        profile.immutableRevisionAvailable,
      limitation: profile.limitation,
      certificationValidUntil: profile.certificationValidUntil,
      certifiedScenarioSetId: profile.certifiedScenarioSetId,
      certifiedInputScope: profile.certifiedInputScope,
      maximumOutputTokens: model.maxTokens,
      auth: {
        configured: true,
        source: authSource,
      },
      available: true,
    },
  }
}

export interface FormalPiProductionEnvironment
  extends Record<string, string | undefined> {
  COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH?: string
  COUNTRY_OUTAGE_PI_PROFILE?: string
  COUNTRY_OUTAGE_PI_AUTH_PATH?: string
}

export interface CreateFormalPiModelBindingFromEnvironmentOptions {
  env?: FormalPiProductionEnvironment
  runtimeFactory?: FormalPiModelRuntimeFactory
  now?: () => Date
}

export async function createFormalPiModelBindingFromEnvironment(
  options: CreateFormalPiModelBindingFromEnvironmentOptions = {},
): Promise<FormalPiModelBinding> {
  const env = options.env ?? process.env
  const profileId = env.COUNTRY_OUTAGE_PI_PROFILE?.trim()
  if (!profileId) {
    throw new FormalPiRuntimeError('profile_not_selected')
  }
  const authPath = env.COUNTRY_OUTAGE_PI_AUTH_PATH?.trim()
  if (!authPath) throw new FormalPiRuntimeError('auth_path_invalid')
  const registry = await loadCertifiedPiModelRegistry(
    env.COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH?.trim() ||
      defaultRegistryPath(),
  )
  return await createFormalPiModelBinding({
    registry,
    profileId,
    authPath,
    ...(options.runtimeFactory
      ? { runtimeFactory: options.runtimeFactory }
      : {}),
    ...(options.now ? { now: options.now } : {}),
  })
}
