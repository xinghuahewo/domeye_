import { createHash, randomUUID } from 'node:crypto'
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
import { basename, join, resolve } from 'node:path'

type JsonObject = Record<string, unknown>

const DIGEST = /^sha256:[a-f0-9]{64}$/
const MAX_RECEIPT_BYTES = 16 * 1024 * 1024
const RECEIPT_SCHEMA = 'country_outage_p2_s1_trusted_receipt_v1'

export type P2S1TrustedReceiptType =
  | 'source_materialization'
  | 'source_manifest'
  | 'registry_proposal_admission'
  | 'registry_activation'
  | 'tool_query'
  | 'operator_execution'
  | 'investigation_commit'

export interface P2S1PublicationIdentity {
  event_type: 'country_outage'
  incident_id: string
  publication_id: string
  revision: number
  cohort_id: string
  collector_id: 'rrc25'
  window_start_utc: string
  window_end_utc: string
  data_through_utc: string
}

export interface P2S1ReceiptSubject {
  kind: 'source_view' | 'registry_snapshot' | 'execution_unit' | 'investigation'
  id: string
  version: string
  content_digest: string
}

export interface P2S1TrustedReceipt {
  schema_version: typeof RECEIPT_SCHEMA
  receipt_type: P2S1TrustedReceiptType
  receipt_digest: string
  candidate_id: string
  publication_identity: P2S1PublicationIdentity
  subject: P2S1ReceiptSubject
  issued_at_utc: string
  payload_digest: string
  payload: JsonObject
}

export interface P2S1TrustedReceiptInput {
  receipt_type: P2S1TrustedReceiptType
  candidate_id: string
  publication_identity: P2S1PublicationIdentity
  subject: P2S1ReceiptSubject
  issued_at_utc: string
  payload: JsonObject
}

export interface P2S1ReceiptExpectation {
  receipt_type: P2S1TrustedReceiptType
  candidate_id: string
  publication_identity: P2S1PublicationIdentity
  subject: P2S1ReceiptSubject
}

export class P2S1TrustedReceiptError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message)
    this.name = 'P2S1TrustedReceiptError'
  }
}

function canonicalNumber(value: number): string {
  if (!Number.isFinite(value)) {
    throw new P2S1TrustedReceiptError('receipt_invalid', '回执包含非有限数字')
  }
  if (Object.is(value, -0) || value === 0) return '0'
  const sign = value < 0 ? '-' : ''
  const [coefficientPart = '0', exponentPart = '0'] = Math.abs(value).toString().toLowerCase().split('e')
  const explicitExponent = Number.parseInt(exponentPart, 10)
  const decimalAt = coefficientPart.indexOf('.')
  const fractionalLength = decimalAt === -1 ? 0 : coefficientPart.length - decimalAt - 1
  const leadingTrimmed = coefficientPart.replace('.', '').replace(/^0+/, '')
  const trailingCount = leadingTrimmed.length - leadingTrimmed.replace(/0+$/, '').length
  const digits = leadingTrimmed.replace(/0+$/, '')
  const scientificExponent = explicitExponent - fractionalLength + trailingCount + digits.length - 1
  const coefficient = digits.length === 1 ? digits : `${digits[0]}.${digits.slice(1)}`
  return `${sign}${coefficient}e${scientificExponent}`
}

export function p2S1CanonicalJson(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return canonicalNumber(value)
  if (typeof value === 'string') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(p2S1CanonicalJson).join(',')}]`
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as JsonObject)
      .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${p2S1CanonicalJson(item)}`).join(',')}}`
  }
  throw new P2S1TrustedReceiptError('receipt_invalid', '回执包含非 JSON 类型')
}

export function p2S1Digest(value: unknown): string {
  return `sha256:${createHash('sha256')
    .update(p2S1CanonicalJson(value))
    .digest('hex')}`
}

function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value)
    for (const item of Object.values(value as Record<string, unknown>)) deepFreeze(item)
  }
  return value
}

function jsonObject(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new P2S1TrustedReceiptError('receipt_invalid', `${label} 必须是对象`)
  }
  return value as JsonObject
}

function assertExactKeys(value: JsonObject, keys: readonly string[], label: string): void {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new P2S1TrustedReceiptError('receipt_invalid', `${label} 字段集合不符合冻结合同`)
  }
}

function assertDigest(value: unknown, label: string): asserts value is string {
  if (typeof value !== 'string' || !DIGEST.test(value)) {
    throw new P2S1TrustedReceiptError('receipt_invalid', `${label} 不是规范 SHA-256 摘要`)
  }
}

function assertNonempty(value: unknown, label: string): asserts value is string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new P2S1TrustedReceiptError('receipt_invalid', `${label} 必须是非空字符串`)
  }
}

function validatePublicationIdentity(value: unknown): P2S1PublicationIdentity {
  const identity = jsonObject(value, 'publication_identity')
  assertExactKeys(identity, [
    'event_type', 'incident_id', 'publication_id', 'revision', 'cohort_id',
    'collector_id', 'window_start_utc', 'window_end_utc', 'data_through_utc',
  ], 'publication_identity')
  if (
    identity.event_type !== 'country_outage'
    || identity.collector_id !== 'rrc25'
    || !Number.isSafeInteger(identity.revision)
    || (identity.revision as number) < 1
  ) {
    throw new P2S1TrustedReceiptError('receipt_boundary_violation', '回执越过 country_outage/RRC25 单 publication 边界')
  }
  for (const field of [
    'incident_id', 'publication_id', 'cohort_id', 'window_start_utc',
    'window_end_utc', 'data_through_utc',
  ] as const) assertNonempty(identity[field], `publication_identity.${field}`)
  return identity as unknown as P2S1PublicationIdentity
}

function validateSubject(value: unknown): P2S1ReceiptSubject {
  const subject = jsonObject(value, 'subject')
  assertExactKeys(subject, ['kind', 'id', 'version', 'content_digest'], 'subject')
  if (!['source_view', 'registry_snapshot', 'execution_unit', 'investigation'].includes(String(subject.kind))) {
    throw new P2S1TrustedReceiptError('receipt_invalid', 'subject.kind 无效')
  }
  assertNonempty(subject.id, 'subject.id')
  assertNonempty(subject.version, 'subject.version')
  assertDigest(subject.content_digest, 'subject.content_digest')
  return subject as unknown as P2S1ReceiptSubject
}

function receiptDigestInput(receipt: Omit<P2S1TrustedReceipt, 'receipt_digest'>): JsonObject {
  return structuredClone(receipt) as unknown as JsonObject
}

export function validateP2S1TrustedReceipt(value: unknown): P2S1TrustedReceipt {
  const receipt = jsonObject(value, 'receipt')
  assertExactKeys(receipt, [
    'schema_version', 'receipt_type', 'receipt_digest', 'candidate_id',
    'publication_identity', 'subject', 'issued_at_utc', 'payload_digest', 'payload',
  ], 'receipt')
  if (receipt.schema_version !== RECEIPT_SCHEMA) {
    throw new P2S1TrustedReceiptError('receipt_invalid', '回执 schema_version 无效')
  }
  if (![
    'source_materialization', 'source_manifest', 'registry_proposal_admission',
    'registry_activation', 'tool_query', 'operator_execution', 'investigation_commit',
  ].includes(String(receipt.receipt_type))) {
    throw new P2S1TrustedReceiptError('receipt_invalid', 'receipt_type 无效')
  }
  assertDigest(receipt.receipt_digest, 'receipt_digest')
  assertDigest(receipt.payload_digest, 'payload_digest')
  assertNonempty(receipt.candidate_id, 'candidate_id')
  assertNonempty(receipt.issued_at_utc, 'issued_at_utc')
  const publicationIdentity = validatePublicationIdentity(receipt.publication_identity)
  const subject = validateSubject(receipt.subject)
  const payload = jsonObject(receipt.payload, 'payload')
  if (receipt.payload_digest !== p2S1Digest(payload)) {
    throw new P2S1TrustedReceiptError('receipt_payload_digest_mismatch', '回执 payload 摘要不一致')
  }
  const withoutDigest = structuredClone(receipt) as JsonObject
  delete withoutDigest.receipt_digest
  if (receipt.receipt_digest !== p2S1Digest(withoutDigest)) {
    throw new P2S1TrustedReceiptError('receipt_digest_mismatch', '回执内容寻址摘要不一致')
  }
  return deepFreeze({
    ...(structuredClone(receipt) as unknown as P2S1TrustedReceipt),
    publication_identity: publicationIdentity,
    subject,
  })
}

export function createP2S1TrustedReceipt(input: P2S1TrustedReceiptInput): P2S1TrustedReceipt {
  const payload = structuredClone(input.payload)
  const withoutDigest: Omit<P2S1TrustedReceipt, 'receipt_digest'> = {
    schema_version: RECEIPT_SCHEMA,
    receipt_type: input.receipt_type,
    candidate_id: input.candidate_id,
    publication_identity: structuredClone(input.publication_identity),
    subject: structuredClone(input.subject),
    issued_at_utc: input.issued_at_utc,
    payload_digest: p2S1Digest(payload),
    payload,
  }
  return validateP2S1TrustedReceipt({
    ...withoutDigest,
    receipt_digest: p2S1Digest(receiptDigestInput(withoutDigest)),
  })
}

function sameValue(left: unknown, right: unknown): boolean {
  return p2S1CanonicalJson(left) === p2S1CanonicalJson(right)
}

function validateExpectation(
  receipt: P2S1TrustedReceipt,
  expected: P2S1ReceiptExpectation,
): void {
  if (receipt.receipt_type !== expected.receipt_type) {
    throw new P2S1TrustedReceiptError('receipt_type_replay_denied', '回执类型与消费上下文不一致')
  }
  if (receipt.candidate_id !== expected.candidate_id) {
    throw new P2S1TrustedReceiptError('receipt_candidate_replay_denied', '回执候选与消费上下文不一致')
  }
  if (!sameValue(receipt.publication_identity, expected.publication_identity)) {
    throw new P2S1TrustedReceiptError('receipt_publication_replay_denied', '回执 publication 与消费上下文不一致')
  }
  if (!sameValue(receipt.subject, expected.subject)) {
    throw new P2S1TrustedReceiptError('receipt_subject_replay_denied', '回执 subject 与消费上下文不一致')
  }
}

export class P2S1TrustedReceiptStore {
  readonly #root: string

  constructor(root: string) {
    this.#root = resolve(root)
    mkdirSync(this.#root, { recursive: true, mode: 0o700 })
    const stat = lstatSync(this.#root)
    const processUid = typeof process.getuid === 'function' ? process.getuid() : undefined
    if (
      !stat.isDirectory()
      || stat.isSymbolicLink()
      || (stat.mode & 0o022) !== 0
      || (processUid !== undefined && stat.uid !== processUid)
    ) {
      throw new P2S1TrustedReceiptError(
        'receipt_store_unsafe',
        '可信回执根目录必须是当前进程持有、最终根无符号链接且禁止组/其他用户写入的实体目录',
      )
    }
    // macOS 的 /var 是系统级符号链接；这里只拒绝调用方给出的最终根本身，
    // 后续所有对象都直接落在该已验证实体目录，且文件级读取继续拒绝 symlink。
    realpathSync(this.#root)
  }

  put(receiptValue: unknown): P2S1TrustedReceipt {
    const receipt = validateP2S1TrustedReceipt(receiptValue)
    const target = this.pathFor(receipt.receipt_digest)
    const serialized = `${p2S1CanonicalJson(receipt)}\n`
    try {
      const existing = this.readFile(target)
      if (existing !== serialized) {
        throw new P2S1TrustedReceiptError('receipt_store_collision', '同摘要目标已存在不同内容')
      }
      return receipt
    } catch (error) {
      if (error instanceof P2S1TrustedReceiptError && error.code !== 'receipt_missing') throw error
    }

    const temporary = join(this.#root, `.${basename(target)}.${process.pid}.${randomUUID()}.tmp`)
    let descriptor: number | undefined
    try {
      descriptor = openSync(temporary, 'wx', 0o600)
      writeSync(descriptor, serialized, undefined, 'utf8')
      fsyncSync(descriptor)
      closeSync(descriptor)
      descriptor = undefined
      // Hard-link publication is atomic and refuses EEXIST. Unlike rename, it
      // cannot overwrite an immutable target during a concurrent writer race.
      linkSync(temporary, target)
      unlinkSync(temporary)
      const directory = openSync(this.#root, 'r')
      try { fsyncSync(directory) } finally { closeSync(directory) }
    } catch (error) {
      if (descriptor !== undefined) closeSync(descriptor)
      try { unlinkSync(temporary) } catch { /* best-effort temporary cleanup */ }
      throw error
    }
    const stored = this.read(receipt.receipt_digest, {
      receipt_type: receipt.receipt_type,
      candidate_id: receipt.candidate_id,
      publication_identity: receipt.publication_identity,
      subject: receipt.subject,
    })
    return stored
  }

  read(digest: string, expected: P2S1ReceiptExpectation): P2S1TrustedReceipt {
    const target = this.pathFor(digest)
    const raw = this.readFile(target)
    let parsed: unknown
    try { parsed = JSON.parse(raw) } catch {
      throw new P2S1TrustedReceiptError('receipt_invalid', '可信回执文件不是合法 JSON')
    }
    const receipt = validateP2S1TrustedReceipt(parsed)
    if (`${p2S1CanonicalJson(receipt)}\n` !== raw) {
      throw new P2S1TrustedReceiptError('receipt_not_canonical', '可信回执文件不是冻结 canonical JSON')
    }
    if (receipt.receipt_digest !== digest) {
      throw new P2S1TrustedReceiptError('receipt_digest_mismatch', '文件名摘要与回执摘要不一致')
    }
    validateExpectation(receipt, expected)
    return receipt
  }

  private pathFor(digest: string): string {
    if (!DIGEST.test(digest)) {
      throw new P2S1TrustedReceiptError('receipt_digest_invalid', '请求的回执摘要无效')
    }
    return join(this.#root, `${digest.slice('sha256:'.length)}.json`)
  }

  private readFile(path: string): string {
    let stat
    try { stat = lstatSync(path) } catch {
      throw new P2S1TrustedReceiptError('receipt_missing', '可信回执不存在（ghost receipt）')
    }
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_RECEIPT_BYTES) {
      throw new P2S1TrustedReceiptError('receipt_store_unsafe', '可信回执必须是大小受限的实体文件')
    }
    return readFileSync(path, 'utf8')
  }
}
