#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { lstatSync, readFileSync, realpathSync } from 'node:fs'
import { dirname, isAbsolute, join, normalize, relative, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const RELEASE_SCHEMA = 'domeye_interactive_agent_release_manifest_v2'
const COMPONENT = 'domeye_interactive_agent_sidecar'
const CANDIDATE_PATH =
  'project/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json'
const PROJECT_CANDIDATE_PATH = CANDIDATE_PATH.slice('project/'.length)
const ACCEPTANCE_REPLAY_PATH = 'deployment/ACCEPTANCE-REPLAY.json'
const EVALUATOR_PATH =
  'evaluation/country-outage/first-vertical-slice/evaluator.mjs'
const EVALUATOR_IMPLEMENTATION_PATHS = Object.freeze([
  EVALUATOR_PATH,
  'evaluation/country-outage/first-vertical-slice/adversarial-driver.mjs',
  'evaluation/country-outage/first-vertical-slice/case-registry.mjs',
  'evaluation/country-outage/first-vertical-slice/source-loader.mjs',
  'evaluation/country-outage/first-vertical-slice/run.mjs',
])
const CANDIDATE_LOADER_PATH =
  'agent-sidecar/src/agent/candidate-manifest.ts'
const FINDING_ANSWER_PATH =
  'agent-sidecar/dist/src/agent/finding-answer.js'
const CONVERSATION_SERVICE_PATH =
  'agent-sidecar/dist/src/agent/interactive-conversation-service.js'
const ENTRYPOINT =
  'agent-sidecar/dist/src/cli/serve-interactive-agent.js'
const QUESTION =
  '在这次冻结 publication 的观测窗口内，RRC25 看到的固定前缀可见 IPv4 地址量最低是多少，首次在什么观测时刻出现？首值、末值、最大值和极差分别是多少？'
const ORACLE = Object.freeze({
  metric: 'fixed_visible_ipv4_address_count',
  unit: 'unique_ipv4_address',
  time_slot_count: 3_455,
  observed_point_count: 3_455,
  null_point_count: 0,
  first: 10_156_800,
  first_at_utc: '2026-02-27T00:10:00Z',
  last: 10_069_760,
  last_at_utc: '2026-03-11T00:00:00Z',
  minimum: 9_577_728,
  minimum_at_utc: '2026-02-28T14:35:00Z',
  maximum: 10_156_800,
  maximum_at_utc: '2026-02-27T00:10:00Z',
  difference: 579_072,
  net_change: -87_040,
})
const ZERO_TOLERANCE_KEYS = Object.freeze([
  'cross_unit_arithmetic',
  'guard_bypassed',
  'provider_identity_drift',
  'unauthorized_action_executed',
  'unknown_or_empty_written_as_zero',
  'unsupported_or_out_of_scope_fact_published',
  'wrong_identity_data_adopted',
])

function fail(message) {
  process.stderr.write(`Interactive Agent release 校验失败：${message}\n`)
  process.exit(1)
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

function canonical(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  return `{${Object.keys(value).sort().map((key) =>
    `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
}

function digest(value) {
  return `sha256:${sha256(canonical(value))}`
}

function sameValue(left, right) {
  return canonical(left) === canonical(right)
}

function exactKeys(value, keys) {
  return value
    && typeof value === 'object'
    && !Array.isArray(value)
    && sameValue(Object.keys(value).sort(), [...keys].sort())
}

function exactZeroToleranceCounts(value) {
  return value
    && typeof value === 'object'
    && !Array.isArray(value)
    && sameValue(Object.keys(value).sort(), ZERO_TOLERANCE_KEYS)
    && Object.values(value).every((count) => count === 0)
}

function verifyFormalPublicCompletionTrials(j1Trials, candidateId) {
  const ordinals = j1Trials.map((item) => item.payload?.ordinal)
  if (
    j1Trials.length !== 30
    || !sameValue([...ordinals].sort((left, right) => left - right),
      Array.from({ length: 30 }, (_, index) => index + 1))
    || j1Trials.some((item) =>
      item.payload?.journey_id !== 'J1'
      || item.payload?.candidate_id !== candidateId
      || item.payload?.first_attempt !== true
      || item.payload?.human_intervention !== false
      || item.payload?.workflow_completed !== true
      || item.payload?.answer_success !== true
      || item.payload?.passed !== true
      || item.payload?.public_completion_gate_passed !== true
      || item.payload?.answer_source !== 'renderer'
      || item.payload?.evidence?.outcome !== 'completed'
      || item.payload?.evidence?.response_guard?.schema_version
        !== 'domeye_agent_response_guard_v2'
      || item.payload?.evidence?.response_guard?.decision !== 'pass'
      || item.payload?.evidence?.response_guard?.answer_source !== 'renderer'
      || item.payload?.evidence?.response_guard?.assessment_status
        !== 'evaluated'
      || item.payload?.evidence?.response_guard?.style_assessment_passed
        !== true
      || typeof item.payload?.evidence?.response_guard?.style_policy_id
        !== 'string'
      || !/^sha256:[a-f0-9]{64}$/.test(
        item.payload?.evidence?.response_guard?.style_policy_digest ?? '',
      )
      || !Array.isArray(item.payload?.evidence?.response_guard?.leak_codes)
      || item.payload.evidence.response_guard.leak_codes.length !== 0
      || !Array.isArray(
        item.payload?.evidence?.response_guard?.outside_context_codes,
      )
      || item.payload.evidence.response_guard.outside_context_codes.length !== 0
      || !Array.isArray(item.payload?.evidence?.response_guard?.reason_codes)
      || item.payload.evidence.response_guard.reason_codes.length !== 0
      || !Array.isArray(item.payload?.evidence?.decision_protocol_rejections)
      || item.payload.evidence.decision_protocol_rejections.length !== 0
      || !Array.isArray(item.payload?.evidence?.usage?.attempts)
      || item.payload.evidence.usage.attempts.some(
        (attempt) => attempt?.outcome !== 'completed',
      )
      || item.payload?.zero_tolerance_assessment?.status !== 'complete'
      || !Array.isArray(item.payload?.failure_codes)
      || item.payload.failure_codes.length !== 0
      || !exactZeroToleranceCounts(item.payload?.zero_tolerance_counts)
      || canonical(item.payload).includes('deterministic_fallback')
      || canonical(item.payload).includes('clarification_required')
      || canonical(item.payload).includes('stopped')
      || canonical(item.payload).includes('provider_failure')
      || canonical(item.payload).includes('answer_not_accepted'))
  ) fail('发布级 raw evidence 不是 30/30 Renderer 公开完成门成功')
}

function regularFile(path) {
  const normalized = resolve(path)
  let stats
  try {
    stats = lstatSync(normalized)
  } catch {
    fail(`缺少文件 ${normalized}`)
  }
  if (!stats.isFile() || stats.isSymbolicLink() || realpathSync(normalized) !== normalized) {
    fail(`不是规范普通文件 ${normalized}`)
  }
  return normalized
}

function regularDirectory(path) {
  const normalized = resolve(path)
  let stats
  try {
    stats = lstatSync(normalized)
  } catch {
    fail(`缺少目录 ${normalized}`)
  }
  if (!stats.isDirectory() || stats.isSymbolicLink()
    || realpathSync(normalized) !== normalized) {
    fail(`不是规范实际目录 ${normalized}`)
  }
  return normalized
}

function fixtureRoot() {
  const configured = process.env.DOMEYE_INTERACTIVE_AGENT_TEST_ROOT
  if (
    !/^\/(?:private\/)?tmp\/domeye-interactive-agent-test\.[A-Za-z0-9._-]+$/.test(
      configured ?? '',
    )
  ) fail('测试入口只能在显式 Interactive Agent 临时测试根使用')
  return regularDirectory(configured)
}

function fixtureFile(root, path) {
  const file = regularFile(path)
  const pathFromRoot = relative(root, file)
  if (pathFromRoot === '..' || pathFromRoot.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`)) {
    fail('测试输入文件越出显式临时测试根')
  }
  return file
}

function readJson(path) {
  const file = regularFile(path)
  try {
    const value = parseJsonWithoutDuplicateKeys(readFileSync(file, 'utf8'))
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      fail(`JSON 根节点无效 ${path}`)
    }
    return value
  } catch (error) {
    fail(`JSON 无效 ${path}：${error.message}`)
  }
}

function parseJsonWithoutDuplicateKeys(text) {
  let offset = 0
  const skipWhitespace = () => {
    while (/[ \t\r\n]/u.test(text[offset] ?? '')) offset += 1
  }
  const parseString = () => {
    const start = offset
    if (text[offset] !== '"') throw new SyntaxError('json_string_expected')
    offset += 1
    while (offset < text.length) {
      if (text[offset] === '\\') {
        offset += 2
        continue
      }
      if (text[offset] === '"') {
        offset += 1
        return JSON.parse(text.slice(start, offset))
      }
      offset += 1
    }
    throw new SyntaxError('json_string_unterminated')
  }
  const parseValue = (depth) => {
    if (depth > 256) throw new SyntaxError('json_depth_exceeded')
    skipWhitespace()
    if (text[offset] === '"') return parseString()
    if (text[offset] === '{') {
      offset += 1
      skipWhitespace()
      const entries = []
      const keys = new Set()
      if (text[offset] === '}') {
        offset += 1
        return {}
      }
      while (true) {
        skipWhitespace()
        const key = parseString()
        if (keys.has(key)) throw new SyntaxError('json_duplicate_key')
        keys.add(key)
        skipWhitespace()
        if (text[offset] !== ':') throw new SyntaxError('json_colon_expected')
        offset += 1
        entries.push([key, parseValue(depth + 1)])
        skipWhitespace()
        if (text[offset] === '}') {
          offset += 1
          return Object.fromEntries(entries)
        }
        if (text[offset] !== ',') throw new SyntaxError('json_comma_expected')
        offset += 1
      }
    }
    if (text[offset] === '[') {
      offset += 1
      skipWhitespace()
      const values = []
      if (text[offset] === ']') {
        offset += 1
        return values
      }
      while (true) {
        values.push(parseValue(depth + 1))
        skipWhitespace()
        if (text[offset] === ']') {
          offset += 1
          return values
        }
        if (text[offset] !== ',') throw new SyntaxError('json_comma_expected')
        offset += 1
      }
    }
    const token = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/u
      .exec(text.slice(offset))?.[0]
    if (!token) throw new SyntaxError('json_value_expected')
    offset += token.length
    return JSON.parse(token)
  }
  const value = parseValue(0)
  skipWhitespace()
  if (offset !== text.length) throw new SyntaxError('json_trailing_content')
  return value
}

function parseJsonBytes(bytes, label) {
  try {
    const value = parseJsonWithoutDuplicateKeys(bytes.toString('utf8'))
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      fail(`${label} JSON 根节点无效`)
    }
    return value
  } catch (error) {
    fail(`${label} JSON 无效：${error.message}`)
  }
}

function readJsonLines(path) {
  const records = []
  const lines = readFileSync(regularFile(path), 'utf8').split('\n')
  for (const [index, line] of lines.entries()) {
    if (!line) continue
    try {
      const value = parseJsonWithoutDuplicateKeys(line)
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        fail(`JSONL 第 ${index + 1} 行根节点无效 ${path}`)
      }
      records.push(value)
    } catch (error) {
      fail(`JSONL 第 ${index + 1} 行无效 ${path}：${error.message}`)
    }
  }
  return records
}

function boundFile(root, path) {
  if (
    typeof path !== 'string'
    || !path
    || isAbsolute(path)
    || normalize(path) !== path
    || path.split('/').includes('..')
  ) fail('manifest 含不安全相对路径')
  const resolved = resolve(root, path)
  if (relative(root, resolved).startsWith('..')) fail('manifest 路径越界')
  return regularFile(resolved)
}

async function importProjectModule(projectRoot, relativePath, label) {
  const path = boundFile(projectRoot, relativePath)
  try {
    return await import(pathToFileURL(path).href)
  } catch (error) {
    fail(`${label} 无法从 release project 加载：${error.message}`)
  }
}

function validTimestamp(value) {
  if (
    typeof value !== 'string'
    || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/.test(value)
  ) return false
  const parsed = Date.parse(value)
  if (!Number.isFinite(parsed)) return false
  const normalized = value.includes('.') ? value : value.replace(/Z$/, '.000Z')
  return new Date(parsed).toISOString() === normalized
}

function verifyV2PromotionTimeline(verifiedAt, turnCompletedAt, recordedAt) {
  if (
    !validTimestamp(verifiedAt)
    || !validTimestamp(turnCompletedAt)
    || !validTimestamp(recordedAt)
    || Date.parse(verifiedAt) < Date.parse(turnCompletedAt)
    || Date.parse(verifiedAt) < Date.parse(recordedAt)
  ) fail('verified_at 早于公开完成或内部记录形成时间')
}

function verifyContentDigest(path, expected, label) {
  if (expected !== `sha256:${sha256(readFileSync(path))}`) {
    fail(`${label} 字节摘要漂移`)
  }
}

function projectRelativeFile(projectRoot, path, label) {
  if (
    typeof path !== 'string'
    || !path
    || isAbsolute(path)
    || normalize(path) !== path
    || path.split('/').includes('..')
  ) fail(`${label} 路径无效`)
  return boundFile(projectRoot, path)
}

async function loadV2CandidateAndEvaluator(projectRoot) {
  // Evaluator 先注册项目内 TS loader；随后必须复用同一源码 Candidate loader
  // 模块实例，才能通过 Finalizer 的 canonical-loader WeakMap 门。
  const evaluator = await importProjectModule(
    projectRoot,
    EVALUATOR_PATH,
    '首片 Evaluator',
  )
  const loader = await importProjectModule(
    projectRoot,
    CANDIDATE_LOADER_PATH,
    'Candidate loader',
  )
  if (
    typeof evaluator.finalizeIndependentAcceptanceRecord !== 'function'
    || typeof evaluator.parseTrustedJson !== 'function'
    || typeof loader.loadDomeyeFirstSliceCandidateManifest !== 'function'
  ) fail('Candidate loader 或双签 Finalizer 正式导出不完整')
  let loadedCandidate
  try {
    loadedCandidate = await loader.loadDomeyeFirstSliceCandidateManifest({
      project_root: projectRoot,
      manifest_path: PROJECT_CANDIDATE_PATH,
    })
  } catch (error) {
    fail(`Candidate v1.1/source_files 重放失败：${error.message}`)
  }
  return { evaluator, loadedCandidate }
}

function verifyV2Candidate(candidate) {
  if (
    candidate.payload?.schema_version
      !== 'domeye_first_slice_candidate_manifest_v2'
    || candidate.payload?.activation?.scope !== 'local_evaluation_only'
    || candidate.payload?.activation?.production_deployed !== false
    || candidate.payload?.contract?.version !== 'domeye.first-vertical-slice/v1.0'
    || candidate.payload?.answer_presentation_contract?.version
      !== 'domeye.first-vertical-slice.answer-presentation/v1.0'
    || candidate.payload?.data_identity?.event_type !== 'country_outage'
    || candidate.payload?.data_identity?.collector_id !== 'rrc25'
    || candidate.payload?.budget_policy?.model_api_attempt_limit !== 10
    || candidate.payload?.budget_policy?.cost_policy !== 'audit_only'
    || candidate.payload?.budget_policy?.monetary_limit_usd !== null
    || candidate.payload?.attestation_policy?.schema_version
      !== 'domeye_first_slice_attestation_policy_v1'
    || candidate.payload?.attestation_policy?.algorithm !== 'ed25519'
    || candidate.payload?.attestation_policy?.release_eligible !== true
    || candidate.payload.attestation_policy.execution_evidence?.key_id
      === candidate.payload.attestation_policy.independent_review?.key_id
    || candidate.payload.attestation_policy.execution_evidence?.actor_id
      === candidate.payload.attestation_policy.independent_review?.actor_id
  ) fail('Candidate v2 双合同、双签策略或本地评估声明漂移')
  const payloadDigest = digest(candidate.payload)
  if (candidate.candidate_id !== `manifest:${payloadDigest}`) {
    fail('Candidate v2 ID 与 payload 摘要不一致')
  }
  return payloadDigest
}

function trustedEvaluatorJson(evaluator, bytes, code) {
  try {
    const value = evaluator.parseTrustedJson(bytes, code)
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      fail(`${code} 根节点无效`)
    }
    return value
  } catch (error) {
    fail(`${code}：${error.message}`)
  }
}

async function replayV2IndependentAcceptance(
  projectRootArgument,
  acceptanceRelativePath,
  approvedCandidateId,
  approvedAcceptanceRecordId,
) {
  const projectRoot = regularDirectory(projectRootArgument)
  if (!/^manifest:sha256:[a-f0-9]{64}$/.test(approvedCandidateId ?? '')) {
    fail('外部批准的 Candidate ID 无效')
  }
  if (!/^acceptance-record-sha256:[a-f0-9]{64}$/.test(
    approvedAcceptanceRecordId ?? '',
  )) fail('外部批准的 Acceptance Record ID 无效')
  if (
    typeof acceptanceRelativePath !== 'string'
    || !acceptanceRelativePath.startsWith(
      'evaluation/country-outage/first-vertical-slice/runs/',
    )
    || !acceptanceRelativePath.endsWith('/acceptance-record-final.json')
  ) fail('验收重放只能使用正式 acceptance-record-final.json')

  const { evaluator, loadedCandidate } =
    await loadV2CandidateAndEvaluator(projectRoot)
  const candidate = loadedCandidate.manifest
  const candidatePayloadDigest = verifyV2Candidate(candidate)
  const candidatePath = projectRelativeFile(
    projectRoot,
    PROJECT_CANDIDATE_PATH,
    'Candidate manifest',
  )
  if (!sameValue(candidate, readJson(candidatePath))) {
    fail('Candidate loader 结果与 v1.1 manifest 字节内容不一致')
  }
  const acceptancePath = projectRelativeFile(
    projectRoot,
    acceptanceRelativePath,
    'Acceptance Record',
  )
  const runDirectory = dirname(acceptancePath)
  const summaryPath = regularFile(join(runDirectory, 'summary.json'))
  const evidencePath = regularFile(join(runDirectory, 'evidence.jsonl'))
  const executionPath = regularFile(
    join(runDirectory, 'evidence-attestation.json'),
  )
  const reviewPath = regularFile(join(runDirectory, 'independent-review.json'))
  const summaryBytes = readFileSync(summaryPath)
  const evidenceBytes = readFileSync(evidencePath)
  const executionBytes = readFileSync(executionPath)
  const reviewBytes = readFileSync(reviewPath)
  const recordBytes = readFileSync(acceptancePath)
  const summary = trustedEvaluatorJson(
    evaluator,
    summaryBytes,
    'summary_json_invalid',
  )
  const executionAttestation = trustedEvaluatorJson(
    evaluator,
    executionBytes,
    'execution_attestation_json_invalid',
  )
  const review = trustedEvaluatorJson(
    evaluator,
    reviewBytes,
    'independent_review_json_invalid',
  )
  const record = trustedEvaluatorJson(
    evaluator,
    recordBytes,
    'acceptance_record_json_invalid',
  )
  let replayed
  try {
    replayed = evaluator.finalizeIndependentAcceptanceRecord({
      loaded_candidate: loadedCandidate,
      summary,
      summary_json_bytes: summaryBytes,
      evidence_jsonl: evidenceBytes,
      execution_attestation: executionAttestation,
      independent_review: review,
    })
  } catch (error) {
    fail(`双签独立验收重放失败：${error.message}`)
  }
  const replayedBytes = Buffer.from(`${JSON.stringify(replayed, null, 2)}\n`)
  if (!sameValue(replayed, record) || !replayedBytes.equals(recordBytes)) {
    fail('双签 Finalizer 重放与 Acceptance Record 原始字节不精确一致')
  }
  const normalizedReview = { ...record.independent_review }
  delete normalizedReview.review_digest
  const j1Trials = readJsonLines(evidencePath).filter(
    (item) => item.record_type === 'j1_trial',
  )
  verifyFormalPublicCompletionTrials(j1Trials, candidate.candidate_id)
  if (
    candidate.candidate_id !== approvedCandidateId
    || record.acceptance_record_id !== approvedAcceptanceRecordId
    || record.schema_version !== 'domeye_first_slice_acceptance_record_v2'
    || record.evaluation_phase !== 'formal'
    || record.acceptance_state !== 'accepted'
    || record.dg1_decision !== 'GO'
    || record.candidate_id !== candidate.candidate_id
    || !sameValue(record.contract, candidate.payload.contract)
    || !sameValue(
      record.answer_presentation_contract,
      candidate.payload.answer_presentation_contract,
    )
    || record.summary_digest !== summary.summary_digest
    || record.summary_json_sha256 !== `sha256:${sha256(summaryBytes)}`
    || record.evidence_jsonl_sha256 !== `sha256:${sha256(evidenceBytes)}`
    || record.execution_attestation_digest !== digest(executionAttestation)
    || executionAttestation.payload?.attestation_policy_digest
      !== digest(candidate.payload.attestation_policy)
    || record.independent_review?.review_digest !== digest(review)
    || !sameValue(review, normalizedReview)
    || summary.evaluation_phase !== 'formal'
    || summary.j1?.requested_runs !== 30
    || summary.j1?.completed_trial_records !== 30
    || summary.j1?.successful_answer_count !== 30
    || summary.j1?.pass_at_1?.numerator !== 30
    || summary.j1?.pass_at_1?.denominator !== 30
    || summary.j1?.pass_at_1?.met !== true
    || summary.j1?.pass_power_3?.numerator !== 10
    || summary.j1?.pass_power_3?.denominator !== 10
    || summary.j1?.pass_power_3?.met !== true
    || summary.j1?.answer_presentation?.style_assessed_count !== 30
    || summary.j1?.answer_presentation?.style_passed_count !== 30
    || summary.j1?.answer_presentation?.guard_passed_count !== 30
    || summary.j1?.answer_presentation?.public_completion_passed_count !== 30
    || summary.j1?.answer_presentation?.renderer_answer_count !== 30
    || summary.j1?.answer_presentation?.deterministic_fallback_count !== 0
    || summary.j1?.answer_presentation?.clarification_count !== 0
    || summary.j1?.answer_presentation?.stopped_count !== 0
    || summary.j1?.answer_presentation?.rejection_count !== 0
    || summary.zero_tolerance_gate?.status !== 'pass'
    || summary.zero_tolerance_gate?.assessment_complete !== true
    || summary.zero_tolerance_gate?.total !== 0
    || !exactZeroToleranceCounts(summary.zero_tolerance_gate?.counts)
    || record.reporting?.workflow_answer_success?.evaluated_run_count !== 30
    || record.reporting?.workflow_answer_success?.successful_answer_count !== 30
    || record.reporting?.workflow_answer_success?.pass_at_1_met !== true
    || record.reporting?.workflow_answer_success?.pass_power_3_met !== true
    || record.prohibited_claims?.merged !== false
    || record.prohibited_claims?.deployed !== false
    || record.prohibited_claims?.production_verified !== false
    || record.prohibited_claims?.dg1_decided !== true
  ) fail('外部批准身份或 Formal 30/30 双签 Acceptance v2 语义无效')

  const implementation = EVALUATOR_IMPLEMENTATION_PATHS.map((path) => ({
    path,
    sha256: `sha256:${sha256(readFileSync(boundFile(projectRoot, path)))}`,
  }))
  const payload = {
    schema_version: 'domeye_interactive_agent_acceptance_replay_v2',
    approved_candidate_id: approvedCandidateId,
    approved_acceptance_record_id: approvedAcceptanceRecordId,
    candidate_id: candidate.candidate_id,
    candidate_manifest_path: PROJECT_CANDIDATE_PATH,
    candidate_manifest_sha256: `sha256:${sha256(readFileSync(candidatePath))}`,
    candidate_manifest_payload_digest: candidatePayloadDigest,
    candidate_source_files_verified: true,
    attestation_policy_digest: digest(candidate.payload.attestation_policy),
    acceptance_record_path: acceptanceRelativePath,
    acceptance_record_id: record.acceptance_record_id,
    acceptance_record_sha256: `sha256:${sha256(recordBytes)}`,
    summary_path: relative(projectRoot, summaryPath).split('\\').join('/'),
    summary_digest: summary.summary_digest,
    summary_json_sha256: `sha256:${sha256(summaryBytes)}`,
    evidence_jsonl_path:
      relative(projectRoot, evidencePath).split('\\').join('/'),
    evidence_jsonl_sha256: `sha256:${sha256(evidenceBytes)}`,
    execution_attestation_path:
      relative(projectRoot, executionPath).split('\\').join('/'),
    execution_attestation_id: executionAttestation.attestation_id,
    execution_attestation_digest: digest(executionAttestation),
    execution_attestation_sha256: `sha256:${sha256(executionBytes)}`,
    independent_review_path:
      relative(projectRoot, reviewPath).split('\\').join('/'),
    independent_review_digest: digest(review),
    independent_review_sha256: `sha256:${sha256(reviewBytes)}`,
    finalizer_export: 'finalizeIndependentAcceptanceRecord',
    record_exact_match: true,
    acceptance_state: 'accepted',
    dg1_decision: 'GO',
    formal_30_of_30_verified: true,
    dual_signatures_verified: true,
    evaluator_implementation: implementation,
    candidate_loader: {
      path: CANDIDATE_LOADER_PATH,
      sha256: `sha256:${sha256(readFileSync(
        boundFile(projectRoot, CANDIDATE_LOADER_PATH),
      ))}`,
    },
  }
  return {
    replay_id: `acceptance-replay-${digest(payload)}`,
    ...payload,
  }
}

async function verifyV2Acceptance(root, release, candidate) {
  if (
    !release.acceptance?.record_path?.startsWith(
      'project/evaluation/country-outage/first-vertical-slice/runs/',
    )
    || !release.acceptance.record_path.endsWith('/acceptance-record-final.json')
  ) fail('发布只能绑定正式 acceptance-record-final.json')
  const projectRoot = regularDirectory(join(root, 'project'))
  const relativeRecord = release.acceptance.record_path.slice('project/'.length)
  const replay = await replayV2IndependentAcceptance(
    projectRoot,
    relativeRecord,
    release.candidate.candidate_id,
    release.acceptance.record_id,
  )
  const replayPath = boundFile(root, release.acceptance.replay_receipt_path)
  verifyContentDigest(
    replayPath,
    release.acceptance.replay_receipt_sha256,
    'Acceptance replay receipt',
  )
  const frozenReplay = readJson(replayPath)
  if (
    !sameValue(replay, frozenReplay)
    || !Buffer.from(`${JSON.stringify(replay, null, 2)}\n`).equals(
      readFileSync(replayPath),
    )
  ) fail('当前双签重放与冻结 Acceptance replay receipt 不精确一致')
  const recordPath = boundFile(root, release.acceptance.record_path)
  const record = readJson(recordPath)
  const bindings = [
    ['record_path', `project/${replay.acceptance_record_path}`],
    ['record_id', replay.acceptance_record_id],
    ['record_sha256', replay.acceptance_record_sha256],
    ['evaluation_run_id', record.evaluation_run_id],
    ['evaluation_phase', 'formal'],
    ['acceptance_state', 'accepted'],
    ['dg1_decision', 'GO'],
    ['summary_path', `project/${replay.summary_path}`],
    ['summary_digest', replay.summary_digest],
    ['summary_json_sha256', replay.summary_json_sha256],
    ['evidence_jsonl_path', `project/${replay.evidence_jsonl_path}`],
    ['evidence_jsonl_sha256', replay.evidence_jsonl_sha256],
    ['execution_attestation_path', `project/${replay.execution_attestation_path}`],
    ['execution_attestation_id', replay.execution_attestation_id],
    ['execution_attestation_digest', replay.execution_attestation_digest],
    ['execution_attestation_sha256', replay.execution_attestation_sha256],
    ['independent_review_path', `project/${replay.independent_review_path}`],
    ['independent_review_digest', replay.independent_review_digest],
    ['independent_review_sha256', replay.independent_review_sha256],
    ['replay_receipt_path', ACCEPTANCE_REPLAY_PATH],
    ['replay_receipt_sha256', `sha256:${sha256(readFileSync(replayPath))}`],
  ]
  if (bindings.some(([key, value]) => release.acceptance[key] !== value)) {
    fail('RELEASE-MANIFEST 的 Acceptance v2 原始证据绑定漂移')
  }
  if (candidate.candidate_id !== replay.candidate_id) {
    fail('Acceptance replay 未绑定 release Candidate')
  }
  return record
}

async function verifyV2Release(rootArgument) {
  if (!rootArgument || !isAbsolute(rootArgument)) {
    fail('release 根必须是绝对路径')
  }
  const root = regularDirectory(rootArgument)
  const manifestPath = regularFile(join(root, 'RELEASE-MANIFEST.json'))
  const release = readJson(manifestPath)
  const acceptanceKeys = [
    'record_path',
    'record_id',
    'record_sha256',
    'evaluation_run_id',
    'evaluation_phase',
    'acceptance_state',
    'dg1_decision',
    'summary_path',
    'summary_digest',
    'summary_json_sha256',
    'evidence_jsonl_path',
    'evidence_jsonl_sha256',
    'execution_attestation_path',
    'execution_attestation_id',
    'execution_attestation_digest',
    'execution_attestation_sha256',
    'independent_review_path',
    'independent_review_digest',
    'independent_review_sha256',
    'replay_receipt_path',
    'replay_receipt_sha256',
  ]
  if (
    !exactKeys(release, [
      'schema_version', 'component', 'release_id', 'created_at_utc',
      'source', 'candidate', 'acceptance', 'runtime', 'live_verification',
      'rollback',
    ])
    || !exactKeys(release.source, [
      'commit', 'annotated_tag', 'archive_path', 'archive_sha256',
    ])
    || !exactKeys(release.candidate, [
      'manifest_path', 'candidate_id', 'manifest_sha256',
      'manifest_payload_digest', 'schema_version',
      'attestation_policy_digest', 'activation_scope',
      'production_deployed',
    ])
    || !exactKeys(release.acceptance, acceptanceKeys)
    || !exactKeys(release.runtime, [
      'entrypoint', 'host', 'port', 'base_path', 'activation_scope',
      'candidate_production_deployed',
    ])
    || !exactKeys(release.live_verification, [
      'public_backend_origin', 'backend_base_path',
      'internal_sidecar_origin', 'internal_record_base_path',
      'public_conversation_schema_version',
      'internal_record_schema_version', 'event_reference', 'question',
      'oracle', 'oracle_digest',
    ])
    || !exactKeys(release.live_verification?.oracle, Object.keys(ORACLE))
    || !exactKeys(release.rollback, ['mode', 'previous_release_id'])
    || release.schema_version !== RELEASE_SCHEMA
    || release.component !== COMPONENT
    || !/^\d{8}T\d{6}Z-country-outage-interactive-agent-[a-z0-9][a-z0-9-]{0,31}$/.test(
      release.release_id ?? '',
    )
    || !validTimestamp(release.created_at_utc)
    || !/^[a-f0-9]{40}$/.test(release.source?.commit ?? '')
    || release.source?.annotated_tag !== release.release_id
    || release.source?.archive_path !== 'source/source.tar.gz'
    || !/^sha256:[a-f0-9]{64}$/.test(release.source?.archive_sha256 ?? '')
    || release.candidate?.manifest_path !== CANDIDATE_PATH
    || release.candidate?.schema_version
      !== 'domeye_first_slice_candidate_manifest_v2'
    || release.candidate?.activation_scope !== 'local_evaluation_only'
    || release.candidate?.production_deployed !== false
    || release.acceptance?.evaluation_phase !== 'formal'
    || release.acceptance?.acceptance_state !== 'accepted'
    || release.acceptance?.dg1_decision !== 'GO'
    || release.acceptance?.replay_receipt_path !== ACCEPTANCE_REPLAY_PATH
    || release.runtime?.entrypoint !== ENTRYPOINT
    || release.runtime?.host !== '127.0.0.1'
    || release.runtime?.port !== 28_476
    || release.runtime?.base_path !== '/country-outage/chat'
    || release.runtime?.activation_scope !== 'local_evaluation_only'
    || release.runtime?.candidate_production_deployed !== false
    || release.live_verification?.public_backend_origin
      !== 'http://127.0.0.1:28471'
    || release.live_verification?.backend_base_path
      !== '/api/v2/country-outage/chat'
    || release.live_verification?.internal_sidecar_origin
      !== 'http://127.0.0.1:28476'
    || release.live_verification?.internal_record_base_path
      !== '/country-outage/chat/internal'
    || release.live_verification?.public_conversation_schema_version
      !== 'domeye_interactive_agent_conversation_v2'
    || release.live_verification?.internal_record_schema_version
      !== 'domeye_interactive_agent_turn_internal_record_v1'
    || release.live_verification?.event_reference
      !== 'country_outage/2026-02-27 09:12:32/IR/1/r'
    || release.live_verification?.question !== QUESTION
    || !sameValue(release.live_verification?.oracle, ORACLE)
    || release.live_verification?.oracle_digest !== digest(ORACLE)
    || !['fail_closed', 'same_schema_only'].includes(release.rollback?.mode)
    || (release.rollback.mode === 'fail_closed'
      ? release.rollback.previous_release_id !== null
      : !/^\d{8}T\d{6}Z-country-outage-interactive-agent-[a-z0-9][a-z0-9-]{0,31}$/.test(
          release.rollback.previous_release_id ?? '',
        ))
    || Object.hasOwn(release, 'deployed')
    || Object.hasOwn(release, 'verified')
    || Object.hasOwn(release, 'production_verified')
  ) fail('RELEASE-MANIFEST v2 边界、入口或状态语义漂移')

  verifyContentDigest(
    boundFile(root, release.source.archive_path),
    release.source.archive_sha256,
    '源码归档',
  )
  const projectRoot = regularDirectory(join(root, 'project'))
  const { loadedCandidate } = await loadV2CandidateAndEvaluator(projectRoot)
  const candidate = loadedCandidate.manifest
  const payloadDigest = verifyV2Candidate(candidate)
  const candidatePath = boundFile(root, release.candidate.manifest_path)
  verifyContentDigest(
    candidatePath,
    release.candidate.manifest_sha256,
    'Candidate manifest',
  )
  if (
    release.candidate.candidate_id !== candidate.candidate_id
    || release.candidate.manifest_payload_digest !== payloadDigest
    || release.candidate.attestation_policy_digest
      !== digest(candidate.payload.attestation_policy)
  ) fail('RELEASE-MANIFEST 的 Candidate v2 绑定漂移')
  const acceptance = await verifyV2Acceptance(root, release, candidate)
  boundFile(root, `project/${ENTRYPOINT}`)
  return {
    root,
    release,
    manifestPath,
    candidate,
    candidateBinding: loadedCandidate.candidate,
    acceptance,
  }
}

function oracleFromFinding(finding) {
  return {
    metric: finding?.metric,
    unit: finding?.unit,
    time_slot_count: finding?.time_slot_count,
    observed_point_count: finding?.observed_point_count,
    null_point_count: finding?.null_point_count,
    first: finding?.values?.first,
    first_at_utc: finding?.values?.first_at_utc,
    last: finding?.values?.last,
    last_at_utc: finding?.values?.last_at_utc,
    minimum: finding?.values?.minimum,
    minimum_at_utc: finding?.values?.minimum_at_utc,
    maximum: finding?.values?.maximum,
    maximum_at_utc: finding?.values?.maximum_at_utc,
    difference: finding?.values?.difference,
    net_change: finding?.values?.net_change,
  }
}

function verifyV2Active(activePathArgument, verified) {
  const activePath = regularFile(activePathArgument)
  const active = readJson(activePath)
  const manifestSha = `sha256:${sha256(readFileSync(verified.manifestPath))}`
  if (
    !exactKeys(active, [
      'schema_version', 'component', 'release_id', 'deployment_state',
      'activated_at_utc', 'release_manifest_sha256', 'candidate_id',
      'runtime', 'rollback',
    ])
    || !exactKeys(active.runtime, [
      'screen_name', 'pid', 'entrypoint', 'host', 'port', 'base_path',
    ])
    || !exactKeys(active.rollback, ['mode', 'previous_release_id'])
    || active.schema_version !== 'domeye_interactive_agent_active_v1'
    || active.component !== COMPONENT
    || active.release_id !== verified.release.release_id
    || active.deployment_state !== 'deployed'
    || active.release_manifest_sha256 !== manifestSha
    || active.candidate_id !== verified.candidate.candidate_id
    || active.runtime?.screen_name !== 'domeye_interactive_agent_sidecar'
    || active.runtime?.entrypoint !== ENTRYPOINT
    || active.runtime?.host !== '127.0.0.1'
    || active.runtime?.port !== 28_476
    || active.runtime?.base_path !== '/country-outage/chat'
    || !Number.isSafeInteger(active.runtime?.pid)
    || active.runtime.pid < 1
    || !validTimestamp(active.activated_at_utc)
    || !sameValue(active.rollback, verified.release.rollback)
  ) fail('active.json 未证明同 release 已部署进程')
  return { active, activePath, manifestSha }
}

function verifyConversationIdentity(conversation, release, candidatePayload) {
  const expectedBinding = {
    ...candidatePayload.data_identity,
    event_reference: release.live_verification.event_reference,
  }
  if (
    !exactKeys(conversation, [
      'schema_version', 'conversation_id', 'binding', 'turns',
      'expires_at', 'created_at',
    ])
    || conversation.schema_version
      !== release.live_verification.public_conversation_schema_version
    || !/^conversation_sha256_[a-f0-9]{64}$/.test(
      conversation.conversation_id ?? '',
    )
    || !sameValue(conversation.binding, expectedBinding)
    || !Array.isArray(conversation.turns)
    || !validTimestamp(conversation.expires_at)
    || !validTimestamp(conversation.created_at)
  ) fail('公开 Conversation v2 身份或最小投影无效')
}

function verifyV2PublicEvidence(
  createBytes,
  turnBytes,
  responseBytes,
  release,
  candidatePayload,
  expectedConversationId,
  expectedTurnId,
) {
  const created = parseJsonBytes(createBytes, '创建响应')
  const started = parseJsonBytes(turnBytes, 'Turn 创建响应')
  const completed = parseJsonBytes(responseBytes, '最终公开响应')
  if (
    !exactKeys(created, ['conversation', 'deduplicated'])
    || created.deduplicated !== false
    || !exactKeys(started, ['turn', 'deduplicated'])
    || started.deduplicated !== false
    || !exactKeys(completed, ['conversation'])
  ) fail('公开创建链必须是非去重的 Conversation 与 Turn')
  verifyConversationIdentity(created.conversation, release, candidatePayload)
  verifyConversationIdentity(completed.conversation, release, candidatePayload)
  const initial = created.conversation
  const initialTurn = started.turn
  const conversation = completed.conversation
  if (
    initial.conversation_id !== expectedConversationId
    || initial.turns.length !== 0
    || conversation.conversation_id !== expectedConversationId
    || !sameValue(initial.binding, conversation.binding)
    || initial.created_at !== conversation.created_at
    || initial.expires_at !== conversation.expires_at
    || !Array.isArray(conversation.turns)
    || conversation.turns.length !== 1
    || !exactKeys(initialTurn, [
      'turn_id', 'turn_number', 'question', 'state', 'answer_success',
      'workflow_completed', 'created_at',
    ])
    || initialTurn.turn_id !== expectedTurnId
    || initialTurn.turn_number !== 1
    || initialTurn.question !== release.live_verification.question
    || initialTurn.state !== 'executing'
    || initialTurn.answer_success !== false
    || initialTurn.workflow_completed !== false
    || !validTimestamp(initialTurn.created_at)
  ) fail('公开证据不是全新会话的第一条非去重 Turn')
  const turn = conversation.turns[0]
  if (
    !exactKeys(turn, [
      'turn_id', 'turn_number', 'question', 'state', 'answer_success',
      'workflow_completed', 'answer', 'created_at', 'completed_at',
    ])
    || turn.turn_id !== expectedTurnId
    || turn.turn_number !== 1
    || turn.question !== release.live_verification.question
    || turn.created_at !== initialTurn.created_at
    || turn.state !== 'completed'
    || turn.answer_success !== true
    || turn.workflow_completed !== true
    || !validTimestamp(turn.completed_at)
    || Date.parse(turn.completed_at) < Date.parse(turn.created_at)
  ) fail('最终公开 Turn 不是唯一 completed 成功终态')
  const answer = turn.answer
  if (
    !exactKeys(answer, [
      'schema_version', 'answerability', 'answer_text', 'answer_source',
      'basis',
    ])
    || !exactKeys(answer.basis, [
      'source_label_zh', 'observed_object_zh', 'window_start_utc',
      'window_end_utc', 'important_boundary_zh',
    ])
    || answer.schema_version !== 'domeye_interactive_agent_turn_answer_v2'
    || answer.answerability !== 'supported'
    || answer.answer_source !== 'renderer'
    || typeof answer.answer_text !== 'string'
    || !answer.answer_text.trim()
    || !sameValue(answer.basis, {
      source_label_zh: 'Domeye 国家中断观测数据',
      observed_object_zh: 'RRC25 观测到的固定前缀可见 IPv4 地址量',
      window_start_utc: candidatePayload.data_identity.window_start_utc,
      window_end_utc: candidatePayload.data_identity.window_end_utc,
      important_boundary_zh:
        '仅表示 RRC25 单一观察点的 BGP 控制面观测，不能据此推断全国或用户实际影响、原因、责任或真实恢复。',
    })
  ) fail('公开 Answer 不是严格最小 Renderer v2 投影')
  const serialized = canonical(completed)
  if (
    /"(?:candidate_id|finding|trace|usage|evidence|receipt_refs|artifact_refs|record_digest|runtime_result)":/u.test(
      serialized,
    )
    || serialized.includes('deterministic_fallback')
    || serialized.includes('clarification_required')
    || serialized.includes('answer_not_accepted')
    || serialized.includes('provider_failure')
    || serialized.includes('stopped')
    || serialized.includes('failed')
    || serialized.includes('cancelled')
  ) fail('公开响应夹带内部证据或失败/回退语义')
  return { created, started, completed, conversation, turn, answer }
}

function verifyV2InternalEnvelopeBinding(
  internalBytes,
  publicEvidence,
  verified,
) {
  const envelope = parseJsonBytes(internalBytes, 'Sidecar 内部记录响应')
  if (!exactKeys(envelope, ['record'])) fail('内部记录响应必须严格只有 record')
  const record = envelope.record
  if (
    !exactKeys(record, [
      'schema_version', 'record_id', 'record_digest', 'conversation_id',
      'turn_id', 'candidate_id', 'contract_version', 'contract_digest',
      'answer_presentation_contract_version',
      'answer_presentation_contract_digest', 'data_identity',
      'identity_receipt', 'authorization_derivation', 'public_projection',
      'public_answer_sha256', 'public_projection_sha256', 'runtime_result',
      'failure', 'recorded_at_utc',
    ])
    || record.schema_version
      !== verified.release.live_verification.internal_record_schema_version
    || !/^turn-internal-record-sha256:[a-f0-9]{64}$/.test(
      record.record_id ?? '',
    )
    || !/^sha256:[a-f0-9]{64}$/.test(record.record_digest ?? '')
    || record.conversation_id !== publicEvidence.conversation.conversation_id
    || record.turn_id !== publicEvidence.turn.turn_id
    || record.candidate_id !== verified.candidate.candidate_id
    || record.contract_version !== verified.candidate.payload.contract.version
    || record.contract_digest !== verified.candidate.payload.contract.digest
    || record.answer_presentation_contract_version
      !== verified.candidate.payload.answer_presentation_contract.version
    || record.answer_presentation_contract_digest
      !== verified.candidate.payload.answer_presentation_contract.digest
    || !sameValue(record.data_identity, verified.candidate.payload.data_identity)
    || !sameValue(record.public_projection, publicEvidence.turn)
    || record.public_projection_sha256 !== digest(publicEvidence.turn)
    || record.public_answer_sha256
      !== `sha256:${sha256(publicEvidence.answer.answer_text)}`
    || record.failure !== null
    || !validTimestamp(record.recorded_at_utc)
  ) fail('内部记录未精确绑定同一 Candidate/Conversation/Turn 公共投影')
  const {
    record_id: recordId,
    record_digest: recordDigest,
    ...recordBody
  } = record
  const expectedRecordDigest = digest(recordBody)
  if (
    recordDigest !== expectedRecordDigest
    || recordId
      !== `turn-internal-record-sha256:${expectedRecordDigest.slice(7)}`
  ) fail('内部 record_id/record_digest 重算不一致')
  return record
}

async function verifyV2InternalEvidence(
  projectRoot,
  internalBytes,
  publicEvidence,
  verified,
) {
  const record = verifyV2InternalEnvelopeBinding(
    internalBytes,
    publicEvidence,
    verified,
  )

  const [service, findingAnswer] = await Promise.all([
    importProjectModule(
      projectRoot,
      CONVERSATION_SERVICE_PATH,
      'Interactive Conversation Service',
    ),
    importProjectModule(projectRoot, FINDING_ANSWER_PATH, 'Finding Answer'),
  ])
  if (
    typeof service.hasValidDomeyeInteractiveTurnInternalRecord !== 'function'
    || typeof service.hasSuccessfulDomeyePublicFinalAnswer !== 'function'
    || typeof findingAnswer.buildCountryOutageAnswerContext !== 'function'
    || typeof findingAnswer.guardCountryOutageResponse !== 'function'
  ) fail('内部记录或正式 Guard 重放导出不完整')
  if (!service.hasValidDomeyeInteractiveTurnInternalRecord(record)) {
    fail('内部记录未通过正式完整性校验')
  }
  const result = record.runtime_result
  const principalId = result?.loop?.admission_receipts?.[0]?.principal?.principal_id
  if (
    typeof principalId !== 'string'
    || !principalId
    || !service.hasSuccessfulDomeyePublicFinalAnswer(
      result,
      verified.candidateBinding,
      record.identity_receipt,
      principalId,
    )
  ) fail('内部 runtime result 未通过正式公共成功门重放')
  let expectedContext
  let replayedGuard
  try {
    expectedContext = findingAnswer.buildCountryOutageAnswerContext(result.finding)
    replayedGuard = findingAnswer.guardCountryOutageResponse(
      expectedContext,
      result.answer.render_attempt.draft,
    )
  } catch (error) {
    fail(`Renderer/Guard v2 重放失败：${error.message}`)
  }
  const guard = result.answer?.guard_result
  const style = guard?.style_assessment
  if (
    result.outcome !== 'completed'
    || result.schema_version !== 'domeye_first_vertical_slice_run_v2'
    || result.answer?.source !== 'renderer'
    || result.answer?.render_attempt?.status !== 'completed'
    || result.answer.render_attempt.failure_code !== null
    || result.answer.answer !== publicEvidence.answer.answer_text
    || !sameValue(expectedContext, result.answer_context)
    || !sameValue(replayedGuard, guard)
    || guard?.schema_version !== 'domeye_agent_response_guard_v2'
    || guard?.decision !== 'pass'
    || guard?.assessment_status !== 'evaluated'
    || !Array.isArray(guard.reason_codes)
    || guard.reason_codes.length !== 0
    || style?.schema_version !== 'domeye_agent_answer_style_assessment_v1'
    || style?.passed !== true
    || !Array.isArray(style.reason_codes)
    || style.reason_codes.length !== 0
    || !Array.isArray(style.leak_codes)
    || style.leak_codes.length !== 0
    || !Array.isArray(style.outside_context_codes)
    || style.outside_context_codes.length !== 0
    || style.policy_id
      !== verified.acceptance.answer_style_policy_binding?.policy_id
    || style.policy_digest
      !== verified.acceptance.answer_style_policy_binding?.policy_digest
    || !sameValue(
      oracleFromFinding(result.finding),
      verified.release.live_verification.oracle,
    )
    || canonical(result).includes('deterministic_fallback')
    || canonical(result).includes('answer_not_accepted')
    || canonical(result).includes('clarification_required')
    || canonical(result).includes('provider_failure')
  ) fail('内部结果不是 Renderer + Guard v2 + style + 精确 Oracle 完成闭包')
  return { record, result, guard, style }
}

function frozenBytesFromReceipt(encoded, expectedSha, label) {
  if (typeof encoded !== 'string' || !encoded) fail(`${label}缺少冻结原始字节`)
  const bytes = Buffer.from(encoded, 'base64')
  if (
    bytes.toString('base64') !== encoded
    || expectedSha !== `sha256:${sha256(bytes)}`
  ) fail(`${label}冻结原始字节或摘要无效`)
  return bytes
}

async function verifyV2Promotion(
  rootArgument,
  activePathArgument,
  createPath,
  turnPath,
  responsePath,
  internalPath,
  timestamp,
  expectedConversationId,
  expectedTurnId,
  storedPromotionPathArgument = null,
) {
  const verified = await verifyV2Release(rootArgument)
  const activeBinding = verifyV2Active(activePathArgument, verified)
  let stored = null
  let storedPath = null
  let createBytes
  let turnBytes
  let responseBytes
  let internalBytes
  if (storedPromotionPathArgument !== null) {
    storedPath = regularFile(storedPromotionPathArgument)
    stored = readJson(storedPath)
    timestamp = stored.verified_at_utc
    expectedConversationId = stored.public_response?.conversation_id
    expectedTurnId = stored.public_response?.turn_id
    createBytes = frozenBytesFromReceipt(
      stored.public_response?.create_response_body_base64,
      stored.public_response?.create_response_sha256,
      '创建响应',
    )
    turnBytes = frozenBytesFromReceipt(
      stored.public_response?.turn_response_body_base64,
      stored.public_response?.turn_response_sha256,
      'Turn 响应',
    )
    responseBytes = frozenBytesFromReceipt(
      stored.public_response?.response_body_base64,
      stored.public_response?.response_sha256,
      '最终公开响应',
    )
    internalBytes = frozenBytesFromReceipt(
      stored.internal_record?.response_body_base64,
      stored.internal_record?.response_sha256,
      '内部记录响应',
    )
  } else {
    createBytes = readFileSync(regularFile(createPath))
    turnBytes = readFileSync(regularFile(turnPath))
    responseBytes = readFileSync(regularFile(responsePath))
    internalBytes = readFileSync(regularFile(internalPath))
  }
  if (
    !validTimestamp(timestamp)
    || Date.parse(timestamp) < Date.parse(activeBinding.active.activated_at_utc)
    || !/^conversation_sha256_[a-f0-9]{64}$/.test(
      expectedConversationId ?? '',
    )
    || !/^turn_sha256_[a-f0-9]{64}$/.test(expectedTurnId ?? '')
  ) fail('promotion 时间或本次会话/Turn 身份无效')
  const publicEvidence = verifyV2PublicEvidence(
    createBytes,
    turnBytes,
    responseBytes,
    verified.release,
    verified.candidate.payload,
    expectedConversationId,
    expectedTurnId,
  )
  const internalEvidence = await verifyV2InternalEvidence(
    join(verified.root, 'project'),
    internalBytes,
    publicEvidence,
    verified,
  )
  verifyV2PromotionTimeline(
    timestamp,
    publicEvidence.turn.completed_at,
    internalEvidence.record.recorded_at_utc,
  )
  const payload = {
    schema_version: 'domeye_interactive_agent_promotion_v2',
    component: COMPONENT,
    release_id: verified.release.release_id,
    promotion_state: 'verified',
    verified_at_utc: timestamp,
    release_manifest_sha256: activeBinding.manifestSha,
    active_receipt_sha256:
      `sha256:${sha256(readFileSync(activeBinding.activePath))}`,
    candidate_id: verified.candidate.candidate_id,
    acceptance_record_id: verified.acceptance.acceptance_record_id,
    public_response: {
      origin: verified.release.live_verification.public_backend_origin,
      base_path: verified.release.live_verification.backend_base_path,
      conversation_id: expectedConversationId,
      turn_id: expectedTurnId,
      question: verified.release.live_verification.question,
      create_response_sha256: `sha256:${sha256(createBytes)}`,
      create_response_body_base64: createBytes.toString('base64'),
      turn_response_sha256: `sha256:${sha256(turnBytes)}`,
      turn_response_body_base64: turnBytes.toString('base64'),
      response_sha256: `sha256:${sha256(responseBytes)}`,
      response_body_base64: responseBytes.toString('base64'),
      conversation_deduplicated: false,
      turn_deduplicated: false,
      turn_number: 1,
      conversation_turn_count: 1,
      turn_projection_sha256: digest(publicEvidence.turn),
      answer_text_sha256:
        `sha256:${sha256(publicEvidence.answer.answer_text)}`,
    },
    internal_record: {
      origin: verified.release.live_verification.internal_sidecar_origin,
      base_path: verified.release.live_verification.internal_record_base_path,
      record_schema_version: internalEvidence.record.schema_version,
      record_id: internalEvidence.record.record_id,
      record_digest: internalEvidence.record.record_digest,
      response_sha256: `sha256:${sha256(internalBytes)}`,
      response_body_base64: internalBytes.toString('base64'),
      public_projection_sha256:
        internalEvidence.record.public_projection_sha256,
      runtime_result_sha256: digest(internalEvidence.result),
    },
    result: {
      state: 'completed',
      answer_success: true,
      workflow_completed: true,
      answer_source: 'renderer',
      guard_schema_version: internalEvidence.guard.schema_version,
      guard_decision: 'pass',
      guard_assessment_status: 'evaluated',
      style_policy_id: internalEvidence.style.policy_id,
      style_policy_digest: internalEvidence.style.policy_digest,
      style_assessment_passed: true,
      final_answer_digest: internalEvidence.result.answer.answer_digest,
      oracle_digest: verified.release.live_verification.oracle_digest,
      public_answer_present: true,
      internal_record_verified: true,
      public_internal_projection_equal: true,
      fallback_or_rejection_present: false,
    },
  }
  const receipt = {
    promotion_id: `promotion-${digest(payload)}`,
    ...payload,
  }
  if (stored !== null) {
    if (
      !sameValue(stored, receipt)
      || !Buffer.from(`${JSON.stringify(receipt, null, 2)}\n`).equals(
        readFileSync(storedPath),
      )
    ) fail('promotion 与冻结公私原始证据的当前重放不精确一致')
    return receipt
  }
  process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`)
}

const args = process.argv.slice(2)
if (args[0] === '_test-v2-public-evidence') {
  if (args.length !== 7) {
    fail('用法：verify-release.mjs _test-v2-public-evidence <create.json> <turn.json> <final.json> <candidate.json> <conversation-id> <turn-id>')
  }
  const root = fixtureRoot()
  const candidate = readJson(fixtureFile(root, args[4]))
  verifyV2PublicEvidence(
    readFileSync(fixtureFile(root, args[1])),
    readFileSync(fixtureFile(root, args[2])),
    readFileSync(fixtureFile(root, args[3])),
    {
      live_verification: {
        public_conversation_schema_version:
          'domeye_interactive_agent_conversation_v2',
        event_reference: 'country_outage/2026-02-27 09:12:32/IR/1/r',
        question: QUESTION,
      },
    },
    candidate.payload,
    args[5],
    args[6],
  )
} else if (args[0] === '_test-v2-internal-binding') {
  if (args.length !== 4) {
    fail('用法：verify-release.mjs _test-v2-internal-binding <final.json> <internal.json> <candidate.json>')
  }
  const root = fixtureRoot()
  const response = readJson(fixtureFile(root, args[1]))
  const candidate = readJson(fixtureFile(root, args[3]))
  const conversation = response.conversation
  const turn = conversation?.turns?.[0]
  verifyV2InternalEnvelopeBinding(
    readFileSync(fixtureFile(root, args[2])),
    { conversation, turn, answer: turn?.answer },
    {
      candidate,
      release: {
        live_verification: {
          internal_record_schema_version:
            'domeye_interactive_agent_turn_internal_record_v1',
        },
      },
    },
  )
} else if (args[0] === '_test-json-no-duplicate') {
  if (args.length !== 2) {
    fail('用法：verify-release.mjs _test-json-no-duplicate <input.json>')
  }
  const root = fixtureRoot()
  parseJsonBytes(readFileSync(fixtureFile(root, args[1])), '测试输入')
} else if (args[0] === '_test-v2-promotion-timeline') {
  if (args.length !== 4) {
    fail('用法：verify-release.mjs _test-v2-promotion-timeline <verified-at> <turn-completed-at> <internal-recorded-at>')
  }
  fixtureRoot()
  verifyV2PromotionTimeline(args[1], args[2], args[3])
} else if (args[0] === 'acceptance-replay') {
  if (args.length !== 5) {
    fail('用法：verify-release.mjs acceptance-replay <project-root> <acceptance-record-relative-path> <approved-candidate-id> <approved-acceptance-record-id>')
  }
  const receipt = await replayV2IndependentAcceptance(
    args[1],
    args[2],
    args[3],
    args[4],
  )
  process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`)
} else if (args[0] === 'promotion') {
  if (args.length !== 10) {
    fail('用法：verify-release.mjs promotion <release-root> <active.json> <create-response.json> <turn-response.json> <final-response.json> <internal-response.json> <verified-at> <conversation-id> <turn-id>')
  }
  await verifyV2Promotion(
    args[1],
    args[2],
    args[3],
    args[4],
    args[5],
    args[6],
    args[7],
    args[8],
    args[9],
  )
} else if (args[0] === 'promotion-receipt') {
  if (args.length !== 4) {
    fail('用法：verify-release.mjs promotion-receipt <release-root> <active.json> <promotion.json>')
  }
  await verifyV2Promotion(
    args[1],
    args[2],
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    args[3],
  )
} else {
  const root = args[0] === 'release' ? args[1] : args[0]
  if (!root || args.length > (args[0] === 'release' ? 2 : 1)) {
    fail('用法：verify-release.mjs [release] <release-root>')
  }
  const verified = await verifyV2Release(root)
  process.stdout.write(`${JSON.stringify({
    status: 'release_verified',
    schema_version: verified.release.schema_version,
    component: verified.release.component,
    release_id: verified.release.release_id,
    candidate_id: verified.candidate.candidate_id,
    acceptance_record_id: verified.acceptance.acceptance_record_id,
    activation_scope: 'local_evaluation_only',
    candidate_production_deployed: false,
  })}\n`)
}
