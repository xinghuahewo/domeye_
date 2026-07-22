#!/usr/bin/env node
'use strict'

const fs = require('fs')
const path = require('path')

const root = path.resolve(__dirname, '..', '..')
const contractRoot = path.join(root, 'contracts', 'research')
const Ajv2020 = require(path.join(
  root,
  'frontend',
  'node_modules',
  '@redocly',
  'ajv',
  'dist',
  '2020',
)).default

const outputContracts = new Set([
  'research-run',
  'country-outage-sample',
  'country-outage-episode',
  'country-outage-wave',
  'country-outage-episode-as',
  'reconciliation-result',
  'research-evidence-sidecar',
])

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'))
}

function utcDateTime(value) {
  if (typeof value !== 'string') return false
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$/.exec(value)
  if (!match) return false
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString().replace('.000Z', 'Z') === value
}

function fixtureFiles(directory, prefix) {
  return fs.readdirSync(directory)
    .filter((name) => name.startsWith(prefix) && name.endsWith('.json'))
    .sort()
    .map((name) => path.join(directory, name))
}

function semanticSampleErrors(payload) {
  const errors = []
  const slotSeconds = (Date.parse(payload.slot.end) - Date.parse(payload.slot.start)) / 1000
  if (slotSeconds !== payload.slot.granularity_seconds) {
    errors.push('sample 槽端点必须严格相差 300 秒')
  }

  const checkIdentity = (value, field) => {
    if (!value || typeof value !== 'object') return
    if (value.sample_id !== payload.sample_id) errors.push(`${field}.sample_id 与样本不一致`)
    if (value.snapshot_id !== payload.snapshot_id) errors.push(`${field}.snapshot_id 与快照不一致`)
  }
  for (const [name, measure] of Object.entries(payload.metrics)) {
    checkIdentity(measure, `metrics.${name}`)
  }
  for (const [name, setValue] of Object.entries(payload.asn_sets)) {
    checkIdentity(setValue, `asn_sets.${name}`)
  }

  const ratio = payload.metrics.damaged_asn_ratio
  if (ratio.numerator !== null && ratio.denominator !== null) {
    checkIdentity(ratio.numerator, 'metrics.damaged_asn_ratio.numerator')
    checkIdentity(ratio.denominator, 'metrics.damaged_asn_ratio.denominator')
    if (ratio.numerator.snapshot_id !== ratio.denominator.snapshot_id) {
      errors.push('比例分子与分母必须来自同一快照')
    }
    const expected = ratio.numerator.value / ratio.denominator.value
    if (Math.abs(expected - ratio.value) > 1e-12) errors.push('比例值与同快照分子分母不一致')
    if (ratio.value === 0 && ratio.value_state !== 'observed_zero') {
      errors.push('真实零比例必须标为 observed_zero')
    }
  }
  return errors
}

function semanticRunErrors(payload) {
  const errors = []
  const gateIds = payload.quality_gates.map((gate) => gate.gate_id)
  if (new Set(gateIds).size !== gateIds.length) errors.push('质量门 gate_id 必须唯一')
  if (payload.acceptance_state === 'accepted') {
    const failed = payload.quality_gates.filter((gate) => gate.blocking && gate.status !== 'pass')
    if (failed.length > 0) errors.push('accepted 运行不能包含未通过的阻断质量门')
    const missing = [...new Set([
      'input_integrity', 'parse_integrity', 'state_continuity', 'vp_coverage',
      'mapping_coverage', 'stable_identity', 'reference_closure',
      'unknown_missingness', 'resource_usage', 'reproducibility',
    ].filter((name) => !gateIds.includes(name)))]
    if (missing.length > 0) errors.push(`accepted 运行缺少质量门：${missing.join(',')}`)
  }
  if (payload.execution.finished_at !== null && Date.parse(payload.execution.finished_at) < Date.parse(payload.execution.started_at)) {
    errors.push('运行完成时间早于开始时间')
  }
  return errors
}

function semanticEpisodeErrors(payload) {
  const errors = []
  if (Date.parse(payload.detected_at) < Date.parse(payload.onset_at)) errors.push('detected_at 早于 onset_at')
  if (Date.parse(payload.observation_end_at) < Date.parse(payload.detected_at)) errors.push('observation_end_at 早于 detected_at')
  if (payload.partial_recovery_at !== null && Date.parse(payload.partial_recovery_at) < Date.parse(payload.onset_at)) {
    errors.push('partial_recovery_at 早于 onset_at')
  }
  if (payload.full_recovery_at !== null && payload.partial_recovery_at !== null && Date.parse(payload.full_recovery_at) < Date.parse(payload.partial_recovery_at)) {
    errors.push('full_recovery_at 早于 partial_recovery_at')
  }
  const duration = payload.duration
  if (duration.duration_state === 'interval' && duration.maximum_seconds < duration.minimum_seconds) {
    errors.push('持续时间区间上界小于下界')
  }
  return errors
}

function semanticWaveErrors(payload) {
  const errors = []
  if (Date.parse(payload.detected_at) < Date.parse(payload.onset_at)) errors.push('wave detected_at 早于 onset_at')
  if (Date.parse(payload.trough_at) < Date.parse(payload.onset_at)) errors.push('wave trough_at 早于 onset_at')
  if (payload.split_evidence !== null) {
    const split = payload.split_evidence
    if (split.rebound_amplitude.sample_id !== split.rebound_sample_id) errors.push('回升幅度未绑定回升样本')
    if (split.new_decline_amplitude.sample_id !== split.new_decline_sample_id) errors.push('再次下降幅度未绑定下降样本')
  }
  return errors
}

function semanticEpisodeAsErrors(payload) {
  const errors = []
  const ipv4 = payload.address_families.ipv4.visibility.fully_invisible
  const ipv6 = payload.address_families.ipv6.visibility.fully_invisible
  if (ipv4 === true && ipv6 === true && payload.overall_classification !== 'dual_stack_fully_invisible') {
    errors.push('双栈均完全不可见时综合分类不一致')
  }
  if (ipv4 === true && ipv6 === false && payload.overall_classification !== 'ipv4_only_fully_invisible') {
    errors.push('IPv4 完全不可见但 IPv6 可见时不能标为双栈完全不可见')
  }
  if (ipv4 === false && ipv6 === true && payload.overall_classification !== 'ipv6_only_fully_invisible') {
    errors.push('IPv6 完全不可见但 IPv4 可见时综合分类不一致')
  }
  if (payload.recovered_at !== null && payload.last_damaged_at !== null && Date.parse(payload.recovered_at) < Date.parse(payload.last_damaged_at)) {
    errors.push('ASN recovered_at 早于 last_damaged_at')
  }
  return errors
}

function semanticReconciliationErrors(payload) {
  const errors = []
  const ids = payload.evidence_registry.map((item) => item.evidence_id)
  const registry = new Set(ids)
  if (registry.size !== ids.length) errors.push('对账 evidence_id 必须唯一')
  const claimIds = payload.claims.map((item) => item.claim_id)
  if (new Set(claimIds).size !== claimIds.length) errors.push('claim_id 必须唯一')
  for (const claim of payload.claims) {
    for (const ref of [...claim.evidence_refs, ...claim.counterevidence_refs]) {
      if (!registry.has(ref)) errors.push(`主张引用未闭合：${claim.claim_id} -> ${ref}`)
    }
    const causal = new Set(['active_withdrawal_intent', 'physical_cut', 'bgp_session_closed', 'traffic_impact', 'government_intent'])
    if (causal.has(claim.claim_type) && claim.evidence_scope === 'rrc25_only' && ['confirmed', 'revised'].includes(claim.rating)) {
      errors.push(`RRC25 单源因果结论越界：${claim.claim_id}`)
    }
  }
  const actual = {confirmed: 0, revised: 0, unverifiable: 0, hypothesis_only: 0}
  for (const claim of payload.claims) actual[claim.rating] += 1
  for (const [rating, count] of Object.entries(actual)) {
    if (payload.summary[rating] !== count) errors.push(`summary.${rating} 与主张计数不一致`)
  }
  return errors
}

function semanticResearchEvidenceSidecarErrors(payload) {
  const errors = []
  const ids = (values, field) => {
    const result = values.map((item) => item[field])
    if (new Set(result).size !== result.length) errors.push(`${field} 必须唯一`)
    return new Set(result)
  }
  const sameSet = (left, right, message) => {
    if (left.size !== right.size || [...left].some((value) => !right.has(value))) errors.push(message)
  }

  const bundleIds = ids(payload.bundle_refs, 'bundle_id')
  const bundleIncidentIds = new Set(payload.bundle_refs.map((item) => item.incident_id))
  const incidentIds = ids(payload.incident_episode_links, 'incident_id')
  const incidentBundleIds = new Set(payload.incident_episode_links.map((item) => item.bundle_id))
  sameSet(new Set(payload.mapping.incident_ids), incidentIds, 'mapping.incident_ids 与 Incident link 不闭合')
  sameSet(new Set(payload.mapping.bundle_ids), bundleIds, 'mapping.bundle_ids 与 Bundle ref 不闭合')
  sameSet(bundleIncidentIds, incidentIds, 'Bundle→Incident 引用未闭合')
  sameSet(incidentBundleIds, bundleIds, 'Incident→Bundle 引用未闭合')

  const waveIds = ids(payload.wave_refs, 'wave_id')
  const sampleIds = ids(payload.sample_refs, 'sample_id')
  sameSet(new Set(payload.episode_ref.wave_ids), waveIds, 'episode→wave 引用未闭合')
  sameSet(new Set(payload.episode_ref.supporting_sample_ids), sampleIds, 'episode→sample 引用未闭合')
  for (const item of payload.incident_episode_links) {
    if (item.episode_id !== payload.episode_ref.episode_id) errors.push('Incident→episode 引用未闭合')
    for (const sampleId of item.evidence_sample_ids) {
      if (!sampleIds.has(sampleId)) errors.push(`Incident evidence sample 未解析：${sampleId}`)
    }
  }
  for (const wave of payload.wave_refs) {
    for (const sampleId of wave.supporting_sample_ids) {
      if (!sampleIds.has(sampleId)) errors.push(`wave sample 未解析：${sampleId}`)
    }
  }

  const linkSampleIds = ids(payload.sample_route_event_links, 'sample_id')
  sameSet(linkSampleIds, sampleIds, 'sample→RouteEvent link 未覆盖全部样本')
  const routeIds = ids(payload.route_event_refs, 'route_event_id')
  const linkedRoutes = new Set(payload.sample_route_event_links.flatMap((item) => item.route_event_ids))
  sameSet(linkedRoutes, routeIds, 'sample→RouteEvent 引用未闭合')
  for (const route of payload.route_event_refs) {
    for (const bundleId of route.bundle_ids) {
      if (!bundleIds.has(bundleId)) errors.push(`RouteEvent Bundle 未解析：${bundleId}`)
    }
  }

  const rawIds = ids(payload.raw_record_refs, 'raw_record_ref_id')
  const linkedRaw = new Set(payload.route_event_refs.flatMap((item) => item.raw_record_ref_ids))
  sameSet(linkedRaw, rawIds, 'RouteEvent→raw 引用未闭合')
  const artifactIds = ids(payload.artifact_refs, 'artifact_id')
  const linkedArtifacts = new Set(payload.raw_record_refs.map((item) => item.artifact_id))
  sameSet(linkedArtifacts, artifactIds, 'raw→artifact 引用未闭合')
  for (const artifact of payload.artifact_refs) {
    const expectedRaw = new Set(
      payload.raw_record_refs
        .filter((item) => item.artifact_id === artifact.artifact_id)
        .map((item) => item.raw_record_ref_id),
    )
    sameSet(new Set(artifact.raw_record_ref_ids), expectedRaw, 'artifact raw 反向引用未闭合')
  }
  for (const candidate of payload.recovery_assessment.candidates) {
    for (const sampleId of candidate.supporting_sample_ids) {
      if (!sampleIds.has(sampleId)) errors.push(`恢复候选 sample 未解析：${sampleId}`)
    }
  }
  for (const fact of payload.legacy_source_fact_refs) {
    if (!bundleIds.has(fact.bundle_id)) errors.push(`legacy fact Bundle 未解析：${fact.bundle_id}`)
    if (!incidentIds.has(fact.incident_id)) errors.push(`legacy fact Incident 未解析：${fact.incident_id}`)
  }
  if (payload.reference_closure.unresolved_refs.length !== 0) errors.push('unresolved_refs 必须为空')
  if (payload.conclusion.causal_conclusion !== null) errors.push('研究 sidecar 禁止因果结论')
  return errors
}

function semanticErrors(name, payload) {
  if (name === 'country-outage-sample') return semanticSampleErrors(payload)
  if (name === 'research-run') return semanticRunErrors(payload)
  if (name === 'country-outage-episode') return semanticEpisodeErrors(payload)
  if (name === 'country-outage-wave') return semanticWaveErrors(payload)
  if (name === 'country-outage-episode-as') return semanticEpisodeAsErrors(payload)
  if (name === 'reconciliation-result') return semanticReconciliationErrors(payload)
  if (name === 'research-evidence-sidecar') return semanticResearchEvidenceSidecarErrors(payload)
  return []
}

const ajv = new Ajv2020({allErrors: true, allowUnionTypes: true, strict: true, validateFormats: true})
ajv.addFormat('date-time', {type: 'string', validate: utcDateTime})

const schemaFiles = fs.readdirSync(contractRoot)
  .filter((name) => name.endsWith('.schema.json'))
  .sort()
if (schemaFiles.length === 0) throw new Error('contracts/research 下没有 Schema')

const schemas = new Map()
for (const fileName of schemaFiles) {
  const name = fileName.replace(/\.schema\.json$/, '')
  const schemaPath = path.join(contractRoot, fileName)
  const schema = readJson(schemaPath)
  if (schema.$schema !== 'https://json-schema.org/draft/2020-12/schema') throw new Error(`${schemaPath} 必须声明 JSON Schema 2020-12`)
  if (typeof schema.$id !== 'string' || schema.$id.length === 0) throw new Error(`${schemaPath} 缺少稳定 $id`)
  ajv.addSchema(schema)
  schemas.set(name, schema)
}

for (const required of outputContracts) {
  if (!schemas.has(required)) throw new Error(`缺少研究输出合同：${required}.schema.json`)
}

let validCount = 0
let invalidCount = 0
for (const [name, schema] of schemas) {
  const fixtureDir = path.join(contractRoot, 'fixtures', name)
  if (!fs.existsSync(fixtureDir)) continue
  const validFixtures = fixtureFiles(fixtureDir, 'valid')
  const invalidFixtures = fixtureFiles(fixtureDir, 'invalid')
  if (validFixtures.length < 1 || invalidFixtures.length < 1) throw new Error(`${name} 至少需要 1 个 valid 和 1 个 invalid fixture`)
  const validate = ajv.getSchema(schema.$id)
  for (const file of validFixtures) {
    const payload = readJson(file)
    if (!validate(payload)) throw new Error(`正例未通过 ${file}: ${ajv.errorsText(validate.errors, {separator: '; '})}`)
    const errors = semanticErrors(name, payload)
    if (errors.length > 0) throw new Error(`正例语义未通过 ${file}: ${errors.join('; ')}`)
    validCount += 1
  }
  for (const file of invalidFixtures) {
    const payload = readJson(file)
    const schemaValid = validate(payload)
    const errors = schemaValid ? semanticErrors(name, payload) : []
    if (schemaValid && errors.length === 0) throw new Error(`反例错误通过：${file}`)
    invalidCount += 1
  }
}

process.stdout.write(`研究数据合同验证通过：${schemas.size} 个 Schema，${validCount} 个正例，${invalidCount} 个反例。\n`)
