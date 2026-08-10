import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

const source = readFileSync(
  new URL('./CountryOutageChatPage.vue', import.meta.url),
  'utf8',
)

describe('国家中断 P1 聊天页用户效果', () => {
  it('始终展示事件、publication、revision、RRC25、data-through 和结束未知', () => {
    for (const anchor of [
      'Publication',
      'Revision',
      'RRC25',
      '数据截至',
      '事件结束未知',
    ]) expect(source).toContain(anchor)
  })

  it('将回答状态、UserGoalPlan、GroundingPlan、Tool、Evidence 和 DialogState 分层展示', () => {
    for (const anchor of [
      'statusLabel(result.answerability)',
      'UserGoalPlan',
      'GroundingPlan',
      'Tool / Operator',
      '选中证据',
      'DialogState',
      'state_receipt.before',
      'state_receipt.after',
    ]) expect(source).toContain(anchor)
  })

  it('保留两个高风险 IP 原问题、多意图问题和中途取消入口', () => {
    expect(source).toContain("'IP地址变化情况'")
    expect(source).toContain("'IP地址变化趋势'")
    expect(source).toContain("'现在还有多少前缀不可见，是不是全国都断了'")
    expect(source).toContain('cancelCountryOutageChatTurn')
    expect(source).toContain('取消本轮')
  })

  it('在窄屏将核对面板移到对话下方，不隐藏证据链', () => {
    expect(source).toContain('@media (max-width: 820px)')
    expect(source).toContain('.chat-workbench { display: flex; flex-direction: column; }')
    expect(source).toContain('.audit-panel { height: auto; overflow: visible;')
  })
})
