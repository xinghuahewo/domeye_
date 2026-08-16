import assert from 'node:assert/strict'
import test from 'node:test'

import type { CountryOutageReportDocument } from '../src/report/contracts.js'
import { renderReportMarkdown } from '../src/report/markdown-renderer.js'
import { MUTABLE_MODEL_ALIAS_LIMITATION_ZH } from '../src/pi/index.js'

function reportDocument(): CountryOutageReportDocument {
  return {
    schemaVersion: 'country_outage_report_document_v1',
    artifactId: 'report_markdown_security_test',
    reportContentSha256: 'a'.repeat(64),
    reportSpecificationVersion: 'country_outage_report_spec_v1',
    projectKnowledgeVersion: 'country_outage_report_skill_v6',
    validatorRulesVersion: 'country_outage_report_validator_rules_v5',
    skillBundleSha256: 'b'.repeat(64),
    generatedAt: '2026-07-29T12:00:00.000Z',
    aiGenerated: true,
    humanReviewed: false,
    event: {
      incident_id: 'incident-test',
      legacy_reference:
        'country_outage/2026-02-27 09:12:32/IR/1/r',
      event_type: 'country_outage',
      country_code: 'IR',
      country_name: '伊朗',
      display_name: '伊朗 BGP 路由观测',
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
    factSetId: 'facts-test',
    model: {
      provider: 'test',
      model: 'safe-model',
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
      subtitle: '窗口内路由可见性下降',
      summary: {
        text: 'RRC25 观察到伊朗相关 BGP 控制面可见性下降。',
        evidenceRefs: ['overview:/observation_scope'],
      },
      highlights: [
        {
          label: '窗口最低覆盖率',
          value: '82.32%',
          evidenceRefs: ['series:/series/1'],
        },
      ],
      sections: [
        {
          id: 'scope',
          title: '观测范围',
          paragraphs: [
            {
              text: '本报告只描述 RRC25 的 BGP 控制面。',
              evidenceRefs: ['overview:/observation_scope'],
            },
          ],
        },
      ],
      unknowns: ['现有证据不能回答用户与业务影响'],
    },
  }
}

test('Markdown 正常内容保留层级且 front matter 使用安全标量', () => {
  const markdown = renderReportMarkdown(reportDocument())
  assert.match(markdown, /^# 伊朗 BGP 路由可见性观测报告$/mu)
  assert.match(markdown, /^country_name: "伊朗"$/mu)
  assert.match(markdown, /^revision: 1$/mu)
  assert.match(markdown, /^ai_generated: true$/mu)
  assert.match(
    markdown,
    /^validator_rules: "country_outage_report_validator_rules_v5"$/mu,
  )
  assert.doesNotMatch(markdown, /mutable_alias|认证有效至/u)
})

test('Markdown 条件展示正式 Pi 可变别名限制、有效期与认证输入范围', () => {
  const document = reportDocument()
  document.model = {
    ...document.model,
    adapter: 'pi-sdk',
    piVersion: '0.84.1',
    runtimeIdentity: 'formal',
    modelRevisionKind: 'mutable_alias',
    immutableRevisionAvailable: false,
    limitation: MUTABLE_MODEL_ALIAS_LIMITATION_ZH,
    certificationValidUntil: '2026-08-12T16:00:00Z',
    certifiedScenarioSetId:
      'country-outage-certified-scenarios-v1',
    certifiedInputScope: 'legal_country_outage_rrc25_v1',
  }
  const markdown = renderReportMarkdown(document)

  assert.match(
    markdown,
    /^model_revision_kind: "mutable_alias"$/mu,
  )
  assert.match(markdown, /^immutable_revision_available: false$/mu)
  assert.match(
    markdown,
    /^certification_valid_until: "2026-08-12T16:00:00Z"$/mu,
  )
  assert.match(
    markdown,
    /^certified_scenario_set_id: "country-outage-certified-scenarios-v1"$/mu,
  )
  assert.match(
    markdown,
    /^certified_input_scope: "legal_country_outage_rrc25_v1"$/mu,
  )
  assert.match(
    markdown,
    new RegExp(MUTABLE_MODEL_ALIAS_LIMITATION_ZH, 'u'),
  )
  assert.match(
    markdown,
    /不可变权重 revision：供应方未提供/u,
  )
  assert.match(
    markdown,
    /认证场景集：`country-outage-certified-scenarios-v1`/u,
  )
  assert.match(
    markdown,
    /认证输入范围：`legal_country_outage_rrc25_v1`/u,
  )
})

test('Markdown 将 HTML、事件属性、危险 URI、链接和换行标题注入降为字面文本', () => {
  const document = reportDocument()
  document.artifactId = 'report\n---\nforged: true'
  document.event.country_name = '伊朗\n---\nadmin: true'
  document.model.provider = 'test\nprovider_forged: true'
  document.draft.title =
    '伊朗 BGP 路由观测 <script>alert(1)</script>\n# 注入标题'
  document.draft.subtitle =
    '[点击](javascript:alert(1))\n## 注入副标题'
  document.draft.summary.text =
    '<img src=x onerror=alert(1)> [外链](https://evil.example/x)'
  document.draft.highlights[0]!.label =
    '指标\n| 伪造 | 表格 |'
  document.draft.highlights[0]!.value =
    '<svg onload=alert(1)>javascript:alert(1)'
  document.draft.sections[0]!.title =
    '范围\n---\n<script>alert(1)</script>'
  document.draft.sections[0]!.paragraphs[0]!.text =
    '# 伪造正文\n<a href="javascript:alert(1)">链接</a>'
  document.draft.unknowns[0] =
    '- 伪造列表\n![图](data:text/html,attack)'

  const markdown = renderReportMarkdown(document)
  assert.equal(markdown.match(/^---$/gmu)?.length, 2)
  assert.match(
    markdown,
    /artifact_id: "report\\n---\\nforged: true"/u,
  )
  assert.match(
    markdown,
    /country_name: "伊朗\\n---\\nadmin: true"/u,
  )
  assert.match(
    markdown,
    /model_provider: "test\\nprovider_forged: true"/u,
  )
  assert.doesNotMatch(markdown, /<(?:script|img|svg|a)\b/iu)
  assert.doesNotMatch(markdown, /(?:javascript|data|https):/iu)
  assert.doesNotMatch(markdown, /\]\s*\(/u)
  assert.doesNotMatch(markdown, /^#{1,6} 注入/gmu)
  assert.doesNotMatch(markdown, /^#{1,6} 伪造/gmu)
  assert.doesNotMatch(markdown, /^\| 伪造 \|/gmu)
  assert.doesNotMatch(markdown, /^-\s+伪造列表$/gmu)
  assert.match(markdown, /&lt;script&gt;/u)
  assert.ok(
    markdown.includes(
      '&lt;img src=x onerror=alert\\(1\\)&gt;',
    ),
  )
  assert.match(markdown, /javascript：alert/u)
  assert.match(markdown, /https：\/\/evil\.example/u)
  assert.match(markdown, /\\# 注入标题/u)
})
