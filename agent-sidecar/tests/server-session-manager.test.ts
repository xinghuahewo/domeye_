import assert from 'node:assert/strict'
import test from 'node:test'

import type { SnapshotIdentity } from '../src/domain/contracts.js'
import {
  externalEvidenceFrozenBindingId,
  type ExternalEvidenceAppendix,
  type ExternalEvidenceFrozenBinding,
} from '../src/external/index.js'
import type {
  CountryOutageReportDocument,
  ReportArtifact,
  ReportArtifactBuildResult,
} from '../src/report/contracts.js'
import {
  CountryOutageBaseReportCache,
  CountryOutageHttpError,
  CountryOutageSessionManager,
  buildExternalAppendixMarkdownArtifact,
  DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_MAX_ENTRIES,
  DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_MAX_TOTAL_BYTES,
  DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_TTL_MS,
  ExternalAppendixArtifactError,
  type CountryOutageAgentEvent,
  type CountryOutagePrincipal,
  type CountryOutageQuestionService,
  type CountryOutageReportService,
  type CountryOutageReportServiceIdentity,
  type CountryOutageBaseReportCacheKey,
  type CreateQuestionRequest,
  type QuestionAnswer,
  type ReportGenerationInput,
  type ReportGenerationResult,
} from '../src/server/index.js'

const REFERENCE = 'country_outage/2026-02-27 09:12:32/IR/1/r'
const PUBLICATION = 'publication_v1_test'
const PRINCIPAL: CountryOutagePrincipal = {
  userId: 'user-a',
  authorizationScope: 'scope-ir-readers',
}

const REPORT_SERVICE_IDENTITY: CountryOutageReportServiceIdentity = {
  reportSpecificationVersion: 'country_outage_report_spec_v1',
  projectKnowledgeVersion: 'country_outage_report_skill_v6',
  validatorRulesVersion: 'country_outage_report_validator_rules_v5',
  skillBundleSha256: 'd'.repeat(64),
  model: {
    provider: 'test',
    model: 'test',
    modelVersion: '1',
    adapter: 'deterministic-acceptance',
  },
}

function snapshot(revision = 1): SnapshotIdentity {
  return {
    incidentId: 'incident-ir',
    publicationId: PUBLICATION,
    revision,
    dataThrough: '2026-02-28T15:00:00Z',
    isFinal: true,
    cohortId: 'cohort-ir-r1',
    collectorId: 'rrc25',
    windowStartUtc: '2026-02-28T10:05:00Z',
    windowEndUtc: '2026-02-28T15:00:00Z',
  }
}

function document(): CountryOutageReportDocument {
  const reportSnapshot = snapshot()
  return {
    schemaVersion: 'country_outage_report_document_v1',
    artifactId: 'report_server_test',
    reportContentSha256: 'a'.repeat(64),
    reportSpecificationVersion: 'country_outage_report_spec_v1',
    projectKnowledgeVersion: 'country_outage_report_skill_v6',
    validatorRulesVersion: 'country_outage_report_validator_rules_v5',
    skillBundleSha256: 'd'.repeat(64),
    generatedAt: '2026-07-28T09:30:45.000Z',
    aiGenerated: true,
    humanReviewed: false,
    event: {
      incident_id: reportSnapshot.incidentId,
      legacy_reference: REFERENCE,
      event_type: 'country_outage',
      country_code: 'IR',
      country_name: '伊朗',
      display_name: '伊朗',
    },
    snapshot: reportSnapshot,
    factSetId: 'facts_server_test',
    model: {
      provider: 'test',
      model: 'test',
      modelVersion: '1',
      adapter: 'deterministic-acceptance',
    },
    validation: {
      passed: true,
      errors: [],
      warnings: [],
      checkedEvidenceRefs: ['series:/series/0/visible_prefix_vp_count'],
    },
    draft: {
      schemaVersion: 'country_outage_report_draft_v1',
      title: '伊朗 BGP 路由可见性观测报告',
      subtitle: '窗口结束时仍低于起点',
      summary: {
        text: 'RRC25 观察到控制面可见性下降。',
        evidenceRefs: ['series:/series/0/visible_prefix_vp_count'],
      },
      highlights: [
        {
          label: '起点可见关系',
          value: '367,215 条',
          evidenceRefs: [
            'series:/series/0/visible_prefix_vp_count',
          ],
        },
      ],
      sections: [
        {
          id: 'scope',
          title: '观测范围',
          paragraphs: [
            {
              text: '只描述 RRC25 的 BGP 控制面。',
              evidenceRefs: ['overview:/observation_scope'],
            },
          ],
        },
      ],
      unknowns: [
        '全国数据面状态',
        '用户与业务影响',
        '原因与责任',
        '窗口之后是否完全恢复',
      ],
    },
  }
}

function artifact(
  format: 'markdown' | 'pdf',
  content: string,
): ReportArtifact {
  const buffer = Buffer.from(content)
  return {
    format,
    filename: `IR_test.${format === 'markdown' ? 'md' : 'pdf'}`,
    mediaType:
      format === 'markdown'
        ? 'text/markdown; charset=utf-8'
        : 'application/pdf',
    byteLength: buffer.byteLength,
    sha256: format === 'markdown' ? 'b'.repeat(64) : 'c'.repeat(64),
    content: buffer,
  }
}

function artifacts(
  pdfFailed = false,
): ReportArtifactBuildResult {
  return {
    artifactId: 'report_server_test',
    markdown: {
      status: 'ready',
      artifact: artifact('markdown', '# 伊朗报告'),
    },
    pdf: pdfFailed
      ? {
          status: 'failed',
          error: {
            format: 'pdf',
            code: 'PdfRenderError',
            message: 'PDF 中文字体不可用',
          },
        }
      : {
          status: 'ready',
          artifact: artifact('pdf', '%PDF-1.7 test'),
        },
  }
}

function result(pdfFailed = false): ReportGenerationResult {
  const report = document()
  return {
    document: report,
    artifacts: artifacts(pdfFailed),
    questionContext: {
      factSetId: report.factSetId,
      snapshot: report.snapshot,
      evidenceRefs: [
        'overview:/observation_scope',
        'series:/series/0/visible_prefix_vp_count',
      ],
      payload: { frozen: true },
    },
  }
}

function validQuestionAnswer(
  text = '该结论只描述 RRC25 BGP 控制面观测。',
): QuestionAnswer {
  return {
    kind: 'evidence_boundary',
    text,
    evidenceRefs: ['overview:/observation_scope'],
    evidenceRecords: [
      {
        evidenceRef: 'overview:/observation_scope',
        source: 'overview',
        label: '观测范围',
        metric: null,
        value: 'RRC25',
        observedAtUtc: null,
        observedAtLocal: null,
        statisticalScope: 'RRC25 固定统计范围',
      },
    ],
    missingEvidence: ['用户与业务测量'],
    limitations: ['不能据此判断用户影响'],
  }
}

function resultFor(
  input: Pick<
    ReportGenerationInput,
    'eventReference' | 'publicationId' | 'revision'
  >,
  identity: CountryOutageReportServiceIdentity =
    REPORT_SERVICE_IDENTITY,
): ReportGenerationResult {
  const generated = result()
  const pinnedSnapshot = {
    ...generated.document.snapshot,
    publicationId: input.publicationId,
    revision: input.revision,
  }
  generated.document.event.legacy_reference = input.eventReference
  generated.document.snapshot = pinnedSnapshot
  generated.document.reportSpecificationVersion =
    identity.reportSpecificationVersion
  generated.document.projectKnowledgeVersion =
    identity.projectKnowledgeVersion
  generated.document.validatorRulesVersion =
    identity.validatorRulesVersion
  generated.document.skillBundleSha256 =
    identity.skillBundleSha256
  generated.document.model = { ...identity.model }
  if (generated.questionContext) {
    generated.questionContext = {
      ...generated.questionContext,
      snapshot: { ...pinnedSnapshot },
    }
  }
  return generated
}

function request(
  idempotencyKey = 'report-request-0001',
): {
  event_reference: string
  publication_id: string
  revision: number
  idempotency_key: string
} {
  return {
    event_reference: REFERENCE,
    publication_id: PUBLICATION,
    revision: 1,
    idempotency_key: idempotencyKey,
  }
}

function cacheKey(
  authorizationScope: string,
): CountryOutageBaseReportCacheKey {
  return {
    authorizationScope,
    eventReference: REFERENCE,
    publicationId: PUBLICATION,
    revision: 1,
    reportServiceIdentity: REPORT_SERVICE_IDENTITY,
  }
}

async function settle(): Promise<void> {
  await new Promise<void>((resolve) => setImmediate(resolve))
  await new Promise<void>((resolve) => setImmediate(resolve))
}

function replayEvents(
  manager: CountryOutageSessionManager,
  reportId: string,
  principal = PRINCIPAL,
  afterEventId = 0,
): Promise<CountryOutageAgentEvent[]> {
  return manager
    .subscribe(principal, reportId, afterEventId, () => {})
    .then((subscription) => {
      const replay = subscription.replay
      subscription.close()
      return replay
    })
}

test('报告阶段有序、完成前不暴露草稿，幂等请求复用同一运行', async () => {
  const reportGenerator = {
    async generateReport(input: ReportGenerationInput) {
      input.onPhase('generating_report')
      input.onPhase('validating')
      return result(true)
    },
  }
  const manager = new CountryOutageSessionManager({
    reportGenerator,
    questionService: {
      async answer() {
        throw new Error('本测试不应追问')
      },
    },
    authorize: () => true,
    timersEnabled: false,
  })

  const created = await manager.createReport(PRINCIPAL, request())
  const duplicate = await manager.createReport(PRINCIPAL, request())
  assert.equal(duplicate.report_id, created.report_id)
  assert.equal(duplicate.run_id, created.run_id)
  assert.equal(duplicate.deduplicated, true)
  await settle()

  const events = await replayEvents(manager, created.report_id)
  assert.deepEqual(
    events
      .filter((event) => event.event_type === 'report_state')
      .map((event) => event.phase),
    [
      'queued',
      'reading_data',
      'generating_report',
      'validating',
      'completed',
    ],
  )
  for (const event of events.slice(0, -1)) {
    if (event.event_type === 'report_state') {
      assert.equal(event.report, undefined)
      assert.equal(event.artifacts, undefined)
    }
  }
  const completed = events.at(-1)
  assert.equal(completed?.event_type, 'report_state')
  if (completed?.event_type !== 'report_state') {
    assert.fail('最后事件应为报告完成')
  }
  assert.equal(completed.report?.artifactId, 'report_server_test')
  assert.equal(completed.snapshot?.collectorId, 'rrc25')
  assert.deepEqual(
    completed.artifacts?.map((item) => [item.format, item.status]),
    [
      ['markdown', 'ready'],
      ['pdf', 'failed'],
    ],
  )
  const markdown = await manager.getArtifact(
    PRINCIPAL,
    created.report_id,
    'markdown',
  )
  assert.match(markdown.content.toString(), /伊朗报告/)
  await assert.rejects(
    manager.getArtifact(PRINCIPAL, created.report_id, 'pdf'),
    (error: unknown) =>
      error instanceof CountryOutageHttpError &&
      error.status === 409 &&
      error.code === 'PdfRenderError',
  )
})

test('追问固定复用原报告事实合同，重复问题不会生成第二份回答', async () => {
  let answerCalls = 0
  let receivedPayload: unknown
  const questionService: CountryOutageQuestionService = {
    async answer(input) {
      answerCalls += 1
      receivedPayload = input.questionContext.payload
      assert.equal(input.report.snapshot.publicationId, PUBLICATION)
      assert.equal(input.evidenceMode, 'domeye_only')
      return {
        kind: 'metric_semantics',
        text: '该数字表示 Prefix×VP 观测关系，不代表用户数量。',
        evidenceRefs: [
          'series:/series/0/visible_prefix_vp_count',
        ],
        evidenceRecords: [
          {
            evidenceRef:
              'series:/series/0/visible_prefix_vp_count',
            source: 'series',
            label: '起点可见 Prefix×VP',
            metric: 'visible_prefix_vp_count',
            value: '367215',
            observedAtUtc: '2026-02-28T10:05:00Z',
            observedAtLocal: '2026-02-28T18:05:00+08:00',
            statisticalScope: 'RRC25 固定 Prefix×VP 统计范围',
          },
        ],
        missingEvidence: ['用户与业务测量'],
        limitations: ['不能据此判断用户影响'],
      }
    },
  }
  const manager = new CountryOutageSessionManager({
    reportService: { async generate() { return result() } },
    questionService,
    authorize: () => true,
    timersEnabled: false,
  })
  const created = await manager.createReport(PRINCIPAL, request())
  await settle()
  const first = await manager.createQuestion(
    PRINCIPAL,
    created.report_id,
    {
      question: '这个数字代表用户数量吗？',
      evidence_mode: 'domeye_only',
      quote: {
        kind: 'section_paragraph',
        section_id: 'scope',
        paragraph_index: 0,
        evidence_refs: [
          'overview:/observation_scope',
        ],
      },
      idempotency_key: 'question-request-0001',
    },
  )
  const duplicate = await manager.createQuestion(
    PRINCIPAL,
    created.report_id,
    {
      question: '这个数字代表用户数量吗？',
      evidence_mode: 'domeye_only',
      quote: {
        kind: 'section_paragraph',
        section_id: 'scope',
        paragraph_index: 0,
        evidence_refs: [
          'overview:/observation_scope',
        ],
      },
      idempotency_key: 'question-request-0002',
    },
  )
  assert.equal(duplicate.question_id, first.question_id)
  assert.equal(duplicate.deduplicated, true)
  await settle()
  assert.equal(answerCalls, 1)
  assert.deepEqual(receivedPayload, { frozen: true })

  const events = await replayEvents(manager, created.report_id)
  const questionEvents = events.filter(
    (event) => event.event_type === 'question_state',
  )
  assert.deepEqual(
    questionEvents.map((event) => event.phase),
    ['answering', 'completed'],
  )
  const completed = questionEvents.at(-1)
  if (completed?.event_type !== 'question_state') {
    assert.fail('缺少正式回答事件')
  }
  assert.match(completed.question.answer?.text ?? '', /不代表用户数量/)
  assert.deepEqual(completed.question.answer?.evidence_refs, [
    'series:/series/0/visible_prefix_vp_count',
  ])
  assert.equal(
    completed.question.answer?.evidence_records[0]?.statistical_scope,
    'RRC25 固定 Prefix×VP 统计范围',
  )
  assert.equal(
    completed.question.answer?.snapshot.publicationId,
    PUBLICATION,
  )
  await assert.rejects(
    manager.createQuestion(PRINCIPAL, created.report_id, {
      question: '允许联网搜索吗？',
      evidence_mode: 'external' as 'domeye_only',
      idempotency_key: 'question-request-0003',
    }),
    (error: unknown) =>
      error instanceof CountryOutageHttpError &&
      error.code === 'core_evidence_mode_not_allowed',
  )
})

test('失败追问保留原记录和原幂等结果，新幂等键可重新回答', async () => {
  let answerCalls = 0
  const manager = new CountryOutageSessionManager({
    reportService: { async generate() { return result() } },
    questionService: {
      async answer() {
        answerCalls += 1
        if (answerCalls === 1) {
          throw new Error('第一次回答失败')
        }
        return validQuestionAnswer('第二次回答成功。')
      },
    },
    authorize: () => true,
    timersEnabled: false,
  })
  const created = await manager.createReport(
    PRINCIPAL,
    request('failed-question-report-0001'),
  )
  await settle()
  const questionRequest = {
    question: '当前证据能否证明用户断网？',
    evidence_mode: 'domeye_only' as const,
  }
  const failed = await manager.createQuestion(
    PRINCIPAL,
    created.report_id,
    {
      ...questionRequest,
      idempotency_key: 'failed-question-0001',
    },
  )
  await settle()

  const sameIdempotency = await manager.createQuestion(
    PRINCIPAL,
    created.report_id,
    {
      ...questionRequest,
      idempotency_key: 'failed-question-0001',
    },
  )
  assert.equal(sameIdempotency.question_id, failed.question_id)
  assert.equal(sameIdempotency.state, 'failed')
  assert.equal(sameIdempotency.deduplicated, true)

  const retried = await manager.createQuestion(
    PRINCIPAL,
    created.report_id,
    {
      ...questionRequest,
      idempotency_key: 'failed-question-0002',
    },
  )
  assert.notEqual(retried.question_id, failed.question_id)
  assert.equal(retried.number, 2)
  assert.equal(retried.deduplicated, false)
  await settle()
  assert.equal(answerCalls, 2)

  const events = await replayEvents(manager, created.report_id)
  const terminalQuestions = events
    .filter((event) => event.event_type === 'question_state')
    .filter(
      (event) =>
        event.state === 'failed' || event.state === 'completed',
    )
  assert.deepEqual(
    terminalQuestions.map((event) => [
      event.question.question_id,
      event.state,
    ]),
    [
      [failed.question_id, 'failed'],
      [retried.question_id, 'completed'],
    ],
  )
})

test('取消追问保留原记录和原幂等结果，新幂等键可重新回答', async () => {
  let answerCalls = 0
  let firstAnswerStarted!: () => void
  const firstAnswerStart = new Promise<void>((resolve) => {
    firstAnswerStarted = resolve
  })
  const manager = new CountryOutageSessionManager({
    reportService: { async generate() { return result() } },
    questionService: {
      async answer(input) {
        answerCalls += 1
        if (answerCalls === 1) {
          firstAnswerStarted()
          if (!input.signal.aborted) {
            await new Promise<void>((resolve) => {
              input.signal.addEventListener('abort', () => resolve(), {
                once: true,
              })
            })
          }
          input.signal.throwIfAborted()
        }
        return validQuestionAnswer('取消后的新运行回答成功。')
      },
    },
    authorize: () => true,
    timersEnabled: false,
  })
  const created = await manager.createReport(
    PRINCIPAL,
    request('cancelled-question-report-0001'),
  )
  await settle()
  const questionRequest = {
    question: '窗口结束时是否已经完全恢复？',
    evidence_mode: 'domeye_only' as const,
  }
  const cancelled = await manager.createQuestion(
    PRINCIPAL,
    created.report_id,
    {
      ...questionRequest,
      idempotency_key: 'cancelled-question-0001',
    },
  )
  await firstAnswerStart
  const abort = await manager.abortRun(PRINCIPAL, cancelled.run_id)
  assert.equal(abort.abort_effective, true)
  assert.equal(abort.state, 'cancelled')
  await settle()

  const sameIdempotency = await manager.createQuestion(
    PRINCIPAL,
    created.report_id,
    {
      ...questionRequest,
      idempotency_key: 'cancelled-question-0001',
    },
  )
  assert.equal(sameIdempotency.question_id, cancelled.question_id)
  assert.equal(sameIdempotency.state, 'cancelled')
  assert.equal(sameIdempotency.deduplicated, true)

  const retried = await manager.createQuestion(
    PRINCIPAL,
    created.report_id,
    {
      ...questionRequest,
      idempotency_key: 'cancelled-question-0002',
    },
  )
  assert.notEqual(retried.question_id, cancelled.question_id)
  assert.equal(retried.number, 2)
  await settle()
  assert.equal(answerCalls, 2)

  const events = await replayEvents(manager, created.report_id)
  const terminalQuestions = events
    .filter((event) => event.event_type === 'question_state')
    .filter(
      (event) =>
        event.state === 'cancelled' || event.state === 'completed',
    )
  assert.deepEqual(
    terminalQuestions.map((event) => [
      event.question.question_id,
      event.state,
    ]),
    [
      [cancelled.question_id, 'cancelled'],
      [retried.question_id, 'completed'],
    ],
  )
})

test('全局并发、单用户运行和队列上限具有确定结果，取消不会迟到发布', async () => {
  const pending = new Map<
    string,
    {
      input: ReportGenerationInput
      resolve(value: ReportGenerationResult): void
    }
  >()
  const reportService: CountryOutageReportService = {
    generate(input) {
      return new Promise((resolve) => {
        pending.set(input.publicationId, { input, resolve })
      })
    },
  }
  const manager = new CountryOutageSessionManager({
    reportService,
    questionService: {
      async answer() {
        throw new Error('本测试不应追问')
      },
    },
    authorize: () => true,
    limits: {
      maximumActiveReportRunsGlobal: 1,
      maximumQueueDepth: 1,
    },
    timersEnabled: false,
  })
  const userB = { userId: 'user-b', authorizationScope: 'scope-ir-readers' }
  const userC = { userId: 'user-c', authorizationScope: 'scope-ir-readers' }
  const first = await manager.createReport(PRINCIPAL, request('report-run-a001'))
  assert.equal(first.phase, 'reading_data')
  const queued = await manager.createReport(userB, {
    ...request('report-run-b001'),
    publication_id: 'publication-queued',
  })
  assert.equal(queued.phase, 'queued')
  await assert.rejects(
    manager.createReport(PRINCIPAL, {
      ...request('report-run-a002'),
      publication_id: 'publication-other',
    }),
    (error: unknown) =>
      error instanceof CountryOutageHttpError &&
      error.code === 'active_report_run_exists',
  )
  await assert.rejects(
    manager.createReport(userC, {
      ...request('report-run-c001'),
      publication_id: 'publication-full',
    }),
    (error: unknown) =>
      error instanceof CountryOutageHttpError &&
      error.code === 'report_queue_full',
  )

  const aborted = await manager.abortRun(PRINCIPAL, first.run_id)
  assert.equal(aborted.abort_effective, true)
  assert.equal(aborted.state, 'cancelled')
  assert.equal(pending.get(PUBLICATION)?.input.signal.aborted, true)
  await settle()
  assert.ok(pending.has('publication-queued'))

  pending.get(PUBLICATION)?.resolve(result())
  await settle()
  const firstEvents = await replayEvents(manager, first.report_id)
  assert.equal(
    firstEvents.some(
      (event) =>
        event.event_type === 'report_state' &&
        event.phase === 'completed',
    ),
    false,
  )
})

test('SSE 可按事件号重放，会话在五分钟前提醒并于三十分钟清理', async () => {
  let now = Date.parse('2026-07-28T10:00:00.000Z')
  const liveEvents: CountryOutageAgentEvent[] = []
  const manager = new CountryOutageSessionManager({
    reportService: { async generate() { return result() } },
    questionService: {
      async answer() {
        throw new Error('本测试不应追问')
      },
    },
    authorize: () => true,
    now: () => now,
    timersEnabled: false,
  })
  const created = await manager.createReport(PRINCIPAL, request())
  await settle()
  const startedDownload = await manager.getArtifact(
    PRINCIPAL,
    created.report_id,
    'markdown',
  )
  assert.equal(
    startedDownload.downloadDeadlineAtMs,
    now + 32 * 60 * 1000,
  )
  const initial = await replayEvents(manager, created.report_id)
  const lastSeen = initial[1]!.event_id
  const subscription = await manager.subscribe(
    PRINCIPAL,
    created.report_id,
    lastSeen,
    (event) => liveEvents.push(event),
  )
  assert.deepEqual(
    subscription.replay.map((event) => event.event_id),
    initial
      .filter((event) => event.event_id > lastSeen)
      .map((event) => event.event_id),
  )
  subscription.activate()

  now += 25 * 60 * 1000
  manager.sweep()
  assert.equal(
    liveEvents.some(
      (event) =>
        event.event_type === 'session_notice' &&
        event.phase === 'session_expiring',
    ),
    true,
  )
  now += 5 * 60 * 1000
  manager.sweep()
  assert.equal(
    liveEvents.at(-1)?.event_type,
    'session_notice',
  )
  assert.equal(
    liveEvents.at(-1)?.state,
    'expired',
  )
  await assert.rejects(
    manager.getArtifact(PRINCIPAL, created.report_id, 'markdown'),
    (error: unknown) =>
      error instanceof CountryOutageHttpError &&
      error.status === 410 &&
      error.code === 'session_expired',
  )
  subscription.close()

  now += 5 * 60 * 1000
  manager.sweep()
  await assert.rejects(
    manager.getArtifact(PRINCIPAL, created.report_id, 'markdown'),
    (error: unknown) =>
      error instanceof CountryOutageHttpError &&
      error.status === 404 &&
      error.code === 'report_not_found',
  )

  const recreated = await manager.createReport(PRINCIPAL, request())
  assert.notEqual(recreated.report_id, created.report_id)
  assert.equal(recreated.deduplicated, false)
})

test('到期墓碑只保留身份语义，所有旧入口先一致返回 410，删除后返回 404 并允许同键重建', async () => {
  let now = Date.parse('2026-07-28T10:00:00.000Z')
  const liveEvents: CountryOutageAgentEvent[] = []
  const manager = new CountryOutageSessionManager({
    reportService: { async generate() { return result() } },
    questionService: {
      async answer() {
        return validQuestionAnswer()
      },
    },
    authorize: () => true,
    now: () => now,
    timersEnabled: false,
  })
  const reportRequest = request('tombstone-report-key-0001')
  const created = await manager.createReport(PRINCIPAL, reportRequest)
  await settle()
  const questionRequest = {
    question: '当前控制面证据能说明什么？',
    evidence_mode: 'domeye_only' as const,
    idempotency_key: 'tombstone-question-key-0001',
  }
  const question = await manager.createQuestion(
    PRINCIPAL,
    created.report_id,
    questionRequest,
  )
  await settle()
  const subscription = await manager.subscribe(
    PRINCIPAL,
    created.report_id,
    0,
    (event) => liveEvents.push(event),
  )
  subscription.activate()

  const assertRejected = async (
    operation: Promise<unknown>,
    status: number,
    code: string,
  ): Promise<void> => {
    await assert.rejects(
      operation,
      (error: unknown) =>
        error instanceof CountryOutageHttpError &&
        error.status === status &&
        error.code === code,
    )
  }
  const tombstoneQuestionRequest = {
    question: '到期后还能继续追问吗？',
    evidence_mode: 'domeye_only' as const,
    idempotency_key: 'tombstone-question-probe-0001',
  }

  now += 30 * 60 * 1000
  manager.sweep()
  const expiryEvent = liveEvents.at(-1)
  assert.equal(expiryEvent?.event_type, 'session_notice')
  assert.equal(expiryEvent?.phase, 'session_expired')
  assert.equal('report' in (expiryEvent ?? {}), false)
  assert.equal('question' in (expiryEvent ?? {}), false)

  await assertRejected(
    manager.createQuestion(
      PRINCIPAL,
      created.report_id,
      tombstoneQuestionRequest,
    ),
    410,
    'session_expired',
  )
  await assertRejected(
    manager.subscribe(
      PRINCIPAL,
      created.report_id,
      0,
      () => {},
    ),
    410,
    'session_expired',
  )
  await assertRejected(
    manager.abortRun(PRINCIPAL, created.run_id),
    410,
    'session_expired',
  )
  await assertRejected(
    manager.abortRun(PRINCIPAL, question.run_id),
    410,
    'session_expired',
  )
  await assertRejected(
    manager.getArtifact(PRINCIPAL, created.report_id, 'markdown'),
    410,
    'session_expired',
  )
  await assertRejected(
    manager.createReport(PRINCIPAL, reportRequest),
    410,
    'session_expired',
  )
  subscription.close()

  now += 5 * 60 * 1000
  manager.sweep()
  await assertRejected(
    manager.createQuestion(
      PRINCIPAL,
      created.report_id,
      tombstoneQuestionRequest,
    ),
    404,
    'report_not_found',
  )
  await assertRejected(
    manager.subscribe(
      PRINCIPAL,
      created.report_id,
      0,
      () => {},
    ),
    404,
    'report_not_found',
  )
  await assertRejected(
    manager.abortRun(PRINCIPAL, created.run_id),
    404,
    'run_not_found',
  )
  await assertRejected(
    manager.abortRun(PRINCIPAL, question.run_id),
    404,
    'run_not_found',
  )
  await assertRejected(
    manager.getArtifact(PRINCIPAL, created.report_id, 'markdown'),
    404,
    'report_not_found',
  )
  const recreated = await manager.createReport(PRINCIPAL, reportRequest)
  assert.notEqual(recreated.report_id, created.report_id)
  assert.notEqual(recreated.run_id, created.run_id)
  assert.equal(recreated.deduplicated, false)
})

test('回答并发和每分钟六问按用户计算，不能通过多个报告绕过', async () => {
  let releaseFirstAnswer: (() => void) | undefined
  let answerCalls = 0
  const questionService: CountryOutageQuestionService = {
    async answer(input) {
      answerCalls += 1
      if (answerCalls === 1) {
        await new Promise<void>((resolve) => {
          releaseFirstAnswer = resolve
          input.signal.addEventListener('abort', () => resolve(), {
            once: true,
          })
        })
        input.signal.throwIfAborted()
      }
      return {
        kind: 'evidence_boundary',
        text: '当前证据只能说明 RRC25 BGP 控制面。',
        evidenceRefs: ['overview:/observation_scope'],
        evidenceRecords: [
          {
            evidenceRef: 'overview:/observation_scope',
            source: 'overview',
            label: '观测范围',
            metric: null,
            value: 'RRC25',
            observedAtUtc: null,
            observedAtLocal: null,
            statisticalScope: 'RRC25 固定统计范围',
          },
        ],
        missingEvidence: [],
        limitations: [],
      }
    },
  }
  const manager = new CountryOutageSessionManager({
    reportService: { async generate() { return result() } },
    questionService,
    authorize: () => true,
    limits: {
      maximumReportRunsPerUserPerHour: 10,
    },
    timersEnabled: false,
  })
  const firstReport = await manager.createReport(
    PRINCIPAL,
    request('cross-report-0001'),
  )
  await settle()
  const secondReport = await manager.createReport(
    PRINCIPAL,
    request('cross-report-0002'),
  )
  await settle()
  const active = await manager.createQuestion(
    PRINCIPAL,
    firstReport.report_id,
    {
      question: '第一问',
      evidence_mode: 'domeye_only',
      idempotency_key: 'cross-question-0001',
    },
  )
  await assert.rejects(
    manager.createQuestion(PRINCIPAL, secondReport.report_id, {
      question: '第二份报告上的并发问题',
      evidence_mode: 'domeye_only',
      idempotency_key: 'cross-question-0002',
    }),
    (error: unknown) =>
      error instanceof CountryOutageHttpError &&
      error.code === 'answer_run_active',
  )
  await manager.abortRun(PRINCIPAL, active.run_id)
  releaseFirstAnswer?.()
  await settle()

  for (let index = 1; index < 6; index += 1) {
    await manager.createQuestion(PRINCIPAL, secondReport.report_id, {
      question: `限频问题 ${index}`,
      evidence_mode: 'domeye_only',
      idempotency_key: `rate-question-000${index}`,
    })
    await settle()
  }
  assert.equal(answerCalls, 6)
  await assert.rejects(
    manager.createQuestion(PRINCIPAL, secondReport.report_id, {
      question: '第七个一分钟内问题',
      evidence_mode: 'domeye_only',
      idempotency_key: 'rate-question-0007',
    }),
    (error: unknown) =>
      error instanceof CountryOutageHttpError &&
      error.code === 'question_rate_limited',
  )
})

test('无冒号 fact_ 派生事实引用可由冻结 allowlist 安全发布', async () => {
  const factRef = `fact_${'1'.repeat(24)}`
  const base = result()
  base.questionContext!.evidenceRefs = [
    ...base.questionContext!.evidenceRefs,
    factRef,
  ]
  const manager = new CountryOutageSessionManager({
    reportService: { async generate() { return base } },
    questionService: {
      async answer() {
        return {
          kind: 'fact',
          text: '起点到最低点减少 50,482 条。',
          evidenceRefs: [factRef],
          evidenceRecords: [
            {
              evidenceRef: factRef,
              source: 'derived_fact',
              label: '起点至最低点变化',
              metric: 'start_to_lowest_change',
              value: '-50482',
              observedAtUtc: null,
              observedAtLocal: null,
              statisticalScope: 'RRC25 固定 Prefix×VP 统计范围',
            },
          ],
          missingEvidence: [],
          limitations: [],
        }
      },
    },
    authorize: () => true,
    timersEnabled: false,
  })
  const created = await manager.createReport(
    PRINCIPAL,
    request('fact-reference-0001'),
  )
  await settle()
  await manager.createQuestion(PRINCIPAL, created.report_id, {
    question: '起点到最低点减少多少？',
    evidence_mode: 'domeye_only',
    idempotency_key: 'fact-question-0001',
  })
  await settle()
  const events = await replayEvents(manager, created.report_id)
  const completed = events.find(
    (event) =>
      event.event_type === 'question_state' &&
      event.phase === 'completed',
  )
  if (completed?.event_type !== 'question_state') {
    assert.fail('fact_ 引用回答应通过校验')
  }
  assert.deepEqual(completed.question.answer?.evidence_refs, [factRef])
})

test('生成结果必须与固定 publication/revision/RRC25 一致且跨用户不可见', async () => {
  const conflicting = result()
  conflicting.document.snapshot.publicationId = 'publication-switched'
  const manager = new CountryOutageSessionManager({
    reportService: { async generate() { return conflicting } },
    questionService: {
      async answer() {
        throw new Error('本测试不应追问')
      },
    },
    authorize: (_principal, reference) => reference === REFERENCE,
    timersEnabled: false,
  })
  const created = await manager.createReport(PRINCIPAL, request())
  await settle()
  const events = await replayEvents(manager, created.report_id)
  const failed = events.at(-1)
  assert.equal(failed?.state, 'failed')
  if (failed?.event_type !== 'report_state') {
    assert.fail('应发布受控失败事件')
  }
  assert.equal(failed.error?.code, 'snapshot_identity_conflict')
  assert.equal(failed.report, undefined)
  await assert.rejects(
    replayEvents(
      manager,
      created.report_id,
      { userId: 'other-user', authorizationScope: 'scope-ir-readers' },
    ),
    (error: unknown) =>
      error instanceof CountryOutageHttpError &&
      error.status === 404,
  )
})

test('基础报告缓存按 authorizationScope、revision 和完整报告服务身份分区', async () => {
  let generationCalls = 0
  const manager = new CountryOutageSessionManager({
    reportService: {
      async generate(input) {
        generationCalls += 1
        return resultFor(input)
      },
    },
    questionService: {
      async answer() {
        throw new Error('本测试不应追问')
      },
    },
    baseReportCache: {
      reportServiceIdentity: REPORT_SERVICE_IDENTITY,
    },
    authorize: () => true,
    timersEnabled: false,
  })
  const sameScopeB = {
    userId: 'cache-partition-b',
    authorizationScope: PRINCIPAL.authorizationScope,
  }
  const otherScope = {
    userId: 'cache-partition-other-scope',
    authorizationScope: 'scope-ir-auditors',
  }
  const revisionTwo = {
    userId: 'cache-partition-revision-two',
    authorizationScope: PRINCIPAL.authorizationScope,
  }

  await manager.createReport(
    PRINCIPAL,
    request('cache-partition-first'),
  )
  await settle()
  await manager.createReport(otherScope, request('cache-partition-scope'))
  await settle()
  await manager.createReport(revisionTwo, {
    ...request('cache-partition-revision'),
    revision: 2,
  })
  await settle()
  await manager.createReport(
    sameScopeB,
    request('cache-partition-hit'),
  )
  await settle()
  assert.equal(generationCalls, 3)

  const sharedStore = new CountryOutageBaseReportCache()
  assert.equal(
    sharedStore.ttlMs,
    DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_TTL_MS,
  )
  const changedModel: CountryOutageReportServiceIdentity = {
    ...REPORT_SERVICE_IDENTITY,
    model: {
      ...REPORT_SERVICE_IDENTITY.model,
      model: 'test-v2',
      modelVersion: '2',
    },
  }
  const changedValidator: CountryOutageReportServiceIdentity = {
    ...REPORT_SERVICE_IDENTITY,
    validatorRulesVersion:
      'country_outage_report_validator_rules_v5-test-drift',
  }
  const changedSkill: CountryOutageReportServiceIdentity = {
    ...REPORT_SERVICE_IDENTITY,
    skillBundleSha256: 'e'.repeat(64),
  }

  const createManager = (
    identity: CountryOutageReportServiceIdentity,
    onGenerate: () => void,
  ) =>
    new CountryOutageSessionManager({
      reportService: {
        async generate(input) {
          onGenerate()
          return resultFor(input, identity)
        },
      },
      questionService: {
        async answer() {
          throw new Error('本测试不应追问')
        },
      },
      baseReportCache: {
        reportServiceIdentity: identity,
        store: sharedStore,
      },
      authorize: () => true,
      timersEnabled: false,
    })

  let baseCalls = 0
  const baseManager = createManager(
    REPORT_SERVICE_IDENTITY,
    () => { baseCalls += 1 },
  )
  await baseManager.createReport(
    PRINCIPAL,
    request('cache-identity-base'),
  )
  await settle()

  for (const [index, identity] of [
    changedModel,
    changedValidator,
    changedSkill,
  ].entries()) {
    let changedCalls = 0
    const changedManager = createManager(
      identity,
      () => { changedCalls += 1 },
    )
    await changedManager.createReport(
      {
        userId: `cache-identity-changed-${index}`,
        authorizationScope: PRINCIPAL.authorizationScope,
      },
      request(`cache-identity-change-${index}`),
    )
    await settle()
    assert.equal(changedCalls, 1)
  }

  let sameIdentityCalls = 0
  const sameIdentityManager = createManager(
    REPORT_SERVICE_IDENTITY,
    () => { sameIdentityCalls += 1 },
  )
  await sameIdentityManager.createReport(
    {
      userId: 'cache-identity-same',
      authorizationScope: PRINCIPAL.authorizationScope,
    },
    request('cache-identity-same'),
  )
  await settle()
  assert.equal(baseCalls, 1)
  assert.equal(sameIdentityCalls, 0)
})

test('基础报告缓存默认限制条目和总字节，超限时淘汰旧项或跳过缓存', () => {
  const defaults = new CountryOutageBaseReportCache()
  assert.equal(
    defaults.maxEntries,
    DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_MAX_ENTRIES,
  )
  assert.equal(
    defaults.maxTotalBytes,
    DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_MAX_TOTAL_BYTES,
  )

  let now = 1_000
  const bounded = new CountryOutageBaseReportCache({
    maxEntries: 1,
    maxTotalBytes: 1024 * 1024,
    now: () => now,
  })
  const firstKey = cacheKey('scope-cache-first')
  const secondKey = cacheKey('scope-cache-second')
  assert.equal(
    bounded.set(
      firstKey,
      resultFor({
        eventReference: REFERENCE,
        publicationId: PUBLICATION,
        revision: 1,
      }),
    ),
    true,
  )
  now += 1
  assert.equal(
    bounded.set(
      secondKey,
      resultFor({
        eventReference: REFERENCE,
        publicationId: PUBLICATION,
        revision: 1,
      }),
    ),
    true,
  )
  assert.equal(bounded.get(firstKey), undefined)
  assert.ok(bounded.get(secondKey))

  const tooSmall = new CountryOutageBaseReportCache({
    maxTotalBytes: 1,
  })
  assert.equal(
    tooSmall.set(
      cacheKey('scope-cache-oversized'),
      resultFor({
        eventReference: REFERENCE,
        publicationId: PUBLICATION,
        revision: 1,
      }),
    ),
    false,
  )
  assert.equal(
    tooSmall.get(cacheKey('scope-cache-oversized')),
    undefined,
  )
})

test('基础报告缓存按 TTL 到期，且未通过校验的结果永不写入', async () => {
  let now = Date.parse('2026-07-28T15:00:00.000Z')
  let generationCalls = 0
  let returnValid = true
  const manager = new CountryOutageSessionManager({
    reportService: {
      async generate(input) {
        generationCalls += 1
        const generated = resultFor(input)
        generated.document.validation.passed = returnValid
        return generated
      },
    },
    questionService: {
      async answer() {
        throw new Error('本测试不应追问')
      },
    },
    baseReportCache: {
      reportServiceIdentity: REPORT_SERVICE_IDENTITY,
      ttlMs: 1_000,
    },
    authorize: () => true,
    now: () => now,
    timersEnabled: false,
  })

  await manager.createReport(
    PRINCIPAL,
    request('cache-ttl-first'),
  )
  await settle()
  now += 999
  await manager.createReport(
    {
      userId: 'cache-ttl-hit',
      authorizationScope: PRINCIPAL.authorizationScope,
    },
    request('cache-ttl-hit'),
  )
  await settle()
  assert.equal(generationCalls, 1)

  now += 1
  await manager.createReport(
    {
      userId: 'cache-ttl-expired',
      authorizationScope: PRINCIPAL.authorizationScope,
    },
    request('cache-ttl-expired'),
  )
  await settle()
  assert.equal(generationCalls, 2)

  returnValid = false
  now += 1_000
  const invalidA = await manager.createReport(
    {
      userId: 'cache-invalid-a',
      authorizationScope: PRINCIPAL.authorizationScope,
    },
    request('cache-invalid-a'),
  )
  await settle()
  const invalidB = await manager.createReport(
    {
      userId: 'cache-invalid-b',
      authorizationScope: PRINCIPAL.authorizationScope,
    },
    request('cache-invalid-b'),
  )
  await settle()
  assert.equal(generationCalls, 4)
  const invalidAEvents = await replayEvents(
    manager,
    invalidA.report_id,
    {
      userId: 'cache-invalid-a',
      authorizationScope: PRINCIPAL.authorizationScope,
    },
  )
  const invalidBEvents = await replayEvents(
    manager,
    invalidB.report_id,
    {
      userId: 'cache-invalid-b',
      authorizationScope: PRINCIPAL.authorizationScope,
    },
  )
  assert.equal(invalidAEvents.at(-1)?.state, 'failed')
  assert.equal(invalidBEvents.at(-1)?.state, 'failed')
})

test('独立 Markdown 附录校验并展示 factSet/cohort，拒绝任一身份或结构化事实绑定漂移', () => {
  const report = document()
  const frozenBinding: ExternalEvidenceFrozenBinding = {
    incidentId: report.event.incident_id,
    publicationId: report.snapshot.publicationId,
    revision: report.snapshot.revision,
    dataThrough: report.snapshot.dataThrough,
    factSetId: report.factSetId,
    cohortId: report.snapshot.cohortId,
    countryCode: report.event.country_code,
    collectorId: report.snapshot.collectorId,
    windowStartUtc: report.snapshot.windowStartUtc,
    windowEndUtc: report.snapshot.windowEndUtc,
  }
  const bindingId = externalEvidenceFrozenBindingId(frozenBinding)
  const appendix: ExternalEvidenceAppendix = {
    schemaVersion: 'country_outage_external_appendix_v1',
    classificationPolicyVersion:
      'country_outage_external_source_classification_policy_v1',
    status: 'completed',
    comparisonStatus: 'insufficient',
    frozenBinding,
    query: '外部结构化事实是否一致？',
    requestedAt: '2026-07-28T15:00:01.000Z',
    retrievedAt: '2026-07-28T15:00:02.000Z',
    claims: [],
    sources: [
      {
        sourceId: 'source-artifact-binding',
        title: '外部绑定测试',
        publisher: 'Cloudflare Radar',
        url: 'https://radar.cloudflare.com/notice',
        publishedAt: null,
        retrievedAt: '2026-07-28T15:00:02.000Z',
        sourceClassification: 'measurement_platform',
        sourceTier: 'direct',
        readStatus: 'readable',
        readStatusDetail: null,
        summary: '只验证独立附录冻结身份。',
        evidenceStatus: 'available',
        evidenceStatusDetail: '结构化事实已绑定',
        structuredFacts: [
          {
            factId: 'external_fact_artifact_binding',
            bindingId,
            metric: 'bgp_control_plane_visibility_state',
            addressFamily: 'all',
            observedWindowStartUtc: frozenBinding.windowStartUtc,
            observedWindowEndUtc: frozenBinding.windowEndUtc,
            sourceValue: 'degraded',
            normalizedValue: 'degraded',
          },
        ],
      },
    ],
  }
  const input = {
    document: report,
    questionId: 'question-artifact-binding',
    questionNumber: 1,
    question: appendix.query,
    appendix,
  }
  const artifact = buildExternalAppendixMarkdownArtifact(input)
  const markdown = artifact.content.toString('utf8')
  const escapedBindingId = bindingId.replaceAll('_', String.raw`\_`)
  assert.match(markdown, /fact_set_id：facts\\_server\\_test/)
  assert.match(markdown, /cohort_id：cohort\\-ir\\-r1/)
  assert.ok(
    markdown.includes(`frozen_binding_id：${escapedBindingId}`),
  )
  assert.ok(markdown.includes(`绑定 ${escapedBindingId}`))

  const driftCases: Array<{
    label: string
    mutate: (value: ExternalEvidenceAppendix) => void
  }> = [
    {
      label: 'factSet drift',
      mutate(value) {
        value.frozenBinding!.factSetId = 'facts_server_other'
      },
    },
    {
      label: 'cohort drift',
      mutate(value) {
        value.frozenBinding!.cohortId = 'cohort-server-other'
      },
    },
    {
      label: 'missing factSet',
      mutate(value) {
        delete (
          value.frozenBinding as Partial<ExternalEvidenceFrozenBinding>
        ).factSetId
      },
    },
    {
      label: 'missing cohort',
      mutate(value) {
        delete (
          value.frozenBinding as Partial<ExternalEvidenceFrozenBinding>
        ).cohortId
      },
    },
    {
      label: 'structured fact binding drift',
      mutate(value) {
        value.sources[0]!.structuredFacts![0]!.bindingId =
          'external_binding_other'
      },
    },
  ]
  for (const driftCase of driftCases) {
    const drifted = structuredClone(appendix)
    driftCase.mutate(drifted)
    assert.throws(
      () =>
        buildExternalAppendixMarkdownArtifact({
          ...input,
          appendix: drifted,
        }),
      (error: unknown) =>
        error instanceof ExternalAppendixArtifactError &&
        error.code === 'external_appendix_binding_conflict',
      driftCase.label,
    )
  }
})

test('问题指纹对 quote evidenceRefs 使用确定性排序且不改写首次业务数组顺序', async () => {
  const generated = result()
  const evidenceRefs = [
    'series:/series/0/visible_prefix_vp_count',
    'overview:/observation_scope',
  ]
  generated.document.draft.summary.evidenceRefs = [...evidenceRefs]
  let answerCalls = 0
  const manager = new CountryOutageSessionManager({
    reportService: { async generate() { return generated } },
    questionService: {
      async answer() {
        answerCalls += 1
        return validQuestionAnswer()
      },
    },
    authorize: () => true,
    timersEnabled: false,
  })
  const created = await manager.createReport(
    PRINCIPAL,
    request('deterministic-fingerprint-report'),
  )
  await settle()
  const first = await manager.createQuestion(
    PRINCIPAL,
    created.report_id,
    {
      question: '引用顺序不应改变同一问题身份',
      evidence_mode: 'domeye_only',
      quote: { kind: 'summary', evidence_refs: evidenceRefs },
      idempotency_key: 'deterministic-fingerprint-question-a',
    },
  )
  await settle()
  const duplicate = await manager.createQuestion(
    PRINCIPAL,
    created.report_id,
    {
      question: '引用顺序不应改变同一问题身份',
      evidence_mode: 'domeye_only',
      quote: {
        kind: 'summary',
        evidence_refs: [...evidenceRefs].reverse(),
      },
      idempotency_key: 'deterministic-fingerprint-question-b',
    },
  )

  assert.equal(duplicate.question_id, first.question_id)
  assert.equal(duplicate.deduplicated, true)
  assert.equal(answerCalls, 1)
  const events = await replayEvents(manager, created.report_id)
  const completed = events
    .filter((event) => event.event_type === 'question_state')
    .at(-1)!
  assert.deepEqual(
    completed.question.quote?.evidence_refs,
    evidenceRefs,
  )
})
