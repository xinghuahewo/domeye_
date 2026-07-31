import { describe, expect, it } from 'vitest'

import type {
  CountryOutageAgentEvent,
  CountryOutageExternalAppendix,
  CountryOutageReportDocument,
} from '@/api/countryOutageAgent'
import type { EventObservation } from '@/types/api'
import {
  appendixMatchesCurrentReport,
  canonicalCountryOutageEventReference,
  CountryOutageAbortableRequestGate,
  CountryOutageObservationRequestGate,
  decideCountryOutageObservationRefresh,
  freezeCountryOutageReportBinding,
  matchCountryOutageQuestionEvent,
  validateCompletedCountryOutageReportEvent,
  validateCountryOutageQuestionAnswerSnapshot,
  validateCountryOutagePageObservationIdentity,
} from './countryOutageRuntime'

const EVENT_REFERENCE = 'country_outage/2026-02-27 09:12:32/IR/1/r'

function validObservation(): EventObservation {
  return {
    schema_version: 'country_outage_observation_v2',
    revision: 1,
    publication_id: 'publication-v1',
    publication_state: 'published',
    observation_state: 'evidence_complete',
    data_mode: 'replay',
    data_through: '2026-02-28T15:00:00Z',
    updated_at: '2026-02-28T15:01:00Z',
    is_final: true,
    processing_status: {
      state: 'final',
      updated_at: '2026-02-28T15:01:00Z',
      attempted_through: '2026-02-28T15:00:00Z',
      reason: null,
      last_complete_data_through: '2026-02-28T15:00:00Z',
    },
    missing_slot_count: 0,
    incident_id: 'incident-ir',
    cohort_id: 'cohort-ir',
    window_start_utc: '2026-02-28T10:05:00Z',
    window_end_utc: '2026-02-28T15:00:00Z',
    capability_contract_version: 'country_outage_capabilities_v1',
    event_identity: {
      incident_id: 'incident-ir',
      legacy_reference: EVENT_REFERENCE,
      legacy_record_time_local: '2026-02-27T09:12:32+08:00',
      event_type: 'country_outage',
      country_code: 'IR',
      country_name: '伊朗',
      display_name: '伊朗 BGP 路由可见性观测',
    },
    observation_scope: {
      collector_id: 'rrc25',
      collector_ids: ['rrc25'],
      collector_count: 1,
      window_start_utc: '2026-02-28T10:05:00Z',
      window_end_utc: '2026-02-28T15:00:00Z',
    },
    cohort: {
      cohort_id: 'cohort-ir',
    },
    audit: {
      schema_version: 'country_outage_audit_v2',
      incident_id: 'incident-ir',
      publication_id: 'publication-v1',
      revision: 1,
      publication_state: 'published',
      observation_state: 'evidence_complete',
      data_mode: 'replay',
      data_through: '2026-02-28T15:00:00Z',
      updated_at: '2026-02-28T15:01:00Z',
      is_final: true,
      processing_status: {
        state: 'final',
        updated_at: '2026-02-28T15:01:00Z',
        attempted_through: '2026-02-28T15:00:00Z',
        reason: null,
        last_complete_data_through: '2026-02-28T15:00:00Z',
      },
      missing_slot_count: 0,
      cohort_id: 'cohort-ir',
      window_start_utc: '2026-02-28T10:05:00Z',
      window_end_utc: '2026-02-28T15:00:00Z',
      capability_contract_version: 'country_outage_capabilities_v1',
      revision_history: [
        {
          publication_id: 'publication-v1',
          revision: 1,
          data_through: '2026-02-28T15:00:00Z',
          updated_at: '2026-02-28T15:01:00Z',
          publication_state: 'published',
          supersedes_publication_id: null,
          correction_reason: null,
          publication_kind: 'baseline',
          processing_status: {
            state: 'final',
            updated_at: '2026-02-28T15:01:00Z',
            attempted_through: '2026-02-28T15:00:00Z',
            reason: null,
            last_complete_data_through: '2026-02-28T15:00:00Z',
          },
        },
      ],
    } as unknown as EventObservation['audit'],
  } as unknown as EventObservation
}

function observationAtRevision(revision: number): EventObservation {
  const value = structuredClone(validObservation())
  value.revision = revision
  value.publication_id = `publication-v${revision}`
  value.audit!.revision = revision
  value.audit!.publication_id = `publication-v${revision}`
  value.audit!.revision_history = Array.from(
    { length: revision },
    (_, index) => ({
      publication_id: `publication-v${index + 1}`,
      revision: index + 1,
      data_through: '2026-02-28T15:00:00Z',
      updated_at: `2026-02-28T15:0${index + 1}:00Z`,
      publication_state: 'published',
      supersedes_publication_id:
        index === 0 ? null : `publication-v${index}`,
      correction_reason: index === 0 ? null : '历史数据补正',
      publication_kind: index === 0 ? 'baseline' : 'correction',
      processing_status: structuredClone(value.processing_status),
    }),
  )
  return value
}

function sameRevisionPublication(
  kind: 'append' | 'status',
): EventObservation {
  const value = structuredClone(validObservation())
  value.publication_id = `publication-${kind}`
  value.updated_at = '2026-02-28T15:06:00Z'
  if (kind === 'append') {
    value.data_through = '2026-02-28T15:05:00Z'
    value.window_end_utc = '2026-02-28T15:05:00Z'
    value.observation_scope.window_end_utc = '2026-02-28T15:05:00Z'
  } else {
    value.is_final = false
    value.processing_status = {
      state: 'waiting_for_source',
      updated_at: '2026-02-28T15:06:00Z',
      attempted_through: '2026-02-28T15:05:00Z',
      reason: '等待下一份 RRC25 源文件',
      last_complete_data_through: '2026-02-28T15:00:00Z',
    }
  }
  Object.assign(value.audit!, {
    publication_id: value.publication_id,
    data_through: value.data_through,
    updated_at: value.updated_at,
    is_final: value.is_final,
    processing_status: structuredClone(value.processing_status),
    window_end_utc: value.window_end_utc,
  })
  value.audit!.revision_history = [
    ...structuredClone(validObservation().audit!.revision_history ?? []),
    {
      publication_id: value.publication_id,
      revision: 1,
      data_through: value.data_through,
      updated_at: value.updated_at,
      publication_state: 'published',
      supersedes_publication_id: null,
      correction_reason: null,
      publication_kind: kind,
      processing_status: structuredClone(value.processing_status),
    },
  ]
  return value
}

function reportDocument(): CountryOutageReportDocument {
  return {
    schemaVersion: 'country_outage_report_document_v1',
    artifactId: `report_${'1'.repeat(32)}`,
    reportContentSha256: 'a'.repeat(64),
    reportSpecificationVersion: 'country_outage_report_spec_v1',
    projectKnowledgeVersion: 'country_outage_report_skill_v6',
    factSetId: `facts_${'2'.repeat(32)}`,
    validatorRulesVersion: 'country_outage_report_validator_rules_v5',
    skillBundleSha256: 'b'.repeat(64),
    generatedAt: '2026-07-29T12:00:00Z',
    aiGenerated: true,
    humanReviewed: false,
    event: {
      incident_id: 'incident-ir',
      legacy_reference: EVENT_REFERENCE,
      event_type: 'country_outage',
      country_code: 'IR',
      country_name: '伊朗',
      display_name: '伊朗 BGP 路由可见性观测',
    },
    snapshot: {
      incidentId: 'incident-ir',
      publicationId: 'publication-v1',
      revision: 1,
      dataThrough: '2026-02-28T15:00:00Z',
      isFinal: true,
      collectorId: 'rrc25',
      windowStartUtc: '2026-02-28T10:05:00Z',
      windowEndUtc: '2026-02-28T15:00:00Z',
      cohortId: 'cohort-ir',
    },
    model: {
      provider: 'deepseek',
      model: 'deepseek-v4-flash',
      modelVersion: 'deepseek-v4-flash',
      adapter: 'pi-sdk',
      piVersion: '0.82.1',
      runtimeIdentity: 'formal',
    },
    validation: {
      passed: true,
      errors: [],
      warnings: [],
      checkedEvidenceRefs: ['overview:/observation_scope'],
    },
    draft: {
      schemaVersion: 'country_outage_report_draft_v1',
      title: '伊朗 BGP 路由可见性观测报告',
      subtitle: '窗口结束时仍未回到起点水平',
      summary: {
        text: '摘要',
        evidenceRefs: ['overview:/observation_scope'],
      },
      highlights: [],
      sections: [],
      unknowns: [],
    },
  }
}

function externalAppendix(
  report: CountryOutageReportDocument,
): CountryOutageExternalAppendix {
  return {
    schema_version: 'country_outage_external_appendix_v1',
    classification_policy_version:
      'country_outage_external_source_classification_policy_v1',
    status: 'collecting',
    comparison_status: 'insufficient',
    frozen_binding: {
      incident_id: report.event.incident_id,
      publication_id: report.snapshot.publicationId,
      revision: report.snapshot.revision,
      data_through: report.snapshot.dataThrough,
      fact_set_id: report.factSetId,
      cohort_id: report.snapshot.cohortId,
      country_code: report.event.country_code,
      collector_id: 'rrc25',
      window_start_utc: report.snapshot.windowStartUtc,
      window_end_utc: report.snapshot.windowEndUtc,
    },
    query: '窗口结束时恢复了吗？',
    requested_at: '2026-07-29T12:05:00Z',
    retrieved_at: null,
    claims: [],
    sources: [],
  }
}

function completedEvent(): CountryOutageAgentEvent {
  const report = reportDocument()
  return {
    schema_version: 'country_outage_agent_event_v1',
    event_id: 5,
    report_id: 'report-1',
    run_id: 'run-1',
    event_type: 'report_state',
    at: '2026-07-29T12:00:00Z',
    state: 'completed',
    phase: 'completed',
    session: {
      expires_at: '2026-07-29T12:30:00Z',
      reminder_at: '2026-07-29T12:25:00Z',
    },
    snapshot: structuredClone(report.snapshot),
    report,
    artifacts: [
      {
        format: 'markdown',
        status: 'ready',
        artifact_id: report.artifactId,
        filename: 'IR.md',
        media_type: 'text/markdown; charset=utf-8',
        byte_length: 1_024,
        sha256: 'c'.repeat(64),
      },
      {
        format: 'pdf',
        status: 'ready',
        artifact_id: report.artifactId,
        filename: 'IR.pdf',
        media_type: 'application/pdf',
        byte_length: 2_048,
        sha256: 'd'.repeat(64),
      },
    ],
  }
}

function validate(event: CountryOutageAgentEvent) {
  return validateCompletedCountryOutageReportEvent(event, {
    expectedReportId: 'report-1',
    expectedRunId: 'run-1',
    binding: freezeCountryOutageReportBinding(
      validObservation(),
      EVENT_REFERENCE,
    ),
  })
}

describe('国家中断 SSE 完成事件运行时身份', () => {
  it('统一闭合 event/report 快照、冻结事件、RRC25 与制品身份', () => {
    expect(validate(completedEvent())).toEqual({
      accepted: true,
      code: 'accepted',
      message: '',
    })
    expect(canonicalCountryOutageEventReference(
      `?ref=${EVENT_REFERENCE.replace(' ', '+')}`,
    )).toBe(EVENT_REFERENCE)
  })

  it('event.snapshot 与 report.snapshot 任一字段不同都失败关闭', () => {
    const event = completedEvent()
    event.snapshot!.publicationId = 'publication-forged'

    const result = validate(event)
    expect(result.accepted).toBe(false)
    expect(result.message).toContain(
      'event.snapshot 与 event.report.snapshot 不一致',
    )
  })

  it.each([
    {
      name: '冻结 publication',
      mutate(event: CountryOutageAgentEvent) {
        event.snapshot!.publicationId = 'publication-v2'
        event.report!.snapshot.publicationId = 'publication-v2'
      },
    },
    {
      name: '冻结国家',
      mutate(event: CountryOutageAgentEvent) {
        event.report!.event.country_code = 'US'
      },
    },
    {
      name: '唯一 collector',
      mutate(event: CountryOutageAgentEvent) {
        event.snapshot!.collectorId = 'rrc24' as 'rrc25'
        event.report!.snapshot.collectorId = 'rrc24' as 'rrc25'
      },
    },
    {
      name: '当前 report',
      mutate(event: CountryOutageAgentEvent) {
        event.report_id = 'report-other'
      },
    },
    {
      name: '当前 run',
      mutate(event: CountryOutageAgentEvent) {
        event.run_id = 'run-other'
      },
    },
    {
      name: '制品 artifact',
      mutate(event: CountryOutageAgentEvent) {
        const markdown = event.artifacts?.[0]
        if (markdown?.status === 'ready') {
          markdown.artifact_id = 'artifact-forged'
        }
      },
    },
    {
      name: 'factSet',
      mutate(event: CountryOutageAgentEvent) {
        event.report!.factSetId = ''
      },
    },
  ])('$name 身份不一致时不接受完成事件', ({ mutate }) => {
    const event = completedEvent()
    mutate(event)
    expect(validate(event)).toMatchObject({
      accepted: false,
      code: 'report_protocol_identity_conflict',
    })
  })

  it.each([
    {
      name: '报告 schema',
      mutate(event: CountryOutageAgentEvent) {
        event.report!.schemaVersion =
          'invalid' as 'country_outage_report_document_v1'
      },
    },
    {
      name: '旧版项目知识',
      mutate(event: CountryOutageAgentEvent) {
        event.report!.projectKnowledgeVersion =
          'country_outage_report_skill_v5' as 'country_outage_report_skill_v6'
      },
    },
    {
      name: 'v5 校验规则',
      mutate(event: CountryOutageAgentEvent) {
        event.report!.validatorRulesVersion =
          'v4' as 'country_outage_report_validator_rules_v5'
      },
    },
    {
      name: '报告 ID 格式',
      mutate(event: CountryOutageAgentEvent) {
        event.report!.artifactId = 'report-not-content-addressed'
      },
    },
    {
      name: '事实集 ID 格式',
      mutate(event: CountryOutageAgentEvent) {
        event.report!.factSetId = 'facts-not-content-addressed'
      },
    },
    {
      name: 'SHA-256 格式',
      mutate(event: CountryOutageAgentEvent) {
        event.report!.skillBundleSha256 = 'ABC'
      },
    },
    {
      name: '信任标志',
      mutate(event: CountryOutageAgentEvent) {
        event.report!.humanReviewed = true as false
      },
    },
    {
      name: '正文 evidenceRefs',
      mutate(event: CountryOutageAgentEvent) {
        event.report!.draft.summary.evidenceRefs = ['not-an-evidence-ref']
      },
    },
    {
      name: '模型身份',
      mutate(event: CountryOutageAgentEvent) {
        event.report!.model.adapter = 'shell' as 'pi-sdk'
      },
    },
    {
      name: '正式校验结果',
      mutate(event: CountryOutageAgentEvent) {
        event.report!.validation.passed = false
        event.report!.validation.errors = ['validator rejected']
      },
    },
    {
      name: 'Markdown 媒体类型',
      mutate(event: CountryOutageAgentEvent) {
        const artifact = event.artifacts?.[0]
        if (artifact?.status === 'ready') {
          artifact.media_type = 'text/markdown'
        }
      },
    },
    {
      name: 'PDF 字节长度',
      mutate(event: CountryOutageAgentEvent) {
        const artifact = event.artifacts?.[1]
        if (artifact?.status === 'ready') artifact.byte_length = 0
      },
    },
    {
      name: '全部制品失败',
      mutate(event: CountryOutageAgentEvent) {
        event.artifacts = [
          {
            format: 'markdown',
            status: 'failed',
            code: 'render_failed',
            message: 'Markdown 生成失败',
          },
          {
            format: 'pdf',
            status: 'failed',
            code: 'render_failed',
            message: 'PDF 生成失败',
          },
        ]
      },
    },
  ])('$name 运行时结构不合法时不得发布报告', ({ mutate }) => {
    const event = completedEvent()
    mutate(event)
    expect(validate(event)).toMatchObject({
      accepted: false,
      code: 'report_protocol_identity_conflict',
    })
  })

  it.each([
    {
      field: 'artifact',
      mutate(report: CountryOutageReportDocument) {
        report.artifactId = `report_${'3'.repeat(32)}`
      },
    },
    {
      field: 'factSet',
      mutate(report: CountryOutageReportDocument) {
        report.factSetId = `facts_${'4'.repeat(32)}`
      },
    },
    {
      field: '正文内容',
      mutate(report: CountryOutageReportDocument) {
        report.reportContentSha256 = 'e'.repeat(64)
      },
    },
  ])(
    '同一 report 重放时 $field 身份不得漂移',
    ({ mutate }) => {
      const retained = reportDocument()
      const retainedBeforeValidation = structuredClone(retained)
      const event = completedEvent()
      mutate(event.report!)
      if (event.report!.artifactId !== retained.artifactId) {
        for (const artifact of event.artifacts ?? []) {
          if (artifact.status === 'ready') {
            artifact.artifact_id = event.report!.artifactId
          }
        }
      }

      const result = validateCompletedCountryOutageReportEvent(event, {
        expectedReportId: 'report-1',
        expectedRunId: 'run-1',
        binding: freezeCountryOutageReportBinding(
          validObservation(),
          EVENT_REFERENCE,
        ),
        retainedReport: retained,
      })
      expect(result.accepted).toBe(false)
      expect(result.message).toContain('artifact/factSet/正文身份发生漂移')
      expect(retained).toEqual(retainedBeforeValidation)
    },
  )

  it('同 ID 与声明哈希的旧报告重放若 draft 变化仍拒绝且保留旧对象', () => {
    const retainedEvent = completedEvent()
    const retainedReport = retainedEvent.report!
    const retainedArtifacts = retainedEvent.artifacts!
    const retainedReportBefore = structuredClone(retainedReport)
    const retainedArtifactsBefore = structuredClone(retainedArtifacts)
    const replay = completedEvent()
    replay.report!.draft.summary.text = '同一声明哈希下被篡改的报告摘要'

    const result = validateCompletedCountryOutageReportEvent(replay, {
      expectedReportId: 'report-1',
      expectedRunId: 'run-1',
      binding: freezeCountryOutageReportBinding(
        validObservation(),
        EVENT_REFERENCE,
      ),
      retainedReport,
      retainedArtifacts,
    })
    expect(result).toMatchObject({
      accepted: false,
      code: 'report_protocol_identity_conflict',
    })
    expect(result.message).toContain('完整 document 内容发生漂移')
    expect(retainedReport).toEqual(retainedReportBefore)
    expect(retainedArtifacts).toEqual(retainedArtifactsBefore)
  })

  it('旧报告完整 document 与两种制品 metadata 完全一致时允许幂等重放', () => {
    const retained = completedEvent()
    const replay = completedEvent()
    expect(validateCompletedCountryOutageReportEvent(replay, {
      expectedReportId: 'report-1',
      expectedRunId: 'run-1',
      binding: freezeCountryOutageReportBinding(
        validObservation(),
        EVENT_REFERENCE,
      ),
      retainedReport: retained.report,
      retainedArtifacts: retained.artifacts,
    })).toEqual({
      accepted: true,
      code: 'accepted',
      message: '',
    })
  })

  it.each([
    {
      field: 'SHA-256',
      mutate(event: CountryOutageAgentEvent) {
        const markdown = event.artifacts?.[0]
        if (markdown?.status === 'ready') {
          markdown.sha256 = 'e'.repeat(64)
        }
      },
    },
    {
      field: 'filename',
      mutate(event: CountryOutageAgentEvent) {
        const markdown = event.artifacts?.[0]
        if (markdown?.status === 'ready') {
          markdown.filename = 'IR-replayed.md'
        }
      },
    },
  ])(
    '旧报告重放的 ready 文件 $field 变化时拒绝且保留旧制品',
    ({ mutate }) => {
      const retainedEvent = completedEvent()
      const retainedReport = retainedEvent.report!
      const retainedArtifacts = retainedEvent.artifacts!
      const retainedReportBefore = structuredClone(retainedReport)
      const retainedArtifactsBefore = structuredClone(retainedArtifacts)
      const replay = completedEvent()
      mutate(replay)

      const result = validateCompletedCountryOutageReportEvent(replay, {
        expectedReportId: 'report-1',
        expectedRunId: 'run-1',
        binding: freezeCountryOutageReportBinding(
          validObservation(),
          EVENT_REFERENCE,
        ),
        retainedReport,
        retainedArtifacts,
      })
      expect(result).toMatchObject({
        accepted: false,
        code: 'report_protocol_identity_conflict',
      })
      expect(result.message).toContain('下载制品 metadata 发生漂移')
      expect(retainedReport).toEqual(retainedReportBefore)
      expect(retainedArtifacts).toEqual(retainedArtifactsBefore)
    },
  )
})

describe('国家中断观测刷新运行时仲裁', () => {
  it('页面身份允许 published 但尚无报告冻结条件的合法观测', () => {
    const incomplete = validObservation()
    incomplete.data_through = null
    incomplete.cohort_id = null
    incomplete.cohort = null
    incomplete.window_start_utc = null
    incomplete.window_end_utc = null
    incomplete.observation_scope.window_start_utc = null
    incomplete.observation_scope.window_end_utc = null
    Object.assign(incomplete.audit!, {
      data_through: null,
      cohort_id: null,
      window_start_utc: null,
      window_end_utc: null,
    })

    expect(validateCountryOutagePageObservationIdentity(
      incomplete,
      EVENT_REFERENCE,
    ).accepted).toBe(true)
    expect(() => freezeCountryOutageReportBinding(
      incomplete,
      EVENT_REFERENCE,
    )).toThrow('尚未形成可冻结的报告快照')

    incomplete.publication_state = 'candidate'
    incomplete.audit!.publication_state = 'candidate'
    expect(validateCountryOutagePageObservationIdentity(
      incomplete,
      EVENT_REFERENCE,
    )).toMatchObject({
      accepted: false,
      code: 'invalid_identity',
    })
  })

  it('按历史顺序接受 correction 精确升版并拒绝 revision 回退', () => {
    expect(decideCountryOutageObservationRefresh(
      observationAtRevision(1),
      observationAtRevision(2),
      EVENT_REFERENCE,
    ).accepted).toBe(true)

    const regression = decideCountryOutageObservationRefresh(
      observationAtRevision(2),
      observationAtRevision(1),
      EVENT_REFERENCE,
    )
    expect(regression).toMatchObject({
      accepted: false,
      code: 'revision_regression',
    })
  })

  it.each(['append', 'status'] as const)(
    '接受 revision 不变但 publication 单调推进的 %s 发布',
    (kind) => {
      expect(decideCountryOutageObservationRefresh(
        validObservation(),
        sameRevisionPublication(kind),
        EVENT_REFERENCE,
      )).toEqual({
        accepted: true,
        code: 'accepted',
        message: '',
      })
    },
  )

  it('兼容真实首次 append 中首项缺失 publication_kind 的旧 registry 迁移历史', () => {
    const incoming = sameRevisionPublication('append')
    incoming.audit!.revision_history![0]!.publication_kind = null

    expect(decideCountryOutageObservationRefresh(
      validObservation(),
      incoming,
      EVENT_REFERENCE,
    )).toEqual({
      accepted: true,
      code: 'accepted',
      message: '',
    })
  })

  it('旧 publication 的迟到响应不能覆盖已经接受的新 publication', () => {
    const current = sameRevisionPublication('append')
    const stale = validObservation()
    stale.audit!.revision_history = structuredClone(
      current.audit!.revision_history,
    )
    expect(decideCountryOutageObservationRefresh(
      current,
      stale,
      EVENT_REFERENCE,
    )).toMatchObject({
      accepted: false,
      code: 'publication_regression',
    })
  })

  it('发布历史未知 kind、重复 ID 或 append 改 cohort 均失败关闭', () => {
    const unknownKind = sameRevisionPublication('append')
    unknownKind.audit!.revision_history![1]!.publication_kind = 'replace'
    expect(decideCountryOutageObservationRefresh(
      validObservation(),
      unknownKind,
      EVENT_REFERENCE,
    )).toMatchObject({
      accepted: false,
      code: 'publication_regression',
    })

    const duplicate = sameRevisionPublication('append')
    duplicate.audit!.revision_history![1]!.publication_id = 'publication-v1'
    expect(decideCountryOutageObservationRefresh(
      validObservation(),
      duplicate,
      EVENT_REFERENCE,
    )).toMatchObject({
      accepted: false,
      code: 'publication_regression',
    })

    const cohortDrift = sameRevisionPublication('append')
    cohortDrift.cohort_id = 'cohort-other'
    cohortDrift.cohort!.cohort_id = 'cohort-other'
    cohortDrift.audit!.cohort_id = 'cohort-other'
    expect(decideCountryOutageObservationRefresh(
      validObservation(),
      cohortDrift,
      EVENT_REFERENCE,
    )).toMatchObject({
      accepted: false,
      code: 'publication_identity_conflict',
    })
  })

  it.each([
    {
      field: 'dataThrough',
      mutate(value: EventObservation) {
        value.data_through = '2026-02-28T15:05:00Z'
        value.audit!.data_through = '2026-02-28T15:05:00Z'
      },
    },
    {
      field: 'cohortId',
      mutate(value: EventObservation) {
        value.cohort_id = 'cohort-drift'
        value.cohort!.cohort_id = 'cohort-drift'
        value.audit!.cohort_id = 'cohort-drift'
      },
    },
    {
      field: 'windowStartUtc',
      mutate(value: EventObservation) {
        value.window_start_utc = '2026-02-28T10:00:00Z'
        value.observation_scope.window_start_utc =
          '2026-02-28T10:00:00Z'
        value.audit!.window_start_utc = '2026-02-28T10:00:00Z'
      },
    },
    {
      field: 'isFinal',
      mutate(value: EventObservation) {
        value.is_final = false
        value.audit!.is_final = false
      },
    },
    {
      field: 'observationState',
      mutate(value: EventObservation) {
        value.observation_state = 'state_complete'
        value.audit!.observation_state = 'state_complete'
      },
    },
    {
      field: 'dataMode',
      mutate(value: EventObservation) {
        value.data_mode = 'live'
        value.audit!.data_mode = 'live'
      },
    },
    {
      field: 'updatedAt',
      mutate(value: EventObservation) {
        value.updated_at = '2026-02-28T15:02:00Z'
        value.audit!.updated_at = '2026-02-28T15:02:00Z'
      },
    },
    {
      field: 'processingStatus',
      mutate(value: EventObservation) {
        value.processing_status = {
          ...value.processing_status!,
          reason: 'same revision mutated',
        }
        value.audit!.processing_status = structuredClone(
          value.processing_status,
        )
      },
    },
    {
      field: 'missingSlotCount',
      mutate(value: EventObservation) {
        value.missing_slot_count = 1
        value.audit!.missing_slot_count = 1
      },
    },
  ])(
    '同 revision 的 $field 漂移会被显式检测',
    ({ field, mutate }) => {
      const incoming = validObservation()
      mutate(incoming)
      const result = decideCountryOutageObservationRefresh(
        validObservation(),
        incoming,
        EVENT_REFERENCE,
      )
      expect(result).toMatchObject({
        accepted: false,
        code: 'same_revision_identity_drift',
      })
      expect(result.message).toContain(field)
    },
  )

  it('其他事件、国家或非唯一 RRC25 刷新均不覆盖当前观测', () => {
    const otherEvent = validObservation()
    otherEvent.event_identity.incident_id = 'incident-other'
    otherEvent.incident_id = 'incident-other'
    otherEvent.audit!.incident_id = 'incident-other'
    expect(decideCountryOutageObservationRefresh(
      validObservation(),
      otherEvent,
      EVENT_REFERENCE,
    )).toMatchObject({
      accepted: false,
      code: 'different_event',
    })

    const otherCollector = validObservation()
    otherCollector.observation_scope.collector_id = 'rrc24'
    expect(decideCountryOutageObservationRefresh(
      validObservation(),
      otherCollector,
      EVENT_REFERENCE,
    )).toMatchObject({
      accepted: false,
      code: 'invalid_identity',
    })
  })
})

describe('国家中断观测请求门闩', () => {
  it('同一路由的刷新严格单飞，完成后才允许下一次', () => {
    const gate = new CountryOutageObservationRequestGate()
    gate.setReference(EVENT_REFERENCE)
    const first = gate.beginRefresh()
    expect(first).not.toBeNull()
    expect(gate.beginRefresh()).toBeNull()
    gate.finish(first!)
    expect(gate.beginRefresh()).not.toBeNull()
  })

  it('路由变化立即废弃旧响应，旧 finally 不能释放新请求', () => {
    const gate = new CountryOutageObservationRequestGate()
    gate.setReference(EVENT_REFERENCE)
    const oldRefresh = gate.beginRefresh()!

    const nextReference =
      'country_outage/2026-02-27 09:12:32/US/2/r'
    gate.setReference(nextReference)
    const currentInitial = gate.beginInitial()
    expect(gate.isCurrent(oldRefresh)).toBe(false)
    expect(gate.isCurrent(currentInitial)).toBe(true)

    const currentRefresh = gate.beginRefresh()
    expect(currentRefresh).not.toBeNull()
    gate.finish(oldRefresh)
    expect(gate.beginRefresh()).toBeNull()
    gate.finish(currentRefresh!)
    expect(gate.beginRefresh()).not.toBeNull()
  })

  it('同一路由重新加载也会按请求序号忽略先返回的旧响应', () => {
    const gate = new CountryOutageObservationRequestGate()
    gate.setReference(EVENT_REFERENCE)
    const first = gate.beginInitial()
    const retry = gate.beginInitial()
    expect(gate.isCurrent(first)).toBe(false)
    expect(gate.isCurrent(retry)).toBe(true)
  })

  it('组件卸载失效门闩后，迟到的初始响应不得恢复页面轮询', () => {
    const gate = new CountryOutageObservationRequestGate()
    gate.setReference(EVENT_REFERENCE)
    const initial = gate.beginInitial()
    gate.invalidate()
    expect(gate.isCurrent(initial)).toBe(false)
    expect(gate.beginRefresh()).not.toBeNull()
  })
})

describe('国家中断报告启动请求 epoch', () => {
  it('新请求会中止旧请求，旧 finally 不能完成新请求', () => {
    const gate = new CountryOutageAbortableRequestGate()
    const first = gate.begin()
    const second = gate.begin()

    expect(first.controller.signal.aborted).toBe(true)
    expect(gate.isCurrent(first)).toBe(false)
    expect(gate.finish(first)).toBe(false)
    expect(gate.isCurrent(second)).toBe(true)
    expect(gate.finish(second)).toBe(true)
  })

  it('显式失效后迟到成功响应不再拥有安装报告身份的资格', () => {
    const gate = new CountryOutageAbortableRequestGate()
    const request = gate.begin()
    gate.invalidate()
    expect(request.controller.signal.aborted).toBe(true)
    expect(gate.isCurrent(request)).toBe(false)
  })
})

describe('国家中断问题状态身份', () => {
  const entries = [
    { runId: 'run-a', questionId: 'q-a' },
    { runId: 'run-b', questionId: 'q-b' },
  ]

  it('只有 runId 与 questionId 同时指向同一记录才允许更新', () => {
    expect(matchCountryOutageQuestionEvent(
      entries,
      'run-a',
      'q-a',
    )).toMatchObject({
      accepted: true,
      action: 'update',
      index: 0,
    })
    expect(matchCountryOutageQuestionEvent(
      entries,
      'run-a',
      'q-b',
    )).toMatchObject({
      accepted: false,
      action: 'reject',
    })
    expect(matchCountryOutageQuestionEvent(
      entries,
      'run-new',
      'q-new',
    )).toMatchObject({
      accepted: true,
      action: 'create',
    })
  })

  it.each([
    ['incident_id', 'incident-other'],
    ['publication_id', 'publication-other'],
    ['revision', 2],
    ['data_through', '2026-02-28T15:05:00Z'],
    ['fact_set_id', `facts_${'9'.repeat(32)}`],
    ['cohort_id', 'cohort-other'],
    ['country_code', 'US'],
    ['collector_id', 'rrc24'],
    ['window_start_utc', '2026-02-28T10:00:00Z'],
    ['window_end_utc', '2026-02-28T15:05:00Z'],
  ] as const)(
    '外部附录 frozen_binding 的 %s 跨快照时不得进入当前报告',
    (field, forgedValue) => {
      const currentReport = reportDocument()
      const appendix = externalAppendix(currentReport)
      expect(appendixMatchesCurrentReport(
        appendix,
        currentReport,
      )).toBe(true)

      const forged = structuredClone(appendix)
      const binding = forged.frozen_binding as unknown as
        Record<string, unknown>
      binding[field] = forgedValue
      expect(appendixMatchesCurrentReport(
        forged,
        currentReport,
      )).toBe(false)
    },
  )

  it('外部附录缺少 frozen_binding 时失败关闭', () => {
    const currentReport = reportDocument()
    const appendix = externalAppendix(currentReport)
    delete appendix.frozen_binding
    expect(appendixMatchesCurrentReport(
      appendix,
      currentReport,
    )).toBe(false)
  })

  it('completed 正式回答必须绑定当前报告的完整快照', () => {
    const currentReport = reportDocument()
    const event = completedEvent()
    event.event_type = 'question_state'
    event.question = {
      question_id: 'q-a',
      number: 1,
      question: '窗口结束时恢复了吗？',
      evidence_mode: 'domeye_only',
      state: 'completed',
      answer: {
        text: '窗口结束时仍未回到起点水平。',
        evidence_refs: ['overview:/observation_scope'],
        limitations: [],
        snapshot: structuredClone(currentReport.snapshot),
      },
    }
    expect(validateCountryOutageQuestionAnswerSnapshot(
      event,
      currentReport,
    ).accepted).toBe(true)

    event.question.answer!.snapshot!.publicationId = 'publication-forged'
    expect(validateCountryOutageQuestionAnswerSnapshot(
      event,
      currentReport,
    )).toMatchObject({
      accepted: false,
      code: 'question_protocol_identity_conflict',
    })

    event.phase = 'collecting_external'
    expect(validateCountryOutageQuestionAnswerSnapshot(
      event,
      currentReport,
    )).toMatchObject({
      accepted: false,
      code: 'question_protocol_identity_conflict',
    })

    delete event.question.answer
    event.phase = 'completed'
    expect(validateCountryOutageQuestionAnswerSnapshot(
      event,
      currentReport,
    )).toMatchObject({
      accepted: false,
      code: 'question_protocol_identity_conflict',
    })

    event.phase = 'collecting_external'
    expect(validateCountryOutageQuestionAnswerSnapshot(
      event,
      currentReport,
    )).toMatchObject({
      accepted: true,
      code: 'accepted',
    })
  })
})
