import type { InjectionKey } from 'vue'

export type CountryOutageAgentReportPhase =
  | 'queued'
  | 'reading_data'
  | 'generating_report'
  | 'validating'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type CountryOutageAgentQuestionPhase =
  | 'answering'
  | 'collecting_external'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type CountryOutageAgentEventType =
  | 'report_state'
  | 'question_state'
  | 'session_notice'

export interface CountryOutageAgentSession {
  expires_at: string
  reminder_at: string
}

export interface CountryOutageExternalEvidencePolicy {
  version: string
  sha256: string
  allowed_host_roots: string[]
  minimum_urls: number
  maximum_urls: number
}

interface CountryOutageExternalEvidenceCapabilityBase {
  schema_version: 'country_outage_external_evidence_capability_v1'
  capability: 'external_evidence'
  checked_at: string
}

export interface CountryOutageExternalEvidenceCapabilityReady
  extends CountryOutageExternalEvidenceCapabilityBase {
  state: 'ready'
  provider: 'managed-egress-v1'
  policy: CountryOutageExternalEvidencePolicy
  reason_code?: never
}

export interface CountryOutageExternalEvidenceCapabilityNotConfigured
  extends CountryOutageExternalEvidenceCapabilityBase {
  state: 'not_configured'
  provider: 'disabled'
  policy: null
  reason_code: string
}

export interface CountryOutageExternalEvidenceCapabilitySelfCheckFailed
  extends CountryOutageExternalEvidenceCapabilityBase {
  state: 'self_check_failed'
  provider: 'managed-egress-v1'
  policy: null
  reason_code: string
}

export type CountryOutageExternalEvidenceCapability =
  | CountryOutageExternalEvidenceCapabilityReady
  | CountryOutageExternalEvidenceCapabilityNotConfigured
  | CountryOutageExternalEvidenceCapabilitySelfCheckFailed

export interface CountryOutageReportStartRequest {
  event_reference: string
  publication_id: string
  revision: number
  idempotency_key: string
}

export interface CountryOutageReportStartResponse {
  schema_version: string
  report_id: string
  run_id: string
  state: string
  phase: CountryOutageAgentReportPhase
  session: CountryOutageAgentSession
  deduplicated: boolean
}

export interface CountryOutageQuestionQuote {
  kind: 'summary' | 'highlight' | 'section_paragraph'
  section_id?: string
  paragraph_index?: number
  highlight_index?: number
  evidence_refs?: string[]
}

export type CountryOutageEvidenceMode =
  | 'domeye_only'
  | 'domeye_plus_external'

interface CountryOutageQuestionRequestBase {
  question: string
  quote?: CountryOutageQuestionQuote
  idempotency_key: string
}

export interface CountryOutageDomeyeOnlyQuestionRequest
  extends CountryOutageQuestionRequestBase {
  evidence_mode: 'domeye_only'
  external_authorization?: never
  external_urls?: never
}

export interface CountryOutageExternalQuestionRequest
  extends CountryOutageQuestionRequestBase {
  evidence_mode: 'domeye_plus_external'
  external_authorization: {
    authorized: true
    authorized_at: string
  }
  external_urls: string[]
}

export type CountryOutageQuestionRequest =
  | CountryOutageDomeyeOnlyQuestionRequest
  | CountryOutageExternalQuestionRequest

export interface CountryOutageQuestionStartResponse {
  schema_version: string
  report_id: string
  run_id: string
  question_id: string
  number: number
  state: string
  phase: CountryOutageAgentQuestionPhase
  session: CountryOutageAgentSession
  deduplicated: boolean
}

export interface CountryOutageReportParagraph {
  text: string
  evidenceRefs: string[]
}

export interface CountryOutageReportHighlight {
  label: string
  value: string
  evidenceRefs: string[]
}

export interface CountryOutageReportSection {
  id: string
  title: string
  paragraphs: CountryOutageReportParagraph[]
}

export interface CountryOutageReportDocument {
  schemaVersion: 'country_outage_report_document_v1'
  artifactId: string
  reportContentSha256: string
  reportSpecificationVersion: 'country_outage_report_spec_v1'
  projectKnowledgeVersion: 'country_outage_report_skill_v6'
  factSetId: string
  validatorRulesVersion: 'country_outage_report_validator_rules_v5'
  skillBundleSha256: string
  generatedAt: string
  aiGenerated: true
  humanReviewed: false
  event: {
    incident_id: string
    legacy_reference: string
    event_type: 'country_outage'
    country_code: string
    country_name: string
    display_name: string
  }
  snapshot: {
    incidentId: string
    publicationId: string
    revision: number
    dataThrough: string | null
    isFinal: boolean
    collectorId: 'rrc25'
    windowStartUtc: string
    windowEndUtc: string
    cohortId: string
  }
  model: {
    provider: string
    model: string
    modelVersion: string
    adapter: 'pi-sdk' | 'deterministic-acceptance'
    piVersion?: string
    runtimeIdentity?: 'formal' | 'candidate'
    modelRevisionKind?: 'mutable_alias'
    immutableRevisionAvailable?: false
    limitation?: string
    certificationValidUntil?: string
    certifiedScenarioSetId?: string
    certifiedInputScope?: string
  }
  validation: {
    passed: boolean
    errors: string[]
    warnings: string[]
    checkedEvidenceRefs: string[]
  }
  draft: {
    schemaVersion: 'country_outage_report_draft_v1'
    title: string
    subtitle: string
    summary: CountryOutageReportParagraph
    highlights: CountryOutageReportHighlight[]
    sections: CountryOutageReportSection[]
    unknowns: string[]
  }
}

export interface CountryOutageArtifactReady {
  format: 'markdown' | 'pdf'
  status: 'ready'
  artifact_id: string
  filename: string
  media_type: string
  byte_length: number
  sha256: string
  download_url?: string
}

export interface CountryOutageArtifactFailed {
  format: 'markdown' | 'pdf'
  status: 'failed'
  code: string
  message: string
}

export type CountryOutageArtifact =
  | CountryOutageArtifactReady
  | CountryOutageArtifactFailed

export interface CountryOutageExternalClaim {
  claim_id: string
  text: string
  status: 'supported' | 'mixed' | 'conflict' | 'insufficient'
  source_ids: string[]
  limitations: string[]
}

export interface CountryOutageExternalFrozenBinding {
  incident_id: string
  publication_id: string
  revision: number
  data_through: string | null
  fact_set_id: string
  cohort_id: string
  country_code: string
  collector_id: 'rrc25'
  window_start_utc: string
  window_end_utc: string
}

export interface CountryOutageExternalStructuredFact {
  fact_id: string
  binding_id: string
  metric: 'bgp_control_plane_visibility_state'
  address_family: 'all' | 'ipv4' | 'ipv6'
  observed_window_start_utc: string
  observed_window_end_utc: string
  source_value:
    | 'degraded'
    | 'visibility_reduced'
    | 'stable'
    | 'no_material_change'
    | 'recovering'
    | 'visibility_improving'
    | 'recovered'
    | 'baseline_restored'
  normalized_value:
    | 'degraded'
    | 'stable'
    | 'recovering'
    | 'recovered'
}

export interface CountryOutageExternalSource {
  source_id: string
  title: string | null
  publisher: string | null
  url: string
  published_at: string | null
  retrieved_at: string | null
  source_classification: 'measurement_platform' | 'unknown'
  source_tier: 'direct' | 'secondary' | 'lead' | 'unknown'
  read_status: 'readable' | 'unreadable' | 'blocked' | 'failed'
  read_status_detail: string | null
  summary: string | null
  evidence_status?: 'available' | 'insufficient' | 'read_failed'
  evidence_status_detail?: string | null
  structured_facts?: CountryOutageExternalStructuredFact[]
}

export interface CountryOutageExternalAppendix {
  schema_version: 'country_outage_external_appendix_v1'
  classification_policy_version:
    'country_outage_external_source_classification_policy_v1'
  status: 'collecting' | 'completed' | 'partial' | 'failed'
  comparison_status?:
    | 'supported'
    | 'mixed'
    | 'conflict'
    | 'insufficient'
  frozen_binding?: CountryOutageExternalFrozenBinding
  query: string
  requested_at: string
  retrieved_at: string | null
  claims: CountryOutageExternalClaim[]
  sources: CountryOutageExternalSource[]
  error?: CountryOutageAgentError
}

export interface CountryOutageQuestionResult {
  question_id: string
  number: number
  question: string
  quote?: CountryOutageQuestionQuote
  evidence_mode: CountryOutageEvidenceMode
  state: string
  answer?: {
    text: string
    evidence_refs: string[]
    limitations: string[]
    kind?:
      | 'fact'
      | 'metric_semantics'
      | 'evidence_boundary'
      | 'insufficient_evidence'
    evidence_records?: Array<{
      evidence_ref: string
      source:
        | 'report'
        | 'overview'
        | 'series'
        | 'audit'
        | 'derived_fact'
        | 'asn_detail'
      label: string
      metric: string | null
      value: string | null
      observed_at_utc: string | null
      observed_at_local: string | null
      statistical_scope: string
    }>
    missing_evidence?: string[]
    snapshot?: CountryOutageReportDocument['snapshot']
  }
  external_appendix?: CountryOutageExternalAppendix
  error?: CountryOutageAgentError
}

export interface CountryOutageAgentError {
  code: string
  message: string
  retryable: boolean
  next_action?: string
}

export class CountryOutageAgentRequestError extends Error {
  readonly code: string
  readonly retryable: boolean
  readonly nextAction?: string

  constructor(error: CountryOutageAgentError) {
    super(error.message)
    this.name = error.code
    this.code = error.code
    this.retryable = error.retryable
    this.nextAction = error.next_action
  }
}

export class CountryOutageAgentOutcomeUncertainError extends Error {
  readonly outcomeUncertain = true

  constructor(message: string) {
    super(message)
    this.name = 'country_outage_request_outcome_uncertain'
  }
}

const COUNTRY_OUTAGE_UNCERTAIN_PROXY_ERROR_CODES = new Set([
  'agent_unavailable',
  'agent_response_too_large',
  'HTTP_502',
  'HTTP_503',
  'HTTP_504',
])

export function isCountryOutageRequestOutcomeUncertain(
  cause: unknown,
): boolean {
  return (
    cause instanceof CountryOutageAgentOutcomeUncertainError
    || (
      cause instanceof CountryOutageAgentRequestError
      && COUNTRY_OUTAGE_UNCERTAIN_PROXY_ERROR_CODES.has(cause.code)
    )
    || cause instanceof TypeError
  )
}

export interface CountryOutageAgentEvent {
  schema_version: 'country_outage_agent_event_v1'
  event_id: number
  report_id: string
  run_id?: string
  event_type: CountryOutageAgentEventType
  at: string
  state: string
  phase:
    | CountryOutageAgentReportPhase
    | CountryOutageAgentQuestionPhase
    | 'session_expiring'
    | 'session_expired'
  session: CountryOutageAgentSession
  snapshot?: CountryOutageReportDocument['snapshot']
  report?: CountryOutageReportDocument
  artifacts?: CountryOutageArtifact[]
  question?: CountryOutageQuestionResult
  error?: CountryOutageAgentError
}

export interface CountryOutageAgentEventCallbacks {
  onEvent: (event: CountryOutageAgentEvent) => void
  onConnectionChange?: (state: 'connected' | 'retrying') => void
  onProtocolError?: (message: string) => void
}

export interface CountryOutageAgentSubscription {
  close(): void
}

export interface CountryOutageAgentApi {
  getExternalEvidenceCapability(
    signal?: AbortSignal,
  ): Promise<CountryOutageExternalEvidenceCapability>
  startReport(
    request: CountryOutageReportStartRequest,
    signal?: AbortSignal,
  ): Promise<CountryOutageReportStartResponse>
  askQuestion(
    reportId: string,
    request: CountryOutageQuestionRequest,
    signal?: AbortSignal,
  ): Promise<CountryOutageQuestionStartResponse>
  abortRun(runId: string, signal?: AbortSignal): Promise<void>
  subscribe(
    reportId: string,
    callbacks: CountryOutageAgentEventCallbacks,
  ): CountryOutageAgentSubscription
  artifactUrl(reportId: string, format: 'markdown' | 'pdf'): string
  externalAppendixArtifactUrl(
    reportId: string,
    questionId: string,
  ): string
}

export const COUNTRY_OUTAGE_AGENT_API_KEY:
InjectionKey<CountryOutageAgentApi> = Symbol('CountryOutageAgentApi')

export function resolveCountryOutageAgentBase(apiUrl: string | undefined): string {
  const configured = apiUrl?.trim() || '/api/v1/'
  const normalized = configured.endsWith('/') ? configured : `${configured}/`
  const v2 = /\/api\/v1\/$/i.test(normalized)
    ? normalized.replace(/\/api\/v1\/$/i, '/api/v2/')
    : (
        /\/api\/v2\/$/i.test(normalized)
          ? normalized
          : '/api/v2/'
      )
  return `${v2}country-outage/`
}

function joinAgentPath(path: string): string {
  return `${resolveCountryOutageAgentBase(import.meta.env.VITE_API_URL)}${path}`
}

function errorFromPayload(status: number, payload: unknown): Error {
  if (
    typeof payload === 'object'
    && payload !== null
    && 'error' in payload
    && typeof payload.error === 'object'
    && payload.error !== null
    && 'message' in payload.error
    && typeof payload.error.message === 'string'
  ) {
    return new CountryOutageAgentRequestError({
      code: (
        'code' in payload.error && typeof payload.error.code === 'string'
          ? payload.error.code
          : `HTTP_${status}`
      ),
      message: payload.error.message,
      retryable: (
        'retryable' in payload.error && typeof payload.error.retryable === 'boolean'
          ? payload.error.retryable
          : false
      ),
      ...(
        'next_action' in payload.error && typeof payload.error.next_action === 'string'
          ? { next_action: payload.error.next_action }
          : {}
      ),
    })
  }
  return new CountryOutageAgentRequestError({
    code: `HTTP_${status}`,
    message: `国家中断报告服务请求失败（HTTP ${status}）`,
    retryable: false,
  })
}

function isAbortError(cause: unknown): boolean {
  return (
    cause instanceof Error
    && cause.name === 'AbortError'
  )
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return (
    typeof value === 'object'
    && value !== null
    && !Array.isArray(value)
  )
    ? value as Record<string, unknown>
    : null
}

function hasExactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return (
    actual.length === expected.length
    && actual.every((key, index) => key === expected[index])
  )
}

function validPolicyHostRoot(value: unknown): value is string {
  return (
    typeof value === 'string'
    && value.length <= 253
    && value === value.toLowerCase()
    && value.includes('.')
    && !value.endsWith('.')
    && /^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/.test(value)
    && value.split('.').every(
      (label) => (
        label.length >= 1
        && label.length <= 63
        && !label.startsWith('-')
        && !label.endsWith('-')
      ),
    )
  )
}

function parseExternalEvidencePolicy(
  value: unknown,
): CountryOutageExternalEvidencePolicy | null {
  const policy = recordValue(value)
  if (
    !policy
    || !hasExactKeys(policy, [
      'version',
      'sha256',
      'allowed_host_roots',
      'minimum_urls',
      'maximum_urls',
    ])
    || typeof policy.version !== 'string'
    || !policy.version.trim()
    || typeof policy.sha256 !== 'string'
    || !/^[a-f0-9]{64}$/.test(policy.sha256)
    || !Array.isArray(policy.allowed_host_roots)
    || policy.allowed_host_roots.length === 0
    || policy.allowed_host_roots.length > 5
    || !policy.allowed_host_roots.every(validPolicyHostRoot)
    || new Set(policy.allowed_host_roots).size
      !== policy.allowed_host_roots.length
    || typeof policy.minimum_urls !== 'number'
    || typeof policy.maximum_urls !== 'number'
    || !Number.isInteger(policy.minimum_urls)
    || !Number.isInteger(policy.maximum_urls)
    || policy.minimum_urls !== 1
    || policy.maximum_urls < 1
    || policy.maximum_urls > 5
  ) {
    return null
  }
  return {
    version: policy.version,
    sha256: policy.sha256,
    allowed_host_roots: [...policy.allowed_host_roots] as string[],
    minimum_urls: policy.minimum_urls,
    maximum_urls: policy.maximum_urls,
  }
}

export function parseCountryOutageExternalEvidenceCapability(
  value: unknown,
): CountryOutageExternalEvidenceCapability {
  const payload = recordValue(value)
  const fail = (): never => {
    throw new CountryOutageAgentRequestError({
      code: 'external_evidence_capability_protocol_invalid',
      message: '公开来源旁证能力状态响应无效，当前按不可用处理。',
      retryable: true,
      next_action: '可重新检查能力状态；Domeye 报告、追问和下载不受影响。',
    })
  }
  if (
    !payload
    || payload.schema_version
      !== 'country_outage_external_evidence_capability_v1'
    || payload.capability !== 'external_evidence'
    || typeof payload.checked_at !== 'string'
    || !Number.isFinite(Date.parse(payload.checked_at))
  ) {
    return fail()
  }
  if (payload.state === 'ready') {
    const policy = parseExternalEvidencePolicy(payload.policy)
    if (
      !hasExactKeys(payload, [
        'schema_version',
        'capability',
        'state',
        'provider',
        'checked_at',
        'policy',
      ])
      || payload.provider !== 'managed-egress-v1'
      || !policy
    ) {
      return fail()
    }
    return {
      schema_version: payload.schema_version,
      capability: payload.capability,
      state: payload.state,
      provider: payload.provider,
      checked_at: payload.checked_at,
      policy,
    }
  }
  if (
    payload.state === 'not_configured'
    || payload.state === 'self_check_failed'
  ) {
    if (
      !hasExactKeys(payload, [
        'schema_version',
        'capability',
        'state',
        'provider',
        'checked_at',
        'policy',
        'reason_code',
      ])
      || payload.policy !== null
      || typeof payload.reason_code !== 'string'
      || !payload.reason_code.trim()
    ) {
      return fail()
    }
    if (
      payload.state === 'not_configured'
      && payload.provider === 'disabled'
    ) {
      return {
        schema_version: payload.schema_version,
        capability: payload.capability,
        state: payload.state,
        provider: payload.provider,
        checked_at: payload.checked_at,
        policy: null,
        reason_code: payload.reason_code,
      }
    }
    if (
      payload.state === 'self_check_failed'
      && payload.provider === 'managed-egress-v1'
    ) {
      return {
        schema_version: payload.schema_version,
        capability: payload.capability,
        state: payload.state,
        provider: payload.provider,
        checked_at: payload.checked_at,
        policy: null,
        reason_code: payload.reason_code,
      }
    }
  }
  return fail()
}

async function getJson(path: string, signal?: AbortSignal): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(joinAgentPath(path), {
      method: 'GET',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
      },
      signal,
    })
  } catch (cause) {
    if (isAbortError(cause)) throw cause
    throw new CountryOutageAgentRequestError({
      code: 'external_evidence_capability_unavailable',
      message: '暂时无法确认公开来源旁证能力状态。',
      retryable: true,
      next_action: '可重新检查；Domeye 报告、追问和下载不受影响。',
    })
  }
  let payload: unknown = null
  try {
    payload = await response.json()
  } catch {
    if (!response.ok) throw errorFromPayload(response.status, null)
    throw new CountryOutageAgentRequestError({
      code: 'external_evidence_capability_protocol_invalid',
      message: '公开来源旁证能力状态响应无法读取，当前按不可用处理。',
      retryable: true,
      next_action: '可重新检查；Domeye 报告、追问和下载不受影响。',
    })
  }
  if (!response.ok) throw errorFromPayload(response.status, payload)
  return payload
}

async function postJson<T>(
  path: string,
  body: object,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response
  try {
    response = await fetch(joinAgentPath(path), {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        ...(
          'idempotency_key' in body && typeof body.idempotency_key === 'string'
            ? { 'Idempotency-Key': body.idempotency_key }
            : {}
        ),
      },
      body: JSON.stringify(body),
      signal,
    })
  } catch (cause) {
    if (isAbortError(cause)) throw cause
    throw new CountryOutageAgentOutcomeUncertainError(
      '请求可能已经被服务端接受，但浏览器未收到响应；重试将复用同一幂等键。',
    )
  }
  let payload: unknown = null
  try {
    payload = await response.json()
  } catch (cause) {
    if (response.ok && isAbortError(cause)) {
      throw new CountryOutageAgentOutcomeUncertainError(
        '服务端已返回成功状态，但浏览器在取得运行身份前停止读取响应；重试将复用同一幂等键。',
      )
    }
  }
  if (!response.ok) throw errorFromPayload(response.status, payload)
  if (
    typeof payload !== 'object'
    || payload === null
    || Array.isArray(payload)
  ) {
    throw new CountryOutageAgentOutcomeUncertainError(
      '服务端返回成功状态，但响应内容未完整到达；重试将复用同一幂等键。',
    )
  }
  return payload as T
}

const streamEventNames = [
  'report_state',
  'question_state',
  'session_notice',
] as const

function createEventSourceSubscription(
  reportId: string,
  callbacks: CountryOutageAgentEventCallbacks,
): CountryOutageAgentSubscription {
  const source = new EventSource(
    joinAgentPath(`reports/${encodeURIComponent(reportId)}/events`),
    { withCredentials: true },
  )

  const receive = (message: MessageEvent<string>) => {
    try {
      const event = JSON.parse(message.data) as CountryOutageAgentEvent
      if (
        event.schema_version !== 'country_outage_agent_event_v1'
        || event.report_id !== reportId
      ) {
        callbacks.onProtocolError?.('收到与当前报告身份不一致的状态事件')
        return
      }
      callbacks.onEvent(event)
    } catch {
      callbacks.onProtocolError?.('报告状态事件格式无效')
    }
  }

  source.onopen = () => callbacks.onConnectionChange?.('connected')
  source.onerror = () => callbacks.onConnectionChange?.('retrying')
  for (const eventName of streamEventNames) {
    source.addEventListener(eventName, receive as EventListener)
  }

  return {
    close() {
      source.close()
    },
  }
}

export const countryOutageAgentApi: CountryOutageAgentApi = {
  async getExternalEvidenceCapability(signal) {
    return parseCountryOutageExternalEvidenceCapability(
      await getJson('capabilities/external-evidence', signal),
    )
  },
  startReport(request, signal) {
    return postJson<CountryOutageReportStartResponse>('reports', request, signal)
  },
  askQuestion(reportId, request, signal) {
    return postJson<CountryOutageQuestionStartResponse>(
      `reports/${encodeURIComponent(reportId)}/questions`,
      request,
      signal,
    )
  },
  async abortRun(runId, signal) {
    await postJson<unknown>(
      `runs/${encodeURIComponent(runId)}/abort`,
      {},
      signal,
    )
  },
  subscribe: createEventSourceSubscription,
  artifactUrl(reportId, format) {
    return joinAgentPath(
      `reports/${encodeURIComponent(reportId)}/artifacts/${format}`,
    )
  },
  externalAppendixArtifactUrl(reportId, questionId) {
    return joinAgentPath(
      (
        `reports/${encodeURIComponent(reportId)}/questions/`
        + `${encodeURIComponent(questionId)}/artifacts/external-appendix`
      ),
    )
  },
}
