export const CORE_EVENT_TYPES = [
  '前缀劫持',
  '子前缀劫持',
  '前缀中断',
  'AS中断',
  '国家中断',
  '路由泄漏',
] as const

export type EventLabel = (typeof CORE_EVENT_TYPES)[number]
export type EventLevel = 'high' | 'middle' | 'low' | 'unknown'

export const EVENT_KIND_LABELS = {
  hijack: '前缀劫持',
  sub_hijack: '子前缀劫持',
  prefix_outage: '前缀中断',
  as_outage: 'AS中断',
  country_outage: '国家中断',
  leak: '路由泄漏',
} as const

export type EventKind = keyof typeof EVENT_KIND_LABELS

export interface EventRow {
  key: string
  type: string
  level: EventLevel
  startTime: string | null
  endTime: string | null
  attackerAs: string
  attackedAs: string
  attackerOrg: string
  attackedOrg: string
  attackerCountry: string
  attackedCountry: string
  affectedPrefix: string
  summary: string
  detailUrl: string
}

export interface EventPage {
  data: EventRow[]
  totalPage: number
  recordCount: number
}

export interface ParsedDetailRef {
  raw: string
  kind: EventKind
  startTime: string
  problem: string
  eventId: string
  source: string
}

export interface FeaturePoint {
  time: string
  announce: number | null
  withdraw: number | null
  ipv4Prefixes: number | null
  ipv6Prefixes: number | null
  ipv4Addresses: number | null
}

export interface OutagePoint {
  time: string
  count: number
}

export interface CountPoint {
  time: string
  count: number
}

export interface HealthPayload {
  status: string
  service: string
  time: string
}

export interface EventQuery {
  page_num?: number
  page_size?: number
  event_type?: string
  level?: string
  country?: string
  attacker_as?: string
  attacked_as?: string
  attacker_org?: string
  attacked_org?: string
  attacker_country?: string
  attacked_country?: string
  event_info?: string
  date?: string
  sort_mode?: string
}
