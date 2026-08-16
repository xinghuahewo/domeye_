import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ArtifactBuildInputError,
  COUNTRY_OUTAGE_MARKDOWN_MAX_BYTES,
  CountryOutageArtifactBuilder,
} from '../src/report/artifact-builder.js'
import type { CountryOutageReportDocument } from '../src/report/contracts.js'

function reportDocument(): CountryOutageReportDocument {
  return {
    schemaVersion: 'country_outage_report_document_v1',
    artifactId: 'report_artifact_builder_test',
    reportContentSha256: 'a'.repeat(64),
    reportSpecificationVersion: 'country_outage_report_spec_v1',
    projectKnowledgeVersion: 'country_outage_report_skill_v6',
    validatorRulesVersion: 'country_outage_report_validator_rules_v5',
    skillBundleSha256: 'd'.repeat(64),
    generatedAt: '2026-07-28T09:30:45.000Z',
    aiGenerated: true,
    humanReviewed: false,
    event: {
      incident_id: 'incident-test',
      legacy_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
      event_type: 'country_outage',
      country_code: 'IR',
      country_name: '伊朗',
      display_name: '伊朗',
    },
    snapshot: {
      incidentId: 'incident-test',
      publicationId: 'publication-test',
      revision: 1,
      dataThrough: '2026-02-28T15:00:00Z',
      isFinal: true,
      cohortId: 'cohort-test',
      collectorId: 'rrc25',
      windowStartUtc: '2026-02-28T10:05:00Z',
      windowEndUtc: '2026-02-28T15:00:00Z',
    },
    factSetId: 'facts_test',
    model: {
      provider: 'test',
      model: 'test',
      modelVersion: '1',
      adapter: 'deterministic-acceptance',
    },
    validation: {
      passed: true,
      errors: [],
      warnings: [],
      checkedEvidenceRefs: ['overview:/observation_scope'],
    },
    draft: {
      schemaVersion: 'country_outage_report_draft_v1',
      title: '伊朗 BGP 路由可见性观测报告',
      subtitle: '窗口内可见性下降',
      summary: {
        text: 'RRC25 观察到控制面可见性下降。',
        evidenceRefs: ['overview:/observation_scope'],
      },
      highlights: [
        {
          label: '观测源',
          value: 'RRC25',
          evidenceRefs: ['overview:/observation_scope'],
        },
      ],
      sections: [
        {
          id: 'scope',
          title: '观测范围',
          paragraphs: [
            {
              text: '只描述 RRC25 的 BGP 控制面。',
              evidenceRefs: ['overview:/observation_scope'],
            },
          ],
        },
      ],
      unknowns: [
        '全国数据面状态',
        '用户与业务影响',
        '原因与责任',
        '窗口之后是否完全恢复',
      ],
    },
  }
}

test('Markdown 与 PDF 共享制品身份并分别计算摘要', async () => {
  const builder = new CountryOutageArtifactBuilder({
    async render() {
      return Buffer.from('%PDF-1.7\nacceptance\n%%EOF\n')
    },
  })
  const result = await builder.build(reportDocument())
  assert.equal(result.artifactId, 'report_artifact_builder_test')
  assert.equal(result.markdown.status, 'ready')
  assert.equal(result.pdf.status, 'ready')
  if (result.markdown.status !== 'ready' || result.pdf.status !== 'ready') {
    assert.fail('两个制品应均可下载')
  }
  assert.match(
    result.markdown.artifact.filename,
    /^IR_country-outage_20260228T100500Z_20260228T150000Z_r1_20260728T093045Z\.md$/,
  )
  assert.match(result.pdf.artifact.filename, /\.pdf$/)
  assert.equal(result.markdown.artifact.sha256.length, 64)
  assert.equal(result.pdf.artifact.sha256.length, 64)
  assert.notEqual(
    result.markdown.artifact.sha256,
    result.pdf.artifact.sha256,
  )
})

test('PDF 单项失败不覆盖成功的 Markdown', async () => {
  const builder = new CountryOutageArtifactBuilder({
    async render() {
      throw new Error('PDF 字体不可用')
    },
  })
  const result = await builder.build(reportDocument())
  assert.equal(result.markdown.status, 'ready')
  assert.equal(result.pdf.status, 'failed')
  if (result.pdf.status === 'failed') {
    assert.match(result.pdf.error.message, /字体不可用/)
  }
})

test('Markdown 超限与 PDF 结果相互独立', async () => {
  const document = reportDocument()
  document.draft.summary.text = '观'.repeat(
    COUNTRY_OUTAGE_MARKDOWN_MAX_BYTES + 1,
  )
  const builder = new CountryOutageArtifactBuilder({
    async render() {
      return Buffer.from('%PDF-1.7\nacceptance\n%%EOF\n')
    },
  })
  const result = await builder.build(document)
  assert.equal(result.markdown.status, 'failed')
  assert.equal(result.pdf.status, 'ready')
})

test('未通过机器校验的文档不生成任何下载制品', async () => {
  const document = reportDocument()
  document.validation.passed = false
  const builder = new CountryOutageArtifactBuilder({
    async render() {
      assert.fail('不得调用 PDF 渲染器')
    },
  })
  await assert.rejects(
    builder.build(document),
    ArtifactBuildInputError,
  )
})
