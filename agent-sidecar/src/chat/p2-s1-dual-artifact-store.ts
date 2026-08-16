import { randomUUID } from 'node:crypto'
import {
  closeSync,
  fsyncSync,
  linkSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
  unlinkSync,
  writeSync,
} from 'node:fs'
import { join, resolve } from 'node:path'

import {
  P2S1W5ContractError,
  p2S1W5CanonicalJson,
  p2S1W5Clone,
  p2S1W5DeepFreeze,
  p2S1W5Digest,
  p2S1W5DigestWithout,
  type P2S1Json,
  type P2S1W5AlignmentReceipt,
  type P2S1W5CommittedEvidenceGraphRecord,
  type P2S1W5DualModelFlow,
  type P2S1W5GroundingPlanRecord,
  type P2S1W5ModelRunReceipt,
  type P2S1W5QuestionOracleRecord,
  type P2S1W5StudentAnswerArtifact,
  type P2S1W5StudentAnswerPayload,
  type P2S1W5ValidationReceipt,
} from './p2-s1-composition-contracts.js'

export type P2S1W5ArtifactKind =
  | 'validated_plan'
  | 'committed_graph'
  | 'oracle'
  | 'student_answer'
  | 'alignment_receipt'
  | 'model_run_receipt'
  | 'gate_receipt'
  | 'flow_revision'
  | 'publish_receipt'

export interface P2S1W5StoredArtifact<T extends P2S1Json = P2S1Json> {
  schema_version: 'country_outage_p2_s1_w5_host_artifact_v1'
  kind: P2S1W5ArtifactKind
  record_key: string
  shared_answer_binding_digest: string
  store_contract_id: string
  attestation_provider_id: 'country_outage_p2_s1_w5_host'
  payload_digest: string
  payload: T
  receipt_digest: string
}

const STORE_CONTRACT_BY_KIND: Readonly<Record<P2S1W5ArtifactKind, string>> = {
  validated_plan: 'country_outage_p2_trusted_validated_plan_store_v1',
  committed_graph: 'country_outage_p2_trusted_committed_graph_store_v1',
  oracle: 'country_outage_p2_trusted_oracle_store_v1',
  student_answer: 'country_outage_p2_trusted_student_answer_artifact_store_v1',
  alignment_receipt: 'country_outage_p2_trusted_alignment_receipt_store_v1',
  model_run_receipt: 'country_outage_p2_s1_w5_model_run_receipt_store_v1',
  gate_receipt: 'country_outage_p2_s1_w5_gate_receipt_store_v1',
  flow_revision: 'country_outage_p2_s1_w5_flow_revision_store_v1',
  publish_receipt: 'country_outage_p2_s1_w5_publish_receipt_store_v1',
}

function toJson<T>(value: T): P2S1Json {
  return p2S1W5Clone(value) as unknown as P2S1Json
}

export class P2S1W5ArtifactStore {
  readonly #root: string

  constructor(root: string) {
    this.#root = resolve(root)
    mkdirSync(this.#root, { recursive: true, mode: 0o700 })
    const stat = lstatSync(this.#root)
    const uid = typeof process.getuid === 'function' ? process.getuid() : undefined
    if (
      !stat.isDirectory()
      || stat.isSymbolicLink()
      || (stat.mode & 0o022) !== 0
      || (uid !== undefined && stat.uid !== uid)
    ) {
      throw new P2S1W5ContractError('unsafe_artifact_store', 'W5 Store 必须为当前进程持有且禁止组/其他用户写入')
    }
    realpathSync(this.#root)
    for (const kind of Object.keys(STORE_CONTRACT_BY_KIND) as P2S1W5ArtifactKind[]) {
      mkdirSync(join(this.#root, kind), { mode: 0o700 })
    }
  }

  putValidatedPlan(
    bindingDigest: string,
    plan: P2S1W5GroundingPlanRecord,
    validationReceipt: P2S1Json,
  ): P2S1W5StoredArtifact {
    return this.#put('validated_plan', plan.investigation_plan_digest, bindingDigest, {
      plan: toJson(plan),
      validation_receipt: p2S1W5Clone(validationReceipt),
      validation_context: {
        trusted_registry_store: plan.registry_snapshot_id,
        parameter_bindings: [],
        previous_plan_definition: null,
        previous_investigation_snapshot: null,
      },
    })
  }

  putCommittedGraph(
    bindingDigest: string,
    graph: P2S1W5CommittedEvidenceGraphRecord,
    validationReceipt: P2S1Json,
  ): P2S1W5StoredArtifact {
    return this.#put('committed_graph', graph.evidence_graph_digest, bindingDigest, {
      graph: toJson(graph),
      validation_receipt: p2S1W5Clone(validationReceipt),
      validation_context: {
        plan_definition: graph.investigation_plan_digest,
        trusted_registry_store: graph.registry_snapshot_id,
        previous_graph: null,
      },
    })
  }

  putOracle(bindingDigest: string, oracle: P2S1W5QuestionOracleRecord): P2S1W5StoredArtifact {
    if (oracle.oracle_digest !== p2S1W5DigestWithout(
      oracle as unknown as Record<string, unknown>,
      'oracle_digest',
    )) throw new P2S1W5ContractError('oracle_digest_mismatch', 'Oracle record 摘要不一致')
    return this.#put('oracle', oracle.oracle_digest, bindingDigest, toJson(oracle))
  }

  putStudentAnswer(
    bindingDigest: string,
    payload: P2S1W5StudentAnswerPayload,
  ): P2S1W5StudentAnswerArtifact {
    const answerPayload = p2S1W5DeepFreeze(p2S1W5Clone(payload))
    const answerDigest = p2S1W5Digest(answerPayload)
    const withoutReceipt = {
      artifact_ref: `artifact:student-answer:${answerDigest}`,
      artifact_schema_ref: '#/$defs/studentAnswerPayload' as const,
      answer_payload: answerPayload,
      answer_digest: answerDigest,
    }
    const artifact: P2S1W5StudentAnswerArtifact = p2S1W5DeepFreeze({
      ...withoutReceipt,
      artifact_receipt_digest: p2S1W5Digest(withoutReceipt),
    })
    this.#put('student_answer', artifact.artifact_ref, bindingDigest, toJson(artifact))
    return artifact
  }

  resolveStudentAnswer(options: {
    artifactRef: string
    sharedAnswerBindingDigest: string
    expectedAnswerDigest: string
  }): P2S1W5StudentAnswerArtifact {
    const stored = this.#read('student_answer', options.artifactRef, options.sharedAnswerBindingDigest)
    const artifact = stored.payload as unknown as P2S1W5StudentAnswerArtifact
    if (
      artifact.artifact_ref !== options.artifactRef
      || artifact.answer_digest !== options.expectedAnswerDigest
      || artifact.answer_digest !== p2S1W5Digest(artifact.answer_payload)
      || artifact.artifact_receipt_digest !== p2S1W5DigestWithout(
        artifact as unknown as Record<string, unknown>,
        'artifact_receipt_digest',
      )
    ) throw new P2S1W5ContractError('student_artifact_replay_denied', 'Student artifact 无法从 Host Store 重放')
    return p2S1W5DeepFreeze(p2S1W5Clone(artifact))
  }

  putAlignment(bindingDigest: string, receipt: P2S1W5AlignmentReceipt): P2S1W5StoredArtifact {
    return this.#put('alignment_receipt', receipt.receipt_digest, bindingDigest, toJson(receipt))
  }

  resolveAlignment(
    receiptDigest: string,
    bindingDigest: string,
  ): P2S1W5AlignmentReceipt {
    const stored = this.#read('alignment_receipt', receiptDigest, bindingDigest)
    const receipt = stored.payload as unknown as P2S1W5AlignmentReceipt
    if (receipt.receipt_digest !== receiptDigest) {
      throw new P2S1W5ContractError('alignment_receipt_replay_denied', 'Alignment receipt 摘要不一致')
    }
    return p2S1W5DeepFreeze(p2S1W5Clone(receipt))
  }

  putModelRun(bindingDigest: string, receipt: P2S1W5ModelRunReceipt): P2S1W5StoredArtifact {
    const key = p2S1W5Digest(receipt)
    return this.#put('model_run_receipt', key, bindingDigest, toJson(receipt))
  }

  putGate(bindingDigest: string, receipt: P2S1W5ValidationReceipt): P2S1W5StoredArtifact {
    return this.#put('gate_receipt', receipt.receipt_digest, bindingDigest, toJson(receipt))
  }

  putFlow(bindingDigest: string, flow: P2S1W5DualModelFlow): P2S1W5StoredArtifact {
    return this.#put(
      'flow_revision',
      `${flow.flow_id}:r${flow.flow_revision}`,
      bindingDigest,
      toJson(flow),
    )
  }

  putPublish(bindingDigest: string, payload: P2S1Json): P2S1W5StoredArtifact {
    return this.#put('publish_receipt', p2S1W5Digest(payload), bindingDigest, payload)
  }

  #put(
    kind: P2S1W5ArtifactKind,
    recordKey: string,
    bindingDigest: string,
    payload: P2S1Json,
  ): P2S1W5StoredArtifact {
    const withoutReceipt = {
      schema_version: 'country_outage_p2_s1_w5_host_artifact_v1' as const,
      kind,
      record_key: recordKey,
      shared_answer_binding_digest: bindingDigest,
      store_contract_id: STORE_CONTRACT_BY_KIND[kind],
      attestation_provider_id: 'country_outage_p2_s1_w5_host' as const,
      payload_digest: p2S1W5Digest(payload),
      payload: p2S1W5Clone(payload),
    }
    const record: P2S1W5StoredArtifact = p2S1W5DeepFreeze({
      ...withoutReceipt,
      receipt_digest: p2S1W5Digest(withoutReceipt),
    })
    const target = this.#path(kind, recordKey, bindingDigest)
    const serialized = `${p2S1W5CanonicalJson(record)}\n`
    try {
      const existing = this.#readFile(target)
      if (existing !== serialized) {
        throw new P2S1W5ContractError('artifact_store_collision', '同一 Store key 已存在不同内容')
      }
      return record
    } catch (error) {
      if (!(error instanceof P2S1W5ContractError) || error.code !== 'artifact_missing') throw error
    }
    const temporary = join(this.#root, kind, `.${randomUUID()}.tmp`)
    let descriptor: number | undefined
    try {
      descriptor = openSync(temporary, 'wx', 0o600)
      writeSync(descriptor, serialized, undefined, 'utf8')
      fsyncSync(descriptor)
      closeSync(descriptor)
      descriptor = undefined
      try {
        linkSync(temporary, target)
      } catch (error) {
        const existing = this.#readFile(target)
        if (existing !== serialized) throw error
      }
    } finally {
      if (descriptor !== undefined) closeSync(descriptor)
      try { unlinkSync(temporary) } catch { /* 已清理或尚未创建。 */ }
    }
    return record
  }

  #read(
    kind: P2S1W5ArtifactKind,
    recordKey: string,
    bindingDigest: string,
  ): P2S1W5StoredArtifact {
    const text = this.#readFile(this.#path(kind, recordKey, bindingDigest))
    let raw: unknown
    try { raw = JSON.parse(text) } catch {
      throw new P2S1W5ContractError('artifact_invalid', 'Store 制品不是 JSON')
    }
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new P2S1W5ContractError('artifact_invalid', 'Store 制品结构无效')
    }
    const record = raw as unknown as P2S1W5StoredArtifact
    if (
      record.kind !== kind
      || record.record_key !== recordKey
      || record.shared_answer_binding_digest !== bindingDigest
      || record.store_contract_id !== STORE_CONTRACT_BY_KIND[kind]
      || record.attestation_provider_id !== 'country_outage_p2_s1_w5_host'
      || record.payload_digest !== p2S1W5Digest(record.payload)
      || record.receipt_digest !== p2S1W5DigestWithout(
        record as unknown as Record<string, unknown>,
        'receipt_digest',
      )
    ) throw new P2S1W5ContractError('artifact_replay_denied', 'Store 制品绑定或摘要不一致')
    return p2S1W5DeepFreeze(p2S1W5Clone(record))
  }

  #path(kind: P2S1W5ArtifactKind, recordKey: string, bindingDigest: string): string {
    return join(this.#root, kind, `${p2S1W5Digest({ recordKey, bindingDigest })}.json`)
  }

  #readFile(path: string): string {
    try {
      const stat = lstatSync(path)
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 32 * 1024 * 1024) {
        throw new P2S1W5ContractError('artifact_invalid', 'Store 制品必须是有界普通文件')
      }
      return readFileSync(path, 'utf8')
    } catch (error) {
      if (error instanceof P2S1W5ContractError) throw error
      const code = (error as NodeJS.ErrnoException).code
      if (code === 'ENOENT') throw new P2S1W5ContractError('artifact_missing', 'Store 制品不存在')
      throw error
    }
  }
}
