import { createHash } from 'node:crypto'
import { lstatSync, readFileSync, realpathSync } from 'node:fs'
import { dirname, isAbsolute, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { VERSION as INSTALLED_PI_VERSION } from '@earendil-works/pi-coding-agent'

import { FORMAL_PI_VERSION } from './formal-model-runtime.js'
import { STATIC_RESOURCE_LOADER_ID } from './static-resource-loader.js'

export const COUNTRY_OUTAGE_DEPENDENCY_SECURITY_ATTESTATION_SCHEMA_VERSION =
  'country_outage_dependency_security_attestation_v1' as const

const EXPECTED_ATTESTATION_ID =
  'country-outage-pi-0.84.1-production-audit-20260811-v1'
const EXPECTED_PI_PACKAGE = '@earendil-works/pi-coding-agent'
const EXPECTED_LOCKFILE_SHA256 =
  'eb63baab11ae6714b447273501de76ad4b1e3e8c7a8de2f0c60402ea22d90cf6'
const EXPECTED_BRACE_EXPANSION = 'brace-expansion@5.0.9'
const EXPECTED_UNDICI = 'undici@8.9.0'
const MAX_RESOURCE_BYTES = 64 * 1024
const MAX_PACKAGE_BYTES = 256 * 1024
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$/

const EXPECTED_RESPONSE_MODEL_VENDOR_PATCH = Object.freeze({
  patchId: 'pi-ai-openai-completions-response-model-v1',
  targetPackage: '@earendil-works/pi-ai',
  targetVersion: '0.84.1',
  targetRelativePathFromCodingAgent:
    'node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js',
  upstreamSourceSha256:
    '727d744f20985f667151e8ecee3ad30af388d9d66d91a92d0fb9ad3261da4363',
  patchedSourceSha256:
    '9bb5badc07dc1f073e094743acf4b81390601ae5bead8c35f15c54f7f0bc0504',
  patchArtifactSha256:
    'a7e89d8dae4ddb8a3aa2548153c2e0e68f57fd7b8102bdde10ecc8d297836c28',
  patchManifestSha256:
    'ba5f5bceae09c868285926d0b63c562f88168211284c52036aa62d8346bab1ad',
  sameNameResponseModelPreserved: true,
  applicationMode: 'postinstall_exact_hash_replacement_v1',
} as const)

const EXPECTED_REVALIDATION_TRIGGERS = Object.freeze([
  'package_lock_changed',
  'pi_version_changed',
  'production_dependency_graph_changed',
  'formal_path_changed',
  'capability_scope_changed',
  'vendor_patch_drift',
  'vendor_patch_no_longer_required',
] as const)

interface VulnerabilityCounts {
  info: 0
  low: 0
  moderate: 0
  high: 0
  critical: 0
  total: 0
}

export interface CountryOutageDependencySecurityAttestation {
  schemaVersion:
    typeof COUNTRY_OUTAGE_DEPENDENCY_SECURITY_ATTESTATION_SCHEMA_VERSION
  attestationId: string
  status: 'verified'
  verifiedAt: string
  package: {
    name: typeof EXPECTED_PI_PACKAGE
    version: typeof FORMAL_PI_VERSION
    lockfileSha256: typeof EXPECTED_LOCKFILE_SHA256
  }
  productionAudit: {
    command: 'npm audit --omit=dev --audit-level=high'
    exitCode: 0
    vulnerabilities: VulnerabilityCounts
  }
  installedComponents: {
    braceExpansion: typeof EXPECTED_BRACE_EXPANSION
    undici: typeof EXPECTED_UNDICI
  }
  runtimeConstraints: {
    resourceLoaderId: typeof STATIC_RESOURCE_LOADER_ID
    packageManagerResolutionEnabled: false
    modelResolverEnabled: false
    externalGlobEnabled: false
    capabilityExpansionAllowed: false
    responseModelVendorPatch:
      typeof EXPECTED_RESPONSE_MODEL_VENDOR_PATCH
  }
  revalidationTriggers: readonly (typeof EXPECTED_REVALIDATION_TRIGGERS)[number][]
}

export interface VerifiedCountryOutageDependencySecurityAttestation {
  attestation: CountryOutageDependencySecurityAttestation
  audit: {
    attestationId: string
    verifiedAt: string
    lockfileSha256: string
    status: 'verified'
  }
}

export type CountryOutageDependencySecurityAttestationErrorCode =
  | 'dependency_security_attestation_invalid'
  | 'dependency_security_attestation_mismatch'

export class CountryOutageDependencySecurityAttestationError extends Error {
  constructor(
    readonly code: CountryOutageDependencySecurityAttestationErrorCode,
  ) {
    super(
      code === 'dependency_security_attestation_invalid'
        ? 'Pi 生产依赖安全证明无效'
        : 'Pi 生产依赖安全证明与当前不可变依赖不一致',
    )
    this.name = 'CountryOutageDependencySecurityAttestationError'
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function exactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  return JSON.stringify(Object.keys(value).sort()) ===
    JSON.stringify([...keys].sort())
}

function exactArray(
  actual: readonly unknown[],
  expected: readonly string[],
): boolean {
  return actual.length === expected.length &&
    actual.every((value, index) => value === expected[index])
}

function sha256File(path: string, maximumBytes: number): string {
  const normalized = resolve(path)
  const stats = lstatSync(normalized)
  if (
    !stats.isFile() ||
    stats.isSymbolicLink() ||
    stats.size <= 0 ||
    stats.size > maximumBytes ||
    realpathSync(normalized) !== normalized
  ) {
    throw new Error('invalid immutable file')
  }
  return createHash('sha256')
    .update(readFileSync(normalized))
    .digest('hex')
}

function sidecarRoot(): string {
  const moduleDirectory = dirname(fileURLToPath(import.meta.url))
  const candidates = [
    resolve(moduleDirectory, '../..'),
    resolve(moduleDirectory, '../../..'),
  ]
  const selected = candidates.find((candidate) => {
    try {
      const packageJson = JSON.parse(
        readFileSync(resolve(candidate, 'package.json'), 'utf8'),
      ) as unknown
      return isObject(packageJson) &&
        packageJson.name === 'domeye-country-outage-agent-sidecar'
    } catch {
      return false
    }
  })
  if (!selected) throw new Error('sidecar root unavailable')
  return selected
}

function installedPiRoot(): string {
  const entry = realpathSync(
    fileURLToPath(import.meta.resolve('@earendil-works/pi-coding-agent')),
  )
  return resolve(dirname(entry), '..')
}

function installedComponentIdentity(
  relativePath: string,
  expectedName: string,
): string {
  const metadataPath = resolve(installedPiRoot(), relativePath)
  const metadata = JSON.parse(
    readFileSync(metadataPath, 'utf8'),
  ) as unknown
  if (
    !isObject(metadata) ||
    metadata.name !== expectedName ||
    typeof metadata.version !== 'string'
  ) {
    throw new Error('component identity unavailable')
  }
  return `${metadata.name}@${metadata.version}`
}

function validateVendorPatch(): void {
  const piRoot = installedPiRoot()
  const piAiMetadata = JSON.parse(
    readFileSync(
      resolve(piRoot, 'node_modules/@earendil-works/pi-ai/package.json'),
      'utf8',
    ),
  ) as unknown
  if (
    !isObject(piAiMetadata) ||
    piAiMetadata.name !== EXPECTED_RESPONSE_MODEL_VENDOR_PATCH.targetPackage ||
    piAiMetadata.version !== EXPECTED_RESPONSE_MODEL_VENDOR_PATCH.targetVersion
  ) {
    throw new Error('pi-ai identity mismatch')
  }
  if (
    sha256File(
      resolve(
        piRoot,
        EXPECTED_RESPONSE_MODEL_VENDOR_PATCH.targetRelativePathFromCodingAgent,
      ),
      MAX_PACKAGE_BYTES,
    ) !== EXPECTED_RESPONSE_MODEL_VENDOR_PATCH.patchedSourceSha256
  ) {
    throw new Error('patched adapter mismatch')
  }
  const root = sidecarRoot()
  if (
    sha256File(
      resolve(
        root,
        'resources/vendor-patches/pi-ai-openai-completions-response-model-v1.json',
      ),
      MAX_RESOURCE_BYTES,
    ) !== EXPECTED_RESPONSE_MODEL_VENDOR_PATCH.patchManifestSha256 ||
    sha256File(
      resolve(
        root,
        'vendor-patches/pi-ai-0.84.1-openai-completions-response-model-v1.patch',
      ),
      MAX_RESOURCE_BYTES,
    ) !== EXPECTED_RESPONSE_MODEL_VENDOR_PATCH.patchArtifactSha256
  ) {
    throw new Error('vendor patch evidence mismatch')
  }
}

function parseAttestation(
  value: unknown,
): CountryOutageDependencySecurityAttestation {
  if (
    !isObject(value) ||
    !exactKeys(value, [
      'schemaVersion',
      'attestationId',
      'status',
      'verifiedAt',
      'package',
      'productionAudit',
      'installedComponents',
      'runtimeConstraints',
      'revalidationTriggers',
    ]) ||
    value.schemaVersion !==
      COUNTRY_OUTAGE_DEPENDENCY_SECURITY_ATTESTATION_SCHEMA_VERSION ||
    typeof value.attestationId !== 'string' ||
    !SAFE_ID.test(value.attestationId) ||
    value.status !== 'verified' ||
    typeof value.verifiedAt !== 'string' ||
    !Number.isFinite(Date.parse(value.verifiedAt)) ||
    !isObject(value.package) ||
    !exactKeys(value.package, ['name', 'version', 'lockfileSha256']) ||
    !isObject(value.productionAudit) ||
    !exactKeys(value.productionAudit, [
      'command',
      'exitCode',
      'vulnerabilities',
    ]) ||
    !isObject(value.productionAudit.vulnerabilities) ||
    !exactKeys(value.productionAudit.vulnerabilities, [
      'info',
      'low',
      'moderate',
      'high',
      'critical',
      'total',
    ]) ||
    !isObject(value.installedComponents) ||
    !exactKeys(value.installedComponents, ['braceExpansion', 'undici']) ||
    !isObject(value.runtimeConstraints) ||
    !exactKeys(value.runtimeConstraints, [
      'resourceLoaderId',
      'packageManagerResolutionEnabled',
      'modelResolverEnabled',
      'externalGlobEnabled',
      'capabilityExpansionAllowed',
      'responseModelVendorPatch',
    ]) ||
    !isObject(value.runtimeConstraints.responseModelVendorPatch) ||
    !Array.isArray(value.revalidationTriggers)
  ) {
    throw new CountryOutageDependencySecurityAttestationError(
      'dependency_security_attestation_invalid',
    )
  }
  return value as unknown as CountryOutageDependencySecurityAttestation
}

export function validateCountryOutageDependencySecurityAttestation(
  value: unknown,
): VerifiedCountryOutageDependencySecurityAttestation {
  const attestation = parseAttestation(value)
  const counts = attestation.productionAudit.vulnerabilities
  try {
    if (
      attestation.attestationId !== EXPECTED_ATTESTATION_ID ||
      attestation.package.name !== EXPECTED_PI_PACKAGE ||
      attestation.package.version !== FORMAL_PI_VERSION ||
      INSTALLED_PI_VERSION !== FORMAL_PI_VERSION ||
      attestation.package.lockfileSha256 !== EXPECTED_LOCKFILE_SHA256 ||
      sha256File(resolve(sidecarRoot(), 'package-lock.json'), MAX_PACKAGE_BYTES) !==
        EXPECTED_LOCKFILE_SHA256 ||
      attestation.productionAudit.command !==
        'npm audit --omit=dev --audit-level=high' ||
      attestation.productionAudit.exitCode !== 0 ||
      Object.values(counts).some((count) => count !== 0) ||
      attestation.installedComponents.braceExpansion !==
        EXPECTED_BRACE_EXPANSION ||
      installedComponentIdentity(
        'node_modules/brace-expansion/package.json',
        'brace-expansion',
      ) !== EXPECTED_BRACE_EXPANSION ||
      attestation.installedComponents.undici !== EXPECTED_UNDICI ||
      installedComponentIdentity('node_modules/undici/package.json', 'undici') !==
        EXPECTED_UNDICI ||
      attestation.runtimeConstraints.resourceLoaderId !==
        STATIC_RESOURCE_LOADER_ID ||
      attestation.runtimeConstraints.packageManagerResolutionEnabled !== false ||
      attestation.runtimeConstraints.modelResolverEnabled !== false ||
      attestation.runtimeConstraints.externalGlobEnabled !== false ||
      attestation.runtimeConstraints.capabilityExpansionAllowed !== false ||
      JSON.stringify(attestation.runtimeConstraints.responseModelVendorPatch) !==
        JSON.stringify(EXPECTED_RESPONSE_MODEL_VENDOR_PATCH) ||
      !exactArray(
        attestation.revalidationTriggers,
        EXPECTED_REVALIDATION_TRIGGERS,
      )
    ) {
      throw new Error('attestation mismatch')
    }
    validateVendorPatch()
  } catch {
    throw new CountryOutageDependencySecurityAttestationError(
      'dependency_security_attestation_mismatch',
    )
  }
  return Object.freeze({
    attestation: Object.freeze(attestation),
    audit: Object.freeze({
      attestationId: attestation.attestationId,
      verifiedAt: attestation.verifiedAt,
      lockfileSha256: attestation.package.lockfileSha256,
      status: 'verified' as const,
    }),
  })
}

function defaultAttestationPath(): string {
  const root = sidecarRoot()
  return resolve(
    root,
    'resources/dependency-security/pi-0.84.1-production-dependency-attestation-v1.json',
  )
}

export function loadCountryOutageDependencySecurityAttestation(
  options: { path?: string, now?: Date } = {},
): VerifiedCountryOutageDependencySecurityAttestation {
  const path = options.path ?? defaultAttestationPath()
  if (!isAbsolute(path)) {
    throw new CountryOutageDependencySecurityAttestationError(
      'dependency_security_attestation_invalid',
    )
  }
  try {
    const normalized = resolve(path)
    const stats = lstatSync(normalized)
    if (
      !stats.isFile() ||
      stats.isSymbolicLink() ||
      stats.size <= 0 ||
      stats.size > MAX_RESOURCE_BYTES ||
      realpathSync(normalized) !== normalized
    ) {
      throw new Error('invalid attestation resource')
    }
    return validateCountryOutageDependencySecurityAttestation(
      JSON.parse(readFileSync(normalized, 'utf8')) as unknown,
    )
  } catch (error) {
    if (error instanceof CountryOutageDependencySecurityAttestationError) {
      throw error
    }
    throw new CountryOutageDependencySecurityAttestationError(
      'dependency_security_attestation_invalid',
    )
  }
}
