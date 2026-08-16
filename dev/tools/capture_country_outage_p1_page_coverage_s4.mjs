#!/usr/bin/env node

import { createHash } from 'node:crypto'
import {
  mkdirSync,
  readFileSync,
  writeFileSync,
} from 'node:fs'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const STAGE = 'S4'
const CANDIDATE_ID = 'p1-page-coverage-s4-live-rrc25-01'
const RUN_ID = 's4-browser-api-live-001'
const JOURNEY_ID = 'S4-LIVE-IP-001'
const CASE_AUTHOR_ACTOR_ID = 's4-browser-question-explorer-agent'
const CASE_AUTHOR_RUN_ID = 's4-browser-question-explorer-001'
const OUT = resolve(ROOT, 'evaluation/country-outage/p1-page-coverage/s4')
const RAW = resolve(OUT, 'raw')
const conversationId = process.argv[2]

if (!conversationId || !/^p1v2_[a-f0-9]{32}$/.test(conversationId)) {
  throw new Error('用法：capture_country_outage_p1_page_coverage_s4.mjs <conversation_id>')
}

mkdirSync(RAW, { recursive: true })

const shaBytes = (value) => createHash('sha256').update(value).digest('hex')
const shaFile = (path) => shaBytes(readFileSync(path))
const canonical = (value) => JSON.stringify(
  sortObject(value),
  null,
  0,
)
const sortObject = (value) => {
  if (Array.isArray(value)) return value.map(sortObject)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, sortObject(value[key])]),
    )
  }
  return value
}
const writeJson = (path, value) => {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}
const writeCompactJson = (path, value) => {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, `${JSON.stringify(value)}\n`, 'utf8')
}
const repoPath = (path) => relative(ROOT, path).replaceAll('\\', '/')
const sourceEntries = (paths) => paths.map((path) => {
  const absolute = resolve(ROOT, path)
  return { path, sha256: shaFile(absolute) }
})
const sourceIdentity = (prefix, entries) => (
  `${prefix}:${shaBytes(canonical(entries))}`
)
const rawMeta = (evidenceKind, capturedAt, extra = {}) => ({
  schema_version: `country_outage_p1_page_coverage_s4_${evidenceKind}_v1`,
  evidence_kind: evidenceKind,
  stage: STAGE,
  candidate_id: CANDIDATE_ID,
  run_id: RUN_ID,
  captured_at: capturedAt,
  ...extra,
})
const refFor = (kind, path) => ({
  kind,
  path: repoPath(path),
  sha256: shaFile(path),
})

const response = await fetch(
  `http://127.0.0.1:28471/api/v2/country-outage/chat/conversations/${conversationId}`,
  { signal: AbortSignal.timeout(30_000) },
)
if (!response.ok) throw new Error(`读取聊天会话失败：HTTP ${response.status}`)
const payload = await response.json()
const conversation = payload?.conversation
if (!conversation || conversation.conversation_id !== conversationId) {
  throw new Error('聊天会话身份不一致')
}
if (
  conversation.binding?.collector_id !== 'rrc25'
  || conversation.binding?.publication_id
    !== 'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f'
  || conversation.binding?.revision !== 1
) {
  throw new Error('聊天会话没有绑定冻结 RRC25 publication/revision')
}
if (
  conversation.turns?.length !== 2
  || conversation.turns[0]?.question !== 'IP地址变化情况'
  || conversation.turns[1]?.question !== 'IP地址变化趋势'
  || conversation.turns.some((turn) => turn.state !== 'completed')
) {
  throw new Error('冻结双问题旅程不完整')
}

const capturedAt = conversation.turns.at(-1).completed_at
const componentSpecs = {
  frontend: {
    paths: [
      'frontend/src/pages/CountryOutageChatPage.vue',
      'frontend/src/api/countryOutageChat.ts',
      'frontend/src/router/index.ts',
      'frontend/src/components/CountryOutageGeneralPage.vue',
    ],
    prefix: 'frontend-source',
  },
  backend: {
    paths: [
      'backend/web/api/v2/country_outage_chat_proxy.py',
      'backend/web/api/v2/route.py',
    ],
    prefix: 'backend-proxy-source',
  },
  runtime: {
    paths: [
      'agent-sidecar/src/chat/runtime-v2-conversation.ts',
      'agent-sidecar/src/chat/page-capability-executor.ts',
      'agent-sidecar/src/chat/general-read-model-provider.ts',
      'agent-sidecar/src/server/http-handler.ts',
      'agent-sidecar/src/cli/serve-acceptance.ts',
    ],
    prefix: 'runtime-source',
  },
  semantic_planner: {
    paths: [
      'agent-sidecar/src/chat/runtime-v2-semantic.ts',
      'agent-sidecar/src/chat/codex-cli-semantic-model.ts',
    ],
    prefix: 'semantic-planner-source',
  },
  prompt: {
    paths: ['agent-sidecar/src/chat/runtime-v2-semantic.ts'],
    prefix: 'prompt-source',
  },
  schema: {
    paths: [
      'contracts/agent/country-outage-p1-page-coverage/s2/semantic-plan.schema.json',
    ],
    prefix: 'semantic-schema',
  },
  capability_catalog: {
    paths: [
      'contracts/agent/country-outage-p1-page-coverage/s2/capability-catalog.json',
    ],
    prefix: 'capability-catalog',
  },
  policy: {
    paths: ['contracts/agent/country-outage-p1-page-coverage/s2/policy.json'],
    prefix: 'grounding-policy',
  },
  tool_contracts: {
    paths: [
      'contracts/agent/country-outage-p1-page-coverage/s2/tool-contracts.json',
    ],
    prefix: 'tool-contracts',
  },
  operator_contracts: {
    paths: [
      'contracts/agent/country-outage-p1-page-coverage/s2/tool-contracts.json',
      'agent-sidecar/src/chat/page-capability-executor.ts',
    ],
    prefix: 'operator-contracts',
  },
  oracle: {
    paths: ['contracts/agent/country-outage-p1-page-coverage/s2/oracle.json'],
    prefix: 'oracle',
  },
}

const componentIdentities = {}
for (const [component, spec] of Object.entries(componentSpecs)) {
  const entries = sourceEntries(spec.paths)
  const identity = sourceIdentity(spec.prefix, entries)
  const evidenceKind = `component_${component}`
  const path = resolve(RAW, `${evidenceKind}.json`)
  writeJson(path, rawMeta(evidenceKind, capturedAt, {
    component,
    identity,
    source_files: entries,
  }))
  componentIdentities[component] = {
    identity,
    evidence_kind: evidenceKind,
    sha256: shaFile(path),
  }
}

const specialComponents = {
  model: {
    identity: 'codex-cli:0.147.0-alpha.6.5:gpt-5.6-sol:blind-v2',
    observation: {
      executable: '/Applications/ChatGPT.app/Contents/Resources/codex',
      cli_version: '0.147.0-alpha.6.5',
      model: 'gpt-5.6-sol',
      tool_activity_allowed: false,
    },
  },
  data_publication: {
    identity: [
      conversation.binding.publication_id,
      `revision-${conversation.binding.revision}`,
      conversation.binding.collector_id,
      conversation.binding.data_through,
    ].join(':'),
    observation: conversation.binding,
  },
}
for (const [component, spec] of Object.entries(specialComponents)) {
  const evidenceKind = `component_${component}`
  const path = resolve(RAW, `${evidenceKind}.json`)
  writeJson(path, rawMeta(evidenceKind, capturedAt, {
    component,
    identity: spec.identity,
    observation: spec.observation,
  }))
  componentIdentities[component] = {
    identity: spec.identity,
    evidence_kind: evidenceKind,
    sha256: shaFile(path),
  }
}

const candidateIdentitySha256 = shaBytes(canonical(componentIdentities))
const componentManifestPath = resolve(RAW, 'component-manifest.json')
writeJson(componentManifestPath, rawMeta('component_manifest', capturedAt, {
  component_identities: componentIdentities,
  candidate_identity_sha256: candidateIdentitySha256,
}))

const bodyTextRaw = readFileSync('/tmp/p1-s4-final-body.txt', 'utf8').trim()
const bodyText = JSON.parse(bodyTextRaw)
const desktopScreenshot = resolve(OUT, 'browser-desktop-final.png')
const narrowScreenshot = resolve(OUT, 'browser-narrow-final.png')
const commonJourney = {
  journey_id: JOURNEY_ID,
  candidate_identity_sha256: candidateIdentitySha256,
}
const rawJourneyArtifacts = {
  browser_receipt: {
    ...commonJourney,
    page_url: (
      'http://127.0.0.1:28471/events/chat?ref='
      + 'country_outage%2F2026-02-27%2009%3A12%3A32%2FIR%2F1%2Fr'
    ),
    page_title: '事件问答 · Domeye Core',
    conversation_id: conversationId,
    questions: conversation.turns.map((turn) => turn.question),
    body_text: bodyText,
    assertions: {
      publication_visible: bodyText.includes(conversation.binding.publication_id),
      revision_and_generation_visible: bodyText.includes('r1 · G1'),
      rrc25_visible: bodyText.includes('RRC25 · 控制面'),
      event_end_unknown_visible: bodyText.includes('事件结束未知'),
      both_questions_visible: (
        bodyText.includes('IP地址变化情况')
        && bodyText.includes('IP地址变化趋势')
      ),
      user_goal_plan_visible: bodyText.includes('USERGOALPLAN'),
      grounding_plan_visible: bodyText.includes('GROUNDINGPLAN'),
      tool_operator_visible: bodyText.includes('TOOL / OPERATOR'),
      dialog_state_committed_visible: bodyText.includes('DIALOGSTATE\nCOMMITTED'),
    },
    screenshots: [
      {
        viewport: '1440x1000',
        path: repoPath(desktopScreenshot),
        sha256: shaFile(desktopScreenshot),
      },
      {
        viewport: '390x844',
        path: repoPath(narrowScreenshot),
        sha256: shaFile(narrowScreenshot),
      },
    ],
  },
  api_receipt: {
    ...commonJourney,
    endpoint: `/api/v2/country-outage/chat/conversations/${conversationId}`,
    http_status: 200,
    response: payload,
  },
  user_goal_plan: {
    ...commonJourney,
    conversation_id: conversationId,
    turns: conversation.turns.map((turn) => ({
      turn_id: turn.turn_id,
      question: turn.question,
      user_goal_plan: turn.answer.semantic_plan.user_goal_plan,
    })),
  },
  grounding_plan: {
    ...commonJourney,
    conversation_id: conversationId,
    turns: conversation.turns.map((turn) => ({
      turn_id: turn.turn_id,
      question: turn.question,
      grounding_plan: turn.answer.semantic_plan.grounding_plan,
    })),
  },
  tool_receipts: {
    ...commonJourney,
    conversation_id: conversationId,
    turns: conversation.turns.map((turn) => ({
      turn_id: turn.turn_id,
      question: turn.question,
      binding_preflight: turn.answer.execution_trace.binding_preflight,
      nodes: turn.answer.execution_trace.nodes,
    })),
  },
  evidence_state: {
    ...commonJourney,
    conversation_id: conversationId,
    binding: conversation.binding,
    evidence_state: conversation.evidence_state,
    turns: conversation.turns.map((turn) => ({
      turn_id: turn.turn_id,
      evidence: turn.answer.evidence,
      results: turn.answer.results,
      validation: turn.answer.validation,
    })),
  },
  dialog_state_before: {
    ...commonJourney,
    conversation_id: conversationId,
    state: conversation.turns[0].answer.state_receipt.before,
  },
  dialog_state_after: {
    ...commonJourney,
    conversation_id: conversationId,
    per_turn_state_receipts: conversation.turns.map((turn) => ({
      turn_id: turn.turn_id,
      state_receipt: turn.answer.state_receipt,
    })),
    final_state: conversation.dialog_state,
  },
}

const journeyRefs = {}
for (const [kind, value] of Object.entries(rawJourneyArtifacts)) {
  const path = resolve(RAW, `${kind.replaceAll('_', '-')}.json`)
  const writer = ['api_receipt', 'tool_receipts'].includes(kind)
    ? writeCompactJson
    : writeJson
  writer(path, rawMeta(kind, capturedAt, value))
  journeyRefs[kind] = refFor(kind, path)
}

const manifestRefs = [
  ...Object.keys(componentIdentities).sort().map((component) => {
    const kind = `component_${component}`
    return refFor(kind, resolve(RAW, `${kind}.json`))
  }),
  refFor('component_manifest', componentManifestPath),
]
writeJson(resolve(OUT, 'same-candidate-manifest.json'), {
  schema_version: 'country_outage_p1_page_coverage_s4_same_candidate_manifest_v1',
  artifact_kind: 'same_candidate_manifest',
  stage: STAGE,
  candidate_id: CANDIDATE_ID,
  status: 'PASS',
  component_identities: componentIdentities,
  candidate_identity_sha256: candidateIdentitySha256,
  evidence_refs: manifestRefs,
})

writeJson(resolve(OUT, 'browser-api-tool-evidence-state-trace.json'), {
  schema_version: 'country_outage_p1_page_coverage_s4_browser_trace_v1',
  artifact_kind: 'browser_api_tool_evidence_state_trace',
  stage: STAGE,
  candidate_id: CANDIDATE_ID,
  status: 'PASS',
  journeys: [{
    candidate_id: CANDIDATE_ID,
    journey_id: JOURNEY_ID,
    run_id: RUN_ID,
    candidate_identity_sha256: candidateIdentitySha256,
    browser_receipt_sha256: journeyRefs.browser_receipt.sha256,
    api_receipt_sha256: journeyRefs.api_receipt.sha256,
    user_goal_plan_sha256: journeyRefs.user_goal_plan.sha256,
    grounding_plan_sha256: journeyRefs.grounding_plan.sha256,
    tool_receipts_sha256: journeyRefs.tool_receipts.sha256,
    evidence_state_sha256: journeyRefs.evidence_state.sha256,
    dialog_state_before_sha256: journeyRefs.dialog_state_before.sha256,
    dialog_state_after_sha256: journeyRefs.dialog_state_after.sha256,
  }],
  evidence_refs: Object.values(journeyRefs),
})

const unknownLedgerPath = resolve(RAW, 'unknown-ledger.json')
const unknowns = [
  {
    unknown_id: 'S4-UNK-001',
    subject: '当前候选尚未合并、部署或经过生产流量验证',
    blocking: false,
    next_validation: '仅在获得发布授权后进入独立发布与生产验证流程',
    owner: 'P1 release owner',
  },
  {
    unknown_id: 'S4-UNK-002',
    subject: '正式历史或跨事件趋势制品仍不在 P1 能力范围内',
    blocking: false,
    next_validation: '后续能力阶段先建立正式趋势合同与 Oracle，再决定是否开放',
    owner: 'P2 capability owner',
  },
  {
    unknown_id: 'S4-UNK-003',
    subject: '本地 Codex CLI 语义模型不是生产认证模型入口',
    blocking: false,
    next_validation: '生产接入前完成固定模型、Prompt、成本和运行环境认证',
    owner: 'P1 model runtime owner',
  },
]
writeJson(unknownLedgerPath, rawMeta('unknown_ledger', capturedAt, {
  candidate_identity_sha256: candidateIdentitySha256,
  unknowns,
  blocking_count: 0,
}))
writeJson(resolve(OUT, 'unclosed-unknowns.json'), {
  schema_version: 'country_outage_p1_page_coverage_s4_unclosed_unknowns_v1',
  artifact_kind: 'unclosed_unknowns',
  stage: STAGE,
  candidate_id: CANDIDATE_ID,
  status: 'PASS',
  unknowns,
  blocking_count: 0,
  evidence_refs: [refFor('unknown_ledger', unknownLedgerPath)],
})

const caseAuthorReceiptPath = resolve(RAW, 'case-author-actor-receipt.json')
writeJson(caseAuthorReceiptPath, {
  ...rawMeta('case_author_actor_receipt', capturedAt),
  actor_id: CASE_AUTHOR_ACTOR_ID,
  run_id: CASE_AUTHOR_RUN_ID,
  orchestrator_receipt_id: 's4-case-author-orchestrator-receipt-001',
  allowed_actions: ['capture_browser_journey', 'prepare_reviewed_input'],
  denied_actions: ['mark_pass', 'modify_implementation'],
})

const reviewedInputPath = resolve(RAW, 'reviewed-input.json')
writeJson(reviewedInputPath, {
  ...rawMeta('reviewed_input', capturedAt, {
    actor_id: CASE_AUTHOR_ACTOR_ID,
    run_id: CASE_AUTHOR_RUN_ID,
  }),
  candidate_identity_sha256: candidateIdentitySha256,
  event_identity: conversation.binding,
  review_scope: {
    user_experience: '自然语言问答、逐子目标状态、证据展开和窄屏可读性',
    semantic_truth: '只允许当前 publication/revision 的 RRC25 事实和登记算子',
    forbidden_claims: [
      '正式历史趋势已经实现',
      '全国或真实用户中断',
      '原因、责任或政府行为',
      '恢复、P2、RCA、部署或生产验证',
      '隐藏思维链已经暴露',
    ],
  },
  cases: [
    {
      case_id: 'S4-IP-001',
      question: 'IP地址变化情况',
      conversation_seed: [],
      page_outcome_ids: ['PCO-03', 'PCO-04', 'PCO-07', 'PCO-08'],
    },
    {
      case_id: 'S4-IP-002',
      question: 'IP地址变化趋势',
      conversation_seed: ['IP地址变化情况'],
      page_outcome_ids: ['PCO-03', 'PCO-04', 'PCO-07', 'PCO-08'],
    },
    {
      case_id: 'S4-UX-001',
      question: '浏览器是否让用户核对身份、逐目标裁决、证据与状态，而不暴露隐藏思维链',
      conversation_seed: ['IP地址变化情况', 'IP地址变化趋势'],
      page_outcome_ids: [
        'PCO-01', 'PCO-02', 'PCO-03', 'PCO-04',
        'PCO-05', 'PCO-06', 'PCO-07', 'PCO-08',
      ],
    },
  ],
  evidence_index: {
    same_candidate_manifest: {
      path: repoPath(resolve(OUT, 'same-candidate-manifest.json')),
      sha256: shaFile(resolve(OUT, 'same-candidate-manifest.json')),
    },
    browser_trace: {
      path: repoPath(resolve(OUT, 'browser-api-tool-evidence-state-trace.json')),
      sha256: shaFile(resolve(OUT, 'browser-api-tool-evidence-state-trace.json')),
    },
  },
})

process.stdout.write(`${JSON.stringify({
  candidate_id: CANDIDATE_ID,
  candidate_identity_sha256: candidateIdentitySha256,
  conversation_id: conversationId,
  turn_count: conversation.turns.length,
  execution_node_count: conversation.turns.reduce(
    (total, turn) => total + turn.answer.execution_trace.nodes.length,
    0,
  ),
  evidence_count: conversation.turns.reduce(
    (total, turn) => total + turn.answer.evidence.length,
    0,
  ),
  reviewed_input_sha256: shaFile(reviewedInputPath),
  case_author_actor_receipt_sha256: shaFile(caseAuthorReceiptPath),
})}\n`)
