import type {
  CapabilityState,
  CountryOutageFactSet,
  DerivedNumericFact,
  FactProvenance,
  KeyVisibilityPoint,
  ObservationBatch,
  ResourceSlot,
  ReportEligibility,
  SnapshotEnvelope,
  SnapshotIdentity,
  VisibilitySlot,
} from './contracts.js'
import {
  ReportDataInsufficientError,
  UnsupportedCollectorError,
} from './errors.js'
import { assertBatchIdentity } from './domeye-client.js'
import {
  canonicalJsonSha256,
  compareUnicodeCodePoints,
} from '../shared/deterministic-json.js'

export const COUNTRY_OUTAGE_CAPABILITY_VOCABULARY = Object.freeze([
  'legacy_summary',
  'fixed_cohort',
  'country_resources',
  'update_activity',
  'address_families',
  'asn_matrix',
  'audit',
  'normal_band',
] as const)

const CAPABILITY_STATES = new Set<CapabilityState['state']>([
  'available',
  'building',
  'unavailable',
  'not_applicable',
  'unknown',
])

function normalizedCapabilities(
  value: Record<string, CapabilityState>,
): Record<string, CapabilityState> {
  const normalized = Object.fromEntries(
    Object.entries(value)
      .sort(([left], [right]) =>
        compareUnicodeCodePoints(left, right),
      )
      .map(([capability, item]) => [
        capability,
        item && CAPABILITY_STATES.has(item.state)
          ? item
          : {
              state: 'unknown' as const,
              reason: 'Domeye 返回了无法识别的能力状态',
            },
      ]),
  )
  for (const capability of COUNTRY_OUTAGE_CAPABILITY_VOCABULARY) {
    normalized[capability] ??= {
      state: 'unknown',
      reason: '当前快照未声明该固定能力',
    }
  }
  return Object.fromEntries(
    Object.entries(normalized).sort(([left], [right]) =>
      compareUnicodeCodePoints(left, right),
    ),
  )
}

function provenance(
  batch: ObservationBatch,
  endpoint: FactProvenance['endpoint'],
  schemaVersion: string,
  pointer: string,
): FactProvenance {
  return {
    endpoint,
    schemaVersion,
    pointer,
    publicationId: batch.resolution.publication_id,
  }
}

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isPositiveSafeInteger(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isSafeInteger(value) &&
    value > 0
  )
}

function isNonNegativeSafeInteger(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isSafeInteger(value) &&
    value >= 0
  )
}

function nearlyEqual(
  left: number,
  right: number,
  tolerance = 1e-12,
): boolean {
  return Math.abs(left - right) <= tolerance
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isShanghaiTimestampPair(
  utc: unknown,
  local: unknown,
): utc is string {
  if (
    typeof utc !== 'string' ||
    typeof local !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(utc) ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?\+08:00$/.test(
      local,
    )
  ) {
    return false
  }
  const utcInstant = Date.parse(utc)
  return (
    Number.isFinite(utcInstant) &&
    Date.parse(local) === utcInstant
  )
}

type ExtremaGroup = 'metric_extrema' | 'resource_metric_extrema'
type ExtremaSourceSlot = VisibilitySlot | ResourceSlot

function extremaSource(
  batch: ObservationBatch,
  group: ExtremaGroup,
): ExtremaSourceSlot[] {
  if (group === 'metric_extrema') {
    return batch.series.series.filter(
      (slot) => slot.slot_state === 'observed',
    )
  }
  return batch.series.resource_series
}

function extremaGroupSemanticErrors(
  batch: ObservationBatch,
  group: ExtremaGroup,
  extrema: Record<string, unknown>,
): string[] {
  const errors = new Set<string>()
  for (const [metric, rawEntry] of Object.entries(extrema)) {
    if (!isRecord(rawEntry)) {
      errors.add(`series.${group}_semantics`)
      continue
    }
    const sides = (['min', 'max'] as const).filter(
      (side) => rawEntry[side] !== undefined,
    )
    if (sides.length === 0) {
      errors.add(`series.${group}_semantics`)
      continue
    }
    const source = extremaSource(batch, group)
    const numericSource = source.flatMap((slot, index) => {
      const value = slot[metric]
      return isNumber(value) ? [{ slot, index, value }] : []
    })
    if (numericSource.length === 0) {
      errors.add(`series.${group}_semantics`)
      continue
    }

    for (const side of sides) {
      const rawPoint = rawEntry[side]
      if (!isRecord(rawPoint)) {
        errors.add(`series.${group}_semantics`)
        continue
      }
      const pointMetric = rawPoint.metric
      const pointValue = rawPoint.value
      const pointUtc = rawPoint.observed_at_utc
      const pointLocal = rawPoint.observed_at_local
      if (
        pointMetric !== metric ||
        !isNumber(pointValue) ||
        !isShanghaiTimestampPair(pointUtc, pointLocal)
      ) {
        errors.add(`series.${group}_semantics`)
        continue
      }
      const referenced = numericSource.find(
        ({ slot }) =>
          slot.observed_at_utc === pointUtc &&
          slot.observed_at_local === pointLocal,
      )
      if (
        !referenced ||
        !nearlyEqual(referenced.value, pointValue) ||
        (
          rawPoint.slot_index !== undefined &&
          (
            !isNonNegativeSafeInteger(rawPoint.slot_index) ||
            rawPoint.slot_index !== referenced.index
          )
        )
      ) {
        errors.add(`series.${group}_semantics`)
        continue
      }
      const expectedValue =
        side === 'min'
          ? Math.min(...numericSource.map(({ value }) => value))
          : Math.max(...numericSource.map(({ value }) => value))
      if (!nearlyEqual(pointValue, expectedValue)) {
        errors.add(`series.${group}_semantics`)
      }
    }
  }
  return [...errors]
}

function hasValidUpdateActivity(
  slot: VisibilitySlot | ResourceSlot,
): boolean {
  const announce = slot.announce_count
  const withdraw = slot.withdraw_count
  const total = slot.update_total
  const withdrawRatio = slot.withdraw_ratio
  if (
    !isNonNegativeSafeInteger(announce) ||
    !isNonNegativeSafeInteger(withdraw) ||
    !isNonNegativeSafeInteger(total) ||
    announce + withdraw !== total
  ) {
    return false
  }
  if (total === 0) {
    return withdrawRatio === null
  }
  return (
    isNumber(withdrawRatio) &&
    withdrawRatio >= 0 &&
    withdrawRatio <= 1 &&
    nearlyEqual(withdrawRatio, withdraw / total)
  )
}

function resourceSeriesSemanticErrors(
  batch: ObservationBatch,
): string[] {
  const errors = new Set<string>()
  const resources = batch.series.resource_series
  const visibility = batch.series.series
  const capabilities = batch.overview.capabilities
  const countryResourcesAvailable =
    capabilities.country_resources?.state === 'available'
  const updateActivityAvailable =
    capabilities.update_activity?.state === 'available'

  if (!countryResourcesAvailable && resources.length === 0) {
    return []
  }
  if (
    resources.length === 0 ||
    resources.length !== visibility.length
  ) {
    errors.add('series.resource_complete_time_grid')
    return [...errors]
  }

  let previous: ResourceSlot | undefined
  for (let index = 0; index < resources.length; index += 1) {
    const slot = resources[index]!
    const visibilitySlot = visibility[index]!
    if (
      !isShanghaiTimestampPair(
        slot.observed_at_utc,
        slot.observed_at_local,
      ) ||
      slot.observed_at_utc !== visibilitySlot.observed_at_utc ||
      slot.observed_at_local !== visibilitySlot.observed_at_local
    ) {
      errors.add('series.resource_complete_time_grid')
    }

    const ipv4Equivalent = slot.ipv4_24_equivalent_count
    const ipv6Equivalent = slot.ipv6_48_equivalent_count
    const ipv4Address = slot.ipv4_address_count
    if (
      !isNonNegativeSafeInteger(ipv4Equivalent) ||
      !isNonNegativeSafeInteger(ipv6Equivalent) ||
      !isNonNegativeSafeInteger(ipv4Address) ||
      !Number.isSafeInteger(ipv4Equivalent * 256) ||
      ipv4Address !== ipv4Equivalent * 256
    ) {
      errors.add('series.resource_count_semantics')
    }

    if (updateActivityAvailable && !hasValidUpdateActivity(slot)) {
      errors.add('series.resource_update_activity_semantics')
    }

    const deltaPairs = [
      [
        'ipv4_24_equivalent_count',
        'ipv4_24_equivalent_delta',
      ],
      [
        'ipv6_48_equivalent_count',
        'ipv6_48_equivalent_delta',
      ],
      ['ipv4_address_count', 'ipv4_address_delta'],
      ...(updateActivityAvailable
        ? [
            ['announce_count', 'announce_delta'],
            ['withdraw_count', 'withdraw_delta'],
          ]
        : []),
    ] as const
    for (const [metric, deltaMetric] of deltaPairs) {
      const delta = slot[deltaMetric]
      if (!previous) {
        if (delta !== null) {
          errors.add('series.resource_first_slot_delta=null')
        }
        continue
      }
      const current = slot[metric]
      const prior = previous[metric]
      if (
        !isNonNegativeSafeInteger(current) ||
        !isNonNegativeSafeInteger(prior) ||
        !isNumber(delta) ||
        !Number.isSafeInteger(delta) ||
        delta !== current - prior
      ) {
        errors.add('series.resource_adjacent_delta_semantics')
      }
    }
    previous = slot
  }
  return [...errors]
}

function extendedSeriesSemanticErrors(
  batch: ObservationBatch,
): string[] {
  const errors = new Set<string>()
  const cohort = batch.overview.cohort
  if (!cohort) return []
  const capabilities = batch.overview.capabilities
  const observedSlots = batch.series.series.filter(
    (slot) => slot.slot_state === 'observed',
  )

  if (capabilities.asn_matrix?.state === 'available') {
    for (const slot of observedSlots) {
      const fullyVisible = slot.fully_visible_asn_count
      const partiallyVisible = slot.partially_visible_asn_count
      const fullyInvisible = slot.fully_invisible_asn_count
      const visibleOrigin = slot.visible_origin_asn_count
      if (
        !isNonNegativeSafeInteger(fullyVisible) ||
        !isNonNegativeSafeInteger(partiallyVisible) ||
        !isNonNegativeSafeInteger(fullyInvisible) ||
        !isNonNegativeSafeInteger(visibleOrigin) ||
        visibleOrigin !== fullyVisible + partiallyVisible ||
        fullyVisible + partiallyVisible + fullyInvisible !==
          cohort.origin_asn_count
      ) {
        errors.add('series.asn_partition_semantics')
        break
      }
    }
  }

  if (capabilities.address_families?.state === 'available') {
    const ipv4Denominator = cohort.ipv4_prefix_vp_count
    const ipv6Denominator = cohort.ipv6_prefix_vp_count
    if (
      !isPositiveSafeInteger(ipv4Denominator) ||
      !isPositiveSafeInteger(ipv6Denominator) ||
      ipv4Denominator + ipv6Denominator !== cohort.prefix_vp_count
    ) {
      errors.add('cohort.address_family_denominator_semantics')
    } else {
      for (const slot of observedSlots) {
        const ipv4Count = slot.ipv4_visible_prefix_vp_count
        const ipv6Count = slot.ipv6_visible_prefix_vp_count
        const ipv4Ratio = slot.ipv4_visible_prefix_vp_ratio
        const ipv6Ratio = slot.ipv6_visible_prefix_vp_ratio
        if (
          !isNonNegativeSafeInteger(ipv4Count) ||
          !isNonNegativeSafeInteger(ipv6Count) ||
          ipv4Count > ipv4Denominator ||
          ipv6Count > ipv6Denominator ||
          !isNumber(ipv4Ratio) ||
          !isNumber(ipv6Ratio) ||
          ipv4Ratio < 0 ||
          ipv4Ratio > 1 ||
          ipv6Ratio < 0 ||
          ipv6Ratio > 1 ||
          !nearlyEqual(ipv4Ratio, ipv4Count / ipv4Denominator) ||
          !nearlyEqual(ipv6Ratio, ipv6Count / ipv6Denominator) ||
          ipv4Count + ipv6Count !== slot.visible_prefix_vp_count
        ) {
          errors.add('series.address_family_semantics')
          break
        }
      }
    }
  }

  if (capabilities.update_activity?.state === 'available') {
    for (const slot of observedSlots) {
      if (!hasValidUpdateActivity(slot)) {
        errors.add('series.update_activity_semantics')
        break
      }
    }
  }

  for (const error of resourceSeriesSemanticErrors(batch)) {
    errors.add(error)
  }
  for (const error of extremaGroupSemanticErrors(
    batch,
    'metric_extrema',
    batch.series.metric_extrema,
  )) {
    errors.add(error)
  }
  for (const error of extremaGroupSemanticErrors(
    batch,
    'resource_metric_extrema',
    batch.series.resource_metric_extrema,
  )) {
    errors.add(error)
  }
  return [...errors]
}

function extremaValue(
  extrema: Record<string, unknown>,
  metric: string,
  side: 'min' | 'max',
): number | undefined {
  const metricValue = extrema[metric]
  if (!metricValue || typeof metricValue !== 'object') return undefined
  const sideValue = (metricValue as Record<string, unknown>)[side]
  if (!sideValue || typeof sideValue !== 'object') return undefined
  const value = (sideValue as Record<string, unknown>).value
  return isNumber(value) ? value : undefined
}

function visibleSlots(series: VisibilitySlot[]): Array<{
  slot: VisibilitySlot
  index: number
}> {
  return series.flatMap((slot, index) =>
    // 正式读取路径只接受 country_outage_series_v2；v2 的观测槽必须
    // 显式声明 observed，未知或缺失状态不得靠残留数字补足最低门槛。
    slot.slot_state === 'observed' &&
    !slot.missing_reason &&
    isNumber(slot.visible_prefix_vp_count) &&
    Number.isInteger(slot.visible_prefix_vp_count) &&
    slot.visible_prefix_vp_count >= 0 &&
    isNumber(slot.visible_prefix_vp_ratio) &&
    slot.visible_prefix_vp_ratio >= 0 &&
    slot.visible_prefix_vp_ratio <= 1
      ? [{ slot, index }]
      : [],
  )
}

function coreVisibilitySemanticErrors(
  batch: ObservationBatch,
): string[] {
  const cohort = batch.overview.cohort
  if (!cohort || !isPositiveSafeInteger(cohort.prefix_vp_count)) {
    return []
  }
  const errors = new Set<string>()
  const series = batch.series.series
  if (series.length < 3) {
    errors.add('series.start_lowest_end')
  }
  if (
    series[0]?.observed_at_utc !== batch.overview.window_start_utc ||
    series[0]?.slot_state !== 'observed'
  ) {
    errors.add('series.window_start_observed')
  }
  if (
    series.at(-1)?.observed_at_utc !== batch.overview.window_end_utc ||
    series.at(-1)?.slot_state !== 'observed'
  ) {
    errors.add('series.window_end_observed')
  }

  let previousObserved: VisibilitySlot | undefined
  for (const slot of series) {
    if (slot.slot_state !== 'observed') {
      errors.add('series.complete_observed_grid')
      previousObserved = undefined
      continue
    }
    const count = slot.visible_prefix_vp_count
    const ratio = slot.visible_prefix_vp_ratio
    if (
      !isNumber(count) ||
      !Number.isSafeInteger(count) ||
      count < 0 ||
      count > cohort.prefix_vp_count ||
      !isNumber(ratio) ||
      ratio < 0 ||
      ratio > 1 ||
      !nearlyEqual(ratio, count / cohort.prefix_vp_count)
    ) {
      errors.add('series.visible_prefix_vp_semantics')
      previousObserved = slot
      continue
    }

    const rawDelta = slot.visible_prefix_vp_delta
    const rawRatioDelta = slot.visible_prefix_vp_ratio_delta_pp
    if (!previousObserved) {
      if (
        rawDelta !== undefined &&
        rawDelta !== null ||
        rawRatioDelta !== undefined &&
        rawRatioDelta !== null
      ) {
        errors.add('series.first_slot_delta=null')
      }
    } else {
      const previousCount = previousObserved.visible_prefix_vp_count
      const previousRatio = previousObserved.visible_prefix_vp_ratio
      if (
        !isNumber(previousCount) ||
        !isNumber(previousRatio) ||
        !isNumber(rawDelta) ||
        !Number.isSafeInteger(rawDelta) ||
        rawDelta !== count - previousCount ||
        !isNumber(rawRatioDelta) ||
        !nearlyEqual(rawRatioDelta, (ratio - previousRatio) * 100)
      ) {
        errors.add('series.adjacent_delta_semantics')
      }
    }
    previousObserved = slot
  }
  return [...errors]
}

function keyPoint(
  batch: ObservationBatch,
  kind: KeyVisibilityPoint['kind'],
  slot: VisibilitySlot,
  index: number,
): KeyVisibilityPoint {
  return {
    kind,
    slotIndex: index,
    observedAtUtc: slot.observed_at_utc,
    observedAtLocal: slot.observed_at_local,
    visiblePrefixVpCount: slot.visible_prefix_vp_count!,
    visiblePrefixVpRatio: slot.visible_prefix_vp_ratio!,
    provenance: provenance(
      batch,
      'series',
      batch.series.schema_version,
      `/series/${index}`,
    ),
  }
}

function derive(
  batch: ObservationBatch,
  metric: string,
  label: string,
  value: number,
  unit: string,
  formula: string,
  operands: Record<string, number>,
): DerivedNumericFact {
  const factId = `fact_${canonicalJsonSha256({
    publicationId: batch.resolution.publication_id,
    revision: batch.resolution.latest_revision,
    metric,
    operands,
  }).slice(0, 24)}`
  return {
    factId,
    metric,
    label,
    value,
    unit,
    formula,
    operands,
    provenance: provenance(
      batch,
      'series',
      batch.series.schema_version,
      '/series',
    ),
  }
}

function snapshotIdentity(
  batch: ObservationBatch,
  collectorId: 'rrc25',
): SnapshotIdentity {
  const cohortId = batch.overview.cohort_id ?? batch.series.cohort_id ?? ''
  return {
    incidentId: batch.overview.incident_id,
    publicationId: batch.overview.publication_id,
    revision: batch.overview.revision,
    dataThrough: batch.overview.data_through,
    isFinal: batch.overview.is_final,
    cohortId,
    collectorId,
    windowStartUtc: batch.overview.window_start_utc,
    windowEndUtc: batch.overview.window_end_utc,
  }
}

function evaluateEligibility(
  batch: ObservationBatch,
  availableSlots: ReturnType<typeof visibleSlots>,
  capabilities: Record<string, CapabilityState>,
): ReportEligibility {
  const missingRequiredFields: string[] = []
  const overview = batch.overview
  if (!overview.incident_id) missingRequiredFields.push('incident_id')
  if (!overview.publication_id) missingRequiredFields.push('publication_id')
  if (
    typeof overview.data_through !== 'string' ||
    overview.data_through.trim().length === 0
  ) {
    missingRequiredFields.push('data_through')
  }
  if (!overview.event_identity?.country_code) {
    missingRequiredFields.push('event_identity.country_code')
  }
  if (!overview.event_identity?.country_name?.trim()) {
    missingRequiredFields.push('event_identity.country_name')
  }
  if (!overview.event_identity?.display_name?.trim()) {
    missingRequiredFields.push('event_identity.display_name')
  }
  if (!overview.observation_scope?.window_start_utc) {
    missingRequiredFields.push('observation_scope.window_start_utc')
  }
  if (!overview.observation_scope?.window_end_utc) {
    missingRequiredFields.push('observation_scope.window_end_utc')
  }
  if (
    !overview.cohort ||
    !isPositiveSafeInteger(overview.cohort.prefix_vp_count)
  ) {
    missingRequiredFields.push('cohort.prefix_vp_count')
  }
  if (
    !overview.cohort ||
    !isPositiveSafeInteger(overview.cohort.origin_asn_count)
  ) {
    missingRequiredFields.push('cohort.origin_asn_count')
  }
  if (!overview.cohort?.cohort_id?.trim()) {
    missingRequiredFields.push('cohort.cohort_id')
  }
  if (!overview.cohort?.denominator_policy?.trim()) {
    missingRequiredFields.push('cohort.denominator_policy')
  }
  if (
    !isPositiveSafeInteger(overview.observation_scope?.interval_seconds) ||
    !isPositiveSafeInteger(batch.series.interval_seconds)
  ) {
    missingRequiredFields.push('observation_scope.interval_seconds')
  }
  if (overview.observation_scope?.quality_status !== 'pass') {
    missingRequiredFields.push('observation_scope.quality_status=pass')
  }
  if (batch.audit.quality_status !== 'pass') {
    missingRequiredFields.push('audit.quality_status=pass')
  }
  if (
    overview.missing_slot_count !== 0 ||
    batch.series.missing_slot_count !== 0 ||
    batch.audit.missing_slot_count !== 0
  ) {
    missingRequiredFields.push('series.no_missing_slots')
  }
  for (const error of coreVisibilitySemanticErrors(batch)) {
    missingRequiredFields.push(error)
  }
  for (const error of extendedSeriesSemanticErrors(batch)) {
    missingRequiredFields.push(error)
  }
  if (availableSlots.length !== batch.series.series.length) {
    missingRequiredFields.push('series.complete_visibility_values')
  }

  const reasons: string[] = []
  if (overview.publication_state !== 'published') {
    reasons.push('快照尚未发布')
  }
  if (
    !['state_complete', 'evidence_complete'].includes(
      overview.observation_state,
    )
  ) {
    reasons.push('观测完整度不足以生成正式报告')
  }
  if (overview.observation_scope.collector_id !== 'rrc25') {
    reasons.push('观测源不是 RRC25')
  }
  if (missingRequiredFields.length > 0) {
    reasons.push(`缺少最低数据：${missingRequiredFields.join('、')}`)
  }

  const degradedCapabilities = Object.fromEntries(
    Object.entries(capabilities).filter(
      ([, capability]) => capability.state !== 'available',
    ),
  )
  return {
    eligible: reasons.length === 0,
    reasons,
    missingRequiredFields,
    degradedCapabilities,
  }
}

function chooseExtreme(
  values: Array<{ slot: VisibilitySlot; index: number }>,
  compare: (left: VisibilitySlot, right: VisibilitySlot) => number,
): { slot: VisibilitySlot; index: number } {
  return values.reduce((selected, candidate) =>
    compare(candidate.slot, selected.slot) < 0 ? candidate : selected,
  )
}

function selectDeltaSlot(
  values: Array<{ slot: VisibilitySlot; index: number }>,
  direction: 'min' | 'max',
): { slot: VisibilitySlot; index: number } | undefined {
  const candidates = values.filter(({ slot }) =>
    isNumber(slot.visible_prefix_vp_delta),
  )
  if (candidates.length === 0) return undefined
  return candidates.reduce((selected, candidate) => {
    const left = candidate.slot.visible_prefix_vp_delta!
    const right = selected.slot.visible_prefix_vp_delta!
    return direction === 'min'
      ? left < right
        ? candidate
        : selected
      : left > right
        ? candidate
        : selected
  })
}

function sameSnapshotEnvelope(
  value: SnapshotEnvelope,
  snapshot: SnapshotIdentity,
): boolean {
  return (
    value.incident_id === snapshot.incidentId &&
    value.publication_id === snapshot.publicationId &&
    value.revision === snapshot.revision &&
    value.data_through === snapshot.dataThrough
  )
}

export function assembleCountryOutageFacts(
  batch: ObservationBatch,
  options: { requireEligible?: boolean } = {},
): CountryOutageFactSet {
  assertBatchIdentity(batch)
  const scope = batch.overview.observation_scope
  if (
    scope.collector_id !== 'rrc25' ||
    scope.collector_count !== 1 ||
    scope.collector_ids.length !== 1 ||
    scope.collector_ids[0] !== 'rrc25'
  ) {
    throw new UnsupportedCollectorError(scope.collector_id)
  }
  const cohort = batch.overview.cohort
  const availableSlots = visibleSlots(batch.series.series)
  const capabilities = normalizedCapabilities(
    batch.overview.capabilities,
  )
  const eligibility = evaluateEligibility(
    batch,
    availableSlots,
    capabilities,
  )
  if ((!cohort || !eligibility.eligible) && options.requireEligible !== false) {
    throw new ReportDataInsufficientError(
      eligibility.reasons.length > 0
        ? eligibility.reasons
        : ['固定统计人口不可用'],
    )
  }
  if (!cohort) {
    throw new ReportDataInsufficientError(['固定统计人口不可用'])
  }

  const start = availableSlots.find(({ index }) => index === 0)
  const end = availableSlots.find(
    ({ index }) => index === batch.series.series.length - 1,
  )
  if (!start || !end) {
    throw new ReportDataInsufficientError(['可见性序列没有可用起点和结束点'])
  }
  const lowest = chooseExtreme(
    availableSlots,
    (left, right) =>
      left.visible_prefix_vp_count! - right.visible_prefix_vp_count!,
  )
  const largestDrop = selectDeltaSlot(availableSlots, 'min')
  const largestRecovery = selectDeltaSlot(availableSlots, 'max')

  const keyVisibilityPoints: KeyVisibilityPoint[] = [
    keyPoint(batch, 'start', start.slot, start.index),
    keyPoint(batch, 'lowest', lowest.slot, lowest.index),
    keyPoint(batch, 'end', end.slot, end.index),
  ]
  if (largestDrop) {
    keyVisibilityPoints.push(
      keyPoint(batch, 'largest_drop', largestDrop.slot, largestDrop.index),
    )
  }
  if (largestRecovery) {
    keyVisibilityPoints.push(
      keyPoint(
        batch,
        'largest_recovery',
        largestRecovery.slot,
        largestRecovery.index,
      ),
    )
  }

  const startCount = start.slot.visible_prefix_vp_count!
  const lowestCount = lowest.slot.visible_prefix_vp_count!
  const endCount = end.slot.visible_prefix_vp_count!
  const loss = startCount - lowestCount
  const endGap = startCount - endCount
  const recovered = endCount - lowestCount
  const derivedFacts: DerivedNumericFact[] = [
    derive(
      batch,
      'start_to_lowest_visible_prefix_vp_change',
      '起点至最低点可见关系减少量',
      loss,
      'Prefix×VP',
      'start_visible_prefix_vp_count - lowest_visible_prefix_vp_count',
      {
        start_visible_prefix_vp_count: startCount,
        lowest_visible_prefix_vp_count: lowestCount,
      },
    ),
    derive(
      batch,
      'start_to_lowest_loss_ratio',
      '起点至最低点损失占起点比例',
      startCount === 0 ? 0 : loss / startCount,
      'ratio',
      '(start_visible_prefix_vp_count - lowest_visible_prefix_vp_count) / start_visible_prefix_vp_count',
      {
        start_visible_prefix_vp_count: startCount,
        lowest_visible_prefix_vp_count: lowestCount,
      },
    ),
    derive(
      batch,
      'end_gap_from_start',
      '窗口结束相对起点的可见关系缺口',
      endGap,
      'Prefix×VP',
      'start_visible_prefix_vp_count - end_visible_prefix_vp_count',
      {
        start_visible_prefix_vp_count: startCount,
        end_visible_prefix_vp_count: endCount,
      },
    ),
    derive(
      batch,
      'recovered_from_lowest',
      '最低点至窗口结束的回升量',
      recovered,
      'Prefix×VP',
      'end_visible_prefix_vp_count - lowest_visible_prefix_vp_count',
      {
        end_visible_prefix_vp_count: endCount,
        lowest_visible_prefix_vp_count: lowestCount,
      },
    ),
    derive(
      batch,
      'recovery_share_of_prior_loss',
      '最低点后回升占此前损失比例',
      loss === 0 ? 0 : recovered / loss,
      'ratio',
      '(end_visible_prefix_vp_count - lowest_visible_prefix_vp_count) / (start_visible_prefix_vp_count - lowest_visible_prefix_vp_count)',
      {
        start_visible_prefix_vp_count: startCount,
        lowest_visible_prefix_vp_count: lowestCount,
        end_visible_prefix_vp_count: endCount,
      },
    ),
  ]
  const ipv4ResourceMax = extremaValue(
    batch.series.resource_metric_extrema,
    'ipv4_24_equivalent_count',
    'max',
  )
  const ipv4ResourceMin = extremaValue(
    batch.series.resource_metric_extrema,
    'ipv4_24_equivalent_count',
    'min',
  )
  if (ipv4ResourceMax !== undefined && ipv4ResourceMin !== undefined) {
    derivedFacts.push(
      derive(
        batch,
        'ipv4_24_equivalent_max_to_min_change',
        'IPv4 /24 等价资源最大值至最低值变化量',
        ipv4ResourceMax - ipv4ResourceMin,
        '/24 equivalent',
        'max_ipv4_24_equivalent_count - min_ipv4_24_equivalent_count',
        {
          max_ipv4_24_equivalent_count: ipv4ResourceMax,
          min_ipv4_24_equivalent_count: ipv4ResourceMin,
        },
      ),
    )
  }

  const snapshot = snapshotIdentity(batch, 'rrc25')
  if (
    !sameSnapshotEnvelope(batch.series, snapshot) ||
    !sameSnapshotEnvelope(batch.audit, snapshot)
  ) {
    throw new ReportDataInsufficientError(['事实装配期间快照身份发生变化'])
  }
  const factSetContent: Omit<CountryOutageFactSet, 'factSetId'> = {
    schemaVersion: 'country_outage_report_facts_v1',
    snapshot,
    event: batch.overview.event_identity,
    scope,
    cohort,
    capabilities,
    quality: {
      status: batch.audit.quality_status || scope.quality_status,
      missingSlotCount: Math.max(
        batch.overview.missing_slot_count,
        batch.series.missing_slot_count,
        batch.audit.missing_slot_count,
      ),
      limitations: batch.overview.limitations,
    },
    eligibility,
    keyVisibilityPoints,
    derivedFacts,
    series: batch.series.series,
    resourceSeries: batch.series.resource_series,
    metricExtrema: batch.series.metric_extrema,
    resourceMetricExtrema: batch.series.resource_metric_extrema,
    annotations: batch.series.annotations,
    audit: {
      sourceSystem: batch.audit.source_system,
      sourceReference: batch.audit.source_reference,
      evidenceLevel: batch.audit.evidence_level,
      algorithmVersion: batch.audit.algorithm_version,
      mappingVersion: batch.audit.mapping_version,
      verifiedHashes: batch.audit.verified_hashes,
    },
  }
  // factSetId 是报告所依赖事实的内容地址。只要任一可进入报告或追问
  // 的事实、能力、质量说明、溯源信息发生变化，就必须得到新身份；
  // canonical JSON 保证仅 JSON 对象键顺序变化时身份保持稳定。
  const factSetId =
    `facts_${canonicalJsonSha256(factSetContent).slice(0, 32)}`

  return {
    ...factSetContent,
    factSetId,
  }
}
