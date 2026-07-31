import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

import {
  assertFormalCountryOutageRuntimeMatchesAcceptance,
  FormalAcceptanceConfigurationError,
  frozenAcceptanceEnvironmentInteger,
  mapFormalCountryOutageAcceptanceConfiguration,
} from '../src/formal-acceptance-runtime.js'

function acceptanceSource(): Record<string, unknown> {
  return JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        '../config/country-outage-agent-core-acceptance-v3.json',
      ),
      'utf8',
    ),
  ) as Record<string, unknown>
}

test('正式核心运行完整映射 core-v3，且不消费外部能力包配置', () => {
  const source = acceptanceSource()
  const runtime =
    mapFormalCountryOutageAcceptanceConfiguration(source)

  const projected = {
    schema_version: runtime.schemaVersion,
    id: runtime.id,
    status: runtime.status,
    frozen_at: runtime.frozenAt,
    acceptance_profile: runtime.acceptanceProfile,
    business_timezone: runtime.businessTimezone,
    validator_rules_version: runtime.validatorRulesVersion,
    scope: {
      event_type: runtime.scope.eventType,
      collector_id: runtime.scope.collectorId,
      trigger: runtime.scope.trigger,
      data_access: runtime.scope.dataAccess,
      public_network_access: runtime.scope.publicNetworkAccess,
      external_evidence_pack_required_for_core_acceptance:
        runtime.scope.externalEvidencePackRequiredForCoreAcceptance,
      conversation_persistence:
        runtime.scope.conversationPersistence,
    },
    representative_event: {
      event_reference: runtime.representativeEvent.eventReference,
      incident_id: runtime.representativeEvent.incidentId,
      publication_id: runtime.representativeEvent.publicationId,
      revision: runtime.representativeEvent.revision,
      is_final: runtime.representativeEvent.isFinal,
      data_through: runtime.representativeEvent.dataThrough,
      collector_id: runtime.representativeEvent.collectorId,
      cohort_id: runtime.representativeEvent.cohortId,
      window_start_utc:
        runtime.representativeEvent.windowStartUtc,
      window_end_utc: runtime.representativeEvent.windowEndUtc,
      interval_seconds:
        runtime.representativeEvent.intervalSeconds,
      expected_observation_count:
        runtime.representativeEvent.expectedObservationCount,
    },
    session: {
      ttl_seconds: runtime.session.ttlSeconds,
      expiry_reminder_seconds:
        runtime.session.expiryReminderSeconds,
      completed_download_grace_seconds:
        runtime.session.completedDownloadGraceSeconds,
      maximum_questions: runtime.session.maximumQuestions,
      maximum_active_answers:
        runtime.session.maximumActiveAnswers,
    },
    timeouts: {
      first_status_ms: runtime.timeouts.firstStatusMs,
      snapshot_resolution_ms:
        runtime.timeouts.snapshotResolutionMs,
      report_run_ms: runtime.timeouts.reportRunMs,
      question_run_ms: runtime.timeouts.questionRunMs,
      model_attempt_ms: runtime.timeouts.modelAttemptMs,
      pdf_render_ms: runtime.timeouts.pdfRenderMs,
      reconnect_status_ms: runtime.timeouts.reconnectStatusMs,
    },
    capacity: {
      maximum_active_report_runs_per_user:
        runtime.capacity.maximumActiveReportRunsPerUser,
      maximum_active_report_runs_global:
        runtime.capacity.maximumActiveReportRunsGlobal,
      maximum_queue_depth: runtime.capacity.maximumQueueDepth,
      maximum_snapshot_batch_retries:
        runtime.capacity.maximumSnapshotBatchRetries,
      maximum_model_attempts:
        runtime.capacity.maximumModelAttempts,
      maximum_provider_requests_per_report:
        runtime.capacity.maximumProviderRequestsPerReport,
      maximum_provider_context_utf8_bytes:
        runtime.capacity.maximumProviderContextUtf8Bytes,
      maximum_questions_per_minute:
        runtime.capacity.maximumQuestionsPerMinute,
      maximum_report_runs_per_user_per_hour:
        runtime.capacity.maximumReportRunsPerUserPerHour,
      maximum_fact_records:
        runtime.capacity.maximumFactRecords,
      maximum_context_tokens:
        runtime.capacity.maximumContextTokens,
      maximum_answer_characters:
        runtime.capacity.maximumAnswerCharacters,
    },
    downloads: {
      maximum_pdf_pages: runtime.downloads.maximumPdfPages,
      maximum_pdf_bytes: runtime.downloads.maximumPdfBytes,
      maximum_markdown_bytes:
        runtime.downloads.maximumMarkdownBytes,
      pdf_page_size: runtime.downloads.pdfPageSize,
      required_formats: runtime.downloads.requiredFormats,
    },
    viewports: runtime.viewports,
    browser_acceptance: {
      browser: runtime.browserAcceptance.browser,
      version: runtime.browserAcceptance.version,
      platform: runtime.browserAcceptance.platform,
      keyboard_required:
        runtime.browserAcceptance.keyboardRequired,
      semantic_tree_required:
        runtime.browserAcceptance.semanticTreeRequired,
      reduced_motion_required:
        runtime.browserAcceptance.reducedMotionRequired,
      specific_real_screen_reader_required:
        runtime.browserAcceptance.specificRealScreenReaderRequired,
    },
    retention: {
      session_state_seconds:
        runtime.retention.sessionStateSeconds,
      temporary_report_seconds:
        runtime.retention.temporaryReportSeconds,
      temporary_question_seconds:
        runtime.retention.temporaryQuestionSeconds,
      temporary_download_seconds:
        runtime.retention.temporaryDownloadSeconds,
      operational_log_days:
        runtime.retention.operationalLogDays,
      store_prompt_or_answer_bodies_in_operational_logs:
        runtime.retention
          .storePromptOrAnswerBodiesInOperationalLogs,
    },
    authorization: {
      required_capability:
        runtime.authorization.requiredCapability,
      report_cache_scope:
        runtime.authorization.reportCacheScope,
      question_scope: runtime.authorization.questionScope,
      download_scope: runtime.authorization.downloadScope,
      anonymous_access: runtime.authorization.anonymousAccess,
      cross_user_sessions_isolated:
        runtime.authorization.crossUserSessionsIsolated,
      cross_authorization_scope_cache_isolated:
        runtime.authorization.crossAuthorizationScopeCacheIsolated,
      event_capability_checked_on_each_entry:
        runtime.authorization.eventCapabilityCheckedOnEachEntry,
    },
  }

  assert.deepEqual(projected, source)
  assert.equal(runtime.scope.publicNetworkAccess, 'none')
  assert.equal(
    runtime.scope.externalEvidencePackRequiredForCoreAcceptance,
    false,
  )
  assert.equal(
    Object.prototype.hasOwnProperty.call(runtime, 'externalEvidence'),
    false,
  )
  assert.equal(runtime.formal.domeyeApiTimeoutMs, 5_000)
  assert.equal(runtime.formal.baseReportCacheTtlMs, 3_600_000)
  assert.deepEqual(runtime.formal.sessionLimits, {
    sessionTtlMs: 1_800_000,
    expiryReminderMs: 300_000,
    reportRunTimeoutMs: 120_000,
    questionRunTimeoutMs: 60_000,
    maximumQuestions: 30,
    maximumActiveAnswers: 1,
    maximumActiveReportRunsPerUser: 1,
    maximumActiveReportRunsGlobal: 8,
    maximumQueueDepth: 32,
    maximumQuestionsPerMinute: 6,
    maximumReportRunsPerUserPerHour: 3,
    maximumAnswerCharacters: 4_000,
    maximumQuestionCharacters: 4_000,
    completedDownloadGraceMs: 120_000,
    tombstoneTtlMs: 300_000,
  })
  assert.equal(Object.isFrozen(runtime), true)
  assert.equal(Object.isFrozen(runtime.formal.sessionLimits), true)
  assert.doesNotThrow(() =>
    assertFormalCountryOutageRuntimeMatchesAcceptance(runtime),
  )
})

test('核心配置身份、RRC25、无公网和交叉边界漂移时拒绝映射', () => {
  const mutations: Array<
    [string, (source: Record<string, unknown>) => void]
  > = [
    ['配置版本', (source) => {
      source.id = 'country-outage-agent-acceptance-v2'
    }],
    ['验收剖面', (source) => {
      source.acceptance_profile = 'external-evidence-pack-v1'
    }],
    ['collector', (source) => {
      ;(source.scope as Record<string, unknown>).collector_id = 'rrc24'
    }],
    ['核心公网访问', (source) => {
      ;(source.scope as Record<string, unknown>).public_network_access =
        'managed'
    }],
    ['外部能力包成为核心前置', (source) => {
      ;(
        source.scope as Record<string, unknown>
      ).external_evidence_pack_required_for_core_acceptance = true
    }],
    ['validator', (source) => {
      source.validator_rules_version =
        'country_outage_report_validator_rules_v4'
    }],
    ['代表性事件时间槽', (source) => {
      ;(
        source.representative_event as Record<string, unknown>
      ).expected_observation_count = 59
    }],
    ['候选浏览器版本', (source) => {
      ;(
        source.browser_acceptance as Record<string, unknown>
      ).version = 'current'
    }],
    ['缓存留存', (source) => {
      ;(
        source.retention as Record<string, unknown>
      ).temporary_download_seconds = 3_599
    }],
    ['匿名访问', (source) => {
      ;(
        source.authorization as Record<string, unknown>
      ).anonymous_access = true
    }],
    ['未映射量化字段', (source) => {
      ;(
        source.capacity as Record<string, unknown>
      ).unversioned_runtime_limit = 1
    }],
  ]

  for (const [name, mutate] of mutations) {
    const source = acceptanceSource()
    mutate(source)
    assert.throws(
      () => mapFormalCountryOutageAcceptanceConfiguration(source),
      (error: unknown) =>
        error instanceof FormalAcceptanceConfigurationError &&
        error.code === 'acceptance_config_invalid',
      name,
    )
  }
})

test('旧环境变量仅允许省略或等于冻结值，任意覆盖均失败关闭', () => {
  assert.equal(
    frozenAcceptanceEnvironmentInteger({}, 'FROZEN_VALUE', 5_000),
    5_000,
  )
  assert.equal(
    frozenAcceptanceEnvironmentInteger(
      { FROZEN_VALUE: '5000' },
      'FROZEN_VALUE',
      5_000,
    ),
    5_000,
  )
  for (const value of ['10000', '4999', '5.5', 'invalid']) {
    assert.throws(
      () =>
        frozenAcceptanceEnvironmentInteger(
          { FROZEN_VALUE: value },
          'FROZEN_VALUE',
          5_000,
        ),
      (error: unknown) =>
        error instanceof FormalAcceptanceConfigurationError &&
        error.code === 'acceptance_environment_drift',
    )
  }
})
