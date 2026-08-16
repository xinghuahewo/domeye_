import { isAxiosError } from 'axios'

import { apiGet } from './client'
import { CORE_EVENT_TYPES, type EventQuery, type ParsedDetailRef } from '@/types/api'
import {
  buildEvidenceEndpoint,
  buildDetailEndpoint,
  buildObservationEndpoint,
  buildStoryEndpoint,
  normalizeEvidenceBundle,
  normalizeEventObservation,
  normalizeEventStory,
  normalizeEventArray,
  normalizeEventPage,
  isRecord,
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

export async function getEventEvidenceBundle(reference: string) {
  const parsed = parseDetailUrl(reference)
  if (!parsed) throw new Error('事件详情引用无效')
  const payload = await apiGet<unknown>(buildEvidenceEndpoint(parsed))
  return { parsed, bundle: normalizeEvidenceBundle(payload) }
}

export async function getEventStory(reference: string) {
  const parsed = parseDetailUrl(reference)
  if (!parsed) throw new Error('事件详情引用无效')
  const payload = await apiGet<unknown>(buildStoryEndpoint(parsed))
  return { parsed, story: normalizeEventStory(payload) }
}

export async function getEventObservation(reference: string) {
  const parsed = parseDetailUrl(reference)
  if (!parsed) throw new Error('事件详情引用无效')
  const payload = await apiGet<unknown>(buildObservationEndpoint(parsed))
  return { parsed, observation: normalizeEventObservation(payload) }
}

export function isEventObservationNotConfigured(error: unknown): boolean {
  if (!isAxiosError(error) || error.response?.status !== 404) return false
  const payload = error.response.data
  return isRecord(payload) && payload.observation_state === 'not_configured'
}

export const parseEventReference = (reference: string): ParsedDetailRef | null =>
  parseDetailUrl(reference)
