import { startFormalP2S1W5Sidecar } from './formal-p2-s1-w5-sidecar.js'

async function main(): Promise<void> {
  const sidecar = await startFormalP2S1W5Sidecar()
  const shutdown = (): void => {
    sidecar.server.close(() => process.exit(0))
  }
  process.once('SIGINT', shutdown)
  process.once('SIGTERM', shutdown)
  process.stdout.write(`${JSON.stringify({
    event: 'country_outage_p2_s1_w5_fixture_sidecar_ready',
    host: sidecar.host,
    port: sidecar.port,
    execution_mode: sidecar.execution.mode,
    planning_grounding_endpoint: '/country-outage/p2-s1-w5/planning-groundings',
    full_investigation_plan_owner: 'python_host_runtime',
    external_provider_enabled: false,
    p1_certification_reused: false,
    production_handler_integrated: false,
    production_deployed: false,
  })}\n`)
}

void main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error)
  process.stderr.write(`国家中断 P2 S1 W5 fixture Sidecar 启动失败：${message}\n`)
  process.exitCode = 1
})
