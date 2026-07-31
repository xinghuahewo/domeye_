import { readFileSync } from 'node:fs'
import { basename, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { isDeepStrictEqual } from 'node:util'

import { FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS } from './formal-runtime-limits.js'
import {
  COUNTRY_OUTAGE_MARKDOWN_MAX_BYTES,
} from './report/artifact-builder.js'
import {
  COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION,
} from './report/draft-validator.js'
import {
  COUNTRY_OUTAGE_PDF_MAX_BYTES,
  COUNTRY_OUTAGE_PDF_MAX_PAGES,
} from './report/pdf-renderer.js'
import {
  DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_TTL_MS,
  DEFAULT_COUNTRY_OUTAGE_SERVER_LIMITS,
  type CountryOutageServerLimits,
} from './server/index.js'
import {
  FORMAL_PI_AUDIT_RETENTION_DAYS,
} from './cli/formal-pi-audit-log.js'

export const FORMAL_COUNTRY_OUTAGE_ACCEPTANCE_CONFIG_ID =
  'country-outage-agent-core-acceptance-v3' as const

export const FORMAL_COUNTRY_OUTAGE_ACCEPTANCE_CONFIG_SCHEMA_VERSION =
  3 as const

export type FormalAcceptanceEnvironment = Readonly<
  Record<string, string | undefined>
>

export interface FormalCountryOutageAcceptanceRuntime {
  readonly schemaVersion:
    typeof FORMAL_COUNTRY_OUTAGE_ACCEPTANCE_CONFIG_SCHEMA_VERSION
  readonly id: typeof FORMAL_COUNTRY_OUTAGE_ACCEPTANCE_CONFIG_ID
  readonly status: 'frozen'
  readonly frozenAt: string
  readonly acceptanceProfile: 'core-v1'
  readonly businessTimezone: 'Asia/Shanghai'
  readonly validatorRulesVersion:
    'country_outage_report_validator_rules_v5'
  readonly scope: {
    readonly eventType: 'country_outage'
    readonly collectorId: 'rrc25'
    readonly trigger: 'user_only'
    readonly dataAccess: 'published_read_only'
    readonly publicNetworkAccess: 'none'
    readonly externalEvidencePackRequiredForCoreAcceptance: false
    readonly conversationPersistence: 'ephemeral'
  }
  readonly representativeEvent: {
    readonly eventReference:
      'country_outage/2026-02-27 09:12:32/IR/1/r'
    readonly incidentId:
      'incident_go_v1_a1de26f854831330c616a72af21597eb'
    readonly publicationId:
      'publication_v1_38bddead083db3f49023c2e1'
    readonly revision: 1
    readonly isFinal: true
    readonly dataThrough: '2026-02-28T15:00:00Z'
    readonly collectorId: 'rrc25'
    readonly cohortId:
      'cohort_go_v1_4ff75dc68f95249de99c11bec48391fb'
    readonly windowStartUtc: '2026-02-28T10:05:00Z'
    readonly windowEndUtc: '2026-02-28T15:00:00Z'
    readonly intervalSeconds: 300
    readonly expectedObservationCount: 60
  }
  readonly session: {
    readonly ttlSeconds: number
    readonly expiryReminderSeconds: number
    readonly completedDownloadGraceSeconds: number
    readonly maximumQuestions: number
    readonly maximumActiveAnswers: 1
  }
  readonly timeouts: {
    readonly firstStatusMs: number
    readonly snapshotResolutionMs: number
    readonly reportRunMs: number
    readonly questionRunMs: number
    readonly modelAttemptMs: number
    readonly pdfRenderMs: number
    readonly reconnectStatusMs: number
  }
  readonly capacity: {
    readonly maximumActiveReportRunsPerUser: 1
    readonly maximumActiveReportRunsGlobal: number
    readonly maximumQueueDepth: number
    readonly maximumSnapshotBatchRetries: number
    readonly maximumModelAttempts: number
    readonly maximumProviderRequestsPerReport: number
    readonly maximumProviderContextUtf8Bytes: number
    readonly maximumQuestionsPerMinute: number
    readonly maximumReportRunsPerUserPerHour: number
    readonly maximumFactRecords: number
    readonly maximumContextTokens: number
    readonly maximumAnswerCharacters: number
  }
  readonly viewports: readonly [
    Readonly<{ id: 'desktop'; width: 1440; height: 900 }>,
    Readonly<{ id: 'compact_desktop'; width: 1024; height: 720 }>,
    Readonly<{ id: 'tablet'; width: 768; height: 1024 }>,
    Readonly<{ id: 'mobile'; width: 390; height: 844 }>,
  ]
  readonly browserAcceptance: {
    readonly browser: 'Google Chrome'
    readonly version: '150.0.7871.187'
    readonly platform: 'macOS'
    readonly keyboardRequired: true
    readonly semanticTreeRequired: true
    readonly reducedMotionRequired: true
    readonly specificRealScreenReaderRequired: false
  }
  readonly downloads: {
    readonly maximumPdfPages: number
    readonly maximumPdfBytes: number
    readonly maximumMarkdownBytes: number
    readonly pdfPageSize: 'A4'
    readonly requiredFormats: readonly ['pdf', 'markdown']
  }
  readonly retention: {
    readonly sessionStateSeconds: number
    readonly temporaryReportSeconds: number
    readonly temporaryQuestionSeconds: number
    readonly temporaryDownloadSeconds: number
    readonly operationalLogDays: number
    readonly storePromptOrAnswerBodiesInOperationalLogs: false
  }
  readonly authorization: {
    readonly requiredCapability: 'country_outage_event_read'
    readonly reportCacheScope: 'same_authorization_scope'
    readonly questionScope: 'requesting_user_only'
    readonly downloadScope:
      'requesting_user_and_current_event_authorization'
    readonly anonymousAccess: false
    readonly crossUserSessionsIsolated: true
    readonly crossAuthorizationScopeCacheIsolated: true
    readonly eventCapabilityCheckedOnEachEntry: true
  }
  readonly formal: {
    readonly domeyeApiTimeoutMs: number
    readonly baseReportCacheTtlMs: number
    readonly sessionLimits: Readonly<CountryOutageServerLimits>
  }
}

export type FormalAcceptanceConfigurationErrorCode =
  | 'acceptance_config_invalid'
  | 'acceptance_runtime_drift'
  | 'acceptance_environment_drift'

export class FormalAcceptanceConfigurationError extends Error {
  constructor(
    readonly code: FormalAcceptanceConfigurationErrorCode,
    message: string,
  ) {
    super(message)
    this.name = 'FormalAcceptanceConfigurationError'
  }
}

function isRecord(
  value: unknown,
): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function record(
  parent: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  const value = parent[key]
  if (!isRecord(value)) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      `冻结验收配置 ${key} 必须是对象`,
    )
  }
  return value
}

function assertExactKeys(
  value: Record<string, unknown>,
  label: string,
  expected: readonly string[],
): void {
  const actual = Object.keys(value).sort()
  const sortedExpected = [...expected].sort()
  if (
    actual.length !== sortedExpected.length ||
    actual.some((key, index) => key !== sortedExpected[index])
  ) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      `冻结验收配置 ${label} 字段集合漂移`,
    )
  }
}

function positiveInteger(
  parent: Record<string, unknown>,
  key: string,
): number {
  const value = parent[key]
  if (!Number.isSafeInteger(value) || Number(value) <= 0) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      `冻结验收配置 ${key} 必须是正整数`,
    )
  }
  return Number(value)
}

function exactValue<T extends string | number | boolean>(
  parent: Record<string, unknown>,
  key: string,
  expected: T,
): T {
  if (parent[key] !== expected) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      `冻结验收配置 ${key} 必须为 ${String(expected)}`,
    )
  }
  return expected
}

function nonemptyString(
  parent: Record<string, unknown>,
  key: string,
): string {
  const value = parent[key]
  if (typeof value !== 'string' || !value.trim()) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      `冻结验收配置 ${key} 必须是非空字符串`,
    )
  }
  return value
}

function exactStringArray<T extends readonly string[]>(
  parent: Record<string, unknown>,
  key: string,
  expected: T,
): T {
  const value = parent[key]
  if (
    !Array.isArray(value) ||
    value.length !== expected.length ||
    value.some((item, index) => item !== expected[index])
  ) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      `冻结验收配置 ${key} 必须为 ${expected.join(',')}`,
    )
  }
  return expected
}

function secondsToMilliseconds(
  seconds: number,
  label: string,
): number {
  const milliseconds = seconds * 1000
  if (!Number.isSafeInteger(milliseconds) || milliseconds <= 0) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      `冻结验收配置 ${label} 无法安全换算为毫秒`,
    )
  }
  return milliseconds
}

function frozen<T extends object>(value: T): Readonly<T> {
  for (const member of Object.values(value)) {
    if (member && typeof member === 'object' && !Object.isFrozen(member)) {
      frozen(member)
    }
  }
  return Object.freeze(value)
}

export function mapFormalCountryOutageAcceptanceConfiguration(
  value: unknown,
): FormalCountryOutageAcceptanceRuntime {
  if (!isRecord(value)) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      '冻结验收配置必须是 JSON 对象',
    )
  }
  assertExactKeys(value, 'root', [
    'schema_version',
    'id',
    'status',
    'frozen_at',
    'acceptance_profile',
    'business_timezone',
    'validator_rules_version',
    'scope',
    'representative_event',
    'session',
    'timeouts',
    'capacity',
    'downloads',
    'viewports',
    'browser_acceptance',
    'retention',
    'authorization',
  ])

  const schemaVersion = exactValue(
    value,
    'schema_version',
    FORMAL_COUNTRY_OUTAGE_ACCEPTANCE_CONFIG_SCHEMA_VERSION,
  )
  const id = exactValue(
    value,
    'id',
    FORMAL_COUNTRY_OUTAGE_ACCEPTANCE_CONFIG_ID,
  )
  const status = exactValue(value, 'status', 'frozen')
  const frozenAt = nonemptyString(value, 'frozen_at')
  if (!Number.isFinite(Date.parse(frozenAt))) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      '冻结验收配置 frozen_at 必须是有效时间',
    )
  }
  const acceptanceProfile = exactValue(
    value,
    'acceptance_profile',
    'core-v1',
  )
  const businessTimezone = exactValue(
    value,
    'business_timezone',
    'Asia/Shanghai',
  )
  const validatorRulesVersion = exactValue(
    value,
    'validator_rules_version',
    'country_outage_report_validator_rules_v5',
  )

  const scopeSource = record(value, 'scope')
  assertExactKeys(scopeSource, 'scope', [
    'event_type',
    'collector_id',
    'trigger',
    'data_access',
    'public_network_access',
    'external_evidence_pack_required_for_core_acceptance',
    'conversation_persistence',
  ])
  const scope = {
    eventType: exactValue(
      scopeSource,
      'event_type',
      'country_outage',
    ),
    collectorId: exactValue(scopeSource, 'collector_id', 'rrc25'),
    trigger: exactValue(scopeSource, 'trigger', 'user_only'),
    dataAccess: exactValue(
      scopeSource,
      'data_access',
      'published_read_only',
    ),
    publicNetworkAccess: exactValue(
      scopeSource,
      'public_network_access',
      'none',
    ),
    externalEvidencePackRequiredForCoreAcceptance: exactValue(
      scopeSource,
      'external_evidence_pack_required_for_core_acceptance',
      false,
    ),
    conversationPersistence: exactValue(
      scopeSource,
      'conversation_persistence',
      'ephemeral',
    ),
  } as const

  const representativeEventSource = record(
    value,
    'representative_event',
  )
  assertExactKeys(representativeEventSource, 'representative_event', [
    'event_reference',
    'incident_id',
    'publication_id',
    'revision',
    'is_final',
    'data_through',
    'collector_id',
    'cohort_id',
    'window_start_utc',
    'window_end_utc',
    'interval_seconds',
    'expected_observation_count',
  ])
  const representativeEvent = {
    eventReference: exactValue(
      representativeEventSource,
      'event_reference',
      'country_outage/2026-02-27 09:12:32/IR/1/r',
    ),
    incidentId: exactValue(
      representativeEventSource,
      'incident_id',
      'incident_go_v1_a1de26f854831330c616a72af21597eb',
    ),
    publicationId: exactValue(
      representativeEventSource,
      'publication_id',
      'publication_v1_38bddead083db3f49023c2e1',
    ),
    revision: exactValue(representativeEventSource, 'revision', 1),
    isFinal: exactValue(representativeEventSource, 'is_final', true),
    dataThrough: exactValue(
      representativeEventSource,
      'data_through',
      '2026-02-28T15:00:00Z',
    ),
    collectorId: exactValue(
      representativeEventSource,
      'collector_id',
      'rrc25',
    ),
    cohortId: exactValue(
      representativeEventSource,
      'cohort_id',
      'cohort_go_v1_4ff75dc68f95249de99c11bec48391fb',
    ),
    windowStartUtc: exactValue(
      representativeEventSource,
      'window_start_utc',
      '2026-02-28T10:05:00Z',
    ),
    windowEndUtc: exactValue(
      representativeEventSource,
      'window_end_utc',
      '2026-02-28T15:00:00Z',
    ),
    intervalSeconds: exactValue(
      representativeEventSource,
      'interval_seconds',
      300,
    ),
    expectedObservationCount: exactValue(
      representativeEventSource,
      'expected_observation_count',
      60,
    ),
  } as const

  const sessionSource = record(value, 'session')
  assertExactKeys(sessionSource, 'session', [
    'ttl_seconds',
    'expiry_reminder_seconds',
    'completed_download_grace_seconds',
    'maximum_questions',
    'maximum_active_answers',
  ])
  const maximumActiveAnswers = exactValue(
    sessionSource,
    'maximum_active_answers',
    1,
  )
  const session = {
    ttlSeconds: positiveInteger(sessionSource, 'ttl_seconds'),
    expiryReminderSeconds: positiveInteger(
      sessionSource,
      'expiry_reminder_seconds',
    ),
    completedDownloadGraceSeconds: positiveInteger(
      sessionSource,
      'completed_download_grace_seconds',
    ),
    maximumQuestions: positiveInteger(
      sessionSource,
      'maximum_questions',
    ),
    maximumActiveAnswers,
  } as const

  const timeoutSource = record(value, 'timeouts')
  assertExactKeys(timeoutSource, 'timeouts', [
    'first_status_ms',
    'snapshot_resolution_ms',
    'report_run_ms',
    'question_run_ms',
    'model_attempt_ms',
    'pdf_render_ms',
    'reconnect_status_ms',
  ])
  const timeouts = {
    firstStatusMs: positiveInteger(timeoutSource, 'first_status_ms'),
    snapshotResolutionMs: positiveInteger(
      timeoutSource,
      'snapshot_resolution_ms',
    ),
    reportRunMs: positiveInteger(timeoutSource, 'report_run_ms'),
    questionRunMs: positiveInteger(
      timeoutSource,
      'question_run_ms',
    ),
    modelAttemptMs: positiveInteger(
      timeoutSource,
      'model_attempt_ms',
    ),
    pdfRenderMs: positiveInteger(timeoutSource, 'pdf_render_ms'),
    reconnectStatusMs: positiveInteger(
      timeoutSource,
      'reconnect_status_ms',
    ),
  } as const

  const capacitySource = record(value, 'capacity')
  assertExactKeys(capacitySource, 'capacity', [
    'maximum_active_report_runs_per_user',
    'maximum_active_report_runs_global',
    'maximum_queue_depth',
    'maximum_snapshot_batch_retries',
    'maximum_model_attempts',
    'maximum_provider_requests_per_report',
    'maximum_provider_context_utf8_bytes',
    'maximum_questions_per_minute',
    'maximum_report_runs_per_user_per_hour',
    'maximum_fact_records',
    'maximum_context_tokens',
    'maximum_answer_characters',
  ])
  const maximumActiveReportRunsPerUser = exactValue(
    capacitySource,
    'maximum_active_report_runs_per_user',
    1,
  )
  const capacity = {
    maximumActiveReportRunsPerUser,
    maximumActiveReportRunsGlobal: positiveInteger(
      capacitySource,
      'maximum_active_report_runs_global',
    ),
    maximumQueueDepth: positiveInteger(
      capacitySource,
      'maximum_queue_depth',
    ),
    maximumSnapshotBatchRetries: positiveInteger(
      capacitySource,
      'maximum_snapshot_batch_retries',
    ),
    maximumModelAttempts: positiveInteger(
      capacitySource,
      'maximum_model_attempts',
    ),
    maximumProviderRequestsPerReport: positiveInteger(
      capacitySource,
      'maximum_provider_requests_per_report',
    ),
    maximumProviderContextUtf8Bytes: positiveInteger(
      capacitySource,
      'maximum_provider_context_utf8_bytes',
    ),
    maximumQuestionsPerMinute: positiveInteger(
      capacitySource,
      'maximum_questions_per_minute',
    ),
    maximumReportRunsPerUserPerHour: positiveInteger(
      capacitySource,
      'maximum_report_runs_per_user_per_hour',
    ),
    maximumFactRecords: positiveInteger(
      capacitySource,
      'maximum_fact_records',
    ),
    maximumContextTokens: positiveInteger(
      capacitySource,
      'maximum_context_tokens',
    ),
    maximumAnswerCharacters: positiveInteger(
      capacitySource,
      'maximum_answer_characters',
    ),
  } as const

  const downloadSource = record(value, 'downloads')
  assertExactKeys(downloadSource, 'downloads', [
    'maximum_pdf_pages',
    'maximum_pdf_bytes',
    'maximum_markdown_bytes',
    'pdf_page_size',
    'required_formats',
  ])
  const downloads = {
    maximumPdfPages: positiveInteger(
      downloadSource,
      'maximum_pdf_pages',
    ),
    maximumPdfBytes: positiveInteger(
      downloadSource,
      'maximum_pdf_bytes',
    ),
    maximumMarkdownBytes: positiveInteger(
      downloadSource,
      'maximum_markdown_bytes',
    ),
    pdfPageSize: exactValue(downloadSource, 'pdf_page_size', 'A4'),
    requiredFormats: exactStringArray(
      downloadSource,
      'required_formats',
      ['pdf', 'markdown'] as const,
    ),
  } as const

  const expectedViewports = [
    { id: 'desktop', width: 1440, height: 900 },
    { id: 'compact_desktop', width: 1024, height: 720 },
    { id: 'tablet', width: 768, height: 1024 },
    { id: 'mobile', width: 390, height: 844 },
  ] as const
  if (!isDeepStrictEqual(value.viewports, expectedViewports)) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      '冻结验收配置 viewports 必须与四个候选验收视口完全一致',
    )
  }
  const viewports = expectedViewports

  const browserAcceptanceSource = record(
    value,
    'browser_acceptance',
  )
  assertExactKeys(browserAcceptanceSource, 'browser_acceptance', [
    'browser',
    'version',
    'platform',
    'keyboard_required',
    'semantic_tree_required',
    'reduced_motion_required',
    'specific_real_screen_reader_required',
  ])
  const browserAcceptance = {
    browser: exactValue(
      browserAcceptanceSource,
      'browser',
      'Google Chrome',
    ),
    version: exactValue(
      browserAcceptanceSource,
      'version',
      '150.0.7871.187',
    ),
    platform: exactValue(
      browserAcceptanceSource,
      'platform',
      'macOS',
    ),
    keyboardRequired: exactValue(
      browserAcceptanceSource,
      'keyboard_required',
      true,
    ),
    semanticTreeRequired: exactValue(
      browserAcceptanceSource,
      'semantic_tree_required',
      true,
    ),
    reducedMotionRequired: exactValue(
      browserAcceptanceSource,
      'reduced_motion_required',
      true,
    ),
    specificRealScreenReaderRequired: exactValue(
      browserAcceptanceSource,
      'specific_real_screen_reader_required',
      false,
    ),
  } as const

  const retentionSource = record(value, 'retention')
  assertExactKeys(retentionSource, 'retention', [
    'session_state_seconds',
    'temporary_report_seconds',
    'temporary_question_seconds',
    'temporary_download_seconds',
    'operational_log_days',
    'store_prompt_or_answer_bodies_in_operational_logs',
  ])
  const retention = {
    sessionStateSeconds: positiveInteger(
      retentionSource,
      'session_state_seconds',
    ),
    temporaryReportSeconds: positiveInteger(
      retentionSource,
      'temporary_report_seconds',
    ),
    temporaryQuestionSeconds: positiveInteger(
      retentionSource,
      'temporary_question_seconds',
    ),
    temporaryDownloadSeconds: positiveInteger(
      retentionSource,
      'temporary_download_seconds',
    ),
    operationalLogDays: positiveInteger(
      retentionSource,
      'operational_log_days',
    ),
    storePromptOrAnswerBodiesInOperationalLogs: exactValue(
      retentionSource,
      'store_prompt_or_answer_bodies_in_operational_logs',
      false,
    ),
  } as const

  const authorizationSource = record(value, 'authorization')
  assertExactKeys(authorizationSource, 'authorization', [
    'required_capability',
    'report_cache_scope',
    'question_scope',
    'download_scope',
    'anonymous_access',
    'cross_user_sessions_isolated',
    'cross_authorization_scope_cache_isolated',
    'event_capability_checked_on_each_entry',
  ])
  const authorization = {
    requiredCapability: exactValue(
      authorizationSource,
      'required_capability',
      'country_outage_event_read',
    ),
    reportCacheScope: exactValue(
      authorizationSource,
      'report_cache_scope',
      'same_authorization_scope',
    ),
    questionScope: exactValue(
      authorizationSource,
      'question_scope',
      'requesting_user_only',
    ),
    downloadScope: exactValue(
      authorizationSource,
      'download_scope',
      'requesting_user_and_current_event_authorization',
    ),
    anonymousAccess: exactValue(
      authorizationSource,
      'anonymous_access',
      false,
    ),
    crossUserSessionsIsolated: exactValue(
      authorizationSource,
      'cross_user_sessions_isolated',
      true,
    ),
    crossAuthorizationScopeCacheIsolated: exactValue(
      authorizationSource,
      'cross_authorization_scope_cache_isolated',
      true,
    ),
    eventCapabilityCheckedOnEachEntry: exactValue(
      authorizationSource,
      'event_capability_checked_on_each_entry',
      true,
    ),
  } as const

  if (
    retention.sessionStateSeconds !== session.ttlSeconds ||
    retention.temporaryQuestionSeconds !== session.ttlSeconds
  ) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      '会话与问题的冻结留存时间必须与会话 TTL 一致',
    )
  }
  if (session.expiryReminderSeconds >= session.ttlSeconds) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      '冻结到期提醒必须早于会话 TTL',
    )
  }
  if (
    retention.temporaryDownloadSeconds !==
    retention.temporaryReportSeconds
  ) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      '冻结下载与基础报告缓存留存时间必须一致',
    )
  }

  const sessionLimits: CountryOutageServerLimits = {
    sessionTtlMs: secondsToMilliseconds(
      session.ttlSeconds,
      'session.ttl_seconds',
    ),
    expiryReminderMs: secondsToMilliseconds(
      session.expiryReminderSeconds,
      'session.expiry_reminder_seconds',
    ),
    reportRunTimeoutMs: timeouts.reportRunMs,
    questionRunTimeoutMs: timeouts.questionRunMs,
    maximumQuestions: session.maximumQuestions,
    maximumActiveAnswers,
    maximumActiveReportRunsPerUser,
    maximumActiveReportRunsGlobal:
      capacity.maximumActiveReportRunsGlobal,
    maximumQueueDepth: capacity.maximumQueueDepth,
    maximumQuestionsPerMinute: capacity.maximumQuestionsPerMinute,
    maximumReportRunsPerUserPerHour:
      capacity.maximumReportRunsPerUserPerHour,
    maximumAnswerCharacters: capacity.maximumAnswerCharacters,
    // core-v3 对输入和输出采用同一个 4,000 字符文本边界。
    maximumQuestionCharacters: capacity.maximumAnswerCharacters,
    completedDownloadGraceMs: secondsToMilliseconds(
      session.completedDownloadGraceSeconds,
      'session.completed_download_grace_seconds',
    ),
    // 到期占位信息仅保留一个提醒周期，不另设隐式运行配置。
    tombstoneTtlMs: secondsToMilliseconds(
      session.expiryReminderSeconds,
      'session.expiry_reminder_seconds',
    ),
  }

  return frozen({
    schemaVersion,
    id,
    status,
    frozenAt,
    acceptanceProfile,
    businessTimezone,
    validatorRulesVersion,
    scope,
    representativeEvent,
    session,
    timeouts,
    capacity,
    downloads,
    viewports,
    browserAcceptance,
    retention,
    authorization,
    formal: {
      domeyeApiTimeoutMs: timeouts.snapshotResolutionMs,
      baseReportCacheTtlMs: secondsToMilliseconds(
        retention.temporaryReportSeconds,
        'retention.temporary_report_seconds',
      ),
      sessionLimits,
    },
  })
}

function acceptanceConfigurationPath(): string {
  const moduleDirectory = dirname(fileURLToPath(import.meta.url))
  const parentDirectory = dirname(moduleDirectory)
  const sidecarDirectory =
    basename(parentDirectory) === 'dist'
      ? dirname(parentDirectory)
      : parentDirectory
  return resolve(
    sidecarDirectory,
    '..',
    'config',
    'country-outage-agent-core-acceptance-v3.json',
  )
}

function mismatch(
  name: string,
  actual: unknown,
  expected: unknown,
): never {
  throw new FormalAcceptanceConfigurationError(
    'acceptance_runtime_drift',
    `正式运行值 ${name}=${JSON.stringify(actual)} 与冻结验收配置 ${JSON.stringify(expected)} 不一致`,
  )
}

function assertEqual(
  name: string,
  actual: unknown,
  expected: unknown,
): void {
  if (!isDeepStrictEqual(actual, expected)) {
    mismatch(name, actual, expected)
  }
}

/**
 * 验证仍由代码实现的硬上限没有偏离版本化验收配置。正式入口在模型预检和
 * HTTP Server 创建前调用；任何一项漂移都失败关闭。
 */
export function assertFormalCountryOutageRuntimeMatchesAcceptance(
  runtime: FormalCountryOutageAcceptanceRuntime,
): void {
  assertEqual(
    'validatorRulesVersion',
    COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION,
    runtime.validatorRulesVersion,
  )
  assertEqual(
    'modelAttemptTimeoutMs',
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.modelAttemptTimeoutMs,
    runtime.timeouts.modelAttemptMs,
  )
  assertEqual(
    'maximumModelAttempts',
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumModelAttempts,
    runtime.capacity.maximumModelAttempts,
  )
  assertEqual(
    'maximumProviderRequestsPerReport',
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
      .maximumProviderRequestsPerReport,
    runtime.capacity.maximumProviderRequestsPerReport,
  )
  assertEqual(
    'maximumProviderContextBytes',
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumProviderContextBytes,
    runtime.capacity.maximumProviderContextUtf8Bytes,
  )
  assertEqual(
    'maximumEvidenceRecords',
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumEvidenceRecords,
    runtime.capacity.maximumFactRecords,
  )
  assertEqual(
    'maximumContextInputTokens',
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS.maximumContextInputTokens,
    runtime.capacity.maximumContextTokens,
  )
  assertEqual(
    'minimumModelContextWindowTokens',
    FORMAL_COUNTRY_OUTAGE_RUNTIME_LIMITS
      .minimumModelContextWindowTokens,
    runtime.capacity.maximumContextTokens,
  )
  assertEqual(
    'maximumPdfPages',
    COUNTRY_OUTAGE_PDF_MAX_PAGES,
    runtime.downloads.maximumPdfPages,
  )
  assertEqual(
    'maximumPdfBytes',
    COUNTRY_OUTAGE_PDF_MAX_BYTES,
    runtime.downloads.maximumPdfBytes,
  )
  assertEqual(
    'maximumMarkdownBytes',
    COUNTRY_OUTAGE_MARKDOWN_MAX_BYTES,
    runtime.downloads.maximumMarkdownBytes,
  )
  assertEqual(
    'operationalLogDays',
    FORMAL_PI_AUDIT_RETENTION_DAYS,
    runtime.retention.operationalLogDays,
  )
  assertEqual(
    'defaultBaseReportCacheTtlMs',
    DEFAULT_COUNTRY_OUTAGE_BASE_REPORT_CACHE_TTL_MS,
    runtime.formal.baseReportCacheTtlMs,
  )
  assertEqual(
    'defaultServerLimits',
    DEFAULT_COUNTRY_OUTAGE_SERVER_LIMITS,
    runtime.formal.sessionLimits,
  )
}

export function loadFormalCountryOutageAcceptanceRuntime(
): FormalCountryOutageAcceptanceRuntime {
  const path = acceptanceConfigurationPath()
  let raw: unknown
  try {
    raw = JSON.parse(readFileSync(path, 'utf8')) as unknown
  } catch (error) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_config_invalid',
      `无法读取固定验收配置 ${path}：${error instanceof Error ? error.message : '未知错误'}`,
    )
  }
  const runtime = mapFormalCountryOutageAcceptanceConfiguration(raw)
  assertFormalCountryOutageRuntimeMatchesAcceptance(runtime)
  return runtime
}

/**
 * 可省略环境变量；若部署仍保留旧变量，则其值只能与冻结配置完全相同。
 */
export function frozenAcceptanceEnvironmentInteger(
  env: FormalAcceptanceEnvironment,
  name: string,
  expected: number,
): number {
  const raw = env[name]?.trim()
  if (!raw) return expected
  const parsed = Number(raw)
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_environment_drift',
      `${name} 必须是与冻结验收配置一致的正整数 ${expected}`,
    )
  }
  if (parsed !== expected) {
    throw new FormalAcceptanceConfigurationError(
      'acceptance_environment_drift',
      `${name}=${parsed} 与冻结验收配置 ${expected} 不一致`,
    )
  }
  return expected
}
