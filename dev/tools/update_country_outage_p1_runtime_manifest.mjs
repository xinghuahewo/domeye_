#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { existsSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const repositoryRoot = resolve(fileURLToPath(new URL('../..', import.meta.url)))
const manifestPath = resolve(
  repositoryRoot,
  'evaluation/country-outage/p1-runtime-v2/manifest.json',
)
const identityPath = resolve(
  repositoryRoot,
  'evaluation/country-outage/p1-runtime-v2/candidate-identity.json',
)
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
const identity = JSON.parse(readFileSync(identityPath, 'utf8'))

const requiredS4Paths = [
  'agent-sidecar/src/chat/deterministic-engine.ts',
  'agent-sidecar/src/chat/runtime-v2-conversation.ts',
  'agent-sidecar/src/chat/runtime-v2-semantic.ts',
  'agent-sidecar/tests/p1-http-handler.test.ts',
  'agent-sidecar/tests/p1-runtime-v2-conversation.test.ts',
  'dev/tools/generate_country_outage_p1_candidate_identity.mjs',
  'dev/tools/capture_country_outage_p1_s4_live_evidence.mjs',
  'dev/tools/capture_country_outage_p1_s4_browser_api.mjs',
  'dev/tools/evaluate_country_outage_p1_s2_semantics.py',
  'dev/tools/update_country_outage_p1_runtime_manifest.mjs',
  'dev/tools/validate_country_outage_p1_s4.py',
  'dev/tests/test_country_outage_p1_s4.py',
  'evaluation/country-outage/p1-runtime-v2/candidate-identity.json',
  'evaluation/country-outage/p1-runtime-v2/s4-runtime-budgets.json',
  'evaluation/country-outage/p1-runtime-v2/s4-p0-v1-3-results.json',
  'evaluation/country-outage/p1-runtime-v2/s4-p0-live-evidence.json',
  'evaluation/country-outage/p1-runtime-v2/s4-semantic-current-prompt-candidate.json',
  'evaluation/country-outage/p1-runtime-v2/s4-semantic-current-prompt-evaluation.json',
  'evaluation/country-outage/p1-runtime-v2/s4-joint-acceptance.json',
  'evaluation/country-outage/p1-runtime-v2/s4-browser-api-conversation.json',
  'evaluation/country-outage/p1-runtime-v2/s4-browser-desktop.png',
  'evaluation/country-outage/p1-runtime-v2/s4-browser-narrow.png',
  'evaluation/country-outage/p1-runtime-v2/s4-browser-desktop.snapshot.txt',
  'evaluation/country-outage/p1-runtime-v2/stage-receipts/S4.json',
  'docs/agent/P1-聊天问答/P1-runtime-v2-阶段与最终验收记录.md',
  'docs/agent/P1-聊天问答/P1-runtime-v2-P2入口回执.md',
]

if (!existsSync(resolve(
  repositoryRoot,
  'evaluation/country-outage/p1-runtime-v2/stage-receipts/S4.json',
))) {
  throw new Error('S4 阶段回执尚未存在，拒绝把 manifest 标记为 completed')
}

const existingPaths = manifest.artifacts.map((item) => item.path)
const allPaths = [...existingPaths]
for (const path of requiredS4Paths) {
  if (!allPaths.includes(path)) allPaths.push(path)
}

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

manifest.candidate_id = identity.candidate_id
manifest.stages.S4 = 'completed'
manifest.artifacts = allPaths.map((path) => {
  const absolute = resolve(repositoryRoot, path)
  if (!existsSync(absolute)) throw new Error(`manifest 制品不存在：${path}`)
  return {
    path,
    size_bytes: statSync(absolute).size,
    sha256: sha256(absolute),
  }
})
manifest.boundary = {
  s0_contract_accepted: true,
  single_turn_accepted: true,
  semantic_planner_accepted: true,
  multi_turn_accepted: true,
  joint_acceptance: true,
  local_candidate_only: true,
  merged: false,
  deployed: false,
  production_verified: false,
  p2_or_rca: false,
}

writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
process.stdout.write(`${identity.candidate_id} ${manifest.artifacts.length} artifacts\n`)
