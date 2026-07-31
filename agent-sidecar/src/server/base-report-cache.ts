import { deserialize, serialize } from 'node:v8'

import type {
  CountryOutageReportDocument,
  ReportModelIdentity,
} from '../report/contracts.js'
import type { ReportGenerationResult } from './contracts.js'
import { CountryOutageHttpError } from './errors.js'

export const DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_TTL_MS =
  3_600_000
export const DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_MAX_ENTRIES = 64
export const DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_MAX_TOTAL_BYTES =
  128 * 1024 * 1024
const COUNTRY_OUTAGE_REFERENCE =
  /^country_outage\/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}\/([A-Z]{2})\/\d+\/r$/

export interface CountryOutageReportServiceIdentity {
  reportSpecificationVersion:
    CountryOutageReportDocument['reportSpecificationVersion']
  projectKnowledgeVersion:
    CountryOutageReportDocument['projectKnowledgeVersion']
  validatorRulesVersion: string
  skillBundleSha256: string
  model: ReportModelIdentity
}

export interface CountryOutageBaseReportCacheKey {
  authorizationScope: string
  eventReference: string
  publicationId: string
  revision: number
  reportServiceIdentity: CountryOutageReportServiceIdentity
}

export interface CountryOutageBaseReportCacheOptions {
  ttlMs?: number
  maxEntries?: number
  maxTotalBytes?: number
  now?: () => number
}

export interface CountryOutageBaseReportCachingOptions {
  reportServiceIdentity: CountryOutageReportServiceIdentity
  ttlMs?: number
  store?: CountryOutageBaseReportCache
}

interface StoredBaseReport {
  expiresAtMs: number
  storedAtMs: number
  sequence: number
  serializedResult: Buffer
}

function normalizedReference(value: string): string {
  return value.replace(' ', '+')
}

function modelIdentityKey(identity: ReportModelIdentity): readonly unknown[] {
  return [
    identity.provider,
    identity.model,
    identity.modelVersion,
    identity.adapter,
    identity.piVersion ?? null,
  ]
}

function keyOf(key: CountryOutageBaseReportCacheKey): string {
  return JSON.stringify([
    key.authorizationScope,
    normalizedReference(key.eventReference),
    key.publicationId,
    key.revision,
    key.reportServiceIdentity.reportSpecificationVersion,
    key.reportServiceIdentity.projectKnowledgeVersion,
    key.reportServiceIdentity.validatorRulesVersion,
    key.reportServiceIdentity.skillBundleSha256,
    ...modelIdentityKey(key.reportServiceIdentity.model),
  ])
}

function sameModelIdentity(
  left: ReportModelIdentity,
  right: ReportModelIdentity,
): boolean {
  return (
    left.provider === right.provider &&
    left.model === right.model &&
    left.modelVersion === right.modelVersion &&
    left.adapter === right.adapter &&
    (left.piVersion ?? null) === (right.piVersion ?? null)
  )
}

function sameSnapshot(
  left: CountryOutageReportDocument['snapshot'],
  right: CountryOutageReportDocument['snapshot'],
): boolean {
  return (
    left.incidentId === right.incidentId &&
    left.publicationId === right.publicationId &&
    left.revision === right.revision &&
    left.dataThrough === right.dataThrough &&
    left.isFinal === right.isFinal &&
    left.cohortId === right.cohortId &&
    left.collectorId === right.collectorId &&
    left.windowStartUtc === right.windowStartUtc &&
    left.windowEndUtc === right.windowEndUtc
  )
}

function matchesKey(
  key: CountryOutageBaseReportCacheKey,
  result: ReportGenerationResult,
): boolean {
  const { document, artifacts, questionContext } = result
  const expectedCountryCode =
    COUNTRY_OUTAGE_REFERENCE.exec(key.eventReference)?.[1]
  return (
    document.validation.passed === true &&
    normalizedReference(document.event.legacy_reference) ===
      normalizedReference(key.eventReference) &&
    document.event.incident_id === document.snapshot.incidentId &&
    document.event.event_type === 'country_outage' &&
    Boolean(expectedCountryCode) &&
    document.event.country_code === expectedCountryCode &&
    document.event.country_name.trim().length > 0 &&
    document.event.display_name.trim().length > 0 &&
    document.snapshot.publicationId === key.publicationId &&
    document.snapshot.revision === key.revision &&
    document.snapshot.collectorId === 'rrc25' &&
    document.reportSpecificationVersion ===
      key.reportServiceIdentity.reportSpecificationVersion &&
    document.projectKnowledgeVersion ===
      key.reportServiceIdentity.projectKnowledgeVersion &&
    document.validatorRulesVersion ===
      key.reportServiceIdentity.validatorRulesVersion &&
    document.skillBundleSha256 ===
      key.reportServiceIdentity.skillBundleSha256 &&
    sameModelIdentity(document.model, key.reportServiceIdentity.model) &&
    artifacts.artifactId === document.artifactId &&
    (!questionContext ||
      (questionContext.factSetId === document.factSetId &&
        sameSnapshot(questionContext.snapshot, document.snapshot)))
  )
}

export function freezeCountryOutageReportServiceIdentity(
  identity: CountryOutageReportServiceIdentity,
): CountryOutageReportServiceIdentity {
  const normalized: CountryOutageReportServiceIdentity = {
    reportSpecificationVersion: identity.reportSpecificationVersion,
    projectKnowledgeVersion: identity.projectKnowledgeVersion,
    validatorRulesVersion: identity.validatorRulesVersion,
    skillBundleSha256: identity.skillBundleSha256.toLowerCase(),
    model: {
      provider: identity.model.provider,
      model: identity.model.model,
      modelVersion: identity.model.modelVersion,
      adapter: identity.model.adapter,
      ...(identity.model.piVersion
        ? { piVersion: identity.model.piVersion }
        : {}),
    },
  }
  if (
    !normalized.reportSpecificationVersion ||
    !normalized.projectKnowledgeVersion ||
    !normalized.validatorRulesVersion.trim() ||
    !/^[a-f0-9]{64}$/.test(normalized.skillBundleSha256) ||
    !normalized.model.provider.trim() ||
    !normalized.model.model.trim() ||
    !normalized.model.modelVersion.trim()
  ) {
    throw new Error(
      'reportServiceIdentity 必须完整固定报告规范、项目知识、校验规则、Skill 摘要和模型身份',
    )
  }
  Object.freeze(normalized.model)
  return Object.freeze(normalized)
}

export function assertBaseReportMatchesCacheKey(
  key: CountryOutageBaseReportCacheKey,
  result: ReportGenerationResult,
): void {
  if (!matchesKey(key, result)) {
    throw new CountryOutageHttpError(
      409,
      'report_service_identity_conflict',
      '基础报告与事件快照或固定报告服务身份不一致',
      true,
      '使用当前已认证报告服务重新生成',
    )
  }
}

/**
 * 进程内短期基础报告缓存。缓存内容以 V8 序列化快照保存，每次命中都反序列化
 * 为新对象，避免不同用户会话共享可变 document、questionContext 或 Buffer。
 */
export class CountryOutageBaseReportCache {
  readonly #ttlMs: number
  readonly #maxEntries: number
  readonly #maxTotalBytes: number
  readonly #now: () => number
  readonly #entries = new Map<string, StoredBaseReport>()
  #totalBytes = 0
  #nextSequence = 0

  constructor(options: CountryOutageBaseReportCacheOptions = {}) {
    const ttlMs =
      options.ttlMs ??
      DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_TTL_MS
    if (!Number.isSafeInteger(ttlMs) || ttlMs <= 0) {
      throw new Error('基础报告缓存 TTL 必须是正整数')
    }
    const maxEntries =
      options.maxEntries ??
      DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_MAX_ENTRIES
    const maxTotalBytes =
      options.maxTotalBytes ??
      DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_MAX_TOTAL_BYTES
    if (!Number.isSafeInteger(maxEntries) || maxEntries <= 0) {
      throw new Error('基础报告缓存最大条目数必须是正整数')
    }
    if (!Number.isSafeInteger(maxTotalBytes) || maxTotalBytes <= 0) {
      throw new Error('基础报告缓存最大总字节数必须是正整数')
    }
    this.#ttlMs = ttlMs
    this.#maxEntries = maxEntries
    this.#maxTotalBytes = maxTotalBytes
    this.#now = options.now ?? Date.now
  }

  get ttlMs(): number {
    return this.#ttlMs
  }

  get maxEntries(): number {
    return this.#maxEntries
  }

  get maxTotalBytes(): number {
    return this.#maxTotalBytes
  }

  #delete(cacheKey: string): void {
    const entry = this.#entries.get(cacheKey)
    if (!entry) return
    this.#entries.delete(cacheKey)
    this.#totalBytes -= entry.serializedResult.byteLength
  }

  #evictOne(): boolean {
    let selected:
      | { cacheKey: string; entry: StoredBaseReport }
      | undefined
    for (const [cacheKey, entry] of this.#entries) {
      if (
        !selected ||
        entry.expiresAtMs < selected.entry.expiresAtMs ||
        (entry.expiresAtMs === selected.entry.expiresAtMs &&
          (entry.storedAtMs < selected.entry.storedAtMs ||
            (entry.storedAtMs === selected.entry.storedAtMs &&
              entry.sequence < selected.entry.sequence)))
      ) {
        selected = { cacheKey, entry }
      }
    }
    if (!selected) return false
    this.#delete(selected.cacheKey)
    return true
  }

  get(
    key: CountryOutageBaseReportCacheKey,
  ): ReportGenerationResult | undefined {
    const cacheKey = keyOf(key)
    const entry = this.#entries.get(cacheKey)
    if (!entry) return undefined
    if (this.#now() >= entry.expiresAtMs) {
      this.#delete(cacheKey)
      return undefined
    }
    try {
      const result = deserialize(
        entry.serializedResult,
      ) as ReportGenerationResult
      if (!matchesKey(key, result)) {
        this.#delete(cacheKey)
        return undefined
      }
      return result
    } catch {
      this.#delete(cacheKey)
      return undefined
    }
  }

  set(
    key: CountryOutageBaseReportCacheKey,
    result: ReportGenerationResult,
  ): boolean {
    assertBaseReportMatchesCacheKey(key, result)
    let serializedResult: Buffer
    try {
      serializedResult = serialize(result)
    } catch {
      return false
    }
    if (serializedResult.byteLength > this.#maxTotalBytes) {
      return false
    }
    const cacheKey = keyOf(key)
    this.sweep()
    this.#delete(cacheKey)
    while (
      this.#entries.size >= this.#maxEntries ||
      this.#totalBytes + serializedResult.byteLength >
        this.#maxTotalBytes
    ) {
      if (!this.#evictOne()) return false
    }
    const storedAtMs = this.#now()
    this.#entries.set(cacheKey, {
      expiresAtMs: storedAtMs + this.#ttlMs,
      storedAtMs,
      sequence: this.#nextSequence++,
      serializedResult,
    })
    this.#totalBytes += serializedResult.byteLength
    return true
  }

  sweep(): void {
    const now = this.#now()
    for (const [key, entry] of this.#entries) {
      if (now >= entry.expiresAtMs) this.#delete(key)
    }
  }
}
