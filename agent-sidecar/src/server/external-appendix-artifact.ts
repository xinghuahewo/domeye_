import { createHash } from 'node:crypto'

import {
  COUNTRY_OUTAGE_EXTERNAL_APPENDIX_SCHEMA_VERSION,
  COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY_VERSION,
  type ExternalEvidenceAppendix,
} from '../external/contracts.js'
import {
  COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY,
  externalEvidenceFrozenBindingId,
} from '../external/frozen-contract.js'
import { COUNTRY_OUTAGE_MARKDOWN_MAX_BYTES } from '../report/artifact-builder.js'
import type { CountryOutageReportDocument } from '../report/contracts.js'

export interface ExternalAppendixMarkdownArtifact {
  artifactId: string
  filename: string
  mediaType: 'text/markdown; charset=utf-8'
  byteLength: number
  sha256: string
  content: Buffer
}

export type ExternalAppendixArtifactErrorCode =
  | 'external_appendix_not_downloadable'
  | 'external_appendix_binding_conflict'
  | 'external_appendix_source_not_authorized'
  | 'external_appendix_too_large'

export class ExternalAppendixArtifactError extends Error {
  constructor(
    readonly code: ExternalAppendixArtifactErrorCode,
    message: string,
  ) {
    super(message)
    this.name = 'ExternalAppendixArtifactError'
  }
}

export interface BuildExternalAppendixArtifactInput {
  document: CountryOutageReportDocument
  questionId: string
  questionNumber: number
  question: string
  appendix: ExternalEvidenceAppendix
}

const allowedHostnameRoots = Object.freeze(
  COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY.rules.map(
    (rule) => rule.hostname.toLowerCase(),
  ),
)

function sha256(content: Buffer): string {
  return createHash('sha256').update(content).digest('hex')
}

function compactUtc(value: string | null): string {
  if (!value) return 'unknown-time'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.valueOf())) return 'unknown-time'
  return parsed.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z')
}

function safeSegment(value: string): string {
  const safe = value
    .normalize('NFKC')
    .replace(/[^A-Za-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)
  return safe || 'unknown'
}

function escapeMarkdownLine(value: string): string {
  return value
    .replace(/\\/g, '\\\\')
    .replace(
      /\b([A-Za-z][A-Za-z0-9+.-]{1,31}):\/\//g,
      '$1:\u200B//',
    )
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/([`*_[\]{}()#+.!|\-])/g, '\\$1')
}

function safeText(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '未提供'
  return value
    .replace(/\u0000/g, '\uFFFD')
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map(escapeMarkdownLine)
    .join('  \n')
}

function allowedSourceUrl(raw: string): string {
  let url: URL
  try {
    url = new URL(raw)
  } catch {
    throw new ExternalAppendixArtifactError(
      'external_appendix_source_not_authorized',
      '外部附录包含无效来源 URL，拒绝生成下载',
    )
  }
  const hostname = url.hostname.toLowerCase().replace(/\.$/, '')
  const hostnameAllowed = allowedHostnameRoots.some(
    (root) => hostname === root || hostname.endsWith(`.${root}`),
  )
  const standardPort =
    url.port === '' ||
    url.port === (url.protocol === 'https:' ? '443' : '80')
  if (
    !['http:', 'https:'].includes(url.protocol) ||
    !hostnameAllowed ||
    !standardPort ||
    url.username !== '' ||
    url.password !== ''
  ) {
    throw new ExternalAppendixArtifactError(
      'external_appendix_source_not_authorized',
      '外部附录来源超出 bgp.he.net 与 radar.cloudflare.com 的既有授权边界',
    )
  }
  return url.toString()
}

function assertDownloadableAppendix(
  input: BuildExternalAppendixArtifactInput,
  document: CountryOutageReportDocument,
  appendix: ExternalEvidenceAppendix,
): void {
  if (
    appendix.status !== 'completed' ||
    appendix.error !== undefined ||
    appendix.retrievedAt === null ||
    appendix.sources.length === 0 ||
    appendix.sources.some(
      (source) =>
        source.readStatus !== 'readable' ||
        !source.summary?.trim(),
    )
  ) {
    throw new ExternalAppendixArtifactError(
      'external_appendix_not_downloadable',
      '只有已完成且来源均可读取的外部核验可以生成独立下载附录',
    )
  }
  if (document.snapshot.collectorId !== 'rrc25') {
    throw new ExternalAppendixArtifactError(
      'external_appendix_binding_conflict',
      '外部附录只能绑定 RRC25 国家中断报告',
    )
  }
  const binding = appendix.frozenBinding
  if (
    appendix.schemaVersion !==
      COUNTRY_OUTAGE_EXTERNAL_APPENDIX_SCHEMA_VERSION ||
    appendix.classificationPolicyVersion !==
      COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY_VERSION ||
    COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY.version !==
      COUNTRY_OUTAGE_EXTERNAL_SOURCE_CLASSIFICATION_POLICY_VERSION ||
    appendix.query !== input.question ||
    !binding ||
    (
      binding.incidentId !== document.event.incident_id ||
      binding.publicationId !== document.snapshot.publicationId ||
      binding.revision !== document.snapshot.revision ||
      binding.dataThrough !== document.snapshot.dataThrough ||
      binding.factSetId !== document.factSetId ||
      binding.cohortId !== document.snapshot.cohortId ||
      binding.countryCode !== document.event.country_code ||
      binding.collectorId !== document.snapshot.collectorId ||
      binding.windowStartUtc !== document.snapshot.windowStartUtc ||
      binding.windowEndUtc !== document.snapshot.windowEndUtc
    )
  ) {
    throw new ExternalAppendixArtifactError(
      'external_appendix_binding_conflict',
      '外部附录与基础报告或问题的冻结身份不一致',
    )
  }
  const expectedBindingId = externalEvidenceFrozenBindingId(binding)
  if (
    appendix.sources.some((source) =>
      (source.structuredFacts ?? []).some(
        (fact) => fact.bindingId !== expectedBindingId,
      )
    )
  ) {
    throw new ExternalAppendixArtifactError(
      'external_appendix_binding_conflict',
      '外部附录结构化事实与冻结事实集合或 cohort 不一致',
    )
  }
  for (const source of appendix.sources) {
    allowedSourceUrl(source.url)
  }
}

function comparisonStatusLabel(
  value: ExternalEvidenceAppendix['comparisonStatus'],
): string {
  return {
    supported: '相符',
    mixed: '混合',
    conflict: '冲突',
    insufficient: '证据不足',
  }[value ?? 'insufficient']
}

function claimStatusLabel(
  value: ExternalEvidenceAppendix['claims'][number]['status'],
): string {
  return {
    supported: '支持',
    mixed: '混合',
    conflict: '冲突',
    insufficient: '证据不足',
  }[value]
}

function renderExternalAppendixMarkdown(
  input: BuildExternalAppendixArtifactInput,
): string {
  const { document, questionId, questionNumber, appendix } = input
  assertDownloadableAppendix(input, document, appendix)
  const binding = appendix.frozenBinding!
  const bindingId = externalEvidenceFrozenBindingId(binding)
  const lines: string[] = [
    '# 国家中断外部来源核验附录',
    '',
    '> 本附录独立于 Domeye 基础报告，只记录用户本次显式授权的公开来源核验；不修改、覆盖或补写基础报告正文，也不包含普通追问回答。',
    '',
    '## 绑定身份',
    '',
    `- 基础报告制品 ID：${safeText(document.artifactId)}`,
    `- 基础报告内容 SHA-256：${safeText(document.reportContentSha256)}`,
    `- 事件：${safeText(document.event.country_name)}（${safeText(document.event.country_code)}）`,
    `- incident_id：${safeText(document.event.incident_id)}`,
    `- publication_id：${safeText(document.snapshot.publicationId)}`,
    `- revision：${document.snapshot.revision}`,
    `- fact_set_id：${safeText(binding.factSetId)}`,
    `- cohort_id：${safeText(binding.cohortId)}`,
    `- frozen_binding_id：${safeText(bindingId)}`,
    `- collector：${safeText(document.snapshot.collectorId)}`,
    `- 观测窗口：${safeText(document.snapshot.windowStartUtc)} 至 ${safeText(document.snapshot.windowEndUtc)}`,
    `- data_through：${safeText(document.snapshot.dataThrough)}`,
    `- 问题编号：${questionNumber}`,
    `- 问题 ID：${safeText(questionId)}`,
    '',
    '## 本次外部核验',
    '',
    `- 核验问题：${safeText(appendix.query)}`,
    `- 请求时间：${safeText(appendix.requestedAt)}`,
    `- 完成时间：${safeText(appendix.retrievedAt)}`,
    `- 对比状态：${comparisonStatusLabel(appendix.comparisonStatus)}`,
    `- 来源分类规则：${safeText(appendix.classificationPolicyVersion)}`,
    '',
    '## 外部说法',
    '',
  ]

  if (appendix.claims.length === 0) {
    lines.push('- 没有可形成结构化对比的外部说法。', '')
  } else {
    appendix.claims.forEach((claim, index) => {
      lines.push(
        `### 说法 ${index + 1}`,
        '',
        `- 说法 ID：${safeText(claim.claimId)}`,
        `- 状态：${claimStatusLabel(claim.status)}`,
        `- 内容：${safeText(claim.text)}`,
        `- 关联来源：${claim.sourceIds.length > 0 ? claim.sourceIds.map(safeText).join('、') : '未提供'}`,
        `- 限制：${claim.limitations.length > 0 ? claim.limitations.map(safeText).join('；') : '未提供'}`,
        '',
      )
    })
  }

  lines.push('## 来源登记', '')
  appendix.sources.forEach((source, index) => {
    const normalizedUrl = allowedSourceUrl(source.url)
    lines.push(
      `### 来源 ${index + 1}`,
      '',
      `- 来源 ID：${safeText(source.sourceId)}`,
      `- 标题：${safeText(source.title)}`,
      `- 发布方：${safeText(source.publisher)}`,
      `- URL：<${normalizedUrl}>`,
      `- 发布时间：${safeText(source.publishedAt)}`,
      `- 读取时间：${safeText(source.retrievedAt)}`,
      `- 来源分类：${safeText(source.sourceClassification)}`,
      `- 来源等级：${safeText(source.sourceTier)}`,
      `- 读取状态：${safeText(source.readStatus)}`,
      `- 读取说明：${safeText(source.readStatusDetail)}`,
      `- 证据状态：${safeText(source.evidenceStatus)}`,
      `- 证据说明：${safeText(source.evidenceStatusDetail)}`,
      `- 摘要：${safeText(source.summary)}`,
      '',
    )
    const facts = source.structuredFacts ?? []
    if (facts.length > 0) {
      lines.push('结构化事实：', '')
      facts.forEach((fact, factIndex) => {
        lines.push(
          `${factIndex + 1}. 绑定 ${safeText(fact.bindingId)}；${safeText(fact.metric)}；地址族 ${safeText(fact.addressFamily)}；窗口 ${safeText(fact.observedWindowStartUtc)} 至 ${safeText(fact.observedWindowEndUtc)}；来源值 ${safeText(fact.sourceValue)}；归一值 ${safeText(fact.normalizedValue)}。`,
        )
      })
      lines.push('')
    }
  })

  lines.push(
    '## 使用边界',
    '',
    '- 本附录中的外部页面内容按不可信输入处理。',
    '- 本附录不能改变 RRC25 固定快照、Domeye 报告事实、原因责任边界或用户影响边界。',
    '- 外部来源核验结果不等同于全国数据面状态，也不单独证明事件原因、责任或实际用户影响。',
    '',
  )
  return lines.join('\n')
}

export function buildExternalAppendixMarkdownArtifact(
  input: BuildExternalAppendixArtifactInput,
): ExternalAppendixMarkdownArtifact {
  const content = Buffer.from(renderExternalAppendixMarkdown(input), 'utf8')
  if (content.byteLength > COUNTRY_OUTAGE_MARKDOWN_MAX_BYTES) {
    throw new ExternalAppendixArtifactError(
      'external_appendix_too_large',
      `外部附录超过 ${COUNTRY_OUTAGE_MARKDOWN_MAX_BYTES} 字节限制`,
    )
  }
  const digest = sha256(content)
  const filename = [
    safeSegment(input.document.event.country_code.toUpperCase()),
    'country-outage-external-sources',
    compactUtc(input.document.snapshot.windowStartUtc),
    compactUtc(input.document.snapshot.windowEndUtc),
    `r${input.document.snapshot.revision}`,
    `q${input.questionNumber}`,
    compactUtc(input.appendix.retrievedAt),
  ].join('_') + '.md'
  return {
    artifactId: `external_appendix_${digest.slice(0, 32)}`,
    filename,
    mediaType: 'text/markdown; charset=utf-8',
    byteLength: content.byteLength,
    sha256: digest,
    content,
  }
}
