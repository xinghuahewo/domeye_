import axios, { type AxiosRequestConfig } from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api/v1/',
  timeout: 20_000,
  headers: {
    Accept: 'application/json',
  },
})

export async function apiGet<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const response = await api.get<T>(url, config)
  return response.data
}
