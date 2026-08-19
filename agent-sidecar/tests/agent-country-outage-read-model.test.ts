import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DomeyeReadModelError,
  HttpCountryOutageReadModel,
} from '../src/agent/country-outage-read-model.js'

const identity = {
  incident_id: 'incident-go',
  publication_id: 'publication-go',
  revision: 1,
  collector_id: 'rrc25',
  cohort_id: 'cohort-go',
  window_start_utc: '2026-02-27T00:10:00Z',
  window_end_utc: '2026-02-27T00:15:00Z',
  data_through: '2026-02-27T00:15:00Z',
  is_final_in_data_range: false,
  lifecycle_state: 'event_end_unknown',
} as const

const resolution = {
  schema_version: 'country_outage_general_resolution_v1',
  event_type: 'country_outage',
  country_code: 'IR',
  ...identity,
  capabilities: { overview: 'available', event_series: 'available' },
}

function response(payload: unknown) {
  return {
    ok: true,
    status: 200,
    async text() { return JSON.stringify(payload) },
  }
}

function verifierWithOverview(overview: Record<string, unknown>) {
  let call = 0
  return new HttpCountryOutageReadModel('https://domeye.invalid/api/v2/', {
    fetch: async () => response(call++ === 0 ? resolution : overview),
  })
}

test('resolver 完整身份可通过同 publication/incident 的无重复国家字段 overview 传递绑定', async () => {
  const verifier = verifierWithOverview({
    schema_version: 'country_outage_general_overview_v1',
    ...identity,
  })
  const receipt = await verifier.verify({
    reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
    publication_id: identity.publication_id,
    revision: identity.revision,
    candidate_id: 'candidate-go',
  })
  assert.equal(receipt.data_identity.country_code, 'IR')
  assert.equal(receipt.data_identity.event_type, 'country_outage')
  assert.equal(Object.isFrozen(receipt), true)
  assert.equal(Object.isFrozen(receipt.data_identity), true)
  assert.equal(Object.isFrozen(receipt.evidence_refs), true)
})

for (const conflict of [
  { country_code: 'US' },
  { event_type: 'other_event' },
  { schema_version: 'other_overview' },
]) {
  test(`overview 一旦显式返回冲突身份就失败关闭：${Object.keys(conflict)[0]}`, async () => {
    const verifier = verifierWithOverview({
      schema_version: 'country_outage_general_overview_v1',
      ...identity,
      ...conflict,
    })
    await assert.rejects(
      () => verifier.verify({
        reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
        publication_id: identity.publication_id,
        revision: identity.revision,
        candidate_id: 'candidate-go',
      }),
      (error: unknown) => error instanceof DomeyeReadModelError
        && error.code === 'identity_conflict',
    )
  })
}

test('series 显式返回错误国家时不得被请求身份重新标记', async () => {
  const reader = new HttpCountryOutageReadModel(
    'https://domeye.invalid/api/v2/',
    {
      fetch: async () => response({
        schema_version: 'country_outage_general_series_v1',
        ...identity,
        country_code: 'US',
        timestamps: [identity.window_start_utc, identity.window_end_utc],
        tracks: { fixed_visible_ipv4_address_count: [10, 9] },
        track_definitions: {
          fixed_visible_ipv4_address_count: {
            unit: 'unique_ipv4_address',
            definition: '固定人口定义',
          },
        },
        quality_state: 'complete',
        observation_state: 'evidence_complete',
        missing_slot_count: 0,
      }),
    },
  )
  await assert.rejects(
    () => reader.readMetricSeries({
      data_identity: {
        event_type: 'country_outage',
        country_code: 'IR',
        ...identity,
      },
      metric: 'fixed_visible_ipv4_address_count',
    }),
    (error: unknown) => error instanceof DomeyeReadModelError
      && error.code === 'identity_conflict',
  )
})
