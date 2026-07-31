import type {
  CountryOutageAgentEvent,
  CountryOutageArtifact,
  CountryOutageExternalAppendix,
  CountryOutageReportDocument,
} from '@/api/countryOutageAgent'
import type { EventObservation } from '@/types/api'

export interface CountryOutageFrozenReportBinding {
  eventReference: string
  incidentId: string
  countryCode: string
  countryName: string
  displayName: string
  publicationId: string
  revision: number
  dataThrough: string
  isFinal: boolean
  collectorId: 'rrc25'
  windowStartUtc: string
  windowEndUtc: string
  cohortId: string
}

export interface CountryOutageRuntimeDecision {
  accepted: boolean
  code:
    | 'accepted'
    | 'invalid_identity'
    | 'different_event'
    | 'revision_regression'
    | 'publication_regression'
    | 'publication_identity_conflict'
    | 'same_revision_identity_drift'
    | 'report_protocol_identity_conflict'
    | 'question_protocol_identity_conflict'
  message: string
}

export interface CountryOutageObservationRequestToken {
  readonly reference: string
  readonly generation: number
  readonly sequence: number
  readonly kind: 'initial' | 'refresh'
}

export interface CountryOutageAbortableRequestToken {
  readonly epoch: number
  readonly controller: AbortController
}

export interface CountryOutageQuestionEventMatch {
  accepted: boolean
  action: 'create' | 'update' | 'reject'
  index: number | null
  message: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function decodedReferencePart(value: string): string {
  try {
    return decodeURIComponent(value.replace(/\+/g, ' '))
  } catch {
    return value.replace(/\+/g, ' ')
  }
}

export function canonicalCountryOutageEventReference(
  value: string,
): string | null {
  let candidate = value.trim()
  if (!candidate) return null
  if (candidate.includes('?')) {
    try {
      const parsed = new URL(candidate, 'https://domeye.invalid')
      candidate = parsed.searchParams.get('ref') ?? candidate
    } catch {
      return null
    }
  }
  const parts = candidate.split('/')
  if (parts.length !== 5) return null
  const decoded = parts.map(decodedReferencePart)
  if (
    decoded[0] !== 'country_outage'
    || !decoded[1]
    || !decoded[2]
    || !/^\d+$/.test(decoded[3] ?? '')
    || !decoded[4]
  ) return null
  return decoded.join('/')
}

function nullableString(value: unknown): boolean {
  return value === null || typeof value === 'string'
}

function processingStatusIsValid(value: unknown): boolean {
  if (!isRecord(value)) return false
  return (
    ['idle', 'processing', 'waiting_for_source', 'failed', 'final']
      .includes(String(value.state ?? ''))
    && nullableString(value.updated_at)
    && nullableString(value.attempted_through)
    && nullableString(value.reason)
    && nullableString(value.last_complete_data_through)
  )
}

function releaseMetadataMatchesAudit(
  observation: EventObservation,
): boolean {
  const audit = observation.audit
  return Boolean(
    audit
    && audit.schema_version === 'country_outage_audit_v2'
    && audit.incident_id === observation.incident_id
    && audit.publication_id === observation.publication_id
    && audit.revision === observation.revision
    && audit.publication_state === observation.publication_state
    && audit.observation_state === observation.observation_state
    && audit.data_mode === observation.data_mode
    && audit.data_through === observation.data_through
    && audit.updated_at === observation.updated_at
    && audit.is_final === observation.is_final
    && JSON.stringify(audit.processing_status)
      === JSON.stringify(observation.processing_status)
    && audit.missing_slot_count === observation.missing_slot_count
    && audit.cohort_id === observation.cohort_id
    && audit.window_start_utc === observation.window_start_utc
    && audit.window_end_utc === observation.window_end_utc
    && audit.capability_contract_version
      === observation.capability_contract_version
  )
}

function pageObservationIdentityError(
  observation: EventObservation,
  expectedReference: string,
): string | null {
  const canonicalReference = canonicalCountryOutageEventReference(
    expectedReference,
  )
  const actualReference = canonicalCountryOutageEventReference(
    observation.event_identity?.legacy_reference ?? '',
  )
  const expectedCountryCode = canonicalReference?.split('/')[2]?.toUpperCase()
  const scope = observation.observation_scope
  const cohort = observation.cohort
  const audit = observation.audit

  if (!canonicalReference) return '当前路由事件引用无效'
  if (observation.schema_version !== 'country_outage_observation_v2') {
    return '观测不是 country_outage_observation_v2'
  }
  if (
    observation.event_identity?.event_type !== 'country_outage'
    || actualReference !== canonicalReference
  ) {
    return '观测事件引用或事件类型与当前路由不一致'
  }
  if (
    !isNonEmptyString(observation.incident_id)
    || observation.incident_id !== observation.event_identity.incident_id
  ) {
    return '观测 incident 身份未闭合'
  }
  if (
    !isNonEmptyString(observation.event_identity.country_code)
    || observation.event_identity.country_code.toUpperCase()
      !== expectedCountryCode
    || !isNonEmptyString(observation.event_identity.country_name)
    || !isNonEmptyString(observation.event_identity.display_name)
  ) {
    return '观测国家身份与当前事件不一致或不完整'
  }
  if (
    !Number.isSafeInteger(observation.revision)
    || (observation.revision ?? 0) < 1
    || !isNonEmptyString(observation.publication_id)
    || observation.publication_state !== 'published'
    || ![
      'legacy_summary',
      'aggregate_available',
      'state_partial',
      'state_complete',
      'evidence_complete',
    ].includes(observation.observation_state ?? '')
    || !['legacy', 'replay', 'live', 'mixed'].includes(
      observation.data_mode ?? '',
    )
    || !nullableString(observation.data_through)
    || !nullableString(observation.updated_at)
    || typeof observation.is_final !== 'boolean'
    || !Number.isSafeInteger(observation.missing_slot_count)
    || (observation.missing_slot_count ?? -1) < 0
    || observation.capability_contract_version
      !== 'country_outage_capabilities_v1'
    || !processingStatusIsValid(observation.processing_status)
  ) {
    return '观测完整发布身份不合法'
  }
  if (
    scope?.collector_id !== 'rrc25'
    || scope.collector_count !== 1
    || (
      scope.collector_ids !== undefined
      && (
        scope.collector_ids.length !== 1
        || scope.collector_ids[0] !== 'rrc25'
      )
    )
  ) {
    return '观测源不是唯一 rrc25'
  }
  if (
    !nullableString(observation.window_start_utc)
    || !nullableString(observation.window_end_utc)
    || observation.window_start_utc !== scope.window_start_utc
    || observation.window_end_utc !== scope.window_end_utc
  ) {
    return '观测窗口身份未闭合'
  }
  if (
    !(
      (
        observation.cohort_id === null
        && cohort === null
      )
      || (
        isNonEmptyString(observation.cohort_id)
        && Boolean(cohort)
        && observation.cohort_id === cohort?.cohort_id
      )
    )
  ) {
    return '观测 cohort 身份未闭合'
  }
  if (!audit || !releaseMetadataMatchesAudit(observation)) {
    return '观测与审计发布身份未闭合'
  }
  return null
}

export function validateCountryOutagePageObservationIdentity(
  observation: EventObservation,
  eventReference: string,
): CountryOutageRuntimeDecision {
  const error = pageObservationIdentityError(observation, eventReference)
  return error
    ? {
        accepted: false,
        code: 'invalid_identity',
        message: `观测页面身份无效：${error}`,
      }
    : {
        accepted: true,
        code: 'accepted',
        message: '',
      }
}

export function freezeCountryOutageReportBinding(
  observation: EventObservation,
  eventReference: string,
): CountryOutageFrozenReportBinding {
  const identityError = pageObservationIdentityError(
    observation,
    eventReference,
  )
  if (identityError) throw new Error(identityError)
  if (
    observation.publication_state !== 'published'
    || !isNonEmptyString(observation.data_through)
    || !isNonEmptyString(observation.cohort_id)
    || !observation.cohort
    || !isNonEmptyString(observation.window_start_utc)
    || !isNonEmptyString(observation.window_end_utc)
  ) {
    throw new Error('当前观测可展示，但尚未形成可冻结的报告快照')
  }
  return {
    eventReference:
      canonicalCountryOutageEventReference(eventReference) as string,
    incidentId: observation.incident_id as string,
    countryCode: observation.event_identity.country_code,
    countryName: observation.event_identity.country_name,
    displayName: observation.event_identity.display_name,
    publicationId: observation.publication_id as string,
    revision: observation.revision as number,
    dataThrough: observation.data_through as string,
    isFinal: observation.is_final as boolean,
    collectorId: 'rrc25',
    windowStartUtc: observation.window_start_utc as string,
    windowEndUtc: observation.window_end_utc as string,
    cohortId: observation.cohort_id as string,
  }
}

function observationStableEventIdentity(
  observation: EventObservation,
): string {
  return JSON.stringify({
    incidentId: observation.incident_id,
    legacyReference: canonicalCountryOutageEventReference(
      observation.event_identity.legacy_reference,
    ),
    legacyRecordTimeLocal:
      observation.event_identity.legacy_record_time_local,
    eventType: observation.event_identity.event_type,
    countryCode: observation.event_identity.country_code.toUpperCase(),
    countryName: observation.event_identity.country_name,
    displayName: observation.event_identity.display_name,
  })
}

function observationRevisionIdentity(
  observation: EventObservation,
): Record<string, unknown> {
  const processingStatus = observation.processing_status
  return {
    publicationId: observation.publication_id,
    publicationState: observation.publication_state,
    observationState: observation.observation_state,
    dataMode: observation.data_mode,
    dataThrough: observation.data_through,
    updatedAt: observation.updated_at,
    cohortId: observation.cohort_id,
    isFinal: observation.is_final,
    windowStartUtc: observation.window_start_utc,
    windowEndUtc: observation.window_end_utc,
    collectorId: observation.observation_scope.collector_id,
    collectorIds: JSON.stringify(
      observation.observation_scope.collector_ids ?? [],
    ),
    collectorCount: observation.observation_scope.collector_count,
    processingStatus: JSON.stringify(processingStatus ?? null),
    missingSlotCount: observation.missing_slot_count,
    capabilityContractVersion: observation.capability_contract_version,
    auditRunId: observation.audit?.run_id ?? null,
    auditArtifactSetId: observation.audit?.artifact_set_id ?? null,
    auditQualityStatus: observation.audit?.quality_status ?? null,
    auditHashesVerified:
      observation.audit?.consumed_deliverable_hashes_verified ?? null,
  }
}

interface CountryOutagePublicationHistoryEntry {
  publicationId: string
  revision: number
  dataThrough: string | null
  publicationState: 'published'
  publicationKind: 'baseline' | 'append' | 'status' | 'correction'
  supersedesPublicationId: string | null
  correctionReason: string | null
}

interface CountryOutagePublicationHistory {
  entries: CountryOutagePublicationHistoryEntry[]
  error: string | null
}

function validIsoDateTime(value: unknown): value is string {
  return (
    typeof value === 'string'
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
      .test(value)
    && Number.isFinite(Date.parse(value))
  )
}

function parsePublicationHistory(
  observation: EventObservation,
): CountryOutagePublicationHistory {
  const rawHistory = observation.audit?.revision_history
  if (!Array.isArray(rawHistory) || rawHistory.length === 0) {
    return { entries: [], error: 'audit.revision_history 缺失或为空' }
  }
  const entries: CountryOutagePublicationHistoryEntry[] = []
  const publicationIds = new Set<string>()
  for (const [index, rawEntry] of rawHistory.entries()) {
    if (!isRecord(rawEntry)) {
      return { entries: [], error: `publication 历史第 ${index + 1} 项不是对象` }
    }
    const publicationId = rawEntry.publication_id
    const revision = rawEntry.revision
    const dataThrough = rawEntry.data_through
    const publicationKind = rawEntry.publication_kind
    const publicationState = rawEntry.publication_state
    const supersedes = rawEntry.supersedes_publication_id
    const correctionReason = rawEntry.correction_reason
    const normalizedPublicationKind = (
      index === 0
      && (publicationKind === null || publicationKind === undefined)
      && (supersedes === null || supersedes === undefined)
      && (correctionReason === null || correctionReason === undefined)
    )
      ? 'baseline'
      : publicationKind
    if (
      !isNonEmptyString(publicationId)
      || !Number.isSafeInteger(revision)
      || Number(revision) < 1
      || publicationState !== 'published'
      || !(dataThrough === null || validIsoDateTime(dataThrough))
      || !['baseline', 'append', 'status', 'correction']
        .includes(String(normalizedPublicationKind ?? ''))
      || !(supersedes === null || supersedes === undefined
        || isNonEmptyString(supersedes))
      || !(correctionReason === null || correctionReason === undefined
        || isNonEmptyString(correctionReason))
    ) {
      return {
        entries: [],
        error: `publication 历史第 ${index + 1} 项身份不合法`,
      }
    }
    if (publicationIds.has(publicationId)) {
      return {
        entries: [],
        error: `publication 历史重复登记 ${publicationId}`,
      }
    }
    publicationIds.add(publicationId)
    const entry: CountryOutagePublicationHistoryEntry = {
      publicationId,
      revision: Number(revision),
      dataThrough,
      publicationState,
      publicationKind: normalizedPublicationKind as
        CountryOutagePublicationHistoryEntry['publicationKind'],
      supersedesPublicationId:
        typeof supersedes === 'string' ? supersedes : null,
      correctionReason:
        typeof correctionReason === 'string' ? correctionReason : null,
    }
    if (index === 0) {
      if (
        entry.publicationKind !== 'baseline'
        || entry.supersedesPublicationId !== null
      ) {
        return {
          entries: [],
          error: 'publication 历史首项必须是无替代关系的 baseline',
        }
      }
      entries.push(entry)
      continue
    }
    const previous = entries[index - 1]!
    if (entry.publicationKind === 'baseline') {
      return {
        entries: [],
        error: 'publication 历史只能在首项使用 baseline',
      }
    }
    if (entry.publicationKind === 'append') {
      if (
        entry.revision !== previous.revision
        || !validIsoDateTime(previous.dataThrough)
        || !validIsoDateTime(entry.dataThrough)
        || Date.parse(entry.dataThrough) <= Date.parse(previous.dataThrough)
        || entry.supersedesPublicationId !== null
        || entry.correctionReason !== null
      ) {
        return {
          entries: [],
          error: 'append publication 未保持 revision 或未严格推进截止点',
        }
      }
    } else if (entry.publicationKind === 'status') {
      if (
        entry.revision !== previous.revision
        || entry.dataThrough !== previous.dataThrough
        || entry.supersedesPublicationId !== null
        || entry.correctionReason !== null
      ) {
        return {
          entries: [],
          error: 'status publication 改变了 revision、截止点或替代关系',
        }
      }
    } else if (
      entry.revision !== previous.revision + 1
      || entry.supersedesPublicationId !== previous.publicationId
      || !entry.correctionReason
    ) {
      return {
        entries: [],
        error: 'correction publication 未精确升级 revision 或替代前一发布',
      }
    }
    entries.push(entry)
  }
  return { entries, error: null }
}

function historyEntryMatchesObservation(
  entry: CountryOutagePublicationHistoryEntry,
  observation: EventObservation,
): boolean {
  return (
    entry.publicationId === observation.publication_id
    && entry.revision === observation.revision
    && entry.dataThrough === observation.data_through
    && entry.publicationState === observation.publication_state
  )
}

export function decideCountryOutageObservationRefresh(
  current: EventObservation,
  incoming: EventObservation,
  expectedReference: string,
): CountryOutageRuntimeDecision {
  const incomingError = pageObservationIdentityError(
    incoming,
    expectedReference,
  )
  if (incomingError) {
    return {
      accepted: false,
      code: 'invalid_identity',
      message: `观测刷新协议错误：${incomingError}；已保留最近一次合法修订。`,
    }
  }
  if (
    observationStableEventIdentity(current)
      !== observationStableEventIdentity(incoming)
  ) {
    return {
      accepted: false,
      code: 'different_event',
      message: '观测刷新返回了其他事件或国家；已保留当前事件。',
    }
  }
  const currentRevision = current.revision ?? 0
  const incomingRevision = incoming.revision ?? 0
  const currentPublication = current.publication_id ?? ''
  const incomingPublication = incoming.publication_id ?? ''
  if (incomingRevision < currentRevision) {
    return {
      accepted: false,
      code: 'revision_regression',
      message: (
        `观测刷新 revision 从 ${currentRevision} 回退到 `
        + `${incomingRevision}；已保留较新修订。`
      ),
    }
  }
  if (incomingPublication === currentPublication) {
    if (incomingRevision !== currentRevision) {
      return {
        accepted: false,
        code: 'publication_identity_conflict',
        message: (
          `不可变 publication ${currentPublication} 的 revision `
          + '发生变化；已保留当前发布。'
        ),
      }
    }
    const currentIdentity = observationRevisionIdentity(current)
    const incomingIdentity = observationRevisionIdentity(incoming)
    const driftedFields = Object.keys(currentIdentity).filter(
      (field) => currentIdentity[field] !== incomingIdentity[field],
    )
    if (driftedFields.length > 0) {
      return {
        accepted: false,
        code: 'same_revision_identity_drift',
        message: (
          `观测刷新在 REV ${currentRevision} 发生不可变身份漂移`
          + `（${driftedFields.join('、')}）；已保留当前 publication。`
        ),
      }
    }
    return {
      accepted: true,
      code: 'accepted',
      message: '',
    }
  }
  // append/status 合同允许 revision 不变，因此以不可变发布历史验证单调推进。
  const history = parsePublicationHistory(incoming)
  if (history.error) {
    return {
      accepted: false,
      code: 'publication_regression',
      message: `观测刷新发布历史无效：${history.error}；已保留当前发布。`,
    }
  }
  const currentHistoryIndex = history.entries.findIndex(
    (entry) => entry.publicationId === currentPublication,
  )
  const incomingHistoryIndex = history.entries.findIndex(
    (entry) => entry.publicationId === incomingPublication,
  )
  if (
    currentHistoryIndex < 0
    || incomingHistoryIndex < 0
    || incomingHistoryIndex <= currentHistoryIndex
  ) {
    return {
      accepted: false,
      code: 'publication_regression',
      message: (
        `REV ${incomingRevision} 返回的 publication 未在审计历史中`
        + '单调推进；已保留当前发布。'
      ),
    }
  }
  const currentHistory = history.entries[currentHistoryIndex]!
  const incomingHistory = history.entries[incomingHistoryIndex]!
  if (
    !historyEntryMatchesObservation(currentHistory, current)
    || !historyEntryMatchesObservation(incomingHistory, incoming)
  ) {
    return {
      accepted: false,
      code: 'publication_identity_conflict',
      message: (
        '观测刷新顶层发布身份与 audit.revision_history 不一致；'
        + '已保留当前发布。'
      ),
    }
  }
  const traversed = history.entries.slice(
    currentHistoryIndex + 1,
    incomingHistoryIndex + 1,
  )
  const crossedCorrection = traversed.some(
    (entry) => entry.publicationKind === 'correction',
  )
  if (
    !crossedCorrection
    && (
      incoming.cohort_id !== current.cohort_id
      || incoming.window_start_utc !== current.window_start_utc
    )
  ) {
    return {
      accepted: false,
      code: 'publication_identity_conflict',
      message: (
        'append/status publication 改变了固定 cohort 或旧窗口起点；'
        + '已保留当前发布。'
      ),
    }
  }
  if (
    traversed.every((entry) => entry.publicationKind === 'status')
    && incoming.window_end_utc !== current.window_end_utc
  ) {
    return {
      accepted: false,
      code: 'publication_identity_conflict',
      message: 'status publication 改变了固定观测窗口；已保留当前发布。',
    }
  }
  return {
    accepted: true,
    code: 'accepted',
    message: '',
  }
}

function snapshotIdentityValues(
  value: unknown,
): Record<string, unknown> | null {
  if (!isRecord(value)) return null
  return {
    incidentId: value.incidentId,
    publicationId: value.publicationId,
    revision: value.revision,
    dataThrough: value.dataThrough,
    isFinal: value.isFinal,
    collectorId: value.collectorId,
    windowStartUtc: value.windowStartUtc,
    windowEndUtc: value.windowEndUtc,
    cohortId: value.cohortId,
  }
}

export function sameCountryOutageSnapshotIdentity(
  left: unknown,
  right: unknown,
): boolean {
  const leftIdentity = snapshotIdentityValues(left)
  const rightIdentity = snapshotIdentityValues(right)
  return (
    leftIdentity !== null
    && rightIdentity !== null
    && Object.keys(leftIdentity).every(
      (field) => leftIdentity[field] === rightIdentity[field],
    )
  )
}

export function appendixMatchesCurrentReport(
  appendix: unknown,
  currentReport: CountryOutageReportDocument | null,
): appendix is CountryOutageExternalAppendix {
  if (!isRecord(appendix) || !currentReport) return false
  const binding = appendix.frozen_binding
  if (!isRecord(binding)) return false
  return (
    binding.incident_id === currentReport.event.incident_id
    && binding.publication_id === currentReport.snapshot.publicationId
    && binding.revision === currentReport.snapshot.revision
    && binding.data_through === currentReport.snapshot.dataThrough
    && binding.fact_set_id === currentReport.factSetId
    && binding.cohort_id === currentReport.snapshot.cohortId
    && binding.country_code === currentReport.event.country_code
    && binding.collector_id === currentReport.snapshot.collectorId
    && binding.collector_id === 'rrc25'
    && binding.window_start_utc === currentReport.snapshot.windowStartUtc
    && binding.window_end_utc === currentReport.snapshot.windowEndUtc
  )
}

function reportSnapshotMatchesBinding(
  snapshot: CountryOutageReportDocument['snapshot'],
  binding: CountryOutageFrozenReportBinding,
): boolean {
  return sameCountryOutageSnapshotIdentity(snapshot, {
    incidentId: binding.incidentId,
    publicationId: binding.publicationId,
    revision: binding.revision,
    dataThrough: binding.dataThrough,
    isFinal: binding.isFinal,
    collectorId: binding.collectorId,
    windowStartUtc: binding.windowStartUtc,
    windowEndUtc: binding.windowEndUtc,
    cohortId: binding.cohortId,
  })
}

const COUNTRY_OUTAGE_SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/
const COUNTRY_OUTAGE_REPORT_ID = /^report_[a-f0-9]{32}$/
const COUNTRY_OUTAGE_FACT_SET_ID = /^facts_[a-f0-9]{32}$/
const COUNTRY_OUTAGE_SHA256 = /^[0-9a-f]{64}$/
const COUNTRY_OUTAGE_EVIDENCE_REF =
  /^(?:[a-z][a-z0-9_]*:[A-Za-z0-9_~./:@+-]{1,480}|fact_[A-Za-z0-9_-]{1,128})$/
const COUNTRY_OUTAGE_SECTION_IDS = new Set([
  'scope',
  'key_numbers',
  'visibility',
  'asn_scope',
  'address_families',
  'updates',
  'end_state',
  'resources',
  'assessment',
])

function stringArrayMatches(
  value: unknown,
  predicate: (item: string) => boolean = () => true,
): value is string[] {
  return (
    Array.isArray(value)
    && value.every((item) => typeof item === 'string' && predicate(item))
  )
}

function evidenceRefsAreValid(value: unknown): value is string[] {
  return stringArrayMatches(
    value,
    (item) => item.length <= 512 && COUNTRY_OUTAGE_EVIDENCE_REF.test(item),
  )
}

function reportParagraphIsValid(value: unknown): boolean {
  return (
    isRecord(value)
    && typeof value.text === 'string'
    && evidenceRefsAreValid(value.evidenceRefs)
  )
}

function reportSnapshotStructureError(value: unknown): string | null {
  if (!isRecord(value)) return '报告快照不是对象'
  if (
    !isNonEmptyString(value.incidentId)
    || !COUNTRY_OUTAGE_SAFE_ID.test(value.incidentId)
    || !isNonEmptyString(value.publicationId)
    || !COUNTRY_OUTAGE_SAFE_ID.test(value.publicationId)
    || !Number.isSafeInteger(value.revision)
    || Number(value.revision) < 1
    || !(value.dataThrough === null || validIsoDateTime(value.dataThrough))
    || typeof value.isFinal !== 'boolean'
    || value.collectorId !== 'rrc25'
    || !validIsoDateTime(value.windowStartUtc)
    || !validIsoDateTime(value.windowEndUtc)
    || Date.parse(value.windowEndUtc) < Date.parse(value.windowStartUtc)
    || !isNonEmptyString(value.cohortId)
    || !COUNTRY_OUTAGE_SAFE_ID.test(value.cohortId)
  ) {
    return '报告快照字段、时间或唯一 RRC25 身份不合法'
  }
  return null
}

function reportDraftStructureError(value: unknown): string | null {
  if (!isRecord(value)) return '报告正文不是对象'
  if (
    value.schemaVersion !== 'country_outage_report_draft_v1'
    || typeof value.title !== 'string'
    || typeof value.subtitle !== 'string'
    || !reportParagraphIsValid(value.summary)
    || !Array.isArray(value.highlights)
    || !Array.isArray(value.sections)
    || !stringArrayMatches(value.unknowns)
  ) {
    return '报告正文基础结构或 schemaVersion 不合法'
  }
  for (const [index, highlight] of value.highlights.entries()) {
    if (
      !isRecord(highlight)
      || typeof highlight.label !== 'string'
      || typeof highlight.value !== 'string'
      || !evidenceRefsAreValid(highlight.evidenceRefs)
    ) {
      return `报告重点数字第 ${index + 1} 项结构或 evidenceRefs 不合法`
    }
  }
  const sectionIds = new Set<string>()
  for (const [index, section] of value.sections.entries()) {
    if (
      !isRecord(section)
      || typeof section.id !== 'string'
      || !COUNTRY_OUTAGE_SECTION_IDS.has(section.id)
      || sectionIds.has(section.id)
      || typeof section.title !== 'string'
      || !Array.isArray(section.paragraphs)
      || !section.paragraphs.every(reportParagraphIsValid)
    ) {
      return `报告章节第 ${index + 1} 项结构、段落或 evidenceRefs 不合法`
    }
    sectionIds.add(section.id)
  }
  return null
}

function reportDocumentStructureError(value: unknown): string | null {
  if (!isRecord(value)) return '正式报告不是对象'
  if (
    value.schemaVersion !== 'country_outage_report_document_v1'
    || value.reportSpecificationVersion
      !== 'country_outage_report_spec_v1'
    || value.projectKnowledgeVersion !== 'country_outage_report_skill_v6'
    || value.validatorRulesVersion
      !== 'country_outage_report_validator_rules_v5'
  ) {
    return '正式报告 schema、规范、项目知识或校验规则版本不合法'
  }
  if (
    typeof value.artifactId !== 'string'
    || !COUNTRY_OUTAGE_REPORT_ID.test(value.artifactId)
    || typeof value.factSetId !== 'string'
    || !COUNTRY_OUTAGE_FACT_SET_ID.test(value.factSetId)
    || typeof value.reportContentSha256 !== 'string'
    || !COUNTRY_OUTAGE_SHA256.test(value.reportContentSha256)
    || typeof value.skillBundleSha256 !== 'string'
    || !COUNTRY_OUTAGE_SHA256.test(value.skillBundleSha256)
    || !validIsoDateTime(value.generatedAt)
    || value.aiGenerated !== true
    || value.humanReviewed !== false
  ) {
    return '正式报告 ID、SHA-256、生成时间或信任标志不合法'
  }
  if (
    !isRecord(value.event)
    || !isNonEmptyString(value.event.incident_id)
    || !COUNTRY_OUTAGE_SAFE_ID.test(value.event.incident_id)
    || !isNonEmptyString(value.event.legacy_reference)
    || value.event.event_type !== 'country_outage'
    || typeof value.event.country_code !== 'string'
    || !/^[A-Z]{2}$/.test(value.event.country_code)
    || !isNonEmptyString(value.event.country_name)
    || !isNonEmptyString(value.event.display_name)
  ) {
    return '正式报告事件身份结构不合法'
  }
  const snapshotError = reportSnapshotStructureError(value.snapshot)
  if (snapshotError) return snapshotError
  if (
    !isRecord(value.model)
    || !isNonEmptyString(value.model.provider)
    || !isNonEmptyString(value.model.model)
    || !isNonEmptyString(value.model.modelVersion)
    || !['pi-sdk', 'deterministic-acceptance'].includes(
      String(value.model.adapter ?? ''),
    )
    || !(
      value.model.piVersion === undefined
      || isNonEmptyString(value.model.piVersion)
    )
    || !(
      value.model.runtimeIdentity === undefined
      || ['formal', 'candidate'].includes(
        String(value.model.runtimeIdentity),
      )
    )
  ) {
    return '正式报告模型身份结构不合法'
  }
  if (
    !isRecord(value.validation)
    || value.validation.passed !== true
    || !Array.isArray(value.validation.errors)
    || value.validation.errors.length !== 0
    || !stringArrayMatches(value.validation.warnings)
    || !evidenceRefsAreValid(value.validation.checkedEvidenceRefs)
  ) {
    return '正式报告未通过 v5 校验或校验结果结构不合法'
  }
  return reportDraftStructureError(value.draft)
}

function artifactIdentityError(
  artifacts: unknown,
  artifactId: string,
): string | null {
  if (!Array.isArray(artifacts) || artifacts.length !== 2) {
    return 'Markdown/PDF 制品登记不完整'
  }
  const formats = artifacts.map(
    (artifact) => isRecord(artifact) ? artifact.format : null,
  )
  if (
    new Set(formats).size !== 2
    || !formats.includes('markdown')
    || !formats.includes('pdf')
  ) {
    return 'Markdown/PDF 制品格式登记重复或缺失'
  }
  if (
    artifacts.filter(
      (artifact) => isRecord(artifact) && artifact.status === 'ready',
    ).length < 1
  ) {
    return '完成事件至少需要一种 ready 下载制品'
  }
  for (const [index, artifact] of artifacts.entries()) {
    if (!isRecord(artifact)) {
      return `制品登记第 ${index + 1} 项不是对象`
    }
    const format = artifact.format
    if (artifact.status === 'ready') {
      const expectedMediaType = format === 'markdown'
        ? 'text/markdown; charset=utf-8'
        : 'application/pdf'
      if (
        artifact.artifact_id !== artifactId
        || !isNonEmptyString(artifact.filename)
        || artifact.filename.length > 255
        || /[\u0000-\u001f/\\]/.test(artifact.filename)
        || artifact.media_type !== expectedMediaType
        || !Number.isSafeInteger(artifact.byte_length)
        || Number(artifact.byte_length) < 1
        || typeof artifact.sha256 !== 'string'
        || !COUNTRY_OUTAGE_SHA256.test(artifact.sha256)
      ) {
        return `${String(format)} ready 制品结构或内容身份不合法`
      }
    } else if (
      artifact.status !== 'failed'
      || !isNonEmptyString(artifact.code)
      || !isNonEmptyString(artifact.message)
    ) {
      return `${String(format)} failed 制品结构不合法`
    }
  }
  return null
}

function deterministicJsonEqual(left: unknown, right: unknown): boolean {
  const canonicalize = (value: unknown): string => {
    if (value === null) return 'null'
    if (Array.isArray(value)) {
      return `[${value.map((item) => canonicalize(item)).join(',')}]`
    }
    if (isRecord(value)) {
      return `{${Object.keys(value)
        .filter((key) => value[key] !== undefined)
        .sort()
        .map(
          (key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`,
        )
        .join(',')}}`
    }
    const serialized = JSON.stringify(value)
    return serialized === undefined ? 'undefined' : serialized
  }
  return canonicalize(left) === canonicalize(right)
}

export function validateCompletedCountryOutageReportEvent(
  event: CountryOutageAgentEvent,
  options: {
    expectedReportId: string
    expectedRunId: string
    binding: CountryOutageFrozenReportBinding | null
    retainedReport?: CountryOutageReportDocument | null
    retainedArtifacts?: CountryOutageArtifact[] | null
  },
): CountryOutageRuntimeDecision {
  const conflict = (detail: string): CountryOutageRuntimeDecision => ({
    accepted: false,
    code: 'report_protocol_identity_conflict',
    message: `报告完成事件协议错误：${detail}；未替换页面中的冻结报告。`,
  })
  if (
    event.event_type !== 'report_state'
    || event.phase !== 'completed'
    || event.report_id !== options.expectedReportId
    || event.run_id !== options.expectedRunId
  ) {
    return conflict('report/run 身份与当前运行不一致')
  }
  if (!options.binding) return conflict('当前运行缺少前端冻结身份')
  if (!event.report || !event.snapshot) {
    return conflict('完成事件缺少正式报告或快照身份')
  }
  const reportStructureError = reportDocumentStructureError(event.report)
  if (reportStructureError) return conflict(reportStructureError)
  const report = event.report
  if (!sameCountryOutageSnapshotIdentity(event.snapshot, report.snapshot)) {
    return conflict('event.snapshot 与 event.report.snapshot 不一致')
  }
  if (
    report.event.event_type !== 'country_outage'
    || canonicalCountryOutageEventReference(report.event.legacy_reference)
      !== options.binding.eventReference
    || report.event.incident_id !== options.binding.incidentId
    || report.event.country_code !== options.binding.countryCode
    || report.event.country_name !== options.binding.countryName
    || report.event.display_name !== options.binding.displayName
  ) {
    return conflict('报告事件或国家身份与当前冻结事件不一致')
  }
  if (!reportSnapshotMatchesBinding(report.snapshot, options.binding)) {
    return conflict('报告快照与当前冻结 publication/revision 不一致')
  }
  if (
    !isNonEmptyString(report.artifactId)
    || !isNonEmptyString(report.factSetId)
    || !isNonEmptyString(report.reportContentSha256)
  ) {
    return conflict('报告 artifact、factSet 或正文哈希身份不完整')
  }
  const artifactError = artifactIdentityError(
    event.artifacts,
    report.artifactId,
  )
  if (artifactError) return conflict(artifactError)
  if (
    options.retainedReport
    && (
      options.retainedReport.artifactId !== report.artifactId
      || options.retainedReport.factSetId !== report.factSetId
      || options.retainedReport.reportContentSha256
        !== report.reportContentSha256
    )
  ) {
    return conflict('同一 report 的 artifact/factSet/正文身份发生漂移')
  }
  if (
    options.retainedReport
    && !deterministicJsonEqual(options.retainedReport, report)
  ) {
    return conflict('同一 report 的完整 document 内容发生漂移')
  }
  if (
    options.retainedReport
    && (
      !Array.isArray(options.retainedArtifacts)
      || !deterministicJsonEqual(
        options.retainedArtifacts,
        event.artifacts,
      )
    )
  ) {
    return conflict('同一 report 的下载制品 metadata 发生漂移')
  }
  return {
    accepted: true,
    code: 'accepted',
    message: '',
  }
}

export function validateCountryOutageQuestionAnswerSnapshot(
  event: CountryOutageAgentEvent,
  currentReport: CountryOutageReportDocument | null,
): CountryOutageRuntimeDecision {
  const conflict = (detail: string): CountryOutageRuntimeDecision => ({
    accepted: false,
    code: 'question_protocol_identity_conflict',
    message: `问题完成事件协议错误：${detail}；未改写现有研读记录。`,
  })
  if (
    event.event_type !== 'question_state'
    || !isNonEmptyString(event.run_id)
    || !event.question
    || !isNonEmptyString(event.question.question_id)
  ) {
    return conflict('run/question 身份不完整')
  }
  if (!currentReport) return conflict('页面没有可绑定的冻结报告')
  const answer = event.question.answer
  if (!answer) {
    return event.phase === 'completed'
      ? conflict('completed 正式回答缺失')
      : {
          accepted: true,
          code: 'accepted',
          message: '',
        }
  }
  if (!answer.snapshot) {
    return conflict('正式回答缺少冻结快照身份')
  }
  if (
    !sameCountryOutageSnapshotIdentity(
      answer.snapshot,
      currentReport.snapshot,
    )
  ) {
    return conflict('answer.snapshot 与当前报告快照不一致')
  }
  return {
    accepted: true,
    code: 'accepted',
    message: '',
  }
}

/**
 * 事件详情页的请求门闩。刷新请求单飞；路由切换或新的初始请求会使旧 token
 * 立即失效。旧请求的 finally 也不能释放新路由上的刷新请求。
 */
export class CountryOutageObservationRequestGate {
  private reference = ''
  private generation = 0
  private sequence = 0
  private latestSequence = 0
  private activeRefreshSequence: number | null = null

  setReference(reference: string): void {
    this.reference = reference
    this.generation += 1
    this.latestSequence = 0
    this.activeRefreshSequence = null
  }

  invalidate(): void {
    this.reference = ''
    this.generation += 1
    this.latestSequence = 0
    this.activeRefreshSequence = null
  }

  beginInitial(): CountryOutageObservationRequestToken {
    const token = this.createToken('initial')
    this.latestSequence = token.sequence
    return token
  }

  beginRefresh(): CountryOutageObservationRequestToken | null {
    if (this.activeRefreshSequence !== null) return null
    const token = this.createToken('refresh')
    this.latestSequence = token.sequence
    this.activeRefreshSequence = token.sequence
    return token
  }

  isCurrent(token: CountryOutageObservationRequestToken): boolean {
    return (
      token.reference === this.reference
      && token.generation === this.generation
      && token.sequence === this.latestSequence
    )
  }

  finish(token: CountryOutageObservationRequestToken): void {
    if (
      token.kind === 'refresh'
      && token.generation === this.generation
      && token.sequence === this.activeRefreshSequence
    ) {
      this.activeRefreshSequence = null
    }
  }

  private createToken(
    kind: CountryOutageObservationRequestToken['kind'],
  ): CountryOutageObservationRequestToken {
    this.sequence += 1
    return {
      reference: this.reference,
      generation: this.generation,
      sequence: this.sequence,
      kind,
    }
  }
}

export class CountryOutageAbortableRequestGate {
  private epoch = 0
  private active: CountryOutageAbortableRequestToken | null = null

  begin(): CountryOutageAbortableRequestToken {
    this.invalidate()
    const token = {
      epoch: this.epoch,
      controller: new AbortController(),
    }
    this.active = token
    return token
  }

  isCurrent(token: CountryOutageAbortableRequestToken): boolean {
    return (
      this.active === token
      && token.epoch === this.epoch
      && !token.controller.signal.aborted
    )
  }

  finish(token: CountryOutageAbortableRequestToken): boolean {
    if (!this.isCurrent(token)) return false
    this.active = null
    return true
  }

  invalidate(): void {
    this.epoch += 1
    this.active?.controller.abort()
    this.active = null
  }
}

export function matchCountryOutageQuestionEvent(
  entries: ReadonlyArray<{ runId: string; questionId: string }>,
  runId: string | undefined,
  questionId: string | undefined,
): CountryOutageQuestionEventMatch {
  if (!isNonEmptyString(runId) || !isNonEmptyString(questionId)) {
    return {
      accepted: false,
      action: 'reject',
      index: null,
      message: '问题状态事件缺少 runId 或 questionId',
    }
  }
  const runIndexes = entries.flatMap(
    (entry, index) => entry.runId === runId ? [index] : [],
  )
  const questionIndexes = entries.flatMap(
    (entry, index) => entry.questionId === questionId ? [index] : [],
  )
  if (runIndexes.length === 0 && questionIndexes.length === 0) {
    return {
      accepted: true,
      action: 'create',
      index: null,
      message: '',
    }
  }
  if (
    runIndexes.length !== 1
    || questionIndexes.length !== 1
    || runIndexes[0] !== questionIndexes[0]
  ) {
    return {
      accepted: false,
      action: 'reject',
      index: null,
      message: '问题状态事件的 runId 与 questionId 未指向同一条研读记录',
    }
  }
  return {
    accepted: true,
    action: 'update',
    index: runIndexes[0]!,
    message: '',
  }
}
