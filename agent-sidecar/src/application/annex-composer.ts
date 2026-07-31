import type {
  EvidenceEnvelope,
  ExternalEvidenceAnchor,
} from './contracts.js'

export interface ExternalEvidenceAnnex {
  schema_version: 'country_outage_external_annex_v2'
  annex_id: string
  parent_report_artifact_id: string
  parent_report_content_sha256: string
  parent_question_id: string
  parent_question_answer_sha256: string
  fact_set_id: string
  provider_id: 'managed-egress-v1'
  policy_version: string
  policy_sha256: string
  envelopes: readonly EvidenceEnvelope[]
}

export interface AnnexComposer {
  compose(input: {
    anchor: Readonly<ExternalEvidenceAnchor>
    providerId: 'managed-egress-v1'
    policyVersion: string
    policySha256: string
    envelopes: readonly EvidenceEnvelope[]
  }): ExternalEvidenceAnnex
}

export class AnnexComposerUnavailableError extends Error {
  constructor() {
    super('外部证据附件组合器尚未部署')
    this.name = 'AnnexComposerUnavailableError'
  }
}

/**
 * 核心 Sidecar 使用的禁用态组合器。它不接收报告正文，也不会生成或改写
 * 基础 Markdown/PDF。
 */
export class DisabledAnnexComposer implements AnnexComposer {
  compose(): ExternalEvidenceAnnex {
    throw new AnnexComposerUnavailableError()
  }
}
