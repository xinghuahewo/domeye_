import { resolveApiTimeout } from './client'
import type {
  CountryOutageInvestigationActionResponse,
  CountryOutageInvestigationCasRequest,
  CountryOutageInvestigationCreateRequest,
  CountryOutageInvestigationEnvelope,
  CountryOutageInvestigationEvidenceGraph,
  CountryOutageInvestigationExportEnvelope,
  CountryOutageInvestigationExportRequest,
  CountryOutageInvestigationReceiptPage,
  CountryOutageInvestigationResultSet,
  CountryOutageInvestigationTurnRequest,
} from '@/types/api'

function investigationApiV2BaseUrl(value: string): string {
  const normalized = value.endsWith('/') ? value : `${value}/`
  if (/\/api\/v1\/$/i.test(normalized)) {
    return normalized.replace(/\/api\/v1\/$/i, '/api/v2/')
  }
  return '/api/v2/'
}

const baseUrl = () => investigationApiV2BaseUrl(import.meta.env.VITE_API_URL || '/api/v1/')
const timeout = () => resolveApiTimeout(import.meta.env.VITE_API_TIMEOUT_MS)
const safe = (value: string) => encodeURIComponent(value)

export class CountryOutageInvestigationRequestError extends Error {
  readonly status: number
  readonly code: string
  readonly retryable: boolean
  readonly nextAction?: string

  constructor(options: {
    status: number
    code: string
    message: string
    retryable?: boolean
    nextAction?: string
  }) {
    super(options.message)
    this.name = 'CountryOutageInvestigationRequestError'
    this.status = options.status
    this.code = options.code
    this.retryable = Boolean(options.retryable)
    this.nextAction = options.nextAction
  }
}

async function requestJson<T>(
  path: string,
  options: { method?: 'GET' | 'POST'; body?: unknown; idempotencyKey?: string } = {},
): Promise<T> {
  const response = await fetch(`${baseUrl()}${path}`, {
    method: options.method ?? 'GET',
    headers: {
      Accept: 'application/json',
      ...(options.body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(options.idempotencyKey ? { 'Idempotency-Key': options.idempotencyKey } : {}),
    },
    ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
    signal: AbortSignal.timeout(timeout()),
  })
  if (!response.ok) {
    let detail: Record<string, unknown> = {}
    try {
      const payload = await response.json() as { error?: Record<string, unknown> }
      detail = payload.error ?? {}
    } catch {
      // 非 JSON 错误保持闭合的客户端通用错误。
    }
    throw new CountryOutageInvestigationRequestError({
      status: response.status,
      code: typeof detail.code === 'string' ? detail.code : 'investigation_request_failed',
      message: typeof detail.message === 'string'
        ? detail.message
        : `组合调查请求失败：HTTP ${response.status}`,
      retryable: detail.retryable === true,
      nextAction: typeof detail.next_action === 'string' ? detail.next_action : undefined,
    })
  }
  return await response.json() as T
}

function post<T>(path: string, body: { idempotency_key: string }): Promise<T> {
  return requestJson<T>(path, {
    method: 'POST',
    body,
    idempotencyKey: body.idempotency_key,
  })
}

export function createCountryOutageInvestigation(
  body: CountryOutageInvestigationCreateRequest,
) {
  return post<CountryOutageInvestigationEnvelope>('country-outage/investigations', body)
}

export function getCountryOutageInvestigation(investigationId: string) {
  return requestJson<CountryOutageInvestigationEnvelope>(
    `country-outage/investigations/${safe(investigationId)}`,
  )
}

export function startCountryOutageInvestigation(
  investigationId: string,
  body: CountryOutageInvestigationCasRequest,
) {
  return post<CountryOutageInvestigationActionResponse>(
    `country-outage/investigations/${safe(investigationId)}/start`,
    body,
  )
}

export function cancelCountryOutageInvestigation(
  investigationId: string,
  body: CountryOutageInvestigationCasRequest,
) {
  return post<CountryOutageInvestigationActionResponse>(
    `country-outage/investigations/${safe(investigationId)}/cancel`,
    body,
  )
}

export function cancelCountryOutageInvestigationNode(
  investigationId: string,
  nodeId: string,
  body: CountryOutageInvestigationCasRequest,
) {
  return post<CountryOutageInvestigationActionResponse>(
    `country-outage/investigations/${safe(investigationId)}/nodes/${safe(nodeId)}/cancel`,
    body,
  )
}

export function rerunCountryOutageInvestigationNode(
  investigationId: string,
  nodeId: string,
  body: CountryOutageInvestigationCasRequest,
) {
  return post<CountryOutageInvestigationActionResponse>(
    `country-outage/investigations/${safe(investigationId)}/nodes/${safe(nodeId)}/reruns`,
    body,
  )
}

export function createCountryOutageInvestigationTurn(
  investigationId: string,
  body: CountryOutageInvestigationTurnRequest,
) {
  return post<CountryOutageInvestigationActionResponse>(
    `country-outage/investigations/${safe(investigationId)}/turns`,
    body,
  )
}

export function getCountryOutageInvestigationResultSet(
  investigationId: string,
  resultSetId: string,
  resultSetRevision: number,
  options: { pageSize?: number; pageToken?: string } = {},
) {
  const query = new URLSearchParams()
  query.set('page_size', String(options.pageSize ?? 50))
  if (options.pageToken) query.set('page_token', options.pageToken)
  return requestJson<CountryOutageInvestigationResultSet>(
    `country-outage/investigations/${safe(investigationId)}/result-sets/${safe(resultSetId)}/revisions/${resultSetRevision}?${query}`,
  )
}

export function getCountryOutageInvestigationEvidenceGraph(
  investigationId: string,
  graphRevision: number,
) {
  return requestJson<CountryOutageInvestigationEvidenceGraph>(
    `country-outage/investigations/${safe(investigationId)}/evidence-graphs/${graphRevision}`,
  )
}

export function getCountryOutageInvestigationReceipts(
  investigationId: string,
  kind?: 'tool' | 'operator' | 'model' | 'cost' | 'latency' | 'reuse' | 'transaction',
) {
  const query = kind ? `?kind=${encodeURIComponent(kind)}` : ''
  return requestJson<CountryOutageInvestigationReceiptPage>(
    `country-outage/investigations/${safe(investigationId)}/receipts${query}`,
  )
}

export function createCountryOutageInvestigationExport(
  investigationId: string,
  body: CountryOutageInvestigationExportRequest,
) {
  return post<CountryOutageInvestigationExportEnvelope>(
    `country-outage/investigations/${safe(investigationId)}/exports`,
    body,
  )
}

export function getCountryOutageInvestigationExport(
  investigationId: string,
  exportId: string,
) {
  return requestJson<CountryOutageInvestigationExportEnvelope>(
    `country-outage/investigations/${safe(investigationId)}/exports/${safe(exportId)}`,
  )
}

export function countryOutageInvestigationExportArtifactUrl(
  investigationId: string,
  exportId: string,
): string {
  return `${baseUrl()}country-outage/investigations/${safe(investigationId)}/exports/${safe(exportId)}/artifact`
}
