import assert from 'node:assert/strict'
import test from 'node:test'

import {
  AcceptanceReportBindingError,
  assertAcceptanceReportMatchesRepresentativeEvent,
  assertAcceptanceReportReference,
} from '../src/cli/acceptance-report-binding.js'
import {
  loadFormalCountryOutageAcceptanceRuntime,
  type FormalCountryOutageAcceptanceRuntime,
} from '../src/formal-acceptance-runtime.js'
import type {
  CompiledCountryOutageReport,
} from '../src/report/report-compiler.js'

function observationGrid(
  runtime: FormalCountryOutageAcceptanceRuntime,
): string[] {
  const representative = runtime.representativeEvent
  const start = Date.parse(representative.windowStartUtc)
  return Array.from(
    { length: representative.expectedObservationCount },
    (_, index) =>
      new Date(
        start + index * representative.intervalSeconds * 1_000,
      ).toISOString().replace('.000Z', 'Z'),
  )
}

function representativeCompiledReport(
  runtime: FormalCountryOutageAcceptanceRuntime,
): CompiledCountryOutageReport {
  const representative = runtime.representativeEvent
  const event = {
    incident_id: representative.incidentId,
    legacy_reference: representative.eventReference,
    event_type: 'country_outage' as const,
    country_code: 'IR',
    country_name: '伊朗',
    display_name: '伊朗 BGP 路由观测',
  }
  const snapshot = {
    incidentId: representative.incidentId,
    publicationId: representative.publicationId,
    revision: representative.revision,
    dataThrough: representative.dataThrough,
    isFinal: representative.isFinal,
    cohortId: representative.cohortId,
    collectorId: representative.collectorId,
    windowStartUtc: representative.windowStartUtc,
    windowEndUtc: representative.windowEndUtc,
  }
  const grid = observationGrid(runtime)
  const factSetId = 'facts_acceptance_binding_test'
  return {
    document: {
      event,
      snapshot,
      factSetId,
    },
    evidence: {
      facts: {
        event: { ...event },
        snapshot: { ...snapshot },
        factSetId,
        scope: {
          collector_id: representative.collectorId,
          collector_ids: [representative.collectorId],
          collector_count: 1,
          window_start_utc: representative.windowStartUtc,
          window_end_utc: representative.windowEndUtc,
          interval_seconds: representative.intervalSeconds,
          observation_count:
            representative.expectedObservationCount,
          expected_observation_count:
            representative.expectedObservationCount,
          missing_observation_count: 0,
        },
        cohort: {
          cohort_id: representative.cohortId,
        },
        quality: {
          missingSlotCount: 0,
        },
        series: grid.map((observedAtUtc) => ({
          observed_at_utc: observedAtUtc,
          observed_at_local: observedAtUtc,
          slot_state: 'observed' as const,
        })),
        resourceSeries: grid.map((observedAtUtc) => ({
          observed_at_utc: observedAtUtc,
          observed_at_local: observedAtUtc,
        })),
      },
      asnPages: [
        {
          incident_id: representative.incidentId,
          publication_id: representative.publicationId,
          revision: representative.revision,
          data_through: representative.dataThrough,
          is_final: representative.isFinal,
          cohort_id: representative.cohortId,
          window_start_utc: representative.windowStartUtc,
          window_end_utc: representative.windowEndUtc,
        },
      ],
    },
  } as unknown as CompiledCountryOutageReport
}

test('验收报告绑定只接受 v2 固定 reference 和完整 60 槽身份', () => {
  const runtime = loadFormalCountryOutageAcceptanceRuntime()
  const compiled = representativeCompiledReport(runtime)

  assert.doesNotThrow(() =>
    assertAcceptanceReportReference(
      runtime.representativeEvent.eventReference,
      runtime,
    ),
  )
  assert.doesNotThrow(() =>
    assertAcceptanceReportMatchesRepresentativeEvent(
      compiled,
      runtime,
    ),
  )
})

test('验收报告 CLI 在任何读取前机械拒绝非代表事件 reference', () => {
  const runtime = loadFormalCountryOutageAcceptanceRuntime()
  for (const reference of [
    `${runtime.representativeEvent.eventReference} `,
    runtime.representativeEvent.eventReference.replace('/IR/', '/US/'),
    runtime.representativeEvent.eventReference.replace(' ', '+'),
  ]) {
    assert.throws(
      () => assertAcceptanceReportReference(reference, runtime),
      (error: unknown) =>
        error instanceof AcceptanceReportBindingError &&
        error.code === 'acceptance_reference_mismatch',
    )
  }
})

test('生成后任一事件、快照、窗口、cohort 或 RRC25 身份漂移均失败关闭', () => {
  const runtime = loadFormalCountryOutageAcceptanceRuntime()
  const mutations: readonly [
    string,
    (compiled: CompiledCountryOutageReport) => void,
  ][] = [
    [
      '报告事件',
      (compiled) => {
        compiled.document.event.incident_id = 'incident-drift'
      },
    ],
    [
      '事实事件',
      (compiled) => {
        compiled.evidence.facts.event.legacy_reference =
          'country_outage/drift/IR/1/r'
      },
    ],
    [
      '报告快照',
      (compiled) => {
        compiled.document.snapshot.publicationId =
          'publication-drift'
      },
    ],
    [
      '事实快照',
      (compiled) => {
        compiled.evidence.facts.snapshot.cohortId = 'cohort-drift'
      },
    ],
    [
      '窗口',
      (compiled) => {
        compiled.evidence.facts.scope.window_end_utc =
          '2026-02-28T14:55:00Z'
      },
    ],
    [
      '唯一 RRC25',
      (compiled) => {
        compiled.evidence.facts.scope.collector_ids = [
          'rrc25',
          'rrc24',
        ]
      },
    ],
    [
      'cohort',
      (compiled) => {
        compiled.evidence.facts.cohort.cohort_id = 'cohort-drift'
      },
    ],
    [
      'ASN 页快照',
      (compiled) => {
        compiled.evidence.asnPages[0]!.revision = 2
      },
    ],
  ]

  for (const [name, mutate] of mutations) {
    const compiled = representativeCompiledReport(runtime)
    mutate(compiled)
    assert.throws(
      () =>
        assertAcceptanceReportMatchesRepresentativeEvent(
          compiled,
          runtime,
        ),
      (error: unknown) =>
        error instanceof AcceptanceReportBindingError &&
        error.code === 'acceptance_report_identity_mismatch',
      name,
    )
  }
})

test('生成后可见性与资源任一缺槽、错槽或非观测槽均失败关闭', () => {
  const runtime = loadFormalCountryOutageAcceptanceRuntime()
  const mutations: readonly [
    string,
    (compiled: CompiledCountryOutageReport) => void,
  ][] = [
    [
      '可见性缺槽',
      (compiled) => {
        compiled.evidence.facts.series.pop()
      },
    ],
    [
      '可见性错槽',
      (compiled) => {
        compiled.evidence.facts.series[10]!.observed_at_utc =
          '2026-02-28T10:56:00Z'
      },
    ],
    [
      '可见性非观测槽',
      (compiled) => {
        compiled.evidence.facts.series[10]!.slot_state =
          'processing_gap'
      },
    ],
    [
      '资源缺槽',
      (compiled) => {
        compiled.evidence.facts.resourceSeries.pop()
      },
    ],
    [
      '资源错槽',
      (compiled) => {
        compiled.evidence.facts.resourceSeries[10]!
          .observed_at_utc = '2026-02-28T10:56:00Z'
      },
    ],
  ]

  for (const [name, mutate] of mutations) {
    const compiled = representativeCompiledReport(runtime)
    mutate(compiled)
    assert.throws(
      () =>
        assertAcceptanceReportMatchesRepresentativeEvent(
          compiled,
          runtime,
        ),
      (error: unknown) =>
        error instanceof AcceptanceReportBindingError &&
        error.code === 'acceptance_report_identity_mismatch',
      name,
    )
  }
})
