import { isDeepStrictEqual } from 'node:util'

import type {
  FormalCountryOutageAcceptanceRuntime,
} from '../formal-acceptance-runtime.js'
import type {
  CompiledCountryOutageReport,
} from '../report/report-compiler.js'

export type AcceptanceReportBindingErrorCode =
  | 'acceptance_reference_mismatch'
  | 'acceptance_report_identity_mismatch'

export class AcceptanceReportBindingError extends Error {
  constructor(
    readonly code: AcceptanceReportBindingErrorCode,
    message: string,
  ) {
    super(message)
    this.name = 'AcceptanceReportBindingError'
  }
}

function identityMismatch(name: string): never {
  throw new AcceptanceReportBindingError(
    'acceptance_report_identity_mismatch',
    `验收报告 ${name} 与 v2 representative_event 不一致`,
  )
}

function assertEqual(
  name: string,
  actual: unknown,
  expected: unknown,
): void {
  if (!isDeepStrictEqual(actual, expected)) {
    identityMismatch(name)
  }
}

function expectedCountryCode(
  runtime: FormalCountryOutageAcceptanceRuntime,
): string {
  const segments =
    runtime.representativeEvent.eventReference.split('/')
  const countryCode = segments[2]
  if (!countryCode || !/^[A-Z]{2}$/.test(countryCode)) {
    identityMismatch('event.country_code')
  }
  return countryCode
}

function expectedObservationGrid(
  runtime: FormalCountryOutageAcceptanceRuntime,
): string[] {
  const representative = runtime.representativeEvent
  const start = Date.parse(representative.windowStartUtc)
  const end = Date.parse(representative.windowEndUtc)
  const step = representative.intervalSeconds * 1_000
  const count = representative.expectedObservationCount
  if (
    !Number.isFinite(start) ||
    !Number.isFinite(end) ||
    step <= 0 ||
    start + (count - 1) * step !== end
  ) {
    identityMismatch('representative_event observation grid')
  }
  return Array.from(
    { length: count },
    (_, index) =>
      new Date(start + index * step)
        .toISOString()
        .replace('.000Z', 'Z'),
  )
}

function snapshotProjection(
  value: CompiledCountryOutageReport['document']['snapshot'],
): Record<string, unknown> {
  return {
    incidentId: value.incidentId,
    publicationId: value.publicationId,
    revision: value.revision,
    dataThrough: value.dataThrough,
    isFinal: value.isFinal,
    cohortId: value.cohortId,
    collectorId: value.collectorId,
    windowStartUtc: value.windowStartUtc,
    windowEndUtc: value.windowEndUtc,
  }
}

function expectedSnapshotProjection(
  runtime: FormalCountryOutageAcceptanceRuntime,
): Record<string, unknown> {
  const representative = runtime.representativeEvent
  return {
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
}

export function assertAcceptanceReportReference(
  reference: string,
  runtime: FormalCountryOutageAcceptanceRuntime,
): void {
  if (reference !== runtime.representativeEvent.eventReference) {
    throw new AcceptanceReportBindingError(
      'acceptance_reference_mismatch',
      '验收报告 CLI 只允许 v2 representative_event.event_reference',
    )
  }
}

/**
 * 确定性验收报告只能来自 v2 固定代表事件。该断言在任何制品渲染或写盘前
 * 执行，覆盖报告与事实集的事件、快照、窗口、cohort、唯一 RRC25 及完整
 * 60 槽时间网格，防止“生成成功”被误当成代表事件验收通过。
 */
export function assertAcceptanceReportMatchesRepresentativeEvent(
  compiled: CompiledCountryOutageReport,
  runtime: FormalCountryOutageAcceptanceRuntime,
): void {
  const representative = runtime.representativeEvent
  const { document, evidence } = compiled
  const { facts } = evidence
  const countryCode = expectedCountryCode(runtime)
  const expectedGrid = expectedObservationGrid(runtime)

  assertEqual('document.event', {
    incidentId: document.event.incident_id,
    legacyReference: document.event.legacy_reference,
    eventType: document.event.event_type,
    countryCode: document.event.country_code,
  }, {
    incidentId: representative.incidentId,
    legacyReference: representative.eventReference,
    eventType: 'country_outage',
    countryCode,
  })
  assertEqual('facts.event', {
    incidentId: facts.event.incident_id,
    legacyReference: facts.event.legacy_reference,
    eventType: facts.event.event_type,
    countryCode: facts.event.country_code,
  }, {
    incidentId: representative.incidentId,
    legacyReference: representative.eventReference,
    eventType: 'country_outage',
    countryCode,
  })
  assertEqual(
    'document.snapshot',
    snapshotProjection(document.snapshot),
    expectedSnapshotProjection(runtime),
  )
  assertEqual(
    'facts.snapshot',
    snapshotProjection(facts.snapshot),
    expectedSnapshotProjection(runtime),
  )
  assertEqual('fact_set_id', document.factSetId, facts.factSetId)

  assertEqual('facts.scope', {
    collectorId: facts.scope.collector_id,
    collectorIds: facts.scope.collector_ids,
    collectorCount: facts.scope.collector_count,
    windowStartUtc: facts.scope.window_start_utc,
    windowEndUtc: facts.scope.window_end_utc,
    intervalSeconds: facts.scope.interval_seconds,
    observationCount: facts.scope.observation_count,
    expectedObservationCount:
      facts.scope.expected_observation_count,
    missingObservationCount:
      facts.scope.missing_observation_count ?? 0,
  }, {
    collectorId: representative.collectorId,
    collectorIds: [representative.collectorId],
    collectorCount: 1,
    windowStartUtc: representative.windowStartUtc,
    windowEndUtc: representative.windowEndUtc,
    intervalSeconds: representative.intervalSeconds,
    observationCount: representative.expectedObservationCount,
    expectedObservationCount:
      representative.expectedObservationCount,
    missingObservationCount: 0,
  })
  assertEqual(
    'facts.cohort.cohort_id',
    facts.cohort.cohort_id,
    representative.cohortId,
  )
  assertEqual(
    'facts.quality.missing_slot_count',
    facts.quality.missingSlotCount,
    0,
  )
  assertEqual(
    'facts.series observation grid',
    facts.series.map((slot) => slot.observed_at_utc),
    expectedGrid,
  )
  assertEqual(
    'facts.series slot states',
    facts.series.map((slot) => slot.slot_state),
    expectedGrid.map(() => 'observed'),
  )
  assertEqual(
    'facts.resource_series observation grid',
    facts.resourceSeries.map((slot) => slot.observed_at_utc),
    expectedGrid,
  )

  for (const page of evidence.asnPages) {
    assertEqual('ASN page snapshot', {
      incidentId: page.incident_id,
      publicationId: page.publication_id,
      revision: page.revision,
      dataThrough: page.data_through,
      isFinal: page.is_final,
      cohortId: page.cohort_id,
      windowStartUtc: page.window_start_utc,
      windowEndUtc: page.window_end_utc,
    }, {
      incidentId: representative.incidentId,
      publicationId: representative.publicationId,
      revision: representative.revision,
      dataThrough: representative.dataThrough,
      isFinal: representative.isFinal,
      cohortId: representative.cohortId,
      windowStartUtc: representative.windowStartUtc,
      windowEndUtc: representative.windowEndUtc,
    })
  }
}
