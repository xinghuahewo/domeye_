import type { SnapshotIdentity } from '../domain/contracts.js'
import type {
  CreateDomeyeOnlyQuestionRequest,
} from '../server/contracts.js'

export const COUNTRY_OUTAGE_EXTERNAL_EVIDENCE_CAPABILITY_SCHEMA_VERSION =
  'country_outage_external_evidence_capability_v1' as const

export const COUNTRY_OUTAGE_EVIDENCE_ENVELOPE_SCHEMA_VERSION =
  'country_outage_evidence_envelope_v1' as const

export type ExternalEvidenceCapabilityState =
  | 'ready'
  | 'not_configured'
  | 'self_check_failed'

interface ExternalEvidenceReadinessBase {
  schema_version:
    typeof COUNTRY_OUTAGE_EXTERNAL_EVIDENCE_CAPABILITY_SCHEMA_VERSION
  capability: 'external_evidence'
  checked_at: string
}

export interface ExternalEvidenceReady
extends ExternalEvidenceReadinessBase {
  state: 'ready'
  provider: 'managed-egress-v1'
  policy: {
    version: string
    sha256: string
    allowed_host_roots: string[]
    minimum_urls: 1
    maximum_urls: number
  }
}

export interface ExternalEvidenceNotConfigured
extends ExternalEvidenceReadinessBase {
  state: 'not_configured'
  provider: 'disabled'
  policy: null
  reason_code: 'external_evidence_not_configured'
}

export interface ExternalEvidenceSelfCheckFailed
extends ExternalEvidenceReadinessBase {
  state: 'self_check_failed'
  provider: 'managed-egress-v1'
  policy: null
  reason_code:
    | 'managed_egress_not_deployed'
    | 'managed_egress_unavailable'
}

export type ExternalEvidenceReadiness =
  | ExternalEvidenceReady
  | ExternalEvidenceNotConfigured
  | ExternalEvidenceSelfCheckFailed

export interface CreateExternalEvidenceQuestionRequest {
  question: string
  evidence_mode: 'domeye_plus_external'
  quote?: {
    kind: 'summary' | 'highlight' | 'section_paragraph'
    highlight_index?: number
    section_id?: string
    paragraph_index?: number
    evidence_refs?: string[]
  }
  external_authorization: {
    authorized: true
    authorized_at: string
  }
  external_urls: string[]
  idempotency_key: string
}

export type CreateOrchestratedQuestionRequest =
  | CreateDomeyeOnlyQuestionRequest
  | CreateExternalEvidenceQuestionRequest

export interface ExternalEvidenceAnchor {
  reportId: string
  reportArtifactId: string
  reportContentSha256: string
  factSetId: string
  snapshot: SnapshotIdentity
  questionId: string
  questionText: string
  questionAnswerSha256: string
}

export interface AuthorizedEvidenceRequest {
  anchor: ExternalEvidenceAnchor
  authorization: {
    authorized: true
    authorizedAt: string
  }
  urls: readonly string[]
  signal: AbortSignal
}

export interface EvidenceEnvelope {
  schema_version:
    typeof COUNTRY_OUTAGE_EVIDENCE_ENVELOPE_SCHEMA_VERSION
  provider: 'managed-egress-v1'
  policy_version: string
  policy_sha256: string
  status: 'fetched' | 'blocked' | 'failed'
  evidence_status: 'available' | 'insufficient' | 'read_failed'
  source_url: string
  final_url: string | null
  title: string | null
  publisher: string | null
  published_at: string | null
  retrieved_at: string | null
  summary: string | null
  content_sha256: string | null
}

export interface ExternalEvidenceCollection {
  provider_id: 'managed-egress-v1'
  policy_version: string
  policy_sha256: string
  requested_at: string
  completed_at: string
  envelopes: readonly EvidenceEnvelope[]
}

export interface ExternalEvidenceProvider {
  readonly providerId: 'disabled' | 'managed-egress-v1'
  readiness(): ExternalEvidenceReadiness
  fetch(
    request: AuthorizedEvidenceRequest,
  ): Promise<ExternalEvidenceCollection>
}

export type ExternalEvidenceRunState =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface ExternalEvidenceRunDescriptor {
  run_id: string
  report_id: string
  question_id: string
  state: ExternalEvidenceRunState
}

/**
 * 独立 external run 的冻结边界。当前版本只定义合同；未部署 managed-egress
 * 时，编排层必须在创建运行前失败关闭。
 */
export interface ExternalEvidenceRunCoordinator {
  start(
    request: AuthorizedEvidenceRequest,
  ): Promise<ExternalEvidenceRunDescriptor>
}
