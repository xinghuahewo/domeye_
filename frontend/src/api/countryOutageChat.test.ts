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
  it('S2 语义轮次只发送冻结身份和用户原问题', async () => {
    const payload = {
      schema_version: 'country_outage_p1_semantic_turn_v2',
      answerability: 'partial',
      results: [],
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => payload,
    })
    vi.stubGlobal('fetch', fetchMock)
    const request = {
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication-test',
      revision: 1,
      question: '现在还有多少前缀不可见，是不是全国都断了？',
    }

    await expect(
      countryOutageChatApi.createRuntimeV2SemanticTurn(request),
    ).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v2/country-outage/runtime-v2/semantic-turn',
      {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
        signal: undefined,
      },
    )
  })

  it('S1 受控单轮请求只发送冻结身份和 event_summary', async () => {
    const payload = {
      schema_version: 'country_outage_p1_single_turn_v2',
      answerability: 'partial',
      answer_text: '确定性事件概览',
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => payload,
    })
    vi.stubGlobal('fetch', fetchMock)
    const request = {
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication-test',
      revision: 1,
      controlled_goal: 'event_summary' as const,
    }

    await expect(
      countryOutageChatApi.createRuntimeV2SingleTurn(request),
    ).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v2/country-outage/runtime-v2/single-turn',
      {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
        signal: undefined,
      },
    )
  })

  it('S3 会话轮次只发送原问题和幂等键到事务化窄接口', async () => {
    const payload = {
      turn: {
        turn_id: 'p1v2turn_test',
        state: 'completed',
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
      question: '到最后还剩多少？',
      idempotency_key: 'turn-runtime-v2-0001',
    }

    await expect(
      countryOutageChatApi.createRuntimeV2ConversationTurn(
        'p1v2_conversation',
        request,
      ),
    ).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v2/country-outage/runtime-v2/conversations/p1v2_conversation/turns',
      {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'Idempotency-Key': 'turn-runtime-v2-0001',
        },
        body: JSON.stringify(request),
        signal: undefined,
      },
    )
  })

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
