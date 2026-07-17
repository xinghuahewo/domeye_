import {
  EVENT_KIND_LABELS,
  type CountPoint,
  type EventKind,
  type EventLevel,
  type EventPage,
  type EventRow,
  type FeaturePoint,
  type OutagePoint,
  type ParsedDetailRef,
} from '@/types/api'

export const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

export const cleanText = (value: unknown): string => {
  if (value === null || value === undefined) return ''
  const text = String(value).trim()
  return ['None', 'NaT', 'null', 'undefined'].includes(text) ? '' : text
}

export const finiteNumber = (value: unknown): number | null => {
  if (value === '' || value === null || value === undefined) return null
  const number = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(number) ? number : null
}

export const normalizeTime = (value: unknown): string | null => {
  const text = cleanText(value)
  if (!text || text === '-') return null
  return text.replace(/\s+/g, ' ')
}

const normalizeLevel = (value: unknown): EventLevel => {
  const level = cleanText(value).toLowerCase()
  return level === 'high' || level === 'middle' || level === 'low' ? level : 'unknown'
}

export const normalizeEventRow = (value: unknown): EventRow | null => {
  if (!isRecord(value)) return null
  const detailUrl = cleanText(value.detail_url)
  const type = cleanText(value.event_type) || '未知事件'
  const startTime = normalizeTime(value.start_time ?? value.s_time)
  const summary = cleanText(value.event_info)

  return {
    key: detailUrl || `${type}-${startTime || 'unknown'}-${summary}`,
    type,
    level: normalizeLevel(value.level ?? value.event_level),
    startTime,
    endTime: normalizeTime(value.end_time ?? value.e_time),
    attackerAs: cleanText(value.attacker_as),
    attackedAs: cleanText(value.attacked_as),
    attackerOrg: cleanText(value.attacker_org),
    attackedOrg: cleanText(value.attacked_org),
    attackerCountry: cleanText(value.attacker_country),
    attackedCountry: cleanText(value.attacked_country),
    affectedPrefix: cleanText(value.affected_prefix),
    summary,
    detailUrl,
  }
}

export const normalizeEventPage = (payload: unknown): EventPage => {
  if (!isRecord(payload)) throw new Error('事件列表响应格式异常')
  if (payload.status === false) throw new Error(cleanText(payload.msg) || '事件查询失败')
  const rows = Array.isArray(payload.data) ? payload.data : []
  const data = rows.map(normalizeEventRow).filter((row): row is EventRow => row !== null)

  return {
    data,
    totalPage: Math.max(0, finiteNumber(payload.total_page) ?? 0),
    recordCount: Math.max(0, finiteNumber(payload.record_count) ?? data.length),
  }
}

export const normalizeEventArray = (payload: unknown): EventRow[] => {
  if (isRecord(payload) && payload.status === false) {
    throw new Error(cleanText(payload.msg) || '事件查询失败')
  }
  const rows = Array.isArray(payload)
    ? payload
    : isRecord(payload) && Array.isArray(payload.data)
      ? payload.data
      : null
  if (!rows) throw new Error('事件响应格式异常')
  return rows.map(normalizeEventRow).filter((row): row is EventRow => row !== null)
}

const safeDecode = (value: string): string => {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

export const parseDetailUrl = (raw: string): ParsedDetailRef | null => {
  const preserved = raw.trim()
  const clean = preserved
    .replace(/^https?:\/\/[^/]+/i, '')
    .replace(/^\/+/, '')
    .replace(/^api\/v1\//, '')
  const parts = clean.split('/')
  if (parts.length !== 5) return null

  const [encodedKind, encodedStart, encodedProblem, encodedId, encodedSource] = parts
  if (!encodedKind || !encodedStart || !encodedProblem || !encodedId || !encodedSource) return null
  const kind = safeDecode(encodedKind) as EventKind
  const eventId = safeDecode(encodedId)
  if (!(kind in EVENT_KIND_LABELS) || !/^\d+$/.test(eventId)) return null

  return {
    raw: preserved,
    kind,
    startTime: safeDecode(encodedStart),
    problem: safeDecode(encodedProblem),
    eventId,
    source: safeDecode(encodedSource),
  }
}

export const buildDetailEndpoint = (detail: ParsedDetailRef): string =>
  [detail.kind, detail.startTime, detail.problem, detail.eventId, detail.source]
    .map((part) => encodeURIComponent(part))
    .join('/')

const extractArray = (payload: unknown, context: string): unknown[] => {
  if (isRecord(payload) && payload.status === false) {
    throw new Error(cleanText(payload.msg) || `${context}查询失败`)
  }
  if (Array.isArray(payload)) return payload
  if (isRecord(payload) && Array.isArray(payload.data)) return payload.data
  throw new Error(`${context}响应格式异常`)
}

export const normalizeFeaturePoints = (payload: unknown): FeaturePoint[] =>
  extractArray(payload, '特征').flatMap((value) => {
    if (!isRecord(value)) return []
    const time = normalizeTime(value.t ?? value.time)
    if (!time) return []
    return [{
      time,
      announce: finiteNumber(value.announce),
      withdraw: finiteNumber(value.withdraw),
      ipv4Prefixes: finiteNumber(value.v4Prefix_num),
      ipv6Prefixes: finiteNumber(value.v6Prefix_num),
      ipv4Addresses: finiteNumber(value.v4IP_num),
    }]
  })

export const normalizeOutagePoints = (payload: unknown): OutagePoint[] =>
  extractArray(payload, '中断时序').flatMap((value) => {
    if (!isRecord(value)) return []
    const time = normalizeTime(value.time_slot ?? value.time)
    const count = finiteNumber(value.outage_count ?? value.count)
    return time && count !== null ? [{ time, count }] : []
  })

export const normalizeCountPoints = (payload: unknown): CountPoint[] =>
  extractArray(payload, '事件统计').flatMap((value) => {
    if (!isRecord(value)) return []
    const time = normalizeTime(value.time)
    const count = finiteNumber(value.num)
    return time && count !== null ? [{ time, count }] : []
  })

export const errorMessage = (error: unknown): string => {
  if (error instanceof Error && error.message) return error.message
  return '暂时无法获取数据'
}
