import type { components, paths } from '@/types/openapi.generated'

import { apiGet } from './client'

export type P0Status = paths['/p0/status']['get']['responses'][200]['content']['application/json']
export type P0MetricResponse = paths['/p0/metrics/{metric_name}']['get']['responses'][200]['content']['application/json']
export type P0EvidenceResponse = paths['/p0/evidence/{incident_id}']['get']['responses'][200]['content']['application/json']
export type P0QualityResponse = paths['/p0/quality']['get']['responses'][200]['content']['application/json']
export type P0MetricName = paths['/p0/metrics/{metric_name}']['get']['parameters']['path']['metric_name']
export type P0MetricSeries = components['schemas']['metric-series.schema']
export type P0MetricPoint = components['schemas']['point']
export type P0Profile = components['schemas']['P0Profile']

export const getP0Status = () => apiGet<P0Status>('p0/status')

export const getP0MetricSeries = (metricName: P0MetricName) =>
  apiGet<P0MetricResponse>(`p0/metrics/${encodeURIComponent(metricName)}`)

export const getP0Evidence = (incidentId: string) =>
  apiGet<P0EvidenceResponse>(`p0/evidence/${encodeURIComponent(incidentId)}`)

export const getP0Quality = () => apiGet<P0QualityResponse>('p0/quality')
