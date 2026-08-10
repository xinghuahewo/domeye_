#!/usr/bin/env node

import { createHash, randomUUID } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  HttpP1GeneralReadModelProvider,
  P1CodexCliSemanticModel,
  P1ModelUserGoalPlanner,
  P1RuntimeV2ConversationService,
} from '../../agent-sidecar/dist/src/chat/index.js'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const repositoryRoot = resolve(scriptDirectory, '../..')

function repositoryRelative(filePath) {
  const normalizedRoot = `${repositoryRoot}/`
  const normalizedPath = resolve(filePath)
  if (!normalizedPath.startsWith(normalizedRoot)) {
    throw new Error(`path_outside_repository:${normalizedPath}`)
  }
  return normalizedPath.slice(normalizedRoot.length)
}

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

function sha256Text(value) {
  return createHash('sha256').update(value).digest('hex')
}

function canonicalSha256(value) {
  return sha256Text(JSON.stringify(value))
}

const outputDirectory = resolve(argument(
  '--output-dir',
  resolve(repositoryRoot, 'evaluation/country-outage/p1-page-coverage/s3/raw/journeys'),
))
const caseFile = resolve(argument(
  '--case-file',
  resolve(repositoryRoot, 'contracts/agent/country-outage-p1-page-coverage/s3/journeys.json'),
))
const caseText = readFileSync(caseFile, 'utf8')
const loadedJourneys = JSON.parse(caseText)
if (!Array.isArray(loadedJourneys) || loadedJourneys.length === 0) {
  throw new Error('journey_file_must_be_non_empty_array')
}
const requestedJourneyIds = new Set(argumentsFor('--journey-id'))
const journeys = requestedJourneyIds.size === 0
  ? loadedJourneys
  : loadedJourneys.filter((item) => requestedJourneyIds.has(item.journey_id))
if (journeys.length === 0) throw new Error('requested_journey_ids_not_found')

const candidateId = argument('--candidate-id', 's3-development-candidate')
const runId = argument('--run-id', `s3-explorer-${randomUUID()}`)
const actorId = argument('--actor-id', 's3-question-explorer-agent')
const modelName = argument('--model', 'gpt-5.6-sol')
const codexExecutable = resolve(argument(
  '--codex',
  '/Applications/ChatGPT.app/Contents/Resources/codex',
))
const baseUrl = argument('--base-url', 'http://10.99.8.16:28471/api/v2/')
const eventReference = argument(
  '--event-reference',
  'country_outage/2026-02-27 09:12:32/IR/1/r',
)
const publicationId = argument(
  '--publication-id',
  'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f',
)
const revision = Number(argument('--revision', '1'))

mkdirSync(outputDirectory, { recursive: true })
const provider = new HttpP1GeneralReadModelProvider(baseUrl)
const model = new P1CodexCliSemanticModel({
  executable: codexExecutable,
  model: modelName,
  timeoutMs: 180_000,
})
const planner = new P1ModelUserGoalPlanner(model)
const service = new P1RuntimeV2ConversationService({
  provider,
  planner,
  ttlMs: 30 * 60 * 1000,
  turnTimeoutMs: 200_000,
})
const principal = {
  userId: actorId,
  authorizationScope: 'country_outage:read',
}

const resultJourneys = []
for (const journey of journeys) {
  process.stderr.write(`[S3 Explorer] 开始 ${journey.journey_id}\n`)
  const created = await service.createConversation(principal, {
    event_reference: eventReference,
    publication_id: publicationId,
    revision,
    idempotency_key: `${runId}:${journey.journey_id}:create`,
  })
  const conversationId = created.conversation.conversation_id
  const rawTurns = []
  const rebindReceipts = []
  for (const item of journey.turns) {
    process.stderr.write(`  [turn] ${item.turn_id}: ${item.question}\n`)
    const result = await service.createTurn(principal, conversationId, {
      question: item.question,
      idempotency_key: `${runId}:${item.turn_id}`,
    })
    rawTurns.push(result.turn)
    if (item.after_action === 'rebind_current') {
      const rebound = await service.rebind(principal, conversationId, {
        event_reference: eventReference,
        publication_id: publicationId,
        revision,
        idempotency_key: `${runId}:${item.turn_id}:rebind`,
      })
      rebindReceipts.push({
        after_turn_id: item.turn_id,
        previous_binding: rebound.previous_binding,
        conversation_after: rebound.conversation,
      })
    }
  }
  const finalConversation = await service.getConversation(principal, conversationId)
  const receipt = {
    schema_version: 'country_outage_p1_page_coverage_s3_journey_receipt_v1',
    evidence_kind: 'multiturn_journey_receipt',
    stage: 'S3',
    candidate_id: candidateId,
    run_id: runId,
    actor_id: actorId,
    journey_id: journey.journey_id,
    captured_at: new Date().toISOString(),
    purpose: journey.purpose,
    page_outcome_ids: journey.page_outcome_ids,
    questions: journey.turns,
    initial_conversation: created.conversation,
    turns: rawTurns,
    rebind_receipts: rebindReceipts,
    final_conversation: finalConversation,
    model_identity: model.identity,
    data_api_base_url: baseUrl,
  }
  const receiptText = `${JSON.stringify(receipt, null, 2)}\n`
  const receiptPath = resolve(outputDirectory, `${journey.journey_id}.json`)
  writeFileSync(receiptPath, receiptText, 'utf8')
  resultJourneys.push({
    ...journey,
    raw_receipt_ref: repositoryRelative(receiptPath),
    raw_receipt_sha256: sha256Text(receiptText),
    completed_turns: rawTurns.filter((turn) => turn.state === 'completed').length,
    failed_turns: rawTurns.filter((turn) => turn.state !== 'completed').length,
  })
  process.stderr.write(`[S3 Explorer] 完成 ${journey.journey_id}\n`)
}

const wrapper = {
  schema_version: 'country_outage_p1_page_coverage_s3_journey_set_v1',
  evidence_kind: 'multiturn_journey_set',
  stage: 'S3',
  candidate_id: candidateId,
  run_id: runId,
  actor_id: actorId,
  captured_at: new Date().toISOString(),
  model_identity: model.identity,
  case_source_ref: repositoryRelative(caseFile),
  case_source_sha256: sha256Text(caseText),
  case_set_sha256: canonicalSha256(journeys),
  page_outcome_ids: [...new Set(journeys.flatMap((item) => item.page_outcome_ids))].sort(),
  journeys: resultJourneys,
}
writeFileSync(
  resolve(outputDirectory, 'journey-set.json'),
  `${JSON.stringify(wrapper, null, 2)}\n`,
  'utf8',
)
process.stdout.write(`${JSON.stringify(wrapper, null, 2)}\n`)
