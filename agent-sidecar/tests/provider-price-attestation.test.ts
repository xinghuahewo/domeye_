import assert from 'node:assert/strict'
import {
  chmodSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import test, { after } from 'node:test'

import {
  canonicalProviderPriceDecimal,
  COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_RELATIVE_PATH,
  COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
  COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_RELATIVE_PATH,
  COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS,
  loadCurrentProviderPriceAttestation,
  loadPiModelCandidate,
  ProviderPriceAttestationError,
  readA4CandidateReadinessStatus,
  writeCurrentProviderPriceAttestation,
  type CandidateActivityBudgetPolicy,
  type LoadedPiModelCandidate,
  type ProviderPriceCandidateBinding,
} from '../src/pi/index.js'
import {
  initializeCandidateActivityLedgerWithPreLedgerFailure,
  initializeCleanCandidateActivityLedger,
} from '../src/pi/candidate-activity-ledger.js'

const TEST_ROOT = mkdtempSync(
  join(tmpdir(), 'domeye-provider-price-attestation-test-'),
)
chmodSync(TEST_ROOT, 0o700)

after(() => {
  rmSync(TEST_ROOT, { recursive: true, force: true })
})

function root(label: string): string {
  const path = join(TEST_ROOT, label)
  mkdirSync(path, { mode: 0o700 })
  chmodSync(path, 0o700)
  return path
}

function prices() {
  return {
    input: '0.14',
    output: '0.28',
    cacheRead: '0.0028',
    cacheWrite: '0',
  }
}

function candidatePrices() {
  return {
    input: 0.14,
    output: 0.28,
    cacheRead: 0.0028,
    cacheWrite: 0,
  }
}

function activityPolicy(
  loaded: LoadedPiModelCandidate,
): CandidateActivityBudgetPolicy {
  const maximumSingleReportCostCny = 5.7835008
  return {
    candidateId: loaded.candidate.candidateId,
    candidateResourceSha256: loaded.resourceSha256,
    provider: loaded.candidate.provider,
    model: loaded.candidate.model,
    budgetLimitCny: 20,
    maximumSingleReportCostCny,
    maximumCertificationCostCny:
      maximumSingleReportCostCny * 2,
    conservativeCnyPerUsd: 8,
    priceUsdPerMillionTokens: candidatePrices(),
  }
}

function writeValid(
  repositoryRoot: string,
  loaded: LoadedPiModelCandidate,
  observedAt = '2026-07-29T04:00:00.000Z',
) {
  return writeCurrentProviderPriceAttestation({
    repositoryRoot,
    candidate: loaded,
    observedAt,
    evidenceSha256: 'e'.repeat(64),
    priceUsdPerMillionTokens: prices(),
    now: new Date(observedAt),
  })
}

test('运维写入固定路径、固定24小时、0600当前用户普通文件并可只读核验', async () => {
  const loaded = await loadPiModelCandidate()
  const repositoryRoot = root('valid')
  const attestation = writeValid(repositoryRoot, loaded)
  const path = resolve(
    repositoryRoot,
    COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_RELATIVE_PATH,
  )
  const stat = lstatSync(path)
  assert.equal(stat.isFile(), true)
  assert.equal(stat.isSymbolicLink(), false)
  assert.equal(stat.nlink, 1)
  assert.equal(stat.mode & 0o777, 0o600)
  assert.equal(stat.uid, process.getuid?.())
  assert.equal(
    Date.parse(attestation.expiresAt) -
      Date.parse(attestation.observedAt),
    COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_VALIDITY_SECONDS *
      1_000,
  )
  assert.deepEqual(
    loadCurrentProviderPriceAttestation({
      repositoryRoot,
      candidate: loaded,
      now: new Date('2026-07-29T12:00:00.000Z'),
    }),
    attestation,
  )
  assert.equal(attestation.candidateResourceSha256, loaded.resourceSha256)
  assert.equal(attestation.provider, 'deepseek')
  assert.equal(attestation.model, 'deepseek-v4-flash')
  assert.equal(attestation.currency, 'USD')
  assert.equal(attestation.billingUnit, 'per_1_million_tokens')
  assert.equal(attestation.evidenceSha256, 'e'.repeat(64))
})

test('缺失、过期与未来观测分别失败关闭', async (context) => {
  const loaded = await loadPiModelCandidate()
  await context.test('missing', () => {
    assert.throws(
      () =>
        loadCurrentProviderPriceAttestation({
          repositoryRoot: root('missing'),
          candidate: loaded,
          now: new Date('2026-07-29T12:00:00.000Z'),
        }),
      (error: unknown) =>
        error instanceof ProviderPriceAttestationError &&
        error.code === 'price_attestation_missing',
    )
  })
  await context.test('expired', () => {
    const repositoryRoot = root('expired')
    writeValid(repositoryRoot, loaded)
    assert.throws(
      () =>
        loadCurrentProviderPriceAttestation({
          repositoryRoot,
          candidate: loaded,
          now: new Date('2026-07-30T04:00:00.000Z'),
        }),
      (error: unknown) =>
        error instanceof ProviderPriceAttestationError &&
        error.code === 'price_attestation_expired',
    )
  })
  await context.test('future', () => {
    const repositoryRoot = root('future')
    writeValid(
      repositoryRoot,
      loaded,
      '2026-07-29T10:00:00.000Z',
    )
    assert.throws(
      () =>
        loadCurrentProviderPriceAttestation({
          repositoryRoot,
          candidate: loaded,
          now: new Date('2026-07-29T09:59:59.999Z'),
        }),
      (error: unknown) =>
        error instanceof ProviderPriceAttestationError &&
        error.code === 'price_attestation_future_observation',
    )
  })
})

test('运维传入已过期观测时在任何写入前拒绝，既有有效证明字节不变', async () => {
  const loaded = await loadPiModelCandidate()
  const repositoryRoot = root('expired-write-preserves-current')
  writeValid(repositoryRoot, loaded)
  const path = resolve(
    repositoryRoot,
    COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_RELATIVE_PATH,
  )
  const before = readFileSync(path)
  assert.throws(
    () =>
      writeCurrentProviderPriceAttestation({
        repositoryRoot,
        candidate: loaded,
        observedAt: '2026-07-27T04:00:00.000Z',
        evidenceSha256: 'a'.repeat(64),
        priceUsdPerMillionTokens: prices(),
        now: new Date('2026-07-29T04:00:00.000Z'),
      }),
    (error: unknown) =>
      error instanceof ProviderPriceAttestationError &&
      error.code === 'price_attestation_expired',
  )
  assert.deepEqual(readFileSync(path), before)
})

test('价格十进制全程精确保留，任何小于浮点分辨率的正向上调也失败关闭', async () => {
  const loaded = await loadPiModelCandidate()
  const repositoryRoot = root('exact-decimal-rebudget')
  writeValid(repositoryRoot, loaded)
  const path = resolve(
    repositoryRoot,
    COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_RELATIVE_PATH,
  )
  const before = readFileSync(path)
  assert.equal(
    canonicalProviderPriceDecimal('0.1400000000000000000'),
    '0.14',
  )
  assert.throws(
    () =>
      writeCurrentProviderPriceAttestation({
        repositoryRoot,
        candidate: loaded,
        observedAt: '2026-07-29T04:01:00.000Z',
        evidenceSha256: 'b'.repeat(64),
        priceUsdPerMillionTokens: {
          ...prices(),
          input: '0.1400000000000000001',
        },
        now: new Date('2026-07-29T04:01:00.000Z'),
      }),
    (error: unknown) =>
      error instanceof ProviderPriceAttestationError &&
      error.code === 'price_attestation_rebudget_required',
  )
  assert.deepEqual(readFileSync(path), before)
})

test('任一供应商价格高于候选冻结值时要求重新预算', async (context) => {
  const loaded = await loadPiModelCandidate()
  for (const key of [
    'input',
    'output',
    'cacheRead',
    'cacheWrite',
  ] as const) {
    await context.test(key, () => {
      const repositoryRoot = root(`rebudget-${key}`)
      const higherCandidate = structuredClone(
        loaded,
      ) as ProviderPriceCandidateBinding
      higherCandidate.candidate.catalog.priceUsdPerMillionTokens[
        key
      ] = candidatePrices()[key] + 1
      writeCurrentProviderPriceAttestation({
        repositoryRoot,
        candidate: higherCandidate,
        observedAt: '2026-07-29T04:00:00.000Z',
        evidenceSha256: 'f'.repeat(64),
        priceUsdPerMillionTokens: {
          ...prices(),
          [key]: String(candidatePrices()[key] + 1),
        },
        now: new Date('2026-07-29T04:00:00.000Z'),
      })
      assert.throws(
        () =>
          loadCurrentProviderPriceAttestation({
            repositoryRoot,
            candidate: loaded,
            now: new Date('2026-07-29T05:00:00.000Z'),
          }),
        (error: unknown) =>
          error instanceof ProviderPriceAttestationError &&
          error.code === 'price_attestation_rebudget_required',
      )
    })
  }
})

test('候选资源摘要漂移失败关闭', async () => {
  const loaded = await loadPiModelCandidate()
  const repositoryRoot = root('candidate-drift')
  const drifted = structuredClone(
    loaded,
  ) as ProviderPriceCandidateBinding
  drifted.resourceSha256 = 'b'.repeat(64)
  writeCurrentProviderPriceAttestation({
    repositoryRoot,
    candidate: drifted,
    observedAt: '2026-07-29T04:00:00.000Z',
    evidenceSha256: 'c'.repeat(64),
    priceUsdPerMillionTokens: prices(),
    now: new Date('2026-07-29T04:00:00.000Z'),
  })
  assert.throws(
    () =>
      loadCurrentProviderPriceAttestation({
        repositoryRoot,
        candidate: loaded,
        now: new Date('2026-07-29T05:00:00.000Z'),
      }),
    (error: unknown) =>
      error instanceof ProviderPriceAttestationError &&
      error.code === 'price_attestation_candidate_drift',
  )
})

test('格式、权限与 symlink 均失败关闭', async (context) => {
  const loaded = await loadPiModelCandidate()
  await context.test('format', () => {
    const repositoryRoot = root('invalid-format')
    writeValid(repositoryRoot, loaded)
    const path = resolve(
      repositoryRoot,
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_RELATIVE_PATH,
    )
    const value = JSON.parse(readFileSync(path, 'utf8')) as Record<
      string,
      unknown
    >
    value.unexpected = true
    writeFileSync(path, `${JSON.stringify(value)}\n`, 'utf8')
    chmodSync(path, 0o600)
    assert.throws(
      () =>
        loadCurrentProviderPriceAttestation({
          repositoryRoot,
          candidate: loaded,
          now: new Date('2026-07-29T05:00:00.000Z'),
        }),
      (error: unknown) =>
        error instanceof ProviderPriceAttestationError &&
        error.code === 'price_attestation_invalid',
    )
  })
  await context.test('noncanonical-json-bytes', () => {
    const repositoryRoot = root('invalid-noncanonical-json')
    writeValid(repositoryRoot, loaded)
    const path = resolve(
      repositoryRoot,
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_RELATIVE_PATH,
    )
    const value = JSON.parse(readFileSync(path, 'utf8')) as unknown
    writeFileSync(path, JSON.stringify(value), 'utf8')
    chmodSync(path, 0o600)
    assert.throws(
      () =>
        loadCurrentProviderPriceAttestation({
          repositoryRoot,
          candidate: loaded,
          now: new Date('2026-07-29T05:00:00.000Z'),
        }),
      (error: unknown) =>
        error instanceof ProviderPriceAttestationError &&
        error.code === 'price_attestation_invalid',
    )
  })
  await context.test('permission', () => {
    const repositoryRoot = root('invalid-permission')
    writeValid(repositoryRoot, loaded)
    const path = resolve(
      repositoryRoot,
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_RELATIVE_PATH,
    )
    chmodSync(path, 0o644)
    assert.throws(
      () =>
        loadCurrentProviderPriceAttestation({
          repositoryRoot,
          candidate: loaded,
          now: new Date('2026-07-29T05:00:00.000Z'),
        }),
      (error: unknown) =>
        error instanceof ProviderPriceAttestationError &&
        error.code === 'price_attestation_invalid',
    )
  })
  await context.test('symlink', () => {
    const repositoryRoot = root('invalid-symlink')
    writeValid(repositoryRoot, loaded)
    const path = resolve(
      repositoryRoot,
      COUNTRY_OUTAGE_PROVIDER_PRICE_ATTESTATION_RELATIVE_PATH,
    )
    const target = join(repositoryRoot, 'attestation-target.json')
    writeFileSync(target, readFileSync(path), { mode: 0o600 })
    rmSync(path)
    symlinkSync(target, path)
    assert.throws(
      () =>
        loadCurrentProviderPriceAttestation({
          repositoryRoot,
          candidate: loaded,
          now: new Date('2026-07-29T05:00:00.000Z'),
        }),
      (error: unknown) =>
        error instanceof ProviderPriceAttestationError &&
        error.code === 'price_attestation_invalid',
    )
  })
})

test('纯只读 readiness 同时区分价格缺失或过期与历史用量未结清，且不创建 lock', async () => {
  const loaded = await loadPiModelCandidate()
  const repositoryRoot = root('readiness')
  const ledger = initializeCandidateActivityLedgerWithPreLedgerFailure({
    repositoryRoot,
    policy: activityPolicy(loaded),
    recordedAt: new Date('2026-07-29T03:00:00.000Z'),
  })
  ledger.close()
  const missing = await readA4CandidateReadinessStatus({
    repositoryRoot,
    now: () => new Date('2026-07-29T05:00:00.000Z'),
  })
  assert.equal(missing.ready, false)
  assert.deepEqual(missing.blockers, [
    'price_attestation_missing',
    'historical_usage_unresolved',
  ])
  assert.equal(missing.safety.readOnly, true)
  assert.equal(missing.safety.credentialsRead, false)
  assert.equal(missing.safety.networkAccessed, false)
  writeValid(repositoryRoot, loaded)
  const insufficient = await readA4CandidateReadinessStatus({
    repositoryRoot,
    now: () => new Date('2026-07-30T03:50:00.000Z'),
  })
  assert.deepEqual(insufficient.blockers, [
    'price_attestation_insufficient_runway',
    'historical_usage_unresolved',
  ])
  const expired = await readA4CandidateReadinessStatus({
    repositoryRoot,
    now: () => new Date('2026-07-30T04:00:00.000Z'),
  })
  assert.deepEqual(expired.blockers, [
    'price_attestation_expired',
    'historical_usage_unresolved',
  ])
  const ledgerLock = resolve(
    dirname(
      resolve(
        repositoryRoot,
        COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH,
      ),
    ),
    '.deepseek-v4-flash-pi-0.84.1-v1-activity-v1.lock',
  )
  assert.equal(lstatExists(ledgerLock), false)
  assert.equal(
    lstatExists(
      resolve(
        repositoryRoot,
        COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_RELATIVE_PATH,
      ),
    ),
    true,
  )
})

test('纯只读 readiness 对合法价格和 resolved 零成本账本给出 ready', async () => {
  const loaded = await loadPiModelCandidate()
  const repositoryRoot = root('readiness-ready')
  writeValid(repositoryRoot, loaded)
  const ledger = initializeCleanCandidateActivityLedger({
    repositoryRoot,
    policy: activityPolicy(loaded),
    recordedAt: new Date('2026-07-29T03:00:00.000Z'),
  })
  ledger.close()
  const status = await readA4CandidateReadinessStatus({
    repositoryRoot,
    now: () => new Date('2026-07-29T05:00:00.000Z'),
  })
  assert.equal(status.ready, true)
  assert.deepEqual(status.blockers, [])
  assert.equal(status.priceAttestation.status, 'valid')
  assert.equal(status.activity.status, 'ready')
})

test('显式 operator CLI 只接受固定环境标量，不接受 argv 或任意证据/输出路径', () => {
  const source = readFileSync(
    resolve(
      process.cwd(),
      'src/cli/create-a4-provider-price-attestation.ts',
    ),
    'utf8',
  )
  const environmentNames = [
    ...new Set(
      source.match(/COUNTRY_OUTAGE_PI_PRICE_[A-Z0-9_]+/g) ?? [],
    ),
  ].sort()
  assert.deepEqual(environmentNames, [
    'COUNTRY_OUTAGE_PI_PRICE_CACHE_READ_USD_PER_MILLION',
    'COUNTRY_OUTAGE_PI_PRICE_CACHE_WRITE_USD_PER_MILLION',
    'COUNTRY_OUTAGE_PI_PRICE_EVIDENCE_SHA256',
    'COUNTRY_OUTAGE_PI_PRICE_INPUT_USD_PER_MILLION',
    'COUNTRY_OUTAGE_PI_PRICE_OBSERVED_AT',
    'COUNTRY_OUTAGE_PI_PRICE_OUTPUT_USD_PER_MILLION',
  ])
  assert.doesNotMatch(source, /process\.argv/)
  assert.doesNotMatch(
    source,
    /COUNTRY_OUTAGE_PI_PRICE_(?:EVIDENCE|OUTPUT|ATTESTATION)_PATH/,
  )
  assert.doesNotMatch(source, /\breadFile(?:Sync)?\s*\(/)
})

function lstatExists(path: string): boolean {
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
    throw error
  }
}
