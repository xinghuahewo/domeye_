#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { lstatSync, readFileSync, realpathSync } from 'node:fs'
import { isAbsolute, join, relative, resolve, sep } from 'node:path'

const SHA256_PATTERN = /^[0-9a-f]{64}$/
const EXPECTED_REUSE_ANCHOR_SHA256 =
  '2a19f66ba2493aa9c123f73082a37283f27f9aac6c38e953ca6a2105db9f6844'
const EXPECTED_REUSE_GROUP_IDS = [
  'deepseek_model_and_api_adapter',
  'pi_0_82_1_dependency_identity',
  'three_readonly_tools_and_schema',
  'skill_prompt_report_contract_and_validator',
  'rrc25_fact_contract_and_context_assembly',
  'timeout_retry_and_token_limits',
]
const EXPECTED_SKILL_BUNDLE_FILES = [
  'SKILL.md',
  'references/metrics-and-boundaries.md',
  'references/report-output-contract.md',
]
const EXPECTED_RESPONSE_MODEL_TARGET =
  'node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js'

function fail(message) {
  process.stderr.write(`正式 Sidecar release 预检失败：${message}\n`)
  process.exit(1)
}

function readJson(path, label) {
  let value
  try {
    value = JSON.parse(readFileSync(path, 'utf8'))
  } catch (error) {
    fail(`${label} 无法读取或不是 JSON：${error.message}`)
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail(`${label} 根节点必须是对象`)
  }
  return value
}

function requireRegularFile(path, label) {
  let metadata
  try {
    metadata = lstatSync(path)
  } catch (error) {
    fail(`${label} 不存在：${error.message}`)
  }
  if (
    !metadata.isFile() ||
    metadata.isSymbolicLink() ||
    realpathSync(path) !== resolve(path)
  ) {
    fail(`${label} 必须是无符号链接的普通文件`)
  }
}

function sha256File(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

function safeReleaseFile(releaseDirectory, relativePath, label) {
  if (
    typeof relativePath !== 'string' ||
    relativePath.length === 0 ||
    isAbsolute(relativePath) ||
    relativePath.includes('\\') ||
    relativePath.split('/').some(
      (segment) => segment.length === 0 || segment === '.' || segment === '..',
    )
  ) {
    fail(`${label} 路径不是安全的 release 相对路径`)
  }
  const target = resolve(releaseDirectory, relativePath)
  const difference = relative(releaseDirectory, target)
  if (
    difference === '..' ||
    difference.startsWith(`..${sep}`) ||
    isAbsolute(difference)
  ) {
    fail(`${label} 路径逃逸 release 根目录`)
  }
  requireRegularFile(target, label)
  return target
}

const [releaseArgument, expectedProfile] = process.argv.slice(2)
if (!releaseArgument || !expectedProfile) {
  fail('用法：verify-formal-release.mjs <release-dir> <profile-id>')
}

const releaseDirectory = realpathSync(resolve(releaseArgument))
const sidecarDirectory = join(releaseDirectory, 'agent-sidecar')
const registryPath = join(
  sidecarDirectory,
  'resources',
  'certified-models',
  'country-outage-pi-models-v1.json',
)
const riskPath = join(
  sidecarDirectory,
  'resources',
  'risk-exceptions',
  'country-outage-pi-ghsa-mh99-v99m-4gvg-v2.json',
)
const reuseAnchorPath = join(
  sidecarDirectory,
  'resources',
  'certified-models',
  'country-outage-model-certification-reuse-identity-v1.json',
)
const acceptancePath = join(
  releaseDirectory,
  'config',
  'country-outage-agent-core-acceptance-v3.json',
)
const packagePath = join(sidecarDirectory, 'package.json')
const lockPath = join(sidecarDirectory, 'package-lock.json')
const formalEntryPath = join(
  sidecarDirectory,
  'dist',
  'src',
  'cli',
  'serve-formal.js',
)

for (const [path, label] of [
  [registryPath, '认证模型注册表'],
  [reuseAnchorPath, '模型认证复用身份锚点'],
  [riskPath, 'Pi 风险例外'],
  [acceptancePath, '核心验收配置'],
  [packagePath, 'Sidecar package.json'],
  [lockPath, 'Sidecar package-lock.json'],
  [formalEntryPath, '正式 Sidecar 编译入口'],
]) {
  requireRegularFile(path, label)
}

if (sha256File(reuseAnchorPath) !== EXPECTED_REUSE_ANCHOR_SHA256) {
  fail('模型认证复用身份锚点摘要不符合冻结版本')
}
const reuseAnchor = readJson(
  reuseAnchorPath,
  '模型认证复用身份锚点',
)
if (
  reuseAnchor.schemaVersion !==
    'country_outage_model_certification_reuse_identity_v1' ||
  reuseAnchor.identityId !==
    'deepseek-v4-flash-pi-0.82.1-a3-v6-reuse-v1' ||
  reuseAnchor.status !== 'frozen' ||
  reuseAnchor.baseline?.manifestPath !==
    'artifacts/country-outage-agent/a3-v6-current-source-20260730T165806+0800/source-end-manifest.json' ||
  reuseAnchor.baseline?.manifestAlgorithm !==
    'sha256(path\\0bytes\\0sha256\\n, sorted)' ||
  reuseAnchor.baseline?.manifestCombinedSha256 !==
    '983fb7034a9f20abd1ad8f557eb101cef7b3e836002f510008289ce0741e3c45' ||
  reuseAnchor.certifiedModel?.profileId !==
    'deepseek-v4-flash-pi-0.82.1-v1' ||
  reuseAnchor.certifiedModel?.registryVersion !==
    'deepseek-v4-flash-certified-v1' ||
  reuseAnchor.certifiedModel?.certificationEvidenceId !==
    'evidence:model-certification:b50f247c7b1322df6d05afa45c5c1078b58349329d9f27ec5800bbfa5770a1d4' ||
  reuseAnchor.certifiedModel?.provider !== 'deepseek' ||
  reuseAnchor.certifiedModel?.model !== 'deepseek-v4-flash' ||
  reuseAnchor.certifiedModel?.piVersion !== '0.82.1'
) {
  fail('模型认证复用身份锚点的基线或模型身份不符合冻结合同')
}
if (
  !Array.isArray(reuseAnchor.groups) ||
  reuseAnchor.groups.length !== EXPECTED_REUSE_GROUP_IDS.length ||
  JSON.stringify(reuseAnchor.groups.map((group) => group?.id)) !==
    JSON.stringify(EXPECTED_REUSE_GROUP_IDS) ||
  reuseAnchor.groups.some(
    (group) =>
      !Array.isArray(group?.paths) ||
      group.paths.length === 0 ||
      group.paths.some((path) => typeof path !== 'string'),
  )
) {
  fail('模型认证复用身份锚点必须精确包含六组非空路径')
}
if (
  !Array.isArray(reuseAnchor.files) ||
  reuseAnchor.files.length !== 21
) {
  fail('模型认证复用身份锚点必须精确包含 21 个去重文件')
}
const reuseFilePaths = reuseAnchor.files.map((item) => item?.path)
const groupedPaths = reuseAnchor.groups.flatMap((group) => group.paths)
if (
  reuseAnchor.files.some(
    (item) =>
      !item ||
      typeof item !== 'object' ||
      typeof item.path !== 'string' ||
      !SHA256_PATTERN.test(item.a3BaselineSha256),
  ) ||
  new Set(reuseFilePaths).size !== 21 ||
  groupedPaths.length !== 21 ||
  new Set(groupedPaths).size !== 21 ||
  JSON.stringify([...new Set(groupedPaths)].sort()) !==
    JSON.stringify([...new Set(reuseFilePaths)].sort())
) {
  fail('六组路径与 21 文件 A3 摘要集合不一致')
}
for (const item of reuseAnchor.files) {
  const target = safeReleaseFile(
    releaseDirectory,
    item.path,
    `模型认证复用文件 ${item.path}`,
  )
  if (sha256File(target) !== item.a3BaselineSha256) {
    fail(`模型认证复用文件已偏离 A3 基线：${item.path}`)
  }
}

if (
  reuseAnchor.skillBundle?.version !==
    'country_outage_report_skill_v6' ||
  reuseAnchor.skillBundle?.algorithm !==
    'sha256(relativePath\\0utf8Content\\0, ordered)' ||
  reuseAnchor.skillBundle?.basePath !==
    'agent-sidecar/resources/skills/country-outage-report' ||
  JSON.stringify(reuseAnchor.skillBundle?.files) !==
    JSON.stringify(EXPECTED_SKILL_BUNDLE_FILES) ||
  reuseAnchor.skillBundle?.sha256 !==
    '5f108d26f39dea9ff5a2902b00cdb113e3a76a8afd1b6560dffd7e9453d3a88d'
) {
  fail('Skill bundle 身份不符合已认证冻结合同')
}
const skillDigest = createHash('sha256')
for (const skillRelativePath of EXPECTED_SKILL_BUNDLE_FILES) {
  const skillPath = safeReleaseFile(
    releaseDirectory,
    `${reuseAnchor.skillBundle.basePath}/${skillRelativePath}`,
    `Skill bundle 文件 ${skillRelativePath}`,
  )
  skillDigest.update(skillRelativePath)
  skillDigest.update('\0')
  skillDigest.update(readFileSync(skillPath, 'utf8'))
  skillDigest.update('\0')
}
if (skillDigest.digest('hex') !== reuseAnchor.skillBundle.sha256) {
  fail('Skill bundle 内容摘要已偏离已认证身份')
}

if (
  reuseAnchor.responseModelPatch?.patchId !==
    'pi-ai-openai-completions-response-model-v1' ||
  reuseAnchor.responseModelPatch?.targetPathFromSidecar !==
    EXPECTED_RESPONSE_MODEL_TARGET ||
  reuseAnchor.responseModelPatch?.patchedSha256 !==
    '5805cc08566c4d9437280f68d996ef0fb452c15e2becb67b94c967b7ace2023b'
) {
  fail('responseModel vendor patch 身份不符合冻结合同')
}
const responseModelTarget = safeReleaseFile(
  sidecarDirectory,
  reuseAnchor.responseModelPatch.targetPathFromSidecar,
  'responseModel vendor patch 目标',
)
if (
  sha256File(responseModelTarget) !==
  reuseAnchor.responseModelPatch.patchedSha256
) {
  fail('responseModel adapter 源码未应用已认证补丁')
}

const registry = readJson(registryPath, '认证模型注册表')
if (
  registry.schemaVersion !== 'country_outage_pi_certified_models_v1' ||
  registry.registryVersion !== 'deepseek-v4-flash-certified-v1' ||
  registry.status !== 'frozen' ||
  !Array.isArray(registry.profiles)
) {
  fail('认证模型注册表身份或状态不符合冻结合同')
}
const profile = registry.profiles.find((item) => item?.id === expectedProfile)
if (
  !profile ||
  profile.status !== 'certified' ||
  profile.provider !== 'deepseek' ||
  profile.model !== 'deepseek-v4-flash' ||
  profile.expectedResponseModel !== 'deepseek-v4-flash' ||
  profile.piVersion !== '0.82.1' ||
  profile.certifiedInputScope !== 'legal_country_outage_rrc25_v1' ||
  profile.certificationEvidenceId !==
    reuseAnchor.certifiedModel.certificationEvidenceId
) {
  fail('正式模型 profile 不存在或身份超出已认证边界')
}
const certificationExpiry = Date.parse(profile.certificationValidUntil)
if (!Number.isFinite(certificationExpiry) || certificationExpiry <= Date.now()) {
  fail('正式模型认证已经到期')
}

const risk = readJson(riskPath, 'Pi 风险例外')
const expectedTools = [
  'country_outage_resolve',
  'country_outage_get_observation',
  'country_outage_get_asns',
]
if (
  risk.schemaVersion !== 'country_outage_dependency_risk_exception_v2' ||
  risk.status !== 'approved' ||
  risk.risk?.piVersion !== '0.82.1' ||
  risk.constraints?.capabilityExpansionAllowed !== false ||
  risk.constraints?.externalGlobEnabled !== false ||
  risk.constraints?.modelResolverEnabled !== false ||
  risk.constraints?.packageManagerResolutionEnabled !== false ||
  JSON.stringify(risk.constraints?.allowedTools) !==
    JSON.stringify(expectedTools)
) {
  fail('Pi 风险例外身份、状态或能力约束不符合批准合同')
}
const riskExpiry = Date.parse(risk.expiresAt)
if (!Number.isFinite(riskExpiry) || riskExpiry <= Date.now()) {
  fail('Pi 风险例外已经到期')
}

const acceptance = readJson(acceptancePath, '核心验收配置')
if (
  acceptance.id !== 'country-outage-agent-core-acceptance-v3' ||
  acceptance.status !== 'frozen' ||
  acceptance.scope?.collector_id !== 'rrc25' ||
  acceptance.scope?.public_network_access !== 'none' ||
  acceptance.scope?.external_evidence_pack_required_for_core_acceptance !== false
) {
  fail('核心验收配置不是冻结的 RRC25-only / external-disabled 合同')
}

const packageJson = readJson(packagePath, 'Sidecar package.json')
const packageLock = readJson(lockPath, 'Sidecar package-lock.json')
if (
  packageJson.dependencies?.['@earendil-works/pi-coding-agent'] !== '0.82.1' ||
  packageLock.packages?.[
    'node_modules/@earendil-works/pi-coding-agent'
  ]?.version !== '0.82.1'
) {
  fail('package 与 lock 中的 Pi 版本不是精确 0.82.1')
}

process.stdout.write(
  `${JSON.stringify({
    status: 'ready',
    releaseDirectory,
    profile: profile.id,
    model: profile.model,
    piVersion: profile.piVersion,
    certificationValidUntil: profile.certificationValidUntil,
    riskExceptionId: risk.exceptionId,
    riskExceptionExpiresAt: risk.expiresAt,
    collector: 'rrc25',
    externalEvidence: 'disabled',
    modelReuseIdentity: reuseAnchor.identityId,
    modelReuseFiles: '21/21',
    modelReuseAnchorSha256: EXPECTED_REUSE_ANCHOR_SHA256,
    skillBundleSha256: reuseAnchor.skillBundle.sha256,
    responseModelPatchedSha256:
      reuseAnchor.responseModelPatch.patchedSha256,
  })}\n`,
)
