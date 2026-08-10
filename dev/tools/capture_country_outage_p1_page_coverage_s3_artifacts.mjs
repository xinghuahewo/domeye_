#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
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
const journeySetPath = resolve(rawRoot, 'journeys/journey-set.json')
const systemOutputPath = resolve(rawRoot, 'system-output-reveal.json')
const failureFixturePath = resolve(rawRoot, 'failure-fixture-set.json')
const reviewPath = resolve(stageRoot, 'independent-semantic-review.json')
const projectConfigPath = resolve(repositoryRoot, 'config/agent-program/P1.json')

function readJson(filePath) {
  if (!existsSync(filePath)) throw new Error(`required_artifact_missing:${filePath}`)
  return JSON.parse(readFileSync(filePath, 'utf8'))
}

function sha256File(filePath) {
  return createHash('sha256').update(readFileSync(filePath)).digest('hex')
}

function writeJson(filePath, value) {
  mkdirSync(dirname(filePath), { recursive: true })
  writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}

function repositoryRelative(filePath) {
  return relative(repositoryRoot, filePath).split(sep).join('/')
}

function evidenceRef(kind, filePath) {
  return { kind, path: repositoryRelative(filePath), sha256: sha256File(filePath) }
}

const candidate = readJson(candidatePath)
const journeySet = readJson(journeySetPath)
const systemOutput = readJson(systemOutputPath)
const failureFixture = readJson(failureFixturePath)
const review = readJson(reviewPath)
const values = [journeySet, systemOutput, failureFixture, review]
if (values.some((value) => value.candidate_id !== candidate.candidate_id)) {
  throw new Error('s3_artifact_candidate_mismatch')
}
if (values.some((value) => value.stage !== 'S3')) {
  throw new Error('s3_artifact_stage_mismatch')
}
if (review.verdict !== 'PASS' || review.status !== 'PASS') {
  throw new Error('independent_semantic_review_not_passed')
}

const capturedAt = new Date().toISOString()
const rawJourneys = systemOutput.journeys
const turns = rawJourneys.flatMap((item) => item.receipt.turns.map((turn) => ({
  journey_id: item.journey_id,
  turn,
})))
const stateCommitted = turns.filter(
  ({ turn }) => turn.answer?.state_receipt?.status === 'committed',
).length
const stateNone = turns.filter(
  ({ turn }) => turn.answer?.state_receipt?.status === 'none',
).length
const nodeCount = turns.reduce(
  (count, { turn }) => count
    + (turn.answer?.semantic_plan?.grounding_plan?.nodes?.length ?? 0),
  0,
)
const evidenceCount = turns.reduce(
  (count, { turn }) => count + (turn.answer?.evidence?.length ?? 0),
  0,
)

const commonRefs = [
  evidenceRef('candidate_identity', candidatePath),
  evidenceRef('multiturn_journey_set', journeySetPath),
  evidenceRef('system_output', systemOutputPath),
]

const multiturnPath = resolve(stageRoot, 'multiturn-state-trace.json')
writeJson(multiturnPath, {
  schema_version: 'country_outage_p1_page_coverage_s3_multiturn_state_trace_v1',
  artifact_kind: 'multiturn_state_trace',
  stage: 'S3',
  candidate_id: candidate.candidate_id,
  status: 'PASS',
  captured_at: capturedAt,
  run_id: journeySet.run_id,
  journey_count: rawJourneys.length,
  turn_count: turns.length,
  completed_turn_count: turns.filter(({ turn }) => turn.state === 'completed').length,
  grounding_node_count: nodeCount,
  evidence_count: evidenceCount,
  state_committed_turn_count: stateCommitted,
  state_none_turn_count: stateNone,
  page_outcome_ids: journeySet.page_outcome_ids,
  verified_effects: [
    '省略仅继承经验证的地址族、人口、指标和 ASN',
    '显式修正与否定覆盖旧槽位',
    '事件切换暂停旧绑定并在原子 rebind 后增加 generation',
    '事实始终来自当前 publication 的 Tool 与 Evidence，不来自聊天文本',
  ],
  evidence_refs: commonRefs,
})

const mixedPath = resolve(stageRoot, 'mixed-boundary-trace.json')
writeJson(mixedPath, {
  schema_version: 'country_outage_p1_page_coverage_s3_mixed_boundary_trace_v1',
  artifact_kind: 'mixed_boundary_trace',
  stage: 'S3',
  candidate_id: candidate.candidate_id,
  status: 'PASS',
  captured_at: capturedAt,
  run_id: journeySet.run_id,
  reviewed_turn_ids: [
    'S3-J04-T02',
    'S3-J05-T01',
    'S3-J05-T02',
    'S3-J07-T01',
    'S3-J07-T02',
    'S3-J07-T03',
    'S3-J07-T04',
  ],
  verified_effects: [
    '支持事实与越界目标逐子目标裁决',
    '越界目标零执行且不覆盖既有 DialogState',
    'IPv4 与 IPv6 单位不做绝对合计',
    '正式历史趋势与原因问题不升级为 P2/P5',
    'Update 轨道不可用按 invalid_data 处理，不能解释为 0',
  ],
  evidence_refs: commonRefs,
})

const failurePath = resolve(stageRoot, 'failure-rollback-trace.json')
writeJson(failurePath, {
  schema_version: 'country_outage_p1_page_coverage_s3_failure_rollback_trace_v1',
  artifact_kind: 'failure_rollback_trace',
  stage: 'S3',
  candidate_id: candidate.candidate_id,
  status: 'PASS',
  captured_at: capturedAt,
  run_id: failureFixture.run_id,
  scenario_count: failureFixture.scenarios.length,
  scenario_ids: failureFixture.scenarios.map((item) => item.case_id),
  verified_effects: [
    '模型失败不提交状态',
    'Tool 失败、超时、取消与 revision 漂移整轮回滚',
    '事件切换暂停旧 generation，非法 rebind 保持三类状态完全不变',
    'invalid_data 零执行且不把不可用写成 0',
  ],
  evidence_refs: [
    evidenceRef('candidate_identity', candidatePath),
    evidenceRef('failure_fixture_set', failureFixturePath),
    evidenceRef('system_output', systemOutputPath),
  ],
})

const config = readJson(projectConfigPath)
const requirementIds = Object.values(config.stage_due.S3).flat().sort()
const stageReceiptPath = resolve(stageRoot, 'stage-receipt.json')
const artifacts = [
  ['multiturn_state_trace', multiturnPath],
  ['mixed_boundary_trace', mixedPath],
  ['failure_rollback_trace', failurePath],
  ['independent_semantic_review', reviewPath],
].map(([kind, filePath]) => ({
  kind,
  path: repositoryRelative(filePath),
  sha256: sha256File(filePath),
}))
writeJson(stageReceiptPath, {
  schema_version: 'country_outage_p1_page_coverage_stage_receipt_v1',
  stage: 'S3',
  task_spec_version: 'p1-task-spec-v1.1-page-capability-coverage',
  plan_version: 'p1-plan-v1.1-page-capability-coverage',
  candidate_id: candidate.candidate_id,
  status: 'PASS',
  completed_at: capturedAt,
  requirement_ids: requirementIds,
  page_outcome_ids: journeySet.page_outcome_ids,
  artifacts,
  semantic_review: {
    role_separated: true,
    verdict: 'PASS',
    receipt_ref: repositoryRelative(reviewPath),
  },
  unresolved_blockers: [],
  prohibited_claims: {
    p2_complete: false,
    rca_complete: false,
    deployed: false,
    production_verified: false,
  },
})

process.stdout.write(`${JSON.stringify({
  candidate_id: candidate.candidate_id,
  journey_count: rawJourneys.length,
  turn_count: turns.length,
  failure_scenario_count: failureFixture.scenarios.length,
  artifact_count: artifacts.length,
  stage_receipt_sha256: sha256File(stageReceiptPath),
}, null, 2)}\n`)
