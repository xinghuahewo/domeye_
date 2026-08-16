import { describe, expect, it } from 'vitest'

import type { EventObservation } from '@/types/api'
import {
  createDurationBuckets,
  createEventObservationPresentation,
  formatInterval,
  formatSlotDuration,
} from '@/utils/eventObservationTemplate'

function observationFixture(
  overrides: {
    countryCode?: string
    countryName?: string
    collectorId?: string
    collectorCount?: number
    timezone?: string
    intervalSeconds?: number
    expectedObservationCount?: number
  } = {},
): EventObservation {
  return {
    event_identity: {
      country_code: overrides.countryCode ?? 'IR',
      country_name: overrides.countryName ?? '伊朗',
      display_name: 'BGP 路由观测',
      event_type: 'country_outage',
    },
    observation_scope: {
      collector_id: overrides.collectorId ?? 'rrc25',
      collector_count: overrides.collectorCount ?? 1,
      timezone: overrides.timezone ?? 'Asia/Shanghai',
      interval_seconds: overrides.intervalSeconds ?? 300,
      expected_observation_count: overrides.expectedObservationCount ?? 60,
    },
  } as EventObservation
}

describe('event observation template presentation', () => {
  it('keeps the Iran instance entirely data-driven', () => {
    const presentation = createEventObservationPresentation(observationFixture())

    expect(presentation.mastheadKicker).toBe('BGP DATA OBSERVATORY · IR / RRC25')
    expect(presentation.originScopeLabel).toBe('伊朗 origin 归属')
    expect(presentation.collectorCountLabel).toBe('单 COLLECTOR')
    expect(presentation.intervalTag).toBe('5 分钟粒度')
    expect(presentation.localTimeLabel).toBe('北京时间')
  })

  it('replaces country, collector, timezone and interval without component changes', () => {
    const presentation = createEventObservationPresentation(observationFixture({
      countryCode: 'DE',
      countryName: '德国',
      collectorId: 'rrc00',
      collectorCount: 2,
      timezone: 'UTC',
      intervalSeconds: 900,
      expectedObservationCount: 24,
    }))

    expect(presentation.mastheadKicker).toBe('BGP DATA OBSERVATORY · DE / RRC00')
    expect(presentation.originScopeLabel).toBe('德国 origin 归属')
    expect(presentation.collectorScopeLabel).toBe('RRC00 全量')
    expect(presentation.collectorCountLabel).toBe('2 COLLECTORS')
    expect(presentation.intervalTag).toBe('15 分钟粒度')
    expect(presentation.localTimeLabel).toBe('UTC')
    expect(presentation.countryMessageDescription).toContain('归入德国的报文')
  })
})

describe('event observation interval helpers', () => {
  it('formats arbitrary observation intervals and slot durations', () => {
    expect(formatInterval(300)).toBe('5 分钟')
    expect(formatInterval(3600)).toBe('1 小时')
    expect(formatSlotDuration(3, 900)).toBe('45 分钟')
    expect(formatSlotDuration(5, 900)).toBe('1 小时 15 分钟')
  })

  it('keeps the original six duration ranges for a 60-slot window', () => {
    expect(createDurationBuckets(300, 60).map((bucket) => bucket.label)).toEqual([
      '0',
      '5 分钟–30 分钟',
      '35 分钟–1 小时',
      '1 小时 5 分钟–2 小时',
      '2 小时 5 分钟–3 小时',
      '3 小时 5 分钟–4 小时',
      '4 小时 5 分钟–5 小时',
    ])
  })
})
