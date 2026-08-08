import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  countryOutageChatApi,
  createP1IdempotencyKey,
} from './countryOutageChat'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('P1 事件绑定聊天 API', () => {
  it('创建会话时把 publication/revision 和幂等键送到窄接口', async () => {
    const payload = {
      conversation: {
        schema_version: 'country_outage_p1_chat_v1',
        conversation_id: 'conv_test',
      },
      deduplicated: false,
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => payload,
    })
    vi.stubGlobal('fetch', fetchMock)
    const request = {
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication-test',
      revision: 1,
      idempotency_key: 'conversation-test-01',
    }

    await expect(countryOutageChatApi.createConversation(request)).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith('/api/v2/country-outage/chat/conversations', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'Idempotency-Key': 'conversation-test-01',
      },
      body: JSON.stringify(request),
      signal: undefined,
    })
  })

  it('轮次错误保留稳定 code、retryable 和 next_action', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({
        error: {
          code: 'revision_drift',
          message: '活动 revision 已变化',
          retryable: false,
          next_action: '重新绑定',
        },
      }),
    }))
    await expect(countryOutageChatApi.createTurn('conv_test', {
      question: '峰值呢？',
      idempotency_key: 'turn-test-0001',
    })).rejects.toMatchObject({
      code: 'revision_drift', retryable: false, nextAction: '重新绑定',
    })
  })

  it('生成满足代理白名单的安全幂等键', () => {
    expect(createP1IdempotencyKey('conversation')).toMatch(/^conversation-[a-f0-9-]{36}$/)
    expect(createP1IdempotencyKey('turn')).toMatch(/^turn-[a-f0-9-]{36}$/)
  })
})
