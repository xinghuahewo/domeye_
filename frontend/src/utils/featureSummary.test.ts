import { describe, expect, it } from 'vitest'

import type { FeaturePoint } from '@/types/api'
import { summarizeFeatureWindow } from './featureSummary'

describe('特征窗口摘要', () => {
  it('按窗口总量计算报文构成并定位更新峰值', () => {
    const points: FeaturePoint[] = [
      {
        time: '2026-03-31 20:00:00',
        announce: 100,
        withdraw: 20,
        ipv4Prefixes: null,
        ipv6Prefixes: null,
        ipv4Addresses: null,
      },
      {
        time: '2026-03-31 20:03:00',
        announce: 200,
        withdraw: 25,
        ipv4Prefixes: null,
        ipv6Prefixes: null,
        ipv4Addresses: null,
      },
      {
        time: '2026-03-31 20:06:00',
        announce: 50,
        withdraw: 5,
        ipv4Prefixes: null,
        ipv6Prefixes: null,
        ipv4Addresses: null,
      },
    ]

    expect(summarizeFeatureWindow(points)).toEqual({
      announceTotal: 350,
      withdrawTotal: 50,
      updateTotal: 400,
      withdrawRate: 0.125,
      peakUpdates: 225,
      peakTime: '2026-03-31 20:03:00',
      observedPoints: 3,
    })
  })

  it('保留缺失语义，并在分母为零时不伪造撤回率', () => {
    const points: FeaturePoint[] = [
      {
        time: '2026-03-31 20:00:00',
        announce: null,
        withdraw: null,
        ipv4Prefixes: null,
        ipv6Prefixes: null,
        ipv4Addresses: null,
      },
      {
        time: '2026-03-31 20:03:00',
        announce: 0,
        withdraw: 0,
        ipv4Prefixes: null,
        ipv6Prefixes: null,
        ipv4Addresses: null,
      },
    ]

    expect(summarizeFeatureWindow(points)).toEqual({
      announceTotal: 0,
      withdrawTotal: 0,
      updateTotal: 0,
      withdrawRate: null,
      peakUpdates: 0,
      peakTime: '2026-03-31 20:03:00',
      observedPoints: 1,
    })
  })
})
