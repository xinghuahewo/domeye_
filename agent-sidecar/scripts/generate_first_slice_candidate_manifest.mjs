import { execFileSync } from 'node:child_process'
import { createHash, createPublicKey } from 'node:crypto'
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises'
import {
  dirname,
  isAbsolute,
  relative,
  resolve,
  sep,
} from 'node:path'
import { fileURLToPath } from 'node:url'

import ts from 'typescript'

const projectRoot = resolve(process.argv[2] ?? resolve(import.meta.dirname, '../..'))
const dryRun = process.argv.includes('--dry-run')
const sidecarRoot = resolve(projectRoot, 'agent-sidecar')
const outputPath = resolve(
  projectRoot,
  'contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json',
)

const sourceEntryPaths = [
  'agent-sidecar/src/cli/serve-interactive-agent.ts',
]
const requiredRuntimeSourcePaths = [
  'agent-sidecar/src/formal-runtime-limits.ts',
  'agent-sidecar/src/pi/country-outage-skill-bundle.ts',
  'agent-sidecar/src/pi/formal-model-runtime.ts',
  'agent-sidecar/src/cli/sidecar-security.ts',
]
const fixedSourcePaths = [
  'docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md',
  'docs/architecture/Domeye_First_Vertical_Slice_Answer_Presentation_Addendum_v1.0.md',
  'contracts/agent/domeye-first-vertical-slice/v1/model-runtime.json',
  'agent-sidecar/package.json',
  'agent-sidecar/package-lock.json',
  'agent-sidecar/tsconfig.json',
  'agent-sidecar/scripts/generate_first_slice_candidate_manifest.mjs',
  'agent-sidecar/scripts/apply_pi_response_model_patch.mjs',
  'agent-sidecar/resources/vendor-patches/pi-ai-openai-completions-response-model-v1.json',
  'agent-sidecar/vendor-patches/pi-ai-0.84.1-openai-completions-response-model-v1.patch',
  'contracts/agent/domeye-first-vertical-slice/v1.1/attestors/execution-public-key.json',
  'contracts/agent/domeye-first-vertical-slice/v1.1/attestors/reviewer-public-key.json',
  'evaluation/country-outage/first-vertical-slice/evaluator.mjs',
  'evaluation/country-outage/first-vertical-slice/adversarial-driver.mjs',
  'evaluation/country-outage/first-vertical-slice/case-registry.mjs',
  'evaluation/country-outage/first-vertical-slice/source-loader.mjs',
  'evaluation/country-outage/first-vertical-slice/run.mjs',
]
const patchedProviderRelativePath =
  'agent-sidecar/node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js'

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
        .map(([key, item]) => [key, canonical(item)]),
    )
  }
  return value
}

function canonicalDigest(value) {
  return sha256(JSON.stringify(canonical(value)))
}

function projectRelative(path) {
  const value = relative(projectRoot, path)
  if (
    value === ''
    || value === '..'
    || value.startsWith(`..${sep}`)
    || isAbsolute(value)
  ) throw new Error('candidate_source_path_outside_project')
  return value.split(sep).join('/')
}

async function isRegularFile(path) {
  try {
    return (await stat(path)).isFile()
  } catch {
    return false
  }
}

function runtimeModuleSpecifiers(sourceFile) {
  const specifiers = new Set()
  const add = (value) => {
    if (typeof value === 'string' && value.startsWith('.')) {
      specifiers.add(value)
    }
  }
  const visit = (node) => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteralLike(node.moduleSpecifier)) {
      const clause = node.importClause
      const namedBindings = clause?.namedBindings
      const typeOnlyNamedImport = Boolean(
        clause
        && !clause.name
        && namedBindings
        && ts.isNamedImports(namedBindings)
        && namedBindings.elements.length > 0
        && namedBindings.elements.every((item) => item.isTypeOnly),
      )
      if (!clause?.isTypeOnly && !typeOnlyNamedImport) {
        add(node.moduleSpecifier.text)
      }
    } else if (
      ts.isExportDeclaration(node)
      && node.moduleSpecifier
      && ts.isStringLiteralLike(node.moduleSpecifier)
    ) {
      const typeOnlyNamedExport = Boolean(
        node.exportClause
        && ts.isNamedExports(node.exportClause)
        && node.exportClause.elements.length > 0
        && node.exportClause.elements.every((item) => item.isTypeOnly),
      )
      if (!node.isTypeOnly && !typeOnlyNamedExport) {
        add(node.moduleSpecifier.text)
      }
    } else if (
      ts.isCallExpression(node)
      && node.expression.kind === ts.SyntaxKind.ImportKeyword
      && node.arguments.length === 1
      && ts.isStringLiteralLike(node.arguments[0])
    ) {
      add(node.arguments[0].text)
    }
    ts.forEachChild(node, visit)
  }
  visit(sourceFile)
  return [...specifiers]
}

async function resolveRelativeSourceImport(importerPath, specifier) {
  const base = resolve(dirname(importerPath), specifier)
  const candidates = specifier.endsWith('.js')
    ? [`${base.slice(0, -3)}.ts`]
    : specifier.endsWith('.mjs')
      ? [`${base.slice(0, -4)}.mts`]
      : specifier.endsWith('.cjs')
        ? [`${base.slice(0, -4)}.cts`]
        : specifier.endsWith('.ts')
          || specifier.endsWith('.mts')
          || specifier.endsWith('.cts')
          ? [base]
          : [`${base}.ts`, resolve(base, 'index.ts')]
  const matches = []
  for (const candidate of candidates) {
    if (await isRegularFile(candidate)) matches.push(candidate)
  }
  if (matches.length !== 1) {
    throw new Error(
      `candidate_runtime_source_import_unresolved:${projectRelative(importerPath)}:${specifier}`,
    )
  }
  return matches[0]
}

async function discoverRuntimeSourceClosure(entries) {
  const pending = entries.map((path) => resolve(projectRoot, path))
  const discovered = new Set()
  while (pending.length > 0) {
    const path = pending.shift()
    const relativePath = projectRelative(path)
    if (discovered.has(relativePath)) continue
    if (!await isRegularFile(path)) {
      throw new Error(`candidate_runtime_source_missing:${relativePath}`)
    }
    discovered.add(relativePath)
    const source = await readFile(path, 'utf8')
    const sourceFile = ts.createSourceFile(
      path,
      source,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TS,
    )
    for (const specifier of runtimeModuleSpecifiers(sourceFile)) {
      pending.push(await resolveRelativeSourceImport(path, specifier))
    }
  }
  return [...discovered].sort()
}

async function resolveRelativeDistImport(importerPath, specifier) {
  const base = resolve(dirname(importerPath), specifier)
  const candidates = specifier.endsWith('.js')
    ? [base]
    : [`${base}.js`, resolve(base, 'index.js')]
  const matches = []
  for (const candidate of candidates) {
    if (await isRegularFile(candidate)) matches.push(candidate)
  }
  if (matches.length !== 1) {
    throw new Error(
      `candidate_runtime_dist_import_unresolved:${projectRelative(importerPath)}:${specifier}`,
    )
  }
  return matches[0]
}

async function discoverRuntimeDistClosure(sourceClosure) {
  const entry = resolve(
    projectRoot,
    'agent-sidecar/dist/src/cli/serve-interactive-agent.js',
  )
  const pending = [entry]
  const discovered = new Set()
  while (pending.length > 0) {
    const path = pending.shift()
    const relativePath = projectRelative(path)
    if (discovered.has(relativePath)) continue
    if (!await isRegularFile(path)) {
      throw new Error(`candidate_runtime_dist_missing:${relativePath}`)
    }
    discovered.add(relativePath)
    const source = await readFile(path, 'utf8')
    const sourceFile = ts.createSourceFile(
      path,
      source,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.JS,
    )
    for (const specifier of runtimeModuleSpecifiers(sourceFile)) {
      pending.push(await resolveRelativeDistImport(path, specifier))
    }
  }
  const expected = sourceClosure.map((path) => {
    if (!path.startsWith('agent-sidecar/src/') || !path.endsWith('.ts')) {
      throw new Error(`candidate_runtime_source_path_invalid:${path}`)
    }
    return `agent-sidecar/dist/src/${path.slice('agent-sidecar/src/'.length, -3)}.js`
  }).sort()
  const actual = [...discovered].sort()
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error('candidate_runtime_source_dist_closure_mismatch')
  }
  return actual
}

function assertPatchedProviderPath() {
  const codingAgentEntry = fileURLToPath(
    import.meta.resolve('@earendil-works/pi-coding-agent'),
  )
  const codingAgentRoot = resolve(dirname(codingAgentEntry), '..')
  const target = resolve(
    codingAgentRoot,
    'node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js',
  )
  if (projectRelative(target) !== patchedProviderRelativePath) {
    throw new Error('candidate_patched_provider_path_mismatch')
  }
}

const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm'
execFileSync(npmCommand, ['run', 'build'], {
  cwd: sidecarRoot,
  stdio: 'inherit',
})

const { parseDomeyeJsonWithoutDuplicateKeys } = await import(
  '../dist/src/agent/candidate-manifest.js'
)

async function readAttestorPublicKey(path, role) {
  const value = parseDomeyeJsonWithoutDuplicateKeys(
    await readFile(resolve(projectRoot, path), 'utf8'),
  )
  const expectedKeys = [
    'algorithm',
    'key_id',
    'public_key_spki_der_base64',
    'role',
    'schema_version',
  ]
  if (
    !value
    || typeof value !== 'object'
    || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(expectedKeys)
    || value.schema_version !== 'domeye_first_slice_attestor_public_key_v1'
    || value.role !== role
    || value.algorithm !== 'ed25519'
    || typeof value.public_key_spki_der_base64 !== 'string'
  ) throw new Error(`candidate_attestor_public_key_invalid:${role}`)
  try {
    const der = Buffer.from(value.public_key_spki_der_base64, 'base64')
    if (
      der.length === 0
      || der.toString('base64') !== value.public_key_spki_der_base64
    ) throw new Error('spki_base64_noncanonical')
    const key = createPublicKey({ key: der, format: 'der', type: 'spki' })
    if (key.asymmetricKeyType !== 'ed25519') throw new Error('not_ed25519')
    const canonicalDer = key.export({ format: 'der', type: 'spki' })
    const keyId = `ed25519-spki-sha256:${sha256(der)}`
    if (!Buffer.from(canonicalDer).equals(der) || value.key_id !== keyId) {
      throw new Error('attestor_key_id_mismatch')
    }
  } catch {
    throw new Error(`candidate_attestor_public_key_invalid:${role}`)
  }
  return value
}

assertPatchedProviderPath()
const runtimeSourcePaths = await discoverRuntimeSourceClosure(sourceEntryPaths)
for (const requiredPath of requiredRuntimeSourcePaths) {
  if (!runtimeSourcePaths.includes(requiredPath)) {
    throw new Error(`candidate_required_runtime_source_missing:${requiredPath}`)
  }
}
const runtimeDistPaths = await discoverRuntimeDistClosure(runtimeSourcePaths)
const sourcePaths = [...new Set([
  ...fixedSourcePaths,
  patchedProviderRelativePath,
  ...runtimeSourcePaths,
  ...runtimeDistPaths,
])].sort()

const sourceFiles = await Promise.all(sourcePaths.map(async (path) => ({
  path,
  sha256: `sha256:${sha256(await readFile(resolve(projectRoot, path)))}`,
})))
const sourceDigest = new Map(sourceFiles.map((item) => [item.path, item.sha256]))
const task = parseDomeyeJsonWithoutDuplicateKeys(await readFile(
  resolve(projectRoot, '.codex/TASK.json'),
  'utf8',
))
const modelResource = parseDomeyeJsonWithoutDuplicateKeys(await readFile(
  resolve(projectRoot, 'contracts/agent/domeye-first-vertical-slice/v1/model-runtime.json'),
  'utf8',
))
const executionAttestor = await readAttestorPublicKey(
  'contracts/agent/domeye-first-vertical-slice/v1.1/attestors/execution-public-key.json',
  'execution_evidence',
)
const reviewerAttestor = await readAttestorPublicKey(
  'contracts/agent/domeye-first-vertical-slice/v1.1/attestors/reviewer-public-key.json',
  'independent_review',
)
if (executionAttestor.key_id === reviewerAttestor.key_id) {
  throw new Error('candidate_attestor_keys_must_be_distinct')
}
const contractDigest = sourceDigest.get(
  'docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md',
)
const answerPresentationContractDigest = sourceDigest.get(
  'docs/architecture/Domeye_First_Vertical_Slice_Answer_Presentation_Addendum_v1.0.md',
)
const machineContractDigest = sourceDigest.get(
  'agent-sidecar/src/agent/contracts.ts',
)
const implementationDigest = sourceDigest.get(
  'agent-sidecar/src/agent/capability-execution.ts',
)
const modelResourceDigest = sourceDigest.get(
  'contracts/agent/domeye-first-vertical-slice/v1/model-runtime.json',
)
if (
  typeof task.baseCommit !== 'string'
  || !contractDigest
  || !answerPresentationContractDigest
  || !machineContractDigest
  || !implementationDigest
  || !modelResourceDigest
) throw new Error('candidate_source_binding_incomplete')

const policyBody = {
  tenant_id: 'domeye',
  required_scope: 'country_outage:read',
  allowed_capability_ids: ['CAP-006', 'CAP-016'],
  model_api_attempt_limit: 10,
  approved_action_limit: 2,
  cost_policy: 'audit_only',
  monetary_limit_usd: null,
}
const policyHash = canonicalDigest(policyBody)
const capabilities = [
  {
    capability_id: 'CAP-006',
    state: 'active',
    execution_binding: {
      execution_unit_id: 'TOOL-03',
      execution_unit_name: 'read_metric_series',
      execution_unit_version: '1.0.0',
      contract_digest: machineContractDigest,
      implementation_digest: implementationDigest,
      semantic_digest: `sha256:${canonicalDigest({
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
      contract_digest: machineContractDigest,
      implementation_digest: implementationDigest,
      semantic_digest: `sha256:${canonicalDigest({
        input: 'qualified_metric_series_artifact_ref',
        operation: 'series_extrema',
        tie_policy: 'first_observed_occurrence',
        null_policy: 'exclude_null_never_zero_fill',
        empty_policy: 'empty_observed_set',
      })}`,
    },
  },
]
const registryHash = canonicalDigest({ capabilities })
const payload = {
  schema_version: 'domeye_first_slice_candidate_manifest_v2',
  base_commit: task.baseCommit,
  contract: {
    version: 'domeye.first-vertical-slice/v1.0',
    digest: contractDigest,
  },
  answer_presentation_contract: {
    version: 'domeye.first-vertical-slice.answer-presentation/v1.0',
    digest: answerPresentationContractDigest,
  },
  data_identity: {
    event_type: 'country_outage',
    incident_id: 'incident_go_v1_a1de26f854831330c616a72af21597eb',
    publication_id: 'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f',
    revision: 1,
    collector_id: 'rrc25',
    cohort_id: 'country_event_cohort_v1_1e04abfc6430776bef20403fac528698',
    country_code: 'IR',
    window_start_utc: '2026-02-27T00:10:00Z',
    window_end_utc: '2026-03-11T00:00:00Z',
    data_through: '2026-03-11T00:00:00Z',
    is_final_in_data_range: false,
    lifecycle_state: 'event_end_unknown',
  },
  series_response_sha256: 'sha256:45700171b9cef9c41eeaa6e124c1f0920b57dd544be7e00d45b3c7c0706925d6',
  model: {
    candidate_id: modelResource.candidate_id,
    resource_sha256: modelResourceDigest,
    provider: modelResource.provider,
    model: modelResource.model,
    model_version: modelResource.model_version,
    expected_response_model: modelResource.expected_response_model,
    api: modelResource.api,
    base_url: modelResource.base_url,
    maximum_output_tokens: modelResource.maximum_output_tokens,
    thinking_level: modelResource.thinking_level,
    pi_version: modelResource.pi_version,
  },
  budget_policy: {
    model_api_attempt_limit: 10,
    approved_action_limit: 2,
    cost_policy: 'audit_only',
    monetary_limit_usd: null,
  },
  policy: {
    policy_id: `policy-sha256:${policyHash}`,
    policy_digest: `sha256:${policyHash}`,
    state: 'active',
    allowed_capability_ids: ['CAP-006', 'CAP-016'],
  },
  registry: {
    registry_snapshot_id: `registry-snapshot-sha256:${registryHash}`,
    registry_digest: `sha256:${registryHash}`,
    state: 'active',
    capabilities,
  },
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
      role: executionAttestor.role,
      actor_id: 'domeye-first-slice-real-runtime-attestor-v1',
      key_id: executionAttestor.key_id,
      public_key_spki_der_base64:
        executionAttestor.public_key_spki_der_base64,
    },
    independent_review: {
      role: reviewerAttestor.role,
      actor_id: 'domeye-first-slice-independent-reviewer-v1',
      key_id: reviewerAttestor.key_id,
      public_key_spki_der_base64:
        reviewerAttestor.public_key_spki_der_base64,
    },
  },
  source_files: sourceFiles,
  activation: {
    scope: 'local_evaluation_only',
    production_deployed: false,
  },
}
const manifest = {
  candidate_id: `manifest:sha256:${canonicalDigest(payload)}`,
  payload,
}
if (!dryRun) {
  await mkdir(dirname(outputPath), { recursive: true })
  await writeFile(outputPath, `${JSON.stringify(manifest, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o644,
  })
}
process.stdout.write(`${JSON.stringify({
  event: dryRun
    ? 'domeye_first_slice_candidate_manifest_verified'
    : 'domeye_first_slice_candidate_manifest_generated',
  candidate_id: manifest.candidate_id,
  output_path: dryRun ? null : outputPath,
  runtime_source_file_count: runtimeSourcePaths.length,
  runtime_dist_file_count: runtimeDistPaths.length,
  source_file_count: sourceFiles.length,
})}\n`)
