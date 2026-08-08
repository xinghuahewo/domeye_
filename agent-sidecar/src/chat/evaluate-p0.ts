import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

import {
  P1_CASE_SET_REVISION,
  P1_CONTRACT_REVISION,
  type P1AnswerEnvelope,
  type P1Answerability,
} from './contracts.js'
import { P1ConversationManager } from './conversation-manager.js'
import { HttpP1GeneralReadModelProvider } from './general-read-model-provider.js'

interface P0Fact {
  evidence_ref: string
  value: unknown
  unit: string | null
}

interface P0Case {
  case_id: string
  category: 'direct' | 'multi_turn' | 'boundary' | 'exception'
  question: string
  answerability: P1Answerability
  turns?: Array<{ user: string }>
  expected: {
    facts: P0Fact[]
    forbidden_assertions: string[]
  }
}

interface P0CaseSet {
  revision: string
  event_binding: {
    legacy_reference: string
    publication_id: string
    revision: number
  }
  cases: P0Case[]
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(',')}}`
  }
  return JSON.stringify(value)
}

function sha256(value: unknown): string {
  return createHash('sha256').update(canonical(value), 'utf8').digest('hex')
}

function actualFacts(answers: P1AnswerEnvelope[]): Map<string, { value: unknown, unit: string | null }> {
  return new Map(answers.flatMap((answer) => answer.evidence.map((item) => [
    item.evidence_ref,
    { value: item.value, unit: item.unit },
  ])))
}

function factMatches(expected: P0Fact, actual: { value: unknown, unit: string | null } | undefined): boolean {
  return Boolean(actual) && canonical(actual!.value) === canonical(expected.value)
    && (expected.unit === null || actual!.unit === expected.unit)
}

function statusMatches(expected: P1Answerability, actual: P1Answerability): boolean {
  return expected === actual
}

function contextChecks(caseId: string, answers: P1AnswerEnvelope[], state: Record<string, unknown>): string[] {
  const failures: string[] = []
  const publications = new Set(answers.map((answer) => answer.binding.publication_id))
  if (publications.size !== 1) failures.push('publication_changed_within_case')
  if (caseId === 'P0-M-01') {
    if (state.metric !== 'interrupted_prefix_count') failures.push('metric_not_inherited')
  }
  if (caseId === 'P0-M-02') {
    if (state.asn !== 49556) failures.push('asn_correction_not_committed')
    if (answers.at(-1)?.answer_text.includes('AS48715')) failures.push('old_asn_leaked')
  }
  if (caseId === 'P0-M-03') {
    if (state.topic !== 'path' || state.address_family !== null) failures.push('topic_switch_not_isolated')
  }
  if (caseId === 'P0-M-04') {
    if (state.pending_clarification !== 'event_reference') failures.push('event_clarification_not_pending')
    if (answers.slice(1).some((answer) => answer.answer_text.includes('3,855'))) failures.push('old_event_fact_leaked')
  }
  if (caseId === 'P0-M-05') {
    if (answers.at(-1)?.answerability !== 'partial') failures.push('cause_not_degraded')
    if (state.topic !== 'boundary' || state.metric !== null || state.asn !== null) {
      failures.push('cause_boundary_state_not_isolated')
    }
  }
  return failures
}

async function main(): Promise<void> {
  const repositoryRoot = resolve(process.argv[2] ?? process.cwd())
  const outputPath = resolve(
    process.argv[3] ?? resolve(repositoryRoot, 'evaluation/country-outage/p1-v1/candidate-result.json'),
  )
  const apiBaseUrl = process.argv[4] ?? 'http://10.99.8.16:28471/api/v2/'
  const caseSet = JSON.parse(await readFile(
    resolve(repositoryRoot, 'evaluation/country-outage/p0-v1/cases.json'),
    'utf8',
  )) as P0CaseSet
  if (caseSet.revision !== P1_CASE_SET_REVISION) {
    throw new Error(`P0 revision 不一致：${caseSet.revision}`)
  }
  const provider = new HttpP1GeneralReadModelProvider(apiBaseUrl, 20_000)
  const principal = { userId: 'p1-evaluator', authorizationScope: 'country_outage_event_read' }
  const results: Array<Record<string, unknown>> = []
  for (const item of caseSet.cases) {
    const manager = new P1ConversationManager({ provider })
    const created = await manager.createConversation(principal, {
      event_reference: caseSet.event_binding.legacy_reference,
      publication_id: caseSet.event_binding.publication_id,
      revision: caseSet.event_binding.revision,
      idempotency_key: `conversation-${item.case_id.toLowerCase()}`,
    })
    const answers: P1AnswerEnvelope[] = []
    const questions = item.turns?.map((turn) => turn.user) ?? [item.question]
    for (const [index, question] of questions.entries()) {
      const response = await manager.createTurn(
        principal,
        created.conversation.conversation_id,
        {
          question,
          idempotency_key: `turn-${item.case_id.toLowerCase()}-${index + 1}`,
        },
      )
      if (response.turn.answer) answers.push(response.turn.answer)
    }
    const snapshot = await manager.getConversation(principal, created.conversation.conversation_id)
    const facts = actualFacts(answers)
    const missingOrMismatched = item.expected.facts
      .filter((expected) => !factMatches(expected, facts.get(expected.evidence_ref)))
      .map((expected) => expected.evidence_ref)
    const finalAnswer = answers.at(-1)
    const forbiddenHits = item.expected.forbidden_assertions.filter((forbidden) =>
      answers.some((answer) => answer.answer_text.includes(forbidden)),
    )
    const identityFailures = answers.filter((answer) =>
      answer.binding.publication_id !== caseSet.event_binding.publication_id ||
      answer.binding.revision !== caseSet.event_binding.revision ||
      answer.binding.collector_id !== 'rrc25',
    ).length
    const contextFailures = contextChecks(
      item.case_id,
      answers,
      snapshot.state as unknown as Record<string, unknown>,
    )
    const failures = [
      ...(finalAnswer && statusMatches(item.answerability, finalAnswer.answerability) ? [] : ['answerability_mismatch']),
      ...missingOrMismatched.map((ref) => `fact_mismatch:${ref}`),
      ...forbiddenHits.map((text) => `forbidden_assertion:${text}`),
      ...(identityFailures ? ['identity_mismatch'] : []),
      ...contextFailures,
    ]
    results.push({
      case_id: item.case_id,
      category: item.category,
      passed: failures.length === 0,
      expected_answerability: item.answerability,
      actual_answerability: finalAnswer?.answerability ?? null,
      expected_fact_count: item.expected.facts.length,
      matched_fact_count: item.expected.facts.length - missingOrMismatched.length,
      forbidden_assertion_hits: forbiddenHits,
      identity_failure_count: identityFailures,
      context_failures: contextFailures,
      failures,
      turns: answers.map((answer) => ({
        turn_number: answer.turn_number,
        answerability: answer.answerability,
        evidence_refs: answer.evidence.map((evidence) => evidence.evidence_ref),
        publication_id: answer.binding.publication_id,
        revision: answer.binding.revision,
        validation_passed: answer.validation.passed,
      })),
    })
  }
  const counts = Object.fromEntries(['direct', 'multi_turn', 'boundary', 'exception'].map((category) => {
    const categoryResults = results.filter((result) => result.category === category)
    return [category, {
      total: categoryResults.length,
      passed: categoryResults.filter((result) => result.passed).length,
    }]
  }))
  const sourcePaths = [
    'agent-sidecar/src/chat/contracts.ts',
    'agent-sidecar/src/chat/conversation-manager.ts',
    'agent-sidecar/src/chat/deterministic-engine.ts',
    'agent-sidecar/src/chat/general-read-model-provider.ts',
  ]
  const sourceContents = await Promise.all(sourcePaths.map(async (path) => ({
    path,
    content: await readFile(resolve(repositoryRoot, path), 'utf8'),
  })))
  const result = {
    schema_version: 'country_outage_p1_candidate_result_v1',
    candidate_id: `p1-candidate-${sha256(sourceContents).slice(0, 16)}`,
    p0_case_set_revision: caseSet.revision,
    p1_contract_revision: P1_CONTRACT_REVISION,
    evaluated_at: new Date().toISOString(),
    api_base_url: apiBaseUrl,
    collector_id: 'rrc25',
    implementation_source_sha256: sha256(sourceContents),
    base_commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repositoryRoot, encoding: 'utf8' }).trim(),
    counts,
    hard_gates: {
      all_35_passed: results.every((item) => item.passed),
      event_binding_percent: results.every((item) => item.identity_failure_count === 0) ? 100 : 0,
      publication_binding_percent: results.every((item) => item.identity_failure_count === 0) ? 100 : 0,
      forbidden_assertion_hits: results.reduce((sum, item) => sum + (item.forbidden_assertion_hits as string[]).length, 0),
      invalid_answer_publications: results.filter((item) => item.category === 'exception' && !item.passed).length,
    },
    results,
  }
  await writeFile(outputPath, `${JSON.stringify(result, null, 2)}\n`, 'utf8')
  process.stdout.write(`${JSON.stringify({
    candidate_id: result.candidate_id,
    output_path: outputPath,
    counts,
    hard_gates: result.hard_gates,
  })}\n`)
  if (!result.hard_gates.all_35_passed) process.exitCode = 1
}

void main().catch((error: unknown) => {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`)
  process.exitCode = 1
})
