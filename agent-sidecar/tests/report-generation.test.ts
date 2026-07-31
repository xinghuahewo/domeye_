import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import test from 'node:test'

import type {
  CountryOutageAsnPage,
  ObservationBatch,
} from '../src/domain/contracts.js'
import { assembleCountryOutageFacts } from '../src/domain/observation-assembler.js'
import { CountryOutageEvidenceCapacityError } from '../src/formal-runtime-limits.js'
import {
  computeCountryOutageSkillBundleSha256,
  COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
} from '../src/pi/country-outage-skill-bundle.js'
import {
  createCountryOutageReportAuditManifestArtifact,
  describeCountryOutageReportAuditManifestArtifact,
} from '../src/report/audit-manifest.js'
import { DeterministicAcceptanceNarrator } from '../src/report/deterministic-narrator.js'
import {
  COUNTRY_OUTAGE_REPORT_DRAFT_TEXT_DIAGNOSTICS,
  parseReportDraft,
  parseReportDraftText,
  ReportDraftTextParseError,
  validateReportDraft,
} from '../src/report/draft-validator.js'
import { renderReportMarkdown } from '../src/report/markdown-renderer.js'
import {
  CountryOutageReportCompiler,
  ReportValidationError,
} from '../src/report/report-compiler.js'
import { iranReferenceResourceSeries } from './helpers/iran-reference-resource-series.js'
import { iranReferenceVisibilitySeries } from './helpers/iran-reference-visibility-series.js'

const incidentId = 'incident-report-test'
const publicationId = 'publication-report-test'
const cohortId = 'cohort-report-test'

function reportBatch(): ObservationBatch {
  const envelope = {
    incident_id: incidentId,
    publication_id: publicationId,
    publication_state: 'published',
    observation_state: 'state_complete',
    revision: 1,
    data_through: '2026-02-28T15:00:00Z',
    is_final: true,
    window_start_utc: '2026-02-28T10:05:00Z',
    window_end_utc: '2026-02-28T15:00:00Z',
    cohort_id: cohortId,
  }
  const points = iranReferenceVisibilitySeries()
  const resourcePoints = iranReferenceResourceSeries()
  const extremaPoint = (
    metric: string,
    value: number,
    observed_at_local: string,
  ) => ({
    metric,
    value,
    observed_at_local,
    observed_at_utc: new Date(observed_at_local)
      .toISOString()
      .replace('.000Z', 'Z'),
  })
  return {
    resolution: {
      schema_version: 'country_outage_resolution_v2',
      incident_id: incidentId,
      publication_id: publicationId,
      legacy_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      event_type: 'country_outage',
      observation_state: 'state_complete',
      latest_revision: 1,
      data_mode: 'replay',
      data_through: '2026-02-28T15:00:00Z',
      is_final: true,
      missing_slot_count: 0,
      capability_contract_version: 'country_outage_capabilities_v1',
      capabilities: {},
    },
    overview: {
      ...envelope,
      schema_version: 'country_outage_overview_v2',
      event_identity: {
        incident_id: incidentId,
        legacy_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
        event_type: 'country_outage',
        country_code: 'IR',
        country_name: '伊朗',
        display_name: '伊朗 BGP 路由观测',
      },
      observation_scope: {
        collector_id: 'rrc25',
        collector_ids: ['rrc25'],
        collector_count: 1,
        window_start_utc: '2026-02-28T10:05:00Z',
        window_start_local: '2026-02-28T18:05:00+08:00',
        window_end_utc: '2026-02-28T15:00:00Z',
        window_end_local: '2026-02-28T23:00:00+08:00',
        timezone: 'Asia/Shanghai',
        interval_seconds: 300,
        observation_count: 60,
        expected_observation_count: 60,
        missing_observation_count: 0,
        quality_status: 'pass',
        last_observation_at_utc: '2026-02-28T15:00:00Z',
        last_observation_at_local: '2026-02-28T23:00:00+08:00',
        right_boundary: '窗口结束后无本页同口径状态',
      },
      cohort: {
        cohort_id: cohortId,
        denominator_policy: 'fixed_from_complete_rib',
        origin_asn_count: 563,
        prefix_vp_count: 384767,
        ipv4_prefix_vp_count: 383804,
        ipv6_prefix_vp_count: 963,
      },
      capabilities: {
        fixed_cohort: { state: 'available' },
        asn_matrix: { state: 'available' },
        address_families: { state: 'available' },
        update_activity: { state: 'available' },
        country_resources: { state: 'available' },
        normal_band: {
          state: 'unavailable',
          reason: '缺少可信正常参照',
        },
      },
      capability_contract_version: 'country_outage_capabilities_v1',
      missing_slot_count: 0,
      processing_status: { state: 'final' },
      limitations: ['仅为 RRC25 BGP 控制面观测。'],
    },
    series: {
      ...envelope,
      schema_version: 'country_outage_series_v2',
      interval_seconds: 300,
      missing_slot_count: 0,
      metric_definitions: [],
      series: points,
      metric_extrema: {
        fully_invisible_asn_count: {
          max: extremaPoint(
            'fully_invisible_asn_count',
            87,
            '2026-02-28T21:50:00+08:00',
          ),
        },
        partially_visible_asn_count: {
          max: extremaPoint(
            'partially_visible_asn_count',
            188,
            '2026-02-28T18:40:00+08:00',
          ),
        },
        ipv4_visible_prefix_vp_ratio: {
          min: extremaPoint(
            'ipv4_visible_prefix_vp_ratio',
            0.8228522891892737,
            '2026-02-28T22:35:00+08:00',
          ),
        },
        ipv6_visible_prefix_vp_ratio: {
          min: extremaPoint(
            'ipv6_visible_prefix_vp_ratio',
            0.9532710280373832,
            '2026-02-28T22:50:00+08:00',
          ),
        },
      },
      resource_series: resourcePoints,
      resource_metric_extrema: {
        update_total: {
          max: extremaPoint(
            'update_total',
            340960,
            '2026-02-28T18:25:00+08:00',
          ),
        },
        announce_count: {
          max: extremaPoint(
            'announce_count',
            298812,
            '2026-02-28T18:25:00+08:00',
          ),
        },
        withdraw_count: {
          max: extremaPoint(
            'withdraw_count',
            42148,
            '2026-02-28T18:25:00+08:00',
          ),
        },
        ipv4_24_equivalent_count: {
          max: extremaPoint(
            'ipv4_24_equivalent_count',
            39260,
            '2026-02-28T18:20:00+08:00',
          ),
          min: extremaPoint(
            'ipv4_24_equivalent_count',
            37379,
            '2026-02-28T22:30:00+08:00',
          ),
        },
      },
      annotations: [],
    },
    audit: {
      ...envelope,
      schema_version: 'country_outage_audit_v2',
      quality_status: 'pass',
      missing_slot_count: 0,
      missing_slots: [],
      source_system: 'country_outage_observation_package',
      source_reference: incidentId,
      evidence_level: 'aggregated_route_state_with_artifact_hashes',
      algorithm_version: 'test/1',
      mapping_version: 'mapping-test',
      verified_hashes: { 'cohort.json': 'abc123' },
    },
  } as ObservationBatch
}

function asnPage(): CountryOutageAsnPage {
  return {
    schema_version: 'country_outage_asn_page_v2',
    incident_id: incidentId,
    publication_id: publicationId,
    publication_state: 'published',
    observation_state: 'state_complete',
    revision: 1,
    data_through: '2026-02-28T15:00:00Z',
    is_final: true,
    window_start_utc: '2026-02-28T10:05:00Z',
    window_end_utc: '2026-02-28T15:00:00Z',
    cohort_id: cohortId,
    page: 1,
    page_size: 2,
    page_count: 282,
    total: 563,
    items: [
      {
        asn: 34369,
        longest_fully_invisible_slots: 60,
        baseline_prefix_vp_count: 10,
      },
      {
        asn: 51554,
        longest_fully_invisible_slots: 60,
        baseline_prefix_vp_count: 20,
      },
    ],
  }
}

function directionalReportBatch(
  counts: (index: number, slotCount: number) => number,
): ObservationBatch {
  const batch = reportBatch()
  const cohort = batch.overview.cohort
  assert.ok(cohort)
  const denominator = cohort.prefix_vp_count
  const sourceSeries = batch.series.series
  batch.series.series = sourceSeries.map((slot, index) => {
    const {
      visible_prefix_vp_delta: _discardedDelta,
      visible_prefix_vp_ratio_delta_pp: _discardedRatioDelta,
      ...rest
    } = slot
    const count = counts(index, sourceSeries.length)
    const previousCount =
      index === 0 ? undefined : counts(index - 1, sourceSeries.length)
    return {
      ...rest,
      visible_prefix_vp_count: count,
      visible_prefix_vp_ratio: count / denominator,
      ...(previousCount === undefined
        ? {}
        : {
            visible_prefix_vp_delta: count - previousCount,
            visible_prefix_vp_ratio_delta_pp:
              (count - previousCount) / denominator * 100,
          }),
    }
  })
  batch.overview.capabilities = {
    fixed_cohort: { state: 'available' },
    asn_matrix: {
      state: 'not_applicable',
      reason: '方向合同测试不需要 ASN 扩展能力',
    },
    address_families: {
      state: 'not_applicable',
      reason: '方向合同测试不需要地址族扩展能力',
    },
    update_activity: {
      state: 'not_applicable',
      reason: '方向合同测试不需要 UPDATE 扩展能力',
    },
    country_resources: {
      state: 'not_applicable',
      reason: '方向合同测试不需要资源扩展能力',
    },
    normal_band: {
      state: 'unavailable',
      reason: '缺少可信正常参照',
    },
  }
  batch.series.metric_extrema = {}
  batch.series.resource_series = []
  batch.series.resource_metric_extrema = {}
  return batch
}

function nonFinalReportBatch(): ObservationBatch {
  const batch = reportBatch()
  batch.resolution.is_final = false
  batch.overview.is_final = false
  batch.series.is_final = false
  batch.audit.is_final = false
  batch.overview.processing_status = { state: 'updating' }
  return batch
}

function nonFinalAsnPage(): CountryOutageAsnPage {
  const page = asnPage()
  page.is_final = false
  return page
}

test('确定性验收叙事通过数字、证据和边界校验', async () => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const evidence = { facts, asnPages: [asnPage()] }
  const narrator = new DeterministicAcceptanceNarrator()
  const draft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence,
  })
  const validation = validateReportDraft(draft, evidence)
  assert.equal(validation.passed, true, validation.errors.join('\n'))
  assert.match(draft.title, /伊朗 BGP 路由可见性观测报告/)
  assert.match(draft.summary.text, /95\.44%/)
  assert.match(draft.summary.text, /82\.32%/)
  assert.ok(draft.sections.some((section) => section.id === 'asn_scope'))
  assert.ok(draft.unknowns.some((item) => item.includes('全国性')))
})

test('正式草稿标题和摘要绑定冻结国家显示身份且使用中文叙事', async (context) => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const evidence = { facts, asnPages: [asnPage()] }
  const narrator = new DeterministicAcceptanceNarrator()
  const validDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence,
  })
  const cases: Array<{
    name: string
    expectedError: string
    mutate: (draft: typeof validDraft) => void
  }> = [
    {
      name: '标题不能替换为其他国家',
      expectedError:
        '报告标题没有绑定冻结事件的 country_name/display_name',
      mutate(draft) {
        draft.title = '伊拉克 BGP 路由可见性观测报告'
      },
    },
    {
      name: '标题不能丢失冻结 display_name 的观测对象',
      expectedError:
        '报告标题没有绑定冻结事件的 country_name/display_name',
      mutate(draft) {
        draft.title = '伊朗网络报告'
      },
    },
    {
      name: '摘要不能替换为其他国家',
      expectedError: '报告摘要没有绑定冻结事件的 country_name',
      mutate(draft) {
        draft.summary.text = draft.summary.text.replaceAll('伊朗', '伊拉克')
      },
    },
    {
      name: '正式标题不能改为英文叙事',
      expectedError: 'title 必须使用中文叙事',
      mutate(draft) {
        draft.title = 'Iran BGP Route Visibility Observation Report'
      },
    },
    {
      name: '正式段落不能改为英文叙事',
      expectedError: 'scope[0] 必须使用中文叙事',
      mutate(draft) {
        draft.sections[0]!.paragraphs[0]!.text =
          'RRC25 observed BGP control plane visibility.'
      },
    },
    {
      name: '夹带国家名不能把英文摘要伪装成中文',
      expectedError: 'summary 必须使用中文叙事',
      mutate(draft) {
        draft.summary.text =
          'This report is English prose for 伊朗 RRC25.'
      },
    },
  ]
  for (const item of cases) {
    await context.test(item.name, () => {
      const draft = structuredClone(validDraft)
      item.mutate(draft)
      const validation = validateReportDraft(draft, evidence)
      assert.equal(validation.passed, false)
      assert.ok(
        validation.errors.includes(item.expectedError),
        validation.errors.join('\n'),
      )
    })
  }
})

test('所有可发布文本的变化方向由对应派生事实失败关闭', async (context) => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const narrator = new DeterministicAcceptanceNarrator()
  const validDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence: { facts, asnPages: [asnPage()] },
  })
  const cases = [
    {
      name: '下降结论不能由负的减少量支持',
      metric: 'start_to_lowest_visible_prefix_vp_change',
      value: -1,
      description: '起点至最低点下降',
    },
    {
      name: '回升结论不能由负的回升量支持',
      metric: 'recovered_from_lowest',
      value: -1,
      description: '最低点至窗口结束回升',
    },
    {
      name: '结束低于起点不能由负的结束缺口支持',
      metric: 'end_gap_from_start',
      value: -1,
      description: '窗口结束低于起点',
    },
  ] as const
  for (const item of cases) {
    await context.test(item.name, () => {
      const mutatedFacts = structuredClone(facts)
      const derivedFact = mutatedFacts.derivedFacts.find(
        (fact) => fact.metric === item.metric,
      )
      assert.ok(derivedFact)
      derivedFact.value = item.value
      const validation = validateReportDraft(validDraft, {
        facts: mutatedFacts,
        asnPages: [asnPage()],
      })
      assert.equal(validation.passed, false)
      assert.ok(
        validation.errors.some(
          (error) =>
            error.includes(item.description) &&
            error.includes('方向不一致'),
        ),
        validation.errors.join('\n'),
      )
    })
  }

  await context.test('方向结论缺少对应派生事实时拒绝', () => {
    const mutatedFacts = structuredClone(facts)
    mutatedFacts.derivedFacts = mutatedFacts.derivedFacts.filter(
      (fact) => fact.metric !== 'recovered_from_lowest',
    )
    const validation = validateReportDraft(validDraft, {
      facts: mutatedFacts,
      asnPages: [asnPage()],
    })
    assert.equal(validation.passed, false)
    assert.ok(
      validation.errors.some(
        (error) =>
          error.includes('最低点至窗口结束回升') &&
          error.includes('缺少对应的确定性派生事实'),
      ),
      validation.errors.join('\n'),
    )
  })

  const contradictoryClaims = [
    {
      subtitle:
        '起点至最低点可见关系上升，窗口结束时仍低于起点',
      description: '起点至最低点上升',
    },
    {
      subtitle:
        '窗口后段可见关系回落，窗口结束时仍低于起点',
      description: '最低点至窗口结束下降',
    },
    {
      subtitle:
        '窗口内可见性下降，但窗口结束时高于起点',
      description: '窗口结束高于起点',
    },
  ] as const
  for (const item of contradictoryClaims) {
    await context.test(`反向措辞失败关闭：${item.description}`, () => {
      const draft = structuredClone(validDraft)
      draft.subtitle = item.subtitle
      const validation = validateReportDraft(draft, {
        facts,
        asnPages: [asnPage()],
      })
      assert.equal(validation.passed, false)
      assert.ok(
        validation.errors.some(
          (error) =>
            error.includes(item.description) &&
            error.includes('方向不一致'),
        ),
        validation.errors.join('\n'),
      )
    })
  }
})

test('方向矛盾在标题、摘要、关键数字、章节和未知项中均失败关闭', async (context) => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const evidence = { facts, asnPages: [asnPage()] }
  const narrator = new DeterministicAcceptanceNarrator()
  const validDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence,
  })
  const cases: Array<{
    name: string
    expectedLocation: string
    mutate: (draft: typeof validDraft) => void
  }> = [
    {
      name: '标题',
      expectedLocation: 'title',
      mutate(draft) {
        draft.title += '：起点至最低点可见性上升'
      },
    },
    {
      name: '副标题',
      expectedLocation: 'subtitle',
      mutate(draft) {
        draft.subtitle =
          '起点至最低点可见性上升，窗口结束仍低于起点'
      },
    },
    {
      name: '摘要',
      expectedLocation: 'summary',
      mutate(draft) {
        draft.summary.text += ' 起点至最低点可见性上升。'
      },
    },
    {
      name: '关键数字标题',
      expectedLocation: 'highlights[0].label',
      mutate(draft) {
        draft.highlights[0]!.label = '窗口结束高于起点'
      },
    },
    {
      name: '关键数字数值',
      expectedLocation: 'highlights[0].value',
      mutate(draft) {
        draft.highlights[0]!.value = '最低点至窗口结束继续下降'
      },
    },
    {
      name: '章节标题',
      expectedLocation: 'end_state.title',
      mutate(draft) {
        const section = draft.sections.find(
          (item) => item.id === 'end_state',
        )
        assert.ok(section)
        section.title = '最低点至窗口结束继续下降'
      },
    },
    {
      name: '章节段落',
      expectedLocation: 'assessment[0]',
      mutate(draft) {
        const section = draft.sections.find(
          (item) => item.id === 'assessment',
        )
        assert.ok(section)
        section.paragraphs[0]!.text +=
          ' 窗口结束高于起点。'
      },
    },
    {
      name: '不能回答项',
      expectedLocation: 'unknowns[0]',
      mutate(draft) {
        draft.unknowns[0] += '；起点至最低点可见性上升'
      },
    },
  ]

  for (const item of cases) {
    await context.test(item.name, () => {
      const draft = structuredClone(validDraft)
      item.mutate(draft)
      const validation = validateReportDraft(draft, evidence)
      assert.equal(validation.passed, false)
      assert.ok(
        validation.errors.some(
          (error) =>
            error.includes(item.expectedLocation) &&
            error.includes('方向不一致'),
        ),
        validation.errors.join('\n'),
      )
    })
  }
})

test('确定性叙事按合法序列事实生成下降、持平和上升方向', async (context) => {
  const cases = [
    {
      name: '全窗口持平',
      batch: directionalReportBatch(() => 300_000),
      expected: [
        /起点至最低点路由可见性持平/,
        /最低点至窗口结束持平/,
        /结束时与起点持平/,
      ],
    },
    {
      name: '从起点单调上升',
      batch: directionalReportBatch(
        (index) => 300_000 + index * 100,
      ),
      expected: [
        /起点至最低点路由可见性持平/,
        /窗口后段出现回升/,
        /结束时高于起点/,
      ],
    },
    {
      name: '到窗口结束单调下降',
      batch: directionalReportBatch(
        (index) => 300_000 - index * 100,
      ),
      expected: [
        /窗口内路由可见性明显下降/,
        /最低点至窗口结束持平/,
        /结束时仍未回到起点水平/,
      ],
    },
    {
      name: '下降后回到起点',
      batch: directionalReportBatch((index, slotCount) => {
        const lowestIndex = Math.floor((slotCount - 1) / 2)
        if (index <= lowestIndex) {
          return 300_000 - index * 100
        }
        const remaining = slotCount - 1 - lowestIndex
        return (
          300_000 -
          lowestIndex * 100 +
          Math.round(
            lowestIndex * 100 * (index - lowestIndex) / remaining,
          )
        )
      }),
      expected: [
        /窗口内路由可见性明显下降/,
        /窗口后段出现回升/,
        /结束时与起点持平/,
      ],
    },
  ] as const

  for (const item of cases) {
    await context.test(item.name, async () => {
      const facts = assembleCountryOutageFacts(item.batch)
      const evidence = { facts, asnPages: [] }
      const draft = await new DeterministicAcceptanceNarrator().generate({
        reference: facts.event.legacy_reference,
        evidence,
      })
      const validation = validateReportDraft(draft, evidence)
      assert.equal(validation.passed, true, validation.errors.join('\n'))
      const directionText = [
        draft.subtitle,
        draft.summary.text,
        draft.sections.find((section) => section.id === 'visibility')
          ?.title ?? '',
        draft.sections.find((section) => section.id === 'end_state')
          ?.title ?? '',
      ].join('\n')
      for (const pattern of item.expected) {
        assert.match(directionText, pattern)
      }
    })
  }
})

test('身份一致的非最终快照生成合法报告并明确数据截止与窗口外未知', async () => {
  const batch = nonFinalReportBatch()
  const page = nonFinalAsnPage()
  assert.deepEqual(
    [
      batch.resolution.is_final,
      batch.overview.is_final,
      batch.series.is_final,
      batch.audit.is_final,
      page.is_final,
    ],
    [false, false, false, false, false],
  )

  const compiler = new CountryOutageReportCompiler({
    client: {
      async getObservationBatch() {
        return batch
      },
      async getAsns() {
        return page
      },
    },
    narrator: new DeterministicAcceptanceNarrator(),
    now: () => new Date('2026-07-29T16:00:00Z'),
  })
  const compiled = await compiler.compileWithEvidence(
    'country_outage/2026-02-27 09:12:32/IR/1/r',
  )
  assert.equal(compiled.document.snapshot.isFinal, false)
  assert.equal(
    compiled.document.snapshot.dataThrough,
    '2026-02-28T15:00:00Z',
  )
  assert.equal(compiled.document.validation.passed, true)

  const scopeText = compiled.document.draft.sections
    .find((section) => section.id === 'scope')!
    .paragraphs.map((paragraph) => paragraph.text)
    .join('\n')
  assert.match(scopeText, /当前发布仍为非最终状态/)
  assert.match(scopeText, /数据截至 2026-02-28T15:00:00Z/)
  assert.match(scopeText, /观测窗口之外/)
  assert.match(scopeText, /数据截止点之后的状态未知/)
  assert.ok(
    compiled.document.draft.unknowns.some(
      (item) =>
        item.includes('观测窗口之外') &&
        item.includes('数据截止点之后') &&
        item.includes('后续是否已经完全恢复'),
    ),
  )

  const markdown = renderReportMarkdown(compiled.document)
  assert.match(markdown, /data_through: "2026-02-28T15:00:00Z"/)
  assert.match(markdown, /当前发布仍为非最终状态/)
  const audit =
    createCountryOutageReportAuditManifestArtifact(compiled)
  assert.equal(audit.manifest.snapshotIdentity.isFinal, false)
  assert.equal(
    audit.manifest.snapshotIdentity.dataThrough,
    '2026-02-28T15:00:00Z',
  )
  // Markdown/PDF 是否新增独立 is_final 元数据超出本任务允许编辑的文件；
  // 本测试固定报告文档和审计清单身份，主流程联合验收继续核对下载呈现。
})

test('报告草稿拒绝空白阅读内容和空章节', async (context) => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const evidence = { facts, asnPages: [asnPage()] }
  const narrator = new DeterministicAcceptanceNarrator()
  const validDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence,
  })
  const cases: Array<{
    name: string
    mutate: (draft: typeof validDraft) => void
  }> = [
    {
      name: '空白 title',
      mutate(draft) {
        draft.title = ' \t '
      },
    },
    {
      name: '空白 subtitle',
      mutate(draft) {
        draft.subtitle = '\n'
      },
    },
    {
      name: '空白 summary',
      mutate(draft) {
        draft.summary.text = '　'
      },
    },
    {
      name: '空白 highlight label',
      mutate(draft) {
        draft.highlights[0]!.label = ' '
      },
    },
    {
      name: '空白 highlight value',
      mutate(draft) {
        draft.highlights[0]!.value = '\t'
      },
    },
    {
      name: '空白 section title',
      mutate(draft) {
        draft.sections[0]!.title = ' \n '
      },
    },
    {
      name: '空 section paragraphs',
      mutate(draft) {
        draft.sections[0]!.paragraphs = []
      },
    },
    {
      name: '空白 paragraph text',
      mutate(draft) {
        draft.sections[0]!.paragraphs[0]!.text = '　\t'
      },
    },
    {
      name: '空白 unknown',
      mutate(draft) {
        draft.unknowns[0] = ' '
      },
    },
  ]

  for (const item of cases) {
    await context.test(item.name, () => {
      const draft = structuredClone(validDraft)
      item.mutate(draft)
      const validation = validateReportDraft(draft, evidence)
      assert.equal(validation.passed, false, item.name)
      assert.throws(() => parseReportDraft(draft), Error, item.name)
    })
  }
})

test('关键数字表通过结构化事实引用覆盖固定人口、起点、最低点和结束点', async (context) => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const evidence = { facts, asnPages: [asnPage()] }
  const narrator = new DeterministicAcceptanceNarrator()
  const validDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence,
  })
  const pointReference = (kind: 'start' | 'lowest' | 'end'): string => {
    const point = facts.keyVisibilityPoints.find(
      (item) => item.kind === kind,
    )
    assert.ok(point)
    return `${point.provenance.endpoint}:${point.provenance.pointer}`
  }
  const requiredReferences = {
    fixedOriginAsnPopulation: new Set([
      'overview:/cohort',
      'overview:/cohort/origin_asn_count',
    ]),
    fixedPrefixVpPopulation: new Set([
      'overview:/cohort',
      'overview:/cohort/prefix_vp_count',
    ]),
    start: new Set([pointReference('start')]),
    lowest: new Set([pointReference('lowest')]),
    end: new Set([pointReference('end')]),
  }

  await context.test('标签和顺序变化不影响结构化覆盖判断', () => {
    const draft = structuredClone(validDraft)
    const labels = ['甲', '乙', '丙', '丁', '戊', '己', '庚', '辛']
    draft.highlights.forEach((highlight, index) => {
      highlight.label = `关键项${labels[index] ?? '补充'}`
    })
    draft.highlights.reverse()
    const validation = validateReportDraft(draft, evidence)
    assert.equal(validation.passed, true, validation.errors.join('\n'))
  })

  const missingCases = [
    {
      name: '固定 origin ASN 人口',
      references: requiredReferences.fixedOriginAsnPopulation,
    },
    {
      name: '固定 Prefix×VP 人口',
      references: requiredReferences.fixedPrefixVpPopulation,
    },
    {
      name: '窗口起点',
      references: requiredReferences.start,
    },
    {
      name: '窗口最低点',
      references: requiredReferences.lowest,
    },
    {
      name: '窗口结束点',
      references: requiredReferences.end,
    },
  ]
  for (const item of missingCases) {
    await context.test(`缺少${item.name}结构化关键数字时拒绝`, () => {
      const draft = structuredClone(validDraft)
      draft.highlights = draft.highlights.filter(
        (highlight) =>
          !highlight.evidenceRefs.some((reference) =>
            item.references.has(reference),
          ),
      )
      assert.ok(draft.highlights.length >= 5)
      const validation = validateReportDraft(draft, evidence)
      assert.equal(validation.passed, false)
      assert.ok(
        validation.errors.includes(
          `关键数字缺少结构化覆盖：${item.name}`,
        ),
        validation.errors.join('\n'),
      )
    })
  }

  await context.test('只有起点引用但没有对应可见性数值时拒绝', () => {
    const draft = structuredClone(validDraft)
    const start = draft.highlights.find((highlight) =>
      highlight.evidenceRefs.some((reference) =>
        requiredReferences.start.has(reference),
      ),
    )
    assert.ok(start)
    start.label = '窗口起点'
    start.value = '见冻结事实'
    const validation = validateReportDraft(draft, evidence)
    assert.equal(validation.passed, false)
    assert.ok(
      validation.errors.includes(
        '关键数字缺少结构化覆盖：窗口起点',
      ),
      validation.errors.join('\n'),
    )
  })
})

test('常见同义越界肯定结论被拒绝且局部否定边界句保留', async (context) => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const evidence = { facts, asnPages: [asnPage()] }
  const narrator = new DeterministicAcceptanceNarrator()
  const validDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence,
  })
  const positiveCases = [
    {
      name: '全国互联网中断',
      text: '全国互联网已经中断。',
      id: 'nationwide_outage',
    },
    {
      name: '攻击造成',
      text: '本次变化由攻击造成。',
      id: 'unsupported_cause',
    },
    {
      name: '政策行为导致',
      text: '本次变化由政策行为导致。',
      id: 'unsupported_cause',
    },
    {
      name: '配置错误引起',
      text: '本次变化是配置错误引起的。',
      id: 'unsupported_cause',
    },
    {
      name: '基础设施故障所致',
      text: '本次变化由基础设施故障所致。',
      id: 'unsupported_cause',
    },
    {
      name: '源于攻击',
      text: '本次变化源于攻击。',
      id: 'unsupported_cause',
    },
    {
      name: '归因于政策行为',
      text: '本次变化应归因于政策行为。',
      id: 'unsupported_cause',
    },
    {
      name: '原因就是攻击',
      text: '这次变化的原因就是攻击。',
      id: 'unsupported_cause',
    },
    {
      name: '配置错误触发',
      text: '本次中断由配置错误触发。',
      id: 'unsupported_cause',
    },
    {
      name: '用户断网',
      text: '当地用户已经断网。',
      id: 'user_or_business_outage',
    },
    {
      name: '业务中断',
      text: '关键业务已经中断。',
      id: 'user_or_business_outage',
    },
    {
      name: '恢复正常',
      text: '网络已恢复正常。',
      id: 'unsupported_recovery',
    },
    {
      name: '完全恢复',
      text: '网络已经完全恢复。',
      id: 'unsupported_recovery',
    },
    {
      name: '前句否定不能掩盖后句肯定归因',
      text:
        '现有数据不能认定全国互联网中断，但本次变化由攻击造成。',
      id: 'unsupported_cause',
    },
    {
      name: '无标点因果连接后的肯定结论不能被前项否定掩盖',
      text: '现有数据不能确认原因因此全国互联网已经中断。',
      id: 'nationwide_outage',
    },
  ] as const
  for (const item of positiveCases) {
    await context.test(item.name, () => {
      const draft = structuredClone(validDraft)
      draft.summary.text += ` ${item.text}`
      const validation = validateReportDraft(draft, evidence)
      assert.equal(validation.passed, false)
      assert.ok(
        validation.errors.includes(
          `报告出现越界肯定结论：${item.id}`,
        ),
        validation.errors.join('\n'),
      )
    })
  }

  await context.test('关键数字与未知项中的肯定越界同样拒绝', () => {
    const draft = structuredClone(validDraft)
    draft.highlights[0]!.label = '全国互联网已经中断'
    draft.unknowns[0] = '本次变化源于攻击'
    const validation = validateReportDraft(draft, evidence)
    assert.equal(validation.passed, false)
    assert.ok(
      validation.errors.includes(
        '报告出现越界肯定结论：nationwide_outage',
      ),
      validation.errors.join('\n'),
    )
    assert.ok(
      validation.errors.includes(
        '报告出现越界肯定结论：unsupported_cause',
      ),
      validation.errors.join('\n'),
    )
  })

  const negativeBoundaryCases = [
    '现有数据不能认定全国互联网已经中断。',
    '现有证据不足以证明本次变化由攻击造成。',
    '本次变化并非攻击所致。',
    '本次变化不是由配置错误造成。',
    '现有证据不足以证明本次变化源于政策行为。',
    '这些控制面数据不得理解为用户或业务中断。',
    '这不表示网络已恢复正常，也无法确认是否已经完全恢复。',
    '网络尚未恢复正常。',
    '事件是否结束不能由此认定。',
  ]
  for (const text of negativeBoundaryCases) {
    await context.test(text, () => {
      const draft = structuredClone(validDraft)
      draft.summary.text += ` ${text}`
      const validation = validateReportDraft(draft, evidence)
      assert.equal(validation.passed, true, validation.errors.join('\n'))
    })
  }
})

test('固定 unknown 否定句通过，缺少否定前缀的自然问句继续失败关闭', async () => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const evidence = { facts, asnPages: [asnPage()] }
  const narrator = new DeterministicAcceptanceNarrator()
  const validDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence,
  })
  const safeUnknowns = [
    '现有 RRC25 控制面证据不能回答是否属于全国性互联网中断',
    '现有 RRC25 控制面证据不能回答用户和具体业务受到多大影响',
    '现有 RRC25 控制面证据不能回答事件原因或责任主体',
    '现有 RRC25 控制面证据不能回答观测窗口之后是否已经完全恢复',
  ]
  const safeDraft = structuredClone(validDraft)
  safeDraft.unknowns = safeUnknowns
  const safeValidation = validateReportDraft(safeDraft, evidence)
  assert.equal(
    safeValidation.passed,
    true,
    safeValidation.errors.join('\n'),
  )

  const unsafeUnknowns = [
    '用户或业务是否受到影响',
    '用户和具体业务受到多大影响',
    '用户或业务是否中断',
    '哪个运营商或 ASN 应承担责任',
  ]
  for (const unsafeUnknown of unsafeUnknowns) {
    const unsafeDraft = structuredClone(safeDraft)
    unsafeDraft.unknowns[1] = unsafeUnknown
    const validation = validateReportDraft(unsafeDraft, evidence)
    assert.equal(validation.passed, false)
    assert.ok(
      validation.errors.some((error) =>
        error.startsWith('报告出现越界肯定结论：'),
      ),
      validation.errors.join('\n'),
    )
  }
})

test('报告文本解析只暴露固定安全诊断并保留合法路径', async (context) => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const narrator = new DeterministicAcceptanceNarrator()
  const validDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence: { facts, asnPages: [asnPage()] },
  })
  assert.deepEqual(
    parseReportDraftText(
      `\`\`\`json\n${JSON.stringify(validDraft)}\n\`\`\``,
    ),
    validDraft,
  )

  const cases = [
    {
      name: '缺少 JSON 对象',
      input: 'SENSITIVE_RAW_BODY',
      code: 'json_object_missing',
    },
    {
      name: 'JSON 语法无效',
      input: '{"secret":"SENSITIVE_FIELD_VALUE",}',
      code: 'json_syntax_invalid',
    },
    {
      name: '草稿结构无效',
      input: '{"secret":"SENSITIVE_FIELD_VALUE"}',
      code: 'draft_schema_invalid',
    },
  ] as const

  for (const item of cases) {
    await context.test(item.name, () => {
      assert.throws(
        () => parseReportDraftText(item.input),
        (error: unknown) => {
          assert.ok(error instanceof ReportDraftTextParseError)
          assert.equal(error.code, item.code)
          assert.equal(
            error.message,
            COUNTRY_OUTAGE_REPORT_DRAFT_TEXT_DIAGNOSTICS[item.code].message,
          )
          assert.deepEqual(
            error.diagnostic,
            COUNTRY_OUTAGE_REPORT_DRAFT_TEXT_DIAGNOSTICS[item.code],
          )
          assert.equal('cause' in error, false)
          assert.doesNotMatch(
            JSON.stringify({
              name: error.name,
              message: error.message,
              code: error.code,
              diagnostic: error.diagnostic,
            }),
            /SENSITIVE_RAW_BODY|SENSITIVE_FIELD_VALUE|Unexpected|position/i,
          )
          return true
        },
      )
    })
  }
})

test('报告草稿拒绝根对象和嵌套对象中的未声明字段', async (context) => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const narrator = new DeterministicAcceptanceNarrator()
  const validDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence: { facts, asnPages: [asnPage()] },
  })
  const cases: Array<{
    name: string
    mutate: (draft: typeof validDraft) => void
  }> = [
    {
      name: '根对象未声明字段',
      mutate(draft) {
        Object.assign(
          draft as unknown as Record<string, unknown>,
          { hidden: true },
        )
      },
    },
    {
      name: '证据段落未声明字段',
      mutate(draft) {
        Object.assign(
          draft.summary as unknown as Record<string, unknown>,
          { hidden: true },
        )
      },
    },
    {
      name: '关键数字未声明字段',
      mutate(draft) {
        Object.assign(
          draft.highlights[0] as unknown as Record<string, unknown>,
          { hidden: true },
        )
      },
    },
    {
      name: '章节未声明字段',
      mutate(draft) {
        Object.assign(
          draft.sections[0] as unknown as Record<string, unknown>,
          { hidden: true },
        )
      },
    },
  ]

  for (const item of cases) {
    await context.test(item.name, () => {
      const draft = structuredClone(validDraft)
      item.mutate(draft)
      assert.throws(() => parseReportDraft(draft), Error, item.name)
      assert.throws(
        () => parseReportDraftText(JSON.stringify(draft)),
        (error: unknown) =>
          error instanceof ReportDraftTextParseError &&
          error.code === 'draft_schema_invalid',
        item.name,
      )
    })
  }
})

test('不存在的数字和越界肯定结论会关闭发布', async () => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const evidence = { facts, asnPages: [asnPage()] }
  const narrator = new DeterministicAcceptanceNarrator()
  const draft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence,
  })
  draft.summary.text += ' 全国互联网已经中断，影响 999999999 名用户。'
  const validation = validateReportDraft(draft, evidence)
  assert.equal(validation.passed, false)
  assert.ok(
    validation.errors.some((error) => error.includes('999999999')),
  )
  assert.ok(
    validation.errors.some((error) => error.includes('越界肯定结论')),
  )

  const highlightDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence,
  })
  highlightDraft.highlights[0]!.value = '999999999 个'
  highlightDraft.highlights[0]!.evidenceRefs = [
    'overview:/cohort/not_a_real_field',
  ]
  const highlightValidation = validateReportDraft(highlightDraft, evidence)
  assert.equal(highlightValidation.passed, false)
  assert.ok(
    highlightValidation.errors.some((error) =>
      error.includes('999999999'),
    ),
  )
  assert.ok(
    highlightValidation.errors.some((error) =>
      error.includes('无效证据引用'),
    ),
  )
})

test('事实数字必须由所在块自身的 evidenceRefs 支持', async (context) => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const evidence = { facts, asnPages: [asnPage()] }
  const narrator = new DeterministicAcceptanceNarrator()
  const validDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence,
  })
  const cases: Array<{
    name: string
    expectedLocation: string
    expectedToken: string
    mutate: (draft: typeof validDraft) => void
  }> = [
    {
      name: 'summary 的真实覆盖率不能引用审计等级',
      expectedLocation: 'summary',
      expectedToken: '95.44%',
      mutate(draft) {
        draft.summary.evidenceRefs = ['audit:/evidence_level']
      },
    },
    {
      name: 'highlight 的真实最低值不能引用起点槽',
      expectedLocation: 'highlights[3]',
      expectedToken: '82.32%',
      mutate(draft) {
        draft.highlights[3]!.evidenceRefs = ['series:/series/0']
      },
    },
    {
      name: 'highlight label 的真实数字不能引用审计等级',
      expectedLocation: 'highlights[0].label',
      expectedToken: '563',
      mutate(draft) {
        draft.highlights[0]!.label = '固定观测范围 563'
        draft.highlights[0]!.value = '固定范围'
        draft.highlights[0]!.evidenceRefs = ['audit:/evidence_level']
      },
    },
    {
      name: 'paragraph 的真实数量不能引用审计等级',
      expectedLocation: 'visibility[0]',
      expectedToken: '367,215',
      mutate(draft) {
        const section = draft.sections.find(
          (item) => item.id === 'visibility',
        )!
        section.paragraphs[0]!.evidenceRefs = [
          'audit:/evidence_level',
        ]
      },
    },
  ]

  for (const item of cases) {
    await context.test(item.name, () => {
      const draft = structuredClone(validDraft)
      item.mutate(draft)
      const validation = validateReportDraft(draft, evidence)
      assert.equal(validation.passed, false, item.name)
      assert.ok(
        validation.errors.some(
          (error) =>
            error.includes(item.expectedLocation) &&
            error.includes('当前证据引用不支持的数字') &&
            error.includes(item.expectedToken),
        ),
        validation.errors.join('\n'),
      )
      assert.ok(
        !validation.errors.some((error) =>
          error.includes('使用无效证据引用'),
        ),
        validation.errors.join('\n'),
      )
    })
  }
})

test('证据数字必须同时匹配指标、单位和派生结果', async (context) => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const evidence = { facts, asnPages: [asnPage()] }
  const narrator = new DeterministicAcceptanceNarrator()
  const validDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence,
  })

  await context.test('UPDATE 数量不能换写成 Prefix×VP', () => {
    const draft = structuredClone(validDraft)
    const update = draft.highlights.find((highlight) =>
      highlight.evidenceRefs.includes(
        'series:/resource_metric_extrema/update_total/max',
      ),
    )
    assert.ok(update)
    update.label = 'Prefix×VP 可见关系'
    update.value = '340,960 条'
    const validation = validateReportDraft(draft, evidence)
    assert.equal(validation.passed, false)
    assert.ok(
      validation.errors.some((error) =>
        error.includes('指标或单位不一致'),
      ),
      validation.errors.join('\n'),
    )
  })

  await context.test('派生事实的操作数不能冒充最终 value', () => {
    const draft = structuredClone(validDraft)
    const section = draft.sections.find(
      (item) => item.id === 'end_state',
    )
    assert.ok(section)
    const recovered = facts.derivedFacts.find(
      (fact) => fact.metric === 'recovered_from_lowest',
    )
    assert.ok(recovered)
    section.paragraphs[0] = {
      text: '最低点后回升 333,938 条可见关系。',
      evidenceRefs: [recovered.factId],
    }
    const validation = validateReportDraft(draft, evidence)
    assert.equal(validation.passed, false)
    assert.ok(
      validation.errors.some(
        (error) =>
          error.includes('333,938') &&
          error.includes('当前证据引用不支持的数字'),
      ),
      validation.errors.join('\n'),
    )
  })

  await context.test('IPv4 极值不能改写为 IPv6 指标', () => {
    const draft = structuredClone(validDraft)
    const section = draft.sections.find(
      (item) => item.id === 'address_families',
    )
    assert.ok(section)
    section.paragraphs[0] = {
      text: 'IPv6 最低覆盖率为 82.285%。',
      evidenceRefs: [
        'series:/metric_extrema/ipv4_visible_prefix_vp_ratio/min',
      ],
    }
    const validation = validateReportDraft(draft, evidence)
    assert.equal(validation.passed, false)
    assert.ok(
      validation.errors.some((error) =>
        error.includes('指标或单位不一致'),
      ),
      validation.errors.join('\n'),
    )
  })
})

test('所有可发布文本中的精确数字都必须有所在块证据', async (context) => {
  const facts = assembleCountryOutageFacts(reportBatch())
  const evidence = { facts, asnPages: [asnPage()] }
  const narrator = new DeterministicAcceptanceNarrator()
  const validDraft = await narrator.generate({
    reference: facts.event.legacy_reference,
    evidence,
  })
  const cases = [
    {
      name: '标题',
      mutate(draft: typeof validDraft) {
        draft.title = '563 个 ASN 的国家中断报告'
      },
      location: 'title',
    },
    {
      name: '副标题',
      mutate(draft: typeof validDraft) {
        draft.subtitle = '固定范围包含 563 个 ASN'
      },
      location: 'subtitle',
    },
    {
      name: '章节标题',
      mutate(draft: typeof validDraft) {
        draft.sections[0]!.title = '563 个 ASN 的观测范围'
      },
      location: 'scope.title',
    },
    {
      name: '不能回答项',
      mutate(draft: typeof validDraft) {
        draft.unknowns[0] = '现有证据不能回答 563 个 ASN 是否代表全国断网'
      },
      location: 'unknowns[0]',
    },
  ]
  for (const item of cases) {
    await context.test(item.name, () => {
      const draft = structuredClone(validDraft)
      item.mutate(draft)
      const validation = validateReportDraft(draft, evidence)
      assert.equal(validation.passed, false)
      assert.ok(
        validation.errors.some(
          (error) =>
            error.includes(item.location) &&
            error.includes('含数字但没有证据引用'),
        ),
        validation.errors.join('\n'),
      )
    })
  }
})

test('编译器生成稳定身份并由同一文档渲染 Markdown', async () => {
  const source = {
    async getObservationBatch() {
      return reportBatch()
    },
    async getAsns() {
      return asnPage()
    },
  }
  const acceptanceNarrator = new DeterministicAcceptanceNarrator()
  const compiler = new CountryOutageReportCompiler({
    client: source,
    narrator: acceptanceNarrator,
    now: () => new Date('2026-07-28T14:00:00Z'),
  })
  const document = await compiler.compile(
    'country_outage/2026-02-27 09:12:32/IR/1/r',
  )
  const repeated = await compiler.compile(
    'country_outage/2026-02-27 09:12:32/IR/1/r',
  )
  const markdown = renderReportMarkdown(document)
  assert.equal(repeated.artifactId, document.artifactId)
  assert.equal(repeated.reportContentSha256, document.reportContentSha256)
  assert.match(document.artifactId, /^report_[0-9a-f]{32}$/)
  assert.match(document.reportContentSha256, /^[0-9a-f]{64}$/)
  assert.equal(
    document.validatorRulesVersion,
    'country_outage_report_validator_rules_v5',
  )
  assert.match(document.skillBundleSha256, /^[a-f0-9]{64}$/)
  assert.equal(
    document.projectKnowledgeVersion,
    COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION,
  )
  assert.equal(
    document.skillBundleSha256,
    computeCountryOutageSkillBundleSha256(),
  )
  assert.equal(document.model.adapter, 'deterministic-acceptance')
  assert.match(markdown, /AI 生成并经机器校验，未经人工审核/)
  assert.match(markdown, new RegExp(document.artifactId))
  assert.match(markdown, new RegExp(document.reportContentSha256))
  assert.match(
    markdown,
    /validator_rules: "country_outage_report_validator_rules_v5"/,
  )
  assert.match(
    markdown,
    new RegExp(`skill_bundle_sha256: "${document.skillBundleSha256}"`),
  )
  assert.match(markdown, /窗口后段出现回升，但还不能称为恢复/)

  const changedSkillNarrator = {
    identity: acceptanceNarrator.identity,
    validatorRulesVersion: acceptanceNarrator.validatorRulesVersion,
    skillBundleSha256: 'e'.repeat(64),
    async generate(
      request: Parameters<
        DeterministicAcceptanceNarrator['generate']
      >[0],
    ) {
      return await acceptanceNarrator.generate(request)
    },
  }
  assert.throws(
    () =>
      new CountryOutageReportCompiler({
        client: source,
        narrator: changedSkillNarrator,
        now: () => new Date('2026-07-28T14:00:00Z'),
      }),
    (error: unknown) =>
      error instanceof ReportValidationError &&
      error.message.includes('启动时固定资源不一致'),
  )
})

test('编译器在读取 Domeye 或调用叙述器前拒绝启动后 Skill 摘要漂移', async () => {
  const acceptanceNarrator = new DeterministicAcceptanceNarrator()
  let declaredSkillBundleSha256 =
    acceptanceNarrator.skillBundleSha256
  const mutableNarrator = {
    identity: acceptanceNarrator.identity,
    validatorRulesVersion: acceptanceNarrator.validatorRulesVersion,
    get skillBundleSha256() {
      return declaredSkillBundleSha256
    },
    async generate() {
      assert.fail('Skill 摘要漂移时不得调用叙述器')
    },
  }
  const compiler = new CountryOutageReportCompiler({
    client: {
      async getObservationBatch() {
        assert.fail('Skill 摘要漂移时不得读取 Domeye')
      },
      async getAsns() {
        assert.fail('Skill 摘要漂移时不得读取 ASN')
      },
    },
    narrator: mutableNarrator,
  })
  declaredSkillBundleSha256 = 'e'.repeat(64)
  await assert.rejects(
    compiler.compile(
      'country_outage/2026-02-27 09:12:32/IR/1/r',
    ),
    (error: unknown) =>
      error instanceof ReportValidationError &&
      error.message.includes('启动时固定资源不一致'),
  )
})

test('扩展能力缺失时只降级对应章节且不读取 ASN', async () => {
  const source = {
    async getObservationBatch() {
      const batch = reportBatch()
      batch.overview.capabilities = {
        fixed_cohort: { state: 'available' },
        asn_matrix: { state: 'unavailable', reason: '验收降级' },
        address_families: { state: 'unavailable', reason: '验收降级' },
        update_activity: { state: 'building', reason: '验收降级' },
        country_resources: { state: 'not_applicable', reason: '验收降级' },
      }
      return batch
    },
    async getAsns(): Promise<CountryOutageAsnPage> {
      assert.fail('ASN 能力不可用时不应读取分页')
    },
  }
  const compiler = new CountryOutageReportCompiler({
    client: source,
    narrator: new DeterministicAcceptanceNarrator(),
    now: () => new Date('2026-07-28T14:00:00Z'),
  })
  const document = await compiler.compile(
    'country_outage/2026-02-27 09:12:32/IR/1/r',
  )
  assert.deepEqual(
    document.draft.sections.map((section) => section.id),
    ['scope', 'key_numbers', 'visibility', 'end_state', 'assessment'],
  )
  assert.equal(document.validation.passed, true)
})

test('编译器拒绝未通过校验的 narrator 输出', async () => {
  const validNarrator = new DeterministicAcceptanceNarrator()
  const invalidNarrator = {
    identity: validNarrator.identity,
    validatorRulesVersion: validNarrator.validatorRulesVersion,
    skillBundleSha256: validNarrator.skillBundleSha256,
    async generate(request: Parameters<typeof validNarrator.generate>[0]) {
      const draft = await validNarrator.generate(request)
      draft.sections = draft.sections.filter(
        (section) => section.id !== 'assessment',
      )
      return draft
    },
  }
  const compiler = new CountryOutageReportCompiler({
    client: {
      async getObservationBatch() {
        return reportBatch()
      },
      async getAsns() {
        return asnPage()
      },
    },
    narrator: invalidNarrator,
  })
  await assert.rejects(
    compiler.compile(
      'country_outage/2026-02-27 09:12:32/IR/1/r',
    ),
    ReportValidationError,
  )
})

test('编译器在叙述器调用前拒绝超过 2000 条的报告证据', async () => {
  const oversizedBatch = reportBatch()
  const firstPoint = oversizedBatch.series.series[0]!
  const windowStart = Date.parse(
    oversizedBatch.overview.window_start_utc,
  )
  const slotCount = 2_001
  const windowEnd = windowStart + (slotCount - 1) * 1000
  const asLocal = (value: number): string =>
    new Date(value + 8 * 60 * 60 * 1000)
      .toISOString()
      .replace('Z', '+08:00')
  const asUtc = (value: number): string =>
    new Date(value).toISOString().replace('.000Z', 'Z')
  const windowEndUtc = asUtc(windowEnd)
  const windowEndLocal = asLocal(windowEnd)
  oversizedBatch.overview.window_end_utc = windowEndUtc
  oversizedBatch.overview.observation_scope.window_end_utc = windowEndUtc
  oversizedBatch.overview.observation_scope.window_end_local = windowEndLocal
  oversizedBatch.overview.observation_scope.last_observation_at_utc =
    windowEndUtc
  oversizedBatch.overview.observation_scope.last_observation_at_local =
    windowEndLocal
  oversizedBatch.overview.observation_scope.interval_seconds = 1
  oversizedBatch.overview.observation_scope.observation_count = slotCount
  oversizedBatch.overview.observation_scope.expected_observation_count =
    slotCount
  oversizedBatch.series.window_end_utc = windowEndUtc
  oversizedBatch.series.interval_seconds = 1
  oversizedBatch.audit.window_end_utc = windowEndUtc
  oversizedBatch.series.series = Array.from(
    { length: slotCount },
    (_, index) => {
      const observedAt = windowStart + index * 1000
      return {
        ...firstPoint,
        observed_at_utc: asUtc(observedAt),
        observed_at_local: asLocal(observedAt),
        ...(index === 0
          ? {}
          : {
              visible_prefix_vp_delta: 0,
              visible_prefix_vp_ratio_delta_pp: 0,
            }),
      }
    },
  )
  // 本用例只验证容量门槛；重建序列后不再沿用原 5 分钟样本的极值回指。
  oversizedBatch.series.metric_extrema = {}
  oversizedBatch.overview.capabilities.country_resources = {
    state: 'not_applicable',
    reason: '容量门槛用例不携带国家资源轨道',
  }
  oversizedBatch.series.resource_series = []
  oversizedBatch.series.resource_metric_extrema = {}
  let narratorCalls = 0
  const baseNarrator = new DeterministicAcceptanceNarrator()
  const compiler = new CountryOutageReportCompiler({
    client: {
      async getObservationBatch() {
        return oversizedBatch
      },
      async getAsns() {
        return asnPage()
      },
    },
    narrator: {
      identity: baseNarrator.identity,
      validatorRulesVersion: baseNarrator.validatorRulesVersion,
      skillBundleSha256: baseNarrator.skillBundleSha256,
      async generate(request) {
        narratorCalls += 1
        return await baseNarrator.generate(request)
      },
    },
  })

  await assert.rejects(
    compiler.compile(
      'country_outage/2026-02-27 09:12:32/IR/1/r',
    ),
    (error: unknown) =>
      error instanceof CountryOutageEvidenceCapacityError &&
      error.count.total > 2_000,
  )
  assert.equal(narratorCalls, 0)
})

test('审计清单只收录实际引用证据与安全投影，且两次重放字节一致', async () => {
  const source = {
    async getObservationBatch() {
      const batch = reportBatch()
      batch.series.series[0]!.credential =
        '不应进入审计清单的凭据'
      batch.series.series[0]!.prompt =
        '不应进入审计清单的提示正文'
      return batch
    },
    async getAsns() {
      const page = asnPage()
      page.items[0]!.api_key = '不应进入审计清单的 API key'
      page.items[0]!.system_prompt = '不应进入的系统提示'
      return page
    },
  }
  const createCompiler = () =>
    new CountryOutageReportCompiler({
      client: source,
      narrator: new DeterministicAcceptanceNarrator(),
      now: () => new Date('2026-07-28T14:00:00Z'),
    })
  const firstCompiled = await createCompiler().compileWithEvidence(
    'country_outage/2026-02-27 09:12:32/IR/1/r',
  )
  const secondCompiled = await createCompiler().compileWithEvidence(
    'country_outage/2026-02-27 09:12:32/IR/1/r',
  )
  const first =
    createCountryOutageReportAuditManifestArtifact(firstCompiled)
  const second =
    createCountryOutageReportAuditManifestArtifact(secondCompiled)

  assert.equal(
    first.manifest.schemaVersion,
    'country_outage_report_audit_manifest_v1',
  )
  assert.equal(
    first.manifest.reportIdentity.artifactId,
    firstCompiled.document.artifactId,
  )
  assert.deepEqual(
    first.manifest.snapshotIdentity,
    firstCompiled.document.snapshot,
  )
  assert.equal(
    first.manifest.factSetIdentity.factSetId,
    firstCompiled.document.factSetId,
  )
  assert.deepEqual(
    first.manifest.modelIdentity,
    firstCompiled.document.model,
  )
  assert.equal(
    first.manifest.contractIdentity.reportSpecificationVersion,
    firstCompiled.document.reportSpecificationVersion,
  )
  assert.equal(
    first.manifest.contractIdentity.projectKnowledgeVersion,
    firstCompiled.document.projectKnowledgeVersion,
  )
  assert.equal(
    first.manifest.contractIdentity.validatorRulesVersion,
    firstCompiled.document.validatorRulesVersion,
  )
  assert.equal(
    first.manifest.contractIdentity.skillBundleSha256,
    firstCompiled.document.skillBundleSha256,
  )
  assert.deepEqual(
    first.manifest.evidenceTrace.usedEvidenceRefs,
    firstCompiled.document.validation.checkedEvidenceRefs,
  )
  assert.ok(
    first.manifest.evidenceTrace.keyObservationSlots.length >= 3,
  )
  assert.ok(first.manifest.evidenceTrace.derivedFacts.length >= 5)
  assert.equal(first.manifest.evidenceTrace.asnItems.length, 2)
  assert.ok(first.manifest.evidenceTrace.extremaPoints.length >= 1)
  assert.ok(
    first.manifest.capabilityBoundary.unavailableOrDegraded.some(
      (item) =>
        item.capability === 'normal_band' &&
        item.state === 'unavailable',
    ),
  )
  assert.deepEqual(
    first.manifest.cannotAnswer,
    firstCompiled.document.draft.unknowns,
  )
  assert.deepEqual(
    first.manifest.validation,
    firstCompiled.document.validation,
  )

  const serialized = first.artifact.content.toString('utf8')
  assert.doesNotMatch(serialized, /不应进入审计清单的凭据/)
  assert.doesNotMatch(serialized, /不应进入审计清单的提示正文/)
  assert.doesNotMatch(serialized, /不应进入审计清单的 API key/)
  assert.doesNotMatch(serialized, /不应进入的系统提示/)
  assert.doesNotMatch(serialized, /你是 Domeye/)
  assert.equal(first.artifact.filename, 'audit-manifest.json')
  assert.equal(first.artifact.byteLength, first.artifact.content.byteLength)
  assert.equal(
    first.artifact.sha256,
    createHash('sha256')
      .update(first.artifact.content)
      .digest('hex'),
  )
  assert.deepEqual(
    describeCountryOutageReportAuditManifestArtifact(first),
    {
      schemaVersion: 'country_outage_report_audit_manifest_v1',
      filename: 'audit-manifest.json',
      byteLength: first.artifact.byteLength,
      sha256: first.artifact.sha256,
    },
  )

  assert.deepEqual(second.manifest, first.manifest)
  assert.equal(
    second.artifact.content.equals(first.artifact.content),
    true,
  )
  assert.equal(second.artifact.sha256, first.artifact.sha256)
  assert.equal(second.artifact.byteLength, first.artifact.byteLength)
})
