#!/usr/bin/env node
'use strict'

const fs = require('fs')
const path = require('path')
const zlib = require('zlib')

const root = path.resolve(__dirname, '..', '..')
const contractRoot = path.join(root, 'contracts', 'research')
const Ajv2020 = require(path.join(
  root, 'frontend', 'node_modules', '@redocly', 'ajv', 'dist', '2020',
)).default

function fail(message) {
  process.stderr.write(`${message}\n`)
  process.exit(1)
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'))
}

function readJsonlGzip(file) {
  const text = zlib.gunzipSync(fs.readFileSync(file)).toString('utf8')
  if (text && !text.endsWith('\n')) fail(`JSONL 缺少结尾换行：${file}`)
  return text.split('\n').filter(Boolean).map((line, index) => {
    try { return JSON.parse(line) } catch (error) {
      fail(`JSONL 第 ${index + 1} 行非法：${file}`)
    }
  })
}

function utcDateTime(value) {
  if (typeof value !== 'string') return false
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$/.exec(value)
  if (!match) return false
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString().replace('.000Z', 'Z') === value
}

const ajv = new Ajv2020({allErrors: true, strict: false})
ajv.addFormat('date-time', {type: 'string', validate: utcDateTime})
for (const name of fs.readdirSync(contractRoot).filter((name) => name.endsWith('.schema.json')).sort()) {
  ajv.addSchema(readJson(path.join(contractRoot, name)))
}

const schemaIds = {
  sample: 'https://domeye.example/contracts/research/country-outage-sample.schema.json',
  episodeAs: 'https://domeye.example/contracts/research/country-outage-episode-as.schema.json',
  sampleSemantics: 'https://domeye.example/contracts/research/rrc25-full-window-sample-measurement-semantics.schema.json',
  episodeAsSemantics: 'https://domeye.example/contracts/research/rrc25-full-window-episode-as-measurement-semantics.schema.json',
  prefixImpact: 'https://domeye.example/contracts/research/rrc25-full-window-episode-prefix-impact.schema.json',
}
const validators = Object.fromEntries(Object.entries(schemaIds).map(([key, id]) => {
  const validate = ajv.getSchema(id)
  if (!validate) fail(`无法编译合同：${id}`)
  return [key, validate]
}))

function validateRows(kind, rows, file) {
  const validate = validators[kind]
  rows.forEach((row, index) => {
    if (!validate(row)) {
      fail(`${file} 第 ${index + 1} 行合同失败：${ajv.errorsText(validate.errors, {separator: '；'})}`)
    }
  })
}

const index = process.argv.indexOf('--package-root')
if (index === -1) {
  process.stdout.write(`${JSON.stringify({ok: true, compiled_schema_count: Object.keys(validators).length})}\n`)
  process.exit(0)
}
if (!process.argv[index + 1]) fail('--package-root 缺少目录')
const packageRoot = path.resolve(process.argv[index + 1])
const views = ['compatible', 'revised']
for (const view of views) {
  const sampleFile = path.join(packageRoot, 'data', `${view}-country-samples.jsonl.gz`)
  const sampleSidecarFile = path.join(packageRoot, 'data', `${view}-sample-measurement-semantics.jsonl.gz`)
  const episodeAsFile = path.join(packageRoot, 'data', `${view}-episode-as.jsonl.gz`)
  const episodeAsSidecarFile = path.join(packageRoot, 'data', `${view}-episode-as-measurement-semantics.jsonl.gz`)
  const prefixFile = path.join(packageRoot, 'data', `${view}-prefix-impact.jsonl.gz`)
  const samples = readJsonlGzip(sampleFile)
  const sampleSidecars = readJsonlGzip(sampleSidecarFile)
  const episodeAs = readJsonlGzip(episodeAsFile)
  const episodeAsSidecars = readJsonlGzip(episodeAsSidecarFile)
  const prefixes = readJsonlGzip(prefixFile)
  validateRows('sample', samples, sampleFile)
  validateRows('sampleSemantics', sampleSidecars, sampleSidecarFile)
  validateRows('episodeAs', episodeAs, episodeAsFile)
  validateRows('episodeAsSemantics', episodeAsSidecars, episodeAsSidecarFile)
  validateRows('prefixImpact', prefixes, prefixFile)

  const samplesById = new Map(samples.map((row) => [row.sample_id, row]))
  if (samplesById.size !== samples.length || sampleSidecars.length !== samples.length) {
    fail(`${view} sample 与 measurement-semantics 不是 1:1`)
  }
  for (const sidecar of sampleSidecars) {
    const sample = samplesById.get(sidecar.sample_id)
    if (!sample || sample.snapshot_id !== sidecar.snapshot_id || sample.cohort_view !== sidecar.cohort_view) {
      fail(`${view} sample sidecar 稳定身份未闭合`)
    }
    if (sidecar.source_value_state === 'observed_route_state_partial_vp_coverage') {
      for (const [name, state] of Object.entries(sidecar.metric_value_states)) {
        if (state === 'observed_route_state_partial_vp_coverage') {
          const projected = sample.metrics[name]
          if (projected.value !== null || projected.value_state !== 'unknown_state_gap') {
            fail(`${view} partial 指标 ${name} 在 v1 中被伪装为 observed`)
          }
        }
      }
      for (const [name, state] of Object.entries(sidecar.asn_set_value_states)) {
        if (state === 'observed_route_state_partial_vp_coverage') {
          const projected = sample.asn_sets[name]
          if (projected.value !== null || projected.value_state !== 'unknown_state_gap') {
            fail(`${view} partial ASN 集合 ${name} 在 v1 中被伪装为 observed`)
          }
        }
      }
    }
  }
  const episodeAsIds = new Set(episodeAs.map((row) => row.episode_as_id))
  if (episodeAsIds.size !== episodeAs.length || episodeAsSidecars.some((row) => !episodeAsIds.has(row.episode_as_id))) {
    fail(`${view} episode-AS sidecar 未闭合到 v1 记录`)
  }
}

process.stdout.write(`${JSON.stringify({ok: true, package_root: packageRoot, schema_validation: 'passed'})}\n`)
