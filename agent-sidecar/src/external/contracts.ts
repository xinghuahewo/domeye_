export const COUNTRY_OUTAGE_EXTERNAL_APPENDIX_SCHEMA_VERSION =
  'country_outage_external_appendix_v1' as const

export const COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY_VERSION =
  'country_outage_external_source_classification_policy_v1' as const

export const COUNTRY_OUTAGE_EXTERNAL_STRUCTURED_FACT_SCHEMA_VERSION =
  'country_outage_external_structured_facts_v1' as const

export type ExternalSourceTier =
  | 'direct'
  | 'secondary'
  | 'lead'
  | 'unknown'

export type ExternalSourceClassification =
  | 'measurement_platform'
  | 'unknown'

export type ExternalSourceReadStatus =
  | 'readable'
  | 'unreadable'
  | 'blocked'
  | 'failed'

export type ExternalEvidenceSourceStatus =
  | 'available'
  | 'insufficient'
  | 'read_failed'

export type ExternalEvidenceComparisonStatus =
  | 'supported'
  | 'mixed'
  | 'conflict'
  | 'insufficient'

export interface ExternalEvidenceFrozenBinding {
  incidentId: string
  publicationId: string
  revision: number
  dataThrough: string | null
  factSetId: string
  cohortId: string
  countryCode: string
  collectorId: 'rrc25'
  windowStartUtc: string
  windowEndUtc: string
}

export type ExternalComparableMetric =
  'bgp_control_plane_visibility_state'

export type ExternalComparableAddressFamily =
  | 'all'
  | 'ipv4'
  | 'ipv6'

export type ExternalComparableSourceValue =
  | 'degraded'
  | 'visibility_reduced'
  | 'stable'
  | 'no_material_change'
  | 'recovering'
  | 'visibility_improving'
  | 'recovered'
  | 'baseline_restored'

export type ExternalComparableNormalizedValue =
  | 'degraded'
  | 'stable'
  | 'recovering'
  | 'recovered'

export interface ExternalComparableFact {
  factId: string
  bindingId: string
  metric: ExternalComparableMetric
  addressFamily: ExternalComparableAddressFamily
  observedWindowStartUtc: string
  observedWindowEndUtc: string
  sourceValue: ExternalComparableSourceValue
  normalizedValue: ExternalComparableNormalizedValue
}

export interface ExternalEvidenceAuthorization {
  authorized: true
  authorizedAt: string
}

export interface ExternalEvidenceClaim {
  claimId: string
  text: string
  status: 'supported' | 'mixed' | 'conflict' | 'insufficient'
  sourceIds: string[]
  limitations: string[]
}

export interface ExternalEvidenceSource {
  sourceId: string
  title: string | null
  publisher: string | null
  url: string
  publishedAt: string | null
  retrievedAt: string | null
  sourceClassification: ExternalSourceClassification
  sourceTier: ExternalSourceTier
  readStatus: ExternalSourceReadStatus
  readStatusDetail: string | null
  summary: string | null
  evidenceStatus?: ExternalEvidenceSourceStatus
  evidenceStatusDetail?: string | null
  structuredFacts?: ExternalComparableFact[]
}

export interface ExternalEvidenceAppendix {
  schemaVersion: typeof COUNTRY_OUTAGE_EXTERNAL_APPENDIX_SCHEMA_VERSION
  classificationPolicyVersion:
    typeof COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY_VERSION
  status: 'completed' | 'partial' | 'failed'
  comparisonStatus?: ExternalEvidenceComparisonStatus
  frozenBinding?: ExternalEvidenceFrozenBinding
  query: string
  requestedAt: string
  retrievedAt: string | null
  claims: ExternalEvidenceClaim[]
  sources: ExternalEvidenceSource[]
  error?: {
    code: string
    message: string
    retryable: boolean
    nextAction?: string
  }
}

export interface ExternalEvidenceRequest {
  query: string
  authorization: ExternalEvidenceAuthorization
  urls: readonly string[]
  frozenBinding?: ExternalEvidenceFrozenBinding
  signal: AbortSignal
}

export interface CountryOutageExternalEvidenceService {
  collect(request: ExternalEvidenceRequest): Promise<ExternalEvidenceAppendix>
}

export interface ExternalDnsAddress {
  address: string
  family: 4 | 6
}

export interface ExternalDnsResolver {
  resolve(hostname: string): Promise<readonly ExternalDnsAddress[]>
}

export interface ExternalHttpRequest {
  url: URL
  addresses: readonly ExternalDnsAddress[]
  headers: Readonly<Record<string, string>>
  signal: AbortSignal
  maximumBytes: number
}

export interface ExternalHttpResponse {
  status: number
  headers: Readonly<Record<string, string>>
  body: Buffer
}

export interface ExternalHttpTransport {
  request(input: ExternalHttpRequest): Promise<ExternalHttpResponse>
}
