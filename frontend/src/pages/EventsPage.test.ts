import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

const source = readFileSync(new URL('./EventsPage.vue', import.meta.url), 'utf8')

describe('事件页快捷时间范围', () => {
  it('提供三个固定入口并显示机器合同允许的完整范围', () => {
    expect(source).toContain("label: '近 7 天'")
    expect(source).toContain("label: '近 30 天'")
    expect(source).toContain("label: '整个数据窗口'")
    expect(source).toContain('可选范围 {{ minimumDate }} — {{ maximumDate }}')
  })

  it('相对范围绑定构建时固定窗口，不使用浏览器当前日期', () => {
    expect(source).toContain('recentDateRange(preset.days, import.meta.env)')
    expect(source).not.toContain('recentDateRange(preset.days)')
    expect(source).toContain("? { start: minimumDate ?? '', end: maximumDate ?? '' }")
  })

  it('点击入口后更新日期、回到第一页并立即查询', () => {
    expect(source).toContain('@click="applyDatePreset(preset)"')
    expect(source).toContain('filters.startDate = range.start')
    expect(source).toContain('filters.endDate = range.end')
    expect(source).toContain('void load(true)')
  })

  it('以按钮组和 aria-pressed 暴露当前选中范围', () => {
    expect(source).toContain('role="group" aria-label="快捷时间范围"')
    expect(source).toContain(':aria-pressed="activeDatePreset === preset.id"')
    expect(source).toContain(":class=\"{ 'is-active': activeDatePreset === preset.id }\"")
  })
})
