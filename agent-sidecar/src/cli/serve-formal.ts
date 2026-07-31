import { startFormalCountryOutageSidecar } from './formal-sidecar.js'

async function main(): Promise<void> {
  const sidecar = await startFormalCountryOutageSidecar()
  const shutdown = (): void => {
    sidecar.server.close(() => process.exit(0))
  }
  process.once('SIGINT', shutdown)
  process.once('SIGTERM', shutdown)

  process.stdout.write(
    `${JSON.stringify({
      event: 'country_outage_agent_sidecar_ready',
      host: sidecar.host,
      port: sidecar.port,
      collector: 'rrc25',
      narrator: 'pi-sdk-certified',
      persistence: 'ephemeral',
      piVersion: sidecar.binding.preflight.piVersion,
      modelProfile: sidecar.binding.preflight.profileId,
      registryVersion: sidecar.binding.preflight.registryVersion,
      baseReportCacheTtlMs: sidecar.baseReportCacheTtlMs,
      validatorRulesVersion:
        sidecar.reportServiceIdentity.validatorRulesVersion,
      skillBundleSha256:
        sidecar.reportServiceIdentity.skillBundleSha256,
      externalEvidence: 'disabled',
      externalEvidenceProvider: 'disabled',
    })}\n`,
  )
}

void main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error)
  process.stderr.write(
    `国家中断 Agent 正式 Sidecar 启动失败：${message}\n`,
  )
  process.exitCode = 1
})
