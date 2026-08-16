import { startFormalP1Sidecar } from './formal-p1-sidecar.js'

async function main(): Promise<void> {
  const sidecar = await startFormalP1Sidecar()
  const shutdown = (): void => {
    sidecar.server.close(() => process.exit(0))
  }
  process.once('SIGINT', shutdown)
  process.once('SIGTERM', shutdown)
  process.stdout.write(
    `${JSON.stringify({
      event: 'country_outage_p1_sidecar_ready',
      host: sidecar.host,
      port: sidecar.port,
      collector: sidecar.runtime.collector,
      eventType: sidecar.runtime.eventType,
      persistence: 'ephemeral',
      piVersion: sidecar.binding.preflight.piVersion,
      modelProfile: sidecar.binding.preflight.profileId,
      registryVersion: sidecar.binding.preflight.registryVersion,
      modelIdentity: sidecar.modelIdentity,
      maximumProviderRequestCountPerTurn:
        sidecar.runtime.maximumProviderRequestCountPerTurn,
      businessCostLimit: sidecar.runtime.businessCostLimit,
      usageAndEstimatedCostAudit: 'required_per_provider_call',
      reportCapability: sidecar.runtime.reportCapability,
      externalEvidence: sidecar.runtime.externalEvidence,
    })}\n`,
  )
}

void main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error)
  process.stderr.write(`国家中断 P1 Sidecar 启动失败：${message}\n`)
  process.exitCode = 1
})
