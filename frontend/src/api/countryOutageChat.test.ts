import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  cancelCountryOutageChatTurn,
  createCountryOutageChatConversation,
  createCountryOutageChatTurn,
  getCountryOutageChatConversation,
} from './countryOutageChat'
import { apiV2Get } from './client'

vi.mock('./client', () => ({
  apiV2Get: vi.fn(),
  resolveApiTimeout: vi.fn(() => 60_000),
}))

describe('P1 页面能力聊天 API', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({}),
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterAll(() => vi.unstubAllGlobals())

  it('绑定事件身份且将幂等键同时放入正文与 Header', async () => {
    const request = {
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication-test',
      revision: 1,
      idempotency_key: 'chat-create-0001',
    }
    await createCountryOutageChatConversation(request)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v2/country-outage/chat/conversations',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify(request),
        headers: expect.objectContaining({
          'Idempotency-Key': 'chat-create-0001',
        }),
      }),
    )
  })

  it('只提交自然语言问题和幂等键，不允许前端选 Tool 或写状态', async () => {
    await createCountryOutageChatTurn('p1v2_test', 'IP地址变化趋势', 'chat-turn-00001')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v2/country-outage/chat/conversations/p1v2_test/turns',
      expect.objectContaining({
        body: JSON.stringify({
          question: 'IP地址变化趋势',
          idempotency_key: 'chat-turn-00001',
        }),
        headers: expect.objectContaining({
          'Idempotency-Key': 'chat-turn-00001',
        }),
      }),
    )
  })

  it('读取会话和取消轮次均使用编码后的有限路径', async () => {
    vi.mocked(apiV2Get).mockResolvedValue({ conversation: {} })
    await getCountryOutageChatConversation('conversation/1')
    await cancelCountryOutageChatTurn('conversation/1', 'turn/1')
    expect(apiV2Get).toHaveBeenCalledWith(
      'country-outage/chat/conversations/conversation%2F1',
    )
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v2/country-outage/chat/conversations/conversation%2F1/turns/turn%2F1/cancel',
      expect.objectContaining({ body: '{}' }),
    )
  })
})
