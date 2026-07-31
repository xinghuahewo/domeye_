#!/usr/bin/env node

import { readFileSync } from 'node:fs'

function fail(message) {
  process.stderr.write(`Sidecar readiness 探针失败：${message}\n`)
  process.exit(1)
}

function parseConfiguration(path) {
  const result = new Map()
  let raw
  try {
    raw = readFileSync(path, 'utf8')
  } catch (error) {
    fail(`无法读取运行配置：${error.message}`)
  }
  for (const [index, line] of raw.split('\n').entries()) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) continue
    const separator = line.indexOf('=')
    if (separator <= 0) fail(`运行配置第 ${index + 1} 行格式无效`)
    const key = line.slice(0, separator)
    const value = line.slice(separator + 1)
    if (result.has(key)) fail(`运行配置键重复：${key}`)
    result.set(key, value)
  }
  return result
}

const [configurationPath] = process.argv.slice(2)
if (!configurationPath) {
  fail('用法：probe-sidecar.mjs <country-outage-agent.env>')
}
const configuration = parseConfiguration(configurationPath)
const baseUrl = configuration.get('COUNTRY_OUTAGE_AGENT_URL')
const token = configuration.get('COUNTRY_OUTAGE_AGENT_SHARED_TOKEN')
const user = configuration.get('COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID')
if (
  baseUrl !== 'http://127.0.0.1:28474' ||
  !token ||
  token.length < 32 ||
  !user
) {
  fail('探针所需的固定 URL、内部凭据或用户身份无效')
}

let response
try {
  response = await fetch(
    `${baseUrl}/country-outage/capabilities/external-evidence`,
    {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${token}`,
        'X-Domeye-User': user,
        'X-Domeye-Authorization-Scope': 'country_outage_event_read:IR',
      },
      signal: AbortSignal.timeout(5_000),
      redirect: 'manual',
    },
  )
} catch (error) {
  fail(`无法访问 127.0.0.1:28474：${error.message}`)
}

let payload
try {
  payload = await response.json()
} catch {
  fail(`readiness 返回非 JSON，HTTP ${response.status}`)
}
if (
  response.status !== 200 ||
  payload?.schema_version !==
    'country_outage_external_evidence_capability_v1' ||
  payload?.capability !== 'external_evidence' ||
  payload?.state !== 'not_configured' ||
  payload?.provider !== 'disabled'
) {
  fail('readiness 未返回 external_evidence=not_configured/provider=disabled')
}

process.stdout.write(
  `${JSON.stringify({
    status: 'ready',
    host: '127.0.0.1',
    port: 28474,
    collector: 'rrc25',
    externalEvidence: payload.state,
    provider: payload.provider,
  })}\n`,
)
