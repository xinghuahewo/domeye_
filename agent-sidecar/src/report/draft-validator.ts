import type { JsonObject } from '../domain/contracts.js'
import type {
  CountryOutageReportDraft,
  EvidenceParagraph,
  ReportEvidenceBundle,
  ReportHighlight,
  ReportSection,
  ReportValidationResult,
} from './contracts.js'

export const COUNTRY_OUTAGE_REPORT_VALIDATOR_RULES_VERSION =
  'country_outage_report_validator_rules_v5' as const

const sectionIds = [
  'scope',
  'key_numbers',
  'visibility',
  'asn_scope',
  'address_families',
  'updates',
  'end_state',
  'resources',
  'assessment',
] as const

const requiredUnknownPatterns = [
  /全国|数据面/,
  /用户|业务/,
  /原因|责任/,
  /窗口之后|后续|完全恢复/,
]

const forbiddenPositiveClaims = [
  {
    id: 'nationwide_outage',
    pattern:
      /全国(?:性)?(?:互联网|网络)?[^，,；;。！？\n]{0,8}(?:中断|断网|不可用|瘫痪)/,
  },
  {
    id: 'unsupported_cause',
    pattern:
      /(?:UPDATE|攻击|政策(?:行为|措施)?|配置错误|基础设施(?:故障)?)[^，,；;。！？\n]{0,16}(?:导致|造成|引发|引起|致使|所致|触发)/i,
  },
  {
    id: 'unsupported_cause',
    pattern:
      /(?:源于|归因于)[^，,；;。！？\n]{0,10}(?:攻击|政策(?:行为|措施)?|配置错误|基础设施(?:故障)?)/,
  },
  {
    id: 'unsupported_cause',
    pattern:
      /(?:事件|中断|变化)(?:的)?(?:原因|起因)[^，,；;。！？\n]{0,6}(?<!不)(?:就是|是|为)[^，,；;。！？\n]{0,6}(?:攻击|政策(?:行为|措施)?|配置错误|基础设施(?:故障)?)/,
  },
  {
    id: 'unsupported_cause',
    pattern:
      /(?:事件|中断|变化)[^，,；;。！？\n]{0,8}(?<!不是)(?<!并非)(?:由|被)[^，,；;。！？\n]{0,4}(?:攻击|政策(?:行为|措施)?|配置错误|基础设施(?:故障)?)[^，,；;。！？\n]{0,6}(?:触发|导致|造成|引发|引起)/,
  },
  {
    id: 'user_or_business_outage',
    pattern:
      /(?:用户|业务|服务)[^，,；;。！？\n]{0,12}(?:中断|断网|不可用|停摆|瘫痪|无法(?:正常)?(?:上网|使用))/,
  },
  {
    id: 'outage_affects_user_or_business',
    pattern:
      /(?:中断|断网|不可用|停摆|瘫痪)[^，,；;。！？\n]{0,8}(?:用户|业务|服务)/,
  },
  {
    id: 'unsupported_recovery',
    pattern:
      /(?:(?:已经|已|现已)[^，,；;。！？\n]{0,3})?(?:完全恢复|全面恢复|恢复正常|恢复至正常|恢复完毕)/,
  },
  {
    id: 'event_ended',
    pattern:
      /事件[^，,；;。！？\n]{0,6}(?:(?:已经|已|现已)[^，,；;。！？\n]{0,2})?(?:结束|终止)/,
  },
  {
    id: 'unsupported_user_impact',
    pattern: /用户[^，,；;。！？\n]{0,8}受到[^，,；;。！？\n]{0,8}影响/,
  },
  {
    id: 'unsupported_responsibility',
    pattern:
      /(?:运营商|ASN)[^，,；;。！？\n]{0,8}(?:应承担|负有)责任/,
  },
] as const

const explicitBoundaryNegationBefore =
  /(?:不能|不可|无法|不足以|不得|不应|尚不能|仍不能|未能|没有(?:足够|充分)?证据|不(?:代表|表示|意味着|等于|构成|证明)|并非|不是|是否|(?:尚|仍|并)?未|没有)[^，,；;。！？\n]{0,80}$/

const explicitBoundaryNegationAfter =
  /^[^，,；;。！？\n]{0,18}(?:不能|不可|无法|不足以|不得|不应)[^，,；;。！？\n]{0,12}(?:认定|确认|证明|判断|说明|推断|回答|理解为|解释为)/

function containsUnsupportedPositiveClaim(
  text: string,
  pattern: RegExp,
): boolean {
  for (const clause of text.split(
    /[。！？\n；;，,]|(?:但(?:是)?|然而|不过|却|因此|因而|所以|故而|从而|由此可见|这(?:表明|说明)|可见)/,
  )) {
    const flags = [...new Set(`${pattern.flags}g`)].join('')
    const matcher = new RegExp(pattern.source, flags)
    for (const match of clause.matchAll(matcher)) {
      const index = match.index ?? 0
      const before = clause.slice(0, index)
      const after = clause.slice(
        index + match[0].length,
        index + match[0].length + 40,
      )
      if (
        !explicitBoundaryNegationBefore.test(before) &&
        !explicitBoundaryNegationAfter.test(after)
      ) {
        return true
      }
    }
  }
  return false
}

function isObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function hasExactKeys(
  value: JsonObject,
  expectedKeys: readonly string[],
): boolean {
  const actualKeys = Object.keys(value).sort()
  const normalizedExpectedKeys = [...expectedKeys].sort()
  return (
    actualKeys.length === normalizedExpectedKeys.length &&
    actualKeys.every(
      (key, index) => key === normalizedExpectedKeys[index],
    )
  )
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isNonBlankString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function normalizedIdentityText(value: string): string {
  return value
    .normalize('NFKC')
    .toLowerCase()
    .replace(/\s+/gu, '')
}

function containsFrozenIdentity(text: string, identity: string): boolean {
  const normalizedIdentity = normalizedIdentityText(identity)
  return (
    normalizedIdentity.length > 0 &&
    normalizedIdentityText(text).includes(normalizedIdentity)
  )
}

function titleMatchesFrozenDisplayName(
  title: string,
  countryName: string,
  displayName: string,
): boolean {
  if (!containsFrozenIdentity(title, countryName)) return false
  const normalizedTitle = normalizedIdentityText(title)
  const normalizedCountry = normalizedIdentityText(countryName)
  const displayWithoutCountry = normalizedIdentityText(displayName)
    .replace(normalizedCountry, '')
  const requiredLatinTokens =
    displayWithoutCountry.match(/[a-z][a-z0-9]*/gu) ?? []
  const requiredHanCharacters = [
    ...new Set(
      [...displayWithoutCountry].filter((character) =>
        /\p{Script=Han}/u.test(character),
      ),
    ),
  ]
  return (
    requiredLatinTokens.every((token) =>
      normalizedTitle.includes(token),
    ) &&
    requiredHanCharacters.every((character) =>
      normalizedTitle.includes(character),
    )
  )
}

function isChineseNarrative(text: string): boolean {
  if (!/\p{Script=Han}/u.test(text)) return false
  const withoutApprovedTechnicalTerms = text.replace(
    /\b(?:Domeye|BGP|RRC\d+|Prefix|VP|origin|ASN|AS\d+|IPv4|IPv6|UPDATE|ANNOUNCE|WITHDRAW|IP|AI|API|URL|UTC|RIB|MRT|collector)\b/giu,
    '',
  )
  return !/[A-Za-z]{2,}/u.test(withoutApprovedTechnicalTerms)
}

function nonBlankStrings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(isNonBlankString)
}

function parseParagraph(value: unknown, location: string): EvidenceParagraph {
  if (
    !isObject(value) ||
    !hasExactKeys(value, ['text', 'evidenceRefs']) ||
    !isNonBlankString(value.text) ||
    !strings(value.evidenceRefs)
  ) {
    throw new Error(`${location} 不是有效的证据段落`)
  }
  return { text: value.text, evidenceRefs: value.evidenceRefs }
}

function parseHighlight(value: unknown, location: string): ReportHighlight {
  if (
    !isObject(value) ||
    !hasExactKeys(value, ['label', 'value', 'evidenceRefs']) ||
    !isNonBlankString(value.label) ||
    !isNonBlankString(value.value) ||
    !strings(value.evidenceRefs)
  ) {
    throw new Error(`${location} 不是有效的关键数字`)
  }
  return {
    label: value.label,
    value: value.value,
    evidenceRefs: value.evidenceRefs,
  }
}

function parseSection(value: unknown, location: string): ReportSection {
  if (
    !isObject(value) ||
    !hasExactKeys(value, ['id', 'title', 'paragraphs']) ||
    !sectionIds.includes(value.id as (typeof sectionIds)[number]) ||
    !isNonBlankString(value.title) ||
    !Array.isArray(value.paragraphs) ||
    value.paragraphs.length === 0
  ) {
    throw new Error(`${location} 不是有效的报告章节`)
  }
  return {
    id: value.id as ReportSection['id'],
    title: value.title,
    paragraphs: value.paragraphs.map((item, index) =>
      parseParagraph(item, `${location}.paragraphs[${index}]`),
    ),
  }
}

export function parseReportDraft(value: unknown): CountryOutageReportDraft {
  if (
    !isObject(value) ||
    !hasExactKeys(value, [
      'schemaVersion',
      'title',
      'subtitle',
      'summary',
      'highlights',
      'sections',
      'unknowns',
    ]) ||
    value.schemaVersion !== 'country_outage_report_draft_v1' ||
    !isNonBlankString(value.title) ||
    !isNonBlankString(value.subtitle) ||
    !Array.isArray(value.highlights) ||
    !Array.isArray(value.sections) ||
    !nonBlankStrings(value.unknowns)
  ) {
    throw new Error('模型输出不符合 country_outage_report_draft_v1')
  }
  return {
    schemaVersion: 'country_outage_report_draft_v1',
    title: value.title,
    subtitle: value.subtitle,
    summary: parseParagraph(value.summary, 'summary'),
    highlights: value.highlights.map((item, index) =>
      parseHighlight(item, `highlights[${index}]`),
    ),
    sections: value.sections.map((item, index) =>
      parseSection(item, `sections[${index}]`),
    ),
    unknowns: value.unknowns,
  }
}

export const COUNTRY_OUTAGE_REPORT_DRAFT_TEXT_DIAGNOSTICS = Object.freeze({
  json_object_missing: Object.freeze({
    code: 'json_object_missing',
    message: '报告响应中未找到 JSON 对象',
  } as const),
  json_syntax_invalid: Object.freeze({
    code: 'json_syntax_invalid',
    message: '报告响应中的 JSON 语法无效',
  } as const),
  draft_schema_invalid: Object.freeze({
    code: 'draft_schema_invalid',
    message: '报告 JSON 不符合草稿结构',
  } as const),
} as const)

export type ReportDraftTextDiagnosticCode =
  keyof typeof COUNTRY_OUTAGE_REPORT_DRAFT_TEXT_DIAGNOSTICS

export type ReportDraftTextDiagnostic =
  (typeof COUNTRY_OUTAGE_REPORT_DRAFT_TEXT_DIAGNOSTICS)[ReportDraftTextDiagnosticCode]

export class ReportDraftTextParseError extends Error {
  readonly code: ReportDraftTextDiagnosticCode
  readonly diagnostic: ReportDraftTextDiagnostic

  constructor(code: ReportDraftTextDiagnosticCode) {
    const diagnostic = COUNTRY_OUTAGE_REPORT_DRAFT_TEXT_DIAGNOSTICS[code]
    super(diagnostic.message)
    this.name = 'ReportDraftTextParseError'
    this.code = code
    this.diagnostic = diagnostic
  }
}

export function parseReportDraftText(text: string): CountryOutageReportDraft {
  const trimmed = text.trim()
  const withoutFence = trimmed
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/, '')
    .trim()
  const firstBrace = withoutFence.indexOf('{')
  const lastBrace = withoutFence.lastIndexOf('}')
  if (firstBrace < 0 || lastBrace <= firstBrace) {
    throw new ReportDraftTextParseError('json_object_missing')
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(
      withoutFence.slice(firstBrace, lastBrace + 1),
    ) as unknown
  } catch {
    throw new ReportDraftTextParseError('json_syntax_invalid')
  }
  try {
    return parseReportDraft(parsed)
  } catch {
    throw new ReportDraftTextParseError('draft_schema_invalid')
  }
}

function collectNumbers(value: unknown, output: Set<number>): void {
  if (typeof value === 'number' && Number.isFinite(value)) {
    output.add(value)
    return
  }
  if (Array.isArray(value)) {
    for (const item of value) collectNumbers(item, output)
    return
  }
  if (isObject(value)) {
    for (const item of Object.values(value)) collectNumbers(item, output)
  }
}

function collectTemporalTokens(value: unknown, output: Set<string>): void {
  if (typeof value === 'string') {
    if (
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value) ||
      /^\d{2}:\d{2}(?::\d{2})?$/.test(value)
    ) {
      for (const token of value.match(/\d+/g) ?? []) {
        output.add(token)
        output.add(String(Number(token)))
      }
    }
    return
  }
  if (Array.isArray(value)) {
    for (const item of value) collectTemporalTokens(item, output)
    return
  }
  if (isObject(value)) {
    for (const item of Object.values(value)) {
      collectTemporalTokens(item, output)
    }
  }
}

function addNumberVariants(output: Set<string>, value: number): void {
  output.add(String(value))
  if (Number.isInteger(value)) {
    output.add(new Intl.NumberFormat('zh-CN').format(value))
  }
  for (const digits of [1, 2, 3]) {
    output.add(value.toFixed(digits))
  }
  if (value >= 0 && value <= 1) {
    for (const digits of [0, 1, 2, 3]) {
      output.add(`${(value * 100).toFixed(digits)}%`)
    }
  }
}

function allowedNumericTokens(evidence: ReportEvidenceBundle): Set<string> {
  const numbers = new Set<number>()
  collectNumbers(evidence.facts, numbers)
  collectNumbers(evidence.asnPages, numbers)
  if (evidence.facts.scope.interval_seconds) {
    const intervalMinutes = evidence.facts.scope.interval_seconds / 60
    numbers.add(intervalMinutes)
    for (const item of evidence.asnPages.flatMap((page) => page.items)) {
      if (typeof item.asn === 'string' && /^\d+$/.test(item.asn)) {
        numbers.add(Number(item.asn))
      }
      for (const [key, value] of Object.entries(item)) {
        if (
          key.endsWith('_slots') &&
          typeof value === 'number' &&
          Number.isFinite(value)
        ) {
          const durationMinutes = value * intervalMinutes
          numbers.add(durationMinutes)
          numbers.add(Math.floor(durationMinutes / 60))
          numbers.add(durationMinutes % 60)
        }
      }
    }
  }
  for (const value of [4, 5, 6, 24, 48]) numbers.add(value)

  const output = new Set<string>()
  for (const value of numbers) {
    addNumberVariants(output, value)
    if (value < 0) addNumberVariants(output, Math.abs(value))
  }
  collectTemporalTokens(evidence.facts, output)
  collectTemporalTokens(evidence.asnPages, output)
  return output
}

function numericTokens(text: string): string[] {
  return (
    text
      .replace(/\bRRC25\b/g, 'RRC')
      .replace(/\bIPv[46]\b/gi, 'IPv')
      .replace(/\/(?:24|48)(?!\d)/g, '/prefix')
      .replace(/(^|[^\d])5\s*分钟(?!\d)/g, '$1分钟')
      .match(/\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|\d+(?:\.\d+)?%?/g) ?? []
  )
}

interface RequiredHighlightCoverage {
  label: string
  evidenceRefs: ReadonlySet<string>
  numericValues: readonly number[]
}

function requiredHighlightCoverage(
  evidence: ReportEvidenceBundle,
): RequiredHighlightCoverage[] {
  const pointCoverage = (
    kind: 'start' | 'lowest' | 'end',
    label: string,
  ): RequiredHighlightCoverage => {
    const point = evidence.facts.keyVisibilityPoints.find(
      (item) => item.kind === kind,
    )
    return {
      label,
      evidenceRefs: new Set(
        point
          ? [`${point.provenance.endpoint}:${point.provenance.pointer}`]
          : [],
      ),
      numericValues: point
        ? [point.visiblePrefixVpCount, point.visiblePrefixVpRatio]
        : [],
    }
  }

  return [
    {
      label: '固定 origin ASN 人口',
      evidenceRefs: new Set([
        'overview:/cohort',
        'overview:/cohort/origin_asn_count',
      ]),
      numericValues: [evidence.facts.cohort.origin_asn_count],
    },
    {
      label: '固定 Prefix×VP 人口',
      evidenceRefs: new Set([
        'overview:/cohort',
        'overview:/cohort/prefix_vp_count',
      ]),
      numericValues: [evidence.facts.cohort.prefix_vp_count],
    },
    pointCoverage('start', '窗口起点'),
    pointCoverage('lowest', '窗口最低点'),
    pointCoverage('end', '窗口结束点'),
  ]
}

function highlightCoversRequirement(
  highlight: ReportHighlight,
  requirement: RequiredHighlightCoverage,
): boolean {
  if (
    !highlight.evidenceRefs.some((reference) =>
      requirement.evidenceRefs.has(reference),
    )
  ) {
    return false
  }
  const expectedTokens = new Set<string>()
  for (const value of requirement.numericValues) {
    addNumberVariants(expectedTokens, value)
  }
  return numericTokens(`${highlight.label}\n${highlight.value}`).some(
    (token) => expectedTokens.has(token),
  )
}

const unresolvedEvidenceReference = Symbol('unresolvedEvidenceReference')

function valueAtOwnPath(
  value: unknown,
  segments: string[],
): unknown | typeof unresolvedEvidenceReference {
  let current: unknown = value
  for (const segment of segments) {
    if (!isObject(current) || !(segment in current)) {
      return unresolvedEvidenceReference
    }
    current = current[segment]
  }
  return current
}

function resolveEvidenceReference(
  reference: string,
  evidence: ReportEvidenceBundle,
): unknown | typeof unresolvedEvidenceReference {
  const derivedFact = evidence.facts.derivedFacts.find(
    (fact) => fact.factId === reference,
  )
  if (derivedFact) return derivedFact
  const overviewMatch = reference.match(
    /^overview:\/(observation_scope|cohort|capabilities|limitations)(?:\/([A-Za-z0-9_/-]+))?$/,
  )
  if (overviewMatch) {
    const root = overviewMatch[1]
    const tail = overviewMatch[2]?.split('/').filter(Boolean) ?? []
    const source =
      root === 'observation_scope'
        ? evidence.facts.scope
        : root === 'cohort'
          ? evidence.facts.cohort
          : root === 'capabilities'
          ? evidence.facts.capabilities
            : evidence.facts.quality.limitations
    return tail.length === 0 ? source : valueAtOwnPath(source, tail)
  }
  if (reference === 'audit:/evidence_level') {
    return evidence.facts.audit.evidenceLevel ||
      unresolvedEvidenceReference
  }
  const seriesMatch = reference.match(/^series:\/series\/(\d+)$/)
  if (seriesMatch) {
    const index = Number(seriesMatch[1])
    return Number.isSafeInteger(index) &&
      index < evidence.facts.series.length
      ? evidence.facts.series[index]!
      : unresolvedEvidenceReference
  }
  const extremaMatch = reference.match(
    /^series:\/(metric_extrema|resource_metric_extrema)\/([A-Za-z0-9_-]+)\/(min|max)$/,
  )
  if (extremaMatch) {
    const source =
      extremaMatch[1] === 'metric_extrema'
        ? evidence.facts.metricExtrema
        : evidence.facts.resourceMetricExtrema
    return valueAtOwnPath(source, [
      extremaMatch[2]!,
      extremaMatch[3]!,
    ])
  }
  const asnMatch = reference.match(/^asns:\/items\/(\d+)$/)
  if (asnMatch) {
    const index = Number(asnMatch[1])
    const items = evidence.asnPages.flatMap((page) => page.items)
    return Number.isSafeInteger(index) && index < items.length
      ? items[index]!
      : unresolvedEvidenceReference
  }
  return unresolvedEvidenceReference
}

function evidenceRefIsValid(
  reference: string,
  evidence: ReportEvidenceBundle,
): boolean {
  return (
    resolveEvidenceReference(reference, evidence) !==
    unresolvedEvidenceReference
  )
}

interface ReferencedNumericSupport {
  tokens: Set<string>
  temporalTokens: Set<string>
  metricsByToken: Map<string, Set<string>>
}

function emptyReferencedNumericSupport(): ReferencedNumericSupport {
  return {
    tokens: new Set<string>(),
    temporalTokens: new Set<string>(),
    metricsByToken: new Map<string, Set<string>>(),
  }
}

function addReferencedNumber(
  support: ReferencedNumericSupport,
  metric: string,
  value: number,
): void {
  if (!Number.isFinite(value)) return
  const variants = new Set<string>()
  addNumberVariants(variants, value)
  if (value < 0) addNumberVariants(variants, Math.abs(value))
  for (const token of variants) {
    support.tokens.add(token)
    const metrics = support.metricsByToken.get(token) ?? new Set<string>()
    metrics.add(metric)
    support.metricsByToken.set(token, metrics)
  }
}

function addReferencedTemporalTokens(
  support: ReferencedNumericSupport,
  value: unknown,
): void {
  const tokens = new Set<string>()
  collectTemporalTokens(value, tokens)
  for (const token of tokens) {
    support.tokens.add(token)
    support.temporalTokens.add(token)
  }
}

function collectReferencedObjectNumbers(
  support: ReferencedNumericSupport,
  value: unknown,
  path: string[] = [],
): void {
  if (typeof value === 'number') {
    addReferencedNumber(
      support,
      path.at(-1) ?? 'numeric_value',
      value,
    )
    return
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) =>
      collectReferencedObjectNumbers(support, item, [...path, String(index)]),
    )
    return
  }
  if (!isObject(value)) return
  for (const [key, item] of Object.entries(value)) {
    collectReferencedObjectNumbers(support, item, [...path, key])
  }
}

function referencedNumericSupportForText(
  references: string[],
  evidence: ReportEvidenceBundle,
): ReferencedNumericSupport {
  const support = emptyReferencedNumericSupport()
  for (const reference of references) {
    const value = resolveEvidenceReference(reference, evidence)
    if (value === unresolvedEvidenceReference) continue
    addReferencedTemporalTokens(support, value)

    const derivedFact = evidence.facts.derivedFacts.find(
      (fact) => fact.factId === reference,
    )
    if (derivedFact) {
      // 派生事实引用默认只授权发布最终 value。operands 只用于审计和解释公式，
      // 不能被模型换写成该派生指标的结果。
      addReferencedNumber(
        support,
        `${derivedFact.metric}|${derivedFact.unit}`,
        derivedFact.value,
      )
      continue
    }

    const extremaMatch = reference.match(
      /^series:\/(?:metric_extrema|resource_metric_extrema)\/([A-Za-z0-9_-]+)\/(?:min|max)$/,
    )
    if (extremaMatch && isObject(value)) {
      if (typeof value.value === 'number') {
        addReferencedNumber(support, extremaMatch[1]!, value.value)
      }
      continue
    }

    const seriesMatch = reference.match(/^series:\/series\/\d+$/)
    if (seriesMatch && isObject(value)) {
      for (const [metric, item] of Object.entries(value)) {
        if (typeof item === 'number') {
          addReferencedNumber(support, metric, item)
        }
      }
      continue
    }

    const asnMatch = reference.match(/^asns:\/items\/\d+$/)
    if (asnMatch && isObject(value)) {
      for (const [metric, item] of Object.entries(value)) {
        if (typeof item === 'number') {
          addReferencedNumber(support, metric, item)
        } else if (
          metric === 'asn' &&
          typeof item === 'string' &&
          /^\d+$/.test(item)
        ) {
          addReferencedNumber(support, metric, Number(item))
        }
      }
      const intervalSeconds = evidence.facts.scope.interval_seconds
      if (
        typeof intervalSeconds === 'number' &&
        Number.isFinite(intervalSeconds) &&
        intervalSeconds > 0
      ) {
        const intervalMinutes = intervalSeconds / 60
        for (const [metric, item] of Object.entries(value)) {
          if (
            metric.endsWith('_slots') &&
            typeof item === 'number' &&
            Number.isFinite(item)
          ) {
            const durationMinutes = item * intervalMinutes
            addReferencedNumber(
              support,
              `${metric}_duration`,
              durationMinutes,
            )
            addReferencedNumber(
              support,
              `${metric}_duration`,
              Math.floor(durationMinutes / 60),
            )
            addReferencedNumber(
              support,
              `${metric}_duration`,
              durationMinutes % 60,
            )
          }
        }
      }
      continue
    }

    collectReferencedObjectNumbers(
      support,
      value,
      reference.split('/').filter(Boolean),
    )
  }
  return support
}

type NumericMetricKind =
  | 'prefix_vp'
  | 'ratio'
  | 'asn'
  | 'update'
  | 'announce'
  | 'withdraw'
  | 'resource'
  | 'duration'
  | 'observation'

interface NumericMetricTags {
  kinds: Set<NumericMetricKind>
  addressFamilies: Set<'ipv4' | 'ipv6'>
}

function metricTags(metric: string): NumericMetricTags {
  const normalized = metric.toLowerCase()
  const kinds = new Set<NumericMetricKind>()
  const addressFamilies = new Set<'ipv4' | 'ipv6'>()
  if (normalized.includes('ipv4') || normalized.includes('/24')) {
    addressFamilies.add('ipv4')
  }
  if (normalized.includes('ipv6') || normalized.includes('/48')) {
    addressFamilies.add('ipv6')
  }
  if (
    normalized === 'ratio' ||
    normalized.includes('_ratio') ||
    normalized.includes('ratio_') ||
    normalized.endsWith('|ratio') ||
    normalized.includes('share') ||
    normalized.includes('percentage') ||
    normalized.includes('_pp')
  ) {
    kinds.add('ratio')
  } else if (normalized.includes('announce')) {
    kinds.add('announce')
  } else if (normalized.includes('withdraw')) {
    kinds.add('withdraw')
  } else if (
    normalized.includes('update_total') ||
    normalized === 'update'
  ) {
    kinds.add('update')
  } else if (
    normalized === 'asn' ||
    normalized.includes('_asn_') ||
    normalized.endsWith('_asn_count') ||
    normalized.includes('origin_asn')
  ) {
    kinds.add('asn')
  } else if (
    normalized.includes('equivalent') ||
    normalized.includes('address_count')
  ) {
    kinds.add('resource')
  } else if (
    normalized.includes('duration') ||
    normalized.endsWith('_slots') ||
    normalized.includes('interval_seconds')
  ) {
    kinds.add('duration')
  } else if (normalized.includes('observation_count')) {
    kinds.add('observation')
  } else if (
    normalized.includes('prefix_vp') ||
    normalized.includes('prefix×vp')
  ) {
    kinds.add('prefix_vp')
  }
  return { kinds, addressFamilies }
}

function tokenHasDurationUnit(text: string, token: string): boolean {
  const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(
    `(?<![\\d,.])${escaped}(?![\\d,.])\\s*(?:小时|分钟|秒)`,
  ).test(text)
}

function textMetricTags(
  text: string,
  token: string,
): NumericMetricTags {
  const kinds = new Set<NumericMetricKind>()
  const addressFamilies = new Set<'ipv4' | 'ipv6'>()
  if (/IPv4/i.test(text)) addressFamilies.add('ipv4')
  if (/IPv6/i.test(text)) addressFamilies.add('ipv6')
  if (/(?:覆盖率|比例|占比|百分点|%)/.test(text)) kinds.add('ratio')
  if (/ANNOUNCE/i.test(text)) kinds.add('announce')
  if (/WITHDRAW/i.test(text)) kinds.add('withdraw')
  if (/UPDATE/i.test(text)) kinds.add('update')
  if (/(?:origin\s+ASN|ASN|AS\d+)/i.test(text)) kinds.add('asn')
  if (
    /(?:Prefix\s*[×x*]\s*VP|路由观测关系|可见关系|不可见关系)/i.test(
      text,
    )
  ) {
    kinds.add('prefix_vp')
  }
  if (/(?:等价资源|等价块|\/24|\/48|地址等价)/.test(text)) {
    kinds.add('resource')
  }
  if (tokenHasDurationUnit(text, token)) kinds.add('duration')
  if (/(?:观测槽|观测次数|观测点数)/.test(text)) {
    kinds.add('observation')
  }
  return { kinds, addressFamilies }
}

function tokenContexts(text: string, token: string): string[] {
  const contexts: string[] = []
  const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const matcher = new RegExp(
    `(?<![\\d,.])${escaped}(?![\\d,.])`,
    'g',
  )
  for (const match of text.matchAll(matcher)) {
    const index = match.index ?? 0
    const before = text.slice(0, index)
    const after = text.slice(index + token.length)
    const leftBoundary = Math.max(
      before.lastIndexOf('，'),
      before.lastIndexOf(','),
      before.lastIndexOf('；'),
      before.lastIndexOf(';'),
      before.lastIndexOf('。'),
      before.lastIndexOf('！'),
      before.lastIndexOf('？'),
      before.lastIndexOf('\n'),
    )
    const rightOffsets = ['，', ',', '；', ';', '。', '！', '？', '\n']
      .map((separator) => after.indexOf(separator))
      .filter((offset) => offset >= 0)
    const rightBoundary =
      rightOffsets.length > 0
        ? index + token.length + Math.min(...rightOffsets)
        : text.length
    contexts.push(text.slice(leftBoundary + 1, rightBoundary))
  }
  return contexts.length > 0 ? contexts : [text]
}

function numericMetricContextIsCompatible(
  text: string,
  token: string,
  metrics: ReadonlySet<string>,
): boolean {
  const claims = [...metrics].map(metricTags)
  if (claims.length === 0) return false
  if (
    tokenHasDurationUnit(text, token) &&
    claims.some((claim) => claim.kinds.has('duration'))
  ) {
    return true
  }
  return tokenContexts(text, token).every((context) => {
    const stated = textMetricTags(context, token)
    if (
      stated.addressFamilies.size > 0 &&
      !claims.some((claim) =>
        [...stated.addressFamilies].some((family) =>
          claim.addressFamilies.has(family),
        ),
      )
    ) {
      return false
    }
    if (
      stated.kinds.size > 0 &&
      !claims.some((claim) =>
        [...stated.kinds].some((kind) => claim.kinds.has(kind)),
      )
    ) {
      return false
    }
    return true
  })
}

function expectedSections(evidence: ReportEvidenceBundle): ReportSection['id'][] {
  const capabilities = evidence.facts.capabilities
  return [
    'scope',
    'key_numbers',
    'visibility',
    ...(capabilities.asn_matrix?.state === 'available'
      ? (['asn_scope'] as const)
      : []),
    ...(capabilities.address_families?.state === 'available'
      ? (['address_families'] as const)
      : []),
    ...(capabilities.update_activity?.state === 'available'
      ? (['updates'] as const)
      : []),
    'end_state',
    ...(capabilities.country_resources?.state === 'available'
      ? (['resources'] as const)
      : []),
    'assessment',
  ]
}

interface PublishableTextEntry {
  location: string
  text: string
}

function publishableTextEntries(
  draft: CountryOutageReportDraft,
): PublishableTextEntry[] {
  return [
    { location: 'title', text: draft.title },
    { location: 'subtitle', text: draft.subtitle },
    { location: 'summary', text: draft.summary.text },
    ...draft.highlights.flatMap((highlight, index) => [
      {
        location: `highlights[${index}].label`,
        text: highlight.label,
      },
      {
        location: `highlights[${index}].value`,
        text: highlight.value,
      },
    ]),
    ...draft.sections.flatMap((section) => [
      { location: `${section.id}.title`, text: section.title },
      ...section.paragraphs.map((item, index) => ({
        location: `${section.id}[${index}]`,
        text: item.text,
      })),
    ]),
    ...draft.unknowns.map((unknown, index) => ({
      location: `unknowns[${index}]`,
      text: unknown,
    })),
  ]
}

interface DirectionRule {
  pattern: RegExp
  locations?: RegExp
  metric: string
  expectedUnit: string
  supports(value: number): boolean
  description: string
}

const directionRules: readonly DirectionRule[] = [
  {
    pattern:
      /(?:(?:从|由)?起点[^，,。！？；\n]{0,48}(?:降至|下降到|下滑至|减少到)[^，,。！？；\n]{0,32}(?:最低(?:点|覆盖率)?|低点)|最低覆盖率[^，,。！？；\n]{0,24}(?:从|由)起点[^，,。！？；\n]{0,32}(?:降至|下降到)|(?:最低点|低点)[^，,。！？；\n]{0,32}(?:相对|比)[^，,。！？；\n]{0,12}起点[^，,。！？；\n]{0,24}(?:下降|降低|减少|低于)|起点(?:至|到)[^，,。！？；\n]{0,16}(?:最低点|低点)[^，,。！？；\n]{0,24}(?:下降|降低|减少))/u,
    metric: 'start_to_lowest_visible_prefix_vp_change',
    expectedUnit: 'Prefix×VP',
    supports: (value) => value > 0,
    description: '起点至最低点下降',
  },
  {
    pattern:
      /(?:(?:从|由)?起点[^，,。！？；\n]{0,48}(?:升至|上升到|增加到)[^，,。！？；\n]{0,32}(?:最低(?:点|覆盖率)?|低点)|最低覆盖率[^，,。！？；\n]{0,24}(?:从|由)起点[^，,。！？；\n]{0,32}(?:升至|上升到)|(?:最低点|低点)[^，,。！？；\n]{0,32}(?:相对|比)[^，,。！？；\n]{0,12}起点[^，,。！？；\n]{0,24}(?:上升|增加|高于)|起点(?:至|到)[^，,。！？；\n]{0,16}(?:最低点|低点)[^，,。！？；\n]{0,24}(?:上升|增加)|(?:最低点|低点)[^，,。！？；\n]{0,32}(?:高于|超过)起点)/u,
    metric: 'start_to_lowest_visible_prefix_vp_change',
    expectedUnit: 'Prefix×VP',
    supports: (value) => value < 0,
    description: '起点至最低点上升',
  },
  {
    pattern:
      /(?:(?:起点(?:至|到|与)[^，,。！？；\n]{0,20}(?:最低点|低点)|(?:最低点|低点)[^，,。！？；\n]{0,16}(?:相对|与)[^，,。！？；\n]{0,12}起点)[^，,。！？；\n]{0,24}(?:持平|相同|没有变化|无变化|差值为零)|起点与最低点[^，,。！？；\n]{0,20}(?:均为|相同))/u,
    metric: 'start_to_lowest_visible_prefix_vp_change',
    expectedUnit: 'Prefix×VP',
    supports: (value) => value === 0,
    description: '起点至最低点持平',
  },
  {
    pattern: /(?:可见性|路由传播覆盖)[^，,。！？；\n]{0,16}(?:下降|下滑)/u,
    locations: /^(?:subtitle|summary|visibility(?:\.title|\[\d+\]))$/,
    metric: 'start_to_lowest_visible_prefix_vp_change',
    expectedUnit: 'Prefix×VP',
    supports: (value) => value > 0,
    description: '起点至最低点下降',
  },
  {
    pattern: /(?:可见性|路由传播覆盖)[^，,。！？；\n]{0,16}(?:上升|增加)/u,
    locations: /^(?:subtitle|summary|visibility(?:\.title|\[\d+\]))$/,
    metric: 'start_to_lowest_visible_prefix_vp_change',
    expectedUnit: 'Prefix×VP',
    supports: (value) => value < 0,
    description: '起点至最低点上升',
  },
  {
    pattern: /(?:可见性|路由传播覆盖)[^，,。！？；\n]{0,16}(?:持平|没有变化|无变化)/u,
    locations: /^(?:subtitle|summary|visibility(?:\.title|\[\d+\]))$/,
    metric: 'start_to_lowest_visible_prefix_vp_change',
    expectedUnit: 'Prefix×VP',
    supports: (value) => value === 0,
    description: '起点至最低点持平',
  },
  {
    pattern:
      /(?:(?:最低点|低点)(?:之后)?[，,]?[^，,。！？；\n]{0,40}(?:到|至)[^，,。！？；\n]{0,16}(?:窗口结束|结束)[^，,。！？；\n]{0,24}(?:回升|反弹|上升|增加)|(?:窗口后段|最低点之后)[^，,。！？；\n]{0,32}(?:回升|反弹|上升|增加))/u,
    metric: 'recovered_from_lowest',
    expectedUnit: 'Prefix×VP',
    supports: (value) => value > 0,
    description: '最低点至窗口结束回升',
  },
  {
    pattern:
      /(?:(?:窗口后段|最低点之后)[，,]?[^，,。！？；\n]{0,32}(?:下降|回落|减少)|(?:最低点|低点)[，,]?[^，,。！？；\n]{0,24}(?:到|至)[^，,。！？；\n]{0,16}(?:结束|窗口结束)[^，,。！？；\n]{0,24}(?:下降|回落|减少))/u,
    metric: 'recovered_from_lowest',
    expectedUnit: 'Prefix×VP',
    supports: (value) => value < 0,
    description: '最低点至窗口结束下降',
  },
  {
    pattern:
      /(?:(?:最低点|低点)(?:之后)?[，,]?[^，,。！？；\n]{0,40}(?:到|至)[^，,。！？；\n]{0,16}(?:窗口结束|结束)[^，,。！？；\n]{0,24}(?:持平|保持不变|没有变化|无变化)|(?:窗口后段|最低点之后)[^，,。！？；\n]{0,32}(?:持平|保持不变|没有变化|无变化))/u,
    metric: 'recovered_from_lowest',
    expectedUnit: 'Prefix×VP',
    supports: (value) => value === 0,
    description: '最低点至窗口结束持平',
  },
  {
    pattern:
      /(?:结束(?:时)?|窗口结束)[^，,。！？；\n]{0,32}(?:低于起点|未(?:回到|恢复到)起点)/u,
    metric: 'end_gap_from_start',
    expectedUnit: 'Prefix×VP',
    supports: (value) => value > 0,
    description: '窗口结束低于起点',
  },
  {
    pattern:
      /(?:结束(?:时)?|窗口结束)[^，,。！？；\n]{0,32}(?:高于|超过)起点/u,
    metric: 'end_gap_from_start',
    expectedUnit: 'Prefix×VP',
    supports: (value) => value < 0,
    description: '窗口结束高于起点',
  },
  {
    pattern:
      /(?:结束(?:时)?|窗口结束)[^，,。！？；\n]{0,32}(?:(?<!未)回到起点|与起点持平|等于起点)/u,
    metric: 'end_gap_from_start',
    expectedUnit: 'Prefix×VP',
    supports: (value) => value === 0,
    description: '窗口结束回到起点',
  },
] as const

function validateDirectionClaims(
  draft: CountryOutageReportDraft,
  evidence: ReportEvidenceBundle,
  errors: string[],
): void {
  for (const entry of publishableTextEntries(draft)) {
    for (const rule of directionRules) {
      if (
        rule.locations &&
        !rule.locations.test(entry.location)
      ) {
        continue
      }
      if (!rule.pattern.test(entry.text)) continue
      const fact = evidence.facts.derivedFacts.find(
        (item) => item.metric === rule.metric,
      )
      if (
        !fact ||
        fact.unit !== rule.expectedUnit ||
        !Number.isFinite(fact.value)
      ) {
        errors.push(
          `${entry.location} 结论“${rule.description}”缺少对应的确定性派生事实：${rule.metric}`,
        )
        continue
      }
      if (!rule.supports(fact.value)) {
        errors.push(
          `${entry.location} 结论“${rule.description}”与对应派生事实方向不一致：${rule.metric}`,
        )
      }
    }
  }
}

export function validateReportDraft(
  draft: CountryOutageReportDraft,
  evidence: ReportEvidenceBundle,
): ReportValidationResult {
  const errors: string[] = []
  const warnings: string[] = []
  const checkedEvidenceRefs: string[] = []
  const expected = expectedSections(evidence)
  const actual = draft.sections.map((section) => section.id)
  if (!isNonBlankString(draft.title)) {
    errors.push('报告标题不能为空')
  }
  if (!isNonBlankString(draft.subtitle)) {
    errors.push('报告副标题不能为空')
  }
  if (!isNonBlankString(draft.summary.text)) {
    errors.push('报告摘要不能为空')
  }
  if (
    !isNonBlankString(evidence.facts.event.country_name) ||
    !isNonBlankString(evidence.facts.event.display_name)
  ) {
    errors.push('冻结事件缺少 country_name 或 display_name')
  } else {
    if (
      !titleMatchesFrozenDisplayName(
        draft.title,
        evidence.facts.event.country_name,
        evidence.facts.event.display_name,
      )
    ) {
      errors.push(
        '报告标题没有绑定冻结事件的 country_name/display_name',
      )
    }
    if (
      !containsFrozenIdentity(
        draft.summary.text,
        evidence.facts.event.country_name,
      )
    ) {
      errors.push('报告摘要没有绑定冻结事件的 country_name')
    }
  }
  const chineseNarratives: Array<[string, string]> = [
    ['title', draft.title],
    ['subtitle', draft.subtitle],
    ['summary', draft.summary.text],
    ...draft.highlights.map(
      (highlight, index) =>
        [`highlights[${index}].label`, highlight.label] as [string, string],
    ),
    ...draft.sections.flatMap((section) => [
      [`${section.id}.title`, section.title] as [string, string],
      ...section.paragraphs.map(
        (paragraph, index) =>
          [`${section.id}[${index}]`, paragraph.text] as [string, string],
      ),
    ]),
    ...draft.unknowns.map(
      (unknown, index) =>
        [`unknowns[${index}]`, unknown] as [string, string],
    ),
  ]
  for (const [location, text] of chineseNarratives) {
    if (!isChineseNarrative(text)) {
      errors.push(`${location} 必须使用中文叙事`)
    }
  }
  validateDirectionClaims(draft, evidence, errors)
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    errors.push(
      `章节顺序或能力降级不正确：期望 ${expected.join(', ')}，实际 ${actual.join(', ')}`,
    )
  }
  if (draft.highlights.length < 5) {
    errors.push('关键数字少于 5 项')
  }
  for (const requirement of requiredHighlightCoverage(evidence)) {
    if (
      !draft.highlights.some((highlight) =>
        highlightCoversRequirement(highlight, requirement),
      )
    ) {
      errors.push(
        `关键数字缺少结构化覆盖：${requirement.label}`,
      )
    }
  }
  if (draft.unknowns.length < 4) {
    errors.push('不能回答的问题少于 4 项')
  }
  for (const [index, unknown] of draft.unknowns.entries()) {
    if (!isNonBlankString(unknown)) {
      errors.push(`不能回答的问题第 ${index + 1} 项不能为空`)
    }
  }
  for (const pattern of requiredUnknownPatterns) {
    if (!draft.unknowns.some((item) => pattern.test(item))) {
      errors.push(`不能回答的问题缺少边界：${pattern}`)
    }
  }

  const paragraphs: Array<{
    location: string
    paragraph: EvidenceParagraph
  }> = [
    { location: 'summary', paragraph: draft.summary },
    ...draft.sections.flatMap((section) =>
      section.paragraphs.map((item, index) => ({
        location: `${section.id}[${index}]`,
        paragraph: item,
      })),
    ),
  ]
  const numericAllowed = allowedNumericTokens(evidence)
  const validateNumericText = (
    location: string,
    text: string,
    references: string[],
  ): void => {
    const tokens = numericTokens(text)
    if (tokens.length > 0 && references.length === 0) {
      errors.push(`${location} 含数字但没有证据引用`)
      return
    }
    const referencedNumericSupport =
      referencedNumericSupportForText(references, evidence)
    for (const token of tokens) {
      if (!numericAllowed.has(token)) {
        errors.push(`${location} 出现事实集合中不存在的数字：${token}`)
      } else if (!referencedNumericSupport.tokens.has(token)) {
        errors.push(
          `${location} 出现当前证据引用不支持的数字：${token}`,
        )
      } else if (
        !referencedNumericSupport.temporalTokens.has(token) &&
        !numericMetricContextIsCompatible(
          text,
          token,
          referencedNumericSupport.metricsByToken.get(token) ??
            new Set<string>(),
        )
      ) {
        errors.push(
          `${location} 的数字 ${token} 与当前证据引用的指标或单位不一致`,
        )
      }
    }
  }
  validateNumericText('title', draft.title, [])
  validateNumericText('subtitle', draft.subtitle, [])
  draft.unknowns.forEach((unknown, index) =>
    validateNumericText(`unknowns[${index}]`, unknown, []),
  )
  for (const { location, paragraph } of paragraphs) {
    if (!isNonBlankString(paragraph.text)) {
      errors.push(`${location} 段落正文不能为空`)
    }
    validateNumericText(location, paragraph.text, paragraph.evidenceRefs)
    for (const reference of paragraph.evidenceRefs) {
      if (!evidenceRefIsValid(reference, evidence)) {
        errors.push(`${location} 使用无效证据引用：${reference}`)
      } else {
        checkedEvidenceRefs.push(reference)
      }
    }
  }
  for (const [index, highlight] of draft.highlights.entries()) {
    if (!isNonBlankString(highlight.label)) {
      errors.push(`highlights[${index}] 标题不能为空`)
    }
    if (!isNonBlankString(highlight.value)) {
      errors.push(`highlights[${index}] 数值不能为空`)
    }
    validateNumericText(
      `highlights[${index}].label`,
      highlight.label,
      highlight.evidenceRefs,
    )
    validateNumericText(
      `highlights[${index}]`,
      `${highlight.label}：${highlight.value}`,
      highlight.evidenceRefs,
    )
    if (highlight.evidenceRefs.length === 0) {
      errors.push(`highlights[${index}] 没有证据引用`)
    }
    for (const reference of highlight.evidenceRefs) {
      if (!evidenceRefIsValid(reference, evidence)) {
        errors.push(`highlights[${index}] 使用无效证据引用：${reference}`)
      } else {
        checkedEvidenceRefs.push(reference)
      }
    }
  }
  for (const section of draft.sections) {
    if (!isNonBlankString(section.title)) {
      errors.push(`${section.id} 章节标题不能为空`)
    }
    validateNumericText(
      `${section.id}.title`,
      section.title,
      [],
    )
    if (section.paragraphs.length === 0) {
      errors.push(`${section.id} 章节必须至少包含一个段落`)
    }
  }

  const allText = publishableTextEntries(draft)
    .map((entry) => entry.text)
    .join('\n')
  for (const claim of forbiddenPositiveClaims) {
    if (containsUnsupportedPositiveClaim(allText, claim.pattern)) {
      errors.push(`报告出现越界肯定结论：${claim.id}`)
    }
  }
  if (!allText.includes('RRC25')) {
    errors.push('报告没有明确 RRC25 观测范围')
  }
  if (!allText.includes('控制面')) {
    warnings.push('报告未在正文中重复解释控制面边界')
  }

  return {
    passed: errors.length === 0,
    errors,
    warnings,
    checkedEvidenceRefs: [...new Set(checkedEvidenceRefs)].sort(),
  }
}
