import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const source = readFileSync(new URL('./EventDetailPage.vue', import.meta.url), 'utf8')

describe('国家中断事件页 W5 入口', () => {
  it('只在已有冻结 publication 与 revision 时构造组合调查入口', () => {
    expect(source).toContain('const investigationEntry = computed')
    expect(source).toContain('publication_id: identity.publication_id')
    expect(source).toContain('revision: String(identity.revision)')
    expect(source).toContain('name: \'country-outage-investigation\'')
  })

  it('清楚标注本地隔离、RRC25 和 P2.1 fan-out 延期', () => {
    expect(source).toContain('P2-S1 W5 · LOCAL ISOLATED')
    expect(source).toContain('RRC25 publication')
    expect(source).toContain('P2.1 动态 fan-out 继续延期')
    expect(source).toContain('不表示生产部署')
  })
})
