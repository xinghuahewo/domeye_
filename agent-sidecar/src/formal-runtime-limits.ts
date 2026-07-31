import type { JsonObject } from './domain/contracts.js'
import type { ReportEvidenceBundle } from './report/contracts.js'

export const FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS = Object.freeze({
  modelAttemptTimeoutMs: 75_000,
  maximumModelAttempts: 2,
  minimumModelContextWindowTokens: 64_000,
  maximumContextInputTokens: 64_000,
  maximumModelOutputTokens: 16_384,
  // DeepSeek 官方 tokenizer 与服务端 chat framing 当前未随候选资源固定。
  // 因此正式运行在 adapter 完成最终 payload 后、HTTP 发送前使用更窄的
  // UTF-8 字节门，并为服务端 framing 预留 4,096 token。任意一轮只有在
  // `payload bytes + framing reserve <= 64,000` 时才可触达供应商。
  maximumProviderPayloadBytes: 59_904,
  providerFramingTokenReserve: 4_096,
  // 每次报告最多向上游供应商发起五轮请求。工具返回错误不会终止 Pi
  // agent loop，因此该限制必须直接位于 streamFunction 边界。
  maximumProviderRequestsPerReport: 5,
  // Context JSON 的 900,000-byte 门仍是进入 adapter 前的容量/DoS 上限；
  // 它不是计费 token 上限。费用上界由上面的最终 payload 门单独执行。
  maximumProviderContextBytes: 900_000,
  maximumEvidenceRecords: 2_000,
  // 总上限保留为四次，以与“最多五轮供应商请求（工具轮次加最终
  // 叙述/修订轮次）”的冻结候选合同一致；逐工具上限的和为三次，
  // 因而正式路径实际最多执行 resolve、observation、ASN 各一次。
  maximumToolExecutions: 4,
  // 附加安全上限：三个工具结果既受单次限制，也受同一报告累计限制。
  // 这样会在模型上下文膨胀前失败，并为系统提示、Skill、工具 schema
  // 和消息 framing 留出确定空间；最终 59,904-byte 发送前门仍是权威上限。
  maximumToolResultBytes: 24_576,
  maximumCumulativeToolResultBytes: 36_864,
  maximumToolExecutionsByName: Object.freeze({
    country_outage_resolve: 1,
    country_outage_get_observation: 1,
    country_outage_get_asns: 1,
  }),
} as const)

export interface CountryOutageEvidenceRecordCount {
  fixedFacts: number
  capabilities: number
  qualityLimitations: number
  eligibilityRecords: number
  verifiedHashes: number
  visibilitySeries: number
  resourceSeries: number
  keyVisibilityPoints: number
  derivedFacts: number
  metricExtrema: number
  resourceMetricExtrema: number
  annotations: number
  asnItems: number
  total: number
}

export class CountryOutageEvidenceCapacityError extends Error {
  readonly code = 'evidence_record_limit_exceeded' as const

  constructor(
    readonly count: CountryOutageEvidenceRecordCount,
  ) {
    super(
      `报告证据记录数 ${count.total} 超过冻结上限 ${FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumEvidenceRecords}`,
    )
    this.name = 'CountryOutageEvidenceCapacityError'
  }
}

function isRecord(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function countExtremaRecords(extrema: JsonObject): number {
  return Object.values(extrema).reduce<number>((total, metric) => {
    if (!isRecord(metric)) return total + 1
    const pointCount = ['min', 'max'].filter((key) =>
      Object.prototype.hasOwnProperty.call(metric, key),
    ).length
    return total + Math.max(1, pointCount)
  }, 0)
}

export function countCountryOutageEvidenceRecords(
  evidence: ReportEvidenceBundle,
): CountryOutageEvidenceRecordCount {
  const { facts } = evidence
  const count = {
    // event、scope、cohort、audit 各作为一条固定事实记录。
    fixedFacts: 4,
    capabilities: Object.keys(facts.capabilities).length,
    qualityLimitations: facts.quality.limitations.length,
    eligibilityRecords:
      facts.eligibility.reasons.length +
      facts.eligibility.missingRequiredFields.length +
      Object.keys(facts.eligibility.degradedCapabilities).length,
    verifiedHashes: Object.keys(facts.audit.verifiedHashes).length,
    visibilitySeries: facts.series.length,
    resourceSeries: facts.resourceSeries.length,
    keyVisibilityPoints: facts.keyVisibilityPoints.length,
    derivedFacts: facts.derivedFacts.length,
    metricExtrema: countExtremaRecords(facts.metricExtrema),
    resourceMetricExtrema: countExtremaRecords(
      facts.resourceMetricExtrema,
    ),
    annotations: facts.annotations.length,
    asnItems: evidence.asnPages.reduce(
      (total, page) => total + page.items.length,
      0,
    ),
  }
  return {
    ...count,
    total:
      count.fixedFacts +
      count.capabilities +
      count.qualityLimitations +
      count.eligibilityRecords +
      count.verifiedHashes +
      count.visibilitySeries +
      count.resourceSeries +
      count.keyVisibilityPoints +
      count.derivedFacts +
      count.metricExtrema +
      count.resourceMetricExtrema +
      count.annotations +
      count.asnItems,
  }
}

export function assertCountryOutageEvidenceCapacity(
  evidence: ReportEvidenceBundle,
): CountryOutageEvidenceRecordCount {
  const count = countCountryOutageEvidenceRecords(evidence)
  if (
    count.total >
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumEvidenceRecords
  ) {
    throw new CountryOutageEvidenceCapacityError(count)
  }
  return count
}
