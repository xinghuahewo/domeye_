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
  P2S1_W1_ACTIVATION_UNIT_IDS,
  P2S1_W2_ACTIVATION_UNIT_IDS,
  P2S1_V1_OPERATOR_IDS,
  P2S1_V1_TOOL_IDS,
  P2S1_W0_PROPOSAL_UNIT_IDS,
  P2S1RegistryProposalResolver,
  P2S1RegistryRuntimeError,
  P2S1RegistryWaveBindingAdmitter,
  createP2S1RegistryProposal,
  createP2S1RegistryUnitTestEvidence,
  createP2S1RegistryWaveHandlerManifest,
  createP2S1RegistryWaveSnapshot,
  p2S1ExpectedAtomicCapabilityId,
  p2S1ExpectedDesignContractDigest,
  p2S1ExpectedHandlerId,
  p2S1ExpectedUnitSemanticDigest,
  validateP2S1RegistryProposal,
  type P2S1RegistryProposalPayload,
  type P2S1RegistryProposalSnapshot,
  type P2S1RegistryProposalUnit,
  type P2S1RegistryUnitKind,
  type P2S1RegistryWaveHandlerManifest,
  type P2S1RegistryWaveId,
  type P2S1RegistryWaveSnapshot,
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

function fileDigest(path: string): string {
  return `sha256:${createHash('sha256').update(readFileSync(path)).digest('hex')}`
}

const structuralBindingDigest = fileDigest(join(
  process.cwd(), '..', 'contracts', 'agent', 'country-outage-p2-s1-implementation',
  'w1-w2-structural-binding.schema.json',
))
const toolImplementationDigest = fileDigest(join(
  process.cwd(), '..', 'backend', 'services', 'country_outage_p2_s1_tools.py',
))
const operatorImplementationDigest = fileDigest(join(
  process.cwd(), '..', 'backend', 'services', 'country_outage_p2_s1_operators.py',
))
type StageRunReceipt = {
  receipt_digest: string
  test_case_coverage: Array<{
    test_id: string
    coverage_kind: string
    unit_ids: string[]
    executed_unit_ids: string[]
  }>
}

function runnerEvidence(waveId: P2S1RegistryWaveId, unitId: string) {
  const match = ['positive', 'boundary', 'attack'].map((category) => {
    const relative = `contracts/agent/country-outage-p2-s1-implementation/wave-evidence/run-receipts/${waveId.toLowerCase()}-${category}.json`
    const raw = readFileSync(join(process.cwd(), '..', relative))
    const receipt = JSON.parse(raw.toString('utf8')) as StageRunReceipt
    return { relative, raw, receipt }
  }).find((candidate) => candidate.receipt.test_case_coverage.some((item) => item.executed_unit_ids.includes(unitId)))
  assert.ok(match)
  const { relative, raw, receipt } = match
  const testCaseIds = receipt.test_case_coverage
    .filter((item) => item.executed_unit_ids.includes(unitId))
    .map((item) => item.test_id)
  assert.ok(testCaseIds.length > 0)
  return {
    runner_receipt_digest: `sha256:${receipt.receipt_digest}`,
    runner_receipt_file_digest: `sha256:${createHash('sha256').update(raw).digest('hex')}`,
    runner_receipt_path: relative,
    test_case_ids: testCaseIds,
    tested_execution_count: testCaseIds.length,
  }
}

function implementationDigest(unitId: string): string {
  return unitId.startsWith('TOOL-') ? toolImplementationDigest : operatorImplementationDigest
}

function waveIds(waveId: P2S1RegistryWaveId): readonly string[] {
  return waveId === 'W1' ? P2S1_W1_ACTIVATION_UNIT_IDS : P2S1_W2_ACTIVATION_UNIT_IDS
}

function waveManifest(waveId: P2S1RegistryWaveId): P2S1RegistryWaveHandlerManifest {
  const proposalById = new Map(proposalUnits().map((unit) => [unit.unit_id, unit]))
  return createP2S1RegistryWaveHandlerManifest({
    candidate_id: candidateId,
    design_candidate_digest: designDigest,
    wave_id: waveId,
    structural_binding_contract_digest: structuralBindingDigest,
    handlers: waveIds(waveId).map((unitId) => {
      const runner = runnerEvidence(waveId, unitId)
      const implementation = implementationDigest(unitId)
      const handlerId = p2S1ExpectedHandlerId(unitId)
      return {
        unit_id: unitId,
        handler_id: handlerId,
        implementation_digest: implementation,
        contract_digest: p2S1ExpectedDesignContractDigest(unitId),
        semantic_digest: p2S1ExpectedUnitSemanticDigest(unitId),
        structural_binding_contract_digest: structuralBindingDigest,
        dependencies: structuredClone(proposalById.get(unitId)!.dependencies),
        test_evidence: createP2S1RegistryUnitTestEvidence({
          candidate_id: candidateId,
          design_candidate_digest: designDigest,
          wave_id: waveId,
          unit_id: unitId,
          handler_id: handlerId,
          implementation_digest: implementation,
          contract_digest: p2S1ExpectedDesignContractDigest(unitId),
          semantic_digest: p2S1ExpectedUnitSemanticDigest(unitId),
          structural_binding_contract_digest: structuralBindingDigest,
          ...runner,
          test_result: 'passed',
        }),
      }
    }),
  })
}

const w1Manifest = waveManifest('W1')
const w2Manifest = waveManifest('W2')

function activationContext() {
  const allHandlers = [
    ...w1Manifest.manifest_payload.handlers,
    ...w2Manifest.manifest_payload.handlers,
  ]
  return {
    candidate_id: candidateId,
    design_candidate_digest: designDigest,
    publication_identity: structuredClone(publicationIdentity),
    existing_registry_snapshot_id: existingSnapshotId,
    existing_registry_snapshot_digest: existingSnapshotDigest,
    structural_binding_contract_digest: structuralBindingDigest,
    implementation_digest_by_unit: Object.fromEntries(
      allHandlers.map((handler) => [handler.unit_id, handler.implementation_digest]),
    ),
    test_evidence_receipt_digest_by_unit: Object.fromEntries(
      allHandlers.map((handler) => [handler.unit_id, handler.test_evidence.receipt_digest]),
    ),
  }
}

function refOf(snapshot: P2S1RegistryProposalSnapshot | P2S1RegistryWaveSnapshot) {
  return {
    registry_snapshot_id: snapshot.registry_snapshot_id,
    snapshot_digest: snapshot.snapshot_digest,
    registry_revision: snapshot.snapshot_payload.registry_revision,
  }
}

function waveSnapshot(
  waveId: P2S1RegistryWaveId,
  previous: P2S1RegistryProposalSnapshot | P2S1RegistryWaveSnapshot,
  proposalSnapshot: P2S1RegistryProposalSnapshot,
  manifest = waveId === 'W1' ? w1Manifest : w2Manifest,
): P2S1RegistryWaveSnapshot {
  return createP2S1RegistryWaveSnapshot(
    waveId === 'W1' ? '2026-08-13T02:00:00Z' : '2026-08-13T03:00:00Z',
    {
      candidate_id: candidateId,
      design_candidate_digest: designDigest,
      registry_revision: previous.snapshot_payload.registry_revision + 1,
      wave_id: waveId,
      activation_scope: 'complete_atomic_wave_binding_admission',
      permission_mode: 'read_only',
      external_data_allowed: false,
      production_deployed: false,
      publication_identity: structuredClone(publicationIdentity),
      proposal_snapshot_ref: refOf(proposalSnapshot),
      previous_snapshot_ref: refOf(previous),
      handler_manifest: manifest,
      admitted_wave_binding_unit_ids: [...waveIds(waveId)],
      admitted_binding_unit_ids: waveId === 'W1'
        ? [...P2S1_W1_ACTIVATION_UNIT_IDS]
        : [...P2S1_W1_ACTIVATION_UNIT_IDS, ...P2S1_W2_ACTIVATION_UNIT_IDS],
    },
  )
}

function resealManifest(value: P2S1RegistryWaveHandlerManifest): P2S1RegistryWaveHandlerManifest {
  const mutable = structuredClone(value)
  const digest = p2S1Digest(mutable.manifest_payload)
  mutable.handler_manifest_digest = digest
  mutable.handler_manifest_id = `p2-s1-handler-manifest-sha256:${digest.slice('sha256:'.length)}`
  return mutable
}

function resealTestEvidence(value: Record<string, unknown>): void {
  const copy = structuredClone(value)
  delete copy.receipt_digest
  value.receipt_digest = p2S1Digest(copy)
}

function resealWave(value: P2S1RegistryWaveSnapshot): P2S1RegistryWaveSnapshot {
  const mutable = structuredClone(value)
  const digest = p2S1Digest(mutable.snapshot_payload)
  mutable.snapshot_digest = digest
  mutable.registry_snapshot_id = `p2-s1-registry-wave-sha256:${digest.slice('sha256:'.length)}`
  return mutable
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

test('W1/W2 Registry 以完整波次、同候选实现与测试证据顺序原子准入 binding，执行授权为空', () => {
  const proposalSnapshot = proposal()
  const admitter = new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot)
  assert.deepEqual(admitter.currentSnapshotRef(), refOf(proposalSnapshot))

  const w1 = waveSnapshot('W1', proposalSnapshot, proposalSnapshot)
  const w1Admission = admitter.admitBindings(w1)
  assert.equal(w1Admission.status, 'admitted_complete_atomic_wave_bindings')
  assert.equal(w1Admission.wave_id, 'W1')
  assert.equal(w1Admission.registry_revision, 4)
  assert.deepEqual(w1Admission.admitted_wave_binding_unit_ids, [...P2S1_W1_ACTIVATION_UNIT_IDS])
  assert.deepEqual(w1Admission.admitted_binding_unit_ids, [...P2S1_W1_ACTIVATION_UNIT_IDS])
  assert.deepEqual(w1Admission.execution_allowed_unit_ids, [])
  assert.equal(w1Admission.partial_binding_admission, false)
  assert.equal(w1Admission.execution_started, false)
  assert.equal(w1Admission.production_deployed, false)
  const w1AdmissionInput = structuredClone(w1Admission) as unknown as Record<string, unknown>
  delete w1AdmissionInput.receipt_digest
  assert.equal(w1Admission.receipt_digest, p2S1Digest(w1AdmissionInput))

  assert.equal(admitter.resolveAdmittedBinding('TOOL-07', w1).handler_id, p2S1ExpectedHandlerId('TOOL-07'))
  expectRegistryError('registry_binding_not_admitted', () => admitter.resolveAdmittedBinding('TOOL-12', w1))
  let executionCount = 0
  expectRegistryError('registry_dispatch_not_bound', () => {
    admitter.assertExecutionAuthorized('TOOL-07', w1)
    executionCount += 1
  })
  assert.equal(executionCount, 0)

  const w2 = waveSnapshot('W2', w1, proposalSnapshot)
  const w2Admission = admitter.admitBindings(w2)
  assert.equal(w2Admission.wave_id, 'W2')
  assert.equal(w2Admission.registry_revision, 5)
  assert.deepEqual(w2Admission.admitted_wave_binding_unit_ids, [...P2S1_W2_ACTIVATION_UNIT_IDS])
  assert.deepEqual(w2Admission.admitted_binding_unit_ids, [
    ...P2S1_W1_ACTIVATION_UNIT_IDS,
    ...P2S1_W2_ACTIVATION_UNIT_IDS,
  ])
  assert.deepEqual(w2Admission.execution_allowed_unit_ids, [])
  assert.equal(admitter.resolveAdmittedBinding('OP-05', w2).handler_id, p2S1ExpectedHandlerId('OP-05'))
  assert.equal(admitter.resolveAdmittedBinding('OP-28', w2).handler_id, p2S1ExpectedHandlerId('OP-28'))
  assert.equal(admitter.currentSnapshotRef().registry_revision, 5)
})

test('W1 binding 准入拒绝 digest、候选、revision、合同、实现、handler 与测试证据攻击，失败不改变 CAS', () => {
  const proposalSnapshot = proposal()

  const digestTampered = structuredClone(waveSnapshot('W1', proposalSnapshot, proposalSnapshot))
  digestTampered.snapshot_digest = p2S1Digest({ forged: 'snapshot-digest' })
  expectRegistryError('registry_wave_snapshot_digest_mismatch', () =>
    new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot).admitBindings(digestTampered))

  const candidateTampered = structuredClone(waveSnapshot('W1', proposalSnapshot, proposalSnapshot))
  candidateTampered.snapshot_payload.candidate_id = `${candidateId}-other`
  expectRegistryError('registry_candidate_binding_mismatch', () =>
    new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot).admitBindings(resealWave(candidateTampered)))

  const revisionTampered = structuredClone(waveSnapshot('W1', proposalSnapshot, proposalSnapshot))
  revisionTampered.snapshot_payload.registry_revision = 5
  expectRegistryError('registry_revision_chain_invalid', () =>
    new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot).admitBindings(resealWave(revisionTampered)))

  const contractManifest = structuredClone(w1Manifest)
  const contractHandler = contractManifest.manifest_payload.handlers[0]!
  contractHandler.contract_digest = p2S1Digest({ forged: 'contract' })
  contractHandler.test_evidence.contract_digest = contractHandler.contract_digest
  resealTestEvidence(contractHandler.test_evidence as unknown as Record<string, unknown>)
  const contractSnapshot = waveSnapshot('W1', proposalSnapshot, proposalSnapshot, resealManifest(contractManifest))
  expectRegistryError('registry_unit_contract_drift', () =>
    new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot).admitBindings(contractSnapshot))

  const implementationManifest = structuredClone(w1Manifest)
  const implementationHandler = implementationManifest.manifest_payload.handlers[0]!
  implementationHandler.implementation_digest = p2S1Digest({ forged: 'implementation' })
  implementationHandler.test_evidence.implementation_digest = implementationHandler.implementation_digest
  resealTestEvidence(implementationHandler.test_evidence as unknown as Record<string, unknown>)
  const implementationSnapshot = waveSnapshot('W1', proposalSnapshot, proposalSnapshot, resealManifest(implementationManifest))
  expectRegistryError('registry_implementation_digest_mismatch', () =>
    new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot).admitBindings(implementationSnapshot))

  const handlerManifest = structuredClone(w1Manifest)
  const handler = handlerManifest.manifest_payload.handlers[0]!
  handler.handler_id = 'python:forged.handler'
  handler.test_evidence.handler_id = handler.handler_id
  resealTestEvidence(handler.test_evidence as unknown as Record<string, unknown>)
  const handlerSnapshot = waveSnapshot('W1', proposalSnapshot, proposalSnapshot, resealManifest(handlerManifest))
  expectRegistryError('registry_handler_binding_mismatch', () =>
    new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot).admitBindings(handlerSnapshot))

  const testManifest = structuredClone(w1Manifest)
  const tested = testManifest.manifest_payload.handlers[0]!.test_evidence
  tested.runner_receipt_file_digest = p2S1Digest({ forged: 'test-artifact' })
  resealTestEvidence(tested as unknown as Record<string, unknown>)
  const testSnapshot = waveSnapshot('W1', proposalSnapshot, proposalSnapshot, resealManifest(testManifest))
  const admitter = new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot)
  expectRegistryError('registry_test_evidence_binding_mismatch', () => admitter.admitBindings(testSnapshot))
  assert.deepEqual(admitter.currentSnapshotRef(), refOf(proposalSnapshot))
})

test('Registry binding 准入拒绝半波、依赖、重复、跨波、过期 CAS 与重复准入；执行 spy 为 0', () => {
  const proposalSnapshot = proposal()

  const partialManifest = structuredClone(w1Manifest)
  partialManifest.manifest_payload.handlers.pop()
  expectRegistryError('registry_partial_wave_forbidden', () =>
    new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot).admitBindings(
      waveSnapshot('W1', proposalSnapshot, proposalSnapshot, resealManifest(partialManifest)),
    ))

  const dependencyManifest = structuredClone(w1Manifest)
  dependencyManifest.manifest_payload.handlers[0]!.dependencies[0]!.contract_digest = p2S1Digest({ forged: 'dependency' })
  expectRegistryError('registry_dependency_invalid', () =>
    new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot).admitBindings(
      waveSnapshot('W1', proposalSnapshot, proposalSnapshot, resealManifest(dependencyManifest)),
    ))

  const duplicateManifest = structuredClone(w1Manifest)
  duplicateManifest.manifest_payload.handlers[1] = structuredClone(duplicateManifest.manifest_payload.handlers[0]!)
  expectRegistryError('registry_partial_wave_forbidden', () =>
    new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot).admitBindings(
      waveSnapshot('W1', proposalSnapshot, proposalSnapshot, resealManifest(duplicateManifest)),
    ))

  const crossWaveManifest = structuredClone(w1Manifest)
  const crossUnitId = 'TOOL-12'
  const crossRunner = runnerEvidence('W2', crossUnitId)
  const crossImplementation = implementationDigest(crossUnitId)
  crossWaveManifest.manifest_payload.handlers[0] = {
    unit_id: crossUnitId,
    handler_id: p2S1ExpectedHandlerId(crossUnitId),
    implementation_digest: crossImplementation,
    contract_digest: p2S1ExpectedDesignContractDigest(crossUnitId),
    semantic_digest: p2S1ExpectedUnitSemanticDigest(crossUnitId),
    structural_binding_contract_digest: structuralBindingDigest,
    dependencies: [],
    test_evidence: createP2S1RegistryUnitTestEvidence({
      candidate_id: candidateId,
      design_candidate_digest: designDigest,
      wave_id: 'W1',
      unit_id: crossUnitId,
      handler_id: p2S1ExpectedHandlerId(crossUnitId),
      implementation_digest: crossImplementation,
      contract_digest: p2S1ExpectedDesignContractDigest(crossUnitId),
      semantic_digest: p2S1ExpectedUnitSemanticDigest(crossUnitId),
      structural_binding_contract_digest: structuralBindingDigest,
      ...crossRunner,
      test_result: 'passed',
    }),
  }
  expectRegistryError('registry_partial_wave_forbidden', () =>
    new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot).admitBindings(
      waveSnapshot('W1', proposalSnapshot, proposalSnapshot, resealManifest(crossWaveManifest)),
    ))

  const sequenceAdmitter = new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot)
  expectRegistryError('registry_wave_sequence_invalid', () =>
    sequenceAdmitter.admitBindings(waveSnapshot('W2', proposalSnapshot, proposalSnapshot)))
  const w1 = waveSnapshot('W1', proposalSnapshot, proposalSnapshot)
  sequenceAdmitter.admitBindings(w1)
  expectRegistryError('registry_wave_sequence_invalid', () => sequenceAdmitter.admitBindings(w1))

  const staleW2 = waveSnapshot('W2', proposalSnapshot, proposalSnapshot)
  expectRegistryError('registry_cas_mismatch', () => sequenceAdmitter.admitBindings(staleW2))

  let unauthorizedExecutionCount = 0
  const fresh = new P2S1RegistryWaveBindingAdmitter(activationContext(), proposalSnapshot)
  const validW1 = waveSnapshot('W1', proposalSnapshot, proposalSnapshot)
  expectRegistryError('registry_dispatch_not_bound', () => {
    fresh.assertExecutionAuthorized('TOOL-07', validW1)
    unauthorizedExecutionCount += 1
  })
  expectRegistryError('registry_dispatch_not_bound', () => {
    fresh.assertExecutionAuthorized('TOOL-13', {})
    unauthorizedExecutionCount += 1
  })
  expectRegistryError('registry_dispatch_not_bound', () => {
    fresh.assertExecutionAuthorized('TOOL-11', {})
    unauthorizedExecutionCount += 1
  })
  assert.equal(unauthorizedExecutionCount, 0)
})

test('回执摘要和 proposal 摘要使用同一 deterministic canonical JSON', () => {
  const value = { z: 1, a: ['x', true, null], nested: { b: 2, a: 3 } }
  const expected = `sha256:${createHash('sha256').update(p2S1CanonicalJson(value)).digest('hex')}`
  assert.equal(p2S1Digest(value), expected)
})
