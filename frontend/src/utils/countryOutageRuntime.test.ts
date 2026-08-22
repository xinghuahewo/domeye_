import { describe, expect, it } from 'vitest'

import type { EventObservation } from '@/types/api'
import {
  canonicalCountryOutageEventReference,
  CountryOutageObservationRequestGate,
  decideCountryOutageObservationRefresh,
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

describe('国家中断观测刷新运行时仲裁', () => {
  it('规范化查询参数中的事件引用', () => {
    expect(canonicalCountryOutageEventReference(
      `?ref=${EVENT_REFERENCE.replace(' ', '+')}`,
    )).toBe(EVENT_REFERENCE)
  })

  it('页面身份允许尚未补齐数据截止点、cohort 与窗口的合法 published 观测', () => {
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
