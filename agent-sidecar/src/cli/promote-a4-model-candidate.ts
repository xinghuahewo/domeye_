import {
  PiModelCertificationError,
  promotePersistedA4ModelCandidate,
} from '../pi/index.js'

class PromotionConfigurationError extends Error {
  constructor() {
    super('DeepSeek 候选晋级参数无效')
    this.name = 'PromotionConfigurationError'
  }
}

function parseArguments(argv: readonly string[]): {
  evidenceId: string
  registryVersion: string
} {
  const values = new Map<string, string>()
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]
    const value = argv[index + 1]
    if (
      !key ||
      !value ||
      !['--evidence-id', '--registry-version'].includes(key) ||
      values.has(key)
    ) {
      throw new PromotionConfigurationError()
    }
    values.set(key, value)
  }
  const evidenceId = values.get('--evidence-id')?.trim() ?? ''
  const registryVersion =
    values.get('--registry-version')?.trim() ?? ''
  if (!evidenceId || !registryVersion || values.size !== 2) {
    throw new PromotionConfigurationError()
  }
  return { evidenceId, registryVersion }
}

async function main(): Promise<void> {
  const argumentsValue = parseArguments(process.argv.slice(2))
  const result = await promotePersistedA4ModelCandidate({
    evidenceId: argumentsValue.evidenceId,
    newRegistryVersion: argumentsValue.registryVersion,
  })
  process.stdout.write(
    `${JSON.stringify({
      event: 'country_outage_a4_model_candidate_promoted',
      evidenceId: result.certificationEvidenceId,
      registryVersion: result.registryVersion,
      registrySha256: result.registrySha256,
    })}\n`,
  )
}

void main().catch((error: unknown) => {
  const code =
    error instanceof PiModelCertificationError
      ? error.code
      : error instanceof PromotionConfigurationError
        ? 'candidate_promotion_configuration_invalid'
        : 'candidate_promotion_failed'
  const message =
    error instanceof PiModelCertificationError ||
    error instanceof PromotionConfigurationError
      ? error.message
      : 'DeepSeek 候选晋级失败'
  process.stderr.write(
    `${JSON.stringify({
      event: 'country_outage_a4_model_candidate_promotion_failed',
      code,
      message,
    })}\n`,
  )
  process.exitCode = 1
})
