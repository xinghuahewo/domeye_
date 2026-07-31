import type {
  CountryOutageAgentReportPhase,
  CountryOutageArtifact,
  CountryOutageExternalEvidencePolicy,
} from '@/api/countryOutageAgent'
import type { EventObservation } from '@/types/api'

export const MAXIMUM_COUNTRY_OUTAGE_ANSWER_CHARACTERS = 4_000

export type CountryOutageView = 'observation' | 'report'

export interface ExpirableReaderQuestion {
  state: 'answering' | 'completed' | 'failed' | 'cancelled'
  error?: string
  nextAction?: string
}

export interface ExternalEvidenceUrlValidation {
  valid: boolean
  normalized?: string
  error?: string
}

export interface ExternalEvidenceUrlListValidation {
  urls: string[]
  fieldErrors: Record<number, string>
  globalError?: string
}

export type ExternalEvidenceUrlFocusTarget =
  | { kind: 'url'; index: number }
  | { kind: 'add' }

export interface ReportStagePresentation {
  index: string
  label: string
  detail: string
}

export interface CountryOutagePagePreflightCheck {
  label: string
  passed: boolean
}

export type LogicalSubmissionOutcome =
  | 'accepted'
  | 'deterministic_rejection'
  | 'outcome_uncertain'

export interface LogicalSubmissionAttempt {
  readonly fingerprint: string
  readonly idempotencyKey: string
}

export class LogicalSubmissionIdempotency {
  private pending: LogicalSubmissionAttempt | null = null

  constructor(private readonly createKey: () => string) {}

  begin(fingerprint: string): LogicalSubmissionAttempt {
    if (!fingerprint) {
      throw new Error('逻辑提交 fingerprint 不能为空')
    }
    if (this.pending?.fingerprint === fingerprint) return this.pending
    this.pending = {
      fingerprint,
      idempotencyKey: this.createKey(),
    }
    return this.pending
  }

  settle(
    attempt: LogicalSubmissionAttempt,
    outcome: LogicalSubmissionOutcome,
  ): void {
    if (
      this.pending?.fingerprint !== attempt.fingerprint
      || this.pending.idempotencyKey !== attempt.idempotencyKey
    ) return
    if (outcome !== 'outcome_uncertain') this.pending = null
  }

  clear(): void {
    this.pending = null
  }
}

export function logicalSubmissionFingerprint(value: unknown): string {
  const fingerprint = JSON.stringify(value)
  if (typeof fingerprint !== 'string' || !fingerprint) {
    throw new Error('逻辑提交内容无法序列化')
  }
  return fingerprint
}

const TERMINAL_AGENT_RUN_STATES = new Set([
  'completed',
  'failed',
  'cancelled',
])

export function shouldIgnoreLateAgentEvent(
  currentState: string | null | undefined,
  incomingState: string,
  cancellationPending = false,
): boolean {
  if (cancellationPending && incomingState === 'completed') return true
  return (
    Boolean(currentState)
    && TERMINAL_AGENT_RUN_STATES.has(currentState!)
    && currentState !== incomingState
  )
}

const stages: Record<CountryOutageAgentReportPhase, ReportStagePresentation> = {
  queued: {
    index: '01',
    label: '排队',
    detail: '已登记本次用户触发请求，等待只读执行槽位。',
  },
  reading_data: {
    index: '02',
    label: '读取数据',
    detail: '正在固定 RRC25 发布快照并组装确定性事实。',
  },
  generating_report: {
    index: '03',
    label: '生成报告',
    detail: '正在根据项目知识组织中文技术叙事。',
  },
  validating: {
    index: '04',
    label: '校验',
    detail: '正在核对数字、来源、章节能力和控制面边界。',
  },
  completed: {
    index: '05',
    label: '完成',
    detail: '只读报告已一次性发布。',
  },
  failed: {
    index: '—',
    label: '失败',
    detail: '本次运行没有发布草稿或半成品。',
  },
  cancelled: {
    index: '—',
    label: '已取消',
    detail: '本次运行已停止，不会发布稍后到达的结果。',
  },
}

export function reportStagePresentation(
  phase: CountryOutageAgentReportPhase,
): ReportStagePresentation {
  return stages[phase]
}

export function sessionSecondsRemaining(
  expiresAt: string | null | undefined,
  now = Date.now(),
): number {
  if (!expiresAt) return 0
  const expiry = Date.parse(expiresAt)
  if (!Number.isFinite(expiry)) return 0
  return Math.max(0, Math.ceil((expiry - now) / 1000))
}

export function formatRemainingTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
}

export function countryOutageViewForTabKey(
  current: CountryOutageView,
  key: string,
): CountryOutageView | null {
  if (key === 'Home') return 'observation'
  if (key === 'End') return 'report'
  if (key === 'ArrowLeft') {
    return current === 'observation' ? 'report' : 'observation'
  }
  if (key === 'ArrowRight') {
    return current === 'report' ? 'observation' : 'report'
  }
  return null
}

export function closeAnsweringQuestionsAtSessionExpiry<
  T extends ExpirableReaderQuestion,
>(questions: T[]): number {
  let closed = 0
  for (const question of questions) {
    if (question.state !== 'answering') continue
    question.state = 'failed'
    question.error = '短期会话已到期，本次回答未发布。'
    question.nextAction = '请基于当前合法快照重新生成报告后再提问。'
    closed += 1
  }
  return closed
}

function isNonPublicIpv4(hostname: string): boolean {
  const parts = hostname.split('.')
  if (
    parts.length !== 4
    || parts.some((part) => !/^\d{1,3}$/.test(part))
  ) {
    return false
  }
  const octets = parts.map(Number)
  if (octets.some((value) => value < 0 || value > 255)) return true
  const first = octets[0]!
  const second = octets[1]!
  const third = octets[2]!
  return (
    first === 0
    || first === 10
    || first === 100 && second >= 64 && second <= 127
    || first === 127
    || first === 169 && second === 254
    || first === 172 && second >= 16 && second <= 31
    || first === 192 && second === 0 && third === 0
    || first === 192 && second === 0 && third === 2
    || first === 192 && second === 168
    || first === 198 && second >= 18 && second <= 19
    || first === 198 && second === 51 && third === 100
    || first === 203 && second === 0 && third === 113
    || first >= 224
  )
}

function isNonPublicHostname(value: string): boolean {
  const hostname = value.toLowerCase().replace(/^\[|\]$/g, '')
  if (
    hostname === 'localhost'
    || hostname.endsWith('.localhost')
    || hostname.endsWith('.local')
    || hostname.endsWith('.internal')
    || hostname.endsWith('.home')
    || !hostname.includes('.')
  ) {
    return true
  }
  if (isNonPublicIpv4(hostname)) return true
  if (!hostname.includes(':')) return false
  return (
    hostname === '::'
    || hostname === '::1'
    || hostname.startsWith('fc')
    || hostname.startsWith('fd')
    || /^fe[89ab]/.test(hostname)
    || hostname.startsWith('2001:db8:')
    || hostname.startsWith('::ffff:')
  )
}

function isAllowedExternalEvidenceHostname(
  value: string,
  allowedHostRoots: readonly string[],
): boolean {
  const hostname = value.toLowerCase().replace(/\.$/, '')
  return allowedHostRoots.some(
    (valueRoot) => {
      const root = valueRoot.toLowerCase().replace(/\.$/, '')
      return hostname === root || hostname.endsWith(`.${root}`)
    },
  )
}

export function validateExternalEvidenceUrl(
  value: string,
  policy: CountryOutageExternalEvidencePolicy,
): ExternalEvidenceUrlValidation {
  const trimmed = value.trim()
  if (!trimmed) return { valid: false, error: '请输入公开 URL。' }
  let parsed: URL
  try {
    parsed = new URL(trimmed)
  } catch {
    return { valid: false, error: 'URL 格式无效。' }
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    return { valid: false, error: '只允许 http 或 https 公开链接。' }
  }
  if (parsed.port) {
    return { valid: false, error: '只允许 http/https 标准端口。' }
  }
  if (parsed.username || parsed.password) {
    return { valid: false, error: '链接不能包含用户名或密码。' }
  }
  if (isNonPublicHostname(parsed.hostname)) {
    return { valid: false, error: '不允许本机、内网或保留地址。' }
  }
  if (
    !isAllowedExternalEvidenceHostname(
      parsed.hostname,
      policy.allowed_host_roots,
    )
  ) {
    return {
      valid: false,
      error: (
        `只允许当前策略中的主机族：`
        + `${policy.allowed_host_roots.join('、')} 及其点边界子域。`
      ),
    }
  }
  parsed.hostname = parsed.hostname.toLowerCase().replace(/\.$/, '')
  parsed.hash = ''
  return { valid: true, normalized: parsed.toString() }
}

export function validateExternalEvidenceUrls(
  values: string[],
  policy: CountryOutageExternalEvidencePolicy,
): ExternalEvidenceUrlListValidation {
  const fieldErrors: Record<number, string> = {}
  const urls: string[] = []
  const seen = new Set<string>()
  if (values.length > policy.maximum_urls) {
    return {
      urls,
      fieldErrors,
      globalError: `当前策略允许的公开 URL 最多 ${policy.maximum_urls} 个。`,
    }
  }
  values.forEach((value, index) => {
    if (!value.trim()) return
    const result = validateExternalEvidenceUrl(value, policy)
    if (!result.valid || !result.normalized) {
      fieldErrors[index] = result.error ?? 'URL 不可用。'
      return
    }
    if (seen.has(result.normalized)) {
      fieldErrors[index] = '请勿重复输入同一 URL。'
      return
    }
    seen.add(result.normalized)
    urls.push(result.normalized)
  })
  return {
    urls,
    fieldErrors,
    ...(urls.length < policy.minimum_urls
      ? {
          globalError: (
            `请至少提供 ${policy.minimum_urls} 个当前策略允许的公开 URL，`
            + '再明确确认读取。'
          ),
        }
      : {}),
  }
}

export function externalEvidenceUrlFocusTargetAfterRemoval(
  remainingCount: number,
  removedIndex: number,
): ExternalEvidenceUrlFocusTarget {
  const normalizedCount = Math.max(0, Math.trunc(remainingCount))
  if (normalizedCount === 0) return { kind: 'add' }
  return {
    kind: 'url',
    index: Math.min(
      Math.max(0, Math.trunc(removedIndex)),
      normalizedCount - 1,
    ),
  }
}

export function safeExternalEvidenceHref(
  value: string,
  policy: CountryOutageExternalEvidencePolicy,
): string | null {
  const result = validateExternalEvidenceUrl(value, policy)
  return result.valid ? result.normalized ?? null : null
}

export function observationAnchorForEvidence(evidenceRefs: string[]): string {
  const joined = evidenceRefs.join(' ').toLowerCase()
  if (joined.includes('asn')) return 'observation-asn'
  if (joined.includes('update') || joined.includes('announce') || joined.includes('withdraw')) {
    return 'observation-updates'
  }
  if (joined.includes('resource') || joined.includes('ipv4_24') || joined.includes('ipv6_48')) {
    return 'observation-resources'
  }
  return 'observation-visibility'
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isNonNegativeSafeInteger(value: unknown): value is number {
  return (
    isFiniteNumber(value)
    && Number.isSafeInteger(value)
    && value >= 0
  )
}

function isPositiveSafeInteger(value: unknown): value is number {
  return isNonNegativeSafeInteger(value) && value > 0
}

function nearlyEqual(left: number, right: number): boolean {
  return Math.abs(left - right) <= 1e-9
}

/**
 * 浏览器侧只做保守的页面预检。正式可发布性仍由 Sidecar 对同一冻结快照的
 * typed 读取与事实门槛决定；页面不会把缺失状态或残留数值补成 observed。
 */
export function countryOutagePagePreflightChecks(
  observation: EventObservation,
): CountryOutagePagePreflightCheck[] {
  const scope = observation.observation_scope
  const cohort = observation.cohort
  const audit = observation.audit
  const series = observation.series
  const windowStartMs = Date.parse(scope.window_start_utc ?? '')
  const windowEndMs = Date.parse(scope.window_end_utc ?? '')
  const intervalSeconds = scope.interval_seconds
  const intervalMs = isPositiveSafeInteger(intervalSeconds)
    ? intervalSeconds * 1_000
    : Number.NaN
  const spanMs = windowEndMs - windowStartMs
  const expectedGridCount = (
    Number.isFinite(windowStartMs)
    && Number.isFinite(windowEndMs)
    && windowEndMs >= windowStartMs
    && Number.isSafeInteger(intervalMs)
    && intervalMs > 0
    && spanMs % intervalMs === 0
  )
    ? spanMs / intervalMs + 1
    : Number.NaN

  const collectorIds = scope.collector_ids
  const collectorIsFixed = (
    scope.collector_id.toLowerCase() === 'rrc25'
    && scope.collector_count === 1
    && (
      collectorIds === undefined
      || (
        collectorIds.length === 1
        && collectorIds[0]?.toLowerCase() === 'rrc25'
      )
    )
  )
  const publicationIsBound = Boolean(
    observation.schema_version === 'country_outage_observation_v2'
    && observation.publication_id
    && isPositiveSafeInteger(observation.revision)
    && observation.publication_state === 'published'
    && observation.data_through
    && observation.incident_id
    && observation.incident_id === observation.event_identity.incident_id
    && observation.cohort_id
    && observation.cohort_id === cohort?.cohort_id
    && observation.window_start_utc === scope.window_start_utc
    && observation.window_end_utc === scope.window_end_utc
  )
  const cohortAndGridAreBound = Boolean(
    cohort
    && cohort.cohort_id
    && isPositiveSafeInteger(cohort.prefix_vp_count)
    && isPositiveSafeInteger(cohort.origin_asn_count)
    && cohort.denominator_policy
    && Number.isSafeInteger(expectedGridCount)
    && expectedGridCount >= 3
    && series.length === expectedGridCount
    && scope.observation_count === expectedGridCount
    && scope.expected_observation_count === expectedGridCount
    && scope.missing_observation_count === 0
    && observation.missing_slot_count === 0
    && scope.last_observation_at_utc === scope.window_end_utc
  )

  let previous: EventObservation['series'][number] | undefined
  const completeVisibilityGrid = (
    cohortAndGridAreBound
    && series.every((point, index) => {
      const observedAtMs = Date.parse(point.observed_at_utc)
      const expectedAtMs = windowStartMs + index * intervalMs
      if (
        point.slot_state !== 'observed'
        || point.missing_reason !== null
        || observedAtMs !== expectedAtMs
        || !isNonNegativeSafeInteger(point.visible_prefix_vp_count)
        || point.visible_prefix_vp_count > cohort!.prefix_vp_count
        || !isFiniteNumber(point.visible_prefix_vp_ratio)
        || point.visible_prefix_vp_ratio < 0
        || point.visible_prefix_vp_ratio > 1
        || !nearlyEqual(
          point.visible_prefix_vp_ratio,
          point.visible_prefix_vp_count / cohort!.prefix_vp_count,
        )
      ) return false

      if (!previous) {
        const firstSlotValid = (
          point.visible_prefix_vp_delta === null
          && point.visible_prefix_vp_ratio_delta_pp === null
        )
        previous = point
        return firstSlotValid
      }
      const deltasValid = (
        isFiniteNumber(point.visible_prefix_vp_delta)
        && point.visible_prefix_vp_delta
          === point.visible_prefix_vp_count
            - previous.visible_prefix_vp_count!
        && isFiniteNumber(point.visible_prefix_vp_ratio_delta_pp)
        && nearlyEqual(
          point.visible_prefix_vp_ratio_delta_pp,
          (
            point.visible_prefix_vp_ratio
            - previous.visible_prefix_vp_ratio!
          ) * 100,
        )
      )
      previous = point
      return deltasValid
    })
    && series[0]?.observed_at_utc === scope.window_start_utc
    && series.at(-1)?.observed_at_utc === scope.window_end_utc
  )
  const auditQualityIsBound = Boolean(
    audit
    && audit.schema_version === 'country_outage_audit_v2'
    && audit.quality_status === 'pass'
    && audit.consumed_deliverable_hashes_verified === true
    && audit.publication_id === observation.publication_id
    && audit.revision === observation.revision
    && audit.incident_id === observation.incident_id
    && audit.cohort_id === observation.cohort_id
    && audit.window_start_utc === scope.window_start_utc
    && audit.window_end_utc === scope.window_end_utc
    && audit.missing_slot_count === 0
    && scope.quality_status === 'pass'
  )

  return [
    {
      label: '合法 country_outage 事件',
      passed: (
        observation.event_identity.event_type === 'country_outage'
        && Boolean(observation.event_identity.country_code)
        && Boolean(observation.event_identity.country_name)
      ),
    },
    {
      label: '唯一观测源 RRC25',
      passed: collectorIsFixed,
    },
    {
      label: '已发布且身份闭合的固定快照',
      passed: publicationIsBound,
    },
    {
      label: '固定 cohort 与完整时间网格',
      passed: cohortAndGridAreBound,
    },
    {
      label: '首尾精确且核心可见性值完整',
      passed: completeVisibilityGrid,
    },
    {
      label: '页面与审计质量均通过',
      passed: auditQualityIsBound,
    },
  ]
}

export function suggestedReportQuestions(
  observation: EventObservation,
): string[] {
  const suggestions = [
    '窗口最低点发生在什么时候，较起点变化了多少？',
    '窗口结束时的回升能否称为完全恢复？',
  ]
  const capabilities = observation.capabilities ?? {}
  if (capabilities.asn_matrix?.state === 'available') {
    suggestions.push('哪些 ASN 的全不可见状态持续时间最长？')
  }
  if (capabilities.country_update_activity?.state === 'available') {
    suggestions.push('UPDATE 峰值与可见性下降在时间上如何对应？')
  }
  return suggestions
}

export function artifactByFormat(
  artifacts: CountryOutageArtifact[],
  format: 'markdown' | 'pdf',
): CountryOutageArtifact | undefined {
  return artifacts.find((item) => item.format === format)
}

export function countryOutageArtifactStateLabel(
  artifact: CountryOutageArtifact | undefined,
  options: { sessionExpired: boolean; hasReport: boolean },
): string {
  if (options.sessionExpired) return '会话已到期，需重新生成'
  if (!artifact) return options.hasReport ? '生成中' : '等待报告'
  return artifact.status === 'ready' ? '可下载' : '生成失败'
}

export function answerFitsPublishedLimit(text: string): boolean {
  return text.length > 0 && text.length <= MAXIMUM_COUNTRY_OUTAGE_ANSWER_CHARACTERS
}
