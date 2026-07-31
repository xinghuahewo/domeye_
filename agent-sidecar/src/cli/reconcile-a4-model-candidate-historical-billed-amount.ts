import {
  PiModelCertificationError,
  reconcileA4PreLedgerHistoricalBilledAmount,
  type CandidateActivityBilledCurrency,
  type CandidateActivityBillingEvidenceTimezone,
  type CandidateActivityBillingScope,
} from '../pi/index.js'

const SHA256 = /^[a-f0-9]{64}$/
const CANONICAL_DECIMAL =
  /^(?:0|[1-9]\d{0,14})(?:\.\d{1,18})?$/
const EVIDENCE_TIMESTAMP =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?(Z|\+08:00)$/
const SHANGHAI_OFFSET_MS = 8 * 60 * 60 * 1000

class HistoricalBilledAmountConfigurationError extends Error {
  constructor() {
    super('DeepSeek 候选认证历史账单金额结清配置无效')
    this.name = 'HistoricalBilledAmountConfigurationError'
  }
}

function required(name: string): string {
  const value = process.env[name]?.trim()
  if (!value) throw new HistoricalBilledAmountConfigurationError()
  return value
}

function requiredEnum<T extends string>(
  name: string,
  accepted: readonly T[],
): T {
  const value = required(name)
  if (!accepted.includes(value as T)) {
    throw new HistoricalBilledAmountConfigurationError()
  }
  return value as T
}

function normalizedEvidenceTimestamp(
  name: string,
  timezone: CandidateActivityBillingEvidenceTimezone,
): string {
  const value = required(name)
  const match = EVIDENCE_TIMESTAMP.exec(value)
  const requiredSuffix =
    timezone === 'UTC' ? 'Z' : '+08:00'
  if (!match || match[8] !== requiredSuffix) {
    throw new HistoricalBilledAmountConfigurationError()
  }
  const milliseconds = (match[7] ?? '').padEnd(3, '0')
  const canonical = `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6]}.${milliseconds}${requiredSuffix}`
  const parsed = new Date(canonical)
  if (!Number.isFinite(parsed.valueOf())) {
    throw new HistoricalBilledAmountConfigurationError()
  }
  const canonicalRoundTrip =
    timezone === 'UTC'
      ? parsed.toISOString()
      : new Date(
          parsed.valueOf() + SHANGHAI_OFFSET_MS,
        )
          .toISOString()
          .replace(/Z$/, '+08:00')
  if (canonicalRoundTrip !== canonical) {
    throw new HistoricalBilledAmountConfigurationError()
  }
  return parsed.toISOString()
}

async function main(): Promise<void> {
  if (process.argv.length !== 2) {
    throw new HistoricalBilledAmountConfigurationError()
  }
  const evidenceSha256 = required(
    'COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_EVIDENCE_SHA256',
  )
  if (!SHA256.test(evidenceSha256)) {
    throw new HistoricalBilledAmountConfigurationError()
  }
  const billedAmountDecimal = required(
    'COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLED_AMOUNT_DECIMAL',
  )
  if (!CANONICAL_DECIMAL.test(billedAmountDecimal)) {
    throw new HistoricalBilledAmountConfigurationError()
  }
  const billedCurrency =
    requiredEnum<CandidateActivityBilledCurrency>(
      'COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLED_CURRENCY',
      ['CNY', 'USD'],
    )
  const billingScope = requiredEnum<CandidateActivityBillingScope>(
    'COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_SCOPE',
    [
      'single_attempt_exact_charge',
      'enclosing_account_window_upper_bound',
    ],
  )
  const billingFinality = requiredEnum(
    'COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_FINALITY',
    ['settled_final'] as const,
  )
  const evidenceTimezone =
    requiredEnum<CandidateActivityBillingEvidenceTimezone>(
      'COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_EVIDENCE_TIMEZONE',
      ['UTC', 'Asia/Shanghai'],
    )
  const evidenceWindowStartUtc = normalizedEvidenceTimestamp(
    'COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_EVIDENCE_WINDOW_START',
    evidenceTimezone,
  )
  const evidenceWindowEndUtc = normalizedEvidenceTimestamp(
    'COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_EVIDENCE_WINDOW_END',
    evidenceTimezone,
  )
  const evidenceAcquiredAt = normalizedEvidenceTimestamp(
    'COUNTRY_OUTAGE_PI_PRE_LEDGER_BILLING_EVIDENCE_ACQUIRED_AT',
    evidenceTimezone,
  )

  await reconcileA4PreLedgerHistoricalBilledAmount({
    billedAmount: {
      evidenceSha256,
      evidenceWindowStartUtc,
      evidenceWindowEndUtc,
      evidenceTimezone,
      evidenceAcquiredAt,
      billingFinality,
      billingScope,
      billedAmountDecimal,
      billedCurrency,
    },
  })
  process.stdout.write(
    'DeepSeek 候选认证首次历史调用最终账单金额已确认结清。\n',
  )
}

void main().catch((error: unknown) => {
  const message =
    error instanceof PiModelCertificationError ||
    error instanceof HistoricalBilledAmountConfigurationError
      ? error.message
      : 'DeepSeek 候选认证首次历史调用最终账单金额结清失败'
  process.stderr.write(`${message}\n`)
  process.exitCode = 1
})
