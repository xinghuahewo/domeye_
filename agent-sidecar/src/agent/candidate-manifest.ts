import { createHash } from 'node:crypto'
import { lstat, readFile, realpath } from 'node:fs/promises'
import { isAbsolute, posix, relative, resolve, sep } from 'node:path'

import { Type, type Static } from 'typebox'
import { Check } from 'typebox/value'

import { canonicalJsonSha256 } from '../shared/deterministic-json.js'
import {
  DomeyeDataIdentitySchema,
  DomeyeExecutionBindingSchema,
} from './contracts.js'
import type { DomeyeFirstSliceCandidateBinding } from './first-slice-runtime.js'
import type { DomeyePiModelIdentity } from './pi-interactive-agent-loop.js'

const Identifier = Type.String({ minLength: 1, maxLength: 256 })
const Sha256 = Type.String({ pattern: '^sha256:[a-f0-9]{64}$' })
const BaseCommit = Type.String({ pattern: '^[a-f0-9]{40}$' })
const SourcePath = Type.String({ minLength: 1, maxLength: 512 })

const ANCHOR_SOURCE_PATH =
  'docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md'
const MODEL_RESOURCE_SOURCE_PATH =
  'contracts/agent/domeye-first-vertical-slice/v1/model-runtime.json'
const MACHINE_CONTRACT_SOURCE_PATH =
  'agent-sidecar/src/agent/contracts.ts'
const CAPABILITY_IMPLEMENTATION_SOURCE_PATH =
  'agent-sidecar/src/agent/capability-execution.ts'
const PATCHED_PROVIDER_SOURCE_PATH =
  'agent-sidecar/node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js'
const REQUIRED_FIXED_SOURCE_PATHS = Object.freeze([
  ANCHOR_SOURCE_PATH,
  MODEL_RESOURCE_SOURCE_PATH,
  'agent-sidecar/package.json',
  'agent-sidecar/package-lock.json',
  'agent-sidecar/tsconfig.json',
  'agent-sidecar/scripts/generate_first_slice_candidate_manifest.mjs',
  'agent-sidecar/scripts/apply_pi_response_model_patch.mjs',
  'agent-sidecar/resources/vendor-patches/pi-ai-openai-completions-response-model-v1.json',
  'agent-sidecar/vendor-patches/pi-ai-0.84.1-openai-completions-response-model-v1.patch',
  PATCHED_PROVIDER_SOURCE_PATH,
] as const)
const REQUIRED_RUNTIME_SOURCE_PATHS = Object.freeze([
  'agent-sidecar/src/cli/serve-interactive-agent.ts',
  MACHINE_CONTRACT_SOURCE_PATH,
  CAPABILITY_IMPLEMENTATION_SOURCE_PATH,
  'agent-sidecar/src/formal-runtime-limits.ts',
  'agent-sidecar/src/pi/country-outage-skill-bundle.ts',
  'agent-sidecar/src/pi/formal-model-runtime.ts',
  'agent-sidecar/src/cli/sidecar-security.ts',
] as const)
const RUNTIME_SOURCE_PREFIX = 'agent-sidecar/src/'
const RUNTIME_DIST_PREFIX = 'agent-sidecar/dist/src/'

export const DomeyeFirstSliceModelBindingPayloadSchema = Type.Object({
  candidate_id: Identifier,
  resource_sha256: Sha256,
  provider: Identifier,
  model: Identifier,
  model_version: Identifier,
  expected_response_model: Identifier,
  api: Type.Literal('openai-completions'),
  base_url: Type.String({ minLength: 1, maxLength: 2_048 }),
  maximum_output_tokens: Type.Integer({ minimum: 1 }),
  thinking_level: Type.Union([
    Type.Literal('off'),
    Type.Literal('minimal'),
    Type.Literal('low'),
    Type.Literal('medium'),
    Type.Literal('high'),
    Type.Literal('xhigh'),
  ]),
  pi_version: Type.Literal('0.84.1'),
}, { additionalProperties: false })

export type DomeyeFirstSliceModelBindingPayload = Static<
  typeof DomeyeFirstSliceModelBindingPayloadSchema
> & DomeyePiModelIdentity

const BudgetPolicySchema = Type.Object({
  model_api_attempt_limit: Type.Literal(10),
  approved_action_limit: Type.Literal(2),
  cost_policy: Type.Literal('audit_only'),
  monetary_limit_usd: Type.Null(),
}, { additionalProperties: false })

const PolicySchema = Type.Object({
  policy_id: Identifier,
  policy_digest: Sha256,
  state: Type.Literal('active'),
  allowed_capability_ids: Type.Tuple([
    Type.Literal('CAP-006'),
    Type.Literal('CAP-016'),
  ]),
}, { additionalProperties: false })

const RegistryCapabilitySchema = (
  capabilityId: 'CAP-006' | 'CAP-016',
) => Type.Object({
  capability_id: Type.Literal(capabilityId),
  state: Type.Literal('active'),
  execution_binding: DomeyeExecutionBindingSchema,
}, { additionalProperties: false })

const RegistrySchema = Type.Object({
  registry_snapshot_id: Identifier,
  registry_digest: Sha256,
  state: Type.Literal('active'),
  capabilities: Type.Tuple([
    RegistryCapabilitySchema('CAP-006'),
    RegistryCapabilitySchema('CAP-016'),
  ]),
}, { additionalProperties: false })

const SourceFileSchema = Type.Object({
  path: SourcePath,
  sha256: Sha256,
}, { additionalProperties: false })

export const DomeyeFirstSliceCandidateManifestPayloadSchema = Type.Object({
  schema_version: Type.Literal('domeye_first_slice_candidate_manifest_v1'),
  base_commit: BaseCommit,
  contract: Type.Object({
    version: Type.Literal('domeye.first-vertical-slice/v1.0'),
    digest: Sha256,
  }, { additionalProperties: false }),
  data_identity: DomeyeDataIdentitySchema,
  series_response_sha256: Sha256,
  model: DomeyeFirstSliceModelBindingPayloadSchema,
  budget_policy: BudgetPolicySchema,
  policy: PolicySchema,
  registry: RegistrySchema,
  source_files: Type.Array(SourceFileSchema, {
    minItems: 2,
    uniqueItems: true,
  }),
  activation: Type.Object({
    scope: Type.Literal('local_evaluation_only'),
    production_deployed: Type.Literal(false),
  }, { additionalProperties: false }),
}, { additionalProperties: false })

export type DomeyeFirstSliceCandidateManifestPayload = Static<
  typeof DomeyeFirstSliceCandidateManifestPayloadSchema
>

export const DomeyeFirstSliceCandidateManifestSchema = Type.Object({
  candidate_id: Type.String({
    pattern: '^manifest:sha256:[a-f0-9]{64}$',
  }),
  payload: DomeyeFirstSliceCandidateManifestPayloadSchema,
}, { additionalProperties: false })

export type DomeyeFirstSliceCandidateManifest = Static<
  typeof DomeyeFirstSliceCandidateManifestSchema
>

export type DomeyeCandidateManifestErrorCode =
  | 'project_root_invalid'
  | 'manifest_file_invalid'
  | 'manifest_json_invalid'
  | 'manifest_schema_invalid'
  | 'candidate_id_mismatch'
  | 'model_binding_invalid'
  | 'policy_binding_invalid'
  | 'registry_binding_invalid'
  | 'source_binding_invalid'
  | 'runtime_closure_invalid'
  | 'source_file_path_invalid'
  | 'source_file_invalid'
  | 'source_file_hash_mismatch'

export class DomeyeCandidateManifestError extends Error {
  constructor(
    readonly code: DomeyeCandidateManifestErrorCode,
    message: string,
  ) {
    super(message)
    this.name = 'DomeyeCandidateManifestError'
  }
}

export interface LoadedDomeyeFirstSliceCandidateManifest {
  readonly candidate: DomeyeFirstSliceCandidateBinding
  readonly model_identity: DomeyeFirstSliceModelBindingPayload
  readonly manifest: DomeyeFirstSliceCandidateManifest
}

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value as Record<string, unknown>)) {
      deepFreeze(child)
    }
    Object.freeze(value)
  }
  return value
}

export function domeyeFirstSliceCandidateId(
  payload: DomeyeFirstSliceCandidateManifestPayload,
): string {
  return `manifest:sha256:${canonicalJsonSha256(payload)}`
}

function assertModelBinding(
  manifest: DomeyeFirstSliceCandidateManifest,
): void {
  try {
    const url = new URL(manifest.payload.model.base_url)
    if (
      !['http:', 'https:'].includes(url.protocol)
      || url.username
      || url.password
      || url.search
      || url.hash
    ) throw new Error('unsafe_url')
  } catch {
    throw new DomeyeCandidateManifestError(
      'model_binding_invalid',
      '模型 base_url 必须是无凭据、无 fragment 的 HTTP(S) URL',
    )
  }
}

interface FixedSourceDigests {
  readonly machine_contract_digest: string
  readonly capability_implementation_digest: string
}

function runtimeDistPath(sourcePath: string): string {
  return `${RUNTIME_DIST_PREFIX}${sourcePath.slice(
    RUNTIME_SOURCE_PREFIX.length,
    -'.ts'.length,
  )}.js`
}

function runtimeSourcePath(distPath: string): string {
  return `${RUNTIME_SOURCE_PREFIX}${distPath.slice(
    RUNTIME_DIST_PREFIX.length,
    -'.js'.length,
  )}.ts`
}

function assertRuntimeSourcePairs(paths: ReadonlySet<string>): void {
  for (const path of REQUIRED_RUNTIME_SOURCE_PATHS) {
    if (!paths.has(path) || !paths.has(runtimeDistPath(path))) {
      throw new DomeyeCandidateManifestError(
        'runtime_closure_invalid',
        `Candidate 缺少固定首片运行时源码或 build 产物：${path}`,
      )
    }
  }
  for (const path of paths) {
    if (path.startsWith(RUNTIME_SOURCE_PREFIX) && path.endsWith('.ts')) {
      if (!paths.has(runtimeDistPath(path))) {
        throw new DomeyeCandidateManifestError(
          'runtime_closure_invalid',
          `运行时源码没有绑定对应 build 产物：${path}`,
        )
      }
    }
    if (path.startsWith(RUNTIME_DIST_PREFIX) && path.endsWith('.js')) {
      if (!paths.has(runtimeSourcePath(path))) {
        throw new DomeyeCandidateManifestError(
          'runtime_closure_invalid',
          `运行时 build 产物没有绑定对应源码：${path}`,
        )
      }
    }
  }
}

function assertSourceManifest(
  manifest: DomeyeFirstSliceCandidateManifest,
): FixedSourceDigests {
  const paths = manifest.payload.source_files.map((source) => source.path)
  if (new Set(paths).size !== paths.length) {
    throw new DomeyeCandidateManifestError(
      'source_file_path_invalid',
      'source_files 路径不得重复',
    )
  }
  const sourceByPath = new Map(
    manifest.payload.source_files.map((source) => [source.path, source.sha256]),
  )
  for (const path of REQUIRED_FIXED_SOURCE_PATHS) {
    if (!sourceByPath.has(path)) {
      throw new DomeyeCandidateManifestError(
        'source_binding_invalid',
        `Candidate 缺少固定来源文件：${path}`,
      )
    }
  }
  assertRuntimeSourcePairs(new Set(paths))
  if (sourceByPath.get(ANCHOR_SOURCE_PATH) !== manifest.payload.contract.digest) {
    throw new DomeyeCandidateManifestError(
      'source_binding_invalid',
      '纵向切片合同摘要没有绑定固定 Anchor 路径',
    )
  }
  if (
    sourceByPath.get(MODEL_RESOURCE_SOURCE_PATH)
    !== manifest.payload.model.resource_sha256
  ) {
    throw new DomeyeCandidateManifestError(
      'model_binding_invalid',
      '模型资源摘要没有绑定固定 model-runtime 路径',
    )
  }
  const machineContractDigest = sourceByPath.get(MACHINE_CONTRACT_SOURCE_PATH)
  const implementationDigest = sourceByPath.get(
    CAPABILITY_IMPLEMENTATION_SOURCE_PATH,
  )
  if (!machineContractDigest || !implementationDigest) {
    throw new DomeyeCandidateManifestError(
      'source_binding_invalid',
      '机器合同或 Capability 实现没有绑定固定源码路径',
    )
  }
  return {
    machine_contract_digest: machineContractDigest,
    capability_implementation_digest: implementationDigest,
  }
}

function assertPolicyBinding(
  manifest: DomeyeFirstSliceCandidateManifest,
): void {
  const policyHash = canonicalJsonSha256({
    tenant_id: 'domeye',
    required_scope: 'country_outage:read',
    allowed_capability_ids: ['CAP-006', 'CAP-016'],
    model_api_attempt_limit: 10,
    approved_action_limit: 2,
    cost_policy: 'audit_only',
    monetary_limit_usd: null,
  })
  if (
    manifest.payload.policy.policy_id !== `policy-sha256:${policyHash}`
    || manifest.payload.policy.policy_digest !== `sha256:${policyHash}`
  ) {
    throw new DomeyeCandidateManifestError(
      'policy_binding_invalid',
      'Policy ID 或摘要不等于固定首片策略语义的重算结果',
    )
  }
}

function expectedRegistryCapabilities(
  sourceDigests: FixedSourceDigests,
) {
  const contractDigest = sourceDigests.machine_contract_digest
  const implementationDigest = sourceDigests.capability_implementation_digest
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
  ] as const
}

function assertRegistryBinding(
  manifest: DomeyeFirstSliceCandidateManifest,
  sourceDigests: FixedSourceDigests,
): void {
  const capabilities = expectedRegistryCapabilities(sourceDigests)
  const expectedCapabilitiesDigest = canonicalJsonSha256(capabilities)
  const actualCapabilitiesDigest = canonicalJsonSha256(
    manifest.payload.registry.capabilities,
  )
  const registryHash = canonicalJsonSha256({ capabilities })
  if (
    actualCapabilitiesDigest !== expectedCapabilitiesDigest
    || manifest.payload.registry.registry_snapshot_id
      !== `registry-snapshot-sha256:${registryHash}`
    || manifest.payload.registry.registry_digest !== `sha256:${registryHash}`
  ) {
    throw new DomeyeCandidateManifestError(
      'registry_binding_invalid',
      'Registry 内容、ID 或摘要不等于固定首片 Registry 的重算结果',
    )
  }
}

export function parseDomeyeFirstSliceCandidateManifest(
  value: unknown,
): DomeyeFirstSliceCandidateManifest {
  if (!Check(DomeyeFirstSliceCandidateManifestSchema, value)) {
    throw new DomeyeCandidateManifestError(
      'manifest_schema_invalid',
      'Candidate 清单不符合精确机器合同',
    )
  }
  const manifest = structuredClone(value)
  if (manifest.candidate_id !== domeyeFirstSliceCandidateId(manifest.payload)) {
    throw new DomeyeCandidateManifestError(
      'candidate_id_mismatch',
      'Candidate ID 与 canonical payload 摘要不一致',
    )
  }
  assertModelBinding(manifest)
  const sourceDigests = assertSourceManifest(manifest)
  assertPolicyBinding(manifest)
  assertRegistryBinding(manifest, sourceDigests)
  return deepFreeze(manifest)
}

function isWithin(root: string, target: string): boolean {
  const pathFromRoot = relative(root, target)
  return pathFromRoot === ''
    || (!pathFromRoot.startsWith(`..${sep}`)
      && pathFromRoot !== '..'
      && !isAbsolute(pathFromRoot))
}

function validSourcePath(path: string): boolean {
  return !isAbsolute(path)
    && !path.includes('\\')
    && !path.includes('\u0000')
    && posix.normalize(path) === path
    && path !== '.'
    && !path.split('/').some((part) => part === '' || part === '..')
}

async function assertRegularFile(
  projectRoot: string,
  path: string,
): Promise<string> {
  if (!validSourcePath(path)) {
    throw new DomeyeCandidateManifestError(
      'source_file_path_invalid',
      `source_files 路径无效：${path}`,
    )
  }
  let current = projectRoot
  try {
    for (const part of path.split('/')) {
      current = resolve(current, part)
      const metadata = await lstat(current)
      if (metadata.isSymbolicLink()) {
        throw new DomeyeCandidateManifestError(
          'source_file_invalid',
          `source_files 不接受符号链接：${path}`,
        )
      }
    }
    const canonical = await realpath(current)
    const metadata = await lstat(current)
    if (!isWithin(projectRoot, canonical) || !metadata.isFile()) {
      throw new DomeyeCandidateManifestError(
        'source_file_invalid',
        `source_files 必须是项目内普通文件：${path}`,
      )
    }
    return current
  } catch (error) {
    if (error instanceof DomeyeCandidateManifestError) throw error
    throw new DomeyeCandidateManifestError(
      'source_file_invalid',
      `无法读取 source_files 文件：${path}`,
    )
  }
}

async function checkedProjectRoot(path: string): Promise<string> {
  try {
    const canonical = await realpath(resolve(path))
    const metadata = await lstat(canonical)
    if (!metadata.isDirectory()) throw new Error('not_directory')
    return canonical
  } catch {
    throw new DomeyeCandidateManifestError(
      'project_root_invalid',
      'project_root 必须是可读取的真实目录',
    )
  }
}

async function readManifestFile(
  projectRoot: string,
  manifestPath: string,
): Promise<unknown> {
  const absolute = resolve(projectRoot, manifestPath)
  if (!isWithin(projectRoot, absolute)) {
    throw new DomeyeCandidateManifestError(
      'manifest_file_invalid',
      'Candidate 清单必须位于 project_root 内',
    )
  }
  let text: string
  try {
    const relativePath = relative(projectRoot, absolute).split(sep).join('/')
    const path = await assertRegularFile(projectRoot, relativePath)
    text = await readFile(path, 'utf8')
  } catch (error) {
    if (error instanceof DomeyeCandidateManifestError) {
      throw new DomeyeCandidateManifestError(
        'manifest_file_invalid',
        error.message,
      )
    }
    throw new DomeyeCandidateManifestError(
      'manifest_file_invalid',
      'Candidate 清单文件不可读取',
    )
  }
  try {
    return JSON.parse(text) as unknown
  } catch {
    throw new DomeyeCandidateManifestError(
      'manifest_json_invalid',
      'Candidate 清单不是有效 JSON',
    )
  }
}

async function verifySourceFiles(
  projectRoot: string,
  manifest: DomeyeFirstSliceCandidateManifest,
): Promise<void> {
  for (const source of manifest.payload.source_files) {
    const path = await assertRegularFile(projectRoot, source.path)
    const content = await readFile(path)
    const actual = `sha256:${createHash('sha256').update(content).digest('hex')}`
    if (actual !== source.sha256) {
      throw new DomeyeCandidateManifestError(
        'source_file_hash_mismatch',
        `source_files 摘要不一致：${source.path}`,
      )
    }
  }
}

export async function loadDomeyeFirstSliceCandidateManifest(
  options: {
    readonly project_root: string
    readonly manifest_path: string
  },
): Promise<LoadedDomeyeFirstSliceCandidateManifest> {
  const projectRoot = await checkedProjectRoot(options.project_root)
  const value = await readManifestFile(projectRoot, options.manifest_path)
  const manifest = parseDomeyeFirstSliceCandidateManifest(value)
  await verifySourceFiles(projectRoot, manifest)
  const candidate: DomeyeFirstSliceCandidateBinding = deepFreeze({
    candidate_id: manifest.candidate_id,
    contract_version: manifest.payload.contract.version,
    contract_digest: manifest.payload.contract.digest,
    data_identity: manifest.payload.data_identity,
    series_response_sha256: manifest.payload.series_response_sha256,
    model_identity: manifest.payload.model,
    budget_policy: manifest.payload.budget_policy,
    policy: manifest.payload.policy,
    registry: manifest.payload.registry,
  })
  return deepFreeze({
    candidate,
    model_identity: manifest.payload.model,
    manifest,
  })
}
