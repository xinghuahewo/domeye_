#!/usr/bin/env node

import { randomUUID } from 'node:crypto'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const repositoryRoot = resolve(fileURLToPath(new URL('../..', import.meta.url)))
const evaluationRoot = resolve(
  repositoryRoot,
  'evaluation/country-outage/p1-runtime-v2',
)
const apiBase = process.env.P1_S4_API_BASE
  ?? 'http://127.0.0.1:29473/api/v2/country-outage/runtime-v2'
const candidateId = JSON.parse(readFileSync(
  resolve(evaluationRoot, 'candidate-identity.json'),
  'utf8',
)).candidate_id
const p0v13 = JSON.parse(readFileSync(
  resolve(repositoryRoot, 'evaluation/country-outage/p0-v1-3/cases.json'),
  'utf8',
))
const p0base = JSON.parse(readFileSync(
  resolve(repositoryRoot, 'evaluation/country-outage/p0-v1/cases.json'),
  'utf8',
))
const semanticVariants = JSON.parse(readFileSync(
  resolve(evaluationRoot, 's2-semantic-variants.json'),
  'utf8',
))

const event = {
  event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
  publication_id: 'country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f',
  revision: 1,
}
const runId = `s4-${new Date().toISOString().replace(/[^0-9]/g, '').slice(0, 14)}-${randomUUID().slice(0, 8)}`
const maximumAttempts = 3
const sidecarRoot = resolve(repositoryRoot, 'agent-sidecar')
const captureScope = process.env.P1_S4_CAPTURE_SCOPE ?? 'all'

if (!['all', 'p0', 'semantic'].includes(captureScope)) {
  throw new Error(`P1_S4_CAPTURE_SCOPE 无效：${captureScope}`)
}

function sleep(ms) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms))
}

async function post(path, body) {
  const response = await fetch(`${apiBase}${path}`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...(body.idempotency_key
        ? { 'Idempotency-Key': body.idempotency_key }
        : {}),
    },
    body: JSON.stringify(body),
  })
  const text = await response.text()
  let payload = null
  try {
    payload = JSON.parse(text)
  } catch {
    payload = { non_json_body: text }
  }
  return {
    http_status: response.status,
    ok: response.ok,
    payload,
  }
}

function p0Turns(caseV13) {
  const base = p0base.cases.find((item) => item.case_id === caseV13.base_case_id)
  if (!base) throw new Error(`找不到 ${caseV13.base_case_id}`)
  if (Array.isArray(base.turns)) return base.turns.map((turn) => turn.user)
  return [caseV13.question]
}

function summarizeTurn(raw) {
  const turn = raw.payload?.turn
  const answer = turn?.answer
  return {
    http_status: raw.http_status,
    state: turn?.state ?? null,
    error: turn?.error ?? raw.payload?.error ?? null,
    answerability: answer?.answerability ?? null,
    answer_text: answer?.answer_text ?? null,
    user_goal_plan: answer?.semantic_plan?.user_goal_plan ?? null,
    grounding_plan: answer?.semantic_plan?.grounding_plan ?? null,
    results: answer?.results ?? null,
    evidence: answer?.evidence ?? null,
    execution_trace: answer?.execution_trace ?? null,
    validation: answer?.validation ?? null,
    state_receipt: answer?.state_receipt ?? null,
    binding: answer?.binding ?? null,
    runtime_identity: answer?.runtime_identity ?? null,
  }
}

async function runP0CaseAttempt(caseV13, attemptNumber) {
  const attemptId = `${runId}-${caseV13.case_id}-a${attemptNumber}`
  const created = await post('/conversations', {
    ...event,
    idempotency_key: `${attemptId}-create`,
  })
  if (!created.ok || !created.payload?.conversation?.conversation_id) {
    return {
      attempt: attemptNumber,
      success: false,
      create: created,
      turns: [],
    }
  }
  const conversationId = created.payload.conversation.conversation_id
  const turns = []
  let success = true
  for (const [index, question] of p0Turns(caseV13).entries()) {
    const raw = await post(`/conversations/${conversationId}/turns`, {
      question,
      idempotency_key: `${attemptId}-turn-${index + 1}`,
    })
    const summary = summarizeTurn(raw)
    turns.push({
      turn_number: index + 1,
      question,
      ...summary,
    })
    const plannerAccepted = summary.execution_trace?.planner_outcome === 'accepted'
    if (!raw.ok || summary.state !== 'completed' || !plannerAccepted) {
      success = false
      break
    }
    await sleep(1200)
  }
  return {
    attempt: attemptNumber,
    success,
    conversation_id: conversationId,
    binding: created.payload.conversation.binding,
    binding_generation: created.payload.conversation.binding_generation,
    create_http_status: created.http_status,
    turns,
  }
}

async function captureP0() {
  const build = spawnSync('npm', ['run', 'build'], {
    cwd: sidecarRoot,
    encoding: 'utf8',
  })
  if (build.status !== 0) {
    throw new Error(`无法构建同候选 Sidecar：${build.stderr || build.stdout}`)
  }
  const cases = []
  for (const caseV13 of p0v13.cases) {
    if (caseV13.case_id === 'P013-X-04' || caseV13.case_id === 'P013-X-05') {
      const testName = caseV13.case_id === 'P013-X-04'
        ? 'P0-X-04 overview 与 series 跨 publication 冲突时整轮失败且旧状态不变'
        : 'P0-X-05 series 声明人口与轨道长度不一致时整轮失败且不计算极值'
      const receiptPath = resolve(
        evaluationRoot,
        `.${runId}-${caseV13.case_id}-controlled-receipt.json`,
      )
      const testRun = spawnSync(process.execPath, [
        '--test',
        '--test-name-pattern',
        testName,
        'dist/tests/p1-runtime-v2-conversation.test.js',
      ], {
        cwd: sidecarRoot,
        encoding: 'utf8',
        env: {
          ...process.env,
          P1_S4_CONTROLLED_RECEIPT_PATH: receiptPath,
          P1_S4_CONTROLLED_RECEIPT_CASE: caseV13.case_id,
          P1_S4_CANDIDATE_ID: candidateId,
        },
      })
      if (testRun.status !== 0) {
        throw new Error(
          `${caseV13.case_id} 故障注入测试失败：${testRun.stderr || testRun.stdout}`,
        )
      }
      let receipt
      try {
        receipt = JSON.parse(readFileSync(receiptPath, 'utf8'))
      } finally {
        unlinkSync(receiptPath)
      }
      cases.push({
        case_id: caseV13.case_id,
        category: caseV13.category,
        evidence_mode: 'controlled_deterministic_injection',
        success: true,
        actual_answerability: 'invalid_data_fail_closed',
        reason: '当前真实 publication 不能安全制造跨 publication 或数组截断；使用同候选 Provider 故障注入验证整轮回滚',
        controlled_failure_receipt: receipt,
      })
      process.stdout.write(`[P0] ${caseV13.case_id} controlled injection\n`)
      continue
    }
    const attempts = []
    for (let attempt = 1; attempt <= maximumAttempts; attempt += 1) {
      const value = await runP0CaseAttempt(caseV13, attempt)
      attempts.push(value)
      if (value.success) break
      process.stdout.write(`[P0] ${caseV13.case_id} attempt ${attempt} failed closed\n`)
      await sleep(1500)
    }
    const finalAttempt = attempts.at(-1)
    cases.push({
      case_id: caseV13.case_id,
      category: caseV13.category,
      evidence_mode: 'live_model_api',
      success: finalAttempt?.success === true,
      attempts,
      accepted_attempt: finalAttempt?.success ? finalAttempt.attempt : null,
    })
    process.stdout.write(
      `[P0] ${caseV13.case_id} ${finalAttempt?.success ? 'accepted' : 'FAILED'} attempt=${finalAttempt?.attempt}\n`,
    )
    await sleep(1500)
  }
  const document = {
    schema_version: 'country_outage_p1_s4_p0_live_evidence_v2',
    candidate_id: candidateId,
    run_id: runId,
    captured_at: new Date().toISOString(),
    event_binding_request: event,
    retry_semantics: {
      automatic_retry_inside_turn: false,
      evaluation_driver_explicit_attempts: true,
      maximum_attempts: maximumAttempts,
      failed_attempts_preserved: true,
      new_conversation_per_case_attempt: true,
    },
    cases,
    counts: {
      total: cases.length,
      live_model_api: cases.filter((item) => item.evidence_mode === 'live_model_api').length,
      controlled_deterministic_injection: cases.filter((item) => item.evidence_mode === 'controlled_deterministic_injection').length,
      accepted: cases.filter((item) => item.success).length,
      failed: cases.filter((item) => !item.success).length,
      first_attempt_accepted: cases.filter((item) => item.accepted_attempt === 1).length,
      explicit_retry_accepted: cases.filter((item) => (item.accepted_attempt ?? 0) > 1).length,
    },
    boundary: '实时回执保留完整 UserGoalPlan、GroundingPlan、Tool 执行、证据、状态和失败尝试；故障注入只用于不能在真实 publication 安全制造的身份/形状冲突。',
  }
  writeFileSync(
    resolve(evaluationRoot, 's4-p0-live-evidence.json'),
    `${JSON.stringify(document, null, 2)}\n`,
    'utf8',
  )
  const actualAnswerability = (item) => {
    if (item.evidence_mode === 'controlled_deterministic_injection') {
      return item.actual_answerability
    }
    const accepted = item.attempts.find(
      (attempt) => attempt.attempt === item.accepted_attempt,
    )
    return accepted?.turns?.at(-1)?.answerability ?? null
  }
  const expectedActual = {
    answerable: new Set(['supported']),
    partial: new Set(['partial']),
    clarify: new Set(['clarify']),
    unsupported: new Set(['unsupported']),
    invalid_data: new Set(['invalid_data', 'invalid_data_fail_closed']),
  }
  const results = cases.map((item, index) => {
    const contract = p0v13.cases.find((value) => value.case_id === item.case_id)
    const actual = actualAnswerability(item)
    return {
      case_id: item.case_id,
      expected_answerability: contract.expected_mode,
      actual_answerability: actual,
      passed: item.success === true
        && expectedActual[contract.expected_mode]?.has(actual) === true,
      hard_gates_passed: contract.hard_gates,
      proof: [{
        artifact: 'evaluation/country-outage/p1-runtime-v2/s4-p0-live-evidence.json',
        json_pointer: `/cases/${index}`,
        record_id: item.case_id,
      }],
    }
  })
  const passed = results.filter((item) => item.passed)
  const resultDocument = {
    schema_version: 'country_outage_p1_runtime_v2_p0_results_v2',
    candidate_id: candidateId,
    p0_entry_revision: p0v13.revision,
    collector_id: 'rrc25',
    evaluated_at: new Date().toISOString(),
    counts: {
      total: results.length,
      passed: passed.length,
      direct: `${passed.filter((item) => item.case_id.includes('-D-')).length}/20`,
      multi_turn: `${passed.filter((item) => item.case_id.includes('-M-')).length}/5`,
      boundary: `${passed.filter((item) => item.case_id.includes('-B-')).length}/5`,
      exception: `${passed.filter((item) => item.case_id.includes('-X-')).length}/5`,
    },
    results,
  }
  writeFileSync(
    resolve(evaluationRoot, 's4-p0-v1-3-results.json'),
    `${JSON.stringify(resultDocument, null, 2)}\n`,
    'utf8',
  )
  return document
}

async function captureSemanticVariants() {
  const results = []
  for (const semanticCase of semanticVariants.cases) {
    const attempts = []
    for (let attempt = 1; attempt <= maximumAttempts; attempt += 1) {
      const attemptId = `${runId}-${semanticCase.case_id}-a${attempt}`
      const created = await post('/conversations', {
        ...event,
        idempotency_key: `${attemptId}-create`,
      })
      let raw = null
      if (created.ok && created.payload?.conversation?.conversation_id) {
        raw = await post(
          `/conversations/${created.payload.conversation.conversation_id}/turns`,
          {
            question: semanticCase.question,
            idempotency_key: `${attemptId}-turn-1`,
          },
        )
      }
      const answer = raw?.payload?.turn?.answer
      const accepted = created.ok
        && raw?.ok
        && raw.payload?.turn?.state === 'completed'
        && answer?.execution_trace?.planner_outcome === 'accepted'
        && answer?.semantic_plan?.user_goal_plan
      attempts.push({
        attempt,
        conversation_id: created.payload?.conversation?.conversation_id ?? null,
        create_http_status: created.http_status,
        turn_http_status: raw?.http_status ?? null,
        accepted: Boolean(accepted),
        error: !created.ok
          ? created.payload?.error ?? created.payload
          : !raw?.ok
            ? raw?.payload?.error ?? raw?.payload
            : raw?.payload?.turn?.error ?? null,
        answer: raw?.ok ? answer : null,
      })
      if (accepted) break
      process.stdout.write(`[SEM] ${semanticCase.case_id} attempt ${attempt} failed closed\n`)
      await sleep(1500)
    }
    const finalAttempt = attempts.at(-1)
    results.push({
      case_id: semanticCase.case_id,
      status: finalAttempt?.accepted ? 'accepted' : 'failed',
      user_goal_plan: finalAttempt?.answer?.semantic_plan?.user_goal_plan ?? null,
      grounding_plan: finalAttempt?.answer?.semantic_plan?.grounding_plan ?? null,
      answerability: finalAttempt?.answer?.answerability ?? null,
      attempts,
      accepted_attempt: finalAttempt?.accepted ? finalAttempt.attempt : null,
    })
    process.stdout.write(
      `[SEM] ${semanticCase.case_id} ${finalAttempt?.accepted ? 'accepted' : 'FAILED'} attempt=${finalAttempt?.attempt}\n`,
    )
    await sleep(1500)
  }
  const document = {
    schema_version: 'country_outage_p1_s4_current_prompt_candidate_v1',
    candidate_id: candidateId,
    candidate_identity: 'codex-cli:0.147.0-alpha.6.5:gpt-5.6-sol:blind-v2',
    prompt_identity: 'runtime-prompt-v2-current-s4:conversation:has_dialog_state=true',
    blind_input: true,
    run_id: runId,
    captured_at: new Date().toISOString(),
    retry_semantics: {
      automatic_retry_inside_turn: false,
      evaluation_driver_explicit_attempts: true,
      maximum_attempts: maximumAttempts,
      failed_attempts_preserved: true,
    },
    results,
    counts: {
      total: results.length,
      accepted: results.filter((item) => item.status === 'accepted').length,
      failed: results.filter((item) => item.status !== 'accepted').length,
      first_attempt_accepted: results.filter((item) => item.accepted_attempt === 1).length,
      explicit_retry_accepted: results.filter((item) => (item.accepted_attempt ?? 0) > 1).length,
    },
  }
  writeFileSync(
    resolve(evaluationRoot, 's4-semantic-current-prompt-candidate.json'),
    `${JSON.stringify(document, null, 2)}\n`,
    'utf8',
  )
  return document
}

const p0 = captureScope === 'semantic'
  ? JSON.parse(readFileSync(
      resolve(evaluationRoot, 's4-p0-live-evidence.json'),
      'utf8',
    ))
  : await captureP0()
if (p0.candidate_id !== candidateId) {
  throw new Error(
    `P0 已落盘证据候选不一致：${p0.candidate_id} != ${candidateId}`,
  )
}
const semantic = captureScope === 'p0'
  ? null
  : await captureSemanticVariants()
process.stdout.write(`${JSON.stringify({
  scope: captureScope,
  p0: p0.counts,
  semantic: semantic?.counts ?? null,
})}\n`)
if (p0.counts.failed > 0 || (semantic?.counts.failed ?? 0) > 0) {
  process.exitCode = 1
}
