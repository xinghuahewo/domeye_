import type { IncomingMessage } from 'node:http'

import type { SnapshotIdentity } from '../domain/contracts.js'
import type {
  CountryOutageReportDocument,
  ReportArtifactBuildResult,
} from '../report/contracts.js'

export const COUNTRY_OUTAGE_AGENT_EVENT_SCHEMA_VERSION =
  'country_outage_agent_event_v1' as const
export const COUNTRY_OUTAGE_AGENT_HTTP_SCHEMA_VERSION =
  'country_outage_agent_http_v1' as const

export type ReportPhase =
  | 'queued'
  | 'reading_data'
  | 'generating_report'
  | 'validating'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type QuestionPhase =
  | 'answering'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type SessionNoticePhase = 'session_expiring' | 'session_expired'

export type RunState =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'expired'

export interface CountryOutagePrincipal {
  userId: string
  authorizationScope: string
}

export interface SessionDescriptor {
  expires_at: string
  reminder_at: string
}

export interface AgentPublicError {
  code: string
  message: string
  retryable: boolean
  next_action?: string
}

export interface ArtifactReadyMetadata {
  format: 'markdown' | 'pdf'
  status: 'ready'
  artifact_id: string
  filename: string
  media_type: string
  byte_length: number
  sha256: string
}

export interface ArtifactFailedMetadata {
  format: 'markdown' | 'pdf'
  status: 'failed'
  code: string
  message: string
}

export type ArtifactMetadata =
  | ArtifactReadyMetadata
  | ArtifactFailedMetadata

export interface ReportQuestionContext {
  factSetId: string
  snapshot: SnapshotIdentity
  evidenceRefs: readonly string[]
  /**
   * 只在当前内存会话中传给注入的问答服务，不经 HTTP、SSE 或运行日志输出。
   * 宿主可在这里保留生成报告时冻结的完整事实合同。
   */
  payload?: unknown
}

export interface ReportGenerationInput {
  eventReference: string
  publicationId: string
  revision: number
  signal: AbortSignal
  onPhase(phase: 'reading_data' | 'generating_report' | 'validating'): void
}

export interface ReportGenerationResult {
  document: CountryOutageReportDocument
  artifacts: ReportArtifactBuildResult
  questionContext?: ReportQuestionContext
}

export interface CountryOutageReportService {
  generate(input: ReportGenerationInput): Promise<ReportGenerationResult>
}

export type ReportQuote =
  | {
      kind: 'summary'
      evidenceRefs?: string[]
    }
  | {
      kind: 'highlight'
      highlightIndex: number
      evidenceRefs?: string[]
    }
  | {
      kind: 'section_paragraph'
      sectionId: string
      paragraphIndex: number
      evidenceRefs?: string[]
    }

export interface QuestionAnswer {
  kind:
    | 'fact'
    | 'metric_semantics'
    | 'evidence_boundary'
    | 'insufficient_evidence'
  text: string
  evidenceRefs: string[]
  evidenceRecords: QuestionEvidenceRecord[]
  missingEvidence: string[]
  limitations: string[]
}

export interface QuestionEvidenceRecord {
  evidenceRef: string
  source:
    | 'report'
    | 'overview'
    | 'series'
    | 'audit'
    | 'derived_fact'
    | 'asn_detail'
  label: string
  metric: string | null
  value: string | null
  observedAtUtc: string | null
  observedAtLocal: string | null
  statisticalScope: string
}

export interface QuestionAnswerInput {
  reportId: string
  report: CountryOutageReportDocument
  questionContext: ReportQuestionContext
  question: string
  evidenceMode: 'domeye_only'
  quote?: ReportQuote
  signal: AbortSignal
}

export interface CountryOutageQuestionService {
  answer(input: QuestionAnswerInput): Promise<QuestionAnswer>
}

export type AuthorizeCountryOutageEvent = (
  principal: CountryOutagePrincipal,
  eventReference: string,
) => boolean | Promise<boolean>

export type AuthenticateCountryOutageRequest = (
  request: IncomingMessage,
) =>
  | CountryOutagePrincipal
  | null
  | Promise<CountryOutagePrincipal | null>

export interface CreateReportRequest {
  event_reference: string
  publication_id: string
  revision: number
  idempotency_key: string
}

export interface CreateDomeyeOnlyQuestionRequest {
  question: string
  quote?: {
    kind: 'summary' | 'highlight' | 'section_paragraph'
    highlight_index?: number
    section_id?: string
    paragraph_index?: number
    evidence_refs?: string[]
  }
  idempotency_key: string
  evidence_mode: 'domeye_only'
}

export type CreateQuestionRequest = CreateDomeyeOnlyQuestionRequest

export interface CreateReportResponse {
  schema_version: typeof COUNTRY_OUTAGE_AGENT_HTTP_SCHEMA_VERSION
  report_id: string
  run_id: string
  state: RunState
  phase: ReportPhase
  session: SessionDescriptor
  deduplicated: boolean
}

export interface CreateQuestionResponse {
  schema_version: typeof COUNTRY_OUTAGE_AGENT_HTTP_SCHEMA_VERSION
  report_id: string
  question_id: string
  number: number
  run_id: string
  state: RunState
  phase: QuestionPhase
  session: SessionDescriptor
  deduplicated: boolean
}

export interface AbortRunResponse {
  schema_version: typeof COUNTRY_OUTAGE_AGENT_HTTP_SCHEMA_VERSION
  report_id: string
  run_id: string
  state: RunState
  abort_effective: boolean
}

interface EventBase {
  schema_version: typeof COUNTRY_OUTAGE_AGENT_EVENT_SCHEMA_VERSION
  event_id: number
  report_id: string
  at: string
  state: RunState
  session: SessionDescriptor
}

export interface ReportStateEvent extends EventBase {
  event_type: 'report_state'
  run_id: string
  phase: ReportPhase
  snapshot?: SnapshotIdentity
  report?: CountryOutageReportDocument
  artifacts?: ArtifactMetadata[]
  error?: AgentPublicError
}

export interface QuestionStateEvent extends EventBase {
  event_type: 'question_state'
  run_id: string
  phase: QuestionPhase
  question: {
    question_id: string
    number: number
    question: string
    evidence_mode: 'domeye_only'
    quote?: {
      kind: 'summary' | 'highlight' | 'section_paragraph'
      highlight_index?: number
      section_id?: string
      paragraph_index?: number
      evidence_refs?: string[]
    }
    state: RunState
    answer?: {
      kind:
        | 'fact'
        | 'metric_semantics'
        | 'evidence_boundary'
        | 'insufficient_evidence'
      text: string
      evidence_refs: string[]
      evidence_records: Array<{
        evidence_ref: string
        source:
          | 'report'
          | 'overview'
          | 'series'
          | 'audit'
          | 'derived_fact'
          | 'asn_detail'
        label: string
        metric: string | null
        value: string | null
        observed_at_utc: string | null
        observed_at_local: string | null
        statistical_scope: string
      }>
      missing_evidence: string[]
      limitations: string[]
      snapshot: SnapshotIdentity
    }
    error?: AgentPublicError
  }
}

export interface SessionNoticeEvent extends EventBase {
  event_type: 'session_notice'
  phase: SessionNoticePhase
}

export type CountryOutageAgentEvent =
  | ReportStateEvent
  | QuestionStateEvent
  | SessionNoticeEvent

export interface EventSubscription {
  replay: CountryOutageAgentEvent[]
  activate(): void
  close(): void
}

export interface DownloadArtifact {
  artifactId: string
  filename: string
  mediaType: string
  byteLength: number
  sha256: string
  content: Buffer
  downloadDeadlineAtMs: number
}

export interface CountryOutageServerLimits {
  sessionTtlMs: number
  expiryReminderMs: number
  reportRunTimeoutMs: number
  questionRunTimeoutMs: number
  maximumQuestions: number
  maximumActiveAnswers: 1
  maximumActiveReportRunsPerUser: 1
  maximumActiveReportRunsGlobal: number
  maximumQueueDepth: number
  maximumQuestionsPerMinute: number
  /** null 表示不限制单用户每小时报告生成次数。 */
  maximumReportRunsPerUserPerHour: number | null
  maximumAnswerCharacters: number
  maximumQuestionCharacters: number
  completedDownloadGraceMs: number
  tombstoneTtlMs: number
}
