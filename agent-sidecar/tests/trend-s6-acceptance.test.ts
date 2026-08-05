import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import type {
  CountryOutageAsnPage,
  CountryOutageTrendProduct,
  ObservationBatch,
} from '../src/domain/contracts.js'
import { DOMEYE_ONLY_EVIDENCE_MODE } from '../src/qa/contracts.js'
import { DeterministicCountryOutageQuestionEngine } from '../src/qa/deterministic-question-engine.js'
import { CountryOutageArtifactBuilder } from '../src/report/artifact-builder.js'
import { DeterministicAcceptanceNarrator } from '../src/report/deterministic-narrator.js'
import { renderReportMarkdown } from '../src/report/markdown-renderer.js'
import { CountryOutageReportCompiler } from '../src/report/report-compiler.js'
import { canonicalJsonSha256 } from '../src/shared/deterministic-json.js'
import {
  a4AsnPage,
  a4ObservationBatch,
} from './helpers/a4-country-outage-fixture.js'


// 生产 prepare 会把 Sidecar 复制到最小不可变候选目录，但仍通过受信的
// COA_PROJECT_ROOT 绑定完整源码归档。测试必须从该绑定根读取同一 Python 候选，
// 不能要求把 backend/dev/docs 扩进 Sidecar 运行包。
const repositoryRoot = process.env.COA_PROJECT_ROOT
  ?? fileURLToPath(new URL('../../..', import.meta.url))

function acceptedProduct(): CountryOutageTrendProduct {
  return JSON.parse(execFileSync(
    'python3',
    ['dev/verify_country_outage_trend_analysis_s6.py', '--emit-product'],
    { cwd: repositoryRoot, encoding: 'utf8' },
  )) as CountryOutageTrendProduct
}

function alignBatchToProduct(
  batch: ObservationBatch,
  product: CountryOutageTrendProduct,
): ObservationBatch {
  const snapshot = product.snapshot
  const profile = product.profile as {
    snapshot: { event_reference: string; country_code: string }
  }
  const startMilliseconds = Date.parse(snapshot.window_start_utc)
  const utcAt = (index: number) => new Date(startMilliseconds + index * 300_000)
    .toISOString().replace('.000Z', 'Z')
  const shanghaiAt = (index: number) => new Date(startMilliseconds + index * 300_000 + 8 * 60 * 60 * 1000)
    .toISOString().replace('.000Z', '+08:00')
  const envelope = {
    incident_id: snapshot.incident_id,
    publication_id: snapshot.publication_id,
    revision: snapshot.revision,
    data_through: snapshot.data_through,
    is_final: true,
    window_start_utc: snapshot.window_start_utc,
    window_end_utc: snapshot.window_end_utc,
  }
  Object.assign(batch.overview, envelope)
  Object.assign(batch.series, envelope)
  Object.assign(batch.audit, envelope)
  Object.assign(batch.resolution, {
    incident_id: snapshot.incident_id,
    publication_id: snapshot.publication_id,
    latest_revision: snapshot.revision,
    data_through: snapshot.data_through,
    is_final: true,
    legacy_reference: profile.snapshot.event_reference,
  })
  Object.assign(batch.overview.event_identity, {
    incident_id: snapshot.incident_id,
    legacy_reference: profile.snapshot.event_reference,
    country_code: profile.snapshot.country_code,
    country_name: '验收国家',
    display_name: '验收国家 BGP 路由观测',
  })
  Object.assign(batch.overview.observation_scope, {
    collector_id: 'rrc25',
    collector_ids: ['rrc25'],
    collector_count: 1,
    window_start_utc: snapshot.window_start_utc,
    window_start_local: shanghaiAt(0),
    window_end_utc: snapshot.window_end_utc,
    window_end_local: shanghaiAt(11),
    timezone: 'Asia/Shanghai',
    observation_count: 12,
    expected_observation_count: 12,
    missing_observation_count: 0,
    last_observation_at_utc: snapshot.data_through,
    last_observation_at_local: shanghaiAt(11),
  })
  batch.series.series = batch.series.series.slice(0, 12).map((slot, index) => ({
    ...slot,
    observed_at_utc: utcAt(index),
    observed_at_local: shanghaiAt(index),
  }))
  batch.series.resource_series = batch.series.resource_series.slice(0, 12).map((slot, index) => ({
    ...slot,
    observed_at_utc: utcAt(index),
    observed_at_local: shanghaiAt(index),
  }))
  batch.series.metric_extrema = {}
  batch.series.resource_metric_extrema = {}
  Object.assign(batch.overview.capabilities, {
    address_families: { state: 'unavailable', reason: '同制品验收不复用底层扩展序列' },
    update_activity: { state: 'unavailable', reason: '同制品验收不复用底层扩展序列' },
    country_resources: { state: 'unavailable', reason: '同制品验收不复用底层扩展序列' },
  })
  batch.trendProduct = structuredClone(product)
  return batch
}

function alignAsnPageToProduct(
  page: CountryOutageAsnPage,
  product: CountryOutageTrendProduct,
): CountryOutageAsnPage {
  const snapshot = product.snapshot
  Object.assign(page, {
    incident_id: snapshot.incident_id,
    publication_id: snapshot.publication_id,
    revision: snapshot.revision,
    data_through: snapshot.data_through,
    is_final: true,
    window_start_utc: snapshot.window_start_utc,
    window_end_utc: snapshot.window_end_utc,
  })
  return page
}

async function compileAcceptedCandidate() {
  const product = acceptedProduct()
  const batch = alignBatchToProduct(a4ObservationBatch(), product)
  const page = alignAsnPageToProduct(a4AsnPage(), product)
  const compiler = new CountryOutageReportCompiler({
    client: {
      async getObservationBatch() {
        return structuredClone(batch)
      },
      async getAsns() {
        return structuredClone(page)
      },
    },
    narrator: new DeterministicAcceptanceNarrator(),
    now: () => new Date('2026-08-05T00:00:00Z'),
  })
  return {
    product,
    compiled: await compiler.compileWithEvidence(batch.resolution.legacy_reference),
  }
}

test('S6 报告、Markdown、PDF 与 JSON 下载消费同一 Python 候选', async () => {
  const { product, compiled } = await compileAcceptedCandidate()
  const claimNodes = product.evidence_graph.nodes.filter(
    (node) => node.node_type === 'Claim',
  )
  const assessment = compiled.document.draft.sections
    .find((section) => section.id === 'assessment')
  assert.ok(assessment)
  for (const claim of claimNodes) {
    assert.ok(
      assessment.paragraphs.some((paragraph) => paragraph.text === claim.text),
      `报告缺少冻结 Claim：${claim.claim_kind}`,
    )
  }
  assert.equal(compiled.evidence.facts.trendProduct?.product_id, product.product_id)
  assert.equal(compiled.evidence.facts.trendProduct?.graph_id, product.graph_id)
  assert.equal(compiled.document.validation.passed, true)

  const markdown = renderReportMarkdown(compiled.document)
  const unescapedMarkdown = markdown.replace(
    /\\([\\`*_\[\]{}()#!|~+\-])/gu,
    '$1',
  )
  for (const claim of claimNodes) {
    assert.ok(
      unescapedMarkdown.includes(claim.text ?? ''),
      `Markdown 缺少冻结 Claim：${claim.claim_kind}`,
    )
  }

  const artifacts = await new CountryOutageArtifactBuilder({
    async render(document) {
      const texts = document.draft.sections
        .flatMap((section) => section.paragraphs)
        .map((paragraph) => paragraph.text)
        .join('\n')
      return Buffer.from(`%PDF-1.4\n${texts}\n%%EOF`, 'utf8')
    },
  }).build(compiled.document)
  assert.equal(artifacts.markdown.status, 'ready')
  assert.equal(artifacts.pdf.status, 'ready')
  if (artifacts.markdown.status === 'ready') {
    assert.ok(artifacts.markdown.artifact.content.includes(claimNodes[0]?.text ?? ''))
  }
  if (artifacts.pdf.status === 'ready') {
    assert.ok(artifacts.pdf.artifact.content.includes(claimNodes[0]?.text ?? ''))
  }

  assert.equal(
    canonicalJsonSha256(product),
    '69c98516a5bf1d49576234da2d97fb5389fe92005cbcdd6de96259fbe83ed48b',
  )
  assert.deepEqual(JSON.parse(JSON.stringify(product)), product)
})

test('S6 组合追问直接引用同一 product_id 的冻结 Claim', async () => {
  const { product, compiled } = await compileAcceptedCandidate()
  const context = {
    report: compiled.document,
    facts: compiled.evidence.facts,
    asnPages: compiled.evidence.asnPages,
  }
  const engine = new DeterministicCountryOutageQuestionEngine()
  const cases = [
    ['这条曲线由哪些阶段组成？', 'phase_sequence'],
    ['最快恶化槽和对应数值是什么？', 'fastest_change'],
    ['哪些 ASN 持续未回到起点？', 'asn_persistence'],
    ['UPDATE 峰值与状态谷值是同槽还是相邻槽？', 'activity_alignment'],
    ['IPv4 和 IPv6 的地址族分化如何？', 'address_family_comparison'],
    ['目标在同期全球参照中的分布位置？', 'contemporaneous_reference'],
    ['查看趋势结论依据。', 'window_state'],
  ] as const
  for (let index = 0; index < cases.length; index += 1) {
    const [question, claimKind] = cases[index]!
    const answer = await engine.answer({
      schemaVersion: 'country_outage_question_request_v1',
      requestId: `s6-question-${index}`,
      idempotencyKey: `s6-question-${index}`,
      binding: {
        reportArtifactId: compiled.document.artifactId,
        reportContentSha256: compiled.document.reportContentSha256,
        factSetId: compiled.evidence.facts.factSetId,
        snapshot: compiled.evidence.facts.snapshot,
        evidenceMode: DOMEYE_ONLY_EVIDENCE_MODE,
      },
      question,
    }, context)
    assert.equal(answer.kind, 'fact')
    assert.ok(answer.evidenceRefs.some((ref) => ref.startsWith('trend:/nodes/')))
    assert.equal(answer.evidence[0]?.source, 'derived_fact')
    assert.match(answer.evidence[0]?.value ?? '', new RegExp(claimKind))
    assert.equal(compiled.evidence.facts.trendProduct?.product_id, product.product_id)
  }

  for (const question of [
    '这是一次攻击吗？',
    '有多少用户受影响？',
    '哪个 ASN 应对此负责？',
    '窗口结束后是否完全恢复？',
  ]) {
    const answer = await engine.answer({
      schemaVersion: 'country_outage_question_request_v1',
      requestId: `s6-boundary-${question}`,
      idempotencyKey: `s6-boundary-${question}`,
      binding: {
        reportArtifactId: compiled.document.artifactId,
        reportContentSha256: compiled.document.reportContentSha256,
        factSetId: compiled.evidence.facts.factSetId,
        snapshot: compiled.evidence.facts.snapshot,
        evidenceMode: DOMEYE_ONLY_EVIDENCE_MODE,
      },
      question,
    }, context)
    assert.equal(answer.kind, 'evidence_boundary')
    assert.ok(answer.missingEvidence.length > 0)
  }
})
