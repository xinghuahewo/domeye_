import { timingSafeEqual } from 'node:crypto'
import type { IncomingMessage, RequestListener, ServerResponse } from 'node:http'

import {
  P2S1W5ContractError,
  p2S1W5Digest,
  type P2S1W5RunRequest,
} from '../chat/p2-s1-composition-contracts.js'
import type { P2S1W5CompositionRuntime } from '../chat/p2-s1-composition-runtime.js'
import type {
  P2S1W5PlanningBindingSummary,
  P2S1W5PlanningGroundingRequest,
  P2S1W5PlanningGroundingRuntime,
} from '../chat/p2-s1-planning-grounding-port.js'

const DEFAULT_BASE_PATH = '/country-outage/p2-s1-w5'
const MAX_REQUEST_BYTES = 32 * 1024

function exactObject(value: unknown, keys: string[], label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new P2S1W5ContractError('invalid_request', `${label} 必须是对象`)
  const record = value as Record<string, unknown>
  if (Object.keys(record).sort().join('\0') !== [...keys].sort().join('\0')) throw new P2S1W5ContractError('invalid_request_fields', `${label} 字段不闭合`)
  return record
}

const prefixedDigest = (value: unknown): string => `sha256:${p2S1W5Digest(value)}`

function integratedAnswer(value: unknown): Record<string, unknown> {
  const envelope = exactObject(value, ['schema_version', 'fixture_id', 'fixture_digest', 'idempotency_key', 'request'], 'integrated answer request')
  if (envelope.schema_version !== 'country_outage_p2_s1_w5_integrated_answer_request_v1') throw new P2S1W5ContractError('invalid_request', 'integrated answer request 版本无效')
  if (typeof envelope.fixture_id !== 'string' || typeof envelope.fixture_digest !== 'string' || typeof envelope.idempotency_key !== 'string') throw new P2S1W5ContractError('invalid_request', 'fixture/idempotency 字段无效')
  const request = exactObject(envelope.request, [
    'schema_version', 'investigation_id', 'source_investigation_revision', 'source_current_digest',
    'identity', 'question', 'question_digest', 'anchor', 'plan_ref', 'result_set_refs',
    'evidence_graph_ref', 'evidence_refs', 'host_graph_facts', 'host_oracle',
    'shared_answer_binding', 'shared_answer_binding_digest',
  ], 'model turn request')
  const binding = exactObject(request.shared_answer_binding, [
    'investigation_id', 'source_investigation_revision', 'source_current_digest', 'identity_digest',
    'plan_ref', 'result_set_refs', 'evidence_graph_ref', 'host_oracle_digest', 'question_digest',
    'anchor', 'registry_snapshot_id', 'registry_snapshot_digest', 'binding_generation',
  ], 'shared answer binding')
  if (request.shared_answer_binding_digest !== prefixedDigest(binding) || request.question_digest !== prefixedDigest(request.question)) throw new P2S1W5ContractError('binding_digest_invalid', 'Sidecar 输入 binding/question 摘要不一致')
  const oracle = exactObject(request.host_oracle, ['schema_version', 'boundary_texts', 'boundary_assertions', 'limitations', 'unknown_texts', 'unknowns', 'prohibited_claim_patterns', 'oracle_digest'], 'host oracle')
  if (oracle.oracle_digest !== prefixedDigest(Object.fromEntries(Object.entries(oracle).filter(([key]) => key !== 'oracle_digest')))) throw new P2S1W5ContractError('oracle_digest_invalid', 'Host Oracle 摘要无效')
  const facts = request.host_graph_facts
  if (!Array.isArray(facts) || facts.length === 0) throw new P2S1W5ContractError('graph_fact_missing', 'Sidecar 回答需要已提交 Graph fact')
  const fact = exactObject(facts[0], [
    'fact_id', 'source_node_id', 'source_value_digest', 'evidence_refs',
    'claim_kind', 'claim_relation', 'allowed_claim_text',
  ], 'graph fact')
  if (!Array.isArray(fact.evidence_refs) || fact.evidence_refs.length === 0) throw new P2S1W5ContractError('graph_evidence_missing', 'Graph fact 缺少 evidence refs')
  for (const field of ['boundary_texts', 'boundary_assertions', 'limitations', 'unknown_texts', 'unknowns', 'prohibited_claim_patterns']) {
    if (!Array.isArray(oracle[field]) || oracle[field].length === 0) throw new P2S1W5ContractError('oracle_boundary_missing', `Host Oracle ${field} 不完整`)
  }
  return envelope
}

export interface P2S1W5HttpHandlerOptions {
  runtime: P2S1W5CompositionRuntime
  planningGroundingRuntime?: P2S1W5PlanningGroundingRuntime
  sharedToken: string
  basePath?: string
  integratedAnswerRuntimeEnabled?: boolean
  integratedAnswerRuntime?: { run(value: Record<string, unknown>): Promise<Record<string, unknown>> }
}

function writeJson(response: ServerResponse, status: number, value: unknown): void {
  const content = Buffer.from(JSON.stringify(value), 'utf8')
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': String(content.byteLength),
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  })
  response.end(content)
}

function authorized(request: IncomingMessage, expected: string): boolean {
  const header = request.headers.authorization
  if (!header?.startsWith('Bearer ')) return false
  const supplied = Buffer.from(header.slice('Bearer '.length), 'utf8')
  const wanted = Buffer.from(expected, 'utf8')
  return supplied.length === wanted.length && timingSafeEqual(supplied, wanted)
}

async function readJson(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = []
  let total = 0
  for await (const raw of request) {
    const chunk = Buffer.isBuffer(raw) ? raw : Buffer.from(raw)
    total += chunk.byteLength
    if (total > MAX_REQUEST_BYTES) throw new P2S1W5ContractError('request_too_large', '请求体超过限制')
    chunks.push(chunk)
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  } catch {
    throw new P2S1W5ContractError('invalid_json', '请求体不是有效 JSON')
  }
}

function runRequest(value: unknown): P2S1W5RunRequest {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new P2S1W5ContractError('invalid_request', '请求体必须是对象')
  }
  const body = value as Record<string, unknown>
  const allowed = ['fixture_id', 'idempotency_key', 'degraded_authorization_id']
  const unexpected = Object.keys(body).filter((key) => !allowed.includes(key))
  if (unexpected.length) {
    throw new P2S1W5ContractError('invalid_request_fields', `请求不得携带模型、计划、证据或 Oracle 字段：${unexpected.join(',')}`)
  }
  if (typeof body.fixture_id !== 'string' || typeof body.idempotency_key !== 'string') {
    throw new P2S1W5ContractError('invalid_request', 'fixture_id 与 idempotency_key 必须是字符串')
  }
  return {
    fixture_id: body.fixture_id,
    idempotency_key: body.idempotency_key,
    ...(typeof body.degraded_authorization_id === 'string'
      ? { degraded_authorization_id: body.degraded_authorization_id }
      : {}),
  }
}

function planningGroundingRequest(value: unknown): P2S1W5PlanningGroundingRequest {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new P2S1W5ContractError('invalid_request', '请求体必须是对象')
  }
  const body = value as Record<string, unknown>
  const allowed = [
    'fixture_id', 'goal', 'goal_digest', 'binding_summary',
    'binding_summary_digest', 'idempotency_key',
  ]
  const unexpected = Object.keys(body).filter((key) => !allowed.includes(key))
  if (unexpected.length) {
    throw new P2S1W5ContractError(
      'invalid_request_fields',
      `planning/grounding 请求只能携带 fixture、goal、身份摘要与幂等键：${unexpected.join(',')}`,
    )
  }
  if (
    typeof body.fixture_id !== 'string'
    || typeof body.goal !== 'string'
    || typeof body.goal_digest !== 'string'
    || typeof body.binding_summary_digest !== 'string'
    || typeof body.idempotency_key !== 'string'
    || !body.binding_summary
    || typeof body.binding_summary !== 'object'
    || Array.isArray(body.binding_summary)
  ) throw new P2S1W5ContractError('invalid_request', 'planning/grounding 请求字段类型无效')
  return {
    fixture_id: body.fixture_id,
    goal: body.goal,
    goal_digest: body.goal_digest,
    binding_summary: body.binding_summary as unknown as P2S1W5PlanningBindingSummary,
    binding_summary_digest: body.binding_summary_digest,
    idempotency_key: body.idempotency_key,
  }
}

function errorStatus(error: P2S1W5ContractError): number {
  if (error.code === 'fixture_not_found') return 404
  if (error.code === 'idempotency_conflict') return 409
  if (error.code.includes('unavailable')) return 503
  return 400
}

export function createP2S1W5HttpHandler(options: P2S1W5HttpHandlerOptions): RequestListener {
  if (options.sharedToken.length < 24) throw new Error('W5 shared token 至少需要 24 字符')
  const basePath = options.basePath ?? DEFAULT_BASE_PATH
  return async (request, response): Promise<void> => {
    const pathname = new URL(request.url ?? '/', 'http://127.0.0.1').pathname
    if (!authorized(request, options.sharedToken)) {
      writeJson(response, 401, { error: { code: 'unauthorized', message: 'W5 Sidecar 鉴权失败' } })
      return
    }
    try {
      if (request.method === 'GET' && pathname === `${basePath}/readyz`) {
        writeJson(response, 200, {
          schema_version: 'country_outage_p2_s1_w5_readiness_v1',
          ready: true,
          collector_id: 'rrc25',
          execution_mode: 'trusted_fixture_replay_only',
          external_provider_enabled: false,
          p1_certification_reused: false,
          planning_grounding_endpoint_enabled: Boolean(options.planningGroundingRuntime),
          full_investigation_plan_owner: 'python_host_runtime',
          runtime_integrated: options.integratedAnswerRuntimeEnabled === true && Boolean(options.integratedAnswerRuntime),
          production_deployed: false,
        })
        return
      }
      if (request.method === 'POST' && pathname === `${basePath}/planning-groundings`) {
        if (!options.planningGroundingRuntime) {
          writeJson(response, 503, {
            error: {
              code: 'planning_grounding_unavailable',
              message: 'W5 planning/grounding fixture 端口未配置',
            },
          })
          return
        }
        const result = await options.planningGroundingRuntime.run(
          planningGroundingRequest(await readJson(request)),
        )
        writeJson(response, result.disposition === 'planning_unavailable' ? 503 : 200, result)
        return
      }
      if (request.method === 'POST' && pathname === `${basePath}/runs`) {
        const result = await options.runtime.run(runRequest(await readJson(request)))
        writeJson(response, 200, result)
        return
      }
      if (request.method === 'POST' && pathname === `${basePath}/answer-turns`) {
        if (options.integratedAnswerRuntimeEnabled !== true || !options.integratedAnswerRuntime) {
          writeJson(response, 503, { error: { code: 'integrated_answer_unavailable', message: 'W5 integrated answer runtime 未配置' } })
          return
        }
        const value = await readJson(request)
        integratedAnswer(value) // 先执行闭合输入与 Host binding 校验；不得绕过 handler 边界。
        writeJson(response, 200, await options.integratedAnswerRuntime.run(value as Record<string, unknown>))
        return
      }
      writeJson(response, 404, { error: { code: 'not_found', message: 'W5 路由不存在' } })
    } catch (error) {
      if (error instanceof P2S1W5ContractError) {
        writeJson(response, errorStatus(error), {
          error: { code: error.code, message: error.message },
        })
        return
      }
      writeJson(response, 500, {
        error: { code: 'internal_error', message: 'W5 fixture replay 执行失败' },
      })
    }
  }
}
