import type { JsonObject } from '../domain/contracts.js'

export function formatInteger(value: number): string {
  return new Intl.NumberFormat('zh-CN', {
    maximumFractionDigits: 0,
  }).format(value)
}

export function formatPercent(value: number, digits = 2): string {
  return `${(value * 100).toFixed(digits)}%`
}

export function formatDurationMinutes(value: number): string {
  const totalMinutes = Math.max(0, Math.round(value))
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (hours === 0) return `${minutes} 分钟`
  if (minutes === 0) return `${hours} 小时`
  return `${hours} 小时 ${minutes} 分钟`
}

export function localDateTimeLabel(value: string): string {
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):\d{2}/,
  )
  if (!match) return value
  const [, year, month, day, hour, minute] = match
  return `${Number(year)}年${Number(month)}月${Number(day)}日${hour}:${minute}`
}

export function localTimeLabel(value: string): string {
  const match = value.match(/T(\d{2}):(\d{2}):\d{2}/)
  return match ? `${match[1]}:${match[2]}` : value
}

export interface ExtremaPoint {
  metric: string
  value: number
  observed_at_utc: string
  observed_at_local: string
}

export function extremaPoint(
  extrema: JsonObject,
  metric: string,
  side: 'min' | 'max',
): ExtremaPoint | undefined {
  const metricValue = extrema[metric]
  if (!metricValue || typeof metricValue !== 'object') return undefined
  const sideValue = (metricValue as JsonObject)[side]
  if (!sideValue || typeof sideValue !== 'object') return undefined
  const point = sideValue as JsonObject
  if (
    typeof point.metric !== 'string' ||
    typeof point.value !== 'number' ||
    typeof point.observed_at_utc !== 'string' ||
    typeof point.observed_at_local !== 'string'
  ) {
    return undefined
  }
  return point as unknown as ExtremaPoint
}
