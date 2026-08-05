import { describe, expect, it } from 'vitest'

import { normalizeCountryOutageTrendProduct } from '@/utils/normalize'

function product() {
  const snapshot = {
    incident_id: 'incident-s4',
    publication_id: 'publication-s4',
    revision: 1,
    data_through: '2026-02-28T00:10:00Z',
    collector_id: 'rrc25',
    window_start_utc: '2026-02-28T00:00:00Z',
    window_end_utc: '2026-02-28T00:10:00Z',
  }
  return {
    schema_version: 'country_outage_trend_product_v1',
    algorithm_version: 'country_outage_trend_product_s4_v1',
    product_id: 'trend_product_v1_test',
    profile_id: 'profile-test',
    analysis_id: 'analysis-test',
    graph_id: 'graph-test',
    snapshot,
    profile: {},
    contexts: {},
    evidence_graph: {
      schema_version: 'country_outage_evidence_graph_v1',
      graph_id: 'graph-test',
      profile_id: 'profile-test',
      analysis_id: 'analysis-test',
      hypothesis_nodes_allowed: false,
      causal_relations_allowed: false,
      nodes: [
        {
          node_id: 'claim-1',
          node_type: 'Claim',
          evidence_refs: ['evidence-1'],
          limitation_refs: ['limitation-1'],
          unknown_refs: ['unknown-1'],
        },
        { node_id: 'evidence-1', node_type: 'Evidence' },
        { node_id: 'limitation-1', node_type: 'Limitation' },
        { node_id: 'unknown-1', node_type: 'Unknown' },
      ],
      edges: [
        { from: 'claim-1', relation: 'supported_by', to: 'evidence-1' },
        { from: 'claim-1', relation: 'limited_by', to: 'limitation-1' },
        { from: 'claim-1', relation: 'unknown_about', to: 'unknown-1' },
      ],
    },
    claim_ids: ['claim-1'],
    render_contract: {
      source_product_id: 'trend_product_v1_test',
    },
  }
}

describe('国家中断趋势制品归一化', () => {
  it('接受 RRC25 同身份白名单图', () => {
    expect(normalizeCountryOutageTrendProduct(product()).product_id)
      .toBe('trend_product_v1_test')
  })

  it('拒绝 Hypothesis 能力开关', () => {
    const payload = product()
    payload.evidence_graph.hypothesis_nodes_allowed = true
    expect(() => normalizeCountryOutageTrendProduct(payload))
      .toThrow('国家中断趋势制品 v1 响应格式异常')
  })

  it('拒绝无 Evidence 的 Claim', () => {
    const payload = product()
    payload.evidence_graph.nodes[0]!.evidence_refs = []
    expect(() => normalizeCountryOutageTrendProduct(payload))
      .toThrow('国家中断趋势 Claim 缺少')
  })

  it('拒绝悬空或自由关系', () => {
    const payload = product()
    payload.evidence_graph.edges[0]!.relation = 'caused_by'
    expect(() => normalizeCountryOutageTrendProduct(payload))
      .toThrow('国家中断趋势证据关系无效')
  })
})

