import { createHash } from 'node:crypto'
import {
  lstatSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from 'node:fs'
import { isAbsolute, join, relative, resolve } from 'node:path'

import {
  P1ModelUserGoalPlanner,
  P1PiSemanticModel,
  P1RuntimeV2Grounder,
  type P1ConversationBinding,
  type P1PiSemanticModelAuditRecord,
  type P1UserGoal,
  type P1UserGoalPlan,
} from '../chat/index.js'
import {
  createCandidatePiModelBinding,
  loadPiModelCandidate,
} from '../pi/index.js'
import {
  FORMAL_P1_CERTIFIED_INPUT_SCOPE,
  FORMAL_P1_CERTIFIED_SCENARIO_SET_ID,
} from './formal-p1-sidecar.js'

interface ExplorerCase {
  case_id: string
  question: string
  raw_agent_receipt_ref: string
}

interface ExplorerCaseSet {
  cases: ExplorerCase[]
}

interface ExpectedReceipt {
  case_id: string
  original_question: string
  user_goal_plan: P1UserGoalPlan
  grounding_plan: {
    identity: Record<string, unknown>
  }
}

const SHA256 = /^[0-9a-f]{64}$/
const CERTIFIED_SOURCE_PATHS = [
  'agent-sidecar/package-lock.json',
  'agent-sidecar/src/chat/pi-semantic-model.ts',
  'agent-sidecar/src/chat/runtime-v2-semantic.ts',
  'agent-sidecar/src/pi/formal-run-audit.ts',
  'agent-sidecar/src/pi/formal-model-runtime.ts',
  'agent-sidecar/src/pi/pi-report-narrator.ts',
  'agent-sidecar/src/pi/static-resource-loader.ts',
  'agent-sidecar/resources/model-candidates/deepseek-v4-flash-pi-0.84.1-v1.json',
  'agent-sidecar/resources/vendor-patches/pi-ai-openai-completions-response-model-v1.json',
  'agent-sidecar/vendor-patches/pi-ai-0.84.1-openai-completions-response-model-v1.patch',
  'contracts/agent/country-outage-p1-page-coverage/s2/semantic-plan.schema.json',
  'contracts/agent/country-outage-p1-page-coverage/s2/capability-catalog.json',
  'contracts/agent/country-outage-p1-page-coverage/s2/tool-contracts.json',
  'contracts/agent/country-outage-p1-page-coverage/s2/oracle.json',
  'contracts/agent/country-outage-p1-page-coverage/s2/policy.json',
] as const

function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim() ?? ''
  if (!value) throw new Error(`缺少环境变量 ${name}`)
  return value
}

function regularFile(path: string): string {
  if (!isAbsolute(path)) throw new Error('认证输入必须是绝对路径')
  const normalized = resolve(path)
  const stats = lstatSync(normalized)
  if (
    !stats.isFile() ||
    stats.isSymbolicLink() ||
    realpathSync(normalized) !== normalized
  ) {
    throw new Error('认证输入必须是无符号链接普通文件')
  }
  return normalized
}

function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(regularFile(path), 'utf8')) as T
}

function sha256Bytes(value: Buffer | string): string {
  return createHash('sha256').update(value).digest('hex')
}

function canonical(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  const record = value as Record<string, unknown>
  return `{${Object.keys(record).sort().map((key) =>
    `${JSON.stringify(key)}:${canonical(record[key])}`,
  ).join(',')}}`
}

function goalSignature(goal: P1UserGoal): string {
  return canonical({
    normalized_kind: goal.normalized_kind,
    entities: goal.entities,
    references: goal.references,
    ambiguity: goal.ambiguity,
    context_dependencies: goal.context_dependencies,
  })
}

function bindingFromExpected(
  expected: ExpectedReceipt,
  reference: string,
): P1ConversationBinding {
  const identity = expected.grounding_plan.identity
  return {
    event_type: 'country_outage',
    incident_id: String(identity.incident_id),
    legacy_reference: reference,
    publication_id: String(identity.publication_id),
    revision: Number(identity.revision),
    collector_id: 'rrc25',
    cohort_id: String(identity.cohort_id),
    country_code: String(identity.country_code),
    detected_at_utc: null,
    window_start_utc: String(identity.window_start_utc),
    window_end_utc: String(identity.window_end_utc),
    data_through:
      identity.data_through === null ? null : String(identity.data_through),
    is_final_in_data_range: Boolean(identity.is_final_in_data_range),
    lifecycle_state: String(identity.lifecycle_state),
    observation_state: String(identity.observation_state),
    quality_state: 'verified',
    missing_slot_count: 0,
    capabilities: structuredClone(
      identity.capabilities,
    ) as P1ConversationBinding['capabilities'],
  }
}

async function main(): Promise<void> {
  const projectRoot = resolve(
    requiredEnvironment('COUNTRY_OUTAGE_P1_PROJECT_ROOT'),
  )
  if (!isAbsolute(projectRoot) || lstatSync(projectRoot).isSymbolicLink()) {
    throw new Error('P1 项目根目录无效')
  }
  const sourceIdentity = CERTIFIED_SOURCE_PATHS.map((relativePath) => {
    const path = regularFile(join(projectRoot, relativePath))
    return {
      path: relativePath,
      sha256: sha256Bytes(readFileSync(path)),
    }
  })
  const casesPath = regularFile(
    requiredEnvironment('COUNTRY_OUTAGE_P1_SEMANTIC_CASES_PATH'),
  )
  const expectedDirectory = resolve(
    requiredEnvironment('COUNTRY_OUTAGE_P1_EXPECTED_RECEIPT_DIRECTORY'),
  )
  if (!isAbsolute(expectedDirectory) || lstatSync(expectedDirectory).isSymbolicLink()) {
    throw new Error('期望回执目录无效')
  }
  const outputDirectory = resolve(
    requiredEnvironment('COUNTRY_OUTAGE_P1_SEMANTIC_CERTIFICATION_DIRECTORY'),
  )
  if (!isAbsolute(outputDirectory)) throw new Error('认证输出目录必须是绝对路径')
  mkdirSync(outputDirectory, { recursive: true, mode: 0o700 })
  const caseOutputDirectory = join(outputDirectory, 'cases')
  mkdirSync(caseOutputDirectory, { recursive: true, mode: 0o700 })

  const loadedCandidate = await loadPiModelCandidate(
    process.env.COUNTRY_OUTAGE_PI_CANDIDATE_PATH?.trim() || undefined,
  )
  const binding = await createCandidatePiModelBinding({
    loadedCandidate,
    authPath: requiredEnvironment('COUNTRY_OUTAGE_PI_CANDIDATE_AUTH_PATH'),
  })
  const modelAudits: P1PiSemanticModelAuditRecord[] = []
  const model = new P1PiSemanticModel({
    binding,
    allowCandidate: true,
    auditSink(record) {
      modelAudits.push(structuredClone(record))
    },
  })
  const planner = new P1ModelUserGoalPlanner(model)
  const grounder = new P1RuntimeV2Grounder()
  const caseSet = readJson<ExplorerCaseSet>(casesPath)
  if (!Array.isArray(caseSet.cases) || caseSet.cases.length < 20) {
    throw new Error('P1 语义认证案例不足')
  }
  const eventReference =
    'country_outage/2026-02-27 09:12:32/IR/1/r'
  let expectedGoalCount = 0
  let matchedGoalCount = 0
  let extraGoalCount = 0
  let groundingCasePassCount = 0
  const caseReceipts: Array<Record<string, unknown>> = []

  for (const item of caseSet.cases) {
    const expectedPath = regularFile(
      join(expectedDirectory, item.raw_agent_receipt_ref),
    )
    const expected = readJson<ExpectedReceipt>(expectedPath)
    if (
      expected.case_id !== item.case_id ||
      expected.original_question !== item.question
    ) {
      throw new Error(`案例身份漂移：${item.case_id}`)
    }
    const auditStart = modelAudits.length
    const actualPlan = await planner.plan(item.question, {
      event_type: 'country_outage',
      country_code: 'IR',
      event_reference: eventReference,
      has_dialog_state: false,
    })
    const eventBinding = bindingFromExpected(expected, eventReference)
    const semanticPlan = grounder.ground(
      actualPlan,
      eventBinding,
      eventReference,
    )
    const groundingErrors = grounder.validate(semanticPlan, eventBinding)
    const expectedGoals = expected.user_goal_plan.goals
    const actualGoals = actualPlan.goals
    expectedGoalCount += expectedGoals.length
    extraGoalCount += Math.max(0, actualGoals.length - expectedGoals.length)
    const perGoal = expectedGoals.map((goal, index) => {
      const actual = actualGoals[index]
      const matched = Boolean(actual) && goalSignature(actual!) === goalSignature(goal)
      if (matched) matchedGoalCount += 1
      return {
        goal_id: goal.goal_id,
        expected_signature: goalSignature(goal),
        actual_signature: actual ? goalSignature(actual) : null,
        matched,
      }
    })
    const groundingPassed =
      groundingErrors.length === 0 &&
      semanticPlan.grounding_plan.decisions.length === actualGoals.length
    if (groundingPassed) groundingCasePassCount += 1
    const audit = modelAudits[auditStart]
    if (
      modelAudits.length !== auditStart + 1 ||
      !audit ||
      audit.outcome !== 'completed' ||
      audit.usage === null ||
      audit.billing.estimatedCost === null
    ) {
      throw new Error(`模型费用审计未闭合：${item.case_id}`)
    }
    const receipt = {
      schema_version: 'country_outage_p1_semantic_certification_case_v1',
      case_id: item.case_id,
      question: item.question,
      candidate_id: binding.candidate.candidateId,
      candidate_resource_sha256: binding.candidateResourceSha256,
      expected_receipt_path: relative(projectRoot, expectedPath),
      expected_receipt_sha256: sha256Bytes(readFileSync(expectedPath)),
      expected_user_goal_plan: expected.user_goal_plan,
      actual_user_goal_plan: actualPlan,
      grounding_plan: semanticPlan.grounding_plan,
      grounding_errors: groundingErrors,
      goal_comparison: perGoal,
      grounding_passed: groundingPassed,
      model_audit: audit,
    }
    const receiptText = `${JSON.stringify(receipt, null, 2)}\n`
    const receiptPath = join(caseOutputDirectory, `${item.case_id}.json`)
    writeFileSync(receiptPath, receiptText, { mode: 0o600 })
    caseReceipts.push({
      case_id: item.case_id,
      path: `cases/${item.case_id}.json`,
      sha256: sha256Bytes(receiptText),
      goal_match_count: perGoal.filter((goal) => goal.matched).length,
      expected_goal_count: expectedGoals.length,
      actual_goal_count: actualGoals.length,
      grounding_passed: groundingPassed,
      estimated_cost_usd: audit.billing.estimatedCost,
    })
  }

  const denominator = expectedGoalCount + extraGoalCount
  const fidelity = denominator === 0 ? 0 : matchedGoalCount / denominator
  const groundingRate = groundingCasePassCount / caseSet.cases.length
  const usage = modelAudits.reduce(
    (total, audit) => ({
      input: total.input + (audit.usage?.tokens.input ?? 0),
      output: total.output + (audit.usage?.tokens.output ?? 0),
      cache_read: total.cache_read + (audit.usage?.tokens.cacheRead ?? 0),
      cache_write: total.cache_write + (audit.usage?.tokens.cacheWrite ?? 0),
      total: total.total + (audit.usage?.tokens.total ?? 0),
      estimated_cost_usd:
        total.estimated_cost_usd + (audit.billing.estimatedCost ?? 0),
    }),
    {
      input: 0,
      output: 0,
      cache_read: 0,
      cache_write: 0,
      total: 0,
      estimated_cost_usd: 0,
    },
  )
  const certified = fidelity >= 0.95 && groundingRate === 1
  const certifiedAt = new Date().toISOString()
  const validUntil = new Date(Date.parse(certifiedAt) + 7 * 24 * 60 * 60 * 1000)
    .toISOString()
  const evidencePayload = {
    schema_version: 'country_outage_p1_semantic_model_certification_v1',
    status: certified ? 'certified' : 'rejected',
    certified_at: certifiedAt,
    valid_until: validUntil,
    candidate_id: binding.candidate.candidateId,
    candidate_resource_sha256: binding.candidateResourceSha256,
    provider: binding.candidate.provider,
    model: binding.candidate.model,
    model_version: binding.candidate.modelVersion,
    expected_response_model: binding.candidate.expectedResponseModel,
    thinking_level: binding.candidate.thinkingLevel,
    pi_version: binding.candidate.piVersion,
    scenario_set_id: FORMAL_P1_CERTIFIED_SCENARIO_SET_ID,
    input_scope: FORMAL_P1_CERTIFIED_INPUT_SCOPE,
    source_identity: {
      algorithm: 'sha256-file-bytes-v1',
      files: sourceIdentity,
    },
    cases_source_path: relative(projectRoot, casesPath),
    cases_sha256: sha256Bytes(readFileSync(casesPath)),
    case_count: caseSet.cases.length,
    metrics: {
      expected_goal_count: expectedGoalCount,
      matched_goal_count: matchedGoalCount,
      extra_goal_count: extraGoalCount,
      user_goal_fidelity: fidelity,
      required_user_goal_fidelity: 0.95,
      grounding_case_pass_count: groundingCasePassCount,
      grounding_case_total: caseSet.cases.length,
      grounding_legality_rate: groundingRate,
      required_grounding_legality_rate: 1,
    },
    usage_and_cost: usage,
    model_call_count: modelAudits.length,
    per_call_cost_recorded: modelAudits.every(
      (audit) => audit.billing.estimatedCost !== null,
    ),
    case_receipts: caseReceipts,
  }
  const evidenceId =
    `evidence:p1-semantic-certification:${sha256Bytes(canonical(evidencePayload))}`
  const manifest = { ...evidencePayload, evidence_id: evidenceId }
  const manifestText = `${JSON.stringify(manifest, null, 2)}\n`
  const manifestPath = join(outputDirectory, 'manifest.json')
  writeFileSync(manifestPath, manifestText, { mode: 0o600 })
  process.stdout.write(`${JSON.stringify({
    event: 'country_outage_p1_semantic_model_certification_completed',
    certified,
    evidenceId,
    manifestPath,
    manifestSha256: sha256Bytes(manifestText),
    userGoalFidelity: fidelity,
    groundingLegalityRate: groundingRate,
    modelCallCount: modelAudits.length,
    usageAndCost: usage,
  })}\n`)
  if (!certified) process.exitCode = 1
}

void main().catch((error: unknown) => {
  process.stderr.write(`${JSON.stringify({
    event: 'country_outage_p1_semantic_model_certification_failed',
    code: 'p1_semantic_certification_failed',
    message: error instanceof Error ? error.message : 'P1 语义认证失败',
  })}\n`)
  process.exitCode = 1
})
