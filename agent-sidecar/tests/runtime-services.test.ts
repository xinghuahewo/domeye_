import assert from 'node:assert/strict'
import test from 'node:test'

import type {
  CountryOutageAsnPage,
  ObservationBatch,
  SnapshotIdentity,
} from '../src/domain/contracts.js'
import { DeterministicAcceptanceNarrator } from '../src/report/deterministic-narrator.js'
import { CountryOutageArtifactBuilder } from '../src/report/artifact-builder.js'
import {
  RuntimeCountryOutageQuestionService,
  RuntimeCountryOutageReportService,
} from '../src/runtime/index.js'

const reference = 'country_outage/2026-02-27 09:12:32/IR/1/r'
const incidentId = 'incident-runtime-test'
const publicationId = 'publication-runtime-test'
const cohortId = 'cohort-runtime-test'

function batch(): ObservationBatch {
  const envelope = {
    incident_id: incidentId,
    publication_id: publicationId,
    publication_state: 'published',
    observation_state: 'state_complete',
    revision: 1,
    data_through: '2026-02-28T15:00:00Z',
    is_final: true,
    window_start_utc: '2026-02-28T10:05:00Z',
    window_end_utc: '2026-02-28T10:15:00Z',
    cohort_id: cohortId,
  }
  return {
    resolution: {
      schema_version: 'country_outage_resolution_v2',
      incident_id: incidentId,
      publication_id: publicationId,
      legacy_reference: reference,
      event_type: 'country_outage',
      observation_state: 'state_complete',
      latest_revision: 1,
      data_mode: 'replay',
      data_through: envelope.data_through,
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
        legacy_reference: reference,
        event_type: 'country_outage',
        country_code: 'IR',
        country_name: '伊朗',
        display_name: '伊朗 BGP 路由观测',
      },
      observation_scope: {
        collector_id: 'rrc25',
        collector_ids: ['rrc25'],
        collector_count: 1,
        window_start_utc: envelope.window_start_utc,
        window_start_local: '2026-02-28T18:05:00+08:00',
        window_end_utc: envelope.window_end_utc,
        window_end_local: '2026-02-28T18:15:00+08:00',
        timezone: 'Asia/Shanghai',
        interval_seconds: 300,
        observation_count: 3,
        expected_observation_count: 3,
        missing_observation_count: 0,
        quality_status: 'pass',
        last_observation_at_utc: envelope.window_end_utc,
        last_observation_at_local: '2026-02-28T18:15:00+08:00',
      },
      cohort: {
        cohort_id: cohortId,
        denominator_policy: 'fixed_from_complete_rib',
        origin_asn_count: 563,
        prefix_vp_count: 384_767,
      },
      capabilities: {
        fixed_cohort: { state: 'available' },
        asn_matrix: { state: 'unavailable', reason: '测试未提供' },
        address_families: { state: 'unavailable', reason: '测试未提供' },
        update_activity: { state: 'unavailable', reason: '测试未提供' },
        country_resources: { state: 'unavailable', reason: '测试未提供' },
        normal_band: { state: 'unavailable', reason: '测试未提供' },
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
      series: [
        {
          observed_at_utc: '2026-02-28T10:05:00Z',
          observed_at_local: '2026-02-28T18:05:00+08:00',
          slot_state: 'observed',
          visible_prefix_vp_count: 367_215,
          visible_prefix_vp_ratio: 367_215 / 384_767,
        },
        {
          observed_at_utc: '2026-02-28T10:10:00Z',
          observed_at_local: '2026-02-28T18:10:00+08:00',
          slot_state: 'observed',
          visible_prefix_vp_count: 316_733,
          visible_prefix_vp_ratio: 316_733 / 384_767,
          visible_prefix_vp_delta: -50_482,
          visible_prefix_vp_ratio_delta_pp:
            -50_482 / 384_767 * 100,
        },
        {
          observed_at_utc: '2026-02-28T10:15:00Z',
          observed_at_local: '2026-02-28T18:15:00+08:00',
          slot_state: 'observed',
          visible_prefix_vp_count: 333_938,
          visible_prefix_vp_ratio: 333_938 / 384_767,
          visible_prefix_vp_delta: 17_205,
          visible_prefix_vp_ratio_delta_pp:
            17_205 / 384_767 * 100,
        },
      ],
      metric_extrema: {},
      resource_series: [],
      resource_metric_extrema: {},
      annotations: [],
    },
    audit: {
      ...envelope,
      schema_version: 'country_outage_audit_v2',
      quality_status: 'pass',
      missing_slot_count: 0,
      missing_slots: [],
      source_system: 'domeye',
      source_reference: reference,
      evidence_level: 'control_plane_observation',
      algorithm_version: 'runtime-test',
      mapping_version: 'runtime-test',
      verified_hashes: {},
      limitations: ['不能据此认定全国断网或用户影响。'],
    },
  }
}

function client() {
  return {
    async getObservationBatch(requestedReference: string) {
      assert.equal(requestedReference, reference)
      return batch()
    },
    async getAsns(
      _snapshot: SnapshotIdentity,
    ): Promise<CountryOutageAsnPage> {
      throw new Error('ASN 能力不可用时不应读取分页')
    },
  }
}

test('运行组合从同一冻结快照生成报告、下载与可展开追问', async () => {
  const phases: string[] = []
  const reportService = new RuntimeCountryOutageReportService({
    client: client(),
    narrator: new DeterministicAcceptanceNarrator(),
    artifactBuilder: new CountryOutageArtifactBuilder({
      async render() {
        return Buffer.from('%PDF-1.4\nruntime acceptance\n%%EOF\n')
      },
    }),
    now: () => new Date('2026-07-28T14:30:00Z'),
  })
  const generated = await reportService.generate({
    eventReference: reference,
    publicationId,
    revision: 1,
    signal: new AbortController().signal,
    onPhase(phase) {
      phases.push(phase)
    },
  })
  assert.deepEqual(phases, ['generating_report', 'validating'])
  assert.equal(generated.document.snapshot.collectorId, 'rrc25')
  assert.equal(generated.document.snapshot.publicationId, publicationId)
  assert.equal(generated.artifacts.markdown.status, 'ready')
  assert.equal(generated.artifacts.pdf.status, 'ready')
  assert.ok(generated.questionContext)

  const questionService = new RuntimeCountryOutageQuestionService()
  const answer = await questionService.answer({
    reportId: 'cor_runtime',
    report: generated.document,
    questionContext: generated.questionContext!,
    question: '最低覆盖率是多少？',
    evidenceMode: 'domeye_only',
    signal: new AbortController().signal,
  })
  assert.equal(answer.kind, 'fact')
  assert.match(answer.text, /82\.32%/)
  assert.ok(answer.evidenceRecords.length > 0)
  assert.ok(
    answer.evidenceRecords.every((record) =>
      record.statisticalScope.includes('RRC25'),
    ),
  )
  const allowed = new Set(generated.questionContext!.evidenceRefs)
  assert.ok(answer.evidenceRefs.every((ref) => allowed.has(ref)))
})

test('用户触发时固定的 publication 或 revision 不匹配时失败关闭', async () => {
  let rendered = false
  const reportService = new RuntimeCountryOutageReportService({
    client: client(),
    narrator: new DeterministicAcceptanceNarrator(),
    artifactBuilder: new CountryOutageArtifactBuilder({
      async render() {
        rendered = true
        return Buffer.from('%PDF-1.4\nshould-not-render\n%%EOF\n')
      },
    }),
  })
  await assert.rejects(
    reportService.generate({
      eventReference: reference,
      publicationId: 'publication-other',
      revision: 2,
      signal: new AbortController().signal,
      onPhase() {},
    }),
    /快照与用户触发时固定的身份不一致/,
  )
  assert.equal(rendered, false)
})

test('报告读取阶段取消后不再读取 ASN、调用模型或生成制品', async () => {
  let markReadingStarted!: () => void
  const readingStarted = new Promise<void>((resolve) => {
    markReadingStarted = resolve
  })
  let observationReads = 0
  let asnReads = 0
  let modelCalls = 0
  let artifactRenders = 0
  let observedSignal: AbortSignal | undefined
  const phases: string[] = []
  const baseNarrator = new DeterministicAcceptanceNarrator()
  const reportService = new RuntimeCountryOutageReportService({
    client: {
      async getObservationBatch(
        requestedReference: string,
        signal?: AbortSignal,
      ): Promise<ObservationBatch> {
        assert.equal(requestedReference, reference)
        assert.ok(signal)
        observationReads += 1
        observedSignal = signal
        markReadingStarted()
        if (!signal.aborted) {
          await new Promise<void>((resolve) => {
            signal.addEventListener('abort', () => resolve(), {
              once: true,
            })
          })
        }
        signal.throwIfAborted()
        return batch()
      },
      async getAsns(
        _snapshot: SnapshotIdentity,
        _query?: unknown,
        _signal?: AbortSignal,
      ): Promise<CountryOutageAsnPage> {
        asnReads += 1
        throw new Error('取消后不应读取 ASN')
      },
    },
    narrator: {
      identity: baseNarrator.identity,
      validatorRulesVersion: baseNarrator.validatorRulesVersion,
      skillBundleSha256: baseNarrator.skillBundleSha256,
      async generate(
        request: Parameters<
          DeterministicAcceptanceNarrator['generate']
        >[0],
      ) {
        modelCalls += 1
        return await baseNarrator.generate(request)
      },
    },
    artifactBuilder: new CountryOutageArtifactBuilder({
      async render() {
        artifactRenders += 1
        return Buffer.from('%PDF-1.4\nshould-not-render\n%%EOF\n')
      },
    }),
  })
  const controller = new AbortController()
  const pending = reportService.generate({
    eventReference: reference,
    publicationId,
    revision: 1,
    signal: controller.signal,
    onPhase(phase) {
      phases.push(phase)
    },
  })
  await readingStarted
  assert.equal(observedSignal, controller.signal)
  controller.abort()
  await assert.rejects(
    pending,
    (error: unknown) =>
      error instanceof DOMException && error.name === 'AbortError',
  )
  await new Promise<void>((resolve) => setImmediate(resolve))

  assert.equal(observationReads, 1)
  assert.equal(asnReads, 0)
  assert.equal(modelCalls, 0)
  assert.equal(artifactRenders, 0)
  assert.deepEqual(phases, [])
})
