import type { EventObservation } from '@/types/api'

export interface DurationBucket {
  minSlots: number
  maxSlots: number
  label: string
}

export interface EventObservationPresentation {
  subjectName: string
  subjectCode: string
  collectorLabel: string
  collectorCountLabel: string
  mastheadKicker: string
  intervalLabel: string
  intervalTag: string
  localTimeLabel: string
  observerScopeText: string
  originScopeLabel: string
  collectorScopeLabel: string
  countryResourceDenominator: string
  countryMessageDescription: string
  collectorMessageDescription: string
}

function positiveInteger(value: number, fallback: number): number {
  return Number.isInteger(value) && value > 0 ? value : fallback
}

export function formatInterval(seconds: number): string {
  const safeSeconds = positiveInteger(seconds, 1)
  if (safeSeconds % 3600 === 0) return `${safeSeconds / 3600} 小时`
  if (safeSeconds % 60 === 0) return `${safeSeconds / 60} 分钟`
  return `${safeSeconds} 秒`
}

export function formatSlotDuration(slots: number, intervalSeconds: number): string {
  const totalSeconds = Math.max(0, slots) * positiveInteger(intervalSeconds, 1)
  if (totalSeconds === 0) return '0 分钟'
  if (totalSeconds % 3600 === 0) return `${totalSeconds / 3600} 小时`
  if (totalSeconds >= 3600) {
    const hours = Math.floor(totalSeconds / 3600)
    const minutes = Math.floor((totalSeconds % 3600) / 60)
    const seconds = totalSeconds % 60
    return [
      `${hours} 小时`,
      minutes ? `${minutes} 分钟` : '',
      seconds ? `${seconds} 秒` : '',
    ].filter(Boolean).join(' ')
  }
  if (totalSeconds % 60 === 0) return `${totalSeconds / 60} 分钟`
  if (totalSeconds >= 60) {
    return `${Math.floor(totalSeconds / 60)} 分钟 ${totalSeconds % 60} 秒`
  }
  return `${totalSeconds} 秒`
}

export function createDurationBuckets(
  intervalSeconds: number,
  expectedObservationCount: number,
): DurationBucket[] {
  const totalSlots = positiveInteger(expectedObservationCount, 1)
  const upperBounds = [0.1, 0.2, 0.4, 0.6, 0.8, 1]
    .map((ratio) => Math.ceil(totalSlots * ratio))
    .filter((value, index, values) => index === 0 || value > values[index - 1]!)

  const buckets: DurationBucket[] = [{ minSlots: 0, maxSlots: 0, label: '0' }]
  let minSlots = 1
  for (const maxSlots of upperBounds) {
    if (maxSlots < minSlots) continue
    buckets.push({
      minSlots,
      maxSlots,
      label: minSlots === maxSlots
        ? formatSlotDuration(maxSlots, intervalSeconds)
        : `${formatSlotDuration(minSlots, intervalSeconds)}–${formatSlotDuration(maxSlots, intervalSeconds)}`,
    })
    minSlots = maxSlots + 1
  }
  return buckets
}

export function formatTimezoneLabel(timezone: string): string {
  if (timezone === 'Asia/Shanghai') return '北京时间'
  return timezone || '本地时间'
}

export function createEventObservationPresentation(
  observation: EventObservation,
): EventObservationPresentation {
  const identity = observation.event_identity
  const scope = observation.observation_scope
  const subjectName = identity.country_name || identity.display_name || '观测对象'
  const subjectCode = identity.country_code || identity.event_type || 'EVENT'
  const collectorLabel = scope.collector_id || '未标识'
  const collectorDisplayLabel = collectorLabel.toUpperCase()
  const collectorCount = positiveInteger(scope.collector_count, 1)
  const intervalLabel = formatInterval(scope.interval_seconds)

  return {
    subjectName,
    subjectCode,
    collectorLabel,
    collectorCountLabel: collectorCount === 1
      ? '单 COLLECTOR'
      : `${collectorCount} COLLECTORS`,
    mastheadKicker: `BGP DATA OBSERVATORY · ${subjectCode.toUpperCase()} / ${collectorDisplayLabel}`,
    intervalLabel,
    intervalTag: `${intervalLabel}粒度`,
    localTimeLabel: formatTimezoneLabel(scope.timezone),
    observerScopeText: collectorCount === 1
      ? `仅 ${collectorDisplayLabel}`
      : `${collectorCount} 个 collector`,
    originScopeLabel: `${subjectName} origin 归属`,
    collectorScopeLabel: `${collectorDisplayLabel} 全量`,
    countryResourceDenominator: `Core BGPFeature ${subjectName}国家资源聚合`,
    countryMessageDescription: (
      `Core BGPFeature：每个 UPDATE 文件内，按 origin ASN 归入${subjectName}的报文；`
      + '统计范围不同于固定路由观测关系。'
    ),
    collectorMessageDescription: (
      `重放输入：${collectorDisplayLabel} 每个 ${intervalLabel}槽内的全部 UPDATE；`
      + `不是${subjectName}人口报文子集。`
    ),
  }
}
