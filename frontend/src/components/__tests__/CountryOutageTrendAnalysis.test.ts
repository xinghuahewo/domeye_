import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'


const source = readFileSync(
  new URL('../CountryOutageTrendAnalysis.vue', import.meta.url),
  'utf8',
)

describe('国家中断趋势阅读旅程', () => {
  it('只渲染后端冻结制品，不在组件内重算阶段和数字', () => {
    expect(source).toContain('props.product.profile.analysis.phases')
    expect(source).toContain('props.product.profile.analysis.derived_facts')
    expect(source).not.toMatch(/change.?point|detectPhase|classifyPattern/i)
  })

  it('每条结论同时显示 Evidence、Limitation 和 Unknown', () => {
    expect(source).toContain('<h4>Evidence</h4>')
    expect(source).toContain('<h4>Limitation</h4>')
    expect(source).toContain('<h4>Unknown</h4>')
    expect(source).toContain('selectedClaim.value?.evidence_refs')
    expect(source).toContain('selectedClaim.value?.limitation_refs')
    expect(source).toContain('selectedClaim.value?.unknown_refs')
  })

  it('显示产品、发布、修订和数据截止身份', () => {
    expect(source).toContain('product.product_id')
    expect(source).toContain('product.snapshot.publication_id')
    expect(source).toContain('product.snapshot.revision')
    expect(source).toContain('product.snapshot.data_through')
  })

  it('明确展示控制面和窗外未知边界', () => {
    expect(source).toContain('RRC25 CONTROL-PLANE CLAIM')
    expect(source).toContain('不是历史正常带')
    expect(source).toContain('不包含原因、攻击、用户影响、责任或窗口外完全恢复判断')
  })
})

