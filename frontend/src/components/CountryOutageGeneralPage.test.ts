import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

const source = readFileSync(
  new URL('./CountryOutageGeneralPage.vue', import.meta.url),
  'utf8',
)
const template = source.slice(source.indexOf('<template>'), source.indexOf('</template>'))
const asnPage = readFileSync(new URL('../pages/AsnPage.vue', import.meta.url), 'utf8')

describe('国家中断通用观测页用户效果', () => {
  it('keeps the agreed information order', () => {
    const headings = [
      '前缀中断数量变化',
      'AS 中断数量变化',
      'IP 地址变化趋势',
      '哪些 AS 出现了路由不可见',
      '实际路径中关联了哪些网络',
    ]
    let cursor = -1
    for (const heading of headings) {
      const next = template.indexOf(heading)
      expect(next, heading).toBeGreaterThan(cursor)
      cursor = next
    }
  })

  it('does not expose internal release vocabulary on the ordinary page', () => {
    for (const forbidden of [
      'PRODUCT',
      'PUBLICATION',
      'REVISION',
      'DATA THROUGH',
      'Prefix×VP',
      '同一冻结制品',
      'incident_go_',
      'trend_product_',
      'observation_publication_',
    ]) expect(template).not.toContain(forbidden)
  })

  it('links the affected AS to the existing profile with the exact event window', () => {
    expect(source).toContain("name: 'asn-detail'")
    expect(source).toContain('event_start: props.page.resolution.window_start_utc')
    expect(source).toContain('event_end: props.page.resolution.window_end_utc')
    expect(source).toContain("return_anchor: 'affected-as'")
    expect(asnPage).toContain('按国家中断事件窗口查看')
    expect(asnPage).toContain('query.start = toInputTime(eventContext.value.startDate)')
    expect(asnPage).toContain('query.end = toInputTime(eventContext.value.endDate)')
    expect(asnPage).toContain('Boolean(eventContext.value)')
    expect(asnPage).toContain('eventContext.value?.reference')
    expect(asnPage).toContain('cursor += 5 * 60 * 1000')
    expect(asnPage).toContain('announce: null')
    expect(asnPage).toContain('返回事件中的相关 AS')
  })

  it('keeps drilldowns bounded and does not request the audit endpoint', () => {
    expect(source).toContain('const asPageSize = 20')
    expect(source).toContain('const pathPageSize = 15')
    expect(source).not.toContain('/audit')
    expect(source).toContain('查看关联路径')
    expect(source).toContain('不可见独立方向峰值')
    expect(source).toContain('同期中断前缀峰值')
    expect(source).toContain('同期 IPv4 地址量峰值')
    expect(source).toContain('同期 IPv6 /48 峰值')
  })

  it('opens P1 chat with the exact current event publication identity', () => {
    expect(source).toContain("name: 'country-outage-chat'")
    expect(source).toContain('ref: props.reference')
    expect(source).toContain('publication_id: props.page.resolution.publication_id')
    expect(source).toContain('revision: String(props.page.resolution.revision)')
    expect(source).toContain('围绕此事件提问')
  })
})
