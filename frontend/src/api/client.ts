import axios, { type AxiosRequestConfig } from 'axios'

export const DEFAULT_API_TIMEOUT_MS = 60_000

export function resolveApiTimeout(value: string | undefined): number {
  const timeout = Number(value)
  return Number.isFinite(timeout) && timeout > 0 ? timeout : DEFAULT_API_TIMEOUT_MS
}

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api/v1/',
  timeout: resolveApiTimeout(import.meta.env.VITE_API_TIMEOUT_MS),
  headers: {
    Accept: 'application/json',
  },
})

function v2BaseUrl(value: string): string {
  const normalized = value.endsWith('/') ? value : `${value}/`
  if (/\/api\/v1\/$/i.test(normalized)) {
    return normalized.replace(/\/api\/v1\/$/i, '/api/v2/')
  }
  return '/api/v2/'
}

const apiV2 = axios.create({
  baseURL: v2BaseUrl(import.meta.env.VITE_API_URL || '/api/v1/'),
  timeout: resolveApiTimeout(import.meta.env.VITE_API_TIMEOUT_MS),
  headers: {
    Accept: 'application/json',
  },
})

export async function apiGet<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const response = await api.get<T>(url, config)
  return response.data
}

export async function apiV2Get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const response = await apiV2.get<T>(url, config)
  return response.data
}
