import assert from 'node:assert/strict'
import test from 'node:test'

import type { CountryOutageTrendProduct } from '../src/domain/contracts.js'
import { DomeyeCountryOutageClient } from '../src/domain/domeye-client.js'
import { a4ObservationBatch } from './helpers/a4-country-outage-fixture.js'


function trendProduct(): CountryOutageTrendProduct {
  const batch = a4ObservationBatch()
  return {
    schema_version: 'country_outage_trend_product_v1',
    product_id: 'trend_product_v1_client_test',
    profile_id: 'trend_profile_v1_client_test',
    analysis_id: 'trend_analysis_s2_client_test',
    graph_id: 'evidence_graph_v1_client_test',
    snapshot: {
      incident_id: batch.overview.incident_id,
      publication_id: batch.overview.publication_id,
      revision: batch.overview.revision,
      data_through: batch.overview.data_through!,
      collector_id: 'rrc25',
      window_start_utc: batch.overview.window_start_utc,
      window_end_utc: batch.overview.window_end_utc,
    },
    evidence_graph: {
      schema_version: 'country_outage_evidence_graph_v1',
      graph_id: 'evidence_graph_v1_client_test',
      profile_id: 'trend_profile_v1_client_test',
      analysis_id: 'trend_analysis_s2_client_test',
      nodes: [],
      edges: [],
      hypothesis_nodes_allowed: false,
      causal_relations_allowed: false,
    },
    render_contract: {
      source_product_id: 'trend_product_v1_client_test',
      surfaces: ['page', 'report', 'qa', 'markdown', 'pdf', 'json_download'],
      model_may_rewrite_deterministic_values: false,
    },
  }
}

test('客户端只在 capability 可用时读取并绑定同快照趋势制品', async () => {
  const batch = a4ObservationBatch()
  batch.overview.capabilities.trend_analysis = { state: 'available' }
  const product = trendProduct()
  const paths: string[] = []
  const fetchImplementation: typeof fetch = async (input) => {
    const url = new URL(
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input
          : input.url,
    )
    paths.push(url.pathname)
    if (url.pathname.endsWith('/events/resolve')) {
      return Response.json(batch.resolution)
    }
    if (url.pathname.endsWith('/overview')) return Response.json(batch.overview)
    if (url.pathname.endsWith('/series')) return Response.json(batch.series)
    if (url.pathname.endsWith('/audit')) return Response.json(batch.audit)
    if (url.pathname.endsWith('/trend')) return Response.json(product)
    return new Response('not found', { status: 404 })
  }
  const client = new DomeyeCountryOutageClient({
    baseUrl: 'http://domeye.test/api/v2/',
    fetchImplementation,
  })
  const result = await client.getObservationBatch(
    batch.resolution.legacy_reference,
  )
  assert.equal(result.trendProduct?.product_id, product.product_id)
  assert.equal(paths.filter((path) => path.endsWith('/trend')).length, 1)
})

test('客户端在 capability 未声明时保持旧报告读取路径', async () => {
  const batch = a4ObservationBatch()
  const paths: string[] = []
  const fetchImplementation: typeof fetch = async (input) => {
    const url = new URL(
      typeof input === 'string'
        ? input
        : input instanceof URL
          ? input
          : input.url,
    )
    paths.push(url.pathname)
    if (url.pathname.endsWith('/events/resolve')) return Response.json(batch.resolution)
    if (url.pathname.endsWith('/overview')) return Response.json(batch.overview)
    if (url.pathname.endsWith('/series')) return Response.json(batch.series)
    if (url.pathname.endsWith('/audit')) return Response.json(batch.audit)
    return new Response('not found', { status: 404 })
  }
  const client = new DomeyeCountryOutageClient({
    baseUrl: 'http://domeye.test/api/v2/',
    fetchImplementation,
  })
  const result = await client.getObservationBatch(batch.resolution.legacy_reference)
  assert.equal(result.trendProduct, undefined)
  assert.equal(paths.some((path) => path.endsWith('/trend')), false)
})

