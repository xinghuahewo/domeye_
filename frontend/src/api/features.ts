import { apiGet } from './client'
import {
  normalizeAsOverview,
  normalizeCountryOverview,
  normalizeEventPage,
  normalizeFeaturePoints,
  normalizeOutagePoints,
} from '@/utils/normalize'

export interface FeatureRange {
  start_time: string
  end_time: string
}

export async function getTopFeatures(target: string, range: FeatureRange) {
  const payload = await apiGet<unknown>('features/top', {
    params: { target, ...range },
  })
  return normalizeFeaturePoints(payload)
}

export async function getCountryOverview(range: FeatureRange, country?: string, limit = 6) {
  return normalizeCountryOverview(await apiGet<unknown>('features/countries/overview', {
    params: { ...range, country: country || undefined, limit },
  }))
}

export async function getAsOverview(
  range: FeatureRange,
  asn?: string,
  limit = 6,
  eventWindow = false,
  eventReference = '',
) {
  return normalizeAsOverview(await apiGet<unknown>('features/ases/overview', {
    params: {
      ...range,
      asn: asn || undefined,
      limit,
      event_window: eventWindow || undefined,
      event_reference: eventWindow ? eventReference : undefined,
    },
  }))
}

export async function getAsRecentEvents(
  asn: string,
  range: FeatureRange,
  pageSize = 10,
  eventWindow = false,
  eventReference = '',
) {
  return normalizeEventPage(await apiGet<unknown>('features/ases/events', {
    params: {
      ...range,
      asn,
      page_size: pageSize,
      event_window: eventWindow || undefined,
      event_reference: eventWindow ? eventReference : undefined,
    },
  }))
}

export async function getGlobalASOutages(range: FeatureRange) {
  return normalizeOutagePoints(await apiGet<unknown>('features/outages/global-as', { params: range }))
}

export async function getGlobalPrefixOutages(range: FeatureRange) {
  return normalizeOutagePoints(await apiGet<unknown>('features/outages/global-prefix', { params: range }))
}

export async function getCountryASOutages(country: string, range: FeatureRange) {
  return normalizeOutagePoints(await apiGet<unknown>('features/outages/country-as', {
    params: { country, ...range },
  }))
}

export async function getCountryPrefixOutages(country: string, range: FeatureRange) {
  return normalizeOutagePoints(await apiGet<unknown>('features/outages/country-prefix', {
    params: { country, ...range },
  }))
}

export async function getASPrefixOutages(asn: string, range: FeatureRange) {
  return normalizeOutagePoints(await apiGet<unknown>('features/outages/as-prefix', {
    params: { asn, ...range },
  }))
}
