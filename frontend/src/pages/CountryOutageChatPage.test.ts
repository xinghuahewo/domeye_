import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

const source = readFileSync(new URL('./CountryOutageChatPage.vue', import.meta.url), 'utf8')
const generalPage = readFileSync(
  new URL('../components/CountryOutageGeneralPage.vue', import.meta.url),
  'utf8',
)
const router = readFileSync(new URL('../router/index.ts', import.meta.url), 'utf8')

describe('P1 事件绑定聊天页面效果', () => {
  it('从事件页显式进入并持续展示冻结身份', () => {
    expect(generalPage).toContain("name: 'country-outage-chat'")
    expect(generalPage).toContain('publication_id: props.page.resolution.publication_id')
    expect(generalPage).toContain('revision: String(props.page.resolution.revision)')
    expect(router).toContain("path: '/events/chat'")
    for (const label of ['PUBLICATION', 'REVISION', 'COLLECTOR', 'DATA THROUGH', 'FINALITY']) {
      expect(source).toContain(label)
    }
  })

  it('呈现五级回答、证据、限制、未知和结构化上下文', () => {
    for (const label of ['已回答', '部分回答', '需要澄清', '当前不支持', '数据无效']) {
      expect(source).toContain(label)
    }
    expect(source).toContain('字段级证据')
    expect(source).toContain('<h4>限制</h4>')
    expect(source).toContain('<h4>未知</h4>')
    expect(source).toContain('contextChips')
    expect(source).toContain('last_committed_turn_number')
  })

  it('支持键盘发送、取消、重连恢复和到期后新建会话', () => {
    expect(source).toContain("event.key === 'Enter'")
    expect(source).toContain('Shift+Enter 换行')
    expect(source).toContain('取消本轮')
    expect(source).toContain('localStorage.getItem')
    expect(source).toContain('getConversation(savedId)')
    expect(source).toContain('上一短期会话已到期或因服务重启不可恢复')
    expect(source).toContain('role="status"')
    expect(source).toContain('以当前事件新建会话')
    expect(source).toContain('aria-live="polite"')
  })

  it('明确保持 P1 边界且不暴露外部证据开关', () => {
    expect(source).toContain('不接入 OONI / IODA / Cloudflare')
    expect(source).toContain('不判断真实用户影响、责任或原因')
    expect(source).not.toContain('domeye_plus_external')
    expect(source).not.toContain('Evidence Graph')
    expect(source).not.toContain('RCA')
  })
})
