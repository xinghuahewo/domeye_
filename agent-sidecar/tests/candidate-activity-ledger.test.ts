import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test, { after } from 'node:test'

import {
  CandidateActivityLedgerError,
  inspectCandidateActivityLedger,
  openCandidateActivityLedger,
  type CandidateActivityBudgetPolicy,
  type CandidateActivityLedger,
} from '../src/pi/index.js'
import {
  COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
  initializeCandidateActivityLedgerWithPreLedgerFailure,
  initializeCleanCandidateActivityLedger,
  reconcileCandidateActivityLedgerHistoricalBilledAmount,
  reconcileCandidateActivityLedgerHistoricalUsage,
} from '../src/pi/candidate-activity-ledger.js'
import type {
  CandidateActivityHistoricalBilledAmount,
} from '../src/pi/candidate-activity-ledger.js'

const TEST_DIRECTORY = mkdtempSync(
  join(tmpdir(), 'domeye-candidate-activity-ledger-test-'),
)
chmodSync(TEST_DIRECTORY, 0o700)
const FROZEN_CANDIDATE_RESOURCE_SHA256 =
  '1b8294f946f0bd9ad13ea874b2bf0da79a65adeb7a6713241eccfb2e3b6e6d41'
const TEST_MODULE_DIRECTORY = dirname(fileURLToPath(import.meta.url))

function fixturePath(filename: string): string {
  const candidates = [
    join(TEST_MODULE_DIRECTORY, 'fixtures', filename),
    join(TEST_MODULE_DIRECTORY, '../../tests/fixtures', filename),
  ]
  const path = candidates.find((candidate) => existsSync(candidate))
  assert.ok(path, `缺少账本兼容测试 fixture：${filename}`)
  return path
}

const LEGACY_PREFIX_TEXT = readFileSync(
  fixturePath('a4-legacy-activity-prefix-v1.jsonl'),
  'utf8',
)
const LEGACY_ANCHOR_TEXT = readFileSync(
  fixturePath('a4-legacy-activity-prefix-v1-anchor.json'),
  'utf8',
)

after(() => {
  rmSync(TEST_DIRECTORY, { recursive: true, force: true })
})

function root(label: string): string {
  const path = join(TEST_DIRECTORY, label)
  return mkdtempSync(`${path}-`)
}

function seedFrozenLegacyPrefix(repositoryRoot: string): {
  ledgerPath: string
  anchorPath: string
} {
  const varDirectory = join(repositoryRoot, 'var')
  const agentDirectory = join(varDirectory, 'country-outage-agent')
  const ledgerDirectory = join(
    agentDirectory,
    'a4-model-certification-activity',
  )
  mkdirSync(varDirectory, { mode: 0o700 })
  mkdirSync(agentDirectory, { mode: 0o700 })
  mkdirSync(ledgerDirectory, { mode: 0o700 })
  chmodSync(varDirectory, 0o700)
  chmodSync(agentDirectory, 0o700)
  chmodSync(ledgerDirectory, 0o700)
  const ledgerPath = join(
    ledgerDirectory,
    'deepseek-v4-flash-pi-0.82.1-v1-activity-v1.jsonl',
  )
  const anchorPath = join(
    ledgerDirectory,
    'deepseek-v4-flash-pi-0.82.1-v1-activity-anchor-v1.json',
  )
  writeFileSync(ledgerPath, LEGACY_PREFIX_TEXT, {
    encoding: 'utf8',
    mode: 0o600,
  })
  writeFileSync(anchorPath, LEGACY_ANCHOR_TEXT, {
    encoding: 'utf8',
    mode: 0o600,
  })
  chmodSync(ledgerPath, 0o600)
  chmodSync(anchorPath, 0o600)
  return { ledgerPath, anchorPath }
}

function compareCodePoints(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0
}

function compareLegacyAsciiCaseFolded(
  left: string,
  right: string,
): number {
  const foldedLeft = left.replace(/[A-Z]/g, (character) =>
    String.fromCharCode(character.charCodeAt(0) + 32),
  )
  const foldedRight = right.replace(/[A-Z]/g, (character) =>
    String.fromCharCode(character.charCodeAt(0) + 32),
  )
  return (
    compareCodePoints(foldedLeft, foldedRight) ||
    compareCodePoints(left, right)
  )
}

function canonicalHash(
  value: unknown,
  comparator: (left: string, right: string) => number,
): string {
  const canonicalize = (item: unknown): unknown => {
    if (Array.isArray(item)) return item.map(canonicalize)
    if (!item || typeof item !== 'object') return item
    return Object.fromEntries(
      Object.entries(item as Record<string, unknown>)
        .sort(([left], [right]) => comparator(left, right))
        .map(([key, nested]) => [key, canonicalize(nested)]),
    )
  }
  return createHash('sha256')
    .update(JSON.stringify(canonicalize(value)))
    .digest('hex')
}

function recordHash(
  value: Record<string, unknown>,
  comparator: (left: string, right: string) => number,
): string {
  const { recordSha256: _recordSha256, ...withoutSha } = value
  return canonicalHash(withoutSha, comparator)
}

function policy(
  candidateResourceSha256 = 'a'.repeat(64),
): CandidateActivityBudgetPolicy {
  const maximumSingleReportCostCny = 5.7835008
  return {
    candidateId: 'deepseek-v4-flash-pi-0.82.1-v1',
    candidateResourceSha256,
    provider: 'deepseek',
    model: 'deepseek-v4-flash',
    budgetLimitCny: 20,
    maximumSingleReportCostCny,
    maximumCertificationCostCny:
      maximumSingleReportCostCny * 2,
    conservativeCnyPerUsd: 8,
    priceUsdPerMillionTokens: {
      input: 0.14,
      output: 0.28,
      cacheRead: 0.0028,
      cacheWrite: 0,
    },
  }
}

function currentPolicy(
  candidateResourceSha256 = 'a'.repeat(64),
): CandidateActivityBudgetPolicy {
  const maximumSingleReportCostCny = 0.5419008
  return {
    ...policy(candidateResourceSha256),
    maximumSingleReportCostCny,
    maximumCertificationCostCny:
      maximumSingleReportCostCny * 2,
  }
}

function zeroToolPolicy(
  candidateResourceSha256 = 'b'.repeat(64),
): CandidateActivityBudgetPolicy {
  const maximumSingleReportCostCny = 0.21676032
  return {
    ...policy(candidateResourceSha256),
    maximumSingleReportCostCny,
    maximumCertificationCostCny:
      maximumSingleReportCostCny * 2,
  }
}

function initializeLedger(
  repositoryRoot: string,
  selectedPolicy = policy(),
): CandidateActivityLedger {
  return initializeCleanCandidateActivityLedger({
    repositoryRoot,
    policy: selectedPolicy,
    recordedAt: new Date('2026-07-29T04:00:00Z'),
  })
}

function initializePreLedger(
  repositoryRoot: string,
  selectedPolicy = policy(),
): CandidateActivityLedger {
  return initializeCandidateActivityLedgerWithPreLedgerFailure({
    repositoryRoot,
    policy: selectedPolicy,
    recordedAt: new Date('2026-07-29T04:00:00Z'),
  })
}

function historicalBilledAmount(
  overrides: Partial<CandidateActivityHistoricalBilledAmount> = {},
): CandidateActivityHistoricalBilledAmount {
  return {
    evidenceSha256: 'd'.repeat(64),
    evidenceWindowStartUtc: '2026-07-29T03:18:48.000Z',
    evidenceWindowEndUtc: '2026-07-29T03:19:25.000Z',
    evidenceTimezone: 'Asia/Shanghai',
    evidenceAcquiredAt: '2026-07-29T04:00:00.000Z',
    billingFinality: 'settled_final',
    billingScope: 'single_attempt_exact_charge',
    billedAmountDecimal: '0.10838016',
    billedCurrency: 'CNY',
    ...overrides,
  }
}

test('固定八条 legacy 前缀只读兼容且检查不会改写账本或 anchor', () => {
  const repositoryRoot = root('frozen-legacy-prefix-readonly')
  const { ledgerPath, anchorPath } =
    seedFrozenLegacyPrefix(repositoryRoot)
  const ledgerBefore = readFileSync(ledgerPath, 'utf8')
  const anchorBefore = readFileSync(anchorPath, 'utf8')

  const snapshot = inspectCandidateActivityLedger({
    repositoryRoot,
    policy: currentPolicy(FROZEN_CANDIDATE_RESOURCE_SHA256),
  })

  assert.deepEqual(snapshot, {
    committedCostCny: 11.72276896,
    remainingBudgetCny: 8.27723104,
    openReservations: 0,
    recordCount: 8,
    historicalUsageStatus: 'resolved',
  })
  assert.equal(readFileSync(ledgerPath, 'utf8'), ledgerBefore)
  assert.equal(readFileSync(anchorPath, 'utf8'), anchorBefore)
})

test('固定 legacy 前缀内容、记录顺序、previous hash 或长度任一漂移均失败关闭', async (context) => {
  const cases = [
    {
      name: '内容漂移',
      mutate(records: Record<string, unknown>[]) {
        records[3]!.recordedAt = '2026-07-29T06:34:40.121Z'
      },
    },
    {
      name: '记录顺序漂移',
      mutate(records: Record<string, unknown>[]) {
        ;[records[2], records[3]] = [records[3]!, records[2]!]
      },
    },
    {
      name: 'previous hash 漂移',
      mutate(records: Record<string, unknown>[]) {
        records[3]!.previousRecordSha256 = '0'.repeat(64)
      },
    },
    {
      name: '固定前缀不足八条',
      mutate(records: Record<string, unknown>[]) {
        records.pop()
      },
    },
  ]
  for (const item of cases) {
    await context.test(item.name, () => {
      const repositoryRoot = root(
        `frozen-legacy-prefix-${item.name}`,
      )
      const { ledgerPath } =
        seedFrozenLegacyPrefix(repositoryRoot)
      const records = LEGACY_PREFIX_TEXT.trim()
        .split('\n')
        .map(
          (line) =>
            JSON.parse(line) as Record<string, unknown>,
        )
      item.mutate(records)
      writeFileSync(
        ledgerPath,
        `${records.map((record) => JSON.stringify(record)).join('\n')}\n`,
        'utf8',
      )
      assert.throws(
        () =>
          inspectCandidateActivityLedger({
            repositoryRoot,
            policy: currentPolicy(FROZEN_CANDIDATE_RESOURCE_SHA256),
          }),
        (error: unknown) =>
          error instanceof CandidateActivityLedgerError &&
          error.code === 'activity_ledger_invalid',
      )
    })
  }
})

test('旧记录内容即使级联重算 legacy hash 与 anchor 也不能替换固定八条前缀', () => {
  const repositoryRoot = root('frozen-legacy-prefix-rehashed')
  const { ledgerPath, anchorPath } =
    seedFrozenLegacyPrefix(repositoryRoot)
  const records = LEGACY_PREFIX_TEXT.trim()
    .split('\n')
    .map(
      (line) => JSON.parse(line) as Record<string, unknown>,
    )
  records[3]!.recordedAt = '2026-07-29T06:34:40.121Z'
  for (let index = 3; index < records.length; index += 1) {
    if (index > 3) {
      records[index]!.previousRecordSha256 =
        records[index - 1]!.recordSha256
    }
    records[index]!.recordSha256 = recordHash(
      records[index]!,
      compareLegacyAsciiCaseFolded,
    )
  }
  writeFileSync(
    ledgerPath,
    `${records.map((record) => JSON.stringify(record)).join('\n')}\n`,
    'utf8',
  )
  const anchor = JSON.parse(
    readFileSync(anchorPath, 'utf8'),
  ) as Record<string, unknown>
  anchor.lastRecordSha256 = records.at(-1)!.recordSha256
  writeFileSync(anchorPath, `${JSON.stringify(anchor)}\n`, 'utf8')

  assert.throws(
    () =>
      inspectCandidateActivityLedger({
        repositoryRoot,
        policy: currentPolicy(FROZEN_CANDIDATE_RESOURCE_SHA256),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
})

test('旧八条前缀后 writer 只追加 code-point 记录且 sequence 9 的 legacy hash 被拒绝', () => {
  const repositoryRoot = root('frozen-legacy-prefix-append')
  const { ledgerPath, anchorPath } =
    seedFrozenLegacyPrefix(repositoryRoot)
  const ledger = openCandidateActivityLedger({
    repositoryRoot,
    policy: currentPolicy(FROZEN_CANDIDATE_RESOURCE_SHA256),
  })
  ledger.reserve(1, new Date('2026-07-29T12:55:00.000Z'))
  ledger.close()

  const records = readFileSync(ledgerPath, 'utf8')
    .trim()
    .split('\n')
    .map(
      (line) => JSON.parse(line) as Record<string, unknown>,
    )
  assert.equal(records.length, 9)
  const ninth = records[8]!
  assert.equal(ninth.reservedCostCny, 0.5419008)
  assert.equal(
    ninth.recordSha256,
    recordHash(ninth, compareCodePoints),
  )
  assert.notEqual(
    ninth.recordSha256,
    recordHash(ninth, compareLegacyAsciiCaseFolded),
  )
  assert.equal(
    inspectCandidateActivityLedger({
      repositoryRoot,
      policy: currentPolicy(FROZEN_CANDIDATE_RESOURCE_SHA256),
    }).recordCount,
    9,
  )

  const legacyNinthSha256 = recordHash(
    ninth,
    compareLegacyAsciiCaseFolded,
  )
  ninth.recordSha256 = legacyNinthSha256
  writeFileSync(
    ledgerPath,
    `${records.map((record) => JSON.stringify(record)).join('\n')}\n`,
    'utf8',
  )
  const anchor = JSON.parse(
    readFileSync(anchorPath, 'utf8'),
  ) as Record<string, unknown>
  anchor.lastRecordSha256 = legacyNinthSha256
  writeFileSync(anchorPath, `${JSON.stringify(anchor)}\n`, 'utf8')

  assert.throws(
    () =>
      inspectCandidateActivityLedger({
        repositoryRoot,
        policy: currentPolicy(FROZEN_CANDIDATE_RESOURCE_SHA256),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
})

test('旧八条前缀后的 sequence 9 不能继续使用旧 5.7835008 预留', () => {
  const repositoryRoot = root('frozen-legacy-prefix-old-reserve')
  const { ledgerPath, anchorPath } =
    seedFrozenLegacyPrefix(repositoryRoot)
  const ledger = openCandidateActivityLedger({
    repositoryRoot,
    policy: currentPolicy(FROZEN_CANDIDATE_RESOURCE_SHA256),
  })
  ledger.reserve(1, new Date('2026-07-29T12:56:00.000Z'))
  ledger.close()

  const records = readFileSync(ledgerPath, 'utf8')
    .trim()
    .split('\n')
    .map(
      (line) => JSON.parse(line) as Record<string, unknown>,
    )
  const ninth = records[8]!
  ninth.reservedCostCny = 5.7835008
  ninth.recordSha256 = recordHash(ninth, compareCodePoints)
  writeFileSync(
    ledgerPath,
    `${records.map((record) => JSON.stringify(record)).join('\n')}\n`,
    'utf8',
  )
  const anchor = JSON.parse(
    readFileSync(anchorPath, 'utf8'),
  ) as Record<string, unknown>
  anchor.lastRecordSha256 = ninth.recordSha256
  writeFileSync(anchorPath, `${JSON.stringify(anchor)}\n`, 'utf8')

  assert.throws(
    () =>
      inspectCandidateActivityLedger({
        repositoryRoot,
        policy: currentPolicy(FROZEN_CANDIDATE_RESOURCE_SHA256),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
})

test('零工具候选可只读续接此前 0.5419008 历史预留且新预留使用更低包络', () => {
  const repositoryRoot = root('zero-tool-policy-transition')
  seedFrozenLegacyPrefix(repositoryRoot)
  const previous = openCandidateActivityLedger({
    repositoryRoot,
    policy: currentPolicy(FROZEN_CANDIDATE_RESOURCE_SHA256),
  })
  const previousReservation = previous.reserve(
    1,
    new Date('2026-08-05T12:16:08.212Z'),
  )
  previous.settle(previousReservation, {
    outcome: 'rejected',
    recordedAt: new Date('2026-08-05T12:16:45.181Z'),
    formalRejectionCode: 'provider_request_limit_exceeded',
  })
  previous.close()

  const nextPolicy = zeroToolPolicy()
  const before = inspectCandidateActivityLedger({
    repositoryRoot,
    policy: nextPolicy,
  })
  assert.equal(before.recordCount, 10)
  assert.equal(before.openReservations, 0)
  assert.ok(
    Math.abs(before.committedCostCny - 12.26466976) < 1e-12,
  )

  const current = openCandidateActivityLedger({
    repositoryRoot,
    policy: nextPolicy,
  })
  const currentReservation = current.reserve(
    1,
    new Date('2026-08-05T13:20:00.000Z'),
  )
  assert.equal(currentReservation.reservedCostCny, 0.21676032)
  current.close()
})

test('候选切换时未知历史候选预算不能借哈希链自报成本', () => {
  const repositoryRoot = root('unknown-policy-transition')
  const oldPolicy = currentPolicy('c'.repeat(64))
  const oldLedger = initializeLedger(repositoryRoot, oldPolicy)
  oldLedger.reserve(1, new Date('2026-08-05T12:00:00.000Z'))
  oldLedger.close()

  assert.throws(
    () =>
      inspectCandidateActivityLedger({
        repositoryRoot,
        policy: zeroToolPolicy(),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
})

test('新建账本 writer 从第一条开始只使用 code-point hash', () => {
  const repositoryRoot = root('code-point-writer')
  const ledger = initializeLedger(repositoryRoot)
  const ledgerPath = ledger.path
  ledger.close()
  const record = JSON.parse(
    readFileSync(ledgerPath, 'utf8').trim(),
  ) as Record<string, unknown>

  assert.equal(
    record.recordSha256,
    recordHash(record, compareCodePoints),
  )
  assert.notEqual(
    record.recordSha256,
    recordHash(record, compareLegacyAsciiCaseFolded),
  )
})

test('首个既有失败补记明确标为 pre-ledger 且不伪装成当前 provider run', () => {
  const repositoryRoot = root('reconciliation')
  const first = initializePreLedger(repositoryRoot)
  const firstSnapshot = first.snapshot()
  const ledgerPath = first.path
  first.close()

  assert.equal(firstSnapshot.recordCount, 1)
  assert.equal(
    firstSnapshot.committedCostCny,
    COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
  )
  assert.equal(firstSnapshot.historicalUsageStatus, 'unresolved')
  const text = readFileSync(ledgerPath, 'utf8')
  const record = JSON.parse(text.trim()) as Record<string, unknown>
  assert.equal(record.recordType, 'pre_ledger_reconciliation')
  assert.equal(record.attemptedAt, null)
  assert.equal(record.providerRunInitiatedAtReconciliation, false)
  assert.equal(
    record.reconciliationReason,
    'pre_ledger_failed_provider_run_usage_unavailable',
  )
  assert.equal(
    record.costBasis,
    'worst_case_single_report_reservation',
  )
  assert.equal(
    record.candidateRejectionCode,
    'candidate_runner_failed',
  )
  assert.equal(record.formalRejectionCode, null)
  assert.equal(record.usage, null)

  // 20 CNY 是同一 A4 候选活动的总预算；资源摘要更新不能把既有费用清零。
  const reopened = openCandidateActivityLedger({
    repositoryRoot,
    policy: policy('b'.repeat(64)),
  })
  assert.equal(
    reopened.snapshot().committedCostCny,
    firstSnapshot.committedCostCny,
  )
  assert.throws(
    () => reopened.assertCertificationBudgetAvailable(),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_historical_usage_unresolved',
  )
  reopened.close()
})

test('历史实际用量只能显式结清一次且结清后才允许未来 5.7835008 预留', () => {
  const repositoryRoot = root('historical-settlement')
  const legacy = initializePreLedger(repositoryRoot)
  const ledgerPath = legacy.path
  legacy.close()

  const reconciled =
    reconcileCandidateActivityLedgerHistoricalUsage({
      repositoryRoot,
      policy: policy(),
      recordedAt: new Date('2026-07-29T04:00:30Z'),
      evidenceSha256: 'e'.repeat(64),
      usage: {
        providerRequestCount: 1,
        inputTokens: 64_000,
        outputTokens: 16_384,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
      },
    })
  assert.equal(
    reconciled.snapshot().committedCostCny,
    COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
  )
  assert.equal(reconciled.snapshot().historicalUsageStatus, 'resolved')
  assert.equal(reconciled.snapshot().recordCount, 2)
  const reservation = reconciled.reserve(
    1,
    new Date('2026-07-29T04:00:31Z'),
  )
  assert.equal(reservation.reservedCostCny, 5.7835008)
  reconciled.close()

  const records = readFileSync(ledgerPath, 'utf8')
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line) as Record<string, unknown>)
  assert.equal(
    records[1]?.recordType,
    'pre_ledger_historical_settlement',
  )
  assert.equal(
    records[1]?.evidenceDescription,
    'pre_ledger_failed_run_usage_evidence_v1',
  )
  assert.equal(records[1]?.evidenceSha256, 'e'.repeat(64))

  assert.throws(
    () =>
      reconcileCandidateActivityLedgerHistoricalUsage({
        repositoryRoot,
        policy: policy(),
        recordedAt: new Date('2026-07-29T04:00:32Z'),
        evidenceSha256: 'f'.repeat(64),
        usage: {
          providerRequestCount: 1,
          inputTokens: 64_000,
          outputTokens: 16_384,
          cacheReadTokens: 0,
          cacheWriteTokens: 0,
        },
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_reservation_invalid',
  )
})

test('历史实际成本低于已记 0.10838016 时拒绝且 ledger 与 anchor 均不变化', () => {
  const repositoryRoot = root('historical-undercharge')
  const legacy = initializePreLedger(repositoryRoot)
  const ledgerPath = legacy.path
  const anchorPath = legacy.anchorPath
  const ledgerBefore = readFileSync(ledgerPath, 'utf8')
  const anchorBefore = readFileSync(anchorPath, 'utf8')
  legacy.close()

  assert.throws(
    () =>
      reconcileCandidateActivityLedgerHistoricalUsage({
        repositoryRoot,
        policy: policy(),
        recordedAt: new Date('2026-07-29T04:00:40Z'),
        evidenceSha256: 'e'.repeat(64),
        usage: {
          providerRequestCount: 1,
          inputTokens: 1,
          outputTokens: 1,
          cacheReadTokens: 0,
          cacheWriteTokens: 0,
        },
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_reservation_invalid',
  )
  assert.equal(readFileSync(ledgerPath, 'utf8'), ledgerBefore)
  assert.equal(readFileSync(anchorPath, 'utf8'), anchorBefore)
})

test('最终 CNY 实扣低于 legacy floor 时仍完成结清并保留 0.10838016', () => {
  const repositoryRoot = root('historical-billed-cny-floor')
  const legacy = initializePreLedger(repositoryRoot)
  const ledgerPath = legacy.path
  legacy.close()

  const reconciled =
    reconcileCandidateActivityLedgerHistoricalBilledAmount({
      repositoryRoot,
      policy: policy(),
      recordedAt: new Date('2026-07-29T04:00:30Z'),
      billedAmount: historicalBilledAmount({
        billedAmountDecimal: '0.05',
      }),
    })
  assert.equal(reconciled.snapshot().historicalUsageStatus, 'resolved')
  assert.equal(
    reconciled.snapshot().committedCostCny,
    COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
  )
  reconciled.close()

  const records = readFileSync(ledgerPath, 'utf8')
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line) as Record<string, unknown>)
  assert.equal(
    records[1]?.recordType,
    'pre_ledger_historical_billed_amount_settlement',
  )
  assert.equal(
    records[1]?.evidenceDescription,
    'pre_ledger_failed_run_billing_evidence_v1',
  )
  assert.equal(records[1]?.billedAmountDecimal, '0.05')
  assert.equal(records[1]?.billedCurrency, 'CNY')
  assert.equal(records[1]?.conversionBasis, 'identity_cny')
  assert.equal(records[1]?.conversionRateCnyPerUnitDecimal, '1')
  assert.equal(records[1]?.convertedBilledCostCnyE8, 5_000_000)
  assert.equal(records[1]?.chargedCostCnyE8, 10_838_016)
  assert.equal(records[1]?.chargedCostCny, 0.10838016)
  assert.equal(records[1]?.adjustmentCostCny, 0)
  assert.equal(records[1]?.usage, null)
})

test('USD 实扣只按冻结 8 CNY/USD 换算并向上取整到 CNY E8', () => {
  const repositoryRoot = root('historical-billed-usd-ceil')
  const legacy = initializePreLedger(repositoryRoot)
  const ledgerPath = legacy.path
  legacy.close()

  const reconciled =
    reconcileCandidateActivityLedgerHistoricalBilledAmount({
      repositoryRoot,
      policy: policy(),
      recordedAt: new Date('2026-07-29T04:00:30Z'),
      billedAmount: historicalBilledAmount({
        billedAmountDecimal: '0.013547521',
        billedCurrency: 'USD',
      }),
    })
  assert.equal(reconciled.snapshot().committedCostCny, 0.10838017)
  reconciled.close()

  const record = JSON.parse(
    readFileSync(ledgerPath, 'utf8').trim().split('\n')[1]!,
  ) as Record<string, unknown>
  assert.equal(
    record.conversionBasis,
    'frozen_conservative_cny_per_usd',
  )
  assert.equal(record.conversionRateCnyPerUnitDecimal, '8')
  assert.equal(record.convertedBilledCostCnyE8, 10_838_017)
  assert.equal(record.chargedCostCnyE8, 10_838_017)
  assert.equal(record.chargedCostCny, 0.10838017)
})

test('高额历史实扣如实结清为 resolved，但由双报告 20 CNY preflight 阻断', () => {
  const repositoryRoot = root('historical-billed-over-preflight')
  const legacy = initializePreLedger(repositoryRoot)
  legacy.close()

  const reconciled =
    reconcileCandidateActivityLedgerHistoricalBilledAmount({
      repositoryRoot,
      policy: policy(),
      recordedAt: new Date('2026-07-29T04:00:30Z'),
      billedAmount: historicalBilledAmount({
        billedAmountDecimal: '9',
      }),
    })
  assert.equal(reconciled.snapshot().historicalUsageStatus, 'resolved')
  assert.equal(reconciled.snapshot().committedCostCny, 9)
  assert.throws(
    () => reconciled.assertCertificationBudgetAvailable(),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_budget_preflight_failed',
  )
  reconciled.close()
})

test('历史实扣 8.4329984 可预留双报告，高一个 CNY E8 单位即失败', () => {
  for (const [label, amount, shouldPass] of [
    ['boundary', '8.4329984', true],
    ['over-one-e8', '8.43299841', false],
  ] as const) {
    const repositoryRoot = root(`historical-billed-${label}`)
    const legacy = initializePreLedger(repositoryRoot)
    legacy.close()
    const reconciled =
      reconcileCandidateActivityLedgerHistoricalBilledAmount({
        repositoryRoot,
        policy: policy(),
        recordedAt: new Date('2026-07-29T04:00:30Z'),
        billedAmount: historicalBilledAmount({
          billedAmountDecimal: amount,
        }),
      })
    const ledgerPath = reconciled.path
    if (shouldPass) {
      assert.doesNotThrow(() =>
        reconciled.assertCertificationBudgetAvailable(),
      )
    } else {
      assert.throws(
        () => reconciled.assertCertificationBudgetAvailable(),
        (error: unknown) =>
          error instanceof CandidateActivityLedgerError &&
          error.code === 'activity_budget_preflight_failed',
      )
    }
    reconciled.close()
    const record = JSON.parse(
      readFileSync(ledgerPath, 'utf8').trim().split('\n')[1]!,
    ) as Record<string, unknown>
    assert.equal(
      record.chargedCostCnyE8,
      shouldPass ? 843_299_840 : 843_299_841,
    )
  }
})

test('相同最终账单重复结清幂等，不同证据或金额拒绝且账本与 anchor 不变', () => {
  const repositoryRoot = root('historical-billed-idempotent')
  const legacy = initializePreLedger(repositoryRoot)
  const ledgerPath = legacy.path
  const anchorPath = legacy.anchorPath
  legacy.close()
  const billedAmount = historicalBilledAmount({
    billedAmountDecimal: '1.0',
  })

  const first =
    reconcileCandidateActivityLedgerHistoricalBilledAmount({
      repositoryRoot,
      policy: policy(),
      recordedAt: new Date('2026-07-29T04:00:30Z'),
      billedAmount,
    })
  first.close()
  const ledgerAfterFirst = readFileSync(ledgerPath, 'utf8')
  const anchorAfterFirst = readFileSync(anchorPath, 'utf8')
  assert.equal(
    (
      JSON.parse(
        ledgerAfterFirst.trim().split('\n')[1]!,
      ) as Record<string, unknown>
    ).billedAmountDecimal,
    '1',
  )

  const duplicate =
    reconcileCandidateActivityLedgerHistoricalBilledAmount({
      repositoryRoot,
      policy: policy(),
      recordedAt: new Date('2026-07-29T04:01:00Z'),
      billedAmount: historicalBilledAmount({
        billedAmountDecimal: '1.00',
      }),
    })
  assert.equal(duplicate.snapshot().recordCount, 2)
  assert.equal(duplicate.snapshot().committedCostCny, 1)
  duplicate.close()
  assert.equal(readFileSync(ledgerPath, 'utf8'), ledgerAfterFirst)
  assert.equal(readFileSync(anchorPath, 'utf8'), anchorAfterFirst)

  for (const changed of [
    historicalBilledAmount({
      evidenceSha256: 'e'.repeat(64),
      billedAmountDecimal: '1.00',
    }),
    historicalBilledAmount({
      billedAmountDecimal: '1.00000001',
    }),
  ]) {
    assert.throws(
      () =>
        reconcileCandidateActivityLedgerHistoricalBilledAmount({
          repositoryRoot,
          policy: policy(),
          recordedAt: new Date('2026-07-29T04:01:30Z'),
          billedAmount: changed,
        }),
      (error: unknown) =>
        error instanceof CandidateActivityLedgerError &&
        error.code === 'activity_reservation_invalid',
    )
    assert.equal(readFileSync(ledgerPath, 'utf8'), ledgerAfterFirst)
    assert.equal(readFileSync(anchorPath, 'utf8'), anchorAfterFirst)
  }
})

test('金额、币种、覆盖窗口、时区、最终性和取得时间非法时失败关闭且不写账', () => {
  const invalidCases: Array<
    [string, CandidateActivityHistoricalBilledAmount]
  > = [
    [
      'unsupported-currency',
      historicalBilledAmount({
        billedCurrency: 'EUR' as 'CNY',
      }),
    ],
    [
      'scientific-amount',
      historicalBilledAmount({ billedAmountDecimal: '1e-3' }),
    ],
    [
      'comma-amount',
      historicalBilledAmount({ billedAmountDecimal: '1,00' }),
    ],
    [
      'window-starts-late',
      historicalBilledAmount({
        evidenceWindowStartUtc: '2026-07-29T03:18:49.000Z',
      }),
    ],
    [
      'window-ends-early',
      historicalBilledAmount({
        evidenceWindowEndUtc: '2026-07-29T03:19:24.000Z',
      }),
    ],
    [
      'unknown-timezone',
      historicalBilledAmount({
        evidenceTimezone: 'Asia/Tokyo' as 'UTC',
      }),
    ],
    [
      'not-final',
      historicalBilledAmount({
        billingFinality: 'pending' as 'settled_final',
      }),
    ],
    [
      'acquired-before-window-end',
      historicalBilledAmount({
        evidenceAcquiredAt: '2026-07-29T03:19:24.500Z',
      }),
    ],
    [
      'settlement-recorded-before-acquired',
      historicalBilledAmount({
        evidenceAcquiredAt: '2026-07-29T04:00:31.000Z',
      }),
    ],
  ]
  for (const [label, billedAmount] of invalidCases) {
    const repositoryRoot = root(`historical-billed-invalid-${label}`)
    const legacy = initializePreLedger(repositoryRoot)
    const ledgerPath = legacy.path
    const anchorPath = legacy.anchorPath
    const ledgerBefore = readFileSync(ledgerPath, 'utf8')
    const anchorBefore = readFileSync(anchorPath, 'utf8')
    legacy.close()
    assert.throws(
      () =>
        reconcileCandidateActivityLedgerHistoricalBilledAmount({
          repositoryRoot,
          policy: policy(),
          recordedAt: new Date('2026-07-29T04:00:30Z'),
          billedAmount,
        }),
      (error: unknown) =>
        error instanceof CandidateActivityLedgerError &&
        error.code === 'activity_reservation_invalid',
    )
    assert.equal(readFileSync(ledgerPath, 'utf8'), ledgerBefore)
    assert.equal(readFileSync(anchorPath, 'utf8'), anchorBefore)
  }
})

test('金额结清拒绝非固定 DeepSeek provider/model 或非冻结 8 CNY/USD 政策', () => {
  for (const [label, changedPolicy] of [
    ['provider', { ...policy(), provider: 'other-provider' }],
    ['model', { ...policy(), model: 'other-model' }],
    [
      'conversion-policy',
      { ...policy(), conservativeCnyPerUsd: 7.9 },
    ],
  ] as const) {
    const repositoryRoot = root(
      `historical-billed-policy-${label}`,
    )
    const legacy = initializePreLedger(repositoryRoot, changedPolicy)
    const ledgerPath = legacy.path
    const anchorPath = legacy.anchorPath
    const ledgerBefore = readFileSync(ledgerPath, 'utf8')
    const anchorBefore = readFileSync(anchorPath, 'utf8')
    legacy.close()
    assert.throws(
      () =>
        reconcileCandidateActivityLedgerHistoricalBilledAmount({
          repositoryRoot,
          policy: changedPolicy,
          recordedAt: new Date('2026-07-29T04:00:30Z'),
          billedAmount: historicalBilledAmount(),
        }),
      (error: unknown) =>
        error instanceof CandidateActivityLedgerError &&
        error.code === 'activity_reservation_invalid',
    )
    assert.equal(readFileSync(ledgerPath, 'utf8'), ledgerBefore)
    assert.equal(readFileSync(anchorPath, 'utf8'), anchorBefore)
  }
})

test('clean genesis 与历史失败迁移不能互换或重复执行', () => {
  const cleanRoot = root('clean-not-preledger')
  const clean = initializeLedger(cleanRoot)
  const cleanText = readFileSync(clean.path, 'utf8')
  const cleanPath = clean.path
  clean.close()
  assert.throws(
    () => initializePreLedger(cleanRoot),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
  assert.equal(readFileSync(cleanPath, 'utf8'), cleanText)

  const legacyRoot = root('preledger-not-clean')
  const legacy = initializePreLedger(legacyRoot)
  const legacyText = readFileSync(legacy.path, 'utf8')
  const legacyPath = legacy.path
  legacy.close()
  assert.throws(
    () => initializeLedger(legacyRoot),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
  assert.equal(readFileSync(legacyPath, 'utf8'), legacyText)
})

test('两个未来失败均按最坏预留结算后，下一次双报告认证在 20 CNY 前置门失败', () => {
  const repositoryRoot = root('multiple-failed-attempts')
  const ledger = initializeLedger(repositoryRoot)
  for (const runNumber of [1, 2] as const) {
    const reservation = ledger.reserve(
      runNumber,
      new Date(`2026-07-29T04:0${runNumber}:00Z`),
    )
    ledger.settle(reservation, {
      outcome: 'rejected',
      recordedAt: new Date(
        `2026-07-29T04:0${runNumber}:30Z`,
      ),
      formalRejectionCode: 'provider_call_failed',
    })
  }
  assert.equal(ledger.snapshot().committedCostCny, 11.5670016)
  assert.throws(
    () => ledger.assertCertificationBudgetAvailable(),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_budget_preflight_failed',
  )
  ledger.close()
})

test('失败 usage 超过预留时按实际保守成本结算并让后续预检失败关闭', () => {
  const repositoryRoot = root('over-reservation')
  const ledger = initializeLedger(repositoryRoot)
  const reservation = ledger.reserve(
    1,
    new Date('2026-07-29T04:01:00Z'),
  )
  ledger.settle(reservation, {
    outcome: 'rejected',
    recordedAt: new Date('2026-07-29T04:02:00Z'),
    candidateRejectionCode: 'candidate_run_evidence_invalid',
    usage: {
      providerRequestCount: 1,
      inputTokens: 0,
      outputTokens: 9_000_000,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    },
  })
  const snapshot = ledger.snapshot()
  const ledgerPath = ledger.path
  assert.ok(
    Math.abs(
      snapshot.committedCostCny -
        20.16,
    ) < 1e-12,
  )
  assert.throws(
    () => ledger.assertCertificationBudgetAvailable(),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_budget_preflight_failed',
  )
  ledger.close()

  const records = readFileSync(ledgerPath, 'utf8')
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line) as Record<string, unknown>)
  assert.equal(records.length, 3)
  assert.equal(records[2]?.costBasis, 'actual_usage')
  assert.ok(
    Math.abs(Number(records[2]?.chargedCostCny) - 20.16) < 1e-12,
  )
  assert.equal(
    records[2]?.candidateRejectionCode,
    'candidate_run_evidence_invalid',
  )
})

test('活动账本目录固定为 0700 且文件固定为 0600', () => {
  const repositoryRoot = root('permissions')
  const ledger = initializeLedger(repositoryRoot)
  const ledgerPath = ledger.path
  assert.equal(lstatSync(dirname(ledgerPath)).mode & 0o777, 0o700)
  assert.equal(lstatSync(ledgerPath).mode & 0o777, 0o600)
  assert.equal(lstatSync(ledger.anchorPath).mode & 0o777, 0o600)
  ledger.close()
})

test('第二打开者失败为 busy 且不能删除第一打开者持有的锁', () => {
  const repositoryRoot = root('concurrent-lock')
  const first = initializeLedger(repositoryRoot)
  const lockPath = join(
    dirname(first.path),
    '.deepseek-v4-flash-pi-0.82.1-v1-activity-v1.lock',
  )
  assert.equal(existsSync(lockPath), true)
  assert.throws(
    () =>
      openCandidateActivityLedger({
        repositoryRoot,
        policy: policy(),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_busy',
  )
  assert.equal(existsSync(lockPath), true)
  first.close()
  assert.equal(existsSync(lockPath), false)
})

test('未结 reservation 在关闭重开后仍按最坏成本计入且保持 open', () => {
  const repositoryRoot = root('open-reservation')
  const first = initializeLedger(repositoryRoot)
  first.reserve(1, new Date('2026-07-29T04:03:00Z'))
  first.close()

  const reopened = openCandidateActivityLedger({
    repositoryRoot,
    policy: policy(),
  })
  const snapshot = reopened.snapshot()
  assert.equal(snapshot.openReservations, 1)
  assert.equal(snapshot.recordCount, 2)
  assert.ok(
    Math.abs(
      snapshot.committedCostCny -
        policy().maximumSingleReportCostCny,
    ) < 1e-12,
  )
  reopened.close()
})

test('rejected settlement 必须至少携带一个固定拒绝码', () => {
  const repositoryRoot = root('rejected-requires-code')
  const ledger = initializeLedger(repositoryRoot)
  const reservation = ledger.reserve(
    1,
    new Date('2026-07-29T04:03:30Z'),
  )

  assert.throws(
    () =>
      ledger.settle(reservation, {
        outcome: 'rejected',
        recordedAt: new Date('2026-07-29T04:03:31Z'),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_reservation_invalid',
  )
  assert.equal(ledger.snapshot().recordCount, 2)
  assert.equal(ledger.snapshot().openReservations, 1)
  ledger.close()
})

test('历史行 previous hash 被篡改后账本失败关闭', () => {
  const repositoryRoot = root('hash-tamper')
  const first = initializeLedger(repositoryRoot)
  const reservation = first.reserve(
    1,
    new Date('2026-07-29T04:04:00Z'),
  )
  first.settle(reservation, {
    outcome: 'rejected',
    recordedAt: new Date('2026-07-29T04:05:00Z'),
    formalRejectionCode: 'provider_call_failed',
  })
  const ledgerPath = first.path
  first.close()

  const records = readFileSync(ledgerPath, 'utf8')
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line) as Record<string, unknown>)
  records[1]!.previousRecordSha256 = '0'.repeat(64)
  writeFileSync(
    ledgerPath,
    `${records.map((record) => JSON.stringify(record)).join('\n')}\n`,
    'utf8',
  )
  assert.throws(
    () =>
      openCandidateActivityLedger({
        repositoryRoot,
        policy: policy(),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
})

test('既有账本文件权限过宽时失败关闭而不自动收窄掩盖风险', () => {
  const repositoryRoot = root('wide-permissions')
  const first = initializeLedger(repositoryRoot)
  const ledgerPath = first.path
  first.close()
  chmodSync(ledgerPath, 0o644)

  assert.throws(
    () =>
      openCandidateActivityLedger({
        repositoryRoot,
        policy: policy(),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
  assert.equal(lstatSync(ledgerPath).mode & 0o777, 0o644)
})

test('普通打开不创建缺失的 ledger 或 anchor', () => {
  const repositoryRoot = root('formal-open-uninitialized')
  assert.throws(
    () =>
      openCandidateActivityLedger({
        repositoryRoot,
        policy: policy(),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
  assert.equal(
    existsSync(
      join(
        repositoryRoot,
        'var/country-outage-agent/a4-model-certification-activity',
      ),
    ),
    false,
  )
})

test('clean genesis 初始化只能执行一次且保持零成本', () => {
  const repositoryRoot = root('unique-initialization')
  const first = initializeLedger(repositoryRoot)
  const ledgerPath = first.path
  const anchorPath = first.anchorPath
  const ledgerBefore = readFileSync(ledgerPath, 'utf8')
  const anchorBefore = readFileSync(anchorPath, 'utf8')
  first.close()

  assert.throws(
    () => initializeLedger(repositoryRoot),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
  assert.equal(readFileSync(ledgerPath, 'utf8'), ledgerBefore)
  assert.equal(readFileSync(anchorPath, 'utf8'), anchorBefore)
})

test('reconcile 可为唯一一条合法 legacy pre-ledger 补 anchor 且不重复收费', () => {
  const repositoryRoot = root('legacy-anchor-migration')
  const first = initializePreLedger(repositoryRoot)
  const ledgerPath = first.path
  const anchorPath = first.anchorPath
  const ledgerBefore = readFileSync(ledgerPath, 'utf8')
  first.close()
  rmSync(anchorPath)

  const migrated =
    initializeCandidateActivityLedgerWithPreLedgerFailure({
      repositoryRoot,
      policy: policy(),
      recordedAt: new Date('2026-07-29T05:00:00Z'),
      formalRejectionCode: 'provider_call_failed',
    })
  assert.equal(readFileSync(ledgerPath, 'utf8'), ledgerBefore)
  assert.equal(migrated.snapshot().recordCount, 1)
  assert.equal(
    migrated.snapshot().committedCostCny,
    COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
  )
  assert.equal(existsSync(anchorPath), true)
  migrated.close()

  const reopened = openCandidateActivityLedger({
    repositoryRoot,
    policy: policy(),
  })
  assert.equal(reopened.snapshot().recordCount, 1)
  reopened.close()
})

test('ledger 整文件删除后即使 anchor 尚在也失败关闭', () => {
  const repositoryRoot = root('ledger-deleted')
  const ledger = initializeLedger(repositoryRoot)
  const ledgerPath = ledger.path
  const anchorPath = ledger.anchorPath
  ledger.close()
  rmSync(ledgerPath)

  assert.equal(existsSync(anchorPath), true)
  assert.throws(
    () =>
      openCandidateActivityLedger({
        repositoryRoot,
        policy: policy(),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
})

test('ledger 被清空后失败关闭', () => {
  const repositoryRoot = root('ledger-empty')
  const ledger = initializeLedger(repositoryRoot)
  const ledgerPath = ledger.path
  ledger.close()
  writeFileSync(ledgerPath, '', 'utf8')

  assert.throws(
    () =>
      openCandidateActivityLedger({
        repositoryRoot,
        policy: policy(),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
})

test('ledger 尾部字节截断后失败关闭', () => {
  const repositoryRoot = root('ledger-tail-truncated')
  const ledger = initializeLedger(repositoryRoot)
  const ledgerPath = ledger.path
  ledger.close()
  const complete = readFileSync(ledgerPath, 'utf8')
  writeFileSync(ledgerPath, complete.slice(0, -10), 'utf8')

  assert.throws(
    () =>
      openCandidateActivityLedger({
        repositoryRoot,
        policy: policy(),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
})

test('ledger 回退到旧合法前缀时因 tail anchor 不匹配而失败关闭', () => {
  const repositoryRoot = root('ledger-old-prefix')
  const first = initializeLedger(repositoryRoot)
  const ledgerPath = first.path
  const oldPrefix = readFileSync(ledgerPath, 'utf8')
  const reservation = first.reserve(
    1,
    new Date('2026-07-29T04:06:00Z'),
  )
  first.settle(reservation, {
    outcome: 'rejected',
    recordedAt: new Date('2026-07-29T04:07:00Z'),
    formalRejectionCode: 'provider_call_failed',
  })
  first.close()
  writeFileSync(ledgerPath, oldPrefix, 'utf8')

  assert.throws(
    () =>
      openCandidateActivityLedger({
        repositoryRoot,
        policy: policy(),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
})

test('anchor 删除后普通打开失败且不会自动重建', () => {
  const repositoryRoot = root('anchor-deleted')
  const ledger = initializeLedger(repositoryRoot)
  const anchorPath = ledger.anchorPath
  ledger.close()
  rmSync(anchorPath)

  assert.throws(
    () =>
      openCandidateActivityLedger({
        repositoryRoot,
        policy: policy(),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
  assert.equal(existsSync(anchorPath), false)
})

test('reconcile 将 broken symlink 视为非法 anchor 而不是缺失状态', () => {
  const repositoryRoot = root('anchor-broken-symlink')
  const ledger = initializeLedger(repositoryRoot)
  const anchorPath = ledger.anchorPath
  ledger.close()
  rmSync(anchorPath)
  symlinkSync('missing-anchor-target', anchorPath)

  assert.throws(
    () => initializeLedger(repositoryRoot),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
  assert.equal(lstatSync(anchorPath).isSymbolicLink(), true)
})

test('anchor 回退时即使 ledger 是有效新尾部也失败关闭', () => {
  const repositoryRoot = root('anchor-rollback')
  const first = initializeLedger(repositoryRoot)
  const anchorPath = first.anchorPath
  const oldAnchor = readFileSync(anchorPath, 'utf8')
  const reservation = first.reserve(
    1,
    new Date('2026-07-29T04:08:00Z'),
  )
  first.settle(reservation, {
    outcome: 'rejected',
    recordedAt: new Date('2026-07-29T04:09:00Z'),
    formalRejectionCode: 'provider_call_failed',
  })
  first.close()
  writeFileSync(anchorPath, oldAnchor, 'utf8')

  assert.throws(
    () =>
      openCandidateActivityLedger({
        repositoryRoot,
        policy: policy(),
      }),
    (error: unknown) =>
      error instanceof CandidateActivityLedgerError &&
      error.code === 'activity_ledger_invalid',
  )
})
