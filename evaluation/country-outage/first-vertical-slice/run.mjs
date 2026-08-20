#!/usr/bin/env node

import {
  createHash,
  createPrivateKey,
  createPublicKey,
  randomBytes,
  sign as signBytes,
} from 'node:crypto'
import { constants as fsConstants } from 'node:fs'
import {
  mkdir,
  open,
  readFile,
  realpath,
  writeFile,
} from 'node:fs/promises'
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path'

import {
  bindRealFirstSliceEvaluationTarget,
  buildExecutionAttestationPayload,
  finalizeAcceptanceRecordFiles,
  parseTrustedJson,
  prepareIndependentReviewForSigning,
  runFirstVerticalSliceEvaluation,
  verifyExecutionAttestation,
  writeEvaluationArtifacts,
} from './evaluator.mjs'

const { canonicalJsonStringify } = await import(
  '../../../agent-sidecar/src/shared/deterministic-json.ts'
)
const { loadDomeyeFirstSliceCandidateManifest } = await import(
  '../../../agent-sidecar/src/agent/candidate-manifest.ts'
)

const EXECUTION_PRIVATE_KEY_ENV =
  'DOMEYE_FIRST_SLICE_EXECUTION_PRIVATE_KEY_FILE'
const REVIEW_PRIVATE_KEY_ENV =
  'DOMEYE_FIRST_SLICE_REVIEW_PRIVATE_KEY_FILE'
const EXECUTION_ATTESTATION_SCHEMA =
  'domeye_first_slice_execution_attestation_v1'
const SIGNATURE_SCHEMA = 'domeye_ed25519_signature_v1'

function requiredString(value, code) {
  if (typeof value !== 'string' || !value.trim() || value !== value.trim()) {
    throw new TypeError(code)
  }
  return value
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function assertExactKeys(value, required, optional, code) {
  if (!isRecord(value)) throw new TypeError(code)
  const actual = Object.keys(value).sort()
  const allowed = [...required, ...optional].sort()
  if (
    required.some((key) => !Object.hasOwn(value, key))
    || actual.some((key) => !allowed.includes(key))
  ) throw new TypeError(code)
}

function assertRunConfig(config) {
  assertExactKeys(config, [
    'schema_version',
    'target',
    'evaluation_phase',
    'runs',
    'output_directory',
  ], [
    'journey_judgments_path',
    'drive_adversarial_cases',
  ], 'run_config_keys_invalid')
  assertExactKeys(config.target, [
    'project_root',
    'api_base_url',
    'manifest_path',
    'model_auth_path',
    'event_reference',
    'principal_id',
    'authorization_scopes',
  ], [
    'api_timeout_ms',
  ], 'run_target_config_keys_invalid')
}

function assertReviewSignConfig(config) {
  assertExactKeys(config, [
    'schema_version',
    'target',
    'summary_path',
    'evidence_jsonl_path',
    'evidence_attestation_path',
    'independent_review_draft_path',
    'output_path',
  ], [], 'review_sign_config_keys_invalid')
  assertExactKeys(config.target, [
    'project_root',
    'manifest_path',
  ], [], 'review_sign_target_config_keys_invalid')
}

function assertAcceptConfig(config) {
  assertExactKeys(config, [
    'schema_version',
    'target',
    'summary_path',
    'evidence_jsonl_path',
    'evidence_attestation_path',
    'independent_review_path',
    'output_path',
  ], [], 'accept_config_keys_invalid')
  assertExactKeys(config.target, [
    'project_root',
    'manifest_path',
  ], [], 'accept_target_config_keys_invalid')
}

function isWithin(root, target) {
  const pathFromRoot = relative(root, target)
  return pathFromRoot === ''
    || (!pathFromRoot.startsWith(`..${sep}`)
      && pathFromRoot !== '..'
      && !isAbsolute(pathFromRoot))
}

function rejectPrivateKeyConfig(value) {
  if (Array.isArray(value)) {
    for (const item of value) rejectPrivateKeyConfig(item)
    return
  }
  if (!isRecord(value)) return
  for (const [key, item] of Object.entries(value)) {
    const normalized = key.normalize('NFKC').toLocaleLowerCase('en-US')
    if (normalized.includes('private') && normalized.includes('key')) {
      throw new TypeError('private_key_config_forbidden')
    }
    rejectPrivateKeyConfig(item)
  }
}

function assertSigningRoleEnvironment(command) {
  if (
    command === 'run'
    && process.env[REVIEW_PRIVATE_KEY_ENV] !== undefined
  ) throw new TypeError('signing_role_environment_conflict')
  if (
    command === 'sign-review'
    && process.env[EXECUTION_PRIVATE_KEY_ENV] !== undefined
  ) throw new TypeError('signing_role_environment_conflict')
  if (
    command === 'accept'
    && (
      process.env[EXECUTION_PRIVATE_KEY_ENV] !== undefined
      || process.env[REVIEW_PRIVATE_KEY_ENV] !== undefined
    )
  ) throw new TypeError('accept_private_key_environment_forbidden')
}

async function readTrustedJson(path, code) {
  try {
    return parseTrustedJson(await readFile(path, 'utf8'))
  } catch {
    throw new TypeError(code)
  }
}

function canonicalSha256(value) {
  return createHash('sha256')
    .update(canonicalJsonStringify(value))
    .digest('hex')
}

function publicKeyIdentity(privateKey) {
  const publicKey = createPublicKey(privateKey)
  const der = publicKey.export({ type: 'spki', format: 'der' })
  return {
    key_id: `ed25519-spki-sha256:${createHash('sha256')
      .update(der)
      .digest('hex')}`,
    public_key_spki_der_base64: Buffer.from(der).toString('base64'),
  }
}

async function canonicalBoundary(path) {
  try {
    return await realpath(resolve(path))
  } catch {
    return resolve(path)
  }
}

async function loadPrivateSigningKey(envName, boundaries) {
  const configuredPath = process.env[envName]
  if (typeof configuredPath !== 'string' || !configuredPath || !isAbsolute(configuredPath)) {
    throw new TypeError('private_signing_key_required')
  }
  const keyPath = resolve(configuredPath)
  const projectRoot = await canonicalBoundary(boundaries.project_root)
  const outputRoot = await canonicalBoundary(boundaries.output_root)
  let handle
  let keyBytes
  try {
    if (!Number.isInteger(fsConstants.O_NOFOLLOW)) {
      throw new TypeError('private_signing_key_nofollow_unavailable')
    }
    handle = await open(
      keyPath,
      fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW,
    )
    const metadata = await handle.stat()
    const currentUid = typeof process.getuid === 'function'
      ? process.getuid()
      : null
    if (
      !metadata.isFile()
      || currentUid === null
      || metadata.uid !== currentUid
      || (metadata.mode & 0o400) !== 0o400
      || (metadata.mode & 0o7177) !== 0
    ) throw new TypeError('private_signing_key_permissions_invalid')
    const canonicalKeyPath = await realpath(keyPath)
    if (
      isWithin(projectRoot, canonicalKeyPath)
      || isWithin(outputRoot, canonicalKeyPath)
    ) throw new TypeError('private_signing_key_location_invalid')
    keyBytes = await handle.readFile()
    const privateKey = createPrivateKey(keyBytes)
    if (
      privateKey.type !== 'private'
      || privateKey.asymmetricKeyType !== 'ed25519'
    ) throw new TypeError('private_signing_key_type_invalid')
    return privateKey
  } catch (error) {
    if (
      error instanceof TypeError
      && /^[a-z][a-z0-9_]{0,127}$/.test(error.message)
    ) throw error
    throw new TypeError('private_signing_key_invalid')
  } finally {
    if (keyBytes) keyBytes.fill(0)
    await handle?.close().catch(() => {})
  }
}

function assertSigningKeyMatchesPolicy(privateKey, policyMember) {
  const identity = publicKeyIdentity(privateKey)
  if (
    !isRecord(policyMember)
    || policyMember.key_id !== identity.key_id
    || policyMember.public_key_spki_der_base64
      !== identity.public_key_spki_der_base64
  ) throw new TypeError('private_signing_key_not_candidate_bound')
}

function signatureInput(domain, payload) {
  return Buffer.from(
    `${requiredString(domain, 'signature_domain_invalid')}\u0000${canonicalJsonStringify(payload)}`,
    'utf8',
  )
}

function signatureForPayload({
  purpose,
  policy,
  policyMember,
  payload,
  privateKey,
}) {
  const signature = signBytes(
    null,
    signatureInput(policy.signature_domains[purpose], payload),
    privateKey,
  )
  return Object.freeze({
    schema_version: SIGNATURE_SCHEMA,
    algorithm: policy.algorithm,
    key_id: policyMember.key_id,
    domain: policy.signature_domains[purpose],
    signature_base64: signature.toString('base64'),
  })
}

async function writeNewJson(path, value) {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 })
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: 'utf8',
    flag: 'wx',
    mode: 0o600,
  })
}

function configPathArgument() {
  const path = process.argv[3]
  if (!path || process.argv.length !== 4) {
    throw new TypeError('command_arguments_invalid')
  }
  return resolve(path)
}

async function runEvaluation(config, configDirectory) {
  if (config.schema_version !== 'domeye_first_slice_evaluation_run_config_v2') {
    throw new TypeError('run_config_schema_invalid')
  }
  assertRunConfig(config)
  if (!['pilot', 'formal'].includes(config.evaluation_phase)) {
    throw new TypeError('evaluation_phase_invalid')
  }
  const expectedRuns = config.evaluation_phase === 'pilot' ? 3 : 30
  if (config.runs !== expectedRuns) {
    throw new TypeError(
      config.evaluation_phase === 'pilot'
        ? 'pilot_runs_must_equal_3'
        : 'formal_runs_must_equal_30',
    )
  }
  const projectRoot = resolve(requiredString(
    config.target?.project_root,
    'project_root_required',
  ))
  const outputDirectory = resolve(
    configDirectory,
    requiredString(config.output_directory, 'output_directory_required'),
  )
  const target = await bindRealFirstSliceEvaluationTarget(config.target)
  const attestationPolicy = target.loaded_candidate.manifest.payload
    .attestation_policy
  let journeyJudgments
  if (config.journey_judgments_path) {
    journeyJudgments = await readTrustedJson(resolve(
      configDirectory,
      config.journey_judgments_path,
    ), 'journey_judgments_json_invalid')
  }
  const driveAdversarialCases = config.drive_adversarial_cases === true
  const result = await runFirstVerticalSliceEvaluation({
    loaded_candidate: target.loaded_candidate,
    run_j1_trial: target.run_j1_trial,
    execution_mode: target.execution_mode,
    runtime_principal_binding: target.runtime_principal_binding,
    runtime_source_binding: target.runtime_source_binding,
    evaluator_implementation: target.evaluator_implementation,
    api_endpoint_attestation: target.api_endpoint_attestation,
    evaluation_project_root: target.evaluation_project_root,
    execution_actor_id: attestationPolicy.execution_evidence.actor_id,
    evaluation_phase: config.evaluation_phase,
    runs: config.runs,
    ...(journeyJudgments ? { journey_judgments: journeyJudgments } : {}),
    ...(driveAdversarialCases ? { drive_adversarial_cases: true } : {}),
  })
  const output = await writeEvaluationArtifacts(result, outputDirectory)
  const evidenceJsonlBytes = await readFile(output.paths.evidence_jsonl)
  const summaryJsonBytes = await readFile(output.paths.summary)
  const summary = (() => {
    try {
      return parseTrustedJson(
        summaryJsonBytes.toString('utf8'),
        'written_summary_json_invalid',
      )
    } catch {
      throw new TypeError('written_summary_json_invalid')
    }
  })()
  const payload = await buildExecutionAttestationPayload({
    result,
    loaded_candidate: target.loaded_candidate,
    summary_json_bytes: summaryJsonBytes,
    evidence_jsonl_bytes: evidenceJsonlBytes,
    nonce: randomBytes(32).toString('hex'),
  })
  let privateKey = await loadPrivateSigningKey(
    EXECUTION_PRIVATE_KEY_ENV,
    { project_root: projectRoot, output_root: outputDirectory },
  )
  let executionSignature
  try {
    assertSigningKeyMatchesPolicy(
      privateKey,
      attestationPolicy?.execution_evidence,
    )
    executionSignature = signatureForPayload({
      purpose: 'execution_evidence',
      policy: attestationPolicy,
      policyMember: attestationPolicy.execution_evidence,
      payload,
      privateKey,
    })
  } finally {
    privateKey = null
  }
  const executionAttestation = Object.freeze({
    schema_version: EXECUTION_ATTESTATION_SCHEMA,
    attestation_id:
      `execution-attestation-sha256:${canonicalSha256(payload)}`,
    payload,
    signature: executionSignature,
  })
  await verifyExecutionAttestation({
    loaded_candidate: target.loaded_candidate,
    summary,
    summary_json_bytes: summaryJsonBytes,
    evidence_jsonl: evidenceJsonlBytes,
    execution_attestation: executionAttestation,
  })
  const evidenceAttestationPath = resolve(
    outputDirectory,
    'evidence-attestation.json',
  )
  await writeNewJson(evidenceAttestationPath, executionAttestation)
  return {
    event: 'domeye_first_slice_evaluation_written',
    candidate_id: result.summary.candidate_id,
    evaluation_run_id: result.summary.evaluation_run_id,
    evidence_gate: result.summary.evidence_gate.status,
    acceptance_state: 'pending_independent_review',
    paths: {
      ...output.paths,
      evidence_attestation: evidenceAttestationPath,
    },
  }
}

async function signIndependentReview(config, configDirectory) {
  if (config.schema_version !== 'domeye_first_slice_review_sign_config_v1') {
    throw new TypeError('review_sign_config_schema_invalid')
  }
  assertReviewSignConfig(config)
  const projectRoot = resolve(requiredString(
    config.target?.project_root,
    'project_root_required',
  ))
  const manifestPath = requiredString(
    config.target?.manifest_path,
    'manifest_path_required',
  )
  const outputPath = resolve(
    configDirectory,
    requiredString(config.output_path, 'output_path_required'),
  )
  const loadedCandidate = await loadDomeyeFirstSliceCandidateManifest({
    project_root: projectRoot,
    manifest_path: manifestPath,
  })
  const attestationPolicy = loadedCandidate.manifest.payload
    .attestation_policy
  const summaryPath = resolve(
    configDirectory,
    requiredString(config.summary_path, 'summary_path_required'),
  )
  const summaryJsonBytes = await readFile(summaryPath)
  const summary = (() => {
    try {
      return parseTrustedJson(
        summaryJsonBytes.toString('utf8'),
        'summary_json_invalid',
      )
    } catch {
      throw new TypeError('summary_json_invalid')
    }
  })()
  const evidenceJsonl = await readFile(resolve(
    configDirectory,
    requiredString(config.evidence_jsonl_path, 'evidence_jsonl_path_required'),
  ))
  const executionAttestation = await readTrustedJson(resolve(
    configDirectory,
    requiredString(
      config.evidence_attestation_path,
      'evidence_attestation_path_required',
    ),
  ), 'evidence_attestation_json_invalid')
  await verifyExecutionAttestation({
    loaded_candidate: loadedCandidate,
    summary,
    summary_json_bytes: summaryJsonBytes,
    evidence_jsonl: evidenceJsonl,
    execution_attestation: executionAttestation,
  })
  const draftReview = await readTrustedJson(resolve(
    configDirectory,
    requiredString(
      config.independent_review_draft_path,
      'independent_review_draft_path_required',
    ),
  ), 'independent_review_draft_json_invalid')
  const preparedReview = await prepareIndependentReviewForSigning({
    loaded_candidate: loadedCandidate,
    summary,
    summary_json_bytes: summaryJsonBytes,
    evidence_jsonl: evidenceJsonl,
    execution_attestation: executionAttestation,
    independent_review_draft: draftReview,
  })
  if (
    preparedReview.signature_domain
      !== attestationPolicy.signature_domains.independent_review
    || preparedReview.key_id
      !== attestationPolicy.independent_review.key_id
  ) throw new TypeError('independent_review_signing_binding_invalid')
  let privateKey = await loadPrivateSigningKey(
    REVIEW_PRIVATE_KEY_ENV,
    { project_root: projectRoot, output_root: dirname(outputPath) },
  )
  let reviewSignature
  try {
    assertSigningKeyMatchesPolicy(
      privateKey,
      attestationPolicy?.independent_review,
    )
    reviewSignature = signatureForPayload({
      purpose: 'independent_review',
      policy: attestationPolicy,
      policyMember: attestationPolicy.independent_review,
      payload: preparedReview.unsigned_review,
      privateKey,
    })
  } finally {
    privateKey = null
  }
  const signedReview = {
    ...preparedReview.unsigned_review,
    signature: reviewSignature,
  }
  await writeNewJson(outputPath, signedReview)
  return {
    event: 'domeye_first_slice_independent_review_signed',
    candidate_id: preparedReview.unsigned_review.candidate_id,
    evaluation_run_id: preparedReview.unsigned_review.evaluation_run_id,
    output_path: outputPath,
  }
}

async function acceptEvaluation(config, configDirectory) {
  if (config.schema_version !== 'domeye_first_slice_accept_config_v2') {
    throw new TypeError('accept_config_schema_invalid')
  }
  assertAcceptConfig(config)
  const projectRoot = resolve(requiredString(
    config.target?.project_root,
    'project_root_required',
  ))
  const record = await finalizeAcceptanceRecordFiles({
    project_root: projectRoot,
    manifest_path: requiredString(
      config.target?.manifest_path,
      'manifest_path_required',
    ),
    summary_path: resolve(configDirectory, config.summary_path),
    evidence_jsonl_path: resolve(configDirectory, config.evidence_jsonl_path),
    evidence_attestation_path: resolve(
      configDirectory,
      config.evidence_attestation_path,
    ),
    independent_review_path: resolve(
      configDirectory,
      config.independent_review_path,
    ),
    output_path: resolve(configDirectory, config.output_path),
  })
  return {
    event: 'domeye_first_slice_acceptance_record_written',
    candidate_id: record.candidate_id,
    evaluation_run_id: record.evaluation_run_id,
    acceptance_state: record.acceptance_state,
    dg1_decision: record.dg1_decision,
    output_path: resolve(configDirectory, config.output_path),
  }
}

async function main() {
  const command = process.argv[2]
  if (!['run', 'sign-review', 'accept'].includes(command)) {
    throw new TypeError('command_invalid')
  }
  const configPath = configPathArgument()
  const config = await readTrustedJson(configPath, 'config_json_invalid')
  rejectPrivateKeyConfig(config)
  assertSigningRoleEnvironment(command)
  const configDirectory = dirname(configPath)
  const result = command === 'run'
    ? await runEvaluation(config, configDirectory)
    : command === 'sign-review'
      ? await signIndependentReview(config, configDirectory)
      : await acceptEvaluation(config, configDirectory)
  process.stdout.write(`${JSON.stringify(result)}\n`)
}

void main().catch((error) => {
  const code = error instanceof Error
    && /^[a-z][a-z0-9_:.-]{0,127}$/.test(error.message)
    ? error.message
    : 'evaluation_failed'
  process.stderr.write(`${JSON.stringify({
    event: 'domeye_first_slice_evaluation_failed',
    code,
  })}\n`)
  process.exitCode = 1
})
