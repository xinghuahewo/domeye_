import { apiGet } from './client'
import { normalizeFeaturePoints, normalizeOutagePoints } from '@/utils/normalize'

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
