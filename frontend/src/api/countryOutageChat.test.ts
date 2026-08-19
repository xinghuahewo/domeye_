import { afterAll, beforeEach, describe, expect, expectTypeOf, it, vi } from 'vitest'

import {
  cancelCountryOutageChatTurn,
  COUNTRY_OUTAGE_FIRST_SLICE_QUESTION,
  createCountryOutageChatConversation,
  createCountryOutageChatTurn,
  getCountryOutageChatConversation,
  type CountryOutageChatTurn,
} from './countryOutageChat'
import { apiV2Get } from './client'

vi.mock('./client', () => ({
  apiV2Get: vi.fn(),
  resolveApiTimeout: vi.fn(() => 60_000),
}))

describe('首个纵向切片交互式 Agent API', () => {
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

  it('绑定冻结事件身份且将幂等键同时放入正文与 Header', async () => {
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

  it('只提交文本问题和幂等键，不允许前端选择能力或执行单元', async () => {
    await createCountryOutageChatTurn(
      'conversation-test',
      COUNTRY_OUTAGE_FIRST_SLICE_QUESTION,
      'chat-turn-00001',
    )
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v2/country-outage/chat/conversations/conversation-test/turns',
      expect.objectContaining({
        body: JSON.stringify({
          question: COUNTRY_OUTAGE_FIRST_SLICE_QUESTION,
          idempotency_key: 'chat-turn-00001',
        }),
        headers: expect.objectContaining({
          'Idempotency-Key': 'chat-turn-00001',
        }),
      }),
    )
  })

  it('读取会话和取消轮次均使用编码后的稳定路径', async () => {
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

  it('把合同外问题的服务拒绝原因原样交给工作台', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: vi.fn().mockResolvedValue({
        error: {
          code: 'goal_outside_first_slice_contract',
          message: '当前 Candidate 只实现首个纵向切片固定问题',
          retryable: false,
        },
      }),
    })

    await expect(createCountryOutageChatTurn(
      'conversation-test',
      '请判断全国是否断网',
      'chat-turn-00002',
    )).rejects.toMatchObject({
      status: 400,
      code: 'goal_outside_first_slice_contract',
      message: '当前 Candidate 只实现首个纵向切片固定问题',
      retryable: false,
    })
  })

  it('用六态判别联合区分正确完成与安全停止', () => {
    type CompletedTurn = Extract<CountryOutageChatTurn, { state: 'completed' }>
    type StoppedTurn = Extract<CountryOutageChatTurn, { state: 'stopped' }>
    type ExecutingTurn = Extract<CountryOutageChatTurn, { state: 'executing' }>
    type CompletedFallback = Extract<
      CompletedTurn['answer'],
      { answer_source: 'deterministic_fallback' }
    >
    type StoppedFallback = Extract<
      StoppedTurn['answer'],
      { answer_source: 'deterministic_fallback' }
    >

    expectTypeOf<CompletedTurn>().toMatchTypeOf<{
      answer_success: true
      workflow_completed: true
      answer: { answerability: 'supported' }
    }>()
    expectTypeOf<StoppedTurn>().toMatchTypeOf<{
      answer_success: false
      workflow_completed: false
      answer: { answerability: 'stopped'; answer_source: 'none' }
    }>()
    expectTypeOf<ExecutingTurn>().toMatchTypeOf<{
      answer_success: false
      workflow_completed: false
    }>()
    expectTypeOf<CompletedFallback>().toMatchTypeOf<{
      answerability: 'supported'
      answer_source: 'deterministic_fallback'
      trace: { response_guard: { decision: 'block' } }
    }>()
    expectTypeOf<StoppedFallback>().toEqualTypeOf<never>()
  })
})
