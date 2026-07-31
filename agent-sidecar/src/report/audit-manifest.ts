import { createHash } from 'node:crypto'

import type {
  CapabilityState,
  DerivedNumericFact,
  JsonObject,
  KeyVisibilityPoint,
} from '../domain/contracts.js'
import { countCountryOutageEvidenceRecords } from '../formal-runtime-limits.js'
import type { COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION } from '../pi/country-outage-skill-bundle.js'
import {
  canonicalJsonStringify,
  compareUnicodeCodePoints,
} from '../shared/deterministic-json.js'
import type { CompiledCountryOutageReport } from './report-compiler.js'

export const COUNTRY_OUTAGE_REPORT_AUDIT_MANIFEST_SCHEMA_VERSION =
  'country_outage_report_audit_manifest_v1' as const
export const COUNTRY_OUTAGE_REPORT_AUDIT_MANIFEST_FILENAME =
  'audit-manifest.json' as const

export interface CountryOutageAuditManifestArtifact {
  filename: typeof COUNTRY_OUTAGE_REPORT_AUDIT_MANIFEST_FILENAME
  mediaType: 'application/json; charset=utf-8'
  byteLength: number
  sha256: string
  content: Buffer
}

export interface CountryOutageAuditManifestArtifactDescriptor {
  schemaVersion: typeof COUNTRY_OUTAGE_REPORT_AUDIT_MANIFEST_SCHEMA_VERSION
  filename: typeof COUNTRY_OUTAGE_REPORT_AUDIT_MANIFEST_FILENAME
  byteLength: number
  sha256: string
}

export interface CountryOutageReportAuditManifest {
  schemaVersion: typeof COUNTRY_OUTAGE_REPORT_AUDIT_MANIFEST_SCHEMA_VERSION
  reportIdentity: {
    schemaVersion: 'country_outage_report_document_v1'
    artifactId: string
    reportContentSha256: string
    generatedAt: string
    aiGenerated: true
    humanReviewed: false
  }
  eventIdentity: {
    incidentId: string
    eventReference: string
    eventType: 'country_outage'
    countryCode: string
    countryName: string
  }
  snapshotIdentity: CompiledCountryOutageReport['document']['snapshot']
  factSetIdentity: {
    schemaVersion: 'country_outage_report_facts_v1'
    factSetId: string
  }
  modelIdentity: CompiledCountryOutageReport['document']['model']
  contractIdentity: {
    reportSpecificationVersion: 'country_outage_report_spec_v1'
    projectKnowledgeVersion:
      typeof COUNTRY_OUTAGE_PROJECT_KNOWLEDGE_VERSION
    validatorRulesVersion: string
    skillBundleSha256: string
  }
  evidenceTrace: {
    usedEvidenceRefs: string[]
    overviewAndBoundaryRefs: string[]
    keyObservationSlots: Array<{
      evidenceRef: string
      slotIndex: number
      keyKinds: KeyVisibilityPoint['kind'][]
      slot: JsonObject
    }>
    extremaPoints: Array<{
      evidenceRef: string
      group: 'metric_extrema' | 'resource_metric_extrema'
      metric: string
      side: 'min' | 'max'
      point: JsonObject
    }>
    derivedFacts: DerivedNumericFact[]
    asnItems: Array<{
      evidenceRef: string
      globalIndex: number
      page: number
      pageSize: number
      item: JsonObject
    }>
    evidenceRecordCount: ReturnType<
      typeof countCountryOutageEvidenceRecords
    >
  }
  capabilityBoundary: {
    unavailableOrDegraded: Array<{
      capability: string
      state: CapabilityState['state']
      reason?: string
    }>
    reportEligible: boolean
    eligibilityReasons: string[]
    missingRequiredFields: string[]
    qualityStatus: string
    missingSlotCount: number
    limitations: string[]
  }
  cannotAnswer: string[]
  validation: CompiledCountryOutageReport['document']['validation']
}

export class CountryOutageAuditManifestInputError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'CountryOutageAuditManifestInputError'
  }
}

const VISIBILITY_SLOT_FIELDS = [
  'observed_at_utc',
  'observed_at_local',
  'slot_state',
  'missing_reason',
  'visible_prefix_vp_count',
  'visible_prefix_vp_ratio',
  'visible_prefix_vp_delta',
  'visible_prefix_vp_ratio_delta_pp',
  'visible_origin_asn_count',
  'fully_visible_asn_count',
  'partially_visible_asn_count',
  'fully_invisible_asn_count',
  'ipv4_visible_prefix_vp_count',
  'ipv4_visible_prefix_vp_ratio',
  'ipv6_visible_prefix_vp_count',
  'ipv6_visible_prefix_vp_ratio',
  'announce_count',
  'withdraw_count',
  'update_total',
  'withdraw_ratio',
] as const

const EXTREMA_POINT_FIELDS = [
  'metric',
  'value',
  'unit',
  'observed_at_utc',
  'observed_at_local',
  'slot_index',
] as const

const ASN_ITEM_FIELDS = [
  'asn',
  'origin_asn',
  'address_family',
  'state',
  'visibility_state',
  'baseline_prefix_vp_count',
  'visible_prefix_vp_count',
  'invisible_prefix_vp_count',
  'fully_visible_slots',
  'partially_visible_slots',
  'fully_invisible_slots',
  'longest_fully_invisible_slots',
  'longest_fully_invisible_start_utc',
  'longest_fully_invisible_end_utc',
  'longest_fully_invisible_start_local',
  'longest_fully_invisible_end_local',
] as const

function isObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function projectSafeScalars(
  source: JsonObject,
  fields: readonly string[],
): JsonObject {
  const output: JsonObject = {}
  for (const field of fields) {
    const value = source[field]
    if (
      value === null ||
      typeof value === 'string' ||
      typeof value === 'boolean' ||
      (typeof value === 'number' && Number.isFinite(value))
    ) {
      output[field] = value
    }
  }
  return output
}

function collectDraftEvidenceRefs(
  compiled: CompiledCountryOutageReport,
): string[] {
  const { draft } = compiled.document
  return [
    ...draft.summary.evidenceRefs,
    ...draft.highlights.flatMap((item) => item.evidenceRefs),
    ...draft.sections.flatMap((section) =>
      section.paragraphs.flatMap((paragraph) => paragraph.evidenceRefs),
    ),
  ]
    .filter((reference) => reference.trim().length > 0)
    .filter((reference, index, values) => values.indexOf(reference) === index)
    .sort(compareUnicodeCodePoints)
}

function assertValidatedEvidenceRefs(
  compiled: CompiledCountryOutageReport,
  usedEvidenceRefs: string[],
): void {
  if (!compiled.document.validation.passed) {
    throw new CountryOutageAuditManifestInputError(
      '未通过机器校验的报告不能生成正式审计清单',
    )
  }
  const checked = [
    ...new Set(compiled.document.validation.checkedEvidenceRefs),
  ].sort(compareUnicodeCodePoints)
  if (JSON.stringify(checked) !== JSON.stringify(usedEvidenceRefs)) {
    throw new CountryOutageAuditManifestInputError(
      '报告正文实际证据引用与机器校验结果不一致',
    )
  }
}

function keyObservationSlots(
  compiled: CompiledCountryOutageReport,
  usedEvidenceRefs: string[],
): CountryOutageReportAuditManifest['evidenceTrace']['keyObservationSlots'] {
  const { facts } = compiled.evidence
  const references = usedEvidenceRefs.flatMap((reference) => {
    const match = reference.match(/^series:\/series\/(\d+)$/)
    return match ? [{ reference, slotIndex: Number(match[1]) }] : []
  })
  return references
    .sort((left, right) => left.slotIndex - right.slotIndex)
    .map(({ reference, slotIndex }) => {
      const slot = facts.series[slotIndex]
      if (!slot) {
        throw new CountryOutageAuditManifestInputError(
          `证据引用对应的观测槽不存在：${reference}`,
        )
      }
      const keyKinds = facts.keyVisibilityPoints
        .filter((point) => point.slotIndex === slotIndex)
        .map((point) => point.kind)
        .sort(compareUnicodeCodePoints)
      return {
        evidenceRef: reference,
        slotIndex,
        keyKinds,
        slot: projectSafeScalars(slot, VISIBILITY_SLOT_FIELDS),
      }
    })
}

function extremaPoints(
  compiled: CompiledCountryOutageReport,
  usedEvidenceRefs: string[],
): CountryOutageReportAuditManifest['evidenceTrace']['extremaPoints'] {
  return usedEvidenceRefs
    .flatMap((reference) => {
      const match = reference.match(
        /^series:\/(metric_extrema|resource_metric_extrema)\/([A-Za-z0-9_-]+)\/(min|max)$/,
      )
      if (!match) return []
      const group = match[1] as
        | 'metric_extrema'
        | 'resource_metric_extrema'
      const metric = match[2]!
      const side = match[3] as 'min' | 'max'
      const source =
        group === 'metric_extrema'
          ? compiled.evidence.facts.metricExtrema
          : compiled.evidence.facts.resourceMetricExtrema
      const metricValue = source[metric]
      const point = isObject(metricValue) ? metricValue[side] : undefined
      if (!isObject(point)) {
        throw new CountryOutageAuditManifestInputError(
          `证据引用对应的极值点不存在：${reference}`,
        )
      }
      return [
        {
          evidenceRef: reference,
          group,
          metric,
          side,
          point: projectSafeScalars(point, EXTREMA_POINT_FIELDS),
        },
      ]
    })
    .sort((left, right) =>
      compareUnicodeCodePoints(left.evidenceRef, right.evidenceRef),
    )
}

function derivedFacts(
  compiled: CompiledCountryOutageReport,
  usedEvidenceRefs: string[],
): DerivedNumericFact[] {
  const used = new Set(usedEvidenceRefs)
  return compiled.evidence.facts.derivedFacts
    .filter((fact) => used.has(fact.factId))
    .sort((left, right) =>
      compareUnicodeCodePoints(left.factId, right.factId),
    )
    .map((fact) => ({
      factId: fact.factId,
      metric: fact.metric,
      label: fact.label,
      value: fact.value,
      unit: fact.unit,
      formula: fact.formula,
      operands: Object.fromEntries(
        Object.entries(fact.operands).sort(([left], [right]) =>
          compareUnicodeCodePoints(left, right),
        ),
      ),
      ...(fact.observedAtUtc
        ? { observedAtUtc: fact.observedAtUtc }
        : {}),
      ...(fact.observedAtLocal
        ? { observedAtLocal: fact.observedAtLocal }
        : {}),
      provenance: {
        endpoint: fact.provenance.endpoint,
        schemaVersion: fact.provenance.schemaVersion,
        pointer: fact.provenance.pointer,
        publicationId: fact.provenance.publicationId,
      },
    }))
}

function asnItems(
  compiled: CompiledCountryOutageReport,
  usedEvidenceRefs: string[],
): CountryOutageReportAuditManifest['evidenceTrace']['asnItems'] {
  const flattened = compiled.evidence.asnPages.flatMap((page) =>
    page.items.map((item) => ({
      page: page.page,
      pageSize: page.page_size,
      item,
    })),
  )
  return usedEvidenceRefs
    .flatMap((reference) => {
      const match = reference.match(/^asns:\/items\/(\d+)$/)
      if (!match) return []
      const globalIndex = Number(match[1])
      const located = flattened[globalIndex]
      if (!located) {
        throw new CountryOutageAuditManifestInputError(
          `证据引用对应的 ASN 项不存在：${reference}`,
        )
      }
      return [
        {
          evidenceRef: reference,
          globalIndex,
          page: located.page,
          pageSize: located.pageSize,
          item: projectSafeScalars(located.item, ASN_ITEM_FIELDS),
        },
      ]
    })
    .sort((left, right) => left.globalIndex - right.globalIndex)
}

function unavailableOrDegraded(
  compiled: CompiledCountryOutageReport,
): CountryOutageReportAuditManifest['capabilityBoundary']['unavailableOrDegraded'] {
  return Object.entries(compiled.evidence.facts.capabilities)
    .filter(([, capability]) => capability.state !== 'available')
    .sort(([left], [right]) =>
      compareUnicodeCodePoints(left, right),
    )
    .map(([capability, value]) => ({
      capability,
      state: value.state,
      ...(value.reason ? { reason: value.reason } : {}),
    }))
}

export function buildCountryOutageReportAuditManifest(
  compiled: CompiledCountryOutageReport,
): CountryOutageReportAuditManifest {
  const { document, evidence } = compiled
  if (
    document.factSetId !== evidence.facts.factSetId ||
    canonicalJsonStringify(document.snapshot) !==
      canonicalJsonStringify(evidence.facts.snapshot)
  ) {
    throw new CountryOutageAuditManifestInputError(
      '报告文档与审计证据的快照身份不一致',
    )
  }
  const usedEvidenceRefs = collectDraftEvidenceRefs(compiled)
  assertValidatedEvidenceRefs(compiled, usedEvidenceRefs)
  const detailedRefPattern =
    /^(?:series:\/series\/\d+|series:\/(?:metric_extrema|resource_metric_extrema)\/|asns:\/items\/|fact_)/

  return {
    schemaVersion:
      COUNTRY_OUTAGE_REPORT_AUDIT_MANIFEST_SCHEMA_VERSION,
    reportIdentity: {
      schemaVersion: document.schemaVersion,
      artifactId: document.artifactId,
      reportContentSha256: document.reportContentSha256,
      generatedAt: document.generatedAt,
      aiGenerated: document.aiGenerated,
      humanReviewed: document.humanReviewed,
    },
    eventIdentity: {
      incidentId: document.event.incident_id,
      eventReference: document.event.legacy_reference,
      eventType: document.event.event_type,
      countryCode: document.event.country_code,
      countryName: document.event.country_name,
    },
    snapshotIdentity: { ...document.snapshot },
    factSetIdentity: {
      schemaVersion: evidence.facts.schemaVersion,
      factSetId: document.factSetId,
    },
    modelIdentity: { ...document.model },
    contractIdentity: {
      reportSpecificationVersion: document.reportSpecificationVersion,
      projectKnowledgeVersion: document.projectKnowledgeVersion,
      validatorRulesVersion: document.validatorRulesVersion,
      skillBundleSha256: document.skillBundleSha256,
    },
    evidenceTrace: {
      usedEvidenceRefs,
      overviewAndBoundaryRefs: usedEvidenceRefs.filter(
        (reference) => !detailedRefPattern.test(reference),
      ),
      keyObservationSlots: keyObservationSlots(
        compiled,
        usedEvidenceRefs,
      ),
      extremaPoints: extremaPoints(compiled, usedEvidenceRefs),
      derivedFacts: derivedFacts(compiled, usedEvidenceRefs),
      asnItems: asnItems(compiled, usedEvidenceRefs),
      evidenceRecordCount:
        countCountryOutageEvidenceRecords(evidence),
    },
    capabilityBoundary: {
      unavailableOrDegraded: unavailableOrDegraded(compiled),
      reportEligible: evidence.facts.eligibility.eligible,
      eligibilityReasons: [...evidence.facts.eligibility.reasons],
      missingRequiredFields: [
        ...evidence.facts.eligibility.missingRequiredFields,
      ],
      qualityStatus: evidence.facts.quality.status,
      missingSlotCount: evidence.facts.quality.missingSlotCount,
      limitations: [...evidence.facts.quality.limitations],
    },
    cannotAnswer: [...document.draft.unknowns],
    validation: {
      passed: document.validation.passed,
      errors: [...document.validation.errors],
      warnings: [...document.validation.warnings],
      checkedEvidenceRefs: [
        ...document.validation.checkedEvidenceRefs,
      ],
    },
  }
}

export function serializeCountryOutageReportAuditManifest(
  manifest: CountryOutageReportAuditManifest,
): Buffer {
  return Buffer.from(
    `${canonicalJsonStringify(manifest, 2)}\n`,
    'utf8',
  )
}

export function createCountryOutageReportAuditManifestArtifact(
  compiled: CompiledCountryOutageReport,
): {
  manifest: CountryOutageReportAuditManifest
  artifact: CountryOutageAuditManifestArtifact
} {
  const manifest = buildCountryOutageReportAuditManifest(compiled)
  const content = serializeCountryOutageReportAuditManifest(manifest)
  return {
    manifest,
    artifact: {
      filename: COUNTRY_OUTAGE_REPORT_AUDIT_MANIFEST_FILENAME,
      mediaType: 'application/json; charset=utf-8',
      byteLength: content.byteLength,
      sha256: createHash('sha256').update(content).digest('hex'),
      content,
    },
  }
}

export function describeCountryOutageReportAuditManifestArtifact(
  value: ReturnType<
    typeof createCountryOutageReportAuditManifestArtifact
  >,
): CountryOutageAuditManifestArtifactDescriptor {
  return {
    schemaVersion: value.manifest.schemaVersion,
    filename: value.artifact.filename,
    byteLength: value.artifact.byteLength,
    sha256: value.artifact.sha256,
  }
}
