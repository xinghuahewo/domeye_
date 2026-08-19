import assert from 'node:assert/strict'
import test from 'node:test'
import { Check } from 'typebox/value'

import {
  DomeyeAnswerContextSchema,
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
  buildCountryOutageAnswerContext,
  buildCountryOutageSeriesExtremaFinding,
  composeCountryOutageAnswer,
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
  return buildCountryOutageAnswerContext(finding, SHA_F)
}

function acceptedDraft(): DomeyeRendererDraft {
  const context = acceptedContext()
  return {
    schema_version: 'domeye_agent_renderer_draft_v1',
    context_id: context.context_id,
    finding_id: context.finding.finding_id,
    candidate_id: context.candidate_id,
    publication_id: context.data_identity.publication_id,
    revision: context.data_identity.revision,
    collector_id: context.data_identity.collector_id,
    window_start_utc: context.data_identity.window_start_utc,
    window_end_utc: context.data_identity.window_end_utc,
    metric: context.finding.metric,
    unit: context.finding.unit,
    values: context.finding.values,
    observer_scope_zh: context.observer_scope_zh,
    limitations_zh: context.mandatory_limitations_zh,
    evidence_refs: context.evidence_refs,
    text: renderCountryOutageDeterministicFallback(context),
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

  const context = buildCountryOutageAnswerContext(finding, SHA_F)
  assert.equal(Check(DomeyeAnswerContextSchema, context), true)
  assert.equal(context.finding.finding_id, finding.finding_id)
  assert.equal(context.observer_scope_zh,
    'RRC25 单一观察点的 BGP 控制面观测')
  assert.equal(context.mandatory_limitations_zh.length, 4)
  assert.deepEqual(context.mandatory_limitations_zh,
    COUNTRY_OUTAGE_MANDATORY_LIMITATIONS.map((item) => item.text))
  assert.deepEqual(context.evidence_refs, finding.evidence_refs)
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
  assert.deepEqual(guardCountryOutageResponse(context, draft), {
    schema_version: 'domeye_agent_response_guard_v1',
    decision: 'pass',
    reason_codes: [],
  })
  assert.equal(Check(
    DomeyeResponseGuardDecisionSchema,
    guardCountryOutageResponse(context, draft),
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
  assert.equal(result.answer, draft.text)
})

test('J4：改值、改单位、漏限制、越界结论和敏感信息全部 fail closed', () => {
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
        values: { ...base.values, minimum: base.values.minimum! + 1 },
      }),
      reason: 'number_mismatch',
    },
    {
      name: '改单位',
      draft: adversarialDraft({ unit: 'user' }),
      reason: 'unit_mismatch',
    },
    {
      name: '漏 limitation',
      draft: adversarialDraft({
        limitations_zh: base.limitations_zh.slice(1),
        text: base.text.replace(base.limitations_zh[0]!, ''),
      }),
      reason: 'mandatory_limitation_missing',
    },
    {
      name: '扩大观察范围',
      draft: adversarialDraft({
        observer_scope_zh: '全国互联网事实',
        text: `${base.text}\n全国网络已经中断。`,
      }),
      reason: 'forbidden_national_outage_claim',
    },
    {
      name: '用户影响',
      draft: adversarialDraft({
        text: `${base.text}\n大量用户受影响。`,
      }),
      reason: 'forbidden_user_impact_claim',
    },
    {
      name: '原因',
      draft: adversarialDraft({
        text: `${base.text}\n事件原因是运营商故障。`,
      }),
      reason: 'forbidden_cause_claim',
    },
    {
      name: '责任',
      draft: adversarialDraft({
        text: `${base.text}\n责任在于运营商。`,
      }),
      reason: 'forbidden_responsibility_claim',
    },
    {
      name: '恢复',
      draft: adversarialDraft({
        text: `${base.text}\n事件已经恢复。`,
      }),
      reason: 'forbidden_recovery_claim',
    },
    {
      name: '观测时间冒充事件时间',
      draft: adversarialDraft({
        text: `${base.text}\n事件实际发生于 ${base.values.minimum_at_utc}。`,
      }),
      reason: 'observed_time_overstated',
    },
    {
      name: '内部 endpoint',
      draft: adversarialDraft({
        text: `${base.text}\nhttp://10.0.0.1:9999/api/private`,
      }),
      reason: 'sensitive_endpoint_detected',
    },
    {
      name: '凭据',
      draft: adversarialDraft({
        text: `${base.text}\nBearer supersecret`,
      }),
      reason: 'sensitive_credential_detected',
    },
    {
      name: 'Context 外数字',
      draft: adversarialDraft({ text: `${base.text}\n附加值 11。` }),
      reason: 'content_outside_answer_context',
    },
    {
      name: 'Context 外无数字事实',
      draft: adversarialDraft({ text: `${base.text}\n数据中心发生火灾。` }),
      reason: 'content_outside_answer_context',
    },
    {
      name: 'publication 身份变化',
      draft: adversarialDraft({ publication_id: 'other-publication' }),
      reason: 'data_identity_mismatch',
    },
  ]
  for (const item of cases) {
    const result = guardCountryOutageResponse(context, item.draft)
    assert.equal(result.decision, 'block', item.name)
    assert.ok(result.reason_codes.includes(item.reason),
      `${item.name}: ${result.reason_codes.join(',')}`)
  }
})

test('J4：Guard block 后丢弃原草稿，同一 Context 回退且不再次调用 Renderer', async () => {
  const context = acceptedContext()
  const unsafeText = `${acceptedDraft().text}\n事件已经恢复。Bearer supersecret`
  let calls = 0
  const renderer: DomeyeAnswerRenderer = {
    async render() {
      calls += 1
      return adversarialDraft({ text: unsafeText })
    },
  }
  const result = await composeCountryOutageAnswer(context, renderer)
  assert.equal(calls, 1)
  assert.equal(result.source, 'deterministic_fallback')
  assert.equal(result.answer, renderCountryOutageDeterministicFallback(context))
  assert.doesNotMatch(result.answer, /Bearer|事件已经恢复。/)
  assert.equal(result.guard_result.decision, 'block')
  assert.deepEqual(Object.keys(result.guard_result).sort(), [
    'decision',
    'reason_codes',
    'schema_version',
  ])
})
