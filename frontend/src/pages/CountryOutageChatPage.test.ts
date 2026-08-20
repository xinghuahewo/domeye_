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

describe('国家中断首片用户问答页', () => {
  it('只展示自然回答和可选的人类可读依据', () => {
    expect(pageSource).toContain('answerFor(turn)?.answer_text')
    for (const field of [
      'source_label_zh',
      'observed_object_zh',
      'window_start_utc',
      'window_end_utc',
      'important_boundary_zh',
    ]) expect(pageSource).toContain(field)

    expect(pageSource).toContain('来源')
    expect(pageSource).toContain('观测对象')
    expect(pageSource).toContain('观测窗口')
    expect(pageSource).toContain('重要边界')
  })

  it('任何页面模式都不提供内部审计对象、技术账本或原始错误码', () => {
    const prohibited = [
      'Candidate',
      'Finding',
      'Evidence',
      'Receipt',
      'Trace',
      'Guard',
      'Provider usage',
      'identity_receipt',
      'candidate_id',
      'result_digest',
      'content_digest',
      'turnErrorFor(turn)?.code',
      'conversation.binding.publication_id',
      'conversation.binding.revision',
      'conversation.binding.cohort_id',
      'conversation.binding.collector_id',
      'conversation.binding.event_reference',
    ]
    for (const term of prohibited) expect(pageSource).not.toContain(term)
    expect(pageSource).not.toContain('<aside')
    expect(pageSource).not.toContain('open>')
    expect(pageSource).toContain('<summary>')
    expect(pageSource).toContain('<span>查看依据</span>')
  })

  it('回答文本不裁剪、不补字，并保留换行和空格', () => {
    expect(pageSource).toContain('<p class="answer-copy">{{ answerFor(turn)?.answer_text }}</p>')
    expect(pageSource).toContain('white-space: pre-wrap')
    expect(pageSource).not.toMatch(/answer_text[^\n]{0,80}trim\(/)
    expect(apiSource).not.toMatch(/answer_text[^\n]{0,80}(trim|replace|slice)\(/)
  })

  it('错误只显示稳定消息，重试入口仅由公开 retryable 控制', () => {
    expect(pageSource).toContain('turnErrorFor(turn)?.message')
    expect(pageSource).toContain('turnErrorFor(turn)?.retryable')
    expect(pageSource).toContain('再试一次')
    expect(pageSource).not.toContain('error.code')
  })

  it('保持固定问题、稳定请求路径和执行中取消入口', () => {
    expect(pageSource).toContain('COUNTRY_OUTAGE_FIRST_SLICE_QUESTION')
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

  it('桌面保持聚焦单栏，窄屏完整呈现回答与依据', () => {
    expect(pageSource).toContain('width: min(980px, calc(100% - 48px))')
    expect(pageSource).toContain('@media (max-width: 760px)')
    expect(pageSource).toContain('.conversation-shell { width: 100%;')
    expect(pageSource).toContain('.answer-basis dl { grid-template-columns: 1fr; }')
    expect(pageSource).toContain('.answer-basis dl > div { grid-column: auto; }')
  })
})
