import { describe, expect, it } from 'vitest'

import { isEventObservationNotConfigured } from '@/api/events'

describe('event observation fallback boundary', () => {
  it('falls back only when the observation API explicitly reports not configured', () => {
    expect(isEventObservationNotConfigured({
      isAxiosError: true,
      response: {
        status: 404,
        data: { observation_state: 'not_configured' },
      },
    })).toBe(true)
  })

  it('keeps transport and server failures visible instead of silently loading legacy UI', () => {
    expect(isEventObservationNotConfigured({
      isAxiosError: true,
      response: {
        status: 500,
        data: { message: 'failed' },
      },
    })).toBe(false)

    expect(isEventObservationNotConfigured(new Error('network failed'))).toBe(false)
  })
})
