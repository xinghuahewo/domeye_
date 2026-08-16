#!/usr/bin/env node

import { createHash, randomUUID } from 'node:crypto'
import {
  chmodSync,
  closeSync,
  constants,
  fchmodSync,
  fsyncSync,
  lstatSync,
  openSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { dirname, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const PATCH_ID = 'pi-ai-openai-completions-response-model-v1'
const CODING_AGENT_PACKAGE = '@earendil-works/pi-coding-agent'
const EXPECTED_VERSION = '0.84.1'
const TARGET_RELATIVE_PATH =
  'node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js'
const PI_AI_PACKAGE_RELATIVE_PATH =
  'node_modules/@earendil-works/pi-ai/package.json'
const UPSTREAM_SHA256 =
  '727d744f20985f667151e8ecee3ad30af388d9d66d91a92d0fb9ad3261da4363'
const PATCHED_SHA256 =
  '9bb5badc07dc1f073e094743acf4b81390601ae5bead8c35f15c54f7f0bc0504'
const PATCH_ARTIFACT_SHA256 =
  'a7e89d8dae4ddb8a3aa2548153c2e0e68f57fd7b8102bdde10ecc8d297836c28'
const PATCH_MANIFEST_SHA256 =
  'ba5f5bceae09c868285926d0b63c562f88168211284c52036aa62d8346bab1ad'
const UPSTREAM_BYTE_LENGTH = 60_161
const PATCHED_BYTE_LENGTH = 60_133
const MAXIMUM_TARGET_BYTES = 128 * 1024
const BEFORE =
  'if (typeof chunk.model === "string" && chunk.model.length > 0 && chunk.model !== model.id) {'
const AFTER =
  'if (typeof chunk.model === "string" && chunk.model.length > 0) {'

class VendorPatchError extends Error {
  constructor() {
    super('Pi responseModel vendor patch 校验或应用失败')
    this.name = 'VendorPatchError'
  }
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

function exactOccurrenceCount(value, needle) {
  return value.split(needle).length - 1
}

function isWithin(root, target) {
  const difference = relative(root, target)
  return (
    difference === '' ||
    (
      difference !== '..' &&
      !difference.startsWith(`..${sep}`) &&
      !difference.startsWith('/') &&
      !difference.startsWith('\\')
    )
  )
}

function checkedRegularFile(path, maximumBytes) {
  const normalized = resolve(path)
  const stats = lstatSync(normalized)
  if (
    !stats.isFile() ||
    stats.isSymbolicLink() ||
    stats.size <= 0 ||
    stats.size > maximumBytes ||
    realpathSync(normalized) !== normalized
  ) {
    throw new VendorPatchError()
  }
  return { normalized, stats }
}

function parseExactPackage(path, expectedName) {
  const { normalized } = checkedRegularFile(path, 64 * 1024)
  const value = JSON.parse(readFileSync(normalized, 'utf8'))
  if (
    !value ||
    typeof value !== 'object' ||
    value.name !== expectedName ||
    value.version !== EXPECTED_VERSION
  ) {
    throw new VendorPatchError()
  }
}

function assertManifest(sidecarRoot) {
  const manifestPath = resolve(
    sidecarRoot,
    'resources/vendor-patches/pi-ai-openai-completions-response-model-v1.json',
  )
  const patchPath = resolve(
    sidecarRoot,
    'vendor-patches/pi-ai-0.84.1-openai-completions-response-model-v1.patch',
  )
  if (
    !isWithin(sidecarRoot, manifestPath) ||
    !isWithin(sidecarRoot, patchPath)
  ) {
    throw new VendorPatchError()
  }
  const manifestBytes = readFileSync(
    checkedRegularFile(manifestPath, 64 * 1024).normalized,
  )
  const patchBytes = readFileSync(
    checkedRegularFile(patchPath, 64 * 1024).normalized,
  )
  if (
    sha256(manifestBytes) !== PATCH_MANIFEST_SHA256 ||
    sha256(patchBytes) !== PATCH_ARTIFACT_SHA256
  ) {
    throw new VendorPatchError()
  }
  const manifest = JSON.parse(manifestBytes.toString('utf8'))
  if (
    manifest?.schemaVersion !== 'country_outage_pi_vendor_patch_v1' ||
    manifest.patchId !== PATCH_ID ||
    manifest.applicationMode !==
      'postinstall_exact_hash_replacement_v1' ||
    manifest.target?.codingAgentPackage !== CODING_AGENT_PACKAGE ||
    manifest.target?.codingAgentVersion !== EXPECTED_VERSION ||
    manifest.target?.package !== '@earendil-works/pi-ai' ||
    manifest.target?.version !== EXPECTED_VERSION ||
    manifest.target?.relativePathFromCodingAgent !==
      TARGET_RELATIVE_PATH ||
    manifest.target?.upstreamSourceSha256 !== UPSTREAM_SHA256 ||
    manifest.target?.patchedSourceSha256 !== PATCHED_SHA256 ||
    manifest.target?.upstreamByteLength !== UPSTREAM_BYTE_LENGTH ||
    manifest.target?.patchedByteLength !== PATCHED_BYTE_LENGTH ||
    manifest.patchArtifact?.sha256 !== PATCH_ARTIFACT_SHA256 ||
    manifest.behavior?.before !== BEFORE ||
    manifest.behavior?.after !== AFTER ||
    manifest.behavior?.preservesSameNameResponseModel !== true ||
    manifest.behavior?.addsNetworkRequests !== false ||
    manifest.behavior?.changesToolCapabilities !== false ||
    manifest.behavior?.changesResourceResolution !== false
  ) {
    throw new VendorPatchError()
  }
}

function fsyncDirectory(path) {
  const descriptor = openSync(
    path,
    constants.O_RDONLY | (constants.O_DIRECTORY ?? 0),
  )
  try {
    fsyncSync(descriptor)
  } finally {
    closeSync(descriptor)
  }
}

function codingAgentRoot() {
  const entry = realpathSync(
    fileURLToPath(import.meta.resolve(CODING_AGENT_PACKAGE)),
  )
  const root = resolve(dirname(entry), '..')
  const stats = lstatSync(root)
  if (
    !stats.isDirectory() ||
    stats.isSymbolicLink() ||
    realpathSync(root) !== root
  ) {
    throw new VendorPatchError()
  }
  parseExactPackage(
    resolve(root, 'package.json'),
    CODING_AGENT_PACKAGE,
  )
  parseExactPackage(
    resolve(root, PI_AI_PACKAGE_RELATIVE_PATH),
    '@earendil-works/pi-ai',
  )
  return root
}

function assertPatchedSource(source) {
  if (
    Buffer.byteLength(source, 'utf8') !== PATCHED_BYTE_LENGTH ||
    sha256(source) !== PATCHED_SHA256 ||
    exactOccurrenceCount(source, AFTER) !== 1 ||
    exactOccurrenceCount(source, BEFORE) !== 0
  ) {
    throw new VendorPatchError()
  }
}

function applyOrVerify(mode) {
  const sidecarRoot = resolve(
    dirname(fileURLToPath(import.meta.url)),
    '..',
  )
  assertManifest(sidecarRoot)
  const packageRoot = codingAgentRoot()
  const target = resolve(packageRoot, TARGET_RELATIVE_PATH)
  if (!isWithin(packageRoot, target)) throw new VendorPatchError()
  const { normalized, stats } = checkedRegularFile(
    target,
    MAXIMUM_TARGET_BYTES,
  )
  const source = readFileSync(normalized, 'utf8')
  const currentSha256 = sha256(source)

  if (currentSha256 === PATCHED_SHA256) {
    assertPatchedSource(source)
    return 'verified'
  }
  if (
    mode !== '--apply' ||
    currentSha256 !== UPSTREAM_SHA256 ||
    Buffer.byteLength(source, 'utf8') !== UPSTREAM_BYTE_LENGTH ||
    exactOccurrenceCount(source, BEFORE) !== 1 ||
    exactOccurrenceCount(source, AFTER) !== 0
  ) {
    throw new VendorPatchError()
  }

  const patched = source.replace(BEFORE, AFTER)
  assertPatchedSource(patched)
  const targetDirectory = dirname(normalized)
  if (realpathSync(targetDirectory) !== targetDirectory) {
    throw new VendorPatchError()
  }
  const temporaryPath = resolve(
    targetDirectory,
    `.openai-completions.js.${process.pid}.${randomUUID()}.tmp`,
  )
  if (!isWithin(targetDirectory, temporaryPath)) {
    throw new VendorPatchError()
  }

  let descriptor
  let temporaryOwned = false
  try {
    descriptor = openSync(
      temporaryPath,
      constants.O_CREAT |
        constants.O_EXCL |
        constants.O_WRONLY |
        (constants.O_NOFOLLOW ?? 0),
      stats.mode & 0o777,
    )
    temporaryOwned = true
    fchmodSync(descriptor, stats.mode & 0o777)
    writeFileSync(descriptor, patched, 'utf8')
    fsyncSync(descriptor)
    closeSync(descriptor)
    descriptor = undefined
    renameSync(temporaryPath, normalized)
    temporaryOwned = false
    chmodSync(normalized, stats.mode & 0o777)
    fsyncDirectory(targetDirectory)
  } catch {
    if (descriptor !== undefined) closeSync(descriptor)
    if (temporaryOwned) rmSync(temporaryPath, { force: true })
    throw new VendorPatchError()
  }

  const installed = readFileSync(
    checkedRegularFile(normalized, MAXIMUM_TARGET_BYTES).normalized,
    'utf8',
  )
  assertPatchedSource(installed)
  return 'applied'
}

try {
  const mode =
    process.argv.length === 3 ? process.argv[2] : undefined
  if (!['--apply', '--verify'].includes(mode)) {
    throw new VendorPatchError()
  }
  const outcome = applyOrVerify(mode)
  process.stdout.write(
    `${JSON.stringify({
      event: 'country_outage_pi_response_model_vendor_patch',
      patchId: PATCH_ID,
      outcome,
      patchedSourceSha256: PATCHED_SHA256,
    })}\n`,
  )
} catch {
  process.stderr.write(
    `${JSON.stringify({
      event: 'country_outage_pi_response_model_vendor_patch_failed',
      code: 'pi_response_model_vendor_patch_invalid',
    })}\n`,
  )
  process.exitCode = 1
}
