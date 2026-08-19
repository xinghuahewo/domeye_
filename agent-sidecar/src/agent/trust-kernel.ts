import type {
  DomeyeActionReceipt,
  DomeyeArtifactEnvelope,
  DomeyeCapabilityProposal,
  DomeyeDataIdentity,
  DomeyeGoalState,
  DomeyeInteractiveAction,
} from './contracts.js'
import {
  DomeyeActionReceiptSchema,
  DomeyeArtifactEnvelopeSchema,
  DomeyeCapabilityProposalSchema,
  DomeyeDataIdentitySchema,
  DomeyeExecutionBindingSchema,
  DomeyeGoalStateSchema,
  DomeyeInteractiveActionSchema,
} from './contracts.js'
import { canonicalJsonSha256 } from '../shared/deterministic-json.js'
import { Check } from 'typebox/value'

const TENANT_ID = 'domeye' as const
const REQUIRED_SCOPE = 'country_outage:read'
const APPROVED_ACTION_LIMIT = 2
const MODEL_API_ATTEMPT_LIMIT = 10

type ExecutionBinding = DomeyeInteractiveAction['execution_binding']

export type DomeyeAdmissionRejectionCode =
  | 'invalid_principal'
  | 'invalid_proposal'
  | 'permission_denied'
  | 'tenant_mismatch'
  | 'candidate_mismatch'
  | 'policy_inactive'
  | 'capability_not_allowed'
  | 'registry_snapshot_inactive'
  | 'capability_not_registered'
  | 'execution_binding_conflict'
  | 'revoked'
  | 'model_api_attempt_limit_exceeded'
  | 'approved_action_limit_exceeded'
  | 'goal_state_conflict'
  | 'action_history_conflict'
  | 'source_artifact_missing'
  | 'source_artifact_not_succeeded'
  | 'source_artifact_conflict'

export interface DomeyePrincipalView {
  readonly principal_id: string
  readonly authorization_scopes: readonly string[]
}

export interface DomeyePolicySnapshotView {
  readonly policy_id: string
  readonly policy_digest: string
  readonly state: 'active' | 'revoked'
  readonly allowed_capability_ids: readonly DomeyeCapabilityProposal['capability_id'][]
}

export interface DomeyeRegistryCapabilityView {
  readonly capability_id: DomeyeCapabilityProposal['capability_id']
  readonly state: 'active' | 'revoked'
  readonly execution_binding: ExecutionBinding
}

export interface DomeyeRegistrySnapshotView {
  readonly registry_snapshot_id: string
  readonly registry_digest: string
  readonly state: 'active' | 'revoked'
  readonly capabilities: readonly DomeyeRegistryCapabilityView[]
}

export interface DomeyeRevocationView {
  readonly state: 'not_revoked' | 'revoked'
  readonly checked_at_utc: string
  readonly reason_code: string | null
}

export interface DomeyeGoalStateBinding {
  readonly goal_id: string
  readonly state_revision: number
  readonly state_digest: string
  readonly artifact_ids: readonly string[]
}

export interface DomeyeAdmissionRequest {
  readonly proposal: DomeyeCapabilityProposal
  readonly proposal_sequence: number
  readonly goal_state: DomeyeGoalState
  readonly principal: DomeyePrincipalView
  readonly tenant_id: string
  readonly data_identity: DomeyeDataIdentity
  readonly candidate_id: string
  readonly policy: DomeyePolicySnapshotView
  readonly registry: DomeyeRegistrySnapshotView
  readonly revocation: DomeyeRevocationView
  readonly model_api_attempts_used: number
  readonly action_history: readonly DomeyeActionReceipt[]
  readonly artifacts: readonly DomeyeArtifactEnvelope[]
  readonly admitted_at_utc: string
}

export interface DomeyeAdmissionReceipt {
  readonly schema_version: 'domeye_agent_admission_receipt_v1'
  readonly receipt_id: string
  readonly proposal_id: string
  readonly proposal_sequence: number
  readonly capability_id: DomeyeCapabilityProposal['capability_id']
  readonly proposal_digest: string
  readonly input_digest: string
  readonly decision: 'admitted' | 'rejected'
  readonly reason_code: DomeyeAdmissionRejectionCode | null
  readonly principal: DomeyePrincipalView
  readonly tenant_id: typeof TENANT_ID
  readonly data_identity: DomeyeDataIdentity
  readonly candidate_id: string
  readonly goal_state: DomeyeGoalStateBinding
  readonly policy: {
    readonly policy_id: string
    readonly policy_digest: string
  }
  readonly registry: {
    readonly registry_snapshot_id: string
    readonly registry_digest: string
  }
  readonly budget: {
    readonly model_api_attempt_limit: typeof MODEL_API_ATTEMPT_LIMIT
    readonly model_api_attempts_used: number
    readonly approved_action_limit: typeof APPROVED_ACTION_LIMIT
    readonly approved_actions_used: number
    readonly monetary_limit_usd: null
    readonly cost_policy: 'audit_only'
  }
  readonly revocation: {
    readonly state: DomeyeRevocationView['state']
    readonly checked_at_utc: string
  }
  readonly occurred_action_ids: readonly string[]
  readonly action_history_digest: string
  readonly execution_binding: ExecutionBinding | null
  readonly created_at_utc: string
  readonly receipt_digest: string
}

export type DomeyeAdmissionDecision =
  | Readonly<{
      schema_version: 'domeye_agent_admission_decision_v1'
      status: 'admitted'
      proposal_id: string
      action: DomeyeInteractiveAction
      receipt: DomeyeAdmissionReceipt
    }>
  | Readonly<{
      schema_version: 'domeye_agent_admission_decision_v1'
      status: 'rejected'
      proposal_id: string
      action: null
      receipt: DomeyeAdmissionReceipt
    }>

export type DomeyeAdmittedDecision = Extract<
  DomeyeAdmissionDecision,
  { readonly status: 'admitted' }
>

function deepFreeze<T>(value: T): Readonly<T> {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value as Record<string, unknown>)) {
      deepFreeze(child)
    }
    Object.freeze(value)
  }
  return value
}

function digest(value: unknown): string {
  return `sha256:${canonicalJsonSha256(value)}`
}

function sameIdentity(
  left: DomeyeDataIdentity,
  right: DomeyeDataIdentity,
): boolean {
  return canonicalJsonSha256(left) === canonicalJsonSha256(right)
}

function expectedExecutionUnit(
  capabilityId: DomeyeCapabilityProposal['capability_id'],
): { readonly unit_id: 'TOOL-03' | 'OP-01', readonly name: string } {
  return capabilityId === 'CAP-006'
    ? { unit_id: 'TOOL-03', name: 'read_metric_series' }
    : { unit_id: 'OP-01', name: 'series_extrema' }
}

function isActionReceiptForRequest(
  receipt: DomeyeActionReceipt,
  request: DomeyeAdmissionRequest,
): boolean {
  const { receipt_digest: receiptDigest, ...receiptBody } = receipt
  return Check(DomeyeActionReceiptSchema, receipt)
    && receiptDigest === digest(receiptBody)
    && receipt.candidate_id === request.candidate_id
    && receipt.tenant_id === TENANT_ID
    && sameIdentity(receipt.data_identity, request.data_identity)
}

function sourceArtifactRejection(
  request: DomeyeAdmissionRequest,
): DomeyeAdmissionRejectionCode | null {
  if (request.proposal.capability_id !== 'CAP-016') return null
  const sourceArtifactId = request.proposal.input.source_artifact_id
  const sourceArtifact = request.artifacts.find((artifact) =>
    artifact.artifact_id === sourceArtifactId,
  )
  if (!sourceArtifact) return 'source_artifact_missing'
  const sourceReceipt = request.action_history.find((receipt) =>
    receipt.status === 'succeeded'
      && receipt.capability_id === 'CAP-006'
      && receipt.action_id === sourceArtifact.producer_action_id
      && receipt.artifact_ids.includes(sourceArtifact.artifact_id),
  )
  if (!sourceReceipt) return 'source_artifact_not_succeeded'
  const payload = sourceArtifact.payload as Record<string, unknown>
  if (
    !Check(DomeyeArtifactEnvelopeSchema, sourceArtifact)
    ||
    sourceArtifact.artifact_kind !== 'metric_series'
    || sourceArtifact.candidate_id !== request.candidate_id
    || sourceArtifact.tenant_id !== TENANT_ID
    || !sourceArtifact.immutable
    || !sameIdentity(sourceArtifact.data_identity, request.data_identity)
    || !isActionReceiptForRequest(sourceReceipt, request)
    || sourceArtifact.content_digest !== digest(payload)
    || !request.goal_state.artifact_ids.includes(sourceArtifact.artifact_id)
    || payload.metric !== request.proposal.input.metric
    || payload.unit !== 'unique_ipv4_address'
  ) {
    return 'source_artifact_conflict'
  }
  return null
}

function validateRequest(
  request: DomeyeAdmissionRequest,
): {
  readonly rejection: DomeyeAdmissionRejectionCode | null
  readonly execution_binding: ExecutionBinding | null
} {
  if (!request.principal.principal_id.trim()) {
    return { rejection: 'invalid_principal', execution_binding: null }
  }
  if (!Check(DomeyeCapabilityProposalSchema, request.proposal)) {
    return { rejection: 'invalid_proposal', execution_binding: null }
  }
  if (
    !Number.isSafeInteger(request.proposal_sequence)
    || request.proposal_sequence < 1
  ) {
    return { rejection: 'goal_state_conflict', execution_binding: null }
  }
  if (
    new Set(request.principal.authorization_scopes).size
      !== request.principal.authorization_scopes.length
    || !request.principal.authorization_scopes.includes(REQUIRED_SCOPE)
  ) {
    return { rejection: 'permission_denied', execution_binding: null }
  }
  if (request.tenant_id !== TENANT_ID) {
    return { rejection: 'tenant_mismatch', execution_binding: null }
  }
  if (!request.candidate_id.trim()) {
    return { rejection: 'candidate_mismatch', execution_binding: null }
  }
  const goalArtifactIds = new Set(request.goal_state.artifact_ids)
  if (
    !Check(DomeyeGoalStateSchema, request.goal_state)
    || !Check(DomeyeDataIdentitySchema, request.data_identity)
    || !request.goal_state.goal_id.trim()
    || !Number.isSafeInteger(request.goal_state.state_revision)
    || request.goal_state.state_revision < 0
    || goalArtifactIds.size !== request.goal_state.artifact_ids.length
    || request.goal_state.state_revision < request.action_history.length
    || request.goal_state.status !== 'active'
    || request.proposal.goal_id !== request.goal_state.goal_id
    || request.proposal.goal_state_revision
      !== request.goal_state.state_revision
    || request.proposal.capability_id === 'CAP-016'
      && !goalArtifactIds.has(request.proposal.input.source_artifact_id)
  ) {
    return { rejection: 'goal_state_conflict', execution_binding: null }
  }
  if (request.policy.state !== 'active') {
    return { rejection: 'policy_inactive', execution_binding: null }
  }
  if (
    !request.policy.policy_id.trim()
    || !/^sha256:[a-f0-9]{64}$/.test(request.policy.policy_digest)
  ) {
    return { rejection: 'policy_inactive', execution_binding: null }
  }
  if (!request.policy.allowed_capability_ids.includes(
    request.proposal.capability_id,
  )) {
    return { rejection: 'capability_not_allowed', execution_binding: null }
  }
  if (request.registry.state !== 'active') {
    return { rejection: 'registry_snapshot_inactive', execution_binding: null }
  }
  if (
    !request.registry.registry_snapshot_id.trim()
    || !/^sha256:[a-f0-9]{64}$/.test(request.registry.registry_digest)
  ) {
    return { rejection: 'registry_snapshot_inactive', execution_binding: null }
  }
  if (request.revocation.state !== 'not_revoked') {
    return { rejection: 'revoked', execution_binding: null }
  }
  if (
    !Number.isSafeInteger(request.model_api_attempts_used)
    || request.model_api_attempts_used < 0
    || request.model_api_attempts_used >= MODEL_API_ATTEMPT_LIMIT
  ) {
    return {
      rejection: 'model_api_attempt_limit_exceeded',
      execution_binding: null,
    }
  }
  if (!request.action_history.every((receipt) =>
    isActionReceiptForRequest(receipt, request)
  )) {
    return { rejection: 'action_history_conflict', execution_binding: null }
  }
  const occurredActionIds = new Set(
    request.action_history.map((receipt) => receipt.action_id),
  )
  if (occurredActionIds.size !== request.action_history.length) {
    return { rejection: 'action_history_conflict', execution_binding: null }
  }
  if (occurredActionIds.size >= APPROVED_ACTION_LIMIT) {
    return {
      rejection: 'approved_action_limit_exceeded',
      execution_binding: null,
    }
  }
  const registryCapability = request.registry.capabilities.find((item) =>
    item.capability_id === request.proposal.capability_id,
  )
  if (!registryCapability || registryCapability.state !== 'active') {
    return { rejection: 'capability_not_registered', execution_binding: null }
  }
  const expected = expectedExecutionUnit(request.proposal.capability_id)
  if (
    !Check(
      DomeyeExecutionBindingSchema,
      registryCapability.execution_binding,
    )
    || registryCapability.execution_binding.execution_unit_id !== expected.unit_id
    || registryCapability.execution_binding.execution_unit_name !== expected.name
    || !registryCapability.execution_binding.execution_unit_version.trim()
    || !/^sha256:[a-f0-9]{64}$/.test(
      registryCapability.execution_binding.contract_digest,
    )
    || !/^sha256:[a-f0-9]{64}$/.test(
      registryCapability.execution_binding.implementation_digest,
    )
    || !/^sha256:[a-f0-9]{64}$/.test(
      registryCapability.execution_binding.semantic_digest,
    )
  ) {
    return { rejection: 'execution_binding_conflict', execution_binding: null }
  }
  const artifactRejection = sourceArtifactRejection(request)
  if (artifactRejection) {
    return { rejection: artifactRejection, execution_binding: null }
  }
  return {
    rejection: null,
    execution_binding: registryCapability.execution_binding,
  }
}

function proposalId(request: DomeyeAdmissionRequest): string {
  return `proposal-sha256:${canonicalJsonSha256({
    candidate_id: request.candidate_id,
    proposal_sequence: request.proposal_sequence,
    capability_id: request.proposal.capability_id,
    proposal_digest: digest(request.proposal),
    input_digest: digest(request.proposal.input),
    proposal: request.proposal,
    goal_state_digest: digest(request.goal_state),
  })}`
}

function makeReceipt(
  request: DomeyeAdmissionRequest,
  proposal_id: string,
  decision: 'admitted' | 'rejected',
  rejection: DomeyeAdmissionRejectionCode | null,
  execution_binding: ExecutionBinding | null,
): DomeyeAdmissionReceipt {
  const occurredActionIds = [...new Set(
    request.action_history.map((receipt) => receipt.action_id),
  )]
  const actionHistoryDigest = digest(request.action_history)
  const withoutDigest = {
    schema_version: 'domeye_agent_admission_receipt_v1' as const,
    proposal_id,
    proposal_sequence: request.proposal_sequence,
    capability_id: request.proposal.capability_id,
    proposal_digest: digest(request.proposal),
    input_digest: digest(request.proposal.input),
    decision,
    reason_code: rejection,
    principal: {
      principal_id: request.principal.principal_id,
      authorization_scopes: [...request.principal.authorization_scopes],
    },
    tenant_id: TENANT_ID,
    data_identity: request.data_identity,
    candidate_id: request.candidate_id,
    goal_state: {
      goal_id: request.goal_state.goal_id,
      state_revision: request.goal_state.state_revision,
      state_digest: digest(request.goal_state),
      artifact_ids: [...request.goal_state.artifact_ids],
    },
    policy: {
      policy_id: request.policy.policy_id,
      policy_digest: request.policy.policy_digest,
    },
    registry: {
      registry_snapshot_id: request.registry.registry_snapshot_id,
      registry_digest: request.registry.registry_digest,
    },
    budget: {
      model_api_attempt_limit: MODEL_API_ATTEMPT_LIMIT,
      model_api_attempts_used: request.model_api_attempts_used,
      approved_action_limit: APPROVED_ACTION_LIMIT,
      approved_actions_used: occurredActionIds.length
        + (decision === 'admitted' ? 1 : 0),
      monetary_limit_usd: null,
      cost_policy: 'audit_only' as const,
    },
    revocation: {
      state: request.revocation.state,
      checked_at_utc: request.revocation.checked_at_utc,
    },
    occurred_action_ids: occurredActionIds,
    action_history_digest: actionHistoryDigest,
    execution_binding,
    created_at_utc: request.admitted_at_utc,
  }
  const receiptId = `admission-receipt-sha256:${canonicalJsonSha256(withoutDigest)}`
  return deepFreeze({
    ...withoutDigest,
    receipt_id: receiptId,
    receipt_digest: digest({ ...withoutDigest, receipt_id: receiptId }),
  }) as DomeyeAdmissionReceipt
}

/**
 * 首片的逐 Action 确定性准入边界。每次调用都重新绑定完整权限上下文，
 * 不缓存会话开头的授权结论，也不签发未来动作。
 */
export class DomeyeTrustKernel {
  admit(request: DomeyeAdmissionRequest): DomeyeAdmissionDecision {
    const id = proposalId(request)
    const validation = validateRequest(request)
    if (validation.rejection || !validation.execution_binding) {
      const receipt = makeReceipt(
        request,
        id,
        'rejected',
        validation.rejection ?? 'capability_not_registered',
        null,
      )
      return deepFreeze({
        schema_version: 'domeye_agent_admission_decision_v1',
        status: 'rejected',
        proposal_id: id,
        action: null,
        receipt,
      })
    }

    const occurredActionIds = [...new Set(
      request.action_history.map((receipt) => receipt.action_id),
    )]
    const actionHistoryDigest = digest(request.action_history)
    const actionId = `action-sha256:${canonicalJsonSha256({
      proposal_id: id,
      candidate_id: request.candidate_id,
      principal_id: request.principal.principal_id,
      tenant_id: TENANT_ID,
      data_identity: request.data_identity,
      goal_state: {
        goal_id: request.goal_state.goal_id,
        state_revision: request.goal_state.state_revision,
        state_digest: digest(request.goal_state),
      },
      policy_digest: request.policy.policy_digest,
      registry_digest: request.registry.registry_digest,
      action_history_digest: actionHistoryDigest,
    })}`
    const actionValue = deepFreeze({
      schema_version: 'domeye_agent_interactive_action_v1',
      action_id: actionId,
      proposal_id: id,
      proposal_sequence: request.proposal_sequence,
      capability_id: request.proposal.capability_id,
      input: request.proposal.input,
      candidate_id: request.candidate_id,
      trust_binding: {
        principal: {
          principal_id: request.principal.principal_id,
          authorization_scopes: [...request.principal.authorization_scopes],
        },
        tenant_id: TENANT_ID,
        data_identity: request.data_identity,
        goal_state: {
          goal_id: request.goal_state.goal_id,
          state_revision: request.goal_state.state_revision,
          state_digest: digest(request.goal_state),
        },
        policy: {
          policy_id: request.policy.policy_id,
          policy_digest: request.policy.policy_digest,
        },
        registry: {
          registry_snapshot_id: request.registry.registry_snapshot_id,
          registry_digest: request.registry.registry_digest,
        },
        budget: {
          model_api_attempt_limit: MODEL_API_ATTEMPT_LIMIT,
          model_api_attempts_used: request.model_api_attempts_used,
          approved_action_limit: APPROVED_ACTION_LIMIT,
          approved_actions_used: occurredActionIds.length + 1,
          monetary_limit_usd: null,
          cost_policy: 'audit_only',
        },
        revocation: {
          state: 'not_revoked',
          checked_at_utc: request.revocation.checked_at_utc,
        },
        occurred_action_ids: occurredActionIds,
        action_history_digest: actionHistoryDigest,
      },
      execution_binding: validation.execution_binding,
      admitted_at_utc: request.admitted_at_utc,
    })
    if (!Check(DomeyeInteractiveActionSchema, actionValue)) {
      throw new Error('interactive_action_contract_violation')
    }
    const action = actionValue as DomeyeInteractiveAction
    const receipt = makeReceipt(
      request,
      id,
      'admitted',
      null,
      validation.execution_binding,
    )
    return deepFreeze({
      schema_version: 'domeye_agent_admission_decision_v1',
      status: 'admitted',
      proposal_id: id,
      action,
      receipt,
    })
  }
}

export const DOMEYE_FIRST_SLICE_ADMISSION_LIMITS = deepFreeze({
  tenant_id: TENANT_ID,
  required_scope: REQUIRED_SCOPE,
  approved_action_limit: APPROVED_ACTION_LIMIT,
  model_api_attempt_limit: MODEL_API_ATTEMPT_LIMIT,
  monetary_limit_usd: null,
  cost_policy: 'audit_only',
})
