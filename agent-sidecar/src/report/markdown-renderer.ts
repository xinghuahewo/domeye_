import type { CountryOutageReportDocument } from './contracts.js'

function singleLine(value: string): string {
  return value
    .normalize('NFC')
    .replace(/[\u0000-\u001f\u007f-\u009f]/gu, ' ')
    .replace(/\s+/gu, ' ')
    .trim()
}

function neutralizeUriSchemes(value: string): string {
  return value.replace(
    /(?:j\s*a\s*v\s*a\s*s\s*c\s*r\s*i\s*p\s*t|v\s*b\s*s\s*c\s*r\s*i\s*p\s*t|d\s*a\s*t\s*a|f\s*i\s*l\s*e|h\s*t\s*t\s*p\s*s?|f\s*t\s*p)\s*:/giu,
    (scheme) => `${scheme.slice(0, -1).replace(/\s+/gu, '')}：`,
  )
}

function escapeMarkdownText(value: string): string {
  const normalized = neutralizeUriSchemes(singleLine(value))
  return [...normalized]
    .map((character) => {
      switch (character) {
        case '&':
          return '&amp;'
        case '<':
          return '&lt;'
        case '>':
          return '&gt;'
        case '"':
          return '&quot;'
        case "'":
          return '&#39;'
        case '\\':
          return '&#92;'
        case '`':
          return '&#96;'
        case '*':
        case '_':
        case '[':
        case ']':
        case '{':
        case '}':
        case '(':
        case ')':
        case '#':
        case '!':
        case '|':
        case '~':
        case '+':
        case '-':
          return `\\${character}`
        default:
          return character
      }
    })
    .join('')
}

function escapeCodeText(value: string): string {
  return [...neutralizeUriSchemes(singleLine(value))]
    .map((character) => {
      switch (character) {
        case '&':
          return '&amp;'
        case '<':
          return '&lt;'
        case '>':
          return '&gt;'
        case '`':
          return '&#96;'
        default:
          return character
      }
    })
    .join('')
}

function yamlScalar(
  value: string | number | boolean | null | undefined,
): string {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? String(value) : 'null'
  }
  if (typeof value === 'boolean') return String(value)
  return JSON.stringify(value ?? '')
    .replaceAll('\u2028', '\\u2028')
    .replaceAll('\u2029', '\\u2029')
}

function frontMatter(
  key: string,
  value: string | number | boolean | null | undefined,
): string {
  return `${key}: ${yamlScalar(value)}`
}

type MutableAliasCertificationIdentity = Required<
  Pick<
    CountryOutageReportDocument['model'],
    | 'modelRevisionKind'
    | 'immutableRevisionAvailable'
    | 'limitation'
    | 'certificationValidUntil'
    | 'certifiedScenarioSetId'
    | 'certifiedInputScope'
  >
>

function mutableAliasCertificationIdentity(
  model: CountryOutageReportDocument['model'],
): MutableAliasCertificationIdentity | undefined {
  if (
    model.adapter !== 'pi-sdk' ||
    model.runtimeIdentity !== 'formal' ||
    model.modelRevisionKind !== 'mutable_alias' ||
    model.immutableRevisionAvailable !== false ||
    typeof model.limitation !== 'string' ||
    model.limitation.length === 0 ||
    typeof model.certificationValidUntil !== 'string' ||
    model.certificationValidUntil.length === 0 ||
    typeof model.certifiedScenarioSetId !== 'string' ||
    model.certifiedScenarioSetId.length === 0 ||
    typeof model.certifiedInputScope !== 'string' ||
    model.certifiedInputScope.length === 0
  ) {
    return undefined
  }
  return {
    modelRevisionKind: 'mutable_alias',
    immutableRevisionAvailable: false,
    limitation: model.limitation,
    certificationValidUntil: model.certificationValidUntil,
    certifiedScenarioSetId: model.certifiedScenarioSetId,
    certifiedInputScope: model.certifiedInputScope,
  }
}

function evidenceLine(references: string[]): string {
  return references.length > 0
    ? `> 证据定位：${references.map((item) => `\`${escapeCodeText(item)}\``).join('、')}`
    : ''
}

export function renderReportMarkdown(
  document: CountryOutageReportDocument,
): string {
  const mutableAliasIdentity =
    mutableAliasCertificationIdentity(document.model)
  const lines = [
    '---',
    frontMatter('artifact_id', document.artifactId),
    frontMatter('report_content_sha256', document.reportContentSha256),
    frontMatter('fact_set_id', document.factSetId),
    frontMatter('event_reference', document.event.legacy_reference),
    frontMatter('country_code', document.event.country_code),
    frontMatter('country_name', document.event.country_name),
    frontMatter('incident_id', document.snapshot.incidentId),
    frontMatter('publication_id', document.snapshot.publicationId),
    frontMatter('revision', document.snapshot.revision),
    frontMatter('collector', document.snapshot.collectorId),
    frontMatter('data_through', document.snapshot.dataThrough),
    frontMatter('generated_at', document.generatedAt),
    frontMatter('model_provider', document.model.provider),
    frontMatter('model', document.model.model),
    frontMatter('model_version', document.model.modelVersion),
    ...(mutableAliasIdentity
      ? [
          frontMatter(
            'model_revision_kind',
            mutableAliasIdentity.modelRevisionKind,
          ),
          frontMatter(
            'immutable_revision_available',
            mutableAliasIdentity.immutableRevisionAvailable,
          ),
          frontMatter(
            'model_identity_limitation',
            mutableAliasIdentity.limitation,
          ),
          frontMatter(
            'certification_valid_until',
            mutableAliasIdentity.certificationValidUntil,
          ),
          frontMatter(
            'certified_scenario_set_id',
            mutableAliasIdentity.certifiedScenarioSetId,
          ),
          frontMatter(
            'certified_input_scope',
            mutableAliasIdentity.certifiedInputScope,
          ),
        ]
      : []),
    frontMatter(
      'report_specification',
      document.reportSpecificationVersion,
    ),
    frontMatter('project_knowledge', document.projectKnowledgeVersion),
    frontMatter('validator_rules', document.validatorRulesVersion),
    frontMatter('skill_bundle_sha256', document.skillBundleSha256),
    frontMatter('ai_generated', true),
    frontMatter('human_reviewed', false),
    '---',
    '',
    `# ${escapeMarkdownText(document.draft.title)}`,
    '',
    escapeMarkdownText(document.draft.subtitle),
    '',
    '> 本报告由 AI 生成并经机器校验，未经人工审核。它只描述 RRC25 的 BGP 控制面观测。',
    '',
    escapeMarkdownText(document.draft.summary.text),
    '',
    evidenceLine(document.draft.summary.evidenceRefs),
    '',
    '## 最值得关注的数字',
    '',
    '| 指标 | 观测结果 |',
    '|---|---:|',
    ...document.draft.highlights.map(
      (item) =>
        `| ${escapeMarkdownText(item.label)} | ${escapeMarkdownText(item.value)} |`,
    ),
    '',
    ...document.draft.sections.flatMap((section) => [
      ...(section.id === 'key_numbers'
        ? []
        : [`## ${escapeMarkdownText(section.title)}`, '']),
      ...section.paragraphs.flatMap((paragraph) => [
        escapeMarkdownText(paragraph.text),
        '',
        evidenceLine(paragraph.evidenceRefs),
        '',
      ]),
    ]),
    '## 不能仅凭本报告回答的问题',
    '',
    ...document.draft.unknowns.map(
      (item) => `- ${escapeMarkdownText(item)}`,
    ),
    '',
    '## 制品与证据说明',
    '',
    `- 报告制品：\`${escapeCodeText(document.artifactId)}\``,
    `- 报告内容摘要：\`${escapeCodeText(document.reportContentSha256)}\``,
    `- 事实集合：\`${escapeCodeText(document.factSetId)}\``,
    `- 固定快照：\`${escapeCodeText(document.snapshot.publicationId)}\`，revision ${document.snapshot.revision}`,
    `- 观测源：${escapeMarkdownText(document.snapshot.collectorId)}`,
    `- 模型：${escapeMarkdownText(document.model.provider)}/${escapeMarkdownText(document.model.model)}/${escapeMarkdownText(document.model.modelVersion)}`,
    ...(mutableAliasIdentity
      ? [
          '- 模型引用类型：可变别名（mutable_alias）',
          '- 不可变权重 revision：供应方未提供',
          `- 模型身份限制：${escapeMarkdownText(mutableAliasIdentity.limitation)}`,
          `- 认证有效至：${escapeMarkdownText(mutableAliasIdentity.certificationValidUntil)}`,
          `- 认证场景集：\`${escapeCodeText(mutableAliasIdentity.certifiedScenarioSetId)}\``,
          `- 认证输入范围：\`${escapeCodeText(mutableAliasIdentity.certifiedInputScope)}\``,
        ]
      : []),
    `- 校验规则：${escapeMarkdownText(document.validatorRulesVersion)}`,
    `- Skill 包摘要：\`${escapeCodeText(document.skillBundleSha256)}\``,
    `- 生成时间：${escapeMarkdownText(document.generatedAt)}`,
    '- AI 生成：是；人工审核：否',
    '',
  ]
  return lines
    .filter((line, index) => line || lines[index - 1] !== '')
    .join('\n')
}
