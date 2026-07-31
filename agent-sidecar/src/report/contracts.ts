import type {
  CountryOutageAsnPage,
  CountryOutageFactSet,
  EventIdentity,
  SnapshotIdentity,
} from '../domain/contracts.js'
import type { COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION } from '../pi/country-outage-skill-bundle.js'

export interface EvidenceParagraph {
  text: string
  evidenceRefs: string[]
}

export interface ReportHighlight {
  label: string
  value: string
  evidenceRefs: string[]
}

export interface ReportSection {
  id:
    | 'scope'
    | 'key_numbers'
    | 'visibility'
    | 'asn_scope'
    | 'address_families'
    | 'updates'
    | 'end_state'
    | 'resources'
    | 'assessment'
  title: string
  paragraphs: EvidenceParagraph[]
}

export interface CountryOutageReportDraft {
  schemaVersion: 'country_outage_report_draft_v1'
  title: string
  subtitle: string
  summary: EvidenceParagraph
  highlights: ReportHighlight[]
  sections: ReportSection[]
  unknowns: string[]
}

export interface ReportEvidenceBundle {
  facts: CountryOutageFactSet
  asnPages: CountryOutageAsnPage[]
}

export interface NarrationRequest {
  reference: string
  evidence: ReportEvidenceBundle
  signal?: AbortSignal
}

export interface ReportNarrator {
  readonly identity: ReportModelIdentity
  readonly validatorRulesVersion: string
  readonly skillBundleSha256: string
  generate(request: NarrationRequest): Promise<CountryOutageReportDraft>
}

export interface ReportModelIdentity {
  provider: string
  model: string
  modelVersion: string
  adapter: 'pi-sdk' | 'deterministic-acceptance'
  piVersion?: string
  runtimeIdentity?: 'formal' | 'candidate'
  modelRevisionKind?: 'mutable_alias'
  immutableRevisionAvailable?: false
  limitation?: string
  certificationValidUntil?: string
  certifiedScenarioSetId?: string
  certifiedInputScope?: string
}

export interface ReportValidationResult {
  passed: boolean
  errors: string[]
  warnings: string[]
  checkedEvidenceRefs: string[]
}

export interface CountryOutageReportDocument {
  schemaVersion: 'country_outage_report_document_v1'
  artifactId: string
  reportContentSha256: string
  reportSpecificationVersion: 'country_outage_report_spec_v1'
  projectKnowledgeVersion:
    typeof COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION
  validatorRulesVersion: string
  skillBundleSha256: string
  generatedAt: string
  aiGenerated: true
  humanReviewed: false
  event: EventIdentity
  snapshot: SnapshotIdentity
  factSetId: string
  model: ReportModelIdentity
  validation: ReportValidationResult
  draft: CountryOutageReportDraft
}

export interface ReportArtifact {
  format: 'markdown' | 'pdf'
  filename: string
  mediaType: string
  byteLength: number
  sha256: string
  content: Buffer
}

export interface ReportArtifactBundle {
  artifactId: string
  markdown: ReportArtifact
  pdf: ReportArtifact
}

export interface ReportArtifactFailure {
  format: 'markdown' | 'pdf'
  code: string
  message: string
}

export type ReportArtifactOutcome =
  | { status: 'ready'; artifact: ReportArtifact }
  | { status: 'failed'; error: ReportArtifactFailure }

export interface ReportArtifactBuildResult {
  artifactId: string
  markdown: ReportArtifactOutcome
  pdf: ReportArtifactOutcome
}
