#!/usr/bin/env node

import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const repositoryRoot = resolve(fileURLToPath(new URL('../..', import.meta.url)))
const evaluationRoot = resolve(
  repositoryRoot,
  'evaluation/country-outage/p1-runtime-v2',
)
const conversationId = process.env.P1_S4_CONVERSATION_ID
if (!conversationId) {
  throw new Error('必须通过 P1_S4_CONVERSATION_ID 指定浏览器实际会话')
}
const apiBase = process.env.P1_S4_API_BASE
  ?? 'http://127.0.0.1:29473/api/v2/country-outage/runtime-v2'
const identity = JSON.parse(readFileSync(
  resolve(evaluationRoot, 'candidate-identity.json'),
  'utf8',
))
const response = await fetch(
  `${apiBase}/conversations/${encodeURIComponent(conversationId)}`,
  { headers: { Accept: 'application/json' } },
)
const body = await response.text()
let payload
try {
  payload = JSON.parse(body)
} catch {
  throw new Error(`浏览器会话 API 不是 JSON（HTTP ${response.status}）`)
}
if (!response.ok) {
  throw new Error(`读取浏览器会话失败（HTTP ${response.status}）`)
}
const conversation = payload.conversation ?? payload
if (!conversation || conversation.conversation_id !== conversationId) {
  throw new Error('响应没有绑定指定浏览器会话')
}
if (conversation.binding?.collector_id !== 'rrc25') {
  throw new Error('浏览器会话不是 RRC25-only')
}
if (!Array.isArray(conversation.turns) || conversation.turns.length < 3) {
  throw new Error('浏览器会话不足三轮，不能作为联合验收回执')
}
for (const turn of conversation.turns) {
  if (
    turn.state !== 'completed'
    || turn.answer?.validation?.grounding_legality !== 'passed'
    || turn.answer?.execution_trace?.model_generated_fact_count !== 0
  ) {
    throw new Error(`浏览器会话轮次 ${turn.turn_number} 未闭合`)
  }
}
const document = {
  schema_version: 'country_outage_p1_s4_browser_api_conversation_v1',
  candidate_id: identity.candidate_id,
  captured_at: new Date().toISOString(),
  api_base: apiBase,
  conversation_id: conversationId,
  http_status: response.status,
  raw_response: payload,
  boundary: {
    browser_and_api_same_conversation: true,
    collector_id: 'rrc25',
    local_candidate_only: true,
    merged: false,
    deployed: false,
  },
}
writeFileSync(
  resolve(evaluationRoot, 's4-browser-api-conversation.json'),
  `${JSON.stringify(document, null, 2)}\n`,
  'utf8',
)
process.stdout.write(`${identity.candidate_id} ${conversationId} ${conversation.turns.length} turns\n`)
