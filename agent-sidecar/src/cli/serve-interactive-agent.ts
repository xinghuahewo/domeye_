import { startDomeyeInteractiveAgentSidecar } from './interactive-agent-sidecar.js'

async function main(): Promise<void> {
  const sidecar = await startDomeyeInteractiveAgentSidecar()
  const shutdown = (): void => {
    sidecar.server.close(() => process.exit(0))
  }
  process.once('SIGINT', shutdown)
  process.once('SIGTERM', shutdown)
  process.stdout.write(`${JSON.stringify({
    event: 'domeye_interactive_agent_ready',
    host: sidecar.host,
    port: sidecar.port,
    readiness: sidecar.readiness,
  })}\n`)
}

void main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error)
  process.stderr.write(`国家中断交互式 Agent 启动失败：${message}\n`)
  process.exitCode = 1
})
