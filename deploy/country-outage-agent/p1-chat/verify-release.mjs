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
const profile = registry.profiles?.[0]
const payload = { ...certification }
delete payload.evidence_id
const expectedEvidenceId =
  `evidence:p1-semantic-certification:${sha256(canonical(payload))}`

if (
  release.schema_version !== 'country_outage_p1_chat_release_v1' ||
  release.component !== 'country_outage_p1_chat_sidecar' ||
  release.boundaries?.collector !== 'rrc25' ||
  release.boundaries?.event_type !== 'country_outage' ||
  release.boundaries?.report_capability !== 'disabled' ||
  release.boundaries?.external_evidence !== 'disabled' ||
  release.billing?.business_cost_limit !== null ||
  release.billing?.per_provider_call_usage_and_estimated_cost !== 'required' ||
  release.runtime?.host !== '127.0.0.1' ||
  release.runtime?.port !== 28475 ||
  release.runtime?.maximum_provider_request_count_per_turn !== 1
) fail('release 边界或运行时合同漂移')

if (
  certification.schema_version !==
    'country_outage_p1_semantic_model_certification_v1' ||
  certification.status !== 'certified' ||
  certification.evidence_id !== expectedEvidenceId ||
  certification.metrics?.user_goal_fidelity < 0.95 ||
  certification.metrics?.grounding_legality_rate !== 1 ||
  certification.per_call_cost_recorded !== true ||
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

if (
  release.hashes?.certification_manifest !== sha256(readFileSync(certificationPath)) ||
  release.hashes?.certified_registry !== sha256(readFileSync(registryPath))
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
  report_capability: 'disabled',
})}\n`)
