import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  CountryOutageInvestigationRequestError,
  createCountryOutageInvestigation,
  createCountryOutageInvestigationExport,
  createCountryOutageInvestigationTurn,
  getCountryOutageInvestigationResultSet,
  rerunCountryOutageInvestigationNode,
} from './countryOutageInvestigation'

vi.mock('./client', () => ({ resolveApiTimeout: vi.fn(() => 60_000) }))

const digest = `sha256:${'a'.repeat(64)}`
const cas = {
  idempotency_key: 'w5-action-0001',
  expected_investigation_revision: 2,
  expected_current_digest: digest,
}

describe('W5 组合调查 API', () => {
  const fetchMock = vi.fn()

  function lastRequestBody(): Record<string, unknown> {
    const call = fetchMock.mock.calls.at(-1)
    if (!call) throw new Error('预期 fetch 已被调用')
    const options = call[1] as RequestInit | undefined
    if (typeof options?.body !== 'string') throw new Error('预期请求包含 JSON body')
    return JSON.parse(options.body) as Record<string, unknown>
  }

  beforeEach(() => {
    vi.clearAllMocks()
    fetchMock.mockResolvedValue({
      ok: true,
      status: 202,
      json: vi.fn().mockResolvedValue({ accepted: true }),
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterAll(() => vi.unstubAllGlobals())

  it('创建只提交冻结事件身份和自然语言目标，不接受前端 Tool 选择', async () => {
    const request = {
      event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      publication_id: 'publication-test',
      revision: 1,
      goal: '事件全景、精确时点下钻和证据一致性',
      idempotency_key: 'w5-create-0001',
    }
    await createCountryOutageInvestigation(request)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v2/country-outage/investigations',
      expect.objectContaining({
        body: JSON.stringify(request),
        headers: expect.objectContaining({ 'Idempotency-Key': 'w5-create-0001' }),
      }),
    )
    expect(lastRequestBody()).not.toHaveProperty('unit_ids')
  })

  it('重跑携带当前 revision 和 digest，路径标识符被编码', async () => {
    await rerunCountryOutageInvestigationNode('inv/1', 'node/1', cas)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v2/country-outage/investigations/inv%2F1/nodes/node%2F1/reruns',
      expect.objectContaining({ body: JSON.stringify(cas) }),
    )
  })

  it('追问必须由调用者提供显式 node revision anchor', async () => {
    await createCountryOutageInvestigationTurn('inv_1', {
      ...cas,
      idempotency_key: 'w5-turn-00001',
      question: '展开那个时间点',
      anchor: { node_id: 'node_1', node_revision: 2, selection_ref: 'timepoint:peak' },
    })
    const payload = lastRequestBody()
    expect(payload.anchor).toEqual({
      node_id: 'node_1',
      node_revision: 2,
      selection_ref: 'timepoint:peak',
    })
  })

  it('ResultSet 请求将 revision 固定在路径且 token 仅作分页参数', async () => {
    await getCountryOutageInvestigationResultSet('inv_1', 'rs_1', 3, {
      pageSize: 20,
      pageToken: 'bound-token',
    })
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe(
      '/api/v2/country-outage/investigations/inv_1/result-sets/rs_1/revisions/3?page_size=20&page_token=bound-token',
    )
  })

  it('导出明确绑定一个冻结 ResultSet revision', async () => {
    await createCountryOutageInvestigationExport('inv_1', {
      ...cas,
      idempotency_key: 'w5-export-0001',
      result_set_id: 'rs_1',
      result_set_revision: 3,
      format: 'csv',
    })
    const payload = lastRequestBody()
    expect(payload).toMatchObject({ result_set_id: 'rs_1', result_set_revision: 3 })
  })

  it('保留冲突代码，页面可阻止旧 revision 静默覆盖', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 409,
      json: vi.fn().mockResolvedValue({
        error: { code: 'revision_conflict', message: '调查 revision 已变化', retryable: false },
      }),
    })
    await expect(rerunCountryOutageInvestigationNode('inv_1', 'node_1', cas))
      .rejects.toEqual(expect.objectContaining<Partial<CountryOutageInvestigationRequestError>>({
        status: 409,
        code: 'revision_conflict',
      }))
  })
})
