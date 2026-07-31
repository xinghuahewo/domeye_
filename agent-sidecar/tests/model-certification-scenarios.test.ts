import assert from 'node:assert/strict'
import test from 'node:test'

import {
  assertAsnPageIdentity,
  assertBatchIdentity,
} from '../src/domain/domeye-client.js'
import { DeterministicAcceptanceNarrator } from '../src/report/deterministic-narrator.js'
import { CountryOutageReportCompiler } from '../src/report/report-compiler.js'
import {
  A4_CERTIFICATION_SCENARIOS,
  A4_CERTIFIED_INPUT_SCOPE,
  A4_CERTIFIED_SCENARIO_SET_ID,
  createA4CertificationScenarioBatch,
  createA4CertificationScenarioClient,
} from '../src/pi/model-certification-scenarios.js'
import {
  A4_REFERENCE,
  a4AsnPage,
  a4ObservationBatch,
} from './helpers/a4-country-outage-fixture.js'

function baseClient() {
  return {
    async getObservationBatch(reference: string) {
      assert.equal(reference, A4_REFERENCE)
      return structuredClone(a4ObservationBatch())
    },
    async getAsns() {
      return structuredClone(a4AsnPage())
    },
  }
}

test('A4 合法场景集身份与三类认证专用场景固定', () => {
  assert.equal(
    A4_CERTIFIED_SCENARIO_SET_ID,
    'country-outage-rrc25-legal-scenarios-v2',
  )
  assert.equal(
    A4_CERTIFIED_INPUT_SCOPE,
    'legal_country_outage_rrc25_v1',
  )
  assert.deepEqual(
    A4_CERTIFICATION_SCENARIOS.map((scenario) => [
      scenario.id,
      scenario.certificationOnly,
      scenario.synthetic,
      scenario.purpose,
    ]),
    [
      [
        'capability-degraded-final',
        true,
        true,
        'capability_degradation',
      ],
      [
        'direction-end-above-start-final',
        true,
        true,
        'direction_change',
      ],
      [
        'non-final-snapshot',
        true,
        true,
        'non_final_snapshot',
      ],
    ],
  )
})

test('能力降级场景只保留基础章节且不会读取 ASN', async () => {
  const client = createA4CertificationScenarioClient(
    baseClient(),
    'capability-degraded-final',
  )
  const batch = await client.getObservationBatch(A4_REFERENCE)
  assert.doesNotThrow(() => assertBatchIdentity(batch))
  assert.equal(batch.overview.capabilities.asn_matrix?.state, 'unavailable')
  assert.equal(batch.overview.capabilities.update_activity?.state, 'building')

  const document = await new CountryOutageReportCompiler({
    client,
    narrator: new DeterministicAcceptanceNarrator(),
    now: () => new Date('2026-07-29T10:00:00Z'),
  }).compile(A4_REFERENCE)
  assert.equal(document.validation.passed, true)
  assert.deepEqual(
    document.draft.sections.map((section) => section.id),
    ['scope', 'key_numbers', 'visibility', 'end_state', 'assessment'],
  )
})

test('方向变化场景结束高于起点且完整报告仍通过 v5', async () => {
  const source = a4ObservationBatch()
  const batch = createA4CertificationScenarioBatch(
    source,
    'direction-end-above-start-final',
  )
  assert.doesNotThrow(() => assertBatchIdentity(batch))
  assert.ok(
    batch.series.series.at(-1)!.visible_prefix_vp_count! >
      batch.series.series[0]!.visible_prefix_vp_count!,
  )

  const client = createA4CertificationScenarioClient(
    baseClient(),
    'direction-end-above-start-final',
  )
  const document = await new CountryOutageReportCompiler({
    client,
    narrator: new DeterministicAcceptanceNarrator(),
    now: () => new Date('2026-07-29T10:00:00Z'),
  }).compile(A4_REFERENCE)
  assert.equal(document.validation.passed, true)
})

test('非 final 场景身份贯穿报告与 ASN 且保留 dataThrough', async () => {
  const client = createA4CertificationScenarioClient(
    baseClient(),
    'non-final-snapshot',
  )
  const batch = await client.getObservationBatch(A4_REFERENCE)
  assert.doesNotThrow(() => assertBatchIdentity(batch))
  assert.equal(batch.resolution.is_final, false)
  assert.equal(batch.overview.is_final, false)
  assert.equal(batch.series.is_final, false)
  assert.equal(batch.audit.is_final, false)
  assert.equal(batch.overview.data_through, '2026-02-28T15:00:00Z')

  const page = await client.getAsns(
    {
      incidentId: batch.overview.incident_id,
      publicationId: batch.overview.publication_id,
      revision: batch.overview.revision,
      dataThrough: batch.overview.data_through,
      isFinal: false,
      cohortId: batch.overview.cohort!.cohort_id,
      collectorId: 'rrc25',
      windowStartUtc: batch.overview.window_start_utc,
      windowEndUtc: batch.overview.window_end_utc,
    },
  )
  assert.equal(page.is_final, false)
  assert.doesNotThrow(() =>
    assertAsnPageIdentity(page, {
      incidentId: batch.overview.incident_id,
      publicationId: batch.overview.publication_id,
      revision: batch.overview.revision,
      dataThrough: batch.overview.data_through,
      isFinal: false,
      cohortId: batch.overview.cohort!.cohort_id,
      collectorId: 'rrc25',
      windowStartUtc: batch.overview.window_start_utc,
      windowEndUtc: batch.overview.window_end_utc,
    }),
  )

  const document = await new CountryOutageReportCompiler({
    client,
    narrator: new DeterministicAcceptanceNarrator(),
    now: () => new Date('2026-07-29T10:00:00Z'),
  }).compile(A4_REFERENCE)
  assert.equal(document.snapshot.isFinal, false)
  assert.equal(document.snapshot.dataThrough, '2026-02-28T15:00:00Z')
  assert.equal(document.validation.passed, true)
  assert.ok(
    document.draft.unknowns.some((item) => /窗口.*之后|之后.*窗口/.test(item)),
  )
})
