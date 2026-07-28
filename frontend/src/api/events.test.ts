import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiV2Get } from '@/api/client'
import {
  getEventObservation,
  isEventObservationNotConfigured,
} from '@/api/events'

vi.mock('@/api/client', () => ({
  apiGet: vi.fn(),
  apiV2Get: vi.fn(),
}))

vi.mock('@/utils/normalize', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/normalize')>()
  return {
    ...actual,
    normalizeCountryOutageObservation: vi.fn(() => ({
      schema_version: 'country_outage_observation_v2',
    })),
  }
})

beforeEach(() => {
  vi.clearAllMocks()
})

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

  it('keeps non-country event types on their existing evidence page', async () => {
    let thrown: unknown
    try {
      await getEventObservation(
        'leak/2026-03-31 23:44:43/62.78.32.0-24/1/r',
      )
    } catch (cause) {
      thrown = cause
    }

    expect(isEventObservationNotConfigured(thrown)).toBe(true)
    expect(apiV2Get).not.toHaveBeenCalled()
  })

  it('pins overview, series and ASN reads to the resolver publication', async () => {
    vi.mocked(apiV2Get)
      .mockResolvedValueOnce({
        incident_id: 'incident-test',
        publication_id: 'publication-test',
      })
      .mockResolvedValueOnce({ schema_version: 'country_outage_overview_v2' })
      .mockResolvedValueOnce({ schema_version: 'country_outage_series_v2' })
      .mockResolvedValueOnce({ schema_version: 'country_outage_asn_page_v2' })

    await getEventObservation(
      'country_outage/2026-02-27 09:12:32/IR/1/r',
    )

    expect(vi.mocked(apiV2Get).mock.calls.slice(1)).toEqual([
      [
        'country-outages/incident-test/overview',
        { params: { publication_id: 'publication-test' } },
      ],
      [
        'country-outages/incident-test/series',
        { params: { publication_id: 'publication-test' } },
      ],
      [
        'country-outages/incident-test/asns',
        {
          params: {
            publication_id: 'publication-test',
            page: 1,
            page_size: 60,
          },
        },
      ],
    ])
  })
})
