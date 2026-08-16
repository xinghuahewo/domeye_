import { createHash } from 'node:crypto'
import { lstatSync, readFileSync, realpathSync } from 'node:fs'
import { createServer, type RequestListener, type Server } from 'node:http'
import { resolve } from 'node:path'

import { P2S1W5CompositionRuntime } from '../chat/p2-s1-composition-runtime.js'
import { P2S1W5ArtifactStore } from '../chat/p2-s1-dual-artifact-store.js'
import { P2S1W5PlanningGroundingRuntime } from '../chat/p2-s1-planning-grounding-port.js'
import {
  InMemoryP2S1W5TrustedFixtureCatalog,
  ReplayOnlyP2S1W5ModelPort,
  type P2S1W5InjectedModelPort,
  type P2S1W5TrustedFixtureCatalog,
} from '../chat/p2-s1-model-runner.js'
import type { P2S1W5TrustedReplayFixture } from '../chat/p2-s1-composition-contracts.js'
import { createP2S1W5HttpHandler } from '../server/p2-s1-w5-http-handler.js'

export type P2S1W5SidecarEnvironment = NodeJS.ProcessEnv & {
  COUNTRY_OUTAGE_P2_S1_W5_HOST?: string
  COUNTRY_OUTAGE_P2_S1_W5_PORT?: string
  COUNTRY_OUTAGE_P2_S1_W5_SHARED_TOKEN?: string
  COUNTRY_OUTAGE_P2_S1_W5_FIXTURE_PATH?: string
  COUNTRY_OUTAGE_P2_S1_W5_FIXTURE_FILE_SHA256?: string
  COUNTRY_OUTAGE_P2_S1_W5_ARTIFACT_STORE?: string
}

export interface P2S1W5SidecarDependencies {
  fixtureCatalog?: P2S1W5TrustedFixtureCatalog
  modelPort?: P2S1W5InjectedModelPort
  artifactStore?: P2S1W5ArtifactStore
  httpServerFactory?: (listener: RequestListener) => Server
}

export interface FormalP2S1W5Sidecar {
  host: string
  port: number
  server: Server
  runtime: P2S1W5CompositionRuntime
  planningGroundingRuntime: P2S1W5PlanningGroundingRuntime
  execution: {
    mode: 'trusted_fixture_replay_only'
    externalProviderEnabled: false
    p1CertificationReused: false
    productionHandlerIntegrated: false
    productionDeployed: false
  }
}

function required(env: P2S1W5SidecarEnvironment, key: keyof P2S1W5SidecarEnvironment): string {
  const value = env[key]?.trim()
  if (!value) throw new Error(`缺少 W5 环境变量 ${String(key)}`)
  return value
}

function configuration(env: P2S1W5SidecarEnvironment) {
  const host = env.COUNTRY_OUTAGE_P2_S1_W5_HOST?.trim() || '127.0.0.1'
  if (host !== '127.0.0.1' && host !== '::1' && host !== 'localhost') {
    throw new Error('W5 fixture Sidecar 只允许 loopback 地址')
  }
  const portText = env.COUNTRY_OUTAGE_P2_S1_W5_PORT?.trim() || '28485'
  const port = Number(portText)
  if (!Number.isSafeInteger(port) || port < 1 || port > 65_535) throw new Error('W5 port 无效')
  const sharedToken = required(env, 'COUNTRY_OUTAGE_P2_S1_W5_SHARED_TOKEN')
  if (sharedToken.length < 24) throw new Error('W5 shared token 至少需要 24 字符')
  return {
    host,
    port,
    sharedToken,
    fixturePath: env.COUNTRY_OUTAGE_P2_S1_W5_FIXTURE_PATH?.trim(),
    fixtureFileSha256: env.COUNTRY_OUTAGE_P2_S1_W5_FIXTURE_FILE_SHA256?.trim(),
    artifactStorePath: env.COUNTRY_OUTAGE_P2_S1_W5_ARTIFACT_STORE?.trim(),
  }
}

function loadFixtureCatalog(pathValue: string, expectedDigest: string): P2S1W5TrustedFixtureCatalog {
  if (!/^[0-9a-f]{64}$/.test(expectedDigest)) throw new Error('W5 fixture file SHA-256 无效')
  const path = resolve(pathValue)
  const stat = lstatSync(path)
  const uid = typeof process.getuid === 'function' ? process.getuid() : undefined
  if (
    !stat.isFile()
    || stat.isSymbolicLink()
    || stat.size > 16 * 1024 * 1024
    || (stat.mode & 0o022) !== 0
    || (uid !== undefined && stat.uid !== uid)
  ) throw new Error('W5 fixture 文件必须是当前进程持有、不可被组/其他用户写入的有界普通文件')
  realpathSync(path)
  const bytes = readFileSync(path)
  const actual = createHash('sha256').update(bytes).digest('hex')
  if (actual !== expectedDigest) throw new Error('W5 fixture 文件摘要不一致')
  const raw = JSON.parse(bytes.toString('utf8')) as unknown
  const fixtures = Array.isArray(raw) ? raw : [raw]
  return new InMemoryP2S1W5TrustedFixtureCatalog(
    fixtures as P2S1W5TrustedReplayFixture[],
  )
}

export function createFormalP2S1W5Sidecar(
  env: P2S1W5SidecarEnvironment = process.env,
  dependencies: P2S1W5SidecarDependencies = {},
): FormalP2S1W5Sidecar {
  const config = configuration(env)
  const fixtures = dependencies.fixtureCatalog ?? loadFixtureCatalog(
    config.fixturePath ?? required(env, 'COUNTRY_OUTAGE_P2_S1_W5_FIXTURE_PATH'),
    config.fixtureFileSha256 ?? required(env, 'COUNTRY_OUTAGE_P2_S1_W5_FIXTURE_FILE_SHA256'),
  )
  const artifactStore = dependencies.artifactStore ?? new P2S1W5ArtifactStore(
    config.artifactStorePath ?? required(env, 'COUNTRY_OUTAGE_P2_S1_W5_ARTIFACT_STORE'),
  )
  const modelPort = dependencies.modelPort ?? new ReplayOnlyP2S1W5ModelPort(fixtures)
  if (modelPort.mode !== 'trusted_fixture_replay') throw new Error('W5 禁止外部 provider model port')
  const runtime = new P2S1W5CompositionRuntime({ fixtures, modelPort, artifactStore })
  const planningGroundingRuntime = new P2S1W5PlanningGroundingRuntime({ fixtures, modelPort })
  const listener = createP2S1W5HttpHandler({
    runtime,
    planningGroundingRuntime,
    sharedToken: config.sharedToken,
  })
  const server = (dependencies.httpServerFactory ?? createServer)(listener)
  return {
    host: config.host,
    port: config.port,
    server,
    runtime,
    planningGroundingRuntime,
    execution: {
      mode: 'trusted_fixture_replay_only',
      externalProviderEnabled: false,
      p1CertificationReused: false,
      productionHandlerIntegrated: false,
      productionDeployed: false,
    },
  }
}

export async function startFormalP2S1W5Sidecar(
  env: P2S1W5SidecarEnvironment = process.env,
  dependencies: P2S1W5SidecarDependencies = {},
): Promise<FormalP2S1W5Sidecar> {
  const sidecar = createFormalP2S1W5Sidecar(env, dependencies)
  await new Promise<void>((resolvePromise, reject) => {
    const onError = (error: Error): void => reject(error)
    sidecar.server.once('error', onError)
    sidecar.server.listen(sidecar.port, sidecar.host, () => {
      sidecar.server.off('error', onError)
      resolvePromise()
    })
  })
  return sidecar
}
