import { randomUUID } from 'node:crypto'

import type {
  CountryOutageReportDocument,
  ReportArtifactBuildResult,
  ReportArtifactOutcome,
} from '../report/contracts.js'
import {
  adaptCountryOutageReportService,
  type CountryOutageReportGenerationPort,
} from '../core/report-generation-port.js'
import {
  COUNTRY_OUTAGE_AGENT_EVENT_SCHEMA_VERSION,
  COUNTRY_OUTAGE_AGENT_HTTP_SCHEMA_VERSION,
  type AbortRunResponse,
  type AgentPublicError,
  type ArtifactMetadata,
  type AuthorizeCountryOutageEvent,
  type CountryOutageAgentEvent,
  type CountryOutagePrincipal,
  type CountryOutageQuestionService,
  type CountryOutageReportService,
  type CountryOutageServerLimits,
  type CreateDomeyeOnlyQuestionRequest,
  type CreateQuestionResponse,
  type CreateReportRequest,
  type CreateReportResponse,
  type DownloadArtifact,
  type EventSubscription,
  type QuestionAnswer,
  type QuestionPhase,
  type QuestionStateEvent,
  type ReportGenerationResult,
  type ReportPhase,
  type ReportQuestionContext,
  type ReportQuote,
  type ReportStateEvent,
  type RunState,
  type SessionDescriptor,
  type SessionNoticeEvent,
} from './contracts.js'
import {
  CountryOutageHttpError,
  publicErrorFromUnknown,
} from './errors.js'
import {
  CountryOutageBaseReportCache,
  freezeCountryOutageReportServiceIdentity,
  type CountryOutageBaseReportCacheKey,
  type CountryOutageBaseReportCachingOptions,
  type CountryOutageReportServiceIdentity,
} from './base-report-cache.js'
import { compareUnicodeCodePoints } from '../shared/deterministic-json.js'

const COUNTRY_OUTAGE_REFERENCE =
  /^country_outage\/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}\/([A-Z]{2})\/\d+\/r$/
const IDEMPOTENCY_KEY = /^[A-Za-z0-9._:-]{8,128}$/
const EVIDENCE_REFERENCE =
  /^(?:[a-z][a-z0-9_]*:[A-Za-z0-9_~./:@+-]{1,480}|fact_[A-Za-z0-9_-]{1,128})$/

export const DEFAULT_COUNTRY_OUTAGE_SERVER_LIMITS =
  Object.freeze<CountryOutageServerLimits>({
    sessionTtlMs: 30 * 60 * 1000,
    expiryReminderMs: 5 * 60 * 1000,
    reportRunTimeoutMs: 120 * 1000,
    questionRunTimeoutMs: 60 * 1000,
    maximumQuestions: 30,
    maximumActiveAnswers: 1,
    maximumActiveReportRunsPerUser: 1,
    maximumActiveReportRunsGlobal: 8,
    maximumQueueDepth: 32,
    maximumQuestionsPerMinute: 6,
    maximumReportRunsPerUserPerHour: 3,
    maximumAnswerCharacters: 4000,
    maximumQuestionCharacters: 4000,
    completedDownloadGraceMs: 120 * 1000,
    tombstoneTtlMs: 5 * 60 * 1000,
  })

type TimerHandle = ReturnType<typeof setTimeout>

interface StoredQuestion {
  id: string
  number: number
  runId: string
  idempotencyKey: string
  fingerprint: string
  question: string
  quote?: ReportQuote
  state: RunState
  phase: QuestionPhase
  controller: AbortController
  answer?: QuestionAnswer
  error?: AgentPublicError
  timeout?: TimerHandle
}

interface StoredReport {
  id: string
  runId: string
  ownerUserId: string
  authorizationScope: string
  eventReference: string
  expectedPublicationId: string
  expectedRevision: number
  idempotencyKey: string
  requestFingerprint: string
  state: RunState
  phase: ReportPhase
  createdAtMs: number
  expiresAtMs: number
  reminderAtMs: number
  reminded: boolean
  expiredAtMs?: number
  controller: AbortController
  timeout?: TimerHandle
  lifecycleTimers: TimerHandle[]
  events: CountryOutageAgentEvent[]
  nextEventId: number
  listeners: Set<(event: CountryOutageAgentEvent) => void>
  document?: CountryOutageReportDocument
  artifacts?: ReportArtifactBuildResult
  questionContext?: ReportQuestionContext
  error?: AgentPublicError
  questions: StoredQuestion[]
  questionIdempotency: Map<string, string>
  questionFingerprints: Map<string, string>
}

interface RunLocator {
  kind: 'report' | 'question'
  reportId: string
  questionId?: string
}

export interface CountryOutageCoreSessionManagerOptions {
  reportGenerator?: CountryOutageReportGenerationPort
  /** @deprecated 旧夹具兼容；正式装配使用 reportGenerator。 */
  reportService?: CountryOutageReportService
  questionService: CountryOutageQuestionService
  baseReportCache?: CountryOutageBaseReportCachingOptions
  authorize: AuthorizeCountryOutageEvent
  limits?: Partial<CountryOutageServerLimits>
  now?: () => number
  timersEnabled?: boolean
}

function iso(value: number): string {
  return new Date(value).toISOString()
}

function sessionDescriptor(report: StoredReport): SessionDescriptor {
  return {
    expires_at: iso(report.expiresAtMs),
    reminder_at: iso(report.reminderAtMs),
  }
}

function normalizedReference(value: string): string {
  return value.replace(' ', '+')
}

function reportFingerprint(request: CreateReportRequest): string {
  return JSON.stringify([
    normalizedReference(request.event_reference),
    request.publication_id,
    request.revision,
  ])
}

function quoteFingerprint(quote: ReportQuote | undefined): string {
  if (!quote) return ''
  if (quote.kind === 'summary') {
    return JSON.stringify([
      quote.kind,
      [...(quote.evidenceRefs ?? [])].sort(compareUnicodeCodePoints),
    ])
  }
  if (quote.kind === 'highlight') {
    return JSON.stringify([
      quote.kind,
      quote.highlightIndex,
      [...(quote.evidenceRefs ?? [])].sort(compareUnicodeCodePoints),
    ])
  }
  return JSON.stringify([
    quote.kind,
    quote.sectionId,
    quote.paragraphIndex,
    [...(quote.evidenceRefs ?? [])].sort(compareUnicodeCodePoints),
  ])
}

function questionFingerprint(
  question: string,
  quote: ReportQuote | undefined,
): string {
  return JSON.stringify([
    question.trim().replace(/\s+/g, ' '),
    quoteFingerprint(quote),
  ])
}

function isTerminal(state: RunState): boolean {
  return (
    state === 'completed' ||
    state === 'failed' ||
    state === 'cancelled' ||
    state === 'expired'
  )
}

function hasStableQuestionResult(question: StoredQuestion): boolean {
  return question.state === 'completed' && question.answer !== undefined
}

function canReuseQuestionFingerprint(question: StoredQuestion): boolean {
  return question.state === 'running' || hasStableQuestionResult(question)
}

function reportPhaseRank(phase: ReportPhase): number {
  return {
    queued: 0,
    reading_data: 1,
    generating_report: 2,
    validating: 3,
    completed: 4,
    failed: 4,
    cancelled: 4,
  }[phase]
}

function collectDocumentEvidenceRefs(
  document: CountryOutageReportDocument,
): string[] {
  const refs = new Set<string>()
  const add = (values: readonly string[]): void => {
    for (const value of values) refs.add(value)
  }
  add(document.draft.summary.evidenceRefs)
  for (const highlight of document.draft.highlights) {
    add(highlight.evidenceRefs)
  }
  for (const section of document.draft.sections) {
    for (const paragraph of section.paragraphs) {
      add(paragraph.evidenceRefs)
    }
  }
  return [...refs].sort(compareUnicodeCodePoints)
}

function artifactMetadata(
  reportId: string,
  artifactId: string,
  outcome: ReportArtifactOutcome,
): ArtifactMetadata {
  if (outcome.status === 'failed') {
    return {
      format: outcome.error.format,
      status: 'failed',
      code: outcome.error.code,
      message: outcome.error.message,
    }
  }
  return {
    format: outcome.artifact.format,
    status: 'ready',
    artifact_id: artifactId,
    filename: outcome.artifact.filename,
    media_type: outcome.artifact.mediaType,
    byte_length: outcome.artifact.byteLength,
    sha256: outcome.artifact.sha256,
  }
}

function validatePrincipal(principal: CountryOutagePrincipal): void {
  if (!principal.userId.trim() || !principal.authorizationScope.trim()) {
    throw new CountryOutageHttpError(
      401,
      'authentication_required',
      '需要有效的登录身份',
    )
  }
}

function validateCreateReportRequest(request: CreateReportRequest): void {
  if (!COUNTRY_OUTAGE_REFERENCE.test(request.event_reference)) {
    throw new CountryOutageHttpError(
      400,
      'invalid_country_outage_reference',
      '只接受已有合法 country_outage 事件引用',
    )
  }
  if (
    !request.publication_id.trim() ||
    request.publication_id.length > 200
  ) {
    throw new CountryOutageHttpError(
      400,
      'invalid_publication',
      'publication_id 无效',
    )
  }
  if (!Number.isSafeInteger(request.revision) || request.revision < 1) {
    throw new CountryOutageHttpError(
      400,
      'invalid_revision',
      'revision 必须为正整数',
    )
  }
  if (!IDEMPOTENCY_KEY.test(request.idempotency_key)) {
    throw new CountryOutageHttpError(
      400,
      'invalid_idempotency_key',
      'idempotency_key 必须为 8 至 128 位安全字符',
    )
  }
}

function normalizedQuote(
  request: CreateDomeyeOnlyQuestionRequest,
): ReportQuote | undefined {
  if (!request.quote) return undefined
  const evidenceRefs = request.quote.evidence_refs
    ?.map((value) => value.trim())
    .filter(Boolean)
  if (request.quote.kind === 'summary') {
    return {
      kind: 'summary',
      ...(evidenceRefs && evidenceRefs.length > 0
        ? { evidenceRefs: [...new Set(evidenceRefs)] }
        : {}),
    }
  }
  if (request.quote.kind === 'highlight') {
    return {
      kind: 'highlight',
      highlightIndex: request.quote.highlight_index ?? Number.NaN,
      ...(evidenceRefs && evidenceRefs.length > 0
        ? { evidenceRefs: [...new Set(evidenceRefs)] }
        : {}),
    }
  }
  return {
    kind: 'section_paragraph',
    sectionId: request.quote.section_id?.trim() ?? '',
    paragraphIndex: request.quote.paragraph_index ?? Number.NaN,
    ...(evidenceRefs && evidenceRefs.length > 0
      ? { evidenceRefs: [...new Set(evidenceRefs)] }
      : {}),
  }
}

function validateCreateQuestionRequest(
  request: CreateDomeyeOnlyQuestionRequest,
  maximumQuestionCharacters: number,
): void {
  if (
    (request as { evidence_mode?: unknown }).evidence_mode !==
    'domeye_only'
  ) {
    throw new CountryOutageHttpError(
      400,
      'core_evidence_mode_not_allowed',
      '国家中断核心追问只接受 domeye_only',
    )
  }
  const question = request.question.trim()
  if (!question) {
    throw new CountryOutageHttpError(
      400,
      'invalid_question',
      '问题不能为空',
    )
  }
  if (question.length > maximumQuestionCharacters) {
    throw new CountryOutageHttpError(
      413,
      'question_too_large',
      `问题超过 ${maximumQuestionCharacters} 字符限制`,
    )
  }
  if (!IDEMPOTENCY_KEY.test(request.idempotency_key)) {
    throw new CountryOutageHttpError(
      400,
      'invalid_idempotency_key',
      'idempotency_key 必须为 8 至 128 位安全字符',
    )
  }
  const evidenceRefs = request.quote?.evidence_refs ?? []
  if (
    evidenceRefs.length > 20 ||
    evidenceRefs.some((reference) => !EVIDENCE_REFERENCE.test(reference))
  ) {
    throw new CountryOutageHttpError(
      400,
      'invalid_report_quote',
      '引用位置包含无效证据标识',
    )
  }
  if (request.quote) {
    if (
      !['summary', 'highlight', 'section_paragraph'].includes(
        request.quote.kind,
      )
    ) {
      throw new CountryOutageHttpError(
        400,
        'invalid_report_quote',
        'quote.kind 必须为 summary、highlight 或 section_paragraph',
      )
    }
    if (
      request.quote.kind === 'summary' &&
      (request.quote.highlight_index !== undefined ||
        request.quote.section_id !== undefined ||
        request.quote.paragraph_index !== undefined)
    ) {
      throw new CountryOutageHttpError(
        400,
        'invalid_report_quote',
        'summary 引用不能包含章节或序号字段',
      )
    }
    if (
      request.quote.kind === 'highlight' &&
      (!Number.isSafeInteger(request.quote.highlight_index) ||
        request.quote.highlight_index! < 0 ||
        request.quote.section_id !== undefined ||
        request.quote.paragraph_index !== undefined)
    ) {
      throw new CountryOutageHttpError(
        400,
        'invalid_report_quote',
        'highlight 引用必须包含非负 highlight_index',
      )
    }
    if (
      request.quote.kind === 'section_paragraph' &&
      (!request.quote.section_id?.trim() ||
        !Number.isSafeInteger(request.quote.paragraph_index) ||
        request.quote.paragraph_index! < 0 ||
        request.quote.highlight_index !== undefined)
    ) {
      throw new CountryOutageHttpError(
        400,
        'invalid_report_quote',
        'section_paragraph 引用必须包含 section_id 和非负 paragraph_index',
      )
    }
  }
}

function validateGenerationResult(
  report: StoredReport,
  result: ReportGenerationResult,
): ReportQuestionContext {
  const { document, artifacts } = result
  if (!document.validation.passed) {
    throw new CountryOutageHttpError(
      422,
      'report_validation_failed',
      '报告未通过机器校验，未发布正式结果',
    )
  }
  const expectedCountryCode =
    COUNTRY_OUTAGE_REFERENCE.exec(report.eventReference)?.[1]
  if (
    normalizedReference(document.event.legacy_reference) !==
      normalizedReference(report.eventReference) ||
    document.event.incident_id !== document.snapshot.incidentId ||
    document.event.event_type !== 'country_outage' ||
    !expectedCountryCode ||
    document.event.country_code !== expectedCountryCode ||
    !document.event.country_name.trim() ||
    !document.event.display_name.trim()
  ) {
    throw new CountryOutageHttpError(
      409,
      'snapshot_identity_conflict',
      '报告事件身份与请求不一致',
      true,
      '重新读取当前事件快照后再试',
    )
  }
  if (
    document.snapshot.publicationId !== report.expectedPublicationId ||
    document.snapshot.revision !== report.expectedRevision ||
    document.snapshot.collectorId !== 'rrc25'
  ) {
    throw new CountryOutageHttpError(
      409,
      'snapshot_identity_conflict',
      '报告快照身份与用户触发时固定的 publication 或 revision 不一致',
      true,
      '刷新事件页面并生成新版报告',
    )
  }
  if (artifacts.artifactId !== document.artifactId) {
    throw new CountryOutageHttpError(
      422,
      'artifact_identity_conflict',
      '下载制品身份与正式报告不一致',
    )
  }
  const context: ReportQuestionContext = result.questionContext ?? {
    factSetId: document.factSetId,
    snapshot: document.snapshot,
    evidenceRefs: collectDocumentEvidenceRefs(document),
  }
  if (
    context.factSetId !== document.factSetId ||
    !sameSnapshot(context.snapshot, document.snapshot) ||
    context.evidenceRefs.some((reference) => !EVIDENCE_REFERENCE.test(reference))
  ) {
    throw new CountryOutageHttpError(
      422,
      'question_context_conflict',
      '追问事实合同与正式报告身份不一致',
    )
  }
  return {
    ...context,
    evidenceRefs: [...new Set(context.evidenceRefs)].sort(
      compareUnicodeCodePoints,
    ),
  }
}

function sameSnapshot(
  left: ReportQuestionContext['snapshot'],
  right: ReportQuestionContext['snapshot'],
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

function validateQuestionAnswer(
  answer: QuestionAnswer,
  context: ReportQuestionContext,
  maximumAnswerCharacters: number,
): QuestionAnswer {
  const text = answer.text.trim()
  if (!text) {
    throw new CountryOutageHttpError(
      422,
      'answer_validation_failed',
      '回答为空，未发布正式结果',
    )
  }
  if (text.length > maximumAnswerCharacters) {
    throw new CountryOutageHttpError(
      422,
      'answer_too_large',
      `回答超过 ${maximumAnswerCharacters} 字符限制`,
    )
  }
  const allowed = new Set(context.evidenceRefs)
  if (
    answer.evidenceRefs.some(
      (reference) =>
        !EVIDENCE_REFERENCE.test(reference) || !allowed.has(reference),
    )
  ) {
    throw new CountryOutageHttpError(
      422,
      'answer_evidence_conflict',
      '回答引用了原报告事实合同之外的证据',
    )
  }
  if (answer.evidenceRecords.length > 100) {
    throw new CountryOutageHttpError(
      422,
      'answer_evidence_too_large',
      '回答证据记录超过 100 条限制',
    )
  }
  const recordsByRef = new Map(
    answer.evidenceRecords.map((record) => [record.evidenceRef, record]),
  )
  const uniqueRefs = [...new Set(answer.evidenceRefs)]
  if (
    recordsByRef.size !== answer.evidenceRecords.length ||
    recordsByRef.size !== uniqueRefs.length ||
    uniqueRefs.some((reference) => !recordsByRef.has(reference)) ||
    answer.evidenceRecords.some(
      (record) =>
        !allowed.has(record.evidenceRef) ||
        !EVIDENCE_REFERENCE.test(record.evidenceRef) ||
        !record.label.trim() ||
        !record.statisticalScope.trim() ||
        !/RRC25/i.test(record.statisticalScope),
    )
  ) {
    throw new CountryOutageHttpError(
      422,
      'answer_evidence_record_conflict',
      '回答证据记录未与原事实引用一一对应，或统计范围未固定为 RRC25',
    )
  }
  return {
    kind: answer.kind,
    text,
    evidenceRefs: uniqueRefs,
    evidenceRecords: answer.evidenceRecords.map((record) => ({
      ...record,
      label: record.label.trim(),
      statisticalScope: record.statisticalScope.trim(),
    })),
    missingEvidence: [...new Set(
      answer.missingEvidence.map((item) => item.trim()),
    )]
      .filter(Boolean)
      .slice(0, 20),
    limitations: [...new Set(answer.limitations.map((item) => item.trim()))]
      .filter(Boolean)
      .slice(0, 20),
  }
}

export class CountryOutageCoreSessionManager {
  readonly #reportGenerator: CountryOutageReportGenerationPort
  readonly #questionService: CountryOutageQuestionService
  readonly #baseReportCache: CountryOutageBaseReportCache | undefined
  readonly #reportServiceIdentity:
    | CountryOutageReportServiceIdentity
    | undefined
  readonly #authorize: AuthorizeCountryOutageEvent
  readonly #limits: CountryOutageServerLimits
  readonly #now: () => number
  readonly #timersEnabled: boolean
  readonly #reports = new Map<string, StoredReport>()
  readonly #runIndex = new Map<string, RunLocator>()
  readonly #reportIdempotency = new Map<string, string>()
  readonly #activeReportByUser = new Map<string, string>()
  readonly #activeQuestionRunByUser = new Map<string, string>()
  readonly #reportAcceptedAtByUser = new Map<string, number[]>()
  readonly #questionAcceptedAtByUser = new Map<string, number[]>()
  readonly #pendingReportIds: string[] = []
  #runningReportCount = 0

  constructor(options: CountryOutageCoreSessionManagerOptions) {
    if (
      (options.reportGenerator === undefined) ===
      (options.reportService === undefined)
    ) {
      throw new TypeError(
        '必须且只能提供一个 RRC25 报告生成端口',
      )
    }
    this.#reportGenerator =
      options.reportGenerator ??
      adaptCountryOutageReportService(options.reportService!)
    this.#questionService = options.questionService
    this.#authorize = options.authorize
    this.#limits = {
      ...DEFAULT_COUNTRY_OUTAGE_SERVER_LIMITS,
      ...options.limits,
      maximumActiveAnswers: 1,
      maximumActiveReportRunsPerUser: 1,
    }
    if (this.#limits.expiryReminderMs >= this.#limits.sessionTtlMs) {
      throw new Error('到期提醒必须早于会话有效期')
    }
    this.#now = options.now ?? Date.now
    this.#timersEnabled = options.timersEnabled ?? true
    if (options.baseReportCache) {
      this.#reportServiceIdentity =
        freezeCountryOutageReportServiceIdentity(
          options.baseReportCache.reportServiceIdentity,
        )
      this.#baseReportCache =
        options.baseReportCache.store ??
        new CountryOutageBaseReportCache({
          ...(options.baseReportCache.ttlMs === undefined
            ? {}
            : { ttlMs: options.baseReportCache.ttlMs }),
          now: this.#now,
        })
    } else {
      this.#reportServiceIdentity = undefined
      this.#baseReportCache = undefined
    }
  }

  get limits(): Readonly<CountryOutageServerLimits> {
    return this.#limits
  }

  async createReport(
    principal: CountryOutagePrincipal,
    request: CreateReportRequest,
  ): Promise<CreateReportResponse> {
    validatePrincipal(principal)
    validateCreateReportRequest(request)
    this.sweep()
    await this.#requireEventAccess(principal, request.event_reference)

    const idempotencyIndex = `${principal.userId}\u0000${request.idempotency_key}`
    const fingerprint = reportFingerprint(request)
    const existingId = this.#reportIdempotency.get(idempotencyIndex)
    if (existingId) {
      const existing = this.#reports.get(existingId)
      if (!existing) {
        this.#reportIdempotency.delete(idempotencyIndex)
      } else {
        if (existing.requestFingerprint !== fingerprint) {
          throw new CountryOutageHttpError(
            409,
            'idempotency_conflict',
            '相同 idempotency_key 已用于不同的报告请求',
          )
        }
        if (existing.state === 'expired') {
          throw new CountryOutageHttpError(
            410,
            'session_expired',
            '短期会话已到期，请基于当前合法快照重新生成',
          )
        }
        return this.#createReportResponse(existing, true)
      }
    }

    const activeId = this.#activeReportByUser.get(principal.userId)
    if (activeId) {
      const active = this.#reports.get(activeId)
      if (active && !isTerminal(active.state)) {
        if (active.requestFingerprint === fingerprint) {
          this.#reportIdempotency.set(idempotencyIndex, active.id)
          return this.#createReportResponse(active, true)
        }
        throw new CountryOutageHttpError(
          409,
          'active_report_run_exists',
          '当前用户已有一项排队或运行中的报告',
          false,
          '等待当前运行完成或先取消该运行',
        )
      }
      this.#activeReportByUser.delete(principal.userId)
    }

    this.#enforceReportRate(principal.userId)
    if (
      this.#runningReportCount >=
        this.#limits.maximumActiveReportRunsGlobal &&
      this.#pendingReportIds.length >= this.#limits.maximumQueueDepth
    ) {
      throw new CountryOutageHttpError(
        429,
        'report_queue_full',
        '报告队列已满，请稍后再试',
        true,
      )
    }

    const createdAtMs = this.#now()
    const report: StoredReport = {
      id: `cor_${randomUUID().replaceAll('-', '')}`,
      runId: `run_${randomUUID().replaceAll('-', '')}`,
      ownerUserId: principal.userId,
      authorizationScope: principal.authorizationScope,
      eventReference: request.event_reference,
      expectedPublicationId: request.publication_id,
      expectedRevision: request.revision,
      idempotencyKey: request.idempotency_key,
      requestFingerprint: fingerprint,
      state: 'queued',
      phase: 'queued',
      createdAtMs,
      expiresAtMs: createdAtMs + this.#limits.sessionTtlMs,
      reminderAtMs:
        createdAtMs +
        this.#limits.sessionTtlMs -
        this.#limits.expiryReminderMs,
      reminded: false,
      controller: new AbortController(),
      lifecycleTimers: [],
      events: [],
      nextEventId: 1,
      listeners: new Set(),
      questions: [],
      questionIdempotency: new Map(),
      questionFingerprints: new Map(),
    }
    this.#reports.set(report.id, report)
    this.#runIndex.set(report.runId, {
      kind: 'report',
      reportId: report.id,
    })
    this.#reportIdempotency.set(idempotencyIndex, report.id)
    this.#activeReportByUser.set(principal.userId, report.id)
    this.#recordReportAccepted(principal.userId, createdAtMs)
    this.#emitReport(report)
    this.#scheduleLifecycle(report)

    if (
      this.#runningReportCount <
      this.#limits.maximumActiveReportRunsGlobal
    ) {
      this.#startReport(report)
    } else {
      this.#pendingReportIds.push(report.id)
    }
    return this.#createReportResponse(report, false)
  }

  async createQuestion(
    principal: CountryOutagePrincipal,
    reportId: string,
    request: CreateDomeyeOnlyQuestionRequest,
  ): Promise<CreateQuestionResponse> {
    validatePrincipal(principal)
    validateCreateQuestionRequest(
      request,
      this.#limits.maximumQuestionCharacters,
    )
    this.sweep()
    const report = await this.#ownedReport(principal, reportId)
    if (report.state === 'expired') {
      throw new CountryOutageHttpError(
        410,
        'session_expired',
        '短期会话已到期，请基于当前合法快照重新生成',
      )
    }
    if (
      report.state !== 'completed' ||
      !report.document ||
      !report.questionContext
    ) {
      throw new CountryOutageHttpError(
        409,
        'report_not_ready',
        '正式报告尚未完成，暂不能追问',
      )
    }
    const idempotencyKey = request.idempotency_key
    const quote = normalizedQuote(request)
    this.#validateQuote(report, quote)
    const fingerprint = questionFingerprint(request.question, quote)

    const idempotentQuestionId =
      report.questionIdempotency.get(idempotencyKey)
    if (idempotentQuestionId) {
      const existing = report.questions.find(
        (item) => item.id === idempotentQuestionId,
      )
      if (!existing) {
        report.questionIdempotency.delete(idempotencyKey)
      } else {
        if (existing.fingerprint !== fingerprint) {
          throw new CountryOutageHttpError(
            409,
            'idempotency_conflict',
            '相同 idempotency_key 已用于不同问题',
          )
        }
        return this.#createQuestionResponse(report, existing, true)
      }
    }

    const duplicateId = report.questionFingerprints.get(fingerprint)
    if (duplicateId) {
      const duplicate = report.questions.find(
        (item) => item.id === duplicateId,
      )
      if (duplicate && canReuseQuestionFingerprint(duplicate)) {
        report.questionIdempotency.set(idempotencyKey, duplicate.id)
        return this.#createQuestionResponse(report, duplicate, true)
      }
      report.questionFingerprints.delete(fingerprint)
    }

    if (report.questions.length >= this.#limits.maximumQuestions) {
      throw new CountryOutageHttpError(
        429,
        'question_limit_reached',
        `当前短期会话最多允许 ${this.#limits.maximumQuestions} 个问题`,
      )
    }
    const activeQuestionRun = this.#activeQuestionRunByUser.get(
      principal.userId,
    )
    if (activeQuestionRun) {
      throw new CountryOutageHttpError(
        409,
        'answer_run_active',
        '当前用户已有一个问题正在回答',
        false,
        '等待当前回答完成或先取消该运行',
      )
    }
    this.#enforceQuestionRate(principal.userId)

    const question: StoredQuestion = {
      id: `q_${randomUUID().replaceAll('-', '')}`,
      number: report.questions.length + 1,
      runId: `run_${randomUUID().replaceAll('-', '')}`,
      idempotencyKey,
      fingerprint,
      question: request.question.trim(),
      ...(quote ? { quote } : {}),
      state: 'running',
      phase: 'answering',
      controller: new AbortController(),
    }
    report.questions.push(question)
    report.questionIdempotency.set(idempotencyKey, question.id)
    report.questionFingerprints.set(fingerprint, question.id)
    this.#recordQuestionAccepted(principal.userId, this.#now())
    this.#activeQuestionRunByUser.set(principal.userId, question.runId)
    this.#runIndex.set(question.runId, {
      kind: 'question',
      reportId: report.id,
      questionId: question.id,
    })
    this.#emitQuestion(report, question)
    this.#startQuestion(report, question)
    return this.#createQuestionResponse(report, question, false)
  }

  async abortRun(
    principal: CountryOutagePrincipal,
    runId: string,
  ): Promise<AbortRunResponse> {
    validatePrincipal(principal)
    this.sweep()
    const locator = this.#runIndex.get(runId)
    if (!locator) {
      throw new CountryOutageHttpError(404, 'run_not_found', '运行不存在')
    }
    const report = await this.#ownedReport(
      principal,
      locator.reportId,
    )
    if (report.state === 'expired') {
      throw new CountryOutageHttpError(
        410,
        'session_expired',
        '短期会话已到期',
      )
    }
    if (locator.kind === 'report') {
      const effective = !isTerminal(report.state)
      if (effective) this.#cancelReport(report)
      return {
        schema_version: COUNTRY_OUTAGE_AGENT_HTTP_SCHEMA_VERSION,
        report_id: report.id,
        run_id: report.runId,
        state: report.state,
        abort_effective: effective,
      }
    }
    const question = report.questions.find(
      (item) => item.id === locator.questionId,
    )
    if (!question) {
      throw new CountryOutageHttpError(404, 'run_not_found', '运行不存在')
    }
    const effective = !isTerminal(question.state)
    if (effective) this.#cancelQuestion(report, question)
    return {
      schema_version: COUNTRY_OUTAGE_AGENT_HTTP_SCHEMA_VERSION,
      report_id: report.id,
      run_id: question.runId,
      state: question.state,
      abort_effective: effective,
    }
  }

  async subscribe(
    principal: CountryOutagePrincipal,
    reportId: string,
    afterEventId: number,
    listener: (event: CountryOutageAgentEvent) => void,
  ): Promise<EventSubscription> {
    validatePrincipal(principal)
    this.sweep()
    const report = await this.#ownedReport(principal, reportId)
    if (report.state === 'expired') {
      throw new CountryOutageHttpError(
        410,
        'session_expired',
        '短期会话已到期，请重新生成',
      )
    }
    const pending: CountryOutageAgentEvent[] = []
    let active = false
    let closed = false
    const bufferedListener = (event: CountryOutageAgentEvent): void => {
      if (closed) return
      if (active) listener(event)
      else pending.push(event)
    }
    report.listeners.add(bufferedListener)
    return {
      replay: report.events.filter(
        (event) => event.event_id > afterEventId,
      ),
      activate: () => {
        if (closed || active) return
        active = true
        for (const event of pending.splice(0)) listener(event)
      },
      close: () => {
        if (closed) return
        closed = true
        report.listeners.delete(bufferedListener)
      },
    }
  }

  async getArtifact(
    principal: CountryOutagePrincipal,
    reportId: string,
    format: 'markdown' | 'pdf',
  ): Promise<DownloadArtifact> {
    validatePrincipal(principal)
    this.sweep()
    const report = await this.#ownedReport(principal, reportId)
    if (report.state === 'expired') {
      throw new CountryOutageHttpError(
        410,
        'session_expired',
        '短期会话已到期，下载不可再开始',
      )
    }
    if (report.state !== 'completed' || !report.artifacts) {
      throw new CountryOutageHttpError(
        409,
        'report_not_ready',
        '正式报告尚未完成，下载不可用',
      )
    }
    const outcome = report.artifacts[format]
    if (outcome.status === 'failed') {
      throw new CountryOutageHttpError(
        409,
        outcome.error.code,
        outcome.error.message,
      )
    }
    return {
      artifactId: report.artifacts.artifactId,
      filename: outcome.artifact.filename,
      mediaType: outcome.artifact.mediaType,
      byteLength: outcome.artifact.byteLength,
      sha256: outcome.artifact.sha256,
      content: outcome.artifact.content,
      downloadDeadlineAtMs:
        report.expiresAtMs + this.#limits.completedDownloadGraceMs,
    }
  }

  sweep(): void {
    this.#baseReportCache?.sweep()
    const now = this.#now()
    for (const report of [...this.#reports.values()]) {
      if (report.state === 'expired') {
        if (
          report.expiredAtMs !== undefined &&
          now - report.expiredAtMs >= this.#limits.tombstoneTtlMs
        ) {
          this.#deleteReport(report)
        }
        continue
      }
      if (!report.reminded && now >= report.reminderAtMs) {
        report.reminded = true
        this.#emitSessionNotice(report, 'session_expiring')
      }
      if (now >= report.expiresAtMs) {
        this.#expireReport(report)
      }
    }
  }

  #createReportResponse(
    report: StoredReport,
    deduplicated: boolean,
  ): CreateReportResponse {
    return {
      schema_version: COUNTRY_OUTAGE_AGENT_HTTP_SCHEMA_VERSION,
      report_id: report.id,
      run_id: report.runId,
      state: report.state,
      phase: report.phase,
      session: sessionDescriptor(report),
      deduplicated,
    }
  }

  #createQuestionResponse(
    report: StoredReport,
    question: StoredQuestion,
    deduplicated: boolean,
  ): CreateQuestionResponse {
    return {
      schema_version: COUNTRY_OUTAGE_AGENT_HTTP_SCHEMA_VERSION,
      report_id: report.id,
      question_id: question.id,
      number: question.number,
      run_id: question.runId,
      state: question.state,
      phase: question.phase,
      session: sessionDescriptor(report),
      deduplicated,
    }
  }

  async #requireEventAccess(
    principal: CountryOutagePrincipal,
    eventReference: string,
  ): Promise<void> {
    let allowed = false
    try {
      allowed = await this.#authorize(principal, eventReference)
    } catch {
      allowed = false
    }
    if (!allowed) {
      throw new CountryOutageHttpError(
        403,
        'event_access_denied',
        '当前用户无权访问该国家中断事件',
      )
    }
  }

  async #ownedReport(
    principal: CountryOutagePrincipal,
    reportId: string,
  ): Promise<StoredReport> {
    const report = this.#reports.get(reportId)
    if (!report || report.ownerUserId !== principal.userId) {
      throw new CountryOutageHttpError(
        404,
        'report_not_found',
        '报告不存在',
      )
    }
    if (report.authorizationScope !== principal.authorizationScope) {
      throw new CountryOutageHttpError(
        403,
        'authorization_scope_changed',
        '当前授权范围与创建报告时不一致',
      )
    }
    await this.#requireEventAccess(principal, report.eventReference)
    return report
  }

  #enforceReportRate(userId: string): void {
    const cutoff = this.#now() - 60 * 60 * 1000
    const accepted = (this.#reportAcceptedAtByUser.get(userId) ?? []).filter(
      (value) => value > cutoff,
    )
    this.#reportAcceptedAtByUser.set(userId, accepted)
    if (
      accepted.length >= this.#limits.maximumReportRunsPerUserPerHour
    ) {
      throw new CountryOutageHttpError(
        429,
        'report_rate_limited',
        '当前用户每小时报告生成次数已达上限',
        true,
      )
    }
  }

  #recordReportAccepted(userId: string, atMs: number): void {
    const values = this.#reportAcceptedAtByUser.get(userId) ?? []
    values.push(atMs)
    this.#reportAcceptedAtByUser.set(userId, values)
  }

  #enforceQuestionRate(userId: string): void {
    const cutoff = this.#now() - 60 * 1000
    const accepted = (
      this.#questionAcceptedAtByUser.get(userId) ?? []
    ).filter((value) => value > cutoff)
    this.#questionAcceptedAtByUser.set(userId, accepted)
    if (
      accepted.length >= this.#limits.maximumQuestionsPerMinute
    ) {
      throw new CountryOutageHttpError(
        429,
        'question_rate_limited',
        '当前用户每分钟问题数已达上限',
        true,
      )
    }
  }

  #recordQuestionAccepted(userId: string, atMs: number): void {
    const values = this.#questionAcceptedAtByUser.get(userId) ?? []
    values.push(atMs)
    this.#questionAcceptedAtByUser.set(userId, values)
  }

  #releaseQuestionSlot(
    report: StoredReport,
    question: StoredQuestion,
  ): void {
    if (
      this.#activeQuestionRunByUser.get(report.ownerUserId) ===
      question.runId
    ) {
      this.#activeQuestionRunByUser.delete(report.ownerUserId)
    }
  }

  #pruneQuestionRate(userId: string): void {
    const cutoff = this.#now() - 60 * 1000
    const values = (
      this.#questionAcceptedAtByUser.get(userId) ?? []
    ).filter((value) => value > cutoff)
    if (values.length > 0) {
      this.#questionAcceptedAtByUser.set(userId, values)
    } else {
      this.#questionAcceptedAtByUser.delete(userId)
    }
  }

  #validateQuote(report: StoredReport, quote: ReportQuote | undefined): void {
    if (!quote || !report.document || !report.questionContext) return
    let anchorEvidenceRefs: readonly string[]
    if (quote.kind === 'summary') {
      anchorEvidenceRefs = report.document.draft.summary.evidenceRefs
    } else if (quote.kind === 'highlight') {
      const highlight = report.document.draft.highlights[quote.highlightIndex]
      if (!highlight) {
        throw new CountryOutageHttpError(
          400,
          'invalid_report_quote',
          '引用的报告关键数字不存在',
        )
      }
      anchorEvidenceRefs = highlight.evidenceRefs
    } else {
      const section = report.document.draft.sections.find(
        (item) => item.id === quote.sectionId,
      )
      const paragraph = section?.paragraphs[quote.paragraphIndex]
      if (!paragraph) {
        throw new CountryOutageHttpError(
          400,
          'invalid_report_quote',
          '引用的报告章节或段落不存在',
        )
      }
      anchorEvidenceRefs = paragraph.evidenceRefs
    }
    const contractRefs = new Set(report.questionContext.evidenceRefs)
    const anchorRefs = new Set(anchorEvidenceRefs)
    if (
      quote.evidenceRefs?.some(
        (reference) =>
          !contractRefs.has(reference) || !anchorRefs.has(reference),
      )
    ) {
      throw new CountryOutageHttpError(
        400,
        'invalid_report_quote',
        '引用证据不属于选定报告位置或原报告事实合同',
      )
    }
  }

  #startReport(report: StoredReport): void {
    if (report.state !== 'queued') return
    this.#runningReportCount += 1
    report.state = 'running'
    report.phase = 'reading_data'
    this.#emitReport(report)
    const reportTimeout = this.#timeout(() => {
      if (report.state !== 'running') return
      report.controller.abort()
      this.#failReport(report, {
        code: 'report_timeout',
        message: '报告生成超过 120 秒时限',
        retryable: true,
        next_action: '使用新的 idempotency_key 重新生成',
      })
    }, this.#limits.reportRunTimeoutMs)
    if (reportTimeout) report.timeout = reportTimeout

    const cacheKey = this.#baseReportCacheKey(report)
    const cachedResult =
      cacheKey && this.#baseReportCache
        ? this.#baseReportCache.get(cacheKey)
        : undefined
    const generation = cachedResult
      ? Promise.resolve({ result: cachedResult, cacheHit: true })
      : this.#reportGenerator
          .generateReport({
            eventReference: report.eventReference,
            publicationId: report.expectedPublicationId,
            revision: report.expectedRevision,
            signal: report.controller.signal,
            onPhase: (phase) => {
              if (
                report.state !== 'running' ||
                reportPhaseRank(phase) <= reportPhaseRank(report.phase)
              ) {
                return
              }
              report.phase = phase
              this.#emitReport(report)
            },
          })
          .then((result) => ({ result, cacheHit: false }))
    void generation
      .then(({ result, cacheHit }) => {
        if (report.state !== 'running') return
        if (reportPhaseRank(report.phase) < reportPhaseRank('validating')) {
          report.phase = 'validating'
          this.#emitReport(report)
        }
        const questionContext = validateGenerationResult(report, result)
        if (
          !cacheHit &&
          cacheKey &&
          this.#baseReportCache
        ) {
          this.#baseReportCache.set(cacheKey, result)
        }
        report.document = result.document
        report.artifacts = result.artifacts
        report.questionContext = questionContext
        report.state = 'completed'
        report.phase = 'completed'
        this.#clearRunTimeout(report)
        this.#emitReport(report)
        this.#releaseReportSlot(report)
      })
      .catch((error: unknown) => {
        if (report.state !== 'running') return
        if (report.controller.signal.aborted) {
          this.#cancelReport(report)
          return
        }
        this.#failReport(
          report,
          publicErrorFromUnknown(
            error,
            'report_generation_failed',
            '报告生成失败，未发布正式结果',
          ),
        )
      })
  }

  #baseReportCacheKey(
    report: StoredReport,
  ): CountryOutageBaseReportCacheKey | undefined {
    if (!this.#reportServiceIdentity || !this.#baseReportCache) {
      return undefined
    }
    return {
      authorizationScope: report.authorizationScope,
      eventReference: report.eventReference,
      publicationId: report.expectedPublicationId,
      revision: report.expectedRevision,
      reportServiceIdentity: this.#reportServiceIdentity,
    }
  }

  #startQuestion(report: StoredReport, question: StoredQuestion): void {
    if (!report.document || !report.questionContext) return
    const questionTimeout = this.#timeout(() => {
      if (question.state !== 'running') return
      question.controller.abort()
      this.#failQuestion(report, question, {
        code: 'question_timeout',
        message: '追问回答超过 60 秒时限',
        retryable: true,
        next_action: '使用新的 idempotency_key 重试',
      })
    }, this.#limits.questionRunTimeoutMs)
    if (questionTimeout) question.timeout = questionTimeout
    void this.#questionService
      .answer({
        reportId: report.id,
        report: report.document,
        questionContext: report.questionContext,
        question: question.question,
        evidenceMode: 'domeye_only',
        ...(question.quote ? { quote: question.quote } : {}),
        signal: question.controller.signal,
      })
      .then((answer) => {
        if (question.state !== 'running') return
        const validatedAnswer = validateQuestionAnswer(
          answer,
          report.questionContext!,
          this.#limits.maximumAnswerCharacters,
        )
        question.answer = validatedAnswer
        question.state = 'completed'
        question.phase = 'completed'
        this.#clearQuestionTimeout(question)
        this.#releaseQuestionSlot(report, question)
        this.#emitQuestion(report, question)
      })
      .catch((error: unknown) => {
        if (question.state !== 'running') return
        if (question.controller.signal.aborted) {
          this.#cancelQuestion(report, question)
          return
        }
        this.#failQuestion(
          report,
          question,
          publicErrorFromUnknown(
            error,
            'question_generation_failed',
            '追问回答失败，未发布正式结果',
          ),
        )
      })
  }

  #cancelReport(report: StoredReport): void {
    if (isTerminal(report.state)) return
    const wasRunning = report.state === 'running'
    report.controller.abort()
    this.#clearRunTimeout(report)
    report.state = 'cancelled'
    report.phase = 'cancelled'
    delete report.document
    delete report.artifacts
    delete report.questionContext
    this.#emitReport(report)
    if (wasRunning) this.#releaseReportSlot(report)
    else {
      const index = this.#pendingReportIds.indexOf(report.id)
      if (index >= 0) this.#pendingReportIds.splice(index, 1)
      this.#releaseUserReport(report)
    }
  }

  #failReport(report: StoredReport, error: AgentPublicError): void {
    if (isTerminal(report.state)) return
    const wasRunning = report.state === 'running'
    this.#clearRunTimeout(report)
    report.state = 'failed'
    report.phase = 'failed'
    report.error = error
    delete report.document
    delete report.artifacts
    delete report.questionContext
    this.#emitReport(report)
    if (wasRunning) this.#releaseReportSlot(report)
    else this.#releaseUserReport(report)
  }

  #cancelQuestion(report: StoredReport, question: StoredQuestion): void {
    if (isTerminal(question.state)) return
    question.controller.abort()
    this.#clearQuestionTimeout(question)
    question.state = 'cancelled'
    question.phase = 'cancelled'
    delete question.answer
    this.#releaseQuestionFingerprint(report, question)
    this.#releaseQuestionSlot(report, question)
    this.#emitQuestion(report, question)
  }

  #failQuestion(
    report: StoredReport,
    question: StoredQuestion,
    error: AgentPublicError,
  ): void {
    if (isTerminal(question.state)) return
    this.#clearQuestionTimeout(question)
    question.state = 'failed'
    question.phase = 'failed'
    question.error = error
    delete question.answer
    this.#releaseQuestionFingerprint(report, question)
    this.#releaseQuestionSlot(report, question)
    this.#emitQuestion(report, question)
  }

  #releaseQuestionFingerprint(
    report: StoredReport,
    question: StoredQuestion,
  ): void {
    if (
      report.questionFingerprints.get(question.fingerprint) ===
      question.id
    ) {
      report.questionFingerprints.delete(question.fingerprint)
    }
  }

  #releaseReportSlot(report: StoredReport): void {
    this.#runningReportCount = Math.max(0, this.#runningReportCount - 1)
    this.#releaseUserReport(report)
    this.#drainQueue()
  }

  #releaseUserReport(report: StoredReport): void {
    if (this.#activeReportByUser.get(report.ownerUserId) === report.id) {
      this.#activeReportByUser.delete(report.ownerUserId)
    }
  }

  #drainQueue(): void {
    while (
      this.#runningReportCount <
        this.#limits.maximumActiveReportRunsGlobal &&
      this.#pendingReportIds.length > 0
    ) {
      const nextId = this.#pendingReportIds.shift()
      if (!nextId) return
      const report = this.#reports.get(nextId)
      if (!report || report.state !== 'queued') continue
      this.#startReport(report)
    }
  }

  #emitReport(report: StoredReport): void {
    const event: ReportStateEvent = {
      schema_version: COUNTRY_OUTAGE_AGENT_EVENT_SCHEMA_VERSION,
      event_id: report.nextEventId,
      report_id: report.id,
      run_id: report.runId,
      event_type: 'report_state',
      at: iso(this.#now()),
      state: report.state,
      phase: report.phase,
      session: sessionDescriptor(report),
      ...(report.document
        ? {
            snapshot: report.document.snapshot,
            report: report.document,
          }
        : {}),
      ...(report.artifacts
        ? {
            artifacts: [
              artifactMetadata(
                report.id,
                report.artifacts.artifactId,
                report.artifacts.markdown,
              ),
              artifactMetadata(
                report.id,
                report.artifacts.artifactId,
                report.artifacts.pdf,
              ),
            ],
          }
        : {}),
      ...(report.error ? { error: report.error } : {}),
    }
    this.#publish(report, event)
  }

  #emitQuestion(report: StoredReport, question: StoredQuestion): void {
    const event: QuestionStateEvent = {
      schema_version: COUNTRY_OUTAGE_AGENT_EVENT_SCHEMA_VERSION,
      event_id: report.nextEventId,
      report_id: report.id,
      run_id: question.runId,
      event_type: 'question_state',
      at: iso(this.#now()),
      state: question.state,
      phase: question.phase,
      session: sessionDescriptor(report),
      question: {
        question_id: question.id,
        number: question.number,
        question: question.question,
        evidence_mode: 'domeye_only',
        ...(question.quote
          ? {
              quote: {
                kind: question.quote.kind,
                ...(question.quote.kind === 'highlight'
                  ? { highlight_index: question.quote.highlightIndex }
                  : {}),
                ...(question.quote.kind === 'section_paragraph'
                  ? {
                      section_id: question.quote.sectionId,
                      paragraph_index: question.quote.paragraphIndex,
                    }
                  : {}),
                ...(question.quote.evidenceRefs
                  ? { evidence_refs: question.quote.evidenceRefs }
                  : {}),
              },
            }
          : {}),
        state: question.state,
        ...(question.answer
          ? {
              answer: {
                kind: question.answer.kind,
                text: question.answer.text,
                evidence_refs: question.answer.evidenceRefs,
                evidence_records: question.answer.evidenceRecords.map(
                  (record) => ({
                    evidence_ref: record.evidenceRef,
                    source: record.source,
                    label: record.label,
                    metric: record.metric,
                    value: record.value,
                    observed_at_utc: record.observedAtUtc,
                    observed_at_local: record.observedAtLocal,
                    statistical_scope: record.statisticalScope,
                  }),
                ),
                missing_evidence: question.answer.missingEvidence,
                limitations: question.answer.limitations,
                snapshot: report.document!.snapshot,
              },
            }
          : {}),
        ...(question.error ? { error: question.error } : {}),
      },
    }
    this.#publish(report, event)
  }

  #emitSessionNotice(
    report: StoredReport,
    phase: 'session_expiring' | 'session_expired',
  ): void {
    const event: SessionNoticeEvent = {
      schema_version: COUNTRY_OUTAGE_AGENT_EVENT_SCHEMA_VERSION,
      event_id: report.nextEventId,
      report_id: report.id,
      event_type: 'session_notice',
      at: iso(this.#now()),
      state: phase === 'session_expired' ? 'expired' : report.state,
      phase,
      session: sessionDescriptor(report),
    }
    this.#publish(report, event)
  }

  #publish(
    report: StoredReport,
    event: CountryOutageAgentEvent,
  ): void {
    report.nextEventId += 1
    report.events.push(event)
    for (const listener of [...report.listeners]) listener(event)
  }

  #scheduleLifecycle(report: StoredReport): void {
    if (!this.#timersEnabled) return
    const reminder = this.#timeout(
      () => this.sweep(),
      Math.max(0, report.reminderAtMs - this.#now()),
    )
    const expiry = this.#timeout(
      () => this.sweep(),
      Math.max(0, report.expiresAtMs - this.#now()),
    )
    if (reminder) report.lifecycleTimers.push(reminder)
    if (expiry) report.lifecycleTimers.push(expiry)
  }

  #expireReport(report: StoredReport): void {
    if (report.state === 'expired') return
    const wasRunning = report.state === 'running'
    const wasQueued = report.state === 'queued'
    report.controller.abort()
    this.#clearRunTimeout(report)
    for (const question of report.questions) {
      if (!isTerminal(question.state)) {
        question.controller.abort()
        this.#releaseQuestionSlot(report, question)
      }
      this.#clearQuestionTimeout(question)
    }
    report.state = 'expired'
    report.phase =
      report.phase === 'completed' ? 'completed' : report.phase
    report.expiredAtMs = this.#now()
    this.#emitSessionNotice(report, 'session_expired')
    // 墓碑只保留最后一条不含报告、问题或回答内容的到期通知。
    // 报告幂等键、owner 和 run locator 继续保留至 tombstone TTL，以便
    // 所有旧入口稳定返回 410，而不是继续在内存中保存短期会话正文。
    const expiryNotice = report.events.at(-1)
    report.events =
      expiryNotice?.event_type === 'session_notice' &&
      expiryNotice.phase === 'session_expired'
        ? [expiryNotice]
        : []
    for (const timer of report.lifecycleTimers) clearTimeout(timer)
    report.lifecycleTimers = []
    delete report.document
    delete report.artifacts
    delete report.questionContext
    delete report.error
    report.questions = []
    report.questionIdempotency.clear()
    report.questionFingerprints.clear()
    this.#pruneQuestionRate(report.ownerUserId)
    report.listeners.clear()
    if (wasRunning) this.#releaseReportSlot(report)
    else {
      if (wasQueued) {
        const index = this.#pendingReportIds.indexOf(report.id)
        if (index >= 0) this.#pendingReportIds.splice(index, 1)
      }
      this.#releaseUserReport(report)
    }
    if (this.#timersEnabled) {
      const tombstone = this.#timeout(
        () => this.sweep(),
        this.#limits.tombstoneTtlMs,
      )
      if (tombstone) report.lifecycleTimers.push(tombstone)
    }
  }

  #deleteReport(report: StoredReport): void {
    this.#reports.delete(report.id)
    for (const [runId, locator] of this.#runIndex) {
      if (locator.reportId === report.id) this.#runIndex.delete(runId)
    }
    for (const [key, value] of this.#reportIdempotency) {
      if (value === report.id) this.#reportIdempotency.delete(key)
    }
    for (const timer of report.lifecycleTimers) clearTimeout(timer)
  }

  #timeout(callback: () => void, delayMs: number): TimerHandle | undefined {
    if (!this.#timersEnabled) return undefined
    const timer = setTimeout(callback, delayMs)
    timer.unref()
    return timer
  }

  #clearRunTimeout(report: StoredReport): void {
    if (report.timeout) clearTimeout(report.timeout)
    delete report.timeout
  }

  #clearQuestionTimeout(question: StoredQuestion): void {
    if (question.timeout) clearTimeout(question.timeout)
    delete question.timeout
  }
}

/**
 * 旧名称仅保留源码兼容；两者指向同一个纯 RRC25 Core 实现。
 */
export {
  CountryOutageCoreSessionManager as CountryOutageSessionManager,
}
export type CountryOutageSessionManagerOptions =
  CountryOutageCoreSessionManagerOptions
