#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from 'node:fs'
import { dirname, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const repositoryRoot = resolve(scriptDirectory, '../..')
const stageRoot = resolve(
  repositoryRoot,
  'evaluation/country-outage/p1-page-coverage/s3',
)
const rawRoot = resolve(stageRoot, 'raw')
const candidatePath = resolve(rawRoot, 'candidate-identity.json')
const reviewedInputPath = resolve(rawRoot, 'reviewed-input.json')
const caseAuthorReceiptPath = resolve(rawRoot, 'case-author-actor-receipt.json')
const systemOutputPath = resolve(rawRoot, 'system-output-reveal.json')
const journeySetPath = resolve(rawRoot, 'journeys/journey-set.json')
const failureFixturePath = resolve(rawRoot, 'failure-fixture-set.json')
const journeyContractPath = resolve(
  repositoryRoot,
  'contracts/agent/country-outage-p1-page-coverage/s3/journeys.json',
)
const priorCandidatePath = resolve(
  repositoryRoot,
  'evaluation/country-outage/p1-page-coverage/s2/raw/candidate-identity.json',
)

const sourceRoles = {
  'agent-sidecar/src/chat/index.ts': 'runtime_export',
  'agent-sidecar/src/chat/codex-cli-semantic-model.ts': 'semantic_model_adapter',
  'agent-sidecar/src/chat/contracts.ts': 'runtime_contract_types',
  'agent-sidecar/src/chat/general-read-model-provider.ts': 'read_model_provider',
  'agent-sidecar/src/chat/page-capability-executor.ts': 'deterministic_executor',
  'agent-sidecar/src/chat/runtime-v2-semantic.ts': 'semantic_planner_grounder',
  'agent-sidecar/src/chat/runtime-v2-conversation.ts': 'conversation_runtime',
  'agent-sidecar/src/chat/runtime-v2-single-turn.ts': 'single_turn_runtime',
  'agent-sidecar/tests/p1-page-capability-conversation.test.ts': 'state_transaction_tests',
  'dev/tools/capture_country_outage_p1_page_coverage_s3.mjs': 'live_capture_harness',
  'contracts/agent/country-outage-p1-page-coverage/s3/journeys.json': 'frozen_multiturn_journeys',
  'contracts/agent/country-outage-p1-page-coverage/s2/semantic-plan.schema.json': 'semantic_schema',
  'contracts/agent/country-outage-p1-page-coverage/s2/capability-catalog.json': 'capability_catalog',
  'contracts/agent/country-outage-p1-page-coverage/s2/tool-contracts.json': 'tool_operator_contracts',
  'contracts/agent/country-outage-p1-page-coverage/s2/oracle.json': 'oracle',
  'contracts/agent/country-outage-p1-page-coverage/s2/policy.json': 'policy',
}

function argument(name, fallback = null) {
  const index = process.argv.indexOf(name)
  return index >= 0 ? process.argv[index + 1] ?? fallback : fallback
}

function readJson(filePath) {
  return JSON.parse(readFileSync(filePath, 'utf8'))
}

function sha256Bytes(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

function sha256File(filePath) {
  return sha256Bytes(readFileSync(filePath))
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalize(value[key])]),
    )
  }
  return value
}

function canonicalSha256(value) {
  const canonicalJson = JSON.stringify(canonicalize(value)).replace(
    /[^\x00-\x7f]/g,
    (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`,
  )
  return sha256Bytes(Buffer.from(canonicalJson))
}

function writeJson(filePath, value) {
  mkdirSync(dirname(filePath), { recursive: true })
  writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}

function repositoryRelative(filePath) {
  return relative(repositoryRoot, filePath).split(sep).join('/')
}

function requireFile(filePath, label) {
  if (!existsSync(filePath)) throw new Error(`${label}_missing:${repositoryRelative(filePath)}`)
  return filePath
}

function verifyCandidate() {
  const candidate = readJson(requireFile(candidatePath, 'candidate_identity'))
  const errors = []
  for (const [sourcePath, source] of Object.entries(candidate.components.sources)) {
    const absolutePath = resolve(repositoryRoot, sourcePath)
    if (!existsSync(absolutePath) || sha256File(absolutePath) !== source.sha256) {
      errors.push(`candidate_source_drift:${sourcePath}`)
    }
  }
  if (canonicalSha256(candidate.components) !== candidate.candidate_identity_sha256) {
    errors.push('candidate_component_digest_mismatch')
  }
  if (errors.length > 0) throw new Error(errors.join('\n'))
  return candidate
}

function prepare() {
  const journeys = readJson(journeyContractPath)
  const priorCandidate = readJson(priorCandidatePath)
  const sources = Object.fromEntries(
    Object.entries(sourceRoles).map(([sourcePath, role]) => {
      const absolutePath = requireFile(resolve(repositoryRoot, sourcePath), 'candidate_source')
      return [sourcePath, { role, sha256: sha256File(absolutePath) }]
    }),
  )
  const codexExecutable = resolve(argument(
    '--codex',
    '/Applications/ChatGPT.app/Contents/Resources/codex',
  ))
  const modelName = argument('--model', 'gpt-5.6-sol')
  const codexVersion = execFileSync(codexExecutable, ['--version'], {
    encoding: 'utf8',
  }).trim().replace(/^codex-cli\s+/, '')
  const components = {
    sources,
    model: {
      identity: `codex-cli:${codexVersion}:${modelName}:blind-v2`,
      executable_sha256: sha256File(codexExecutable),
    },
    prompt: {
      identity: 'runtime-v2-semantic.ts#semanticPlannerPrompt',
      source_sha256: sources['agent-sidecar/src/chat/runtime-v2-semantic.ts'].sha256,
    },
    data_publication: priorCandidate.components.data_publication,
  }
  const candidateIdentitySha256 = canonicalSha256(components)
  const candidateId = `p1-page-coverage-s3-${candidateIdentitySha256.slice(0, 12)}`
  const capturedAt = new Date().toISOString()
  const runId = argument('--run-id', 's3-question-explorer-final-001')
  const actorId = argument('--actor-id', 's3-question-explorer-agent')
  const candidate = {
    schema_version: 'country_outage_p1_page_coverage_candidate_identity_v1',
    evidence_kind: 'candidate_identity',
    stage: 'S3',
    candidate_id: candidateId,
    run_id: 's3-candidate-freeze-001',
    captured_at: capturedAt,
    candidate_identity_sha256: candidateIdentitySha256,
    hash_algorithm: 'sha256-recursive-sorted-key-canonical-json-v1',
    components,
  }
  writeJson(candidatePath, candidate)

  const questions = journeys.flatMap((journey) => {
    const priorQuestions = []
    return journey.turns.map((turn) => {
      const item = {
        case_id: turn.turn_id,
        journey_id: journey.journey_id,
        page_outcome_ids: journey.page_outcome_ids,
        persona: journey.persona,
        purpose: journey.purpose,
        conversation_seed: [...priorQuestions],
        question: turn.question,
        after_action: turn.after_action ?? null,
        review_status: 'frozen',
        event_identity: {
          event_reference: components.data_publication.event_reference,
          publication_id: components.data_publication.publication_id,
          revision: components.data_publication.revision,
          collector_id: components.data_publication.collector_id,
        },
      }
      priorQuestions.push(turn.question)
      return item
    })
  })
  const reviewedInput = {
    schema_version: 'country_outage_p1_page_coverage_s3_reviewed_input_v1',
    evidence_kind: 'reviewed_input',
    stage: 'S3',
    candidate_id: candidateId,
    candidate_identity_sha256: candidateIdentitySha256,
    run_id: runId,
    actor_id: actorId,
    captured_at: capturedAt,
    journey_contract: {
      path: repositoryRelative(journeyContractPath),
      sha256: sha256File(journeyContractPath),
      parsed_sha256: canonicalSha256(journeys),
    },
    page_outcome_ids: [...new Set(journeys.flatMap((item) => item.page_outcome_ids))].sort(),
    questions,
    truth_sources: [
      'docs/agent/P1-聊天问答/Task-Spec-最终验收文档.md',
      'docs/agent/P1-聊天问答/Plan-分阶段计划.md',
      'evaluation/country-outage/p1-page-coverage/s0/page-capability-outcome-map.json',
      'contracts/agent/country-outage-p1-page-coverage/s2/capability-catalog.json',
      'contracts/agent/country-outage-p1-page-coverage/s2/tool-contracts.json',
      'contracts/agent/country-outage-p1-page-coverage/s2/oracle.json',
      'contracts/agent/country-outage-p1-page-coverage/s2/policy.json',
      'contracts/agent/country-outage-p1-page-coverage/s3/journeys.json',
    ],
    unrevealed_system_outputs: [
      'evaluation/country-outage/p1-page-coverage/s3/raw/journeys/**',
      'evaluation/country-outage/p1-page-coverage/s3/raw/failure-fixture-set.json',
      'evaluation/country-outage/p1-page-coverage/s3/raw/system-output-reveal.json',
    ],
    denied_actions: [
      'write_system_output',
      'mark_pass',
      'modify_implementation',
      'modify_test',
      'modify_contract',
      'modify_oracle',
    ],
  }
  writeJson(reviewedInputPath, reviewedInput)
  const caseAuthorReceipt = {
    schema_version: 'country_outage_p1_page_coverage_s3_actor_receipt_v1',
    evidence_kind: 'case_author_actor_receipt',
    stage: 'S3',
    candidate_id: candidateId,
    run_id: runId,
    actor_id: actorId,
    captured_at: capturedAt,
    orchestrator_receipt_id: 's3-question-explorer-orchestrator-receipt-001',
    allowed_actions: [
      'freeze_multiturn_journeys',
      'execute_frozen_journeys',
      'capture_raw_receipts',
    ],
    denied_actions: [
      'write_truth',
      'mark_pass',
      'modify_implementation',
      'modify_contract',
      'modify_oracle',
    ],
    reviewed_input_sha256: sha256File(reviewedInputPath),
  }
  writeJson(caseAuthorReceiptPath, caseAuthorReceipt)
  process.stdout.write(`${JSON.stringify({
    candidate_id: candidateId,
    candidate_identity_sha256: candidateIdentitySha256,
    reviewed_input_sha256: sha256File(reviewedInputPath),
    case_author_actor_receipt_sha256: sha256File(caseAuthorReceiptPath),
    question_count: questions.length,
  }, null, 2)}\n`)
}

function reveal() {
  const candidate = verifyCandidate()
  const reviewedInput = readJson(requireFile(reviewedInputPath, 'reviewed_input'))
  const journeySet = readJson(requireFile(journeySetPath, 'journey_set'))
  const failureFixture = readJson(requireFile(failureFixturePath, 'failure_fixture_set'))
  const blindTruthPath = requireFile(resolve(rawRoot, 'blind-truth.json'), 'blind_truth')
  const reviewerActorPath = requireFile(
    resolve(rawRoot, 'reviewer-actor-receipt.json'),
    'reviewer_actor_receipt',
  )
  if (journeySet.candidate_id !== candidate.candidate_id) {
    throw new Error('journey_set_candidate_mismatch')
  }
  if (failureFixture.candidate_id !== candidate.candidate_id) {
    throw new Error('failure_fixture_candidate_mismatch')
  }
  const journeys = journeySet.journeys.map((journey) => {
    const receiptPath = resolve(repositoryRoot, journey.raw_receipt_ref)
    return {
      journey_id: journey.journey_id,
      raw_receipt_ref: journey.raw_receipt_ref,
      raw_receipt_sha256: sha256File(receiptPath),
      receipt: readJson(receiptPath),
    }
  })
  const capturedAt = new Date().toISOString()
  const systemOutput = {
    schema_version: 'country_outage_p1_page_coverage_s3_system_output_v1',
    evidence_kind: 'system_output',
    stage: 'S3',
    candidate_id: candidate.candidate_id,
    candidate_identity_sha256: candidate.candidate_identity_sha256,
    run_id: journeySet.run_id,
    actor_id: 'p1-page-capability-conversation-runtime',
    captured_at: capturedAt,
    reviewed_input_sha256: sha256File(reviewedInputPath),
    blind_truth_sha256: sha256File(blindTruthPath),
    reviewer_actor_receipt_sha256: sha256File(reviewerActorPath),
    journey_contract_sha256: reviewedInput.journey_contract.sha256,
    journey_set: {
      path: repositoryRelative(journeySetPath),
      sha256: sha256File(journeySetPath),
    },
    failure_fixture_set: {
      path: repositoryRelative(failureFixturePath),
      sha256: sha256File(failureFixturePath),
      scenario_count: failureFixture.scenarios.length,
    },
    journeys,
  }
  writeJson(systemOutputPath, systemOutput)
  process.stdout.write(`${JSON.stringify({
    candidate_id: candidate.candidate_id,
    captured_at: capturedAt,
    journey_count: journeys.length,
    turn_count: journeys.reduce((count, item) => count + item.receipt.turns.length, 0),
    failure_scenario_count: failureFixture.scenarios.length,
    system_output_sha256: sha256File(systemOutputPath),
  }, null, 2)}\n`)
}

const command = process.argv[2]
if (command === 'prepare') prepare()
else if (command === 'reveal') reveal()
else throw new Error(
  '用法：capture_country_outage_p1_page_coverage_s3_review.mjs prepare|reveal',
)
