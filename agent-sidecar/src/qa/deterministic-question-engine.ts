import type {
  CountryOutageAsnPage,
  DerivedNumericFact,
  JsonObject,
  KeyVisibilityPoint,
  SnapshotIdentity,
} from '../domain/contracts.js'
import {
  extremaPoint,
  formatDurationMinutes,
  formatInteger,
  formatPercent,
  localTimeLabel,
} from '../report/format.js'
import {
  canonicalJsonSha256,
  canonicalJsonStringify,
} from '../shared/deterministic-json.js'
import type {
  AnswerQuestionOptions,
  CountryOutageQuestionAnswer,
  CountryOutageQuestionContext,
  CountryOutageQuestionRequest,
  QuestionAnswerDraft,
  QuestionEvidenceRecord,
  QuestionEvidenceSource,
  ReportQuestionAnchor,
  ResolvedReportAnchor,
  SuggestedQuestion,
} from './contracts.js'
import {
  DOMEYE_ONLY_EVIDENCE_MODE,
  DOMEYE_ONLY_EVIDENCE_MODE_LABEL,
  MAXIMUM_ANSWER_CHARACTERS,
} from './contracts.js'
import {
  QuestionAbortedError,
  QuestionAnswerValidationError,
  QuestionBindingError,
  QuestionInputError,
} from './errors.js'

const STATISTICAL_SCOPE =
  'RRC25、当前报告固定 cohort、当前 publication/revision 与观测窗口'

function normalizeQuestion(value: string): string {
  return value.normalize('NFKC').trim().replace(/\s+/g, ' ')
}

function snapshotsEqual(
  left: SnapshotIdentity,
  right: SnapshotIdentity,
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

function assertNotAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new QuestionAbortedError()
}

function assertAsnPageIdentity(
  page: CountryOutageAsnPage,
  snapshot: SnapshotIdentity,
): void {
  if (
    page.incident_id !== snapshot.incidentId ||
    page.publication_id !== snapshot.publicationId ||
    page.revision !== snapshot.revision ||
    page.data_through !== snapshot.dataThrough ||
    page.is_final !== snapshot.isFinal ||
    page.cohort_id !== snapshot.cohortId ||
    page.window_start_utc !== snapshot.windowStartUtc ||
    page.window_end_utc !== snapshot.windowEndUtc
  ) {
    throw new QuestionBindingError(
      `ASN 细化事实与报告快照不一致（page=${page.page}）`,
    )
  }
}

function assertContext(context: CountryOutageQuestionContext): void {
  const { report, facts, asnPages } = context
  if (report.event.event_type !== 'country_outage') {
    throw new QuestionBindingError('报告不是合法 country_outage 事件')
  }
  if (
    report.snapshot.collectorId !== 'rrc25' ||
    facts.snapshot.collectorId !== 'rrc25'
  ) {
    throw new QuestionBindingError('追问只允许使用 RRC25 快照')
  }
  if (!report.validation.passed) {
    throw new QuestionBindingError('追问只能绑定通过发布校验的正式报告')
  }
  if (!facts.eligibility.eligible) {
    throw new QuestionBindingError('追问事实集合未达到正式报告最低门槛')
  }
  if (
    report.factSetId !== facts.factSetId ||
    !snapshotsEqual(report.snapshot, facts.snapshot)
  ) {
    throw new QuestionBindingError('报告与事实集合身份不一致')
  }
  if (
    report.event.incident_id !== facts.snapshot.incidentId ||
    facts.event.incident_id !== facts.snapshot.incidentId
  ) {
    throw new QuestionBindingError('事件身份与报告快照不一致')
  }
  for (const page of asnPages) {
    assertAsnPageIdentity(page, facts.snapshot)
  }
}

function assertRequest(
  request: CountryOutageQuestionRequest,
  context: CountryOutageQuestionContext,
): string {
  const runtimeRequest = request as unknown as Record<string, unknown>
  for (const forbidden of [
    'history',
    'messages',
    'previousAnswers',
    'externalEvidence',
    'urls',
  ]) {
    if (runtimeRequest[forbidden] !== undefined) {
      throw new QuestionInputError(`Domeye-only 追问不接受字段：${forbidden}`)
    }
  }
  if (request.schemaVersion !== 'country_outage_question_request_v1') {
    throw new QuestionInputError('不支持的追问请求版本')
  }
  if (!request.requestId.trim() || !request.idempotencyKey.trim()) {
    throw new QuestionInputError('requestId 和 idempotencyKey 不能为空')
  }
  const question = normalizeQuestion(request.question)
  if (!question) throw new QuestionInputError('问题不能为空')
  if (request.binding.evidenceMode !== DOMEYE_ONLY_EVIDENCE_MODE) {
    throw new QuestionBindingError('A3 追问只支持“仅使用 Domeye 数据”')
  }
  if (
    request.binding.reportArtifactId !== context.report.artifactId ||
    request.binding.reportContentSha256 !==
      context.report.reportContentSha256 ||
    request.binding.factSetId !== context.facts.factSetId ||
    !snapshotsEqual(request.binding.snapshot, context.facts.snapshot)
  ) {
    throw new QuestionBindingError('问题绑定的报告、事实集合或快照身份不一致')
  }
  return question
}

function resolveAnchor(
  anchor: ReportQuestionAnchor | undefined,
  context: CountryOutageQuestionContext,
): ResolvedReportAnchor | null {
  if (!anchor) return null
  const { draft } = context.report
  if (anchor.kind === 'summary') {
    return {
      ref: 'report:/summary',
      label: '报告摘要',
      text: draft.summary.text,
      evidenceRefs: [...draft.summary.evidenceRefs],
    }
  }
  if (anchor.kind === 'highlight') {
    const highlight = draft.highlights[anchor.highlightIndex]
    if (!highlight) {
      throw new QuestionBindingError('引用的关键数字位置不存在')
    }
    return {
      ref: `report:/highlights/${anchor.highlightIndex}`,
      label: highlight.label,
      text: `${highlight.label}：${highlight.value}`,
      evidenceRefs: [...highlight.evidenceRefs],
    }
  }
  const section = draft.sections.find(
    (candidate) => candidate.id === anchor.sectionId,
  )
  const paragraph = section?.paragraphs[anchor.paragraphIndex]
  if (!section || !paragraph) {
    throw new QuestionBindingError('引用的报告段落位置不存在')
  }
  return {
    ref: `report:/sections/${section.id}/paragraphs/${anchor.paragraphIndex}`,
    label: section.title,
    text: paragraph.text,
    evidenceRefs: [...paragraph.evidenceRefs],
  }
}

function factPoint(
  context: CountryOutageQuestionContext,
  kind: KeyVisibilityPoint['kind'],
): KeyVisibilityPoint {
  const point = context.facts.keyVisibilityPoints.find(
    (candidate) => candidate.kind === kind,
  )
  if (!point) throw new QuestionBindingError(`事实集合缺少关键点：${kind}`)
  return point
}

function derivedFact(
  context: CountryOutageQuestionContext,
  metric: string,
): DerivedNumericFact {
  const fact = context.facts.derivedFacts.find(
    (candidate) => candidate.metric === metric,
  )
  if (!fact) throw new QuestionBindingError(`事实集合缺少派生事实：${metric}`)
  return fact
}

function pointRef(point: KeyVisibilityPoint): string {
  return `${point.provenance.endpoint}:${point.provenance.pointer}`
}

function draft(
  kind: QuestionAnswerDraft['kind'],
  text: string,
  evidenceRefs: string[],
  missingEvidence: string[] = [],
): QuestionAnswerDraft {
  return {
    kind,
    text,
    evidenceRefs: [...new Set(evidenceRefs)],
    missingEvidence,
  }
}

type ReportUnknownCategory =
  | 'national_outage'
  | 'user_impact'
  | 'cause'
  | 'responsibility'
  | 'post_window'

const REPORT_UNKNOWN_CATEGORY_PATTERNS: Readonly<
  Record<ReportUnknownCategory, RegExp>
> = {
  national_outage:
    /全国(?:性)?(?:互联网|网络)?(?:中断|断网|不可用)|数据面(?:状态|中断|可用性)/,
  user_impact: /用户|业务/,
  cause:
    /原因|因果|(?:攻击|政策行为|配置错误|基础设施故障).{0,10}(?:引起|导致|造成|起因)/,
  responsibility: /责任|归责|承担/,
  post_window: /窗口之后|观测之后|后续|完全恢复|事件.{0,4}结束/,
}

function reportUnknownRef(
  context: CountryOutageQuestionContext,
  category: ReportUnknownCategory,
): string | null {
  const index = context.report.draft.unknowns.findIndex((item) =>
    REPORT_UNKNOWN_CATEGORY_PATTERNS[category].test(item),
  )
  return index >= 0 ? `report:/unknowns/${index}` : null
}

function withReportUnknown(
  context: CountryOutageQuestionContext,
  category: ReportUnknownCategory,
  evidenceRefs: string[],
): string[] {
  const unknownRef = reportUnknownRef(context, category)
  return unknownRef ? [unknownRef, ...evidenceRefs] : evidenceRefs
}

function answerNationalOutage(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  return draft(
    'evidence_boundary',
    '当前报告不能据此认定“全国断网”。它只证明 RRC25 在固定统计范围内观察到 BGP 控制面路由可见性变化。要判断全国性互联网可用性，还需要多观测点或多测量平台的数据面流量、主动服务探测、运营商运行状态等证据。',
    withReportUnknown(context, 'national_outage', [
      'overview:/limitations',
      'audit:/evidence_level',
    ]),
    ['数据面流量', '主动服务探测', '多观测点或多测量平台证据', '运营商运行状态'],
  )
}

function answerUserImpact(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  return draft(
    'evidence_boundary',
    '当前快照不能回答用户能否上网、受影响用户数或具体业务影响。Prefix×VP、ASN 和等价路由资源都是 BGP 控制面统计，不能换算成用户、流量或业务数量。要回答该问题，需要运营商流量、服务可用性探测、应用遥测或用户侧测量。',
    withReportUnknown(context, 'user_impact', [
      'overview:/limitations',
      'overview:/cohort',
    ]),
    ['运营商流量', '服务可用性探测', '应用遥测', '用户侧测量'],
  )
}

function answerPostWindow(context: CountryOutageQuestionContext): QuestionAnswerDraft {
  return draft(
    'evidence_boundary',
    `当前报告只能说明观测窗口结束时的状态，不能判断其后是否完全恢复或事件是否结束。本快照窗口结束于 ${context.facts.scope.window_end_local}，数据截止为 ${context.facts.snapshot.dataThrough ?? '未提供'}；要回答后续状态，需要窗口之后、同口径且具有明确 publication/revision 的新观测快照。`,
    withReportUnknown(context, 'post_window', [
      'overview:/observation_scope/window_end_utc',
      'overview:/observation_scope/last_observation_at_utc',
    ]),
    ['窗口之后的同口径观测', '新的 publication/revision'],
  )
}

function answerResponsibility(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  return draft(
    'evidence_boundary',
    '当前快照不能认定哪个运营商或 ASN 应承担责任。ASN 的不可见持续时间和固定路由人口只是观测维度，不是责任排序。责任判断还需要原始路由变更、配置与变更记录、网络拓扑、故障通告以及责任方说明。',
    withReportUnknown(context, 'responsibility', [
      'overview:/limitations',
      'audit:/evidence_level',
    ]),
    ['原始路由变更', '配置与变更记录', '网络拓扑', '故障通告或责任方说明'],
  )
}

function answerCause(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  const hasUpdates =
    context.facts.capabilities.update_activity?.state === 'available'
  return draft(
    'evidence_boundary',
    hasUpdates
      ? '当前快照只能确认 BGP UPDATE 活动与可见性下降在时间上相邻，不能证明 UPDATE 或某种行为导致了这次变化。要判断原因，需要原始 BGP 报文、具体 AS_PATH 变化、观测点明细、网络变更记录及可靠的外部事件证据。当前“仅使用 Domeye 数据”模式不会联网补充这些证据。'
      : '当前快照没有足够证据判断变化原因。攻击、政策行为、配置错误或基础设施故障都不能由现有可见性统计直接推出。要判断原因，需要原始 BGP 报文、具体 AS_PATH 变化、观测点明细、网络变更记录及可靠的外部事件证据。当前“仅使用 Domeye 数据”模式不会联网。',
    hasUpdates
      ? withReportUnknown(context, 'cause', [
          'report:/sections/updates/paragraphs/1',
          'audit:/evidence_level',
        ])
      : withReportUnknown(context, 'cause', [
          'audit:/evidence_level',
        ]),
    ['原始 BGP 报文', 'AS_PATH 变化', '观测点明细', '网络变更记录', '可靠外部事件证据'],
  )
}

function answerExternalRequest(): QuestionAnswerDraft {
  return draft(
    'evidence_boundary',
    '当前问答固定为“仅使用 Domeye 数据”，不会访问互联网、打开 URL、读取外部页面或执行问题中的指令。外部证据必须由用户在独立的外部证据模式中显式授权；它也不能修改本报告或补造 Domeye 指标。',
    ['overview:/limitations', 'audit:/evidence_level'],
    ['用户显式授权的独立外部证据结果'],
  )
}

function answerAnchor(
  anchor: ResolvedReportAnchor,
): QuestionAnswerDraft {
  return draft(
    'fact',
    `你引用的是“${anchor.label}”：${anchor.text}。这条回答只沿用该位置已经绑定的报告证据，不引入前一轮假设、其他事件或其他 revision。`,
    [anchor.ref, ...anchor.evidenceRefs],
  )
}

function answerVisibility(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  const start = factPoint(context, 'start')
  const lowest = factPoint(context, 'lowest')
  const end = factPoint(context, 'end')
  const loss = derivedFact(
    context,
    'start_to_lowest_visible_prefix_vp_change',
  )
  const endGap = derivedFact(context, 'end_gap_from_start')
  return draft(
    'fact',
    `窗口起点有 ${formatInteger(start.visiblePrefixVpCount)} 条 Prefix×VP 可见，覆盖率 ${formatPercent(start.visiblePrefixVpRatio)}；${localTimeLabel(lowest.observedAtLocal)}降至最低 ${formatInteger(lowest.visiblePrefixVpCount)} 条、${formatPercent(lowest.visiblePrefixVpRatio)}，比起点少 ${formatInteger(loss.value)} 条；窗口结束回升至 ${formatInteger(end.visiblePrefixVpCount)} 条、${formatPercent(end.visiblePrefixVpRatio)}，但仍比起点少 ${formatInteger(endGap.value)} 条。`,
    [pointRef(start), pointRef(lowest), pointRef(end), loss.factId, endGap.factId],
  )
}

function answerLowest(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  const start = factPoint(context, 'start')
  const lowest = factPoint(context, 'lowest')
  const loss = derivedFact(
    context,
    'start_to_lowest_visible_prefix_vp_change',
  )
  const lossRatio = derivedFact(context, 'start_to_lowest_loss_ratio')
  return draft(
    'fact',
    `窗口最低点出现在 ${lowest.observedAtLocal}：可见 Prefix×VP 为 ${formatInteger(lowest.visiblePrefixVpCount)} 条，覆盖率为 ${formatPercent(lowest.visiblePrefixVpRatio)}。与窗口起点相比，减少 ${formatInteger(loss.value)} 条，相当于起点可见关系的 ${formatPercent(lossRatio.value)}。`,
    [pointRef(start), pointRef(lowest), loss.factId, lossRatio.factId],
  )
}

function answerStart(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  const start = factPoint(context, 'start')
  return draft(
    'fact',
    `窗口起点 ${start.observedAtLocal} 可见 Prefix×VP 为 ${formatInteger(start.visiblePrefixVpCount)} 条，覆盖率为 ${formatPercent(start.visiblePrefixVpRatio)}。`,
    [pointRef(start)],
  )
}

function answerEnd(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  const start = factPoint(context, 'start')
  const end = factPoint(context, 'end')
  const endGap = derivedFact(context, 'end_gap_from_start')
  return draft(
    'fact',
    `窗口结束 ${end.observedAtLocal} 可见 Prefix×VP 为 ${formatInteger(end.visiblePrefixVpCount)} 条，覆盖率为 ${formatPercent(end.visiblePrefixVpRatio)}。它仍比起点少 ${formatInteger(endGap.value)} 条，因此只能说窗口内出现部分回升，不能称为完全恢复。`,
    [pointRef(start), pointRef(end), endGap.factId],
  )
}

function answerLargestDrop(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  const point = factPoint(context, 'largest_drop')
  const slot = context.facts.series[point.slotIndex]
  if (!slot || typeof slot.visible_prefix_vp_delta !== 'number') {
    throw new QuestionBindingError('最大单槽下降缺少对应序列事实')
  }
  const interval = context.facts.scope.interval_seconds
  const intervalLabel =
    typeof interval === 'number'
      ? formatDurationMinutes(interval / 60)
      : '当前观测槽'
  return draft(
    'fact',
    `${point.observedAtLocal}记录到窗口最大单槽下降：${intervalLabel}内可见 Prefix×VP 减少 ${formatInteger(Math.abs(slot.visible_prefix_vp_delta))} 条。`,
    [pointRef(point)],
  )
}

function answerRecoveryWithinWindow(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  const recovered = derivedFact(context, 'recovered_from_lowest')
  const share = derivedFact(context, 'recovery_share_of_prior_loss')
  const endGap = derivedFact(context, 'end_gap_from_start')
  const lowest = factPoint(context, 'lowest')
  const end = factPoint(context, 'end')
  return draft(
    'fact',
    `从最低点到窗口结束，可见 Prefix×VP 回升 ${formatInteger(recovered.value)} 条，相当于此前损失的 ${formatPercent(share.value, 1)}；但结束时仍比起点少 ${formatInteger(endGap.value)} 条。因此这是窗口内部分回升，不是“已经完全恢复”。`,
    [pointRef(lowest), pointRef(end), recovered.factId, share.factId, endGap.factId],
  )
}

function answerScope(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  return draft(
    'fact',
    `本报告固定观察 ${formatInteger(context.facts.cohort.origin_asn_count)} 个 origin ASN、${formatInteger(context.facts.cohort.prefix_vp_count)} 条 Prefix×VP，collector 仅为 RRC25，窗口为 ${context.facts.scope.window_start_local} 至 ${context.facts.scope.window_end_local}。`,
    [
      'overview:/cohort/origin_asn_count',
      'overview:/cohort/prefix_vp_count',
      'overview:/observation_scope',
    ],
  )
}

function answerPrefixVpSemantics(): QuestionAnswerDraft {
  return draft(
    'metric_semantics',
    'Prefix×VP 表示“某个前缀是否能从某个固定 BGP 观测点看到”的一条固定路由观测关系。同一前缀可能对应多个观测点，所以它不是唯一前缀数，也不能直接换算成 IP 数、受影响用户数或业务数。',
    ['overview:/cohort/denominator_policy', 'overview:/cohort/prefix_vp_count'],
  )
}

function answerCoverageSemantics(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  const start = factPoint(context, 'start')
  return draft(
    'metric_semantics',
    `覆盖率是“当前可见 Prefix×VP 数 ÷ 固定 cohort 的 Prefix×VP 总数”。本报告的分母固定为 ${formatInteger(context.facts.cohort.prefix_vp_count)} 条，起点、最低点和结束点都使用同一分母；缺槽不能补零。`,
    [pointRef(start), 'overview:/cohort/prefix_vp_count', 'overview:/cohort/denominator_policy'],
  )
}

function answerAsnStateSemantics(): QuestionAnswerDraft {
  return draft(
    'metric_semantics',
    '在固定统计范围内，“全不可见”表示该 origin ASN 的固定成员路由全部不可见；“部分可见”表示只有一部分成员路由仍可见。它们是控制面状态，不能直接换算为用户影响；不同状态的峰值发生时间也不能直接相加。',
    [
      'overview:/capabilities/asn_matrix',
      'series:/metric_extrema/fully_invisible_asn_count/max',
      'series:/metric_extrema/partially_visible_asn_count/max',
    ],
  )
}

function answerUpdateSemantics(): QuestionAnswerDraft {
  return draft(
    'metric_semantics',
    'ANNOUNCE、WITHDRAW 和 UPDATE 总量表示相应观测槽内的 BGP 更新活动。UPDATE 峰值与可见性下降时间相邻只能说明时间对应，不能单独证明因果、故障持续或恢复。',
    ['overview:/capabilities/update_activity', 'audit:/evidence_level'],
  )
}

function answerResourceSemantics(): QuestionAnswerDraft {
  return draft(
    'metric_semantics',
    'IPv4 /24 和 IPv6 /48 等价资源是规范化、去重后的路由资源覆盖。较大前缀会被折算为很多等价块，因此这些数字不是独立前缀数、实际在线 IP 数或受影响用户数。',
    ['overview:/capabilities/country_resources', 'overview:/limitations'],
  )
}

function asnNumber(item: JsonObject): number | null {
  if (typeof item.asn === 'number' && Number.isInteger(item.asn)) {
    return item.asn
  }
  if (typeof item.asn === 'string' && /^\d+$/.test(item.asn)) {
    return Number(item.asn)
  }
  return null
}

function flattenedAsnItems(
  context: CountryOutageQuestionContext,
): Array<{ page: CountryOutageAsnPage; index: number; item: JsonObject }> {
  return context.asnPages.flatMap((page) =>
    page.items.map((item, index) => ({ page, index, item })),
  )
}

function answerSpecificAsn(
  context: CountryOutageQuestionContext,
  asn: number,
): QuestionAnswerDraft {
  if (context.facts.capabilities.asn_matrix?.state !== 'available') {
    return draft(
      'insufficient_evidence',
      `当前快照的 ASN 细化能力不可用，不能回答 AS${asn} 的状态。`,
      ['overview:/capabilities/asn_matrix'],
      ['同快照 ASN 细化事实'],
    )
  }
  const found = flattenedAsnItems(context).find(
    ({ item }) => asnNumber(item) === asn,
  )
  if (!found) {
    return draft(
      'insufficient_evidence',
      `当前已绑定的同快照 ASN 细化事实中没有 AS${asn} 的记录，因此不能据此判断它的状态；这不等于该 ASN 不存在或未受影响。需要在同一 publication/revision 下读取包含该 ASN 的受限分页结果。`,
      ['overview:/capabilities/asn_matrix'],
      [`同快照中包含 AS${asn} 的 ASN 分页事实`],
    )
  }
  const slots = found.item.longest_fully_invisible_slots
  const baseline = found.item.baseline_prefix_vp_count
  const interval = context.facts.scope.interval_seconds
  const duration =
    typeof slots === 'number' && typeof interval === 'number'
      ? formatDurationMinutes((slots * interval) / 60)
      : '当前细化事实未给出'
  const baselineText =
    typeof baseline === 'number'
      ? `，固定 Prefix×VP 人口为 ${formatInteger(baseline)} 条`
      : ''
  const durationEvidence =
    typeof slots === 'number' && typeof interval === 'number'
      ? `（由 ${formatInteger(slots)} 个连续观测槽 × 每槽 ${formatDurationMinutes(interval / 60)} 换算）`
      : ''
  return draft(
    'fact',
    `AS${asn} 在当前同快照 ASN 细化事实中的最长连续全不可见时间为 ${duration}${durationEvidence}${baselineText}。持续时间和固定人口都不能解释为用户影响或责任排名。`,
    [
      `asns:/pages/${found.page.page}/items/${found.index}`,
      'overview:/observation_scope/interval_seconds',
    ],
  )
}

function answerTopAsns(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  if (
    context.facts.capabilities.asn_matrix?.state !== 'available' ||
    context.asnPages.length === 0
  ) {
    return draft(
      'insufficient_evidence',
      '当前快照没有可用的 ASN 细化事实，不能列出持续时间较长的 ASN。',
      ['overview:/capabilities/asn_matrix'],
      ['同快照 ASN 细化事实'],
    )
  }
  const interval = context.facts.scope.interval_seconds
  if (typeof interval !== 'number') {
    return draft(
      'insufficient_evidence',
      '当前快照缺少观测槽间隔，无法把 ASN 连续槽数换算为持续时间。',
      ['overview:/observation_scope/interval_seconds'],
      ['观测槽间隔'],
    )
  }
  const rows = flattenedAsnItems(context)
    .flatMap((entry) => {
      const asn = asnNumber(entry.item)
      const slots = entry.item.longest_fully_invisible_slots
      return asn !== null && typeof slots === 'number'
        ? [{ ...entry, asn, slots }]
        : []
    })
    .sort((left, right) => right.slots - left.slots || left.asn - right.asn)
    .slice(0, 6)
  if (rows.length === 0) {
    return draft(
      'insufficient_evidence',
      '当前 ASN 分页没有可用于持续时间排序的完整记录。',
      ['overview:/capabilities/asn_matrix'],
      ['ASN 号和最长连续全不可见槽数'],
    )
  }
  return draft(
    'fact',
    `当前已绑定分页中，最长连续全不可见时间靠前的 ASN 为：${rows
      .map(
        (row) =>
          `AS${row.asn}（${formatDurationMinutes((row.slots * interval) / 60)}）`,
      )
      .join('、')}。持续时间由“连续观测槽数 × 每槽 ${formatDurationMinutes(interval / 60)}”换算。这是同快照受限分页内的持续时间排序，不是影响规模或责任排名。`,
    [
      ...rows.map(
        (row) => `asns:/pages/${row.page.page}/items/${row.index}`,
      ),
      'overview:/observation_scope/interval_seconds',
    ],
  )
}

function answerAsnPeak(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  const fully = extremaPoint(
    context.facts.metricExtrema,
    'fully_invisible_asn_count',
    'max',
  )
  const partial = extremaPoint(
    context.facts.metricExtrema,
    'partially_visible_asn_count',
    'max',
  )
  if (!fully || !partial) {
    return draft(
      'insufficient_evidence',
      '当前快照没有同时可用的全不可见和部分可见 ASN 峰值，不能形成同口径比较。',
      ['overview:/capabilities/asn_matrix'],
      ['全不可见 ASN 峰值', '部分可见 ASN 峰值'],
    )
  }
  return draft(
    'fact',
    `全不可见 ASN 峰值为 ${formatInteger(fully.value)} 个，出现在 ${fully.observed_at_local}；部分可见 ASN 峰值为 ${formatInteger(partial.value)} 个，出现在 ${partial.observed_at_local}。两个峰值发生在不同时间，不能相加。`,
    [
      'series:/metric_extrema/fully_invisible_asn_count/max',
      'series:/metric_extrema/partially_visible_asn_count/max',
    ],
  )
}

function answerAddressFamilies(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  if (context.facts.capabilities.address_families?.state !== 'available') {
    return draft(
      'insufficient_evidence',
      '当前快照的 IPv4/IPv6 地址族能力不可用，不能形成地址族比较。',
      ['overview:/capabilities/address_families'],
      ['同快照 IPv4/IPv6 覆盖率事实'],
    )
  }
  const ipv4 = extremaPoint(
    context.facts.metricExtrema,
    'ipv4_visible_prefix_vp_ratio',
    'min',
  )
  const ipv6 = extremaPoint(
    context.facts.metricExtrema,
    'ipv6_visible_prefix_vp_ratio',
    'min',
  )
  if (!ipv4 || !ipv6) {
    return draft(
      'insufficient_evidence',
      '当前快照缺少完整的 IPv4/IPv6 最低覆盖率，不能形成同口径比较。',
      ['overview:/capabilities/address_families'],
      ['IPv4 最低覆盖率', 'IPv6 最低覆盖率'],
    )
  }
  return draft(
    'fact',
    `IPv4 最低覆盖率为 ${formatPercent(ipv4.value, 3)}，IPv6 最低覆盖率为 ${formatPercent(ipv6.value, 3)}。按各自固定人口的覆盖率看，当前窗口变化主要体现在 IPv4；这不能换算为 IPv4 用户受到同比例影响。`,
    [
      'series:/metric_extrema/ipv4_visible_prefix_vp_ratio/min',
      'series:/metric_extrema/ipv6_visible_prefix_vp_ratio/min',
    ],
  )
}

function answerUpdateActivity(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  if (context.facts.capabilities.update_activity?.state !== 'available') {
    return draft(
      'insufficient_evidence',
      '当前快照的 UPDATE 活动能力不可用，不能回答 UPDATE 峰值或时间对应。',
      ['overview:/capabilities/update_activity'],
      ['同快照 UPDATE 序列与峰值'],
    )
  }
  const update = extremaPoint(
    context.facts.resourceMetricExtrema,
    'update_total',
    'max',
  )
  const announce = extremaPoint(
    context.facts.resourceMetricExtrema,
    'announce_count',
    'max',
  )
  const withdraw = extremaPoint(
    context.facts.resourceMetricExtrema,
    'withdraw_count',
    'max',
  )
  const drop = factPoint(context, 'largest_drop')
  if (!update || !announce || !withdraw) {
    return draft(
      'insufficient_evidence',
      '当前快照缺少完整的 UPDATE、ANNOUNCE 或 WITHDRAW 峰值。',
      ['overview:/capabilities/update_activity'],
      ['UPDATE、ANNOUNCE 与 WITHDRAW 峰值'],
    )
  }
  return draft(
    'fact',
    `${update.observed_at_local}，UPDATE 总量达到 ${formatInteger(update.value)} 条，其中 ANNOUNCE ${formatInteger(announce.value)} 条、WITHDRAW ${formatInteger(withdraw.value)} 条；窗口最大单槽可见性下降出现在 ${drop.observedAtLocal}。两者时间相邻，但当前证据不能证明因果。`,
    [
      'series:/resource_metric_extrema/update_total/max',
      'series:/resource_metric_extrema/announce_count/max',
      'series:/resource_metric_extrema/withdraw_count/max',
      pointRef(drop),
    ],
  )
}

function answerResources(
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  if (context.facts.capabilities.country_resources?.state !== 'available') {
    return draft(
      'insufficient_evidence',
      '当前快照的国家级路由资源能力不可用。',
      ['overview:/capabilities/country_resources'],
      ['同快照国家级路由资源事实'],
    )
  }
  const max = extremaPoint(
    context.facts.resourceMetricExtrema,
    'ipv4_24_equivalent_count',
    'max',
  )
  const min = extremaPoint(
    context.facts.resourceMetricExtrema,
    'ipv4_24_equivalent_count',
    'min',
  )
  const change = context.facts.derivedFacts.find(
    (candidate) =>
      candidate.metric === 'ipv4_24_equivalent_max_to_min_change',
  )
  if (!max || !min || !change) {
    return draft(
      'insufficient_evidence',
      '当前快照缺少完整的 IPv4 /24 等价资源极值或派生差值。',
      ['overview:/capabilities/country_resources'],
      ['IPv4 /24 等价资源最大值、最小值和差值'],
    )
  }
  return draft(
    'fact',
    `IPv4 /24 等价资源从窗口最大 ${formatInteger(max.value)} 个降至最低 ${formatInteger(min.value)} 个，相差 ${formatInteger(change.value)} 个。它表示规范化路由资源覆盖，不是在线 IP、独立前缀或用户数量。`,
    [
      'series:/resource_metric_extrema/ipv4_24_equivalent_count/max',
      'series:/resource_metric_extrema/ipv4_24_equivalent_count/min',
      change.factId,
    ],
  )
}

function selectAnswer(
  question: string,
  anchor: ResolvedReportAnchor | null,
  context: CountryOutageQuestionContext,
): QuestionAnswerDraft {
  const lower = question.toLocaleLowerCase('zh-CN')
  if (
    /联网|外部搜索|搜索外部|查一下网页|打开.{0,12}(网址|url)|https?:\/\//i.test(
      lower,
    ) ||
    /忽略.{0,12}(指令|规则|系统)|提示词|system prompt/i.test(lower)
  ) {
    return answerExternalRequest()
  }
  if (/全国.{0,6}(断网|中断)|国家级.{0,6}(断网|中断)/.test(lower)) {
    return answerNationalOutage(context)
  }
  if (/prefix\s*[×x*]\s*vp|路由观测关系/.test(lower)) {
    return answerPrefixVpSemantics()
  }
  if (
    /用户.{0,8}(影响|上网|断网|多少|规模)|业务.{0,8}(影响|中断)|受影响.{0,4}(人|用户)|流量影响/.test(
      lower,
    )
  ) {
    return answerUserImpact(context)
  }
  if (/责任|负责|归责|哪个运营商造成|哪个asn造成/.test(lower)) {
    return answerResponsibility(context)
  }
  if (
    /窗口之后|观测之后|23:00之后|后来|现在.{0,8}(恢复|状态)|完全恢复|事件.{0,4}(结束|结束了吗)/.test(
      lower,
    )
  ) {
    return answerPostWindow(context)
  }
  if (
    /发生原因|什么原因|为何发生|为什么.{0,8}(下降|中断|异常|变化)|导致|攻击|政策行为|配置错误|基础设施故障/.test(
      lower,
    )
  ) {
    return answerCause(context)
  }
  if (
    /最低点?.{0,12}(比|与|相对).{0,8}起点.{0,8}(少|差|下降)|比起点.{0,12}(少|差|下降)|起点.{0,12}最低点?.{0,8}(变化|差|下降)/.test(
      lower,
    )
  ) {
    return answerVisibility(context)
  }
  if (
    anchor &&
    /这|该|这里|就此|这个|怎么来|如何计算|依据|证据|什么意思/.test(
      lower,
    )
  ) {
    return answerAnchor(anchor)
  }
  if (/覆盖率.{0,8}(意思|含义|定义|计算|怎么算|分母)/.test(lower)) {
    return answerCoverageSemantics(context)
  }
  if (
    /(全不可见|部分可见).{0,8}(意思|含义|定义)|asn.{0,6}(状态|分类).{0,6}(意思|含义)/.test(
      lower,
    )
  ) {
    return answerAsnStateSemantics()
  }
  if (
    /update.{0,8}(意思|含义|定义)|announce|withdraw.{0,8}(意思|含义|定义)/i.test(
      lower,
    )
  ) {
    return answerUpdateSemantics()
  }
  if (
    /(\/24|\/48).{0,8}(意思|含义|等价)|等价资源.{0,8}(意思|含义|定义)/.test(
      lower,
    )
  ) {
    return answerResourceSemantics()
  }
  const asnMatch = lower.match(/\bas\s*([0-9]{1,10})\b/i)
  if (asnMatch?.[1]) {
    return answerSpecificAsn(context, Number(asnMatch[1]))
  }
  if (/哪些.{0,4}asn|持续.{0,6}(最久|最长)|asn.{0,8}(排行|排名)/.test(lower)) {
    return answerTopAsns(context)
  }
  if (/asn.{0,8}(峰值|多少)|全不可见.{0,6}(峰值|多少)|部分可见.{0,6}(峰值|多少)/.test(lower)) {
    return answerAsnPeak(context)
  }
  if (/ipv4|ipv6|地址族/.test(lower)) {
    return answerAddressFamilies(context)
  }
  if (/update|announce|withdraw|更新活动/.test(lower)) {
    return answerUpdateActivity(context)
  }
  if (/国家级路由资源|等价资源|\/24|\/48/.test(lower)) {
    return answerResources(context)
  }
  if (/最大.{0,4}(单槽|下降)|单槽.{0,4}下降/.test(lower)) {
    return answerLargestDrop(context)
  }
  if (/最低点|最低覆盖率|什么时候最低/.test(lower)) {
    return answerLowest(context)
  }
  if (/窗口.{0,4}(起点|开始)|起点覆盖率/.test(lower)) {
    return answerStart(context)
  }
  if (
    /窗口.{0,4}(结束|末尾)|结束覆盖率|结束时.{0,6}(状态|多少|恢复了吗)/.test(
      lower,
    )
  ) {
    return answerEnd(context)
  }
  if (/回升.{0,6}(多少|比例|幅度)|恢复了多少|损失.{0,6}恢复/.test(lower)) {
    return answerRecoveryWithinWindow(context)
  }
  if (/观测范围|固定范围|多少.{0,4}(asn|关系)|统计范围/.test(lower)) {
    return answerScope(context)
  }
  if (/可见性|覆盖率|下降过程|发生了什么|主要变化/.test(lower)) {
    return answerVisibility(context)
  }
  if (anchor) return answerAnchor(anchor)
  return draft(
    'insufficient_evidence',
    '当前问题无法仅凭这份报告的固定事实合同形成可靠回答。你可以追问报告中的数字、时间、ASN、指标含义或证据边界；我不会用模型记忆或前一轮假设补足缺失事实。',
    ['overview:/limitations', 'audit:/evidence_level'],
    ['能够直接对应本问题的同快照事实'],
  )
}

function getAtPointer(root: unknown, pointer: string): unknown {
  if (!pointer || pointer === '/') return root
  return pointer
    .split('/')
    .filter(Boolean)
    .reduce<unknown>((value, segment) => {
      if (!value || typeof value !== 'object') return undefined
      return (value as Record<string, unknown>)[segment]
    }, root)
}

function displayUnknown(value: unknown): string | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return JSON.stringify(value)
}

function evidenceRecord(
  ref: string,
  source: QuestionEvidenceSource,
  label: string,
  options: {
    metric?: string | null
    value?: string | null
    observedAtUtc?: string | null
    observedAtLocal?: string | null
  } = {},
): QuestionEvidenceRecord {
  return {
    ref,
    source,
    label,
    metric: options.metric ?? null,
    value: options.value ?? null,
    observedAtUtc: options.observedAtUtc ?? null,
    observedAtLocal: options.observedAtLocal ?? null,
    statisticalScope: STATISTICAL_SCOPE,
  }
}

function resolveReportEvidence(
  ref: string,
  context: CountryOutageQuestionContext,
): QuestionEvidenceRecord {
  if (ref === 'report:/summary') {
    return evidenceRecord(ref, 'report', '报告摘要', {
      value: context.report.draft.summary.text,
    })
  }
  const highlight = ref.match(/^report:\/highlights\/(\d+)$/)
  if (highlight?.[1]) {
    const item = context.report.draft.highlights[Number(highlight[1])]
    return evidenceRecord(ref, 'report', item?.label ?? '报告关键数字', {
      value: item?.value ?? null,
    })
  }
  const paragraph = ref.match(
    /^report:\/sections\/([^/]+)\/paragraphs\/(\d+)$/,
  )
  if (paragraph?.[1] && paragraph[2]) {
    const section = context.report.draft.sections.find(
      (candidate) => candidate.id === paragraph[1],
    )
    const item = section?.paragraphs[Number(paragraph[2])]
    return evidenceRecord(ref, 'report', section?.title ?? '报告段落', {
      value: item?.text ?? null,
    })
  }
  const unknown = ref.match(/^report:\/unknowns\/(\d+)$/)
  if (unknown?.[1]) {
    return evidenceRecord(ref, 'report', '报告不能回答的问题', {
      value: context.report.draft.unknowns[Number(unknown[1])] ?? null,
    })
  }
  return evidenceRecord(ref, 'report', '报告证据位置')
}

function resolveSeriesEvidence(
  ref: string,
  context: CountryOutageQuestionContext,
): QuestionEvidenceRecord {
  const pointer = ref.slice('series:'.length)
  const seriesPoint = pointer.match(/^\/series\/(\d+)$/)
  if (seriesPoint?.[1]) {
    const slot = context.facts.series[Number(seriesPoint[1])]
    return evidenceRecord(ref, 'series', '路由可见性观测槽', {
      metric: 'visible_prefix_vp_count',
      value:
        typeof slot?.visible_prefix_vp_count === 'number'
          ? `${formatInteger(slot.visible_prefix_vp_count)} 条；覆盖率 ${
              typeof slot.visible_prefix_vp_ratio === 'number'
                ? formatPercent(slot.visible_prefix_vp_ratio)
                : '未知'
            }`
          : null,
      observedAtUtc: slot?.observed_at_utc ?? null,
      observedAtLocal: slot?.observed_at_local ?? null,
    })
  }
  const extrema = pointer.match(
    /^\/(metric_extrema|resource_metric_extrema)\/([^/]+)\/(min|max)$/,
  )
  if (extrema?.[1] && extrema[2] && extrema[3]) {
    const root =
      extrema[1] === 'metric_extrema'
        ? context.facts.metricExtrema
        : context.facts.resourceMetricExtrema
    const point = extremaPoint(
      root,
      extrema[2],
      extrema[3] as 'min' | 'max',
    )
    return evidenceRecord(ref, 'series', `${extrema[2]} ${extrema[3]}`, {
      metric: extrema[2],
      value: point ? String(point.value) : null,
      observedAtUtc: point?.observed_at_utc ?? null,
      observedAtLocal: point?.observed_at_local ?? null,
    })
  }
  return evidenceRecord(ref, 'series', '序列证据', {
    value: displayUnknown(
      getAtPointer(
        {
          series: context.facts.series,
          metric_extrema: context.facts.metricExtrema,
          resource_metric_extrema: context.facts.resourceMetricExtrema,
        },
        pointer,
      ),
    ),
  })
}

function resolveOverviewEvidence(
  ref: string,
  context: CountryOutageQuestionContext,
): QuestionEvidenceRecord {
  const pointer = ref.slice('overview:'.length)
  const overviewProjection = {
    event_identity: context.facts.event,
    observation_scope: context.facts.scope,
    cohort: context.facts.cohort,
    capabilities: context.facts.capabilities,
    limitations: context.facts.quality.limitations,
  }
  return evidenceRecord(ref, 'overview', `观测概览 ${pointer}`, {
    value: displayUnknown(getAtPointer(overviewProjection, pointer)),
  })
}

function resolveAsnEvidence(
  ref: string,
  context: CountryOutageQuestionContext,
): QuestionEvidenceRecord {
  const pageReference = ref.match(/^asns:\/pages\/(\d+)\/items\/(\d+)$/)
  if (pageReference?.[1] && pageReference[2]) {
    const page = context.asnPages.find(
      (candidate) => candidate.page === Number(pageReference[1]),
    )
    const item = page?.items[Number(pageReference[2])]
    return evidenceRecord(ref, 'asn_detail', '同快照 ASN 细化事实', {
      metric: 'asn_timeline',
      value: displayUnknown(item),
    })
  }
  const legacyReference = ref.match(/^asns:\/items\/(\d+)$/)
  if (legacyReference?.[1]) {
    const item = flattenedAsnItems(context)[Number(legacyReference[1])]?.item
    return evidenceRecord(ref, 'asn_detail', '同快照 ASN 细化事实', {
      metric: 'asn_timeline',
      value: displayUnknown(item),
    })
  }
  return evidenceRecord(ref, 'asn_detail', '同快照 ASN 细化事实')
}

function resolveEvidence(
  ref: string,
  context: CountryOutageQuestionContext,
): QuestionEvidenceRecord {
  const derived = context.facts.derivedFacts.find(
    (candidate) => candidate.factId === ref,
  )
  if (derived) {
    return evidenceRecord(ref, 'derived_fact', derived.label, {
      metric: derived.metric,
      value: `${derived.value} ${derived.unit}；公式 ${derived.formula}；操作数 ${canonicalJsonStringify(derived.operands)}`,
      observedAtUtc: derived.observedAtUtc ?? null,
      observedAtLocal: derived.observedAtLocal ?? null,
    })
  }
  if (ref.startsWith('report:')) return resolveReportEvidence(ref, context)
  if (ref.startsWith('overview:')) {
    return resolveOverviewEvidence(ref, context)
  }
  if (ref.startsWith('series:')) return resolveSeriesEvidence(ref, context)
  if (ref.startsWith('asns:')) return resolveAsnEvidence(ref, context)
  if (ref.startsWith('audit:')) {
    return evidenceRecord(ref, 'audit', `审计证据 ${ref.slice(6)}`, {
      value:
        ref === 'audit:/evidence_level'
          ? context.facts.audit.evidenceLevel
          : null,
    })
  }
  return evidenceRecord(ref, 'report', '报告已校验证据引用')
}

function validateDraft(value: QuestionAnswerDraft): void {
  if (!value.text.trim()) {
    throw new QuestionAnswerValidationError('回答正文为空')
  }
  if (value.text.length > MAXIMUM_ANSWER_CHARACTERS) {
    throw new QuestionAnswerValidationError(
      `回答超过 ${MAXIMUM_ANSWER_CHARACTERS} 字符`,
    )
  }
  if (value.evidenceRefs.length === 0) {
    throw new QuestionAnswerValidationError('回答缺少 evidence refs')
  }
  if (
    value.kind === 'evidence_boundary' &&
    value.missingEvidence.length === 0
  ) {
    throw new QuestionAnswerValidationError('越界回答没有说明缺少的证据')
  }
}

export function suggestedQuestions(
  context: CountryOutageQuestionContext,
): SuggestedQuestion[] {
  assertContext(context)
  const suggestions: SuggestedQuestion[] = [
    {
      id: 'visibility-change',
      question: '窗口内路由可见性怎样变化？',
      capability: 'fixed_cohort',
    },
    {
      id: 'end-state',
      question: '窗口结束时恢复到起点水平了吗？',
      capability: 'fixed_cohort',
    },
    {
      id: 'prefix-vp-semantics',
      question: 'Prefix×VP 是什么意思？',
      capability: 'fixed_cohort',
    },
  ]
  const capabilities = context.facts.capabilities
  if (capabilities.asn_matrix?.state === 'available') {
    suggestions.push({
      id: 'asn-duration',
      question: '哪些 ASN 的全不可见状态持续时间较长？',
      capability: 'asn_matrix',
    })
  }
  if (capabilities.address_families?.state === 'available') {
    suggestions.push({
      id: 'address-families',
      question: 'IPv4 和 IPv6 的可见性变化有什么不同？',
      capability: 'address_families',
    })
  }
  if (capabilities.update_activity?.state === 'available') {
    suggestions.push({
      id: 'update-timing',
      question: 'UPDATE 峰值与可见性下降在时间上有什么关系？',
      capability: 'update_activity',
    })
  }
  if (capabilities.country_resources?.state === 'available') {
    suggestions.push({
      id: 'country-resources',
      question: '国家级等价路由资源怎样变化？',
      capability: 'country_resources',
    })
  }
  return suggestions
}

export class DeterministicCountryOutageQuestionEngine {
  async answer(
    request: CountryOutageQuestionRequest,
    context: CountryOutageQuestionContext,
    options: AnswerQuestionOptions = {},
  ): Promise<CountryOutageQuestionAnswer> {
    assertNotAborted(options.signal)
    assertContext(context)
    const question = assertRequest(request, context)
    const anchor = resolveAnchor(request.binding.anchor, context)

    // 给调用方一个在确定性规划开始前取消的检查点。
    await Promise.resolve()
    assertNotAborted(options.signal)

    const answerDraft = selectAnswer(question, anchor, context)
    validateDraft(answerDraft)
    assertNotAborted(options.signal)

    const idempotencyFingerprint = canonicalJsonSha256({
      idempotencyKey: request.idempotencyKey,
      binding: request.binding,
      question,
    })
    const evidenceRefs = [...new Set(answerDraft.evidenceRefs)]
    return {
      schemaVersion: 'country_outage_question_answer_v1',
      answerId: `answer_${idempotencyFingerprint.slice(0, 32)}`,
      idempotencyFingerprint,
      requestId: request.requestId,
      binding: request.binding,
      snapshot: context.facts.snapshot,
      evidenceMode: DOMEYE_ONLY_EVIDENCE_MODE,
      evidenceModeLabel: DOMEYE_ONLY_EVIDENCE_MODE_LABEL,
      kind: answerDraft.kind,
      text: answerDraft.text,
      evidenceRefs,
      evidence: evidenceRefs.map((ref) => resolveEvidence(ref, context)),
      missingEvidence: [...answerDraft.missingEvidence],
    }
  }
}
