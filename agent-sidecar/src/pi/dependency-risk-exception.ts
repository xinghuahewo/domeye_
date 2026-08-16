import { createHash } from 'node:crypto'
import {
  lstatSync,
  readFileSync,
  realpathSync,
} from 'node:fs'
import { dirname, isAbsolute, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { VERSION as INSTALLED_PI_VERSION } from '@earendil-works/pi-coding-agent'

import { COUNTRY_OUTAGE_TOOL_NAMES } from './country-outage-tools.js'
import { FORMAL_PI_VERSION } from './formal-model-runtime.js'
import { STATIC_RESOURCE_LOADER_ID } from './static-resource-loader.js'

export const COUNTRY_OUTAGE_DEPENDENCY_RISK_EXCEPTION_SCHEMA_VERSION =
  'country_outage_dependency_risk_exception_v2' as const

const EXPECTED_EXCEPTION_ID =
  'country-outage-pi-ghsa-mh99-v99m-4gvg-20260812-v2'
const EXPECTED_APPROVED_AT = '2026-07-29T03:03:08Z'
const EXPECTED_EXPIRES_AT = '2026-08-12T16:00:00Z'
const EXPECTED_ADVISORY = 'GHSA-mh99-v99m-4gvg'
const EXPECTED_COMPONENT = 'brace-expansion@5.0.7'
const EXPECTED_PI_PACKAGE = '@earendil-works/pi-coding-agent'
const EXPECTED_SKILL_NAME = 'country-outage-report'
const MAX_EXCEPTION_FILE_BYTES = 32_768
const MAX_ADAPTER_FILE_BYTES = 128 * 1024
const SAFE_EXCEPTION_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$/
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
const EXPECTED_REEVALUATION_TRIGGERS = Object.freeze([
  'pi_fixed_version_available',
  'formal_path_changed',
  'capability_scope_changed',
  'vendor_patch_drift',
  'vendor_patch_no_longer_required',
] as const)

export const FORMAL_COUNTRY_OUTAGE_RISK_EXCEPTION_CONSTRAINTS =
  Object.freeze({
    advisory: EXPECTED_ADVISORY,
    component: EXPECTED_COMPONENT,
    piPackage: EXPECTED_PI_PACKAGE,
    piVersion: FORMAL_PI_VERSION,
    resourceLoaderId: STATIC_RESOURCE_LOADER_ID,
    packageManagerResolutionEnabled: false,
    modelResolverEnabled: false,
    externalGlobEnabled: false,
    skillName: EXPECTED_SKILL_NAME,
    allowedTools: Object.freeze([...COUNTRY_OUTAGE_TOOL_NAMES]),
    capabilityExpansionAllowed: false,
    responseModelVendorPatch:
      EXPECTED_RESPONSE_MODEL_VENDOR_PATCH,
    reevaluationTriggers: EXPECTED_REEVALUATION_TRIGGERS,
  } as const)

export interface CountryOutageDependencyRiskException {
  schemaVersion:
    typeof COUNTRY_OUTAGE_DEPENDENCY_RISK_EXCEPTION_SCHEMA_VERSION
  exceptionId: string
  status: 'approved'
  approvedAt: string
  expiresAt: string
  risk: {
    advisory: typeof EXPECTED_ADVISORY
    component: typeof EXPECTED_COMPONENT
    piPackage: typeof EXPECTED_PI_PACKAGE
    piVersion: typeof FORMAL_PI_VERSION
  }
  constraints: {
    resourceLoaderId: typeof STATIC_RESOURCE_LOADER_ID
    packageManagerResolutionEnabled: false
    modelResolverEnabled: false
    externalGlobEnabled: false
    skillName: typeof EXPECTED_SKILL_NAME
    allowedTools: readonly (typeof COUNTRY_OUTAGE_TOOL_NAMES)[number][]
    capabilityExpansionAllowed: false
    responseModelVendorPatch:
      typeof EXPECTED_RESPONSE_MODEL_VENDOR_PATCH
  }
  reevaluationTriggers: readonly (typeof EXPECTED_REEVALUATION_TRIGGERS)[number][]
}

export interface ActiveCountryOutageDependencyRiskException {
  exception: CountryOutageDependencyRiskException
  audit: {
    exceptionId: string
    expiresAt: string
    status: 'active'
  }
}

export type CountryOutageDependencyRiskExceptionErrorCode =
  | 'risk_exception_invalid'
  | 'risk_exception_constraint_mismatch'
  | 'risk_exception_not_yet_active'
  | 'risk_exception_expired'

const SAFE_ERROR_MESSAGES: Record<
  CountryOutageDependencyRiskExceptionErrorCode,
  string
> = {
  risk_exception_invalid: 'Pi 依赖风险例外资源无效',
  risk_exception_constraint_mismatch:
    'Pi 依赖风险例外与当前正式路径约束不一致',
  risk_exception_not_yet_active: 'Pi 依赖风险例外尚未生效',
  risk_exception_expired: 'Pi 依赖风险例外已经到期',
}

export class CountryOutageDependencyRiskExceptionError extends Error {
  constructor(
    readonly code: CountryOutageDependencyRiskExceptionErrorCode,
  ) {
    super(SAFE_ERROR_MESSAGES[code])
    this.name = 'CountryOutageDependencyRiskExceptionError'
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function hasExactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return JSON.stringify(actual) === JSON.stringify(expected)
}

function isIsoTimestamp(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(value) &&
    Number.isFinite(Date.parse(value))
  )
}

function parseException(
  value: unknown,
): CountryOutageDependencyRiskException {
  if (
    !isObject(value) ||
    !hasExactKeys(value, [
      'schemaVersion',
      'exceptionId',
      'status',
      'approvedAt',
      'expiresAt',
      'risk',
      'constraints',
      'reevaluationTriggers',
    ]) ||
    value.schemaVersion !==
      COUNTRY_OUTAGE_DEPENDENCY_RISK_EXCEPTION_SCHEMA_VERSION ||
    typeof value.exceptionId !== 'string' ||
    !SAFE_EXCEPTION_ID.test(value.exceptionId) ||
    value.status !== 'approved' ||
    !isIsoTimestamp(value.approvedAt) ||
    !isIsoTimestamp(value.expiresAt) ||
    Date.parse(value.expiresAt) <= Date.parse(value.approvedAt) ||
    !isObject(value.risk) ||
    !hasExactKeys(value.risk, [
      'advisory',
      'component',
      'piPackage',
      'piVersion',
    ]) ||
    !isObject(value.constraints) ||
    !hasExactKeys(value.constraints, [
      'resourceLoaderId',
      'packageManagerResolutionEnabled',
      'modelResolverEnabled',
      'externalGlobEnabled',
      'skillName',
      'allowedTools',
      'capabilityExpansionAllowed',
      'responseModelVendorPatch',
    ]) ||
    !Array.isArray(value.constraints.allowedTools) ||
    !value.constraints.allowedTools.every(
      (item) => typeof item === 'string',
    ) ||
    !isObject(value.constraints.responseModelVendorPatch) ||
    !hasExactKeys(value.constraints.responseModelVendorPatch, [
      'patchId',
      'targetPackage',
      'targetVersion',
      'targetRelativePathFromCodingAgent',
      'upstreamSourceSha256',
      'patchedSourceSha256',
      'patchArtifactSha256',
      'patchManifestSha256',
      'sameNameResponseModelPreserved',
      'applicationMode',
    ]) ||
    !Array.isArray(value.reevaluationTriggers) ||
    !value.reevaluationTriggers.every(
      (item) => typeof item === 'string',
    )
  ) {
    throw new CountryOutageDependencyRiskExceptionError(
      'risk_exception_invalid',
    )
  }
  return Object.freeze({
    schemaVersion:
      COUNTRY_OUTAGE_DEPENDENCY_RISK_EXCEPTION_SCHEMA_VERSION,
    exceptionId: value.exceptionId,
    status: 'approved',
    approvedAt: value.approvedAt,
    expiresAt: value.expiresAt,
    risk: Object.freeze({
      advisory: value.risk.advisory,
      component: value.risk.component,
      piPackage: value.risk.piPackage,
      piVersion: value.risk.piVersion,
    }) as CountryOutageDependencyRiskException['risk'],
    constraints: Object.freeze({
      resourceLoaderId: value.constraints.resourceLoaderId,
      packageManagerResolutionEnabled:
        value.constraints.packageManagerResolutionEnabled,
      modelResolverEnabled: value.constraints.modelResolverEnabled,
      externalGlobEnabled: value.constraints.externalGlobEnabled,
      skillName: value.constraints.skillName,
      allowedTools: Object.freeze([
        ...value.constraints.allowedTools,
      ]),
      capabilityExpansionAllowed:
        value.constraints.capabilityExpansionAllowed,
      responseModelVendorPatch: Object.freeze({
        patchId:
          value.constraints.responseModelVendorPatch.patchId,
        targetPackage:
          value.constraints.responseModelVendorPatch.targetPackage,
        targetVersion:
          value.constraints.responseModelVendorPatch.targetVersion,
        targetRelativePathFromCodingAgent:
          value.constraints.responseModelVendorPatch
            .targetRelativePathFromCodingAgent,
        upstreamSourceSha256:
          value.constraints.responseModelVendorPatch
            .upstreamSourceSha256,
        patchedSourceSha256:
          value.constraints.responseModelVendorPatch
            .patchedSourceSha256,
        patchArtifactSha256:
          value.constraints.responseModelVendorPatch
            .patchArtifactSha256,
        patchManifestSha256:
          value.constraints.responseModelVendorPatch
            .patchManifestSha256,
        sameNameResponseModelPreserved:
          value.constraints.responseModelVendorPatch
            .sameNameResponseModelPreserved,
        applicationMode:
          value.constraints.responseModelVendorPatch
            .applicationMode,
      }),
    }) as CountryOutageDependencyRiskException['constraints'],
    reevaluationTriggers: Object.freeze([
      ...value.reevaluationTriggers,
    ]) as CountryOutageDependencyRiskException['reevaluationTriggers'],
  })
}

function installedBraceExpansionIdentity(): string {
  try {
    const piEntry = realpathSync(
      fileURLToPath(
        import.meta.resolve('@earendil-works/pi-coding-agent'),
      ),
    )
    const piRoot = resolve(dirname(piEntry), '..')
    const componentPath = resolve(
      piRoot,
      'node_modules/brace-expansion/package.json',
    )
    const stats = lstatSync(componentPath)
    if (
      !stats.isFile() ||
      stats.isSymbolicLink() ||
      stats.size <= 0 ||
      stats.size > MAX_EXCEPTION_FILE_BYTES ||
      realpathSync(componentPath) !== componentPath
    ) {
      throw new Error('invalid component metadata')
    }
    const metadata = JSON.parse(
      readFileSync(componentPath, 'utf8'),
    ) as unknown
    if (
      !isObject(metadata) ||
      metadata.name !== 'brace-expansion' ||
      typeof metadata.version !== 'string'
    ) {
      throw new Error('invalid component identity')
    }
    return `${metadata.name}@${metadata.version}`
  } catch {
    throw new CountryOutageDependencyRiskExceptionError(
      'risk_exception_constraint_mismatch',
    )
  }
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
    throw new Error('invalid vendor patch file')
  }
  return createHash('sha256')
    .update(readFileSync(normalized))
    .digest('hex')
}

function installedResponseModelVendorPatchIdentity():
typeof EXPECTED_RESPONSE_MODEL_VENDOR_PATCH {
  try {
    const piEntry = realpathSync(
      fileURLToPath(
        import.meta.resolve('@earendil-works/pi-coding-agent'),
      ),
    )
    const piRoot = resolve(dirname(piEntry), '..')
    const piAiMetadataPath = resolve(
      piRoot,
      'node_modules/@earendil-works/pi-ai/package.json',
    )
    const piAiMetadata = JSON.parse(
      readFileSync(piAiMetadataPath, 'utf8'),
    ) as unknown
    if (
      !isObject(piAiMetadata) ||
      piAiMetadata.name !==
        EXPECTED_RESPONSE_MODEL_VENDOR_PATCH.targetPackage ||
      piAiMetadata.version !==
        EXPECTED_RESPONSE_MODEL_VENDOR_PATCH.targetVersion
    ) {
      throw new Error('invalid pi-ai identity')
    }
    const adapterPath = resolve(
      piRoot,
      EXPECTED_RESPONSE_MODEL_VENDOR_PATCH
        .targetRelativePathFromCodingAgent,
    )
    if (
      sha256File(adapterPath, MAX_ADAPTER_FILE_BYTES) !==
      EXPECTED_RESPONSE_MODEL_VENDOR_PATCH.patchedSourceSha256
    ) {
      throw new Error('vendor patch source mismatch')
    }

    const moduleDirectory = dirname(fileURLToPath(import.meta.url))
    const sidecarRoots = [
      resolve(moduleDirectory, '../..'),
      resolve(moduleDirectory, '../../..'),
    ]
    const sidecarRoot = sidecarRoots.find((candidate) => {
      try {
        const metadata = JSON.parse(
          readFileSync(resolve(candidate, 'package.json'), 'utf8'),
        ) as unknown
        return (
          isObject(metadata) &&
          metadata.name === 'domeye-country-outage-agent-sidecar'
        )
      } catch {
        return false
      }
    })
    if (!sidecarRoot) throw new Error('sidecar root unavailable')
    const manifestPath = resolve(
      sidecarRoot,
      'resources/vendor-patches/pi-ai-openai-completions-response-model-v1.json',
    )
    const patchPath = resolve(
      sidecarRoot,
      'vendor-patches/pi-ai-0.84.1-openai-completions-response-model-v1.patch',
    )
    if (
      sha256File(manifestPath, MAX_EXCEPTION_FILE_BYTES) !==
        EXPECTED_RESPONSE_MODEL_VENDOR_PATCH.patchManifestSha256 ||
      sha256File(patchPath, MAX_EXCEPTION_FILE_BYTES) !==
        EXPECTED_RESPONSE_MODEL_VENDOR_PATCH.patchArtifactSha256
    ) {
      throw new Error('vendor patch evidence mismatch')
    }
    return EXPECTED_RESPONSE_MODEL_VENDOR_PATCH
  } catch {
    throw new CountryOutageDependencyRiskExceptionError(
      'risk_exception_constraint_mismatch',
    )
  }
}

function exactStringArray(
  actual: readonly string[],
  expected: readonly string[],
): boolean {
  return JSON.stringify(actual) === JSON.stringify(expected)
}

function assertConstraints(
  value: CountryOutageDependencyRiskException,
): void {
  const expected = FORMAL_COUNTRY_OUTAGE_RISK_EXCEPTION_CONSTRAINTS
  if (
    value.exceptionId !== EXPECTED_EXCEPTION_ID ||
    value.approvedAt !== EXPECTED_APPROVED_AT ||
    value.expiresAt !== EXPECTED_EXPIRES_AT ||
    value.risk.advisory !== expected.advisory ||
    value.risk.component !== expected.component ||
    value.risk.piPackage !== expected.piPackage ||
    value.risk.piVersion !== expected.piVersion ||
    INSTALLED_PI_VERSION !== expected.piVersion ||
    installedBraceExpansionIdentity() !== expected.component ||
    value.constraints.resourceLoaderId !== expected.resourceLoaderId ||
    value.constraints.packageManagerResolutionEnabled !==
      expected.packageManagerResolutionEnabled ||
    value.constraints.modelResolverEnabled !==
      expected.modelResolverEnabled ||
    value.constraints.externalGlobEnabled !==
      expected.externalGlobEnabled ||
    value.constraints.skillName !== expected.skillName ||
    !exactStringArray(
      value.constraints.allowedTools,
      expected.allowedTools,
    ) ||
    value.constraints.capabilityExpansionAllowed !==
      expected.capabilityExpansionAllowed ||
    JSON.stringify(value.constraints.responseModelVendorPatch) !==
      JSON.stringify(expected.responseModelVendorPatch) ||
    installedResponseModelVendorPatchIdentity().patchedSourceSha256 !==
      expected.responseModelVendorPatch.patchedSourceSha256 ||
    !exactStringArray(
      value.reevaluationTriggers,
      expected.reevaluationTriggers,
    )
  ) {
    throw new CountryOutageDependencyRiskExceptionError(
      'risk_exception_constraint_mismatch',
    )
  }
}

function defaultRiskExceptionPath(): string {
  const moduleDirectory = dirname(fileURLToPath(import.meta.url))
  const candidates = [
    resolve(
      moduleDirectory,
      '../../resources/risk-exceptions/country-outage-pi-ghsa-mh99-v99m-4gvg-v2.json',
    ),
    resolve(
      moduleDirectory,
      '../../../resources/risk-exceptions/country-outage-pi-ghsa-mh99-v99m-4gvg-v2.json',
    ),
  ]
  const selected = candidates.find((candidate) => {
    try {
      return lstatSync(candidate).isFile()
    } catch {
      return false
    }
  })
  if (!selected) {
    throw new CountryOutageDependencyRiskExceptionError(
      'risk_exception_invalid',
    )
  }
  return selected
}

function readRiskExceptionFile(path: string): unknown {
  const normalized = resolve(path)
  if (!isAbsolute(path)) {
    throw new CountryOutageDependencyRiskExceptionError(
      'risk_exception_invalid',
    )
  }
  try {
    const stats = lstatSync(normalized)
    if (
      !stats.isFile() ||
      stats.isSymbolicLink() ||
      stats.size <= 0 ||
      stats.size > MAX_EXCEPTION_FILE_BYTES ||
      realpathSync(normalized) !== normalized
    ) {
      throw new Error('invalid risk exception resource')
    }
    return JSON.parse(readFileSync(normalized, 'utf8')) as unknown
  } catch (error) {
    if (error instanceof CountryOutageDependencyRiskExceptionError) {
      throw error
    }
    throw new CountryOutageDependencyRiskExceptionError(
      'risk_exception_invalid',
    )
  }
}

export function validateCountryOutageDependencyRiskException(
  value: unknown,
  now: Date,
): ActiveCountryOutageDependencyRiskException {
  if (!Number.isFinite(now.valueOf())) {
    throw new CountryOutageDependencyRiskExceptionError(
      'risk_exception_invalid',
    )
  }
  const exception = parseException(value)
  assertConstraints(exception)
  if (now.valueOf() < Date.parse(exception.approvedAt)) {
    throw new CountryOutageDependencyRiskExceptionError(
      'risk_exception_not_yet_active',
    )
  }
  if (now.valueOf() >= Date.parse(exception.expiresAt)) {
    throw new CountryOutageDependencyRiskExceptionError(
      'risk_exception_expired',
    )
  }
  return Object.freeze({
    exception,
    audit: Object.freeze({
      exceptionId: exception.exceptionId,
      expiresAt: exception.expiresAt,
      status: 'active',
    }),
  })
}

export function loadCountryOutageDependencyRiskException(
  options: {
    path?: string
    now?: Date
  } = {},
): ActiveCountryOutageDependencyRiskException {
  const path = options.path ?? defaultRiskExceptionPath()
  return validateCountryOutageDependencyRiskException(
    readRiskExceptionFile(path),
    options.now ?? new Date(),
  )
}
