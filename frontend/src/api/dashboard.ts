import { apiGet } from './client'
import { normalizeCountPoints } from '@/utils/normalize'

export async function getEventCounts() {
  return normalizeCountPoints(await apiGet<unknown>('dashboard/counts/total'))
}

export const getTypeCount = (eventType: string) =>
  apiGet<Record<string, unknown>>('dashboard/counts/type', {
    params: { event_type: eventType },
  })
