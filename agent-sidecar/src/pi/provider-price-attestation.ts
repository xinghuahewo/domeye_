import { createHash, randomUUID } from 'node:crypto'
import {
  chmodSync,
  closeSync,
  constants,
  fchmodSync,
  fstatSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
  type Stats,
} from 'node:fs'
import { isAbsolute, resolve, sep } from 'node:path'

import { compareUnicodeCodePoints } from '../shared/deterministic-json.js'

export const COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_SCHEMA =
  'country_outage_provider_price_attestation_v1' as const
export const COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_RELATIVE_PATH =
  'var/country-outage-agent/a4-provider-price-attestation/deepseek-v4-flash-pi-0.82.1-v1-price-attestation-v1.json' as const
export const COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS =
  24 * 60 * 60
export const COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_MINIMUM_RUNWAY_SECONDS =
  15 * 60

const SHA256 = /^[a-f0-9]{64}$/
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/
const MAX_ATTESTATION_BYTES = 8 * 1024
const PRICE_KEYS = [
  'input',
  'output',
  'cacheRead',
  'cacheWrite',
] as const

export interface ProviderPriceCandidateBinding {
  candidate: {
    candidateId: string
    provider: string
    model: string
    catalog: {
      priceUsdPerMillionTokens: {
        input: number
        output: number
        cacheRead: number
        cacheWrite: number
      }
    }
  }
  resourceSha256: string
}

export interface ProviderTokenPrices {
  input: string
  output: string
  cacheRead: string
  cacheWrite: string
}

interface ProviderPriceAttestationResource {
  schemaVersion:
    typeof COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_SCHEMA
  attestationId: string
  candidateId: string
  candidateResourceSha256: string
  provider: string
  model: string
  currency: 'USD'
  billingUnit: 'per_1_million_tokens'
  priceUsdPerMillionTokens: ProviderTokenPrices
  observedAt: string
  expiresAt: string
  validitySeconds:
    typeof COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS
  evidence: {
    type: 'provider_pricing_snapshot'
    sha256: string
  }
}

export interface VerifiedProviderPriceAttestation {
  schemaVersion:
    typeof COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_SCHEMA
  attestationId: string
  resourceSha256: string
  candidateId: string
  candidateResourceSha256: string
  provider: string
  model: string
  currency: 'USD'
  billingUnit: 'per_1_million_tokens'
  priceUsdPerMillionTokens: ProviderTokenPrices
  observedAt: string
  expiresAt: string
  validitySeconds:
    typeof COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS
  evidenceSha256: string
}

export type ProviderPriceAttestationErrorCode =
  | 'price_attestation_missing'
  | 'price_attestation_invalid'
  | 'price_attestation_expired'
  | 'price_attestation_future_observation'
  | 'price_attestation_insufficient_runway'
  | 'price_attestation_candidate_drift'
  | 'price_attestation_rebudget_required'
  | 'price_attestation_busy'

const SAFE_ERROR_MESSAGES: Record<
  ProviderPriceAttestationErrorCode,
  string
> = {
  price_attestation_missing: 'DeepSeek 当前价格证明缺失',
  price_attestation_invalid: 'DeepSeek 当前价格证明无效',
  price_attestation_expired: 'DeepSeek 当前价格证明已过期',
  price_attestation_future_observation:
    'DeepSeek 当前价格证明的观测时间在未来',
  price_attestation_insufficient_runway:
    'DeepSeek 当前价格证明剩余有效期不足以启动完整认证',
  price_attestation_candidate_drift:
    'DeepSeek 当前价格证明与冻结候选资源不一致',
  price_attestation_rebudget_required:
    'DeepSeek 当前价格高于冻结候选值，必须重新预算',
  price_attestation_busy:
    'DeepSeek 当前价格证明正由另一个运维进程更新',
}

export class ProviderPriceAttestationError extends Error {
  constructor(readonly code: ProviderPriceAttestationErrorCode) {
    super(SAFE_ERROR_MESSAGES[code])
    this.name = 'ProviderPriceAttestationError'
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function exactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value).sort(compareUnicodeCodePoints)
  const sortedExpected = [...expected].sort(compareUnicodeCodePoints)
  return (
    actual.length === sortedExpected.length &&
    actual.every((key, index) => key === sortedExpected[index])
  )
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize)
  if (!isRecord(value)) return value
  return Object.fromEntries(
    Object.entries(value)
      .sort(([left], [right]) =>
        compareUnicodeCodePoints(left, right),
      )
      .map(([key, item]) => [key, canonicalize(item)]),
  )
}

function sha256(value: string | Buffer): string {
  return createHash('sha256').update(value).digest('hex')
}

function canonicalSha256(value: unknown): string {
  return sha256(JSON.stringify(canonicalize(value)))
}

function parsePrices(value: unknown): ProviderTokenPrices | null {
  if (
    !isRecord(value) ||
    !exactKeys(value, PRICE_KEYS) ||
    typeof value.input !== 'string' ||
    typeof value.output !== 'string' ||
    typeof value.cacheRead !== 'string' ||
    typeof value.cacheWrite !== 'string'
  ) {
    return null
  }
  try {
    const prices = {
      input: canonicalProviderPriceDecimal(value.input),
      output: canonicalProviderPriceDecimal(value.output),
      cacheRead: canonicalProviderPriceDecimal(value.cacheRead),
      cacheWrite: canonicalProviderPriceDecimal(value.cacheWrite),
    }
    return PRICE_KEYS.every((key) => prices[key] === value[key])
      ? prices
      : null
  } catch {
    return null
  }
}

const PROVIDER_PRICE_DECIMAL =
  /^(0|[1-9]\d*)(?:\.(\d+))?$/
const MAXIMUM_PROVIDER_PRICE_DECIMAL_LENGTH = 128
const MAXIMUM_PROVIDER_PRICE_FRACTION_DIGITS = 64

/**
 * 将运维输入规范化为非负十进制字符串。整个供应商价格证明链路保留该字符串，
 * 不经 Number 转换，避免小幅上调被 IEEE-754 舍入回冻结值。
 */
export function canonicalProviderPriceDecimal(
  value: string,
): string {
  if (
    value.length === 0 ||
    value.length > MAXIMUM_PROVIDER_PRICE_DECIMAL_LENGTH
  ) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  const match = PROVIDER_PRICE_DECIMAL.exec(value)
  const integer = match?.[1]
  const fraction = match?.[2] ?? ''
  if (
    integer === undefined ||
    fraction.length > MAXIMUM_PROVIDER_PRICE_FRACTION_DIGITS
  ) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  const normalizedFraction = fraction.replace(/0+$/u, '')
  return normalizedFraction
    ? `${integer}.${normalizedFraction}`
    : integer
}

function decimalParts(value: string): {
  coefficient: bigint
  scale: number
} {
  const canonical = canonicalProviderPriceDecimal(value)
  const [integer, fraction = ''] = canonical.split('.')
  return {
    coefficient: BigInt(`${integer}${fraction}`),
    scale: fraction.length,
  }
}

function compareCanonicalDecimals(
  left: string,
  right: string,
): number {
  const leftParts = decimalParts(left)
  const rightParts = decimalParts(right)
  const scale = Math.max(leftParts.scale, rightParts.scale)
  const leftScaled =
    leftParts.coefficient *
    10n ** BigInt(scale - leftParts.scale)
  const rightScaled =
    rightParts.coefficient *
    10n ** BigInt(scale - rightParts.scale)
  return leftScaled < rightScaled
    ? -1
    : leftScaled > rightScaled
      ? 1
      : 0
}

function candidatePriceDecimal(value: number): string {
  if (!Number.isFinite(value) || value < 0) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  const text = String(value)
  if (/[eE]/u.test(text)) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  return canonicalProviderPriceDecimal(text)
}

function canonicalTimestamp(value: unknown): value is string {
  if (typeof value !== 'string') return false
  const parsed = new Date(value)
  return (
    Number.isFinite(parsed.valueOf()) &&
    parsed.toISOString() === value
  )
}

function attestationBody(
  resource: Omit<ProviderPriceAttestationResource, 'attestationId'>,
): Omit<ProviderPriceAttestationResource, 'attestationId'> {
  return resource
}

function expectedAttestationId(
  resource: Omit<ProviderPriceAttestationResource, 'attestationId'>,
): string {
  return `price-attestation:${canonicalSha256(
    attestationBody(resource),
  )}`
}

function currentUserId(): number {
  if (typeof process.getuid !== 'function') {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  return process.getuid()
}

function permissionBits(stat: Stats): number {
  return stat.mode & 0o777
}

function assertOwned(stat: Stats): void {
  if (stat.uid !== currentUserId()) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
}

function fsyncDirectory(path: string): void {
  const descriptor = openSync(
    path,
    constants.O_RDONLY | (constants.O_DIRECTORY ?? 0),
  )
  try {
    fsyncSync(descriptor)
  } finally {
    closeSync(descriptor)
  }
}

function pathEntryExists(path: string): boolean {
  try {
    lstatSync(path)
    return true
  } catch (error) {
    if (
      error instanceof Error &&
      'code' in error &&
      error.code === 'ENOENT'
    ) {
      return false
    }
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
}

function secureAttestationDirectory(
  repositoryRoot: string,
  initialize: boolean,
): string {
  if (!isAbsolute(repositoryRoot)) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  let root: string
  try {
    const rootStats = lstatSync(repositoryRoot)
    if (
      !rootStats.isDirectory() ||
      rootStats.isSymbolicLink() ||
      (permissionBits(rootStats) & 0o022) !== 0
    ) {
      throw new Error('invalid root')
    }
    assertOwned(rootStats)
    root = realpathSync(repositoryRoot)
  } catch (error) {
    if (error instanceof ProviderPriceAttestationError) throw error
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }

  let current = root
  const segments = [
    'var',
    'country-outage-agent',
    'a4-provider-price-attestation',
  ]
  for (const [index, segment] of segments.entries()) {
    const next = resolve(current, segment)
    if (
      next !== `${current}${sep}${segment}` ||
      !next.startsWith(`${root}${sep}`)
    ) {
      throw new ProviderPriceAttestationError(
        'price_attestation_invalid',
      )
    }
    try {
      if (initialize && !pathEntryExists(next)) {
        mkdirSync(next, { mode: 0o700 })
        chmodSync(next, 0o700)
      }
      const stats = lstatSync(next)
      if (
        !stats.isDirectory() ||
        stats.isSymbolicLink() ||
        realpathSync(next) !== next
      ) {
        throw new Error('invalid directory')
      }
      assertOwned(stats)
      if (
        index === segments.length - 1
          ? permissionBits(stats) !== 0o700
          : (permissionBits(stats) & 0o022) !== 0
      ) {
        throw new Error('unsafe permissions')
      }
    } catch (error) {
      if (
        error instanceof ProviderPriceAttestationError &&
        error.code !== 'price_attestation_missing'
      ) {
        throw error
      }
      if (
        !initialize &&
        error instanceof Error &&
        'code' in error &&
        error.code === 'ENOENT'
      ) {
        throw new ProviderPriceAttestationError(
          'price_attestation_missing',
        )
      }
      throw new ProviderPriceAttestationError(
        'price_attestation_invalid',
      )
    }
    current = next
  }
  return current
}

function assertSafeAttestationFile(path: string, stat: Stats): void {
  if (
    !stat.isFile() ||
    stat.isSymbolicLink() ||
    stat.nlink !== 1 ||
    permissionBits(stat) !== 0o600 ||
    stat.size <= 0 ||
    stat.size > MAX_ATTESTATION_BYTES ||
    realpathSync(path) !== path
  ) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  assertOwned(stat)
}

function resourceWithoutId(
  value: ProviderPriceAttestationResource,
): Omit<ProviderPriceAttestationResource, 'attestationId'> {
  const { attestationId: _attestationId, ...withoutId } = value
  return withoutId
}

function serializedResource(
  resource: ProviderPriceAttestationResource,
): string {
  return `${JSON.stringify(resource, null, 2)}\n`
}

function parseResource(value: unknown): ProviderPriceAttestationResource {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'schemaVersion',
      'attestationId',
      'candidateId',
      'candidateResourceSha256',
      'provider',
      'model',
      'currency',
      'billingUnit',
      'priceUsdPerMillionTokens',
      'observedAt',
      'expiresAt',
      'validitySeconds',
      'evidence',
    ]) ||
    value.schemaVersion !==
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_SCHEMA ||
    typeof value.attestationId !== 'string' ||
    !/^price-attestation:[a-f0-9]{64}$/.test(
      value.attestationId,
    ) ||
    typeof value.candidateId !== 'string' ||
    !SAFE_ID.test(value.candidateId) ||
    typeof value.candidateResourceSha256 !== 'string' ||
    !SHA256.test(value.candidateResourceSha256) ||
    typeof value.provider !== 'string' ||
    !SAFE_ID.test(value.provider) ||
    typeof value.model !== 'string' ||
    !SAFE_ID.test(value.model) ||
    value.currency !== 'USD' ||
    value.billingUnit !== 'per_1_million_tokens' ||
    !canonicalTimestamp(value.observedAt) ||
    !canonicalTimestamp(value.expiresAt) ||
    value.validitySeconds !==
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS ||
    !isRecord(value.evidence) ||
    !exactKeys(value.evidence, ['type', 'sha256']) ||
    value.evidence.type !== 'provider_pricing_snapshot' ||
    typeof value.evidence.sha256 !== 'string' ||
    !SHA256.test(value.evidence.sha256)
  ) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  const prices = parsePrices(value.priceUsdPerMillionTokens)
  if (!prices) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  const resource: ProviderPriceAttestationResource = {
    schemaVersion:
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_SCHEMA,
    attestationId: value.attestationId,
    candidateId: value.candidateId,
    candidateResourceSha256: value.candidateResourceSha256,
    provider: value.provider,
    model: value.model,
    currency: 'USD',
    billingUnit: 'per_1_million_tokens',
    priceUsdPerMillionTokens: prices,
    observedAt: value.observedAt,
    expiresAt: value.expiresAt,
    validitySeconds:
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS,
    evidence: {
      type: 'provider_pricing_snapshot',
      sha256: value.evidence.sha256,
    },
  }
  const observed = Date.parse(resource.observedAt)
  const expires = Date.parse(resource.expiresAt)
  if (
    expires - observed !==
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS *
        1_000 ||
    resource.attestationId !==
      expectedAttestationId(resourceWithoutId(resource))
  ) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  return resource
}

function assertCandidateBinding(
  resource: ProviderPriceAttestationResource,
  candidate: ProviderPriceCandidateBinding,
): void {
  if (
    !SHA256.test(candidate.resourceSha256) ||
    resource.candidateId !== candidate.candidate.candidateId ||
    resource.candidateResourceSha256 !== candidate.resourceSha256 ||
    resource.provider !== candidate.candidate.provider ||
    resource.model !== candidate.candidate.model
  ) {
    throw new ProviderPriceAttestationError(
      'price_attestation_candidate_drift',
    )
  }
}

function assertNoPriceIncrease(
  prices: ProviderTokenPrices,
  candidate: ProviderPriceCandidateBinding,
): void {
  if (
    PRICE_KEYS.some(
      (key) =>
        compareCanonicalDecimals(
          prices[key],
          candidatePriceDecimal(
            candidate.candidate.catalog.priceUsdPerMillionTokens[key],
          ),
        ) > 0,
    )
  ) {
    throw new ProviderPriceAttestationError(
      'price_attestation_rebudget_required',
    )
  }
}

function verifiedIdentity(
  resource: ProviderPriceAttestationResource,
  resourceSha256: string,
): VerifiedProviderPriceAttestation {
  return Object.freeze({
    schemaVersion:
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_SCHEMA,
    attestationId: resource.attestationId,
    resourceSha256,
    candidateId: resource.candidateId,
    candidateResourceSha256: resource.candidateResourceSha256,
    provider: resource.provider,
    model: resource.model,
    currency: 'USD',
    billingUnit: 'per_1_million_tokens',
    priceUsdPerMillionTokens: Object.freeze({
      ...resource.priceUsdPerMillionTokens,
    }),
    observedAt: resource.observedAt,
    expiresAt: resource.expiresAt,
    validitySeconds:
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS,
    evidenceSha256: resource.evidence.sha256,
  })
}

export function assertVerifiedProviderPriceAttestation(
  value: unknown,
  candidate: ProviderPriceCandidateBinding,
): VerifiedProviderPriceAttestation {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'schemaVersion',
      'attestationId',
      'resourceSha256',
      'candidateId',
      'candidateResourceSha256',
      'provider',
      'model',
      'currency',
      'billingUnit',
      'priceUsdPerMillionTokens',
      'observedAt',
      'expiresAt',
      'validitySeconds',
      'evidenceSha256',
    ]) ||
    value.schemaVersion !==
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_SCHEMA ||
    typeof value.attestationId !== 'string' ||
    !/^price-attestation:[a-f0-9]{64}$/.test(
      value.attestationId,
    ) ||
    typeof value.resourceSha256 !== 'string' ||
    !SHA256.test(value.resourceSha256) ||
    typeof value.candidateId !== 'string' ||
    !SAFE_ID.test(value.candidateId) ||
    typeof value.candidateResourceSha256 !== 'string' ||
    !SHA256.test(value.candidateResourceSha256) ||
    typeof value.provider !== 'string' ||
    !SAFE_ID.test(value.provider) ||
    typeof value.model !== 'string' ||
    !SAFE_ID.test(value.model) ||
    value.currency !== 'USD' ||
    value.billingUnit !== 'per_1_million_tokens' ||
    !canonicalTimestamp(value.observedAt) ||
    !canonicalTimestamp(value.expiresAt) ||
    value.validitySeconds !==
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS ||
    typeof value.evidenceSha256 !== 'string' ||
    !SHA256.test(value.evidenceSha256)
  ) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  const prices = parsePrices(value.priceUsdPerMillionTokens)
  if (!prices) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  const resource: ProviderPriceAttestationResource = {
    schemaVersion:
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_SCHEMA,
    attestationId: value.attestationId,
    candidateId: value.candidateId,
    candidateResourceSha256: value.candidateResourceSha256,
    provider: value.provider,
    model: value.model,
    currency: 'USD',
    billingUnit: 'per_1_million_tokens',
    priceUsdPerMillionTokens: prices,
    observedAt: value.observedAt,
    expiresAt: value.expiresAt,
    validitySeconds:
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS,
    evidence: {
      type: 'provider_pricing_snapshot',
      sha256: value.evidenceSha256,
    },
  }
  if (
    Date.parse(resource.expiresAt) -
      Date.parse(resource.observedAt) !==
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS *
        1_000 ||
    resource.attestationId !==
      expectedAttestationId(resourceWithoutId(resource))
  ) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  if (value.resourceSha256 !== sha256(serializedResource(resource))) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  assertCandidateBinding(resource, candidate)
  assertNoPriceIncrease(prices, candidate)
  return verifiedIdentity(resource, value.resourceSha256)
}

export function assertProviderPriceAttestationRunway(
  attestation: VerifiedProviderPriceAttestation,
  now: Date,
): void {
  if (!Number.isFinite(now.valueOf())) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  const observedAt = Date.parse(attestation.observedAt)
  const expiresAt = Date.parse(attestation.expiresAt)
  if (observedAt > now.valueOf()) {
    throw new ProviderPriceAttestationError(
      'price_attestation_future_observation',
    )
  }
  if (now.valueOf() >= expiresAt) {
    throw new ProviderPriceAttestationError(
      'price_attestation_expired',
    )
  }
  if (
    expiresAt - now.valueOf() <
    COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_MINIMUM_RUNWAY_SECONDS *
      1_000
  ) {
    throw new ProviderPriceAttestationError(
      'price_attestation_insufficient_runway',
    )
  }
}

export function loadCurrentProviderPriceAttestation(options: {
  repositoryRoot: string
  candidate: ProviderPriceCandidateBinding
  now?: Date
}): VerifiedProviderPriceAttestation {
  const now = options.now ?? new Date()
  if (!Number.isFinite(now.valueOf())) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  let descriptor: number | undefined
  try {
    const directory = secureAttestationDirectory(
      options.repositoryRoot,
      false,
    )
    const path = resolve(
      directory,
      'deepseek-v4-flash-pi-0.82.1-v1-price-attestation-v1.json',
    )
    if (!pathEntryExists(path)) {
      throw new ProviderPriceAttestationError(
        'price_attestation_missing',
      )
    }
    descriptor = openSync(
      path,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    )
    assertSafeAttestationFile(path, fstatSync(descriptor))
    const text = readFileSync(descriptor, 'utf8')
    closeSync(descriptor)
    descriptor = undefined
    const resource = parseResource(JSON.parse(text) as unknown)
    if (text !== serializedResource(resource)) {
      throw new ProviderPriceAttestationError(
        'price_attestation_invalid',
      )
    }
    assertCandidateBinding(resource, options.candidate)
    assertNoPriceIncrease(
      resource.priceUsdPerMillionTokens,
      options.candidate,
    )
    const observed = Date.parse(resource.observedAt)
    const expires = Date.parse(resource.expiresAt)
    if (observed > now.valueOf()) {
      throw new ProviderPriceAttestationError(
        'price_attestation_future_observation',
      )
    }
    if (now.valueOf() >= expires) {
      throw new ProviderPriceAttestationError(
        'price_attestation_expired',
      )
    }
    return verifiedIdentity(resource, sha256(text))
  } catch (error) {
    if (descriptor !== undefined) closeSync(descriptor)
    if (error instanceof ProviderPriceAttestationError) throw error
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
}

export interface WriteProviderPriceAttestationOptions {
  repositoryRoot: string
  candidate: ProviderPriceCandidateBinding
  observedAt: string
  evidenceSha256: string
  priceUsdPerMillionTokens: ProviderTokenPrices
  now?: Date
}

export function writeCurrentProviderPriceAttestation(
  options: WriteProviderPriceAttestationOptions,
): VerifiedProviderPriceAttestation {
  const now = options.now ?? new Date()
  const observed = new Date(options.observedAt)
  const expiresAtValue =
    observed.valueOf() +
    COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS *
      1_000
  if (
    !Number.isFinite(now.valueOf()) ||
    !Number.isFinite(observed.valueOf()) ||
    observed.valueOf() > now.valueOf() ||
    !SHA256.test(options.evidenceSha256) ||
    !SAFE_ID.test(options.candidate.candidate.candidateId) ||
    !SAFE_ID.test(options.candidate.candidate.provider) ||
    !SAFE_ID.test(options.candidate.candidate.model) ||
    !SHA256.test(options.candidate.resourceSha256)
  ) {
    throw new ProviderPriceAttestationError(
      observed.valueOf() > now.valueOf()
        ? 'price_attestation_future_observation'
      : 'price_attestation_invalid',
    )
  }
  if (now.valueOf() >= expiresAtValue) {
    throw new ProviderPriceAttestationError(
      'price_attestation_expired',
    )
  }
  let prices: ProviderTokenPrices
  try {
    prices = {
      input: canonicalProviderPriceDecimal(
        options.priceUsdPerMillionTokens.input,
      ),
      output: canonicalProviderPriceDecimal(
        options.priceUsdPerMillionTokens.output,
      ),
      cacheRead: canonicalProviderPriceDecimal(
        options.priceUsdPerMillionTokens.cacheRead,
      ),
      cacheWrite: canonicalProviderPriceDecimal(
        options.priceUsdPerMillionTokens.cacheWrite,
      ),
    }
  } catch {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }
  assertNoPriceIncrease(prices, options.candidate)
  const observedAt = observed.toISOString()
  const expiresAt = new Date(
    expiresAtValue,
  ).toISOString()
  const body: Omit<
    ProviderPriceAttestationResource,
    'attestationId'
  > = {
    schemaVersion:
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_SCHEMA,
    candidateId: options.candidate.candidate.candidateId,
    candidateResourceSha256: options.candidate.resourceSha256,
    provider: options.candidate.candidate.provider,
    model: options.candidate.candidate.model,
    currency: 'USD',
    billingUnit: 'per_1_million_tokens',
    priceUsdPerMillionTokens: prices,
    observedAt,
    expiresAt,
    validitySeconds:
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS,
    evidence: {
      type: 'provider_pricing_snapshot',
      sha256: options.evidenceSha256,
    },
  }
  const {
    schemaVersion,
    ...bodyAfterSchemaVersion
  } = body
  const resource: ProviderPriceAttestationResource = {
    schemaVersion,
    attestationId: expectedAttestationId(body),
    ...bodyAfterSchemaVersion,
  }
  const text = serializedResource(resource)
  if (Buffer.byteLength(text, 'utf8') > MAX_ATTESTATION_BYTES) {
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  }

  const directory = secureAttestationDirectory(
    options.repositoryRoot,
    true,
  )
  const path = resolve(
    directory,
    'deepseek-v4-flash-pi-0.82.1-v1-price-attestation-v1.json',
  )
  const lockPath = resolve(
    directory,
    '.deepseek-v4-flash-pi-0.82.1-v1-price-attestation-v1.lock',
  )
  const tempPath = resolve(
    directory,
    `.price-attestation-${randomUUID()}.tmp`,
  )
  let lockDescriptor: number | undefined
  let tempDescriptor: number | undefined
  let lockOwned = false
  let tempOwned = false
  try {
    try {
      lockDescriptor = openSync(
        lockPath,
        constants.O_CREAT |
          constants.O_EXCL |
          constants.O_RDWR |
          (constants.O_NOFOLLOW ?? 0),
        0o600,
      )
      lockOwned = true
      fchmodSync(lockDescriptor, 0o600)
      const lockStat = fstatSync(lockDescriptor)
      assertOwned(lockStat)
      if (
        !lockStat.isFile() ||
        lockStat.nlink !== 1 ||
        permissionBits(lockStat) !== 0o600
      ) {
        throw new ProviderPriceAttestationError(
          'price_attestation_invalid',
        )
      }
      fsyncSync(lockDescriptor)
      fsyncDirectory(directory)
    } catch (error) {
      if (
        error instanceof Error &&
        'code' in error &&
        error.code === 'EEXIST'
      ) {
        throw new ProviderPriceAttestationError(
          'price_attestation_busy',
        )
      }
      throw error
    }
    if (pathEntryExists(path)) {
      assertSafeAttestationFile(path, lstatSync(path))
    }
    tempDescriptor = openSync(
      tempPath,
      constants.O_CREAT |
        constants.O_EXCL |
        constants.O_WRONLY |
        (constants.O_NOFOLLOW ?? 0),
      0o600,
    )
    tempOwned = true
    fchmodSync(tempDescriptor, 0o600)
    const tempStat = fstatSync(tempDescriptor)
    assertOwned(tempStat)
    if (
      !tempStat.isFile() ||
      tempStat.nlink !== 1 ||
      permissionBits(tempStat) !== 0o600
    ) {
      throw new ProviderPriceAttestationError(
        'price_attestation_invalid',
      )
    }
    writeFileSync(tempDescriptor, text, 'utf8')
    fsyncSync(tempDescriptor)
    closeSync(tempDescriptor)
    tempDescriptor = undefined
    if (pathEntryExists(path)) {
      assertSafeAttestationFile(path, lstatSync(path))
    }
    renameSync(tempPath, path)
    tempOwned = false
    fsyncDirectory(directory)
  } catch (error) {
    if (tempDescriptor !== undefined) closeSync(tempDescriptor)
    if (tempOwned) rmSync(tempPath, { force: true })
    if (error instanceof ProviderPriceAttestationError) throw error
    throw new ProviderPriceAttestationError(
      'price_attestation_invalid',
    )
  } finally {
    if (lockDescriptor !== undefined) closeSync(lockDescriptor)
    if (lockOwned) {
      rmSync(lockPath, { force: true })
      fsyncDirectory(directory)
    }
  }
  return loadCurrentProviderPriceAttestation({
    repositoryRoot: options.repositoryRoot,
    candidate: options.candidate,
    now,
  })
}
