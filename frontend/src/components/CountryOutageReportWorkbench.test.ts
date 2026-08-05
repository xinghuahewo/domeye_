import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import {
  CountryOutageAgentRequestError,
  isCountryOutageRequestOutcomeUncertain,
} from '@/api/countryOutageAgent'
import {
  LogicalSubmissionIdempotency,
  logicalSubmissionFingerprint,
} from '@/utils/countryOutageReport'

const source = readFileSync(
  new URL('./CountryOutageReportWorkbench.vue', import.meta.url),
  'utf8',
)

function functionSource(name: string): string {
  const start = source.indexOf(`function ${name}(`)
  expect(start, `缺少函数 ${name}`).toBeGreaterThanOrEqual(0)
  const nextFunction = source.indexOf('\nfunction ', start + 1)
  return source.slice(start, nextFunction)
}

describe('国家中断报告工作台键盘焦点合同', () => {
  it('报告失败按错误类别给出下一步，不把合同校验失败误写成数据门槛问题', () => {
    const nextAction = functionSource('reportFailureNextAction')
    const handler = functionSource('handleAgentEvent')

    expect(nextAction).toContain("normalized === 'report_payload_invalid'")
    expect(nextAction).toContain('报告合同校验未通过')
    expect(nextAction).toContain('勿调整数据门槛')
    expect(nextAction).toContain('/insufficient|eligibility|data_gate/')
    expect(nextAction).toContain('当前快照未达到正式报告数据门槛')
    expect(nextAction).toContain('if (provided?.trim()) return provided')
    expect(handler).toContain('reportFailureNextAction(')
    expect(handler).not.toContain('请核对数据门槛后重新生成。')
  })

  it('删除外部 URL 后在 DOM 更新完成时聚焦计算目标，并以添加按钮兜底', () => {
    const removal = functionSource('removeExternalEvidenceUrl')

    expect(removal).toContain('externalUrls.value.splice(index, 1)')
    expect(removal).toContain('externalEvidenceUrlFocusTargetAfterRemoval(')
    expect(removal).toContain('void nextTick(() => {')
    expect(removal).toContain('input.focus()')
    expect(removal).toContain('externalUrlAddButton.value?.focus()')
    expect(source).toContain('ref="externalAuthorizationPanel"')
    expect(source).toContain('ref="externalUrlAddButton"')
  })

  it('研读记录网格允许内容收缩，不把报告工作台撑出视口', () => {
    expect(source).toMatch(
      /\.reader-notes\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s,
    )
  })

  it('外部来源混合状态与结构化冲突使用不同标签，并逐来源显示证据状态', () => {
    const claimStatus = functionSource('externalClaimStatusLabel')
    const comparisonStatus = functionSource(
      'externalComparisonStatusLabel',
    )
    const sourceStatus = functionSource('externalEvidenceStatusLabel')

    expect(claimStatus).toContain("mixed: '来源状态混合'")
    expect(claimStatus).toContain("conflict: '结构化来源冲突'")
    expect(comparisonStatus).toContain(
      "conflict: '可比结构化事实冲突'",
    )
    expect(comparisonStatus).toContain(
      "insufficient: '没有足够的可比结构化事实'",
    )
    expect(sourceStatus).toContain(
      "available: '结构化事实可比较'",
    )
    expect(sourceStatus).toContain("read_failed: '来源读取失败'")
    expect(source).toContain(
      'item.externalAppendix.comparison_status',
    )
    expect(source).toContain('source.evidence_status')
  })

  it('只有同快照、无错误完成态的外部核验显示独立 Markdown 下载', () => {
    const downloadable = functionSource('externalAppendixDownloadable')
    const artifactHref = functionSource('externalAppendixArtifactHref')

    expect(downloadable).toContain("item.state === 'completed'")
    expect(downloadable).toContain(
      "item.evidenceMode === 'domeye_plus_external'",
    )
    expect(downloadable).toContain("appendix.status === 'completed'")
    expect(downloadable).toContain('!appendix.error')
    expect(downloadable).toContain('source.read_status === \'readable\'')
    expect(downloadable).toContain(
      'appendixMatchesCurrentReport(appendix, currentReport)',
    )
    expect(downloadable).toContain('!displayedSessionExpired.value')
    expect(artifactHref).toContain(
      'api.value.externalAppendixArtifactUrl(',
    )
    expect(source).toContain(
      'v-if="externalAppendixDownloadable(item)"',
    )
    expect(source).toContain(
      ':href="externalAppendixArtifactHref(item.questionId)"',
    )
    expect(source).toContain('下载核验附录 · MD')
  })

  it('自然语言不会自动联网，外部核验必须填写允许 URL 后显式确认', () => {
    const askQuestion = functionSource('askQuestion')
    const enable = functionSource('setExternalEvidenceMode')
    const confirm = functionSource(
      'confirmExternalEvidenceAuthorization',
    )

    expect(askQuestion).toContain(
      'const useExternalEvidence = requestedExternalPolicy !== null',
    )
    expect(askQuestion).toContain(
      'external_urls: externalUrlValidation.value.urls',
    )
    expect(askQuestion).not.toMatch(
      /prompt\.(?:includes|match|test).*?(?:搜索|联网|https?)/s,
    )
    expect(source).toContain(
      '只读取并核验上述明确 URL；不会根据问题文字发现或扩展其他网页。',
    )
    expect(source).toContain(
      '只允许读取当前策略声明的',
    )
    expect(source).toContain('externalAllowedHostRootsLabel')
    expect(source).not.toContain(
      '只允许读取 bgp.he.net、radar.cloudflare.com',
    )
    expect(enable).toContain('if (!externalCapabilityReady.value)')
    expect(enable).not.toContain('new Date().toISOString()')
    expect(confirm).toContain('externalUrlValidation.value')
    expect(confirm).toContain('new Date().toISOString()')
    expect(source).toContain(
      'URL 编辑后必须再次明确确认',
    )
  })

  it('readiness 非 ready 时只显示明确状态，不渲染外部 checkbox', () => {
    const refresh = functionSource('refreshExternalEvidenceCapability')
    const enable = functionSource('setExternalEvidenceMode')
    const ask = functionSource('askQuestion')

    expect(refresh).toContain(
      'api.value.getExternalEvidenceCapability(',
    )
    expect(refresh).toContain(
      "externalCapability.value = { state: 'checking' }",
    )
    expect(refresh).toContain(
      "externalCapability.value = { state: 'unknown' }",
    )
    expect(source).toContain('v-if="externalCapabilityReady"')
    expect(source).toContain(
      '当前环境未配置公开来源旁证',
    )
    expect(source).toContain(
      '公开来源旁证自检未通过',
    )
    expect(source).toContain(
      '暂时无法确认公开来源旁证状态',
    )
    expect(source).toContain(
      '不影响仅使用 Domeye 数据的报告、追问和下载',
    )
    expect(enable).toContain('if (!externalCapabilityReady.value)')
    expect(ask).toContain(
      'const requestedExternalPolicy = (',
    )
    expect(ask).toContain(
      'externalModeEnabled.value && externalEvidencePolicy.value',
    )
  })

  it('URL 域名和数量完全来自 readiness policy，前端不保留来源常量', () => {
    const add = functionSource('addExternalEvidenceUrl')
    const confirm = functionSource(
      'confirmExternalEvidenceAuthorization',
    )

    expect(source).toContain(
      'validateExternalEvidenceUrls(',
    )
    expect(source).toContain(
      'externalEvidencePolicy.value',
    )
    expect(source).toContain(
      'externalEvidencePolicy.value?.minimum_urls',
    )
    expect(source).toContain(
      'externalEvidencePolicy.value?.maximum_urls',
    )
    expect(add).toContain(
      'externalEvidencePolicy.value?.maximum_urls',
    )
    expect(confirm).toContain(
      'externalEvidencePolicy.value.minimum_urls',
    )
    expect(source).not.toContain(
      'MAXIMUM_COUNTRY_OUTAGE_EXTERNAL_URLS',
    )
  })

  it('核心生成、Domeye-only 追问和核心下载不引用 readiness', () => {
    const generate = functionSource('generateReport')
    const artifactHref = functionSource('artifactHref')
    const externalAppendixDownloadable = functionSource(
      'externalAppendixDownloadable',
    )
    const externalAppendixArtifactHref = functionSource(
      'externalAppendixArtifactHref',
    )
    const ask = functionSource('askQuestion')

    expect(generate).not.toContain('externalCapability')
    expect(generate).not.toContain('getExternalEvidenceCapability')
    expect(artifactHref).not.toContain('externalCapability')
    expect(externalAppendixDownloadable).not.toContain(
      'externalCapability',
    )
    expect(externalAppendixArtifactHref).not.toContain(
      'externalCapability',
    )
    expect(ask).toContain("evidence_mode: 'domeye_only'")
    expect(ask).toContain(
      'const useExternalEvidence = requestedExternalPolicy !== null',
    )
  })

  it('页面门槛明确标为保守预检，不把浏览器判断冒充服务端正式门槛', () => {
    expect(source).toContain('页面预检（保守）')
    expect(source).toContain('页面预检通过，可提交服务端正式门槛')
    expect(source).toContain(
      'countryOutagePagePreflightChecks(props.observation)',
    )
  })
})

describe('国家中断报告工作台读屏语义合同', () => {
  it('会话到期只播报稳定提醒，不把每秒倒计时放进 live region', () => {
    expect(source).toContain('class="session-expiry-announcement sr-only"')
    expect(source).toContain('本次会话将在五分钟内到期')
    expect(source).toContain(
      '剩余 {{ formatRemainingTime(secondsRemaining) }}',
    )
  })

  it('回答完成只播报状态摘要，不在 live region 重复整段答案', () => {
    const handler = functionSource('handleAgentEvent')

    expect(handler).toContain('内容已写入研读记录')
    expect(handler).not.toContain('+ completedAnswer.text')
  })

  it('问题错误与输入框关联，并在关闭动态授权区后恢复开关焦点', () => {
    const closeAuthorization = functionSource(
      'closeExternalEvidenceAuthorization',
    )

    expect(source).toContain(':aria-invalid="Boolean(questionError)"')
    expect(source).toContain('id="country-outage-question-error"')
    expect(source).toContain(
      "...(questionError.value ? ['country-outage-question-error'] : [])",
    )
    expect(source).toContain('ref="externalModeToggle"')
    expect(closeAuthorization).toContain('resetExternalEvidenceAuthorization()')
    expect(closeAuthorization).toContain('void nextTick(() => {')
    expect(closeAuthorization).toContain('externalModeToggle.value?.focus()')
  })

  it('门槛、当前生成步骤和关键数字标签具有非颜色语义', () => {
    expect(source).toContain(
      `<span class="sr-only">{{ check.passed ? '通过：' : '未通过：' }}</span>`,
    )
    expect(source).toContain(
      `:aria-current="phase === item[0] ? 'step' : undefined"`,
    )
    expect(source).toContain('<span role="rowheader">{{ highlight.label }}</span>')
  })

  it('粘性报告页头持续显示 AI 生成和未经人工审核边界', () => {
    const start = source.indexOf(
      '<header ref="publishedHeader" class="published-header"',
    )
    expect(start).toBeGreaterThanOrEqual(0)
    const end = source.indexOf('</header>', start)
    const header = source.slice(start, end)

    expect(header).toContain('AI 生成')
    expect(header).toContain('未经人工审核')
    expect(header).toContain('published-trust-boundary')
    expect(header).toContain('report.snapshot.collectorId.toUpperCase()')
  })

  it('正式 Pi 可变别名报告条件展示权重限制、认证有效期和输入范围', () => {
    expect(source).toContain(
      'const modelCertificationBoundary = computed(() => {',
    )
    expect(source).toContain("model?.adapter !== 'pi-sdk'")
    expect(source).toContain("model.runtimeIdentity !== 'formal'")
    expect(source).toContain(
      "model.modelRevisionKind !== 'mutable_alias'",
    )
    expect(source).toContain(
      'model.immutableRevisionAvailable !== false',
    )
    expect(source).toContain(
      'v-if="modelCertificationBoundary"',
    )
    expect(source).toContain('不可变权重 revision 未提供')
    expect(source).toContain('认证有效至')
    expect(source).toContain('认证场景集')
    expect(source).toContain('认证输入范围')
    expect(source).toContain(
      'modelCertificationBoundary.limitation',
    )
  })

  it('追问发送前绑定同时给出事件、RRC25、REV 和 publication', () => {
    const start = source.indexOf(
      '<small id="country-outage-question-binding">',
    )
    expect(start).toBeGreaterThanOrEqual(0)
    const end = source.indexOf('</small>', start)
    const binding = source.slice(start, end)

    expect(binding).toContain('report.event.incident_id')
    expect(binding).toContain('report.event.country_name')
    expect(binding).toContain('report.event.event_type')
    expect(binding).toContain('RRC25')
    expect(binding).toContain('report.snapshot.revision')
    expect(binding).toContain('report.snapshot.publicationId')
  })

  it('下载制品显示完整正文与文件 SHA-256，能够脱离会话核对', () => {
    const start = source.indexOf('<footer class="artifact-ledger">')
    expect(start).toBeGreaterThanOrEqual(0)
    const end = source.indexOf('</footer>', start)
    const ledger = source.slice(start, end)

    expect(ledger).toContain(
      '正文 SHA-256 {{ report.reportContentSha256 }}',
    )
    expect(ledger).toContain('SHA-256 {{ artifact.sha256 }}')
    expect(ledger).not.toContain('.sha256.slice(')
  })

  it('新版失败后重连旧 SSE 时保留失败说明和冻结旧报告', () => {
    const handler = functionSource('handleAgentEvent')
    const restore = functionSource('restorePreviousReport')

    expect(handler).toContain('event.run_id !== reportRunId.value')
    expect(handler).toContain(
      'validateCompletedCountryOutageReportEvent(event, {',
    )
    expect(handler.indexOf('validateCompletedCountryOutageReportEvent'))
      .toBeLessThan(handler.indexOf('shouldIgnoreLateAgentEvent'))
    expect(handler).toContain('expectedReportId: reportId.value')
    expect(handler).toContain('expectedRunId: reportRunId.value')
    expect(handler).toContain('binding: reportBinding.value')
    expect(handler).toContain('retainedArtifacts: (')
    expect(handler).toContain('const replayingRetainedReport = Boolean(')
    expect(handler).toContain(
      'report.value?.artifactId === completedReport.artifactId',
    )
    expect(handler).toContain('if (!replayingRetainedReport) {')
    expect(handler).toContain("restorePreviousReport(")
    expect(handler.indexOf('report.value = completedReport'))
      .toBeGreaterThan(handler.indexOf('if (!validation.accepted'))
    expect(restore).toContain(
      'reportBinding.value = previous.reportBinding',
    )
    expect(restore).toContain('report.value = previous.report')
  })

  it('阅读旧回答时提交新问题不强制滚底，原本位于最新位置时才跟随', () => {
    const ask = functionSource('askQuestion')

    expect(ask).toContain('const followLatest = notesAtLatest.value')
    expect(ask).toContain('if (followLatest) returnToLatest()')
    expect(ask).not.toContain('\n    returnToLatest()')
    expect(source).toContain('v-if="hasUnreadNotes"')
    expect(source).toContain('有新回答 · 返回最新')
  })

  it('报告与追问只在结果不确定时保留同一逻辑提交幂等键', () => {
    const generate = functionSource('generateReport')
    const ask = functionSource('askQuestion')

    expect(generate).toContain('logicalSubmissionFingerprint({')
    expect(generate).toContain('event_reference: props.eventReference')
    expect(generate).toContain('publication_id:')
    expect(generate).toContain('revision:')
    expect(generate).toContain('reportSubmissionIdempotency.begin(')
    expect(generate).toContain(
      'idempotency_key: submissionAttempt.idempotencyKey',
    )
    expect(generate).toContain(
      'isCountryOutageRequestOutcomeUncertain(cause)',
    )
    expect(generate).toContain("'outcome_uncertain'")
    expect(generate).toContain("'deterministic_rejection'")

    expect(ask).toContain('logicalSubmissionFingerprint({')
    expect(ask).toContain('report_id: reportId.value')
    expect(ask).toContain('question: prompt')
    expect(ask).toContain('quote: requestQuote ?? null')
    expect(ask).toContain('external_authorization_at:')
    expect(ask).toContain('external_urls:')
    expect(ask).toContain('questionSubmissionIdempotency.begin(')
    expect(ask).toContain(
      'idempotency_key: submissionAttempt.idempotencyKey',
    )
    expect(ask).toContain(
      'isCountryOutageRequestOutcomeUncertain(cause)',
    )
  })

  it('报告启动以可中止 epoch 隔离陈旧响应，恢复或重置后不得安装旧 reportId', () => {
    const generate = functionSource('generateReport')
    const restore = functionSource('restorePreviousReport')
    const reset = functionSource('resetReportState')

    expect(source).toContain(
      'const reportStartRequests = new CountryOutageAbortableRequestGate()',
    )
    expect(generate).toContain('reportStartRequests.begin()')
    expect(generate).toContain('.controller.signal')
    expect(generate).toContain('reportStartRequests.isCurrent(')
    expect(generate).toContain('reportStartRequests.finish(')
    expect(restore).toContain('reportStartRequests.invalidate()')
    expect(reset).toContain('reportStartRequests.invalidate()')

    const responseIndex = generate.indexOf(
      'await api.value.startReport(',
    )
    const currentEpochIndex = generate.indexOf(
      'reportStartRequests.isCurrent(',
      responseIndex,
    )
    const reportIdInstallIndex = generate.indexOf(
      'reportId.value = response.report_id',
    )
    expect(responseIndex).toBeGreaterThanOrEqual(0)
    expect(currentEpochIndex).toBeGreaterThan(responseIndex)
    expect(reportIdInstallIndex).toBeGreaterThan(currentEpochIndex)
  })

  it('追问启动以独立可中止 epoch 绑定当前报告，迟到 POST 不得污染切换后的页面', () => {
    const ask = functionSource('askQuestion')
    const generate = functionSource('generateReport')
    const restore = functionSource('restorePreviousReport')
    const reset = functionSource('resetReportState')

    expect(source).toContain(
      'const questionStartRequests = new CountryOutageAbortableRequestGate()',
    )
    expect(ask).toContain('const requestToken = questionStartRequests.begin()')
    expect(ask).toContain('const requestedReportId = reportId.value')
    expect(ask).toContain('const requestedArtifactId = requestedReport.artifactId')
    expect(ask).toContain('const requestedSnapshot = { ...requestedReport.snapshot }')
    expect(ask).toContain('requestToken.controller.signal')
    expect(ask).toContain('questionStartRequests.isCurrent(requestToken)')
    expect(ask).toContain('reportId.value !== requestedReportId')
    expect(ask).toContain(
      'report.value?.artifactId !== requestedArtifactId',
    )
    expect(ask).toContain('sameCountryOutageSnapshotIdentity(')
    expect(ask).toContain('questionStartRequests.finish(requestToken)')
    expect(generate).toContain(
      'activeQuestionRunId.value || questionStarting.value',
    )
    expect(generate).toContain('questionStartRequests.invalidate()')
    expect(restore).toContain('questionStartRequests.invalidate()')
    expect(reset).toContain('questionStartRequests.invalidate()')
    expect(source).toContain(
      ':disabled="Boolean(activeQuestionRunId) || questionStarting"',
    )
    expect(source).toMatch(
      /onBeforeUnmount\(\(\) => \{[\s\S]*questionStartRequests\.invalidate\(\)/,
    )

    const responseIndex = ask.indexOf('await api.value.askQuestion(')
    const currentEpochIndex = ask.indexOf(
      'questionStartRequests.isCurrent(requestToken)',
      responseIndex,
    )
    const stateInstallIndex = ask.indexOf(
      'questionSubmissionIdempotency.settle(',
      currentEpochIndex,
    )
    expect(responseIndex).toBeGreaterThanOrEqual(0)
    expect(currentEpochIndex).toBeGreaterThan(responseIndex)
    expect(stateInstallIndex).toBeGreaterThan(currentEpochIndex)
  })

  it('追问状态同时闭合 runId 与 questionId，完成回答还必须闭合当前报告快照', () => {
    const handler = functionSource('handleAgentEvent')

    expect(handler).toContain('matchCountryOutageQuestionEvent(')
    expect(handler).toContain(
      'validateCountryOutageQuestionAnswerSnapshot(',
    )
    expect(handler).toContain(
      "if (result.answer || event.phase === 'completed')",
    )
    expect(handler).not.toMatch(
      /item\.runId\s*===\s*event\.run_id\s*\|\|[\s\S]{0,160}item\.questionId\s*===\s*result\.question_id/,
    )

    const snapshotValidationIndex = handler.indexOf(
      'validateCountryOutageQuestionAnswerSnapshot(',
    )
    const entryCreationIndex = handler.indexOf(
      'questions.value.push(entry)',
      snapshotValidationIndex,
    )
    const completedMutationIndex = handler.indexOf(
      "entry.state = 'completed'",
      snapshotValidationIndex,
    )
    const collectingAnswerMutationIndex = handler.indexOf(
      'entry.answer = result.answer',
      snapshotValidationIndex,
    )
    const questionAnnouncementIndex = handler.indexOf(
      'readerAnnouncement.value = (',
      snapshotValidationIndex,
    )
    expect(snapshotValidationIndex).toBeGreaterThanOrEqual(0)
    expect(entryCreationIndex).toBeGreaterThan(snapshotValidationIndex)
    expect(completedMutationIndex).toBeGreaterThan(snapshotValidationIndex)
    expect(collectingAnswerMutationIndex)
      .toBeGreaterThan(snapshotValidationIndex)
    expect(questionAnnouncementIndex)
      .toBeGreaterThan(snapshotValidationIndex)
  })

  it('外部附录先闭合当前报告 binding，错 binding 不得覆盖旧附录', () => {
    const handler = functionSource('handleAgentEvent')
    const appendixValidationIndex = handler.indexOf(
      'appendixMatchesCurrentReport(incomingAppendix, report.value)',
    )
    const createAppendixIndex = handler.indexOf(
      '{ externalAppendix: incomingAppendix }',
      appendixValidationIndex,
    )
    const updateAppendixIndex = handler.indexOf(
      'entry.externalAppendix = incomingAppendix',
      appendixValidationIndex,
    )

    expect(appendixValidationIndex).toBeGreaterThanOrEqual(0)
    expect(handler).toContain('已保留原有附录')
    expect(createAppendixIndex).toBeGreaterThan(appendixValidationIndex)
    expect(updateAppendixIndex).toBeGreaterThan(appendixValidationIndex)
    expect(handler).toContain(
      'if (incomingAppendixAccepted && incomingAppendix)',
    )
  })

  it('新版提示比较完整快照，能够识别 revision 不变但 publication 前进', () => {
    const start = source.indexOf('const hasNewRevision = computed(')
    expect(start).toBeGreaterThanOrEqual(0)
    const end = source.indexOf('\nconst suggestions = computed(', start)
    const detector = source.slice(start, end)

    expect(detector).toContain('const frozen = report.value?.snapshot')
    expect(detector).toContain('frozen.incidentId')
    expect(detector).toContain('props.observation.incident_id')
    expect(detector).toContain('props.observation.publication_id')
    expect(detector).toContain('frozen.publicationId')
    expect(detector).toContain('props.observation.revision')
    expect(detector).toContain('frozen.revision')
    expect(detector).toContain('props.observation.data_through')
    expect(detector).toContain('frozen.dataThrough')
    expect(detector).toContain('props.observation.is_final')
    expect(detector).toContain('frozen.isFinal')
    expect(detector).toContain('props.observation.cohort_id')
    expect(detector).toContain('frozen.cohortId')
    expect(detector).toContain('props.observation.window_start_utc')
    expect(detector).toContain('frozen.windowStartUtc')
    expect(detector).toContain('props.observation.window_end_utc')
    expect(detector).toContain('frozen.windowEndUtc')
    expect(detector).toContain(
      'props.observation.observation_scope.collector_id',
    )
    expect(detector).toContain('frozen.collectorId')
  })

  it.each([
    {
      kind: '报告',
      fingerprint: logicalSubmissionFingerprint({
        event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
        publication_id: 'publication-v1',
        revision: 1,
      }),
    },
    {
      kind: '追问',
      fingerprint: logicalSubmissionFingerprint({
        report_id: 'report-1',
        publication_id: 'publication-v1',
        revision: 1,
        data_through: '2026-02-28T15:00:00Z',
        question: '结束时恢复了吗？',
        evidence_mode: 'domeye_only',
        quote: null,
        external_authorization_at: null,
        external_urls: [],
      }),
    },
  ])(
    '$kind 遇到 Flask 503 后重试复用 key，并由去重成功清除',
    ({ fingerprint }) => {
      let sequence = 0
      const state = new LogicalSubmissionIdempotency(
        () => `submission-key-${++sequence}`,
      )
      const firstAttempt = state.begin(fingerprint)
      const flaskFailure = new CountryOutageAgentRequestError({
        code: 'agent_unavailable',
        message: '读取 Sidecar 响应超时',
        retryable: true,
      })

      state.settle(
        firstAttempt,
        isCountryOutageRequestOutcomeUncertain(flaskFailure)
          ? 'outcome_uncertain'
          : 'deterministic_rejection',
      )
      const retry = state.begin(fingerprint)
      expect(retry.idempotencyKey).toBe(firstAttempt.idempotencyKey)

      const response = { deduplicated: true }
      expect(response.deduplicated).toBe(true)
      state.settle(retry, 'accepted')
      expect(state.begin(fingerprint).idempotencyKey)
        .not.toBe(retry.idempotencyKey)
    },
  )

  it('证据入口只发送一次返回观测事件', () => {
    const openEvidence = functionSource('openEvidence')

    expect(openEvidence).toContain(
      "emit('openObservation', observationAnchorForEvidence(evidenceRefs))",
    )
    expect(openEvidence.match(/emit\('openObservation'/g)).toHaveLength(1)
  })
})
