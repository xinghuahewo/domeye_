import type {
  CountryOutageAsnPage,
  CountryOutageFactSet,
  SnapshotIdentity,
} from '../domain/contracts.js'
import type {
  CountryOutageReportDocument,
  ReportSection,
} from '../report/contracts.js'

export const DOMEYE_ONLY_EVIDENCE_MODE = 'domeye-only' as const
export const DOMEYE_ONLY_EVIDENCE_MODE_LABEL = '仅使用 Domeye 数据' as const
export const MAXIMUM_ANSWER_CHARACTERS = 4000

export type ReportQuestionAnchor =
  | { kind: 'summary' }
  | { kind: 'highlight'; highlightIndex: number }
  | {
      kind: 'section_paragraph'
      sectionId: ReportSection['id']
      paragraphIndex: number
    }

export interface QuestionReportBinding {
  reportArtifactId: string
  reportContentSha256: string
  factSetId: string
  snapshot: SnapshotIdentity
  evidenceMode: typeof DOMEYE_ONLY_EVIDENCE_MODE
  anchor?: ReportQuestionAnchor
}

export interface CountryOutageQuestionRequest {
  schemaVersion: 'country_outage_question_request_v1'
  requestId: string
  idempotencyKey: string
  binding: QuestionReportBinding
  question: string
}

export interface CountryOutageQuestionContext {
  report: CountryOutageReportDocument
  facts: CountryOutageFactSet
  asnPages: CountryOutageAsnPage[]
}

export type QuestionAnswerKind =
  | 'fact'
  | 'metric_semantics'
  | 'evidence_boundary'
  | 'insufficient_evidence'

export type QuestionEvidenceSource =
  | 'report'
  | 'overview'
  | 'series'
  | 'audit'
  | 'derived_fact'
  | 'asn_detail'

export interface QuestionEvidenceRecord {
  ref: string
  source: QuestionEvidenceSource
  label: string
  metric: string | null
  value: string | null
  observedAtUtc: string | null
  observedAtLocal: string | null
  statisticalScope: string
}

export interface QuestionAnswerDraft {
  kind: QuestionAnswerKind
  text: string
  evidenceRefs: string[]
  missingEvidence: string[]
}

export interface QuestionNarrationRequest {
  question: string
  anchor: ResolvedReportAnchor | null
  groundedDraft: QuestionAnswerDraft
  binding: QuestionReportBinding
  signal?: AbortSignal
}

/**
 * 未来 Pi 问答适配器只能改写已经选定的 groundedDraft，不接收历史消息、外部网页
 * 或其他事件事实。适配器输出仍须由问答服务执行引用、长度和边界校验。
 */
export interface CountryOutageQuestionNarrator {
  readonly identity: QuestionNarratorIdentity
  generate(request: QuestionNarrationRequest): Promise<QuestionAnswerDraft>
}

export interface QuestionNarratorIdentity {
  provider: string
  model: string
  modelVersion: string
  adapter: 'pi-sdk' | 'deterministic-acceptance'
}

export interface ResolvedReportAnchor {
  ref: string
  label: string
  text: string
  evidenceRefs: string[]
}

export interface CountryOutageQuestionAnswer {
  schemaVersion: 'country_outage_question_answer_v1'
  answerId: string
  idempotencyFingerprint: string
  requestId: string
  binding: QuestionReportBinding
  snapshot: SnapshotIdentity
  evidenceMode: typeof DOMEYE_ONLY_EVIDENCE_MODE
  evidenceModeLabel: typeof DOMEYE_ONLY_EVIDENCE_MODE_LABEL
  kind: QuestionAnswerKind
  text: string
  evidenceRefs: string[]
  evidence: QuestionEvidenceRecord[]
  missingEvidence: string[]
}

export interface SuggestedQuestion {
  id: string
  question: string
  capability: string
}

export interface AnswerQuestionOptions {
  signal?: AbortSignal
}
