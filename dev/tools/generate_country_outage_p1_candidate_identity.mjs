#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { readFileSync, statSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const repositoryRoot = resolve(fileURLToPath(new URL('../..', import.meta.url)))
const outputPath = resolve(
  repositoryRoot,
  'evaluation/country-outage/p1-runtime-v2/candidate-identity.json',
)

const artifactPaths = [
  '.codex/TASK.json',
  'config/agent-program/P1.json',
  'docs/agent/P1-聊天问答/Task-Spec-最终验收文档.md',
  'docs/agent/P1-聊天问答/Plan-分阶段计划.md',
  'evaluation/country-outage/p0-v1-3/manifest.json',
  'contracts/agent/country-outage-p1-runtime-v2/README.md',
  'contracts/agent/country-outage-p1-runtime-v2/capability-catalog.json',
  'contracts/agent/country-outage-p1-runtime-v2/tool-contracts.json',
  'contracts/agent/country-outage-p1-runtime-v2/oracle.json',
  'contracts/agent/country-outage-p1-runtime-v2/semantic-plan.schema.json',
  'contracts/agent/country-outage-p1-runtime-v2/policy.json',
  'evaluation/country-outage/p1-runtime-v2/oracle-fixtures.json',
  'evaluation/country-outage/p1-runtime-v2/s2-semantic-variants.json',
  'evaluation/country-outage/p1-runtime-v2/s4-runtime-budgets.json',
  'agent-sidecar/src/chat/codex-cli-semantic-model.ts',
  'agent-sidecar/src/chat/runtime-v2-semantic.ts',
  'agent-sidecar/src/chat/runtime-v2-conversation.ts',
  'agent-sidecar/src/chat/runtime-v2-single-turn.ts',
  'agent-sidecar/src/chat/deterministic-engine.ts',
  'agent-sidecar/src/cli/serve-acceptance.ts',
  'agent-sidecar/src/server/http-handler.ts',
  'agent-sidecar/tests/p1-runtime-v2-semantic.test.ts',
  'agent-sidecar/tests/p1-runtime-v2-conversation.test.ts',
  'agent-sidecar/tests/p1-runtime-v2.test.ts',
  'backend/web/api/v2/country_outage_agent_proxy.py',
  'backend/web/api/v2/country_outage_chat_proxy.py',
  'backend/web/api/v2/route.py',
  'backend/web/tests/test_country_outage_chat_proxy.py',
  'frontend/src/api/countryOutageChat.ts',
  'frontend/src/api/countryOutageChat.test.ts',
  'frontend/src/pages/CountryOutageChatPage.vue',
  'frontend/src/pages/CountryOutageChatPage.test.ts',
  'dev/tools/validate_country_outage_p1_runtime_v2.py',
  'dev/tests/test_country_outage_p1_runtime_v2.py',
  'dev/tools/validate_country_outage_p1_s4.py',
  'dev/tests/test_country_outage_p1_s4.py',
  'dev/tools/generate_country_outage_p1_candidate_identity.mjs',
  'evaluation/country-outage/p1-runtime-v2/stage-receipts/S0.json',
  'evaluation/country-outage/p1-runtime-v2/stage-receipts/S1.json',
  'evaluation/country-outage/p1-runtime-v2/stage-receipts/S2.json',
  'evaluation/country-outage/p1-runtime-v2/stage-receipts/S3.json',
]

function sha256Buffer(value) {
  return createHash('sha256').update(value).digest('hex')
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, stable(value[key])]),
    )
  }
  return value
}

const artifacts = artifactPaths.map((path) => {
  const absolute = resolve(repositoryRoot, path)
  const content = readFileSync(absolute)
  return {
    path,
    size_bytes: statSync(absolute).size,
    sha256: sha256Buffer(content),
  }
})

const basis = {
  base_spec_commit: '6cb2bd3',
  p0_entry_revision: 'p0-v1.3-20260809-ir-r1',
  collector_id: 'rrc25',
  semantic_model: {
    provider: 'codex-cli',
    model: 'gpt-5.6-sol',
    model_identity: 'codex-cli:0.147.0-alpha.6.5:gpt-5.6-sol:blind-v2',
    prompt_contract: 'runtime-prompt-v2',
    timeout_ms: 60000,
  },
  runtime: {
    implementation: 'p1-runtime-v2-conversation',
    persistence: 'ephemeral',
    candidate_scope: 'local_development_candidate',
  },
  event_binding: {
    event_type: 'country_outage',
    incident_id: 'incident_go_v1_a1de26f854831330c616a72af21597eb',
    legacy_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
    publication_id: 'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f',
    revision: 1,
    cohort_id: 'country_event_cohort_v1_1e04abfc6430776bef20403fac528698',
    data_through: '2026-03-11T00:00:00Z',
    is_final_in_data_range: false,
  },
  artifacts,
}

const canonical = JSON.stringify(stable(basis))
const digest = sha256Buffer(Buffer.from(canonical, 'utf8'))
const document = {
  schema_version: 'country_outage_p1_candidate_identity_v2',
  candidate_id: `p1-runtime-v2-${digest.slice(0, 16)}`,
  identity_basis_sha256: digest,
  basis_canonical_json: canonical,
  basis,
  generated_at: new Date().toISOString(),
  boundary: {
    accepted_local_candidate: false,
    merged: false,
    deployed: false,
    production_verified: false,
    p2_or_rca: false,
  },
}

writeFileSync(outputPath, `${JSON.stringify(document, null, 2)}\n`, 'utf8')
process.stdout.write(`${document.candidate_id}\n`)
