import { randomUUID } from 'node:crypto'

import {
  P2S1W5ContractError,
  P2S1_W5_FROZEN_STUDENT_IDENTITY,
  p2S1W5AssertNonempty,
  p2S1W5CanonicalJson,
  p2S1W5Clone,
  p2S1W5DeepFreeze,
  p2S1W5Digest,
  p2S1W5DigestWithout,
  p2S1W5ValidateExactModelIdentity,
  type P2S1Json,
  type P2S1W5ExactModelIdentity,
  type P2S1W5ModelPhase,
  type P2S1W5ModelRunReceipt,
  type P2S1W5RunCost,
  type P2S1W5TrustedReplayFixture,
} from './p2-s1-composition-contracts.js'

export interface P2S1W5ModelPortRequest {
  fixture_id: string
  phase: P2S1W5ModelPhase
  exact_model_identity: P2S1W5ExactModelIdentity
  role_specific_input: P2S1Json
  tools: readonly []
  state_write_allowed: false
  retry_allowed: false
}

export interface P2S1W5ModelPortSuccess {
  disposition: 'completed'
  observed_model_identity: P2S1W5ExactModelIdentity
  output: P2S1Json
  cost: P2S1W5RunCost
  external_provider_called: false
  tool_calls: readonly []
  state_writes: readonly []
}

export interface P2S1W5ModelPortFailure {
  disposition: 'failed' | 'unavailable' | 'cancelled'
  observed_model_identity: P2S1W5ExactModelIdentity
  output: null
  cost: P2S1W5RunCost
  external_provider_called: false
  tool_calls: readonly []
  state_writes: readonly []
}

export type P2S1W5ModelPortResult = P2S1W5ModelPortSuccess | P2S1W5ModelPortFailure

/**
 * W5 只定义可注入端口。正式默认实现必须是下方的受信 fixture replay；
 * 此接口不是 provider 认证，也不得接入 P1 的认证 binding。
 */
export interface P2S1W5InjectedModelPort {
  readonly mode: 'trusted_fixture_replay'
  complete(request: P2S1W5ModelPortRequest): Promise<P2S1W5ModelPortResult>
}

export interface P2S1W5PhaseAttempt {
  receipt: P2S1W5ModelRunReceipt
  output: P2S1Json | null
}

const PHASE_LIMITS: Readonly<Record<P2S1W5ModelPhase, number>> = {
  sol_planning: 1,
  sol_reference: 1,
  ds_first_answer: 1,
  ds_revision: 1,
}

export class P2S1W5CallBudget {
  readonly #counts: Record<P2S1W5ModelPhase, number> = {
    sol_planning: 0,
    sol_reference: 0,
    ds_first_answer: 0,
    ds_revision: 0,
  }
  readonly #receipts: P2S1W5ModelRunReceipt[] = []

  constructor(private readonly degradedMode = false) {}

  admit(phase: P2S1W5ModelPhase): void {
    if (this.#counts[phase] >= PHASE_LIMITS[phase]) {
      throw new P2S1W5ContractError('model_phase_budget_exhausted', `${phase} 调用超过一次`)
    }
    if (phase === 'sol_reference' && this.#counts.sol_planning !== 1) {
      throw new P2S1W5ContractError('model_phase_order_violation', 'Sol reference 必须晚于唯一一次 Sol planning')
    }
    if (phase === 'ds_first_answer' && !this.degradedMode && this.#counts.sol_reference !== 1) {
      throw new P2S1W5ContractError('model_phase_order_violation', 'DS first 必须晚于唯一一次 Sol reference')
    }
    if (this.degradedMode && phase.startsWith('sol_')) {
      throw new P2S1W5ContractError('degraded_teacher_call_forbidden', '降级 revision 禁止 Teacher 调用')
    }
    if (phase === 'ds_revision' && this.#counts.ds_first_answer !== 1) {
      throw new P2S1W5ContractError('model_phase_order_violation', 'DS revision 必须晚于唯一一次 DS first')
    }
    this.#counts[phase] += 1
  }

  record(receipt: P2S1W5ModelRunReceipt): void {
    this.#receipts.push(p2S1W5Clone(receipt))
  }

  receipts(): P2S1W5ModelRunReceipt[] {
    return p2S1W5Clone(this.#receipts)
  }

  counts(): Record<P2S1W5ModelPhase, number> {
    return { ...this.#counts }
  }
}

function validateCost(value: P2S1W5RunCost): P2S1W5RunCost {
  for (const [name, amount] of Object.entries(value)) {
    if (name === 'cost_currency') continue
    if (typeof amount !== 'number' || !Number.isFinite(amount) || amount < 0) {
      throw new P2S1W5ContractError('invalid_model_cost', `模型费用字段 ${name} 无效`)
    }
  }
  if (!Number.isSafeInteger(value.latency_ms)
    || !Number.isSafeInteger(value.input_tokens)
    || !Number.isSafeInteger(value.output_tokens)
    || value.retry_count !== 0
    || !/^[A-Z]{3}$/.test(value.cost_currency)) {
    throw new P2S1W5ContractError('invalid_model_cost', '模型费用/用量回执不符合冻结合同')
  }
  return p2S1W5DeepFreeze(p2S1W5Clone(value))
}

function sameIdentity(left: P2S1W5ExactModelIdentity, right: P2S1W5ExactModelIdentity): boolean {
  return p2S1W5CanonicalJson(left) === p2S1W5CanonicalJson(right)
}

export async function runP2S1W5ModelPhase(options: {
  port: P2S1W5InjectedModelPort
  budget: P2S1W5CallBudget
  fixtureId: string
  phase: P2S1W5ModelPhase
  identity: P2S1W5ExactModelIdentity
  sharedAnswerBindingDigest: string
  roleSpecificInput: P2S1Json
}): Promise<P2S1W5PhaseAttempt> {
  if (options.port.mode !== 'trusted_fixture_replay') {
    throw new P2S1W5ContractError('external_provider_forbidden', 'W5 禁止外部模型 provider')
  }
  options.budget.admit(options.phase)
  const identity = p2S1W5ValidateExactModelIdentity(options.identity)
  const result = await options.port.complete({
    fixture_id: options.fixtureId,
    phase: options.phase,
    exact_model_identity: identity,
    role_specific_input: p2S1W5Clone(options.roleSpecificInput),
    tools: [],
    state_write_allowed: false,
    retry_allowed: false,
  })
  if (
    result.external_provider_called
    || result.tool_calls.length !== 0
    || result.state_writes.length !== 0
  ) {
    throw new P2S1W5ContractError('model_authority_violation', '模型端口越权调用 Tool/provider 或写状态')
  }
  const observed = p2S1W5ValidateExactModelIdentity(result.observed_model_identity)
  if (!sameIdentity(identity, observed)) {
    throw new P2S1W5ContractError('observed_model_identity_mismatch', '响应模型身份与冻结身份不一致')
  }
  const cost = validateCost(result.cost)
  const role = options.phase.startsWith('sol_') ? 'teacher' : 'student'
  const outputDigest = result.output === null ? null : p2S1W5Digest(result.output)
  const receipt: P2S1W5ModelRunReceipt = p2S1W5DeepFreeze({
    run_id: `model-run:${randomUUID()}`,
    role,
    run_phase: options.phase,
    exact_model_identity: identity,
    shared_answer_binding_digest: options.sharedAnswerBindingDigest,
    role_specific_input_digest: p2S1W5Digest(options.roleSpecificInput),
    output_digest: outputDigest,
    validation_receipt_digest: null,
    cost,
    disposition: result.disposition,
  })
  options.budget.record(receipt)
  return {
    receipt,
    output: result.output === null ? null : p2S1W5DeepFreeze(p2S1W5Clone(result.output)),
  }
}

export interface P2S1W5TrustedFixtureCatalog {
  resolve(fixtureId: string): P2S1W5TrustedReplayFixture
}

export class InMemoryP2S1W5TrustedFixtureCatalog implements P2S1W5TrustedFixtureCatalog {
  readonly #fixtures = new Map<string, P2S1W5TrustedReplayFixture>()

  constructor(fixtures: readonly P2S1W5TrustedReplayFixture[]) {
    if (!fixtures.length) throw new P2S1W5ContractError('fixture_catalog_empty', 'fixture catalog 不能为空')
    for (const raw of fixtures) {
      const fixture = validateP2S1W5TrustedReplayFixture(raw)
      if (this.#fixtures.has(fixture.fixture_id)) {
        throw new P2S1W5ContractError('fixture_duplicate', `fixture_id 重复：${fixture.fixture_id}`)
      }
      this.#fixtures.set(fixture.fixture_id, fixture)
    }
  }

  resolve(fixtureId: string): P2S1W5TrustedReplayFixture {
    p2S1W5AssertNonempty(fixtureId, 'fixture_id')
    const fixture = this.#fixtures.get(fixtureId)
    if (!fixture) throw new P2S1W5ContractError('fixture_not_found', '未找到受信 fixture')
    return p2S1W5DeepFreeze(p2S1W5Clone(fixture))
  }
}

export function validateP2S1W5TrustedReplayFixture(
  raw: P2S1W5TrustedReplayFixture,
): P2S1W5TrustedReplayFixture {
  p2S1W5AssertNonempty(raw.fixture_id, 'fixture_id')
  p2S1W5ValidateExactModelIdentity(raw.teacher_identity)
  p2S1W5ValidateExactModelIdentity(raw.student_identity)
  if (!sameIdentity(raw.student_identity, P2S1_W5_FROZEN_STUDENT_IDENTITY)) {
    throw new P2S1W5ContractError('student_identity_not_frozen', 'fixture 未绑定冻结 DS 身份')
  }
  if (raw.binding.question_id !== raw.oracle_seed.question_id) {
    throw new P2S1W5ContractError('oracle_question_mismatch', 'fixture 的 Oracle question 绑定不一致')
  }
  if (raw.grounding_plan.registry_snapshot_digest !== raw.evidence_graph.registry_snapshot_digest
    || raw.grounding_plan.registry_snapshot_id !== raw.evidence_graph.registry_snapshot_id
    || raw.grounding_plan.investigation_plan_digest !== raw.evidence_graph.investigation_plan_digest) {
    throw new P2S1W5ContractError('fixture_plan_graph_mismatch', 'fixture 计划、证据图或 Registry 不一致')
  }
  if (raw.degraded_authorization) {
    if (!raw.degraded_binding
      || raw.degraded_authorization.authorization_digest !== p2S1W5DigestWithout(
        raw.degraded_authorization as unknown as Record<string, unknown>,
        'authorization_digest',
      )) {
      throw new P2S1W5ContractError('fixture_degraded_authorization_invalid', 'fixture 降级授权摘要或 binding 无效')
    }
  } else if (raw.degraded_binding) {
    throw new P2S1W5ContractError('fixture_degraded_authorization_invalid', 'fixture 不得单独携带 degraded binding')
  }
  const expectedDigest = p2S1W5DigestWithout(
    raw as unknown as Record<string, unknown>,
    'fixture_digest',
  )
  if (raw.fixture_digest !== expectedDigest) {
    throw new P2S1W5ContractError('fixture_digest_mismatch', 'fixture 摘要不一致')
  }
  return p2S1W5DeepFreeze(p2S1W5Clone(raw))
}

export class ReplayOnlyP2S1W5ModelPort implements P2S1W5InjectedModelPort {
  readonly mode = 'trusted_fixture_replay' as const

  constructor(private readonly catalog: P2S1W5TrustedFixtureCatalog) {}

  async complete(request: P2S1W5ModelPortRequest): Promise<P2S1W5ModelPortResult> {
    if (request.tools.length || request.state_write_allowed || request.retry_allowed) {
      throw new P2S1W5ContractError('model_authority_violation', 'fixture runner 只接受零 Tool、零状态写、零重试请求')
    }
    const fixture = this.catalog.resolve(request.fixture_id)
    const expectedIdentity = request.phase.startsWith('sol_')
      ? fixture.teacher_identity
      : fixture.student_identity
    if (!sameIdentity(request.exact_model_identity, expectedIdentity)) {
      throw new P2S1W5ContractError('fixture_model_identity_mismatch', 'fixture 阶段模型身份不匹配')
    }
    const inputTokens = Math.ceil(p2S1W5CanonicalJson(request.role_specific_input).length / 4)
    const baseCost: P2S1W5RunCost = {
      latency_ms: 0,
      input_tokens: inputTokens,
      output_tokens: 0,
      cost_amount: 0,
      cost_currency: 'USD',
      retry_count: 0,
    }
    if (fixture.unavailable_phases.includes(request.phase)) {
      return p2S1W5DeepFreeze({
        disposition: 'unavailable',
        observed_model_identity: expectedIdentity,
        output: null,
        cost: baseCost,
        external_provider_called: false,
        tool_calls: [],
        state_writes: [],
      })
    }
    const scriptedOutput = fixture.scripted_outputs[request.phase]
    if (scriptedOutput === undefined) {
      return p2S1W5DeepFreeze({
        disposition: 'failed',
        observed_model_identity: expectedIdentity,
        output: null,
        cost: baseCost,
        external_provider_called: false,
        tool_calls: [],
        state_writes: [],
      })
    }
    const input = request.role_specific_input && typeof request.role_specific_input === 'object'
      && !Array.isArray(request.role_specific_input)
      ? request.role_specific_input as Record<string, P2S1Json>
      : {}
    const substitute = (value: P2S1Json): P2S1Json => {
      if (value === '$W5_SHARED_ANSWER_BINDING_DIGEST') {
        const digest = input.shared_answer_binding_digest
        if (typeof digest !== 'string') throw new P2S1W5ContractError('fixture_template_invalid', 'fixture 模板缺少 shared binding 输入')
        return digest
      }
      if (Array.isArray(value)) return value.map(substitute)
      if (value && typeof value === 'object') {
        const mapped = Object.fromEntries(Object.entries(value).map(([key, item]) => [key, substitute(item)]))
        if (mapped.output_digest === '$W5_RECOMPUTE_OUTPUT_DIGEST') {
          const digestInput = { ...mapped }
          delete digestInput.output_digest
          mapped.output_digest = p2S1W5Digest(digestInput)
        }
        return mapped
      }
      return value
    }
    const output = substitute(scriptedOutput)
    return p2S1W5DeepFreeze({
      disposition: 'completed',
      observed_model_identity: expectedIdentity,
      output: p2S1W5Clone(output),
      cost: {
        ...baseCost,
        output_tokens: Math.ceil(p2S1W5CanonicalJson(output).length / 4),
      },
      external_provider_called: false,
      tool_calls: [],
      state_writes: [],
    })
  }
}
