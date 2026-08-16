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

  it('S1 同候选受控单轮旅程呈现事实、证据、限制、未知和失败关闭', () => {
    expect(source).toContain('确定性事件概览')
    expect(source).toContain("controlled_goal: 'event_summary'")
    expect(source).toContain('不冒充开放自然语言规划')
    expect(source).toContain('0 MODEL FACTS')
    expect(source).toContain('NO STATE COMMIT')
    expect(source).toContain('runtimeSummary.evidence.length')
    expect(source).toContain('runtimeSummary.limitations')
    expect(source).toContain('runtimeSummary.unknowns')
    expect(source).toContain('FAILED CLOSED')
    expect(source).toContain('cancelControlledEventSummary')
  })

  it('S3 聊天输入走服务端事务会话而不是旧正则或无状态语义入口', () => {
    expect(source).toContain('createRuntimeV2ConversationTurn')
    expect(source).toContain('OPEN USER GOAL → CLOSED GROUNDING')
    expect(source).toContain('OPEN GOAL / CLOSED EXECUTION')
    expect(source).toContain('result.requested_goal')
    expect(source).toContain('result.normalized_kind')
    expect(source).toContain('GROUNDING 100% LEGAL')
    expect(source).toContain('0 MODEL FACTS · STATE')
    expect(source).toContain('state_receipt.status')
    expect(source).not.toContain('countryOutageChatApi.createTurn(')
    expect(source).not.toContain('countryOutageChatApi.createRuntimeV2SemanticTurn(')
  })

  it('支持键盘发送、取消、重连恢复和到期后新建会话', () => {
    expect(source).toContain("event.key === 'Enter'")
    expect(source).toContain('Shift+Enter 换行')
    expect(source).toContain('取消本轮')
    expect(source).toContain('localStorage.getItem')
    expect(source).toContain('getRuntimeV2Conversation(savedId)')
    expect(source).toContain('上一短期会话已到期或因服务重启不可恢复')
    expect(source).toContain('role="status"')
    expect(source).toContain('以当前事件新建会话')
    expect(source).toContain('aria-live="polite"')
    expect(source).toContain('rebindRuntimeV2Conversation')
    expect(source).toContain('验证后原子切换')
    expect(source).toContain('IMMUTABLE')
    expect(source).toContain('active_binding_generation === null')
    expect(source).toContain("proposed.clear.includes('event_binding')")
    expect(source).toContain('response.turn.answer.binding.publication_id')
    expect(source).toContain("'event_switch_rebound_atomically'")
    expect(source).toContain('requiresAuthoritativeConversationRefresh')
    expect(source).toContain('getRuntimeV2Conversation(')
    expect(source).toContain('SUSPENDED')
    expect(source).toContain('turn.answer.binding.publication_id')
    expect(source).toContain('shortIdentity(turn.answer.binding.publication_id)')
  })

  it('明确保持 P1 边界且不暴露外部证据开关', () => {
    expect(source).toContain('不接入 OONI / IODA / Cloudflare')
    expect(source).toContain('不判断真实用户影响、责任或原因')
    expect(source).not.toContain('domeye_plus_external')
    expect(source).not.toContain('Evidence Graph')
    expect(source).not.toContain('RCA')
  })
})
