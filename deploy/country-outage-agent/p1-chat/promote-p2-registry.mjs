#!/usr/bin/env node

import { createHash } from 'node:crypto'
import {
  copyFileSync,
  lstatSync,
  readFileSync,
  realpathSync,
  renameSync,
  writeFileSync,
} from 'node:fs'
import { isAbsolute, join, resolve } from 'node:path'

function fail(message) {
  process.stderr.write(`P2 Registry production promotion 失败：${message}\n`)
  process.exit(1)
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
  try {
    const value = JSON.parse(readFileSync(regularFile(path), 'utf8'))
    if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`JSON 根无效 ${path}`)
    return value
  } catch (error) {
    fail(`JSON 无效 ${path}：${error.message}`)
  }
}

function sha256Bytes(value) {
  return `sha256:${createHash('sha256').update(value).digest('hex')}`
}

function sha256File(path) {
  return sha256Bytes(readFileSync(regularFile(path)))
}

function canonicalNumber(value) {
  if (!Number.isFinite(value)) fail('摘要输入包含非有限数字')
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

function canonical(value) {
  if (value === null) return 'null'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return canonicalNumber(value)
  if (typeof value === 'string') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.entries(value)
      .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(',')}}`
  }
  fail('摘要输入包含不支持的类型')
}

function digestValue(value) {
  return sha256Bytes(canonical(value))
}

function sorted(value) {
  if (Array.isArray(value)) return value.map(sorted)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
        .map(([key, item]) => [key, sorted(item)]),
    )
  }
  return value
}

function writeJson(path, value) {
  const temporary = `${path}.tmp`
  writeFileSync(temporary, `${JSON.stringify(sorted(value), null, 2)}\n`, { mode: 0o600 })
  renameSync(temporary, path)
}

const [releaseRootArgument, releaseId, sourceCommit, sourceTag, rollbackReleaseId] =
  process.argv.slice(2)
if (
  !releaseRootArgument || !isAbsolute(releaseRootArgument)
  || !/^[0-9]{8}T[0-9]{6}Z-country-outage-p1-chat-[a-z0-9-]+$/.test(releaseId ?? '')
  || !/^[a-f0-9]{40}$/.test(sourceCommit ?? '')
  || !sourceTag
  || !/^[0-9]{8}T[0-9]{6}Z-country-outage-p1-chat-[a-z0-9-]+$/.test(rollbackReleaseId ?? '')
) {
  fail('用法：promote-p2-registry.mjs <absolute-release-root> <release-id> <commit> <tag> <rollback-release-id>')
}
const releaseRoot = resolve(releaseRootArgument)
if (realpathSync(releaseRoot) !== releaseRoot) fail('release 根必须是实际目录')
const contractRoot = join(
  releaseRoot,
  'contracts/agent/country-outage-p2-s0b-runtime',
)
const candidatePath = join(contractRoot, 'candidate.json')
const snapshotPath = join(contractRoot, 'registry-snapshot.json')
const acceptancePath = join(
  releaseRoot,
  'certification/p2-s0b/acceptance-manifest.json',
)
const reviewPath = join(
  releaseRoot,
  'certification/p2-s0b/product-semantic-review.json',
)
const shadowCandidate = readJson(candidatePath)
const shadowSnapshot = readJson(snapshotPath)
const acceptance = readJson(acceptancePath)
const review = readJson(reviewPath)
const shadowPayload = shadowSnapshot.snapshot_payload
const shadowDigest = digestValue(shadowPayload)
if (
  shadowCandidate.schema_version !== 'country_outage_p2_s0b_candidate_v1'
  || shadowCandidate.candidate_id !== shadowPayload?.candidate_id
  || shadowCandidate.registry_snapshot_id !== shadowSnapshot.registry_snapshot_id
  || shadowCandidate.activation_scope !== 'runtime_candidate_shadow_only'
  || shadowCandidate.runtime_integration !== 'implemented_not_deployed'
  || shadowCandidate.production_deployed !== false
  || shadowSnapshot.production_deployed !== false
  || shadowPayload?.activation_scope !== 'runtime_candidate_shadow_only'
  || shadowPayload?.runtime_integration !== 'implemented_not_deployed'
  || shadowSnapshot.snapshot_digest !== shadowDigest
  || shadowSnapshot.registry_snapshot_id !==
    `registry-snapshot-sha256:${shadowDigest.slice('sha256:'.length)}`
) fail('shadow candidate 或 snapshot 身份无效')
if (
  acceptance.schema_version !== 'country_outage_p2_s0b_acceptance_manifest_v1'
  || acceptance.status !== 'accepted_local_shadow_candidate'
  || acceptance.candidate_id !== shadowCandidate.candidate_id
  || acceptance.production_deployed !== false
  || review.status !== 'PASS'
  || review.blocking_count !== 0
  || review.candidate_id !== shadowCandidate.candidate_id
) fail('shadow 同候选验收或独立产品语义审核无效')
const manifestPayload = { ...acceptance }
delete manifestPayload.manifest_digest
if (acceptance.manifest_digest !== digestValue(manifestPayload)) {
  fail('shadow acceptance manifest 摘要无效')
}
for (const item of shadowCandidate.source_identity?.runtime_material ?? []) {
  if (sha256File(join(releaseRoot, item.path)) !== item.sha256) {
    fail(`shadow candidate 源码摘要漂移 ${item.path}`)
  }
}
for (const unit of shadowPayload.execution_unit_registry?.entries ?? []) {
  for (const item of unit.implementation_files ?? []) {
    if (sha256File(join(releaseRoot, item.path)) !== item.sha256) {
      fail(`Execution Unit 实现摘要漂移 ${unit.unit_id}:${item.path}`)
    }
  }
}

const promotionMaterial = {
  schema_version: 'country_outage_p2_s0b6_promotion_identity_v1',
  release_id: releaseId,
  source_commit: sourceCommit,
  source_tag: sourceTag,
  rollback_release_id: rollbackReleaseId,
  shadow_candidate_id: shadowCandidate.candidate_id,
  shadow_registry_snapshot_id: shadowSnapshot.registry_snapshot_id,
  shadow_acceptance_manifest_digest: acceptance.manifest_digest,
  product_semantic_review_digest: review.receipt_digest,
}
const promotionIdentityDigest = digestValue(promotionMaterial)
const productionCandidateId =
  `p2-s0b6-${promotionIdentityDigest.slice('sha256:'.length, 'sha256:'.length + 16)}`
const productionPayload = structuredClone(shadowPayload)
productionPayload.candidate_id = productionCandidateId
productionPayload.registry_revision = Number(shadowPayload.registry_revision) + 1
productionPayload.activation_scope = 'production_active'
productionPayload.runtime_integration = 'deployed'
const productionDigest = digestValue(productionPayload)
const productionSnapshotId =
  `registry-snapshot-sha256:${productionDigest.slice('sha256:'.length)}`
const productionSnapshot = {
  schema_version: 'country_outage_p2_s0b_registry_snapshot_v1',
  registry_snapshot_id: productionSnapshotId,
  snapshot_digest: productionDigest,
  created_at: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
  production_deployed: true,
  snapshot_payload: productionPayload,
}
const productionCandidate = {
  schema_version: 'country_outage_p2_s0b6_production_candidate_v1',
  candidate_id: productionCandidateId,
  registry_snapshot_id: productionSnapshotId,
  registry_revision: productionPayload.registry_revision,
  release_id: releaseId,
  source_commit: sourceCommit,
  source_tag: sourceTag,
  rollback_release_id: rollbackReleaseId,
  base_shadow_candidate_id: shadowCandidate.candidate_id,
  base_shadow_registry_snapshot_id: shadowSnapshot.registry_snapshot_id,
  promotion_identity_digest: promotionIdentityDigest,
  activation_scope: 'production_active',
  runtime_integration: 'deployed',
  production_deployed: true,
}

copyFileSync(candidatePath, join(contractRoot, 'candidate.shadow.json'))
copyFileSync(snapshotPath, join(contractRoot, 'registry-snapshot.shadow.json'))
writeJson(snapshotPath, productionSnapshot)
writeJson(candidatePath, productionCandidate)
const promotion = {
  ...promotionMaterial,
  promotion_identity_digest: promotionIdentityDigest,
  production_candidate_id: productionCandidateId,
  production_registry_snapshot_id: productionSnapshotId,
  production_registry_revision: productionPayload.registry_revision,
  files: {
    shadow_candidate: sha256File(join(contractRoot, 'candidate.shadow.json')),
    shadow_snapshot: sha256File(join(contractRoot, 'registry-snapshot.shadow.json')),
    production_candidate: sha256File(candidatePath),
    production_snapshot: sha256File(snapshotPath),
    shadow_acceptance_manifest: sha256File(acceptancePath),
    product_semantic_review: sha256File(reviewPath),
  },
  activation_scope: 'production_active',
  runtime_integration: 'deployed',
  production_deployed: true,
}
promotion.receipt_digest = digestValue(promotion)
writeJson(join(releaseRoot, 'P2-REGISTRY-PROMOTION.json'), promotion)
process.stdout.write(`${JSON.stringify({
  status: 'promoted',
  production_candidate_id: productionCandidateId,
  production_registry_snapshot_id: productionSnapshotId,
  registry_revision: productionPayload.registry_revision,
  rollback_release_id: rollbackReleaseId,
})}\n`)
