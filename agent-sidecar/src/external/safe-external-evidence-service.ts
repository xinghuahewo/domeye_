import { createHash } from 'node:crypto'
import { BlockList, isIP } from 'node:net'
import { isDeepStrictEqual } from 'node:util'

import { compareUnicodeCodePoints } from '../shared/deterministic-json.js'
import {
  COUNTRY_OUTAGE_EXTERNAL_APPENDIX_SCHEMA_VERSION,
  COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY_VERSION,
  COUNTRY_OUTAGE_EXTERNAL_STRUCTURED_FACT_SCHEMA_VERSION,
  type CountryOutageExternalEvidenceService,
  type ExternalComparableAddressFamily,
  type ExternalComparableFact,
  type ExternalComparableNormalizedValue,
  type ExternalComparableSourceValue,
  type ExternalDnsAddress,
  type ExternalDnsResolver,
  type ExternalEvidenceAppendix,
  type ExternalEvidenceClaim,
  type ExternalEvidenceComparisonStatus,
  type ExternalEvidenceFrozenBinding,
  type ExternalEvidenceRequest,
  type ExternalEvidenceSource,
  type ExternalHttpResponse,
  type ExternalHttpTransport,
  type ExternalSourceClassification,
  type ExternalSourceTier,
} from './contracts.js'
import { ExternalEvidenceSafetyError } from './errors.js'
import {
  NodeExternalDnsResolver,
  PinnedNodeHttpTransport,
} from './safe-http-transport.js'

export const COUNTRY_OUTAGE_EXTERNAL_RUNTIME_LIMITS = Object.freeze({
  minimumUrls: 1,
  maximumUrls: 5,
  maximumPages: 5,
  authorizationValiditySeconds: 300,
  allowedHostBoundaries: Object.freeze([
    'bgp.he.net',
    'radar.cloudflare.com',
  ] as const),
  explicitPublicUrlsRequired: true,
  urlDiscoveryAllowed: false,
  maximumResponseBytesPerPage: 2 * 1024 * 1024,
  maximumRedirects: 3,
  allowedSchemes: Object.freeze(['http', 'https'] as const),
  allowPrivateNetworks: false,
  allowAuthenticatedPages: false,
  allowFileUploads: false,
})

const MAXIMUM_PAGES =
  COUNTRY_OUTAGE_EXTERNAL_RUNTIME_LIMITS.maximumPages
const MAXIMUM_RESPONSE_BYTES =
  COUNTRY_OUTAGE_EXTERNAL_RUNTIME_LIMITS
    .maximumResponseBytesPerPage
const MAXIMUM_REDIRECTS =
  COUNTRY_OUTAGE_EXTERNAL_RUNTIME_LIMITS.maximumRedirects
const DEFAULT_REQUEST_TIMEOUT_MS = 10_000
const MAXIMUM_URL_CHARACTERS = 2_048
const MAXIMUM_STRUCTURED_FACTS_PER_SOURCE = 20
const USER_AGENT = 'Domeye-Country-Outage-External-Evidence/1.0'

export const COUNTRY_OUTAGE_EXTERNAL_ADDRESS_POLICY = {
  version: 'country_outage_external_address_policy_frozen_v1',
  basis: 'project-frozen-table',
  ipv4BlockedSubnets: [
    ['0.0.0.0', 8],
    ['10.0.0.0', 8],
    ['100.64.0.0', 10],
    ['127.0.0.0', 8],
    ['169.254.0.0', 16],
    ['172.16.0.0', 12],
    ['192.0.0.0', 24],
    ['192.0.2.0', 24],
    ['192.168.0.0', 16],
    ['198.18.0.0', 15],
    ['198.51.100.0', 24],
    ['203.0.113.0', 24],
    ['224.0.0.0', 4],
    ['240.0.0.0', 4],
  ],
  ipv6BlockedSubnets: [
    ['::', 128],
    ['::1', 128],
    ['::ffff:0:0', 96],
    ['64:ff9b::', 96],
    ['100::', 64],
    ['2001::', 32],
    ['2001:db8::', 32],
    ['2002::', 16],
    ['fc00::', 7],
    ['fe80::', 10],
    ['ff00::', 8],
  ],
  ipv6AllowedGlobalUnicastSubnet: ['2000::', 3],
} as const

export const COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY = {
  version: COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY_VERSION,
  basis: 'user-authorized-hostname-rules',
  defaultClassification: 'unknown',
  defaultTier: 'unknown',
  rules: [
    {
      hostname: 'bgp.he.net',
      includeSubdomains: true,
      classification: 'measurement_platform',
      tier: 'direct',
      publisher: 'Hurricane Electric BGP Toolkit',
    },
    {
      hostname: 'radar.cloudflare.com',
      includeSubdomains: true,
      classification: 'measurement_platform',
      tier: 'direct',
      publisher: 'Cloudflare Radar',
    },
  ],
} as const

const blockedIpv4 = new BlockList()
for (
  const [network, prefix]
  of COUNTRY_OUTAGE_EXTERNAL_ADDRESS_POLICY.ipv4BlockedSubnets
) {
  blockedIpv4.addSubnet(network, prefix, 'ipv4')
}

const blockedIpv6 = new BlockList()
for (
  const [network, prefix]
  of COUNTRY_OUTAGE_EXTERNAL_ADDRESS_POLICY.ipv6BlockedSubnets
) {
  blockedIpv6.addSubnet(network, prefix, 'ipv6')
}

const allowedIpv6 = new BlockList()
allowedIpv6.addSubnet(
  COUNTRY_OUTAGE_EXTERNAL_ADDRESS_POLICY.ipv6AllowedGlobalUnicastSubnet[0],
  COUNTRY_OUTAGE_EXTERNAL_ADDRESS_POLICY.ipv6AllowedGlobalUnicastSubnet[1],
  'ipv6',
)

const allowedContentTypes = new Set([
  'application/json',
  'application/xhtml+xml',
  'application/xml',
  'text/html',
  'text/plain',
  'text/xml',
])

interface SourceSeed {
  url: string
}

interface SourceClassification {
  sourceClassification: ExternalSourceClassification
  sourceTier: ExternalSourceTier
  publisher: string | null
}

interface ReadablePage {
  url: string
  title: string | null
  publisher: string | null
  publishedAt: string | null
  retrievedAt: string
  summary: string
  factExtraction: StructuredFactExtraction
}

interface UnboundComparableFact {
  metric: 'bgp_control_plane_visibility_state'
  addressFamily: ExternalComparableAddressFamily
  observedWindowStartUtc: string
  observedWindowEndUtc: string
  sourceValue: ExternalComparableSourceValue
  normalizedValue: ExternalComparableNormalizedValue
}

interface StructuredFactExtraction {
  status:
    | 'available'
    | 'not_provided'
    | 'invalid'
    | 'snapshot_mismatch'
  facts: UnboundComparableFact[]
}

export interface SafeExternalEvidenceServiceOptions {
  resolver?: ExternalDnsResolver
  transport?: ExternalHttpTransport
  /**
   * 仅供依赖注入测试使用。正式构造不传此项，固定采用版本化来源策略中的
   * `bgp.he.net` 与 `radar.cloudflare.com` 点边界主机规则。
   */
  allowedHostnameRoots?: readonly string[]
  now?: () => Date
  requestTimeoutMs?: number
}

function stableId(prefix: string, value: string): string {
  const digest = createHash('sha256').update(value).digest('hex')
  return `${prefix}_${digest.slice(0, 24)}`
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

function isIsoTimestamp(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(value) &&
    Number.isFinite(Date.parse(value))
  )
}

function parsedFrozenBinding(
  value: unknown,
): ExternalEvidenceFrozenBinding | null {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'incidentId',
      'publicationId',
      'revision',
      'dataThrough',
      'factSetId',
      'cohortId',
      'countryCode',
      'collectorId',
      'windowStartUtc',
      'windowEndUtc',
    ]) ||
    typeof value.incidentId !== 'string' ||
    !value.incidentId.trim() ||
    value.incidentId.length > 256 ||
    typeof value.publicationId !== 'string' ||
    !value.publicationId.trim() ||
    value.publicationId.length > 256 ||
    !Number.isSafeInteger(value.revision) ||
    Number(value.revision) < 1 ||
    (
      value.dataThrough !== null &&
      !isIsoTimestamp(value.dataThrough)
    ) ||
    typeof value.factSetId !== 'string' ||
    !value.factSetId.trim() ||
    value.factSetId.length > 256 ||
    typeof value.cohortId !== 'string' ||
    !value.cohortId.trim() ||
    value.cohortId.length > 256 ||
    typeof value.countryCode !== 'string' ||
    !/^[A-Z]{2}$/.test(value.countryCode) ||
    value.collectorId !== 'rrc25' ||
    !isIsoTimestamp(value.windowStartUtc) ||
    !isIsoTimestamp(value.windowEndUtc) ||
    Date.parse(value.windowStartUtc) > Date.parse(value.windowEndUtc)
  ) {
    return null
  }
  return {
    incidentId: value.incidentId,
    publicationId: value.publicationId,
    revision: value.revision as number,
    dataThrough: value.dataThrough as string | null,
    factSetId: value.factSetId,
    cohortId: value.cohortId,
    countryCode: value.countryCode,
    collectorId: 'rrc25',
    windowStartUtc: value.windowStartUtc,
    windowEndUtc: value.windowEndUtc,
  }
}

export function sameExternalEvidenceFrozenBinding(
  left: ExternalEvidenceFrozenBinding,
  right: ExternalEvidenceFrozenBinding,
): boolean {
  return (
    left.incidentId === right.incidentId &&
    left.publicationId === right.publicationId &&
    left.revision === right.revision &&
    left.dataThrough === right.dataThrough &&
    left.factSetId === right.factSetId &&
    left.cohortId === right.cohortId &&
    left.countryCode === right.countryCode &&
    left.collectorId === right.collectorId &&
    left.windowStartUtc === right.windowStartUtc &&
    left.windowEndUtc === right.windowEndUtc
  )
}

export function externalEvidenceFrozenBindingId(
  binding: ExternalEvidenceFrozenBinding,
): string {
  return stableId(
    'external_binding',
    JSON.stringify([
      binding.incidentId,
      binding.publicationId,
      binding.revision,
      binding.dataThrough,
      binding.factSetId,
      binding.cohortId,
      binding.countryCode,
      binding.collectorId,
      binding.windowStartUtc,
      binding.windowEndUtc,
    ]),
  )
}

function normalizedComparableValue(
  value: unknown,
): {
  sourceValue: ExternalComparableSourceValue
  normalizedValue: ExternalComparableNormalizedValue
} | null {
  if (typeof value !== 'string') return null
  const normalized = {
    degraded: 'degraded',
    visibility_reduced: 'degraded',
    stable: 'stable',
    no_material_change: 'stable',
    recovering: 'recovering',
    visibility_improving: 'recovering',
    recovered: 'recovered',
    baseline_restored: 'recovered',
  } as const
  if (!(value in normalized)) return null
  const sourceValue = value as ExternalComparableSourceValue
  return {
    sourceValue,
    normalizedValue: normalized[sourceValue],
  }
}

function structuredFactComparisonKey(
  fact: Pick<
    ExternalComparableFact,
    | 'metric'
    | 'addressFamily'
    | 'observedWindowStartUtc'
    | 'observedWindowEndUtc'
  >,
): string {
  return [
    fact.metric,
    fact.addressFamily,
    fact.observedWindowStartUtc,
    fact.observedWindowEndUtc,
  ].join('\u0000')
}

function parseStructuredFacts(
  raw: string,
  contentType: string,
  expectedBinding: ExternalEvidenceFrozenBinding | undefined,
): StructuredFactExtraction {
  if (contentType !== 'application/json' || !expectedBinding) {
    return { status: 'not_provided', facts: [] }
  }
  let value: unknown
  try {
    value = JSON.parse(raw) as unknown
  } catch {
    return { status: 'not_provided', facts: [] }
  }
  if (
    !isRecord(value) ||
    value.schemaVersion !==
      COUNTRY_OUTAGE_EXTERNAL_STRUCTURED_FACT_SCHEMA_VERSION
  ) {
    return { status: 'not_provided', facts: [] }
  }
  if (
    !exactKeys(value, ['schemaVersion', 'binding', 'facts']) ||
    !Array.isArray(value.facts) ||
    value.facts.length === 0 ||
    value.facts.length > MAXIMUM_STRUCTURED_FACTS_PER_SOURCE
  ) {
    return { status: 'invalid', facts: [] }
  }
  const binding = parsedFrozenBinding(value.binding)
  if (!binding) return { status: 'invalid', facts: [] }
  if (!sameExternalEvidenceFrozenBinding(binding, expectedBinding)) {
    return { status: 'snapshot_mismatch', facts: [] }
  }

  const facts = new Map<string, UnboundComparableFact>()
  for (const item of value.facts) {
    if (
      !isRecord(item) ||
      !exactKeys(item, [
        'metric',
        'addressFamily',
        'observedWindowStartUtc',
        'observedWindowEndUtc',
        'value',
      ]) ||
      item.metric !== 'bgp_control_plane_visibility_state' ||
      !['all', 'ipv4', 'ipv6'].includes(
        String(item.addressFamily),
      ) ||
      !isIsoTimestamp(item.observedWindowStartUtc) ||
      !isIsoTimestamp(item.observedWindowEndUtc)
    ) {
      return { status: 'invalid', facts: [] }
    }
    const start = Date.parse(item.observedWindowStartUtc)
    const end = Date.parse(item.observedWindowEndUtc)
    if (
      start > end ||
      start < Date.parse(binding.windowStartUtc) ||
      end > Date.parse(binding.windowEndUtc)
    ) {
      return { status: 'invalid', facts: [] }
    }
    const comparableValue = normalizedComparableValue(item.value)
    if (!comparableValue) {
      return { status: 'invalid', facts: [] }
    }
    const fact: UnboundComparableFact = {
      metric: 'bgp_control_plane_visibility_state',
      addressFamily:
        item.addressFamily as ExternalComparableAddressFamily,
      observedWindowStartUtc: item.observedWindowStartUtc,
      observedWindowEndUtc: item.observedWindowEndUtc,
      ...comparableValue,
    }
    const key = structuredFactComparisonKey(fact)
    const previous = facts.get(key)
    if (
      previous &&
      previous.normalizedValue !== fact.normalizedValue
    ) {
      return { status: 'invalid', facts: [] }
    }
    if (!previous) facts.set(key, fact)
  }
  return { status: 'available', facts: [...facts.values()] }
}

function hostWithoutBrackets(hostname: string): string {
  return hostname.startsWith('[') && hostname.endsWith(']')
    ? hostname.slice(1, -1)
    : hostname
}

function normalizedClassificationHostname(rawUrl: string): string | null {
  try {
    const value = new URL(rawUrl)
    if (
      !['http:', 'https:'].includes(value.protocol) ||
      value.username ||
      value.password
    ) {
      return null
    }
    return hostWithoutBrackets(value.hostname)
      .toLowerCase()
      .replace(/\.$/, '')
  } catch {
    return null
  }
}

function classifySource(rawUrl: string): SourceClassification {
  const hostname = normalizedClassificationHostname(rawUrl)
  if (hostname) {
    for (
      const rule
      of COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY.rules
    ) {
      if (
        hostname === rule.hostname ||
        (
          rule.includeSubdomains &&
          hostname.endsWith(`.${rule.hostname}`)
        )
      ) {
        return {
          sourceClassification: rule.classification,
          sourceTier: rule.tier,
          publisher: rule.publisher,
        }
      }
    }
  }
  return {
    sourceClassification:
      COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY
        .defaultClassification,
    sourceTier:
      COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY.defaultTier,
    publisher: null,
  }
}

function sourceId(url: string): string {
  return stableId(
    'source',
    `${COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY_VERSION}\u0000${url}`,
  )
}

function isBlockedHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/\.$/, '')
  return (
    normalized === 'localhost' ||
    normalized.endsWith('.localhost') ||
    normalized.endsWith('.local') ||
    normalized.endsWith('.internal') ||
    normalized === 'metadata.google.internal' ||
    normalized === 'instance-data' ||
    normalized.endsWith('.instance-data')
  )
}

function normalizedHostnameRoot(value: string): string {
  const normalized = value.trim().toLowerCase().replace(/\.$/, '')
  if (
    !normalized ||
    normalized.length > 253 ||
    isIP(normalized) !== 0 ||
    !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(
      normalized,
    )
  ) {
    throw new Error('外部来源允许主机根无效')
  }
  return normalized
}

function defaultAllowedHostnameRoots(): readonly string[] {
  return COUNTRY_OUTAGE_EXTERNAL_RUNTIME_LIMITS
    .allowedHostBoundaries.map(
      (hostname) => normalizedHostnameRoot(hostname),
    )
}

export function assertCountryOutageExternalSourcePolicyMatchesRuntimeLimits(
  rules: readonly Readonly<{
    hostname: string
    includeSubdomains: boolean
  }>[] = COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY.rules,
  allowedHostBoundaries: readonly string[] =
    COUNTRY_OUTAGE_EXTERNAL_RUNTIME_LIMITS.allowedHostBoundaries,
): void {
  const ruleBoundaries = rules.map((rule) => {
    if (rule.includeSubdomains !== true) {
      throw new ExternalEvidenceSafetyError(
        'external_policy_runtime_drift',
        '外部来源分类规则与固定运行时主机边界不一致',
      )
    }
    normalizedHostnameRoot(rule.hostname)
    return rule.hostname
  })
  for (const boundary of allowedHostBoundaries) {
    normalizedHostnameRoot(boundary)
  }
  if (!isDeepStrictEqual(ruleBoundaries, allowedHostBoundaries)) {
    throw new ExternalEvidenceSafetyError(
      'external_policy_runtime_drift',
      '外部来源分类规则与固定运行时主机边界不一致',
    )
  }
}

function assertAllowedExternalHostname(
  hostname: string,
  allowedHostnameRoots: readonly string[],
): void {
  const normalized = hostname.toLowerCase().replace(/\.$/, '')
  const allowed = allowedHostnameRoots.some(
    (root) => normalized === root || normalized.endsWith(`.${root}`),
  )
  if (!allowed) {
    throw new ExternalEvidenceSafetyError(
      'external_hostname_not_authorized',
      '外部来源主机不在国家中断 Agent 固定公开 URL 白名单中',
    )
  }
}

function assertPublicAddress(address: ExternalDnsAddress): void {
  const family = isIP(address.address)
  if (family !== address.family) {
    throw new ExternalEvidenceSafetyError(
      'external_dns_invalid',
      '外部来源域名返回了无效地址',
    )
  }
  if (
    (family === 4 && blockedIpv4.check(address.address, 'ipv4')) ||
    (family === 6 && blockedIpv6.check(address.address, 'ipv6'))
  ) {
    throw new ExternalEvidenceSafetyError(
      'external_address_blocked',
      '外部来源解析到内网、本机、元数据或保留地址',
    )
  }
  if (
    family === 6 &&
    !allowedIpv6.check(address.address, 'ipv6')
  ) {
    throw new ExternalEvidenceSafetyError(
      'external_address_blocked',
      '外部来源 IPv6 地址不属于允许的全球单播范围',
    )
  }
}

function normalizedUrl(
  raw: string,
  allowedHostnameRoots: readonly string[],
): URL {
  if (!raw || raw.length > MAXIMUM_URL_CHARACTERS) {
    throw new ExternalEvidenceSafetyError(
      'external_url_invalid',
      '外部来源 URL 为空或过长',
    )
  }
  let value: URL
  try {
    value = new URL(raw)
  } catch {
    throw new ExternalEvidenceSafetyError(
      'external_url_invalid',
      '外部来源 URL 无效',
    )
  }
  if (!['http:', 'https:'].includes(value.protocol)) {
    throw new ExternalEvidenceSafetyError(
      'external_scheme_blocked',
      '外部证据只允许公开 HTTP/HTTPS URL',
    )
  }
  if (value.username || value.password) {
    throw new ExternalEvidenceSafetyError(
      'external_credentials_blocked',
      '外部来源 URL 不允许包含认证信息',
    )
  }
  const expectedPort = value.protocol === 'https:' ? '443' : '80'
  if (value.port && value.port !== expectedPort) {
    throw new ExternalEvidenceSafetyError(
      'external_port_blocked',
      '外部来源只允许标准 HTTP/HTTPS 端口',
    )
  }
  const hostname = hostWithoutBrackets(value.hostname)
  if (!hostname || isBlockedHostname(hostname)) {
    throw new ExternalEvidenceSafetyError(
      'external_hostname_blocked',
      '外部来源主机名指向本机、内网或元数据服务',
    )
  }
  assertAllowedExternalHostname(hostname, allowedHostnameRoots)
  value.hash = ''
  return value
}

function safeDisplayUrl(raw: string): string {
  try {
    const value = new URL(raw)
    value.username = ''
    value.password = ''
    value.hash = ''
    return value.toString().slice(0, MAXIMUM_URL_CHARACTERS)
  } catch {
    return ''
  }
}

function decodeEntities(value: string): string {
  return value
    .replaceAll('&nbsp;', ' ')
    .replaceAll('&amp;', '&')
    .replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>')
    .replaceAll('&quot;', '"')
    .replaceAll('&#39;', "'")
}

function compactText(value: string): string {
  return decodeEntities(value)
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript\b[^>]*>[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function metaContent(html: string, names: readonly string[]): string | null {
  for (const name of names) {
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const patterns = [
      new RegExp(
        `<meta\\b[^>]*(?:name|property)=["']${escaped}["'][^>]*content=["']([^"']*)["'][^>]*>`,
        'i',
      ),
      new RegExp(
        `<meta\\b[^>]*content=["']([^"']*)["'][^>]*(?:name|property)=["']${escaped}["'][^>]*>`,
        'i',
      ),
    ]
    for (const pattern of patterns) {
      const match = html.match(pattern)?.[1]
      if (match?.trim()) return compactText(match).slice(0, 300)
    }
  }
  return null
}

function parsedPublishedAt(html: string): string | null {
  const raw = metaContent(html, [
    'article:published_time',
    'datePublished',
    'date',
  ])
  if (!raw) return null
  const timestamp = Date.parse(raw)
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : null
}

function parsedPage(
  url: string,
  response: ExternalHttpResponse,
  retrievedAt: string,
  expectedBinding: ExternalEvidenceFrozenBinding | undefined,
): ReadablePage {
  const contentType = (
    response.headers['content-type'] ?? ''
  ).split(';', 1)[0]!.trim().toLowerCase()
  if (!allowedContentTypes.has(contentType)) {
    throw new ExternalEvidenceSafetyError(
      'external_content_type_blocked',
      '外部来源响应类型不属于允许的文本、HTML、XML 或 JSON',
    )
  }
  const contentEncoding = (
    response.headers['content-encoding'] ?? 'identity'
  ).trim().toLowerCase()
  if (!['', 'identity'].includes(contentEncoding)) {
    throw new ExternalEvidenceSafetyError(
      'external_content_encoding_blocked',
      '外部来源压缩编码未获允许',
    )
  }
  const raw = response.body.toString('utf8')
  const isHtml = contentType.includes('html')
  if (
    isHtml &&
    (
      /<input\b[^>]*\btype\s*=\s*["']?password\b/i.test(raw) ||
      /<form\b[^>]*(?:\baction\s*=\s*["'][^"']*(?:login|sign-in|signin)|\b(?:id|class)\s*=\s*["'][^"']*(?:login|sign-in|signin))/i.test(raw)
    )
  ) {
    throw new ExternalEvidenceSafetyError(
      'external_authenticated_page_blocked',
      '外部来源是登录或认证页面，未读取其内容',
    )
  }
  const title = isHtml
    ? (
        metaContent(raw, ['og:title', 'twitter:title']) ??
        (
          compactText(
            raw.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i)?.[1] ?? '',
          ).slice(0, 300) || null
        )
      )
    : null
  const publisher = isHtml
    ? metaContent(raw, ['og:site_name', 'publisher', 'author'])
    : null
  const body = compactText(raw)
  if (!body) {
    throw new ExternalEvidenceSafetyError(
      'external_content_unreadable',
      '外部来源没有可读取的正文',
    )
  }
  return {
    url,
    title,
    publisher,
    publishedAt: isHtml ? parsedPublishedAt(raw) : null,
    retrievedAt,
    summary: body.slice(0, 480),
    factExtraction: parseStructuredFacts(
      raw,
      contentType,
      expectedBinding,
    ),
  }
}

function sourceFromFailure(
  seed: SourceSeed,
  error: unknown,
): ExternalEvidenceSource {
  const safety = error instanceof ExternalEvidenceSafetyError
  const aborted = error instanceof Error && error.name === 'AbortError'
  const classification = classifySource(seed.url)
  return {
    sourceId: sourceId(seed.url),
    title: null,
    publisher: classification.publisher,
    url: safeDisplayUrl(seed.url),
    publishedAt: null,
    retrievedAt: null,
    sourceClassification: classification.sourceClassification,
    sourceTier: classification.sourceTier,
    readStatus: safety ? 'blocked' : 'failed',
    readStatusDetail: safety
      ? error.message
      : aborted
        ? '外部来源读取超时或已取消'
        : '外部来源读取失败',
    summary: null,
    evidenceStatus: 'read_failed',
    evidenceStatusDetail: safety
      ? '来源被安全读取边界拒绝，未形成可比较事实'
      : '来源读取失败，未形成可比较事实',
    structuredFacts: [],
  }
}

function isRedirect(status: number): boolean {
  return [301, 302, 303, 307, 308].includes(status)
}

function evidenceStatusDetail(
  page: ReadablePage,
  classification: SourceClassification,
): string {
  if (
    classification.sourceClassification !== 'measurement_platform' ||
    classification.sourceTier !== 'direct'
  ) {
    return '来源不是固定策略认可的直接测量平台，仅保留为低等级线索'
  }
  return {
    available: '已提取与冻结事件快照严格绑定的结构化可比较事实',
    not_provided: '来源未提供受支持的结构化事实合同，普通文本不能用于冲突判断',
    invalid: '来源结构化事实合同无效，未用于冲突判断',
    snapshot_mismatch: '来源结构化事实绑定的事件、事实集合、cohort 或快照与当前冻结报告不一致',
  }[page.factExtraction.status]
}

function boundComparableFacts(
  page: ReadablePage,
  sourceIdentifier: string,
  binding: ExternalEvidenceFrozenBinding,
): ExternalComparableFact[] {
  const bindingIdentifier = externalEvidenceFrozenBindingId(binding)
  return page.factExtraction.facts.map((fact) => ({
    factId: stableId(
      'external_fact',
      [
        sourceIdentifier,
        bindingIdentifier,
        structuredFactComparisonKey(fact),
        fact.sourceValue,
      ].join('\u0000'),
    ),
    bindingId: bindingIdentifier,
    ...fact,
  }))
}

function normalizedValueLabel(
  value: ExternalComparableNormalizedValue,
): string {
  return {
    degraded: '可见性下降',
    stable: '未见明显变化',
    recovering: '可见性回升中',
    recovered: '已恢复至基线',
  }[value]
}

function addressFamilyLabel(
  value: ExternalComparableAddressFamily,
): string {
  return value === 'all' ? '全部地址族' : value.toUpperCase()
}

function comparableClaims(
  sources: readonly ExternalEvidenceSource[],
  binding: ExternalEvidenceFrozenBinding | undefined,
): ExternalEvidenceClaim[] {
  const groups = new Map<
    string,
    {
      fact: ExternalComparableFact
      entries: Array<{
        sourceId: string
        fact: ExternalComparableFact
      }>
    }
  >()
  for (const source of sources) {
    if (source.evidenceStatus !== 'available') continue
    for (const fact of source.structuredFacts ?? []) {
      if (
        !binding ||
        fact.bindingId !== externalEvidenceFrozenBindingId(binding)
      ) {
        continue
      }
      const key = structuredFactComparisonKey(fact)
      const group = groups.get(key)
      if (group) {
        group.entries.push({ sourceId: source.sourceId, fact })
      } else {
        groups.set(key, {
          fact,
          entries: [{ sourceId: source.sourceId, fact }],
        })
      }
    }
  }

  const claims: ExternalEvidenceClaim[] = []
  for (const [key, group] of [...groups.entries()].sort(
    ([left], [right]) => compareUnicodeCodePoints(left, right),
  )) {
    const sourceIds = [
      ...new Set(group.entries.map((entry) => entry.sourceId)),
    ].sort(compareUnicodeCodePoints)
    const values = [
      ...new Set(
        group.entries.map((entry) => entry.fact.normalizedValue),
      ),
    ].sort(
      compareUnicodeCodePoints,
    ) as ExternalComparableNormalizedValue[]
    const status: ExternalEvidenceClaim['status'] =
      sourceIds.length < 2
        ? 'insufficient'
        : values.length > 1
          ? 'conflict'
          : 'supported'
    const scope = `${binding?.countryCode ?? '未知国家'}、${addressFamilyLabel(group.fact.addressFamily)}、${group.fact.observedWindowStartUtc} 至 ${group.fact.observedWindowEndUtc}`
    const text =
      status === 'conflict'
        ? `可比直接来源对 ${scope} 的 BGP 控制面可见性状态存在结构化冲突：${values.map(normalizedValueLabel).join('、')}。`
        : status === 'supported'
          ? `可比直接来源对 ${scope} 的 BGP 控制面可见性状态相符：${normalizedValueLabel(values[0]!)}。`
          : `仅有一个直接来源提供了 ${scope} 的结构化 BGP 控制面可见性状态：${normalizedValueLabel(values[0]!)}；不足以进行跨来源核对。`
    claims.push({
      claimId: stableId(
        'claim',
        [
          externalEvidenceFrozenBindingId(binding!),
          key,
          status,
          values.join(','),
          sourceIds.join(','),
        ].join('\u0000'),
      ),
      text,
      status,
      sourceIds,
      limitations: [
        '状态只由严格绑定同一冻结事件快照、metric、地址族和观测时间窗的结构化事实比较得出；标题、摘要和自然语言措辞不参与冲突判定。',
        status === 'conflict'
          ? '冲突只表示外部测量来源的结构化状态不一致，不据此认定原因、责任、用户影响或全国性中断。'
          : '外部来源相符也不构成原因、责任、用户影响或全国性中断认定。',
      ],
    })
  }

  for (const source of sources) {
    if (
      source.readStatus !== 'readable' ||
      source.evidenceStatus !== 'insufficient' ||
      !source.summary
    ) {
      continue
    }
    claims.push({
      claimId: stableId(
        'claim',
        `${source.sourceId}\u0000${source.summary}`,
      ),
      text: source.summary,
      status: 'insufficient',
      sourceIds: [source.sourceId],
      limitations: [
        source.evidenceStatusDetail ??
          '来源没有形成可比较的结构化事实。',
        '公开页面摘要只作独立线索，不修改 Domeye 报告，也不构成因果或责任认定。',
      ],
    })
  }
  return claims
}

function comparisonStatus(
  sources: readonly ExternalEvidenceSource[],
  claims: readonly ExternalEvidenceClaim[],
): ExternalEvidenceComparisonStatus {
  if (claims.some((claim) => claim.status === 'conflict')) {
    return 'conflict'
  }
  const hasSupported = claims.some(
    (claim) => claim.status === 'supported',
  )
  const hasAvailable = sources.some(
    (source) => source.evidenceStatus === 'available',
  )
  const hasGap = sources.some(
    (source) => source.evidenceStatus !== 'available',
  )
  const distinctSourceStatuses = new Set(
    sources.map(
      (source) => source.evidenceStatus ?? 'insufficient',
    ),
  )
  if ((hasSupported || hasAvailable) && hasGap) return 'mixed'
  if (distinctSourceStatuses.size > 1) return 'mixed'
  if (hasSupported) return 'supported'
  return 'insufficient'
}

export class SafeCountryOutageExternalEvidenceService
implements CountryOutageExternalEvidenceService {
  readonly #resolver: ExternalDnsResolver
  readonly #transport: ExternalHttpTransport
  readonly #allowedHostnameRoots: readonly string[]
  readonly #now: () => Date
  readonly #requestTimeoutMs: number

  constructor(options: SafeExternalEvidenceServiceOptions = {}) {
    assertCountryOutageExternalSourcePolicyMatchesRuntimeLimits()
    this.#resolver = options.resolver ?? new NodeExternalDnsResolver()
    this.#transport = options.transport ?? new PinnedNodeHttpTransport()
    this.#allowedHostnameRoots = Object.freeze(
      (
        options.allowedHostnameRoots ??
        defaultAllowedHostnameRoots()
      ).map(normalizedHostnameRoot),
    )
    this.#now = options.now ?? (() => new Date())
    this.#requestTimeoutMs = Math.max(
      250,
      options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS,
    )
  }

  async collect(
    request: ExternalEvidenceRequest,
  ): Promise<ExternalEvidenceAppendix> {
    if (
      request.authorization.authorized !== true ||
      !Number.isFinite(Date.parse(request.authorization.authorizedAt))
    ) {
      return this.#failure(
        request.query,
        'external_authorization_required',
        '只有用户显式授权后才能读取公开网络',
        false,
        '开启外部证据并重新发送问题',
      )
    }
    const frozenBinding =
      request.frozenBinding === undefined
        ? undefined
        : parsedFrozenBinding(request.frozenBinding)
    if (
      request.frozenBinding !== undefined &&
      frozenBinding === null
    ) {
      return this.#failure(
        request.query,
        'external_snapshot_binding_invalid',
        '外部证据请求没有绑定合法的冻结国家中断事件快照',
        false,
        '重新从当前正式报告发起外部证据补充',
      )
    }
    const query = request.query.trim()
    const requestedAt = this.#now().toISOString()
    let seeds: SourceSeed[]
    try {
      seeds = await this.#resolveSeeds(request.urls)
    } catch (error) {
      const safety = error instanceof ExternalEvidenceSafetyError
      const retryable = safety ? error.retryable : true
      return {
        ...this.#failure(
          query,
          safety
            ? error.code
            : error instanceof Error && error.name === 'AbortError'
              ? 'external_evidence_cancelled'
              : 'external_source_prepare_failed',
          error instanceof Error
            ? error.message
            : '指定 URL 读取准备失败',
          retryable,
          retryable ? '稍后重试外部证据补充' : undefined,
          frozenBinding ?? undefined,
        ),
        requestedAt,
      }
    }
    if (seeds.length === 0) {
      return {
        ...this.#failure(
          query,
          'external_source_required',
          '未提供指定公开 URL，未访问网络',
          false,
          '提供 1 至 5 个允许的公开 URL 后重新确认',
          frozenBinding ?? undefined,
        ),
        requestedAt,
      }
    }

    const sources = await Promise.all(
      seeds.map(async (seed) =>
        await this.#readSource(
          seed,
          request.signal,
          frozenBinding ?? undefined,
        )
      ),
    )
    const readable = sources.filter(
      (source) => source.readStatus === 'readable' && source.summary,
    )
    const retrievedAt = this.#now().toISOString()
    if (readable.length === 0) {
      return {
        schemaVersion: COUNTRY_OUTAGE_EXTERNAL_APPENDIX_SCHEMA_VERSION,
        classificationPolicyVersion:
          COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY_VERSION,
        status: 'failed',
        comparisonStatus: 'insufficient',
        ...(frozenBinding ? { frozenBinding } : {}),
        query,
        requestedAt,
        retrievedAt,
        claims: [],
        sources,
        error: {
          code: 'external_sources_unreadable',
          message: '外部来源均不可读取或被安全边界拒绝',
          retryable: false,
          nextAction: '核对来源是否为无需登录的公开 HTTP/HTTPS 页面',
        },
      }
    }
    const claims = comparableClaims(
      sources,
      frozenBinding ?? undefined,
    )
    return {
      schemaVersion: COUNTRY_OUTAGE_EXTERNAL_APPENDIX_SCHEMA_VERSION,
      classificationPolicyVersion:
        COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY_VERSION,
      status: readable.length === sources.length ? 'completed' : 'partial',
      comparisonStatus: comparisonStatus(sources, claims),
      ...(frozenBinding ? { frozenBinding } : {}),
      query,
      requestedAt,
      retrievedAt,
      claims,
      sources,
    }
  }

  async #resolveSeeds(
    urls: readonly string[],
  ): Promise<SourceSeed[]> {
    if (urls.length > MAXIMUM_PAGES) {
      throw new ExternalEvidenceSafetyError(
        'external_page_limit_exceeded',
        `外部来源最多允许 ${MAXIMUM_PAGES} 个页面`,
      )
    }
    const unique = new Map<string, SourceSeed>()
    for (const rawUrl of urls) {
      const key = rawUrl.trim()
      if (!key || unique.has(key)) continue
      unique.set(key, { url: key })
      if (unique.size >= MAXIMUM_PAGES) break
    }
    return [...unique.values()]
  }

  async #readSource(
    seed: SourceSeed,
    signal: AbortSignal,
    frozenBinding: ExternalEvidenceFrozenBinding | undefined,
  ): Promise<ExternalEvidenceSource> {
    try {
      const page = await this.#fetchPage(
        seed.url,
        signal,
        frozenBinding,
      )
      const classification = classifySource(page.url)
      const comparable =
        frozenBinding !== undefined &&
        page.factExtraction.status === 'available' &&
        classification.sourceClassification === 'measurement_platform' &&
        classification.sourceTier === 'direct'
      const identifier = sourceId(page.url)
      return {
        sourceId: identifier,
        title: page.title,
        publisher: classification.publisher ?? page.publisher,
        url: page.url,
        publishedAt: page.publishedAt,
        retrievedAt: page.retrievedAt,
        sourceClassification: classification.sourceClassification,
        sourceTier: classification.sourceTier,
        readStatus: 'readable',
        readStatusDetail: null,
        summary: page.summary,
        evidenceStatus: comparable ? 'available' : 'insufficient',
        evidenceStatusDetail: evidenceStatusDetail(
          page,
          classification,
        ),
        structuredFacts:
          comparable && frozenBinding
            ? boundComparableFacts(
                page,
                identifier,
                frozenBinding,
              )
            : [],
      }
    } catch (error) {
      return sourceFromFailure(seed, error)
    }
  }

  async #fetchPage(
    rawUrl: string,
    parentSignal: AbortSignal,
    frozenBinding: ExternalEvidenceFrozenBinding | undefined,
  ): Promise<ReadablePage> {
    let url = normalizedUrl(rawUrl, this.#allowedHostnameRoots)
    const visited = new Set<string>()
    for (let redirectCount = 0; ; redirectCount += 1) {
      if (visited.has(url.toString())) {
        throw new ExternalEvidenceSafetyError(
          'external_redirect_loop',
          '外部来源重定向形成循环',
        )
      }
      visited.add(url.toString())
      const hostname = hostWithoutBrackets(url.hostname)
      const addresses = isIP(hostname)
        ? [{
            address: hostname,
            family: isIP(hostname) as 4 | 6,
          }]
        : await this.#resolver.resolve(hostname)
      if (addresses.length === 0) {
        throw new ExternalEvidenceSafetyError(
          'external_dns_empty',
          '外部来源域名没有可验证的地址',
          true,
        )
      }
      for (const address of addresses) assertPublicAddress(address)

      const signal = AbortSignal.any([
        parentSignal,
        AbortSignal.timeout(this.#requestTimeoutMs),
      ])
      const response = await this.#transport.request({
        url,
        addresses,
        headers: {
          Accept: 'text/html, text/plain, application/json, application/xhtml+xml, application/xml;q=0.8, text/xml;q=0.8',
          'Accept-Encoding': 'identity',
          'Cache-Control': 'no-cache',
          'User-Agent': USER_AGENT,
        },
        signal,
        maximumBytes: MAXIMUM_RESPONSE_BYTES,
      })
      if (response.body.byteLength > MAXIMUM_RESPONSE_BYTES) {
        throw new ExternalEvidenceSafetyError(
          'external_response_too_large',
          `外部来源响应超过 ${MAXIMUM_RESPONSE_BYTES} 字节限制`,
        )
      }
      if (isRedirect(response.status)) {
        if (redirectCount >= MAXIMUM_REDIRECTS) {
          throw new ExternalEvidenceSafetyError(
            'external_redirect_limit_exceeded',
            `外部来源重定向超过 ${MAXIMUM_REDIRECTS} 跳限制`,
          )
        }
        const location = response.headers.location
        if (!location) {
          throw new ExternalEvidenceSafetyError(
            'external_redirect_invalid',
            '外部来源重定向缺少 Location',
          )
        }
        const redirected = normalizedUrl(
          new URL(location, url).toString(),
          this.#allowedHostnameRoots,
        )
        if (url.protocol === 'https:' && redirected.protocol !== 'https:') {
          throw new ExternalEvidenceSafetyError(
            'external_redirect_downgrade_blocked',
            '外部来源不允许从 HTTPS 降级到 HTTP',
          )
        }
        url = redirected
        continue
      }
      if (response.status < 200 || response.status >= 300) {
        throw new ExternalEvidenceSafetyError(
          'external_http_status_unreadable',
          `外部来源返回不可读取状态 ${response.status}`,
          response.status >= 500,
        )
      }
      return parsedPage(
        url.toString(),
        response,
        this.#now().toISOString(),
        frozenBinding,
      )
    }
  }

  #failure(
    query: string,
    code: string,
    message: string,
    retryable: boolean,
    nextAction?: string,
    frozenBinding?: ExternalEvidenceFrozenBinding,
  ): ExternalEvidenceAppendix {
    return {
      schemaVersion: COUNTRY_OUTAGE_EXTERNAL_APPENDIX_SCHEMA_VERSION,
      classificationPolicyVersion:
        COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY_VERSION,
      status: 'failed',
      comparisonStatus: 'insufficient',
      ...(frozenBinding ? { frozenBinding } : {}),
      query: query.trim(),
      requestedAt: this.#now().toISOString(),
      retrievedAt: null,
      claims: [],
      sources: [],
      error: {
        code,
        message,
        retryable,
        ...(nextAction ? { nextAction } : {}),
      },
    }
  }
}
