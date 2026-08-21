import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const source = readFileSync(new URL('./EventDetailPage.vue', import.meta.url), 'utf8')
const routerSource = readFileSync(new URL('../router/index.ts', import.meta.url), 'utf8')

describe('国家中断事件页 Legacy Agent 入口退役', () => {
  it('保留数据观测与新架构事件问答，不再渲染旧报告或组合调查', () => {
    expect(source).toContain('CountryOutageDashboard')
    expect(source).toContain('CountryOutageGeneralPage')
    expect(source).not.toContain('CountryOutageReportWorkbench')
    expect(source).not.toContain('country-outage-investigation')
    expect(source).not.toContain('创建组合调查')
    expect(source).not.toContain('报告与追问')
    expect(source).not.toContain('P2-S1 W5')
  })

  it('正式路由只保留新架构事件问答入口', () => {
    expect(routerSource).toContain("path: '/events/chat'")
    expect(routerSource).not.toContain("path: '/events/investigation'")
    expect(routerSource).not.toContain("name: 'country-outage-investigation'")
  })
})
