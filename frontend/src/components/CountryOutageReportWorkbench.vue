<script setup lang="ts">
import {
  computed,
  inject,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue'

import {
  COUNTRY_OUTAGE_AGENT_API_KEY,
  countryOutageAgentApi,
  type CountryOutageAgentApi,
  type CountryOutageAgentEvent,
  type CountryOutageEvidenceMode,
  type CountryOutageExternalAppendix,
  type CountryOutageExternalClaim,
  type CountryOutageExternalEvidenceCapability,
  type CountryOutageExternalEvidencePolicy,
  type CountryOutageExternalSource,
  CountryOutageAgentRequestError,
  type CountryOutageAgentReportPhase,
  type CountryOutageAgentSession,
  type CountryOutageAgentSubscription,
  type CountryOutageArtifact,
  type CountryOutageQuestionQuote,
  type CountryOutageQuestionRequest,
  type CountryOutageQuestionResult,
  type CountryOutageReportDocument,
  isCountryOutageRequestOutcomeUncertain,
} from '@/api/countryOutageAgent'
import type { EventObservation } from '@/types/api'
import {
  answerFitsPublishedLimit,
  artifactByFormat,
  closeAnsweringQuestionsAtSessionExpiry,
  countryOutagePagePreflightChecks,
  countryOutageArtifactStateLabel,
  externalEvidenceUrlFocusTargetAfterRemoval,
  formatRemainingTime,
  LogicalSubmissionIdempotency,
  logicalSubmissionFingerprint,
  MAXIMUM_COUNTRY_OUTAGE_ANSWER_CHARACTERS,
  observationAnchorForEvidence,
  reportStagePresentation,
  safeExternalEvidenceHref,
  sessionSecondsRemaining,
  shouldIgnoreLateAgentEvent,
  suggestedReportQuestions,
  validateExternalEvidenceUrls,
} from '@/utils/countryOutageReport'
import {
  appendixMatchesCurrentReport,
  CountryOutageAbortableRequestGate,
  freezeCountryOutageReportBinding,
  matchCountryOutageQuestionEvent,
  sameCountryOutageSnapshotIdentity,
  type CountryOutageFrozenReportBinding,
  validateCompletedCountryOutageReportEvent,
  validateCountryOutageQuestionAnswerSnapshot,
} from '@/utils/countryOutageRuntime'

defineOptions({ name: 'CountryOutageReportWorkbench' })

const props = defineProps<{
  observation: EventObservation
  eventReference: string
  api?: CountryOutageAgentApi
}>()

const emit = defineEmits<{
  openObservation: [anchor: string]
}>()

interface ReaderQuote extends CountryOutageQuestionQuote {
  label: string
  text: string
}

interface ReaderQuestion {
  questionId: string
  runId: string
  number: number
  prompt: string
  evidenceMode: CountryOutageEvidenceMode
  quote?: ReaderQuote
  state: 'answering' | 'completed' | 'failed' | 'cancelled'
  answer?: CountryOutageQuestionResult['answer']
  externalAppendix?: CountryOutageExternalAppendix
  externalPolicy?: CountryOutageExternalEvidencePolicy
  error?: string
  nextAction?: string
}

interface PreviousReportContext {
  reportId: string
  reportRunId: string
  reportBinding: CountryOutageFrozenReportBinding | null
  phase: CountryOutageAgentReportPhase | null
  session: CountryOutageAgentSession | null
  report: CountryOutageReportDocument
  artifacts: CountryOutageArtifact[]
  questions: ReaderQuestion[]
  frozenSuggestions: string[]
}

type ExternalEvidenceCapabilityView =
  | CountryOutageExternalEvidenceCapability
  | { state: 'checking' }
  | { state: 'unknown' }

const injectedApi = inject(COUNTRY_OUTAGE_AGENT_API_KEY, null)
const api = computed(() => props.api ?? injectedApi ?? countryOutageAgentApi)

const reportId = ref('')
const reportRunId = ref('')
const reportBinding = ref<CountryOutageFrozenReportBinding | null>(null)
const phase = ref<CountryOutageAgentReportPhase | null>(null)
const session = ref<CountryOutageAgentSession | null>(null)
const report = ref<CountryOutageReportDocument | null>(null)
const artifacts = ref<CountryOutageArtifact[]>([])
const runError = ref('')
const runErrorCode = ref('')
const nextAction = ref('')
const starting = ref(false)
const cancelRequested = ref(false)
const connectionState = ref<'idle' | 'connected' | 'retrying'>('idle')
const protocolNotice = ref('')
const questions = ref<ReaderQuestion[]>([])
const activeQuestionRunId = ref('')
const questionText = ref('')
const questionQuote = ref<ReaderQuote | null>(null)
const questionError = ref('')
const questionStarting = ref(false)
const readerAnnouncement = ref('')
const externalModeEnabled = ref(false)
const externalAuthorizationAt = ref('')
const externalUrls = ref<string[]>([''])
const externalCapability = ref<ExternalEvidenceCapabilityView>({
  state: 'checking',
})
const mobilePane = ref<'report' | 'questions'>('report')
const now = ref(Date.now())
const notesScroller = ref<HTMLElement | null>(null)
const questionInput = ref<HTMLTextAreaElement | null>(null)
const externalModeToggle = ref<HTMLInputElement | null>(null)
const externalAuthorizationPanel = ref<HTMLElement | null>(null)
const externalUrlAddButton = ref<HTMLButtonElement | null>(null)
const externalCapabilityStatus = ref<HTMLElement | null>(null)
const publishedHeader = ref<HTMLElement | null>(null)
const reportTitleBlock = ref<HTMLElement | null>(null)
const readerLayout = ref<HTMLElement | null>(null)
const notesAtLatest = ref(true)
const hasUnreadNotes = ref(false)
const frozenSuggestions = ref<string[]>([])
const generationSuggestions = ref<string[]>([])
const pendingUpgrade = ref(false)
const previousReportContext = ref<PreviousReportContext | null>(null)
const upgradeError = ref('')
const upgradeErrorCode = ref('')
const upgradeNextAction = ref('')
const mobileScrollPositions = ref({ report: -1, questions: -1 })
let subscription: CountryOutageAgentSubscription | null = null
let clockTimer: ReturnType<typeof setInterval> | undefined

const reportPhases: CountryOutageAgentReportPhase[] = [
  'queued',
  'reading_data',
  'generating_report',
  'validating',
  'completed',
  'failed',
  'cancelled',
]
const EXTERNAL_AUTHORIZATION_FRESHNESS_MS = 5 * 60 * 1_000

function isReportPhase(value: string): value is CountryOutageAgentReportPhase {
  return reportPhases.includes(value as CountryOutageAgentReportPhase)
}

function reportFailureNextAction(
  code: string | undefined,
  provided: string | undefined,
): string {
  if (provided?.trim()) return provided
  const normalized = (code ?? 'report_failed').toLowerCase()
  if (normalized === 'report_payload_invalid') {
    return '报告合同校验未通过；请保留当前快照并联系维护人员核对模型输出与合并校验，勿调整数据门槛。'
  }
  if (/insufficient|eligibility|data_gate/.test(normalized)) {
    return '当前快照未达到正式报告数据门槛；请查看缺失项，待数据完整后重新生成。'
  }
  if (/snapshot|revision|publication|conflict/.test(normalized)) {
    return '请刷新事件数据，确认 publication 与 revision 后重新生成。'
  }
  if (/permission|forbidden|unauthor/.test(normalized)) {
    return '请联系管理员核对当前事件的报告生成权限。'
  }
  if (/model|narrat/.test(normalized)) {
    return '请稍后基于同一快照重试；若持续失败，请联系维护人员核对模型运行状态。'
  }
  return '请保留当前快照并联系维护人员核对失败原因后再试。'
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '未知'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: props.observation.observation_scope.timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`
}

function idempotencyKey(prefix: string): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${random}`
}

const reportSubmissionIdempotency = new LogicalSubmissionIdempotency(
  () => idempotencyKey('country-outage-report'),
)
const questionSubmissionIdempotency = new LogicalSubmissionIdempotency(
  () => idempotencyKey('country-outage-question'),
)
const reportStartRequests = new CountryOutageAbortableRequestGate()
const questionStartRequests = new CountryOutageAbortableRequestGate()
const externalCapabilityRequests = new CountryOutageAbortableRequestGate()

const dataGateChecks = computed(
  () => countryOutagePagePreflightChecks(props.observation),
)
const dataGatePassed = computed(
  () => dataGateChecks.value.every((item) => item.passed),
)
const isRunning = computed(() => (
  phase.value !== null
  && ['queued', 'reading_data', 'generating_report', 'validating']
    .includes(phase.value)
))
const phaseView = computed(() => (
  phase.value ? reportStagePresentation(phase.value) : null
))
const runFailureLabel = computed(() => {
  const code = runErrorCode.value.toLowerCase()
  if (/abort|cancel/.test(code)) return '取消请求失败'
  if (/session.*expired|expired/.test(code)) return '会话已到期'
  if (/permission|forbidden|unauthor/.test(code)) return '权限不足'
  if (/snapshot|revision|publication|conflict/.test(code)) return '快照冲突'
  if (/insufficient|eligibility|data_gate/.test(code)) return '数据不足'
  if (/validation|semantic|ground/.test(code)) return '报告校验失败'
  if (/model|narrat/.test(code)) return '模型生成失败'
  return '报告生成失败'
})
const secondsRemaining = computed(
  () => sessionSecondsRemaining(session.value?.expires_at, now.value),
)
const sessionExpired = computed(
  () => Boolean(session.value) && secondsRemaining.value === 0,
)
const sessionExpiring = computed(
  () => secondsRemaining.value > 0 && secondsRemaining.value <= 300,
)
const sessionExpiryAnnouncement = computed(() => {
  if (sessionExpired.value) {
    return '本次短期会话已到期。当前页面不承诺恢复旧问答。'
  }
  if (sessionExpiring.value) {
    return '本次会话将在五分钟内到期。请尽快完成阅读、追问或下载。'
  }
  return ''
})
const snapshotRevision = computed(
  () => report.value?.snapshot.revision ?? null,
)
const modelCertificationBoundary = computed(() => {
  const model = report.value?.model
  if (
    model?.adapter !== 'pi-sdk'
    || model.runtimeIdentity !== 'formal'
    || model.modelRevisionKind !== 'mutable_alias'
    || model.immutableRevisionAvailable !== false
    || !model.limitation
    || !model.certificationValidUntil
    || !model.certifiedScenarioSetId
    || !model.certifiedInputScope
  ) {
    return null
  }
  return {
    limitation: model.limitation,
    validUntil: model.certificationValidUntil,
    scenarioSetId: model.certifiedScenarioSetId,
    inputScope: model.certifiedInputScope,
  }
})
const hasNewRevision = computed(
  () => {
    const frozen = report.value?.snapshot
    if (!frozen) return false
    return (
      frozen.incidentId !== props.observation.incident_id
      || frozen.publicationId !== props.observation.publication_id
      || frozen.revision !== props.observation.revision
      || frozen.dataThrough !== props.observation.data_through
      || frozen.isFinal !== props.observation.is_final
      || frozen.collectorId
        !== props.observation.observation_scope.collector_id
      || frozen.windowStartUtc !== props.observation.window_start_utc
      || frozen.windowEndUtc !== props.observation.window_end_utc
      || frozen.cohortId !== props.observation.cohort_id
    )
  },
)
const suggestions = computed(() => (
  report.value && frozenSuggestions.value.length
    ? frozenSuggestions.value
    : suggestedReportQuestions(props.observation)
))
const activeQuestion = computed(
  () => questions.value.find((item) => item.runId === activeQuestionRunId.value),
)
const externalEvidencePolicy = computed<CountryOutageExternalEvidencePolicy | null>(
  () => (
    externalCapability.value.state === 'ready'
      ? externalCapability.value.policy
      : null
  ),
)
const externalCapabilityReady = computed(
  () => externalCapability.value.state === 'ready',
)
const externalCapabilityLabel = computed(() => {
  switch (externalCapability.value.state) {
    case 'ready':
      return '公开来源旁证已就绪'
    case 'not_configured':
      return '当前环境未配置公开来源旁证'
    case 'self_check_failed':
      return '公开来源旁证自检未通过'
    case 'unknown':
      return '公开来源旁证状态暂不可确认'
    default:
      return '正在检查公开来源旁证能力'
  }
})
const externalCapabilityDetail = computed(() => {
  switch (externalCapability.value.state) {
    case 'ready':
      return '仅在你显式开启、填写当前策略允许的 URL 并确认后，才会读取公开来源。'
    case 'not_configured':
      return '当前环境未配置公开来源旁证；不影响仅使用 Domeye 数据的报告、追问和下载。'
    case 'self_check_failed':
      return '公开来源旁证自检未通过，当前不可用；不影响仅使用 Domeye 数据的报告、追问和下载。'
    case 'unknown':
      return '暂时无法确认公开来源旁证状态，当前不可用；不影响仅使用 Domeye 数据的报告、追问和下载。'
    default:
      return '检查期间外部入口不可用；不影响仅使用 Domeye 数据的报告、追问和下载。'
  }
})
const externalAllowedHostRootsLabel = computed(
  () => externalEvidencePolicy.value?.allowed_host_roots.join('、') ?? '',
)
const externalMinimumUrls = computed(
  () => externalEvidencePolicy.value?.minimum_urls
    ?? Number.POSITIVE_INFINITY,
)
const externalMaximumUrls = computed(
  () => externalEvidencePolicy.value?.maximum_urls ?? 0,
)
const externalUrlValidation = computed(
  () => (
    externalEvidencePolicy.value
      ? validateExternalEvidenceUrls(
          externalUrls.value,
          externalEvidencePolicy.value,
        )
      : {
          urls: [],
          fieldErrors: {},
          globalError: '公开来源旁证能力当前不可用。',
        }
  ),
)
const externalAuthorizationFresh = computed(() => {
  const authorizedAtMs = Date.parse(externalAuthorizationAt.value)
  return (
    Number.isFinite(authorizedAtMs)
    && authorizedAtMs <= now.value
    && now.value - authorizedAtMs <= EXTERNAL_AUTHORIZATION_FRESHNESS_MS
  )
})
const canAsk = computed(() => (
  Boolean(report.value)
  && !sessionExpired.value
  && !isRunning.value
  && !pendingUpgrade.value
  && !activeQuestionRunId.value
  && !questionStarting.value
  && (
    !externalModeEnabled.value
    || (
      externalCapabilityReady.value
      && externalAuthorizationFresh.value
      && externalUrlValidation.value.urls.length
        >= externalMinimumUrls.value
      && !externalUrlValidation.value.globalError
      && Object.keys(externalUrlValidation.value.fieldErrors).length === 0
    )
  )
))
const questionAriaDescribedBy = computed(() => [
  ...(questionQuote.value ? ['country-outage-question-quote'] : []),
  'country-outage-question-binding',
  'external-evidence-capability-status',
  ...(externalModeEnabled.value ? ['external-evidence-authority-copy'] : []),
  ...(questionError.value ? ['country-outage-question-error'] : []),
].join(' '))
const displayedReportId = computed(() => (
  pendingUpgrade.value && previousReportContext.value
    ? previousReportContext.value.reportId
    : reportId.value
))
const displayedSession = computed(() => (
  pendingUpgrade.value && previousReportContext.value
    ? previousReportContext.value.session
    : session.value
))
const displayedSecondsRemaining = computed(
  () => sessionSecondsRemaining(displayedSession.value?.expires_at, now.value),
)
const displayedSessionExpired = computed(
  () => Boolean(displayedSession.value) && displayedSecondsRemaining.value === 0,
)
const markdownArtifact = computed(
  () => artifactByFormat(artifacts.value, 'markdown'),
)
const pdfArtifact = computed(
  () => artifactByFormat(artifacts.value, 'pdf'),
)

function closeSubscription() {
  subscription?.close()
  subscription = null
  connectionState.value = 'idle'
}

function clearOperationFailure() {
  runError.value = ''
  runErrorCode.value = ''
  nextAction.value = ''
}

function cloneExternalEvidencePolicy(
  policy: CountryOutageExternalEvidencePolicy,
): CountryOutageExternalEvidencePolicy {
  return {
    ...policy,
    allowed_host_roots: [...policy.allowed_host_roots],
  }
}

async function refreshExternalEvidenceCapability(moveFocus = false) {
  const requestToken = externalCapabilityRequests.begin()
  externalCapability.value = { state: 'checking' }
  try {
    const capability = await api.value.getExternalEvidenceCapability(
      requestToken.controller.signal,
    )
    if (!externalCapabilityRequests.isCurrent(requestToken)) return
    externalCapability.value = capability
  } catch (cause) {
    if (!externalCapabilityRequests.isCurrent(requestToken)) return
    if (cause instanceof Error && cause.name === 'AbortError') return
    externalCapability.value = { state: 'unknown' }
  } finally {
    if (externalCapabilityRequests.finish(requestToken) && moveFocus) {
      void nextTick(() => {
        if (externalCapabilityReady.value) {
          externalModeToggle.value?.focus()
        } else {
          externalCapabilityStatus.value?.focus({ preventScroll: true })
        }
      })
    }
  }
}

function resetExternalEvidenceAuthorization() {
  externalModeEnabled.value = false
  externalAuthorizationAt.value = ''
  externalUrls.value = ['']
}

function setExternalEvidenceMode(enabled: boolean) {
  if (!enabled) {
    resetExternalEvidenceAuthorization()
    return
  }
  if (!externalCapabilityReady.value) {
    readerAnnouncement.value = externalCapabilityDetail.value
    return
  }
  externalModeEnabled.value = true
  externalAuthorizationAt.value = ''
}

function closeExternalEvidenceAuthorization() {
  resetExternalEvidenceAuthorization()
  void nextTick(() => {
    externalModeToggle.value?.focus()
  })
}

function onExternalEvidenceModeChange(event: Event) {
  setExternalEvidenceMode((event.target as HTMLInputElement).checked)
}

function confirmExternalEvidenceAuthorization() {
  if (!externalCapabilityReady.value || !externalEvidencePolicy.value) return
  const validation = externalUrlValidation.value
  if (
    validation.urls.length < externalEvidencePolicy.value.minimum_urls
    || validation.globalError
    || Object.keys(validation.fieldErrors).length > 0
  ) return
  externalAuthorizationAt.value = new Date().toISOString()
}

function addExternalEvidenceUrl() {
  const maximumUrls = externalEvidencePolicy.value?.maximum_urls
  if (
    maximumUrls === undefined
    || externalUrls.value.length >= maximumUrls
  ) return
  externalUrls.value.push('')
}

function removeExternalEvidenceUrl(index: number) {
  if (index < 0 || index >= externalUrls.value.length) return
  externalUrls.value.splice(index, 1)
  const focusTarget = externalEvidenceUrlFocusTargetAfterRemoval(
    externalUrls.value.length,
    index,
  )
  void nextTick(() => {
    if (focusTarget.kind === 'url') {
      const input = externalAuthorizationPanel.value
        ?.querySelector<HTMLInputElement>(
          `#external-evidence-url-${focusTarget.index}`,
        )
      if (input) {
        input.focus()
        return
      }
    }
    externalUrlAddButton.value?.focus()
  })
}

function evidenceModeLabel(mode: CountryOutageEvidenceMode): string {
  return mode === 'domeye_plus_external'
    ? 'Domeye 回答 + 指定 URL 直接旁证'
    : 'Domeye-only'
}

function externalAppendixStatusLabel(
  status: CountryOutageExternalAppendix['status'],
): string {
  return {
    collecting: '正在读取指定 URL',
    completed: '指定 URL 核验已完成',
    partial: '部分来源可用',
    failed: '指定 URL 核验未完成',
  }[status]
}

function externalClaimStatusLabel(
  status: CountryOutageExternalAppendix['claims'][number]['status'],
): string {
  return {
    supported: '有来源支持',
    mixed: '来源状态混合',
    conflict: '结构化来源冲突',
    insufficient: '外部证据不足',
  }[status]
}

function externalComparisonStatusLabel(
  status: CountryOutageExternalAppendix['comparison_status'],
): string {
  return {
    supported: '可比来源相符',
    mixed: '可用、证据不足或读取失败状态并存',
    conflict: '可比结构化事实冲突',
    insufficient: '没有足够的可比结构化事实',
  }[status ?? 'insufficient']
}

function externalSourceTierLabel(
  tier: CountryOutageExternalAppendix['sources'][number]['source_tier'],
): string {
  return {
    direct: '直接来源',
    secondary: '二级来源',
    lead: '低等级线索',
    unknown: '来源等级未知',
  }[tier]
}

function externalSourceClassificationLabel(
  classification:
    CountryOutageExternalAppendix['sources'][number]['source_classification'],
): string {
  return {
    measurement_platform: '测量平台',
    unknown: '来源类型未知',
  }[classification]
}

function externalReadStatusLabel(
  status: CountryOutageExternalAppendix['sources'][number]['read_status'],
): string {
  return {
    readable: '可读取',
    unreadable: '无法读取',
    blocked: '访问受阻',
    failed: '读取失败',
  }[status]
}

function externalEvidenceStatusLabel(
  status:
    CountryOutageExternalAppendix['sources'][number]['evidence_status'],
): string {
  return {
    available: '结构化事实可比较',
    insufficient: '结构化证据不足',
    read_failed: '来源读取失败',
  }[status ?? 'insufficient']
}

interface ExternalClaimSourceReference {
  sourceId: string
  source?: CountryOutageExternalSource
}

function externalClaimSourceReferences(
  appendix: CountryOutageExternalAppendix,
  claim: CountryOutageExternalClaim,
): ExternalClaimSourceReference[] {
  return claim.source_ids.map((sourceId) => ({
    sourceId,
    source: appendix.sources.find((source) => source.source_id === sourceId),
  }))
}

function safeExternalSourceHref(
  item: ReaderQuestion,
  value: string,
): string | null {
  return item.externalPolicy
    ? safeExternalEvidenceHref(value, item.externalPolicy)
    : null
}

function domIdToken(value: string): string {
  const normalized = value
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)
  return normalized || 'item'
}

function externalSourceAnchorId(
  questionId: string,
  sourceIndex: number,
): string {
  return `external-source-${domIdToken(questionId)}-${sourceIndex + 1}`
}

function externalSourceAnchorForReference(
  questionId: string,
  appendix: CountryOutageExternalAppendix,
  sourceId: string,
): string | null {
  const sourceIndex = appendix.sources.findIndex(
    (source) => source.source_id === sourceId,
  )
  return sourceIndex >= 0
    ? externalSourceAnchorId(questionId, sourceIndex)
    : null
}

function capturePreviousReport(): PreviousReportContext | null {
  if (!report.value) return null
  return {
    reportId: reportId.value,
    reportRunId: reportRunId.value,
    reportBinding: reportBinding.value,
    phase: phase.value,
    session: session.value,
    report: report.value,
    artifacts: [...artifacts.value],
    questions: [...questions.value],
    frozenSuggestions: [...frozenSuggestions.value],
  }
}

function restorePreviousReport(
  message: string,
  code: string,
  action: string,
) {
  const previous = previousReportContext.value
  if (!previous) return
  reportStartRequests.invalidate()
  questionStartRequests.invalidate()
  starting.value = false
  questionStarting.value = false
  closeSubscription()
  reportId.value = previous.reportId
  reportRunId.value = previous.reportRunId
  reportBinding.value = previous.reportBinding
  phase.value = previous.phase ?? 'completed'
  session.value = previous.session
  report.value = previous.report
  artifacts.value = previous.artifacts
  questions.value = previous.questions
  frozenSuggestions.value = previous.frozenSuggestions
  pendingUpgrade.value = false
  previousReportContext.value = null
  cancelRequested.value = false
  upgradeError.value = message
  upgradeErrorCode.value = code
  upgradeNextAction.value = action
  if (!sessionExpired.value) connectEvents()
}

function connectEvents() {
  closeSubscription()
  if (!reportId.value) return
  subscription = api.value.subscribe(reportId.value, {
    onEvent: handleAgentEvent,
    onConnectionChange(state) {
      if (!sessionExpired.value) connectionState.value = state
    },
    onProtocolError(message) {
      protocolNotice.value = message
    },
  })
}

function scrollNotesAfterUpdate(wasAtLatest: boolean) {
  void nextTick(() => {
    if (wasAtLatest) {
      returnToLatest()
    } else {
      hasUnreadNotes.value = true
    }
  })
}

function handleAgentEvent(event: CountryOutageAgentEvent) {
  if (event.report_id !== reportId.value) return
  const wasAtLatest = notesAtLatest.value

  if (event.event_type === 'session_notice') {
    session.value = event.session
    if (event.phase === 'session_expired') {
      closeSubscription()
    }
    return
  }

  if (event.event_type === 'report_state') {
    if (event.phase === 'completed') {
      const validation = validateCompletedCountryOutageReportEvent(event, {
        expectedReportId: reportId.value,
        expectedRunId: reportRunId.value,
        binding: reportBinding.value,
        retainedReport: (
          report.value && !pendingUpgrade.value ? report.value : null
        ),
        retainedArtifacts: (
          report.value && !pendingUpgrade.value ? artifacts.value : null
        ),
      })
      if (!validation.accepted || !event.report) {
        protocolNotice.value = validation.message
        if (pendingUpgrade.value && previousReportContext.value) {
          restorePreviousReport(
            validation.message,
            validation.code,
            '请核对当前发布身份后重新生成新版报告。',
          )
          protocolNotice.value = validation.message
          return
        }
        closeSubscription()
        runError.value = validation.message
        runErrorCode.value = validation.code
        nextAction.value = '请保留当前合法数据或旧报告，并重新发起生成。'
        if (!report.value) phase.value = 'failed'
        return
      }
    }
    if (
      event.run_id !== reportRunId.value
      && event.phase !== 'completed'
    ) {
      protocolNotice.value = (
        '收到与当前报告运行身份不一致的状态事件，已忽略该事件。'
      )
      return
    }
    if (
      shouldIgnoreLateAgentEvent(
        phase.value,
        event.phase,
        cancelRequested.value,
      )
    ) return
    if (event.phase === 'completed') {
      const completedReport = event.report
      if (!completedReport) return
      session.value = event.session
      phase.value = 'completed'
      const completedUpgrade = pendingUpgrade.value
      const replayingRetainedReport = Boolean(
        upgradeError.value
        && !completedUpgrade
        && report.value?.artifactId === completedReport.artifactId,
      )
      report.value = completedReport
      artifacts.value = event.artifacts ?? []
      frozenSuggestions.value = [...generationSuggestions.value]
      runError.value = ''
      runErrorCode.value = ''
      nextAction.value = ''
      if (!replayingRetainedReport) {
        upgradeError.value = ''
        upgradeErrorCode.value = ''
        upgradeNextAction.value = ''
      }
      pendingUpgrade.value = false
      previousReportContext.value = null
      if (completedUpgrade) {
        questionSubmissionIdempotency.clear()
        questions.value = []
        activeQuestionRunId.value = ''
        questionText.value = ''
        questionQuote.value = null
        questionError.value = ''
        readerAnnouncement.value = ''
        resetExternalEvidenceAuthorization()
      }
      mobilePane.value = 'report'
      mobileScrollPositions.value = { report: -1, questions: -1 }
      void nextTick(() => {
        const target = window.matchMedia('(max-width: 1100px)').matches
          ? publishedHeader.value
          : reportTitleBlock.value
        target?.scrollIntoView({
          behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
            ? 'auto'
            : 'smooth',
          block: 'start',
        })
        target?.focus({ preventScroll: true })
      })
    } else {
      session.value = event.session
      if (isReportPhase(event.phase)) phase.value = event.phase
    }
    if (event.phase === 'failed') {
      if (pendingUpgrade.value) {
        restorePreviousReport(
          event.error?.message ?? '新版报告生成失败，旧报告保持不变。',
          event.error?.code ?? 'report_failed',
          reportFailureNextAction(
            event.error?.code,
            event.error?.next_action,
          ),
        )
        return
      }
      runError.value = event.error?.message ?? '报告生成失败，未发布任何草稿。'
      runErrorCode.value = event.error?.code ?? 'report_failed'
      nextAction.value = reportFailureNextAction(
        event.error?.code,
        event.error?.next_action,
      )
    } else if (event.phase === 'cancelled') {
      if (pendingUpgrade.value) {
        restorePreviousReport(
          '新版报告生成已取消，旧报告保持不变。',
          'cancelled',
          '可在需要时重新生成新版报告。',
        )
        return
      }
      cancelRequested.value = true
    }
    return
  }

  if (event.event_type === 'question_state') {
    const result = event.question
    const eventMatch = matchCountryOutageQuestionEvent(
      questions.value,
      event.run_id,
      result?.question_id,
    )
    if (!eventMatch.accepted || !result) {
      protocolNotice.value = (
        `问题状态事件协议错误：${eventMatch.message || '缺少问题正文'}；`
        + '未改写现有研读记录。'
      )
      return
    }
    if (result.answer || event.phase === 'completed') {
      const snapshotValidation =
        validateCountryOutageQuestionAnswerSnapshot(
          event,
          report.value,
        )
      if (!snapshotValidation.accepted) {
        protocolNotice.value = snapshotValidation.message
        return
      }
    }
    const incomingAppendix = result.external_appendix
    const incomingAppendixAccepted = (
      !incomingAppendix
      || appendixMatchesCurrentReport(incomingAppendix, report.value)
    )
    if (!incomingAppendixAccepted) {
      protocolNotice.value = (
        '问题状态事件协议错误：外部旁证附录与当前冻结报告绑定不一致；'
        + '已保留原有附录。'
      )
    }
    let entry = eventMatch.action === 'update' && eventMatch.index !== null
      ? questions.value[eventMatch.index]
      : undefined
    if (!entry && eventMatch.action === 'create') {
      entry = {
        questionId: result.question_id,
        runId: event.run_id as string,
        number: result.number,
        prompt: result.question,
        evidenceMode: result.evidence_mode,
        quote: result.quote
          ? {
              ...result.quote,
              label: '报告引用',
              text: '来自当前冻结报告的引用位置',
            }
          : undefined,
        state: 'answering',
        ...(
          result.evidence_mode === 'domeye_plus_external'
          && externalEvidencePolicy.value
            ? {
                externalPolicy: cloneExternalEvidencePolicy(
                  externalEvidencePolicy.value,
                ),
              }
            : {}
        ),
        ...(
          incomingAppendixAccepted && incomingAppendix
            ? { externalAppendix: incomingAppendix }
            : {}
        ),
      }
      questions.value.push(entry)
    }
    if (!entry) return
    if (
      shouldIgnoreLateAgentEvent(
        entry.state,
        event.phase,
        cancelRequested.value && event.run_id === activeQuestionRunId.value,
      )
    ) return
    session.value = event.session
    const completedAnswer = result?.answer ?? entry.answer
    if (result) {
      entry.evidenceMode = result.evidence_mode
      if (incomingAppendixAccepted && incomingAppendix) {
        entry.externalAppendix = incomingAppendix
      }
      if (
        event.phase === 'collecting_external'
        && result.answer
        && answerFitsPublishedLimit(result.answer.text)
      ) {
        entry.answer = result.answer
        readerAnnouncement.value = (
          `研读记录 ${entry.number} 的 Domeye 回答已完成，`
          + '正在读取并核验指定 URL；结果只会进入独立旁证附录。'
        )
      }
    }
    if (
      event.phase === 'completed'
      && completedAnswer
      && answerFitsPublishedLimit(completedAnswer.text)
    ) {
      entry.state = 'completed'
      entry.answer = completedAnswer
      if (result) entry.prompt = result.question
      const appendixAnnouncement = entry.externalAppendix
        ? (
            `；${externalAppendixStatusLabel(entry.externalAppendix.status)}，`
            + `登记 ${entry.externalAppendix.sources.length} 个来源`
          )
        : ''
      readerAnnouncement.value = (
        `研读记录 ${entry.number} 回答已完成${appendixAnnouncement}。`
        + '内容已写入研读记录。'
      )
    } else if (event.phase === 'completed') {
      entry.state = 'failed'
      entry.error = result?.answer
        ? `回答超过 ${MAXIMUM_COUNTRY_OUTAGE_ANSWER_CHARACTERS} 字符上限，未发布到研读记录。`
        : '完成事件缺少经过校验的正式回答。'
      protocolNotice.value = entry.error
    } else if (event.phase === 'failed') {
      entry.state = 'failed'
      entry.error = (
        result?.error?.message
        ?? event.error?.message
        ?? '本次回答失败，已完成回答不受影响。'
      )
      entry.nextAction = result?.error?.next_action
      readerAnnouncement.value = `研读记录 ${entry.number} 回答失败。`
    } else if (event.phase === 'cancelled') {
      entry.state = 'cancelled'
      readerAnnouncement.value = `研读记录 ${entry.number} 已取消。`
    }
    if (
      event.run_id === activeQuestionRunId.value
      && ['completed', 'failed', 'cancelled'].includes(event.phase)
    ) {
      activeQuestionRunId.value = ''
      cancelRequested.value = false
    }
    scrollNotesAfterUpdate(wasAtLatest)
  }
}

function resetReportState() {
  reportStartRequests.invalidate()
  questionStartRequests.invalidate()
  starting.value = false
  questionStarting.value = false
  closeSubscription()
  questionSubmissionIdempotency.clear()
  reportId.value = ''
  reportRunId.value = ''
  reportBinding.value = null
  phase.value = null
  session.value = null
  report.value = null
  artifacts.value = []
  runError.value = ''
  runErrorCode.value = ''
  nextAction.value = ''
  cancelRequested.value = false
  protocolNotice.value = ''
  questions.value = []
  activeQuestionRunId.value = ''
  questionText.value = ''
  questionQuote.value = null
  questionError.value = ''
  readerAnnouncement.value = ''
  resetExternalEvidenceAuthorization()
  hasUnreadNotes.value = false
  frozenSuggestions.value = []
  generationSuggestions.value = []
  pendingUpgrade.value = false
  previousReportContext.value = null
  upgradeError.value = ''
  upgradeErrorCode.value = ''
  upgradeNextAction.value = ''
  mobileScrollPositions.value = { report: -1, questions: -1 }
}

async function generateReport() {
  if (!dataGatePassed.value || starting.value || isRunning.value) return
  if (activeQuestionRunId.value || questionStarting.value) return
  questionStartRequests.invalidate()
  let frozenBinding: CountryOutageFrozenReportBinding
  try {
    frozenBinding = freezeCountryOutageReportBinding(
      props.observation,
      props.eventReference,
    )
  } catch (cause) {
    protocolNotice.value = cause instanceof Error
      ? `当前观测身份无法冻结：${cause.message}`
      : '当前观测身份无法冻结'
    return
  }
  const upgrading = Boolean(report.value)
  clearOperationFailure()
  if (upgrading) {
    previousReportContext.value = capturePreviousReport()
    pendingUpgrade.value = true
    upgradeError.value = ''
    upgradeErrorCode.value = ''
    upgradeNextAction.value = ''
    closeSubscription()
    cancelRequested.value = false
  } else {
    resetReportState()
  }
  reportBinding.value = frozenBinding
  generationSuggestions.value = suggestedReportQuestions(props.observation)
  const submissionFingerprint = logicalSubmissionFingerprint({
    event_reference: props.eventReference,
    publication_id: props.observation.publication_id ?? '',
    revision: props.observation.revision ?? 1,
  })
  const submissionAttempt = reportSubmissionIdempotency.begin(
    submissionFingerprint,
  )
  const requestToken = reportStartRequests.begin()
  starting.value = true
  phase.value = 'queued'
  try {
    const response = await api.value.startReport({
      event_reference: props.eventReference,
      publication_id: props.observation.publication_id ?? '',
      revision: props.observation.revision ?? 1,
      idempotency_key: submissionAttempt.idempotencyKey,
    }, requestToken.controller.signal)
    if (!reportStartRequests.isCurrent(requestToken)) return
    reportSubmissionIdempotency.settle(submissionAttempt, 'accepted')
    reportId.value = response.report_id
    reportRunId.value = response.run_id
    phase.value = response.phase
    session.value = response.session
    connectEvents()
  } catch (cause) {
    if (!reportStartRequests.isCurrent(requestToken)) return
    const outcomeUncertain = isCountryOutageRequestOutcomeUncertain(cause)
    reportSubmissionIdempotency.settle(
      submissionAttempt,
      outcomeUncertain ? 'outcome_uncertain' : 'deterministic_rejection',
    )
    const code = cause instanceof Error ? cause.name : 'report_failed'
    const action = cause instanceof CountryOutageAgentRequestError
      ? cause.nextAction
      : undefined
    if (pendingUpgrade.value) {
      restorePreviousReport(
        cause instanceof Error
          ? cause.message
          : '新版报告服务暂不可用，旧报告保持不变。',
        code,
        action ?? (
          outcomeUncertain
            ? '保留旧报告；直接重试将复用同一逻辑提交，不会重复创建报告。'
            : '保留旧报告，稍后重新生成新版。'
        ),
      )
      return
    }
    phase.value = 'failed'
    runError.value = cause instanceof Error
      ? cause.message
      : '报告服务暂不可用，未发布任何草稿。'
    runErrorCode.value = code
    nextAction.value = action ?? (
      outcomeUncertain
        ? '直接重试；系统将复用同一逻辑提交，不会重复创建报告。'
        : '保留当前数据观测页，稍后重新生成。'
    )
  } finally {
    if (reportStartRequests.finish(requestToken)) {
      starting.value = false
    }
  }
}

async function cancelRun() {
  const runId = activeQuestionRunId.value || reportRunId.value
  if (!runId || cancelRequested.value) return
  clearOperationFailure()
  cancelRequested.value = true
  try {
    await api.value.abortRun(runId)
  } catch (cause) {
    cancelRequested.value = false
    runError.value = cause instanceof Error ? cause.message : '取消请求未送达'
    runErrorCode.value = cause instanceof CountryOutageAgentRequestError
      ? cause.code
      : 'abort_failed'
    nextAction.value = (
      cause instanceof CountryOutageAgentRequestError && cause.nextAction
        ? cause.nextAction
        : '当前运行可能仍在继续；请稍后重试取消或等待最终状态。'
    )
  }
}

function selectQuote(
  label: string,
  text: string,
  anchor: Omit<CountryOutageQuestionQuote, 'evidence_refs'>,
  evidenceRefs: string[],
) {
  questionQuote.value = {
    label,
    text,
    ...anchor,
    evidence_refs: evidenceRefs,
  }
  switchMobilePane('questions')
  void nextTick(() => questionInput.value?.focus())
}

function clearQuote() {
  questionQuote.value = null
  questionInput.value?.focus()
}

function useSuggestion(value: string) {
  questionText.value = value
  switchMobilePane('questions')
  void nextTick(() => questionInput.value?.focus())
}

async function askQuestion() {
  const prompt = questionText.value.trim()
  if (!prompt || !canAsk.value) return
  const requestedReport = report.value
  const requestedReportId = reportId.value
  if (!requestedReport || !requestedReportId) return
  if (prompt.length > 4_000) {
    questionError.value = '问题不能超过 4,000 个字符。'
    return
  }
  const requestedArtifactId = requestedReport.artifactId
  const requestedSnapshot = { ...requestedReport.snapshot }
  const requestToken = questionStartRequests.begin()
  questionStarting.value = true
  const followLatest = notesAtLatest.value
  clearOperationFailure()
  questionError.value = ''
  const quote = questionQuote.value
  const requestedExternalPolicy = (
    externalModeEnabled.value && externalEvidencePolicy.value
      ? cloneExternalEvidencePolicy(externalEvidencePolicy.value)
      : null
  )
  const useExternalEvidence = requestedExternalPolicy !== null
  const evidenceMode: CountryOutageEvidenceMode = useExternalEvidence
    ? 'domeye_plus_external'
    : 'domeye_only'
  const requestQuote = quote
    ? {
        kind: quote.kind,
        section_id: quote.section_id,
        paragraph_index: quote.paragraph_index,
        highlight_index: quote.highlight_index,
        evidence_refs: quote.evidence_refs,
      }
    : undefined
  const submissionFingerprint = logicalSubmissionFingerprint({
    report_id: reportId.value,
    publication_id: report.value?.snapshot.publicationId ?? '',
    revision: report.value?.snapshot.revision ?? 0,
    data_through: report.value?.snapshot.dataThrough ?? null,
    question: prompt,
    evidence_mode: evidenceMode,
    quote: requestQuote ?? null,
    external_authorization_at: useExternalEvidence
      ? externalAuthorizationAt.value
      : null,
    external_urls: useExternalEvidence
      ? externalUrlValidation.value.urls
      : [],
    external_policy_version: requestedExternalPolicy?.version ?? null,
    external_policy_sha256: requestedExternalPolicy?.sha256 ?? null,
  })
  const submissionAttempt = questionSubmissionIdempotency.begin(
    submissionFingerprint,
  )
  const request: CountryOutageQuestionRequest = useExternalEvidence
    ? {
        question: prompt,
        evidence_mode: 'domeye_plus_external',
        external_authorization: {
          authorized: true,
          authorized_at: externalAuthorizationAt.value,
        },
        external_urls: externalUrlValidation.value.urls,
        ...(requestQuote ? { quote: requestQuote } : {}),
        idempotency_key: submissionAttempt.idempotencyKey,
      }
    : {
        question: prompt,
        evidence_mode: 'domeye_only',
        ...(requestQuote ? { quote: requestQuote } : {}),
        idempotency_key: submissionAttempt.idempotencyKey,
      }
  try {
    const response = await api.value.askQuestion(
      requestedReportId,
      request,
      requestToken.controller.signal,
    )
    if (
      !questionStartRequests.isCurrent(requestToken)
      || reportId.value !== requestedReportId
      || report.value?.artifactId !== requestedArtifactId
      || !sameCountryOutageSnapshotIdentity(
        report.value?.snapshot,
        requestedSnapshot,
      )
    ) return
    questionSubmissionIdempotency.settle(submissionAttempt, 'accepted')
    let entry = questions.value.find(
      (item) => item.questionId === response.question_id,
    )
    if (!entry) {
      entry = {
        questionId: response.question_id,
        runId: response.run_id,
        number: response.number,
        prompt,
        evidenceMode,
        quote: quote ?? undefined,
        state: 'answering',
        ...(
          requestedExternalPolicy
            ? { externalPolicy: requestedExternalPolicy }
            : {}
        ),
      }
      questions.value.push(entry)
    } else if (entry.state !== 'completed') {
      entry.runId = response.run_id
      entry.state = 'answering'
      entry.evidenceMode = evidenceMode
    }
    if (requestedExternalPolicy) {
      entry.externalPolicy = requestedExternalPolicy
    }
    cancelRequested.value = false
    activeQuestionRunId.value = entry.state === 'completed'
      ? ''
      : response.run_id
    session.value = response.session
    questionText.value = ''
    questionQuote.value = null
    if (useExternalEvidence) resetExternalEvidenceAuthorization()
    if (followLatest) returnToLatest()
  } catch (cause) {
    if (!questionStartRequests.isCurrent(requestToken)) return
    const outcomeUncertain = isCountryOutageRequestOutcomeUncertain(cause)
    questionSubmissionIdempotency.settle(
      submissionAttempt,
      outcomeUncertain ? 'outcome_uncertain' : 'deterministic_rejection',
    )
    questionError.value = cause instanceof Error
      ? cause.message
      : '问题未提交，请稍后重试。'
    if (cause instanceof CountryOutageAgentRequestError && cause.nextAction) {
      questionError.value += ` ${cause.nextAction}`
    }
  } finally {
    if (questionStartRequests.finish(requestToken)) {
      questionStarting.value = false
    }
  }
}

function openEvidence(evidenceRefs: string[]) {
  emit('openObservation', observationAnchorForEvidence(evidenceRefs))
}

function onNotesScroll() {
  const element = notesScroller.value
  if (!element) return
  notesAtLatest.value = (
    element.scrollHeight - element.scrollTop - element.clientHeight < 48
  )
  if (notesAtLatest.value) hasUnreadNotes.value = false
}

function returnToLatest() {
  const element = notesScroller.value
  if (!element) return
  element.scrollTo({
    top: element.scrollHeight,
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
      ? 'auto'
      : 'smooth',
  })
  notesAtLatest.value = true
  hasUnreadNotes.value = false
}

function artifactStateLabel(artifact: CountryOutageArtifact | undefined): string {
  return countryOutageArtifactStateLabel(artifact, {
    sessionExpired: displayedSessionExpired.value,
    hasReport: Boolean(report.value),
  })
}

function artifactHref(format: 'markdown' | 'pdf'): string {
  return api.value.artifactUrl(displayedReportId.value, format)
}

function externalAppendixDownloadable(item: ReaderQuestion): boolean {
  const appendix = item.externalAppendix
  const currentReport = report.value
  return (
    item.state === 'completed'
    && item.evidenceMode === 'domeye_plus_external'
    && appendix?.schema_version === 'country_outage_external_appendix_v1'
    && appendix.classification_policy_version
      === 'country_outage_external_source_classification_policy_v1'
    && appendix.status === 'completed'
    && !appendix.error
    && Boolean(appendix.retrieved_at)
    && appendix.sources.length > 0
    && appendix.sources.every(
      (source) => (
        source.read_status === 'readable'
        && Boolean(source.summary?.trim())
      ),
    )
    && appendixMatchesCurrentReport(appendix, currentReport)
    && !displayedSessionExpired.value
  )
}

function externalAppendixArtifactHref(questionId: string): string {
  return api.value.externalAppendixArtifactUrl(
    displayedReportId.value,
    questionId,
  )
}

function switchMobilePane(target: 'report' | 'questions') {
  if (target === mobilePane.value) return
  const mobile = window.matchMedia('(max-width: 1100px)').matches
  if (mobile) {
    mobileScrollPositions.value[mobilePane.value] = window.scrollY
  }
  mobilePane.value = target
  if (mobile) {
    void nextTick(() => {
      window.scrollTo({
        top: mobileScrollPositions.value[target] >= 0
          ? mobileScrollPositions.value[target]
          : Math.max(0, (readerLayout.value?.offsetTop ?? 118) - 118),
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
          ? 'auto'
          : 'smooth',
      })
    })
  }
}

watch(
  () => props.eventReference,
  (current, previous) => {
    if (previous !== undefined && current !== previous) {
      reportSubmissionIdempotency.clear()
      questionSubmissionIdempotency.clear()
      resetReportState()
      externalCapabilityRequests.invalidate()
      externalCapability.value = { state: 'checking' }
      void refreshExternalEvidenceCapability()
    }
  },
)

watch(externalCapabilityReady, (ready, wasReady) => {
  if (ready || !wasReady || !externalModeEnabled.value) return
  const activeElement = document.activeElement
  const shouldRestoreFocus = (
    activeElement === externalModeToggle.value
    || Boolean(
      activeElement
      && externalAuthorizationPanel.value?.contains(activeElement),
    )
  )
  resetExternalEvidenceAuthorization()
  readerAnnouncement.value = (
    '公开来源旁证能力当前不可用，已清除尚未提交的外部授权；'
    + 'Domeye 报告、追问和下载不受影响。'
  )
  if (shouldRestoreFocus) {
    void nextTick(() => {
      externalCapabilityStatus.value?.focus({ preventScroll: true })
    })
  }
})

watch(
  externalUrls,
  () => {
    // 授权只绑定用户最后一次明确确认时的 URL 集合；任何编辑都要求重新确认。
    externalAuthorizationAt.value = ''
  },
  { deep: true },
)

watch(sessionExpired, (expired) => {
  if (expired) {
    questionStartRequests.invalidate()
    questionStarting.value = false
    if (pendingUpgrade.value && previousReportContext.value) {
      restorePreviousReport(
        '新版报告会话已到期，旧报告保持不变。',
        'session_expired',
        '请确认当前 revision 后重新生成新版报告。',
      )
      return
    }
    const wasAtLatest = notesAtLatest.value
    const closedQuestions = closeAnsweringQuestionsAtSessionExpiry(questions.value)
    if (closedQuestions > 0) {
      readerAnnouncement.value = (
        `${closedQuestions} 条正在回答的研读记录因会话到期而停止，未发布正式回答。`
      )
      scrollNotesAfterUpdate(wasAtLatest)
    }
    activeQuestionRunId.value = ''
    cancelRequested.value = false
    resetExternalEvidenceAuthorization()
    closeSubscription()
    if (!report.value) {
      phase.value = 'failed'
      runError.value = '短期会话已到期，本次报告未发布。'
      runErrorCode.value = 'session_expired'
      nextAction.value = '请基于当前合法快照重新生成报告。'
    }
  }
})

clockTimer = setInterval(() => {
  now.value = Date.now()
}, 1_000)

onMounted(() => {
  void refreshExternalEvidenceCapability()
})

onBeforeUnmount(() => {
  reportStartRequests.invalidate()
  questionStartRequests.invalidate()
  externalCapabilityRequests.invalidate()
  closeSubscription()
  reportSubmissionIdempotency.clear()
  questionSubmissionIdempotency.clear()
  if (clockTimer) clearInterval(clockTimer)
})
</script>

<template>
  <section class="report-workbench" aria-labelledby="report-workbench-title">
    <header class="report-masthead">
      <div class="report-masthead-copy">
        <p class="editorial-kicker">Domeye Observation Desk / 国家中断</p>
        <h1 id="report-workbench-title">报告与追问</h1>
        <p>
          将当前合法事件的固定 RRC25 发布快照整理为可核对的技术观测报告。
          报告不会使用 Codex 记忆，也不会默认访问互联网。
        </p>
      </div>
      <div class="trust-stamp" aria-label="报告信任边界">
        <strong>AI 生成</strong>
        <span>未经人工审核</span>
        <small>CONTROL-PLANE ONLY</small>
      </div>
    </header>

    <p
      class="session-expiry-announcement sr-only"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      {{ sessionExpiryAnnouncement }}
    </p>

    <section
      v-if="hasNewRevision || pendingUpgrade || upgradeError"
      class="revision-banner"
      role="status"
      aria-live="polite"
    >
      <div>
        <strong v-if="pendingUpgrade">
          {{ hasNewRevision ? '正在生成新版报告' : '正在重新生成当前快照报告' }}
        </strong>
        <strong v-else-if="upgradeError">新运行未替换旧报告</strong>
        <strong v-else>发现新数据版本</strong>
        <p v-if="pendingUpgrade">
          {{ phaseView?.label }}：{{ phaseView?.detail }}
          页面继续显示冻结的旧报告；只有新版完整校验通过后才会整体替换。
        </p>
        <p v-else-if="upgradeError">
          {{ upgradeErrorCode }} · {{ upgradeError }}
          {{ upgradeNextAction }}
        </p>
        <p v-else>
          当前报告固定在
          {{ report?.snapshot.publicationId }} / REV {{ snapshotRevision }}；
          数据观测已到 {{ observation.publication_id }} / REV
          {{ observation.revision }}。旧报告与旧问答保持冻结，不会自动迁移。
        </p>
      </div>
      <button
        v-if="pendingUpgrade"
        type="button"
        :disabled="cancelRequested || starting"
        @click="cancelRun"
      >
        {{
          starting
            ? '正在登记新版…'
            : (cancelRequested ? '正在取消…' : '取消新版生成')
        }}
      </button>
      <button
        v-else
        type="button"
        :disabled="Boolean(activeQuestionRunId) || questionStarting"
        @click="generateReport"
      >
        {{ hasNewRevision ? '生成新版报告' : '重新生成' }}
      </button>
    </section>

    <section v-if="!reportId && phase !== 'failed'" class="preflight-panel">
      <div class="preflight-intro">
        <span>USER-TRIGGERED / READ ONLY</span>
        <h2>生成前核对</h2>
        <p>
          这里只接受当前页面事件，不可更换国家、窗口或 collector。生成过程读取
          Domeye 已发布数据，不修改检测结果。
        </p>
      </div>

      <dl class="snapshot-ledger" aria-label="待生成报告的快照身份">
        <div>
          <dt>事件</dt>
          <dd>{{ observation.event_identity.display_name }}</dd>
        </div>
        <div>
          <dt>国家 / 观测源</dt>
          <dd>{{ observation.event_identity.country_name }} · RRC25</dd>
        </div>
        <div>
          <dt>观察窗口</dt>
          <dd>
            {{ observation.observation_scope.window_start_local }}
            <span>→</span>
            {{ observation.observation_scope.window_end_local }}
          </dd>
        </div>
        <div>
          <dt>PUBLICATION</dt>
          <dd class="mono-wrap">{{ observation.publication_id || '不可用' }}</dd>
        </div>
        <div>
          <dt>REVISION</dt>
          <dd>REV {{ observation.revision ?? 1 }}</dd>
        </div>
        <div>
          <dt>DATA THROUGH</dt>
          <dd class="mono-wrap">{{ observation.data_through || '不可用' }}</dd>
        </div>
      </dl>

      <div class="gate-register">
        <div>
          <span>页面预检（保守）</span>
          <strong>{{
            dataGatePassed
              ? '页面预检通过，可提交服务端正式门槛'
              : '页面预检未通过，不提交生成'
          }}</strong>
        </div>
        <ul>
          <li
            v-for="check in dataGateChecks"
            :key="check.label"
            :class="{ 'is-passed': check.passed }"
          >
            <span aria-hidden="true">{{ check.passed ? '✓' : '×' }}</span>
            <span class="sr-only">{{ check.passed ? '通过：' : '未通过：' }}</span>
            {{ check.label }}
          </li>
        </ul>
      </div>

      <button
        class="primary-action"
        type="button"
        :disabled="!dataGatePassed || starting"
        @click="generateReport"
      >
        <span>生成国家中断观测报告</span>
        <small>固定当前 publication 与 revision</small>
      </button>
    </section>

    <section
      v-else-if="!report"
      class="generation-panel"
      aria-labelledby="generation-state-title"
    >
      <div class="generation-status" role="status" aria-live="polite">
        <span>{{ phaseView?.index ?? '—' }} / 05</span>
        <h2 id="generation-state-title">{{ phaseView?.label ?? '准备生成' }}</h2>
        <p>{{ phaseView?.detail }}</p>
        <small v-if="connectionState === 'retrying'">
          状态连接暂时中断，浏览器正在从最后事件位置重连。
        </small>
      </div>

      <ol class="controlled-progress" aria-label="报告生成阶段">
        <li
          v-for="(item, index) in [
            ['queued', '排队'],
            ['reading_data', '读取数据'],
            ['generating_report', '生成报告'],
            ['validating', '校验'],
            ['completed', '完成'],
          ]"
          :key="item[0]"
          :class="{
            'is-current': phase === item[0],
            'is-done': Math.max(0, reportPhases.indexOf(phase ?? 'queued')) > index,
          }"
          :aria-current="phase === item[0] ? 'step' : undefined"
        >
          <b>{{ String(index + 1).padStart(2, '0') }}</b>
          <span>{{ item[1] }}</span>
        </li>
      </ol>

      <div v-if="runError" class="run-failure" role="alert">
        <span>{{ runFailureLabel }} · {{ runErrorCode }}</span>
        <strong>{{ runError }}</strong>
        <p>{{ nextAction }}</p>
        <button type="button" @click="generateReport">重新生成</button>
      </div>
      <div v-else-if="phase === 'cancelled'" class="run-failure is-cancelled" role="status">
        <strong>本次生成已取消</strong>
        <p>取消后到达的草稿或结果不会显示为正式报告。</p>
        <button type="button" @click="generateReport">重新生成</button>
      </div>
      <button
        v-else
        class="cancel-action"
        type="button"
        :disabled="cancelRequested || starting || !reportRunId"
        @click="cancelRun"
      >
        {{
          starting
            ? '正在登记请求…'
            : (cancelRequested ? '正在取消…' : '取消本次生成')
        }}
      </button>
      <p v-if="protocolNotice" class="protocol-notice" role="alert">
        {{ protocolNotice }}
      </p>
    </section>

    <template v-else>
      <section
        v-if="sessionExpiring || sessionExpired"
        :class="['session-banner', { 'is-expired': sessionExpired }]"
      >
        <strong>{{ sessionExpired ? '本次短期会话已到期' : '本次会话即将到期' }}</strong>
        <p v-if="sessionExpired">
          当前页面不承诺恢复旧问答；可依据当前合法快照重新生成一份新报告。
        </p>
        <p v-else>
          剩余 {{ formatRemainingTime(secondsRemaining) }}。已完成下载不会因页面倒计时被截断。
        </p>
        <button v-if="sessionExpired" type="button" @click="generateReport">
          基于当前快照重新生成
        </button>
      </section>

      <section
        v-if="connectionState === 'retrying' && !sessionExpired"
        class="connection-banner"
        role="status"
        aria-live="polite"
      >
        <strong>状态连接暂时中断</strong>
        <p>
          浏览器正在从最后事件位置重连；当前报告保持不变，尚未完成的回答不会被当作正式结果。
        </p>
      </section>

      <section
        v-if="runError"
        class="run-failure report-operation-failure"
        role="alert"
      >
        <span>{{ runFailureLabel }} · {{ runErrorCode }}</span>
        <strong>{{ runError }}</strong>
        <p>{{ nextAction }}</p>
        <button type="button" @click="clearOperationFailure">关闭提示</button>
      </section>

      <p v-if="protocolNotice" class="protocol-notice report-protocol-notice" role="alert">
        {{ protocolNotice }}
      </p>

      <header ref="publishedHeader" class="published-header" tabindex="-1">
        <div>
          <span>READ-ONLY REPORT / {{ report.snapshot.collectorId.toUpperCase() }}</span>
          <b class="published-trust-boundary">AI 生成 · 未经人工审核</b>
          <strong>{{ report.artifactId }}</strong>
        </div>
        <dl>
          <div><dt>窗口</dt><dd>{{ formatDateTime(report.snapshot.windowStartUtc) }} → {{ formatDateTime(report.snapshot.windowEndUtc) }}</dd></div>
          <div><dt>PUBLICATION</dt><dd>{{ report.snapshot.publicationId }}</dd></div>
          <div><dt>REVISION</dt><dd>REV {{ report.snapshot.revision }}</dd></div>
          <div><dt>DATA THROUGH</dt><dd>{{ report.snapshot.dataThrough || '未记录' }}</dd></div>
          <div><dt>生成时间</dt><dd>{{ formatDateTime(report.generatedAt) }}</dd></div>
          <div>
            <dt>会话</dt>
            <dd>
              {{
                displayedSessionExpired
                  ? '已到期'
                  : `${formatRemainingTime(displayedSecondsRemaining)} 后到期`
              }}
            </dd>
          </div>
        </dl>
        <div class="header-downloads" aria-label="报告下载">
          <a
            v-if="markdownArtifact?.status === 'ready' && !displayedSessionExpired"
            :href="artifactHref('markdown')"
            :download="markdownArtifact.filename"
          >
            Markdown
          </a>
          <span v-else>Markdown · {{ artifactStateLabel(markdownArtifact) }}</span>
          <a
            v-if="pdfArtifact?.status === 'ready' && !displayedSessionExpired"
            :href="artifactHref('pdf')"
            :download="pdfArtifact.filename"
          >
            PDF
          </a>
          <span v-else>PDF · {{ artifactStateLabel(pdfArtifact) }}</span>
        </div>
      </header>

      <nav class="mobile-reader-switch" aria-label="移动端报告与追问视图">
        <button
          type="button"
          :class="{ 'is-active': mobilePane === 'report' }"
          :aria-pressed="mobilePane === 'report'"
          @click="switchMobilePane('report')"
          @keydown.enter.space.prevent="switchMobilePane('report')"
        >
          报告
        </button>
        <button
          type="button"
          :class="{ 'is-active': mobilePane === 'questions' }"
          :aria-pressed="mobilePane === 'questions'"
          @click="switchMobilePane('questions')"
          @keydown.enter.space.prevent="switchMobilePane('questions')"
        >
          追问 <span v-if="questions.length">{{ questions.length }}</span>
        </button>
      </nav>

      <div ref="readerLayout" class="reader-layout">
        <article
          :class="['report-paper', { 'is-mobile-active': mobilePane === 'report' }]"
          aria-labelledby="published-report-title"
        >
          <header ref="reportTitleBlock" class="report-title-block" tabindex="-1">
            <span>AI 生成 · 未经人工审核 · BGP 控制面观测</span>
            <h2 id="published-report-title">{{ report.draft.title }}</h2>
            <p class="report-subtitle">{{ report.draft.subtitle }}</p>
            <p class="report-lead">{{ report.draft.summary.text }}</p>
            <button
              class="inline-question"
              type="button"
              @click="selectQuote(
                '开头摘要',
                report.draft.summary.text,
                { kind: 'summary' },
                report.draft.summary.evidenceRefs,
              )"
            >
              就此追问
            </button>
          </header>

          <section
            v-if="modelCertificationBoundary"
            class="model-certification-boundary"
            role="note"
            aria-labelledby="model-certification-boundary-title"
          >
            <div>
              <span>MODEL IDENTITY / MUTABLE ALIAS</span>
              <h3 id="model-certification-boundary-title">模型身份与认证边界</h3>
              <p>{{ modelCertificationBoundary.limitation }}</p>
            </div>
            <dl>
              <div>
                <dt>模型引用</dt>
                <dd>可变别名 · 不可变权重 revision 未提供</dd>
              </div>
              <div>
                <dt>认证有效至</dt>
                <dd>{{ formatDateTime(modelCertificationBoundary.validUntil) }}</dd>
              </div>
              <div>
                <dt>认证场景集</dt>
                <dd>{{ modelCertificationBoundary.scenarioSetId }}</dd>
              </div>
              <div>
                <dt>认证输入范围</dt>
                <dd>{{ modelCertificationBoundary.inputScope }}</dd>
              </div>
            </dl>
          </section>

          <section class="highlight-section" aria-labelledby="highlight-title">
            <div class="section-number">01</div>
            <div>
              <h3 id="highlight-title">最值得关注的数字</h3>
              <div class="highlight-table" role="table" aria-label="关键数字">
                <div
                  v-for="(highlight, highlightIndex) in report.draft.highlights"
                  :key="highlight.label"
                  class="highlight-row"
                  role="row"
                >
                  <span role="rowheader">{{ highlight.label }}</span>
                  <strong role="cell">{{ highlight.value }}</strong>
                  <button
                    type="button"
                    @click="selectQuote(
                      highlight.label,
                      `${highlight.label}：${highlight.value}`,
                      {
                        kind: 'highlight',
                        highlight_index: highlightIndex,
                      },
                      highlight.evidenceRefs,
                    )"
                  >
                    就此追问
                  </button>
                </div>
              </div>
            </div>
          </section>

          <section
            v-for="(section, sectionIndex) in report.draft.sections"
            :id="`report-section-${section.id}`"
            :key="section.id"
            class="report-section"
          >
            <div class="section-number">{{ String(sectionIndex + 2).padStart(2, '0') }}</div>
            <div>
              <h3>{{ section.title }}</h3>
              <div
                v-for="(paragraph, paragraphIndex) in section.paragraphs"
                :key="`${section.id}-${paragraphIndex}`"
                class="report-paragraph"
              >
                <p>{{ paragraph.text }}</p>
                <div class="paragraph-tools">
                  <button
                    type="button"
                    @click="selectQuote(
                      section.title,
                      paragraph.text,
                      {
                        kind: 'section_paragraph',
                        section_id: section.id,
                        paragraph_index: paragraphIndex,
                      },
                      paragraph.evidenceRefs,
                    )"
                  >
                    就此追问
                  </button>
                  <button
                    type="button"
                    @click="openEvidence(paragraph.evidenceRefs)"
                  >
                    返回数据观测核对
                  </button>
                </div>
              </div>
            </div>
          </section>

          <section class="unknown-section">
            <span>BOUNDARY / UNKNOWN</span>
            <h3>这份报告不能回答的问题</h3>
            <ul>
              <li v-for="item in report.draft.unknowns" :key="item">{{ item }}</li>
            </ul>
          </section>

          <footer class="artifact-ledger">
            <div>
              <span>制品身份</span>
              <strong>{{ report.artifactId }}</strong>
              <small>正文 SHA-256 {{ report.reportContentSha256 }}</small>
            </div>
            <div v-for="artifact in artifacts" :key="artifact.format">
              <span>{{ artifact.format.toUpperCase() }}</span>
              <template v-if="artifact.status === 'ready'">
                <strong>{{ formatBytes(artifact.byte_length) }}</strong>
                <small>SHA-256 {{ artifact.sha256 }}</small>
              </template>
              <template v-else>
                <strong>生成失败</strong>
                <small>{{ artifact.message }}</small>
              </template>
            </div>
          </footer>
        </article>

        <aside
          :class="['reader-notes', { 'is-mobile-active': mobilePane === 'questions' }]"
          aria-labelledby="reader-notes-title"
        >
          <p
            class="sr-only"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            {{ readerAnnouncement }}
          </p>
          <header class="notes-header">
            <div>
              <span>READER NOTES</span>
              <h2 id="reader-notes-title">研读记录</h2>
            </div>
            <div :class="['mode-lock', { 'is-external': externalModeEnabled }]">
              <span aria-hidden="true">●</span>
              {{
                externalModeEnabled
                  ? '本次问题：Domeye + 指定 URL 旁证'
                  : '默认：仅使用 Domeye 数据'
              }}
            </div>
          </header>

          <div
            ref="notesScroller"
            class="notes-scroll"
            tabindex="0"
            aria-label="报告追问记录"
            @scroll="onNotesScroll"
          >
            <section v-if="questions.length === 0" class="notes-empty">
              <span>NO QUESTIONS YET</span>
              <h3>从报告事实继续核对</h3>
              <p>
                可询问报告数字、时间、ASN、指标含义和证据边界。原因、用户影响和
                窗口外恢复若无证据，会明确回答“证据不足”。
              </p>
              <div class="suggestion-list">
                <button
                  v-for="suggestion in suggestions"
                  :key="suggestion"
                  type="button"
                  @click="useSuggestion(suggestion)"
                >
                  {{ suggestion }}
                </button>
              </div>
            </section>

            <article
              v-for="item in questions"
              :key="item.questionId"
              class="question-record"
            >
              <header>
                <span>研读记录 {{ String(item.number).padStart(2, '0') }}</span>
                <em>证据模式 · {{ evidenceModeLabel(item.evidenceMode) }}</em>
              </header>
              <div v-if="item.quote" class="record-quote">
                <strong>{{ item.quote.label }}</strong>
                <p>{{ item.quote.text }}</p>
              </div>
              <h3>{{ item.prompt }}</h3>
              <div
                v-if="item.state === 'answering' && !item.answer"
                class="answer-state"
                role="status"
              >
                <span></span>
                {{
                  item.evidenceMode === 'domeye_plus_external'
                    ? '正在组织 Domeye 回答并准备指定 URL 旁证附录'
                    : '正在读取原报告事实合同并组织回答'
                }}
              </div>
              <div v-if="item.answer" class="answer-copy">
                <p>{{ item.answer.text }}</p>
                <details class="answer-evidence">
                  <summary>展开本回答使用的证据</summary>
                  <dl>
                    <div>
                      <dt>证据模式</dt>
                      <dd>仅使用 Domeye 数据</dd>
                    </div>
                    <div v-if="item.answer.kind">
                      <dt>回答类型</dt>
                      <dd>
                        {{
                          {
                            fact: '事实回答',
                            metric_semantics: '指标释义',
                            evidence_boundary: '证据边界',
                            insufficient_evidence: '证据不足',
                          }[item.answer.kind]
                        }}
                      </dd>
                    </div>
                    <div>
                      <dt>事件</dt>
                      <dd>{{ report.snapshot.incidentId }}</dd>
                    </div>
                    <div>
                      <dt>快照</dt>
                      <dd>
                        {{
                          item.answer.snapshot?.publicationId
                          ?? report.snapshot.publicationId
                        }}
                        · REV
                        {{
                          item.answer.snapshot?.revision
                          ?? report.snapshot.revision
                        }}
                        · DATA THROUGH
                        {{
                          item.answer.snapshot?.dataThrough
                          ?? report.snapshot.dataThrough
                          ?? '未记录'
                        }}
                      </dd>
                    </div>
                    <div v-if="!item.answer.evidence_records?.length">
                      <dt>事实定位</dt>
                      <dd>
                        <code v-for="refValue in item.answer.evidence_refs" :key="refValue">
                          {{ refValue }}
                        </code>
                      </dd>
                    </div>
                  </dl>
                  <div
                    v-if="item.answer.evidence_records?.length"
                    class="answer-fact-register"
                    aria-label="本回答事实、数值、时间与统计范围"
                  >
                    <article
                      v-for="fact in item.answer.evidence_records"
                      :key="fact.evidence_ref"
                    >
                      <header>
                        <strong>{{ fact.label }}</strong>
                        <span>{{ fact.source }}</span>
                      </header>
                      <dl>
                        <div>
                          <dt>事实 / 数值</dt>
                          <dd>{{ fact.value ?? '非数值语义事实' }}</dd>
                        </div>
                        <div>
                          <dt>观测时间</dt>
                          <dd>
                            {{ fact.observed_at_local ?? fact.observed_at_utc ?? '不适用' }}
                          </dd>
                        </div>
                        <div>
                          <dt>统计范围</dt>
                          <dd>{{ fact.statistical_scope }}</dd>
                        </div>
                        <div>
                          <dt>事实定位</dt>
                          <dd><code>{{ fact.evidence_ref }}</code></dd>
                        </div>
                      </dl>
                    </article>
                  </div>
                  <ul v-if="item.answer.limitations.length">
                    <li v-for="limitation in item.answer.limitations" :key="limitation">
                      {{ limitation }}
                    </li>
                  </ul>
                  <div
                    v-if="item.answer.missing_evidence?.length"
                    class="missing-evidence"
                  >
                    <strong>仍缺少的证据</strong>
                    <ul>
                      <li
                        v-for="missing in item.answer.missing_evidence"
                        :key="missing"
                      >
                        {{ missing }}
                      </li>
                    </ul>
                  </div>
                  <button
                    type="button"
                    @click="openEvidence(item.answer.evidence_refs)"
                  >
                    返回数据观测核对
                  </button>
                </details>
              </div>
              <div
                v-if="item.state === 'failed' || item.state === 'cancelled'"
                class="answer-error"
                role="alert"
              >
                <strong>{{ item.state === 'cancelled' ? '本次回答已取消' : '本次回答失败' }}</strong>
                <p>{{ item.error || '已经完成的报告和回答未受影响。' }}</p>
                <small v-if="item.nextAction">{{ item.nextAction }}</small>
              </div>

              <section
                v-if="item.externalAppendix"
                class="external-evidence-appendix"
                aria-label="独立指定 URL 旁证附录"
              >
                <header>
                  <div>
                    <span>EXTERNAL EVIDENCE APPENDIX</span>
                    <h4>指定 URL 直接旁证</h4>
                  </div>
                  <div class="external-appendix-header-actions">
                    <strong
                      class="external-appendix-status"
                      :class="`is-${item.externalAppendix.status}`"
                    >
                      {{ externalAppendixStatusLabel(item.externalAppendix.status) }}
                    </strong>
                    <a
                      v-if="externalAppendixDownloadable(item)"
                      class="external-appendix-download"
                      :href="externalAppendixArtifactHref(item.questionId)"
                      download
                    >
                      下载核验附录 · MD
                    </a>
                  </div>
                </header>
                <p class="external-boundary-copy">
                  本区来自本问题的一次指定 URL 显式确认，仅作直接旁证；不修改 Domeye
                  报告正文，也不混入上方 Domeye-only 回答。
                </p>
                <dl class="external-request-ledger">
                  <div>
                    <dt>核验问题</dt>
                    <dd>{{ item.externalAppendix.query }}</dd>
                  </div>
                  <div>
                    <dt>请求时间</dt>
                    <dd>{{ formatDateTime(item.externalAppendix.requested_at) }}</dd>
                  </div>
                  <div>
                    <dt>读取时间</dt>
                    <dd>
                      {{
                        item.externalAppendix.retrieved_at
                          ? formatDateTime(item.externalAppendix.retrieved_at)
                          : '尚未完成'
                      }}
                    </dd>
                  </div>
                  <div>
                    <dt>结构化比较</dt>
                    <dd>
                      {{
                        externalComparisonStatusLabel(
                          item.externalAppendix.comparison_status,
                        )
                      }}
                    </dd>
                  </div>
                  <div>
                    <dt>来源分类策略</dt>
                    <dd>
                      <code>{{
                        item.externalAppendix.classification_policy_version
                      }}</code>
                    </dd>
                  </div>
                </dl>

                <div
                  v-if="item.externalAppendix.error"
                  class="external-appendix-error"
                  role="status"
                >
                  <strong>
                    {{ item.externalAppendix.error.code }} ·
                    {{ item.externalAppendix.error.message }}
                  </strong>
                  <p>
                    {{
                      item.externalAppendix.error.next_action
                      || 'Domeye 回答仍然有效，可稍后重新确认读取指定 URL。'
                    }}
                  </p>
                </div>

                <section
                  v-if="item.externalAppendix.claims.length"
                  class="external-claims"
                  :aria-labelledby="`external-claims-title-${item.questionId}`"
                >
                  <h5 :id="`external-claims-title-${item.questionId}`">
                    外部说法与证据状态
                  </h5>
                  <article
                    v-for="claim in item.externalAppendix.claims"
                    :key="claim.claim_id"
                  >
                    <header>
                      <strong
                        :class="`is-${claim.status}`"
                        :aria-label="`外部说法状态：${externalClaimStatusLabel(claim.status)}`"
                      >
                        {{ externalClaimStatusLabel(claim.status) }}
                      </strong>
                      <code>{{ claim.claim_id }}</code>
                    </header>
                    <p>{{ claim.text }}</p>
                    <section
                      class="external-claim-sources"
                      :aria-label="`说法 ${claim.claim_id} 的对应来源`"
                    >
                      <h6>对应来源</h6>
                      <p v-if="claim.source_ids.length === 0" class="external-claim-source-missing">
                        无可用来源；此说法不能据此提升可信度。
                      </p>
                      <article
                        v-for="sourceReference in externalClaimSourceReferences(
                          item.externalAppendix,
                          claim,
                        )"
                        :key="sourceReference.sourceId"
                        class="external-claim-source"
                      >
                        <template v-if="sourceReference.source">
                          <header>
                            <strong>
                              {{
                                sourceReference.source.title
                                || sourceReference.source.publisher
                                || '标题与发布方未提供'
                              }}
                            </strong>
                            <span
                              :aria-label="`来源类型：${
                                externalSourceClassificationLabel(
                                  sourceReference.source.source_classification,
                                )
                              }；来源等级：${
                                externalSourceTierLabel(sourceReference.source.source_tier)
                              }；读取状态：${
                                externalReadStatusLabel(sourceReference.source.read_status)
                              }；证据状态：${
                                externalEvidenceStatusLabel(
                                  sourceReference.source.evidence_status,
                                )
                              }`"
                            >
                              {{
                                externalSourceClassificationLabel(
                                  sourceReference.source.source_classification,
                                )
                              }}
                              ·
                              {{ externalSourceTierLabel(sourceReference.source.source_tier) }}
                              ·
                              {{ externalReadStatusLabel(sourceReference.source.read_status) }}
                              ·
                              {{
                                externalEvidenceStatusLabel(
                                  sourceReference.source.evidence_status,
                                )
                              }}
                            </span>
                          </header>
                          <p>
                            {{ sourceReference.source.publisher || '发布方未提供' }}
                            · 发布时间
                            {{
                              sourceReference.source.published_at
                                ? formatDateTime(sourceReference.source.published_at)
                                : '未提供'
                            }}
                            · 读取时间
                            {{
                              sourceReference.source.retrieved_at
                                ? formatDateTime(sourceReference.source.retrieved_at)
                                : '未完成'
                            }}
                          </p>
                          <p v-if="sourceReference.source.summary">
                            {{ sourceReference.source.summary }}
                          </p>
                          <div class="external-claim-source-actions">
                            <a
                              v-if="safeExternalSourceHref(item, sourceReference.source.url)"
                              :href="safeExternalSourceHref(item, sourceReference.source.url) ?? undefined"
                              target="_blank"
                              rel="noopener noreferrer nofollow"
                            >
                              打开公开来源
                            </a>
                            <strong v-else>
                              危险或非公开链接已拦截，不可执行
                            </strong>
                            <a
                              v-if="externalSourceAnchorForReference(
                                item.questionId,
                                item.externalAppendix,
                                sourceReference.sourceId,
                              )"
                              :href="`#${
                                externalSourceAnchorForReference(
                                  item.questionId,
                                  item.externalAppendix,
                                  sourceReference.sourceId,
                                )
                              }`"
                            >
                              查看完整来源登记
                            </a>
                          </div>
                        </template>
                        <template v-else>
                          <strong class="external-claim-source-missing">
                            来源元数据缺失
                          </strong>
                          <code>{{ sourceReference.sourceId }}</code>
                          <p>该来源 ID 未出现在本次外部来源登记中，不能核验或打开。</p>
                        </template>
                      </article>
                    </section>
                    <ul v-if="claim.limitations.length">
                      <li v-for="limitation in claim.limitations" :key="limitation">
                        {{ limitation }}
                      </li>
                    </ul>
                  </article>
                </section>

                <section
                  class="external-sources"
                  :aria-labelledby="`external-sources-title-${item.questionId}`"
                >
                  <h5 :id="`external-sources-title-${item.questionId}`">来源登记</h5>
                  <p v-if="item.externalAppendix.sources.length === 0" class="external-sources-empty">
                    尚无可登记来源；旁证附录不会据此补写结论。
                  </p>
                  <article
                    v-for="(source, sourceIndex) in item.externalAppendix.sources"
                    :id="externalSourceAnchorId(item.questionId, sourceIndex)"
                    :key="source.source_id"
                    tabindex="-1"
                  >
                    <header>
                      <div>
                        <span
                          :aria-label="`来源类型：${
                            externalSourceClassificationLabel(source.source_classification)
                          }；来源等级：${externalSourceTierLabel(source.source_tier)}`"
                        >
                          {{ externalSourceClassificationLabel(source.source_classification) }}
                          · {{ externalSourceTierLabel(source.source_tier) }}
                        </span>
                        <strong>{{ source.publisher || '发布方未提供' }}</strong>
                      </div>
                      <b
                          :class="`is-${source.read_status}`"
                          :aria-label="`读取状态：${externalReadStatusLabel(source.read_status)}；证据状态：${externalEvidenceStatusLabel(source.evidence_status)}`"
                        >
                          {{ externalReadStatusLabel(source.read_status) }}
                          · {{ externalEvidenceStatusLabel(source.evidence_status) }}
                        </b>
                    </header>
                    <a
                      v-if="safeExternalSourceHref(item, source.url)"
                      :href="safeExternalSourceHref(item, source.url) ?? undefined"
                      target="_blank"
                      rel="noopener noreferrer nofollow"
                    >
                      {{ source.title || source.url }}
                    </a>
                    <strong v-else class="unsafe-source-link">
                      危险或非公开链接已拦截，不可执行
                    </strong>
                    <code>{{ source.url }}</code>
                    <dl>
                      <div>
                        <dt>发布时间</dt>
                        <dd>
                          {{ source.published_at ? formatDateTime(source.published_at) : '未提供' }}
                        </dd>
                      </div>
                      <div>
                        <dt>读取时间</dt>
                        <dd>
                          {{ source.retrieved_at ? formatDateTime(source.retrieved_at) : '未完成' }}
                        </dd>
                      </div>
                      <div>
                        <dt>证据状态</dt>
                        <dd>
                          {{ externalEvidenceStatusLabel(source.evidence_status) }}
                        </dd>
                      </div>
                    </dl>
                    <p v-if="source.summary">{{ source.summary }}</p>
                    <small v-if="source.read_status_detail">
                      {{ source.read_status_detail }}
                    </small>
                    <small v-if="source.evidence_status_detail">
                      {{ source.evidence_status_detail }}
                    </small>
                  </article>
                </section>
              </section>
            </article>
          </div>

          <button
            v-if="hasUnreadNotes"
            class="return-latest"
            type="button"
            @click="returnToLatest"
          >
            有新回答 · 返回最新
          </button>

          <form class="question-composer" @submit.prevent="askQuestion">
            <div
              v-if="questionQuote"
              id="country-outage-question-quote"
              class="composer-quote"
              role="note"
            >
              <div>
                <span>当前引用 · {{ questionQuote.label }}</span>
                <p>{{ questionQuote.text }}</p>
              </div>
              <button type="button" aria-label="移除当前引用" @click="clearQuote">×</button>
            </div>
            <label for="country-outage-question">就当前报告追问</label>
            <textarea
              id="country-outage-question"
              ref="questionInput"
              v-model="questionText"
              rows="3"
              maxlength="4000"
              :aria-describedby="questionAriaDescribedBy"
              :aria-invalid="Boolean(questionError)"
              :disabled="!report || sessionExpired || isRunning || pendingUpgrade"
              placeholder="询问报告数字、时间、ASN、指标含义或证据边界"
            ></textarea>
            <div class="composer-toolbar">
              <div class="evidence-modes" aria-label="证据模式">
                <span class="active-mode">● Domeye 回答始终固定</span>
                <label
                  v-if="externalCapabilityReady"
                  class="external-mode-toggle"
                >
                  <input
                    ref="externalModeToggle"
                    type="checkbox"
                    :checked="externalModeEnabled"
                    :disabled="
                      sessionExpired
                      || isRunning
                      || pendingUpgrade
                      || questionStarting
                      || Boolean(activeQuestion)
                    "
                    @change="onExternalEvidenceModeChange"
                  />
                  <span>
                    {{
                      externalModeEnabled
                        ? (
                            externalAuthorizationFresh
                              ? '已确认读取本次指定 URL'
                              : (
                                  externalAuthorizationAt
                                    ? '确认已过期，请重新确认指定 URL'
                                    : '填写并确认本次指定 URL'
                                )
                          )
                        : '为本次问题添加指定 URL 旁证'
                    }}
                  </span>
                </label>
                <strong
                  v-else
                  :class="[
                    'external-capability-badge',
                    `is-${externalCapability.state}`,
                  ]"
                >
                  {{ externalCapabilityLabel }}
                </strong>
              </div>
              <span>{{ questionText.length }} / 4000</span>
            </div>

            <p
              id="external-evidence-capability-status"
              ref="externalCapabilityStatus"
              :class="[
                'external-capability-status',
                `is-${externalCapability.state}`,
              ]"
              role="status"
              aria-live="polite"
              aria-atomic="true"
              :aria-busy="externalCapability.state === 'checking'"
              tabindex="-1"
            >
              <span>
                <strong>{{ externalCapabilityLabel }}。</strong>
                {{ externalCapabilityDetail }}
              </span>
              <button
                v-if="
                  externalCapability.state === 'self_check_failed'
                  || externalCapability.state === 'unknown'
                "
                type="button"
                @click="refreshExternalEvidenceCapability(true)"
              >
                重新检查
              </button>
            </p>

            <section
              v-if="externalModeEnabled && externalCapabilityReady"
              ref="externalAuthorizationPanel"
              class="external-authorization-panel"
              aria-labelledby="external-authorization-title"
            >
              <header>
                <div>
                  <span>PUBLIC WEB / ONE-TIME AUTHORIZATION</span>
                  <h3 id="external-authorization-title">独立指定 URL 旁证附录</h3>
                </div>
                <strong>{{
                  externalAuthorizationFresh
                    ? '已显式确认'
                    : (
                        externalAuthorizationAt
                          ? '确认已过期'
                          : '等待显式确认'
                      )
                }}</strong>
              </header>
              <p id="external-evidence-authority-copy">
                只允许读取当前策略声明的
                {{ externalAllowedHostRootsLabel }} 及其点边界子域中本次明确填写并确认的
                URL；不会从问题文字发现或扩展页面。外部内容仅作独立直接旁证，不修改
                报告正文、不替代 Domeye 事实，也不会把网页指令当作系统指令执行。
              </p>
              <div class="external-url-register">
                <div class="external-url-register-heading">
                  <div>
                    <strong>明确公开 URL</strong>
                    <small id="external-evidence-url-requirement">
                      必填 {{ externalMinimumUrls }}–
                      {{ externalMaximumUrls }} 个 ·
                      仅当前策略主机族的公开 http/https
                    </small>
                  </div>
                  <button
                    ref="externalUrlAddButton"
                    type="button"
                    :disabled="
                      questionStarting
                      || externalUrls.length >= externalMaximumUrls
                    "
                    @click="addExternalEvidenceUrl"
                  >
                    + 添加 URL
                  </button>
                </div>
                <ol>
                  <li v-for="(_, index) in externalUrls" :key="index">
                    <label :for="`external-evidence-url-${index}`">
                      URL {{ index + 1 }}
                    </label>
                    <input
                      :id="`external-evidence-url-${index}`"
                      v-model="externalUrls[index]"
                      type="url"
                      :name="`external_evidence_url_${index + 1}`"
                      inputmode="url"
                      autocomplete="url"
                      placeholder="https://公开来源.example/report"
                      :disabled="questionStarting"
                      :aria-invalid="Boolean(externalUrlValidation.fieldErrors[index])"
                      :aria-describedby="[
                        'external-evidence-url-requirement',
                        ...(externalUrlValidation.fieldErrors[index]
                          ? [`external-evidence-url-error-${index}`]
                          : []),
                      ].join(' ')"
                    />
                    <button
                      type="button"
                      :disabled="questionStarting"
                      :aria-label="`移除公开 URL ${index + 1}`"
                      @click="removeExternalEvidenceUrl(index)"
                    >
                      移除
                    </button>
                    <small
                      v-if="externalUrlValidation.fieldErrors[index]"
                      :id="`external-evidence-url-error-${index}`"
                      class="external-url-error"
                    >
                      {{ externalUrlValidation.fieldErrors[index] }}
                    </small>
                  </li>
                </ol>
                <p
                  v-if="externalUrlValidation.globalError"
                  id="external-evidence-url-global-error"
                  class="external-url-error"
                  role="alert"
                >
                  {{ externalUrlValidation.globalError }}
                </p>
                <p v-else class="external-search-note">
                  只读取并核验上述明确 URL；不会根据问题文字发现或扩展其他网页。
                </p>
              </div>
              <footer>
                <span>
                  {{
                    externalAuthorizationFresh
                      ? `确认时间 ${formatDateTime(externalAuthorizationAt)} · 提交一次后自动清除`
                      : (
                          externalAuthorizationAt
                            ? '确认已超过五分钟，必须再次明确确认'
                            : 'URL 编辑后必须再次明确确认'
                        )
                  }}
                </span>
                <button
                  type="button"
                  :disabled="
                    questionStarting
                    || externalUrlValidation.urls.length < externalMinimumUrls
                    || Boolean(externalUrlValidation.globalError)
                    || Object.keys(externalUrlValidation.fieldErrors).length > 0
                  "
                  @click="confirmExternalEvidenceAuthorization"
                >
                  {{
                    externalAuthorizationFresh
                      ? '重新确认读取上述 URL'
                      : (
                          externalAuthorizationAt
                            ? '确认已过期，重新确认读取'
                            : '确认读取上述 URL'
                        )
                  }}
                </button>
                <button
                  type="button"
                  :disabled="questionStarting"
                  @click="closeExternalEvidenceAuthorization"
                >
                  关闭并清除授权
                </button>
              </footer>
            </section>

            <p
              v-if="questionError"
              id="country-outage-question-error"
              class="composer-error"
              role="alert"
            >
              {{ questionError }}
            </p>
            <div class="composer-actions">
              <small id="country-outage-question-binding">
                当前绑定：事件
                <strong>{{ report.event.incident_id }}</strong> ·
                {{ report.event.country_name }} / {{ report.event.event_type }} ·
                RRC25 · REV {{ report.snapshot.revision }} ·
                {{ report.snapshot.publicationId }} ·
                {{
                  externalModeEnabled
                    ? 'Domeye 回答 + 指定 URL 直接旁证'
                    : '仅使用 Domeye 数据'
                }}
              </small>
              <button
                v-if="activeQuestion"
                type="button"
                class="cancel-question"
                :disabled="cancelRequested"
                @click="cancelRun"
              >
                {{ cancelRequested ? '正在取消…' : '取消本次回答' }}
              </button>
              <button
                v-else
                type="submit"
                :disabled="!canAsk || !questionText.trim()"
              >
                {{
                  sessionExpired
                    ? '会话已到期'
                    : (
                        externalModeEnabled
                          ? '提交并核验指定 URL'
                          : '提交追问'
                      )
                }}
              </button>
            </div>
          </form>
        </aside>
      </div>
    </template>
  </section>
</template>

<style scoped>
.report-workbench {
  --report-ink: #17212a;
  --report-muted: #66717d;
  --report-line: #d8d3c8;
  --report-paper: #fffdf8;
  --report-canvas: #ebe8e0;
  --report-blue: #174f74;
  --report-rust: #a34a2a;
  min-width: 0;
  color: var(--report-ink);
  background:
    linear-gradient(rgba(40, 52, 60, 0.035) 1px, transparent 1px),
    var(--report-canvas);
  background-size: 100% 24px;
}

.report-masthead {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 32px;
  align-items: end;
  padding: 42px clamp(22px, 4vw, 64px) 32px;
  color: #f7f2e7;
  background: #17212a;
  border-bottom: 5px solid var(--report-rust);
}

.editorial-kicker,
.preflight-intro > span,
.published-header > div > span,
.notes-header span,
.notes-empty > span,
.unknown-section > span {
  display: block;
  margin: 0 0 10px;
  font: 700 10px/1.2 var(--mono);
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.report-masthead h1 {
  margin: 0;
  font: 700 clamp(34px, 4vw, 58px)/0.98 "Songti SC", "STSong", serif;
  letter-spacing: -0.045em;
}

.report-masthead-copy > p:last-child {
  max-width: 720px;
  margin: 18px 0 0;
  color: #bbc3c7;
  font-size: 14px;
  line-height: 1.8;
}

.trust-stamp {
  min-width: 154px;
  padding: 17px 18px;
  text-align: center;
  border: 1px solid rgba(255, 255, 255, 0.34);
  transform: rotate(-1.5deg);
}

.trust-stamp strong,
.trust-stamp span,
.trust-stamp small {
  display: block;
}

.trust-stamp strong {
  font-size: 18px;
}

.trust-stamp span {
  margin-top: 5px;
  color: #f4c5ad;
  font-size: 12px;
}

.trust-stamp small {
  margin-top: 12px;
  color: #87949b;
  font: 9px/1.2 var(--mono);
  letter-spacing: 0.08em;
}

.revision-banner,
.session-banner,
.connection-banner {
  display: flex;
  gap: 24px;
  align-items: center;
  justify-content: space-between;
  padding: 16px clamp(22px, 4vw, 64px);
  color: #3c2a11;
  background: #f4dfad;
  border-bottom: 1px solid #d4b976;
}

.revision-banner strong,
.session-banner strong,
.connection-banner strong {
  font-size: 14px;
}

.revision-banner p,
.session-banner p,
.connection-banner p {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.55;
}

.revision-banner button,
.session-banner button {
  flex: 0 0 auto;
  padding: 9px 14px;
  cursor: pointer;
  color: inherit;
  background: transparent;
  border: 1px solid currentColor;
}

.connection-banner {
  color: #173a51;
  background: #e3eef5;
  border-color: #acc8d8;
}

.session-banner.is-expired {
  color: #f6eee8;
  background: #6f3729;
  border-color: #6f3729;
}

.preflight-panel {
  width: min(1080px, calc(100% - 40px));
  margin: 46px auto 70px;
  padding: clamp(24px, 4vw, 48px);
  background: var(--report-paper);
  border: 1px solid var(--report-line);
  box-shadow: 10px 12px 0 rgba(41, 49, 54, 0.08);
}

.preflight-intro {
  max-width: 700px;
}

.preflight-intro > span {
  color: var(--report-rust);
}

.preflight-intro h2 {
  margin: 0;
  font: 700 32px/1.15 "Songti SC", "STSong", serif;
}

.preflight-intro p {
  color: var(--report-muted);
  line-height: 1.8;
}

.snapshot-ledger {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 30px 0;
  border-top: 2px solid var(--report-ink);
  border-left: 1px solid var(--report-line);
}

.snapshot-ledger > div {
  min-width: 0;
  padding: 15px 16px;
  border-right: 1px solid var(--report-line);
  border-bottom: 1px solid var(--report-line);
}

.snapshot-ledger dt,
.published-header dt,
.answer-evidence dt {
  margin-bottom: 6px;
  color: var(--report-muted);
  font: 700 9px/1.2 var(--mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.snapshot-ledger dd,
.published-header dd,
.answer-evidence dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.5;
}

.snapshot-ledger dd span {
  color: var(--report-rust);
}

.mono-wrap {
  overflow-wrap: anywhere;
  font-family: var(--mono);
}

.gate-register {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 26px;
  align-items: center;
  padding: 20px 0;
  border-block: 1px solid var(--report-line);
}

.gate-register > div span,
.gate-register > div strong {
  display: block;
}

.gate-register > div span {
  color: var(--report-muted);
  font-size: 11px;
}

.gate-register > div strong {
  margin-top: 7px;
  font-size: 15px;
}

.gate-register ul {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.gate-register li {
  padding: 7px 9px;
  color: #8b352c;
  background: #f9ece9;
  border: 1px solid #e9c4bd;
  font-size: 11px;
}

.gate-register li.is-passed {
  color: #27614e;
  background: #e8f2ed;
  border-color: #bcd7cb;
}

.primary-action {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  margin-top: 28px;
  padding: 17px 20px;
  cursor: pointer;
  color: #fff;
  background: var(--report-blue);
  border: 0;
  text-align: left;
}

.primary-action:disabled {
  cursor: not-allowed;
  opacity: 0.46;
}

.primary-action span {
  font-weight: 700;
}

.primary-action small {
  color: #bfd3df;
}

.generation-panel {
  width: min(920px, calc(100% - 40px));
  min-height: 470px;
  margin: 48px auto 72px;
  padding: clamp(26px, 5vw, 56px);
  background: var(--report-paper);
  border-top: 6px solid var(--report-blue);
}

.generation-status > span {
  color: var(--report-rust);
  font: 700 11px/1 var(--mono);
}

.generation-status h2 {
  margin: 12px 0 8px;
  font: 700 38px/1.1 "Songti SC", "STSong", serif;
}

.generation-status p {
  color: var(--report-muted);
}

.generation-status small {
  color: #9a5b1e;
}

.controlled-progress {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  margin: 44px 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--report-line);
}

.controlled-progress li {
  position: relative;
  display: grid;
  gap: 7px;
  padding: 17px 8px 0 0;
  color: #92999d;
}

.controlled-progress li::before {
  position: absolute;
  top: -5px;
  left: 0;
  width: 9px;
  height: 9px;
  content: "";
  background: var(--report-paper);
  border: 2px solid #b5b9ba;
  border-radius: 50%;
}

.controlled-progress li.is-current,
.controlled-progress li.is-done {
  color: var(--report-ink);
}

.controlled-progress li.is-current::before {
  background: var(--report-rust);
  border-color: var(--report-rust);
}

.controlled-progress li.is-done::before {
  background: var(--report-blue);
  border-color: var(--report-blue);
}

.controlled-progress b {
  font: 700 9px/1 var(--mono);
}

.controlled-progress span {
  font-size: 12px;
}

.cancel-action,
.run-failure button {
  padding: 10px 14px;
  cursor: pointer;
  color: #7a2f26;
  background: transparent;
  border: 1px solid currentColor;
}

.cancel-action:disabled {
  cursor: wait;
  opacity: 0.6;
}

.run-failure {
  padding: 18px;
  color: #742d27;
  background: #f8e8e5;
  border-left: 4px solid #b44b42;
}

.run-failure > span {
  display: block;
  margin-bottom: 7px;
  font: 700 9px/1.2 var(--mono);
  letter-spacing: .07em;
  text-transform: uppercase;
}

.run-failure > strong {
  display: block;
}

.run-failure.is-cancelled {
  color: #4f5558;
  background: #eeece8;
  border-color: #858b8d;
}

.run-failure p {
  margin: 7px 0 14px;
}

.report-operation-failure {
  padding-inline: clamp(22px, 4vw, 64px);
  border-bottom: 1px solid #dca9a2;
}

.report-protocol-notice {
  margin: 0;
  padding: 10px clamp(22px, 4vw, 64px);
  background: #fff1ef;
  border-bottom: 1px solid #e1b8b2;
}

.protocol-notice,
.composer-error {
  color: #8b352c;
  font-size: 12px;
}

.published-header {
  position: sticky;
  top: 121px;
  z-index: 12;
  display: grid;
  grid-template-columns: minmax(190px, 0.85fr) minmax(480px, 2fr) auto;
  gap: 20px;
  align-items: center;
  padding: 13px clamp(18px, 3vw, 42px);
  color: #f6f2e9;
  background: rgba(23, 33, 42, 0.98);
  border-bottom: 1px solid #49535b;
  backdrop-filter: blur(10px);
}

.published-header:focus {
  outline: 3px solid #ffffff;
  outline-offset: -4px;
}

.report-title-block:focus {
  outline: 3px solid #0b57b7;
  outline-offset: -3px;
}

.published-header > div > span {
  margin-bottom: 4px;
  color: #c2cbd0;
}

.published-header > div > strong {
  display: block;
  overflow: hidden;
  font: 700 11px/1.3 var(--mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.published-trust-boundary {
  display: block;
  margin: 4px 0;
  color: #f0c3aa;
  font: 700 9px/1.25 var(--mono);
  letter-spacing: .04em;
}

.published-header dl {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px 16px;
  margin: 0;
}

.published-header dt {
  margin-bottom: 2px;
  color: #8f9aa1;
}

.published-header dd {
  overflow: hidden;
  color: #e7e9e8;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-downloads {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.header-downloads a,
.header-downloads > span {
  padding: 7px 9px;
  color: #f6f2e9;
  border: 1px solid #73808a;
  font-size: 10px;
  text-decoration: none;
}

.header-downloads a:hover {
  background: #2b3c48;
}

.mobile-reader-switch {
  display: none;
}

.reader-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.62fr) minmax(340px, 0.78fr);
  gap: clamp(14px, 2vw, 28px);
  width: min(1500px, calc(100% - clamp(22px, 4vw, 64px)));
  margin: 28px auto 54px;
  align-items: start;
}

.report-paper {
  min-width: 0;
  padding: clamp(28px, 4vw, 66px);
  background:
    linear-gradient(90deg, transparent 0, transparent calc(100% - 1px), #eee8dd calc(100% - 1px)),
    var(--report-paper);
  border: 1px solid var(--report-line);
  box-shadow: 0 14px 34px rgba(29, 38, 44, 0.08);
}

.report-title-block {
  max-width: 820px;
  padding-bottom: 34px;
  border-bottom: 3px solid var(--report-ink);
  scroll-margin-top: 218px;
}

.model-certification-boundary {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(250px, 0.65fr);
  gap: 24px;
  margin: 28px 0 0;
  padding: 20px 22px;
  color: #49331e;
  background: #f7ead0;
  border: 1px solid #d8bc86;
  border-left: 5px solid var(--report-rust);
}

.model-certification-boundary span {
  display: block;
  color: #8d4a2f;
  font: 700 9px/1.2 var(--mono);
  letter-spacing: 0.1em;
}

.model-certification-boundary h3 {
  margin: 7px 0 8px;
  font: 700 18px/1.35 "Songti SC", "STSong", serif;
}

.model-certification-boundary p {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
}

.model-certification-boundary dl {
  display: grid;
  gap: 9px;
  margin: 0;
}

.model-certification-boundary dt {
  color: #856d52;
  font: 700 9px/1.2 var(--mono);
  letter-spacing: 0.06em;
}

.model-certification-boundary dd {
  margin: 3px 0 0;
  overflow-wrap: anywhere;
  font-size: 11px;
  line-height: 1.5;
}

.report-title-block > span {
  color: var(--report-rust);
  font: 700 10px/1 var(--mono);
  letter-spacing: 0.08em;
}

.report-title-block h2 {
  margin: 17px 0 12px;
  font: 800 clamp(32px, 4vw, 52px)/1.14 "Songti SC", "STSong", serif;
  letter-spacing: -0.035em;
}

.report-subtitle {
  margin: 0;
  color: var(--report-rust);
  font: 600 18px/1.55 "Songti SC", "STSong", serif;
}

.report-lead {
  margin: 25px 0 16px;
  font: 500 17px/1.95 "Songti SC", "STSong", serif;
}

.inline-question,
.paragraph-tools button,
.highlight-row button,
.answer-evidence button {
  padding: 0;
  cursor: pointer;
  color: var(--report-blue);
  background: transparent;
  border: 0;
  border-bottom: 1px solid currentColor;
  font-size: 11px;
}

.highlight-section,
.report-section {
  display: grid;
  grid-template-columns: 46px minmax(0, 1fr);
  gap: 17px;
  max-width: 860px;
  padding: 38px 0;
  border-bottom: 1px solid var(--report-line);
}

.section-number {
  padding-top: 7px;
  color: var(--report-rust);
  font: 700 11px/1 var(--mono);
}

.highlight-section h3,
.report-section h3,
.unknown-section h3 {
  margin: 0 0 21px;
  font: 700 24px/1.25 "Songti SC", "STSong", serif;
}

.highlight-table {
  border-top: 2px solid var(--report-ink);
}

.highlight-row {
  display: grid;
  grid-template-columns: minmax(160px, 1fr) minmax(160px, 1fr) auto;
  gap: 16px;
  align-items: baseline;
  padding: 12px 0;
  border-bottom: 1px solid var(--report-line);
}

.highlight-row span {
  color: var(--report-muted);
  font-size: 13px;
}

.highlight-row strong {
  font: 700 15px/1.4 var(--mono);
}

.report-paragraph + .report-paragraph {
  margin-top: 22px;
}

.report-paragraph p {
  margin: 0;
  font: 400 16px/2 "Songti SC", "STSong", serif;
  overflow-wrap: anywhere;
}

.paragraph-tools {
  display: flex;
  gap: 17px;
  margin-top: 8px;
  opacity: 0.72;
}

.report-paragraph:focus-within .paragraph-tools,
.report-paragraph:hover .paragraph-tools {
  opacity: 1;
}

.unknown-section {
  max-width: 860px;
  margin: 38px 0 0;
  padding: 28px 32px;
  color: #f5eee5;
  background: #26323a;
}

.unknown-section > span {
  color: #d9a98d;
}

.unknown-section ul {
  margin: 0;
  padding-left: 20px;
}

.unknown-section li {
  margin: 9px 0;
  color: #d9dedd;
  line-height: 1.7;
}

.artifact-ledger {
  display: grid;
  grid-template-columns: 1.4fr 1fr 1fr;
  gap: 1px;
  margin-top: 40px;
  background: var(--report-line);
  border: 1px solid var(--report-line);
}

.artifact-ledger > div {
  min-width: 0;
  padding: 14px;
  background: #f6f2e9;
}

.artifact-ledger span,
.artifact-ledger strong,
.artifact-ledger small {
  display: block;
}

.artifact-ledger span {
  color: var(--report-muted);
  font: 700 9px/1 var(--mono);
}

.artifact-ledger strong {
  overflow-wrap: anywhere;
  margin-top: 7px;
  font-size: 11px;
}

.artifact-ledger small {
  overflow-wrap: anywhere;
  margin-top: 5px;
  color: var(--report-muted);
  font-size: 9px;
}

.reader-notes {
  position: sticky;
  top: 92px;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: auto minmax(180px, 1fr) auto;
  min-width: 0;
  max-height: calc(100vh - 118px);
  background: #f7f5f0;
  border: 1px solid #cbc7be;
}

.notes-header {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
  padding: 18px;
  background: #e2ded5;
  border-bottom: 1px solid #cbc7be;
}

.notes-header span {
  margin-bottom: 5px;
  color: var(--report-muted);
}

.notes-header h2 {
  margin: 0;
  font: 700 22px/1.1 "Songti SC", "STSong", serif;
}

.mode-lock {
  padding: 6px 8px;
  color: #285c4c;
  background: #edf5f0;
  border: 1px solid #bcd7cb;
  font-size: 9px;
  font-weight: 700;
  white-space: nowrap;
}

.mode-lock span {
  display: inline;
  color: #2d7b60;
}

.mode-lock.is-external {
  color: #693817;
  background: #f8e5c8;
  border-color: #d4a878;
}

.mode-lock.is-external span {
  color: #ad4d1e;
}

.notes-scroll {
  min-height: 0;
  overflow-y: auto;
  scroll-behavior: smooth;
}

.notes-empty {
  padding: 28px 20px;
}

.notes-empty > span {
  color: var(--report-rust);
}

.notes-empty h3 {
  margin: 0 0 11px;
  font: 700 20px/1.25 "Songti SC", "STSong", serif;
}

.notes-empty p {
  color: var(--report-muted);
  font-size: 12px;
  line-height: 1.75;
}

.suggestion-list {
  display: grid;
  gap: 8px;
  margin-top: 18px;
}

.suggestion-list button {
  padding: 10px 12px;
  cursor: pointer;
  color: var(--report-ink);
  background: #fffdf8;
  border: 1px solid #d7d1c6;
  text-align: left;
  font-size: 11px;
  line-height: 1.5;
}

.suggestion-list button:hover {
  border-color: var(--report-blue);
}

.question-record {
  padding: 21px 20px;
  border-bottom: 1px solid #d9d5cd;
}

.question-record header {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: space-between;
  color: var(--report-muted);
  font: 700 9px/1.2 var(--mono);
  text-transform: uppercase;
}

.question-record header em {
  min-width: 0;
  overflow-wrap: anywhere;
  font-style: normal;
}

.question-record h3 {
  margin: 14px 0;
  font-size: 14px;
  line-height: 1.55;
}

.record-quote {
  margin: 14px 0;
  padding: 10px 12px;
  background: #ece9e2;
  border-left: 3px solid var(--report-rust);
}

.record-quote strong {
  font-size: 10px;
}

.record-quote p {
  display: -webkit-box;
  overflow: hidden;
  margin: 5px 0 0;
  color: var(--report-muted);
  font-size: 10px;
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.answer-state {
  display: flex;
  gap: 9px;
  align-items: center;
  color: var(--report-muted);
  font-size: 11px;
}

.answer-state span {
  width: 8px;
  height: 8px;
  background: var(--report-rust);
  border-radius: 50%;
  animation: status-pulse 1.4s ease-in-out infinite;
}

.answer-copy > p {
  margin: 0;
  font: 400 14px/1.9 "Songti SC", "STSong", serif;
  white-space: pre-wrap;
}

.external-evidence-appendix {
  min-width: 0;
  margin: 22px -20px -21px;
  padding: 22px 20px 24px;
  color: #25231e;
  background:
    repeating-linear-gradient(
      135deg,
      rgba(163, 74, 42, 0.08) 0,
      rgba(163, 74, 42, 0.08) 6px,
      transparent 6px,
      transparent 12px
    ),
    #f4efe4;
  border-top: 5px solid #a34a2a;
}

.external-evidence-appendix > header {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
  color: #413b32;
  text-transform: none;
}

.external-evidence-appendix > header span {
  display: block;
  color: #a34a2a;
  font: 700 8px/1.2 var(--mono);
  letter-spacing: 0.13em;
}

.external-evidence-appendix h4 {
  margin: 5px 0 0;
  font: 700 19px/1.2 "Songti SC", "STSong", serif;
}

.external-appendix-header-actions {
  display: flex;
  flex: 0 0 auto;
  gap: 7px;
  align-items: flex-end;
  flex-direction: column;
}

.external-appendix-status {
  flex: 0 0 auto;
  padding: 5px 7px;
  color: #4d3727;
  background: #ead5ae;
  border: 1px solid #c9a66b;
  font-size: 8px;
}

.external-appendix-status.is-completed {
  color: #245342;
  background: #e0eee7;
  border-color: #9fc5b3;
}

.external-appendix-status.is-partial,
.external-appendix-status.is-failed {
  color: #7a3027;
  background: #f5dfda;
  border-color: #d7a199;
}

.external-appendix-download {
  padding: 6px 8px;
  color: #fffdf8;
  background: #245342;
  border: 1px solid #173d30;
  font: 700 9px/1.2 var(--mono);
  letter-spacing: 0.03em;
  text-decoration: none;
}

.external-appendix-download:hover {
  background: #173d30;
}

.external-appendix-download:focus-visible {
  outline: 3px solid #174f74;
  outline-offset: 2px;
}

.external-boundary-copy {
  margin: 14px 0;
  padding: 10px 11px;
  color: #594838;
  background: rgba(255, 253, 248, 0.82);
  border-left: 3px solid #a34a2a;
  font-size: 10px;
  line-height: 1.65;
}

.external-request-ledger {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 0.8fr);
  margin: 0 0 15px;
  background: #d9d0c1;
  border: 1px solid #d9d0c1;
  gap: 1px;
}

.external-request-ledger > div {
  min-width: 0;
  padding: 8px;
  background: #fffdf8;
}

.external-request-ledger > div:first-child {
  grid-row: span 2;
}

.external-request-ledger dt,
.external-sources dt {
  color: #74695e;
  font: 700 9px/1.3 var(--mono);
  text-transform: uppercase;
}

.external-request-ledger dd,
.external-sources dd {
  min-width: 0;
  overflow-wrap: anywhere;
  margin: 4px 0 0;
  font-size: 9px;
  line-height: 1.45;
}

.external-appendix-error {
  margin-bottom: 14px;
  padding: 10px;
  color: #74372e;
  background: #f6dfdb;
  border: 1px solid #dda69e;
  font-size: 10px;
}

.external-appendix-error strong,
.external-appendix-error p {
  display: block;
  margin: 0;
  overflow-wrap: anywhere;
}

.external-appendix-error p {
  margin-top: 5px;
  line-height: 1.5;
}

.external-claims,
.external-sources {
  min-width: 0;
  margin-top: 18px;
}

.external-claims h5,
.external-sources h5 {
  margin: 0 0 9px;
  padding-bottom: 7px;
  color: #5b4c3d;
  border-bottom: 2px solid #5b4c3d;
  font: 700 10px/1.2 var(--mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.external-claims > article,
.external-sources > article {
  min-width: 0;
  padding: 11px;
  background: rgba(255, 253, 248, 0.94);
  border: 1px solid #d8cdbd;
}

.external-claims > article + article,
.external-sources > article + article {
  margin-top: 8px;
}

.external-sources > article {
  scroll-margin-top: 170px;
}

.external-sources > article:focus {
  outline: 3px solid #174f74;
  outline-offset: 2px;
}

.external-claims > article > header,
.external-sources > article > header {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  justify-content: space-between;
  color: #6b5f52;
  font: 700 8px/1.25 var(--mono);
  text-transform: none;
}

.external-claims > article > header code {
  min-width: 0;
  overflow-wrap: anywhere;
  text-align: right;
}

.external-claims > article > header strong {
  flex: 0 0 auto;
  padding: 3px 5px;
  color: #285c4c;
  background: #e3efe8;
  border: 1px solid #a9c9ba;
}

.external-claims > article > header strong.is-mixed,
.external-claims > article > header strong.is-conflict,
.external-claims > article > header strong.is-insufficient {
  color: #76352c;
  background: #f4ded9;
  border-color: #d9a69e;
}

.external-claims > article > p {
  margin: 9px 0 7px;
  overflow-wrap: anywhere;
  font: 600 12px/1.65 "Songti SC", "STSong", serif;
}

.external-claim-sources {
  min-width: 0;
  margin-top: 10px;
  padding-top: 9px;
  border-top: 1px dashed #c9bda9;
}

.external-claim-sources h6 {
  margin: 0 0 7px;
  color: #74695e;
  font: 700 8px/1.2 var(--mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.external-claim-source {
  min-width: 0;
  padding: 8px;
  background: #f7f1e7;
  border-left: 3px solid #b97755;
}

.external-claim-source + .external-claim-source {
  margin-top: 6px;
}

.external-claim-source > header {
  display: flex;
  min-width: 0;
  gap: 8px;
  align-items: flex-start;
  justify-content: space-between;
}

.external-claim-source > header strong,
.external-claim-source > header span,
.external-claim-source > code,
.external-claim-source > p {
  min-width: 0;
  overflow-wrap: anywhere;
}

.external-claim-source > header strong {
  color: #40382f;
  font-size: 9px;
  line-height: 1.45;
}

.external-claim-source > header span {
  flex: 0 0 auto;
  color: #8b3d27;
  font: 700 8px/1.4 var(--mono);
  text-align: right;
}

.external-claim-source > p {
  margin: 5px 0 0;
  color: #6a5f54;
  font: 400 8px/1.55 var(--mono);
}

.external-claim-source > code {
  display: block;
  margin-top: 4px;
  color: #776f66;
  font-size: 8px;
}

.external-claim-source-actions {
  display: flex;
  min-width: 0;
  gap: 7px 12px;
  flex-wrap: wrap;
  margin-top: 7px;
}

.external-claim-source-actions a,
.external-claim-source-actions strong {
  overflow-wrap: anywhere;
  color: #174f74;
  font-size: 8px;
  line-height: 1.45;
}

.external-claim-source-actions strong {
  color: #8b352c;
}

.external-claim-source-missing {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  color: #8b352c;
  font-size: 8px;
  line-height: 1.55;
}

.external-claims ul {
  margin: 8px 0 0;
  padding-left: 16px;
  color: #755143;
  font-size: 9px;
  line-height: 1.55;
}

.external-sources > article > header > div {
  min-width: 0;
}

.external-sources > article > header span,
.external-sources > article > header strong {
  display: block;
  overflow-wrap: anywhere;
}

.external-sources > article > header span {
  color: #a34a2a;
}

.external-sources > article > header strong {
  margin-top: 3px;
  color: #3e3933;
  font-size: 10px;
}

.external-sources > article > header b {
  flex: 0 0 auto;
  padding: 4px 5px;
  color: #285c4c;
  background: #e3efe8;
  border: 1px solid #a9c9ba;
  font-size: 8px;
}

.external-sources > article > header b.is-unreadable,
.external-sources > article > header b.is-blocked,
.external-sources > article > header b.is-failed {
  color: #76352c;
  background: #f4ded9;
  border-color: #d9a69e;
}

.external-sources a,
.unsafe-source-link {
  display: block;
  margin-top: 10px;
  overflow-wrap: anywhere;
  color: #174f74;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.45;
}

.unsafe-source-link {
  color: #8b352c;
}

.external-sources > article > code {
  display: block;
  margin-top: 5px;
  overflow-wrap: anywhere;
  color: #776f66;
  font-size: 8px;
  line-height: 1.4;
}

.external-sources > article > dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 10px 0 0;
}

.external-sources > article > p,
.external-sources > article > small {
  display: block;
  margin: 9px 0 0;
  overflow-wrap: anywhere;
  color: #5f574f;
  font-size: 9px;
  line-height: 1.55;
}

.external-sources-empty {
  margin: 0;
  padding: 11px;
  color: #6d6256;
  background: rgba(255, 253, 248, 0.8);
  border: 1px dashed #c9bda9;
  font-size: 9px;
  line-height: 1.5;
}

.answer-evidence {
  margin-top: 16px;
  border-top: 1px solid #d6d1c8;
}

.answer-evidence summary {
  padding: 11px 0;
  cursor: pointer;
  color: var(--report-blue);
  font-size: 10px;
}

.answer-evidence dl {
  margin: 0 0 13px;
}

.answer-evidence dl > div {
  padding: 8px 0;
  border-top: 1px dotted #d6d1c8;
}

.answer-evidence code {
  display: block;
  overflow-wrap: anywhere;
  font-size: 9px;
}

.answer-fact-register {
  display: grid;
  gap: 8px;
  margin: 4px 0 14px;
}

.answer-fact-register > article {
  padding: 10px;
  background: #f1eee7;
  border: 1px solid #d8d2c8;
}

.answer-fact-register header {
  display: flex;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
}

.answer-fact-register header strong {
  font-size: 11px;
}

.answer-fact-register header span {
  color: var(--report-muted);
  font: 700 8px/1 var(--mono);
  text-transform: uppercase;
}

.answer-fact-register dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 10px;
  margin: 7px 0 0;
}

.answer-fact-register dl > div {
  min-width: 0;
}

.answer-fact-register dd {
  overflow-wrap: anywhere;
}

.answer-evidence ul {
  padding-left: 18px;
  color: var(--report-muted);
  font-size: 10px;
  line-height: 1.6;
}

.missing-evidence {
  margin: 12px 0;
  padding: 10px;
  color: #714138;
  background: #f7e9e4;
  border-left: 3px solid var(--report-rust);
  font-size: 10px;
}

.missing-evidence ul {
  margin: 6px 0 0;
}

.answer-error {
  padding: 12px;
  color: #813b32;
  background: #f7e7e4;
  font-size: 11px;
}

.answer-error p {
  margin: 6px 0 0;
}

.answer-error small {
  display: block;
  margin-top: 7px;
  color: #6d4841;
  line-height: 1.5;
}

.return-latest {
  position: absolute;
  right: 17px;
  bottom: 196px;
  z-index: 2;
  padding: 8px 11px;
  cursor: pointer;
  color: #fff;
  background: var(--report-blue);
  border: 0;
  box-shadow: 0 4px 12px rgba(23, 79, 116, 0.24);
  font-size: 10px;
}

.question-composer {
  position: relative;
  padding: 15px;
  background: #fffdf8;
  border-top: 1px solid #cbc7be;
}

.question-composer > label {
  display: block;
  margin-bottom: 7px;
  font-size: 11px;
  font-weight: 700;
}

.question-composer textarea {
  width: 100%;
  min-height: 76px;
  resize: vertical;
  padding: 10px;
  color: var(--report-ink);
  background: #fff;
  border: 1px solid #bfc1bd;
  border-radius: 0;
  line-height: 1.55;
}

.question-composer textarea:disabled {
  background: #eceae5;
}

.composer-quote {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 9px;
  margin: -1px -1px 12px;
  padding: 10px;
  background: #e8e4dc;
  border-left: 3px solid var(--report-rust);
}

.composer-quote span {
  color: var(--report-rust);
  font-size: 9px;
  font-weight: 700;
}

.composer-quote p {
  overflow: hidden;
  margin: 5px 0 0;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10px;
}

.composer-quote button {
  width: 26px;
  height: 26px;
  cursor: pointer;
  background: transparent;
  border: 0;
  font-size: 20px;
}

.composer-toolbar,
.composer-actions {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  margin-top: 8px;
}

.composer-toolbar {
  flex-wrap: wrap;
}

.composer-toolbar > span,
.composer-actions small {
  color: var(--report-muted);
  font-size: 9px;
}

.composer-actions small {
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
  white-space: normal;
}

.composer-actions small strong {
  color: inherit;
  font-family: var(--mono);
}

.evidence-modes {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
  flex-wrap: wrap;
}

.active-mode {
  padding: 5px 7px;
  font-size: 9px;
}

.active-mode {
  color: #285c4c;
  background: #edf5f0;
  border: 1px solid #bcd7cb;
  font-weight: 700;
}

.external-capability-badge {
  min-width: 0;
  padding: 5px 7px;
  overflow-wrap: anywhere;
  color: #4e565b;
  background: #eceeec;
  border: 1px solid #c4c8c5;
  font-size: 9px;
  line-height: 1.4;
}

.external-capability-badge.is-self_check_failed,
.external-capability-badge.is-unknown {
  color: #754229;
  background: #f7e9dc;
  border-color: #d8b293;
}

.external-capability-status {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  margin: 9px 0 0;
  padding: 8px 10px;
  color: #4e565b;
  background: #f0f1ef;
  border-left: 3px solid #7e888d;
  font-size: 10px;
  line-height: 1.55;
}

.external-capability-status.is-ready {
  color: #285c4c;
  background: #edf5f0;
  border-left-color: #2d7b60;
}

.external-capability-status.is-self_check_failed,
.external-capability-status.is-unknown {
  color: #754229;
  background: #f8eee5;
  border-left-color: #b8663e;
}

.external-capability-status span {
  min-width: 0;
  overflow-wrap: anywhere;
}

.external-capability-status button {
  flex: 0 0 auto;
  padding: 6px 8px;
  cursor: pointer;
  color: #174f74;
  background: #fffdf8;
  border: 1px solid #7c9aae;
  font-size: 9px;
  font-weight: 700;
}

.external-capability-status:focus-visible,
.external-capability-status button:focus-visible {
  outline: 3px solid #174f74;
  outline-offset: 2px;
}

.external-mode-toggle {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
  padding: 5px 7px;
  color: #6e3b1e;
  background: #f8e8d0;
  border: 1px solid #d6ac7c;
  cursor: pointer;
  font-size: 9px;
  font-weight: 700;
}

.external-mode-toggle input {
  flex: 0 0 auto;
  width: 13px;
  height: 13px;
  margin: 0;
  accent-color: #a34a2a;
}

.external-mode-toggle span {
  overflow-wrap: anywhere;
}

.external-mode-toggle:has(input:disabled) {
  cursor: not-allowed;
  opacity: 0.58;
}

.external-authorization-panel {
  min-width: 0;
  margin-top: 12px;
  color: #f8f0e3;
  background:
    linear-gradient(135deg, rgba(163, 74, 42, 0.18), transparent 42%),
    #252b2f;
  border-top: 4px solid #d17a48;
  box-shadow: inset 0 0 0 1px #485158;
}

.external-authorization-panel > header {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
  padding: 14px 14px 0;
}

.external-authorization-panel > header span {
  color: #dca07d;
  font: 700 8px/1.2 var(--mono);
  letter-spacing: 0.11em;
}

.external-authorization-panel > header h3 {
  margin: 5px 0 0;
  font: 700 18px/1.2 "Songti SC", "STSong", serif;
}

.external-authorization-panel > header > strong {
  flex: 0 0 auto;
  padding: 5px 7px;
  color: #2c211a;
  background: #e9bd83;
  font: 700 8px/1.2 var(--mono);
}

.external-authorization-panel > p {
  margin: 12px 14px;
  color: #d8d3ca;
  font-size: 10px;
  line-height: 1.65;
}

.external-url-register {
  min-width: 0;
  padding: 12px 14px;
  color: #282b2d;
  background: #eee9df;
  border-block: 1px solid #586168;
}

.external-url-register-heading {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
}

.external-url-register-heading strong,
.external-url-register-heading small {
  display: block;
}

.external-url-register-heading strong {
  font-size: 10px;
}

.external-url-register-heading small {
  margin-top: 3px;
  color: #625b55;
  font-size: 9px;
}

.external-url-register button,
.external-authorization-panel > footer button {
  flex: 0 0 auto;
  padding: 6px 8px;
  cursor: pointer;
  color: #71381f;
  background: #fffaf1;
  border: 1px solid #bd8a67;
  font-size: 8px;
  font-weight: 700;
}

.external-url-register button:disabled,
.external-authorization-panel > footer button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.external-url-register ol {
  display: grid;
  gap: 7px;
  margin: 11px 0 0;
  padding: 0;
  list-style: none;
}

.external-url-register li {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  gap: 6px;
  min-width: 0;
  align-items: center;
}

.external-url-register li label {
  color: #6f655b;
  font: 700 8px/1.2 var(--mono);
}

.external-url-register li input {
  width: 100%;
  min-width: 0;
  padding: 7px 8px;
  color: #24282a;
  background: #fffdf8;
  border: 1px solid #aaa69e;
  border-radius: 0;
  font: 9px/1.35 var(--mono);
}

.external-url-register li input[aria-invalid="true"] {
  border-color: #aa493d;
  box-shadow: inset 3px 0 #aa493d;
}

.external-url-error {
  grid-column: 2 / -1;
  margin: 0;
  color: #9a392e;
  font-size: 8px;
  line-height: 1.4;
}

.external-search-note {
  margin: 10px 0 0;
  padding: 8px;
  color: #665b50;
  background: #fffaf1;
  border-left: 3px solid #b97a50;
  font-size: 8px;
  line-height: 1.55;
}

.external-authorization-panel > footer {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
}

.external-authorization-panel > footer span {
  min-width: 0;
  overflow-wrap: anywhere;
  color: #b8b5af;
  font: 8px/1.45 var(--mono);
}

.external-authorization-panel > footer button {
  color: #f0c3aa;
  background: transparent;
  border-color: #a9674a;
}

.composer-actions button {
  flex: 0 0 auto;
  padding: 9px 14px;
  cursor: pointer;
  color: #fff;
  background: var(--report-blue);
  border: 0;
  font-size: 11px;
  font-weight: 700;
}

.composer-actions .cancel-question {
  color: #7a2f26;
  background: transparent;
  border: 1px solid currentColor;
}

.composer-actions button:disabled {
  cursor: not-allowed;
  background: #969c9f;
}

@keyframes status-pulse {
  0%,
  100% { opacity: 0.35; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1); }
}

@media (max-width: 1180px) {
  .published-header {
    grid-template-columns: minmax(180px, 0.8fr) minmax(360px, 1.8fr);
  }

  .header-downloads {
    grid-column: 1 / -1;
    justify-content: flex-start;
  }

  .reader-layout {
    grid-template-columns: minmax(0, 1.38fr) minmax(320px, 0.82fr);
  }
}

@media (max-width: 1100px) {
  .report-masthead {
    grid-template-columns: minmax(0, 1fr);
  }

  .trust-stamp {
    width: 154px;
  }

  .snapshot-ledger {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .gate-register {
    grid-template-columns: minmax(0, 1fr);
  }

  .published-header {
    position: relative;
    grid-template-columns: minmax(0, 1fr);
  }

  .published-header dl {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .model-certification-boundary {
    grid-template-columns: minmax(0, 1fr);
  }

  .mobile-reader-switch {
    position: sticky;
    top: 121px;
    z-index: 20;
    display: grid;
    grid-template-columns: 1fr 1fr;
    padding: 8px;
    background: #ddd9d0;
    border-bottom: 1px solid #c8c3b9;
  }

  .mobile-reader-switch button {
    padding: 11px;
    cursor: pointer;
    color: var(--report-muted);
    background: transparent;
    border: 0;
    font-weight: 700;
  }

  .mobile-reader-switch button.is-active {
    color: #fff;
    background: var(--report-ink);
  }

  .reader-layout {
    display: block;
    width: calc(100% - 24px);
    margin-top: 12px;
  }

  .report-paper,
  .reader-notes {
    display: none;
  }

  .report-paper.is-mobile-active,
  .reader-notes.is-mobile-active {
    display: block;
  }

  .reader-notes {
    position: relative;
    top: auto;
    min-height: min(690px, calc(100vh - 100px));
    max-height: none;
  }

  .notes-scroll {
    max-height: min(52vh, 520px);
  }

  .return-latest {
    bottom: 192px;
  }
}

@media (max-width: 720px) {
  .mobile-reader-switch {
    top: 58px;
  }
}

@media (max-width: 560px) {
  .report-masthead {
    padding: 28px 18px 24px;
  }

  .revision-banner,
  .session-banner,
  .connection-banner {
    align-items: flex-start;
    padding: 14px 16px;
  }

  .preflight-panel,
  .generation-panel {
    width: calc(100% - 20px);
    margin: 18px auto 38px;
    padding: 21px 17px;
    box-shadow: 5px 7px 0 rgba(41, 49, 54, 0.08);
  }

  .snapshot-ledger {
    grid-template-columns: minmax(0, 1fr);
  }

  .primary-action {
    display: grid;
    gap: 6px;
  }

  .controlled-progress {
    grid-template-columns: minmax(0, 1fr);
    border-top: 0;
    border-left: 1px solid var(--report-line);
  }

  .controlled-progress li {
    grid-template-columns: 24px 1fr;
    padding: 7px 0 7px 16px;
  }

  .controlled-progress li::before {
    top: 11px;
    left: -5px;
  }

  .published-header dl {
    grid-template-columns: minmax(0, 1fr);
  }

  .reader-layout {
    width: 100%;
    margin-bottom: 0;
  }

  .report-paper {
    padding: 30px 18px;
    border-inline: 0;
  }

  .highlight-section,
  .report-section {
    grid-template-columns: 28px minmax(0, 1fr);
    gap: 8px;
  }

  .highlight-row {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .highlight-row span {
    grid-column: 1 / -1;
  }

  .artifact-ledger {
    grid-template-columns: minmax(0, 1fr);
  }

  .reader-notes {
    border-inline: 0;
  }

  .notes-header,
  .composer-toolbar,
  .composer-actions {
    align-items: flex-start;
  }

  .notes-header,
  .composer-toolbar,
  .external-capability-status {
    flex-direction: column;
  }

  .composer-actions small {
    white-space: normal;
  }

  .evidence-modes {
    flex-wrap: wrap;
  }

  .external-evidence-appendix > header,
  .external-authorization-panel > header,
  .external-authorization-panel > footer {
    flex-wrap: wrap;
  }

  .external-claim-source > header {
    flex-direction: column;
  }

  .external-claim-source > header span {
    text-align: left;
  }

  .external-request-ledger,
  .external-sources > article > dl {
    grid-template-columns: minmax(0, 1fr);
  }

  .external-request-ledger > div:first-child {
    grid-row: auto;
  }

  .external-url-register li {
    grid-template-columns: 28px minmax(0, 1fr);
  }

  .external-url-register li > button,
  .external-url-error {
    grid-column: 2;
  }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    scroll-behavior: auto !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}
</style>
