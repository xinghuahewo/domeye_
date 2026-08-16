import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { normalizeCountryOutageTrendProduct } from '@/utils/normalize'


const repositoryRoot = fileURLToPath(new URL('../../../..', import.meta.url))
const rawProduct = JSON.parse(execFileSync(
  'python3',
  ['dev/verify_country_outage_trend_analysis_s6.py', '--emit-product'],
  { cwd: repositoryRoot, encoding: 'utf8' },
))
const product = normalizeCountryOutageTrendProduct(rawProduct)
const componentSource = readFileSync(
  new URL('../CountryOutageTrendAnalysis.vue', import.meta.url),
  'utf8',
)


describe('S6 同候选趋势页面', () => {
  it('消费 Python 验收器生成的同一 product_id 与 graph_id', () => {
    expect(product.product_id).toBe('trend_product_v1_4a62c0d73936f3b6174bc0493b1803fd')
    expect(product.graph_id).toBe('evidence_graph_v1_405fd628b95d954da40568c196a3976a')
    expect(product.render_contract.source_product_id).toBe(product.product_id)
    expect(product.snapshot.collector_id).toBe('rrc25')
    expect(product.contexts.contemporaneous_reference?.status).toBe('complete')
  })

  it('全部 Claim 都保持证据、限制与未知引用', () => {
    const nodes = product.evidence_graph.nodes
    const claims = nodes.filter((node) => node.node_type === 'Claim')
    expect(claims).toHaveLength(8)
    for (const claim of claims) {
      expect(claim.evidence_refs?.length).toBeGreaterThan(0)
      expect(claim.limitation_refs?.length).toBeGreaterThan(0)
      expect(claim.unknown_refs?.length).toBeGreaterThan(0)
    }
  })

  it('页面按验收阅读旅程显示身份、阶段、账本、同期参照和边界', () => {
    for (const marker of [
      'product.product_id',
      'product.snapshot.publication_id',
      'props.product.profile.analysis.phases',
      'props.product.profile.analysis.derived_facts',
      'props.product.contexts.contemporaneous_reference',
      'Evidence',
      'Limitation',
      'Unknown',
      '同期国家投影参照',
      '不是历史正常带',
      '不包含原因、攻击、用户影响、责任或窗口外完全恢复判断',
    ]) {
      expect(componentSource).toContain(marker)
    }
  })

  it('组件不重算阶段、不允许 Hypothesis 与因果关系', () => {
    expect(componentSource).not.toMatch(/detectPhase|classifyPattern|caused_by|Hypothesis/)
    expect(product.evidence_graph.hypothesis_nodes_allowed).toBe(false)
    expect(product.evidence_graph.causal_relations_allowed).toBe(false)
  })
})
