import { describe, expect, it } from 'vitest'

import { formatChartTime } from '@/utils/chartTime'

describe('LineChart 时间显示', () => {
  it('固定使用 Asia/Shanghai，而不是浏览器本地时区', () => {
    const utc = '2026-01-31T16:00:00Z'
    expect(formatChartTime(utc, 'Asia/Shanghai', true)).toBe('2026-02-01 00:00')
    expect(formatChartTime(utc, 'Asia/Shanghai')).toBe('02-01 00:00')
  })
})
