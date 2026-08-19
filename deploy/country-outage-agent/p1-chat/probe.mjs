#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import {
  lstatSync,
  readFileSync,
  readlinkSync,
  realpathSync,
} from 'node:fs'
import {
  basename,
  dirname,
  isAbsolute,
  join,
  normalize,
  relative,
  resolve,
} from 'node:path'
import { fileURLToPath } from 'node:url'

const PROBE_SCHEMA = 'domeye_interactive_agent_release_probe_v1'
const RELEASE_SCHEMA = 'domeye_interactive_agent_release_manifest_v1'
const ACTIVE_SCHEMA = 'domeye_interactive_agent_active_v1'
const PROMOTION_SCHEMA = 'domeye_interactive_agent_promotion_v1'
const COMPONENT = 'domeye_interactive_agent_sidecar'
const READINESS_SCHEMA = 'domeye_interactive_agent_readiness_v1'
const ENTRYPOINT = 'agent-sidecar/dist/src/cli/serve-interactive-agent.js'
const CANDIDATE_SCHEMA = 'domeye_first_slice_candidate_manifest_v1'
const CANDIDATE_PATH =
  'project/contracts/agent/domeye-first-vertical-slice/v1/candidate.json'
const FIXED_URL = 'http://127.0.0.1:28476'
const FIXED_HOST = '127.0.0.1'
const FIXED_PORT = 28_476
const FIXED_NODE = '/home/bgpdata/.local/node-v22.23.1-linux-x64/bin/node'
const TEST_ROOT_ENV = 'DOMEYE_INTERACTIVE_AGENT_TEST_ROOT'
const FIXED_QUESTION =
  '在这次冻结 publication 的观测窗口内，RRC25 看到的固定前缀可见 IPv4 地址量最低是多少，首次在什么观测时刻出现？首值、末值、最大值和极差分别是多少？'

class ProbeFailure extends Error {
  constructor(code, message) {
    super(message)
    this.code = code
  }
}

function reject(code, message) {
  throw new ProbeFailure(code, message)
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

function exactKeys(value, expected) {
  return value !== null
    && typeof value === 'object'
    && !Array.isArray(value)
    && sameValue(Object.keys(value).sort(), [...expected].sort())
}

function regularFile(path, code, label) {
  const normalized = resolve(path)
  try {
    const stats = lstatSync(normalized)
    if (
      !stats.isFile()
      || stats.isSymbolicLink()
      || realpathSync(normalized) !== normalized
    ) reject(code, `${label}不是规范普通文件`)
  } catch (error) {
    if (error instanceof ProbeFailure) throw error
    reject(code, `缺少${label}`)
  }
  return normalized
}

function testRoot() {
  const configured = process.env[TEST_ROOT_ENV]
  if (!configured) return null
  if (
    !/^\/(?:private\/)?tmp\/domeye-interactive-agent-test\.[A-Za-z0-9._-]+$/.test(
      configured,
    )
  ) reject('test_boundary_invalid', '测试根路径不在显式临时边界')
  const root = resolve(configured)
  try {
    const stats = lstatSync(root)
    if (!stats.isDirectory() || stats.isSymbolicLink() || realpathSync(root) !== root) {
      reject('test_boundary_invalid', '测试根不是规范实际目录')
    }
  } catch (error) {
    if (error instanceof ProbeFailure) throw error
    reject('test_boundary_invalid', '测试根不存在')
  }
  return root
}

function requireTestBoundPath(path, root) {
  if (root && relative(root, resolve(path)).startsWith('..')) {
    reject('test_boundary_invalid', '测试文件路径越界')
  }
}

function secureStateFile(path, code, label) {
  const file = regularFile(path, code, label)
  const fixtureRoot = testRoot()
  requireTestBoundPath(file, fixtureRoot)
  const stats = lstatSync(file)
  const expectedUid = fixtureRoot ? process.getuid() : 0
  const expectedGid = fixtureRoot ? process.getgid() : 0
  if (
    stats.uid !== expectedUid
    || stats.gid !== expectedGid
    || (stats.mode & 0o777) !== 0o600
  ) {
    const detail = process.env.DOMEYE_INTERACTIVE_AGENT_PROBE_DEBUG === '1'
      ? ` (${stats.uid}:${stats.gid}:${(stats.mode & 0o777).toString(8)} / ${expectedUid}:${expectedGid}:600)`
      : ''
    reject(code, `${label}所有者或权限不是受信 0600${detail}`)
  }
  return file
}

function readJson(path, code, label) {
  const file = regularFile(path, code, label)
  try {
    const value = JSON.parse(readFileSync(file, 'utf8'))
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      reject(code, `${label}根节点无效`)
    }
    return { file, value }
  } catch (error) {
    if (error instanceof ProbeFailure) throw error
    reject(code, `${label} JSON 无效`)
  }
}

function boundFile(root, path, code, label) {
  if (
    typeof path !== 'string'
    || !path
    || isAbsolute(path)
    || normalize(path) !== path
    || path.split('/').includes('..')
  ) reject(code, `${label}路径无效`)
  const target = resolve(root, path)
  if (relative(root, target).startsWith('..')) reject(code, `${label}路径越界`)
  return regularFile(target, code, label)
}

function validTimestamp(value) {
  return typeof value === 'string'
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/.test(value)
    && Number.isFinite(Date.parse(value))
}

function parseConfig(configArgument) {
  const config = regularFile(
    configArgument,
    'config_invalid',
    '运行配置',
  )
  requireTestBoundPath(config, testRoot())
  const values = new Map()
  for (const rawLine of readFileSync(config, 'utf8').split('\n')) {
    if (!rawLine || rawLine.startsWith('#')) continue
    if (rawLine.includes('\r')) reject('config_invalid', '运行配置行无效')
    const separator = rawLine.indexOf('=')
    if (separator <= 0) reject('config_invalid', '运行配置行无效')
    const key = rawLine.slice(0, separator)
    const value = rawLine.slice(separator + 1)
    if (!/^[A-Z][A-Z0-9_]*$/.test(key) || !value || /\s/.test(value)) {
      reject('config_invalid', '运行配置键值无效')
    }
    if (values.has(key)) reject('config_invalid', '运行配置键重复')
    values.set(key, value)
  }
  const baseUrl = values.get('COUNTRY_OUTAGE_AGENT_URL')
  const host = values.get('COUNTRY_OUTAGE_AGENT_HOST')
  const port = values.get('COUNTRY_OUTAGE_AGENT_PORT')
  const token = values.get('COUNTRY_OUTAGE_AGENT_SHARED_TOKEN')
  if (
    baseUrl !== FIXED_URL
    || host !== FIXED_HOST
    || port !== String(FIXED_PORT)
    || typeof token !== 'string'
    || token.length < 32
  ) reject('config_invalid', '固定 Sidecar 连接配置无效')
  return { baseUrl, token }
}

function releaseDirectory(rootArgument) {
  if (!rootArgument || !isAbsolute(rootArgument)) {
    reject('release_invalid', 'release 根必须是绝对路径')
  }
  const root = resolve(rootArgument)
  requireTestBoundPath(root, testRoot())
  try {
    const stats = lstatSync(root)
    if (!stats.isDirectory() || stats.isSymbolicLink() || realpathSync(root) !== root) {
      reject('release_invalid', 'release 根不是规范实际目录')
    }
  } catch (error) {
    if (error instanceof ProbeFailure) throw error
    reject('release_invalid', 'release 根不存在')
  }
  if (basename(dirname(root)) !== 'releases') {
    reject('release_invalid', 'release 根不在固定 releases 目录')
  }
  return root
}

function runImmutableReleaseVerification(root) {
  const verifier = regularFile(
    join(dirname(fileURLToPath(import.meta.url)), 'verify-release.mjs'),
    'release_invalid',
    'release 校验器',
  )
  const child = spawnSync(process.execPath, [verifier, 'release', root], {
    encoding: 'utf8',
    maxBuffer: 1024 * 1024,
    timeout: 60_000,
  })
  if (child.error || child.status !== 0 || child.signal !== null) {
    const detail = process.env.DOMEYE_INTERACTIVE_AGENT_PROBE_DEBUG === '1'
      ? `：${child.stderr.trim()}`
      : ''
    reject('release_invalid', `不可变 release 闭包校验失败${detail}`)
  }
  try {
    const receipt = JSON.parse(child.stdout)
    if (
      receipt?.status !== 'release_verified'
      || receipt?.schema_version !== RELEASE_SCHEMA
      || receipt?.component !== COMPONENT
      || receipt?.activation_scope !== 'local_evaluation_only'
      || receipt?.candidate_production_deployed !== false
    ) reject('release_invalid', 'release 校验回执语义漂移')
    return receipt
  } catch (error) {
    if (error instanceof ProbeFailure) throw error
    reject('release_invalid', 'release 校验回执无效')
  }
}

function runImmutablePromotionVerification(root, activeFile, promotionFile) {
  const verifier = regularFile(
    join(dirname(fileURLToPath(import.meta.url)), 'verify-release.mjs'),
    'promotion_invalid',
    'promotion 校验器',
  )
  const child = spawnSync(
    process.execPath,
    [verifier, 'promotion-receipt', root, activeFile, promotionFile],
    {
      encoding: 'utf8',
      maxBuffer: 1024 * 1024,
      timeout: 60_000,
    },
  )
  if (child.error || child.status !== 0 || child.signal !== null) {
    const detail = process.env.DOMEYE_INTERACTIVE_AGENT_PROBE_DEBUG === '1'
      ? `：${child.stderr.trim()}`
      : ''
    reject('promotion_invalid', `promotion 当前 Guard 重放失败${detail}`)
  }
}

function verifyRelease(rootArgument) {
  const root = releaseDirectory(rootArgument)
  const receipt = runImmutableReleaseVerification(root)
  const manifest = readJson(
    join(root, 'RELEASE-MANIFEST.json'),
    'release_invalid',
    'RELEASE-MANIFEST.json',
  )
  const release = manifest.value
  if (
    release.schema_version !== RELEASE_SCHEMA
    || release.component !== COMPONENT
    || release.release_id !== receipt.release_id
    || release.candidate?.manifest_path !== CANDIDATE_PATH
    || release.runtime?.entrypoint !== ENTRYPOINT
    || release.runtime?.host !== FIXED_HOST
    || release.runtime?.port !== FIXED_PORT
    || release.runtime?.base_path !== '/country-outage/chat'
    || release.runtime?.activation_scope !== 'local_evaluation_only'
    || release.runtime?.candidate_production_deployed !== false
    || release.live_verification?.backend_base_path
      !== '/api/v2/country-outage/chat'
    || release.live_verification?.public_backend_origin
      !== 'http://127.0.0.1:28471'
    || release.live_verification?.question !== FIXED_QUESTION
  ) reject('release_invalid', 'release 身份、入口或固定验证问题漂移')

  const candidateFile = boundFile(
    root,
    release.candidate.manifest_path,
    'candidate_invalid',
    'Candidate manifest',
  )
  const candidate = readJson(
    candidateFile,
    'candidate_invalid',
    'Candidate manifest',
  ).value
  if (
    candidate.payload?.schema_version !== CANDIDATE_SCHEMA
    || candidate.payload?.activation?.scope !== 'local_evaluation_only'
    || candidate.payload?.activation?.production_deployed !== false
  ) reject('candidate_invalid', 'Candidate 非 local_evaluation_only 边界')
  const payloadDigest = digest(candidate.payload)
  const candidateId = `manifest:${payloadDigest}`
  const candidateByteDigest = `sha256:${sha256(readFileSync(candidateFile))}`
  if (
    candidate.candidate_id !== candidateId
    || release.candidate.candidate_id !== candidateId
    || receipt.candidate_id !== candidateId
    || release.candidate.manifest_payload_digest !== payloadDigest
    || release.candidate.manifest_sha256 !== candidateByteDigest
    || release.candidate.activation_scope !== 'local_evaluation_only'
    || release.candidate.production_deployed !== false
  ) reject('candidate_invalid', 'Candidate ID 或摘要绑定漂移')
  return {
    root,
    release,
    candidate,
    candidateId,
    manifestFile: manifest.file,
    manifestDigest: `sha256:${sha256(readFileSync(manifest.file))}`,
  }
}

async function fetchReadiness(config, verified) {
  let response
  try {
    response = await fetch(`${config.baseUrl}/country-outage/chat/readiness`, {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${config.token}`,
        'X-Domeye-User': 'domeye-interactive-agent-release-probe',
        'X-Domeye-Authorization-Scope': 'country_outage_event_read:IR',
      },
      redirect: 'error',
      signal: AbortSignal.timeout(5_000),
    })
  } catch {
    reject('sidecar_unreachable', 'Sidecar readiness 不可达')
  }
  if (response.status !== 200) {
    reject('sidecar_not_ready', 'Sidecar readiness 未返回 200')
  }
  let readiness
  try {
    const body = await response.text()
    if (Buffer.byteLength(body) > 1024 * 1024) {
      reject('sidecar_identity_drift', 'Sidecar readiness 超出大小边界')
    }
    readiness = JSON.parse(body)
  } catch (error) {
    if (error instanceof ProbeFailure) throw error
    reject('sidecar_identity_drift', 'Sidecar readiness JSON 无效')
  }
  const expectedKeys = [
    'schema_version',
    'ready',
    'candidate_id',
    'activation_scope',
    'production_deployed',
    'contract',
    'data_identity',
    'model_identity',
    'budget_policy',
    'policy_id',
    'policy_digest',
    'registry_snapshot_id',
    'registry_digest',
    'capabilities',
    'persistence',
    'report_capability',
    'external_evidence',
  ]
  const payload = verified.candidate.payload
  const capabilities = payload.registry?.capabilities?.map((item) => ({
    capability_id: item.capability_id,
    execution_binding: item.execution_binding,
  }))
  if (
    !exactKeys(readiness, expectedKeys)
    || readiness.schema_version !== READINESS_SCHEMA
    || readiness.ready !== true
    || readiness.candidate_id !== verified.candidateId
    || readiness.activation_scope !== 'local_evaluation_only'
    || readiness.production_deployed !== false
    || !sameValue(readiness.contract, payload.contract)
    || !sameValue(readiness.data_identity, payload.data_identity)
    || !sameValue(readiness.model_identity, payload.model)
    || !sameValue(readiness.budget_policy, payload.budget_policy)
    || readiness.policy_id !== payload.policy?.policy_id
    || readiness.policy_digest !== payload.policy?.policy_digest
    || readiness.registry_snapshot_id !== payload.registry?.registry_snapshot_id
    || readiness.registry_digest !== payload.registry?.registry_digest
    || !sameValue(readiness.capabilities, capabilities)
    || readiness.persistence !== 'ephemeral'
    || readiness.report_capability !== 'disabled'
    || readiness.external_evidence !== 'disabled'
  ) reject('sidecar_identity_drift', 'Sidecar readiness Candidate 身份漂移')
  return readiness
}

function verifyCurrentTarget(verified) {
  const current = join(dirname(dirname(verified.root)), 'current')
  try {
    const stats = lstatSync(current)
    if (!stats.isSymbolicLink()) {
      reject('current_target_drift', 'current 不是符号链接')
    }
    const targetText = readlinkSync(current)
    if (!targetText || targetText.includes('\0')) {
      reject('current_target_drift', 'current 目标无效')
    }
    if (realpathSync(current) !== verified.root) {
      reject('current_target_drift', 'current 未指向同一 release 实际目录')
    }
  } catch (error) {
    if (error instanceof ProbeFailure) throw error
    reject('current_target_drift', 'current 目标不存在或不可解析')
  }
}

function verifyActive(activeArgument, verified) {
  const activeFile = secureStateFile(
    activeArgument,
    'active_drift',
    'active.json',
  )
  const active = readJson(activeFile, 'active_drift', 'active.json')
  const value = active.value
  const keys = [
    'schema_version',
    'component',
    'release_id',
    'deployment_state',
    'activated_at_utc',
    'release_manifest_sha256',
    'candidate_id',
    'runtime',
    'rollback',
  ]
  const runtimeKeys = [
    'screen_name',
    'pid',
    'entrypoint',
    'host',
    'port',
    'base_path',
  ]
  const rollbackKeys = ['mode', 'previous_release_id']
  if (
    !exactKeys(value, keys)
    || !exactKeys(value.runtime, runtimeKeys)
    || !exactKeys(value.rollback, rollbackKeys)
    || value.schema_version !== ACTIVE_SCHEMA
    || value.component !== COMPONENT
    || value.release_id !== verified.release.release_id
    || value.deployment_state !== 'deployed'
    || !validTimestamp(value.activated_at_utc)
    || value.release_manifest_sha256 !== verified.manifestDigest
    || value.candidate_id !== verified.candidateId
    || value.runtime.screen_name !== 'domeye_interactive_agent_sidecar'
    || value.runtime.entrypoint !== ENTRYPOINT
    || value.runtime.host !== FIXED_HOST
    || value.runtime.port !== FIXED_PORT
    || value.runtime.base_path !== '/country-outage/chat'
    || !Number.isSafeInteger(value.runtime.pid)
    || value.runtime.pid < 1
    || !sameValue(value.rollback, verified.release.rollback)
  ) reject('active_drift', 'active.json 未绑定同一部署 release')
  verifyCurrentTarget(verified)
  verifyRuntimeProcess(value.runtime.pid, verified)
  return {
    value,
    file: active.file,
    digest: `sha256:${sha256(readFileSync(active.file))}`,
  }
}

function verifyRuntimeProcess(pid, verified) {
  try {
    process.kill(pid, 0)
  } catch {
    reject('active_process_drift', 'active PID 不存在或不可检查')
  }
  const expectedEntrypoint = join(verified.root, 'project', ENTRYPOINT)
  const expectedCwd = join(verified.root, 'project')
  const fixtureRoot = testRoot()
  const expectedNode = fixtureRoot
    ? join(fixtureRoot, 'tools/node/bin/node')
    : FIXED_NODE
  if (process.platform === 'linux') {
    let commandArguments
    try {
      commandArguments = readFileSync(`/proc/${pid}/cmdline`)
        .toString('utf8')
        .split('\0')
        .filter(Boolean)
    } catch {
      reject('active_process_drift', 'active PID 缺少可校验 cmdline')
    }
    if (
      commandArguments.length !== 2
      || commandArguments[0] !== expectedNode
      || commandArguments[1] !== expectedEntrypoint
    ) {
      reject('active_process_drift', 'active PID 未绑定同 release 入口')
    }
    try {
      if (realpathSync(`/proc/${pid}/cwd`) !== expectedCwd) {
        reject('active_process_drift', 'active PID 工作目录未绑定 release project')
      }
    } catch (error) {
      if (error instanceof ProbeFailure) throw error
      reject('active_process_drift', 'active PID 工作目录不可校验')
    }
    const sockets = spawnSync(
      'ss',
      ['-H', '-ltnp', 'sport = :28476'],
      { encoding: 'utf8', timeout: 5_000 },
    )
    const listeners = sockets.stdout?.split('\n')
      .filter((line) => line.trim())
    if (
      sockets.error
      || sockets.status !== 0
      || listeners.length !== 1
      || listeners[0].trim().split(/\s+/)[3] !== '127.0.0.1:28476'
      || !listeners[0].includes(`pid=${pid},`)
    ) reject('active_process_drift', 'active PID 不是 28476 的监听进程')
    return
  }
  if (!fixtureRoot) {
    reject('active_process_drift', '生产进程身份校验仅支持 Linux /proc')
  }
  const processView = spawnSync(
    'ps',
    ['-p', String(pid), '-o', 'command='],
    { encoding: 'utf8', timeout: 5_000 },
  )
  const commandArguments = processView.stdout?.trim().split(/\s+/)
  if (
    processView.error
    || processView.status !== 0
    || !sameValue(commandArguments, [expectedNode, expectedEntrypoint])
  ) reject('active_process_drift', '测试 active PID 未绑定同 release 入口')
  const sockets = spawnSync(
    '/usr/sbin/lsof',
    [
      '-nP',
      '-a',
      '-p',
      String(pid),
      '-iTCP:28476',
      '-sTCP:LISTEN',
      '-Fpn',
    ],
    { encoding: 'utf8', timeout: 5_000 },
  )
  const listenerPids = sockets.stdout?.split('\n')
    .filter((line) => /^p\d+$/.test(line))
  const listenerNames = sockets.stdout?.split('\n')
    .filter((line) => line.startsWith('n'))
  if (
    sockets.error
    || sockets.status !== 0
    || listenerPids.length !== 1
    || listenerPids[0] !== `p${pid}`
    || listenerNames.length !== 1
    || listenerNames[0] !== 'n127.0.0.1:28476'
  ) reject('active_process_drift', '测试 active PID 不是 28476 的监听进程')
}

function verifyPromotion(promotionArgument, verified, active) {
  const promotionFile = secureStateFile(
    promotionArgument,
    'promotion_invalid',
    'promotion receipt',
  )
  const promotion = readJson(
    promotionFile,
    'promotion_invalid',
    'promotion receipt',
  ).value
  const keys = [
    'promotion_id',
    'schema_version',
    'component',
    'release_id',
    'promotion_state',
    'verified_at_utc',
    'release_manifest_sha256',
    'active_receipt_sha256',
    'candidate_id',
    'backend',
    'result',
  ]
  const backendKeys = [
    'origin',
    'base_path',
    'conversation_id',
    'turn_id',
    'question',
    'response_sha256',
    'response_body_base64',
  ]
  const resultKeys = [
    'state',
    'answer_success',
    'workflow_completed',
    'answer_source',
    'guard_decision',
    'oracle_digest',
    'public_answer_present',
    'fallback_or_rejection_present',
  ]
  const payload = { ...promotion }
  delete payload.promotion_id
  if (
    !exactKeys(promotion, keys)
    || !exactKeys(promotion.backend, backendKeys)
    || !exactKeys(promotion.result, resultKeys)
    || promotion.promotion_id !== `promotion-${digest(payload)}`
    || promotion.schema_version !== PROMOTION_SCHEMA
    || promotion.component !== COMPONENT
    || promotion.release_id !== verified.release.release_id
    || promotion.promotion_state !== 'verified'
    || !validTimestamp(promotion.verified_at_utc)
    || Date.parse(promotion.verified_at_utc)
      < Date.parse(active.value.activated_at_utc)
    || promotion.release_manifest_sha256 !== verified.manifestDigest
    || promotion.active_receipt_sha256 !== active.digest
    || promotion.candidate_id !== verified.candidateId
    || promotion.backend.origin
      !== verified.release.live_verification.public_backend_origin
    || promotion.backend.base_path
      !== verified.release.live_verification.backend_base_path
    || !/^conversation_sha256_[a-f0-9]{64}$/.test(
      promotion.backend.conversation_id ?? '',
    )
    || !/^turn_sha256_[a-f0-9]{64}$/.test(
      promotion.backend.turn_id ?? '',
    )
    || promotion.backend.question !== FIXED_QUESTION
    || !/^sha256:[a-f0-9]{64}$/.test(promotion.backend.response_sha256 ?? '')
    || promotion.result.state !== 'completed'
    || promotion.result.answer_success !== true
    || promotion.result.workflow_completed !== true
    || promotion.result.answer_source !== 'renderer'
    || promotion.result.guard_decision !== 'pass'
    || promotion.result.oracle_digest
      !== verified.release.live_verification.oracle_digest
    || promotion.result.public_answer_present !== true
    || promotion.result.fallback_or_rejection_present !== false
  ) {
    reject(
      'promotion_invalid',
      'promotion 未证明公开 Backend 固定问题的 Renderer + Guard 完整正确回答',
    )
  }
  if (
    typeof promotion.backend.response_body_base64 !== 'string'
    || !promotion.backend.response_body_base64
  ) reject('promotion_invalid', 'promotion 保留的 Backend 原始响应无效')
  const responseBytes = Buffer.from(
    promotion.backend.response_body_base64,
    'base64',
  )
  if (
    responseBytes.toString('base64') !== promotion.backend.response_body_base64
    || promotion.backend.response_sha256
      !== `sha256:${sha256(responseBytes)}`
  ) reject('promotion_invalid', 'promotion 保留的 Backend 原始响应无效')
  runImmutablePromotionVerification(
    verified.root,
    active.file,
    promotionFile,
  )
  return promotion
}

function successPayload(verified, lifecycleState, productionVerified) {
  return {
    schema_version: PROBE_SCHEMA,
    ready: true,
    component: COMPONENT,
    lifecycle_state: lifecycleState,
    release_id: verified.release.release_id,
    release_manifest_sha256: verified.manifestDigest,
    candidate_id: verified.candidateId,
    candidate_activation_scope: 'local_evaluation_only',
    candidate_production_deployed: false,
    current_target_matches: lifecycleState !== 'staged',
    deployment_active: lifecycleState !== 'staged',
    promotion_state: productionVerified ? 'verified' : 'absent',
    production_verified: productionVerified,
  }
}

async function main() {
  const args = process.argv.slice(2)
  const command = args[0]
  if (command === 'readiness') {
    if (args.length !== 3) {
      reject(
        'usage_invalid',
        '用法：probe.mjs readiness <config> <release-root>',
      )
    }
    const config = parseConfig(args[1])
    const verified = verifyRelease(args[2])
    await fetchReadiness(config, verified)
    process.stdout.write(`${JSON.stringify(
      successPayload(verified, 'staged', false),
    )}\n`)
    return
  }
  if (command === 'status') {
    if (args.length !== 5) {
      reject(
        'usage_invalid',
        '用法：probe.mjs status <config> <release-root> <active.json> <promotion-or->',
      )
    }
    const config = parseConfig(args[1])
    const verified = verifyRelease(args[2])
    await fetchReadiness(config, verified)
    const active = verifyActive(args[3], verified)
    if (args[4] === '-') {
      process.stdout.write(`${JSON.stringify(
        successPayload(verified, 'deployed', false),
      )}\n`)
      return
    }
    verifyPromotion(args[4], verified, active)
    process.stdout.write(`${JSON.stringify(
      successPayload(verified, 'verified', true),
    )}\n`)
    return
  }
  reject(
    'usage_invalid',
    '用法：probe.mjs {readiness|status} ...',
  )
}

try {
  await main()
} catch (error) {
  const failure = error instanceof ProbeFailure
    ? error
    : new ProbeFailure('probe_internal_error', '发布组合探针内部失败')
  process.stdout.write(`${JSON.stringify({
    schema_version: PROBE_SCHEMA,
    ready: false,
    component: COMPONENT,
    lifecycle_state: 'invalid',
    production_verified: false,
    reason_code: failure.code,
  })}\n`)
  process.stderr.write(`Interactive Agent 发布探针失败：${failure.message}\n`)
  process.exitCode = 1
}
