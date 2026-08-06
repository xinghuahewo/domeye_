import { isAxiosError } from 'axios'

import { apiGet, apiV2Get } from './client'
import {
  CORE_EVENT_TYPES,
  type CountryOutageAsnPage,
  type EventObservationAudit,
  type EventQuery,
  type ParsedDetailRef,
} from '@/types/api'
import {
  buildEvidenceEndpoint,
  buildDetailEndpoint,
  buildStoryEndpoint,
  normalizeEvidenceBundle,
  normalizeCountryOutageAsnPage,
  normalizeCountryOutageAudit,
  normalizeCountryOutageObservation,
  normalizeCountryOutageTrendProduct,
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

class EventObservationNotConfiguredError extends Error {
  constructor() {
    super('该事件类型未配置国家中断观测页')
    this.name = 'EventObservationNotConfiguredError'
  }
}

export async function getEventObservation(reference: string) {
  const parsed = parseDetailUrl(reference)
  if (!parsed) throw new Error('事件详情引用无效')
  if (parsed.kind !== 'country_outage') {
    throw new EventObservationNotConfiguredError()
  }
  const canonicalReference = [
    parsed.kind,
    parsed.startTime,
    parsed.problem,
    parsed.eventId,
    parsed.source,
  ].join('/')
  const resolution = await apiV2Get<unknown>('events/resolve', {
    params: { ref: canonicalReference },
  })
  if (
    !isRecord(resolution)
    || typeof resolution.incident_id !== 'string'
    || typeof resolution.publication_id !== 'string'
  ) {
    throw new Error('国家中断事件解析响应异常')
  }
  if (resolution.observation_state === 'legacy_summary') {
    throw new EventObservationNotConfiguredError()
  }
  const incidentId = encodeURIComponent(resolution.incident_id)
  const publicationParams = { publication_id: resolution.publication_id }
  const [overview, series, asnPage, audit] = await Promise.all([
    apiV2Get<unknown>(`country-outages/${incidentId}/overview`, {
      params: publicationParams,
    }),
    apiV2Get<unknown>(`country-outages/${incidentId}/series`, {
      params: publicationParams,
    }),
    apiV2Get<unknown>(`country-outages/${incidentId}/asns`, {
      params: {
        ...publicationParams,
        page: 1,
        page_size: 60,
      },
    }),
    apiV2Get<unknown>(`country-outages/${incidentId}/audit`, {
      params: publicationParams,
    }),
  ])
  const hasPublishedObservationIdentity = isRecord(overview)
    && typeof overview.incident_id === 'string'
    && typeof overview.publication_id === 'string'
    && typeof overview.revision === 'number'
    && typeof overview.data_through === 'string'
  const trendProduct = hasPublishedObservationIdentity
    ? await apiV2Get<unknown>(`country-outages/${incidentId}/trend`, {
        params: publicationParams,
      }).catch((error: unknown) => {
        if (isAxiosError(error) && [404, 422].includes(error.response?.status ?? 0)) {
          return null
        }
        throw error
      })
    : null
  return {
    parsed,
    observation: trendProduct === null
      ? normalizeCountryOutageObservation(overview, series, asnPage, audit)
      : normalizeCountryOutageObservation(
          overview,
          series,
          asnPage,
          audit,
          trendProduct,
        ),
  }
}

export async function getCountryOutageTrend(
  incidentId: string,
  publicationId: string,
) {
  const payload = await apiV2Get<unknown>(
    `country-outages/${encodeURIComponent(incidentId)}/trend`,
    { params: { publication_id: publicationId } },
  )
  return normalizeCountryOutageTrendProduct(payload)
}

export interface CountryOutageAsnQuery {
  publication_id: string
  page: number
  page_size: number
  query?: string
  address_family?: string
  state?: string
  sort?: string
}

export async function getCountryOutageAsns(
  incidentId: string,
  params: CountryOutageAsnQuery,
): Promise<CountryOutageAsnPage> {
  const payload = await apiV2Get<unknown>(
    `country-outages/${encodeURIComponent(incidentId)}/asns`,
    { params },
  )
  return normalizeCountryOutageAsnPage(payload)
}

export async function getCountryOutageAudit(
  incidentId: string,
  publicationId: string,
): Promise<EventObservationAudit> {
  const payload = await apiV2Get<unknown>(
    `country-outages/${encodeURIComponent(incidentId)}/audit`,
    { params: { publication_id: publicationId } },
  )
  return normalizeCountryOutageAudit(payload)
}

export function isEventObservationNotConfigured(error: unknown): boolean {
  if (error instanceof EventObservationNotConfiguredError) return true
  if (!isAxiosError(error) || error.response?.status !== 404) return false
  const payload = error.response.data
  return isRecord(payload) && payload.observation_state === 'not_configured'
}

export const parseEventReference = (reference: string): ParsedDetailRef | null =>
  parseDetailUrl(reference)
