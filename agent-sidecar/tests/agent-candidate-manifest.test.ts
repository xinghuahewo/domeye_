import assert from 'node:assert/strict'
import {
  createHash,
  generateKeyPairSync,
} from 'node:crypto'
import {
  mkdirSync,
  mkdtempSync,
  rmSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import test, { after } from 'node:test'

import {
  DomeyeCandidateManifestError,
  domeyeFirstSliceCandidateId,
  loadDomeyeFirstSliceCandidateManifest,
  type DomeyeFirstSliceCandidateManifest,
  type DomeyeFirstSliceCandidateManifestPayload,
} from '../src/agent/candidate-manifest.js'
import type { DomeyeDataIdentity } from '../src/agent/contracts.js'
import { canonicalJsonSha256 } from '../src/shared/deterministic-json.js'

const roots: string[] = []
after(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true })
})

const ANCHOR_PATH =
  'docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md'
const ANSWER_PRESENTATION_PATH =
  'docs/architecture/Domeye_First_Vertical_Slice_Answer_Presentation_Addendum_v1.0.md'
const MODEL_PATH =
  'contracts/agent/domeye-first-vertical-slice/v1/model-runtime.json'
const CONTRACTS_PATH = 'agent-sidecar/src/agent/contracts.ts'
const IMPLEMENTATION_PATH =
  'agent-sidecar/src/agent/capability-execution.ts'
const TRANSITIVE_SOURCE_PATH =
  'agent-sidecar/src/runtime/critical-transitive.ts'
const TRANSITIVE_DIST_PATH =
  'agent-sidecar/dist/src/runtime/critical-transitive.js'
const PATCHED_PROVIDER_PATH =
  'agent-sidecar/node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js'
const EXECUTION_PUBLIC_KEY_PATH =
  'contracts/agent/domeye-first-vertical-slice/v1.1/attestors/execution-public-key.json'
const REVIEWER_PUBLIC_KEY_PATH =
  'contracts/agent/domeye-first-vertical-slice/v1.1/attestors/reviewer-public-key.json'
const EVALUATOR_IMPLEMENTATION_PATHS = [
  'evaluation/country-outage/first-vertical-slice/evaluator.mjs',
  'evaluation/country-outage/first-vertical-slice/adversarial-driver.mjs',
  'evaluation/country-outage/first-vertical-slice/case-registry.mjs',
  'evaluation/country-outage/first-vertical-slice/source-loader.mjs',
  'evaluation/country-outage/first-vertical-slice/run.mjs',
] as const

const RUNTIME_SOURCE_PATHS = [
  'agent-sidecar/src/cli/serve-interactive-agent.ts',
  CONTRACTS_PATH,
  IMPLEMENTATION_PATH,
  'agent-sidecar/src/formal-runtime-limits.ts',
  'agent-sidecar/src/pi/country-outage-skill-bundle.ts',
  'agent-sidecar/src/pi/formal-model-runtime.ts',
  'agent-sidecar/src/cli/sidecar-security.ts',
  TRANSITIVE_SOURCE_PATH,
] as const

const FIXED_SOURCE_PATHS = [
  ANCHOR_PATH,
  ANSWER_PRESENTATION_PATH,
  MODEL_PATH,
  'agent-sidecar/package.json',
  'agent-sidecar/package-lock.json',
  'agent-sidecar/tsconfig.json',
  'agent-sidecar/scripts/generate_first_slice_candidate_manifest.mjs',
  'agent-sidecar/scripts/apply_pi_response_model_patch.mjs',
  'agent-sidecar/resources/vendor-patches/pi-ai-openai-completions-response-model-v1.json',
  'agent-sidecar/vendor-patches/pi-ai-0.84.1-openai-completions-response-model-v1.patch',
  EXECUTION_PUBLIC_KEY_PATH,
  REVIEWER_PUBLIC_KEY_PATH,
  ...EVALUATOR_IMPLEMENTATION_PATHS,
  PATCHED_PROVIDER_PATH,
] as const

const IDENTITY: DomeyeDataIdentity = {
  event_type: 'country_outage',
  incident_id: 'incident_go_v1_a1de26f854831330c616a72af21597eb',
  publication_id:
    'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f',
  revision: 1,
  collector_id: 'rrc25',
  cohort_id: 'country_event_cohort_v1_1e04abfc6430776bef20403fac528698',
  country_code: 'IR',
  window_start_utc: '2026-02-27T00:10:00Z',
  window_end_utc: '2026-03-11T00:00:00Z',
  data_through: '2026-03-11T00:00:00Z',
  is_final_in_data_range: false,
  lifecycle_state: 'event_end_unknown',
}

function shaText(text: string): `sha256:${string}` {
  return `sha256:${createHash('sha256').update(text, 'utf8').digest('hex')}`
}

function sha(character: string): `sha256:${string}` {
  return `sha256:${character.repeat(64)}`
}

function attestorPublicKey(role: 'execution_evidence' | 'independent_review') {
  const { publicKey } = generateKeyPairSync('ed25519')
  const spki = publicKey.export({ format: 'der', type: 'spki' })
  return {
    schema_version: 'domeye_first_slice_attestor_public_key_v1' as const,
    role,
    algorithm: 'ed25519' as const,
    key_id: `ed25519-spki-sha256:${createHash('sha256')
      .update(spki).digest('hex')}`,
    public_key_spki_der_base64: spki.toString('base64'),
  }
}

function runtimeDistPath(path: string): string {
  return `agent-sidecar/dist/src/${path.slice(
    'agent-sidecar/src/'.length,
    -'.ts'.length,
  )}.js`
}

function writeProjectFile(root: string, path: string, content: string): string {
  const absolute = join(root, path)
  mkdirSync(dirname(absolute), { recursive: true })
  writeFileSync(absolute, content)
  return absolute
}

function fixedPolicy(): DomeyeFirstSliceCandidateManifestPayload['policy'] {
  const hash = canonicalJsonSha256({
    tenant_id: 'domeye',
    required_scope: 'country_outage:read',
    allowed_capability_ids: ['CAP-006', 'CAP-016'],
    model_api_attempt_limit: 10,
    approved_action_limit: 2,
    cost_policy: 'audit_only',
    monetary_limit_usd: null,
  })
  return {
    policy_id: `policy-sha256:${hash}`,
    policy_digest: `sha256:${hash}`,
    state: 'active',
    allowed_capability_ids: ['CAP-006', 'CAP-016'],
  }
}

function fixedCapabilities(
  contractDigest: `sha256:${string}`,
  implementationDigest: `sha256:${string}`,
): DomeyeFirstSliceCandidateManifestPayload['registry']['capabilities'] {
  return [
    {
      capability_id: 'CAP-006',
      state: 'active',
      execution_binding: {
        execution_unit_id: 'TOOL-03',
        execution_unit_name: 'read_metric_series',
        execution_unit_version: '1.0.0',
        contract_digest: contractDigest,
        implementation_digest: implementationDigest,
        semantic_digest: `sha256:${canonicalJsonSha256({
          metric: 'fixed_visible_ipv4_address_count',
          operation: 'read_metric_series',
          output: 'immutable_metric_series_artifact',
        })}`,
      },
    },
    {
      capability_id: 'CAP-016',
      state: 'active',
      execution_binding: {
        execution_unit_id: 'OP-01',
        execution_unit_name: 'series_extrema',
        execution_unit_version: '1.0.0',
        contract_digest: contractDigest,
        implementation_digest: implementationDigest,
        semantic_digest: `sha256:${canonicalJsonSha256({
          input: 'qualified_metric_series_artifact_ref',
          operation: 'series_extrema',
          tie_policy: 'first_observed_occurrence',
          null_policy: 'exclude_null_never_zero_fill',
          empty_policy: 'empty_observed_set',
        })}`,
      },
    },
  ]
}

function fixedRegistry(
  capabilities: ReturnType<typeof fixedCapabilities>,
): DomeyeFirstSliceCandidateManifestPayload['registry'] {
  const hash = canonicalJsonSha256({ capabilities })
  return {
    registry_snapshot_id: `registry-snapshot-sha256:${hash}`,
    registry_digest: `sha256:${hash}`,
    state: 'active',
    capabilities,
  }
}

interface Fixture {
  readonly root: string
  readonly payload: DomeyeFirstSliceCandidateManifestPayload
  readonly manifest: DomeyeFirstSliceCandidateManifest
  readonly contractsPath: string
  readonly transitiveSourcePath: string
  readonly transitiveDistPath: string
}

function createFixture(): Fixture {
  const root = mkdtempSync(join(tmpdir(), 'domeye-candidate-manifest-test-'))
  roots.push(root)
  const contents = new Map<string, string>()
  for (const path of FIXED_SOURCE_PATHS) {
    contents.set(path, `fixed:${path}\n`)
  }
  for (const path of RUNTIME_SOURCE_PATHS) {
    contents.set(path, `export const source = ${JSON.stringify(path)}\n`)
    contents.set(
      runtimeDistPath(path),
      `export const built = ${JSON.stringify(path)};\n`,
    )
  }
  contents.set(
    'agent-sidecar/src/cli/serve-interactive-agent.ts',
    `import '../runtime/critical-transitive.js'\nexport const entry = true\n`,
  )
  contents.set(
    'agent-sidecar/dist/src/cli/serve-interactive-agent.js',
    `import '../runtime/critical-transitive.js';\nexport const entry = true;\n`,
  )
  contents.set(ANCHOR_PATH, 'first vertical slice anchor\n')
  contents.set(ANSWER_PRESENTATION_PATH, 'answer presentation addendum\n')
  contents.set(MODEL_PATH, '{"model":"model-first-slice"}\n')
  const executionKey = attestorPublicKey('execution_evidence')
  const reviewerKey = attestorPublicKey('independent_review')
  contents.set(
    EXECUTION_PUBLIC_KEY_PATH,
    `${JSON.stringify(executionKey, null, 2)}\n`,
  )
  contents.set(
    REVIEWER_PUBLIC_KEY_PATH,
    `${JSON.stringify(reviewerKey, null, 2)}\n`,
  )

  const sourceFiles = [...contents.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([path, content]) => {
      writeProjectFile(root, path, content)
      return { path, sha256: shaText(content) }
    })
  const sourceDigest = new Map(
    sourceFiles.map((source) => [source.path, source.sha256]),
  )
  const contractDigest = sourceDigest.get(CONTRACTS_PATH)!
  const implementationDigest = sourceDigest.get(IMPLEMENTATION_PATH)!
  const capabilities = fixedCapabilities(contractDigest, implementationDigest)
  const payload: DomeyeFirstSliceCandidateManifestPayload = {
    schema_version: 'domeye_first_slice_candidate_manifest_v2',
    base_commit: 'a'.repeat(40),
    contract: {
      version: 'domeye.first-vertical-slice/v1.0',
      digest: sourceDigest.get(ANCHOR_PATH)!,
    },
    answer_presentation_contract: {
      version: 'domeye.first-vertical-slice.answer-presentation/v1.0',
      digest: sourceDigest.get(ANSWER_PRESENTATION_PATH)!,
    },
    data_identity: IDENTITY,
    series_response_sha256: sha('2'),
    model: {
      candidate_id: 'model-candidate-first-slice',
      resource_sha256: sourceDigest.get(MODEL_PATH)!,
      provider: 'provider-first-slice',
      model: 'model-first-slice',
      model_version: 'model-first-slice-20260819',
      expected_response_model: 'model-first-slice',
      api: 'openai-completions',
      base_url: 'https://provider.invalid/v1',
      maximum_output_tokens: 4_096,
      thinking_level: 'off',
      pi_version: '0.84.1',
    },
    budget_policy: {
      model_api_attempt_limit: 10,
      approved_action_limit: 2,
      cost_policy: 'audit_only',
      monetary_limit_usd: null,
    },
    policy: fixedPolicy(),
    registry: fixedRegistry(capabilities),
    attestation_policy: {
      schema_version: 'domeye_first_slice_attestation_policy_v1',
      algorithm: 'ed25519',
      canonicalization: 'domeye_unicode_codepoint_canonical_json_v1',
      signature_domains: {
        execution_evidence:
          'domeye.first-slice.evaluation-attestation/execution/v1',
        independent_review:
          'domeye.first-slice.evaluation-attestation/independent-review/v1',
      },
      release_eligible: true,
      execution_evidence: {
        role: executionKey.role,
        actor_id: 'domeye-first-slice-real-runtime-attestor-v1',
        key_id: executionKey.key_id,
        public_key_spki_der_base64: executionKey.public_key_spki_der_base64,
      },
      independent_review: {
        role: reviewerKey.role,
        actor_id: 'domeye-first-slice-independent-reviewer-v1',
        key_id: reviewerKey.key_id,
        public_key_spki_der_base64: reviewerKey.public_key_spki_der_base64,
      },
    },
    source_files: sourceFiles,
    activation: {
      scope: 'local_evaluation_only',
      production_deployed: false,
    },
  }
  const manifest = {
    candidate_id: domeyeFirstSliceCandidateId(payload),
    payload,
  }
  writeManifest(root, manifest)
  return {
    root,
    payload,
    manifest,
    contractsPath: join(root, CONTRACTS_PATH),
    transitiveSourcePath: join(root, TRANSITIVE_SOURCE_PATH),
    transitiveDistPath: join(root, TRANSITIVE_DIST_PATH),
  }
}

function writeManifest(root: string, manifest: unknown): void {
  writeFileSync(join(root, 'candidate.json'), JSON.stringify(manifest, null, 2))
}

function envelope(payload: unknown): Record<string, unknown> {
  return {
    candidate_id: `manifest:sha256:${canonicalJsonSha256(payload)}`,
    payload,
  }
}

async function load(root: string) {
  return await loadDomeyeFirstSliceCandidateManifest({
    project_root: root,
    manifest_path: 'candidate.json',
  })
}

function hasCode(code: DomeyeCandidateManifestError['code']) {
  return (error: unknown): boolean =>
    error instanceof DomeyeCandidateManifestError && error.code === code
}

test('加载器返回冻结且来源闭包完整的 Candidate binding', async () => {
  const fixture = createFixture()
  const loaded = await load(fixture.root)
  const sourceDigest = new Map(
    fixture.payload.source_files.map((source) => [source.path, source.sha256]),
  )

  assert.equal(
    loaded.manifest.candidate_id,
    `manifest:sha256:${canonicalJsonSha256(fixture.payload)}`,
  )
  assert.equal(loaded.candidate.candidate_id, loaded.manifest.candidate_id)
  assert.equal(
    loaded.candidate.contract_version,
    'domeye.first-vertical-slice/v1.0',
  )
  assert.equal(
    loaded.candidate.answer_presentation_contract_version,
    'domeye.first-vertical-slice.answer-presentation/v1.0',
  )
  assert.equal(
    loaded.candidate.answer_presentation_contract_digest,
    sourceDigest.get(ANSWER_PRESENTATION_PATH),
  )
  assert.deepEqual(loaded.candidate.data_identity, IDENTITY)
  assert.equal(
    loaded.candidate.registry.capabilities[0]?.execution_binding
      .contract_digest,
    sourceDigest.get(CONTRACTS_PATH),
  )
  assert.equal(
    loaded.candidate.registry.capabilities[1]?.execution_binding
      .implementation_digest,
    sourceDigest.get(IMPLEMENTATION_PATH),
  )
  assert.deepEqual(loaded.model_identity, fixture.payload.model)
  assert.equal(loaded.manifest.payload.budget_policy.model_api_attempt_limit, 10)
  assert.equal(loaded.manifest.payload.budget_policy.approved_action_limit, 2)
  assert.equal(loaded.manifest.payload.budget_policy.cost_policy, 'audit_only')
  assert.equal(loaded.manifest.payload.budget_policy.monetary_limit_usd, null)
  assert.equal(loaded.manifest.payload.activation.scope, 'local_evaluation_only')
  assert.equal(loaded.manifest.payload.activation.production_deployed, false)
  assert.equal(
    loaded.manifest.payload.attestation_policy!.release_eligible,
    true,
  )
  assert.notEqual(
    loaded.manifest.payload.attestation_policy!.execution_evidence.key_id,
    loaded.manifest.payload.attestation_policy!.independent_review.key_id,
  )
  assert.equal(Object.isFrozen(loaded), true)
  assert.equal(Object.isFrozen(loaded.candidate.registry.capabilities), true)
})

test('attestation policy 必须精确绑定两个不同的 Ed25519 SPKI 公钥', async () => {
  const unknown = createFixture()
  const unknownPayload = {
    ...unknown.payload,
    attestation_policy: {
      ...unknown.payload.attestation_policy,
      caller_may_sign_arbitrary_json: true,
    },
  }
  writeManifest(unknown.root, envelope(unknownPayload))
  await assert.rejects(
    () => load(unknown.root),
    hasCode('manifest_schema_invalid'),
  )

  const wrongId = createFixture()
  const wrongIdPayload = structuredClone(wrongId.payload)
  wrongIdPayload.attestation_policy!.execution_evidence.key_id =
    `ed25519-spki-sha256:${'0'.repeat(64)}`
  writeManifest(wrongId.root, envelope(wrongIdPayload))
  await assert.rejects(
    () => load(wrongId.root),
    hasCode('attestation_policy_invalid'),
  )

  const sameKey = createFixture()
  const sameKeyPayload = structuredClone(sameKey.payload)
  sameKeyPayload.attestation_policy!.independent_review = {
    ...sameKeyPayload.attestation_policy!.execution_evidence,
    role: 'independent_review',
    actor_id: 'domeye-first-slice-independent-reviewer-v1',
  }
  writeManifest(sameKey.root, envelope(sameKeyPayload))
  await assert.rejects(
    () => load(sameKey.root),
    hasCode('attestation_policy_invalid'),
  )

  const mismatchedFile = createFixture()
  const replacement = attestorPublicKey('execution_evidence')
  const replacementText = `${JSON.stringify(replacement, null, 2)}\n`
  writeProjectFile(
    mismatchedFile.root,
    EXECUTION_PUBLIC_KEY_PATH,
    replacementText,
  )
  const mismatchedPayload = structuredClone(mismatchedFile.payload)
  mismatchedPayload.source_files.find(
    (source) => source.path === EXECUTION_PUBLIC_KEY_PATH,
  )!.sha256 = shaText(replacementText)
  writeManifest(mismatchedFile.root, envelope(mismatchedPayload))
  await assert.rejects(
    () => load(mismatchedFile.root),
    hasCode('attestation_policy_invalid'),
  )
})

test('Candidate ID 必须等于 canonical payload 摘要且 payload 不接受额外字段', async () => {
  const mismatch = createFixture()
  writeManifest(mismatch.root, {
    ...mismatch.manifest,
    candidate_id: `manifest:sha256:${'0'.repeat(64)}`,
  })
  await assert.rejects(() => load(mismatch.root), hasCode('candidate_id_mismatch'))

  const extra = createFixture()
  const payload = { ...extra.payload, runtime_selector: 'not-allowed' }
  writeManifest(extra.root, envelope(payload))
  await assert.rejects(() => load(extra.root), hasCode('manifest_schema_invalid'))
})

test('旧 Anchor 与新回答呈现附加合同必须分别绑定固定路径摘要', async () => {
  const fixture = createFixture()
  const payload = structuredClone(fixture.payload)
  payload.answer_presentation_contract.digest = sha('9')
  writeManifest(fixture.root, envelope(payload))
  await assert.rejects(
    () => load(fixture.root),
    hasCode('source_binding_invalid'),
  )
})

test('预算、模型与激活合同失败关闭', async () => {
  const budget = createFixture()
  const budgetPayload = {
    ...budget.payload,
    budget_policy: {
      ...budget.payload.budget_policy,
      model_api_attempt_limit: 9,
    },
  }
  writeManifest(budget.root, envelope(budgetPayload))
  await assert.rejects(() => load(budget.root), hasCode('manifest_schema_invalid'))

  const model = createFixture()
  const modelPayload = structuredClone(model.payload)
  modelPayload.model.base_url = 'file:///private/model'
  writeManifest(model.root, envelope(modelPayload))
  await assert.rejects(() => load(model.root), hasCode('model_binding_invalid'))

  const activation = createFixture()
  const activationPayload = {
    ...activation.payload,
    activation: { scope: 'production', production_deployed: true },
  }
  writeManifest(activation.root, envelope(activationPayload))
  await assert.rejects(
    () => load(activation.root),
    hasCode('manifest_schema_invalid'),
  )
})

test('Policy 即使自报 ID 与摘要一致，也必须等于固定首片语义重算值', async () => {
  const fixture = createFixture()
  const payload = structuredClone(fixture.payload)
  const forgedHash = canonicalJsonSha256({
    ...payload.policy,
    required_scope: 'country_outage:admin',
  })
  payload.policy.policy_id = `policy-sha256:${forgedHash}`
  payload.policy.policy_digest = `sha256:${forgedHash}`
  writeManifest(fixture.root, envelope(payload))
  await assert.rejects(
    () => load(fixture.root),
    hasCode('policy_binding_invalid'),
  )
})

test('Registry 即使按篡改内容重算 ID 与摘要，也必须匹配固定 Capability 语义', async () => {
  const fixture = createFixture()
  const payload = structuredClone(fixture.payload)
  payload.registry.capabilities[0].execution_binding.semantic_digest = sha('9')
  const forgedHash = canonicalJsonSha256({
    capabilities: payload.registry.capabilities,
  })
  payload.registry.registry_snapshot_id =
    `registry-snapshot-sha256:${forgedHash}`
  payload.registry.registry_digest = `sha256:${forgedHash}`
  writeManifest(fixture.root, envelope(payload))
  await assert.rejects(
    () => load(fixture.root),
    hasCode('registry_binding_invalid'),
  )
})

test('contracts.ts 与 capability-execution.ts 必须按固定路径绑定合同和实现摘要', async () => {
  const contract = createFixture()
  const contractPayload = structuredClone(contract.payload)
  const contractSource = contractPayload.source_files.find(
    (source) => source.path === CONTRACTS_PATH,
  )!
  const movedContractPath = 'agent-sidecar/src/agent/contracts-renamed.ts'
  writeProjectFile(
    contract.root,
    movedContractPath,
    'export const source = "renamed"\n',
  )
  writeProjectFile(
    contract.root,
    runtimeDistPath(movedContractPath),
    'export const source = "renamed";\n',
  )
  contractSource.path = movedContractPath
  contractSource.sha256 = shaText('export const source = "renamed"\n')
  contractPayload.source_files.push({
    path: runtimeDistPath(movedContractPath),
    sha256: shaText('export const source = "renamed";\n'),
  })
  writeManifest(contract.root, envelope(contractPayload))
  await assert.rejects(
    () => load(contract.root),
    hasCode('runtime_closure_invalid'),
  )

  const implementation = createFixture()
  const implementationPayload = structuredClone(implementation.payload)
  implementationPayload.registry.capabilities[1].execution_binding
    .implementation_digest = sha('8')
  const forgedHash = canonicalJsonSha256({
    capabilities: implementationPayload.registry.capabilities,
  })
  implementationPayload.registry.registry_snapshot_id =
    `registry-snapshot-sha256:${forgedHash}`
  implementationPayload.registry.registry_digest = `sha256:${forgedHash}`
  writeManifest(implementation.root, envelope(implementationPayload))
  await assert.rejects(
    () => load(implementation.root),
    hasCode('registry_binding_invalid'),
  )
})

test('关键传递依赖与实际 dist 任一字节漂移都失败关闭', async () => {
  const source = createFixture()
  writeFileSync(source.transitiveSourcePath, 'tampered source\n')
  await assert.rejects(
    () => load(source.root),
    hasCode('source_file_hash_mismatch'),
  )

  const dist = createFixture()
  writeFileSync(dist.transitiveDistPath, 'tampered build\n')
  await assert.rejects(
    () => load(dist.root),
    hasCode('source_file_hash_mismatch'),
  )
})

test('runtime source/dist 路径必须成对，关键运行时路径不可移除', async () => {
  const unpaired = createFixture()
  const unpairedPayload = structuredClone(unpaired.payload)
  unpairedPayload.source_files = unpairedPayload.source_files.filter(
    (source) => source.path !== TRANSITIVE_DIST_PATH,
  )
  writeManifest(unpaired.root, envelope(unpairedPayload))
  await assert.rejects(
    () => load(unpaired.root),
    hasCode('runtime_closure_invalid'),
  )

  const missingRequired = createFixture()
  const missingPayload = structuredClone(missingRequired.payload)
  const requiredPath = 'agent-sidecar/src/pi/formal-model-runtime.ts'
  missingPayload.source_files = missingPayload.source_files.filter(
    (source) => ![requiredPath, runtimeDistPath(requiredPath)].includes(source.path),
  )
  writeManifest(missingRequired.root, envelope(missingPayload))
  await assert.rejects(
    () => load(missingRequired.root),
    hasCode('runtime_closure_invalid'),
  )
})

test('source_files 摘要漂移、目录逃逸和符号链接均失败关闭', async () => {
  const changed = createFixture()
  writeFileSync(changed.contractsPath, 'tampered\n')
  await assert.rejects(
    () => load(changed.root),
    hasCode('source_file_hash_mismatch'),
  )

  const escaped = createFixture()
  const escapedPayload = structuredClone(escaped.payload)
  escapedPayload.source_files.push({ path: '../outside.ts', sha256: sha('7') })
  writeManifest(escaped.root, envelope(escapedPayload))
  await assert.rejects(
    () => load(escaped.root),
    hasCode('source_file_path_invalid'),
  )

  const linked = createFixture()
  unlinkSync(linked.contractsPath)
  symlinkSync(linked.transitiveSourcePath, linked.contractsPath)
  await assert.rejects(
    () => load(linked.root),
    hasCode('source_file_invalid'),
  )
})
