import { describe, expect, it } from 'vitest'

import type { CountryOutageExternalEvidencePolicy } from '@/api/countryOutageAgent'
import type { EventObservation } from '@/types/api'
import {
  answerFitsPublishedLimit,
  artifactByFormat,
  closeAnsweringQuestionsAtSessionExpiry,
  countryOutagePagePreflightChecks,
  countryOutageArtifactStateLabel,
  countryOutageViewForTabKey,
  externalEvidenceUrlFocusTargetAfterRemoval,
  formatRemainingTime,
  LogicalSubmissionIdempotency,
  logicalSubmissionFingerprint,
  observationAnchorForEvidence,
  reportStagePresentation,
  safeExternalEvidenceHref,
  sessionSecondsRemaining,
  shouldIgnoreLateAgentEvent,
  suggestedReportQuestions,
  validateExternalEvidenceUrl,
  validateExternalEvidenceUrls,
} from './countryOutageReport'

function externalPolicy(
  overrides: Partial<CountryOutageExternalEvidencePolicy> = {},
): CountryOutageExternalEvidencePolicy {
  return {
    version: 'country-outage-external-v1',
    sha256: 'a'.repeat(64),
    allowed_host_roots: [
      'bgp.he.net',
      'radar.cloudflare.com',
    ],
    minimum_urls: 1,
    maximum_urls: 5,
    ...overrides,
  }
}

function observation(
  capabilities: EventObservation['capabilities'] = {},
): EventObservation {
  return {
    capabilities,
    asn_state: {
      state_codes: {},
      observed_at_utc: [],
      observed_at_local: [],
      timelines: [],
    },
    country_update_series: [],
    resource_series: [],
  } as unknown as EventObservation
}

function validPreflightObservation(): EventObservation {
  const points = [
    {
      observed_at_utc: '2026-02-28T10:05:00Z',
      visible_prefix_vp_count: 95,
      invisible_prefix_vp_count: 5,
      visible_prefix_vp_ratio: 0.95,
      visible_prefix_vp_delta: null,
      visible_prefix_vp_ratio_delta_pp: null,
      visible_origin_asn_count: 9,
      visible_origin_asn_ratio: 0.9,
      visible_origin_asn_delta: null,
      fully_visible_asn_count: 8,
      partially_visible_asn_count: 1,
      fully_invisible_asn_count: 1,
      non_fully_visible_asn_count: 2,
    },
    {
      observed_at_utc: '2026-02-28T10:10:00Z',
      visible_prefix_vp_count: 80,
      invisible_prefix_vp_count: 20,
      visible_prefix_vp_ratio: 0.8,
      visible_prefix_vp_delta: -15,
      visible_prefix_vp_ratio_delta_pp: -15,
      visible_origin_asn_count: 8,
      visible_origin_asn_ratio: 0.8,
      visible_origin_asn_delta: -1,
      fully_visible_asn_count: 7,
      partially_visible_asn_count: 1,
      fully_invisible_asn_count: 2,
      non_fully_visible_asn_count: 3,
    },
    {
      observed_at_utc: '2026-02-28T10:15:00Z',
      visible_prefix_vp_count: 85,
      invisible_prefix_vp_count: 15,
      visible_prefix_vp_ratio: 0.85,
      visible_prefix_vp_delta: 5,
      visible_prefix_vp_ratio_delta_pp: 5,
      visible_origin_asn_count: 9,
      visible_origin_asn_ratio: 0.9,
      visible_origin_asn_delta: 1,
      fully_visible_asn_count: 8,
      partially_visible_asn_count: 1,
      fully_invisible_asn_count: 1,
      non_fully_visible_asn_count: 2,
    },
  ].map((point) => ({
    ...point,
    slot_state: 'observed',
    missing_reason: null,
  }))
  return {
    schema_version: 'country_outage_observation_v2',
    revision: 1,
    publication_id: 'publication-v1',
    publication_state: 'published',
    data_through: '2026-02-28T10:15:00Z',
    missing_slot_count: 0,
    incident_id: 'incident-ir',
    cohort_id: 'cohort-ir',
    window_start_utc: '2026-02-28T10:05:00Z',
    window_end_utc: '2026-02-28T10:15:00Z',
    event_identity: {
      incident_id: 'incident-ir',
      event_type: 'country_outage',
      country_code: 'IR',
      country_name: '伊朗',
      display_name: '伊朗国家中断观测',
    },
    observation_scope: {
      collector_id: 'rrc25',
      collector_ids: ['rrc25'],
      collector_count: 1,
      window_start_utc: '2026-02-28T10:05:00Z',
      window_end_utc: '2026-02-28T10:15:00Z',
      interval_seconds: 300,
      observation_count: 3,
      expected_observation_count: 3,
      missing_observation_count: 0,
      quality_status: 'pass',
      last_observation_at_utc: '2026-02-28T10:15:00Z',
    },
    cohort: {
      cohort_id: 'cohort-ir',
      prefix_vp_count: 100,
      origin_asn_count: 10,
      denominator_policy: 'fixed',
    },
    series: points,
    audit: {
      schema_version: 'country_outage_audit_v2',
      quality_status: 'pass',
      consumed_deliverable_hashes_verified: true,
      publication_id: 'publication-v1',
      revision: 1,
      incident_id: 'incident-ir',
      cohort_id: 'cohort-ir',
      window_start_utc: '2026-02-28T10:05:00Z',
      window_end_utc: '2026-02-28T10:15:00Z',
      missing_slot_count: 0,
    },
    capabilities: {},
    asn_state: {
      state_codes: {},
      observed_at_utc: [],
      observed_at_local: [],
      timelines: [],
    },
    country_update_series: [],
    resource_series: [],
  } as unknown as EventObservation
}

describe('国家中断报告工作台状态', () => {
  it('报告响应丢失后用户重试复用同一 key，成功或确定拒绝后清除', () => {
    let sequence = 0
    const state = new LogicalSubmissionIdempotency(
      () => `report-key-${++sequence}`,
    )
    const revisionOne = logicalSubmissionFingerprint({
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication-v1',
      revision: 1,
    })

    const lostResponse = state.begin(revisionOne)
    state.settle(lostResponse, 'outcome_uncertain')
    const userRetry = state.begin(revisionOne)
    expect(userRetry.idempotencyKey).toBe(lostResponse.idempotencyKey)

    const deduplicatedSuccess = { deduplicated: true }
    expect(deduplicatedSuccess.deduplicated).toBe(true)
    state.settle(userRetry, 'accepted')
    const afterSuccess = state.begin(revisionOne)
    expect(afterSuccess.idempotencyKey).not.toBe(userRetry.idempotencyKey)

    state.settle(afterSuccess, 'deterministic_rejection')
    const afterDeterministicRejection = state.begin(revisionOne)
    expect(afterDeterministicRejection.idempotencyKey)
      .not.toBe(afterSuccess.idempotencyKey)

    state.settle(afterDeterministicRejection, 'outcome_uncertain')
    const revisionTwo = state.begin(logicalSubmissionFingerprint({
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication-v2',
      revision: 2,
    }))
    expect(revisionTwo.idempotencyKey)
      .not.toBe(afterDeterministicRejection.idempotencyKey)
  })

  it('追问响应丢失后同输入复用 key，问题、引用或授权范围变化时换 key', () => {
    let sequence = 0
    const state = new LogicalSubmissionIdempotency(
      () => `question-key-${++sequence}`,
    )
    const fingerprint = (
      question: string,
      options: {
        quote?: string
        authorizationAt?: string
        urls?: string[]
      } = {},
    ) => logicalSubmissionFingerprint({
      report_id: 'report-1',
      publication_id: 'publication-v1',
      revision: 1,
      data_through: '2026-02-28T15:00:00Z',
      question,
      evidence_mode: options.authorizationAt
        ? 'domeye_plus_external'
        : 'domeye_only',
      quote: options.quote ?? null,
      external_authorization_at: options.authorizationAt ?? null,
      external_urls: options.urls ?? [],
    })

    const original = fingerprint('最低点是什么时候？')
    const lostResponse = state.begin(original)
    state.settle(lostResponse, 'outcome_uncertain')
    const userRetry = state.begin(original)
    expect(userRetry.idempotencyKey).toBe(lostResponse.idempotencyKey)

    const deduplicatedSuccess = { deduplicated: true }
    expect(deduplicatedSuccess.deduplicated).toBe(true)
    state.settle(userRetry, 'accepted')

    const deterministicFailure = state.begin(original)
    state.settle(deterministicFailure, 'deterministic_rejection')
    expect(state.begin(original).idempotencyKey)
      .not.toBe(deterministicFailure.idempotencyKey)

    const pendingOriginal = state.begin(original)
    state.settle(pendingOriginal, 'outcome_uncertain')
    const changedQuestion = state.begin(fingerprint('结束时恢复了吗？'))
    expect(changedQuestion.idempotencyKey)
      .not.toBe(pendingOriginal.idempotencyKey)

    state.settle(changedQuestion, 'outcome_uncertain')
    const changedQuote = state.begin(fingerprint(
      '结束时恢复了吗？',
      { quote: 'section:end-state:0' },
    ))
    expect(changedQuote.idempotencyKey)
      .not.toBe(changedQuestion.idempotencyKey)

    state.settle(changedQuote, 'outcome_uncertain')
    const changedAuthorization = state.begin(fingerprint(
      '结束时恢复了吗？',
      {
        quote: 'section:end-state:0',
        authorizationAt: '2026-07-29T10:00:00Z',
        urls: ['https://bgp.he.net/country/IR'],
      },
    ))
    expect(changedAuthorization.idempotencyKey)
      .not.toBe(changedQuote.idempotencyKey)
  })

  it('只展示受控生成阶段，不暴露模型内部过程', () => {
    expect(reportStagePresentation('reading_data')).toEqual({
      index: '02',
      label: '读取数据',
      detail: '正在固定 RRC25 发布快照并组装确定性事实。',
    })
    expect(reportStagePresentation('validating').label).toBe('校验')
    expect(reportStagePresentation('failed').detail)
      .toContain('没有发布草稿')
  })

  it('按冻结的五分钟门槛计算到期提醒', () => {
    const now = Date.parse('2026-07-28T14:00:00Z')
    expect(sessionSecondsRemaining('2026-07-28T14:05:00Z', now)).toBe(300)
    expect(formatRemainingTime(300)).toBe('05:00')
    expect(sessionSecondsRemaining('2026-07-28T13:59:00Z', now)).toBe(0)
  })

  it('外层视图 tab 支持循环方向键以及 Home 和 End', () => {
    expect(countryOutageViewForTabKey('observation', 'ArrowRight')).toBe('report')
    expect(countryOutageViewForTabKey('report', 'ArrowRight')).toBe('observation')
    expect(countryOutageViewForTabKey('observation', 'ArrowLeft')).toBe('report')
    expect(countryOutageViewForTabKey('report', 'Home')).toBe('observation')
    expect(countryOutageViewForTabKey('observation', 'End')).toBe('report')
    expect(countryOutageViewForTabKey('report', 'Enter')).toBeNull()
  })

  it('会话到期时只收口仍在回答中的记录', () => {
    const questions = [
      { state: 'answering' as const },
      { state: 'completed' as const },
    ]
    expect(closeAnsweringQuestionsAtSessionExpiry(questions)).toBe(1)
    expect(questions[0]).toMatchObject({
      state: 'failed',
      error: '短期会话已到期，本次回答未发布。',
      nextAction: '请基于当前合法快照重新生成报告后再提问。',
    })
    expect(questions[1]).toEqual({ state: 'completed' })
  })

  it('取消请求和终态都拒绝迟到完成事件，重复终态保持幂等', () => {
    expect(
      shouldIgnoreLateAgentEvent('answering', 'completed', true),
    ).toBe(true)
    expect(
      shouldIgnoreLateAgentEvent('cancelled', 'completed'),
    ).toBe(true)
    expect(
      shouldIgnoreLateAgentEvent('cancelled', 'cancelled'),
    ).toBe(false)
    expect(
      shouldIgnoreLateAgentEvent('completed', 'failed'),
    ).toBe(true)
    expect(
      shouldIgnoreLateAgentEvent('reading_data', 'generating_report'),
    ).toBe(false)
  })

  it('外部证据 URL 只接受 readiness 策略声明的主机族', () => {
    const policy = externalPolicy()
    expect(validateExternalEvidenceUrl(
      'https://bgp.he.net/report#prompt',
      policy,
    ))
      .toEqual({
        valid: true,
        normalized: 'https://bgp.he.net/report',
      })
    expect(
      validateExternalEvidenceUrl(
        'https://RrC.Deep.BgP.He.NeT./report',
        policy,
      ),
    ).toEqual({
      valid: true,
      normalized: 'https://rrc.deep.bgp.he.net/report',
    })
    expect(
      validateExternalEvidenceUrl(
        'https://api.deep.radar.cloudflare.com/report',
        policy,
      ).valid,
    ).toBe(true)
    for (const value of [
      'javascript:alert(1)',
      'file:///etc/passwd',
      'http://localhost/admin',
      'http://127.0.0.1/',
      'http://169.254.169.254/latest',
      'https://bgp.he.net:8443/report',
      'https://user:secret@bgp.he.net/',
      'https://example.com/report',
      'https://bgp.he.net.evil.example/report',
      'https://evilbgp.he.net/report',
      'https://radar.cloudflare.com.evil.example/report',
      'https://cloudflare-radar.example/report',
    ]) {
      expect(validateExternalEvidenceUrl(value, policy).valid).toBe(false)
      expect(safeExternalEvidenceHref(value, policy)).toBeNull()
    }
  })

  it('外部模式的 URL 数量完全跟随 readiness 策略', () => {
    const policy = externalPolicy()
    expect(validateExternalEvidenceUrls([''], policy)).toEqual({
      urls: [],
      fieldErrors: {},
      globalError: '请至少提供 1 个当前策略允许的公开 URL，再明确确认读取。',
    })

    expect(validateExternalEvidenceUrls(
      ['https://bgp.he.net/only'],
      policy,
    ))
      .toEqual({
        urls: ['https://bgp.he.net/only'],
        fieldErrors: {},
      })

    const fiveUrls = Array.from(
      { length: 5 },
      (_, index) => `https://radar.cloudflare.com/source-${index + 1}`,
    )
    expect(validateExternalEvidenceUrls(fiveUrls, policy)).toEqual({
      urls: fiveUrls,
      fieldErrors: {},
    })

    const invalid = validateExternalEvidenceUrls(
      ['http://127.0.0.1/private'],
      policy,
    )
    expect(invalid.urls).toEqual([])
    expect(invalid.fieldErrors[0]).toContain('内网')
    expect(invalid.globalError)
      .toBe('请至少提供 1 个当前策略允许的公开 URL，再明确确认读取。')

    expect(validateExternalEvidenceUrls(
      Array.from(
        { length: 6 },
        (_, index) => `https://bgp.he.net/${index}`,
      ),
      policy,
    ).globalError).toContain('最多 5 个')

    const narrowed = externalPolicy({
      allowed_host_roots: ['evidence.example'],
      minimum_urls: 2,
      maximum_urls: 7,
    })
    expect(validateExternalEvidenceUrls(
      ['https://evidence.example/one'],
      narrowed,
    ).globalError).toContain('至少提供 2 个')
    expect(validateExternalEvidenceUrls(
      [
        'https://evidence.example/one',
        'https://sub.evidence.example/two',
      ],
      narrowed,
    )).toEqual({
      urls: [
        'https://evidence.example/one',
        'https://sub.evidence.example/two',
      ],
      fieldErrors: {},
    })
    expect(validateExternalEvidenceUrl(
      'https://bgp.he.net/no-longer-allowed',
      narrowed,
    ).valid).toBe(false)
  })

  it('删除外部 URL 后优先选择下一项，其次前一项，删空后返回添加按钮', () => {
    expect(externalEvidenceUrlFocusTargetAfterRemoval(2, 1)).toEqual({
      kind: 'url',
      index: 1,
    })
    expect(externalEvidenceUrlFocusTargetAfterRemoval(1, 1)).toEqual({
      kind: 'url',
      index: 0,
    })
    expect(externalEvidenceUrlFocusTargetAfterRemoval(0, 0)).toEqual({
      kind: 'add',
    })
  })

  it('外部 URL 去重后仍保留可用来源并暴露重复错误', () => {
    const duplicate = validateExternalEvidenceUrls([
      'https://bgp.he.net/a',
      'https://BGP.HE.NET./a#other',
    ], externalPolicy())
    expect(duplicate.urls).toEqual(['https://bgp.he.net/a'])
    expect(duplicate.fieldErrors[1]).toContain('重复')
  })

  it('将事实定位返回对应的数据观测区域', () => {
    expect(observationAnchorForEvidence(['series:/asn_state']))
      .toBe('observation-asn')
    expect(observationAnchorForEvidence(['series:/update_total']))
      .toBe('observation-updates')
    expect(observationAnchorForEvidence(['series:/resource_series']))
      .toBe('observation-resources')
    expect(observationAnchorForEvidence(['series:/visibility']))
      .toBe('observation-visibility')
  })

  it('建议问题只随当前快照真实能力出现', () => {
    const baseline = suggestedReportQuestions(observation())
    expect(baseline).toHaveLength(2)
    expect(baseline.join(' ')).not.toContain('原因')

    const extended = suggestedReportQuestions(observation({
      asn_matrix: { state: 'available' },
      country_update_activity: { state: 'available' },
    }))
    expect(extended).toContain('哪些 ASN 的全不可见状态持续时间最长？')
    expect(extended).toContain('UPDATE 峰值与可见性下降在时间上如何对应？')

    const residual = observation({
      asn_matrix: { state: 'unavailable' },
      country_update_activity: { state: 'unavailable' },
    })
    residual.asn_state.timelines = [{}] as never[]
    residual.country_update_series = [{ update_total: 10 }] as never[]
    residual.resource_series = [{ update_total: 10 }] as never[]
    expect(suggestedReportQuestions(residual)).toHaveLength(2)
  })

  it('页面预检要求 v2 发布身份、完整时间网格、显式 observed 核心值和审计质量', () => {
    const valid = validPreflightObservation()
    expect(countryOutagePagePreflightChecks(valid).every(
      (check) => check.passed,
    )).toBe(true)

    const missingState = structuredClone(valid)
    delete (
      missingState.series[1] as unknown as Record<string, unknown>
    ).slot_state
    const missingStateChecks = countryOutagePagePreflightChecks(missingState)
    expect(missingStateChecks.find(
      (check) => check.label.includes('核心可见性'),
    )?.passed).toBe(false)

    const shortened = structuredClone(valid)
    shortened.series = shortened.series.slice(1)
    const shortenedChecks = countryOutagePagePreflightChecks(shortened)
    expect(shortenedChecks.find(
      (check) => check.label.includes('完整时间网格'),
    )?.passed).toBe(false)
    expect(shortenedChecks.find(
      (check) => check.label.includes('首尾精确'),
    )?.passed).toBe(false)

    const badAudit = structuredClone(valid)
    badAudit.audit!.quality_status = 'failed'
    expect(countryOutagePagePreflightChecks(badAudit).find(
      (check) => check.label.includes('审计质量'),
    )?.passed).toBe(false)
  })

  it('PDF 失败不会覆盖可下载的 Markdown', () => {
    const artifacts = [
      {
        format: 'markdown' as const,
        status: 'ready' as const,
        artifact_id: 'artifact-1',
        filename: 'IR_country-outage.md',
        media_type: 'text/markdown',
        byte_length: 1_024,
        sha256: 'a'.repeat(64),
      },
      {
        format: 'pdf' as const,
        status: 'failed' as const,
        code: 'PDF_FAILED',
        message: 'PDF 生成失败',
      },
    ]
    expect(artifactByFormat(artifacts, 'markdown')?.status).toBe('ready')
    expect(artifactByFormat(artifacts, 'pdf')?.status).toBe('failed')
    expect(countryOutageArtifactStateLabel(artifacts[0], {
      sessionExpired: true,
      hasReport: true,
    })).toBe('会话已到期，需重新生成')
  })

  it('超过 4,000 字符的回答不能进入正式研读记录', () => {
    expect(answerFitsPublishedLimit('可核对的正式回答')).toBe(true)
    expect(answerFitsPublishedLimit('')).toBe(false)
    expect(answerFitsPublishedLimit('答'.repeat(4_000))).toBe(true)
    expect(answerFitsPublishedLimit('答'.repeat(4_001))).toBe(false)
  })
})
