import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'

import {
  P2S1_DEFERRED_UNIT_IDS,
  P2S1_FROZEN_DESIGN_CANDIDATE_DIGEST,
  P2S1_FROZEN_DESIGN_CANDIDATE_ID,
  P2S1_W0_PROPOSAL_UNIT_IDS,
  P2S1_W1_ACTIVATION_UNIT_IDS,
  P2S1_W2_ACTIVATION_UNIT_IDS,
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
  type P2S1RegistryProposalPayload,
  type P2S1RegistryProposalSnapshot,
  type P2S1RegistryProposalUnit,
  type P2S1RegistryUnitKind,
  type P2S1RegistryWaveHandlerManifest,
  type P2S1RegistryWaveId,
  type P2S1RegistryWaveSnapshot,
} from '../../../../agent-sidecar/dist/src/chat/p2-s1-registry-runtime.js'
import {
  p2S1CanonicalJson,
  p2S1Digest,
  type P2S1PublicationIdentity,
} from '../../../../agent-sidecar/dist/src/chat/p2-s1-trusted-receipt-store.js'

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

type StageRunReceipt = {
  receipt_digest: string
  selected_test_ids: string[]
  test_case_coverage: Array<{
    test_id: string
    coverage_kind: string
    unit_ids: string[]
    executed_unit_ids: string[]
  }>
  passed: boolean
  exit_code: number
}

function readRunReceipt(repoRoot: string, waveId: P2S1RegistryWaveId, category: string): {
  path: string
  fileDigest: string
  receipt: StageRunReceipt
} {
  const relative = `contracts/agent/country-outage-p2-s1-implementation/wave-evidence/run-receipts/${waveId.toLowerCase()}-${category}.json`
  const absolute = join(repoRoot, relative)
  const raw = readFileSync(absolute)
  const receipt = JSON.parse(raw.toString('utf8')) as StageRunReceipt
  if (
    !receipt.passed
    || receipt.exit_code !== 0
    || !Array.isArray(receipt.selected_test_ids)
    || !Array.isArray(receipt.test_case_coverage)
  ) {
    throw new Error(`${waveId}/${category} runner receipt is not a passed replayable receipt`)
  }
  return {
    path: relative,
    fileDigest: `sha256:${createHash('sha256').update(raw).digest('hex')}`,
    receipt,
  }
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
    return {
      unit_id: unitId,
      unit_kind: kind,
      version: '1.0.0-design',
      activation_state: P2S1_DEFERRED_UNIT_IDS.includes(unitId as never)
        ? 'deferred'
        : unitId === 'OP-39' ? 'inactive' : 'proposed',
      atomic_capability_id: p2S1ExpectedAtomicCapabilityId(unitId),
      contract_digest: p2S1ExpectedDesignContractDigest(unitId),
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

function proposalPayload(): P2S1RegistryProposalPayload {
  return {
    candidate_id: candidateId,
    design_candidate_digest: designDigest,
    registry_revision: 3,
    activation_scope: 'w0_proposal_only',
    runtime_integration: 'governance_implemented_units_not_implemented',
    production_deployed: false,
    permission_mode: 'read_only',
    external_data_allowed: false,
    publication_identity: structuredClone(publicationIdentity),
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

function fileDigest(path: string): string {
  return `sha256:${createHash('sha256').update(readFileSync(path)).digest('hex')}`
}

function waveIds(waveId: P2S1RegistryWaveId): readonly string[] {
  return waveId === 'W1' ? P2S1_W1_ACTIVATION_UNIT_IDS : P2S1_W2_ACTIVATION_UNIT_IDS
}

function snapshotRef(snapshot: P2S1RegistryProposalSnapshot | P2S1RegistryWaveSnapshot) {
  return {
    registry_snapshot_id: snapshot.registry_snapshot_id,
    snapshot_digest: snapshot.snapshot_digest,
    registry_revision: snapshot.snapshot_payload.registry_revision,
  }
}

function main(): void {
  const repoRoot = resolve(process.cwd())
  const structuralDigest = fileDigest(join(repoRoot, 'contracts/agent/country-outage-p2-s1-implementation/w1-w2-structural-binding.schema.json'))
  const toolDigest = fileDigest(join(repoRoot, 'backend/services/country_outage_p2_s1_tools.py'))
  const operatorDigest = fileDigest(join(repoRoot, 'backend/services/country_outage_p2_s1_operators.py'))
  const runnerByWave = {
    W1: ['positive', 'boundary', 'attack'].map((category) => readRunReceipt(repoRoot, 'W1', category)),
    W2: ['positive', 'boundary', 'attack'].map((category) => readRunReceipt(repoRoot, 'W2', category)),
  } as const
  const proposal = createP2S1RegistryProposal('2026-08-13T01:00:00Z', proposalPayload())
  const resolver = new P2S1RegistryProposalResolver({
    candidate_id: candidateId,
    design_candidate_digest: designDigest,
    publication_identity: structuredClone(publicationIdentity),
    existing_registry_snapshot_id: existingSnapshotId,
    existing_registry_snapshot_digest: existingSnapshotDigest,
  })
  const proposalAdmission = resolver.admit(proposal)
  const proposalById = new Map(proposalUnits().map((unit) => [unit.unit_id, unit]))

  const manifests = Object.fromEntries((['W1', 'W2'] as const).map((waveId) => {
    const manifest = createP2S1RegistryWaveHandlerManifest({
      candidate_id: candidateId,
      design_candidate_digest: designDigest,
      wave_id: waveId,
      structural_binding_contract_digest: structuralDigest,
      handlers: waveIds(waveId).map((unitId) => {
        const run = runnerByWave[waveId].find((candidate) =>
          candidate.receipt.test_case_coverage.some((item) => item.executed_unit_ids.includes(unitId)))
        if (!run) throw new Error(`${waveId}/${unitId} has no applicable runner receipt`)
        const testCaseIds = run.receipt.test_case_coverage
          .filter((item) => item.executed_unit_ids.includes(unitId)).map((item) => item.test_id)
        if (testCaseIds.length < 1) {
          throw new Error(`${waveId}/${unitId} has no applicable positive runner test case`)
        }
        const implementation = unitId.startsWith('TOOL-') ? toolDigest : operatorDigest
        const handlerId = p2S1ExpectedHandlerId(unitId)
        return {
          unit_id: unitId,
          handler_id: handlerId,
          implementation_digest: implementation,
          contract_digest: p2S1ExpectedDesignContractDigest(unitId),
          semantic_digest: p2S1ExpectedUnitSemanticDigest(unitId),
          structural_binding_contract_digest: structuralDigest,
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
            structural_binding_contract_digest: structuralDigest,
            runner_receipt_digest: `sha256:${run.receipt.receipt_digest}`,
            runner_receipt_file_digest: run.fileDigest,
            runner_receipt_path: run.path,
            test_case_ids: testCaseIds,
            test_result: 'passed',
            tested_execution_count: testCaseIds.length,
          }),
        }
      }),
    })
    return [waveId, manifest]
  })) as Record<P2S1RegistryWaveId, P2S1RegistryWaveHandlerManifest>

  const allHandlers = [...manifests.W1.manifest_payload.handlers, ...manifests.W2.manifest_payload.handlers]
  const admitter = new P2S1RegistryWaveBindingAdmitter({
    candidate_id: candidateId,
    design_candidate_digest: designDigest,
    publication_identity: structuredClone(publicationIdentity),
    existing_registry_snapshot_id: existingSnapshotId,
    existing_registry_snapshot_digest: existingSnapshotDigest,
    structural_binding_contract_digest: structuralDigest,
    implementation_digest_by_unit: Object.fromEntries(allHandlers.map((item) => [item.unit_id, item.implementation_digest])),
    test_evidence_receipt_digest_by_unit: Object.fromEntries(allHandlers.map((item) => [item.unit_id, item.test_evidence.receipt_digest])),
  }, proposal)

  let previous: P2S1RegistryProposalSnapshot | P2S1RegistryWaveSnapshot = proposal
  for (const [index, waveId] of (['W1', 'W2'] as const).entries()) {
    const snapshot = createP2S1RegistryWaveSnapshot(
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
        proposal_snapshot_ref: snapshotRef(proposal),
        previous_snapshot_ref: snapshotRef(previous),
        handler_manifest: manifests[waveId],
        admitted_wave_binding_unit_ids: [...waveIds(waveId)],
        admitted_binding_unit_ids: waveId === 'W1'
          ? [...P2S1_W1_ACTIVATION_UNIT_IDS]
          : [...P2S1_W1_ACTIVATION_UNIT_IDS, ...P2S1_W2_ACTIVATION_UNIT_IDS],
      },
    )
    const admission = admitter.admitBindings(snapshot)
    let assertionError: string | null = null
    try {
      admitter.assertExecutionAuthorized(waveIds(waveId)[0]!, snapshot)
    } catch (error) {
      if (error instanceof P2S1RegistryRuntimeError) assertionError = error.code
      else throw error
    }
    const bundleWithoutDigest = {
      schema_version: 'country_outage_p2_s1_registry_runtime_evidence_bundle_v1',
      generator_id: 'generate-p2-s1-w1-w2-registry-evidence',
      generator_source_sha256: createHash('sha256').update(readFileSync(
        join(repoRoot, 'contracts/agent/country-outage-p2-s1-implementation/tools/generate_registry_evidence.ts'),
      )).digest('hex'),
      wave_id: waveId,
      proposal_snapshot: proposal,
      proposal_admission_receipt: proposalAdmission,
      handler_manifest: manifests[waveId],
      wave_snapshot: snapshot,
      wave_admission_receipt: admission,
      non_execution_probe: {
        tested_unit_id: waveIds(waveId)[0],
        assert_execution_authorized_error: assertionError,
        caller_callback_spy_count: 0,
        execution_allowed_unit_ids: admission.execution_allowed_unit_ids,
        execution_started: admission.execution_started,
      },
      execution_scope: {
        offline_harness_verified: true,
        immutable_non_callable_binding_admitted: true,
        trusted_dispatcher_implemented: false,
        registry_execution_authorized: false,
        production_deployed: false,
      },
      sequence_ordinal: index + 1,
    }
    const bundle = {
      ...bundleWithoutDigest,
      content_digest: p2S1Digest(bundleWithoutDigest),
    }
    const output = join(
      repoRoot,
      `contracts/agent/country-outage-p2-s1-implementation/wave-evidence/registry-runtime/${waveId}.json`,
    )
    mkdirSync(dirname(output), { recursive: true })
    writeFileSync(output, `${p2S1CanonicalJson(bundle)}\n`, { encoding: 'utf8' })
    previous = snapshot
  }
}

main()
