import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiV2Get } from '@/api/client'
import {
  getCountryOutageGeneralAffectedAs,
  getCountryOutageGeneralPage,
  getCountryOutageGeneralPathDownstreams,
  getEventObservation,
  isEventObservationNotConfigured,
} from '@/api/events'
import {
  normalizeCountryOutageGeneralAffectedAsPage,
  normalizeCountryOutageGeneralPage,
  normalizeCountryOutageGeneralPathDownstreamPage,
} from '@/utils/countryOutageGeneral'
import { normalizeCountryOutageObservation } from '@/utils/normalize'

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

vi.mock('@/utils/countryOutageGeneral', () => ({
  normalizeCountryOutageGeneralPage: vi.fn(() => ({ page: 'normalized' })),
  normalizeCountryOutageGeneralAffectedAsPage: vi.fn(() => ({ items: [] })),
  normalizeCountryOutageGeneralPathDownstreamPage: vi.fn(() => ({ items: [] })),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('event observation fallback boundary', () => {
  it('loads the general first screen without requesting the audit endpoint', async () => {
    const resolution = {
      schema_version: 'country_outage_general_resolution_v1',
      incident_id: 'incident-general',
      publication_id: 'publication-general',
    }
    const overview = { schema_version: 'country_outage_general_overview_v1' }
    const series = { schema_version: 'country_outage_general_series_v1' }
    vi.mocked(apiV2Get)
      .mockResolvedValueOnce(resolution)
      .mockResolvedValueOnce(overview)
      .mockResolvedValueOnce(series)

    await getCountryOutageGeneralPage(
      'country_outage/2026-02-27 09:12:32/IR/1/r',
    )

    expect(vi.mocked(apiV2Get).mock.calls).toEqual([
      ['events/resolve', { params: { ref: 'country_outage/2026-02-27 09:12:32/IR/1/r' } }],
      ['country-outages/incident-general/overview', { params: { publication_id: 'publication-general' } }],
      ['country-outages/incident-general/series', { params: { publication_id: 'publication-general' } }],
    ])
    expect(normalizeCountryOutageGeneralPage).toHaveBeenCalledWith(
      resolution,
      overview,
      series,
    )
  })

  it('pins both drilldowns to the same event version and bounded paging', async () => {
    const metadata = {
      incident_id: 'incident-general',
      publication_id: 'publication-general',
    } as never
    const asPayload = { schema_version: 'country_outage_general_affected_as_page_v1' }
    const pathPayload = { schema_version: 'country_outage_general_path_downstream_page_v1' }
    vi.mocked(apiV2Get)
      .mockResolvedValueOnce(asPayload)
      .mockResolvedValueOnce(pathPayload)

    await getCountryOutageGeneralAffectedAs(metadata, {
      page: 2,
      page_size: 30,
      classification: 'affected',
    })
    await getCountryOutageGeneralPathDownstreams(metadata, {
      page: 1,
      page_size: 30,
      scope: 'concurrent',
    })

    expect(vi.mocked(apiV2Get).mock.calls).toEqual([
      ['country-outages/incident-general/asns', {
        params: {
          page: 2,
          page_size: 30,
          classification: 'affected',
          publication_id: 'publication-general',
        },
      }],
      ['country-outages/incident-general/path-downstreams', {
        params: {
          page: 1,
          page_size: 30,
          scope: 'concurrent',
          publication_id: 'publication-general',
        },
      }],
    ])
    expect(normalizeCountryOutageGeneralAffectedAsPage).toHaveBeenCalledWith(asPayload, metadata)
    expect(normalizeCountryOutageGeneralPathDownstreamPage).toHaveBeenCalledWith(pathPayload, metadata)
  })

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

  it('falls back to the available Evidence Bundle for legacy summaries', async () => {
    vi.mocked(apiV2Get).mockResolvedValueOnce({
      incident_id: 'legacy-incident',
      publication_id: 'legacy-publication',
      observation_state: 'legacy_summary',
    })

    let thrown: unknown
    try {
      await getEventObservation(
        'country_outage/2026-03-09 22:09:38/MW/2/r',
      )
    } catch (cause) {
      thrown = cause
    }

    expect(isEventObservationNotConfigured(thrown)).toBe(true)
    expect(apiV2Get).toHaveBeenCalledTimes(1)
  })

  it('pins the compact homepage reads to the resolver publication', async () => {
    const overview = { schema_version: 'country_outage_overview_v2' }
    const series = { schema_version: 'country_outage_series_v2' }
    const audit = { schema_version: 'country_outage_audit_v2' }
    vi.mocked(apiV2Get)
      .mockResolvedValueOnce({
        incident_id: 'incident-test',
        publication_id: 'publication-test',
      })
      .mockResolvedValueOnce(overview)
      .mockResolvedValueOnce(series)
      .mockResolvedValueOnce(audit)

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
        'country-outages/incident-test/audit',
        { params: { publication_id: 'publication-test' } },
      ],
    ])
    expect(normalizeCountryOutageObservation).toHaveBeenCalledWith(
      overview,
      series,
      null,
      audit,
    )
  })
})
