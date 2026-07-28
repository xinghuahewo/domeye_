#!/usr/bin/env node
'use strict'

const fs = require('fs')
const path = require('path')
const crypto = require('crypto')

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
  'incident-episode-mapping',
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

function canonicalJson(value) {
  const normalize = (item) => {
    if (Array.isArray(item)) return item.map(normalize)
    if (item !== null && typeof item === 'object') {
      return Object.fromEntries(Object.keys(item).sort().map((key) => [key, normalize(item[key])]))
    }
    return item
  }
  return JSON.stringify(normalize(value))
}

function stableId(prefix, payload, length = 24) {
  return prefix + crypto.createHash('sha256').update(canonicalJson(payload), 'utf8').digest('hex').slice(0, length)
}

function sortedUnique(values) {
  return JSON.stringify(values) === JSON.stringify([...new Set(values)].sort())
}

function causalLocatorErrors(locator, pathPrefix = 'incident') {
  const errors = []
  if (locator.classification !== 'observation_only') errors.push(`${pathPrefix}.classification 必须为 observation_only`)
  if (!Object.prototype.hasOwnProperty.call(locator, 'causal_conclusion') || locator.causal_conclusion !== null) {
    errors.push(`${pathPrefix}.causal_conclusion 必须为 null`)
  }
  const safeStates = new Set(['unknown', 'undetermined', 'not_assessed', 'not_available', 'observation_only'])
  const causalTextTokens = [
    '因果', '根因', '前兆', 'causal', 'causality', 'root cause', 'root_cause',
    'rootcause', 'precursor',
  ]
  const nonAssertiveTextMarkers = [
    '未知', '未确定', '未评估', '未提供', '未验证', '尚未', '不确定', '无法',
    '不能', '不得', '禁止', '不可用', '待验证', '仅为假设', '假设', 'unknown',
    'undetermined', 'not assessed', 'not_assessed', 'not available', 'not_available',
    'unresolved', 'not_causal', 'noncausal', 'non-causal', 'not proven', 'unverified',
    'cannot', 'unable', 'hypothesis',
  ]
  const assertiveCausalText = /(?:(?:根因|root[ _]?cause)\s*(?:就是|是|为|在于|来自|系|=|:|：)|(?:事件|异常|中断|incident|outage).{0,16}(?:是|为|属于|构成|is|was).{0,16}(?:前兆|precursor)|(?:前兆|precursor)\s*(?:就是|是|为|已确认|confirmed|=|:|：))/i
  const walk = (value, currentPath) => {
    if (Array.isArray(value)) {
      value.forEach((item, index) => walk(item, `${currentPath}[${index}]`))
      return
    }
    if (typeof value === 'string') {
      const normalized = value.trim().toLowerCase()
      if (causalTextTokens.some((token) => normalized.includes(token))) {
        if (assertiveCausalText.test(normalized)) {
          errors.push(`${currentPath} 不得包含因果、根因或前兆断言`)
        } else if (!nonAssertiveTextMarkers.some((marker) => normalized.includes(marker))) {
          errors.push(`${currentPath} 含因果、根因或前兆语义，但没有显式未知或非断言限定`)
        }
      }
      return
    }
    if (value === null || typeof value !== 'object') return
    for (const [rawKey, nested] of Object.entries(value)) {
      const key = rawKey.toLowerCase()
      const normalizedKey = key.replace(/[\s-]+/g, '_')
      const nestedPath = `${currentPath}.${rawKey}`
      if (key === 'classification') {
        if (nested !== 'observation_only') errors.push(`${nestedPath} 必须为 observation_only`)
        continue
      }
      if (key === 'causal_conclusion') {
        if (nested !== null) errors.push(`${nestedPath} 禁止携带因果结论`)
        continue
      }
      if (['causal', 'causality', 'root_cause', 'rootcause', 'precursor', '因果', '根因', '前兆']
          .some((token) => normalizedKey.includes(token))) {
        const safe = nested === null || (typeof nested === 'string' && safeStates.has(nested.trim().toLowerCase()))
        if (!safe) errors.push(`${nestedPath} 不得包含因果、根因或前兆断言`)
      }
      walk(nested, nestedPath)
    }
  }
  walk(locator, pathPrefix)
  return errors
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

function semanticIncidentEpisodeMappingErrors(payload) {
  const errors = []
  const identity = {...payload}
  delete identity.mapping_id
  if (payload.mapping_id !== stableId('incident_episode_map_v1_', identity)) {
    errors.push('mapping_id 与规范内容不一致')
  }
  const links = payload.episode_links
  const episodeIds = links.map((item) => item.episode_id)
  if (!sortedUnique(episodeIds)) errors.push('episode_links 必须按 episode_id 去重排序')
  for (const link of links) {
    if (!sortedUnique(link.evidence_sample_ids)) {
      errors.push(`Episode ${link.episode_id} 的 evidence_sample_ids 必须去重排序`)
    }
    if (link.causal !== false) errors.push(`Episode ${link.episode_id} 映射必须 causal=false`)
  }
  const expectedState = links.length === 0
    ? 'no_research_episode'
    : links.length === 1
      ? 'single_research_episode'
      : 'multiple_research_episodes'
  if (payload.mapping_state !== expectedState) errors.push('mapping_state 与 Episode 链接基数不一致')
  if (links.length === 0 && (typeof payload.missing_reason_zh !== 'string' || payload.missing_reason_zh.length === 0)) {
    errors.push('零 Episode 映射必须提供缺失原因')
  }
  if (links.length > 0 && payload.missing_reason_zh !== null) errors.push('已关联 Episode 的映射不得携带缺失原因')
  return errors
}

function semanticErrors(name, payload) {
  if (name === 'country-outage-sample') return semanticSampleErrors(payload)
  if (name === 'research-run') return semanticRunErrors(payload)
  if (name === 'country-outage-episode') return semanticEpisodeErrors(payload)
  if (name === 'country-outage-wave') return semanticWaveErrors(payload)
  if (name === 'country-outage-episode-as') return semanticEpisodeAsErrors(payload)
  if (name === 'reconciliation-result') return semanticReconciliationErrors(payload)
  if (name === 'incident-episode-mapping') return semanticIncidentEpisodeMappingErrors(payload)
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
