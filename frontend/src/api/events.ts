import { apiGet } from './client'
import { CORE_EVENT_TYPES, type EventQuery, type ParsedDetailRef } from '@/types/api'
import {
  buildDetailEndpoint,
  normalizeEventArray,
  normalizeEventPage,
  parseDetailUrl,
} from '@/utils/normalize'

export async function getEvents(params: EventQuery) {
  return normalizeEventPage(await apiGet<unknown>('events', { params }))
}

export async function getTopEvents() {
  const payload = await apiGet<unknown>('events/top', {
    params: { event_type: JSON.stringify(CORE_EVENT_TYPES) },
  })
  return normalizeEventArray(payload)
}

export async function getEventDetail(reference: string) {
  const parsed = parseDetailUrl(reference)
  if (!parsed) throw new Error('事件详情引用无效')
  const payload = await apiGet<unknown>(buildDetailEndpoint(parsed))
  return { parsed, payload }
}

export const parseEventReference = (reference: string): ParsedDetailRef | null =>
  parseDetailUrl(reference)
