import type {
  CountryOutageAsnPage,
  ObservationBatch,
  SnapshotIdentity,
  VisibilitySlot,
} from '../domain/contracts.js'
import type { AsnQuery } from '../domain/domeye-client.js'
import type { CountryOutageReportDataSource } from '../report/report-compiler.js'

export const A4_CERTIFIED_SCENARIO_SET_ID =
  'country-outage-rrc25-legal-scenarios-v2' as const
export const A4_CERTIFIED_INPUT_SCOPE =
  'legal_country_outage_rrc25_v1' as const

export const A4_CERTIFICATION_SCENARIO_IDS = [
  'capability-degraded-final',
  'direction-end-above-start-final',
  'non-final-snapshot',
] as const

export type A4CertificationScenarioId =
  (typeof A4_CERTIFICATION_SCENARIO_IDS)[number]

export interface A4CertificationScenarioDefinition {
  id: A4CertificationScenarioId
  certificationOnly: true
  synthetic: true
  purpose:
    | 'capability_degradation'
    | 'direction_change'
    | 'non_final_snapshot'
}

export const A4_CERTIFICATION_SCENARIOS: readonly A4CertificationScenarioDefinition[] =
  Object.freeze([
    Object.freeze({
      id: 'capability-degraded-final',
      certificationOnly: true,
      synthetic: true,
      purpose: 'capability_degradation',
    }),
    Object.freeze({
      id: 'direction-end-above-start-final',
      certificationOnly: true,
      synthetic: true,
      purpose: 'direction_change',
    }),
    Object.freeze({
      id: 'non-final-snapshot',
      certificationOnly: true,
      synthetic: true,
      purpose: 'non_final_snapshot',
    }),
  ])

const CERTIFICATION_ONLY_LIMITATION =
  '认证专用合成场景，不是 Domeye 事件事实，不得作为观测报告对外发布。'

const SLOT_TIME_KEYS = new Set([
  'snapshot_id',
  'observed_at_utc',
  'observed_at_local',
])

const DELTA_SOURCE_KEYS = Object.freeze({
  visible_prefix_vp_delta: 'visible_prefix_vp_count',
  visible_prefix_vp_ratio_delta_pp: 'visible_prefix_vp_ratio',
  visible_origin_asn_delta: 'visible_origin_asn_count',
  announce_delta: 'announce_count',
  withdraw_delta: 'withdraw_count',
  ipv4_visible_prefix_vp_delta: 'ipv4_visible_prefix_vp_count',
  ipv6_visible_prefix_vp_delta: 'ipv6_visible_prefix_vp_count',
} as const)

function appendCertificationBoundary(
  batch: ObservationBatch,
  scenarioId: A4CertificationScenarioId,
): void {
  batch.resolution.data_mode = `certification_synthetic:${scenarioId}`
  batch.overview.limitations = [
    ...batch.overview.limitations,
    CERTIFICATION_ONLY_LIMITATION,
  ]
  batch.audit.algorithm_version =
    `${batch.audit.algorithm_version}+certification-synthetic:${scenarioId}`
  batch.audit.verified_hashes = {
    certification_only_synthetic_v1: '0'.repeat(64),
  }
}

function degradedCapabilities() {
  return {
    fixed_cohort: { state: 'available' as const },
    asn_matrix: {
      state: 'unavailable' as const,
      reason: '认证场景：ASN 细化不可用',
    },
    address_families: {
      state: 'unavailable' as const,
      reason: '认证场景：地址族细化不可用',
    },
    update_activity: {
      state: 'building' as const,
      reason: '认证场景：UPDATE 活动仍在构建',
    },
    country_resources: {
      state: 'not_applicable' as const,
      reason: '认证场景：国家资源指标不适用',
    },
    normal_band: {
      state: 'unknown' as const,
      reason: '认证场景：缺少可信正常参照',
    },
  }
}

function capabilityDegradedBatch(source: ObservationBatch): ObservationBatch {
  const batch = structuredClone(source)
  const capabilities = degradedCapabilities()
  batch.resolution.capabilities = structuredClone(capabilities)
  batch.overview.capabilities = structuredClone(capabilities)
  appendCertificationBoundary(batch, 'capability-degraded-final')
  return batch
}

function deltaValue(
  current: VisibilitySlot,
  previous: VisibilitySlot | undefined,
  sourceKey: string,
  percentagePoints: boolean,
): number | null {
  if (!previous) return null
  const currentValue = current[sourceKey]
  const previousValue = previous[sourceKey]
  if (
    typeof currentValue !== 'number' ||
    typeof previousValue !== 'number' ||
    !Number.isFinite(currentValue) ||
    !Number.isFinite(previousValue)
  ) {
    return null
  }
  const difference = currentValue - previousValue
  return percentagePoints ? difference * 100 : difference
}

function recomputeVisibilityDeltas(series: VisibilitySlot[]): void {
  for (let index = 0; index < series.length; index += 1) {
    const slot = series[index]!
    const previous = index === 0 ? undefined : series[index - 1]
    for (const [deltaKey, sourceKey] of Object.entries(
      DELTA_SOURCE_KEYS,
    )) {
      slot[deltaKey] = deltaValue(
        slot,
        previous,
        sourceKey,
        deltaKey === 'visible_prefix_vp_ratio_delta_pp',
      )
    }
  }
}

function recomputeMetricExtrema(
  original: ObservationBatch['series']['metric_extrema'],
  series: readonly VisibilitySlot[],
): ObservationBatch['series']['metric_extrema'] {
  const extrema: Record<string, unknown> = {}
  for (const metric of Object.keys(original)) {
    const candidates = series
      .map((slot) => ({
        value: slot[metric],
        observed_at_utc: slot.observed_at_utc,
        observed_at_local: slot.observed_at_local,
      }))
      .filter(
        (
          item,
        ): item is {
          value: number
          observed_at_utc: string
          observed_at_local: string
        } => typeof item.value === 'number' && Number.isFinite(item.value),
      )
    if (candidates.length === 0) continue
    const minimum = candidates.reduce((left, right) =>
      right.value < left.value ? right : left,
    )
    const maximum = candidates.reduce((left, right) =>
      right.value > left.value ? right : left,
    )
    extrema[metric] = {
      min: { metric, ...minimum },
      max: { metric, ...maximum },
    }
  }
  return extrema
}

function directionChangedBatch(source: ObservationBatch): ObservationBatch {
  const batch = structuredClone(source)
  const original = source.series.series
  const reversed = original.map((target, index) => {
    const sourceSlot = structuredClone(
      original[original.length - index - 1]!,
    )
    for (const key of SLOT_TIME_KEYS) {
      if (key in target) sourceSlot[key] = target[key]
      else delete sourceSlot[key]
    }
    return sourceSlot
  })
  recomputeVisibilityDeltas(reversed)
  batch.series.series = reversed
  batch.series.metric_extrema = recomputeMetricExtrema(
    source.series.metric_extrema,
    reversed,
  )
  appendCertificationBoundary(
    batch,
    'direction-end-above-start-final',
  )
  return batch
}

function nonFinalBatch(source: ObservationBatch): ObservationBatch {
  const batch = structuredClone(source)
  batch.resolution.is_final = false
  batch.overview.is_final = false
  batch.series.is_final = false
  batch.audit.is_final = false
  // 非终态仍然可以是一份结构完整、可叙述的已完成快照；这里只改变
  // 数据终态语义，不能把输入伪装成编译器本就应拒绝的构建中响应。
  batch.overview.processing_status = {
    state: 'building',
    last_complete_data_through: batch.overview.data_through,
  }
  appendCertificationBoundary(batch, 'non-final-snapshot')
  return batch
}

export function createA4CertificationScenarioBatch(
  source: ObservationBatch,
  scenarioId: A4CertificationScenarioId,
): ObservationBatch {
  switch (scenarioId) {
    case 'capability-degraded-final':
      return capabilityDegradedBatch(source)
    case 'direction-end-above-start-final':
      return directionChangedBatch(source)
    case 'non-final-snapshot':
      return nonFinalBatch(source)
  }
}

function nonFinalSnapshotAsFinal(
  snapshot: SnapshotIdentity,
): SnapshotIdentity {
  return Object.freeze({ ...snapshot, isFinal: true })
}

function nonFinalAsnPage(
  page: CountryOutageAsnPage,
): CountryOutageAsnPage {
  return {
    ...structuredClone(page),
    is_final: false,
    observation_state: 'state_building',
  }
}

export function createA4CertificationScenarioClient(
  baseClient: CountryOutageReportDataSource,
  scenarioId: A4CertificationScenarioId,
): CountryOutageReportDataSource {
  return Object.freeze({
    async getObservationBatch(reference: string): Promise<ObservationBatch> {
      const batch = await baseClient.getObservationBatch(reference)
      return createA4CertificationScenarioBatch(batch, scenarioId)
    },
    async getAsns(
      snapshot: SnapshotIdentity,
      query?: AsnQuery,
    ): Promise<CountryOutageAsnPage> {
      if (scenarioId === 'capability-degraded-final') {
        throw new Error('能力降级认证场景不得读取 ASN 细化')
      }
      if (scenarioId !== 'non-final-snapshot') {
        return await baseClient.getAsns(snapshot, query)
      }
      const page = await baseClient.getAsns(
        nonFinalSnapshotAsFinal(snapshot),
        query,
      )
      return nonFinalAsnPage(page)
    },
  })
}
