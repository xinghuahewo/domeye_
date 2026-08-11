#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { lstatSync, readFileSync, realpathSync } from 'node:fs'
import { isAbsolute, join, resolve } from 'node:path'

function fail(message) {
  process.stderr.write(`P1 release 校验失败：${message}\n`)
  process.exit(1)
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

function canonical(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  return `{${Object.keys(value).sort().map((key) =>
    `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
}

function p2CanonicalNumber(value) {
  if (!Number.isFinite(value)) fail('P2 摘要包含非有限数字')
  if (Object.is(value, -0) || value === 0) return '0'
  const sign = value < 0 ? '-' : ''
  const [coefficientPart = '0', exponentPart = '0'] = Math.abs(value)
    .toString().toLowerCase().split('e')
  const explicitExponent = Number.parseInt(exponentPart, 10)
  const decimalAt = coefficientPart.indexOf('.')
  const fractionalLength = decimalAt === -1 ? 0 : coefficientPart.length - decimalAt - 1
  const leadingTrimmed = coefficientPart.replace('.', '').replace(/^0+/, '')
  const trailingCount = leadingTrimmed.length - leadingTrimmed.replace(/0+$/, '').length
  const digits = leadingTrimmed.replace(/0+$/, '')
  const scientificExponent = explicitExponent - fractionalLength + trailingCount + digits.length - 1
  const coefficient = digits.length === 1 ? digits : `${digits[0]}.${digits.slice(1)}`
  return `${sign}${coefficient}e${scientificExponent}`
}

function p2Canonical(value) {
  if (value === null) return 'null'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return p2CanonicalNumber(value)
  if (typeof value === 'string') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(p2Canonical).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.entries(value)
      .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
      .map(([key, item]) => `${JSON.stringify(key)}:${p2Canonical(item)}`)
      .join(',')}}`
  }
  fail('P2 摘要包含不支持的类型')
}

function p2Digest(value) {
  return `sha256:${sha256(p2Canonical(value))}`
}

function regularFile(path) {
  const normalized = resolve(path)
  let stats
  try {
    stats = lstatSync(normalized)
  } catch {
    fail(`缺少文件 ${normalized}`)
  }
  if (!stats.isFile() || stats.isSymbolicLink() || realpathSync(normalized) !== normalized) {
    fail(`不是规范普通文件 ${normalized}`)
  }
  return normalized
}

function readJson(path) {
  let value
  try {
    value = JSON.parse(readFileSync(regularFile(path), 'utf8'))
  } catch (error) {
    fail(`JSON 无效 ${path}：${error.message}`)
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail(`JSON 根节点无效 ${path}`)
  }
  return value
}

const [releaseRootArgument] = process.argv.slice(2)
if (!releaseRootArgument || !isAbsolute(releaseRootArgument)) {
  fail('用法：verify-release.mjs <absolute-release-root>')
}
const releaseRoot = resolve(releaseRootArgument)
if (realpathSync(releaseRoot) !== releaseRoot) fail('release 根必须是实际目录')

const release = readJson(join(releaseRoot, 'RELEASE-MANIFEST.json'))
const certificationPath = join(releaseRoot, 'certification', 'manifest.json')
const certification = readJson(certificationPath)
const registryPath = join(
  releaseRoot,
  'agent-sidecar/resources/certified-models/country-outage-p1-semantic-models-v1.json',
)
const registry = readJson(registryPath)
const trendContractRoot = join(
  releaseRoot,
  'contracts/agent/country-outage-p1-trend-operator/v1',
)
const trendIdentityPath = join(releaseRoot, 'TREND-OPERATOR-IDENTITY.json')
const trendIdentity = readJson(trendIdentityPath)
const trendIntegrationPath = join(
  trendContractRoot,
  'p1-integration-contract.json',
)
const trendIntegration = readJson(trendIntegrationPath)
const trendProfilesPath = join(trendContractRoot, 'trend-profiles.json')
const trendProfiles = readJson(trendProfilesPath)
const trendOperatorContract = readJson(
  join(trendContractRoot, 'operator-contract.json'),
)
const p2ContractRoot = join(
  releaseRoot,
  'contracts/agent/country-outage-p2-s0b-runtime',
)
const p2CandidatePath = join(p2ContractRoot, 'candidate.json')
const p2SnapshotPath = join(p2ContractRoot, 'registry-snapshot.json')
const p2ShadowCandidatePath = join(p2ContractRoot, 'candidate.shadow.json')
const p2ShadowSnapshotPath = join(p2ContractRoot, 'registry-snapshot.shadow.json')
const p2PromotionPath = join(releaseRoot, 'P2-REGISTRY-PROMOTION.json')
const p2Candidate = readJson(p2CandidatePath)
const p2Snapshot = readJson(p2SnapshotPath)
const p2ShadowCandidate = readJson(p2ShadowCandidatePath)
const p2ShadowSnapshot = readJson(p2ShadowSnapshotPath)
const p2Promotion = readJson(p2PromotionPath)
const p2AcceptancePath = join(
  releaseRoot,
  'certification/p2-s0b/acceptance-manifest.json',
)
const p2ReviewPath = join(
  releaseRoot,
  'certification/p2-s0b/product-semantic-review.json',
)
const p2Acceptance = readJson(p2AcceptancePath)
const p2Review = readJson(p2ReviewPath)
const profile = registry.profiles?.[0]
const payload = { ...certification }
delete payload.evidence_id
const expectedEvidenceId =
  `evidence:p1-semantic-certification:${sha256(canonical(payload))}`

if (
  release.schema_version !== 'country_outage_p1_chat_release_v2' ||
  release.component !== 'country_outage_p1_chat_sidecar' ||
  release.source?.annotated_tag !== release.release_id ||
  release.rollback?.release_id === release.release_id ||
  release.boundaries?.collector !== 'rrc25' ||
  release.boundaries?.event_type !== 'country_outage' ||
  release.boundaries?.report_capability !== 'disabled' ||
  release.boundaries?.external_evidence !== 'disabled' ||
  release.resource_observation?.release_gate !==
    'cpu_rss_call_count_and_error_log' ||
  release.resource_observation?.fee_audit_gate !== 'not_required' ||
  release.runtime?.host !== '127.0.0.1' ||
  release.runtime?.port !== 28475 ||
  release.runtime?.maximum_provider_request_count_per_turn !== 1 ||
  release.runtime?.event_window_trend_operator?.execution_unit !== 'OP-04' ||
  release.runtime?.event_window_trend_operator?.capability_id !==
    'CAP-TREND-001' ||
  release.runtime?.event_window_trend_operator?.operator_id !==
    'event-window-trend' ||
  release.runtime?.event_window_trend_operator?.operator_version !== '1.2.0' ||
  release.runtime?.event_window_trend_operator?.model_dependency !== 'none' ||
  release.runtime?.tool_operator_registry?.candidate_id !==
    p2Candidate.candidate_id ||
  release.runtime?.tool_operator_registry?.registry_snapshot_id !==
    p2Snapshot.registry_snapshot_id ||
  release.runtime?.tool_operator_registry?.registry_revision !==
    p2Snapshot.snapshot_payload?.registry_revision ||
  release.runtime?.tool_operator_registry?.activation_scope !==
    'production_active' ||
  release.runtime?.tool_operator_registry?.runtime_integration !== 'deployed' ||
  release.runtime?.tool_operator_registry?.production_deployed !== true
) fail('release 边界或运行时合同漂移')

if (
  trendIntegration.schema_version !==
    'country_outage_p1_event_window_trend_integration_v1' ||
  trendIntegration.scope?.event_type !== 'country_outage' ||
  trendIntegration.scope?.collector_id !== 'rrc25' ||
  trendIntegration.scope?.time_scope !== 'current_publication_window' ||
  trendIntegration.scope?.analysis_mode !== 'event_window_trend' ||
  trendIntegration.grounding?.execution_unit !== 'OP-04' ||
  trendIntegration.grounding?.capability_id !== 'CAP-TREND-001' ||
  trendIntegration.grounding?.source_execution_unit !== 'TOOL-03' ||
  trendIntegration.operator?.operator_id !== 'event-window-trend' ||
  trendIntegration.operator?.operator_version !== '1.2.0' ||
  trendIntegration.operator?.model_dependency !== 'none' ||
  trendOperatorContract.operator_id !== 'event-window-trend' ||
  trendOperatorContract.operator_version !== '1.2.0' ||
  trendProfiles.registry_revision !== 3
) fail('P1 趋势算子接入合同漂移')

const expectedTrendIdentityPaths = [
  'agent-sidecar/src/chat/event-window-trend.ts',
  'agent-sidecar/src/chat/trend-aware-grounder.ts',
  'agent-sidecar/src/chat/page-capability-executor.ts',
  'agent-sidecar/src/chat/runtime-v2-conversation.ts',
  'agent-sidecar/src/cli/formal-p1-sidecar.ts',
  'agent-sidecar/dist/src/chat/event-window-trend.js',
  'agent-sidecar/dist/src/chat/trend-aware-grounder.js',
  'agent-sidecar/dist/src/chat/page-capability-executor.js',
  'agent-sidecar/dist/src/chat/runtime-v2-conversation.js',
  'agent-sidecar/dist/src/cli/formal-p1-sidecar.js',
  'contracts/agent/country-outage-p1-trend-operator/v1/operator-contract.json',
  'contracts/agent/country-outage-p1-trend-operator/v1/trend-profiles.json',
  'contracts/agent/country-outage-p1-trend-operator/v1/p1-integration-contract.json',
]
if (
  trendIdentity.schema_version !==
    'country_outage_p1_trend_operator_identity_v1' ||
  trendIdentity.execution_unit !== 'OP-04' ||
  trendIdentity.capability_id !== 'CAP-TREND-001' ||
  trendIdentity.operator_id !== 'event-window-trend' ||
  trendIdentity.operator_version !== '1.2.0' ||
  trendIdentity.profile_registry_version !==
    'country-outage-p1-trend-profile-v1' ||
  trendIdentity.model_dependency !== 'none' ||
  !Array.isArray(trendIdentity.files) ||
  trendIdentity.files.length !== expectedTrendIdentityPaths.length ||
  trendIdentity.files.map((item) => item.path).join('\n') !==
    expectedTrendIdentityPaths.join('\n')
) fail('趋势算子候选身份漂移')
for (const item of trendIdentity.files) {
  const path = regularFile(join(releaseRoot, item.path))
  if (sha256(readFileSync(path)) !== item.sha256) {
    fail(`趋势算子身份文件漂移 ${item.path}`)
  }
}

if (
  certification.schema_version !==
    'country_outage_p1_semantic_model_certification_v1' ||
  certification.status !== 'certified' ||
  certification.evidence_id !== expectedEvidenceId ||
  certification.metrics?.user_goal_fidelity < 0.95 ||
  certification.metrics?.grounding_legality_rate !== 1 ||
  Date.now() >= Date.parse(certification.valid_until) ||
  !Array.isArray(certification.case_receipts) ||
  certification.case_receipts.length !== certification.case_count
) fail('P1 模型认证未达到正式门')

if (
  registry.schemaVersion !== 'country_outage_pi_certified_models_v1' ||
  registry.status !== 'frozen' ||
  registry.profiles?.length !== 1 ||
  profile?.status !== 'certified' ||
  profile?.id !== certification.candidate_id ||
  profile?.piVersion !== '0.84.1' ||
  profile?.certificationEvidenceId !== certification.evidence_id ||
  profile?.certificationValidUntil !== certification.valid_until ||
  profile?.certifiedScenarioSetId !==
    'country-outage-p1-page-coverage-s2-v1' ||
  profile?.certifiedInputScope !==
    'country_outage_p1_rrc25_event_bound_chat_v1'
) fail('P1 正式注册表与认证不一致')

for (const item of certification.case_receipts) {
  const path = regularFile(join(releaseRoot, 'certification', item.path))
  if (sha256(readFileSync(path)) !== item.sha256) fail(`逐案回执漂移 ${item.path}`)
}
for (const item of certification.source_identity?.files ?? []) {
  const path = regularFile(join(releaseRoot, 'source-identity', item.path))
  if (sha256(readFileSync(path)) !== item.sha256) fail(`认证源码漂移 ${item.path}`)
}

const runtimeContractRoot =
  'contracts/agent/country-outage-p1-page-coverage/s2'
for (const fileName of [
  'semantic-plan.schema.json',
  'capability-catalog.json',
  'tool-contracts.json',
  'oracle.json',
  'policy.json',
]) {
  const relativePath = `${runtimeContractRoot}/${fileName}`
  const runtimePath = regularFile(join(releaseRoot, relativePath))
  const certifiedPath = regularFile(
    join(releaseRoot, 'source-identity', relativePath),
  )
  if (sha256(readFileSync(runtimePath)) !== sha256(readFileSync(certifiedPath))) {
    fail(`运行合同与认证源码不一致 ${relativePath}`)
  }
}

const p2ProductionDigest = p2Digest(p2Snapshot.snapshot_payload)
const p2ShadowDigest = p2Digest(p2ShadowSnapshot.snapshot_payload)
const p2PromotionPayload = { ...p2Promotion }
delete p2PromotionPayload.receipt_digest
const p2AcceptancePayload = { ...p2Acceptance }
delete p2AcceptancePayload.manifest_digest
if (
  p2Candidate.schema_version !==
    'country_outage_p2_s0b6_production_candidate_v1' ||
  !/^p2-s0b6-[a-f0-9]{16}$/.test(p2Candidate.candidate_id ?? '') ||
  p2Candidate.registry_snapshot_id !== p2Snapshot.registry_snapshot_id ||
  p2Candidate.registry_revision !== p2Snapshot.snapshot_payload?.registry_revision ||
  p2Candidate.release_id !== release.release_id ||
  p2Candidate.source_commit !== release.source?.commit ||
  p2Candidate.source_tag !== release.source?.annotated_tag ||
  p2Candidate.rollback_release_id !== release.rollback?.release_id ||
  p2Candidate.activation_scope !== 'production_active' ||
  p2Candidate.runtime_integration !== 'deployed' ||
  p2Candidate.production_deployed !== true ||
  p2Snapshot.production_deployed !== true ||
  p2Snapshot.snapshot_payload?.candidate_id !== p2Candidate.candidate_id ||
  p2Snapshot.snapshot_payload?.activation_scope !== 'production_active' ||
  p2Snapshot.snapshot_payload?.runtime_integration !== 'deployed' ||
  p2Snapshot.snapshot_digest !== p2ProductionDigest ||
  p2Snapshot.registry_snapshot_id !==
    `registry-snapshot-sha256:${p2ProductionDigest.slice('sha256:'.length)}`
) fail('P2 production candidate 或 snapshot 身份无效')
if (
  p2ShadowCandidate.schema_version !== 'country_outage_p2_s0b_candidate_v1' ||
  p2ShadowCandidate.candidate_id !== p2ShadowSnapshot.snapshot_payload?.candidate_id ||
  p2ShadowCandidate.registry_snapshot_id !== p2ShadowSnapshot.registry_snapshot_id ||
  p2ShadowCandidate.production_deployed !== false ||
  p2ShadowSnapshot.production_deployed !== false ||
  p2ShadowSnapshot.snapshot_payload?.activation_scope !==
    'runtime_candidate_shadow_only' ||
  p2ShadowSnapshot.snapshot_payload?.runtime_integration !==
    'implemented_not_deployed' ||
  p2ShadowSnapshot.snapshot_digest !== p2ShadowDigest ||
  p2ShadowSnapshot.registry_snapshot_id !==
    `registry-snapshot-sha256:${p2ShadowDigest.slice('sha256:'.length)}` ||
  p2Acceptance.status !== 'accepted_local_shadow_candidate' ||
  p2Acceptance.candidate_id !== p2ShadowCandidate.candidate_id ||
  p2Acceptance.manifest_digest !== p2Digest(p2AcceptancePayload) ||
  p2Review.status !== 'PASS' ||
  p2Review.blocking_count !== 0 ||
  p2Review.candidate_id !== p2ShadowCandidate.candidate_id
) fail('P2 shadow 基线、验收或 Reviewer 身份无效')
if (
  p2Promotion.schema_version !==
    'country_outage_p2_s0b6_promotion_identity_v1' ||
  p2Promotion.release_id !== release.release_id ||
  p2Promotion.source_commit !== release.source?.commit ||
  p2Promotion.source_tag !== release.source?.annotated_tag ||
  p2Promotion.rollback_release_id !== release.rollback?.release_id ||
  p2Promotion.shadow_candidate_id !== p2ShadowCandidate.candidate_id ||
  p2Promotion.shadow_registry_snapshot_id !==
    p2ShadowSnapshot.registry_snapshot_id ||
  p2Promotion.production_candidate_id !== p2Candidate.candidate_id ||
  p2Promotion.production_registry_snapshot_id !== p2Snapshot.registry_snapshot_id ||
  p2Promotion.production_registry_revision !==
    p2Snapshot.snapshot_payload.registry_revision ||
  p2Promotion.activation_scope !== 'production_active' ||
  p2Promotion.runtime_integration !== 'deployed' ||
  p2Promotion.production_deployed !== true ||
  p2Promotion.receipt_digest !== p2Digest(p2PromotionPayload) ||
  p2Promotion.files?.shadow_candidate !==
    `sha256:${sha256(readFileSync(p2ShadowCandidatePath))}` ||
  p2Promotion.files?.shadow_snapshot !==
    `sha256:${sha256(readFileSync(p2ShadowSnapshotPath))}` ||
  p2Promotion.files?.production_candidate !==
    `sha256:${sha256(readFileSync(p2CandidatePath))}` ||
  p2Promotion.files?.production_snapshot !==
    `sha256:${sha256(readFileSync(p2SnapshotPath))}` ||
  p2Promotion.files?.shadow_acceptance_manifest !==
    `sha256:${sha256(readFileSync(p2AcceptancePath))}` ||
  p2Promotion.files?.product_semantic_review !==
    `sha256:${sha256(readFileSync(p2ReviewPath))}`
) fail('P2 production promotion 回执或制品摘要无效')

if (
  release.hashes?.certification_manifest !== sha256(readFileSync(certificationPath)) ||
  release.hashes?.certified_registry !== sha256(readFileSync(registryPath)) ||
  release.hashes?.trend_operator_identity !==
    sha256(readFileSync(trendIdentityPath)) ||
  release.hashes?.trend_integration_contract !==
    sha256(readFileSync(trendIntegrationPath)) ||
  release.hashes?.trend_profiles !== sha256(readFileSync(trendProfilesPath)) ||
  release.hashes?.p2_registry_promotion !== sha256(readFileSync(p2PromotionPath))
) fail('release 摘要与正式制品不一致')

process.stdout.write(`${JSON.stringify({
  status: 'verified',
  release_id: release.release_id,
  source_commit: release.source?.commit,
  source_tag: release.source?.annotated_tag,
  registry_version: registry.registryVersion,
  certification_evidence_id: certification.evidence_id,
  certification_valid_until: certification.valid_until,
  user_goal_fidelity: certification.metrics.user_goal_fidelity,
  grounding_legality_rate: certification.metrics.grounding_legality_rate,
  event_window_trend_operator: release.runtime.event_window_trend_operator,
  tool_operator_registry: release.runtime.tool_operator_registry,
  rollback_release_id: release.rollback.release_id,
  fee_audit_gate: release.resource_observation.fee_audit_gate,
  report_capability: 'disabled',
})}\n`)
