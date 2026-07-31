import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  countryOutageAgentApi,
  CountryOutageAgentOutcomeUncertainError,
  CountryOutageAgentRequestError,
  isCountryOutageRequestOutcomeUncertain,
  parseCountryOutageExternalEvidenceCapability,
  resolveCountryOutageAgentBase,
} from './countryOutageAgent'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('国家中断 Agent 公共控制面客户端', () => {
  it('始终使用 Domeye /api/v2/country-outage 路径', () => {
    expect(resolveCountryOutageAgentBase(undefined))
      .toBe('/api/v2/country-outage/')
    expect(resolveCountryOutageAgentBase('/api/v1/'))
      .toBe('/api/v2/country-outage/')
    expect(resolveCountryOutageAgentBase('https://domeye.test/api/v1'))
      .toBe('https://domeye.test/api/v2/country-outage/')
    expect(resolveCountryOutageAgentBase('/api/v2/'))
      .toBe('/api/v2/country-outage/')
  })

  it('只读查询编排层公开来源旁证 readiness 并接受版本化动态策略', async () => {
    const capability = {
      schema_version: 'country_outage_external_evidence_capability_v1',
      capability: 'external_evidence',
      state: 'ready',
      provider: 'managed-egress-v1',
      checked_at: '2026-07-30T12:00:00Z',
      policy: {
        version: 'country-outage-external-v2',
        sha256: 'a'.repeat(64),
        allowed_host_roots: ['evidence.example'],
        minimum_urls: 1,
        maximum_urls: 5,
      },
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => capability,
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(countryOutageAgentApi.getExternalEvidenceCapability())
      .resolves.toEqual(capability)

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, request] = fetchMock.mock.calls[0]!
    expect(url).toBe(
      '/api/v2/country-outage/capabilities/external-evidence',
    )
    expect(request).toEqual({
      method: 'GET',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
      },
      signal: undefined,
    })
    expect(request).not.toHaveProperty('body')
  })

  it.each([
    {
      state: 'not_configured' as const,
      provider: 'disabled' as const,
      reason_code: 'external_evidence_not_configured',
    },
    {
      state: 'self_check_failed' as const,
      provider: 'managed-egress-v1' as const,
      reason_code: 'external_evidence_self_check_failed',
    },
  ])('解析 $state 时保持 policy=null 并失败关闭入口', (item) => {
    expect(parseCountryOutageExternalEvidenceCapability({
      schema_version: 'country_outage_external_evidence_capability_v1',
      capability: 'external_evidence',
      ...item,
      checked_at: '2026-07-30T12:00:00Z',
      policy: null,
    })).toEqual({
      schema_version: 'country_outage_external_evidence_capability_v1',
      capability: 'external_evidence',
      ...item,
      checked_at: '2026-07-30T12:00:00Z',
      policy: null,
    })
  })

  it.each([
    {
      label: '未知 schema',
      payload: {
        schema_version: 'country_outage_external_evidence_capability_v2',
        capability: 'external_evidence',
        state: 'not_configured',
        provider: 'disabled',
        checked_at: '2026-07-30T12:00:00Z',
        policy: null,
        reason_code: 'not_configured',
      },
    },
    {
      label: 'ready 却使用 disabled provider',
      payload: {
        schema_version: 'country_outage_external_evidence_capability_v1',
        capability: 'external_evidence',
        state: 'ready',
        provider: 'disabled',
        checked_at: '2026-07-30T12:00:00Z',
        policy: {
          version: 'country-outage-external-v1',
          sha256: 'a'.repeat(64),
          allowed_host_roots: ['evidence.example'],
          minimum_urls: 1,
          maximum_urls: 1,
        },
      },
    },
    {
      label: 'not_configured 却使用 managed-egress-v1 provider',
      payload: {
        schema_version: 'country_outage_external_evidence_capability_v1',
        capability: 'external_evidence',
        state: 'not_configured',
        provider: 'managed-egress-v1',
        checked_at: '2026-07-30T12:00:00Z',
        policy: null,
        reason_code: 'not_configured',
      },
    },
    {
      label: 'self_check_failed 却使用 disabled provider',
      payload: {
        schema_version: 'country_outage_external_evidence_capability_v1',
        capability: 'external_evidence',
        state: 'self_check_failed',
        provider: 'disabled',
        checked_at: '2026-07-30T12:00:00Z',
        policy: null,
        reason_code: 'self_check_failed',
      },
    },
    {
      label: '策略域名根不规范',
      payload: {
        schema_version: 'country_outage_external_evidence_capability_v1',
        capability: 'external_evidence',
        state: 'ready',
        provider: 'managed-egress-v1',
        checked_at: '2026-07-30T12:00:00Z',
        policy: {
          version: 'country-outage-external-v1',
          sha256: 'a'.repeat(64),
          allowed_host_roots: ['LOCALHOST'],
          minimum_urls: 1,
          maximum_urls: 1,
        },
      },
    },
    {
      label: '缺少 capability 身份',
      payload: {
        schema_version: 'country_outage_external_evidence_capability_v1',
        state: 'not_configured',
        provider: 'disabled',
        checked_at: '2026-07-30T12:00:00Z',
        policy: null,
        reason_code: 'not_configured',
      },
    },
  ])('$label 时拒绝 capability 并按不可用处理', ({ payload }) => {
    expect(() => parseCountryOutageExternalEvidenceCapability(payload))
      .toThrowError(expect.objectContaining({
        code: 'external_evidence_capability_protocol_invalid',
      }))
  })

  it.each([
    {
      label: '策略域名根超过 5 个',
      policy: {
        allowed_host_roots: Array.from(
          { length: 6 },
          (_, index) => `evidence-${index}.example`,
        ),
        minimum_urls: 1,
        maximum_urls: 5,
      },
    },
    {
      label: 'minimum_urls 不是 1',
      policy: {
        allowed_host_roots: ['evidence.example'],
        minimum_urls: 2,
        maximum_urls: 5,
      },
    },
    {
      label: 'maximum_urls 超过 5',
      policy: {
        allowed_host_roots: ['evidence.example'],
        minimum_urls: 1,
        maximum_urls: 6,
      },
    },
  ])('$label 时拒绝 ready capability', ({ policy }) => {
    expect(() => parseCountryOutageExternalEvidenceCapability({
      schema_version: 'country_outage_external_evidence_capability_v1',
      capability: 'external_evidence',
      state: 'ready',
      provider: 'managed-egress-v1',
      checked_at: '2026-07-30T12:00:00Z',
      policy: {
        version: 'country-outage-external-v1',
        sha256: 'a'.repeat(64),
        ...policy,
      },
    })).toThrowError(expect.objectContaining({
      code: 'external_evidence_capability_protocol_invalid',
    }))
  })

  it('readiness 网络失败是可重试的只读状态失败，不属于写请求结果不确定', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))

    const failure = await countryOutageAgentApi
      .getExternalEvidenceCapability()
      .catch((cause) => cause)

    expect(failure).toMatchObject({
      code: 'external_evidence_capability_unavailable',
      retryable: true,
    })
    expect(isCountryOutageRequestOutcomeUncertain(failure)).toBe(false)
  })

  it('报告生成只提交冻结事件快照和幂等键', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({
        schema_version: 'country_outage_agent_report_start_v1',
        report_id: 'report-1',
        run_id: 'run-1',
        state: 'running',
        phase: 'queued',
        session: {
          expires_at: '2026-07-28T14:30:00Z',
          reminder_at: '2026-07-28T14:25:00Z',
        },
        deduplicated: false,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await countryOutageAgentApi.startReport({
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication_v1',
      revision: 1,
      idempotency_key: 'idem-report-1',
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, request] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/v2/country-outage/reports')
    expect(request).toMatchObject({
      method: 'POST',
      credentials: 'same-origin',
      headers: expect.objectContaining({
        'Idempotency-Key': 'idem-report-1',
        'Content-Type': 'application/json',
      }),
    })
    expect(JSON.parse(request.body)).toEqual({
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication_v1',
      revision: 1,
      idempotency_key: 'idem-report-1',
    })
  })

  it('默认追问保持 Domeye-only，下载仍经公共控制面', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({
        schema_version: 'country_outage_agent_question_start_v1',
        report_id: 'report-1',
        run_id: 'run-q1',
        question_id: 'question-1',
        number: 1,
        state: 'running',
        phase: 'answering',
        session: {
          expires_at: '2026-07-28T14:30:00Z',
          reminder_at: '2026-07-28T14:25:00Z',
        },
        deduplicated: false,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await countryOutageAgentApi.askQuestion('report-1', {
      question: '最低点何时发生？',
      evidence_mode: 'domeye_only',
      quote: {
        kind: 'section_paragraph',
        section_id: 'visibility',
        paragraph_index: 0,
        evidence_refs: ['series:/metric_extrema/visible/max'],
      },
      idempotency_key: 'idem-question-1',
    })

    const [url, request] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/v2/country-outage/reports/report-1/questions')
    const body = JSON.parse(request.body)
    expect(body).toEqual({
      question: '最低点何时发生？',
      evidence_mode: 'domeye_only',
      quote: {
        kind: 'section_paragraph',
        section_id: 'visibility',
        paragraph_index: 0,
        evidence_refs: ['series:/metric_extrema/visible/max'],
      },
      idempotency_key: 'idem-question-1',
    })
    expect(body).not.toHaveProperty('external_authorization')
    expect(body).not.toHaveProperty('external_urls')
    expect(countryOutageAgentApi.artifactUrl('report-1', 'pdf'))
      .toBe('/api/v2/country-outage/reports/report-1/artifacts/pdf')
    expect(
      countryOutageAgentApi.externalAppendixArtifactUrl(
        'report/1',
        'question/1',
      ),
    ).toBe(
      (
        '/api/v2/country-outage/reports/report%2F1/questions/'
        + 'question%2F1/artifacts/external-appendix'
      ),
    )
  })

  it('外部证据追问只转发本次显式授权和公开 URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({
        schema_version: 'country_outage_agent_question_start_v1',
        report_id: 'report-1',
        run_id: 'run-q-external',
        question_id: 'question-external',
        number: 2,
        state: 'running',
        phase: 'collecting_external',
        session: {
          expires_at: '2026-07-28T14:30:00Z',
          reminder_at: '2026-07-28T14:25:00Z',
        },
        deduplicated: false,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await countryOutageAgentApi.askQuestion('report-1', {
      question: '请结合这些公开来源说明外部报道情况。',
      evidence_mode: 'domeye_plus_external',
      external_authorization: {
        authorized: true,
        authorized_at: '2026-07-28T14:01:00.000Z',
      },
      external_urls: [
        'https://bgp.he.net/country/IR',
        'https://radar.cloudflare.com/ir',
      ],
      idempotency_key: 'idem-question-external-1',
    })

    const [url, request] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/v2/country-outage/reports/report-1/questions')
    expect(JSON.parse(request.body)).toEqual({
      question: '请结合这些公开来源说明外部报道情况。',
      evidence_mode: 'domeye_plus_external',
      external_authorization: {
        authorized: true,
        authorized_at: '2026-07-28T14:01:00.000Z',
      },
      external_urls: [
        'https://bgp.he.net/country/IR',
        'https://radar.cloudflare.com/ir',
      ],
      idempotency_key: 'idem-question-external-1',
    })
  })

  it('SSE 断线进入重试、重连后恢复，并且命名事件只处理一次', () => {
    class FakeEventSource {
      static instance: FakeEventSource | null = null
      onopen: ((event: Event) => void) | null = null
      onerror: ((event: Event) => void) | null = null
      onmessage: ((event: MessageEvent<string>) => void) | null = null
      readonly listeners = new Map<string, EventListener[]>()
      closed = false

      constructor(
        readonly url: string,
        readonly options: EventSourceInit,
      ) {
        FakeEventSource.instance = this
      }

      addEventListener(name: string, listener: EventListener) {
        const registered = this.listeners.get(name) ?? []
        registered.push(listener)
        this.listeners.set(name, registered)
      }

      emit(name: string, data: object) {
        const event = { data: JSON.stringify(data) } as MessageEvent<string>
        for (const listener of this.listeners.get(name) ?? []) listener(event)
      }

      close() {
        this.closed = true
      }
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    const received = vi.fn()
    const connectionChanged = vi.fn()

    const subscription = countryOutageAgentApi.subscribe('report-1', {
      onEvent: received,
      onConnectionChange: connectionChanged,
    })
    const source = FakeEventSource.instance!
    expect(source.url)
      .toBe('/api/v2/country-outage/reports/report-1/events')
    expect(source.options).toEqual({ withCredentials: true })
    expect(source.onmessage).toBeNull()
    expect([...source.listeners.keys()]).toEqual([
      'report_state',
      'question_state',
      'session_notice',
    ])
    expect(
      [...source.listeners.values()].every(
        (listeners) => listeners.length === 1,
      ),
    ).toBe(true)

    source.onerror?.(new Event('error'))
    source.onopen?.(new Event('open'))
    expect(connectionChanged.mock.calls).toEqual([
      ['retrying'],
      ['connected'],
    ])

    source.emit('report_state', {
      schema_version: 'country_outage_agent_event_v1',
      event_id: 1,
      report_id: 'report-1',
      event_type: 'report_state',
      at: '2026-07-28T14:00:00Z',
      state: 'running',
      phase: 'reading_data',
      session: {
        expires_at: '2026-07-28T14:30:00Z',
        reminder_at: '2026-07-28T14:25:00Z',
      },
    })
    expect(received).toHaveBeenCalledOnce()

    subscription.close()
    expect(source.closed).toBe(true)
  })

  it('保留服务端错误分类和可执行 next_action', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({
        error: {
          code: 'snapshot_conflict',
          message: '发布快照身份发生冲突',
          retryable: true,
          next_action: '刷新事件数据后重试',
        },
      }),
    }))

    const failure = await countryOutageAgentApi.startReport({
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication_v1',
      revision: 1,
      idempotency_key: 'idem-conflict',
    }).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(CountryOutageAgentRequestError)
    expect(failure).toMatchObject({
      code: 'snapshot_conflict',
      retryable: true,
      nextAction: '刷新事件数据后重试',
    })
    expect(isCountryOutageRequestOutcomeUncertain(failure)).toBe(false)
  })

  it('请求送出后网络响应丢失时标记结果不确定', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new TypeError('network connection lost')),
    )

    const failure = await countryOutageAgentApi.startReport({
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication_v1',
      revision: 1,
      idempotency_key: 'idem-response-lost',
    }).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(CountryOutageAgentOutcomeUncertainError)
    expect(isCountryOutageRequestOutcomeUncertain(failure)).toBe(true)
    expect(failure).toMatchObject({ outcomeUncertain: true })
  })

  it('2xx 响应缺少完整 JSON 对象时标记结果不确定', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => null,
    }))

    const failure = await countryOutageAgentApi.askQuestion('report-1', {
      question: '结束时恢复了吗？',
      evidence_mode: 'domeye_only',
      idempotency_key: 'idem-empty-success',
    }).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(CountryOutageAgentOutcomeUncertainError)
    expect(isCountryOutageRequestOutcomeUncertain(failure)).toBe(true)
  })

  it('收到 2xx 后读取响应被取消时保留为不确定提交', async () => {
    const abortError = new DOMException('用户取消', 'AbortError')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => {
        throw abortError
      },
    }))

    const failure = await countryOutageAgentApi.startReport({
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication_v1',
      revision: 1,
      idempotency_key: 'idem-aborted',
    }).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(CountryOutageAgentOutcomeUncertainError)
    expect(isCountryOutageRequestOutcomeUncertain(failure)).toBe(true)
    expect(failure).toMatchObject({ outcomeUncertain: true })
  })

  it('Flask 代理 503 agent_unavailable 表示 Sidecar 接受结果不确定', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({
        error: {
          code: 'agent_unavailable',
          message: '读取 Sidecar 响应超时',
          retryable: true,
          next_action: '确认本机 Sidecar 配置与运行状态后重试',
        },
      }),
    }))

    const failure = await countryOutageAgentApi.startReport({
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication_v1',
      revision: 1,
      idempotency_key: 'idem-agent-unavailable',
    }).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(CountryOutageAgentRequestError)
    expect(failure).toMatchObject({
      code: 'agent_unavailable',
      retryable: true,
    })
    expect(isCountryOutageRequestOutcomeUncertain(failure)).toBe(true)
  })

  it('代理已收到过大的 Sidecar POST 响应时保留不确定语义', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => ({
        error: {
          code: 'agent_response_too_large',
          message: 'Agent 响应超过 2 MiB',
          retryable: false,
        },
      }),
    }))

    const failure = await countryOutageAgentApi.askQuestion('report-1', {
      question: '结束时恢复了吗？',
      evidence_mode: 'domeye_only',
      idempotency_key: 'idem-agent-response-large',
    }).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(CountryOutageAgentRequestError)
    expect(failure).toMatchObject({ code: 'agent_response_too_large' })
    expect(isCountryOutageRequestOutcomeUncertain(failure)).toBe(true)
  })

  it.each([502, 503, 504])(
    '无结构化 HTTP_%i 代理响应表示接受结果不确定',
    async (status) => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
        ok: false,
        status,
        json: async () => null,
      }))

      const failure = await countryOutageAgentApi.startReport({
        event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
        publication_id: 'publication_v1',
        revision: 1,
        idempotency_key: `idem-unstructured-${status}`,
      }).catch((error: unknown) => error)

      expect(failure).toBeInstanceOf(CountryOutageAgentRequestError)
      expect(failure).toMatchObject({ code: `HTTP_${status}` })
      expect(isCountryOutageRequestOutcomeUncertain(failure)).toBe(true)
    },
  )

  it('不把其他非结构化 5xx 或所有 retryable 错误泛化为不确定', () => {
    const unstructuredInternalError = new CountryOutageAgentRequestError({
      code: 'HTTP_500',
      message: '内部错误',
      retryable: false,
    })
    const explicitNewSubmission = new CountryOutageAgentRequestError({
      code: 'snapshot_conflict',
      message: '请刷新快照后重新提交',
      retryable: true,
    })

    expect(
      isCountryOutageRequestOutcomeUncertain(unstructuredInternalError),
    ).toBe(false)
    expect(
      isCountryOutageRequestOutcomeUncertain(explicitNewSubmission),
    ).toBe(false)
  })
})
