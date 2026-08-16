import { describe, expect, it } from 'vitest'

import { DEFAULT_API_TIMEOUT_MS, resolveApiTimeout } from './client'

describe('API 请求超时配置', () => {
  it('未配置时默认等待 60 秒', () => {
    expect(resolveApiTimeout(undefined)).toBe(DEFAULT_API_TIMEOUT_MS)
    expect(DEFAULT_API_TIMEOUT_MS).toBe(60_000)
  })

  it('允许使用正数毫秒值覆盖默认配置', () => {
    expect(resolveApiTimeout('90000')).toBe(90_000)
  })

  it('无效值回退到默认配置', () => {
    expect(resolveApiTimeout('not-a-number')).toBe(DEFAULT_API_TIMEOUT_MS)
    expect(resolveApiTimeout('0')).toBe(DEFAULT_API_TIMEOUT_MS)
    expect(resolveApiTimeout('-1')).toBe(DEFAULT_API_TIMEOUT_MS)
  })
})
