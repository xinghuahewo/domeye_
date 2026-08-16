import { randomUUID } from 'node:crypto'

import {
  p2S1W5Clone,
  p2S1W5DeepFreeze,
  p2S1W5Digest,
  type P2S1W5AlignmentReceipt,
  type P2S1W5CommittedEvidenceGraphRecord,
  type P2S1W5QuestionOracleRecord,
  type P2S1W5StudentAnswerArtifact,
  type P2S1W5TeacherOracleCoverageReceipt,
  type P2S1W5TeacherReference,
} from './p2-s1-composition-contracts.js'
import { P2S1W5ArtifactStore } from './p2-s1-dual-artifact-store.js'

const EVALUATOR_ID = 'country_outage_p2_alignment_evaluator' as const
const EVALUATOR_VERSION = '1.0.0' as const
const EVALUATOR_CONTRACT_DIGEST = '2a7b9dc73194dc78fc797b9be30676ccdfde38599466e47305e50ef963e53b3b' as const
const EVALUATOR_IMPLEMENTATION_DIGEST = 'd193bee50269c2e464ed3b98ef0deb293dcaee1b55ea12aebbbd2e11ed4947cc' as const

function precision(actual: readonly string[], allowed: ReadonlySet<string>): number {
  if (actual.length === 0) return 1
  return actual.every((item) => allowed.has(item)) ? 1 : 0
}

function advisoryTextSimilarity(left: string, right: string): number {
  const tokenize = (value: string): Set<string> => new Set(
    value.toLocaleLowerCase().split(/[^\p{Letter}\p{Number}]+/u).filter(Boolean),
  )
  const leftTokens = tokenize(left)
  const rightTokens = tokenize(right)
  const union = new Set([...leftTokens, ...rightTokens])
  if (!union.size) return 1
  const overlap = [...leftTokens].filter((token) => rightTokens.has(token)).length
  return overlap / union.size
}

export function evaluateP2S1W5Alignment(options: {
  questionId: string
  sharedAnswerBindingDigest: string
  graph: P2S1W5CommittedEvidenceGraphRecord
  oracle: P2S1W5QuestionOracleRecord
  teacherReference: P2S1W5TeacherReference
  coverageReceipt: P2S1W5TeacherOracleCoverageReceipt
  studentArtifact: P2S1W5StudentAnswerArtifact
  store: P2S1W5ArtifactStore
}): P2S1W5AlignmentReceipt {
  const replayed = options.store.resolveStudentAnswer({
    artifactRef: options.studentArtifact.artifact_ref,
    sharedAnswerBindingDigest: options.sharedAnswerBindingDigest,
    expectedAnswerDigest: options.studentArtifact.answer_digest,
  })
  const factIds = replayed.answer_payload.claims.flatMap((claim) => claim.fact_ids)
  const evidenceRefs = [
    ...replayed.answer_payload.evidence_refs,
    ...replayed.answer_payload.claims.flatMap((claim) => claim.evidence_refs),
  ]
  const boundaryIds = replayed.answer_payload.claims.flatMap((claim) => claim.boundary_assertion_ids)
  const allowedFacts = new Set(options.graph.facts.map((fact) => fact.fact_id))
  const allowedEvidence = new Set(options.graph.facts.flatMap((fact) => fact.evidence_refs))
  const allowedBoundaries = new Set(options.oracle.allowed_boundary_assertion_ids)
  const hardGateMetrics = {
    fact_precision: precision(factIds, allowedFacts),
    evidence_ref_precision: precision(evidenceRefs, allowedEvidence),
    boundary_compliance: precision(boundaryIds, allowedBoundaries),
  }
  const hardPassed = Object.values(hardGateMetrics).every((value) => value === 1)
  const metricInputs = {
    question_id: options.questionId,
    oracle_digest: options.oracle.oracle_digest,
    evidence_graph_digest: options.graph.evidence_graph_digest,
    teacher_reference_digest: options.teacherReference.output_digest,
    teacher_oracle_coverage_receipt_digest: options.coverageReceipt.receipt_digest,
    student_answer_digest: replayed.answer_digest,
  }
  const withoutDigest = {
    alignment_run_id: `alignment:${randomUUID()}`,
    shared_answer_binding_digest: options.sharedAnswerBindingDigest,
    teacher_reference_digest: options.teacherReference.output_digest,
    student_answer_digest: replayed.answer_digest,
    oracle_digest: options.oracle.oracle_digest,
    evidence_graph_digest: options.graph.evidence_graph_digest,
    teacher_oracle_coverage_receipt_digest: options.coverageReceipt.receipt_digest,
    evaluator_id: EVALUATOR_ID,
    evaluator_version: EVALUATOR_VERSION,
    evaluator_contract_digest: EVALUATOR_CONTRACT_DIGEST,
    evaluator_implementation_digest: EVALUATOR_IMPLEMENTATION_DIGEST,
    metric_inputs_digest: p2S1W5Digest(metricInputs),
    hard_gate_metrics: hardGateMetrics,
    advisory_text_similarity: advisoryTextSimilarity(
      replayed.answer_payload.answer_text,
      options.teacherReference.answer_outline.join('\n'),
    ),
    hard_gates_passed: hardPassed,
    disposition: hardPassed ? 'passed' as const : 'rejected' as const,
  }
  const receipt: P2S1W5AlignmentReceipt = p2S1W5DeepFreeze({
    ...withoutDigest,
    receipt_digest: p2S1W5Digest(withoutDigest),
  })
  options.store.putAlignment(options.sharedAnswerBindingDigest, receipt)
  const resolved = options.store.resolveAlignment(receipt.receipt_digest, options.sharedAnswerBindingDigest)
  return p2S1W5DeepFreeze(p2S1W5Clone(resolved))
}
