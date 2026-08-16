import { randomUUID } from 'node:crypto'

import {
  P2S1W5ContractError,
  p2S1W5AssertUnique,
  p2S1W5Clone,
  p2S1W5DeepFreeze,
  p2S1W5Digest,
  type P2S1W5QuestionOracleRecord,
  type P2S1W5QuestionOracleSeed,
  type P2S1W5TeacherOracleCoverageReceipt,
  type P2S1W5TeacherReference,
} from './p2-s1-composition-contracts.js'

export function p2S1W5OracleAssertionId(
  questionId: string,
  kind: 'boundary' | 'unknown' | 'prohibited',
  text: string,
): string {
  return `oracle:${questionId}:${kind}:${p2S1W5Digest(text).slice(0, 24)}`
}

export function materializeP2S1W5QuestionOracle(
  seed: P2S1W5QuestionOracleSeed,
): P2S1W5QuestionOracleRecord {
  if (!/^Q[0-9]{2}$/.test(seed.question_id)) {
    throw new P2S1W5ContractError('oracle_seed_invalid', 'Oracle seed question_id 无效')
  }
  for (const [label, values] of Object.entries(seed)) {
    if (label === 'question_id') continue
    if (!Array.isArray(values) || values.some((item) => typeof item !== 'string' || !item.trim())) {
      throw new P2S1W5ContractError('oracle_seed_invalid', `Oracle seed ${label} 无效`)
    }
    p2S1W5AssertUnique(values, `Oracle seed ${label}`)
  }
  const requiredBoundary = seed.required_boundary_assertions.map((text) =>
    p2S1W5OracleAssertionId(seed.question_id, 'boundary', text))
  const allowedBoundary = [
    ...requiredBoundary,
    ...seed.allowed_boundary_assertions.map((text) =>
      p2S1W5OracleAssertionId(seed.question_id, 'boundary', text)),
  ].filter((value, index, values) => values.indexOf(value) === index)
  const withoutDigest = {
    question_id: seed.question_id,
    required_fact_ids: p2S1W5Clone(seed.required_fact_ids),
    required_boundary_assertion_ids: requiredBoundary,
    allowed_boundary_assertion_ids: allowedBoundary,
    required_unknown_ids: seed.required_unknowns.map((text) =>
      p2S1W5OracleAssertionId(seed.question_id, 'unknown', text)),
    prohibited_assertion_ids: seed.prohibited_assertions.map((text) =>
      p2S1W5OracleAssertionId(seed.question_id, 'prohibited', text)),
  }
  return p2S1W5DeepFreeze({
    ...withoutDigest,
    oracle_digest: p2S1W5Digest(withoutDigest),
  })
}

function containsAll(actual: readonly string[], required: readonly string[]): boolean {
  const values = new Set(actual)
  return required.every((item) => values.has(item))
}

export function evaluateP2S1W5TeacherOracleCoverage(options: {
  oracle: P2S1W5QuestionOracleRecord
  reference: P2S1W5TeacherReference
  sharedAnswerBindingDigest: string
  prohibitedAssertionTexts: readonly string[]
}): P2S1W5TeacherOracleCoverageReceipt {
  if (options.reference.shared_answer_binding_digest !== options.sharedAnswerBindingDigest) {
    throw new P2S1W5ContractError('oracle_coverage_binding_mismatch', 'Oracle coverage 输入 binding 不一致')
  }
  const normalized = options.reference.answer_outline.join('\n').toLocaleLowerCase()
  const prohibitedCount = options.prohibitedAssertionTexts.filter((item) =>
    normalized.includes(item.toLocaleLowerCase())).length
  const facts = containsAll(options.reference.required_fact_ids, options.oracle.required_fact_ids)
  const boundaries = containsAll(
    options.reference.boundary_assertions,
    options.oracle.required_boundary_assertion_ids,
  )
  const unknowns = containsAll(options.reference.unknowns, options.oracle.required_unknown_ids)
  const withoutDigest = {
    coverage_run_id: `oracle-coverage:${randomUUID()}`,
    question_id: options.oracle.question_id,
    shared_answer_binding_digest: options.sharedAnswerBindingDigest,
    oracle_digest: options.oracle.oracle_digest,
    teacher_reference_digest: options.reference.output_digest,
    required_fact_ids_complete: facts,
    required_boundary_assertions_complete: boundaries,
    required_unknowns_complete: unknowns,
    prohibited_assertion_count: prohibitedCount,
    disposition: facts && boundaries && unknowns && prohibitedCount === 0
      ? 'passed' as const
      : 'rejected' as const,
  }
  return p2S1W5DeepFreeze({
    ...withoutDigest,
    receipt_digest: p2S1W5Digest(withoutDigest),
  })
}
