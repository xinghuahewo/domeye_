import {
  COUNTRY_OUTAGE_EXTERNAL_EVIDENCE_CAPABILITY_SCHEMA_VERSION,
  type AuthorizedEvidenceRequest,
  type ExternalEvidenceCollection,
  type ExternalEvidenceProvider,
  type ExternalEvidenceReadiness,
} from './contracts.js'

export class ExternalEvidenceProviderUnavailableError extends Error {
  constructor(
    readonly code:
      | 'external_evidence_not_configured'
      | 'managed_egress_not_deployed'
      | 'managed_egress_unavailable',
    message: string,
  ) {
    super(message)
    this.name = 'ExternalEvidenceProviderUnavailableError'
  }
}

function checkedAt(now: () => Date): string {
  return now().toISOString()
}

export class DisabledExternalEvidenceProvider
implements ExternalEvidenceProvider {
  readonly providerId = 'disabled' as const

  constructor(private readonly now: () => Date = () => new Date()) {}

  readiness(): ExternalEvidenceReadiness {
    return {
      schema_version:
        COUNTRY_OUTAGE_EXTERNAL_EVIDENCE_CAPABILITY_SCHEMA_VERSION,
      capability: 'external_evidence',
      state: 'not_configured',
      provider: this.providerId,
      checked_at: checkedAt(this.now),
      policy: null,
      reason_code: 'external_evidence_not_configured',
    }
  }

  async fetch(
    _request: AuthorizedEvidenceRequest,
  ): Promise<ExternalEvidenceCollection> {
    throw new ExternalEvidenceProviderUnavailableError(
      'external_evidence_not_configured',
      '当前环境未配置外部证据能力',
    )
  }
}

/**
 * managed-egress 的占位失败关闭实现。它没有 Gateway client、DNS、HTTP、
 * TLS 或来源解析能力；后续 capability pack 必须另行实现并认证。
 */
export class UndeployedManagedEgressExternalEvidenceProvider
implements ExternalEvidenceProvider {
  readonly providerId = 'managed-egress-v1' as const

  constructor(private readonly now: () => Date = () => new Date()) {}

  readiness(): ExternalEvidenceReadiness {
    return {
      schema_version:
        COUNTRY_OUTAGE_EXTERNAL_EVIDENCE_CAPABILITY_SCHEMA_VERSION,
      capability: 'external_evidence',
      state: 'self_check_failed',
      provider: this.providerId,
      checked_at: checkedAt(this.now),
      policy: null,
      reason_code: 'managed_egress_not_deployed',
    }
  }

  async fetch(
    _request: AuthorizedEvidenceRequest,
  ): Promise<ExternalEvidenceCollection> {
    throw new ExternalEvidenceProviderUnavailableError(
      'managed_egress_not_deployed',
      'managed-egress 外部证据包尚未部署',
    )
  }
}
