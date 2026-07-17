import { describe, expect, it } from 'vitest'

import { buildDetailEndpoint, normalizeEventPage, normalizeTime, parseDetailUrl } from './normalize'

describe('API 数据归一化', () => {
  it('处理字符串计数、换行时间和空结束时间', () => {
    const page = normalizeEventPage({
      total_page: 10,
      record_count: '100',
      data: [{
        event_type: '前缀劫持',
        level: 'high',
        start_time: '2026-07-17\n08:00:00',
        end_time: 'None',
        detail_url: 'hijack/2026-07-17 08:00:00/1.2.3.0-24/1/r',
      }],
    })

    expect(page.recordCount).toBe(100)
    expect(page.data[0]?.startTime).toBe('2026-07-17 08:00:00')
    expect(page.data[0]?.endTime).toBeNull()
  })

  it('解析六类核心详情引用', () => {
    const refs = [
      'hijack/2026-07-17 08:00:00/1.2.3.0-24/1/r',
      'sub_hijack/2026-07-17 08:00:00/1.2.3.0-25/2/r',
      'prefix_outage/2026-07-17 08:00:00/1.2.3.0-24/3/r',
      'as_outage/2026-07-17 08:00:00/4134/4/r',
      'country_outage/2026-07-17 08:00:00/CN/5/r',
      'leak/2026-07-17 08:00:00/2001:db8::-32/6/r',
    ]

    for (const ref of refs) {
      const parsed = parseDetailUrl(ref)
      expect(parsed).not.toBeNull()
      expect(buildDetailEndpoint(parsed!)).toContain('%20')
    }
  })

  it('拒绝非核心详情并清理无效时间', () => {
    expect(parseDetailUrl('boundary_outage/2026-07-17 08:00:00/a/1/r')).toBeNull()
    expect(normalizeTime('NaT')).toBeNull()
    expect(normalizeTime('-')).toBeNull()
  })
})
