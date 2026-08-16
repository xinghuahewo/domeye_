import { randomUUID } from 'node:crypto'

import {
  P2S1W5ContractError,
  p2S1W5AssertDigest,
  p2S1W5AssertNonempty,
  p2S1W5AssertUnique,
  p2S1W5Clone,
  p2S1W5DeepFreeze,
  p2S1W5Digest,
  p2S1W5DigestWithout,
  type P2S1W5AnswerClaim,
  type P2S1W5CommittedEvidenceGraphRecord,
  type P2S1W5DifferenceItem,
  type P2S1W5QuestionOracleRecord,
  type P2S1W5StructuredFeedback,
  type P2S1W5StudentAnswerPayload,
  type P2S1W5TeacherReference,
  type P2S1W5ValidationReceipt,
} from './p2-s1-composition-contracts.js'

const GATE_IDS = ['GATE-01', 'GATE-02', 'GATE-03', 'GATE-04', 'GATE-05'] as const

function objectValue(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new P2S1W5ContractError('model_output_invalid', `${label} 必须是对象`)
  }
  return value as Record<string, unknown>
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], label: string): void {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new P2S1W5ContractError('model_output_invalid', `${label} 字段集合不符合冻结合同`)
  }
}

function stringArray(value: unknown, label: string, minimum = 0): string[] {
  if (!Array.isArray(value) || value.length < minimum || value.some((item) => typeof item !== 'string' || !item.trim())) {
    throw new P2S1W5ContractError('model_output_invalid', `${label} 必须是非空字符串数组`)
  }
  p2S1W5AssertUnique(value, label)
  return [...value]
}

export function validateP2S1W5TeacherReference(options: {
  value: unknown
  sharedAnswerBindingDigest: string
}): P2S1W5TeacherReference {
  const value = objectValue(options.value, 'TeacherReference')
  exactKeys(value, [
    'teacher_reference_id', 'shared_answer_binding_digest', 'required_fact_ids',
    'evidence_refs', 'boundary_assertions', 'unknowns', 'answer_outline',
    'teacher_reference_is_ground_truth', 'private_chain_of_thought_persisted',
    'output_digest',
  ], 'TeacherReference')
  p2S1W5AssertNonempty(value.teacher_reference_id, 'teacher_reference_id')
  if (value.shared_answer_binding_digest !== options.sharedAnswerBindingDigest) {
    throw new P2S1W5ContractError('teacher_reference_binding_mismatch', 'TeacherReference shared binding 不一致')
  }
  if (value.teacher_reference_is_ground_truth !== false || value.private_chain_of_thought_persisted !== false) {
    throw new P2S1W5ContractError('teacher_role_violation', 'Teacher 不是事实真值，且不得持久化私有思维链')
  }
  stringArray(value.required_fact_ids, 'required_fact_ids')
  stringArray(value.evidence_refs, 'evidence_refs', 1)
  stringArray(value.boundary_assertions, 'boundary_assertions', 1)
  stringArray(value.unknowns, 'unknowns')
  stringArray(value.answer_outline, 'answer_outline', 1)
  p2S1W5AssertDigest(value.output_digest, 'TeacherReference.output_digest')
  if (value.output_digest !== p2S1W5DigestWithout(value, 'output_digest')) {
    throw new P2S1W5ContractError('teacher_reference_digest_mismatch', 'TeacherReference 摘要不一致')
  }
  return p2S1W5DeepFreeze(p2S1W5Clone(value as unknown as P2S1W5TeacherReference))
}

const CLAIM_RELATION: Readonly<Record<P2S1W5AnswerClaim['claim_kind'], P2S1W5AnswerClaim['claim_relation']>> = {
  observed_fact: 'states_observed_fact',
  derived_fact: 'states_derived_fact',
  knowledge_explanation: 'explains_knowledge',
  testable_hypothesis: 'proposes_testable_hypothesis',
  limitation: 'states_limitation',
  unknown: 'states_unknown',
}

function validateClaim(raw: unknown, index: number): P2S1W5AnswerClaim {
  const claim = objectValue(raw, `claims[${index}]`)
  exactKeys(claim, [
    'claim_id', 'claim_kind', 'claim_relation', 'text', 'fact_ids', 'source_node_ids',
    'source_value_digests', 'evidence_refs', 'boundary_assertion_ids',
    'verification_requirements',
  ], `claims[${index}]`)
  p2S1W5AssertNonempty(claim.claim_id, 'claim_id')
  p2S1W5AssertNonempty(claim.text, 'claim.text')
  if (!(String(claim.claim_kind) in CLAIM_RELATION)) {
    throw new P2S1W5ContractError('typed_claim_invalid', 'claim_kind 无效')
  }
  const kind = claim.claim_kind as P2S1W5AnswerClaim['claim_kind']
  if (claim.claim_relation !== CLAIM_RELATION[kind]) {
    throw new P2S1W5ContractError('typed_claim_relation_mismatch', 'claim_kind 与 claim_relation 不一致')
  }
  const factIds = stringArray(claim.fact_ids, 'claim.fact_ids')
  const nodeIds = stringArray(claim.source_node_ids, 'claim.source_node_ids')
  const valueDigests = stringArray(claim.source_value_digests, 'claim.source_value_digests')
  valueDigests.forEach((item) => p2S1W5AssertDigest(item, 'source_value_digest'))
  const evidenceRefs = stringArray(claim.evidence_refs, 'claim.evidence_refs')
  const boundaryIds = stringArray(claim.boundary_assertion_ids, 'claim.boundary_assertion_ids', 1)
  const verification = stringArray(claim.verification_requirements, 'claim.verification_requirements')
  const factual = kind === 'observed_fact' || kind === 'derived_fact'
  const epistemic = kind === 'knowledge_explanation' || kind === 'testable_hypothesis'
  if (factual && (!factIds.length || !nodeIds.length || !valueDigests.length || !evidenceRefs.length || verification.length)) {
    throw new P2S1W5ContractError('typed_claim_invalid', 'observed/derived fact 必须完整绑定事实、节点、值摘要和证据')
  }
  if (epistemic && (factIds.length || nodeIds.length || valueDigests.length || evidenceRefs.length || !verification.length)) {
    throw new P2S1W5ContractError('typed_claim_invalid', '知识解释/假设不得冒充事件事实，且必须声明验证要求')
  }
  if ((kind === 'limitation' || kind === 'unknown')
    && (factIds.length || nodeIds.length || valueDigests.length || evidenceRefs.length)) {
    throw new P2S1W5ContractError('typed_claim_invalid', '限制/未知不得携带事件事实或证据')
  }
  return p2S1W5DeepFreeze(p2S1W5Clone(claim as unknown as P2S1W5AnswerClaim))
}

export function validateP2S1W5StudentAnswerPayload(value: unknown): P2S1W5StudentAnswerPayload {
  const payload = objectValue(value, 'StudentAnswer')
  exactKeys(payload, ['claims', 'evidence_refs', 'limitations', 'unknowns', 'answer_text'], 'StudentAnswer')
  if (!Array.isArray(payload.claims)) throw new P2S1W5ContractError('model_output_invalid', 'claims 必须是数组')
  const claims = payload.claims.map(validateClaim)
  p2S1W5AssertUnique(claims.map((claim) => claim.claim_id), 'claim_id')
  const evidenceRefs = stringArray(payload.evidence_refs, 'StudentAnswer.evidence_refs')
  const limitations = stringArray(payload.limitations, 'StudentAnswer.limitations')
  const unknowns = stringArray(payload.unknowns, 'StudentAnswer.unknowns')
  p2S1W5AssertNonempty(payload.answer_text, 'StudentAnswer.answer_text')
  return p2S1W5DeepFreeze({
    claims,
    evidence_refs: evidenceRefs,
    limitations,
    unknowns,
    answer_text: payload.answer_text,
  })
}

function createReceipt(options: {
  subjectDigest: string
  sharedAnswerBindingDigest: string
  passed: readonly boolean[]
}): P2S1W5ValidationReceipt {
  const gateResults = GATE_IDS.map((gateId, index) => {
    const withoutDigest = {
      gate_id: gateId,
      passed: options.passed[index] ?? false,
      subject_digest: options.subjectDigest,
      shared_answer_binding_digest: options.sharedAnswerBindingDigest,
    }
    return {
      gate_id: gateId,
      passed: withoutDigest.passed,
      receipt_digest: p2S1W5Digest(withoutDigest),
    }
  })
  const withoutDigest = {
    validation_id: `validation:${randomUUID()}`,
    subject_digest: options.subjectDigest,
    shared_answer_binding_digest: options.sharedAnswerBindingDigest,
    gate_results: gateResults,
    all_gates_passed: gateResults.every((gate) => gate.passed),
  }
  return p2S1W5DeepFreeze({
    ...withoutDigest,
    receipt_digest: p2S1W5Digest(withoutDigest),
  })
}

function allIn(values: readonly string[], allowed: ReadonlySet<string>): boolean {
  return values.every((value) => allowed.has(value))
}

function textContainsAny(texts: readonly string[], prohibited: readonly string[]): boolean {
  const normalized = texts.join('\n').toLocaleLowerCase()
  return prohibited.some((item) => normalized.includes(item.toLocaleLowerCase()))
}

export function validateP2S1W5TeacherGates(options: {
  reference: P2S1W5TeacherReference
  graph: P2S1W5CommittedEvidenceGraphRecord
  sharedAnswerBindingDigest: string
  prohibitedAssertions: readonly string[]
}): P2S1W5ValidationReceipt {
  const facts = new Set(options.graph.facts.map((fact) => fact.fact_id))
  const evidence = new Set(options.graph.facts.flatMap((fact) => fact.evidence_refs))
  const boundaries = new Set(options.graph.boundary_assertion_ids)
  const unknowns = new Set(options.graph.unknown_ids)
  const receipt = createReceipt({
    subjectDigest: options.reference.output_digest,
    sharedAnswerBindingDigest: options.sharedAnswerBindingDigest,
    passed: [
      options.reference.shared_answer_binding_digest === options.sharedAnswerBindingDigest,
      allIn(options.reference.required_fact_ids, facts) && allIn(options.reference.evidence_refs, evidence),
      options.reference.answer_outline.length > 0 && options.reference.evidence_refs.length > 0,
      allIn(options.reference.boundary_assertions, boundaries) && allIn(options.reference.unknowns, unknowns),
      !textContainsAny(options.reference.answer_outline, options.prohibitedAssertions),
    ],
  })
  return receipt
}

export interface P2S1W5StudentGateEvaluation {
  receipt: P2S1W5ValidationReceipt
  differences: P2S1W5DifferenceItem[]
}

export function validateP2S1W5StudentGates(options: {
  payload: P2S1W5StudentAnswerPayload
  answerDigest: string
  graph: P2S1W5CommittedEvidenceGraphRecord
  oracle: P2S1W5QuestionOracleRecord
  teacherReference: P2S1W5TeacherReference | null
  sharedAnswerBindingDigest: string
  prohibitedAssertions: readonly string[]
}): P2S1W5StudentGateEvaluation {
  const graphFacts = new Map(options.graph.facts.map((fact) => [fact.fact_id, fact]))
  const graphEvidence = new Set(options.graph.facts.flatMap((fact) => fact.evidence_refs))
  // GATE-04 只验证边界断言来自同一受信 EvidenceGraph；Oracle 对允许边界的
  // 更窄比较留给独立 Alignment evaluator，二者不合并为一个复合算子。
  const allowedBoundaries = new Set(options.graph.boundary_assertion_ids)
  const claimedFacts = new Set(options.payload.claims.flatMap((claim) => claim.fact_ids))
  const claimBoundaries = new Set(options.payload.claims.flatMap((claim) => claim.boundary_assertion_ids))
  const sourceClosure = options.payload.claims.every((claim) => {
    if (!['observed_fact', 'derived_fact'].includes(claim.claim_kind)) return true
    return claim.fact_ids.every((factId) => {
      const fact = graphFacts.get(factId)
      return Boolean(fact)
        && claim.source_node_ids.includes(fact!.source_node_id)
        && claim.source_value_digests.includes(fact!.source_value_digest)
        && fact!.evidence_refs.some((ref) => claim.evidence_refs.includes(ref))
    })
  })
  const requiredFacts = options.teacherReference?.required_fact_ids ?? options.oracle.required_fact_ids
  const requiredUnknowns = options.oracle.required_unknown_ids
  const differences: P2S1W5DifferenceItem[] = []
  for (const factId of requiredFacts) {
    if (!claimedFacts.has(factId)) differences.push({ kind: 'missing_required_fact_id', reference_id: factId })
  }
  for (const ref of options.payload.evidence_refs) {
    if (!graphEvidence.has(ref)) differences.push({ kind: 'incorrect_evidence_ref', reference_id: ref })
  }
  for (const boundaryId of options.oracle.required_boundary_assertion_ids) {
    if (!claimBoundaries.has(boundaryId)) differences.push({ kind: 'missing_boundary_assertion', reference_id: boundaryId })
  }
  for (const unknownId of requiredUnknowns) {
    if (!options.payload.unknowns.includes(unknownId)) differences.push({ kind: 'missing_unknown', reference_id: unknownId })
  }
  const evidenceClosed = allIn(options.payload.evidence_refs, graphEvidence)
    && options.payload.claims.every((claim) => allIn(claim.evidence_refs, graphEvidence))
    && sourceClosure
  const completeness = differences.every((item) => ![
    'missing_required_fact_id', 'missing_unknown', 'structure_gap',
  ].includes(item.kind))
  const boundaryPassed = options.payload.claims.every((claim) => allIn(claim.boundary_assertion_ids, allowedBoundaries))
    && options.oracle.required_boundary_assertion_ids.every((id) => claimBoundaries.has(id))
  const text = [options.payload.answer_text, ...options.payload.claims.map((claim) => claim.text)]
  const prohibitedPassed = !textContainsAny(text, options.prohibitedAssertions)
  const receipt = createReceipt({
    subjectDigest: options.answerDigest,
    sharedAnswerBindingDigest: options.sharedAnswerBindingDigest,
    passed: [true, evidenceClosed, completeness, boundaryPassed, prohibitedPassed],
  })
  if (!receipt.all_gates_passed && differences.length === 0) {
    differences.push({ kind: 'structure_gap', reference_id: 'student_answer_gate_failure' })
  }
  return p2S1W5DeepFreeze({ receipt, differences })
}

export function createP2S1W5StructuredFeedback(options: {
  answerDigest: string
  validationReceipt: P2S1W5ValidationReceipt
  differences: readonly P2S1W5DifferenceItem[]
}): P2S1W5StructuredFeedback {
  if (options.validationReceipt.all_gates_passed) {
    throw new P2S1W5ContractError('feedback_not_admitted', '通过验证的 Student answer 不得进入 revision')
  }
  if (!options.differences.length) {
    throw new P2S1W5ContractError('feedback_empty', 'revision 必须有确定性差异反馈')
  }
  const withoutDigest = {
    feedback_round: 1 as const,
    producer_kind: 'host_deterministic_alignment_evaluator' as const,
    source_student_answer_digest: options.answerDigest,
    source_validation_receipt_digest: options.validationReceipt.receipt_digest,
    difference_items: p2S1W5Clone([...options.differences]),
    may_add_event_facts: false as const,
    may_change_evidence: false as const,
    may_change_prompt_or_policy: false as const,
  }
  return p2S1W5DeepFreeze({
    ...withoutDigest,
    feedback_digest: p2S1W5Digest(withoutDigest),
  })
}
