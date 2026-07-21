#!/usr/bin/env node
'use strict'

const fs = require('fs')
const path = require('path')

const root = path.resolve(__dirname, '..', '..')
const Ajv2020 = require(path.join(
  root,
  'frontend',
  'node_modules',
  '@redocly',
  'ajv',
  'dist',
  '2020',
)).default

const contracts = [
  'route-event',
  'metric-series',
  'evidence-bundle-v2',
  'data-quality-report',
]

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

function evidenceClosureErrors(payload) {
  const registryIds = payload.evidence_registry.map((item) => item.evidence_id)
  const registry = new Set(registryIds)
  const errors = []
  if (registry.size !== registryIds.length) {
    errors.push('evidence_registry.evidence_id 必须唯一')
  }

  const evidenceRefs = [
    ...payload.supporting_evidence_refs,
    ...payload.counterevidence_refs,
    ...Object.values(payload.phase_coverage).flatMap((phase) => phase.evidence_ids),
    ...payload.limitations.flatMap((limitation) => limitation.evidence_refs),
  ]
  for (const evidenceId of evidenceRefs) {
    if (!registry.has(evidenceId)) {
      errors.push(`Evidence ID 未在注册表中：${evidenceId}`)
    }
  }

  const routeEventIds = new Set(payload.route_event_refs.map((item) => item.route_event_id))
  const rawRecordIds = new Set(payload.raw_record_refs.map((item) => item.raw_record_ref_id))
  for (const phase of Object.values(payload.phase_coverage)) {
    for (const routeEventId of phase.route_event_ref_ids) {
      if (!routeEventIds.has(routeEventId)) {
        errors.push(`阶段 RouteEvent 引用未闭合：${routeEventId}`)
      }
    }
  }
  for (const routeEvent of payload.route_event_refs) {
    for (const rawRecordId of routeEvent.raw_record_ref_ids) {
      if (!rawRecordIds.has(rawRecordId)) {
        errors.push(`RouteEvent 原始记录引用未闭合：${rawRecordId}`)
      }
    }
  }
  return errors
}

function semanticErrors(name, payload) {
  if (name === 'evidence-bundle-v2') {
    return evidenceClosureErrors(payload)
  }
  return []
}

const ajv = new Ajv2020({
  allErrors: true,
  allowUnionTypes: true,
  strict: true,
  validateFormats: true,
})
ajv.addFormat('date-time', { type: 'string', validate: utcDateTime })
ajv.addFormat('uri', { type: 'string', validate: (value) => typeof value === 'string' && value.length > 0 })
ajv.addFormat('uri-reference', { type: 'string', validate: (value) => typeof value === 'string' })
ajv.addFormat('ipv4', {
  type: 'string',
  validate: (value) => {
    const parts = typeof value === 'string' ? value.split('.') : []
    return parts.length === 4 && parts.every((part) => /^(0|[1-9]\d{0,2})$/.test(part) && Number(part) <= 255)
  },
})
ajv.addFormat('ipv6', { type: 'string', validate: (value) => typeof value === 'string' && value.includes(':') })

const schemas = new Map()
for (const name of contracts) {
  const schemaPath = path.join(root, 'contracts', 'data', `${name}.schema.json`)
  const schema = readJson(schemaPath)
  if (schema.$schema !== 'https://json-schema.org/draft/2020-12/schema') {
    throw new Error(`${schemaPath} 必须声明 JSON Schema 2020-12`)
  }
  if (typeof schema.$id !== 'string' || schema.$id.length === 0) {
    throw new Error(`${schemaPath} 缺少稳定 $id`)
  }
  ajv.addSchema(schema)
  schemas.set(name, { schema, schemaPath })
}

let validCount = 0
let invalidCount = 0
for (const [name, { schema, schemaPath }] of schemas) {
  const validate = ajv.getSchema(schema.$id)
  if (typeof validate !== 'function') {
    throw new Error(`无法编译合同：${schemaPath}`)
  }
  const fixtureDir = path.join(root, 'contracts', 'data', 'fixtures', name)
  const validFixtures = fixtureFiles(fixtureDir, 'valid')
  const invalidFixtures = fixtureFiles(fixtureDir, 'invalid')
  if (validFixtures.length < 2 || invalidFixtures.length < 4) {
    throw new Error(`${name} 至少需要 2 个 valid 和 4 个 invalid fixture`)
  }
  for (const file of validFixtures) {
    const payload = readJson(file)
    if (!validate(payload)) {
      throw new Error(`正例未通过 ${file}: ${ajv.errorsText(validate.errors, { separator: '; ' })}`)
    }
    const errors = semanticErrors(name, payload)
    if (errors.length > 0) {
      throw new Error(`正例语义引用未闭合 ${file}: ${errors.join('; ')}`)
    }
    validCount += 1
  }
  for (const file of invalidFixtures) {
    const payload = readJson(file)
    const schemaValid = validate(payload)
    const errors = schemaValid ? semanticErrors(name, payload) : []
    if (schemaValid && errors.length === 0) {
      throw new Error(`反例错误通过：${file}`)
    }
    invalidCount += 1
  }
}

process.stdout.write(`P0 数据合同验证通过：${contracts.length} 个 Schema，${validCount} 个正例，${invalidCount} 个反例。\n`)
