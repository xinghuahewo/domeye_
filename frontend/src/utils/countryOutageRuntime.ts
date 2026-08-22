import type { EventObservation } from '@/types/api'

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
  message: string
}

export interface CountryOutageObservationRequestToken {
  readonly reference: string
  readonly generation: number
  readonly sequence: number
  readonly kind: 'initial' | 'refresh'
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
