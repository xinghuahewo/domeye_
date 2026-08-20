import { afterAll, beforeEach, describe, expect, expectTypeOf, it, vi } from 'vitest'

import {
  cancelCountryOutageChatTurn,
  COUNTRY_OUTAGE_FIRST_SLICE_QUESTION,
  createCountryOutageChatConversation,
  createCountryOutageChatTurn,
  getCountryOutageChatConversation,
  type CountryOutageChatTurn,
  type CountryOutageChatSuccessfulTurnAnswer,
} from './countryOutageChat'

vi.mock('./client', () => ({
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
    await getCountryOutageChatConversation('conversation/1')
    await cancelCountryOutageChatTurn('conversation/1', 'turn/1')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v2/country-outage/chat/conversations/conversation%2F1',
      expect.objectContaining({ method: 'GET' }),
    )
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v2/country-outage/chat/conversations/conversation%2F1/turns/turn%2F1/cancel',
      expect.objectContaining({ body: '{}' }),
    )
  })

  it('读取会话失败时只交付稳定的中文说明', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: vi.fn().mockResolvedValue({
        error: {
          code: 'answer_temporarily_unavailable',
          message: '回答暂时不可用，请稍后再试',
          retryable: true,
        },
      }),
    })

    await expect(
      getCountryOutageChatConversation('conversation-test'),
    ).rejects.toMatchObject({
      status: 503,
      code: 'answer_temporarily_unavailable',
      message: '回答暂时不可用，请稍后再试',
      retryable: true,
    })
  })

  it('把公开且稳定的失败说明交给页面', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: vi.fn().mockResolvedValue({
        error: {
          code: 'invalid_request',
          message: '当前请求超出此回答功能支持的范围',
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
      code: 'invalid_request',
      message: '当前请求超出此回答功能支持的范围',
      retryable: false,
    })
  })

  it('不改变公开回答中的空格、换行或措辞', async () => {
    const answerText = '  最低值为 9,577,728。\n边界说明保持原样。  '
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({
        turn: {
          answer: { answer_text: answerText },
        },
        deduplicated: false,
      }),
    })

    const response = await createCountryOutageChatTurn(
      'conversation-test',
      COUNTRY_OUTAGE_FIRST_SLICE_QUESTION,
      'chat-turn-00003',
    )
    expect(
      'answer' in response.turn ? response.turn.answer.answer_text : null,
    ).toBe(answerText)
  })

  it('用六态判别联合区分公开完成与非完成', () => {
    type CompletedTurn = Extract<CountryOutageChatTurn, { state: 'completed' }>
    type StoppedTurn = Extract<CountryOutageChatTurn, { state: 'stopped' }>
    type ExecutingTurn = Extract<CountryOutageChatTurn, { state: 'executing' }>
    type FailedTurn = Extract<CountryOutageChatTurn, { state: 'failed' }>
    type CompletedFallback = Extract<
      CompletedTurn['answer'],
      { answer_source: 'deterministic_fallback' }
    >
    type StoppedFallback = Extract<
      StoppedTurn['answer'],
      { answer_source: 'deterministic_fallback' }
    >
    type InternalAnswerKeys = Extract<
      keyof CountryOutageChatSuccessfulTurnAnswer,
      | 'candidate_id'
      | 'finding'
      | 'evidence'
      | 'trace'
      | 'usage'
    >
    type RetryableTurnError = Extract<
      FailedTurn['error'],
      { code: 'answer_temporarily_unavailable' }
    >
    type InvalidTurnErrorCombination = Extract<
      FailedTurn['error'],
      { code: 'answer_not_published'; retryable: true }
    >

    expectTypeOf<CompletedTurn>().toMatchTypeOf<{
      answer_success: true
      workflow_completed: true
      answer: {
        answerability: 'supported'
        answer_source: 'renderer'
        answer_text: string
        basis: {
          source_label_zh: string
          observed_object_zh: string
          window_start_utc: string
          window_end_utc: string
          important_boundary_zh: string
        }
      }
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
    expectTypeOf<CompletedFallback>().toEqualTypeOf<never>()
    expectTypeOf<StoppedFallback>().toEqualTypeOf<never>()
    expectTypeOf<InternalAnswerKeys>().toEqualTypeOf<never>()
    expectTypeOf<RetryableTurnError>().toEqualTypeOf<{
      code: 'answer_temporarily_unavailable'
      message: '这次没有形成可靠答案，临时服务异常。请稍后重试。'
      retryable: true
    }>()
    expectTypeOf<InvalidTurnErrorCombination>().toEqualTypeOf<never>()
  })
})
