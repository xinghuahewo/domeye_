import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { after, test } from 'node:test'

import {
  P2S1_DEFERRED_UNIT_IDS,
  P2S1_FROZEN_DESIGN_CANDIDATE_DIGEST,
  P2S1_FROZEN_DESIGN_CANDIDATE_ID,
  P2S1_V1_OPERATOR_IDS,
  P2S1_V1_TOOL_IDS,
  P2S1_W0_PROPOSAL_UNIT_IDS,
  P2S1RegistryProposalResolver,
  P2S1RegistryRuntimeError,
  createP2S1RegistryProposal,
  p2S1ExpectedAtomicCapabilityId,
  p2S1ExpectedDesignContractDigest,
  p2S1ExpectedUnitSemanticDigest,
  validateP2S1RegistryProposal,
  type P2S1RegistryProposalPayload,
  type P2S1RegistryProposalSnapshot,
  type P2S1RegistryProposalUnit,
  type P2S1RegistryUnitKind,
} from '../src/chat/p2-s1-registry-runtime.js'
import {
  P2S1TrustedReceiptError,
  P2S1TrustedReceiptStore,
  createP2S1TrustedReceipt,
  p2S1CanonicalJson,
  p2S1Digest,
  type P2S1PublicationIdentity,
  type P2S1ReceiptExpectation,
} from '../src/chat/p2-s1-trusted-receipt-store.js'

const temporaryRoot = mkdtempSync(join(tmpdir(), 'p2-s1-w0-governance-'))
after(() => rmSync(temporaryRoot, { recursive: true, force: true }))

const candidateId = P2S1_FROZEN_DESIGN_CANDIDATE_ID
const designDigest = P2S1_FROZEN_DESIGN_CANDIDATE_DIGEST
const existingSnapshotDigest = 'sha256:46e2c08b311b7b16e003a8eb56ec4f4fd2865ef4644a8bdbe7709c590c8514c2'
const existingSnapshotId = 'registry-snapshot-sha256:46e2c08b311b7b16e003a8eb56ec4f4fd2865ef4644a8bdbe7709c590c8514c2'

const publicationIdentity: P2S1PublicationIdentity = {
  event_type: 'country_outage',
  incident_id: 'incident_go_v1_a1de26f854831330c616a72af21597eb',
  publication_id: 'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f',
  revision: 1,
  cohort_id: 'country_event_cohort_v1_1e04abfc6430776bef20403fac528698',
  collector_id: 'rrc25',
  window_start_utc: '2026-02-27T00:10:00Z',
  window_end_utc: '2026-03-11T00:00:00Z',
  data_through_utc: '2026-03-11T00:00:00Z',
}

function unitKind(unitId: string): P2S1RegistryUnitKind {
  if (unitId.startsWith('TOOL-')) return 'tool'
  if (unitId.startsWith('OP-')) return 'operator'
  if (unitId.startsWith('PLAN-CAP-')) return 'plan_capability'
  return 'control'
}

function permission(kind: P2S1RegistryUnitKind): P2S1RegistryProposalUnit['permission'] {
  if (kind === 'tool') return 'country_outage:read'
  if (kind === 'operator') return 'country_outage:derive'
  if (kind === 'plan_capability') return 'country_outage:plan'
  return 'country_outage:control'
}

function proposalUnits(): P2S1RegistryProposalUnit[] {
  return P2S1_W0_PROPOSAL_UNIT_IDS.map((unitId) => {
    const kind = unitKind(unitId)
    const contractDigest = p2S1ExpectedDesignContractDigest(unitId)
    return {
      unit_id: unitId,
      unit_kind: kind,
      version: '1.0.0-design',
      activation_state: P2S1_DEFERRED_UNIT_IDS.includes(unitId as never)
        ? 'deferred'
        : unitId === 'OP-39' ? 'inactive' : 'proposed',
      atomic_capability_id: p2S1ExpectedAtomicCapabilityId(unitId),
      contract_digest: contractDigest,
      semantic_digest: p2S1ExpectedUnitSemanticDigest(unitId),
      implementation_status: 'not_implemented',
      implementation_digest: null,
      permission: permission(kind),
      identity_constraints: {
        event_type: 'country_outage',
        collector_id: 'rrc25',
        publication_cardinality: 1,
      },
      dependencies: unitId === 'TOOL-07' ? [{
        unit_id: 'TOOL-01',
        unit_version: '1.0.0',
        source: 'existing_registry',
        contract_digest: 'sha256:fd9169810375f1f8181e9a7c8fbd7c8fdfe24e7715d79dd8f2c0f50a160d0b21',
      }] : [],
    }
  })
}

function proposalPayload(identity = publicationIdentity): P2S1RegistryProposalPayload {
  return {
    candidate_id: candidateId,
    design_candidate_digest: designDigest,
    registry_revision: 3,
    activation_scope: 'w0_proposal_only',
    runtime_integration: 'governance_implemented_units_not_implemented',
    production_deployed: false,
    permission_mode: 'read_only',
    external_data_allowed: false,
    publication_identity: structuredClone(identity),
    existing_registry_binding: {
      registry_snapshot_id: existingSnapshotId,
      snapshot_digest: existingSnapshotDigest,
      candidate_id: 'p2-s0b-763eb09a654b8b29',
      registry_revision: 2,
      unit_bindings: [{
        unit_id: 'TOOL-01',
        version: '1.0.0',
        state: 'active',
        contract_digest: 'sha256:fd9169810375f1f8181e9a7c8fbd7c8fdfe24e7715d79dd8f2c0f50a160d0b21',
        implementation_digest: 'sha256:72fc464bf871a9688c23bd550479440cdcd9c53ce8d724b73deb4bbec17c38aa',
        semantic_digest: 'sha256:cc510ef729059e8413cfcf1e263845900c92c378cea0b169b773f788010d9216',
        permission: 'country_outage:read',
      }],
    },
    units: proposalUnits(),
  }
}

function proposal(identity = publicationIdentity): P2S1RegistryProposalSnapshot {
  return createP2S1RegistryProposal('2026-08-13T01:00:00Z', proposalPayload(identity))
}

function resolver(identity = publicationIdentity): P2S1RegistryProposalResolver {
  return new P2S1RegistryProposalResolver({
    candidate_id: candidateId,
    design_candidate_digest: designDigest,
    publication_identity: structuredClone(identity),
    existing_registry_snapshot_id: existingSnapshotId,
    existing_registry_snapshot_digest: existingSnapshotDigest,
  })
}

function reseal(snapshot: P2S1RegistryProposalSnapshot): P2S1RegistryProposalSnapshot {
  const mutable = structuredClone(snapshot)
  const digest = p2S1Digest(mutable.snapshot_payload)
  mutable.snapshot_digest = digest
  mutable.registry_snapshot_id = `p2-s1-registry-proposal-sha256:${digest.slice('sha256:'.length)}`
  return mutable
}

function expectRegistryError(code: string, operation: () => unknown): void {
  assert.throws(operation, (error: unknown) =>
    error instanceof P2S1RegistryRuntimeError && error.code === code)
}

function expectReceiptError(code: string, operation: () => unknown): void {
  assert.throws(operation, (error: unknown) =>
    error instanceof P2S1TrustedReceiptError && error.code === code)
}

test('可信回执以 canonical JSON 原子落盘并按类型、候选、publication、subject 解析', () => {
  const root = join(temporaryRoot, 'receipt-positive')
  const store = new P2S1TrustedReceiptStore(root)
  const subject = {
    kind: 'source_view' as const,
    id: 'fixed_cohort_member_rows',
    version: '1.0.0',
    content_digest: p2S1Digest({ population: 'fixed_cohort_member_rows' }),
  }
  const receipt = createP2S1TrustedReceipt({
    receipt_type: 'source_materialization',
    candidate_id: candidateId,
    publication_identity: publicationIdentity,
    subject,
    issued_at_utc: '2026-08-13T01:01:00Z',
    payload: { row_count: 9257, complete: true },
  })
  const stored = store.put(receipt)
  const expected: P2S1ReceiptExpectation = {
    receipt_type: 'source_materialization',
    candidate_id: candidateId,
    publication_identity: publicationIdentity,
    subject,
  }
  assert.deepEqual(store.read(receipt.receipt_digest, expected), stored)
  const path = join(root, `${receipt.receipt_digest.slice('sha256:'.length)}.json`)
  assert.equal(readFileSync(path, 'utf8'), `${p2S1CanonicalJson(receipt)}\n`)
  assert.equal(stored.payload_digest, p2S1Digest(stored.payload))
})

test('可信回执拒绝 ghost、篡改和跨上下文重放', () => {
  const root = join(temporaryRoot, 'receipt-negative')
  const store = new P2S1TrustedReceiptStore(root)
  const subject = {
    kind: 'registry_snapshot' as const,
    id: 'proposal',
    version: '4',
    content_digest: p2S1Digest({ proposal: 4 }),
  }
  const receipt = createP2S1TrustedReceipt({
    receipt_type: 'registry_proposal_admission',
    candidate_id: candidateId,
    publication_identity: publicationIdentity,
    subject,
    issued_at_utc: '2026-08-13T01:02:00Z',
    payload: { admitted: true },
  })
  const expectation: P2S1ReceiptExpectation = {
    receipt_type: receipt.receipt_type,
    candidate_id: candidateId,
    publication_identity: publicationIdentity,
    subject,
  }
  expectReceiptError('receipt_missing', () =>
    store.read(`sha256:${'0'.repeat(64)}`, expectation))
  store.put(receipt)
  expectReceiptError('receipt_candidate_replay_denied', () => store.read(receipt.receipt_digest, {
    ...expectation,
    candidate_id: `${candidateId}-other`,
  }))
  expectReceiptError('receipt_type_replay_denied', () => store.read(receipt.receipt_digest, {
    ...expectation,
    receipt_type: 'source_manifest',
  }))
  expectReceiptError('receipt_publication_replay_denied', () => store.read(receipt.receipt_digest, {
    ...expectation,
    publication_identity: { ...publicationIdentity, publication_id: 'cross-publication' },
  }))

  const path = join(root, `${receipt.receipt_digest.slice('sha256:'.length)}.json`)
  const tampered = structuredClone(receipt) as unknown as Record<string, unknown>
  tampered.payload = { admitted: false }
  writeFileSync(path, `${JSON.stringify(tampered)}\n`)
  expectReceiptError('receipt_payload_digest_mismatch', () =>
    store.read(receipt.receipt_digest, expectation))
})

test('可信回执根目录拒绝组写、其他用户写和符号链接祖先', () => {
  const unsafe = join(temporaryRoot, 'unsafe-world-writable')
  mkdirSync(unsafe, { mode: 0o700 })
  chmodSync(unsafe, 0o777)
  expectReceiptError('receipt_store_unsafe', () => new P2S1TrustedReceiptStore(unsafe))

  const actual = join(temporaryRoot, 'safe-actual')
  mkdirSync(actual, { mode: 0o700 })
  const linked = join(temporaryRoot, 'safe-linked')
  symlinkSync(actual, linked, 'dir')
  expectReceiptError('receipt_store_unsafe', () => new P2S1TrustedReceiptStore(linked))
})

test('W0 Registry proposal 只准入 proposed/inactive，回执明确执行授权为空', () => {
  const snapshot = proposal()
  const admission = resolver().admit(snapshot)
  assert.equal(admission.status, 'admitted_as_inactive_proposal')
  assert.equal(admission.registry_snapshot_id, snapshot.registry_snapshot_id)
  assert.equal(admission.snapshot_digest, snapshot.snapshot_digest)
  assert.deepEqual(admission.execution_allowed_unit_ids, [])
  assert.equal(admission.execution_started, false)
  assert.equal(admission.production_deployed, false)
  assert.deepEqual(admission.deferred_denied_unit_ids, [...P2S1_DEFERRED_UNIT_IDS].sort())
  assert.ok(admission.proposed_unit_ids.includes('TOOL-07'))
  assert.deepEqual(admission.inactive_unit_ids, ['OP-39'])

  const withoutDigest = structuredClone(admission) as unknown as Record<string, unknown>
  delete withoutDigest.receipt_digest
  assert.equal(admission.receipt_digest, p2S1Digest(withoutDigest))

  const store = new P2S1TrustedReceiptStore(join(temporaryRoot, 'registry-admission-receipt'))
  const trusted = createP2S1TrustedReceipt({
    receipt_type: 'registry_proposal_admission',
    candidate_id: candidateId,
    publication_identity: publicationIdentity,
    subject: {
      kind: 'registry_snapshot',
      id: snapshot.registry_snapshot_id,
      version: String(snapshot.snapshot_payload.registry_revision),
      content_digest: snapshot.snapshot_digest,
    },
    issued_at_utc: '2026-08-13T01:03:00Z',
    payload: structuredClone(admission) as unknown as Record<string, unknown>,
  })
  assert.equal(store.put(trusted).payload_digest, p2S1Digest(admission))
})

test('W0 Registry proposal 拒绝 snapshot 篡改、active/实现冒充、依赖与权限攻击', () => {
  const digestTampered = structuredClone(proposal())
  digestTampered.snapshot_payload.registry_revision = 5
  expectRegistryError('registry_revision_chain_invalid', () =>
    validateP2S1RegistryProposal(digestTampered))

  const active = structuredClone(proposal())
  active.snapshot_payload.units.find((unit) => unit.unit_id === 'TOOL-07')!.activation_state = 'active' as never
  expectRegistryError('registry_w0_active_forbidden', () =>
    validateP2S1RegistryProposal(reseal(active)))

  const implementationClaim = structuredClone(proposal())
  const implemented = implementationClaim.snapshot_payload.units.find((unit) => unit.unit_id === 'TOOL-07')!
  implemented.implementation_status = 'implemented' as never
  implemented.implementation_digest = p2S1Digest({ forged: true }) as never
  expectRegistryError('registry_w0_implementation_claim_forbidden', () =>
    validateP2S1RegistryProposal(reseal(implementationClaim)))

  const permissionTampered = structuredClone(proposal())
  permissionTampered.snapshot_payload.units.find((unit) => unit.unit_id === 'TOOL-07')!.permission = 'country_outage:control'
  expectRegistryError('registry_permission_denied', () =>
    validateP2S1RegistryProposal(reseal(permissionTampered)))

  const dependencyTampered = structuredClone(proposal())
  dependencyTampered.snapshot_payload.units.find((unit) => unit.unit_id === 'TOOL-07')!.dependencies[0]!.contract_digest = p2S1Digest({ forged: true })
  expectRegistryError('registry_dependency_invalid', () =>
    validateP2S1RegistryProposal(reseal(dependencyTampered)))

  const capabilityTampered = structuredClone(proposal())
  capabilityTampered.snapshot_payload.units.find((unit) => unit.unit_id === 'TOOL-07')!.atomic_capability_id = 'read.customer_cone'
  expectRegistryError('registry_unit_contract_drift', () =>
    validateP2S1RegistryProposal(reseal(capabilityTampered)))

  const semanticTampered = structuredClone(proposal())
  semanticTampered.snapshot_payload.units.find((unit) => unit.unit_id === 'OP-19')!.semantic_digest = p2S1Digest({ forged: true })
  expectRegistryError('registry_unit_contract_drift', () =>
    validateP2S1RegistryProposal(reseal(semanticTampered)))
})

test('Registry resolver 拒绝候选、既有 snapshot 与跨 publication 重放', () => {
  const snapshot = proposal()
  expectRegistryError('registry_candidate_binding_mismatch', () =>
    new P2S1RegistryProposalResolver({
      candidate_id: candidateId,
      design_candidate_digest: p2S1Digest({ other: 'design' }),
      publication_identity: publicationIdentity,
      existing_registry_snapshot_id: existingSnapshotId,
      existing_registry_snapshot_digest: existingSnapshotDigest,
    }).admit(snapshot))
  expectRegistryError('registry_dependency_snapshot_mismatch', () =>
    new P2S1RegistryProposalResolver({
      candidate_id: candidateId,
      design_candidate_digest: designDigest,
      publication_identity: publicationIdentity,
      existing_registry_snapshot_id: 'registry-snapshot-other',
      existing_registry_snapshot_digest: existingSnapshotDigest,
    }).admit(snapshot))
  expectRegistryError('registry_publication_replay_denied', () =>
    resolver({ ...publicationIdentity, publication_id: 'another-publication' }).admit(snapshot))
})

test('TOOL-07..12、OP-05..39 的 W0 执行次数为 0，P2.1 始终拒绝', () => {
  const snapshot = proposal()
  const registry = resolver()
  let executionCount = 0
  for (const unitId of [...P2S1_V1_TOOL_IDS, ...P2S1_V1_OPERATOR_IDS]) {
    expectRegistryError('execution_unit_not_active', () => {
      registry.assertExecutionAuthorized(unitId, snapshot)
      executionCount += 1
    })
  }
  for (const unitId of P2S1_DEFERRED_UNIT_IDS) {
    expectRegistryError('p2_1_deferred_forbidden', () => {
      registry.assertExecutionAuthorized(unitId, {})
      executionCount += 1
    })
  }
  assert.equal(executionCount, 0)
})

test('回执摘要和 proposal 摘要使用同一 deterministic canonical JSON', () => {
  const value = { z: 1, a: ['x', true, null], nested: { b: 2, a: 3 } }
  const expected = `sha256:${createHash('sha256').update(p2S1CanonicalJson(value)).digest('hex')}`
  assert.equal(p2S1Digest(value), expected)
})
