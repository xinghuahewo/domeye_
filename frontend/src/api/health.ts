import { apiGet } from './client'
import type { HealthPayload } from '@/types/api'

export const getHealth = () => apiGet<HealthPayload>('healthz')
