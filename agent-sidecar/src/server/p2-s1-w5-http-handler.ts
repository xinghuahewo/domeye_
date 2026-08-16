import { timingSafeEqual } from 'node:crypto'
import type { IncomingMessage, RequestListener, ServerResponse } from 'node:http'

import {
  P2S1W5ContractError,
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

export interface P2S1W5HttpHandlerOptions {
  runtime: P2S1W5CompositionRuntime
  planningGroundingRuntime?: P2S1W5PlanningGroundingRuntime
  sharedToken: string
  basePath?: string
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
          runtime_integrated: false,
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
