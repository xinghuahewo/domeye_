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
  writeSync,
  type Stats,
} from 'node:fs'
import { isAbsolute, resolve, sep } from 'node:path'

import {
  isFormalPiRunRejectionCode,
  type FormalPiRunRejectionCode,
} from './formal-run-audit.js'
import { compareUnicodeCodePoints } from '../shared/deterministic-json.js'

export const COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_SCHEMA =
  'country_outage_pi_model_candidate_activity_v1' as const
export const COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_RELATIVE_PATH =
  'var/country-outage-agent/a4-model-certification-activity/deepseek-v4-flash-pi-0.82.1-v1-activity-v1.jsonl' as const
export const COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_SCHEMA =
  'country_outage_pi_model_candidate_activity_anchor_v1' as const
export const COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_RELATIVE_PATH =
  'var/country-outage-agent/a4-model-certification-activity/deepseek-v4-flash-pi-0.82.1-v1-activity-anchor-v1.json' as const
export const COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY =
  0.10838016 as const
export const COUNTRY_OUTAGE_PRE_LEDGER_EVIDENCE_DESCRIPTION =
  'pre_ledger_failed_run_usage_evidence_v1' as const
export const COUNTRY_OUTAGE_PRE_LEDGER_BILLING_EVIDENCE_DESCRIPTION =
  'pre_ledger_failed_run_billing_evidence_v1' as const
export const COUNTRY_OUTAGE_PRE_LEDGER_HISTORICAL_ATTEMPT_STARTED_AT_UTC =
  '2026-07-29T03:18:48.543Z' as const
export const COUNTRY_OUTAGE_PRE_LEDGER_HISTORICAL_ATTEMPT_ENDED_AT_UTC =
  '2026-07-29T03:19:24.681Z' as const
export const COUNTRY_OUTAGE_PRE_LEDGER_BILLING_PROVIDER =
  'deepseek' as const
export const COUNTRY_OUTAGE_PRE_LEDGER_BILLING_MODEL =
  'deepseek-v4-flash' as const
export const CANDIDATE_ACTIVITY_REJECTION_CODES = Object.freeze([
  'candidate_runner_failed',
  'candidate_report_validation_failed',
  'candidate_response_model_missing',
  'candidate_response_model_mismatch',
  'candidate_run_evidence_invalid',
  'candidate_fixture_mismatch',
  'candidate_internal_audit_invalid',
  'candidate_artifact_write_failed',
  'candidate_budget_exceeded',
] as const)

export type CandidateActivityRejectionCode =
  (typeof CANDIDATE_ACTIVITY_REJECTION_CODES)[number]

const CANDIDATE_ACTIVITY_REJECTION_CODE_SET = new Set<string>(
  CANDIDATE_ACTIVITY_REJECTION_CODES,
)

const SHA256 = /^[a-f0-9]{64}$/
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/
const MAX_LEDGER_BYTES = 4 * 1024 * 1024
const MAX_ANCHOR_BYTES = 4 * 1024
const MAX_RECORD_BYTES = 8 * 1024
const MAX_RECORDS = 10_000
const COST_EPSILON = 1e-12
const FROZEN_LEGACY_ACTIVITY_PREFIX = Object.freeze({
  candidateId: 'deepseek-v4-flash-pi-0.82.1-v1',
  candidateResourceSha256:
    '1b8294f946f0bd9ad13ea874b2bf0da79a65adeb7a6713241eccfb2e3b6e6d41',
  recordCount: 8,
  firstRecordSha256:
    '08f21634ee5af8c2e8c90cff5f3830ba58a0ce0c23c7ef9d2b29bc2b731795fb',
  lastRecordSha256:
    '91f46b18bdbd4f77de3fccbdf901c09a863fd47dd98e5c6b68969f9ce4cd31b3',
  maximumSingleReportCostCny: 5.7835008,
})
const MILLION = 1_000_000
const CNY_E8_SCALE = 100_000_000n
const LEGACY_PRE_LEDGER_COST_CNY_E8 = 10_838_016n
const CONSERVATIVE_CNY_PER_USD = 8n
const MAXIMUM_DECIMAL_FRACTION_DIGITS = 18
const MAXIMUM_DECIMAL_INTEGER_DIGITS = 15

export interface CandidateActivityBudgetPolicy {
  candidateId: string
  candidateResourceSha256: string
  provider: string
  model: string
  budgetLimitCny: number
  maximumSingleReportCostCny: number
  maximumCertificationCostCny: number
  conservativeCnyPerUsd: number
  priceUsdPerMillionTokens: {
    input: number
    output: number
    cacheRead: number
    cacheWrite: number
  }
}

export interface CandidateActivityUsage {
  providerRequestCount: number
  inputTokens: number
  outputTokens: number
  cacheReadTokens: number
  cacheWriteTokens: number
}

export type CandidateActivityBilledCurrency = 'CNY' | 'USD'
export type CandidateActivityBillingEvidenceTimezone =
  | 'UTC'
  | 'Asia/Shanghai'
export type CandidateActivityBillingScope =
  | 'single_attempt_exact_charge'
  | 'enclosing_account_window_upper_bound'

export interface CandidateActivityHistoricalBilledAmount {
  evidenceSha256: string
  evidenceWindowStartUtc: string
  evidenceWindowEndUtc: string
  evidenceTimezone: CandidateActivityBillingEvidenceTimezone
  evidenceAcquiredAt: string
  billingFinality: 'settled_final'
  billingScope: CandidateActivityBillingScope
  billedAmountDecimal: string
  billedCurrency: CandidateActivityBilledCurrency
}

export interface CandidateActivityBudgetSnapshot {
  committedCostCny: number
  remainingBudgetCny: number
  openReservations: number
  recordCount: number
  historicalUsageStatus: 'unresolved' | 'resolved'
}

export type CandidateActivityRunNumber = 1 | 2 | 3 | 4 | 5

export interface CandidateActivityReservation {
  activityId: string
  runNumber: CandidateActivityRunNumber
  reservedCostCny: number
}

export type CandidateActivityLedgerErrorCode =
  | 'activity_ledger_invalid'
  | 'activity_ledger_busy'
  | 'activity_budget_preflight_failed'
  | 'activity_historical_usage_unresolved'
  | 'activity_reservation_invalid'

export class CandidateActivityLedgerError extends Error {
  constructor(readonly code: CandidateActivityLedgerErrorCode) {
    super(
      code === 'activity_ledger_busy'
        ? 'DeepSeek 候选认证活动账本正由另一个进程使用'
        : code === 'activity_budget_preflight_failed'
          ? 'DeepSeek 候选认证历史活动成本超过预算边界'
          : code === 'activity_historical_usage_unresolved'
            ? 'DeepSeek 候选认证首次历史调用用量尚未结清'
          : code === 'activity_reservation_invalid'
            ? 'DeepSeek 候选认证活动预算保留项无效'
            : 'DeepSeek 候选认证活动账本无效',
    )
    this.name = 'CandidateActivityLedgerError'
  }
}

interface CommonRecord {
  schemaVersion: typeof COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_SCHEMA
  sequence: number
  previousRecordSha256: string | null
  recordedAt: string
  recordType:
    | 'genesis'
    | 'pre_ledger_reconciliation'
    | 'pre_ledger_historical_settlement'
    | 'pre_ledger_historical_billed_amount_settlement'
    | 'reservation'
    | 'settlement'
  activityId: string
  candidateId: string
  candidateResourceSha256: string
  provider: string
  model: string
  runNumber: CandidateActivityRunNumber | null
}

interface GenesisRecord extends CommonRecord {
  recordType: 'genesis'
  runNumber: null
  initializationReason: 'clean_environment_no_prior_provider_activity'
  priorProviderActivity: false
  committedCostCnyAfter: 0
  recordSha256: string
}

interface PreLedgerReconciliationRecord extends CommonRecord {
  recordType: 'pre_ledger_reconciliation'
  runNumber: CandidateActivityRunNumber
  attemptedAt: null
  providerRunInitiatedAtReconciliation: false
  reconciliationReason: 'pre_ledger_failed_provider_run_usage_unavailable'
  costBasis: 'worst_case_single_report_reservation'
  chargedCostCny: number
  committedCostCnyAfter: number
  formalRejectionCode: FormalPiRunRejectionCode | null
  candidateRejectionCode: CandidateActivityRejectionCode | null
  usage: null
  recordSha256: string
}

interface ReservationRecord extends CommonRecord {
  recordType: 'reservation'
  runNumber: CandidateActivityRunNumber
  budgetLimitCny: number
  reservedCostCny: number
  priorCommittedCostCny: number
  remainingBudgetBeforeCny: number
  recordSha256: string
}

interface PreLedgerHistoricalSettlementRecord extends CommonRecord {
  recordType: 'pre_ledger_historical_settlement'
  runNumber: CandidateActivityRunNumber
  reconciliationReason: 'operator_supplied_historical_provider_usage'
  evidenceDescription:
    typeof COUNTRY_OUTAGE_PRE_LEDGER_EVIDENCE_DESCRIPTION
  evidenceSha256: string
  reconcilesRecordSha256: string
  priorCommittedCostCny: number
  adjustmentCostCny: number
  chargedCostCny: number
  committedCostCnyAfter: number
  usage: CandidateActivityUsage
  recordSha256: string
}

interface PreLedgerHistoricalBilledAmountSettlementRecord
  extends CommonRecord {
  recordType: 'pre_ledger_historical_billed_amount_settlement'
  runNumber: 1
  reconciliationReason:
    'operator_supplied_historical_provider_billed_amount'
  evidenceDescription:
    typeof COUNTRY_OUTAGE_PRE_LEDGER_BILLING_EVIDENCE_DESCRIPTION
  evidenceSha256: string
  reconcilesRecordSha256: string
  historicalAttemptStartedAtUtc:
    typeof COUNTRY_OUTAGE_PRE_LEDGER_HISTORICAL_ATTEMPT_STARTED_AT_UTC
  historicalAttemptEndedAtUtc:
    typeof COUNTRY_OUTAGE_PRE_LEDGER_HISTORICAL_ATTEMPT_ENDED_AT_UTC
  evidenceWindowStartUtc: string
  evidenceWindowEndUtc: string
  evidenceTimezone: CandidateActivityBillingEvidenceTimezone
  evidenceAcquiredAt: string
  billingFinality: 'settled_final'
  billingScope: CandidateActivityBillingScope
  billedAmountDecimal: string
  billedCurrency: CandidateActivityBilledCurrency
  conversionBasis:
    | 'identity_cny'
    | 'frozen_conservative_cny_per_usd'
  conversionRateCnyPerUnitDecimal: '1' | '8'
  convertedBilledCostCnyE8: number
  chargedCostCnyE8: number
  budgetChargeBasis:
    'max_legacy_floor_and_converted_billed_amount'
  priorCommittedCostCny: number
  adjustmentCostCny: number
  chargedCostCny: number
  committedCostCnyAfter: number
  usage: null
  recordSha256: string
}

interface SettlementRecord extends CommonRecord {
  recordType: 'settlement'
  runNumber: CandidateActivityRunNumber
  outcome: 'completed' | 'rejected'
  costBasis: 'actual_usage' | 'worst_case_reservation'
  chargedCostCny: number
  committedCostCnyAfter: number
  formalRejectionCode: FormalPiRunRejectionCode | null
  candidateRejectionCode: CandidateActivityRejectionCode | null
  usage: CandidateActivityUsage | null
  recordSha256: string
}

type ActivityRecord =
  | GenesisRecord
  | PreLedgerReconciliationRecord
  | PreLedgerHistoricalSettlementRecord
  | PreLedgerHistoricalBilledAmountSettlementRecord
  | ReservationRecord
  | SettlementRecord
type UnsignedActivityRecord =
  | Omit<GenesisRecord, 'recordSha256'>
  | Omit<PreLedgerReconciliationRecord, 'recordSha256'>
  | Omit<PreLedgerHistoricalSettlementRecord, 'recordSha256'>
  | Omit<
      PreLedgerHistoricalBilledAmountSettlementRecord,
      'recordSha256'
    >
  | Omit<ReservationRecord, 'recordSha256'>
  | Omit<SettlementRecord, 'recordSha256'>

interface ReservationState {
  record: ReservationRecord
  settled: boolean
}

interface ParsedLedger {
  records: ActivityRecord[]
  reservations: Map<string, ReservationState>
  committedCostCny: number
  lastRecordSha256: string | null
  historicalUsageStatus: 'unresolved' | 'resolved'
}

interface CandidateActivityTailAnchor {
  schemaVersion:
    typeof COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_SCHEMA
  recordCount: number
  lastRecordSha256: string
  committedCostCny: number
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

function finiteNonnegative(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isFinite(value) &&
    value >= 0
  )
}

function finiteNonnegativeInteger(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isSafeInteger(value) &&
    value >= 0
  )
}

export function isCandidateActivityRejectionCode(
  value: unknown,
): value is CandidateActivityRejectionCode {
  return (
    typeof value === 'string' &&
    CANDIDATE_ACTIVITY_REJECTION_CODE_SET.has(value)
  )
}

function isIsoTimestamp(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(value) &&
    Number.isFinite(Date.parse(value))
  )
}

function isCanonicalIsoTimestamp(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(
      value,
    ) &&
    Number.isFinite(Date.parse(value)) &&
    new Date(value).toISOString() === value
  )
}

interface HistoricalBilledAmountCalculation {
  billedAmountDecimal: string
  billedCurrency: CandidateActivityBilledCurrency
  conversionBasis:
    | 'identity_cny'
    | 'frozen_conservative_cny_per_usd'
  conversionRateCnyPerUnitDecimal: '1' | '8'
  convertedBilledCostCnyE8: number
  chargedCostCnyE8: number
  chargedCostCny: number
  adjustmentCostCny: number
}

function parseCanonicalDecimal(
  value: unknown,
): {
  numerator: bigint
  denominator: bigint
  canonical: string
} | null {
  if (typeof value !== 'string') return null
  const match = new RegExp(
    `^(0|[1-9]\\d{0,${MAXIMUM_DECIMAL_INTEGER_DIGITS - 1}})(?:\\.(\\d{1,${MAXIMUM_DECIMAL_FRACTION_DIGITS}}))?$`,
  ).exec(value)
  if (!match) return null
  const integer = match[1]!
  const fraction = match[2] ?? ''
  const denominator = 10n ** BigInt(fraction.length)
  const canonicalFraction = fraction.replace(/0+$/, '')
  return {
    numerator: BigInt(`${integer}${fraction}`),
    denominator,
    canonical:
      canonicalFraction.length === 0
        ? integer
        : `${integer}.${canonicalFraction}`,
  }
}

function calculateHistoricalBilledAmount(
  billedAmountDecimal: unknown,
  billedCurrency: unknown,
): HistoricalBilledAmountCalculation | null {
  const parsed = parseCanonicalDecimal(billedAmountDecimal)
  if (
    !parsed ||
    typeof billedAmountDecimal !== 'string' ||
    (billedCurrency !== 'CNY' && billedCurrency !== 'USD')
  ) {
    return null
  }
  const conversionRate =
    billedCurrency === 'CNY' ? 1n : CONSERVATIVE_CNY_PER_USD
  const scaledNumerator =
    parsed.numerator * conversionRate * CNY_E8_SCALE
  const convertedCnyE8 =
    (scaledNumerator + parsed.denominator - 1n) /
    parsed.denominator
  const chargedCnyE8 =
    convertedCnyE8 > LEGACY_PRE_LEDGER_COST_CNY_E8
      ? convertedCnyE8
      : LEGACY_PRE_LEDGER_COST_CNY_E8
  if (
    convertedCnyE8 > BigInt(Number.MAX_SAFE_INTEGER) ||
    chargedCnyE8 > BigInt(Number.MAX_SAFE_INTEGER)
  ) {
    return null
  }
  const convertedBilledCostCnyE8 = Number(convertedCnyE8)
  const chargedCostCnyE8 = Number(chargedCnyE8)
  const chargedCostCny =
    chargedCostCnyE8 / Number(CNY_E8_SCALE)
  const adjustmentCostCny =
    Number(chargedCnyE8 - LEGACY_PRE_LEDGER_COST_CNY_E8) /
    Number(CNY_E8_SCALE)
  return {
    billedAmountDecimal: parsed.canonical,
    billedCurrency,
    conversionBasis:
      billedCurrency === 'CNY'
        ? 'identity_cny'
        : 'frozen_conservative_cny_per_usd',
    conversionRateCnyPerUnitDecimal:
      billedCurrency === 'CNY' ? '1' : '8',
    convertedBilledCostCnyE8,
    chargedCostCnyE8,
    chargedCostCny,
    adjustmentCostCny,
  }
}

interface ValidatedHistoricalBilledAmount
  extends CandidateActivityHistoricalBilledAmount,
    HistoricalBilledAmountCalculation {}

function safeHistoricalBilledAmount(
  value: CandidateActivityHistoricalBilledAmount,
  recordedAt: string,
  policy: CandidateActivityBudgetPolicy,
): ValidatedHistoricalBilledAmount | null {
  const calculation = calculateHistoricalBilledAmount(
    value.billedAmountDecimal,
    value.billedCurrency,
  )
  if (
    !calculation ||
    policy.provider !==
      COUNTRY_OUTAGE_PRE_LEDGER_BILLING_PROVIDER ||
    policy.model !== COUNTRY_OUTAGE_PRE_LEDGER_BILLING_MODEL ||
    policy.conservativeCnyPerUsd !==
      Number(CONSERVATIVE_CNY_PER_USD) ||
    !SHA256.test(value.evidenceSha256) ||
    !isCanonicalIsoTimestamp(value.evidenceWindowStartUtc) ||
    !isCanonicalIsoTimestamp(value.evidenceWindowEndUtc) ||
    !isCanonicalIsoTimestamp(value.evidenceAcquiredAt) ||
    !isCanonicalIsoTimestamp(recordedAt) ||
    !['UTC', 'Asia/Shanghai'].includes(value.evidenceTimezone) ||
    value.billingFinality !== 'settled_final' ||
    ![
      'single_attempt_exact_charge',
      'enclosing_account_window_upper_bound',
    ].includes(value.billingScope)
  ) {
    return null
  }
  const attemptStart = Date.parse(
    COUNTRY_OUTAGE_PRE_LEDGER_HISTORICAL_ATTEMPT_STARTED_AT_UTC,
  )
  const attemptEnd = Date.parse(
    COUNTRY_OUTAGE_PRE_LEDGER_HISTORICAL_ATTEMPT_ENDED_AT_UTC,
  )
  const evidenceStart = Date.parse(value.evidenceWindowStartUtc)
  const evidenceEnd = Date.parse(value.evidenceWindowEndUtc)
  const acquiredAt = Date.parse(value.evidenceAcquiredAt)
  const settlementRecordedAt = Date.parse(recordedAt)
  if (
    evidenceStart > attemptStart ||
    evidenceEnd < attemptEnd ||
    evidenceStart > evidenceEnd ||
    acquiredAt < evidenceEnd ||
    settlementRecordedAt < acquiredAt
  ) {
    return null
  }
  return {
    ...value,
    ...calculation,
  }
}

function matchesHistoricalBilledAmount(
  record: PreLedgerHistoricalBilledAmountSettlementRecord,
  billed: ValidatedHistoricalBilledAmount,
): boolean {
  return (
    record.evidenceSha256 === billed.evidenceSha256 &&
    record.evidenceWindowStartUtc ===
      billed.evidenceWindowStartUtc &&
    record.evidenceWindowEndUtc === billed.evidenceWindowEndUtc &&
    record.evidenceTimezone === billed.evidenceTimezone &&
    record.evidenceAcquiredAt === billed.evidenceAcquiredAt &&
    record.billingFinality === billed.billingFinality &&
    record.billingScope === billed.billingScope &&
    record.billedAmountDecimal === billed.billedAmountDecimal &&
    record.billedCurrency === billed.billedCurrency &&
    record.conversionBasis === billed.conversionBasis &&
    record.conversionRateCnyPerUnitDecimal ===
      billed.conversionRateCnyPerUnitDecimal &&
    record.convertedBilledCostCnyE8 ===
      billed.convertedBilledCostCnyE8 &&
    record.chargedCostCnyE8 === billed.chargedCostCnyE8 &&
    approximatelyEqual(
      record.chargedCostCny,
      billed.chargedCostCny,
    )
  )
}

function approximatelyEqual(left: number, right: number): boolean {
  return Math.abs(left - right) <= COST_EPSILON
}

type CanonicalKeyComparator = (left: string, right: string) => number

function foldAsciiCase(value: string): string {
  return value.replace(/[A-Z]/g, (character) =>
    String.fromCharCode(character.charCodeAt(0) + 32),
  )
}

/**
 * 只用于验证 2026-07-29 已落盘的八条固定历史前缀。旧实现使用宿主
 * localeCompare；这些受 exactKeys 约束的 ASCII 字段实际按不区分大小写的
 * 字段名排序。这里把该顺序机械化，避免再次依赖宿主 locale/ICU。
 */
function compareFrozenLegacyActivityKeys(
  left: string,
  right: string,
): number {
  const folded =
    compareUnicodeCodePoints(foldAsciiCase(left), foldAsciiCase(right))
  return folded === 0
    ? compareUnicodeCodePoints(left, right)
    : folded
}

function canonicalizeWithComparator(
  value: unknown,
  comparator: CanonicalKeyComparator,
): unknown {
  if (Array.isArray(value)) {
    return value.map((item) =>
      canonicalizeWithComparator(item, comparator),
    )
  }
  if (!isRecord(value)) return value
  return Object.fromEntries(
    Object.entries(value)
      .sort(([left], [right]) => comparator(left, right))
      .map(([key, item]) => [
        key,
        canonicalizeWithComparator(item, comparator),
      ]),
  )
}

function sha256(value: string): string {
  return createHash('sha256').update(value).digest('hex')
}

function canonicalSha256(value: unknown): string {
  return sha256(
    JSON.stringify(
      canonicalizeWithComparator(value, compareUnicodeCodePoints),
    ),
  )
}

function frozenLegacyCanonicalSha256(value: unknown): string {
  return sha256(
    JSON.stringify(
      canonicalizeWithComparator(
        value,
        compareFrozenLegacyActivityKeys,
      ),
    ),
  )
}

function recordWithoutSha(
  value: ActivityRecord,
): Omit<ActivityRecord, 'recordSha256'> {
  const { recordSha256: _recordSha256, ...withoutSha } = value
  return withoutSha
}

function expectedRecordSha256(value: ActivityRecord): string {
  return canonicalSha256(recordWithoutSha(value))
}

function expectedFrozenLegacyRecordSha256(
  value: ActivityRecord,
): string {
  return frozenLegacyCanonicalSha256(recordWithoutSha(value))
}

function validatePolicy(
  policy: CandidateActivityBudgetPolicy,
): void {
  if (
    !SAFE_ID.test(policy.candidateId) ||
    !SHA256.test(policy.candidateResourceSha256) ||
    !SAFE_ID.test(policy.provider) ||
    !SAFE_ID.test(policy.model) ||
    !finiteNonnegative(policy.budgetLimitCny) ||
    policy.budgetLimitCny <= 0 ||
    !finiteNonnegative(policy.maximumSingleReportCostCny) ||
    policy.maximumSingleReportCostCny <= 0 ||
    !finiteNonnegative(policy.maximumCertificationCostCny) ||
    policy.maximumCertificationCostCny <= 0 ||
    !approximatelyEqual(
      policy.maximumCertificationCostCny,
      policy.maximumSingleReportCostCny * 2,
    ) ||
    policy.maximumCertificationCostCny > policy.budgetLimitCny ||
    !finiteNonnegative(policy.conservativeCnyPerUsd) ||
    policy.conservativeCnyPerUsd <= 0 ||
    !finiteNonnegative(policy.priceUsdPerMillionTokens.input) ||
    !finiteNonnegative(policy.priceUsdPerMillionTokens.output) ||
    !finiteNonnegative(policy.priceUsdPerMillionTokens.cacheRead) ||
    !finiteNonnegative(policy.priceUsdPerMillionTokens.cacheWrite)
  ) {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
}

function safeUsage(value: unknown): CandidateActivityUsage | null {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'providerRequestCount',
      'inputTokens',
      'outputTokens',
      'cacheReadTokens',
      'cacheWriteTokens',
    ]) ||
    !finiteNonnegativeInteger(value.providerRequestCount) ||
    !finiteNonnegativeInteger(value.inputTokens) ||
    !finiteNonnegativeInteger(value.outputTokens) ||
    !finiteNonnegativeInteger(value.cacheReadTokens) ||
    !finiteNonnegativeInteger(value.cacheWriteTokens)
  ) {
    return null
  }
  const aggregateInput =
    value.inputTokens + value.cacheReadTokens + value.cacheWriteTokens
  if (!Number.isSafeInteger(aggregateInput)) return null
  return {
    providerRequestCount: value.providerRequestCount,
    inputTokens: value.inputTokens,
    outputTokens: value.outputTokens,
    cacheReadTokens: value.cacheReadTokens,
    cacheWriteTokens: value.cacheWriteTokens,
  }
}

export function candidateActivityUsageCostCny(
  policy: CandidateActivityBudgetPolicy,
  usage: CandidateActivityUsage,
): number {
  validatePolicy(policy)
  const validated = safeUsage(usage)
  if (!validated) {
    throw new CandidateActivityLedgerError(
      'activity_reservation_invalid',
    )
  }
  const maximumInputLikePriceUsdPerMillionTokens = Math.max(
    policy.priceUsdPerMillionTokens.input,
    policy.priceUsdPerMillionTokens.cacheRead,
    policy.priceUsdPerMillionTokens.cacheWrite,
  )
  const inputLike =
    validated.inputTokens +
    validated.cacheReadTokens +
    validated.cacheWriteTokens
  const usd =
    (inputLike * maximumInputLikePriceUsdPerMillionTokens +
      validated.outputTokens *
        policy.priceUsdPerMillionTokens.output) /
    MILLION
  const cny = usd * policy.conservativeCnyPerUsd
  if (!finiteNonnegative(cny)) {
    throw new CandidateActivityLedgerError(
      'activity_reservation_invalid',
    )
  }
  return cny
}

function parseCommonRecord(
  value: Record<string, unknown>,
  expectedSequence: number,
  previousRecordSha256: string | null,
  policy: CandidateActivityBudgetPolicy,
): CommonRecord | null {
  if (
    value.schemaVersion !==
      COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_SCHEMA ||
    value.sequence !== expectedSequence ||
    value.previousRecordSha256 !== previousRecordSha256 ||
    !isIsoTimestamp(value.recordedAt) ||
    ![
      'genesis',
      'pre_ledger_reconciliation',
      'pre_ledger_historical_settlement',
      'pre_ledger_historical_billed_amount_settlement',
      'reservation',
      'settlement',
    ].includes(
      String(value.recordType),
    ) ||
    typeof value.activityId !== 'string' ||
    !/^candidate-activity:[a-f0-9]{64}$/.test(value.activityId) ||
    value.candidateId !== policy.candidateId ||
    !SHA256.test(String(value.candidateResourceSha256)) ||
    value.provider !== policy.provider ||
    value.model !== policy.model ||
    (value.recordType === 'genesis'
      ? value.runNumber !== null
      : ![1, 2, 3, 4, 5].includes(Number(value.runNumber)))
  ) {
    return null
  }
  return {
    schemaVersion:
      COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_SCHEMA,
    sequence: value.sequence as number,
    previousRecordSha256: value.previousRecordSha256 as string | null,
    recordedAt: value.recordedAt,
    recordType: value.recordType as CommonRecord['recordType'],
    activityId: value.activityId,
    candidateId: value.candidateId,
    candidateResourceSha256: value.candidateResourceSha256 as string,
    provider: value.provider,
    model: value.model,
    runNumber: value.runNumber as CandidateActivityRunNumber | null,
  }
}

function parseLedger(
  text: string,
  policy: CandidateActivityBudgetPolicy,
): ParsedLedger {
  if (Buffer.byteLength(text, 'utf8') > MAX_LEDGER_BYTES) {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
  const lines = text ? text.split('\n') : []
  if (lines.at(-1) === '') lines.pop()
  if (
    lines.length > MAX_RECORDS ||
    lines.some(
      (line) =>
        line.length === 0 ||
        Buffer.byteLength(line, 'utf8') > MAX_RECORD_BYTES,
    )
  ) {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }

  const records: ActivityRecord[] = []
  const reservations = new Map<string, ReservationState>()
  let committedCostCny = 0
  let previousRecordSha256: string | null = null
  let historicalUsageStatus: 'unresolved' | 'resolved' =
    'unresolved'
  let frozenLegacyPrefix = false

  for (const [index, line] of lines.entries()) {
    let unknownRecord: unknown
    try {
      unknownRecord = JSON.parse(line) as unknown
    } catch {
      throw new CandidateActivityLedgerError(
        'activity_ledger_invalid',
      )
    }
    if (!isRecord(unknownRecord)) {
      throw new CandidateActivityLedgerError(
        'activity_ledger_invalid',
      )
    }
    const common = parseCommonRecord(
      unknownRecord,
      index + 1,
      previousRecordSha256,
      policy,
    )
    if (!common) {
      throw new CandidateActivityLedgerError(
        'activity_ledger_invalid',
      )
    }

    let record: ActivityRecord
    if (common.recordType === 'genesis') {
      if (
        index !== 0 ||
        !exactKeys(unknownRecord, [
          'schemaVersion',
          'sequence',
          'previousRecordSha256',
          'recordedAt',
          'recordType',
          'activityId',
          'candidateId',
          'candidateResourceSha256',
          'provider',
          'model',
          'runNumber',
          'initializationReason',
          'priorProviderActivity',
          'committedCostCnyAfter',
          'recordSha256',
        ]) ||
        common.runNumber !== null ||
        unknownRecord.initializationReason !==
          'clean_environment_no_prior_provider_activity' ||
        unknownRecord.priorProviderActivity !== false ||
        unknownRecord.committedCostCnyAfter !== 0 ||
        typeof unknownRecord.recordSha256 !== 'string' ||
        !SHA256.test(unknownRecord.recordSha256)
      ) {
        throw new CandidateActivityLedgerError(
          'activity_ledger_invalid',
        )
      }
      record = {
        ...common,
        recordType: 'genesis',
        runNumber: null,
        initializationReason:
          'clean_environment_no_prior_provider_activity',
        priorProviderActivity: false,
        committedCostCnyAfter: 0,
        recordSha256: unknownRecord.recordSha256,
      }
      committedCostCny = 0
      historicalUsageStatus = 'resolved'
    } else if (
      common.recordType === 'pre_ledger_reconciliation'
    ) {
      const formalRejectionCode =
        unknownRecord.formalRejectionCode === null
          ? null
          : isFormalPiRunRejectionCode(
                unknownRecord.formalRejectionCode,
              )
            ? unknownRecord.formalRejectionCode
            : undefined
      const candidateRejectionCode =
        unknownRecord.candidateRejectionCode === null
          ? null
          : isCandidateActivityRejectionCode(
                unknownRecord.candidateRejectionCode,
              )
            ? unknownRecord.candidateRejectionCode
            : undefined
      if (
        index !== 0 ||
        !exactKeys(unknownRecord, [
          'schemaVersion',
          'sequence',
          'previousRecordSha256',
          'recordedAt',
          'recordType',
          'activityId',
          'candidateId',
          'candidateResourceSha256',
          'provider',
          'model',
          'runNumber',
          'attemptedAt',
          'providerRunInitiatedAtReconciliation',
          'reconciliationReason',
          'costBasis',
          'chargedCostCny',
          'committedCostCnyAfter',
          'formalRejectionCode',
          'candidateRejectionCode',
          'usage',
          'recordSha256',
        ]) ||
        unknownRecord.attemptedAt !== null ||
        unknownRecord.providerRunInitiatedAtReconciliation !== false ||
        unknownRecord.reconciliationReason !==
          'pre_ledger_failed_provider_run_usage_unavailable' ||
        unknownRecord.costBasis !==
          'worst_case_single_report_reservation' ||
        !finiteNonnegative(unknownRecord.chargedCostCny) ||
        !approximatelyEqual(
          unknownRecord.chargedCostCny,
          COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
        ) ||
        !finiteNonnegative(unknownRecord.committedCostCnyAfter) ||
        !approximatelyEqual(
          unknownRecord.committedCostCnyAfter,
          COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
        ) ||
        formalRejectionCode === undefined ||
        candidateRejectionCode !== 'candidate_runner_failed' ||
        unknownRecord.usage !== null ||
        typeof unknownRecord.recordSha256 !== 'string' ||
        !SHA256.test(unknownRecord.recordSha256)
      ) {
        throw new CandidateActivityLedgerError(
          'activity_ledger_invalid',
        )
      }
      record = {
        ...common,
        recordType: 'pre_ledger_reconciliation',
        runNumber: common.runNumber as CandidateActivityRunNumber,
        attemptedAt: null,
        providerRunInitiatedAtReconciliation: false,
        reconciliationReason:
          'pre_ledger_failed_provider_run_usage_unavailable',
        costBasis: 'worst_case_single_report_reservation',
        chargedCostCny: unknownRecord.chargedCostCny,
        committedCostCnyAfter:
          unknownRecord.committedCostCnyAfter,
        formalRejectionCode,
        candidateRejectionCode,
        usage: null,
        recordSha256: unknownRecord.recordSha256,
      }
      committedCostCny =
        unknownRecord.committedCostCnyAfter as number
    } else if (
      common.recordType === 'pre_ledger_historical_settlement'
    ) {
      const usage = safeUsage(unknownRecord.usage)
      const chargedCostCny =
        usage === null
          ? Number.NaN
          : candidateActivityUsageCostCny(policy, usage)
      if (
        index !== 1 ||
        records[0]?.recordType !==
          'pre_ledger_reconciliation' ||
        historicalUsageStatus !== 'unresolved' ||
        reservations.size !== 0 ||
        !exactKeys(unknownRecord, [
          'schemaVersion',
          'sequence',
          'previousRecordSha256',
          'recordedAt',
          'recordType',
          'activityId',
          'candidateId',
          'candidateResourceSha256',
          'provider',
          'model',
          'runNumber',
          'reconciliationReason',
          'evidenceDescription',
          'evidenceSha256',
          'reconcilesRecordSha256',
          'priorCommittedCostCny',
          'adjustmentCostCny',
          'chargedCostCny',
          'committedCostCnyAfter',
          'usage',
          'recordSha256',
        ]) ||
        common.runNumber !== 1 ||
        unknownRecord.reconciliationReason !==
          'operator_supplied_historical_provider_usage' ||
        unknownRecord.evidenceDescription !==
          COUNTRY_OUTAGE_PRE_LEDGER_EVIDENCE_DESCRIPTION ||
        typeof unknownRecord.evidenceSha256 !== 'string' ||
        !SHA256.test(unknownRecord.evidenceSha256) ||
        unknownRecord.reconcilesRecordSha256 !==
          records[0]?.recordSha256 ||
        usage === null ||
        usage.providerRequestCount < 1 ||
        !finiteNonnegative(chargedCostCny) ||
        chargedCostCny + COST_EPSILON <
          COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY ||
        !finiteNonnegative(unknownRecord.priorCommittedCostCny) ||
        !approximatelyEqual(
          unknownRecord.priorCommittedCostCny,
          committedCostCny,
        ) ||
        !finiteNonnegative(unknownRecord.adjustmentCostCny) ||
        !approximatelyEqual(
          unknownRecord.adjustmentCostCny,
          chargedCostCny - committedCostCny,
        ) ||
        !finiteNonnegative(unknownRecord.chargedCostCny) ||
        !approximatelyEqual(
          unknownRecord.chargedCostCny,
          chargedCostCny,
        ) ||
        !finiteNonnegative(unknownRecord.committedCostCnyAfter) ||
        !approximatelyEqual(
          unknownRecord.committedCostCnyAfter,
          chargedCostCny,
        ) ||
        typeof unknownRecord.recordSha256 !== 'string' ||
        !SHA256.test(unknownRecord.recordSha256)
      ) {
        throw new CandidateActivityLedgerError(
          'activity_ledger_invalid',
        )
      }
      record = {
        ...common,
        recordType: 'pre_ledger_historical_settlement',
        runNumber: 1,
        reconciliationReason:
          'operator_supplied_historical_provider_usage',
        evidenceDescription:
          COUNTRY_OUTAGE_PRE_LEDGER_EVIDENCE_DESCRIPTION,
        evidenceSha256: unknownRecord.evidenceSha256,
        reconcilesRecordSha256:
          unknownRecord.reconcilesRecordSha256 as string,
        priorCommittedCostCny:
          unknownRecord.priorCommittedCostCny,
        adjustmentCostCny: unknownRecord.adjustmentCostCny,
        chargedCostCny: unknownRecord.chargedCostCny,
        committedCostCnyAfter:
          unknownRecord.committedCostCnyAfter,
        usage,
        recordSha256: unknownRecord.recordSha256,
      }
      committedCostCny = chargedCostCny
      historicalUsageStatus = 'resolved'
    } else if (
      common.recordType ===
      'pre_ledger_historical_billed_amount_settlement'
    ) {
      const billed = safeHistoricalBilledAmount(
        {
          evidenceSha256: unknownRecord.evidenceSha256,
          evidenceWindowStartUtc:
            unknownRecord.evidenceWindowStartUtc,
          evidenceWindowEndUtc:
            unknownRecord.evidenceWindowEndUtc,
          evidenceTimezone: unknownRecord.evidenceTimezone,
          evidenceAcquiredAt: unknownRecord.evidenceAcquiredAt,
          billingFinality: unknownRecord.billingFinality,
          billingScope: unknownRecord.billingScope,
          billedAmountDecimal: unknownRecord.billedAmountDecimal,
          billedCurrency: unknownRecord.billedCurrency,
        } as CandidateActivityHistoricalBilledAmount,
        common.recordedAt,
        policy,
      )
      if (
        index !== 1 ||
        records[0]?.recordType !==
          'pre_ledger_reconciliation' ||
        historicalUsageStatus !== 'unresolved' ||
        reservations.size !== 0 ||
        !exactKeys(unknownRecord, [
          'schemaVersion',
          'sequence',
          'previousRecordSha256',
          'recordedAt',
          'recordType',
          'activityId',
          'candidateId',
          'candidateResourceSha256',
          'provider',
          'model',
          'runNumber',
          'reconciliationReason',
          'evidenceDescription',
          'evidenceSha256',
          'reconcilesRecordSha256',
          'historicalAttemptStartedAtUtc',
          'historicalAttemptEndedAtUtc',
          'evidenceWindowStartUtc',
          'evidenceWindowEndUtc',
          'evidenceTimezone',
          'evidenceAcquiredAt',
          'billingFinality',
          'billingScope',
          'billedAmountDecimal',
          'billedCurrency',
          'conversionBasis',
          'conversionRateCnyPerUnitDecimal',
          'convertedBilledCostCnyE8',
          'chargedCostCnyE8',
          'budgetChargeBasis',
          'priorCommittedCostCny',
          'adjustmentCostCny',
          'chargedCostCny',
          'committedCostCnyAfter',
          'usage',
          'recordSha256',
        ]) ||
        common.runNumber !== 1 ||
        unknownRecord.reconciliationReason !==
          'operator_supplied_historical_provider_billed_amount' ||
        unknownRecord.evidenceDescription !==
          COUNTRY_OUTAGE_PRE_LEDGER_BILLING_EVIDENCE_DESCRIPTION ||
        unknownRecord.reconcilesRecordSha256 !==
          records[0]?.recordSha256 ||
        unknownRecord.historicalAttemptStartedAtUtc !==
          COUNTRY_OUTAGE_PRE_LEDGER_HISTORICAL_ATTEMPT_STARTED_AT_UTC ||
        unknownRecord.historicalAttemptEndedAtUtc !==
          COUNTRY_OUTAGE_PRE_LEDGER_HISTORICAL_ATTEMPT_ENDED_AT_UTC ||
        billed === null ||
        unknownRecord.conversionBasis !== billed?.conversionBasis ||
        unknownRecord.conversionRateCnyPerUnitDecimal !==
          billed?.conversionRateCnyPerUnitDecimal ||
        unknownRecord.convertedBilledCostCnyE8 !==
          billed?.convertedBilledCostCnyE8 ||
        unknownRecord.chargedCostCnyE8 !==
          billed?.chargedCostCnyE8 ||
        unknownRecord.budgetChargeBasis !==
          'max_legacy_floor_and_converted_billed_amount' ||
        !finiteNonnegative(unknownRecord.priorCommittedCostCny) ||
        !approximatelyEqual(
          unknownRecord.priorCommittedCostCny,
          committedCostCny,
        ) ||
        !finiteNonnegative(unknownRecord.adjustmentCostCny) ||
        !approximatelyEqual(
          unknownRecord.adjustmentCostCny,
          billed?.adjustmentCostCny ?? Number.NaN,
        ) ||
        !finiteNonnegative(unknownRecord.chargedCostCny) ||
        !approximatelyEqual(
          unknownRecord.chargedCostCny,
          billed?.chargedCostCny ?? Number.NaN,
        ) ||
        !finiteNonnegative(unknownRecord.committedCostCnyAfter) ||
        !approximatelyEqual(
          unknownRecord.committedCostCnyAfter,
          billed?.chargedCostCny ?? Number.NaN,
        ) ||
        unknownRecord.usage !== null ||
        typeof unknownRecord.recordSha256 !== 'string' ||
        !SHA256.test(unknownRecord.recordSha256)
      ) {
        throw new CandidateActivityLedgerError(
          'activity_ledger_invalid',
        )
      }
      record = {
        ...common,
        recordType:
          'pre_ledger_historical_billed_amount_settlement',
        runNumber: 1,
        reconciliationReason:
          'operator_supplied_historical_provider_billed_amount',
        evidenceDescription:
          COUNTRY_OUTAGE_PRE_LEDGER_BILLING_EVIDENCE_DESCRIPTION,
        evidenceSha256: billed.evidenceSha256,
        reconcilesRecordSha256:
          unknownRecord.reconcilesRecordSha256 as string,
        historicalAttemptStartedAtUtc:
          COUNTRY_OUTAGE_PRE_LEDGER_HISTORICAL_ATTEMPT_STARTED_AT_UTC,
        historicalAttemptEndedAtUtc:
          COUNTRY_OUTAGE_PRE_LEDGER_HISTORICAL_ATTEMPT_ENDED_AT_UTC,
        evidenceWindowStartUtc: billed.evidenceWindowStartUtc,
        evidenceWindowEndUtc: billed.evidenceWindowEndUtc,
        evidenceTimezone: billed.evidenceTimezone,
        evidenceAcquiredAt: billed.evidenceAcquiredAt,
        billingFinality: 'settled_final',
        billingScope: billed.billingScope,
        billedAmountDecimal: billed.billedAmountDecimal,
        billedCurrency: billed.billedCurrency,
        conversionBasis: billed.conversionBasis,
        conversionRateCnyPerUnitDecimal:
          billed.conversionRateCnyPerUnitDecimal,
        convertedBilledCostCnyE8:
          billed.convertedBilledCostCnyE8,
        chargedCostCnyE8: billed.chargedCostCnyE8,
        budgetChargeBasis:
          'max_legacy_floor_and_converted_billed_amount',
        priorCommittedCostCny:
          unknownRecord.priorCommittedCostCny,
        adjustmentCostCny: unknownRecord.adjustmentCostCny,
        chargedCostCny: unknownRecord.chargedCostCny,
        committedCostCnyAfter:
          unknownRecord.committedCostCnyAfter,
        usage: null,
        recordSha256: unknownRecord.recordSha256,
      }
      committedCostCny = billed.chargedCostCny
      historicalUsageStatus = 'resolved'
    } else if (common.recordType === 'reservation') {
      const expectedReservationCostCny =
        frozenLegacyPrefix &&
        index < FROZEN_LEGACY_ACTIVITY_PREFIX.recordCount
          ? FROZEN_LEGACY_ACTIVITY_PREFIX
              .maximumSingleReportCostCny
          : policy.maximumSingleReportCostCny
      if (
        historicalUsageStatus !== 'resolved' ||
        !exactKeys(unknownRecord, [
          'schemaVersion',
          'sequence',
          'previousRecordSha256',
          'recordedAt',
          'recordType',
          'activityId',
          'candidateId',
          'candidateResourceSha256',
          'provider',
          'model',
          'runNumber',
          'budgetLimitCny',
          'reservedCostCny',
          'priorCommittedCostCny',
          'remainingBudgetBeforeCny',
          'recordSha256',
        ]) ||
        unknownRecord.budgetLimitCny !== policy.budgetLimitCny ||
        !finiteNonnegative(unknownRecord.reservedCostCny) ||
        !approximatelyEqual(
          unknownRecord.reservedCostCny,
          expectedReservationCostCny,
        ) ||
        !finiteNonnegative(unknownRecord.priorCommittedCostCny) ||
        !approximatelyEqual(
          unknownRecord.priorCommittedCostCny,
          committedCostCny,
        ) ||
        !finiteNonnegative(unknownRecord.remainingBudgetBeforeCny) ||
        !approximatelyEqual(
          unknownRecord.remainingBudgetBeforeCny,
          policy.budgetLimitCny - committedCostCny,
        ) ||
        typeof unknownRecord.recordSha256 !== 'string' ||
        !SHA256.test(unknownRecord.recordSha256) ||
        reservations.has(common.activityId)
      ) {
        throw new CandidateActivityLedgerError(
          'activity_ledger_invalid',
        )
      }
      record = {
        ...common,
        recordType: 'reservation',
        runNumber: common.runNumber as CandidateActivityRunNumber,
        budgetLimitCny: unknownRecord.budgetLimitCny,
        reservedCostCny: unknownRecord.reservedCostCny,
        priorCommittedCostCny:
          unknownRecord.priorCommittedCostCny,
        remainingBudgetBeforeCny:
          unknownRecord.remainingBudgetBeforeCny,
        recordSha256: unknownRecord.recordSha256,
      }
      committedCostCny += record.reservedCostCny
      reservations.set(common.activityId, {
        record,
        settled: false,
      })
    } else {
      const reservation = reservations.get(common.activityId)
      const usage = safeUsage(unknownRecord.usage)
      const formalRejectionCode =
        unknownRecord.formalRejectionCode === null
          ? null
          : isFormalPiRunRejectionCode(
                unknownRecord.formalRejectionCode,
              )
            ? unknownRecord.formalRejectionCode
            : undefined
      const candidateRejectionCode =
        unknownRecord.candidateRejectionCode === null
          ? null
          : isCandidateActivityRejectionCode(
                unknownRecord.candidateRejectionCode,
              )
            ? unknownRecord.candidateRejectionCode
            : undefined
      if (
        !exactKeys(unknownRecord, [
          'schemaVersion',
          'sequence',
          'previousRecordSha256',
          'recordedAt',
          'recordType',
          'activityId',
          'candidateId',
          'candidateResourceSha256',
          'provider',
          'model',
          'runNumber',
          'outcome',
          'costBasis',
          'chargedCostCny',
          'committedCostCnyAfter',
          'formalRejectionCode',
          'candidateRejectionCode',
          'usage',
          'recordSha256',
        ]) ||
        !reservation ||
        reservation.settled ||
        reservation.record.runNumber !== common.runNumber ||
        !['completed', 'rejected'].includes(
          String(unknownRecord.outcome),
        ) ||
        !['actual_usage', 'worst_case_reservation'].includes(
          String(unknownRecord.costBasis),
        ) ||
        !finiteNonnegative(unknownRecord.chargedCostCny) ||
        !finiteNonnegative(unknownRecord.committedCostCnyAfter) ||
        formalRejectionCode === undefined ||
        candidateRejectionCode === undefined ||
        (unknownRecord.outcome === 'completed' &&
          (formalRejectionCode !== null ||
            candidateRejectionCode !== null)) ||
        (unknownRecord.outcome === 'rejected' &&
          formalRejectionCode === null &&
          candidateRejectionCode === null) ||
        (unknownRecord.costBasis === 'actual_usage' &&
          usage === null) ||
        (unknownRecord.costBasis === 'worst_case_reservation' &&
          (unknownRecord.usage !== null ||
            !approximatelyEqual(
              unknownRecord.chargedCostCny,
              reservation.record.reservedCostCny,
            ))) ||
        typeof unknownRecord.recordSha256 !== 'string' ||
        !SHA256.test(unknownRecord.recordSha256)
      ) {
        throw new CandidateActivityLedgerError(
          'activity_ledger_invalid',
        )
      }
      if (
        usage &&
        !approximatelyEqual(
          unknownRecord.chargedCostCny,
          candidateActivityUsageCostCny(policy, usage),
        )
      ) {
        throw new CandidateActivityLedgerError(
          'activity_ledger_invalid',
        )
      }
      const nextCommittedCostCny =
        committedCostCny -
        reservation.record.reservedCostCny +
        unknownRecord.chargedCostCny
      if (
        !Number.isFinite(nextCommittedCostCny) ||
        !approximatelyEqual(
          unknownRecord.committedCostCnyAfter,
          nextCommittedCostCny,
        )
      ) {
        throw new CandidateActivityLedgerError(
          'activity_ledger_invalid',
        )
      }
      record = {
        ...common,
        recordType: 'settlement',
        runNumber: common.runNumber as CandidateActivityRunNumber,
        outcome: unknownRecord.outcome as 'completed' | 'rejected',
        costBasis: unknownRecord.costBasis as
          | 'actual_usage'
          | 'worst_case_reservation',
        chargedCostCny: unknownRecord.chargedCostCny,
        committedCostCnyAfter:
          unknownRecord.committedCostCnyAfter,
        formalRejectionCode,
        candidateRejectionCode,
        usage,
        recordSha256: unknownRecord.recordSha256,
      }
      committedCostCny = nextCommittedCostCny
      reservation.settled = true
    }

    if (index === 0) {
      frozenLegacyPrefix =
        record.candidateId ===
          FROZEN_LEGACY_ACTIVITY_PREFIX.candidateId &&
        record.candidateResourceSha256 ===
          FROZEN_LEGACY_ACTIVITY_PREFIX.candidateResourceSha256 &&
        record.recordSha256 ===
          FROZEN_LEGACY_ACTIVITY_PREFIX.firstRecordSha256 &&
        record.recordSha256 ===
          expectedFrozenLegacyRecordSha256(record)
    }
    const withinFrozenLegacyPrefix =
      frozenLegacyPrefix &&
      index < FROZEN_LEGACY_ACTIVITY_PREFIX.recordCount
    const expectedSha256 = withinFrozenLegacyPrefix
      ? expectedFrozenLegacyRecordSha256(record)
      : expectedRecordSha256(record)
    if (
      record.recordSha256 !== expectedSha256 ||
      (withinFrozenLegacyPrefix &&
        (record.candidateId !==
          FROZEN_LEGACY_ACTIVITY_PREFIX.candidateId ||
          record.candidateResourceSha256 !==
            FROZEN_LEGACY_ACTIVITY_PREFIX
              .candidateResourceSha256)) ||
      (index ===
        FROZEN_LEGACY_ACTIVITY_PREFIX.recordCount - 1 &&
        frozenLegacyPrefix &&
        record.recordSha256 !==
          FROZEN_LEGACY_ACTIVITY_PREFIX.lastRecordSha256)
    ) {
      throw new CandidateActivityLedgerError(
        'activity_ledger_invalid',
      )
    }
    records.push(record)
    previousRecordSha256 = record.recordSha256
  }

  if (
    frozenLegacyPrefix &&
    records.length < FROZEN_LEGACY_ACTIVITY_PREFIX.recordCount
  ) {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }

  return {
    records,
    reservations,
    committedCostCny,
    lastRecordSha256: previousRecordSha256,
    historicalUsageStatus,
  }
}

function permissionBits(stat: Stats): number {
  return stat.mode & 0o777
}

function currentUserId(): number {
  if (typeof process.getuid !== 'function') {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
  return process.getuid()
}

function assertOwned(stat: Stats): void {
  if (stat.uid !== currentUserId()) {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
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
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
}

function secureLedgerDirectory(
  repositoryRoot: string,
  initialize: boolean,
): string {
  if (!isAbsolute(repositoryRoot)) {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
  let root: string
  try {
    const rootStats = lstatSync(repositoryRoot)
    if (!rootStats.isDirectory() || rootStats.isSymbolicLink()) {
      throw new Error('invalid root')
    }
    root = realpathSync(repositoryRoot)
  } catch {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }

  let current = root
  const segments = [
    'var',
    'country-outage-agent',
    'a4-model-certification-activity',
  ]
  for (const [index, segment] of segments.entries()) {
    const next = resolve(current, segment)
    if (
      next !== `${current}${sep}${segment}` ||
      !next.startsWith(`${root}${sep}`)
    ) {
      throw new CandidateActivityLedgerError(
        'activity_ledger_invalid',
      )
    }
    try {
      if (initialize) {
        try {
          mkdirSync(next, { mode: 0o700 })
          chmodSync(next, 0o700)
        } catch (error) {
          if (
            !(error instanceof Error) ||
            !('code' in error) ||
            error.code !== 'EEXIST'
          ) {
            throw error
          }
        }
      }
      const stats = lstatSync(next)
      if (
        !stats.isDirectory() ||
        stats.isSymbolicLink() ||
        realpathSync(next) !== next
      ) {
        throw new Error('invalid ledger directory')
      }
      assertOwned(stats)
      if (
        index === segments.length - 1
          ? permissionBits(stats) !== 0o700
          : (permissionBits(stats) & 0o022) !== 0
      ) {
        throw new Error('unsafe ledger directory permissions')
      }
    } catch {
      throw new CandidateActivityLedgerError(
        'activity_ledger_invalid',
      )
    }
    current = next
  }
  return current
}

function assertSafeLedgerFile(path: string, stat: Stats): void {
  if (
    !stat.isFile() ||
    stat.isSymbolicLink() ||
    stat.nlink !== 1 ||
    permissionBits(stat) !== 0o600 ||
    realpathSync(path) !== path ||
    stat.size > MAX_LEDGER_BYTES
  ) {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
  assertOwned(stat)
}

function assertSafeAnchorFile(path: string, stat: Stats): void {
  if (
    !stat.isFile() ||
    stat.isSymbolicLink() ||
    stat.nlink !== 1 ||
    permissionBits(stat) !== 0o600 ||
    realpathSync(path) !== path ||
    stat.size <= 0 ||
    stat.size > MAX_ANCHOR_BYTES
  ) {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
  assertOwned(stat)
}

function parseTailAnchor(
  value: unknown,
): CandidateActivityTailAnchor {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'schemaVersion',
      'recordCount',
      'lastRecordSha256',
      'committedCostCny',
    ]) ||
    value.schemaVersion !==
      COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_SCHEMA ||
    !Number.isSafeInteger(value.recordCount) ||
    Number(value.recordCount) < 1 ||
    typeof value.lastRecordSha256 !== 'string' ||
    !SHA256.test(value.lastRecordSha256) ||
    !finiteNonnegative(value.committedCostCny)
  ) {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
  return {
    schemaVersion:
      COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_SCHEMA,
    recordCount: value.recordCount as number,
    lastRecordSha256: value.lastRecordSha256,
    committedCostCny: value.committedCostCny,
  }
}

function readTailAnchor(path: string): CandidateActivityTailAnchor {
  let descriptor: number | undefined
  try {
    descriptor = openSync(
      path,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    )
    assertSafeAnchorFile(path, fstatSync(descriptor))
    const value = JSON.parse(
      readFileSync(descriptor, 'utf8'),
    ) as unknown
    closeSync(descriptor)
    descriptor = undefined
    return parseTailAnchor(value)
  } catch (error) {
    if (descriptor !== undefined) closeSync(descriptor)
    if (error instanceof CandidateActivityLedgerError) throw error
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
}

function assertTailAnchorMatches(
  parsed: ParsedLedger,
  anchor: CandidateActivityTailAnchor,
): void {
  if (
    parsed.records.length < 1 ||
    parsed.lastRecordSha256 === null ||
    anchor.recordCount !== parsed.records.length ||
    anchor.lastRecordSha256 !== parsed.lastRecordSha256 ||
    anchor.committedCostCny !== parsed.committedCostCny
  ) {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
}

function writeComplete(descriptor: number, content: string): void {
  const bytes = Buffer.from(content, 'utf8')
  let offset = 0
  while (offset < bytes.length) {
    const written = writeSync(
      descriptor,
      bytes,
      offset,
      bytes.length - offset,
    )
    if (written <= 0) {
      throw new CandidateActivityLedgerError(
        'activity_ledger_invalid',
      )
    }
    offset += written
  }
}

function writeTailAnchorAtomically(
  directory: string,
  anchorPath: string,
  anchor: CandidateActivityTailAnchor,
): void {
  const tempPath = resolve(
    directory,
    `.activity-anchor-${randomUUID()}.tmp`,
  )
  let descriptor: number | undefined
  let tempCreated = false
  try {
    descriptor = openSync(
      tempPath,
      constants.O_CREAT |
        constants.O_EXCL |
        constants.O_WRONLY |
        (constants.O_NOFOLLOW ?? 0),
      0o600,
    )
    tempCreated = true
    fchmodSync(descriptor, 0o600)
    const stat = fstatSync(descriptor)
    if (
      !stat.isFile() ||
      stat.nlink !== 1 ||
      permissionBits(stat) !== 0o600
    ) {
      throw new CandidateActivityLedgerError(
        'activity_ledger_invalid',
      )
    }
    assertOwned(stat)
    writeComplete(
      descriptor,
      `${JSON.stringify(anchor)}\n`,
    )
    fsyncSync(descriptor)
    closeSync(descriptor)
    descriptor = undefined
    renameSync(tempPath, anchorPath)
    tempCreated = false
    fsyncDirectory(directory)
  } catch (error) {
    if (descriptor !== undefined) closeSync(descriptor)
    if (tempCreated) rmSync(tempPath, { force: true })
    if (error instanceof CandidateActivityLedgerError) throw error
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
}

/*
 * 威胁模型：账本与独立 tail anchor 用于发现意外删除、空写、截断、单文件回退和
 * 普通同 UID 进程造成的漂移。它不声称抵御能够修改本程序代码、同时重算并替换
 * ledger 与 anchor 的同 UID 恶意操作者；该场景需要仓库/主机之外的可信锚。
 * 两文件同时缺失时，本机无法区分首次初始化与历史被删除；只有显式 reconcile
 * 运维命令可以建立新 genesis，正式认证路径本身永远不会初始化或重置预算。
 */

export interface CandidateActivityLedger {
  readonly path: string
  readonly anchorPath: string
  snapshot(): CandidateActivityBudgetSnapshot
  assertCertificationBudgetAvailable(): void
  reserve(
    runNumber: CandidateActivityRunNumber,
    recordedAt: Date,
  ): CandidateActivityReservation
  settle(
    reservation: CandidateActivityReservation,
    options: {
      outcome: 'completed' | 'rejected'
      recordedAt: Date
      formalRejectionCode?: FormalPiRunRejectionCode
      candidateRejectionCode?: CandidateActivityRejectionCode
      usage?: CandidateActivityUsage
    },
  ): void
  close(): void
}

/**
 * 只读检查当前 ledger 与独立 tail anchor。该函数不会创建 lock、文件或目录，
 * 仅用于无凭据、无网络的运维 readiness 状态；正式认证仍使用带锁入口。
 */
export function inspectCandidateActivityLedger(options: {
  repositoryRoot: string
  policy: CandidateActivityBudgetPolicy
}): CandidateActivityBudgetSnapshot {
  validatePolicy(options.policy)
  const directory = secureLedgerDirectory(
    options.repositoryRoot,
    false,
  )
  const path = resolve(
    directory,
    'deepseek-v4-flash-pi-0.82.1-v1-activity-v1.jsonl',
  )
  const anchorPath = resolve(
    directory,
    'deepseek-v4-flash-pi-0.82.1-v1-activity-anchor-v1.json',
  )
  if (!pathEntryExists(path) || !pathEntryExists(anchorPath)) {
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
  let descriptor: number | undefined
  try {
    descriptor = openSync(
      path,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    )
    assertSafeLedgerFile(path, fstatSync(descriptor))
    const parsed = parseLedger(
      readFileSync(descriptor, 'utf8'),
      options.policy,
    )
    closeSync(descriptor)
    descriptor = undefined
    assertTailAnchorMatches(parsed, readTailAnchor(anchorPath))
    return Object.freeze({
      committedCostCny: parsed.committedCostCny,
      remainingBudgetCny:
        options.policy.budgetLimitCny - parsed.committedCostCny,
      openReservations: [...parsed.reservations.values()].filter(
        (item) => !item.settled,
      ).length,
      recordCount: parsed.records.length,
      historicalUsageStatus: parsed.historicalUsageStatus,
    })
  } catch (error) {
    if (descriptor !== undefined) closeSync(descriptor)
    if (error instanceof CandidateActivityLedgerError) throw error
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
}

function openCandidateActivityLedgerInternal(options: {
  repositoryRoot: string
  policy: CandidateActivityBudgetPolicy
  initialization?: {
    kind: 'clean' | 'pre_ledger_failure'
    recordedAt: Date
    formalRejectionCode?: FormalPiRunRejectionCode
  }
  historicalReconciliation?: {
    recordedAt: Date
    evidenceSha256: string
    usage: CandidateActivityUsage
  }
  historicalBilledAmountReconciliation?: {
    recordedAt: Date
    billedAmount: CandidateActivityHistoricalBilledAmount
  }
}): CandidateActivityLedger {
  validatePolicy(options.policy)
  const initializing = options.initialization !== undefined
  const reconcilingHistoricalUsage =
    options.historicalReconciliation !== undefined
  const reconcilingHistoricalBilledAmount =
    options.historicalBilledAmountReconciliation !== undefined
  const historicalUsage =
    options.historicalReconciliation === undefined
      ? null
      : safeUsage(options.historicalReconciliation.usage)
  const historicalBilledAmountRecordedAt =
    options.historicalBilledAmountReconciliation?.recordedAt
  const historicalBilledAmount =
    historicalBilledAmountRecordedAt !== undefined &&
    Number.isFinite(historicalBilledAmountRecordedAt.valueOf())
      ? safeHistoricalBilledAmount(
          options.historicalBilledAmountReconciliation!
            .billedAmount,
          historicalBilledAmountRecordedAt.toISOString(),
          options.policy,
        )
      : null
  if (
    Number(initializing) +
      Number(reconcilingHistoricalUsage) +
      Number(reconcilingHistoricalBilledAmount) >
      1 ||
    options.initialization !== undefined &&
    (!Number.isFinite(options.initialization.recordedAt.valueOf()) ||
      (options.initialization.formalRejectionCode !== undefined &&
        !isFormalPiRunRejectionCode(
          options.initialization.formalRejectionCode,
        )))
    ||
    (options.historicalReconciliation !== undefined &&
      (!Number.isFinite(
        options.historicalReconciliation.recordedAt.valueOf(),
      ) ||
        !SHA256.test(
          options.historicalReconciliation.evidenceSha256,
        ) ||
        historicalUsage === null ||
        historicalUsage.providerRequestCount < 1))
    ||
    (options.historicalBilledAmountReconciliation !== undefined &&
      historicalBilledAmount === null)
  ) {
    throw new CandidateActivityLedgerError(
      'activity_reservation_invalid',
    )
  }
  const directory = secureLedgerDirectory(
    options.repositoryRoot,
    initializing,
  )
  const path = resolve(
    directory,
    'deepseek-v4-flash-pi-0.82.1-v1-activity-v1.jsonl',
  )
  const anchorPath = resolve(
    directory,
    'deepseek-v4-flash-pi-0.82.1-v1-activity-anchor-v1.json',
  )
  const lockPath = resolve(
    directory,
    '.deepseek-v4-flash-pi-0.82.1-v1-activity-v1.lock',
  )
  let lockDescriptor: number | undefined
  let ledgerDescriptor: number | undefined
  let lockOwned = false

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
      const lockStats = fstatSync(lockDescriptor)
      assertOwned(lockStats)
      if (
        !lockStats.isFile() ||
        lockStats.nlink !== 1 ||
        permissionBits(lockStats) !== 0o600
      ) {
        throw new CandidateActivityLedgerError(
          'activity_ledger_invalid',
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
        throw new CandidateActivityLedgerError(
          'activity_ledger_busy',
        )
      }
      throw error
    }

    const ledgerExists = pathEntryExists(path)
    const anchorExists = pathEntryExists(anchorPath)
    const freshInitialization =
      initializing && !ledgerExists && !anchorExists
    const legacyPreLedgerAnchorMigration =
      options.initialization?.kind === 'pre_ledger_failure' &&
      ledgerExists &&
      !anchorExists
    if (
      options.initialization?.kind === 'clean'
        ? !freshInitialization
        : initializing
          ? !freshInitialization &&
            !legacyPreLedgerAnchorMigration
        : !ledgerExists || !anchorExists
    ) {
      throw new CandidateActivityLedgerError(
        'activity_ledger_invalid',
      )
    }
    ledgerDescriptor = openSync(
      path,
      (freshInitialization
        ? constants.O_CREAT | constants.O_EXCL
        : 0) |
        constants.O_APPEND |
        constants.O_RDWR |
        (constants.O_NOFOLLOW ?? 0),
      0o600,
    )
    assertSafeLedgerFile(path, fstatSync(ledgerDescriptor))
    if (freshInitialization) fsyncDirectory(directory)
    const parsed = parseLedger(
      readFileSync(ledgerDescriptor, 'utf8'),
      options.policy,
    )
    if (!initializing) {
      assertTailAnchorMatches(parsed, readTailAnchor(anchorPath))
    } else if (
      freshInitialization &&
      parsed.records.length !== 0
    ) {
      throw new CandidateActivityLedgerError(
        'activity_ledger_invalid',
      )
    } else if (
      legacyPreLedgerAnchorMigration &&
      (parsed.records.length !== 1 ||
        parsed.records[0]?.recordType !==
          'pre_ledger_reconciliation' ||
        parsed.reservations.size !== 0 ||
        parsed.lastRecordSha256 === null ||
        parsed.historicalUsageStatus !== 'unresolved' ||
        !approximatelyEqual(
          parsed.committedCostCny,
          COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
        ))
    ) {
      throw new CandidateActivityLedgerError(
        'activity_ledger_invalid',
      )
    }
    let closed = false

    const append = (
      withoutSha: UnsignedActivityRecord,
      committedCostCnyAfter: number,
    ): ActivityRecord => {
      if (
        closed ||
        parsed.records.length >= MAX_RECORDS ||
        !finiteNonnegative(committedCostCnyAfter)
      ) {
        throw new CandidateActivityLedgerError(
          'activity_ledger_invalid',
        )
      }
      const record = {
        ...withoutSha,
        recordSha256: canonicalSha256(withoutSha),
      } as ActivityRecord
      const line = `${JSON.stringify(record)}\n`
      const lineBytes = Buffer.byteLength(line, 'utf8')
      if (
        lineBytes > MAX_RECORD_BYTES ||
        fstatSync(ledgerDescriptor!).size + lineBytes >
          MAX_LEDGER_BYTES
      ) {
        throw new CandidateActivityLedgerError(
          'activity_ledger_invalid',
        )
      }
      writeComplete(ledgerDescriptor!, line)
      fsyncSync(ledgerDescriptor!)
      writeTailAnchorAtomically(directory, anchorPath, {
        schemaVersion:
          COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_SCHEMA,
        recordCount: parsed.records.length + 1,
        lastRecordSha256: record.recordSha256,
        committedCostCny: committedCostCnyAfter,
      })
      parsed.records.push(record)
      parsed.lastRecordSha256 = record.recordSha256
      parsed.committedCostCny = committedCostCnyAfter
      return record
    }

    if (reconcilingHistoricalBilledAmount) {
      const reconciliation =
        options.historicalBilledAmountReconciliation!
      const billed = historicalBilledAmount!
      const reconciledRecord = parsed.records[0]
      const existingSettlement = parsed.records[1]
      if (parsed.historicalUsageStatus === 'resolved') {
        if (
          reconciledRecord?.recordType !==
            'pre_ledger_reconciliation' ||
          existingSettlement?.recordType !==
            'pre_ledger_historical_billed_amount_settlement' ||
          existingSettlement.reconcilesRecordSha256 !==
            reconciledRecord.recordSha256 ||
          !matchesHistoricalBilledAmount(
            existingSettlement,
            billed,
          )
        ) {
          throw new CandidateActivityLedgerError(
            'activity_reservation_invalid',
          )
        }
      } else {
        if (
          parsed.records.length !== 1 ||
          reconciledRecord?.recordType !==
            'pre_ledger_reconciliation' ||
          parsed.reservations.size !== 0
        ) {
          throw new CandidateActivityLedgerError(
            'activity_reservation_invalid',
          )
        }
        const recordedAt = reconciliation.recordedAt.toISOString()
        const activityId = `candidate-activity:${canonicalSha256({
          nonce: randomUUID(),
          recordedAt,
          historicalBilledAmountReconciliation: true,
          evidenceSha256: billed.evidenceSha256,
          candidateId: options.policy.candidateId,
        })}`
        const priorCommittedCostCny = parsed.committedCostCny
        append(
          {
            schemaVersion:
              COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_SCHEMA,
            sequence: 2,
            previousRecordSha256:
              reconciledRecord.recordSha256,
            recordedAt,
            recordType:
              'pre_ledger_historical_billed_amount_settlement',
            activityId,
            candidateId: options.policy.candidateId,
            candidateResourceSha256:
              options.policy.candidateResourceSha256,
            provider: options.policy.provider,
            model: options.policy.model,
            runNumber: 1,
            reconciliationReason:
              'operator_supplied_historical_provider_billed_amount',
            evidenceDescription:
              COUNTRY_OUTAGE_PRE_LEDGER_BILLING_EVIDENCE_DESCRIPTION,
            evidenceSha256: billed.evidenceSha256,
            reconcilesRecordSha256:
              reconciledRecord.recordSha256,
            historicalAttemptStartedAtUtc:
              COUNTRY_OUTAGE_PRE_LEDGER_HISTORICAL_ATTEMPT_STARTED_AT_UTC,
            historicalAttemptEndedAtUtc:
              COUNTRY_OUTAGE_PRE_LEDGER_HISTORICAL_ATTEMPT_ENDED_AT_UTC,
            evidenceWindowStartUtc:
              billed.evidenceWindowStartUtc,
            evidenceWindowEndUtc: billed.evidenceWindowEndUtc,
            evidenceTimezone: billed.evidenceTimezone,
            evidenceAcquiredAt: billed.evidenceAcquiredAt,
            billingFinality: 'settled_final',
            billingScope: billed.billingScope,
            billedAmountDecimal: billed.billedAmountDecimal,
            billedCurrency: billed.billedCurrency,
            conversionBasis: billed.conversionBasis,
            conversionRateCnyPerUnitDecimal:
              billed.conversionRateCnyPerUnitDecimal,
            convertedBilledCostCnyE8:
              billed.convertedBilledCostCnyE8,
            chargedCostCnyE8: billed.chargedCostCnyE8,
            budgetChargeBasis:
              'max_legacy_floor_and_converted_billed_amount',
            priorCommittedCostCny,
            adjustmentCostCny: billed.adjustmentCostCny,
            chargedCostCny: billed.chargedCostCny,
            committedCostCnyAfter: billed.chargedCostCny,
            usage: null,
          },
          billed.chargedCostCny,
        )
        parsed.historicalUsageStatus = 'resolved'
      }
    } else if (reconcilingHistoricalUsage) {
      const reconciliation =
        options.historicalReconciliation!
      const usage = historicalUsage!
      const chargedCostCny = candidateActivityUsageCostCny(
        options.policy,
        usage,
      )
      const reconciledRecord = parsed.records[0]
      if (
        parsed.records.length !== 1 ||
        reconciledRecord?.recordType !==
          'pre_ledger_reconciliation' ||
        parsed.historicalUsageStatus !== 'unresolved' ||
        parsed.reservations.size !== 0 ||
        chargedCostCny + COST_EPSILON <
          COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY
      ) {
        throw new CandidateActivityLedgerError(
          'activity_reservation_invalid',
        )
      }
      const activityId = `candidate-activity:${canonicalSha256({
        nonce: randomUUID(),
        recordedAt: reconciliation.recordedAt.toISOString(),
        historicalReconciliation: true,
        evidenceSha256: reconciliation.evidenceSha256,
        candidateId: options.policy.candidateId,
      })}`
      const priorCommittedCostCny = parsed.committedCostCny
      append(
        {
          schemaVersion:
            COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_SCHEMA,
          sequence: 2,
          previousRecordSha256: reconciledRecord.recordSha256,
          recordedAt: reconciliation.recordedAt.toISOString(),
          recordType: 'pre_ledger_historical_settlement',
          activityId,
          candidateId: options.policy.candidateId,
          candidateResourceSha256:
            options.policy.candidateResourceSha256,
          provider: options.policy.provider,
          model: options.policy.model,
          runNumber: 1,
          reconciliationReason:
            'operator_supplied_historical_provider_usage',
          evidenceDescription:
            COUNTRY_OUTAGE_PRE_LEDGER_EVIDENCE_DESCRIPTION,
          evidenceSha256: reconciliation.evidenceSha256,
          reconcilesRecordSha256:
            reconciledRecord.recordSha256,
          priorCommittedCostCny,
          adjustmentCostCny:
            chargedCostCny - priorCommittedCostCny,
          chargedCostCny,
          committedCostCnyAfter: chargedCostCny,
          usage,
        },
        chargedCostCny,
      )
      parsed.historicalUsageStatus = 'resolved'
    } else if (legacyPreLedgerAnchorMigration) {
      writeTailAnchorAtomically(directory, anchorPath, {
        schemaVersion:
          COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_ANCHOR_SCHEMA,
        recordCount: 1,
        lastRecordSha256: parsed.lastRecordSha256!,
        committedCostCny: parsed.committedCostCny,
      })
    } else if (freshInitialization) {
      const initialization = options.initialization!
      const activityId = `candidate-activity:${canonicalSha256({
        nonce: randomUUID(),
        recordedAt: initialization.recordedAt.toISOString(),
        reconciliation: true,
        candidateId: options.policy.candidateId,
      })}`
      if (initialization.kind === 'clean') {
        append(
          {
            schemaVersion:
              COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_SCHEMA,
            sequence: 1,
            previousRecordSha256: null,
            recordedAt: initialization.recordedAt.toISOString(),
            recordType: 'genesis',
            activityId,
            candidateId: options.policy.candidateId,
            candidateResourceSha256:
              options.policy.candidateResourceSha256,
            provider: options.policy.provider,
            model: options.policy.model,
            runNumber: null,
            initializationReason:
              'clean_environment_no_prior_provider_activity',
            priorProviderActivity: false,
            committedCostCnyAfter: 0,
          },
          0,
        )
        parsed.historicalUsageStatus = 'resolved'
      } else {
        append(
          {
            schemaVersion:
              COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_SCHEMA,
            sequence: 1,
            previousRecordSha256: null,
            recordedAt: initialization.recordedAt.toISOString(),
            recordType: 'pre_ledger_reconciliation',
            activityId,
            candidateId: options.policy.candidateId,
            candidateResourceSha256:
              options.policy.candidateResourceSha256,
            provider: options.policy.provider,
            model: options.policy.model,
            runNumber: 1,
            attemptedAt: null,
            providerRunInitiatedAtReconciliation: false,
            reconciliationReason:
              'pre_ledger_failed_provider_run_usage_unavailable',
            costBasis:
              'worst_case_single_report_reservation',
            chargedCostCny:
              COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
            committedCostCnyAfter:
              COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
            formalRejectionCode:
              initialization.formalRejectionCode ?? null,
            candidateRejectionCode: 'candidate_runner_failed',
            usage: null,
          },
          COUNTRY_OUTAGE_LEGACY_PRE_LEDGER_COST_CNY,
        )
        parsed.historicalUsageStatus = 'unresolved'
      }
    }

    return {
      path,
      anchorPath,
      snapshot() {
        return Object.freeze({
          committedCostCny: parsed.committedCostCny,
          remainingBudgetCny:
            options.policy.budgetLimitCny -
            parsed.committedCostCny,
          openReservations: [...parsed.reservations.values()].filter(
            (item) => !item.settled,
          ).length,
          recordCount: parsed.records.length,
          historicalUsageStatus: parsed.historicalUsageStatus,
        })
      },
      assertCertificationBudgetAvailable() {
        if (parsed.historicalUsageStatus !== 'resolved') {
          throw new CandidateActivityLedgerError(
            'activity_historical_usage_unresolved',
          )
        }
        if (
          !Number.isFinite(parsed.committedCostCny) ||
          parsed.committedCostCny +
            options.policy.maximumCertificationCostCny >
            options.policy.budgetLimitCny +
              COST_EPSILON
        ) {
          throw new CandidateActivityLedgerError(
            'activity_budget_preflight_failed',
          )
        }
      },
      reserve(runNumber, recordedAt) {
        if (parsed.historicalUsageStatus !== 'resolved') {
          throw new CandidateActivityLedgerError(
            'activity_historical_usage_unresolved',
          )
        }
        if (
          closed ||
          ![1, 2, 3, 4, 5].includes(runNumber) ||
          !Number.isFinite(recordedAt.valueOf()) ||
          parsed.committedCostCny +
            options.policy.maximumSingleReportCostCny >
            options.policy.budgetLimitCny +
              COST_EPSILON
        ) {
          throw new CandidateActivityLedgerError(
            'activity_reservation_invalid',
          )
        }
        const activityId = `candidate-activity:${canonicalSha256({
          nonce: randomUUID(),
          recordedAt: recordedAt.toISOString(),
          runNumber,
          sequence: parsed.records.length + 1,
          candidateId: options.policy.candidateId,
        })}`
        const priorCommittedCostCny = parsed.committedCostCny
        const committedCostCnyAfter =
          priorCommittedCostCny +
          options.policy.maximumSingleReportCostCny
        const record = append(
          {
            schemaVersion:
              COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_SCHEMA,
            sequence: parsed.records.length + 1,
            previousRecordSha256: parsed.lastRecordSha256,
            recordedAt: recordedAt.toISOString(),
            recordType: 'reservation',
            activityId,
            candidateId: options.policy.candidateId,
            candidateResourceSha256:
              options.policy.candidateResourceSha256,
            provider: options.policy.provider,
            model: options.policy.model,
            runNumber,
            budgetLimitCny: options.policy.budgetLimitCny,
            reservedCostCny:
              options.policy.maximumSingleReportCostCny,
            priorCommittedCostCny,
            remainingBudgetBeforeCny:
              options.policy.budgetLimitCny -
              priorCommittedCostCny,
          },
          committedCostCnyAfter,
        ) as ReservationRecord
        parsed.reservations.set(activityId, {
          record,
          settled: false,
        })
        return Object.freeze({
          activityId,
          runNumber,
          reservedCostCny: record.reservedCostCny,
        })
      },
      settle(reservation, settlementOptions) {
        const state = parsed.reservations.get(reservation.activityId)
        const usage =
          settlementOptions.usage === undefined
            ? null
            : safeUsage(settlementOptions.usage)
        if (
          closed ||
          !state ||
          state.settled ||
          state.record.runNumber !== reservation.runNumber ||
          !approximatelyEqual(
            state.record.reservedCostCny,
            reservation.reservedCostCny,
          ) ||
          !Number.isFinite(settlementOptions.recordedAt.valueOf()) ||
          (settlementOptions.usage !== undefined && usage === null) ||
          (settlementOptions.outcome === 'completed' && usage === null) ||
          (settlementOptions.outcome === 'completed' &&
            (settlementOptions.formalRejectionCode !== undefined ||
              settlementOptions.candidateRejectionCode !== undefined)) ||
          (settlementOptions.outcome === 'rejected' &&
            settlementOptions.formalRejectionCode === undefined &&
            settlementOptions.candidateRejectionCode === undefined) ||
          (settlementOptions.formalRejectionCode !== undefined &&
            !isFormalPiRunRejectionCode(
              settlementOptions.formalRejectionCode,
            )) ||
          (settlementOptions.candidateRejectionCode !== undefined &&
            !isCandidateActivityRejectionCode(
              settlementOptions.candidateRejectionCode,
            ))
        ) {
          throw new CandidateActivityLedgerError(
            'activity_reservation_invalid',
          )
        }
        const chargedCostCny =
          usage === null
            ? state.record.reservedCostCny
            : candidateActivityUsageCostCny(options.policy, usage)
        const committedCostCnyAfter =
          parsed.committedCostCny -
          state.record.reservedCostCny +
          chargedCostCny
        append(
          {
            schemaVersion:
              COUNTRY_OUTAGE_CANDIDATE_ACTIVITY_LEDGER_SCHEMA,
            sequence: parsed.records.length + 1,
            previousRecordSha256: parsed.lastRecordSha256,
            recordedAt:
              settlementOptions.recordedAt.toISOString(),
            recordType: 'settlement',
            activityId: reservation.activityId,
            candidateId: options.policy.candidateId,
            candidateResourceSha256:
              options.policy.candidateResourceSha256,
            provider: options.policy.provider,
            model: options.policy.model,
            runNumber: reservation.runNumber,
            outcome: settlementOptions.outcome,
            costBasis:
              usage === null
                ? 'worst_case_reservation'
                : 'actual_usage',
            chargedCostCny,
            committedCostCnyAfter,
            formalRejectionCode:
              settlementOptions.formalRejectionCode ?? null,
            candidateRejectionCode:
              settlementOptions.candidateRejectionCode ?? null,
            usage,
          },
          committedCostCnyAfter,
        )
        state.settled = true
      },
      close() {
        if (closed) return
        closed = true
        if (ledgerDescriptor !== undefined) {
          closeSync(ledgerDescriptor)
          ledgerDescriptor = undefined
        }
        if (lockDescriptor !== undefined) {
          closeSync(lockDescriptor)
          lockDescriptor = undefined
        }
        if (lockOwned) {
          rmSync(lockPath, { force: true })
          lockOwned = false
          fsyncDirectory(directory)
        }
      },
    }
  } catch (error) {
    if (ledgerDescriptor !== undefined) closeSync(ledgerDescriptor)
    if (lockDescriptor !== undefined) closeSync(lockDescriptor)
    if (lockOwned) rmSync(lockPath, { force: true })
    if (error instanceof CandidateActivityLedgerError) throw error
    throw new CandidateActivityLedgerError(
      'activity_ledger_invalid',
    )
  }
}

export function openCandidateActivityLedger(options: {
  repositoryRoot: string
  policy: CandidateActivityBudgetPolicy
}): CandidateActivityLedger {
  return openCandidateActivityLedgerInternal(options)
}

/**
 * @internal 仅供显式 reconcile CLI 的产品包装调用。它不是认证/付费运行入口。
 * legacy 分支只接受唯一一条合法 pre-ledger 记录并补 anchor，不追加第二笔费用。
 */
export function initializeCandidateActivityLedgerWithPreLedgerFailure(
  options: {
    repositoryRoot: string
    policy: CandidateActivityBudgetPolicy
    recordedAt: Date
    formalRejectionCode?: FormalPiRunRejectionCode
  },
): CandidateActivityLedger {
  return openCandidateActivityLedgerInternal({
    repositoryRoot: options.repositoryRoot,
    policy: options.policy,
    initialization: {
      kind: 'pre_ledger_failure',
      recordedAt: options.recordedAt,
      ...(options.formalRejectionCode === undefined
        ? {}
        : {
            formalRejectionCode: options.formalRejectionCode,
          }),
    },
  })
}

/**
 * @internal 仅供显式 clean-environment initializer 的产品包装调用。
 * 只在 ledger 与 anchor 同时不存在时建立零成本 genesis；正式认证入口不会调用。
 */
export function initializeCleanCandidateActivityLedger(options: {
  repositoryRoot: string
  policy: CandidateActivityBudgetPolicy
  recordedAt: Date
}): CandidateActivityLedger {
  return openCandidateActivityLedgerInternal({
    repositoryRoot: options.repositoryRoot,
    policy: options.policy,
    initialization: {
      kind: 'clean',
      recordedAt: options.recordedAt,
    },
  })
}

/**
 * @internal 仅供一次性历史实际用量结清 CLI 的产品包装调用。
 * 调用方只能提交固定字段和证据摘要；本函数不接受、也不读取任意证据路径。
 */
export function reconcileCandidateActivityLedgerHistoricalUsage(
  options: {
    repositoryRoot: string
    policy: CandidateActivityBudgetPolicy
    recordedAt: Date
    evidenceSha256: string
    usage: CandidateActivityUsage
  },
): CandidateActivityLedger {
  return openCandidateActivityLedgerInternal({
    repositoryRoot: options.repositoryRoot,
    policy: options.policy,
    historicalReconciliation: {
      recordedAt: options.recordedAt,
      evidenceSha256: options.evidenceSha256,
      usage: options.usage,
    },
  })
}

/**
 * @internal 仅供固定 A4 首次调用的最终账单金额结清 CLI 包装调用。
 * 金额、币种与证据元数据均为固定字段；本函数不接受证据路径，不读取认证文件，
 * 也不访问网络或创建模型运行时。
 */
export function reconcileCandidateActivityLedgerHistoricalBilledAmount(
  options: {
    repositoryRoot: string
    policy: CandidateActivityBudgetPolicy
    recordedAt: Date
    billedAmount: CandidateActivityHistoricalBilledAmount
  },
): CandidateActivityLedger {
  return openCandidateActivityLedgerInternal({
    repositoryRoot: options.repositoryRoot,
    policy: options.policy,
    historicalBilledAmountReconciliation: {
      recordedAt: options.recordedAt,
      billedAmount: options.billedAmount,
    },
  })
}
