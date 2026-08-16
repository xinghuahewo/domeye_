import { createHash } from 'node:crypto'
import { isDeepStrictEqual } from 'node:util'

import type { ExternalEvidenceFrozenBinding } from './contracts.js'
import { ExternalEvidenceSafetyError } from './errors.js'

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

export const COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY = {
  version: 'country_outage_external_source_classification_policy_v1',
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

function normalizedHostnameRoot(value: string): string {
  const normalized = value.trim().toLowerCase().replace(/\.$/, '')
  if (
    !normalized ||
    normalized.length > 253 ||
    !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(
      normalized,
    )
  ) {
    throw new Error('外部来源允许主机根无效')
  }
  return normalized
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
    return normalizedHostnameRoot(rule.hostname)
  })
  const normalizedBoundaries = allowedHostBoundaries.map(
    normalizedHostnameRoot,
  )
  if (!isDeepStrictEqual(ruleBoundaries, normalizedBoundaries)) {
    throw new ExternalEvidenceSafetyError(
      'external_policy_runtime_drift',
      '外部来源分类规则与固定运行时主机边界不一致',
    )
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
  const digest = createHash('sha256')
    .update(JSON.stringify([
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
    ]))
    .digest('hex')
  return `external_binding_${digest.slice(0, 24)}`
}
