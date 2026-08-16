#!/usr/bin/env node

import { createHash, randomUUID } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  HttpP1GeneralReadModelProvider,
  P1CodexCliSemanticModel,
  P1ModelUserGoalPlanner,
  P1RuntimeV2SemanticTurnService,
} from '../../agent-sidecar/dist/src/chat/index.js'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const repositoryRoot = resolve(scriptDirectory, '../..')

function argument(name, fallback = null) {
  const index = process.argv.indexOf(name)
  return index >= 0 ? process.argv[index + 1] ?? fallback : fallback
}

function argumentsFor(name) {
  return process.argv.flatMap((value, index) =>
    value === name && process.argv[index + 1]
      ? [process.argv[index + 1]]
      : []
  )
}

function canonicalSha256(value) {
  return createHash('sha256')
    .update(JSON.stringify(value))
    .digest('hex')
}

function loadCases(path) {
  if (!path) {
    return [
      {
        case_id: 'S2-IP-001',
        page_outcome_ids: ['PCO-03', 'PCO-04'],
        expression_type: 'generic',
        persona: '普通分析用户',
        conversation_seed: [],
        question: 'IP地址变化情况',
        review_status: 'candidate',
      },
      {
        case_id: 'S2-IP-002',
        page_outcome_ids: ['PCO-03', 'PCO-04'],
        expression_type: 'generic_event_window_trend',
        persona: '普通分析用户',
        conversation_seed: [],
        question: 'ip地址变化趋势',
        review_status: 'candidate',
      },
    ]
  }
  const parsed = JSON.parse(readFileSync(resolve(path), 'utf8'))
  if (!Array.isArray(parsed) || parsed.length === 0) {
    throw new Error('case_file_must_be_non_empty_array')
  }
  return parsed
}

const outputDirectory = resolve(
  argument(
    '--output-dir',
    resolve(
      repositoryRoot,
      'evaluation/country-outage/p1-page-coverage/s2/raw/agent-receipts',
    ),
  ),
)
const candidateId = argument('--candidate-id', 's2-development-candidate')
const runId = argument('--run-id', `s2-explorer-${randomUUID()}`)
const actorId = argument('--actor-id', 'question-explorer-agent')
const modelName = argument('--model', 'gpt-5.6-sol')
const codexExecutable = resolve(
  argument(
    '--codex',
    '/Applications/ChatGPT.app/Contents/Resources/codex',
  ),
)
const baseUrl = argument(
  '--base-url',
  'http://10.99.8.16:28471/api/v2/',
)
const eventReference = argument(
  '--event-reference',
  'country_outage/2026-02-27 09:12:32/IR/1/r',
)
const publicationId = argument(
  '--publication-id',
  'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f',
)
const revision = Number(argument('--revision', '1'))
const caseFileArgument = argument('--case-file')
const requestedCaseIds = new Set(argumentsFor('--case-id'))
const loadedCases = loadCases(caseFileArgument)
const cases = requestedCaseIds.size === 0
  ? loadedCases
  : loadedCases.filter((item) => requestedCaseIds.has(item.case_id))
if (cases.length === 0) {
  throw new Error('requested_case_ids_not_found')
}
const caseSourcePath = caseFileArgument === null
  ? null
  : resolve(caseFileArgument)
const caseSourceText = caseSourcePath === null
  ? null
  : readFileSync(caseSourcePath, 'utf8')

mkdirSync(outputDirectory, { recursive: true })

const provider = new HttpP1GeneralReadModelProvider(baseUrl)
const model = new P1CodexCliSemanticModel({
  executable: codexExecutable,
  model: modelName,
  timeoutMs: 180_000,
})
const planner = new P1ModelUserGoalPlanner(model)
const service = new P1RuntimeV2SemanticTurnService(provider, planner)
const principal = {
  userId: actorId,
  authorizationScope: 'country_outage:read',
}

const resultCases = []
for (const item of cases) {
  process.stderr.write(
    `[S2 Explorer] 开始 ${item.case_id}: ${item.question}\n`,
  )
  const startedAt = new Date().toISOString()
  const receipt = {
    schema_version: 'country_outage_p1_page_coverage_agent_receipt_v1',
    evidence_kind: 'raw_agent_receipt',
    stage: 'S2',
    candidate_id: candidateId,
    run_id: runId,
    actor_id: actorId,
    case_id: item.case_id,
    captured_at: startedAt,
    event_identity: {
      event_reference: eventReference,
      publication_id: publicationId,
      revision,
      collector_id: 'rrc25',
    },
    original_question: item.question,
    conversation_seed: item.conversation_seed ?? [],
    initial_state: {
      evidence_state: {},
      dialog_state: {},
      candidate_state: 'frozen_for_case',
    },
    user_goal_plan: null,
    grounding_plan: null,
    tool_and_operator_receipts: [],
    evidence: [],
    answer: null,
    state_receipt: {
      proposal: null,
      commit: 'none',
      before: {},
      after: {},
    },
    error: null,
  }
  try {
    const answer = await service.answer(
      principal,
      {
        event_reference: eventReference,
        publication_id: publicationId,
        revision,
        question: item.question,
      },
    )
    receipt.captured_at = answer.completed_at
    receipt.user_goal_plan = answer.semantic_plan.user_goal_plan
    receipt.grounding_plan = answer.semantic_plan.grounding_plan
    receipt.tool_and_operator_receipts = answer.execution_trace.nodes
    receipt.evidence = answer.evidence
    receipt.answer = {
      answerability: answer.answerability,
      results: answer.results,
      answer_text: answer.answer_text,
      limitations: answer.limitations,
      unknowns: answer.unknowns,
      runtime_identity: answer.runtime_identity,
      validation: answer.validation,
    }
  } catch (error) {
    receipt.captured_at = new Date().toISOString()
    receipt.error = {
      code: error && typeof error === 'object' && 'code' in error
        ? String(error.code)
        : error instanceof Error ? error.name : 'unknown_error',
      message: error instanceof Error ? error.message : String(error),
    }
  }
  const filename = `${item.case_id}.json`
  const path = resolve(outputDirectory, filename)
  const receiptText = `${JSON.stringify(receipt, null, 2)}\n`
  writeFileSync(path, receiptText, 'utf8')
  resultCases.push({
    ...item,
    candidate_id: candidateId,
    event_identity: receipt.event_identity,
    raw_agent_receipt_ref: filename,
    raw_agent_receipt_sha256: createHash('sha256')
      .update(receiptText)
      .digest('hex'),
    execution_status: receipt.error === null ? 'completed' : 'failed',
  })
  process.stderr.write(
    `[S2 Explorer] 完成 ${item.case_id}: ${receipt.error === null ? 'completed' : receipt.error.code}\n`,
  )
}

const wrapper = {
  schema_version: 'country_outage_p1_question_explorer_raw_v1',
  evidence_kind: 'question_explorer_results',
  stage: 'S2',
  candidate_id: candidateId,
  run_id: runId,
  actor_id: actorId,
  question_explorer_actor_id: actorId,
  question_explorer_run_id: runId,
  model_identity: model.identity,
  captured_at: new Date().toISOString(),
  case_source_ref: caseSourcePath,
  case_source_sha256: caseSourceText === null
    ? null
    : createHash('sha256').update(caseSourceText).digest('hex'),
  case_set_hash_algorithm:
    'sha256-json-stringify-parsed-input-preserving-key-order-v1',
  page_outcome_ids: [
    ...new Set(resultCases.flatMap((item) => item.page_outcome_ids)),
  ].sort(),
  case_set_sha256: canonicalSha256(cases),
  cases: resultCases,
}
writeFileSync(
  resolve(outputDirectory, 'question-explorer-results.raw.json'),
  `${JSON.stringify(wrapper, null, 2)}\n`,
  'utf8',
)
process.stdout.write(`${JSON.stringify(wrapper, null, 2)}\n`)
