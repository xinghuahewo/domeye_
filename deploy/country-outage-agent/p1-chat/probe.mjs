#!/usr/bin/env node

import { readFileSync } from 'node:fs'

function fail(message) {
  process.stderr.write(`P1 Sidecar 探针失败：${message}\n`)
  process.exit(1)
}

const [configPath] = process.argv.slice(2)
if (!configPath) fail('用法：probe.mjs <config>')
const values = new Map()
for (const [index, line] of readFileSync(configPath, 'utf8').split('\n').entries()) {
  if (!line || line.startsWith('#')) continue
  const separator = line.indexOf('=')
  if (separator <= 0) fail(`配置第 ${index + 1} 行无效`)
  const key = line.slice(0, separator)
  const value = line.slice(separator + 1)
  if (values.has(key)) fail(`配置键重复 ${key}`)
  values.set(key, value)
}
const baseUrl = values.get('COUNTRY_OUTAGE_AGENT_URL')
const token = values.get('COUNTRY_OUTAGE_AGENT_SHARED_TOKEN')
if (baseUrl !== 'http://127.0.0.1:28475' || !token || token.length < 32) {
  fail('固定 URL 或 Token 无效')
}
const headers = {
  Accept: 'application/json',
  Authorization: `Bearer ${token}`,
  'X-Domeye-User': 'domeye-p1-readiness',
  'X-Domeye-Authorization-Scope': 'country_outage_event_read:IR',
}
let readiness
try {
  const response = await fetch(`${baseUrl}/country-outage/chat/readiness`, {
    headers,
    redirect: 'manual',
    signal: AbortSignal.timeout(5_000),
  })
  readiness = await response.json()
  if (response.status !== 200) fail(`readiness HTTP ${response.status}`)
} catch (error) {
  fail(`readiness 不可达：${error.message}`)
}
if (
  readiness?.schema_version !== 'country_outage_p1_chat_readiness_v1' ||
  readiness?.ready !== true ||
  readiness?.event_type !== 'country_outage' ||
  readiness?.collector_id !== 'rrc25' ||
  readiness?.maximum_provider_request_count_per_turn !== 1 ||
  readiness?.report_capability !== 'disabled' ||
  readiness?.external_evidence !== 'disabled' ||
  readiness?.event_window_trend_operator?.execution_unit !== 'OP-04' ||
  readiness?.event_window_trend_operator?.capability_id !== 'CAP-TREND-001' ||
  readiness?.event_window_trend_operator?.operator_id !== 'event-window-trend' ||
  readiness?.event_window_trend_operator?.operator_version !== '1.2.0' ||
  readiness?.event_window_trend_operator?.model_dependency !== 'none' ||
  !/^p2-s0b6-[a-f0-9]{16}$/.test(
    readiness?.tool_operator_registry?.candidate_id ?? '',
  ) ||
  !/^registry-snapshot-sha256:[a-f0-9]{64}$/.test(
    readiness?.tool_operator_registry?.registry_snapshot_id ?? '',
  ) ||
  !Number.isSafeInteger(readiness?.tool_operator_registry?.registry_revision) ||
  readiness?.tool_operator_registry?.activation_scope !== 'production_active' ||
  readiness?.tool_operator_registry?.runtime_integration !== 'deployed' ||
  readiness?.tool_operator_registry?.production_deployed !== true
) fail('readiness 合同漂移')

const reportResponse = await fetch(`${baseUrl}/country-outage/reports`, {
  headers,
  redirect: 'manual',
  signal: AbortSignal.timeout(5_000),
})
if (reportResponse.status !== 404) fail('P1 Sidecar 暴露了报告路由')

process.stdout.write(`${JSON.stringify({
  status: 'ready',
  host: '127.0.0.1',
  port: 28475,
  model_profile: readiness.model_profile,
  registry_version: readiness.registry_version,
  certification_evidence_id: readiness.certification_evidence_id,
  event_window_trend_operator: readiness.event_window_trend_operator,
  tool_operator_registry: readiness.tool_operator_registry,
  report_capability: 'disabled',
  fee_audit_gate: 'not_required',
})}\n`)
