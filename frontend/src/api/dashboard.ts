import { apiGet } from './client'
import { normalizeCountPoints, normalizeDashboardOverview } from '@/utils/normalize'

export interface DashboardRange {
  start_time: string
  end_time: string
}

export async function getEventCounts() {
  return normalizeCountPoints(await apiGet<unknown>('dashboard/counts/total'))
}

export const getTypeCount = (eventType: string) =>
  apiGet<Record<string, unknown>>('dashboard/counts/type', {
    params: { event_type: eventType },
  })

export async function getDashboardOverview(range: DashboardRange) {
  return normalizeDashboardOverview(await apiGet<unknown>('dashboard/overview', { params: range }))
}
