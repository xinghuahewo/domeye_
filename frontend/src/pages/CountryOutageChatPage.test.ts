import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

const pageSource = readFileSync(
  new URL('./CountryOutageChatPage.vue', import.meta.url),
  'utf8',
)
const apiSource = readFileSync(
  new URL('../api/countryOutageChat.ts', import.meta.url),
  'utf8',
)

describe('国家中断首片调查工作台', () => {
  it('展示 Candidate、身份回执和完整冻结数据身份', () => {
    for (const anchor of [
      'conversation.candidate_id',
      'conversation.identity_receipt_id',
      'conversation.binding.event_reference',
      'Publication',
      'Revision',
      'RRC25',
      'Data through',
      '事件结束未知',
    ]) expect(pageSource).toContain(anchor)
  })

  it('按真实服务结构展示答案、Finding、证据、执行回执与模型用量', () => {
    for (const anchor of [
      'answer.answer_text',
      'answer.answer_source',
      'answer.finding',
      'answer.evidence',
      'trace.admission_receipts',
      'trace.action_receipts',
      'trace.artifacts',
      'trace.observations',
      'trace.response_guard',
      'answer.usage',
      'Provider usage',
    ]) expect(pageSource).toContain(anchor)
    expect(pageSource).not.toContain('deterministic_fallback')
  })

  it('只推荐合同固定问题，同时允许直接输入并展示合同外拒绝', () => {
    expect(pageSource).toContain('COUNTRY_OUTAGE_FIRST_SLICE_QUESTION')
    expect(pageSource).toContain('输入框可以直接填写')
    expect(pageSource).toContain('合同外问题会收到明确拒绝')
    expect(apiSource).toContain('CountryOutageChatApiError')
    expect(pageSource).toContain('role="alert"')
  })

  it('保持稳定请求路径和执行中取消入口', () => {
    expect(apiSource).toContain('country-outage/chat/conversations')
    expect(apiSource).toContain('/cancel')
    expect(pageSource).toContain('cancelCountryOutageChatTurn')
    expect(pageSource).toContain('取消本轮')
  })

  it('用会话 generation 和请求身份隔离 submit、poll 与 cancel 的异步结果', () => {
    for (const anchor of [
      'conversationGeneration',
      'ConversationRequestIdentity',
      'currentConversationRequestIdentity',
      'isCurrentConversationRequest',
      'identity.conversationId',
      'submitRequestId',
      'cancelRequestId',
      '会话轮询响应身份不一致',
    ]) expect(pageSource).toContain(anchor)

    expect(pageSource.match(/isCurrentConversationRequest\(identity\)/g)?.length)
      .toBeGreaterThanOrEqual(6)
    expect(pageSource).toContain('generation !== conversationGeneration')
    expect(pageSource).toContain('snapshot.conversation.conversation_id !== identity.conversationId')
  })

  it('不再包含退役对话架构字段或切换入口', () => {
    const retiredTerms = [
      ['normalized', 'kind'].join('_'),
      ['Semantic', 'Plan'].join(''),
      ['Grounding', 'Plan'].join(''),
      ['Dialog', 'State'].join(''),
      ['binding', 'generation'].join('_'),
      ['re', 'bind'].join(''),
      ['P', '1'].join(''),
    ]
    for (const term of retiredTerms) {
      expect(pageSource).not.toContain(term)
      expect(apiSource).not.toContain(term)
    }
  })

  it('窄屏把完整回执账本移到回答下方而不隐藏', () => {
    expect(pageSource).toContain('@media (max-width: 820px)')
    expect(pageSource).toContain('.chat-workbench { display: flex; flex-direction: column; }')
    expect(pageSource).toContain('.audit-panel { height: auto; overflow: visible;')
  })
})
