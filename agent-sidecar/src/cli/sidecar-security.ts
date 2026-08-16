import { timingSafeEqual } from 'node:crypto'
import type { IncomingMessage } from 'node:http'

import type { CountryOutagePrincipal } from '../server/index.js'

export const COUNTRY_OUTAGE_LOOPBACK_HOSTS = new Set([
  '127.0.0.1',
  '::1',
  'localhost',
])

const SAFE_PRINCIPAL = /^[A-Za-z0-9@._:-]{1,256}$/
const SAFE_SCOPE = /^[A-Za-z0-9._:,-]{1,512}$/
const COUNTRY_FROM_REFERENCE =
  /^country_outage\/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}\/([A-Z]{2})\/[1-9]\d*\/r$/

export type SidecarEnvironment = Readonly<
  Record<string, string | undefined>
>

export function requiredEnvironmentValue(
  env: SidecarEnvironment,
  name: string,
): string {
  const value = env[name]?.trim()
  if (!value) throw new Error(`缺少必需环境变量 ${name}`)
  return value
}

export function positiveIntegerEnvironmentValue(
  env: SidecarEnvironment,
  name: string,
  fallback: number,
): number {
  const raw = env[name]?.trim()
  if (!raw) return fallback
  const parsed = Number(raw)
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} 必须是正整数`)
  }
  return parsed
}

export function assertCountryOutageLoopbackHost(host: string): void {
  if (!COUNTRY_OUTAGE_LOOPBACK_HOSTS.has(host)) {
    throw new Error('国家中断 Agent Sidecar 只允许监听本机地址')
  }
}

function safeEqual(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left)
  const rightBuffer = Buffer.from(right)
  return (
    leftBuffer.byteLength === rightBuffer.byteLength &&
    timingSafeEqual(leftBuffer, rightBuffer)
  )
}

function header(request: IncomingMessage, name: string): string {
  const value = request.headers[name]
  return typeof value === 'string' ? value : value?.[0] ?? ''
}

export function createCountryOutageInternalAuthenticator(
  sharedToken: string,
): (request: IncomingMessage) => CountryOutagePrincipal | null {
  if (sharedToken.length < 24) {
    throw new Error('COUNTRY_OUTAGE_AGENT_SHARED_TOKEN 至少需要 24 字符')
  }
  return (request: IncomingMessage): CountryOutagePrincipal | null => {
    const authorization = header(request, 'authorization')
    const expected = `Bearer ${sharedToken}`
    if (!safeEqual(authorization, expected)) return null
    const userId = header(request, 'x-domeye-user').trim()
    const authorizationScope = header(
      request,
      'x-domeye-authorization-scope',
    ).trim()
    if (
      !SAFE_PRINCIPAL.test(userId) ||
      !SAFE_SCOPE.test(authorizationScope)
    ) {
      return null
    }
    return { userId, authorizationScope }
  }
}

export function countryOutageScopeAllowsEvent(
  principal: CountryOutagePrincipal,
  eventReference: string,
): boolean {
  const country = eventReference.match(COUNTRY_FROM_REFERENCE)?.[1]
  if (!country) return false
  const capabilities = new Set(
    principal.authorizationScope.split(',').map((value) => value.trim()),
  )
  return (
    capabilities.has('country_outage_event_read') ||
    capabilities.has(`country_outage_event_read:${country}`)
  )
}
