import assert from 'node:assert/strict'
import test from 'node:test'
import { Check } from 'typebox/value'

import {
  DomeyeAnswerContextSchema,
  DomeyeAnswerStyleAssessmentSchema,
  DomeyeRendererDraftSchema,
  DomeyeResponseGuardDecisionSchema,
  DomeyeTypedFindingSchema,
  type DomeyeActionReceipt,
  type DomeyeArtifactEnvelope,
  type DomeyeDataIdentity,
  type DomeyeExecutionBinding,
  type DomeyeRendererDraft,
} from '../src/agent/contracts.js'

/*
 * 这里刻意只构造公开机器合同；Renderer 是测试替身，不读取任何额外事实源。
 */
import {
  COUNTRY_OUTAGE_MANDATORY_LIMITATIONS,
  COUNTRY_OUTAGE_REQUIRED_ANSWER_BOUNDARIES,
  assessCountryOutageAnswerStyle,
  buildCountryOutageAnswerContext,
  buildCountryOutageSeriesExtremaFinding,
  composeCountryOutageAnswer,
  composeCountryOutageRendererDraftText,
  guardCountryOutageResponse,
  renderCountryOutageDeterministicFallback,
  type DomeyeAnswerRenderer,
  DomeyeFindingAnswerError,
} from '../src/agent/finding-answer.js'
import { canonicalJsonSha256 } from '../src/shared/deterministic-json.js'

const SHA_A = `sha256:${'a'.repeat(64)}`
const SHA_B = `sha256:${'b'.repeat(64)}`
const SHA_C = `sha256:${'c'.repeat(64)}`
const SHA_D = `sha256:${'d'.repeat(64)}`
const SHA_E = `sha256:${'e'.repeat(64)}`
const SHA_F = `sha256:${'f'.repeat(64)}`

const IDENTITY: DomeyeDataIdentity = {
  event_type: 'country_outage',
  incident_id: 'incident_go_v1_a1de26f854831330c616a72af21597eb',
  publication_id: 'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f',
  revision: 1,
  collector_id: 'rrc25',
  cohort_id: 'country_event_cohort_v1_1e04abfc6430776bef20403fac528698',
  country_code: 'IR',
  window_start_utc: '2026-02-27T00:10:00Z',
  window_end_utc: '2026-03-11T00:00:00Z',
  data_through: '2026-03-11T00:00:00Z',
  is_final_in_data_range: false,
  lifecycle_state: 'event_end_unknown',
}

const TOOL_BINDING: DomeyeExecutionBinding = {
  execution_unit_id: 'TOOL-03',
  execution_unit_name: 'read_metric_series',
  execution_unit_version: '1.0.0',
  contract_digest: SHA_A,
  implementation_digest: SHA_B,
  semantic_digest: SHA_C,
}

const OPERATOR_BINDING: DomeyeExecutionBinding = {
  execution_unit_id: 'OP-01',
  execution_unit_name: 'series_extrema',
  execution_unit_version: '1.0.0',
  contract_digest: SHA_D,
  implementation_digest: SHA_E,
  semantic_digest: SHA_F,
}

function contentDigest(value: unknown): string {
  return `sha256:${canonicalJsonSha256(value)}`
}

function fixture(): {
  series_artifact: DomeyeArtifactEnvelope
  series_receipt: DomeyeActionReceipt
  extrema_artifact: DomeyeArtifactEnvelope
  extrema_receipt: DomeyeActionReceipt
} {
  const seriesPayload = {
    schema_version: 'domeye_metric_series_artifact_v1' as const,
    metric: 'fixed_visible_ipv4_address_count' as const,
    unit: 'unique_ipv4_address' as const,
    population_definition:
      'normalized_deduplicated_merged_fixed_prefix_ipv4_unique_address_union' as const,
    timestamps_utc: [
      IDENTITY.window_start_utc,
      '2026-02-28T14:35:00Z',
      IDENTITY.window_end_utc,
    ],
    values: [10_156_800, 9_577_728, 10_069_760],
    time_slot_count: 3,
    observed_point_count: 3,
    null_point_count: 0,
    completeness: {
      state: 'complete' as const,
      missing_slot_count: 0,
    },
    definition: '固定 cohort 的 IPv4 唯一地址并集可见量',
    source_response_sha256: SHA_A,
    evidence_refs: [
      'series:/timestamps',
      'series:/tracks/fixed_visible_ipv4_address_count',
    ],
  }
  const seriesArtifact = {
    schema_version: 'domeye_agent_artifact_envelope_v1' as const,
    artifact_id: 'artifact-series-1',
    artifact_kind: 'metric_series' as const,
    candidate_id: 'candidate-first-slice-1',
    tenant_id: 'domeye' as const,
    data_identity: IDENTITY,
    producer_action_id: 'action-cap-006-1',
    execution_binding: TOOL_BINDING,
    immutable: true as const,
    content_digest: contentDigest(seriesPayload),
    created_at_utc: '2026-08-19T01:00:01Z',
    payload: seriesPayload,
  }
  const seriesReceipt: DomeyeActionReceipt = {
    schema_version: 'domeye_agent_action_receipt_v1',
    receipt_id: 'receipt-cap-006-1',
    admission_receipt_id: 'admission-cap-006-1',
    action_id: seriesArtifact.producer_action_id,
    proposal_id: 'proposal-cap-006-1',
    capability_id: 'CAP-006',
    candidate_id: seriesArtifact.candidate_id,
    tenant_id: 'domeye',
    data_identity: IDENTITY,
    execution_binding: TOOL_BINDING,
    status: 'succeeded',
    artifact_ids: [seriesArtifact.artifact_id],
    failure_code: null,
    started_at_utc: '2026-08-19T01:00:00Z',
    completed_at_utc: '2026-08-19T01:00:01Z',
    receipt_digest: SHA_B,
  }
  const extremaPayload = {
    schema_version: 'domeye_series_extrema_artifact_v1' as const,
    result_state: 'known' as const,
    metric: 'fixed_visible_ipv4_address_count' as const,
    unit: 'unique_ipv4_address' as const,
    tie_policy: 'first_observed_occurrence' as const,
    time_slot_count: 3,
    observed_point_count: 3,
    null_point_count: 0,
    first: 10_156_800,
    first_at_utc: IDENTITY.window_start_utc,
    last: 10_069_760,
    last_at_utc: IDENTITY.window_end_utc,
    minimum: 9_577_728,
    minimum_at_utc: '2026-02-28T14:35:00Z',
    maximum: 10_156_800,
    maximum_at_utc: IDENTITY.window_start_utc,
    difference: 579_072,
    net_change: -87_040,
    source_artifact_id: seriesArtifact.artifact_id,
    evidence_refs: [
      ...seriesPayload.evidence_refs,
      'derived:/operators/series_extrema/fixed_visible_ipv4_address_count',
    ],
  }
  const extremaArtifact = {
    schema_version: 'domeye_agent_artifact_envelope_v1' as const,
    artifact_id: 'artifact-extrema-1',
    artifact_kind: 'series_extrema' as const,
    candidate_id: seriesArtifact.candidate_id,
    tenant_id: 'domeye' as const,
    data_identity: IDENTITY,
    producer_action_id: 'action-cap-016-1',
    execution_binding: OPERATOR_BINDING,
    immutable: true as const,
    content_digest: contentDigest(extremaPayload),
    created_at_utc: '2026-08-19T01:00:02Z',
    payload: extremaPayload,
  }
  const extremaReceipt: DomeyeActionReceipt = {
    schema_version: 'domeye_agent_action_receipt_v1',
    receipt_id: 'receipt-cap-016-1',
    admission_receipt_id: 'admission-cap-016-1',
    action_id: extremaArtifact.producer_action_id,
    proposal_id: 'proposal-cap-016-1',
    capability_id: 'CAP-016',
    candidate_id: extremaArtifact.candidate_id,
    tenant_id: 'domeye',
    data_identity: IDENTITY,
    execution_binding: OPERATOR_BINDING,
    status: 'succeeded',
    artifact_ids: [extremaArtifact.artifact_id],
    failure_code: null,
    started_at_utc: '2026-08-19T01:00:01Z',
    completed_at_utc: '2026-08-19T01:00:02Z',
    receipt_digest: SHA_C,
  }
  return {
    series_artifact: seriesArtifact,
    series_receipt: seriesReceipt,
    extrema_artifact: extremaArtifact,
    extrema_receipt: extremaReceipt,
  }
}

function acceptedContext() {
  const finding = buildCountryOutageSeriesExtremaFinding(fixture())
  return buildCountryOutageAnswerContext(finding)
}

function acceptedDraft(): DomeyeRendererDraft {
  const context = acceptedContext()
  return {
    schema_version: 'domeye_agent_renderer_draft_v2',
    lead: {
      fact_keys: ['minimum', 'minimum_at_utc'],
      text: `最低值为 ${context.facts.minimum.display_zh} ${context.unit_zh}，首次观测于 ${context.facts.minimum_at_utc.display_zh}。`,
    },
    fact_blocks: [
      {
        fact_keys: ['first', 'last'],
        text: `首值为 ${context.facts.first.display_zh}，末值为 ${context.facts.last.display_zh}。`,
      },
      {
        fact_keys: ['maximum', 'difference'],
        text: `最大值为 ${context.facts.maximum.display_zh}，极差为 ${context.facts.difference.display_zh}。`,
      },
    ],
    boundary: {
      boundary_codes: context.required_boundaries.map((item) => item.code),
      text: '地址量是固定前缀 IPv4 唯一地址并集，不是用户数；结果只表示 RRC25 的 BGP 控制面观测，不能据此判断全国状态、用户影响、原因、责任或恢复。',
    },
    next_step: null,
  }
}

function adversarialDraft(
  change: Record<string, unknown>,
): DomeyeRendererDraft {
  return { ...acceptedDraft(), ...change } as unknown as DomeyeRendererDraft
}

test('合格 TOOL-03/OP-01 Artifact 与 Receipt 确定性形成最小 Finding 和 Answer Context', () => {
  const input = fixture()
  const finding = buildCountryOutageSeriesExtremaFinding(input)
  const repeated = buildCountryOutageSeriesExtremaFinding(input)
  assert.deepEqual(repeated, finding)
  assert.equal(Check(DomeyeTypedFindingSchema, finding), true)
  assert.equal(finding.value_state, 'known')
  assert.deepEqual(finding.values, {
    first: 10_156_800,
    first_at_utc: '2026-02-27T00:10:00Z',
    last: 10_069_760,
    last_at_utc: '2026-03-11T00:00:00Z',
    minimum: 9_577_728,
    minimum_at_utc: '2026-02-28T14:35:00Z',
    maximum: 10_156_800,
    maximum_at_utc: '2026-02-27T00:10:00Z',
    difference: 579_072,
    net_change: -87_040,
  })
  assert.deepEqual(finding.artifact_refs, [
    input.series_artifact.artifact_id,
    input.extrema_artifact.artifact_id,
  ])
  assert.deepEqual(finding.receipt_refs, [
    input.series_receipt.receipt_id,
    input.extrema_receipt.receipt_id,
  ])
  assert.ok(Object.isFrozen(finding))

  const context = buildCountryOutageAnswerContext(finding)
  assert.equal(Check(DomeyeAnswerContextSchema, context), true)
  assert.equal(context.schema_version, 'domeye_agent_answer_context_v2')
  assert.deepEqual(context.facts, {
    minimum: { value: 9_577_728, display_zh: '9,577,728' },
    minimum_at_utc: {
      value: '2026-02-28T14:35:00Z',
      display_zh: '2026 年 2 月 28 日 14:35 UTC',
    },
    first: { value: 10_156_800, display_zh: '10,156,800' },
    last: { value: 10_069_760, display_zh: '10,069,760' },
    maximum: { value: 10_156_800, display_zh: '10,156,800' },
    difference: { value: 579_072, display_zh: '579,072' },
  })
  assert.deepEqual(
    context.required_boundaries,
    COUNTRY_OUTAGE_REQUIRED_ANSWER_BOUNDARIES,
  )
  const serialized = JSON.stringify(context)
  assert.doesNotMatch(
    serialized,
    /candidate|finding_id|context_id|digest|sha256|receipt|artifact|evidence|path|usage/iu,
  )
  assert.deepEqual(
    finding.limitation_codes,
    COUNTRY_OUTAGE_MANDATORY_LIMITATIONS.map((item) => item.code),
  )
  assert.ok(Object.isFrozen(context))
})

test('不合格或跨身份 OP-01 Artifact 不能形成 Finding', () => {
  const input = fixture()
  const conflicting = {
    ...input.extrema_artifact,
    candidate_id: 'another-candidate',
  } as DomeyeArtifactEnvelope
  assert.throws(
    () => buildCountryOutageSeriesExtremaFinding({
      ...input,
      extrema_artifact: conflicting,
    }),
    (error: unknown) => {
      assert.ok(error instanceof DomeyeFindingAnswerError)
      assert.ok([
        'artifact_receipt_conflict',
        'identity_conflict',
      ].includes(error.code))
      return true
    },
  )
})

test('正常 Renderer 草稿通过确定性 Guard', async () => {
  const context = acceptedContext()
  const draft = acceptedDraft()
  assert.equal(Check(DomeyeRendererDraftSchema, draft), true)
  const assessment = assessCountryOutageAnswerStyle(context, draft)
  assert.equal(Check(DomeyeAnswerStyleAssessmentSchema, assessment), true)
  assert.equal(assessment.passed, true)
  assert.equal(
    assessment.normalization_algorithm_id,
    'unicode-nfc-collapse-whitespace-intl-segmenter-zh-v1',
  )
  assert.deepEqual(assessment.realized_fact_keys, [
    'minimum',
    'minimum_at_utc',
    'first',
    'last',
    'maximum',
    'difference',
  ])
  assert.equal(assessment.counts.fact_block_count, 2)
  assert.equal(assessment.counts.boundary_block_count, 1)
  const decision = guardCountryOutageResponse(context, draft)
  assert.equal(decision.decision, 'pass')
  assert.equal(decision.assessment_status, 'evaluated')
  assert.equal(decision.guarded_text,
    composeCountryOutageRendererDraftText(draft))
  assert.equal(decision.style_assessment.passed, true)
  assert.equal(Check(
    DomeyeResponseGuardDecisionSchema,
    decision,
  ), true)
  let calls = 0
  const renderer: DomeyeAnswerRenderer = {
    async render() {
      calls += 1
      return draft
    },
  }
  const result = await composeCountryOutageAnswer(context, renderer)
  assert.equal(calls, 1)
  assert.equal(result.source, 'renderer')
  assert.equal(result.answer, composeCountryOutageRendererDraftText(draft))
  assert.equal(result.answer_digest, decision.guarded_text_digest)
})

test('有限 grammar 允许非固定整段表达，但仍只消费合同事实和边界', () => {
  const context = acceptedContext()
  const draft: DomeyeRendererDraft = {
    schema_version: 'domeye_agent_renderer_draft_v2',
    lead: {
      fact_keys: ['minimum', 'minimum_at_utc'],
      text: `最小值是 ${context.facts.minimum.display_zh} ${context.unit_zh}；首次出现在 ${context.facts.minimum_at_utc.display_zh}。`,
    },
    fact_blocks: [{
      fact_keys: ['first', 'last', 'maximum', 'difference'],
      text: `首值是 ${context.facts.first.display_zh}；末值是 ${context.facts.last.display_zh}；最大点是 ${context.facts.maximum.display_zh}；极差是 ${context.facts.difference.display_zh}。`,
    }],
    boundary: {
      boundary_codes: context.required_boundaries.map((item) => item.code),
      text: '该地址量仅是固定前缀的 IPv4 唯一地址并集，并非真实用户数；上述结果仅反映 RRC25 控制面观测，也无法据此说明全国互联网状态、真实用户影响、原因、责任与真实恢复。',
    },
    next_step: null,
  }
  const decision = guardCountryOutageResponse(context, draft)
  assert.equal(decision.decision, 'pass')
  assert.equal(decision.style_assessment.passed, true)
})

test('J4：六事实、边界、篇幅、越界结论和内部泄露全部 fail closed', () => {
  const context = acceptedContext()
  const base = acceptedDraft()
  const cases: Array<{
    name: string
    draft: DomeyeRendererDraft
    reason: string
  }> = [
    {
      name: '改值',
      draft: adversarialDraft({
        lead: {
          ...base.lead,
          text: base.lead.text.replace('9,577,728', '9,577,729'),
        },
      }),
      reason: 'visible_fact_missing',
    },
    {
      name: '漏单位',
      draft: adversarialDraft({
        lead: {
          ...base.lead,
          text: base.lead.text.replace(` ${context.unit_zh}`, ''),
        },
      }),
      reason: 'unit_missing_or_duplicate',
    },
    {
      name: '漏事实 key',
      draft: adversarialDraft({
        fact_blocks: [
          base.fact_blocks[0]!,
          {
            ...base.fact_blocks[1]!,
            fact_keys: ['maximum'],
            text: `最大值为 ${context.facts.maximum.display_zh}。`,
          },
        ],
      }),
      reason: 'required_fact_missing',
    },
    {
      name: '重复事实',
      draft: adversarialDraft({
        fact_blocks: [
          base.fact_blocks[0]!,
          base.fact_blocks[1]!,
          {
            fact_keys: ['first'],
            text: `首值仍为 ${context.facts.first.display_zh}。`,
          },
        ],
      }),
      reason: 'duplicate_fact',
    },
    {
      name: '首值与末值标签值互换',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `首值为 ${context.facts.last.display_zh}，末值为 ${context.facts.first.display_zh}。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'expression_outside_contract_grammar',
    },
    {
      name: '最低值与最低时刻标签值互换',
      draft: adversarialDraft({
        lead: {
          ...base.lead,
          text: `最低值为 ${context.facts.minimum_at_utc.display_zh} ${context.unit_zh}，首次观测于 ${context.facts.minimum.display_zh}。`,
        },
      }),
      reason: 'expression_outside_contract_grammar',
    },
    {
      name: '最大值与极差标签值互换',
      draft: adversarialDraft({
        fact_blocks: [base.fact_blocks[0]!, {
          ...base.fact_blocks[1]!,
          text: `最大值为 ${context.facts.difference.display_zh}，极差为 ${context.facts.maximum.display_zh}。`,
        }],
      }),
      reason: 'expression_outside_contract_grammar',
    },
    {
      name: '漏必要边界',
      draft: adversarialDraft({
        boundary: {
          boundary_codes: base.boundary.boundary_codes.slice(1),
          text: '以上仅是 RRC25 的 BGP 控制面观测，不能据此判断全国状态、用户影响、原因、责任与恢复。',
        },
      }),
      reason: 'required_boundary_missing',
    },
    {
      name: '扩大观察范围',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 全国网络已经中断。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'forbidden_national_outage_claim',
    },
    {
      name: '用户影响',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 大量用户受影响。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'forbidden_user_impact_claim',
    },
    {
      name: '原因与责任',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 事件原因是运营商故障，责任在于运营商。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'forbidden_cause_claim',
    },
    {
      name: '恢复',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 事件已经恢复。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'forbidden_recovery_claim',
    },
    {
      name: '边界块夹带反转结论',
      draft: adversarialDraft({
        boundary: {
          ...base.boundary,
          text: `${base.boundary.text.slice(0, -1)}，但全国网络已经中断。`,
        },
      }),
      reason: 'boundary_contains_contrast_claim',
    },
    {
      name: '内部对象与摘要',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} Evidence：Candidate sha256:${'a'.repeat(64)}。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'internal_information_leak',
    },
    {
      name: '路径与 endpoint',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} /home/domeye/a.ts，http://10.0.0.1:9999/api/private。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'internal_information_leak',
    },
    {
      name: 'Context 外数字',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 附加值 11。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'content_outside_answer_context',
    },
    {
      name: '紧邻中文的 Context 外数字',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 另列11项。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'content_outside_answer_context',
    },
    {
      name: 'Context 外无数字事实',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 数据中心发生火灾。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'content_outside_answer_context',
    },
    {
      name: '额外结论：事件已经结束',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 事件已经结束。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'expression_outside_contract_grammar',
    },
    {
      name: '额外结论：网络状态正常',
      draft: adversarialDraft({
        boundary: {
          ...base.boundary,
          text: `${base.boundary.text.slice(0, -1)}，网络状态正常。`,
        },
      }),
      reason: 'boundary_outside_contract_grammar',
    },
    {
      name: '额外结论：观测结果可靠',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 观测结果可靠。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'expression_outside_contract_grammar',
    },
    {
      name: '额外结论：趋势已稳定',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 趋势已稳定。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'expression_outside_contract_grammar',
    },
    {
      name: '额外结论：可见性大幅下降',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 可见性大幅下降。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'expression_outside_contract_grammar',
    },
    {
      name: '额外结论：情况严重',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 情况严重。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'expression_outside_contract_grammar',
    },
    {
      name: '一般未知句子',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 这值得继续关注。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'expression_outside_contract_grammar',
    },
    {
      name: '中文约数或倍数换算',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 约三倍、百分之十，影响数百万。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'content_outside_answer_context',
    },
    {
      name: 'Context 外中文数词',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 另列三项。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'content_outside_answer_context',
    },
    {
      name: '已知事实被改成不确定判断',
      draft: adversarialDraft({
        lead: {
          ...base.lead,
          text: `最低值可能为 ${context.facts.minimum.display_zh} ${context.unit_zh}，首次观测于 ${context.facts.minimum_at_utc.display_zh}。`,
        },
      }),
      reason: 'content_outside_answer_context',
    },
    {
      name: 'lead 过长',
      draft: adversarialDraft({
        lead: { ...base.lead, text: `${base.lead.text}${'说明'.repeat(50)}` },
      }),
      reason: 'lead_too_long',
    },
    {
      name: '事实块超过三个',
      draft: adversarialDraft({
        fact_blocks: [
          ...base.fact_blocks,
          {
            fact_keys: ['first'],
            text: `首值为 ${context.facts.first.display_zh}。`,
          },
          {
            fact_keys: ['last'],
            text: `末值为 ${context.facts.last.display_zh}。`,
          },
        ],
      }),
      reason: 'too_many_fact_blocks',
    },
    {
      name: '全文超过 360 grapheme',
      draft: adversarialDraft({
        boundary: {
          ...base.boundary,
          text: `${base.boundary.text}${'边界说明'.repeat(100)}`,
        },
      }),
      reason: 'answer_too_long',
    },
    {
      name: '句数过多',
      draft: adversarialDraft({
        fact_blocks: [{
          ...base.fact_blocks[0]!,
          text: `${base.fact_blocks[0]!.text} 一。二。三。四。五。六。`,
        }, base.fact_blocks[1]!],
      }),
      reason: 'too_many_sentences',
    },
  ]
  for (const item of cases) {
    const result = guardCountryOutageResponse(context, item.draft)
    assert.equal(result.decision, 'block', item.name)
    assert.equal(Check(DomeyeResponseGuardDecisionSchema, result), true,
      `${item.name}: Guard 决策必须满足 v2 Schema`)
    assert.ok(result.reason_codes.includes(item.reason),
      `${item.name}: ${result.reason_codes.join(',')}`)
  }
})

test('J4：Guard block 后丢弃原草稿，同一 Context 回退且不再次调用 Renderer', async () => {
  const context = acceptedContext()
  const draft = acceptedDraft()
  const unsafeDraft = adversarialDraft({
    fact_blocks: [{
      ...draft.fact_blocks[0]!,
      text: `${draft.fact_blocks[0]!.text} 事件已经恢复。Bearer supersecret`,
    }, draft.fact_blocks[1]!],
  })
  let calls = 0
  const renderer: DomeyeAnswerRenderer = {
    async render() {
      calls += 1
      return unsafeDraft
    },
  }
  const result = await composeCountryOutageAnswer(context, renderer)
  assert.equal(calls, 1)
  assert.equal(result.source, 'deterministic_fallback')
  assert.equal(result.answer, renderCountryOutageDeterministicFallback(context))
  assert.doesNotMatch(result.answer, /Bearer|事件已经恢复。/)
  assert.notEqual(result.answer, composeCountryOutageRendererDraftText(unsafeDraft))
  assert.equal(result.guard_result.decision, 'block')
  assert.equal(result.guard_result.assessment_status, 'evaluated')
  assert.equal(result.guard_result.style_assessment?.passed, false)
})
